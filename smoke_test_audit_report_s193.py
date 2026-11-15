"""The audit report ends with the Section 193 (COBE Act) statement, above the signature block: year from the period end, headings bold,
editable and saved on the Finalisation tab, left out of a review report or when cleared, standard wording for a report saved before this existed."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datetime as dtm
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

S1 = "In fulfilment of the requirements of Section 193 of the Companies and Other Business Entities Act (Chapter 24:31) (“the Act”), we report to the shareholders as follows:"
S2 = ("In our opinion, the 2025 financial statements of the Company are properly drawn up in accordance with the Act so as to give a true and fair view "
      "of the state of the Company’s affairs as at the date of its financial statements for its financial year ended on that date.")
S3 = "We have no matters to report in respect of the Section 193(2) requirements of the Act."

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Broxmen Investment (Private) Limited", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY2025 audit", type="Audit", partner_id=admin.id, manager_id=admin.id, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs", acceptance_required=False)
    db.session.add(e); db.session.commit(); EID, UID = e.id, admin.id
    lines = [TrialBalanceLine(account_name="Cash", fs_category="cash", current_debit=5000, prior_debit=100),
             TrialBalanceLine(account_name="Share capital", fs_category="share_capital", current_credit=5000, prior_credit=100)]
    st = fin.build_all_statements(lines, [], reporting_framework="ifrs", cash_flow_method="indirect")

    # the wording itself
    t = fin.default_statutory_report_text(dtm.date(2025, 12, 31)).split("\n")
    check(t == [S1, "Section 193(1)(a)", S2, "Section 193(2)", S3], "default wording is the firm's text, year 2025: %s" % t[2][:40])
    check("2026 financial statements" in fin.default_statutory_report_text(dtm.date(2026, 10, 31)), "the year follows the period end")
    check("[year]" in fin.default_statutory_report_text(None), "no period end -> [year], not a crash")
    d = fin.default_audit_opinion_paragraphs("audit", "unmodified", "X", dtm.date(2025, 12, 31), "IFRS")
    check(d["statutory_paragraph"].startswith("In fulfilment") and fin.default_audit_opinion_paragraphs("review", "unmodified", "X", dtm.date(2025, 12, 31), "IFRS")["statutory_paragraph"] == "", "audit gets the statement, a review does not")

    # in the Word report: after the auditor's responsibility, before the signature
    class AO:
        report_basis = "audit"; modification = "unmodified"; opinion_paragraph = "OPINION"; basis_paragraph = "BASIS"
        management_responsibility_paragraph = "DIRECTORS"; auditor_responsibility_paragraph = "AUDITOR RESP"
        report_date = dtm.date(2026, 6, 24); statutory_paragraph = None
    def paras(ao):
        return DocxDocument(wp.build_financial_statements_docx(e, st, None, audit_opinion=ao)).paragraphs
    P = paras(AO()); texts = [p.text for p in P]
    check(S1 in texts and S2 in texts and S3 in texts, "all three statements are in the report")
    iresp, i1, isig = texts.index("AUDITOR RESP"), texts.index(S1), next(k for k, x in enumerate(texts) if x.startswith("…"))
    check(iresp < i1 < texts.index(S2) < texts.index(S3) < isig, "order: auditor's responsibility, Section 193, then the signature block")
    h = [p for p in P if p.text in ("Section 193(1)(a)", "Section 193(2)")]
    check(len(h) == 2 and all(p.runs[0].bold for p in h), "the two Section headings are bold")
    # edited wording is used; cleared wording leaves it out; review report has none
    ao = AO(); ao.statutory_paragraph = "Section 193(2)\nWe report the following matter: late filing."
    tx = [p.text for p in paras(ao)]
    check("We report the following matter: late filing." in tx and S3 not in tx and S1 not in tx, "edited wording replaces the standard text")
    ao = AO(); ao.statutory_paragraph = ""
    tx = [p.text for p in paras(ao)]
    check(not any("Section 193" in x for x in tx), "cleared on purpose -> left out")
    ao = AO(); ao.report_basis = "review"
    tx = [p.text for p in paras(ao)]
    check(not any("Section 193" in x for x in tx), "a review report has no Section 193 statement")

# the Finalisation tab saves and shows it
c = app.test_client()
with c.session_transaction() as s: s["_user_id"] = str(UID); s["_fresh"] = True
r = c.get(f"/engagements/{EID}?tab=finalisation")
html = r.data.decode()
check('name="statutory_paragraph"' in html and "In fulfilment of the requirements of Section 193" in html and "the 2025 financial statements" in html, "the tab offers the statement with the standard wording")
form = {"report_basis": "audit", "modification": "unmodified", "opinion_paragraph": "O", "basis_paragraph": "B",
        "management_responsibility_paragraph": "M", "auditor_responsibility_paragraph": "A", "statutory_paragraph": "Section 193(2)\nWe have one matter to report.", "report_date": "2026-06-24"}
r = c.post(f"/engagements/{EID}/audit-opinion/save", data=form, follow_redirects=True)
with app.app_context():
    rec = AuditOpinion.query.filter_by(engagement_id=EID).first()
    check(rec is not None and rec.statutory_paragraph == "Section 193(2)\nWe have one matter to report.", "Save stores the edited statement")
r = c.post(f"/engagements/{EID}/audit-opinion/regenerate-wording", data={"report_basis": "audit", "modification": "unmodified"}, follow_redirects=True)
with app.app_context():
    rec = AuditOpinion.query.filter_by(engagement_id=EID).first()
    check(rec.statutory_paragraph.startswith("In fulfilment of the requirements of Section 193"), "Regenerate wording restores the standard statement")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
