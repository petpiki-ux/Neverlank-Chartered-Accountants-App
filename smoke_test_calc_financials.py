"""Financial-statement, tax-computation, deferred-tax, QPD and date arithmetic against hand-worked figures, with edge cases and invariants."""
import os, sys, tempfile, random
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from types import SimpleNamespace as N
from datetime import date
import financials as F, qpd_calc as Q, leave_calc as LC
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005): return abs(a - b) <= tol
def L(cat, cd=0, cc=0, pd=0, pc=0): return N(fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc)

# A balanced trial balance worked out by hand.
# Current: revenue 1000, cost of sales 600, admin 150, depreciation 50, finance costs 20, tax 45 -> profit before tax 180, profit 135.
# Retained earnings brought forward 275 (= prior opening 200 + prior profit 75), so closing = 275 + 135 = 410.
TB = [L("ppe", 310, 0, 325), L("inventories", 200, 0, 150), L("trade_receivables", 300, 0, 250), L("cash", 150, 0, 100),
      L("share_capital", 0, 100, 0, 100), L("retained_earnings", 0, 275, 0, 200), L("long_term_borrowings", 0, 200, 0, 250), L("trade_payables", 0, 250, 0, 200),
      L("revenue", 0, 1000, 0, 800), L("cost_of_sales", 600, 0, 500), L("admin_expenses", 150, 0, 130), L("depreciation_amortisation", 50, 0, 40),
      L("finance_costs", 20, 0, 25), L("income_tax_expense", 45, 0, 30)]
check(near(sum(l.current_debit for l in TB), sum(l.current_credit for l in TB)) and near(sum(l.prior_debit for l in TB), sum(l.prior_credit for l in TB)), "the test trial balance balances in both years")

t = F.compute_totals(TB)
check(t["revenue"]["current"] == 1000 and t["cost_of_sales"]["current"] == 600 and t["trade_payables"]["prior"] == 200, "credit accounts are positive when in credit, debit accounts when in debit")
check(near(t["ppe"]["current"], 310) and near(t["cash"]["prior"], 100), "assets come out as debit minus credit")
t2 = F.compute_totals(TB + [L("trade_payables", 30, 0, 0, 0), L("no_such_category", 999, 0, 0, 0), L("excluded", 77, 0, 0, 0), N(fs_category="cash", current_debit=None, current_credit=None, prior_debit=None, prior_credit=None)])
check(near(t2["trade_payables"]["current"], 220) and "excluded" not in t2, "a debit on a credit account reduces it; unknown and excluded categories are ignored; blanks count as nil")

pl = F.build_income_statement(t)
check(near(pl["profit_before_tax"]["current"], 180) and near(pl["profit_for_year"]["current"], 135), "profit before tax 180 and profit for the year 135")
check(near(pl["profit_before_tax"]["prior"], 800 - 500 - 130 - 40 - 25) and near(pl["profit_for_year"]["prior"], 75), "prior year: PBT 105 and profit 75")
labels = {r["label"]: r for r in pl["rows"]}
check(near(labels["Gross profit"]["current"], 400) and near(labels["Operating profit"]["current"], 200), "gross profit 400 and operating profit 200 (depreciation sits in administrative expenses)")
check(near(labels["Administrative expenses"]["current"], -200), "administrative expenses shown with depreciation: 150 + 50 = 200")

eq = F.build_equity_statement(t, pl)
check(near(eq["retained_earnings_closing"], 410) and near(eq["retained_earnings_closing_prior_year"], 275), "retained earnings roll forward: 275 + 135 = 410 (prior: 200 + 75 = 275)")
check(near(eq["total_closing"], 510) and near(eq["total_opening"], 375), "total equity 510 closing, 375 opening")
sfp = F.build_financial_position(t, eq)
check(near(sfp["total_assets"]["current"], 960) and near(sfp["total_equity_and_liabilities"]["current"], 960), "total assets and total equity and liabilities are both 960")
check(near(sfp["balance_check"]["current"], 0) and near(sfp["balance_check"]["prior"], 0), "the statement of financial position balances in both years")
check(near(sfp["total_assets"]["prior"], 825), "prior year assets 825")

# an out-of-balance trial balance must show as an imbalance, never be hidden
bad = F.compute_totals(TB + [L("cash", 40, 0, 0, 0)])
bad_sfp = F.build_financial_position(bad, F.build_equity_statement(bad, F.build_income_statement(bad)))
check(near(bad_sfp["balance_check"]["current"], 40), "a trial balance that is 40 out shows a balance check of 40")

for method in ("indirect", "direct"):
    cf = F.build_cash_flow(t, pl, method)
    check(near(cf["cash_open"], 100) and near(cf["cash_close_actual"], 150) and near(cf["net_movement"], 50) and near(cf["variance"], 0),
          f"{method} cash flow: opening 100 + movement 50 = closing 150, no unexplained variance")
    check(near(cf["net_operating"] + cf["net_investing"] + cf["net_financing"], cf["net_movement"]), f"{method}: operating + investing + financing = net movement")
    check(near(cf["net_operating"], 85) and near(cf["net_financing"], -50), f"{method}: net operating 85 (150 generated less interest 20 and tax 45), financing -50")
ci = F.build_cash_flow(t, pl, "indirect"); cd = F.build_cash_flow(t, pl, "direct")
check(near(ci["net_operating"], cd["net_operating"]) and near(ci["net_investing"], cd["net_investing"]), "indirect and direct methods give the same operating and investing totals")
dr = {r["label"]: r.get("current") for r in cd["operating_rows"]}
check(near(dr["Cash received from customers"], 950) and near(dr["Cash paid to suppliers and employees"], -800), "direct method: customers paid 1000 - 50 more owed = 950; suppliers and staff 800")

# a cash line that does not agree with the movements must show up as a variance
t_bad = F.compute_totals(TB[:3] + [L("cash", 190, 0, 100)] + TB[4:])
cfb = F.build_cash_flow(t_bad, F.build_income_statement(t_bad), "indirect")
check(near(abs(cfb["variance"]), 40), "a cash balance 40 higher than the other movements explain shows as a variance of 40")

# ---------------------------------------------------------------- whole-number presentation
check([F.whole(x) for x in (0.5, 1.5, 2.5, -0.5, -1.5, 2.4, -2.4, 0.49)] == [1, 2, 3, -1, -2, 2, -2, 0], "whole numbers round halves away from zero (2.5 -> 3, -2.5 -> -3)")
check(str(F.whole(-0.2)) in ("0", "0.0") and "-" not in str(F.whole(-0.2)), "a tiny negative rounds to plain 0, never -0")

# ---------------------------------------------------------------- income tax computation, by hand
r = F.build_income_tax_computation(1000, [{"item_type": "addback", "amount": 200}, {"item_type": "deduction", "amount": 50}, {"item_type": "capital_allowance", "amount": 150}], 0, 24.7, 3)
check(near(r["taxable_income"], 1000), "taxable income 1000 + 200 add-backs - 50 deductions - 150 allowances = 1000")
check(near(r["base_tax"], 247) and near(r["aids_levy_amount"], 7.41) and near(r["total_tax_charge"], 254.41), "24.7% of 1000 = 247 plus 3% AIDS levy = 254.41")
check(near(r["effective_rate_percent"], 25.441, 0.001), "effective rate 25.441%")
r = F.build_income_tax_computation(1000, [], 400, 25, 0)
check(near(r["loss_utilised"], 400) and near(r["taxable_income"], 600) and near(r["tax_loss_carried_forward"], 0) and near(r["base_tax"], 150), "a 400 loss brought forward is used in full: taxable 600, tax 150")
r = F.build_income_tax_computation(300, [], 1000, 25, 3)
check(near(r["loss_utilised"], 300) and near(r["taxable_income"], 0) and near(r["tax_loss_carried_forward"], 700) and r["total_tax_charge"] == 0, "a 1000 loss with 300 profit: 300 used, 700 carried forward, no tax")
r = F.build_income_tax_computation(-200, [{"item_type": "addback", "amount": 50}], 100, 25, 3)
check(near(r["taxable_income_before_losses"], -150) and r["loss_utilised"] == 0 and near(r["tax_loss_carried_forward"], 250) and r["total_tax_charge"] == 0, "a current-year loss of 150 is added to the 100 brought forward: 250 carried forward")
r = F.build_income_tax_computation(500, [], 0, None, None)
check(r["total_tax_charge"] == 0 and r["taxable_income"] == 500, "no rate entered gives a nil charge rather than a guessed one")
r = F.build_income_tax_computation(0, [{"item_type": "addback", "amount": 10}], 0, 25, 3)
check(r["effective_rate_percent"] is None, "no division by zero when profit before tax is nil")
random.seed(3)
ok = True
for _ in range(300):
    pbt = random.uniform(-500, 2000); bf = random.uniform(0, 800)
    lines = [{"item_type": random.choice(["addback", "deduction", "capital_allowance"]), "amount": random.uniform(0, 300)} for _ in range(random.randint(0, 5))]
    q = F.build_income_tax_computation(pbt, lines, bf, 25, 3)
    ok &= q["taxable_income"] >= 0 and q["tax_loss_carried_forward"] >= -1e-9 and q["loss_utilised"] <= bf + 1e-9
    ok &= near(q["loss_utilised"] + q["tax_loss_carried_forward"] - max(-q["taxable_income_before_losses"], 0), bf, 1e-6)
    ok &= near(q["total_tax_charge"], q["taxable_income"] * .25 * 1.03, 1e-6)
check(ok, "300 random computations: taxable income never negative; losses used + carried forward always add back to the loss brought forward plus any new loss")

# ---------------------------------------------------------------- deferred tax
d = F.build_deferred_tax_computation(25, 800, 500, [])
check(near(d["total_deferred_tax"], 75) and d["classification"] == "liability", "carrying amount 800 against tax base 500 at 25%: deferred tax liability 75")
d = F.build_deferred_tax_computation(25, 300, 500, [])
check(near(d["total_deferred_tax"], -50) and d["classification"] == "asset", "carrying amount below the tax base: deferred tax asset 50")
d = F.build_deferred_tax_computation(25, 500, 500, [])
check(d["classification"] == "nil", "no difference: nil")
d = F.build_deferred_tax_computation(25, 800, 500, [{"description": "Provisions", "accounting_amount": 100, "tax_base_amount": 0}, {"description": "Prepaid", "accounting_amount": 0, "tax_base_amount": 40}])
check(near(d["total_deferred_tax"], 75 + 25 - 10) and len(d["rows"]) == 3, "PPE 75 + provision 25 - 10 on the other temporary difference = 90 net")
d = F.build_deferred_tax_computation(25, None, None, [], ppe_rows=[{"description": "Vehicles", "accounting_amount": 400, "tax_base_amount": 100}, {"description": "Plant", "accounting_amount": 50, "tax_base_amount": 250}])
check(near(d["total_deferred_tax"], 75 - 50), "class by class: vehicles +75 and plant -50 net to 25")
d = F.build_deferred_tax_computation(None, 800, 500, [])
check(d["total_deferred_tax"] == 0, "no rate entered: nil, not a guess")

# ---------------------------------------------------------------- SME size band at every boundary
cases = [((5, 30000, 30000), "micro"), ((6, 100, 100), "small"), ((5, 30001, 100), "small"), ((40, 500000, 500000), "small"), ((41, 1, 1), "medium"),
         ((100, 1_000_000, 1_000_000), "medium"), ((101, 1, 1), "large"), ((10, 1_000_001, 1), "large"), ((0, 0, 0), "micro")]
check(all(F.classify_sme_size(*a) == e for a, e in cases), "size band at each boundary (5/40/100 staff, 30k/500k/1m)")
check(F.classify_sme_size(None, 1, 1) is None and F.classify_sme_size(1, None, 1) is None, "a missing figure leaves the band unclassified")

# ---------------------------------------------------------------- QPD
rates = [N(label="1st", due_month=3, due_day=25, cumulative_pct=10, order=1), N(label="2nd", due_month=6, due_day=25, cumulative_pct=25, order=2),
         N(label="3rd", due_month=9, due_day=25, cumulative_pct=30, order=3), N(label="4th", due_month=12, due_day=20, cumulative_pct=100, order=4)]
ins = Q.build_instalments(1000, list(reversed(rates)))
check([i["label"] for i in ins] == ["1st", "2nd", "3rd", "4th"] and [i["amount"] for i in ins] == [100, 150, 50, 700], "1000 split 10% / 25% / 30% / 100% cumulative: 100, 150, 50, 700")
ok = True
for ch in (0, 0.01, 33.33, 1234.57, 99999.99, 1e7 / 3):
    ok &= near(sum(i["amount"] for i in Q.build_instalments(ch, rates)), round(ch, 2), 0.0001)
check(ok, "instalments always add up to the exact annual charge, to the cent")
check(Q.build_instalments(None, rates)[0]["amount"] == 0 and Q.build_instalments(100, []) == [], "no charge gives nil instalments; no instalment rates gives none")
check(Q.compute_taxable_income_from_vat(1200000, 8.5) == 102000 and Q.compute_taxable_income_from_vat(100, -5) == 0 and Q.compute_taxable_income_from_vat(None, 5) == 0, "VAT-turnover estimate: 8.5% of 1.2m = 102,000; never negative")

# ---------------------------------------------------------------- dates (leave)
check(LC.months_between(date(2026, 1, 15), date(2026, 3, 10)) == 1 and LC.months_between(date(2026, 1, 15), date(2026, 3, 15)) == 2, "completed months: 15 Jan to 10 Mar is 1, to 15 Mar is 2")
check(LC.months_between(date(2026, 1, 31), date(2026, 2, 28)) == 0 and LC.months_between(date(2025, 12, 1), date(2026, 12, 1)) == 12, "31 Jan to 28 Feb is not yet a month; a year is 12")
check(LC.months_between(None, date(2026, 1, 1)) == 0 and LC.months_between(date(2026, 5, 1), date(2026, 5, 1)) == 0 and LC.months_between(date(2026, 5, 1), date(2026, 1, 1)) == 0, "missing, equal or backwards dates give 0")
check(LC.weekdays_between(date(2026, 10, 5), date(2026, 10, 9)) == 5 and LC.weekdays_between(date(2026, 10, 3), date(2026, 10, 4)) == 0, "Mon-Fri is 5 working days; a weekend is 0")
check(LC.weekdays_between(date(2026, 10, 5), date(2026, 10, 18)) == 10 and LC.weekdays_between(date(2026, 10, 7), date(2026, 10, 7)) == 1, "two full weeks is 10; a single weekday is 1")
check(LC.weekdays_between(date(2026, 10, 9), date(2026, 10, 5)) == 0 and LC.weekdays_between(None, date(2026, 10, 5)) == 0, "backwards or missing range is 0")
ok = True
from datetime import timedelta
for _ in range(300):
    a = date(2026, 1, 1) + timedelta(days=random.randint(0, 400)); b = a + timedelta(days=random.randint(0, 60))
    ok &= LC.weekdays_between(a, b) == sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)
check(ok, "300 random ranges: working days agree with counting day by day")

print("\n%d failing" % len(fails))
sys.exit(1 if fails else 0)
