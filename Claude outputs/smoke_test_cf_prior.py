"""Prior-year comparative cash flow captured by hand: seed, save, the two-column statement on screen and in Word, subtotals, the tie-out check."""
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
check(cf_prior.parse_amount("(1,200.50)") == -1200.5 and cf_prior.parse_amount("-3") == -3 and cf_prior.parse_amount("1,000") == 1000 and cf_prior.parse_amount("x") == 0, "amounts: commas, brackets, minus, rubbish")
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Penanel Trading (Private) Limited", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e); db.session.commit(); eid = e.id
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for nm, cat, dr, cr, pdr, pcr in [("Cash", "cash", 9000, 0, 3000, 0), ("Share capital", "share_capital", 0, 100, 0, 100), ("Loan", "long_term_borrowings", 0, 6000, 0, 2000),
                                      ("Retained earnings", "other_reserves", 0, 2900, 0, 900), ("Fixed assets", "ppe", 0, 0, 0, 0)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=nm, fs_category=cat, current_debit=dr, current_credit=cr, prior_debit=pdr, prior_credit=pcr))
    db.session.commit()
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(aid); s["_fresh"] = True
    page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    check("Prior-year comparative cash flow" in page and "current year)" in page, "capture card shown; statement is single-column until comparatives exist")
    r = c.post(f"/engagements/{eid}/cash-flow-prior/seed", follow_redirects=True)
    lines = cf_prior.lines_for(eid)
    secs = {l.section for l in lines}
    check(secs == {"cash_open", "operating", "investing", "financing"} and any(l.label == "Proceeds from/(repayment of) borrowings" for l in lines), "seeding adds this year's headings: %d lines" % len(lines))
    n = len(lines)
    c.post(f"/engagements/{eid}/cash-flow-prior/seed", follow_redirects=True)
    check(len(cf_prior.lines_for(eid)) == n, "seeding twice adds nothing new")
    # type in last year's figures: opening cash 1,000; borrowings +1,500; shares 0; a heading this year does not have
    keys, secs_, labels, amounts = [], [], [], []
    for l in cf_prior.lines_for(eid):
        keys.append(str(l.id)); secs_.append(l.section); labels.append(l.label)
        amounts.append({"Proceeds from/(repayment of) borrowings": "1,500", OPEN_LABEL if False else "Cash at beginning of prior year": "1,000", "Profit before tax": "400"}.get(l.label, "0"))
    keys.append(""); secs_.append("investing"); labels.append("Purchase of vehicles"); amounts.append("(300)")
    c.post(f"/engagements/{eid}/cash-flow-prior/save", data={"line_key": keys, "section": secs_, "label": labels, "amount": amounts}, follow_redirects=True)
    got = {l.label: l.amount for l in cf_prior.lines_for(eid)}
    check(got.get("Purchase of vehicles") == -300 and got.get("Proceeds from/(repayment of) borrowings") == 1500 and got.get("Cash at beginning of prior year") == 1000, "saved, brackets as negatives: %s" % {k: v for k, v in got.items() if v})
    page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    check("Purchase of vehicles" in page and "2025\nUSD" in page and "Statement of Cash Flows (Indirect method)" in page, "statement now has two year columns and the prior-only line")
    # the statement object
    trial = TrialBalance.query.filter_by(engagement_id=eid).first()
    st = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st, e)
    pr = st["cf"]["prior"]
    check(pr["net_operating"] == 400 and pr["net_investing"] == -300 and pr["net_financing"] == 1500 and pr["net_movement"] == 1600 and pr["cash_open"] == 1000 and pr["cash_close_computed"] == 2600,
          "prior totals computed: %s" % {k: pr[k] for k in ("net_operating", "net_investing", "net_financing", "net_movement", "cash_close_computed")})
    check(pr["cash_close_actual"] == 3000 and pr["variance"] == -400, "prior closing cash checked against the trial balance's prior cash")
    veh = [r for r in st["cf"]["investing_rows"] if r["label"] == "Purchase of vehicles"][0]
    check(veh["prior"] == -300 and veh["current"] == 0 and veh["prior_only"], "a heading only last year had is its own row")
    d = DocxDocument(wp.build_financial_statements_docx(e, st, None))
    cft = [t for t in d.tables if "Net cash from operating activities" in " ".join(x.text for x in t._cells)][0]
    check(len(cft.columns) == 4 and cft.rows[0].cells[3].text.startswith("2024"), "Word statement has four columns, headed 2025 / 2024")
    row = [r for r in cft.rows if r.cells[0].text == "Net cash from financing activities"][0]
    check(row.cells[3].text == "1,500", "prior total in Word: %s" % row.cells[3].text)
    check("Purchase of vehicles" in " ".join(x.text for x in cft._cells), "prior-only line in Word")
    # nothing captured -> unchanged single column
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete(); db.session.commit()
    st2 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st2, e)
    d2 = DocxDocument(wp.build_financial_statements_docx(e, st2, None))
    cft2 = [t for t in d2.tables if "Net cash from operating activities" in " ".join(x.text for x in t._cells)][0]
    check(st2["cf"]["prior"] is None and len(cft2.columns) == 3, "no comparatives -> the statement is as before")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
