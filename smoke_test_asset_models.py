"""PPE and intangible assets on the revaluation model (Broxmen): the accounting policies and the basis of preparation say so instead of cost,
worked out from the valuation journals or set on the Finalisation tab."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
import datetime as dtm
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

class AO:  # stand-ins for journal rows
    pass
from types import SimpleNamespace as N
def AL(cat, d=0, c=0, name=""): return N(fs_category=cat, debit=d, credit=c, account_name=name, account_code="")
def ADJ(lines): return N(lines=lines, source_key="", reference="J1", applies_to="current", carry_to_current=True, is_balanced=True, description="valuation")
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Broxmen Investment (Private) Limited", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY2026", type="Audit", partner_id=aid, manager_id=aid, period_end=dtm.date(2026, 10, 31), reporting_framework="full_ifrs", acceptance_required=False)
    db.session.add(e); db.session.commit(); eid = e.id
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for code, nm, cat, cd, cc in [("1641", "Right-of-Use Assets", "ppe", 150000, 0), ("1701.03", "Mining Rights", "intangible_assets", 1500, 0), ("4001", "Paid-in Capital", "share_capital", 0, 151500)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=code, account_name=nm, fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=cd, prior_credit=cc))
    db.session.commit()
    lines = list(TrialBalance.query.filter_by(engagement_id=eid).first().lines)
    def run(adjs=None, models=None):
        return fin.build_all_statements(lines, adjs or [], reporting_framework="full_ifrs", asset_models=models)
    notes = lambda st: {n["title"]: n for n in st["notes"]}
    # no valuation: cost model, cost wording, historical cost basis
    st = run()
    check(st["asset_models"] == {"ppe": "cost", "intangible_assets": "cost"}, "no valuation: both on the cost model")
    check("stated at cost" in notes(st)["Property, plant and equipment"]["policy"] and "carried at cost" in notes(st)["Intangible assets"]["policy"], "cost wording")
    check("except for" not in fin.default_basis_of_preparation("full_ifrs", st["asset_models"]), "basis of preparation is plain historical cost")
    # both valued by journal
    J = [ADJ([AL("ppe", d=40000, name="Right-of-Use Assets"), AL("oci_items", c=40000, name="Revaluation surplus")]),
         ADJ([AL("intangible_assets", d=817978, name="Mining Rights"), AL("oci_items", c=817978, name="Revaluation surplus - mining rights")])]
    st = run(J); n = notes(st)
    check(st["asset_models"] == {"ppe": "revaluation", "intangible_assets": "revaluation"}, "valuation journals on both lines: revaluation model on both")
    check("revalued amounts" in n["Property, plant and equipment"]["policy"] and "stated at cost" not in n["Property, plant and equipment"]["policy"], "PPE policy: revalued amounts, not cost")
    check("revalued amounts" in n["Intangible assets"]["policy"] and "carried at cost" not in n["Intangible assets"]["policy"] and "amortised" in n["Intangible assets"]["policy"], "intangible policy: revalued amounts, keeps the amortisation sentence")
    b = fin.default_basis_of_preparation("full_ifrs", st["asset_models"])
    check("except for property, plant and equipment and intangible assets, which are measured at revalued amounts" in b, "basis of preparation names both lines: " + b[-330:-150])
    # PPE policy built from the Depreciation policy keeps its table of bases
    pol = fin.ppe_accounting_policy_text([{"name": "Buildings", "depreciation_method": "Straight-line", "rate_percent": 2, "useful_life_years": 50}])
    txt, parts = fin._revalued_policy("ppe", str(pol), pol.parts)
    check(txt.startswith("Property, plant and equipment are measured at revalued amounts") and "Buildings" in txt and parts["paragraphs"][0].startswith("Property, plant and equipment are measured at revalued"), "with a depreciation policy table: the first paragraph is the revaluation wording, the bases stay")
    check(parts["table"]["rows"][0][0] == "Buildings" and parts["paragraphs"][1:] == pol.parts["paragraphs"][1:] and pol.parts["paragraphs"][0].startswith("Property, plant and equipment are stated at cost"), "the table of bases and the depreciation paragraph are intact, and the original is not changed")
    # only one line valued
    st = run(J[1:]); check(st["asset_models"] == {"ppe": "cost", "intangible_assets": "revaluation"}, "only Mining Rights valued: PPE stays on cost")
    check("property, plant and equipment" not in fin.default_basis_of_preparation("full_ifrs", st["asset_models"]).split("except for")[1].split("which")[0], "basis names only intangible assets")
    # explicit choice wins both ways
    st = run([], {"ppe": "revaluation", "intangible_assets": "revaluation"}); check(st["asset_models"]["ppe"] == "revaluation" and "revalued amounts" in notes(st)["Intangible assets"]["policy"], "chosen revaluation model without a journal")
    st = run(J, {"ppe": "cost", "intangible_assets": ""}); check(st["asset_models"] == {"ppe": "cost", "intangible_assets": "revaluation"}, "chosen cost model overrides; blank still works it out")
    # Word: policy wording and default basis text
    st = run(J)
    d = DocxDocument(wp.build_financial_statements_docx(Engagement.query.get(eid), st, None))
    text = "\n".join(p.text for p in d.paragraphs)
    check("measured at revalued amounts" in text and "which are measured at revalued amounts" in text and "carried at cost less accumulated amortisation" not in text, "the Word notes carry the revaluation wording and the basis of preparation sentence")
# Right-of-use assets are a significant accounting policy of their own
with app.app_context():
    st = run(); rou = st["extra_policies"]
    check(len(rou) == 1 and rou[0]["title"] == "Right-of-use assets and leases" and "lease liability" in rou[0]["text"], "a right-of-use account in the trial balance gives a Right-of-use assets and leases policy")
    check("revalued amounts" not in rou[0]["text"], "on the cost model the policy does not mention revaluation")
    rv = run(J)["extra_policies"][0]["text"]
    check("measured at revalued amounts" in rv, "with PPE on the revaluation model the policy says right-of-use assets follow the revaluation model")
    check(fin._is_right_of_use_name("Right\u2011of\u2011Use Assets") and fin._is_right_of_use_name("Right-of-use asset - land") and fin._is_right_of_use_name("ROU Assets") and not fin._is_right_of_use_name("Rights issue reserve") and not fin._is_right_of_use_name("Mining Rights"), "name recognition: any hyphen style, ROU; not 'Mining Rights'")
    plain = [TrialBalanceLine(account_code="1", account_name="Motor vehicles", fs_category="ppe", current_debit=10, current_credit=0, prior_debit=10, prior_credit=0)]
    check(fin.extra_accounting_policies(plain) == [], "no right-of-use account: no such policy")
    stp = fin.build_all_statements(plain + [TrialBalanceLine(account_code="2", account_name="Capital", fs_category="share_capital", current_debit=0, current_credit=10, prior_debit=0, prior_credit=10)], [], reporting_framework="full_ifrs")
    check(stp["extra_policies"] == [], "and none in the statements")
    d = DocxDocument(wp.build_financial_statements_docx(Engagement.query.get(eid), run(J), None))
    wt = "\n".join(p.text for p in d.paragraphs)
    check("Right-of-use assets and leases. " in wt and "lease liability" in wt, "the Word pack prints the policy under Significant accounting policies")
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(aid); s["_fresh"] = True
page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
check("Right-of-use assets and leases." in page, "the Finalisation tab shows the right-of-use policy among the significant accounting policies")
check('name="ppe_model"' in page and 'name="intangible_model"' in page, "the Finalisation tab offers the two measurement model choices")
r = c.post(f"/engagements/{eid}/financial-statements/save", data={"ppe_model": "revaluation", "intangible_model": "cost", "basis_of_preparation": ""}, follow_redirects=True)
with app.app_context():
    fs = FinancialStatements.query.filter_by(engagement_id=eid).first()
    check(fs is not None and fs.ppe_model == "revaluation" and fs.intangible_model == "cost", "saved: PPE on revaluation; intangible 'cost' kept as chosen: %s/%s" % (fs and fs.ppe_model, fs and fs.intangible_model))
print("\nALL PASSED" if not fails else "\nFAILED: %d" % len(fails)); sys.exit(1 if fails else 0)
