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
                                      ("Retained earnings", "other_reserves", 0, 2900, 0, 900), ("Fixed assets", "ppe", 0, 0, 0, 0), ("Sales", "revenue", 0, 5000, 0, 7000)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=nm, fs_category=cat, current_debit=dr, current_credit=cr, prior_debit=pdr, prior_credit=pcr))
    db.session.commit()
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(aid); s["_fresh"] = True
    page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    check('href="#cf-statement"' in page and "not entered yet" in page, "a link at the top of the Finalisation tab jumps to the capture card")
    check("Save prior-year figures" in page and 'name="row_amt"' in page and "2025\nUSD" in page, "the statement's prior-year column has a box on every line, even before anything is saved")
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
    check(pr["net_operating"] == 7000 and pr["net_investing"] == -300 and pr["net_financing"] == 1500 and pr["net_movement"] == 8200 and pr["cash_open"] == 1000 and pr["cash_close_computed"] == 9200,
          "prior totals computed: %s" % {k: pr[k] for k in ("net_operating", "net_investing", "net_financing", "net_movement", "cash_close_computed")})
    check(pr["cash_close_actual"] == 3000 and pr["variance"] == 6200, "prior closing cash checked against the trial balance's prior cash")
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
# ---- import from Excel / CSV / pasted rows
import io, openpyxl
with app.app_context():
    eid2 = eid
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(aid); s["_fresh"] = True
    wb = openpyxl.Workbook(); ws = wb.active
    for r in [["Penanel Trading", None, None, None], ["Statement of cash flows", "Note", 2025, 2024], ["Operating activities", None, None, None], ["Profit before tax", None, 120000, 98000],
              ["Finance costs", None, "(5,000)", "(4,000)"], ["Cash generated from operations", None, 115000, 94000], ["Income tax paid", None, -9000, "(8,000)"],
              ["Net cash from operating activities", None, 106000, 86000], ["Investing activities"], ["Purchase of property, plant and equipment", 3, "(30,000)", "(20,000)"],
              ["Net cash used in investing", None, "(30,000)", "(20,000)"], ["Financing activities"], ["Proceeds from borrowings", None, 50000, 10000],
              ["Net increase in cash", None, 126000, 76000], ["Cash at beginning of year", None, 10000, 5000], ["Cash at end of year", None, 136000, 81000]]:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    r = c.post(f"/engagements/{eid2}/cash-flow-prior/import", data={"file": (buf, "cf.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
    got = [(l.section, l.label, l.amount) for l in cf_prior.lines_for(eid2)]
    check(got == [("operating", "Finance costs paid", -4000.0), ("operating", "Income tax paid", -8000.0),
                  ("investing", "Net movement in property, plant and equipment", -20000.0), ("financing", "Proceeds from/(repayment of) borrowings", 10000.0), ("cash_open", "Cash at beginning of prior year", 5000.0)],
          "Excel import reads the 2024 column, sections from the headings, subtotals left out, names matched to this year's headings: %s" % got)
    check("amount column" in r.get_data(as_text=True) and "2024" in r.get_data(as_text=True), "the page says which column was read")
    r = c.post(f"/engagements/{eid2}/cash-flow-prior/import", data={"pasted": "Dividends paid\t(1,000)\nProfit before tax\t5,000"}, follow_redirects=True)
    got = [(l.section, l.label, l.amount) for l in cf_prior.lines_for(eid2)]
    check(got == [("financing", "Dividends paid", -1000.0)], "pasted rows import, replacing the earlier lines; Profit before tax is not imported: %s" % got)
    r = c.post(f"/engagements/{eid2}/cash-flow-prior/import", data={}, follow_redirects=True)
    check(len(cf_prior.lines_for(eid2)) == 1 and "Choose a file" in r.get_data(as_text=True), "nothing chosen: nothing changes")
    r = c.post(f"/engagements/{eid2}/cash-flow-prior/import", data={"file": (io.BytesIO(b"not a workbook"), "bad.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
    check(len(cf_prior.lines_for(eid2)) == 1 and "could not be read" in r.get_data(as_text=True), "an unreadable file changes nothing")
# ---- imported figures sit on the same row as this year's heading, whatever the file calls it
with app.app_context():
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete(); db.session.commit()
    for sec, label, amt in [("operating", "Profit before taxation", 98000.0), ("operating", "Taxation paid", -8000.0), ("operating", "Increase in debtors", -500.0),
                            ("investing", "Purchase of property, plant and equipment", -20000.0), ("financing", "Proceeds from issue of shares", 700.0),
                            ("financing", "Loans received", 10000.0), ("investing", "Purchase of vehicles", -300.0)]:
        db.session.add(CashFlowPriorLine(engagement_id=eid, section=sec, label=label, amount=amt))
    db.session.commit()
    trial = TrialBalance.query.filter_by(engagement_id=eid).first()
    st4 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st4, e)
    rows = {r["label"]: r for k in ("operating_rows", "investing_rows", "financing_rows") for r in st4["cf"][k]}
    check(rows["Profit before tax"]["prior"] == 7000 and rows["Profit before tax"].get("linked") == "income statement" and rows["Income tax paid"]["prior"] == -8000 and rows["(Increase)/decrease in trade and other receivables"]["prior"] == -500,
          "Profit before tax is linked to the prior-year income statement; tax paid and receivables line up by meaning")
    check(rows["Net movement in property, plant and equipment"]["prior"] == -20000 and rows["Proceeds from shares issued"]["prior"] == 700 and rows["Proceeds from/(repayment of) borrowings"]["prior"] == 10000,
          "investing and financing lines are matched by meaning too")
    std = set(l for ls in cf_prior.standard_labels("indirect").values() for l in ls)
    extra = [r["label"] for k in ("operating_rows", "investing_rows", "financing_rows") for r in st4["cf"][k] if r.get("prior_only") and r["label"] not in std]
    check(extra == ["Purchase of vehicles"], "only a heading with no counterpart gets its own extra row: %s" % extra)
    check(st4["cf"]["prior"]["net_operating"] == -1500 and st4["cf"]["prior"]["net_investing"] == -20300 and st4["cf"]["prior"]["net_financing"] == 10700,
          "prior subtotals include the matched lines (PBT 7,000 from the income statement, not the typed 98,000): %s" % st4["cf"]["prior"])
    check(st4["cf"]["prior"]["pbt"] == {"amount": 7000.0, "typed": 98000.0}, "typed Profit before taxation is ignored but remembered")
with app.app_context():
    c = app.test_client()
    with c.session_transaction() as s_:
        s_["_user_id"] = str(aid); s_["_fresh"] = True
    page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    r = c.post(f"/engagements/{eid}/cash-flow-prior/import", data={"pasted": "Profit before taxation\t55,000\nTaxation paid\t(2,000)"}, follow_redirects=True)
    got = [(l.section, l.label, l.amount) for l in cf_prior.lines_for(eid)]
    check(got == [("operating", "Income tax paid", -2000.0)] and "taken from the prior-year income statement" in r.get_data(as_text=True), "import drops Profit before taxation and says why: %s" % got)
# ---- the prior year's own add-backs: finance costs, depreciation, deferred tax, prepayments
with app.app_context():
    c = app.test_client()
    with c.session_transaction() as s_:
        s_["_user_id"] = str(aid); s_["_fresh"] = True
    pasted = "\n".join(["Profit before taxation\t2,000,000", "Finance costs\t150,000", "Depreciation charge\t291,647", "Deferred tax movement\t72,095",
                        "Decrease/(Increase) Prepayments\t270,485", "Increase in trade and other payables\t40,000", "Cash generated from operations\t2,824,227",
                        "Finance costs\t(150,000)", "Taxation paid\t(30,000)", "Purchase of property, plant and equipment\t(500,000)"])
    r = c.post(f"/engagements/{eid}/cash-flow-prior/import", data={"pasted": pasted}, follow_redirects=True)
    got = {(l.section, l.label): l.amount for l in cf_prior.lines_for(eid)}
    check(got.get(("operating", "Finance costs")) == 150000 and got.get(("operating", "Finance costs paid")) == -150000, "finance costs: the positive line is the add-back, the negative one the payment: %s" % got)
    check(got.get(("operating", "Depreciation and amortisation")) == 291647 and got.get(("operating", "Movement in deferred tax")) == 72095
          and got.get(("operating", "(Increase)/decrease in other current assets")) == 270485 and got.get(("operating", "Income tax paid")) == -30000, "depreciation, deferred tax, prepayments and tax paid are recognised: %s" % got)
    trial = TrialBalance.query.filter_by(engagement_id=eid).first()
    st5 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st5, e)
    ops = [(r["label"], r.get("prior"), bool(r.get("bold"))) for r in st5["cf"]["operating_rows"]]
    pri = {l: p_ for l, p_, b in ops}
    check(pri["Finance costs"] == 150000 and pri["Finance costs paid"] == -150000 and pri["Depreciation and amortisation (current year: memo only - see note)"] == 291647 and pri["Movement in deferred tax"] == 72095
          and pri["(Increase)/decrease in other current assets"] == 270485, "the prior-year column shows each on its own row: %s" % ops)
    names = [l for l, _, _ in ops]
    check(names.index("Depreciation and amortisation (current year: memo only - see note)") < names.index("Operating cash flow before working capital changes") and names.index("Movement in deferred tax") < names.index("Depreciation and amortisation (current year: memo only - see note)"),
          "the depreciation add-back sits with the adjustments to profit, above the working-capital changes")
    obw = pri["Operating cash flow before working capital changes"]
    check(obw == 7000 + 150000 + 291647 + 72095, "operating cash flow before working capital adds them back: %s" % obw)
    check(pri["Cash generated from operations"] == obw + 270485 + 40000 and st5["cf"]["prior"]["net_operating"] == pri["Cash generated from operations"] - 150000 - 30000, "operating total: %s" % st5["cf"]["prior"]["net_operating"])
    # lines saved by the earlier version (both finance lines under one name, depreciation unplaced) are put right when the statement is built
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete()
    for i, (sec, lab, amt) in enumerate([("operating", "Finance costs", 150000.0), ("operating", "Finance costs", -150000.0), ("operating", "Depreciation charge", 291647.0)]):
        db.session.add(CashFlowPriorLine(engagement_id=eid, section=sec, label=lab, amount=amt, sort_order=i))
    db.session.commit()
    st6 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st6, e)
    pri6 = {r["label"]: r.get("prior") for r in st6["cf"]["operating_rows"]}
    check(pri6["Finance costs"] == 150000 and pri6["Finance costs paid"] == -150000 and pri6["Depreciation and amortisation (current year: memo only - see note)"] == 291647, "already-saved lines are corrected too: %s" % pri6)
check(cf_prior.refine("operating", "Deferred tax (credit)/charge", 5)[1] == "Movement in deferred tax" and cf_prior.refine("operating", "Interest expense", 5)[1] == "Finance costs"
      and cf_prior.refine("operating", "Interest paid", -5)[1] == "Finance costs paid" and cf_prior.refine("operating", "Profit before tax", 5)[1] == "Profit before tax"
      and cf_prior.match_label("Decrease/(Increase) in prepayments", ["(Increase)/decrease in other current assets", "(Increase)/decrease in trade and other receivables"]) == "(Increase)/decrease in other current assets",
      "wording variants: deferred tax, interest, prepayments")
# ---- typing straight into the prior-year column of the statement
import re as _re, html as _html
with app.app_context():
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete(); db.session.commit()
    c = app.test_client()
    with c.session_transaction() as s_:
        s_["_user_id"] = str(aid); s_["_fresh"] = True
    page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    st_html = page[page.index('id="cf-statement"'):page.index('id="cf-prior"')]
    boxes = _re.findall(r'name="row_sec" value="([^"]*)"><input type="hidden" name="row_lab" value="([^"]*)"><input[^>]*name="row_amt"', st_html)
    labs = [_html.unescape(l) for _, l in boxes]
    check("Profit before tax" not in labs and "Depreciation and amortisation" in labs and "Net movement in property, plant and equipment" in labs and "Finance costs paid" in labs
          and not any(l.startswith("Net cash") or l.startswith("Cash generated") for l in labs), "every ordinary line has a box (also headings that are nil this year); Profit before tax and the subtotals do not: %d boxes" % len(boxes))
    typed = {"Finance costs": "150,000", "Depreciation and amortisation": "291,647", "Movement in deferred tax": "72,095", "(Increase)/decrease in other current assets": "270,485",
             "Finance costs paid": "-150,000", "Income tax paid": "-30,000", "Net movement in property, plant and equipment": "(500,000)"}
    form = {"row_sec": [], "row_lab": [], "row_amt": [], "x_sec": ["investing"], "x_lab": ["Purchase of vehicles"], "x_amt": ["-300"], "cash_open_amt": "1,000"}
    for sec, lab in boxes:
        lab = _html.unescape(lab); form["row_sec"].append(sec); form["row_lab"].append(lab); form["row_amt"].append(typed.get(lab, ""))
    r = c.post(f"/engagements/{eid}/cash-flow-prior/save-inline", data=form, follow_redirects=True)
    got = {(l.section, l.label): l.amount for l in cf_prior.lines_for(eid)}
    check(got == {("cash_open", "Cash at beginning of prior year"): 1000.0, ("operating", "Finance costs"): 150000.0, ("operating", "Depreciation and amortisation"): 291647.0,
                  ("operating", "Movement in deferred tax"): 72095.0, ("operating", "(Increase)/decrease in other current assets"): 270485.0, ("operating", "Finance costs paid"): -150000.0,
                  ("operating", "Income tax paid"): -30000.0, ("investing", "Net movement in property, plant and equipment"): -500000.0, ("investing", "Purchase of vehicles"): -300.0},
          "typed figures saved against their own lines (blank = nothing, brackets = minus): %s" % got)
    page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    st_html = page[page.index('id="cf-statement"'):page.index('id="cf-prior"')]
    check('value="291647.00"' in st_html and 'value="72095.00"' in st_html and 'value="-150000.00"' in st_html and "Prior year: " in st_html and "not entered yet" not in st_html, "the saved figures come back in the boxes and the prior-year checks show")
    trial = TrialBalance.query.filter_by(engagement_id=eid).first()
    st7 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st7, e, editable=True)
    pr7 = st7["cf"]["prior"]
    check(pr7["net_operating"] == 7000 + 150000 + 291647 + 72095 + 270485 - 150000 - 30000 and pr7["net_investing"] == -500300 and pr7["cash_open"] == 1000 and not pr7["empty"],
          "totals worked out from the typed column: %s" % {k: pr7[k] for k in ("net_operating", "net_investing", "net_movement", "cash_close_computed")})
    d7 = DocxDocument(wp.build_financial_statements_docx(e, st7, None))
    cft7 = [t for t in d7.tables if "Net cash from operating activities" in " ".join(x.text for x in t._cells)][0]
    check(len(cft7.columns) == 4 and "291,647" in " ".join(x.text for x in cft7._cells), "the Word statement carries the typed prior-year column")
    # saving everything blank clears the lot and the Word statement goes back to one column
    blank = dict(form); blank["row_amt"] = [""] * len(form["row_amt"]); blank["x_amt"] = [""]; blank["cash_open_amt"] = ""
    c.post(f"/engagements/{eid}/cash-flow-prior/save-inline", data=blank, follow_redirects=True)
    check(cf_prior.lines_for(eid) == [], "blank boxes save nothing")
# ---- tidy-ups: quiet investing headings, one depreciation line, exchange differences under Profit before tax
with app.app_context():
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete()
    for i, (sec, lab, amt) in enumerate([("operating", "Unrealised foreign exchange loss", 9000.0), ("operating", "Depreciation charge", 291647.0), ("investing", "Net movement in investment property", -4000.0)]):
        db.session.add(CashFlowPriorLine(engagement_id=eid, section=sec, label=lab, amount=amt, sort_order=i))
    db.session.commit()
    trial = TrialBalance.query.filter_by(engagement_id=eid).first()
    st8 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st8, e, editable=True)
    ops8 = [r["label"] for r in st8["cf"]["operating_rows"]]
    inv8 = [r["label"] for r in st8["cf"]["investing_rows"]]
    check(not any("depreciation" in l.lower() and "memo" not in l.lower() for l in ops8) and sum("epreciation" in l for l in ops8) == 1, "depreciation is one line: %s" % [l for l in ops8 if "epreciation" in l])
    check(ops8[:3] == ["Profit before tax", "Exchange differences", "Finance costs"] and st8["cf"]["operating_rows"][1]["prior"] == 9000, "exchange differences sit directly under Profit before tax: %s" % ops8[:3])
    check(inv8 == ["Net movement in property, plant and equipment", "Net movement in investment property"], "intangibles / long-term investments / other non-current assets have no line unless they have a figure: %s" % inv8)
    plain = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(plain, e, editable=True)
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete(); db.session.commit()
    st9 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st9, e, editable=True)
    inv9 = [r["label"] for r in st9["cf"]["investing_rows"]]
    check(inv9 == ["Net movement in property, plant and equipment"], "nothing saved: only property, plant and equipment is offered in investing: %s" % inv9)
# ---- lines with nothing in either year are left out
with app.app_context():
    c = app.test_client()
    with c.session_transaction() as s_:
        s_["_user_id"] = str(aid); s_["_fresh"] = True
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete()
    db.session.add(CashFlowPriorLine(engagement_id=eid, section="financing", label="Proceeds from/(repayment of) borrowings", amount=1500.0, sort_order=0)); db.session.commit()
    page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    st_html = page[page.index('id="cf-statement"'):page.index('id="cf-prior"')]
    shown, _, hidden = st_html.partition("left out - open to type")
    check("Proceeds from/(repayment of) borrowings" in shown and "Movement in long-term provisions" not in shown and "Movement in long-term provisions" in hidden and "Dividends paid" not in shown,
          "the statement shows only lines with a figure; the empty headings wait in a fold-away list")
    trial = TrialBalance.query.filter_by(engagement_id=eid).first()
    st10 = fin.build_all_statements(trial.lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    cf_prior.attach_prior_cash_flow(st10, e)
    d10 = DocxDocument(wp.build_financial_statements_docx(e, st10, None))
    cft10 = [t for t in d10.tables if "Net cash from operating activities" in " ".join(x.text for x in t._cells)][0]
    labels10 = [r.cells[0].text for r in cft10.rows]
    check("Movement in long-term provisions" not in labels10 and "Dividends paid" not in labels10 and "Proceeds from/(repayment of) borrowings" in labels10 and "Profit before tax" in labels10 and "Net cash from operating activities" in labels10,
          "the Word statement leaves out lines that are nil in both years: %d rows" % len(labels10))
    CashFlowPriorLine.query.filter_by(engagement_id=eid).delete(); db.session.commit()
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
