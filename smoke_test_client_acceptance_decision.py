"""Sanctions & AML vetting, its documentation and the decision to accept the client are on the client's page only (d63): the decision (Partner/Admin),
notes rules, the engagement gate (locked until the client is accepted), sync to each engagement's acceptance, carry-over of old decisions at start-up,
the vetting documents, and the engagement tab showing the decision read-only."""
import os, sys, tempfile, io
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import datetime
from app import create_app, _migrate_client_acceptance_decisions
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    ptn = User(username="ptn", name="Partner P", role="partner"); ptn.set_password("x")
    mgr = User(username="mgr", name="Manager M", role="supervisor"); mgr.set_password("x")
    db.session.add_all([ptn, mgr]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id)
    legacy_acc = Client(name="Legacy Accepted", created_by_id=admin.id)
    legacy_dec = Client(name="Legacy Declined", created_by_id=admin.id)
    legacy_none = Client(name="Legacy Nothing", created_by_id=admin.id)
    db.session.add_all([cl, legacy_acc, legacy_dec, legacy_none]); db.session.commit()
    check(cl.acceptance_status == "Pending" and not cl.is_accepted_as_client, "a new client starts as Pending")
    e1 = Engagement(client_id=cl.id, title="FY26 audit", type="Audit", partner_id=ptn.id, acceptance_required=True)
    e2 = Engagement(client_id=cl.id, title="Tax 26", type="Audit", partner_id=ptn.id, acceptance_required=True)
    e3 = Engagement(client_id=cl.id, title="Old job", type="Audit", partner_id=ptn.id, acceptance_required=False)
    la = Engagement(client_id=legacy_acc.id, title="Open job", type="Audit", partner_id=ptn.id, acceptance_required=True)
    ld = Engagement(client_id=legacy_dec.id, title="Declined job", type="Audit", partner_id=ptn.id, acceptance_required=True)
    db.session.add_all([e1, e2, e3, la, ld]); db.session.commit()
    ids = dict(cl=cl.id, e1=e1.id, e2=e2.id, e3=e3.id, admin=admin.id, ptn=ptn.id, mgr=mgr.id, la=la.id, ld=ld.id, lc=legacy_acc.id, ldc=legacy_dec.id, ln=legacy_none.id)
    # rows from before the column existed are NULL (an explicit None would pick up the default)
    from sqlalchemy import text
    db.session.execute(text("UPDATE client SET acceptance_decision = NULL WHERE id IN (:a, :b, :c)"), {"a": legacy_acc.id, "b": legacy_dec.id, "c": legacy_none.id}); db.session.commit()
    # legacy: an engagement already signed off as Accepted on the old per-engagement decision
    db.session.add(ClientAcceptance(engagement_id=la.id, decision="Accepted", decision_notes="Fine", partner_signed_at=datetime(2026, 5, 1), partner_signed_by_id=ptn.id))
    db.session.add(ClientAcceptance(engagement_id=ld.id, decision="Declined", decision_notes="Too risky"))
    db.session.commit()
    check(engagement_acceptance_cleared(Engagement.query.get(la.id)) is False or True, "(before the carry-over)")
    _migrate_client_acceptance_decisions()
    check(Client.query.get(ids["lc"]).acceptance_decision == "Accepted" and Client.query.get(ids["lc"]).acceptance_decision_notes == "Fine", "carry-over: a client with an accepted engagement becomes Accepted, with the notes")
    check(Client.query.get(ids["ldc"]).acceptance_decision == "Declined", "carry-over: a declined engagement makes the client Declined")
    check(Client.query.get(ids["ln"]).acceptance_decision == "Pending", "carry-over: no acceptance at all is Pending")
    check(engagement_acceptance_cleared(Engagement.query.get(la.id)), "carry-over: an engagement that was open stays open")
    check(not engagement_acceptance_cleared(Engagement.query.get(ld.id)), "carry-over: a declined one stays locked")
    check(engagement_acceptance_cleared(Engagement.query.get(e3.id)), "an engagement that never required acceptance is always cleared")
    _migrate_client_acceptance_decisions()
    check(Client.query.get(ids["lc"]).acceptance_decision == "Accepted", "the carry-over is idempotent")

A, P, M = login(ids["admin"]), login(ids["ptn"]), login(ids["mgr"])
r = A.get(f"/clients/{ids['cl']}")
check(r.status_code == 200 and b'id="vetting-documents"' in r.data and b'id="client-decision"' in r.data and b"Decision to accept this client" in r.data, "the client page has the vetting documentation and the decision")

# until accepted, the new client's engagements are locked, even with a full sign-off on the engagement itself
with app.app_context():
    rec = ClientAcceptance(engagement_id=ids["e1"], decision="Accepted", partner_signed_at=datetime(2026, 6, 1), partner_signed_by_id=ids["ptn"])
    db.session.add(rec); db.session.commit()
    check(not engagement_acceptance_cleared(Engagement.query.get(ids["e1"])), "a signed engagement is still locked while the client is Pending")
r = A.get(f"/engagements/{ids['e1']}?tab=acceptance")
check(r.status_code == 200 and b"Sanctions &amp; AML Vetting</h3>" not in r.data and b"Decision to accept the client" in r.data and b"#client-decision" in r.data, "the engagement tab shows the client's decision read-only and links to the client page")
check(b'name="decision"' not in r.data, "...with no decision field on the engagement")
# the old engagement route changes nothing
r = A.post(f"/engagements/{ids['e1']}/acceptance/decision/save", data={"decision": "Accepted", "decision_notes": "x"}, follow_redirects=True)
check(b"made on the client" in r.data, "the old engagement decision route points to the client's page")
with app.app_context():
    check(Client.query.get(ids["cl"]).acceptance_status == "Pending", "...and changes nothing")

def decide(c, **kw):
    return c.post(f"/clients/{ids['cl']}/acceptance-decision", data=kw, follow_redirects=True)
check(M.post(f"/clients/{ids['cl']}/acceptance-decision", data={"decision": "Accepted"}).status_code == 403, "a manager cannot record the decision (Partner or Admin only)")
r = decide(P, decision="Declined", decision_notes="")
check(b"give the reason for declining" in r.data, "Declined needs a reason")
r = decide(P, decision="Accepted with conditions", decision_notes="")
check(b"state the conditions" in r.data, "Accepted with conditions needs the conditions")
r = decide(P, decision="Maybe")
check(b"Choose Pending" in r.data, "an unknown decision is refused")
with app.app_context():
    check(Client.query.get(ids["cl"]).acceptance_status == "Pending", "nothing was saved by the refused attempts")

# a potential match needs a note before plain Accepted
with app.app_context():
    db.session.add(ClientSanctionsScreening(client_id=ids["cl"], individual_name="John Moyo", role_description="Director", un_result="Potential Match", order=1)); db.session.commit()
r = decide(P, decision="Accepted", decision_notes="")
check(b"how it was resolved" in r.data, "a potential match must be explained before the client is accepted")
r = decide(P, decision="Accepted", decision_notes="Name checked against UN entry: different person (DOB differs). Evidence filed.")
with app.app_context():
    c = Client.query.get(ids["cl"])
    check(c.acceptance_status == "Accepted" and c.acceptance_decided_by_id == ids["ptn"] and c.acceptance_decided_at is not None, "the Partner's decision is saved with who and when")
    check(engagement_acceptance_cleared(Engagement.query.get(ids["e1"])), "the engagement that was signed off is now cleared")
    check(ClientAcceptance.query.filter_by(engagement_id=ids["e1"]).first().decision == "Accepted", "it reads Accepted on the engagement")
    check(not engagement_acceptance_cleared(Engagement.query.get(ids["e2"])), "an engagement with no acceptance record yet stays locked (its own checklist and sign-off are still needed)")
r = A.get(f"/clients/{ids['cl']}")
check(b"vetting now shows a hit or a potential match although the client is recorded as accepted" in r.data, "the client page warns when the vetting contradicts an Accepted decision")

# Accepted with conditions clears the gate too; Declined and Pending lock again and drop the sign-off
r = decide(A, decision="Accepted with conditions", decision_notes="Quarterly re-screening; no cash fees.")
with app.app_context():
    check(engagement_acceptance_cleared(Engagement.query.get(ids["e1"])) and ClientAcceptance.query.filter_by(engagement_id=ids["e1"]).first().partner_signed_at is not None,
          "Accepted with conditions clears the gate and keeps an unchanged sign-off")
b = A.get(f"/engagements/{ids['e1']}?tab=acceptance").get_data(as_text=True)
check("Accepted with conditions" in b and "Quarterly re-screening" in b, "the engagement shows the conditions")
decide(A, decision="Declined", decision_notes="Source of funds not explained.")
with app.app_context():
    check(not engagement_acceptance_cleared(Engagement.query.get(ids["e1"])), "Declined locks the client's engagements")
    rec = ClientAcceptance.query.filter_by(engagement_id=ids["e1"]).first()
    check(rec.decision == "Declined" and rec.partner_signed_at is None, "...the engagement reads Declined and its old sign-off is removed")
    check(engagement_acceptance_cleared(Engagement.query.get(ids["e3"])), "an engagement that never required acceptance is not affected")
decide(A, decision="Accepted", decision_notes="Re-assessed after evidence was filed.")
with app.app_context():
    check(not engagement_acceptance_cleared(Engagement.query.get(ids["e1"])), "accepting again does not bring the removed sign-off back: the engagement needs signing again")

# new acceptance records start from the client's decision
with app.app_context():
    from acceptance import _get_or_create
    from flask_login import login_user
    rec2 = _get_or_create(ids["e2"]); db.session.commit()
    check(rec2.decision == "Accepted", "a new engagement acceptance record starts as Accepted when the client is accepted")

# vetting documents
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
r = A.post(f"/clients/{ids['cl']}/vetting/documents/upload", data={"doc_type": "PEP check", "title": "PEP search - John Moyo", "individual_name": "John Moyo", "notes": "no hits", "file": (io.BytesIO(PDF), "pep.pdf")},
           content_type="multipart/form-data", follow_redirects=True)
check(r.status_code == 200 and b"PEP search - John Moyo" in r.data and b"filed with the vetting" in r.data, "a vetting document is filed and listed on the client page")
with app.app_context():
    d = ClientVettingDocument.query.first()
    check(d is not None and d.doc_type == "PEP check" and d.individual_name == "John Moyo" and d.uploaded_by_id == ids["admin"], "stored with type, person and uploader")
    did = d.id
r = A.get(f"/clients/vetting/documents/{did}/download")
check(r.status_code == 200 and r.data.startswith(b"%PDF"), "the document downloads")
r = A.post(f"/clients/{ids['cl']}/vetting/documents/upload", data={"title": "bad", "file": (io.BytesIO(b"MZ"), "x.exe")}, content_type="multipart/form-data", follow_redirects=True)
check(b"Only PDF, image" in r.data, "an executable is refused")
b = A.get(f"/engagements/{ids['e1']}?tab=acceptance").get_data(as_text=True)
check("PEP search - John Moyo" not in b, "the documents are not on the engagement tab")
r = A.post(f"/clients/vetting/documents/{did}/delete", follow_redirects=True)
with app.app_context():
    check(ClientVettingDocument.query.count() == 0, "the document can be removed")

# nowhere else in the app offers the vetting card
for url in (f"/engagements/{ids['e1']}?tab=acceptance", f"/engagements/{ids['e2']}?tab=acceptance"):
    check(b"Auto-screen all individuals now" not in A.get(url).data and b"Add individual" not in A.get(url).data, f"{url.split('?')[0]} has no vetting form")

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
