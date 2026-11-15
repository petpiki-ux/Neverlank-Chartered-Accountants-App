"""Prior year = the entity's first year of business: the comparative cash flow is worked out from the trial balance; otherwise it stays
manual. Also the client's "Nature of business and principal activities" reaching Note 1 and the Client Details page."""
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
import datetime as dtm
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
import cf_prior
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

# prior (first) year: cash 50,000 = shares 30,000 + loan 5,000 + payables 10,000 + profit 20,000 - PPE 10,000 - receivables 5,000
ROWS = [("Cash", "cash", 50000, 0, 0, 0, 0, 0), ("Property", "ppe", 0, 0, 0, 0, 0, 0),
        ("Receivables", "trade_receivables", 0, 0, 0, 0, 0, 0)]
LINES = [
    ("Cash", "cash", 80000, 0, 50000, 0), ("Property", "ppe", 18000, 0, 10000, 0), ("Receivables", "trade_receivables", 12000, 0, 5000, 0),
    ("Admin expenses", "admin_expenses", 30000, 0, 20000, 0),
    ("Share capital", "share_capital", 0, 30000, 0, 30000), ("Borrowings", "long_term_borrowings", 0, 5000, 0, 5000),
    ("Payables", "trade_payables", 0, 15000, 0, 10000), ("Sales", "revenue", 0, 90000, 0, 40000),
]
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Broxmen Investment (Private) Limited", created_by_id=aid, industry="Mining",
                principal_activities="Holder of a coal mining claim in Binga.\nOwner of land in Chegutu.")
    db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25", type="Audit", partner_id=aid, manager_id=aid, period_end=dtm.date(2025, 12, 31),
                   reporting_framework="ifrs_for_smes", acceptance_required=False)
    db.session.add(e); db.session.commit(); eid, cid = e.id, cl.id
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for nm, cat, dr, cr, pdr, pcr in LINES:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=nm, fs_category=cat, current_debit=dr, current_credit=cr, prior_debit=pdr, prior_credit=pcr))
    db.session.commit()
    lines = TrialBalance.query.get(tb.id).lines
    def stm():
        return fin.build_all_statements(lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect")

    # 1. not first year: nothing is invented
    s = stm(); cf_prior.attach_prior_cash_flow(s, e)
    check(s["cf"]["prior"] is None, "prior year not flagged as first year and nothing typed: no comparative (manual entry as before)")
    s = stm(); cf_prior.attach_prior_cash_flow(s, e, editable=True)
    check(s["cf"]["prior"]["empty"] and not s["cf"]["prior"]["auto"], "the screen offers empty boxes to type into")

    # 2. first year: worked out
    e.prior_year_first_year = True; db.session.commit()
    s = stm(); cf_prior.attach_prior_cash_flow(s, e)
    p = s["cf"]["prior"]
    check(p is not None and p["auto"], "first year of business: the comparative is prepared automatically")
    check(abs(p["net_operating"] - 25000) < 0.5, "prior operating cash flow = profit 20,000 - receivables 5,000 + payables 10,000 = 25,000 (%s)" % p["net_operating"])
    check(abs(p["net_investing"] + 10000) < 0.5 and abs(p["net_financing"] - 35000) < 0.5, "investing -10,000, financing +35,000 (%s / %s)" % (p["net_investing"], p["net_financing"]))
    check(abs(p["cash_open"]) < 0.01 and abs(p["cash_close_computed"] - 50000) < 0.5 and abs(p["variance"]) < 0.5, "cash at the start of the prior year is nil; it ends at 50,000 and agrees to the trial balance")
    pbt = next(r for r in s["cf"]["operating_rows"] if r["label"] == "Profit before tax")
    check(abs(pbt["prior"] - 20000) < 0.5 and pbt.get("linked"), "profit before tax is the prior-year income statement's, 20,000")
    rec = next(r for r in s["cf"]["operating_rows"] if "receivables" in r["label"])
    check(abs(rec["prior"] + 5000) < 0.5, "increase in receivables in the prior year = -5,000")

    # 3. typed figures win over the automatic ones
    db.session.add(CashFlowPriorLine(engagement_id=eid, section="operating", label="Increase/(decrease) in trade and other payables", amount=7000, sort_order=0))
    db.session.commit()
    s = stm(); cf_prior.attach_prior_cash_flow(s, e)
    check(s["cf"]["prior"] is not None and not s["cf"]["prior"]["auto"], "figures typed by hand are used, not overwritten by the automatic ones")
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete(); db.session.commit()

    # 4. the Word pack carries two columns
    st = stm()
    d = DocxDocument(wp.build_financial_statements_docx(e, st, None))
    txt = "\n".join(p_.text for p_ in d.paragraphs) + "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    check("25,000" in txt, "the Word statement of cash flows shows the prior-year operating cash flow")

    # 5. Note 1 and Client Details
    note1 = fin.general_information_note(Client.query.get(cid), Engagement.query.get(eid))
    check("Nature of business and principal activities: Holder of a coal mining claim in Binga; Owner of land in Chegutu." in note1, "Note 1 prints the client's nature of business and principal activities: %s" % note1)
    check("principal activity is mining" not in note1, "...instead of the industry sentence")
    cl2 = Client(name="Plain Co", created_by_id=aid, industry="Retail"); db.session.add(cl2); db.session.commit()
    n2 = fin.general_information_note(cl2, e)
    check("principal activity is retail" in n2, "no text typed -> the industry sentence as before")
    check("Nature of business and principal activities" in "\n".join(p_.text for p_ in d.paragraphs), "the Client Details page of the pack has the row")

# 5b. a first year with depreciation, finance costs and tax still ties to the cash in the trial balance
with app.app_context():
    e2 = Engagement(client_id=cid, title="FY25 b", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes", prior_year_first_year=True)
    db.session.add(e2); db.session.commit()
    tb2 = TrialBalance(engagement_id=e2.id); db.session.add(tb2); db.session.commit()
    for n, c, d, cr in [("Cash", "cash", 47, 0), ("PPE", "ppe", 20, 0), ("Sales", "revenue", 0, 60), ("Admin", "admin_expenses", 20, 0),
                        ("Depreciation", "depreciation_amortisation", 5, 0), ("Interest", "finance_costs", 3, 0), ("Tax", "income_tax_expense", 4, 0),
                        ("Share", "share_capital", 0, 10), ("Loan", "long_term_borrowings", 0, 20), ("Tax payable", "current_tax_payable", 0, 4), ("Payables", "trade_payables", 0, 5)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb2.id, account_name=n, fs_category=c, current_debit=d, current_credit=cr, prior_debit=d, prior_credit=cr))
    db.session.commit()
    s2 = fin.build_all_statements(TrialBalance.query.get(tb2.id).lines, [], reporting_framework="ifrs_for_smes")
    cf_prior.attach_prior_cash_flow(s2, e2)
    p2 = s2["cf"]["prior"]
    check(abs(p2["variance"]) < 0.5 and abs(p2["cash_close_computed"] - 47) < 0.5, "with depreciation, finance costs and tax the automatic comparative still agrees to the cash in the trial balance (variance %s)" % p2["variance"])
    dep_rows = [r for r in s2["cf"]["operating_rows"] if "depreciation" in r["label"].lower()]
    check(all(not r.get("prior") for r in dep_rows), "depreciation is not added back twice (it stays a memo)")

# 6. the screens
c = app.test_client()
with c.session_transaction() as s_:
    s_["_user_id"] = str(aid); s_["_fresh"] = True
page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
check('name="prior_year_first_year"' in page and "Prepared automatically from the trial balance" in page, "the Finalisation tab shows the first-year box and the automatic note")
r = c.post(f"/engagements/{eid}/cash-flow-prior/first-year", data={}, follow_redirects=True)
with app.app_context():
    check(Engagement.query.get(eid).prior_year_first_year is False, "unticking saves")
r = c.post(f"/engagements/{eid}/cash-flow-prior/first-year", data={"prior_year_first_year": "on"}, follow_redirects=True)
with app.app_context():
    check(Engagement.query.get(eid).prior_year_first_year is True, "ticking saves")
r = c.get(f"/clients/{cid}/edit")
check(r.status_code == 200 and 'name="principal_activities"' in r.get_data(as_text=True) and "Holder of a coal mining claim" in r.get_data(as_text=True), "the client form has the box, filled")
r = c.post(f"/clients/{cid}/edit", data={"name": "Broxmen Investment (Private) Limited", "industry": "Mining", "principal_activities": "Coal mining claim holder"}, follow_redirects=True)
with app.app_context():
    check(Client.query.get(cid).principal_activities == "Coal mining claim holder", "the client form saves it")
r = c.get(f"/clients/{cid}")
check("Coal mining claim holder" in r.get_data(as_text=True), "the client page shows it")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
