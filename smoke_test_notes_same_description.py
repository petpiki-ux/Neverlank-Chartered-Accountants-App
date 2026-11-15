"""Accounts with the same description appear once in the notes: a current-only and a prior-only line of the same name are one note line,
and an adjustment named "Right of Use Asset" lands on "Right-of-Use Assets" (non-breaking hyphens) and not on its accumulated depreciation.
Modelled on Broxmen Investment FY2025. Pure financials test."""
from types import SimpleNamespace as NS
import financials as F

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)

H = "‑"
def L(cat, name, cd=0.0, cc=0.0, pd=0.0, pc=0.0, code=""):
    return NS(fs_category=cat, account_code=code, account_name=name, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc)
def adj(ref, rows, applies_to="current"):
    return NS(reference=ref, description=ref, applies_to=applies_to, carry_to_current=True,
              lines=[NS(account_name=n, fs_category=c, debit=d, credit=cr) for n, c, d, cr in rows])
def note(res, title):
    return next(n for n in res["notes"] if n["title"] == title)

lines = [
    L("ppe", f"Right{H}of{H}Use Assets", cd=150000, pd=150000, code="1641"),
    L("ppe", f"Accumulated Depreciation of Right{H}of{H}Use Assets", cc=2777.78, pc=1515.15, code="1642"),
    L("trade_receivables", "Other Receivables", pd=150000),
    L("trade_receivables", "Other Receivables", cd=150000),
    L("cash", "Bank", cd=100000, pd=100000),
    L("share_capital", "Share capital", cc=2000, pc=2000),
    L("retained_earnings", "Retained earnings", cc=0, pc=0),
]
a = adj("AJE1", [("Right of Use Asset", "ppe", 69478, 0), ("Capital Reserve", "other_reserves", 0, 69478)])
r = F.build_all_statements(lines, adjustments=[a])

rec = note(r, "Trade and other receivables")
check(len(rec["accounts"]) == 1 and rec["accounts"][0]["current"] == 150000 and rec["accounts"][0]["prior"] == 150000,
      "Other Receivables is one line, 150,000 / 150,000 (%s)" % [(x["account_name"], x["current"], x["prior"]) for x in rec["accounts"]])
check(rec["total"]["current"] == 150000 and rec["total"]["prior"] == 150000, "the receivables total is unchanged")

ppe = note(r, "Property, plant and equipment")
rows = {x["account_name"]: x for x in ppe["accounts"]}
rou = rows.get(f"Right{H}of{H}Use Assets")
check(rou is not None and abs(rou["current"] - 219478) < 0.01 and abs(rou["prior"] - 150000) < 0.01,
      "the Right of Use Asset journal is added into the Right-of-Use Assets line (219,478): %s" % [(k, v["current"]) for k, v in rows.items()])
check(not any(k == "Right of Use Asset" for k in rows), "no separate 'Right of Use Asset' line")
acc = rows.get(f"Accumulated Depreciation of Right{H}of{H}Use Assets")
check(acc is not None and abs(acc["current"] - (-2777.78)) < 0.01, "the accumulated depreciation line is untouched")
check(abs(sum(x["current"] for x in ppe["accounts"]) - ppe["total"]["current"]) < 0.01, "the PPE lines add up to the note total")

# two real accounts with the same name that both hold the same year stay apart
two = [L("cash", "Bank", cd=500, pd=500), L("cash", "Bank", cd=700, pd=0), L("share_capital", "Share capital", cc=1200, pc=500)]
c = note(F.build_all_statements(two), "Cash and cash equivalents")
check(len(c["accounts"]) == 2, "two Bank lines that both hold the current year are different accounts and stay apart")

# an accumulated depreciation adjustment still goes to the accumulated depreciation line
b = adj("AJE2", [("Accumulated Depreciation of Right of Use Assets", "ppe", 0, 100), ("Depreciation", "depreciation_amortisation", 100, 0)])
p2 = note(F.build_all_statements(lines, adjustments=[b]), "Property, plant and equipment")
acc2 = next(x for x in p2["accounts"] if x["account_name"].startswith("Accumulated"))
check(abs(acc2["current"] - (-2877.78)) < 0.01, "an accumulated depreciation journal goes onto the accumulated depreciation line (%s)" % acc2["current"])

import sys
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
