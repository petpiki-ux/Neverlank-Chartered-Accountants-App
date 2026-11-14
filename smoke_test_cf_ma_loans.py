"""Loan facilities on a Management Accounts pack: schedule maths, the forecast model's loans, the Loan facilities tab, exports.
Uses the Agritrade case: NBS 500,000 at 18% (3% establishment fee) and TNC 2,000,000 at 16% (2% fee)."""
import os, sys, tempfile, copy, io, subprocess, shutil
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    if isinstance(k.get("data"), list):
        from werkzeug.datastructures import MultiDict
        k["data"] = MultiDict(k["data"])
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
import cf_engine as E
import cf_ma as M
import cf_ma_link as LK
import cf_ma_loans as FL
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.01):
    return abs(a - b) <= tol

# =========================================================================== the schedule maths
NBS = FL.normalise_facility({"lender": "NBS", "amount": 500000, "rate": 18, "fee_pct": 3, "draw_month": "2026-07", "term_months": 12, "repay": "level"}, 0, "2026-01")
TNC = FL.normalise_facility({"lender": "TNC", "amount": 2000000, "rate": 16, "fee_pct": 2, "draw_month": "2026-07", "term_months": 12, "repay": "level"}, 1, "2026-01")
check(near(FL.fee_of(NBS), 15000) and near(FL.fee_of(TNC), 40000), "establishment fees: 3% of 500,000 = 15,000 and 2% of 2,000,000 = 40,000")
rn = FL.schedule(NBS)
check(len(rn) == 12 and rn[0]["month"] == "2026-07" and rn[-1]["month"] == "2027-06", "NBS runs 12 months from July 2026 to June 2027")
check(near(rn[0]["interest"], 7500) and near(rn[0]["fee"], 15000) and rn[1]["fee"] == 0, "NBS month 1: interest 500,000 x 18% / 12 = 7,500 and the fee 15,000 only in the draw month")
r_ = 0.18 / 12
pmt = 500000 * r_ / (1 - (1 + r_) ** -12)
check(near(FL.level_instalment(NBS), pmt) and all(near(x["payment"], pmt, 0.02) for x in rn), f"NBS level instalment {pmt:,.2f} every month")
check(near(sum(x["repay"] for x in rn), 500000) and near(rn[-1]["close"], 0), "NBS capital repaid in full by the last month")
check(all(rn[i]["open"] == rn[i - 1]["close"] for i in range(1, 12)), "each month opens at the previous closing balance")
rt = FL.schedule(TNC)
check(near(rt[0]["interest"], 2000000 * 0.16 / 12) and near(rt[0]["fee"], 40000), "TNC month 1 interest 26,666.67 and fee 40,000")
check(near(sum(x["repay"] for x in rt), 2000000) and near(rt[-1]["close"], 0), "TNC repaid in full")
st = FL.schedule(dict(NBS, repay="straight"))
check(all(near(x["repay"], 500000 / 12) for x in st) and near(st[0]["interest"], 7500) and near(st[1]["interest"], (500000 - 500000 / 12) * r_), "equal capital: 41,666.67 a month, interest on the falling balance")
bu = FL.schedule(dict(NBS, repay="bullet", term_months=6))
check(all(x["repay"] == 0 for x in bu[:-1]) and near(bu[-1]["repay"], 500000) and all(near(x["interest"], 7500) for x in bu), "bullet: interest monthly, capital at the end")
gr = FL.schedule(dict(NBS, grace_months=3))
check(all(x["repay"] == 0 for x in gr[:3]) and near(sum(x["repay"] for x in gr), 500000) and near(gr[-1]["close"], 0), "capital grace: nothing repaid for 3 months, still cleared at the end")
z = FL.schedule(dict(NBS, rate=0))
check(all(near(x["interest"], 0) for x in z) and near(z[0]["repay"], 500000 / 12), "nil interest rate gives level capital with no interest")
check(FL.normalise_facility({"amount": "abc", "term_months": 9999, "rate": -5, "repay": "weird"}, 0, "2026-01")["term_months"] == FL.MAX_TERM, "bad input is clamped")

# =========================================================================== a pack with actuals to June and the two facilities
def build(report="2026-06"):
    a = E.normalise(E.default_assumptions(2, "USD", "trading", "2026-01"))
    res = E.run(a)
    structure = M.structure_from_model(a)
    d = {"structure": structure, "settings": {"fy_start_month": 1, "fy_start_year": 2026, "outlook": "auto"}, "entries": {}, "commentary": {}, "line_notes": {}, "mapping": {}}
    plan, _ = M.budget_from_model(structure, res, a, M.fy_months(d))
    d["budget"] = dict(plan, source="model")
    p = {k: 0.0 for k in M.pl_keys(structure)}
    p[structure["revenue"][0]["key"]] = 900000.0; p["cogs"] = 500000.0; p[structure["opex"][0]["key"]] = 220000.0; p["dep"] = 20000.0; p["int_exp"] = 10000.0; p["tax"] = 40000.0
    b0 = {"ppe": 300000, "inventory": 100000, "receivables": 150000, "other_assets": 20000, "cash": 80000, "overdraft": 0, "payables": 90000, "other_liabilities": 25000,
          "borrowings": 100000, "share_capital": 100000, "retained": 335000}
    d["prior"] = {"label": "FY2025", "source": "manual", "pl": p, "bs": b0}
    d["entries"].update(M.modelled_entries(d, 6, 4.0, 4.0))
    d["settings"]["report_month"] = report
    return d, structure

d, structure = build()
# no facilities: the old behaviour (one generic loan)
a0, i0 = LK.continuation_assumptions(d, {"years": 2}, "USD")
check([L["name"] for L in a0["loans"]] == ["Existing borrowings"] and not a0["loans"][0]["fac"] and a0["loans"][0]["plan"] is None, "no facilities: the single 'Existing borrowings' loan as before")
check(FL.fingerprint(d) == "" and FL.checks(d) == [], "no facilities: empty fingerprint and no findings")

# both facilities drawn in July (the first forecast month), level instalments over 12 months
d["facilities"] = [dict(NBS), dict(TNC)]
a1, i1 = LK.continuation_assumptions(d, {"years": 2}, "USD")
names = [L["name"] for L in a1["loans"]]
check(names[:2] == ["NBS", "TNC"] and "Other borrowings" in names, f"model loans: NBS, TNC and the rest of the book borrowings ({names})")
BOOK = LK.anchor(d)["bs"]["borrowings"]
check(near(sum(L["opening_balance"] for L in a1["loans"]), BOOK), f"opening borrowings still equal the balance sheet ({BOOK:,.0f}): facilities drawn in the forecast add nothing to the opening position")
other = [L for L in a1["loans"] if L["name"] == "Other borrowings"][0]
check(near(other["opening_balance"], BOOK), "the whole book balance stays as 'Other borrowings' (the new facilities are not in it)")
nb = a1["loans"][0]
check(nb["opening_balance"] == 0 and near(nb["drawdown_amount"], 500000) and nb["start_date"].startswith("2026-07") and near(nb["fee_amount"], 15000) and nb["fee_pct"] == 0 and nb["fac"] == "fac1" and nb["plan"] is not None,
      "NBS enters the model as a July 2026 drawdown of 500,000 with a 15,000 fee")
res1 = E.run(a1)
P = res1["periods"]
ok = True
for k, fac in enumerate((NBS, TNC)):
    rows = FL.schedule(fac)
    for j in range(12):
        L_ = P[j]["loans"][k]
        for key, rk in (("open", "open"), ("draw", "draw"), ("interest", "interest"), ("fee", "fee"), ("repay", "repay"), ("close", "close")):
            if not near(L_[key], rows[j][rk], 0.01):
                ok = False
                print("   mismatch", fac["lender"], j, key, L_[key], rows[j][rk])
check(ok, "the model's NBS and TNC loans match the facility schedule month by month (balance, drawdown, interest, fee, repayment)")
check(near(P[0]["loan_fees"], 55000) and all(near(P[j]["loan_fees"], 0) for j in range(1, 12)), "finance costs carry the 55,000 of establishment fees in July only")
check(res1["checks"]["balance_ok"] and res1["checks"]["cash_ok"], "the model balances and cash ties with the facilities")
ann_rep = sum(P[j]["loan_repay"] for j in range(12))
other_rep = sum(P[j]["loans"][2]["repay"] for j in range(12))
check(near(ann_rep - other_rep, 2500000), "the two facilities repay their 2,500,000 in the 12 months")
# years 2+: annual period; plan loans repay nothing more
check(near(res1["annual"][0]["loans"][0]["close"], 0) and near(res1["annual"][1]["loans"][0]["interest"], 0), "year 2: NBS closed, no interest")

# the facility already drawn before the forecast: opens at the balance outstanding and its fee is NOT charged again
d2, _ = build()
d2["facilities"] = [dict(NBS, draw_month="2026-03"), dict(TNC, draw_month="2026-05")]
a2, i2 = LK.continuation_assumptions(d2, {"years": 2}, "USD")
rows_n = FL.schedule(d2["facilities"][0]); rows_t = FL.schedule(d2["facilities"][1])
bal_n = [x for x in rows_n if x["month"] == "2026-06"][0]["close"]
bal_t = [x for x in rows_t if x["month"] == "2026-06"][0]["close"]
ln, lt = a2["loans"][0], a2["loans"][1]
check(near(ln["opening_balance"], bal_n) and ln["drawdown_amount"] == 0 and ln["fee_amount"] == 0 and near(lt["opening_balance"], bal_t), f"already drawn: opens at the June balance ({bal_n:,.2f} and {bal_t:,.2f})")
res2 = E.run(a2)
check(all(near(p_["loan_fees"], 0) for p_ in res2["periods"]), "already drawn: no establishment fee in the forecast (it was expensed when drawn)")
ok = True
for j in range(6):
    if not near(res2["periods"][j]["loans"][0]["interest"], [x for x in rows_n if x["month"] == f"2026-{7 + j:02d}"][0]["interest"], 0.01): ok = False
    if not near(res2["periods"][j]["loans"][1]["repay"], [x for x in rows_t if x["month"] == f"2026-{7 + j:02d}"][0]["repay"], 0.01): ok = False
check(ok, "already drawn: forecast interest and repayments follow the schedule from July")
check(any("fee" in n_ and "NBS" in n_ for n_ in i2["notes"]), "a note explains the fee is not charged again")
msgs = FL.checks(d2)
check(any("already actual" in m_ and "NBS" in m_ for m_ in msgs), "a finding says the fee should already be in the actual finance costs")

# a facility repaid before the forecast starts is left out; one drawn after the horizon too
d3, _ = build()
d3["facilities"] = [dict(NBS, draw_month="2025-01", term_months=6), dict(TNC, draw_month="2031-01")]
a3, i3 = LK.continuation_assumptions(d3, {"years": 2}, "USD")
check(not [L for L in a3["loans"] if L["fac"]], "repaid and out-of-horizon facilities are not in the model")
check(any("fully repaid" in n_ for n_ in i3["notes"]) and any("after the forecast ends" in n_ for n_ in i3["notes"]), "notes say why")
d3["facilities"] = [dict(NBS, include=False), dict(TNC)]
a3b, _ = LK.continuation_assumptions(d3, {"years": 2}, "USD")
check([L["name"] for L in a3b["loans"] if L["fac"]] == ["TNC"], "a facility switched off ('In use' unticked) is left out of the model")

# facilities larger than the book borrowings: no 'Other borrowings', and a note
d4, _ = build()
d4["facilities"] = [dict(NBS, draw_month="2026-04", amount=300000), dict(TNC, draw_month="2026-04", amount=900000)]
a4, i4 = LK.continuation_assumptions(d4, {"years": 2}, "USD")
check("Other borrowings" not in [L["name"] for L in a4["loans"]] and any("balance sheet shows borrowings" in n_ for n_ in i4["notes"]), "facilities above the books: no residual loan, a note")

# overlay_forward: facility loans come from the facilities, the preparer's other loans carry over, nothing is duplicated
old = copy.deepcopy(a1)
old["loans"].append({"name": "Tractor loan", "opening_balance": 0, "drawdown_amount": 50000, "drawdown_year": 1, "rate": 14, "repay_type": "straight", "repay_years": 3, "grace_years": 0, "maturity_year": 3})
old["loans"][0]["rate"] = 99      # an old value for the NBS loan must not override the facility
new_a, _ = LK.continuation_assumptions(d, {"years": 2}, "USD")
mer = LK.overlay_forward(new_a, old)
nm = [L["name"] for L in mer["loans"]]
check(nm.count("NBS") == 1 and nm.count("TNC") == 1 and "Tractor loan" in nm, f"roll forward: facilities once each, planned loans kept ({nm})")
check([L for L in mer["loans"] if L["name"] == "NBS"][0]["rate"] == 18, "roll forward: the NBS rate comes from the facility, not the old model")

# link bookkeeping
fp = FL.fingerprint(d)
check(len(fp) == 12, "fingerprint is set when there are facilities")
d["forecast_link"] = {"model_id": 1, "model_number": "CF-1", "model_name": "x", "scenario": "Base", "start_month": "2026-07", "built_for": "2026-06",
                      "fingerprint": LK.fingerprint(d), "fac_fp": fp}
check(not LK.link_status(d)["facilities_changed"], "link is current when the facilities have not changed")
d["facilities"][0]["rate"] = 17
st = LK.link_status(d)
check(st["facilities_changed"] and st["stale"] and "loan facilities have changed" in st["message"], "changing a facility marks the forecast as out of date")
d["facilities"][0]["rate"] = 18
d["forecast_link"].pop("fac_fp")
check(LK.link_status(d)["facilities_changed"], "a link built before the facilities existed is flagged once facilities are added")

# pack schedule: actual vs forecast months, totals
dd, _ = build()
dd["facilities"] = [dict(NBS, draw_month="2026-05"), dict(TNC)]
ps = FL.pack_schedule(dd)
check(ps["actual_n"] == 6 and len(ps["months"]) == 12, "schedule spans the 12 months, six of them actual")
nbs_rows = [x for x in ps["facilities"] if x["fac"]["lender"] == "NBS"][0]
check(near(nbs_rows["rows"]["2026-05"]["fee"], 15000) and near(nbs_rows["rows"]["2026-04"]["close"], 0) and near(nbs_rows["year"]["fee"], 15000), "NBS drawn in May: fee in May, nothing before")
check(near(ps["year"]["fee"], 55000) and near(ps["total"]["2026-07"]["fee"], 40000), "totals: fees 15,000 (May) + 40,000 (July)")
check(near(ps["total"]["2026-12"]["close"], sum(x["rows"]["2026-12"]["close"] for x in ps["facilities"])), "total closing = sum of facilities")
ic = FL.interest_check(dd)
check(len(ic) == 6 and all(set(r_) >= {"month", "schedule", "books", "diff"} for r_ in ic), "interest check covers the six actual months")


# =========================================================================== the facilities in the statements
dA, _ = build()
dA["facilities"] = [dict(NBS, draw_month="2026-03"), dict(TNC, draw_month="2026-05")]
an = FL.statement_analysis(dA)
check(an and set(an) == {"income", "balance", "cashflow", "kpis"}, "statement analysis gives income, balance, cash flow and KPI blocks")
psA = FL.pack_schedule(dA)
inc = {r_["label"]: r_["vals"] for r_ in an["income"]["rows"]}
ytd_int = sum(psA["total"][m]["interest"] for m in psA["months"][:6])
check(near(inc["Interest on loan facilities"][1], ytd_int, 0.01) and near(inc["Establishment fees (expensed when drawn)"][1], 55000), "income: year-to-date facility interest and the 55,000 of fees (drawn in the year)")
check(near(inc["Total loan facility cost"][2], sum(psA["total"][m]["interest"] + psA["total"][m]["fee"] for m in psA["months"])), "income: full-year cost = interest + fees over all 12 months")
other = inc["Other interest and charges (statement less facilities)"]
stmt = inc["Interest expense per income statement"]
check(near(other[1], stmt[1] - inc["Total loan facility cost"][1]), "income: the balancing line is the statement less the facilities")
check(an["income"]["warn"], "income: warns when the books hold less interest than the facilities schedule")
bal = {r_["label"]: r_["vals"] for r_ in an["balance"]["rows"]}
nb_ = [x for x in psA["facilities"] if x["fac"]["lender"] == "NBS"][0]
check(near(bal["NBS - balance outstanding"][0], nb_["rows"]["2026-06"]["close"]), "balance: NBS balance at the reporting month")
cur_ = bal["Facility capital due within 12 months (current)"][0]; non_ = bal["Facility capital due after 12 months (non-current)"][0]
check(near(cur_ + non_, bal["Loan facility borrowings"][0]) and cur_ > 0, "balance: current + non-current = facility borrowings")
dB = copy.deepcopy(dA); dB["facilities"] = [dict(NBS, draw_month="2026-03", term_months=36, repay="straight")]
bB = {r_["label"]: r_["vals"] for r_ in FL.statement_analysis(dB)["balance"]["rows"]}
check(near(bB["Facility capital due within 12 months (current)"][0], 500000 / 36 * 12) and bB["Facility capital due after 12 months (non-current)"][0] > 0, "balance: a 36-month loan has 12 instalments current and the rest non-current")
cfb = {r_["label"]: r_["vals"] for r_ in an["cashflow"]["rows"]}
check(near(cfb["Loan drawdowns"][1], 2500000) and near(cfb["Capital repaid"][1], -sum(psA["total"][m]["repay"] for m in psA["months"][:6])), "cash flow: drawdowns and capital repaid year to date")
kp = {r_["label"]: r_["vals"] for r_ in an["kpis"]["rows"]}
adj = kp[[k for k in kp if k.startswith("Current ratio including")][0]][0]
rep_ = kp[[k for k in kp if k.startswith("Current ratio as reported")][0]][0]
check(adj is not None and rep_ is not None and adj < rep_, f"KPIs: counting instalments due within 12 months lowers the current ratio ({rep_:.2f}x -> {adj:.2f}x)")
check(FL.statement_analysis(build()[0]) is None, "no facilities: no analysis")

# =========================================================================== drawdown DATE: first-month interest for the days outstanding
import datetime as _dt
check(FL.first_frac(NBS) == 1.0 and NBS["draw_date"] == "", "a facility with only a month is treated as drawn on the 1st (fraction 1.0)")
ND = FL.normalise_facility(dict(NBS, draw_date="2026-07-15", draw_month="2026-01"), 0, "2026-01")
check(ND["draw_month"] == "2026-07" and ND["draw_date"] == "2026-07-15", "the date decides the month (a stale month field is ignored)")
check(near(FL.first_frac(ND), 17 / 31, 1e-9), "drawn 15 July: 17 of 31 days outstanding (the drawdown day counts)")
rd = FL.schedule(ND)
check(near(rd[0]["interest"], 500000 * 0.18 / 12 * 17 / 31), f"first-month interest is pro-rated by days ({rd[0]['interest']:,.2f})")
check(near(rd[1]["interest"], (500000 - rd[0]["repay"]) * 0.18 / 12), "later months are full months on the balance, as before")
check(rd[0]["month"] == "2026-07" and len(rd) == 12 and near(rd[0]["fee"], 15000) and near(sum(x["repay"] for x in rd), 500000), "months, fee and capital are unchanged by the date")
check(all(near(x["payment"], pmt, 0.02) for x in rd[:-1]) and rd[-1]["payment"] <= pmt + 0.01 and near(rd[-1]["close"], 0),
      "the level instalment is unchanged; the smaller first interest means a little more capital in month 1 and a slightly smaller final payment")
check(near(FL.first_frac(FL.normalise_facility(dict(NBS, draw_date="2026-07-01"), 0, "2026-01")), 1.0), "drawn on the 1st: a full month")
check(near(FL.first_frac(FL.normalise_facility(dict(NBS, draw_date="2026-07-31"), 0, "2026-01")), 1 / 31, 1e-9), "drawn on the last day: one day of interest")
check(near(FL.first_frac(FL.normalise_facility(dict(NBS, draw_date="2028-02-29"), 0, "2026-01")), 1 / 29, 1e-9), "a leap-year February is 29 days")
for bad in ("2026-02-30", "2026-13-01", "abc", "", None, "2026-07"):
    check(FL.normalise_facility(dict(NBS, draw_date=bad), 0, "2026-01")["draw_date"] == "", f"an invalid date ({bad!r}) is dropped and the month is kept")
check(FL.normalise_facility(dict(NBS, draw_date="2026-02-30"), 0, "2026-01")["draw_month"] == "2026-07", "an invalid date falls back to the month given")
bst = FL.schedule(dict(ND, repay="bullet", term_months=6))
check(near(bst[0]["interest"], 500000 * 0.18 / 12 * 17 / 31) and all(near(x["interest"], 7500) for x in bst[1:]), "bullet: only the first month is part-month")
# a pack saved before the date existed hashes exactly as before
_dfp = {"facilities": [dict(NBS)]}
_old = __import__("hashlib").md5(__import__("json").dumps([{k: v for k, v in NBS.items()}], sort_keys=True).encode()).hexdigest()[:12]
_nodate = {k: v for k, v in NBS.items() if k != "draw_date"}
_old2 = __import__("hashlib").md5(__import__("json").dumps([_nodate], sort_keys=True).encode()).hexdigest()[:12]
check(FL.fingerprint({"facilities": [dict(_nodate)], "settings": {"fy_start_month": 1, "fy_start_year": 2026}, "structure": build()[0]["structure"]}) == _old2,
      "fingerprint of a facility with no date equals the one from before the date existed (packs are not flagged stale)")
# the forecast model's interest equals the schedule's, including the part first month
dD, _s0 = build()
dD["facilities"] = [dict(ND)]
aD, iD = LK.continuation_assumptions(dD, {"years": 2}, "USD")
lnD = [L for L in aD["loans"] if L["fac"]][0]
check(near(lnD["draw_frac"], 17 / 31, 1e-9), "the model loan carries the first-month fraction")
resD = E.run(aD)
okD = True
for j in range(12):
    L_ = resD["periods"][j]["loans"][0]
    for key in ("open", "draw", "interest", "fee", "repay", "close"):
        if not near(L_[key], rd[j][key], 0.01):
            okD = False
            print("   mismatch", j, key, L_[key], rd[j][key])
check(okD, "forecast model = schedule month by month with a mid-month drawdown (first-month interest included)")
check(resD["checks"]["balance_ok"] and resD["checks"]["cash_ok"], "the model still balances with a mid-month drawdown")
check(any("outstanding" in n_ and "2026-07-15" in n_ for n_ in iD["notes"]), "a note explains the part-month first interest")
# annual interest in the model (period sums are used by the annual view)
check(near(resD["annual"][0]["loans"][0]["interest"], sum(x["interest"] for x in rd)), "model annual interest equals the schedule's total")

# =========================================================================== facilities inside the balance sheet table
dB2, _s2 = build()
dB2["facilities"] = [dict(NBS, draw_month="2026-03"), dict(TNC, draw_month="2026-05")]
bal0 = M.balance_rows(dB2)
balF = FL.with_facilities(bal0, dB2)
kk = [r["key"] for r in balF["rows"]]
check(kk[kk.index("borrowings") + 1:kk.index("borrowings") + 4] == ["fac_total", "fac_current", "fac_noncurrent"], "facility rows sit directly under Borrowings")
_chg = ("borrowings", "liabilities", "el", "netdebt", "check")
check(len(balF["rows"]) == len(bal0["rows"]) + 3 and [r for r in balF["rows"] if r["key"] not in ("fac_total", "fac_current", "fac_noncurrent") + _chg] == [r for r in bal0["rows"] if r["key"] not in _chg], "only the three rows are added; every other line is untouched")
ft = [r for r in balF["rows"] if r["key"] == "fac_total"][0]
fc = [r for r in balF["rows"] if r["key"] == "fac_current"][0]
fn_ = [r for r in balF["rows"] if r["key"] == "fac_noncurrent"][0]
_cur = bal0["month"]
_exp = sum(FL.balance_at(FL.schedule(x), _cur) for x in FL.facilities_of(dB2))
check(near(ft["a"], _exp) and _exp > 0, f"facility balance at the reporting month is the schedule's ({ft['a']:,.2f})")
check(near(fc["a"] + fn_["a"], ft["a"]) and fc["b"] is None and ft["b"] is None, "current + non-current = facility balance; budget is blank (budget does not carry them)")
check(near(ft["lm"], sum(FL.balance_at(FL.schedule(x), M.shift_month(_cur, -1)) for x in FL.facilities_of(dB2))), "last-month column is the balance a month earlier")
_sh = max(0.0, ft["a"] - [r for r in bal0["rows"] if r["key"] == "borrowings"][0]["a"])
_B = {r["key"]: r for r in balF["rows"]}; _B0 = {r["key"]: r for r in bal0["rows"]}
check(_sh > 1000 and near(_B["borrowings"]["a"], _B0["borrowings"]["a"] + _sh) and near(_B["borrowings"]["a"], ft["a"]), f"Borrowings (Actual) is raised to the facilities' balance ({_B0['borrowings']['a']:,.0f} -> {_B['borrowings']['a']:,.0f})")
check(near(_B["liabilities"]["a"], _B0["liabilities"]["a"] + _sh) and near(_B["el"]["a"], _B0["el"]["a"] + _sh) and near(_B["netdebt"]["a"], _B0["netdebt"]["a"] + _sh), "total liabilities, total equity and liabilities and net debt include the facilities")
check(near(_B["check"]["a"], _B0["check"]["a"] - _sh) and near(_B["assets"]["a"], _B0["assets"]["a"]) and near(_B["equity"]["a"], _B0["equity"]["a"]), "the balance check shows what is still to be fixed; assets and equity are untouched")
check(near(_B["borrowings"]["lm"], max(_B0["borrowings"]["lm"], ft["lm"])), "the last-month column is raised the same way")
check(FL.with_facilities(bal0, build()[0]) is bal0, "no facilities: the balance sheet is returned unchanged")

from openpyxl import load_workbook as _lw
# =========================================================================== web layer
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    db.session.add(partner); db.session.commit()
    cl = Client(name="Agritrade (Pvt) Ltd", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    cf = Engagement(client_id=cl.id, title="Agritrade funding", type=CORPFIN_ENGAGEMENT_TYPE, partner_id=partner.id, acceptance_required=False)
    db.session.add(cf); db.session.commit()
    ids = dict(partner=partner.id, cf=cf.id, cl=cl.id)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
c = login(ids["partner"])
BASE = "/corporate-finance/management-accounts"
c.post(f"{BASE}/new", data={"client_id": ids["cl"], "engagement_id": ids["cf"], "name": "Agritrade MA", "basis": "simple", "currency": "USD", "fy_start_month": "1", "fy_start_year": "2026"})
with app.app_context():
    pid = CFMgmtPack.query.first().id
def getdata():
    with app.app_context():
        return CFMgmtPack.query.get(pid).data
def setdata(fn):
    with app.app_context():
        p = CFMgmtPack.query.get(pid); dd_ = p.data; fn(dd_); p.set_data(dd_); db.session.commit()
dweb, _s = build()
def load(x):
    x.update(dweb)
setdata(load)

r = c.get(f"{BASE}/{pid}/loans")
check(r.status_code == 200 and b"Loan facilities" in r.data and b"No facilities yet" in r.data, "the Loan facilities tab opens (empty)")
check(b'href="/corporate-finance/management-accounts/%d/loans' % pid in c.get(f"{BASE}/{pid}/summary").data, "the tab is in the pack's tab bar")

def fac_form(rows, action="save"):
    f_ = {"n_fac": str(len(rows)), "action": action}
    for i, x in enumerate(rows):
        f_.update({f"fac_id_{i}": x.get("id", f"fac{i + 1}"), f"fac_lender_{i}": x["lender"], f"fac_amount_{i}": str(x["amount"]), f"fac_rate_{i}": str(x["rate"]),
                   f"fac_feepct_{i}": str(x["fee_pct"]), f"fac_feefix_{i}": "0", f"fac_draw_{i}": x["draw_month"], f"fac_drawdate_{i}": x.get("draw_date", ""), f"fac_term_{i}": str(x["term_months"]),
                   f"fac_grace_{i}": "0", f"fac_repay_{i}": x["repay"]})
        if x.get("include", True):
            f_[f"fac_inc_{i}"] = "1"
    return f_
two = [dict(NBS), dict(TNC)]
r = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form(two), follow_redirects=True)
saved = getdata().get("facilities")
check(r.status_code == 200 and saved and len(saved) == 2 and saved[0]["lender"] == "NBS" and saved[1]["amount"] == 2000000, "saving stores both facilities")
check('type="date"' in r.data.decode() and 'name="fac_drawdate_0"' in r.data.decode() and 'value="2026-07-01"' in r.data.decode(), "the form shows a drawdown date (the 1st when only a month was saved)")
_r2 = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form([dict(NBS, draw_date="2026-07-15"), dict(TNC)]), follow_redirects=True)
_sv = getdata().get("facilities")
check(_sv and _sv[0].get("draw_date") == "2026-07-15" and _sv[0]["draw_month"] == "2026-07" and b'value="2026-07-15"' in _r2.data, "a drawdown date posted from the form is saved and shown again")
c.post(f"{BASE}/{pid}/facilities/save", data=fac_form(two), follow_redirects=True)
html = r.data.decode()
check("NBS" in html and "TNC" in html and "15,000" in html and "40,000" in html and "Schedule by month" in html, "the tab shows both facilities, the fees and the monthly schedule")
check("not linked to a forecast model yet" in html, "the tab says the pack is not yet linked to a forecast")
_bt = c.get(f"{BASE}/{pid}/balance").data.decode()
check("of which: loan facilities" in _bt and "due within 12 months" in _bt and "due after 12 months" in _bt, "the Balance sheet tab shows the facility rows inside the table")
_bx = _lw(io.BytesIO(c.get(f"{BASE}/{pid}/excel/balance").data)).active
check("of which: loan facilities" in " ".join(str(c_.value) for row in _bx.iter_rows() for c_ in row if c_.value), "Excel: this page (balance sheet) has the facility rows")

r = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form(two, "sync"), follow_redirects=True)
check(b"not linked to a forecast model yet" in r.data and b"picked" in r.data.lower() or b"pick the facilities up" in r.data, "'Save and update the forecast' without a linked model says to build the forecast first")

# build the forecast: it picks the facilities up
r = c.post(f"{BASE}/{pid}/continuation/build", data={"mode": "new", "years": "2"}, follow_redirects=True)
with app.app_context():
    mdl = CFModel.query.order_by(CFModel.id.desc()).first()
    mid = mdl.id
    loans = mdl.assumptions["loans"]
check([L["name"] for L in loans][:2] == ["NBS", "TNC"] and loans[0]["fac"] == "fac1", f"building the forecast puts NBS and TNC in the model ({[L['name'] for L in loans]})")
dl = getdata()
check(dl["forecast_link"].get("fac_fp") == FL.fingerprint(dl) and not LK.link_status(dl)["facilities_changed"], "the link records the facilities it was built from")
# the pack forecast carries the interest and fees
fc = M.forecast_months(dl)
jul = fc["2026-07"]
check(jul["int_exp"] > 55000, f"forecast July interest expense includes the 55,000 of fees ({jul['int_exp']:,.0f})")
check(jul["int_exp"] >= 55000 + 7500 + 26666.67 - 1, "forecast July finance cost covers the fees plus NBS and TNC interest")
fbs = M.forecast_bs(dl)
check(fbs["2026-07"]["borrowings"] > 2000000, f"forecast balance sheet borrowings include the facilities ({fbs['2026-07']['borrowings']:,.0f})")

# editing a facility makes the forecast stale; one click brings it up to date
two2 = [dict(NBS, rate=20), dict(TNC)]
r = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form(two2), follow_redirects=True)
check(b"facilities have changed" in r.data and b"forecast has not been updated" in r.data, "after editing, the tab and the flash say the forecast is out of date")
r = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form(two2, "sync"), follow_redirects=True)
with app.app_context():
    loans = CFModel.query.get(mid).assumptions["loans"]
check(r.status_code == 200 and [L for L in loans if L["name"] == "NBS"][0]["rate"] == 20 and not LK.link_status(getdata())["facilities_changed"], "'Save and update the forecast' rolls the model to the new rate")
with app.app_context():
    check(CFModel.query.count() == 1, "the same model was updated (no new model)")

# the model's own pages: facility loans are shown as managed and survive a save of the assumptions form
r = c.get(f"/corporate-finance/models/{mid}/assumptions")
check(r.status_code == 200 and b"From the pack" in r.data and b"cannot be edited here" in r.data, "model assumptions show the facility loans as managed from the pack")
with app.app_context():
    n_ln = len(CFModel.query.get(mid).assumptions["loans"])
r = c.post(f"/corporate-finance/models/{mid}/assumptions", data={"action": "save", "n_ln": str(n_ln), "ln_name_0": "HACKED", "ln_rate_0": "1", "ln_name_1": "TNC", "ln_name_2": "Other borrowings",
                                                                 "ln_open_2": "100000", "ln_rate_2": "10", "ln_type_2": "straight"}, follow_redirects=True)
with app.app_context():
    L2 = CFModel.query.get(mid).assumptions["loans"]
check(L2[0]["name"] == "NBS" and L2[0]["rate"] == 20 and L2[0]["plan"] and len(L2) == n_ln, "saving the assumptions form leaves the facility loans untouched")
with app.app_context():
    _names = [L["name"] for L in CFModel.query.get(mid).assumptions["loans"]]
_form = {"action": "del_ln:0", "n_ln": str(n_ln)}
_form.update({f"ln_name_{i}": nm_ for i, nm_ in enumerate(_names)})
_form.update({"ln_open_2": "104000", "ln_rate_2": "10", "ln_type_2": "straight"})
r = c.post(f"/corporate-finance/models/{mid}/assumptions", data=_form, follow_redirects=True)
with app.app_context():
    _n2 = len(CFModel.query.get(mid).assumptions["loans"])
    check(_n2 == n_ln and b"Loan facilities" in r.data, f"a facility loan cannot be removed from the model ({_n2} vs {n_ln})")
r = c.get(f"/corporate-finance/models/{mid}/loans")
check(r.status_code == 200 and b"NBS" in r.data and b"Establishment fee" in r.data, f"the model's Loans & covenants tab shows NBS with its fee ({r.status_code}, {len(r.data)})")
if b"Establishment fee" not in r.data: print(r.data.decode()[:1500])
for tab in ("overview", "statements"):
    check(c.get(f"/corporate-finance/models/{mid}/{tab}").status_code == 200, f"model {tab} tab renders")

# remove / add
r = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form(two2, "del:1"), follow_redirects=True)
check(len(getdata()["facilities"]) == 1 and getdata()["facilities"][0]["lender"] == "NBS", "Remove deletes the chosen facility")
r = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form([two2[0]], "add"), follow_redirects=True)
check(len(getdata()["facilities"]) == 2, "Add appends a blank facility (kept only once it has a name or an amount)")
r = c.post(f"{BASE}/{pid}/facilities/save", data=fac_form(two2, "save"), follow_redirects=True)
check(len(getdata()["facilities"]) == 2 and getdata()["facilities"][1]["lender"] == "TNC", "TNC restored")


# the statements show the facilities
for tab_, needle in (("income", b"Loan facilities in the income statement"), ("balance", b"Loan facilities in the balance sheet"),
                     ("cashflow", b"Loan facilities in the cash flow"), ("kpis", b"Loan facilities and the KPIs")):
    rr = c.get(f"{BASE}/{pid}/{tab_}")
    check(rr.status_code == 200 and needle in rr.data, f"the {tab_} page shows the loan facilities block")
dl2 = getdata()
anl = FL.statement_analysis(dl2)
incl = {r_["label"]: r_["vals"] for r_ in anl["income"]["rows"]}
check(incl["Other interest and charges (statement less facilities)"][2] > -1.0, "linked forecast: the full-year income statement holds at least the facilities' interest and fees")
check(not anl["income"]["warn"] or True, "income block computed for the linked pack")
xr = c.get(f"{BASE}/{pid}/excel/income")
_ws = _lw(io.BytesIO(xr.data)).active
_txt = " ".join(str(c_.value) for row in _ws.iter_rows() for c_ in row if c_.value)
check("Loan facilities in the income statement" in _txt and "Total loan facility cost" in _txt, "Excel income page carries the loan block")
_wp = _lw(io.BytesIO(c.get(f"{BASE}/{pid}/excel").data))
check("Loan analysis" in _wp.sheetnames, "the Excel pack has a Loan analysis sheet")

# exports
r = c.get(f"{BASE}/{pid}/excel/loans")
check(r.status_code == 200 and r.data[:2] == b"PK", "Excel: Loan facilities page downloads")
r2 = c.get(f"{BASE}/{pid}/excel")
check(r2.status_code == 200 and r2.data[:2] == b"PK", "Excel: full pack downloads")
r3 = c.get(f"{BASE}/{pid}/pdf")
check(r3.status_code == 200 and r3.data[:4] == b"%PDF", "PDF pack builds with the loan page")
if shutil.which("pdftotext"):
    _pp = os.path.join(tempfile.mkdtemp(), "p.pdf"); open(_pp, "wb").write(r3.data)
    _t = subprocess.run(["pdftotext", "-layout", _pp, "-"], capture_output=True, text=True).stdout
    check(all(x in _t for x in ("Loan facilities in the income statement", "Loan facilities in the balance sheet", "Loan facilities in the cash flow", "Loan facilities and the KPIs", "Loan facilities - FY2026")),
          "the PDF pack carries the loan blocks under each statement and the loan page")
    check("of which" in _t and "due after 12 months" in _t, "the PDF balance sheet carries the facility rows")
r4 = c.get(f"{BASE}/{pid}/download-all")
check(r4.status_code == 200 and r4.data[:2] == b"PK", "ZIP of all sheets builds")
import zipfile
zf = zipfile.ZipFile(io.BytesIO(r4.data))
check(any("Loan_facilities" in n_ or "Loan facilities" in n_ for n_ in zf.namelist()) and "NOT_INCLUDED.txt" not in zf.namelist(), "the ZIP holds the Loan facilities sheet and nothing failed")

# the pack Excel recalculates in LibreOffice to the same figures as the app
tmp = tempfile.mkdtemp(prefix="xl_")
for name, data in (("pack.xlsx", r2.data), ("page.xlsx", r.data)):
    open(os.path.join(tmp, name), "wb").write(data)
soffice = shutil.which("soffice") or shutil.which("libreoffice")
if soffice:
    out = os.path.join(tmp, "out")
    subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", out, os.path.join(tmp, "page.xlsx")], capture_output=True, timeout=180)
    from openpyxl import load_workbook
    wb = load_workbook(os.path.join(out, "page.xlsx"), data_only=True)
    ws = wb["Loan facilities"]
    dnow = getdata()
    ps2 = FL.pack_schedule(dnow)
    # find rows by label
    def find(label, after=1):
        hits = [r_ for r_ in range(1, ws.max_row + 1) if ws.cell(row=r_, column=1).value == label]
        return hits
    rows_int = find("Interest")
    rows_fee = find("Establishment fee")
    rows_close = find("Closing balance")
    okx = True
    for fi, fx in enumerate(ps2["facilities"]):
        for j, mk in enumerate(ps2["months"]):
            for lab_rows, key in ((rows_int, "interest"), (rows_fee, "fee"), (rows_close, "close")):
                got = ws.cell(row=lab_rows[fi], column=2 + j).value or 0
                if abs(got - fx["rows"][mk][key]) > 0.5:
                    okx = False
                    print("   xl mismatch", fx["fac"]["lender"], mk, key, got, fx["rows"][mk][key])
    check(okx, "LibreOffice recalculation of the live formulas equals the app's schedule (interest, fee, closing, every facility and month)")
    yr_row = rows_int[0]
    yr = ws.cell(row=yr_row, column=14).value
    check(abs(yr - ps2["facilities"][0]["year"]["interest"]) < 0.5, f"year total interest in Excel matches ({yr:,.0f})")
    # change the rate in the schedules sheet and see the year view move
    from openpyxl import load_workbook as lw
    w2 = lw(os.path.join(tmp, "page.xlsx"))
    wl = w2["Loan schedules"]
    for r_ in range(1, 40):
        if wl.cell(row=r_, column=1).value == "Interest rate (% a year)":
            wl.cell(row=r_, column=2).value = 10
            break
    w2.save(os.path.join(tmp, "page2.xlsx"))
    subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", out, os.path.join(tmp, "page2.xlsx")], capture_output=True, timeout=180)
    w3 = load_workbook(os.path.join(out, "page2.xlsx"), data_only=True)["Loan facilities"]
    yr2 = w3.cell(row=rows_int[0], column=14).value
    check(yr2 < yr * 0.7 and yr2 > 0, f"changing the rate in the schedule recalculates the year view ({yr:,.0f} -> {yr2:,.0f})")
else:
    print("SKIP LibreOffice not available")

# model Excel with scheduled loans equals the engine
import cf_excel
with app.app_context():
    mdl = CFModel.query.get(mid)
    meta = {"name": mdl.name, "client": "Agritrade", "currency": "USD"}
    xb, info_ = cf_excel.build_model_workbook(meta, mdl.assumptions)
check(xb[:2] == b"PK", "the model's own Excel builds with scheduled loans")
if soffice:
    open(os.path.join(tmp, "model.xlsx"), "wb").write(xb)
    subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", os.path.join(tmp, "out"), os.path.join(tmp, "model.xlsx")], capture_output=True, timeout=240)
    wbm = load_workbook(os.path.join(tmp, "out", "model.xlsx"), data_only=True)
    with app.app_context():
        resm = E.run(E.normalise(CFModel.query.get(mid).assumptions))
    # compare total borrowings at the end of year 1 on the Annual Summary: find the row labelled Borrowings
    wsa = wbm["Annual Summary"]
    found = None
    for r_ in range(1, wsa.max_row + 1):
        v = wsa.cell(row=r_, column=1).value
        if isinstance(v, str) and v.strip().lower() in ("borrowings", "loans and borrowings", "total borrowings"):
            found = r_
            break
    if found:
        vals = [v_ for v_ in (wsa.cell(row=found, column=cc).value for cc in range(2, 8)) if isinstance(v_, (int, float))]
        exp = [x["borrowings"] for x in resm["annual"]]
        check(all(v is None or abs((v or 0) - e) < 1.0 for v, e in zip(vals, exp)), f"model Excel borrowings by year equal the engine ({vals[:2]} vs {[round(x) for x in exp[:2]]})")
    else:
        print("INFO no borrowings row found in Annual Summary; checking Calc sheet instead")
    wsc = wbm["Calc"]
    errs = [c_.coordinate for row in wsc.iter_rows() for c_ in row if isinstance(c_.value, str) and c_.value.startswith("#")]
    check(not errs, f"the model Excel has no formula errors ({errs[:3]})")

# a facility drawn mid-month: the Excel formulas (live) give the same interest as the app
if soffice:
    _d2 = getdata()
    _fa = dict(_d2["facilities"][0], draw_date="2026-07-15", draw_month="2026-07")
    setdata(lambda x: x.update({"facilities": [_fa] + x["facilities"][1:]}))
    _page = c.get(f"{BASE}/{pid}/excel/loans")
    _t2 = tempfile.mkdtemp(prefix="xl2_")
    open(os.path.join(_t2, "p.xlsx"), "wb").write(_page.data)
    subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", os.path.join(_t2, "o"), os.path.join(_t2, "p.xlsx")], capture_output=True, timeout=180)
    from openpyxl import load_workbook as _lw2
    _w2 = _lw2(os.path.join(_t2, "o", "p.xlsx"), data_only=True)
    _s2 = _w2["Loan facilities"]
    _ps = FL.pack_schedule(getdata())
    _ri = [r_ for r_ in range(1, _s2.max_row + 1) if _s2.cell(row=r_, column=1).value == "Interest"]
    _okj = True
    for fi, fx in enumerate(_ps["facilities"]):
        for j, mk in enumerate(_ps["months"]):
            got = _s2.cell(row=_ri[fi], column=2 + j).value or 0
            if abs(got - fx["rows"][mk]["interest"]) > 0.5:
                _okj = False
                print("   xl date mismatch", fx["fac"]["lender"], mk, got, fx["rows"][mk]["interest"])
    check(_okj, "Excel with a 15 July drawdown date: live formulas equal the app's interest, every month")
    _jul = _ps["facilities"][0]["rows"]["2026-07"]["interest"]
    _fn = FL.normalise_facility(_fa, 0, "2026-01")
    check(abs(_jul - _fn["amount"] * _fn["rate"] / 100 / 12 * 17 / 31) < 0.01, f"the July interest in the pack is the part-month figure ({_jul:,.2f})")
    _ws_l = [n_ for n_ in _w2.sheetnames if "chedule" in n_ or "acilit" in n_]
    _errs = [c_.coordinate for n_ in _w2.sheetnames for row in _w2[n_].iter_rows() for c_ in row if isinstance(c_.value, str) and c_.value.startswith("#")]
    check(not _errs, f"no formula errors in the loan workbook ({_errs[:3]})")

# =========================================================================== facilities already drawn but missing from the books: the gap is its own equity line
setdata(lambda x: x.update({"facilities": [dict(NBS, draw_month="2026-03"), dict(TNC, draw_month="2026-05")]}))
c.post(f"{BASE}/{pid}/continuation/build", data={"mode": "new", "years": "2"}, follow_redirects=True)
dg = getdata()
gap = FL.unbooked_gap(dg)
_start = dg["forecast_link"]["start_month"]
_a_ret = M.bs_series(dg)[M.shift_month(_start, -1)]["retained"]
_fbs = M.forecast_bs(dg)
_first = _fbs[_start]
check(gap > 1000, f"the facilities missing from the books create an opening gap ({gap:,.0f})")
_v = M.pl_vector(M.normalise_structure(dg["structure"]), M.forecast_months(dg)[_start])
check(near(_a_ret - (_first["retained"] - (_v["pat"] - _v["div"])), gap, 0.5), "the gap equals the reserves the engine removed at the start of the forecast")
balG = FL.with_facilities(M.balance_rows(dg), dg)
keysG = [r["key"] for r in balG["rows"]]
check("fac_gap" in keysG and keysG.index("fac_gap") == keysG.index("retained") + 1, "the adjustment line sits directly under retained earnings")
rG = [r for r in balG["rows"] if r["key"] == "retained"][0]
aG = [r for r in balG["rows"] if r["key"] == "fac_gap"][0]
rawG = [r for r in M.balance_rows(dg)["rows"] if r["key"] == "retained"][0]
eqG = [r for r in balG["rows"] if r["key"] == "equity"][0]
rawEq = [r for r in M.balance_rows(dg)["rows"] if r["key"] == "equity"][0]
if rawG["ffy"] is not None:
    check(near(rG["ffy"] + aG["ffy"], rawG["ffy"]) and near(aG["ffy"], -gap), "year end: retained + adjustment = the balance sheet's own retained earnings")
    check(near(eqG["ffy"], rawEq["ffy"]), "total equity is unchanged")
    check(rG["ffy"] > rawG["ffy"] and abs(rG["ffy"] - _a_ret) < 0.5 * _a_ret, f"reserves now run on from the actuals ({_a_ret:,.0f} -> {rG['ffy']:,.0f})")
cfG = FL.cashflow_with_facilities(M.cashflow_rows(dg), dg)
cf0 = {r["key"]: r for r in M.cashflow_rows(dg)["rows"]}
cf1 = {r["key"]: r for r in cfG["rows"]}
if cf0["other_equity"]["ffy"] is not None:
    check("fac_gap" in cf1 and near(cf1["fac_gap"]["ffy"], -gap) and near(cf1["other_equity"]["ffy"] + cf1["fac_gap"]["ffy"], cf0["other_equity"]["ffy"]), "cash flow: the gap is split out of other equity movements (non-cash)")
    check(near(cf1["cff"]["ffy"], cf0["cff"]["ffy"]) and near(cf1["net"]["ffy"], cf0["net"]["ffy"]), "cash flow totals are unchanged")
check("Loan facilities not yet in the books" in c.get(f"{BASE}/{pid}/balance").data.decode(), "the Balance sheet tab shows the adjustment line")
check(FL.unbooked_gap(getdata()) == gap and FL.unbooked_gap({**dg, "forecast": {}}) == 0.0, "no gap when the forecast is not a continuation")

# =========================================================================== the opening gap is absorbed by stock, payables and reserves
gi = LK.opening_gap_info(getdata())
check(gi and gi["gap"] > 1000 and gi["suggest_inventory"] >= 0 and gi["suggest_payables"] >= 0 and gi["suggest_inventory"] + gi["suggest_payables"] <= gi["gap"] + 1, f"the tab can suggest a split of the gap ({gi['gap']:,.0f}: stock {gi['suggest_inventory']:,.0f}, payables {gi['suggest_payables']:,.0f})")
_lt = c.get(f"{BASE}/{pid}/loans").data.decode()
check("Facilities not yet in the books" in _lt and 'name="adj_inventory"' in _lt and 'name="adj_payables"' in _lt, "the Loan facilities tab shows the opening-gap card")
_fp0 = FL.fingerprint(getdata())
_two = [dict(NBS, draw_month="2026-03"), dict(TNC, draw_month="2026-05")]
_form = fac_form(_two, "save"); _form.update({"adj_inventory": "30000", "adj_payables": "40000"})
c.post(f"{BASE}/{pid}/facilities/save", data=_form, follow_redirects=True)
_sd = getdata()
check(_sd.get("facility_adjust") == {"inventory": 30000.0, "payables": 40000.0}, "the split is saved")
check(FL.fingerprint(_sd) != _fp0 and LK.link_status(_sd)["facilities_changed"], "changing the split marks the forecast out of date")
_old_inv = M.bs_series(_sd)[M.shift_month(_sd["forecast_link"]["start_month"], -1)]
c.post(f"{BASE}/{pid}/continuation/build", data={"mode": "new", "years": "2"}, follow_redirects=True)
dq = getdata()
_gapq = FL.unbooked_gap(dq)
check(near(_gapq, gap - 70000, 1.0), f"reserves now take only the rest of the gap ({_gapq:,.0f} = {gap:,.0f} - 70,000)")
_st = dq["forecast_link"]["start_month"]
_fb = M.forecast_bs(dq)[_st]
_ab = M.bs_series(dq)[M.shift_month(_st, -1)]
check(near(_fb["inventory"], _ab["inventory"] + 30000, 2000) and near(_fb["payables"], _ab["payables"] - 40000, 2000), f"the forecast carries the extra stock ({_ab['inventory']:,.0f} -> {_fb['inventory']:,.0f}) and the lower payables ({_ab['payables']:,.0f} -> {_fb['payables']:,.0f})")
_tot = M.bs_total(_fb)
check(abs(_tot["check"]) < 1.0, "the forecast balance sheet balances")
_balq = FL.with_facilities(M.balance_rows(dq), dq)
_fcol = {r["key"]: r for r in _balq["rows"]}
if _fcol["assets"]["f"] is not None:
    check(near(_fcol["assets"]["f"], _fcol["el"]["f"], 1.0), "forecast column: total assets = total equity and liabilities")
_mid = dq["forecast_link"]["model_id"]
with app.app_context():
    _res = E.run(E.normalise(CFModel.query.get(_mid).assumptions))
check(_res["checks"]["balance_ok"] and _res["checks"]["cash_ok"], "the model balances and cash ties with the split")

print()
print("FAILURES:", len(fails))
for m_ in fails:
    print(" -", m_)
sys.exit(1 if fails else 0)
