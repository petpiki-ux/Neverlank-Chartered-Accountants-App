"""Payroll arithmetic against figures worked out by hand (not by the code under test), plus edge cases and properties that must always hold."""
import os, sys, random
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import payroll_calc as PC
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005): return abs(a - b) <= tol

BANDS = [NS(lower=0, upper=100, rate_pct=0), NS(lower=100, upper=300, rate_pct=20), NS(lower=300, upper=1000, rate_pct=25), NS(lower=1000, upper=None, rate_pct=30)]
SET = NS(nssa_employee_pct=4.5, nssa_employer_pct=4.5, nssa_insurable_ceiling=700.0, aids_levy_pct=3.0, apwcs_pct=1.25)
def item(cat, amount, taxable=True, nssa=True): return NS(category=cat, amount=amount, taxable=taxable, nssa_applicable=nssa)

# ---------------------------------------------------------------- PAYE bands, by hand
for income, expect in [(0, 0), (-50, 0), (50, 0), (100, 0), (200, 20), (300, 40), (301, 40.25), (1000, 215), (2000, 515), (1234.56, 215 + 234.56 * 0.30)]:
    check(near(PC.calculate_paye(income, BANDS), expect), f"PAYE on {income} = {expect:.2f}")
check(PC.calculate_paye(500, []) == 0 and PC.calculate_paye(None, BANDS) == 0, "no bands or no income gives no tax")
check(near(PC.calculate_paye(500, list(reversed(BANDS))), 40 + 200 * 0.25), "bands entered in any order give the same tax")

# ---------------------------------------------------------------- a simple payslip, by hand
# basic 800, no extras. NSSA base = min(800, 700) = 700 -> 4.5% = 31.50 each side.
# taxable = 800 - 31.50 = 768.50 -> PAYE = 40 + (768.50-300) * 25% = 157.125 -> 157.12 or 157.13 (round-half-even on the float)
r = PC.calculate_payslip(800, [], SET, BANDS)
check(near(r["nssa_employee"], 31.50) and near(r["nssa_employer"], 31.50), "NSSA is capped at the insurable ceiling: 700 x 4.5% = 31.50")
check(near(r["taxable_income"], 768.50), "taxable income = basic less the employee's NSSA = 768.50")
check(near(r["paye_before_credits"], 157.125, 0.01), "PAYE on 768.50 = 157.13")
check(near(r["aids_levy"], r["paye_tax"] * 0.03, 0.01), "AIDS levy is 3% of PAYE")
check(near(r["net_pay"], 800 - r["paye_tax"] - r["aids_levy"] - 31.50), "net pay = gross - PAYE - levy - NSSA")
check(near(r["apwcs"], 10.0), "APWCS = 1.25% of basic only = 10.00")

# ---------------------------------------------------------------- allowances, exempt income, credits, deductions
r = PC.calculate_payslip(500, [item("Allowance", 100), item("Allowance", 50, taxable=False), item("Exempt Income", 40), item("Deduction", 30), item("Tax Credit", 5)], SET, BANDS)
check(near(r["gross_pay"], 690), "gross = 500 basic + 150 allowances + 40 exempt = 690")
check(near(r["nssa_employee"], 690 * 0.045), "NSSA counts basic, every allowance and the exempt bonus (690 x 4.5% = 31.05)")
check(near(r["taxable_income"], 500 + 100 - 31.05), "taxable = basic + taxable allowance - NSSA; the non-taxable allowance and exempt income stay out")
paye = 40 + (568.95 - 300) * 0.25
check(near(r["paye_before_credits"], paye, 0.01) and near(r["paye_tax"], paye - 5, 0.01), "a 5.00 tax credit comes off PAYE")
check(near(r["net_pay"], 690 - r["paye_tax"] - r["aids_levy"] - 31.05 - 30, 0.01), "net pay also takes off the 30 deduction")
r2 = PC.calculate_payslip(500, [item("Allowance", 100), item("Allowance", 50, taxable=False), item("Exempt Income", 40), item("Tax Credit", 5)], SET, BANDS)
check(near(r2["nssa_employee"], r["nssa_employee"]), "a deduction (e.g. the employee's own medical aid) never lowers NSSA earnings")
r3 = PC.calculate_payslip(500, [item("Allowance", 90, nssa=False)], SET, BANDS)
check(near(r3["nssa_employee"], 500 * 0.045) and near(r3["gross_pay"], 590), "an allowance flagged not-NSSA (employer medical aid) counts in gross but not in NSSA")
r4 = PC.calculate_payslip(500, [item("Tax Credit", 10000)], SET, BANDS)
check(r4["paye_tax"] == 0 and r4["aids_levy"] == 0 and r4["net_pay"] > 0, "a credit larger than the tax gives zero PAYE, never a refund")

# ---------------------------------------------------------------- edge cases
r = PC.calculate_payslip(0, [], SET, BANDS)
check(all(v == 0 for v in r.values()), "a nil payslip is all zeros")
r = PC.calculate_payslip(None, [], SET, BANDS)
check(r["net_pay"] == 0, "a missing salary is treated as nil")
nc = NS(nssa_employee_pct=4.5, nssa_employer_pct=4.5, nssa_insurable_ceiling=None, aids_levy_pct=3.0, apwcs_pct=1.25)
check(near(PC.calculate_payslip(5000, [], nc, BANDS)["nssa_employee"], 225.0), "no ceiling set: NSSA on the whole salary")
z = NS(nssa_employee_pct=0, nssa_employer_pct=0, nssa_insurable_ceiling=0, aids_levy_pct=0, apwcs_pct=0)
r = PC.calculate_payslip(1000, [], z, BANDS)
check(near(r["net_pay"], 1000 - 215) and r["aids_levy"] == 0, "all statutory rates at zero: only PAYE is taken (1000 -> 215)")
r = PC.calculate_payslip(100, [item("Deduction", 500)], SET, BANDS)
check(near(r["net_pay"], 100 - 4.5 - 500), "deductions larger than pay give a negative net pay rather than being hidden")

# ---------------------------------------------------------------- properties over many random payslips
random.seed(7)
bad = []
prev_paye = 0
for n in range(400):
    basic = round(random.uniform(0, 8000), 2)
    items = [item(random.choice(["Allowance", "Exempt Income", "Deduction", "Tax Credit"]), round(random.uniform(0, 600), 2), taxable=random.random() < .7, nssa=random.random() < .8)
             for _ in range(random.randint(0, 4))]
    q = PC.calculate_payslip(basic, items, SET, BANDS)
    gross = q["gross_pay"]
    ok = (near(q["net_pay"], gross - q["paye_tax"] - q["aids_levy"] - q["nssa_employee"] - q["other_deductions_total"], 0.011)
          and q["paye_tax"] >= 0 and q["aids_levy"] >= 0 and q["taxable_income"] >= 0 and q["nssa_employee"] <= 700 * .045 + .005
          and q["paye_tax"] <= q["paye_before_credits"] + 0.001 and near(q["aids_levy"], q["paye_tax"] * .03, 0.011) and q["taxable_income"] <= gross + 0.001)
    if not ok: bad.append((basic, items, q))
check(not bad, "400 random payslips: net = gross less every deduction, nothing negative, NSSA within the ceiling, levy = 3% of PAYE")
incomes = sorted(random.uniform(0, 5000) for _ in range(300))
tax = [PC.calculate_paye(x, BANDS) for x in incomes]
check(all(b >= a - 1e-9 for a, b in zip(tax, tax[1:])), "PAYE never falls as income rises")
check(all(PC.calculate_paye(x + 1, BANDS) - PC.calculate_paye(x, BANDS) <= 0.30 + 1e-9 for x in incomes), "an extra 1.00 of income never costs more than the top rate")

print("\n%d failing" % len(fails))
sys.exit(1 if fails else 0)
