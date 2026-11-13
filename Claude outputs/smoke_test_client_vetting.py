"""Sanctions & AML vetting lives on the client: migration from per-engagement rows, client page, acceptance summary, routes, access."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import datetime
from app import create_app, _migrate_screenings_to_client
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    db.session.add(partner); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id)
    other_cl = Client(name="Other Co", created_by_id=admin.id)
    db.session.add_all([cl, other_cl]); db.session.commit()
    e1 = Engagement(client_id=cl.id, title="FY25 audit", type="Audit", partner_id=partner.id, acceptance_required=False)
    e2 = Engagement(client_id=cl.id, title="Forensic job", type="Investigative Engagement", partner_id=partner.id, acceptance_required=False)
    e3 = Engagement(client_id=other_cl.id, title="Other audit", type="Audit", partner_id=partner.id, acceptance_required=False)
    db.session.add_all([e1, e2, e3]); db.session.commit()
    a1 = ClientAcceptance(engagement_id=e1.id); a2 = ClientAcceptance(engagement_id=e2.id); a3 = ClientAcceptance(engagement_id=e3.id)
    db.session.add_all([a1, a2, a3]); db.session.commit()
    # old per-engagement rows: John on two engagements (different results), Mary only on one, Tom on another client
    j1 = SanctionsScreening(client_acceptance_id=a1.id, individual_name="John Moyo", role_description="Director", un_result="Clear", ofac_result="Clear", eu_result="Clear", rbz_result="Clear", fiu_result="Clear", notes="clean", order=1, screened_at=datetime(2026, 1, 5))
    j2 = SanctionsScreening(client_acceptance_id=a2.id, individual_name="  john   moyo ", role_description="Suspect", un_result="Potential Match", ofac_result="Not Checked", eu_result="Clear", notes="possible UN entry", order=1, screened_at=datetime(2026, 6, 5))
    m1 = SanctionsScreening(client_acceptance_id=a1.id, individual_name="Mary Ncube", role_description="Beneficial Owner", un_result="Clear", order=2)
    t1 = SanctionsScreening(client_acceptance_id=a3.id, individual_name="Tom Dube", role_description="Director", order=1)
    db.session.add_all([j1, j2, m1, t1]); db.session.commit()
    db.session.add(AcceptanceFlagReview(client_acceptance_id=a2.id, flag_key=f"screening:{j2.id}", status="Consider", note="looked"))
    db.session.commit()
    ids = dict(cl=cl.id, other=other_cl.id, e1=e1.id, e2=e2.id, e3=e3.id, partner=partner.id, admin=admin.id, j1=j1.id, j2=j2.id)

    _migrate_screenings_to_client()
    rows = ClientSanctionsScreening.query.filter_by(client_id=cl.id).order_by(ClientSanctionsScreening.order).all()
    check(len(rows) == 2 and [r.individual_name for r in rows] == ['John Moyo', 'Mary Ncube'], f"the same person on two engagements becomes one client record with a tidy name ({[r.individual_name for r in rows]})")
    john = next(r for r in rows if "moyo" in r.individual_name.lower())
    check(john.un_result == "Potential Match" and john.eu_result == "Clear" and john.ofac_result == "Clear" and john.rbz_result == "Clear", "merged record keeps the most severe result per list")
    check("possible UN entry" in (john.notes or ""), "notes survive the merge")
    check(ClientSanctionsScreening.query.filter_by(client_id=other_cl.id).count() == 1, "another client's person stays with that client")
    check(all(s.migrated_to_client for s in SanctionsScreening.query.all()), "old rows are flagged as migrated, not deleted")
    before = ClientSanctionsScreening.query.count()
    _migrate_screenings_to_client()
    check(ClientSanctionsScreening.query.count() == before, "the migration is idempotent")
    ClientSanctionsScreening.query.filter_by(client_id=cl.id).delete(); db.session.commit()
    _migrate_screenings_to_client()
    check(ClientSanctionsScreening.query.filter_by(client_id=cl.id).count() == 0, "deleting records on the client page is not undone by a restart")
    # put them back for the web checks
    db.session.add_all([ClientSanctionsScreening(client_id=cl.id, individual_name="John Moyo", role_description="Director", un_result="Potential Match", order=1),
                        ClientSanctionsScreening(client_id=cl.id, individual_name="Mary Ncube", role_description="Beneficial Owner", order=2)])
    db.session.commit()
    a2 = ClientAcceptance.query.get(a2.id)
    check(a2.screening_assessment["level"] == "warning" and a2.screening_assessment["potential"] == 1, f"acceptance reads the client's list {a2.screening_assessment['label']}")
    check(any(i["key"].startswith("screening:") for i in a2.flagged_issues), "a potential match on the client list is a flagged issue on each engagement's acceptance")
    john_id = ClientSanctionsScreening.query.filter_by(client_id=cl.id, individual_name="John Moyo").first().id

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

c = login(ids["admin"])
r = c.get(f"/clients/{ids['cl']}")
check(r.status_code == 200 and b'id="vetting"' in r.data and b"Sanctions &amp; AML Vetting" in r.data and b"John Moyo" in r.data, "client page shows the vetting card with its list")
check(b"Auto-screen all individuals now" in r.data and b"Refresh UN/OFAC/EU lists now" in r.data, "client page offers auto-screening and the list refresh")
r = c.get(f"/engagements/{ids['e1']}?tab=acceptance")
check(r.status_code == 200 and b"Sanctions &amp; AML Vetting</h3>" not in r.data and b"#client-decision" in r.data, "acceptance tab no longer carries the vetting card; it links to the decision on the client page")
check(b"Auto-screen all individuals now" not in r.data and b"Add individual" not in r.data, "acceptance tab no longer holds the vetting form")
r = c.get(f"/engagements/{ids['e2']}?tab=acceptance")
check(r.status_code == 200 and b"Sanctions &amp; AML Vetting</h3>" not in r.data and b"Decision to accept the client" in r.data, "the investigative engagement shows the client's decision only")
# add / duplicate / update / delete
r = c.post(f"/clients/{ids['cl']}/vetting/add", data={"individual_name": "Peter Sibanda", "role_description": "Authorised signatory"}, follow_redirects=True)
check(b"Peter Sibanda" in r.data, "add an individual")
r = c.post(f"/clients/{ids['cl']}/vetting/add", data={"individual_name": "peter  sibanda"}, follow_redirects=True)
with app.app_context():
    check(ClientSanctionsScreening.query.filter_by(client_id=ids["cl"]).count() == 3 and b"already on" in r.data, "a duplicate name is refused")
    pid = ClientSanctionsScreening.query.filter_by(client_id=ids["cl"], individual_name="Peter Sibanda").first().id
r = c.post(f"/clients/vetting/{pid}/update", data={"un_result": "Clear", "ofac_result": "Clear", "eu_result": "Clear", "rbz_result": "Clear", "fiu_result": "Clear", "notes": "all clear", "role_description": "Signatory"}, follow_redirects=True)
with app.app_context():
    p = ClientSanctionsScreening.query.get(pid)
    check(p.overall_status == "Clear" and p.notes == "all clear" and p.screened_by_id == ids["admin"], "manual result saved and attributed")
r = c.post(f"/clients/vetting/{pid}/auto-screen", follow_redirects=True)
check(r.status_code == 200, "auto-screen one individual runs")
r = c.post(f"/clients/{ids['cl']}/vetting/auto-screen-all", follow_redirects=True)
with app.app_context():
    check(all(s.auto_screened_at for s in ClientSanctionsScreening.query.filter_by(client_id=ids["cl"]).all()), "auto-screen all stamps every individual")
r = c.post(f"/clients/vetting/{pid}/delete", follow_redirects=True)
with app.app_context():
    check(ClientSanctionsScreening.query.get(pid) is None, "delete a record")
# key people quick pick
with app.app_context():
    kp = ClientKeyPerson(client_id=ids["cl"], full_name="Grace Phiri", role="Director", status="Confirmed")
    sg = ClientKeyPerson(client_id=ids["cl"], full_name="Unreviewed Person", role="Director", status="Suggested")
    db.session.add_all([kp, sg]); db.session.commit(); kpid, sgid = kp.id, sg.id
r = c.get(f"/clients/{ids['cl']}")
check(b"Grace Phiri (Director)" in r.data and b"Unreviewed Person (Director)" not in r.data.split(b'id="vetting"')[1].split(b"Understanding the Entity")[0], "only confirmed people are offered for vetting")
r = c.post(f"/clients/{ids['cl']}/vetting/add-from-key-person", data={"key_person_id": kpid}, follow_redirects=True)
check(b"Grace Phiri" in r.data, "add from the Directors & Shareholders list")
r = c.post(f"/clients/{ids['cl']}/vetting/add-from-key-person", data={"key_person_id": sgid}, follow_redirects=True)
check(b"not been confirmed" in r.data, "an unconfirmed person is refused")
# a person from another client cannot be added
with app.app_context():
    ok = ClientKeyPerson(client_id=ids["other"], full_name="Elsewhere", role="Director", status="Confirmed"); db.session.add(ok); db.session.commit(); okid = ok.id
r = c.post(f"/clients/{ids['cl']}/vetting/add-from-key-person", data={"key_person_id": okid})
check(r.status_code == 403, "another client's key person is refused")
# old engagement-level URLs are gone
r = c.post(f"/engagements/{ids['e1']}/acceptance/screening/add", data={"individual_name": "x"})
check(r.status_code == 404 or r.status_code == 405, f"old per-engagement route removed ({r.status_code})")
# access: a client outside the user's scope is refused
with app.app_context():
    scoped = User(username="scp", name="Scoped", role="staff"); scoped.set_password("x"); db.session.add(scoped); db.session.commit(); sid = scoped.id
    from models import client_scope_ids
    scope = client_scope_ids(scoped)
    print("scope for plain staff:", scope)
c2 = login(sid)
if scope is not None and ids["cl"] not in scope:
    r = c2.post(f"/clients/{ids['cl']}/vetting/add", data={"individual_name": "Intruder"})
    check(r.status_code == 403, "staff outside the client's scope cannot change its vetting")
print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
