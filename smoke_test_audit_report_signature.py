"""Managing Partner e-signature on the auditor's report: signing rules, the signature in the Word pack, and automatic withdrawal when the report or the accounts change."""
import os, sys, tempfile, io, zipfile, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
import brand_docs
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
if not os.path.exists(brand_docs.SIGNATURE_PATH):          # a checkout without the private signature image: use a stand-in
    from PIL import Image
    _png = os.path.join(tempfile.mkdtemp(prefix="sig_"), "sig.png")
    Image.new("RGBA", (294, 178), (20, 20, 120, 255)).save(_png)
    brand_docs.SIGNATURE_PATH = _png
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
LINES = [("Cash", "cash", 100, 0), ("Share capital", "share_capital", 0, 60), ("Sales", "revenue", 0, 90), ("Expenses", "admin_expenses", 50, 0)]
with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    cl = Client(name="Signed Co", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes", acceptance_required=False)
    db.session.add(e); db.session.commit(); eid = e.id
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for n, c, d, cr in LINES:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=n, fs_category=c, current_debit=d, current_credit=cr))
    db.session.commit()
    ao = AuditOpinion(engagement_id=eid, report_basis="audit", modification="unmodified", opinion_paragraph="In our opinion the financial statements present fairly.",
                      basis_paragraph="b", management_responsibility_paragraph="m", auditor_responsibility_paragraph="a", report_date=None)
    db.session.add(ao); db.session.commit(); aoid = ao.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(aid); s["_fresh"] = True
def sign(): return c.post(f"/sign/audit_report/{aoid}", follow_redirects=True).get_data(as_text=True)
def state():
    with app.app_context():
        o = AuditOpinion.query.get(aoid); return brand_docs.signature_valid(o), o.mp_signed_at
def pack_text_and_images():
    with app.app_context():
        e = Engagement.query.get(eid); o = AuditOpinion.query.get(aoid); t = TrialBalance.query.filter_by(engagement_id=eid).first()
        st = fin.build_all_statements(t.lines, t.adjustments, reporting_framework="ifrs_for_smes")
        buf = wp.build_financial_statements_docx(e, st, None, audit_opinion=o) if "audit_opinion" in wp.build_financial_statements_docx.__code__.co_varnames else wp.build_financial_statements_docx(e, st, o)
        z = zipfile.ZipFile(buf); media = [n for n in z.namelist() if n.startswith("word/media/")]
        return media

html = sign()
check("Set the report date" in html and not state()[0], "refused while the report has no date")
with app.app_context():
    o = AuditOpinion.query.get(aoid); o.report_date = dtm.date(2026, 6, 24); db.session.commit()
with app.app_context():
    t = TrialBalance.query.filter_by(engagement_id=eid).first(); l = t.lines[0]; l.current_debit = 105; db.session.commit()
html = sign()
check("does not balance" in html and not state()[0], "refused while the final trial balance does not balance")
with app.app_context():
    t = TrialBalance.query.filter_by(engagement_id=eid).first(); l = t.lines[0]; l.current_debit = 100; db.session.commit()
before = pack_text_and_images()
html = sign()
check(state()[0] and "now carries the Managing Partner" in html, "signed once the date is set and the accounts balance")
after = pack_text_and_images()
check(len(after) == len(before) + 1, "the Word pack now carries the signature picture on the report (%d -> %d images)" % (len(before), len(after)))
fin_page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
check("Signed by the Managing Partner" in fin_page and "Remove signature" in fin_page, "the Finalisation tab shows the signed badge and Remove signature")
# change the report wording -> withdrawn
with app.app_context():
    o = AuditOpinion.query.get(aoid); o.opinion_paragraph = "Changed wording."; db.session.commit()
check(not state()[0] and len(pack_text_and_images()) == len(before), "changing the report wording withdraws the signature (blank line again)")
fin_page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
check("no longer shows" in fin_page, "the tab explains why it no longer shows")
sign()
check(state()[0], "signed again")
# change an account -> withdrawn
with app.app_context():
    t = TrialBalance.query.filter_by(engagement_id=eid).first()
    db.session.add(TrialBalanceLine(trial_balance_id=t.id, account_name="Bank charges", fs_category="admin_expenses", current_debit=5, current_credit=0))
    t.lines[0].current_debit = 95
    db.session.commit()
check(not state()[0], "changing a trial balance figure withdraws the signature")
# a non-admin cannot sign
with app.app_context():
    u = User(username="staffer", name="Staff", email="s@x.co", role="staff"); u.set_password("pw"); db.session.add(u); db.session.commit(); uid = u.id
c2 = app.test_client()
with c2.session_transaction() as s:
    s["_user_id"] = str(uid); s["_fresh"] = True
check(c2.post(f"/sign/audit_report/{aoid}").status_code == 403, "only the Managing Partner (Admin) can sign")
# remove
sign()
c.post(f"/sign/audit_report/{aoid}/remove")
check(state()[1] is None, "the signature can be removed")
print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
