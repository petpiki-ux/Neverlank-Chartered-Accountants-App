"""A valuation of Mining Rights (intangible assets) entered as an ordinary journal: Dr Mining Rights, Cr revaluation surplus in OCI (less deferred tax)
is not a cash flow. The Cash Flow Statement must not show it as an investing outflow, and closing cash must still agree to the trial balance."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from types import SimpleNamespace as N
import financials as F
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.5): return abs(a - b) <= tol
def L(cat, cd=0, cc=0, pd=0, pc=0, name=""): return N(fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc, account_name=name, account_code="", note_description=None)
def AL(cat, d=0, c=0, name=""): return N(fs_category=cat, debit=d, credit=c, account_name=name, account_code="")
def ADJ(lines, source="", applies="current"): return N(lines=lines, source_key=source, reference=source, applies_to=applies, carry_to_current=True, is_balanced=True, description="valuation")

# Opening: mining rights 1,000,000 at cost, cash 500,000, capital 1,500,000. Nothing else moves in the year.
TB = [L("intangible_assets", 1000000, 0, 1000000, 0, "Mining Rights"), L("cash", 500000, 0, 500000), L("share_capital", 0, 1500000, 0, 1500000)]
VAL = 817978.0
DT = round(VAL * 0.2475, 2)
def run(adjs, method="indirect"):
    st = F.build_all_statements(TB, adjs, reporting_framework="full_ifrs", cash_flow_method=method)
    return st["cf"]
# without the journal: no valuation, no variance
check(near(run([])["variance"], 0), "no valuation: no variance")
# valuation journal without the fix would show an outflow of the whole surplus: with it, none
v1 = ADJ([AL("intangible_assets", d=VAL, name="Mining Rights"), AL("oci_items", c=VAL - DT, name="Revaluation surplus"), AL("oci_items", c=0, name="x")])
v1 = ADJ([AL("intangible_assets", d=VAL, name="Mining Rights"), AL("oci_items", c=VAL, name="Revaluation surplus - mining rights")])
for m in ("indirect", "direct"):
    cf = run([v1], m)
    inv = {r["label"]: r["current"] for r in cf["investing_rows"]}
    check(near(cf["net_investing"], 0) and near(cf["variance"], 0), f"{m}: Mining Rights valuation of {VAL:,.0f} is not an investing outflow; cash ties (investing {cf['net_investing']:.0f}, variance {cf['variance']:.0f})")
cf = run([v1])
check(any("valuation of intangible" in r["label"].lower() and near(r["current"], VAL) for r in cf["investing_rows"]), "a non-cash memo line shows the valuation")
# valuation with deferred tax on the surplus (journal posts DTL line)
v2 = ADJ([AL("intangible_assets", d=VAL, name="Mining Rights"), AL("oci_items", c=VAL - DT, name="Revaluation surplus"), AL("oci_items", c=0), AL("deferred_tax_liability", c=DT, name="Deferred tax on valuation")])
v2.lines[1].debit = DT; v2.lines[1].credit = 0
v2 = ADJ([AL("intangible_assets", d=VAL, name="Mining Rights"), AL("oci_items", d=DT, name="Deferred tax on revaluation surplus"), AL("oci_items", c=VAL, name="Revaluation surplus"), AL("deferred_tax_liability", c=DT, name="Deferred tax liability")])
for m in ("indirect", "direct"):
    cf = run([v2], m)
    check(near(cf["variance"], 0) and near(cf["net_investing"], 0), f"{m}: with deferred tax on the surplus the cash flow still ties (variance {cf['variance']:.2f})")
# valuation as a gain in profit or loss
v3 = ADJ([AL("intangible_assets", d=VAL, name="Mining Rights"), AL("other_income", c=VAL, name="Fair value gain on mining rights")])
for m in ("indirect", "direct"):
    cf = run([v3], m)
    check(near(cf["variance"], 0) and near(cf["net_investing"], 0), f"{m}: a fair value gain in profit or loss is taken back out of operating cash flow (variance {cf['variance']:.2f})")
# a real purchase for cash is still an outflow
v4 = ADJ([AL("intangible_assets", d=200000, name="Mining Rights"), AL("cash", c=200000, name="Bank")])
cf = run([v4])
check(near(cf["net_investing"], -200000) and near(cf["variance"], 0), "a cash purchase of mining rights is still an investing outflow of 200,000")
# amortisation / depreciation journal is not read as a valuation
v5 = ADJ([AL("depreciation_amortisation", d=50000, name="Amortisation"), AL("intangible_assets", c=50000, name="Mining Rights")])
check(F.revaluation_noncash([v5]) == {}, "an amortisation journal is not a valuation")
# prior-year journals are ignored
check(F.revaluation_noncash([ADJ(v1.lines, applies="prior")]) == {}, "a prior-year journal is not read as this year's valuation")
print("\nALL PASSED" if not fails else "\nFAILED: %d" % len(fails)); sys.exit(1 if fails else 0)
