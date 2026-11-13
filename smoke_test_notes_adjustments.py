"""An audit adjustment (for example the Asset Register's PPE-REG depreciation) appears in the notes on the account line it belongs to,
and the lines of every note add up to the note's total. Pure financials.py test."""
import sys
from types import SimpleNamespace as NS
import financials as F

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)

def L(cat, name, cd=0.0, cc=0.0, pd=0.0, pc=0.0, code=""):
    return NS(fs_category=cat, account_code=code, account_name=name, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc)

lines = [
    L("revenue", "Sales", cc=100000, pc=90000),
    L("admin_expenses", "Rent", cd=20000, pd=18000),
    L("admin_expenses", "Salaries", cd=30000, pd=25000),
    L("ppe", "Motor vehicles - cost", cd=50000, pd=50000),
    L("cash", "Bank", cd=60000, pd=57000),
    L("share_capital", "Share capital", cc=1000, pc=1000),
    L("retained_earnings", "Retained earnings", cc=109000, pc=109000),
]
def adj(ref, rows, applies_to="current", carry=True):
    return NS(reference=ref, description=ref, applies_to=applies_to, carry_to_current=carry,
              lines=[NS(account_name=n, fs_category=c, debit=d, credit=cr) for n, c, d, cr in rows])

def expense_note(res):
    return next(n for n in res["notes"] if n["title"] == "Administrative expenses")

# 1. depreciation arrives only through an adjustment
a1 = adj("PPE-REG", [("Depreciation", "depreciation_amortisation", 5000, 0), ("Accumulated depreciation", "ppe", 0, 5000)])
r = F.build_all_statements(lines, adjustments=[a1])
n = expense_note(r)
names = {x["account_name"]: x["current"] for x in n["accounts"]}
check(abs(names.get("Depreciation", 0) - 5000) < 0.005, "the Administrative expenses note shows a Depreciation line of 5,000 (%s)" % names)
check(abs(sum(x["current"] for x in n["accounts"]) - n["total"]["current"]) < 0.005, "the note's lines add up to its total (55,000 + 5,000... %s)" % n["total"])
check(not any("adjust" in x["account_name"].lower() or "journal" in x["account_name"].lower() for x in n["accounts"]), "no line is labelled as an adjustment or journal")

# 2. an adjustment on an account the trial balance already has is added into that line
a2 = adj("AJE 1", [("Rent", "admin_expenses", 1500, 0), ("Accruals", "trade_payables", 0, 1500)])
r = F.build_all_statements(lines, adjustments=[a1, a2])
n = expense_note(r)
names = {x["account_name"]: x["current"] for x in n["accounts"]}
check(abs(names["Rent"] - 21500) < 0.005 and sum(1 for x in n["accounts"] if x["account_name"] == "Rent") == 1, "an adjustment on Rent is added into the Rent line (21,500)")
check(abs(sum(x["current"] for x in n["accounts"]) - n["total"]["current"]) < 0.005, "...and the lines still add up")
pay = next(x for x in r["notes"] if x["title"] == "Trade and other payables")
check(abs(sum(x["current"] for x in pay["accounts"]) - pay["total"]["current"]) < 0.005 and any(x["account_name"] == "Accruals" for x in pay["accounts"]), "the payables note carries the Accruals line")

# 3. whole numbers: rounding keeps the lines adding up
lines_dec = lines + [L("admin_expenses", "Printing", cd=100.4, pd=50.4)]
a3 = adj("AJE 2", [("Depreciation", "depreciation_amortisation", 333.33, 0), ("Accumulated depreciation", "ppe", 0, 333.33)])
r = F.build_all_statements(lines_dec, adjustments=[a3], whole_numbers=True)
n = expense_note(r)
check(sum(x["current"] for x in n["accounts"]) == n["total"]["current"] and any(x["account_name"] == "Depreciation" for x in n["accounts"]), "in whole numbers the lines add up to the total and Depreciation is there (%s)" % [(x["account_name"], x["current"]) for x in n["accounts"]])

# 4. a prior-year adjustment goes to the prior column
a4 = adj("PPE-REG-PY", [("Depreciation", "depreciation_amortisation", 2000, 0), ("Accumulated depreciation", "ppe", 0, 2000)], applies_to="prior")
r = F.build_all_statements(lines, adjustments=[a4])
n = expense_note(r)
dep = next(x for x in n["accounts"] if x["account_name"] == "Depreciation")
check(abs(dep["prior"] - 2000) < 0.005 and abs(dep["current"]) < 0.005, "a prior-year adjustment shows in the prior column only")
check(abs(sum(x["prior"] for x in n["accounts"]) - n["total"]["prior"]) < 0.005, "...and the prior column adds up")

# 4b. the Asset Register's own wording goes onto the trial balance's Depreciation line (one line, not two)
lines_dep = lines + [L("depreciation_amortisation", "Depreciation", cd=0, pd=291647)]
a5 = adj("PPE-REG", [("Depreciation charge for the year (per Asset Register)", "depreciation_amortisation", 35707, 0), ("Accumulated depreciation", "ppe", 0, 35707)])
r = F.build_all_statements(lines_dep, adjustments=[a5])
n = expense_note(r)
deps = [x for x in n["accounts"] if "epreciation" in x["account_name"]]
check(len(deps) == 1 and deps[0]["account_name"] == "Depreciation" and abs(deps[0]["current"] - 35707) < 0.005 and abs(deps[0]["prior"] - 291647) < 0.005, "the register's charge sits on the one Depreciation line: 35,707 / 291,647 (%s)" % deps)
# a new, different expense is NOT swallowed by an unrelated line
lines_one = [x for x in lines if x.account_name != "Salaries"]
a6 = adj("AJE 9", [("Audit fees", "admin_expenses", 900, 0), ("Accruals", "trade_payables", 0, 900)])
r = F.build_all_statements(lines_one, adjustments=[a6])
n = expense_note(r)
check({x["account_name"] for x in n["accounts"]} == {"Rent", "Audit fees"}, "a different expense gets its own line, not added into Rent")

# 5. no adjustments: the notes are exactly as before
r = F.build_all_statements(lines)
n = expense_note(r)
check([x["account_name"] for x in n["accounts"]] == ["Rent", "Salaries"], "without adjustments the note is unchanged")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
