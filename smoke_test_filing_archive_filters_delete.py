"""Filing Archive: quick views for PAYE / VAT / QPDs / ITF, and deleting a wrong filing in Tax Clearances, Returns and Schedules."""
import os, sys, tempfile, io, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    cl = Client(name="Filer Co", created_by_id=aid); db.session.add(cl); db.session.commit(); cid = cl.id
    e = Engagement(client_id=cid, title="Tax 2026", type=TAX_ADVISORY_TYPE, partner_id=aid, period_end=dtm.date(2026, 12, 31), acceptance_required=False); db.session.add(e); db.session.commit()
    staff = User(username="staff1", name="Staff One", email="s1@x.co", role="staff"); staff.set_password("pw")
    other = User(username="staff2", name="Staff Two", email="s2@x.co", role="staff"); other.set_password("pw")
    db.session.add_all([staff, other]); db.session.commit(); sid, oid = staff.id, other.id
    e.team_members.extend([staff, other]); db.session.commit()
    # a role that may NOT delete documents
    for role in ("staff",):
        p = Permission.query.filter_by(role=role, permission_key="delete_documents").first()
        if p is None:
            p = Permission(role=role, permission_key="delete_documents"); db.session.add(p)
        p.allowed = False
    db.session.commit()
    updir = app.config["UPLOAD_FOLDER"]; os.makedirs(updir, exist_ok=True)
    def mk(section, head, title, period, ref, by):
        fn = f"t_{section}_{title.replace(' ', '_')}.pdf"; open(os.path.join(updir, fn), "wb").write(b"%PDF-1.4 x")
        d = TaxAccountingFilingDocument(client_id=cid, section=section, tax_head=head, title=title, period_label=period, reference=ref,
                                        original_filename=fn, stored_filename=fn, uploaded_by_id=by)
        db.session.add(d); db.session.commit(); return d.id
    ids = {
        "paye": mk("return", "PAYE", "PAYE return May", "2026-05", "P1", aid),
        "vat": mk("return", "Value Added Tax (VAT)", "VAT 7 June", "2026-06", "V1", sid),
        "qpd": mk("return", "Provisional Tax (QPDs)", "QPD 3", "2026 Q3", "Q3", aid),
        "itf": mk("return", "Corporate Income Tax (CIT)", "Annual return", "2025", "C1", aid),
        "itf2": mk("schedule", None, "ITF12B working schedule", "2025", None, aid),
        "wht": mk("return", "Withholding Tax (WHT)", "WHT return", "2026-04", "W1", aid),
        "tcc": mk("tax_clearance", None, "TCC 2026", "2026", "TCC9", aid),
    }
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
admin = login(aid)
page = admin.get(f"/clients/{cid}").get_data(as_text=True)
for lab in ("All files (7)", "PAYE (1)", "VAT (1)", "QPDs (1)", "ITF (income tax) (2)", "Other taxes and untagged (2)"):
    check(lab in page, "chip: " + lab)
def titles(html): return [t for t in ("PAYE return May", "VAT 7 June", "QPD 3", "Annual return", "ITF12B working schedule", "WHT return", "TCC 2026") if t in html]
check(len(titles(page)) == 7, "no filter: every file shown")
check(titles(admin.get(f"/clients/{cid}?fa=paye").get_data(as_text=True)) == ["PAYE return May"], "PAYE view: only the PAYE file")
check(titles(admin.get(f"/clients/{cid}?fa=vat").get_data(as_text=True)) == ["VAT 7 June"], "VAT view: only the VAT file")
check(titles(admin.get(f"/clients/{cid}?fa=qpd").get_data(as_text=True)) == ["QPD 3"], "QPDs view: only the provisional tax file")
check(sorted(titles(admin.get(f"/clients/{cid}?fa=itf").get_data(as_text=True))) == ["Annual return", "ITF12B working schedule"], "ITF view: the income tax return and the file marked ITF")
check(sorted(titles(admin.get(f"/clients/{cid}?fa=other").get_data(as_text=True))) == ["TCC 2026", "WHT return"], "Other view: the rest")
check(len(titles(admin.get(f"/clients/{cid}?fa=bogus").get_data(as_text=True))) == 7, "unknown view falls back to all")
_pg_all = admin.get(f"/clients/{cid}").get_data(as_text=True)
_pg_vat = admin.get(f"/clients/{cid}?fa=vat").get_data(as_text=True)
check(_pg_all.count('<details class="fa-section"') == 3, "each of the three sections is collapsible")
check(_pg_all.count('class="fa-section"') == 3 and ' open>' not in _pg_all.split('id="filing-archive"')[1].split("Relevant Legislative")[0].replace('<details class="fa-filter"', ''), "sections are collapsed by default")
import re as _re
check(len(_re.findall(r'<details class="fa-section"[^>]*\sopen>', _pg_vat)) == 3 and len(_re.findall(r'<details class="fa-section"[^>]*\sopen>', _pg_all)) == 0, "sections open while a filter is applied, closed otherwise")
# deleting: staff without the permission can delete only their own filing
st = login(sid); ot = login(oid)
pg = st.get(f"/clients/{cid}").get_data(as_text=True)
check(f"/filing-archive/{ids['vat']}/delete" in pg and f"/filing-archive/{ids['paye']}/delete" not in pg, "a user without the permission sees Delete on their own filing only")
check(ot.post(f"/clients/filing-archive/{ids['vat']}/delete").status_code == 403, "another user without the permission cannot delete it")
check(st.post(f"/clients/filing-archive/{ids['paye']}/delete").status_code == 403, "nor can the uploader delete someone else's")
r = st.post(f"/clients/filing-archive/{ids['vat']}/delete", follow_redirects=True)
with app.app_context():
    check(TaxAccountingFilingDocument.query.get(ids["vat"]) is None and not os.path.exists(os.path.join(updir, "t_return_VAT_7_June.pdf")), "own wrong filing deleted, file removed from disk")
check("Deleted" in r.get_data(as_text=True), "confirmation shown")
# delete in all three sections as admin
for key in ("paye", "itf2", "tcc"):
    admin.post(f"/clients/filing-archive/{ids[key]}/delete")
with app.app_context():
    left = {d.id for d in TaxAccountingFilingDocument.query.filter_by(client_id=cid)}
    check(not ({ids["paye"], ids["itf2"], ids["tcc"]} & left) and {ids["qpd"], ids["itf"], ids["wht"]} <= left, "admin deleted a Return, a Schedule and a Tax Clearance; the others remain")
check("No returns for this tax" in admin.get(f"/clients/{cid}?fa=paye").get_data(as_text=True), "an empty view says so")
print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
