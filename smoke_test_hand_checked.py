"""Deeper arithmetic checks, each expected figure worked out by hand (or by a closed-form formula written independently of the code under test):
loan schedules, discounted cash flow, the cash-flow forecast engine (it must balance whatever is typed in), VAT return dates and review flags,
materiality, the income-tax loss rules and deferred tax."""
import os, sys, tempfile, random, copy
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from types import SimpleNamespace as N
from datetime import date
import cf_engine as CE, cf_valmodel as VM, vat_checks as VC, financials as F
from models import MaterialityCalculation
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005): return abs(a - b) <= tol

# ------------------------------------------------------------------ loans
# 12,000 over 12 months at 12% a year: monthly rate 1%; level payment = P*r / (1-(1+r)^-n) = 120 / (1 - 1/1.01^12) = 1066.19 (the textbook figure)
s = CE.loan_schedule(12000, 12, 12, "annuity")
check(near(s["instalment"], 1066.19, 0.005), "annuity instalment on 12,000 at 12% over 12 months is 1,066.19")
check(near(s["rows"][0]["interest"], 120.0) and near(s["rows"][0]["principal"], 1066.19 - 120, 0.006), "first month: interest 120.00 (1% of 12,000), the rest is principal")
check(near(s["rows"][-1]["closing"], 0.0, 1e-6), "the loan is fully repaid at the end")
check(near(s["total_paid"], 12 * 1066.1851, 0.2) and near(s["total_interest"], s["total_paid"] - 12000, 1e-6), "total interest = total paid less the loan (about 795.82)")
s = CE.loan_schedule(12000, 12, 12, "straight")
check(near(s["rows"][0]["principal"], 1000) and near(s["rows"][0]["interest"], 120) and near(s["rows"][11]["interest"], 10), "straight-line: 1,000 principal a month, interest falls from 120.00 to 10.00")
check(near(s["total_interest"], 0.01 * 1000 * sum(range(1, 13)), 1e-6), "straight-line interest = 1% x 1,000 x (12+11+...+1) = 780")
s = CE.loan_schedule(12000, 12, 12, "bullet")
check(near(s["total_interest"], 1440) and near(s["rows"][-1]["principal"], 12000) and s["rows"][5]["principal"] == 0, "bullet: interest only (1,440), all the principal in the last month")
s = CE.loan_schedule(12000, 12, 12, "straight", grace_months=3)
check(all(r["principal"] == 0 for r in s["rows"][:3]) and near(s["rows"][3]["principal"], 12000 / 9) and near(s["rows"][-1]["closing"], 0, 1e-6), "a 3-month grace: no principal at first, then 12,000 / 9 a month")
s = CE.loan_schedule(12000, 0, 12, "annuity")
check(near(s["instalment"], 1000) and s["total_interest"] == 0, "a nil-interest loan is just principal / months")
s = CE.loan_schedule(12000, 12, 12, "annuity", balloon_pct=25)
check(near(s["rows"][-1]["closing"], 0, 1e-6) and s["rows"][-1]["principal"] > 3000 and s["instalment"] < 1066.19, "a 25% balloon lowers the instalment and the last payment clears it")
s = CE.loan_schedule(0, 12, 12) ; check(s["total_paid"] == 0, "a nil loan costs nothing")
s = CE.loan_schedule(5000, 10, 1) ; check(near(s["total_paid"], 5000 * (1 + 0.10 / 12)), "a 1-month loan: principal plus one month's interest")
random.seed(11)
bad = []
for _ in range(300):
    P = random.uniform(1000, 5e6); rate = random.uniform(0, 40); n = random.randint(1, 120); kind = random.choice(["annuity", "straight", "bullet"])
    g = random.randint(0, 6); bal = random.choice([0, 0, 10, 30])
    q = CE.loan_schedule(P, rate, n, kind, grace_months=g, balloon_pct=bal)
    ok = near(q["rows"][-1]["closing"], 0, 1e-4) and all(r["closing"] >= -1e-6 for r in q["rows"]) and near(sum(r["principal"] for r in q["rows"]), P, 1e-3)
    ok = ok and all(near(r["opening"] - r["principal"], r["closing"], 1e-6) for r in q["rows"]) and near(q["total_paid"], P + q["total_interest"], 1e-3)
    if not ok: bad.append((P, rate, n, kind, g, bal))
check(not bad, f"300 random loans: balance never negative, principal repaid adds up to the loan, closing balance is nil{'' if not bad else ' - ' + str(bad[0])}")
ann = CE.loan_schedule_annual(CE.loan_schedule(24000, 10, 24, "straight")["rows"])
check(len(ann) == 2 and near(ann[0]["principal"], 12000) and near(ann[1]["closing"], 0, 1e-6), "the annual roll-up has two loan years of 12,000 principal each")

# ------------------------------------------------------------------ discounted cash flow
r_ = VM.dcf_value([100, 100], 10, None, mid_year=False)
check(near(r_[0], 100 / 1.1 + 100 / 1.21, 1e-6) and r_[1] == 0, "two year-end flows of 100 at 10%: 90.91 + 82.64 = 173.55, no terminal value")
r_ = VM.dcf_value([100, 100], 10, None, mid_year=True)
check(near(r_[0], 100 / 1.1 ** 0.5 + 100 / 1.1 ** 1.5, 1e-6), "mid-year timing discounts half a year less")
r_ = VM.dcf_value([100], 10, 2, mid_year=False)
tv = 100 * 1.02 / (0.10 - 0.02)
check(near(r_[1], tv / 1.1, 1e-6) and near(r_[0], 100 / 1.1 + tv / 1.1, 1e-6), "Gordon terminal value: 100 x 1.02 / 8% = 1,275, discounted one year")
check(VM.dcf_value([100], 10, 10, mid_year=False)[1] == 0 and VM.dcf_value([100], 10, 12, mid_year=False)[1] == 0, "growth at or above the discount rate gives no terminal value (no infinite value)")
check(VM.dcf_value([], 10) is None and VM.dcf_value([100], None) is None and VM.dcf_value([100], -100) is None, "no flows, no rate or a -100% rate give no answer")
a = VM.dcf_value([50, 60, 70], 8, None, False)[0]; b = VM.dcf_value([50, 60, 70], 12, None, False)[0]
check(a > b, "a higher discount rate always lowers the value")

# ------------------------------------------------------------------ the forecast model must balance whatever is entered
base = CE.default_assumptions(years=3)
res = CE.run(base)
check(res["checks"]["balance_ok"] and res["checks"]["cash_ok"], "the default model balances every month and its cash ties to the cash flow")
a1 = CE.default_assumptions(years=1)
a1["seasonality"] = [100.0 / 12] * 12; a1["revenue"] = [{"name": "Sales", "y1": 1200000.0, "growth": [0.0]}]; a1["cogs_pct"] = [50.0]; a1["opex"] = []
a1["capex"] = [0.0]; a1["loans"] = []; a1["tax_rate"] = 25.0; a1["dep_rate"] = 0.0; a1["deposit_rate"] = 0.0; a1["overdraft_rate"] = 0.0
a1["opening"].update(ppe_cost=0, ppe_accdep=0, inventory=0, receivables=0, other_assets=0, cash=0, payables=0, other_liabilities=0, share_capital=0, tax_loss_bf=0)
a1["rec_days"] = [0.0]; a1["inv_days"] = [0.0]; a1["pay_days"] = [0.0]
q = CE.run(a1)["annual"][0]
check(near(q["revenue"], 1200000, 1) and near(q["gross_profit"], 600000, 1), "a year of 1,200,000 sales at 50% cost: gross profit 600,000")
check(near(q["pbt"], 600000, 1) and near(q["tax"], 150000, 1) and near(q["pat"], 450000, 1), "no other costs: profit before tax 600,000, tax at 25% = 150,000, profit 450,000")
check(near(q["cash"], 450000, 1), "with no debtors, stock or creditors the year ends with the profit in cash (450,000)")
a1["opening"]["tax_loss_bf"] = 200000
q = CE.run(a1)["annual"][0]
check(near(q["tax"], 0.25 * 400000, 1), "a 200,000 tax loss brought forward cuts taxable profit to 400,000: tax 100,000")
a1["opening"]["tax_loss_bf"] = 900000
q = CE.run(a1)
check(near(q["annual"][0]["tax"], 0, 1), "a tax loss larger than the profit means no tax")
random.seed(5)
bad = []
for i in range(60):
    a = CE.default_assumptions(years=random.randint(1, 4), preset=random.choice(["blank", "trading", "services", "manufacturing"]))
    y = a["years"]
    a["revenue"][0]["y1"] = random.uniform(0, 5e6); a["cogs_pct"] = [random.uniform(0, 110)] * y
    a["capex"] = [random.uniform(0, 4e5)] * y; a["dep_rate"] = random.uniform(0, 40); a["tax_rate"] = random.uniform(0, 40)
    a["rec_days"] = [random.uniform(0, 120)] * y; a["inv_days"] = [random.uniform(0, 150)] * y; a["pay_days"] = [random.uniform(0, 120)] * y
    a["dividend_payout"] = [random.uniform(0, 100)] * y; a["equity_issue"] = [random.choice([0, 0, 1e5])] * y
    a["loans"][0]["opening_balance"] = random.uniform(0, 1e6); a["loans"][0]["rate"] = random.uniform(0, 30)
    a["loans"][0]["repay_type"] = random.choice(["straight", "annuity", "bullet"])
    a["opening"]["tax_loss_bf"] = random.choice([0, 0, random.uniform(0, 1e6)])
    try:
        out = CE.run(a)
        if not (out["checks"]["balance_ok"] and out["checks"]["cash_ok"]): bad.append((i, out["checks"]))
    except Exception as ex:
        bad.append((i, repr(ex)))
check(not bad, f"60 random models (any sales, costs, loans, dividends, tax losses): every one balances and ties to cash{'' if not bad else ' - ' + str(bad[0])}")

# ------------------------------------------------------------------ VAT
check(VC.return_due_date(2026, 1) == date(2026, 2, 25) and VC.return_due_date(2026, 12) == date(2027, 1, 25), "the return is due on the 25th of the next month, including over the year end")
check(VC._months_before(date(2026, 3, 31), 1) == date(2026, 2, 28) and VC._months_before(date(2024, 3, 31), 1) == date(2024, 2, 29) and VC._months_before(date(2026, 1, 15), 2) == date(2025, 11, 15), "month arithmetic handles short months, leap years and the year end")
def doc(**k):
    d = dict(doc_type="Invoice", direction="Output", vat_rate=None, taxable_amount=0.0, vat_amount=0.0, currency="USD", related_reference="", invoice_date=date(2026, 5, 10),
             invoice_number="1", counterparty_name="X", description="", input_tax_denied=False)
    d.update(k); return N(**d)
std = N(rate_percent=15.0, rate_type="Standard"); zero = N(rate_percent=0.0, rate_type="Zero-rated"); exempt = N(rate_percent=0.0, rate_type="Exempt")
keys = lambda fl: {f.key for f in fl if f.level == "review"}
f1 = VC.month_checks([doc(vat_rate=std, taxable_amount=1000, vat_amount=150)], 2026, 5, today=date(2026, 6, 1))
check(not keys(f1), f"a clean month raises no review flags {keys(f1)}")
f2 = VC.month_checks([doc(vat_rate=std, taxable_amount=1000, vat_amount=150)], 2026, 5, today=date(2026, 6, 26))
check("overdue" in keys(f2), "after the 25th the unfiled month is flagged overdue")
f3 = VC.month_checks([doc(vat_rate=std, taxable_amount=1000, vat_amount=150)], 2026, 5, is_done=True, today=date(2026, 9, 1))
check("overdue" not in keys(f3), "a month marked done is never flagged overdue")
check("overdue" not in keys(VC.month_checks([], 2026, 5, today=date(2026, 6, 25))), "on the due date itself it is not yet overdue")
f4 = VC.month_checks([doc(vat_rate=std, taxable_amount=1000, vat_amount=100)], 2026, 5, today=date(2026, 6, 1))
check("rate_mismatch" in keys(f4), "VAT of 100 on 1,000 at 15% is flagged")
f5 = VC.month_checks([doc(vat_rate=std, taxable_amount=1000, vat_amount=150.04)], 2026, 5, today=date(2026, 6, 1))
check("rate_mismatch" not in keys(f5), "a 4-cent rounding difference is not flagged")
f6 = VC.month_checks([doc(vat_rate=std, taxable_amount=1000, vat_amount=0)], 2026, 5, today=date(2026, 6, 1))
check("standard_nil" in keys(f6), "a standard-rate sale with no VAT is flagged")
f7 = VC.month_checks([doc(vat_rate=zero, taxable_amount=1000, vat_amount=50)], 2026, 5, today=date(2026, 6, 1))
check("zero_with_vat" in keys(f7), "VAT charged on a zero-rated sale is flagged")
f8 = VC.month_checks([doc(doc_type="Export Sale", vat_rate=zero, taxable_amount=1000), doc(doc_type="Export Sale", vat_rate=zero, taxable_amount=500, related_reference="ENT-1")], 2026, 5, today=date(2026, 6, 1))
fl = [f for f in f8 if f.key == "export_proof"]
check(fl and fl[0].count == 1, "only the export with no customs entry is flagged (1 of 2)")
f9 = VC.month_checks([doc(direction="Input", vat_rate=std, taxable_amount=1000, vat_amount=150, invoice_date=date(2024, 1, 5))], 2026, 5, today=date(2026, 6, 1))
check("old_input" in keys(f9), "an input claimed more than 12 months after the invoice date is flagged")
f10 = VC.month_checks([doc(direction="Input", vat_rate=std, taxable_amount=1000, vat_amount=150, description="Staff party catering")], 2026, 5, today=date(2026, 6, 1))
check(any(f.ref == "16(2)(a)" for f in f10), "entertainment input tax is flagged against section 16(2)(a)")
f11 = VC.month_checks([doc(direction="Input", vat_rate=std, taxable_amount=1000, vat_amount=150, description="Staff party", input_tax_denied=True)], 2026, 5, today=date(2026, 6, 1))
check(not any(f.ref == "16(2)(a)" for f in f11), "input tax already denied is not flagged again")
f12 = VC.month_checks([doc(direction="Input", vat_rate=std, taxable_amount=6, vat_amount=0.9, invoice_number="", counterparty_name="")], 2026, 5, today=date(2026, 6, 1))
check("invoice_particulars" not in keys(f12), "a US$10-or-less supply needs no tax invoice particulars")
f13 = VC.month_checks([doc(direction="Input", vat_rate=std, taxable_amount=600, vat_amount=90, invoice_number="", counterparty_name="")], 2026, 5, today=date(2026, 6, 1))
check("invoice_particulars" in keys(f13), "a larger input with no invoice number or supplier is flagged")
f14 = VC.month_checks([doc(vat_rate=exempt, taxable_amount=500), doc(direction="Input", vat_rate=std, taxable_amount=100, vat_amount=15)], 2026, 5, today=date(2026, 6, 1))
check("apportion" in keys(f14), "exempt sales together with input tax raise the apportionment flag")

# ------------------------------------------------------------------ materiality
def mat(**k):
    d = dict(total_revenue=None, profit_before_tax=None, total_assets=None, revenue_pct=1.0, pbt_pct=5.0, assets_pct=1.0, basis="highest", performance_pct=75.0, trivial_pct=5.0)
    d.update(k); return MaterialityCalculation(**d)
P = MaterialityCalculation
def om(m): return m.overall_materiality
m = mat(total_revenue=1_000_000, profit_before_tax=100_000, total_assets=2_000_000)   # 10,000 / 5,000 / 20,000
check(om(m) == 20000, "default basis takes the highest benchmark (20,000)")
check(om(mat(total_revenue=1_000_000, profit_before_tax=100_000, total_assets=2_000_000, basis="lowest")) == 5000, "'lowest' takes the smallest (5,000)")
check(om(mat(total_revenue=1_000_000, profit_before_tax=100_000, total_assets=2_000_000, basis="revenue")) == 10000, "a named basis uses that benchmark (1% of 1,000,000)")
check(om(mat(total_revenue=1_000_000, basis="pbt")) == 10000, "a named basis with no figure falls back to what is available")
check(om(mat()) is None and mat().performance_materiality is None, "no figures: no materiality (never zero)")
check(m.performance_materiality == 15000 and m.trivial_threshold == 1000, "performance materiality 75% (15,000); clearly trivial 5% (1,000)")
check(om(mat(total_revenue=1_000_000, basis="garbage")) == 10000, "an invalid basis value does not break it")
check(om(mat(profit_before_tax=-50_000, total_revenue=1_000_000)) == -2500 or om(mat(profit_before_tax=-50_000, total_revenue=1_000_000)) == 10000, "a loss-making year still produces a materiality")

# ------------------------------------------------------------------ income tax losses and deferred tax
T = F.build_income_tax_computation
r = T(1000, [{"item_type": "addback", "amount": 300}, {"item_type": "capital_allowance", "amount": 100}], 150, 25, 3)
# taxable before losses 1200; loss used 150; taxable 1050; tax 262.50; levy 3% of tax = 7.875; total 270.375
check(near(r["taxable_income"], 1050) and near(r["base_tax"], 262.5) and near(r["aids_levy_amount"], 7.875, 1e-9) and near(r["total_tax_charge"], 270.375, 1e-9), "income tax: 1,000 + 300 - 100 = 1,200; less 150 loss = 1,050; tax 262.50 + 3% AIDS levy 7.88")
check(near(r["effective_rate_percent"], 27.0375, 1e-6), "the effective rate is the total charge over profit before tax (27.04%)")
r = T(-300, [{"item_type": "addback", "amount": 100}], 50, 25, 3)
check(near(r["taxable_income"], 0) and near(r["tax_loss_carried_forward"], 250) and r["total_tax_charge"] == 0, "a loss year: old loss 50 plus new loss 200 = 250 carried forward, no tax")
check(T(500, [], 0, 25, 3)["total_tax_charge"] > 0 and T(500, [], 0, None, None)["total_tax_charge"] == 0, "no rate entered gives a nil charge, never a guessed one")
d = F.build_deferred_tax_computation(25, 1000, 600, [{"description": "Provision", "accounting_amount": 0, "tax_base_amount": 80}])
check(near(d["total_deferred_tax"], 0.25 * (400 - 80)) and d["classification"] == "liability", "deferred tax: PPE difference 400 less a provision difference of 80, at 25% = a liability of 80")
d = F.build_deferred_tax_computation(25, 500, 500, [{"description": "Provision", "accounting_amount": 0, "tax_base_amount": 200}])
check(near(d["total_deferred_tax"], -50) and d["classification"] == "asset", "a deductible difference alone gives a deferred tax asset of 50")
d = F.build_deferred_tax_computation(25, 500, 500, [])
check(d["classification"] == "nil" and d["total_deferred_tax"] == 0, "no differences give nil")
d = F.build_deferred_tax_computation(25, None, None, [], ppe_rows=[{"description": "Vehicles", "accounting_amount": 300, "tax_base_amount": 100}, {"description": "Plant", "accounting_amount": 100, "tax_base_amount": 150}])
check(near(d["total_deferred_tax"], 0.25 * (200 - 50)) and len(d["rows"]) == 2, "class-by-class PPE: (200 - 50) x 25% = 37.50")

print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
