"""Finance > Firm Finance: asset register, depreciation, year-end balances and the firm's financial statements, checked
against figures worked out by hand."""
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
from datetime import date
from app import create_app
from extensions import db
from models import *
import firm_statements as fs
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
# this test types income tax, tax payable and deferred tax in by hand; the Tax pages have their own test (smoke_test_firm_tax.py)
from sqlalchemy import event
@event.listens_for(FirmYearEnd, "before_insert")
def _manual_tax(mapper, connection, target):
    target.tax_manual = True
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005):
    return a is not None and abs(a - b) <= tol

# ------------------------------------------------------------------ pure depreciation checks
SL25 = {"depreciation_method": "Straight-line", "rate_percent": 25.0, "useful_life_years": None}
laptop = {"date_acquired": date(2025, 3, 15), "cost": 1200.0, "residual_value": 0.0, "disposed_on": None}
m = fs.asset_movement(laptop, SL25, date(2025, 1, 1), date(2025, 12, 31))
check(near(m["charge"], 250) and near(m["additions"], 1200) and near(m["closing_nbv"], 950), "straight-line: bought in March = 10 months of 25.00")
m = fs.asset_movement(laptop, SL25, date(2026, 1, 1), date(2026, 12, 31))
check(near(m["opening_nbv"], 950) and near(m["charge"], 300) and near(m["closing_nbv"], 650) and near(m["additions"], 0), "second year: full 12 months")
m = fs.asset_movement(laptop, SL25, date(2029, 1, 1), date(2029, 12, 31))
check(near(m["closing_nbv"], 0) and near(m["charge"], 50), "depreciation stops at residual value (nil here): bought Mar 2025, fully written off after 48 months, so 2029 charges only Jan and Feb (50)")
sold = dict(laptop, disposed_on=date(2026, 7, 10), disposal_proceeds=500.0)
m = fs.asset_movement(sold, SL25, date(2026, 1, 1), date(2026, 12, 31))
check(near(m["charge"], 150) and near(m["disposals_acc_dep"], 400) and near(m["gain_loss"], -300) and near(m["closing_nbv"], 0), "disposal in July: 6 months charged, loss 300 on NBV 800 vs proceeds 500")
m = fs.asset_movement(sold, SL25, date(2027, 1, 1), date(2027, 12, 31))
check(not m["in_scope"], "an asset sold in an earlier period is not in the later register")
RB20 = dict(SL25, depreciation_method="Reducing balance", rate_percent=20.0)
m = fs.asset_movement(laptop, RB20, date(2025, 1, 1), date(2025, 12, 31)); m2 = fs.asset_movement(laptop, RB20, date(2026, 1, 1), date(2026, 12, 31))
check(near(m["charge"], 200) and near(m2["charge"], 200) and near(m2["closing_nbv"], 800), "reducing balance: 20% of cost for 10 months, then 20% of opening NBV")
bf = dict(laptop, acc_dep_bf=500.0, acc_dep_bf_date=date(2025, 12, 31))
m = fs.asset_movement(bf, SL25, date(2026, 1, 1), date(2026, 12, 31))
check(near(m["opening_acc_dep"], 500) and near(m["charge"], 300) and near(m["closing_nbv"], 400), "accumulated depreciation brought forward replaces the calculated figure from its date")
land = {"date_acquired": date(2025, 1, 1), "cost": 5000.0}
m = fs.asset_movement(land, {"depreciation_method": "Not depreciated", "rate_percent": None, "useful_life_years": None}, date(2025, 1, 1), date(2025, 12, 31))
check(near(m["charge"], 0) and near(m["closing_nbv"], 5000), "'Not depreciated' class keeps cost")
life = {"depreciation_method": "Straight-line", "rate_percent": None, "useful_life_years": 5.0}
m = fs.asset_movement(dict(laptop, residual_value=200.0), life, date(2026, 1, 1), date(2026, 12, 31))
check(near(m["charge"], 200), "useful life of 5 years with residual 200: 1000/5 = 200 a year")

# ------------------------------------------------------------------ the scenario
with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=partner.id)
    db.session.add(cl); db.session.commit()

    def invoice(num, cur, issue, net, status="Sent", receipts=(), vat=15.0):
        inv = Invoice(invoice_number=num, client_id=cl.id, currency=cur, vat_pct=vat, status=status, issue_date=issue, due_date=issue)
        inv.lines.append(InvoiceLineItem(description="Fee", quantity=1, unit_price=net))
        db.session.add(inv); db.session.commit()
        for i, (d, amt) in enumerate(receipts):
            db.session.add(Receipt(receipt_number=f"{num}-R{i}", invoice_id=inv.id, amount=amt, currency=cur, paid_on=d))
        db.session.commit()
        return inv
    invoice("A", "USD", date(2024, 3, 10), 1000, "Paid", [(date(2024, 4, 10), 1150)])
    b = invoice("B", "USD", date(2024, 11, 20), 2000, "Sent", [(date(2025, 2, 1), 1000)])
    cn = CreditNote(credit_note_number="CN-1", invoice_id=b.id, issue_date=date(2025, 6, 15), currency="USD", vat_pct=15.0, reason="discount")
    cn.lines.append(CreditNoteLine(description="Discount", quantity=1, unit_price=200)); db.session.add(cn); db.session.commit()
    invoice("C", "USD", date(2025, 5, 5), 5000, "Sent", [(date(2025, 9, 1), 4000)])
    invoice("D", "ZWG", date(2025, 8, 1), 25000, "Sent")
    invoice("E", "USD", date(2025, 4, 1), 9999, "Draft")

    def exp(d, supplier, cat, amt, cur="USD", status="Paid", paid=None):
        db.session.add(Expense(expense_date=d, supplier=supplier, category=cat, amount=amt, currency=cur, status=status, paid_date=paid))
    exp(date(2024, 2, 1), "Landlord", "Rent & premises", 1200, paid=date(2024, 2, 1))
    exp(date(2024, 12, 10), "ZESA", "Utilities", 300, paid=date(2025, 1, 15))
    exp(date(2025, 2, 1), "Landlord", "Rent & premises", 1200, paid=date(2025, 2, 1))
    exp(date(2025, 6, 30), "Staff", "Salaries & wages", 1500, paid=date(2025, 6, 30))
    exp(date(2025, 12, 15), "ZESA", "Utilities", 400, status="Unpaid")
    exp(date(2025, 9, 1), "Softco", "Software & subscriptions", 2500, cur="ZWG", paid=date(2025, 9, 1))
    db.session.add(ClientAdvance(advance_number="ADV-1", client_id=cl.id, currency="USD", amount=500, received_on=date(2025, 11, 1)))
    db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(ids["partner"]), login(ids["staff"])
F = "/firm-finance"

# access
for path in ("/", "/assets", "/assets/new", "/assets/classes", "/income-statement", "/balance-sheet", "/cash-flow", "/changes-in-equity", "/notes", "/print", "/export.xlsx"):
    check(S.get(F + path).status_code == 403, f"staff cannot open {path}")
check(S.post(F + "/year-ends/new", data={"period_end": "2025-12-31"}).status_code == 403, "staff cannot create a year-end")

# no year-end yet
r = P.get(F + "/")
check(r.status_code == 200 and b"No year-end yet" in r.data and b"Firm Finance" in r.data, "overview with no year-end explains what to do")
r = P.get(F + "/income-statement", follow_redirects=True)
check(b"Add a year-end first" in r.data, "statements without a year-end point to adding one")
r = P.get(F + "/assets")
check(r.status_code == 200 and b"starter set" in r.data, "register offers starter classes when none exist")

# classes
r = P.post(F + "/assets/classes/add", data={"name": "Computer equipment", "depreciation_method": "Straight-line", "rate_percent": "25"}, follow_redirects=True)
check(b"Computer equipment" in r.data and b"added" in r.data, "class added")
P.post(F + "/assets/classes/add", data={"name": "Furniture", "depreciation_method": "Straight-line", "rate_percent": "10"})
r = P.post(F + "/assets/classes/add", data={"name": "computer EQUIPMENT", "depreciation_method": "Straight-line", "rate_percent": "25"}, follow_redirects=True)
check(b"already a class" in r.data, "duplicate class name refused")
r = P.post(F + "/assets/classes/add", data={"name": "Bad", "depreciation_method": "Straight-line"}, follow_redirects=True)
check(b"Enter a rate" in r.data, "class without rate or life refused")
r = P.post(F + "/assets/classes/add", data={"name": "Bad", "depreciation_method": "Straight-line", "rate_percent": "150"}, follow_redirects=True)
check(b"between 0 and 100" in r.data, "rate over 100 refused")
with app.app_context():
    cid = {c.name: c.id for c in FirmAssetClass.query.all()}
check(set(cid) == {"Computer equipment", "Furniture"}, "exactly the two classes exist")

# assets
def add_asset(**kw):
    d = {"description": "x", "asset_class_id": str(cid["Computer equipment"]), "date_acquired": "2024-03-15", "cost": "1200", "residual_value": "0"}
    d.update(kw); return P.post(F + "/assets/new", data=d, follow_redirects=True)
r = add_asset(description="")
check(b"Describe the asset" in r.data, "asset needs a description")
r = add_asset(cost="-5"); check(b"Enter the cost" in r.data, "negative cost refused")
r = add_asset(residual_value="5000"); check(b"residual value cannot" in r.data, "residual above cost refused")
r = add_asset(residual_value="abc"); check(b"residual value cannot" in r.data, "a non-numeric residual value is refused, not read as 0")
r = add_asset(acc_dep_bf="100"); check(b"both the accumulated" in r.data, "b/f figure needs its date")
r = add_asset(description="Dell laptop"); check(b"FA-0001" in r.data, "first asset gets code FA-0001")
r = add_asset(description="Desk", asset_class_id=str(cid["Furniture"]), date_acquired="2025-02-10", cost="1200")
r = add_asset(description="Printer", date_acquired="2024-06-01", cost="600")
with app.app_context():
    check(FirmAsset.query.count() == 3, "three assets saved (invalid ones were not)")
    printer_id = FirmAsset.query.filter_by(description="Printer").first().id
r = P.post(F + f"/assets/{printer_id}/dispose", data={"disposed_on": "2024-01-01", "disposal_proceeds": "300"}, follow_redirects=True)
check(b"not before the acquisition" in r.data, "disposal before acquisition refused")
r = P.post(F + f"/assets/{printer_id}/dispose", data={"disposed_on": "2025-09-20", "disposal_proceeds": "abc"}, follow_redirects=True)
check(b"sale proceeds as a number" in r.data, "non-numeric proceeds refused, not read as 0")
r = P.post(F + f"/assets/{printer_id}/dispose", data={"disposed_on": "2025-09-20", "disposal_proceeds": "300", "disposal_note": "Sold to staff"}, follow_redirects=True)
check(b"marked as disposed" in r.data, "printer disposed 20 Sep 2025 for 300")

# year-ends
r = P.post(F + "/year-ends/new", data={"period_end": "2024-12-31", "opening_nil": "1"}, follow_redirects=True)
check(b"Balances for 31 December 2024" in r.data, "FY2024 year-end created (first period)")
r = P.post(F + "/year-ends/new", data={"period_end": "2024-12-31"}, follow_redirects=True)
check(b"already exists" in r.data, "duplicate year-end refused")
P.post(F + "/year-ends/new", data={"period_end": "2025-12-31", "zwg_rate": "25"})
with app.app_context():
    y24 = FirmYearEnd.query.filter_by(period_end=date(2024, 12, 31)).first().id
    y25 = FirmYearEnd.query.filter_by(period_end=date(2025, 12, 31)).first().id

def line(ye, cat, desc, amt, cur="USD"):
    return P.post(F + f"/year-ends/{ye}/lines/add", data={"fs_category": cat, "description": desc, "amount": str(amt), "currency": cur}, follow_redirects=True)
r = line(y24, "nonsense", "x", 1); check(b"Choose what the figure is" in r.data, "unknown section refused")
r = line(y24, "cash", "", 1); check(b"Describe the figure" in r.data, "description required")
r = line(y24, "cash", "x", "abc"); check(b"as a number" in r.data, "amount must be numeric")

# before the FY2024 balances are entered the balance sheet must say it does not balance
r = P.get(F + f"/balance-sheet?ye={y24}")
check(r.status_code == 200 and b"need attention" in r.data and b"Total assets exceed" in r.data, "unbalanced balance sheet is flagged, with the amount")

line(y24, "cash", "CBZ current account", 600)
line(y24, "other_current_liabilities", "VAT payable", 400)
line(y24, "current_tax_payable", "Income tax payable", 250)
line(y24, "long_term_borrowings", "Bank loan", 1500)
line(y24, "share_capital", "Ordinary shares", 1000)
line(y24, "income_tax_expense", "Income tax for the year", 250)

line(y25, "cash", "CBZ current account", 730)
line(y25, "cash", "ZWG account", 1250, "ZWG")
line(y25, "other_current_liabilities", "VAT payable", 700)
line(y25, "current_tax_payable", "Income tax payable", 350)
line(y25, "long_term_borrowings", "Bank loan", 1300)
line(y25, "share_capital", "Ordinary shares", 1200)
line(y25, "finance_costs", "Loan interest", 150)
line(y25, "income_tax_expense", "Income tax for the year", 400)
line(y25, "dividends_paid", "Dividend", 300)

# ------------------------------------------------------------------ the numbers, FY2024 (first period)
with app.app_context():
    s24 = fs.build_firm_statements(y24)
    t = s24["totals"]
    check(near(t["revenue"]["current"], 3000), "FY2024 revenue 3,000 (invoices A + B excl. VAT; draft ignored)")
    check(near(t["admin_expenses"]["current"], 1500), "FY2024 admin expenses 1,500 (rent 1,200 + utilities 300 unpaid still counted)")
    check(near(t["depreciation_amortisation"]["current"], 337.5), "FY2024 depreciation 337.50 (laptop 250 + printer 87.50)")
    check(near(t["ppe"]["current"], 1462.5), "FY2024 net book value 1,462.50")
    check(near(t["trade_receivables"]["current"], 2300), "FY2024 receivables 2,300 (invoice B incl. VAT; A was paid)")
    check(near(t["trade_payables"]["current"], 300), "FY2024 payables 300 (utilities bill paid only in 2025)")
    check(near(s24["pl"]["profit_before_tax"]["current"], 1162.5) and near(s24["pl"]["profit_for_year"]["current"], 912.5), "FY2024 profit before tax 1,162.50, after tax 912.50")
    check(near(s24["sfp"]["total_assets"]["current"], 4362.5) and near(s24["sfp"]["balance_check"]["current"], 0), "FY2024 balance sheet balances at 4,362.50")
    check(not s24["has_prior"], "FY2024 has no comparative column")
    cf = s24["cf"]
    check(cf is not None and near(cf["net_movement"], 600) and near(cf["variance"], 0), "FY2024 cash flow (first period) explains the 600 of cash")
    check(near(cf["net_investing"], -1800) and near(cf["net_financing"], 2500) and near(cf["net_operating"], -100), "FY2024 operating -100, investing -1,800, financing +2,500")
    check(s24["ok"], "FY2024: every check passes")

# ------------------------------------------------------------------ FY2025
with app.app_context():
    s = fs.build_firm_statements(y25)
    t = s["totals"]
    check(s["has_prior"], "FY2025 has FY2024 as its comparative")
    check(near(t["revenue"]["current"], 5800), "FY2025 revenue 5,800 = C 5,000 + ZWG invoice 25,000/25 = 1,000 - credit note 200")
    check(near(t["admin_expenses"]["current"], 3200), "FY2025 admin 3,200 = rent 1,200 + salaries 1,500 + utilities 400 + software ZWG 2,500/25 = 100")
    check(near(t["depreciation_amortisation"]["current"], 510), "FY2025 depreciation 510 = 300 + 110 + 100")
    check(near(t["other_expenses"]["current"], 112.5), "FY2025 loss on disposal of the printer 112.50 (NBV 412.50, proceeds 300)")
    pl = s["pl"]
    check(near(pl["profit_before_tax"]["current"], 1827.5) and near(pl["profit_for_year"]["current"], 1427.5), "FY2025 profit before tax 1,827.50, after tax 1,427.50")
    check(near(pl["profit_before_tax"]["prior"], 1162.5), "comparative profit before tax is FY2024's 1,162.50")
    check(near(t["trade_receivables"]["current"], 3970), "FY2025 receivables 3,970 = B 1,070 + C 1,750 + D 1,150")
    check(near(t["trade_payables"]["current"], 400) and near(t["other_current_liabilities"]["current"], 1200), "payables 400; other current liabilities 1,200 (advance 500 + VAT 700)")
    check(near(t["cash"]["current"], 780), "ZWG cash 1,250 at 25 = 50 added to USD 730 = 780")
    check(near(t["ppe"]["current"], 1740), "net book value 1,740 = laptop 650 + desk 1,090")
    check(near(t["retained_earnings"]["current"], 912.5), "retained earnings b/f carried from FY2024 closing (912.50)")
    sfp = s["sfp"]
    check(near(sfp["total_assets"]["current"], 6490) and near(sfp["total_equity_and_liabilities"]["current"], 6490) and near(sfp["balance_check"]["current"], 0), "FY2025 balance sheet balances at 6,490")
    check(near(sfp["total_equity"]["current"], 3240), "equity 3,240 = capital 1,200 + retained earnings 2,040 (912.50 + 1,427.50 - 300)")
    check(near(sfp["total_assets"]["prior"], 4362.5), "comparative total assets 4,362.50")
    cf = s["cf"]
    ops = {r["label"]: r["current"] for r in cf["operating_rows"]}
    check(near(ops["Depreciation of property, plant and equipment"], 510) and near(ops["(Profit)/loss on disposal of property, plant and equipment"], 112.5), "cash flow adds back depreciation 510 and the 112.50 loss")
    check(near(ops["Cash generated from operations"], 1830), "cash generated from operations 1,830")
    check(near(ops["Income tax paid"], -300) and near(ops["Finance costs paid"], -150), "tax paid 300 (250 + 400 - 350 payable); finance costs paid 150")
    inv_rows = {r["label"]: r["current"] for r in cf["investing_rows"]}
    check(near(inv_rows["Purchase of property, plant and equipment"], -1200) and near(inv_rows["Proceeds from disposal of property, plant and equipment"], 300), "investing: desk bought for 1,200, printer sold for 300 (gross)")
    fin_rows = {r["label"]: r["current"] for r in cf["financing_rows"]}
    check(near(fin_rows["Proceeds from shares issued"], 200) and near(fin_rows["Proceeds from/(repayment of) borrowings"], -200) and near(fin_rows["Dividends paid"], -300), "financing: shares +200, loan repaid 200, dividends 300")
    check(near(cf["net_movement"], 180) and near(cf["cash_close_computed"], 780) and near(cf["variance"], 0), "net cash movement 180: 600 opening -> 780 closing, no difference")
    soce = s["soce"]
    check(len(soce) == 2, "changes in equity for both years")
    cur = soce[0]; labels = [r["label"] for r in cur["rows"]]
    check(near(cur["rows"][0]["total"], 1912.5) and near(cur["rows"][-1]["total"], 3240), "equity moves 1,912.50 -> 3,240")
    check("Shares issued" in labels and "Dividends" in labels, "equity statement shows shares issued and dividends")
    check(near(soce[1]["rows"][-1]["total"], 1912.5), "comparative closing equity 1,912.50 (capital 1,000 + earnings 912.50)")
    check(s["ok"], "FY2025: every check passes")
    # notes
    titles = {n["title"]: n for n in s["notes"]}
    check([n["number"] for n in s["notes"]][:3] == [1, 2, 3], "notes 1-3 are general information, basis and policies")
    check("Property, plant and equipment" in titles and titles["Property, plant and equipment"]["movement_total"]["additions"] == 1200, "PPE note carries the movement schedule")
    check(near(titles["Administrative expenses"]["total"]["current"], 3710), "administrative expenses note totals 3,710 (3,200 + depreciation 510)")
    check(any("Utilities" == a["name"] and near(a["current"], 400) and near(a["prior"], 300) for a in titles["Administrative expenses"]["accounts"]), "expense note lists Utilities 400 against 300")
    check(near(sum(x for _, x in titles["Fees receivable and other receivables"]["extra_table"]["rows"]), 3970), "receivables ageing adds up to 3,970")
    check("Income tax expense" in titles and any("effective" in p or "% of profit" in p for p in titles["Income tax expense"]["paragraphs"]), "tax note states the rate on profit before tax")
    pl_notes = {r["label"]: r.get("note") for r in pl["rows"]}
    check(pl_notes.get("Revenue") == str(titles["Revenue"]["number"]), "revenue line cross-references its note")
    check(sfp["non_current_assets"][0].get("note") == str(titles["Property, plant and equipment"]["number"]), "balance sheet PPE line cross-references its note")

# ------------------------------------------------------------------ pages
for path in ("/", "/assets", "/assets/classes", f"/year-ends/{y25}", f"/income-statement?ye={y25}", f"/balance-sheet?ye={y25}", f"/cash-flow?ye={y25}",
             f"/changes-in-equity?ye={y25}", f"/notes?ye={y25}", f"/print?ye={y25}", f"/assets?as_at=2025-12-31", f"/assets?as_at=2024-12-31&disposed=1"):
    r = P.get(F + path)
    check(r.status_code == 200, f"partner opens {path}")
r = P.get(F + f"/income-statement?ye={y25}")
check(b"5,800.00" in r.data and b"1,827.50" in r.data and b"1,427.50" in r.data and b"All checks pass" in r.data, "income statement page shows the figures and passing checks")
r = P.get(F + f"/balance-sheet?ye={y25}")
check(b"6,490.00" in r.data and b"3,240.00" in r.data and b"4,362.50" in r.data, "balance sheet page shows current and comparative totals")
r = P.get(F + f"/cash-flow?ye={y25}")
check(b"1,830.00" in r.data and b"(1,200.00)" in r.data and b"780.00" in r.data, "cash flow page shows operating, purchases and closing cash")
r = P.get(F + f"/print?ye={y25}")
check(b"Neverlank Chartered Accountants" in r.data and b"Notes to the financial statements" in r.data and b"unaudited" in r.data, "print page carries all statements")
r = P.get(F + "/assets?as_at=2025-12-31")
check(b"Dell laptop" in r.data and b"Desk" in r.data and b"Printer" not in r.data.split(b"Not in this period")[0], "register at 31 Dec 2025 lists laptop and desk; the sold printer is not listed")
r = P.get(F + "/assets?as_at=2025-12-31&disposed=1")
check(b"Printer" in r.data and b"Sold" not in r.data[:0] and b"Disposed 20 Sep 2025" in r.data, "register can include assets disposed in the period")
r = P.get(F + f"/income-statement?ye={y24}")
check(b"3,000.00" in r.data and r.data.count(b"31 Dec 2024") >= 1, "FY2024 income statement page")

# excel
r = P.get(F + f"/export.xlsx?ye={y25}")
check(r.status_code == 200 and r.data[:2] == b"PK", "Excel export downloads")
from openpyxl import load_workbook
wb = load_workbook(io.BytesIO(r.data))
check(wb.sheetnames == ["Income Statement", "Financial Position", "Cash Flow", "Changes in Equity", "Notes", "Asset Register", "Checks"], "workbook has every statement")
ws = wb["Income Statement"]
vals = {str(row[0].value).strip(): row[2].value for row in ws.iter_rows(min_row=1) if row[0].value}
check(near(vals.get("Revenue"), 5800) and near(vals.get("Profit for the year"), 1427.5), "Excel income statement values")
ws = wb["Financial Position"]
vals = {str(row[0].value).strip(): row[2].value for row in ws.iter_rows(min_row=1) if row[0].value}
check(near(vals.get("Total assets"), 6490) and near(vals.get("Total equity and liabilities"), 6490), "Excel balance sheet balances")

# ------------------------------------------------------------------ editing and what-ifs
with app.app_context():
    ln = FirmBalanceLine.query.filter_by(year_end_id=y25, description="CBZ current account").first(); lid = ln.id
r = P.post(F + f"/year-ends/{y25}/lines/{lid}/edit", data={"fs_category": "cash", "description": "CBZ current account", "amount": "630", "currency": "USD"}, follow_redirects=True)
check(b"Figure updated" in r.data, "an entered figure can be edited")
with app.app_context():
    s = fs.build_firm_statements(y25)
    check(near(s["sfp"]["balance_check"]["current"], -100) and not s["ok"], "cash 100 lower than the balance: sheet out by 100 and the checks say so")
    cashchk = [c for c in s["checks"] if c["key"] == "cash"][0]
    check(not cashchk["ok"] and near(s["cf"]["variance"], -100), "cash flow no longer agrees to cash")
    hint = [c for c in s["checks"] if c["key"] == "balance"][0]["detail"]
    check("912.50" not in hint and "would balance" in hint, "the balance check suggests a retained earnings figure")
P.post(F + f"/year-ends/{y25}/lines/{lid}/edit", data={"fs_category": "cash", "description": "CBZ current account", "amount": "730", "currency": "USD"})
r = P.post(F + f"/year-ends/{y25}/lines/{lid}/delete", follow_redirects=True)
check(b"Figure removed" in r.data, "an entered figure can be removed")
line(y25, "cash", "CBZ current account", 730)

# ZWG with no rate: left out, never added 1:1
r = P.post(F + f"/year-ends/{y25}/settings", data={"period_end": "2025-12-31", "zwg_rate": "", "tax_rate_percent": "24.72", "reporting_framework": "ifrs_for_smes"}, follow_redirects=True)
with app.app_context():
    s = fs.build_firm_statements(y25)
    check(near(s["totals"]["revenue"]["current"], 4800), "without a ZWG rate the ZWG invoice is left out (revenue 4,800), not added 1:1")
    check(any("no ZWG per USD rate" in c["detail"] or "rate has been entered" in c["detail"] or "no rate is entered" in c["detail"] for c in s["checks"]), "a warning names the missing rate")
P.post(F + f"/year-ends/{y25}/settings", data={"period_end": "2025-12-31", "zwg_rate": "25", "tax_rate_percent": "24.72", "reporting_framework": "ifrs_for_smes", "extra_note": "Premises are rented.\nNo contingent liabilities."})
with app.app_context():
    s = fs.build_firm_statements(y25)
    check(near(s["totals"]["revenue"]["current"], 5800) and s["ok"], "rate restored: back to 5,800 and all checks pass")
    check(s["notes"][-1]["title"] == "Additional information" and len(s["notes"][-1]["paragraphs"]) == 2, "additional note printed as the last note")

# entered opening retained earnings that disagrees with the previous closing
line(y25, "retained_earnings", "Per old accounts", 1000)
with app.app_context():
    s = fs.build_firm_statements(y25)
    chk = [c for c in s["checks"] if c["key"] == "re"]
    check(chk and not chk[0]["ok"] and "87.50" in chk[0]["detail"], "opening retained earnings 1,000 vs previous closing 912.50 is flagged (87.50)")
with app.app_context():
    FirmBalanceLine.query.filter_by(year_end_id=y25, fs_category="retained_earnings").delete(); db.session.commit()

# a year-end with nothing before it and no 'first period' tick has no cash flow
P.post(F + "/year-ends/new", data={"period_end": "2023-12-31"})
with app.app_context():
    y23 = FirmYearEnd.query.filter_by(period_end=date(2023, 12, 31)).first().id
    s = fs.build_firm_statements(y23)
    check(s["cf"] is None and not s["cf_available"], "earliest year-end without 'first period' has no cash flow")
    check(any(c["key"] == "cash" and "not available" in c["title"] for c in s["checks"]), "and says why")
    s24b = fs.build_firm_statements(y24)
    check(s24b["has_prior"] and near(s24b["totals"]["retained_earnings"]["current"], 0) and s24b["cf"] is not None, "FY2024 now has FY2023 before it; opening retained earnings carried (nil)")
    ye23 = FirmYearEnd.query.get(y23); ye23.opening_nil = True; db.session.commit()
r = P.get(F + f"/cash-flow?ye={y23}")
check(r.status_code == 200, "cash flow page opens for a year-end without a cash flow")

# copy loans / capital from the previous year-end
r = P.post(F + f"/year-ends/{y25}/copy-previous", follow_redirects=True)
check(b"Copied 0" in r.data or b"Copied" in r.data, "copy from previous year-end runs")
P.post(F + "/year-ends/new", data={"period_end": "2026-12-31"})
with app.app_context():
    y26 = FirmYearEnd.query.filter_by(period_end=date(2026, 12, 31)).first().id
r = P.post(F + f"/year-ends/{y26}/copy-previous", follow_redirects=True)
with app.app_context():
    got = {(l.fs_category, l.amount) for l in FirmYearEnd.query.get(y26).lines}
check(("long_term_borrowings", 1300.0) in got and ("share_capital", 1200.0) in got and not any(c in ("cash", "income_tax_expense", "dividends_paid") for c, _ in got), "only loans, capital and reserves are copied (no cash, tax or dividends)")
r = P.post(F + f"/year-ends/{y26}/copy-previous", follow_redirects=True)
with app.app_context():
    check(FirmBalanceLine.query.filter_by(year_end_id=y26).count() == 2, "copying twice does not duplicate")

# classes: switch off / delete rules / policy change flows through
r = P.post(F + f"/assets/classes/{cid['Computer equipment']}/delete", follow_redirects=True)
check(b"cannot be deleted" in r.data, "a class with assets cannot be deleted")
r = P.post(F + f"/assets/classes/{cid['Computer equipment']}/edit", data={"name": "Computer equipment", "depreciation_method": "Straight-line", "rate_percent": "50"}, follow_redirects=True)
with app.app_context():
    s = fs.build_firm_statements(y25)
    # laptop 1200 at 50%: 2024 = 10 x 50 = 500, 2025 = 600; printer 600 at 50%: 2024 7 x 25 = 175; sold Sep 2025: 8 x 25 = 200
    check(near(s["totals"]["depreciation_amortisation"]["current"], 600 + 110 + 200), "changing the class rate recalculates depreciation (FY2025 now 910)")
r = P.post(F + f"/assets/classes/{cid['Computer equipment']}/edit", data={"name": "Computer equipment", "depreciation_method": "Straight-line", "rate_percent": "25"})
r = P.post(F + f"/assets/classes/{cid['Furniture']}/toggle", follow_redirects=True); check(b"switched off" in r.data, "class can be switched off")
r = P.get(F + "/assets/new"); check(b"Furniture (" not in r.data, "switched-off class is not offered for new assets")
P.post(F + f"/assets/classes/{cid['Furniture']}/toggle")

# undo disposal, edit and delete an asset
r = P.post(F + f"/assets/{printer_id}/undo-disposal", follow_redirects=True)
with app.app_context():
    check(FirmAsset.query.get(printer_id).disposed_on is None, "disposal can be undone")
    s = fs.build_firm_statements(y25)
    check(near(s["totals"]["other_expenses"]["current"], 0), "no loss on disposal once undone")
P.post(F + f"/assets/{printer_id}/dispose", data={"disposed_on": "2025-09-20", "disposal_proceeds": "300"})
r = P.post(F + f"/assets/{printer_id}/edit", data={"description": "Printer (HP)", "asset_class_id": str(cid["Computer equipment"]), "date_acquired": "2024-06-01", "cost": "600", "residual_value": "0"}, follow_redirects=True)
check(b"updated" in r.data, "asset can be edited")
r = P.get(F + f"/assets/{printer_id}/edit"); check(r.status_code == 200 and b"Printer (HP)" in r.data and b"Disposed on 20 Sep 2025" in r.data, "edit form shows the disposal")
r = P.post(F + f"/assets/{printer_id}/delete", follow_redirects=True)
with app.app_context():
    check(FirmAsset.query.count() == 2, "asset can be removed from the register")

# year-end deletion keeps the rest
r = P.post(F + f"/year-ends/{y26}/delete", follow_redirects=True)
with app.app_context():
    check(FirmYearEnd.query.filter_by(id=y26).first() is None and FirmBalanceLine.query.filter_by(year_end_id=y26).count() == 0, "deleting a year-end removes its entered figures")
    check(Invoice.query.count() == 5 and Expense.query.count() == 6, "invoices and expenses are untouched")

# permission tab
r = P.get("/invoices/")
check(b"Firm Finance" in r.data, "Firm Finance tab appears for the Partner")
r = S.get("/invoices/")
check(b"Firm Finance" not in r.data, "and not for staff")
r = P.get("/credit-notes/")
check(b"finance-tabs" in r.data, "credit notes pages now show the finance tab bar")

print()
print("FAILED:" if fails else "ALL PASSED", len(fails) if fails else "")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
