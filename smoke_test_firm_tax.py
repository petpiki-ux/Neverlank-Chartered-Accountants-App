"""Firm Finance > Tax: capital allowances, income tax computation, deferred tax and the QPD estimate, checked against
figures worked out by hand, and shown to reach the firm's statements."""
import os, sys, tempfile
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
import firm_tax as ft
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005):
    return a is not None and abs(a - b) <= tol

REF = date(2025, 12, 31)
def rule(method, wt=0.0, sia=25.0, years=4, cap=None, prorata=False):
    return dict(method=method, wt=wt, sia=sia, sia_years=years, cap=cap, prorata=prorata, verified=True)
def asset(cost, acq, disp=None, proceeds=0.0):
    return {"cost": cost, "date_acquired": acq, "disposed_on": disp, "disposal_proceeds": proceeds}
def last(a, r, upto):
    return ft.simulate_asset(a, r, REF, upto)[-1]

# ------------------------------------------------------------------ pure allowance checks
sia = rule("SIA")
a = asset(4000, date(2025, 3, 10))
rows = ft.simulate_asset(a, sia, REF, date(2029, 12, 31))
check([round(r["allowance"], 2) for r in rows] == [1000, 1000, 1000, 1000, 0], "SIA 25% on 4,000: 1,000 a year for four years, then nothing")
check([round(r["closing"], 2) for r in rows] == [3000, 2000, 1000, 0, 0], "tax value falls 3,000, 2,000, 1,000, 0")
r = ft.simulate_asset(asset(1000, date(2025, 1, 5)), rule("SIA", sia=60.0), REF, date(2027, 12, 31))
check([round(x["allowance"], 2) for x in r] == [600, 400, 0], "an allowance never exceeds the tax value left (60%: 600 then 400)")
r = ft.simulate_asset(asset(1000, date(2024, 6, 1)), rule("WT_SL", wt=20.0), REF, REF)
check([round(x["allowance"], 2) for x in r] == [200, 200] and near(r[-1]["closing"], 600), "wear and tear 20% straight-line: 200 a year on cost")
r = ft.simulate_asset(asset(1000, date(2025, 2, 1)), rule("WT_RB", wt=25.0), REF, date(2026, 12, 31))
check(near(r[0]["allowance"], 250) and near(r[1]["allowance"], 187.5) and near(r[1]["closing"], 562.5), "reducing balance 25%: 250 then 25% of 750 = 187.50")
r = ft.simulate_asset(asset(30000, date(2025, 4, 1)), rule("WT_SL", wt=20.0, cap=20000.0), REF, REF)
check(near(r[-1]["allowance"], 4000) and near(r[-1]["closing"], 16000), "a cost cap of 20,000 limits the base: allowance 4,000, tax value 16,000")
r = ft.simulate_asset(asset(1200, date(2025, 10, 20)), rule("WT_SL", wt=12.0, prorata=True), REF, REF)
check(near(r[-1]["allowance"], 36), "first year by months owned: Oct-Dec = 3/12 of 144 = 36")
r = ft.simulate_asset(asset(1200, date(2025, 10, 20)), rule("WT_SL", wt=12.0), REF, REF)
check(near(r[-1]["allowance"], 144), "without the months switch the full year's allowance is given")
r = ft.simulate_asset(asset(1000, date(2025, 1, 5)), rule("NONE"), REF, REF)
check(near(r[-1]["allowance"], 0) and near(r[-1]["closing"], 1000), "no allowance method keeps the cost as the tax value")
for proceeds, want_recoup, want_scrap in ((3500, 500, 0), (2500, 0, 500), (5000, 1000, 0), (3000, 0, 0)):
    r = last(asset(4000, date(2025, 3, 10), date(2026, 6, 1), proceeds), sia, date(2026, 12, 31))
    check(near(r["allowance"], 0) and near(r["recoupment"], want_recoup) and near(r["scrapping"], want_scrap) and near(r["closing"], 0),
          f"sold for {proceeds} against a tax value of 3,000: recoupment {want_recoup}, scrapping {want_scrap}, no allowance in the year of sale")
sched = ft.capital_allowances([dict(asset(4000, date(2025, 3, 10), date(2026, 6, 1), 3500), asset_class_id=1, asset_code="X", description="x")],
                              [{"id": 1, "name": "Plant"}], {1: sia}, date(2027, 12, 31))
check(sched["rows"] == [], "an asset sold in an earlier year is not in a later year's schedule")
check(ft.fy_end(date(2025, 2, 28), 2028) == date(2028, 2, 29) and ft.fy_end(date(2025, 6, 30), 2026) == date(2026, 6, 30), "a month-end year-end stays a month-end (leap years)")

# ------------------------------------------------------------------ the scenario
with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=partner.id)
    db.session.add(cl); db.session.commit()
    inv = Invoice(invoice_number="C", client_id=cl.id, currency="USD", vat_pct=15.0, status="Sent", issue_date=date(2025, 5, 5), due_date=date(2025, 5, 5))
    inv.lines.append(InvoiceLineItem(description="Fee", quantity=1, unit_price=5000)); db.session.add(inv)
    def exp(d, sup, cat, amt):
        db.session.add(Expense(expense_date=d, supplier=sup, category=cat, amount=amt, currency="USD", status="Paid", paid_date=d))
    exp(date(2025, 2, 1), "Landlord", "Rent & premises", 1200)
    exp(date(2025, 3, 10), "Airline", "Travel & transport", 400)
    exp(date(2026, 2, 1), "Landlord", "Rent & premises", 600)
    comp = FirmAssetClass(name="Computer equipment", depreciation_method="Straight-line", rate_percent=25.0, order=0)
    db.session.add(comp); db.session.commit()
    db.session.add(FirmAsset(asset_code="FA-0001", description="Laptop", asset_class_id=comp.id, date_acquired=date(2025, 3, 15), cost=1200.0))
    bank = CashAccount(name="Bank (USD)", kind="Bank", currency="USD", opening_balance=0.0, opening_date=date(2025, 1, 1), default_in=True, default_out=True)
    db.session.add(bank); db.session.commit()
    for d, amt, cat in ((date(2025, 6, 25), 300, "QPD (provisional tax) paid"), (date(2025, 9, 25), 200, "QPD (provisional tax) paid"),
                        (date(2026, 3, 31), 241.60, "Income tax paid")):
        db.session.add(CashEntry(account_id=bank.id, entry_date=d, direction="out", amount=amt, category=cat, description="ZIMRA"))
    db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, comp=comp.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(ids["partner"]), login(ids["staff"])
F = "/firm-finance"

for path in ("/tax", "/tax/computation", "/tax/capital-allowances", "/tax/deferred", "/tax/qpd", "/tax/rules", "/tax/export.xlsx"):
    check(S.get(F + path).status_code == 403, f"staff cannot open {path}")
check(S.post(F + "/tax/lines/add", data={"ye_id": 1, "item_type": "addback", "description": "x", "amount": "5"}).status_code == 403, "staff cannot add a tax line")

r = P.get(F + "/tax/computation", follow_redirects=True)
check(b"Add a year-end first" in r.data, "tax pages without a year-end point to adding one")

P.post(F + "/year-ends/new", data={"period_end": "2025-12-31", "zwg_rate": "", "opening_nil": "1"})
with app.app_context():
    y1 = FirmYearEnd.query.filter_by(period_end=date(2025, 12, 31)).first().id
    check(abs(FirmYearEnd.query.get(y1).tax_rate_percent - 24.72) < 1e-9, "the year-end starts with the 24.72% combined rate")

# rules page: starter rule for a computer class is wear and tear 25%, flagged not verified
r = P.get(F + "/tax/rules")
check(r.status_code == 200 and b"not verified" in r.data and b"Computer equipment" in r.data, "rules page lists the class with a not-verified starter rule")
with app.app_context():
    rl = FirmTaxAssetRule.query.filter_by(asset_class_id=ids["comp"]).first()
    check(rl.method == "WT_SL" and rl.wt_rate_percent == 25.0 and not rl.verified, "starter rule for computers: wear and tear 25%, not verified")

# category rule: half of travel is not deductible
cats = None
with app.app_context():
    from finance import category_names
    cats = category_names(active_only=False)
data = {}
for i, c in enumerate(cats):
    data[f"name_{i}"] = c; data[f"pct_{i}"] = "50" if c == "Travel & transport" else "0"
data["note_%d" % cats.index("Travel & transport")] = "private element"
r = P.post(F + "/tax/rules/categories", data=data, follow_redirects=True)
check(b"deductibility saved" in r.data, "expense deductibility saved")
data["pct_0"] = "abc"
r = P.post(F + "/tax/rules/categories", data=data, follow_redirects=True)
check(b"Not saved" in r.data, "a non-numeric percentage is refused")
with app.app_context():
    check(FirmTaxCategoryRule.query.count() == 1 and FirmTaxCategoryRule.query.first().disallowed_percent == 50.0, "only the non-zero rule is stored")

# manual lines and assessed loss
r = P.post(F + "/tax/lines/add", data={"ye_id": y1, "item_type": "addback", "description": "Fines", "amount": "100"}, follow_redirects=True)
check(b"Line added" in r.data, "add-back line added")
P.post(F + "/tax/lines/add", data={"ye_id": y1, "item_type": "exempt", "description": "Bank interest", "amount": "50"})
r = P.post(F + "/tax/lines/add", data={"ye_id": y1, "item_type": "addback", "description": "Bad", "amount": "abc"}, follow_redirects=True)
check(b"amount above zero" in r.data, "a non-numeric line amount is refused")
r = P.post(F + "/tax/loss", data={"ye_id": y1, "tax_loss_bf": "350"}, follow_redirects=True)
check(b"loss brought forward saved" in r.data, "assessed loss brought forward entered")
r = P.post(F + "/tax/loss", data={"ye_id": y1, "tax_loss_bf": "-5"}, follow_redirects=True)
check(b"zero or more" in r.data, "a negative loss is refused")

with app.app_context():
    s = fs.build_firm_statements(y1)
    tax = s["cur"]["tax"]
    c = tax["comp"]
    check(near(c["profit_before_tax"], 3150), "profit before tax 3,150 (fees 5,000 - rent 1,200 - travel 400 - depreciation 250)")
    check(near(tax["cap"]["total"]["allowance"], 300) and near(tax["cap"]["total"]["closing"], 900), "laptop: wear and tear 25% of 1,200 = 300, tax value 900")
    check(near(c["total_addbacks"], 250 + 200 + 100) and near(c["total_deductions"], 300 + 50), "add-backs 550 (depreciation 250, half of travel 200, fines 100); deductions 350 (allowance 300, exempt 50)")
    check(near(c["taxable_income_before_losses"], 3350) and near(c["loss_utilised"], 350) and near(c["taxable_income"], 3000), "taxable income 3,350 less assessed loss 350 = 3,000")
    check(near(tax["current_tax"], 741.60), "tax at 24.72% of 3,000 = 741.60")
    check(near(tax["qpd_paid"], 500) and near(tax["payable"], 241.60), "QPD payments of 500 from the cash book leave 241.60 payable")
    check(near(tax["nbv_end"], 950) and near(tax["tv_end"], 900) and near(tax["deferred_closing"], 12.36) and near(tax["deferred_opening"], 0), "deferred tax: NBV 950 - tax value 900 = 50 x 24.72% = 12.36 liability")
    t = s["cur"]["totals"]
    check(near(t["income_tax_expense"], 741.60 + 12.36) and near(t["current_tax_payable"], 241.60) and near(t["deferred_tax_liability"], 12.36),
          "the statements carry tax expense 753.96, tax payable 241.60 and deferred tax liability 12.36")
    check(near(s["pl"]["profit_for_year"]["current"], 3150 - 753.96), "profit for the year 2,396.04")

# typed-in tax figures are ignored while the Tax pages are in charge
P.post(F + f"/year-ends/{y1}/lines/add", data={"fs_category": "income_tax_expense", "description": "Typed", "amount": "9999", "currency": "USD"})
with app.app_context():
    s = fs.build_firm_statements(y1)
    check(near(s["cur"]["totals"]["income_tax_expense"], 753.96) and any("were ignored" in c["detail"] for c in s["checks"]), "a typed-in tax figure is ignored, and a check says so")
r = P.post(F + "/tax/mode", data={"ye_id": y1}, follow_redirects=True)
with app.app_context():
    s = fs.build_firm_statements(y1)
    check(near(s["cur"]["totals"]["income_tax_expense"], 9999) and s["cur"]["tax"] is None, "manual tax uses the typed figure")
r = P.get(F + "/tax/computation?ye=%d" % y1)
check(r.status_code == 200 and b"manual tax" in r.data, "the Tax pages say the year-end is on manual tax")
P.post(F + "/tax/mode", data={"ye_id": y1, "from_tax_pages": "1"})
with app.app_context():
    check(near(fs.build_firm_statements(y1)["cur"]["totals"]["income_tax_expense"], 753.96), "switching back restores the Tax pages' figures")

# pages
r = P.get(F + f"/year-ends/{y1}")
check(r.status_code == 200 and b"come from the" in r.data and b"741.60" in r.data and b"241.60" in r.data, "the year-end page shows the tax figures taken from the Tax pages")
r = P.get(F + f"/tax/computation?ye={y1}")
check(r.status_code == 200 and b"741.60" in r.data and b"3,350.00" in r.data and b"Fines" in r.data and b"241.60" in r.data, "computation page shows the working")
r = P.get(F + f"/tax/capital-allowances?ye={y1}")
check(r.status_code == 200 and b"Laptop" in r.data and b"300.00" in r.data and b"not yet verified" in r.data, "allowances page shows the laptop and warns about unverified rates")
r = P.get(F + f"/tax/deferred?ye={y1}")
check(r.status_code == 200 and b"12.36" in r.data, "deferred tax page shows 12.36")
r = P.post(F + "/tax/deferred/add", data={"ye_id": y1, "description": "Leave pay provision", "accounting_amount": "abc", "tax_base_amount": "0"}, follow_redirects=True)
check(b"as numbers" in r.data, "a non-numeric temporary difference is refused")
P.post(F + "/tax/deferred/add", data={"ye_id": y1, "description": "Leave pay provision", "accounting_amount": "-200", "tax_base_amount": "0"})
with app.app_context():
    s = fs.build_firm_statements(y1); tx = s["cur"]["tax"]
    check(near(tx["deferred_closing"], 12.36 - 49.44), "a provision of 200 (deductible later) gives a deferred tax credit of 49.44, netting the closing balance to (37.08)")
    check(near(s["cur"]["totals"]["deferred_tax_asset"], 37.08), "net deferred tax is an asset of 37.08")
    item = FirmDeferredItem.query.first().id
P.post(F + f"/tax/deferred/{item}/delete")
with app.app_context():
    check(near(fs.build_firm_statements(y1)["cur"]["tax"]["deferred_closing"], 12.36), "removing the item restores 12.36")

# QPD estimate at 30 June 2025
scale = 365.0 / 181.0
pbt = (5000 - 1600) * scale - 250
taxable = pbt + 250 + 0.5 * 400 * scale + 100 - 50 - 300 - 350
want = round(taxable * 0.2472, 2)
r = P.get(F + f"/tax/qpd?ye={y1}&as_at=2025-06-30")
check(r.status_code == 200 and b"QPD" in r.data, "QPD page opens")
with app.app_context():
    st = fs.build_firm_statements(y1)
    classes, assets = fs.load_register()
    est = ft.qpd_estimate(FirmYearEnd.query.get(y1), st["cur"]["period_start"], date(2025, 6, 30), classes, assets, None)
    check(near(est["scale"], scale, 1e-6) and near(est["pbt_annual"], pbt, 0.01), "profit so far is annualised by 365/181")
    check(near(est["annual_tax"], want, 0.02), f"estimated annual tax {want:,.2f} (taxable {taxable:,.2f} x 24.72%)")
    inst = est["instalments"]
    check(len(inst) == 4 and near(sum(i["amount"] for i in inst), est["annual_tax"]), "four instalments that add up to the annual estimate")
    check(near(inst[0]["amount"], est["annual_tax"] * 0.10, 0.02), "the first instalment is 10% of the annual estimate")
    check(near(inst[1]["paid_by_then"], 300) and near(inst[2]["paid_by_then"], 500), "payments are matched to each due date (300 by 25 June, 500 by 25 September)")
r = P.get(F + f"/tax/qpd?ye={y1}&as_at=garbage")
check(r.status_code == 200, "an unreadable date falls back to today")

# Excel
r = P.get(F + f"/tax/export.xlsx?ye={y1}")
check(r.status_code == 200 and r.data[:2] == b"PK", "the tax pack downloads as an Excel file")
import io, openpyxl
wb = openpyxl.load_workbook(io.BytesIO(r.data))
check(wb.sheetnames == ["Tax computation", "Capital allowances", "Deferred tax", "QPD"], "tax pack sheets")

# rules: changing a rule clears its verification; invalid input is refused
cid = ids["comp"]
base = {f"method_{cid}": "WT_SL", f"wt_{cid}": "25", f"sia_{cid}": "25", f"years_{cid}": "4", f"cap_{cid}": "", f"note_{cid}": "checked", f"verified_{cid}": "1"}
r = P.post(F + "/tax/rules/assets", data=base, follow_redirects=True)
with app.app_context():
    check(FirmTaxAssetRule.query.filter_by(asset_class_id=cid).first().verified is True, "an unchanged rule can be marked verified")
r = P.post(F + "/tax/rules/assets", data=dict(base, **{f"wt_{cid}": "20"}), follow_redirects=True)
with app.app_context():
    rl = FirmTaxAssetRule.query.filter_by(asset_class_id=cid).first()
    check(rl.wt_rate_percent == 20.0 and rl.verified is False, "changing the rate clears the verified tick")
r = P.post(F + "/tax/rules/assets", data=dict(base, **{f"wt_{cid}": "abc"}), follow_redirects=True)
check(b"Not saved" in r.data, "a non-numeric rate is refused")
r = P.post(F + "/tax/rules/assets", data=dict(base, **{f"wt_{cid}": "150"}), follow_redirects=True)
check(b"Not saved" in r.data, "a rate over 100 is refused")
P.post(F + "/tax/rules/assets", data=dict(base, **{f"wt_{cid}": "25"}))

# second year: chain of loss, tax payable and deferred tax
P.post(F + "/year-ends/new", data={"period_end": "2026-12-31"})
with app.app_context():
    y2 = FirmYearEnd.query.filter_by(period_end=date(2026, 12, 31)).first().id
    s = fs.build_firm_statements(y2)
    tax2 = s["cur"]["tax"]
    check(near(tax2["comp"]["profit_before_tax"], -900), "2026 loss before tax 900 (rent 600 + depreciation 300)")
    check(near(tax2["comp"]["total_addbacks"], 300) and near(tax2["comp"]["total_deductions"], 300), "depreciation 300 added back and allowance 300 deducted")
    check(near(tax2["loss_bf"], 0) and near(tax2["loss_cf"], 900) and near(tax2["current_tax"], 0), "no tax; assessed loss carried forward 900")
    check(near(tax2["payable_bf"], 241.60) and near(tax2["settled"], 241.60) and near(tax2["payable"], 0), "the 241.60 payable is settled by the payment in March 2026")
    check(near(tax2["deferred_opening"], 12.36) and near(tax2["deferred_closing"], 12.36) and near(tax2["deferred_expense"], 0), "deferred tax opens at the previous year's 12.36 and does not move")
    check(near(s["cur"]["totals"]["income_tax_expense"], 0) and near(s["cur"]["totals"]["deferred_tax_liability"], 12.36), "2026 statements: no tax expense, deferred tax 12.36")
    check(s["pri"] is not None and near(s["pri"]["totals"]["income_tax_expense"], 753.96), "the 2025 comparative keeps its tax expense")
    check(s["cf"] is not None, "the cash flow statement is produced for the second year")
r = P.get(F + f"/tax/computation?ye={y2}")
check(r.status_code == 200 and b"carried forward" in r.data, "second year computation page opens")
r = P.post(F + "/tax/loss", data={"ye_id": y2, "tax_loss_bf": ""}, follow_redirects=True)
check(b"follows the previous year-end" in r.data, "a blank assessed loss follows the previous year-end")
# notes and other statements render
for path in ("/income-statement", "/balance-sheet", "/cash-flow", "/notes", "/print"):
    check(P.get(F + path + f"?ye={y2}").status_code == 200, f"statement page {path} renders with tax")
check(b"Tax" in P.get(F + "/").data, "Firm Finance menu has a Tax link")
check(b"Expenses" in P.get(F + "/").data, "Firm Finance menu has an Expenses link")
r = P.get(F + "/cash-book/entries/new")
check(b"QPD (provisional tax) paid" in r.data, "the cash book entry form offers 'QPD (provisional tax) paid'")

print()
print("FAILED:" if fails else "ALL PASSED", len(fails) if fails else "")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
