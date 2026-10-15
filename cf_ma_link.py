"""Forecast continuation: link a Management Accounts pack to a forecast model.

The forecast picks up exactly where the actuals stop. From the pack's latest actual balance sheet and its year-to-date
(or recent) results this module derives a forecast model's assumptions:

* the model starts the month after the reporting month;
* its opening balance sheet IS the actual balance sheet at the reporting month;
* year-1 revenue, costs, working-capital days, depreciation, interest and tax are the run-rates seen in the actuals,
  adjusted by whatever the preparer enters;
* the model's monthly results for the rest of the financial year become the pack's Forecast, so the pack shows
  actual to date + forecast for the remaining months.

Everything here is plain Python (no database, no Flask) so it can be tested directly.
"""
import copy
import re

import cf_engine as E
import cf_ma as M

f = M.f


class LinkError(Exception):
    """Something the user must fix before a continuation can be built (message is shown as is)."""


DEFAULT_PARAMS = {
    "years": 3,            # model length
    "basis": "ytd",        # ytd | recent3  - which actual months the run-rate is taken from
    "season": "auto",      # auto (budget phasing if the budget covers the year) | flat
    "rev_adj": 0.0,        # % change applied to the run-rate revenue for the next 12 months
    "cost_adj": 0.0,       # % change applied to the run-rate operating expenses
    "growth": None,        # revenue growth % for years 2+ (None = derived from the actuals)
    "opex_growth": 5.0,    # operating-expense growth % for years 2+
    "capex": None,         # annual capex (None = replace what is depreciated)
    "tax_rate": None,      # % (None = the rate seen in the actuals, else the standard rate)
    "payout": None,        # dividend payout % of profit (None = as seen in the actuals)
    "repay_years": 5,      # years over which existing borrowings are repaid
    "keep_forward": True,  # when rolling forward, keep growth / capex / loan terms the preparer has set in the model
}

BASIS_LABELS = {"ytd": "Year to date (all months so far)", "recent3": "Latest three months"}
NON_CREDITOR = re.compile(r"salar|wage|payroll|staff|remunerat|depreciat|amorti|bad debt|provision", re.I)


def _opt(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalise_params(raw):
    raw = raw or {}
    p = dict(DEFAULT_PARAMS)
    try:
        p["years"] = max(1, min(E.MAX_YEARS, int(float(raw.get("years", p["years"])))))
    except (TypeError, ValueError):
        pass
    if raw.get("basis") in BASIS_LABELS:
        p["basis"] = raw["basis"]
    if raw.get("season") in ("auto", "flat"):
        p["season"] = raw["season"]
    p["rev_adj"] = max(-90.0, min(500.0, f(raw.get("rev_adj"), 0.0)))
    p["cost_adj"] = max(-90.0, min(500.0, f(raw.get("cost_adj"), 0.0)))
    g = _opt(raw.get("growth"))
    p["growth"] = None if g is None else max(-50.0, min(200.0, g))
    p["opex_growth"] = max(-50.0, min(100.0, f(raw.get("opex_growth"), DEFAULT_PARAMS["opex_growth"])))
    c = _opt(raw.get("capex"))
    p["capex"] = None if c is None else max(0.0, c)
    t = _opt(raw.get("tax_rate"))
    p["tax_rate"] = None if t is None else max(0.0, min(100.0, t))
    d = _opt(raw.get("payout"))
    p["payout"] = None if d is None else max(0.0, min(100.0, d))
    try:
        p["repay_years"] = max(1, min(30, int(float(raw.get("repay_years", p["repay_years"])))))
    except (TypeError, ValueError):
        pass
    kf = raw.get("keep_forward", p["keep_forward"])
    p["keep_forward"] = kf not in (False, "0", "false", "off", "no", 0)
    return p


# --------------------------------------------------------------------------- anchor: what the actuals say now

def anchor(d):
    """The actual position the forecast continues from. Raises LinkError with a plain-English reason when there is none."""
    structure = M.normalise_structure(d.get("structure"))
    months = M.fy_months(d)
    idx = M.report_index(d)
    cum = M.cumulative_actuals(d)[idx]["cum"]
    if cum is None:
        raise LinkError("There are no actual results for the reporting month yet. Load the actuals first.")
    series = M.bs_series(d)
    bs_month = None
    for i in range(idx, -1, -1):
        if months[i] in series:
            bs_month = months[i]
            break
    if bs_month is None:
        raise LinkError("There is no actual balance sheet up to the reporting month. A continuation needs one to open from "
                        "(load a trial balance or enter a balance sheet on the Actuals tab).")
    return {"idx": idx, "n": idx + 1, "month": months[idx], "months": months, "structure": structure,
            "cum": cum, "bs_month": bs_month, "bs": series[bs_month], "vec": M.pl_vector(structure, cum)}


def fingerprint(d):
    """A short string that changes when the actuals the forecast was built from change (including by an Excel re-import)."""
    try:
        a = anchor(d)
    except LinkError:
        return ""
    t = M.bs_total(a["bs"])
    return "|".join([a["month"], a["bs_month"], f"{a['vec']['revenue']:.0f}", f"{a['vec']['pat']:.0f}", f"{t['assets']:.0f}", f"{t['liabilities']:.0f}"])


# --------------------------------------------------------------------------- seasonality

def _budget_weights(d, structure):
    """Share of the year's budget revenue falling in each month of the financial year, or None if the budget doesn't cover it."""
    bud = M.budget_months(d)
    months = M.fy_months(d)
    if not all(m in bud for m in months):
        return None
    rkeys = [r["key"] for r in structure["revenue"]]
    rev = [sum(f(bud[m].get(k)) for k in rkeys) for m in months]
    tot = sum(rev)
    if tot <= 0 or min(rev) < 0:
        return None
    return [v / tot for v in rev]


# --------------------------------------------------------------------------- assumptions from the actuals

def continuation_assumptions(d, params=None, currency="USD"):
    """(assumptions, info). info carries the notes, figures and basis shown to the preparer."""
    p = normalise_params(params)
    A = anchor(d)
    structure, idx, n, months = A["structure"], A["idx"], A["n"], A["months"]
    notes = []

    # which actual months the run-rate is taken from
    mon = M.monthly_actuals(d)
    window = list(range(0, n))
    if p["basis"] == "recent3" and n > 3:
        window = [i for i in range(n - 3, n) if mon[i]["values"] is not None] or list(range(0, n))
    use_ytd = window == list(range(0, n))
    keys = M.pl_keys(structure)
    if use_ytd:
        tot = {k: f(A["cum"].get(k)) for k in keys}
    else:
        tot = {k: sum(f(mon[i]["values"].get(k)) for i in window) for k in keys}
    nw = len(window)

    # seasonality and the share of a year the window represents
    w = None
    if p["season"] == "auto":
        w = _budget_weights(d, structure)
        if w is None and p["season"] == "auto":
            notes.append("No full-year budget, so the forecast is spread evenly over the months (flat seasonality).")
    share = sum(w[i] for i in window) if w else nw / 12.0
    if share <= 0:
        share = nw / 12.0
        w = None
    season = [100.0 * w[(n + i) % 12] for i in range(12)] if w else [100.0 / 12] * 12
    if w:
        notes.append("Seasonality follows the shape of the budget's revenue across the year.")

    # revenue: annual run-rate per line (+ other income as its own stream so the balance sheet ties to the profit)
    rev_lines = structure["revenue"]
    rev_total_w = sum(tot[r["key"]] for r in rev_lines) + tot["oth_inc"]
    if rev_total_w <= 0:
        raise LinkError("The actuals show no revenue to project from. Check the Actuals tab.")
    k_rev = (1.0 + p["rev_adj"] / 100.0) / share
    streams = []
    for r in rev_lines:
        streams.append({"name": r["label"], "y1": max(0.0, tot[r["key"]] * k_rev), "growth": [0.0] + [0.0] * (p["years"] - 1)})
    has_oth = abs(tot["oth_inc"]) > 0.5
    if has_oth:
        streams.append({"name": "Other income", "y1": max(0.0, tot["oth_inc"] * k_rev), "growth": [0.0] * p["years"]})
    annual_rev = sum(s["y1"] for s in streams) / (1.0 + p["rev_adj"] / 100.0)   # run-rate before the adjustment

    # revenue growth for years 2+
    g = p["growth"]
    if g is None:
        pp = M.prior_pl(d)
        prior_rev = sum(f(pp.get(r["key"])) for r in rev_lines) if pp else 0.0
        if prior_rev > 0:
            g = max(-20.0, min(30.0, (annual_rev / prior_rev - 1.0) * 100.0))
            notes.append(f"Revenue growth for later years ({g:.1f}%) is the run-rate against the prior year's revenue; change it if that is not what you expect.")
        else:
            g = 5.0
            notes.append("Revenue growth for later years is assumed at 5% because there is no prior year to compare with.")
    for s in streams:
        s["growth"] = [0.0] + [g] * (p["years"] - 1)

    # cost of sales as % of all income that carries cost
    income_base = rev_total_w
    cogs_pct = max(0.0, 100.0 * tot["cogs"] / income_base) if income_base > 0 else 0.0

    # operating expenses: monthly average x 12 (not seasonal)
    k_cost = 12.0 / nw * (1.0 + p["cost_adj"] / 100.0)
    opex = []
    for o in structure["opex"]:
        y1 = max(0.0, tot[o["key"]] * k_cost)
        opex.append({"name": o["label"], "kind": "fixed", "y1": y1, "growth": [0.0] + [p["opex_growth"]] * (p["years"] - 1),
                     "pct": [0.0] * p["years"], "creditors": not bool(NON_CREDITOR.search(o["label"]))})
    ann = lambda k: tot[k] * 12.0 / nw

    # opening balance sheet = the actual balance sheet
    bs = A["bs"]
    ppe = max(0.0, f(bs.get("ppe")))
    cash = f(bs.get("cash")) - f(bs.get("overdraft"))
    opening = {"ppe_cost": ppe, "ppe_accdep": 0.0, "inventory": f(bs.get("inventory")), "receivables": f(bs.get("receivables")),
               "other_assets": f(bs.get("other_assets")), "cash": cash, "payables": f(bs.get("payables")),
               "other_liabilities": f(bs.get("other_liabilities")), "share_capital": f(bs.get("share_capital")), "tax_loss_bf": 0.0}
    tb = M.bs_total(bs)
    if abs(tb["check"]) > 1.0:
        notes.append(f"The actual balance sheet at {M.month_label(A['bs_month'], False)} is out by {tb['check']:,.0f}; the forecast opens from it with "
                     "reserves as the balancing figure. Fix it on the Actuals tab (or use Diagnose) and roll the forecast forward.")
    if A["bs_month"] != A["month"]:
        notes.append(f"There is no balance sheet for {M.month_label(A['month'], False)}; the forecast opens from the one at {M.month_label(A['bs_month'], False)}.")

    # depreciation, capex
    dep_ann = max(0.0, ann("dep"))
    if ppe > 0 and dep_ann > 0:
        dep_rate = max(0.0, min(100.0, 100.0 * dep_ann / ppe))
    else:
        dep_rate = 15.0
        notes.append("No depreciation (or no fixed assets) in the actuals, so depreciation is assumed at 15%.")
    capex = p["capex"] if p["capex"] is not None else dep_ann
    if p["capex"] is None:
        notes.append("Capital expenditure is assumed equal to annual depreciation (assets are replaced); enter your own figure if different.")

    # borrowings and interest
    debt = f(bs.get("borrowings")) + f(bs.get("overdraft"))
    int_exp_ann = max(0.0, ann("int_exp"))
    rate = max(0.0, min(60.0, 100.0 * int_exp_ann / debt)) if debt > 0 and int_exp_ann > 0 else 12.0
    loans = []
    if f(bs.get("borrowings")) > 0:
        loans.append({"name": "Existing borrowings", "opening_balance": f(bs.get("borrowings")), "drawdown_amount": 0.0, "drawdown_year": 1,
                      "rate": rate, "repay_type": "straight", "repay_years": p["repay_years"], "grace_years": 0, "maturity_year": p["years"]})
        notes.append(f"Existing borrowings are assumed to be repaid evenly over {p['repay_years']} years at {rate:.1f}% (the rate implied by the actual interest).")
    dep_rate_cash = max(0.0, min(30.0, 100.0 * ann("int_inc") / cash)) if cash > 0 and ann("int_inc") > 0 else 2.0

    # working-capital days from the closing balances against the run-rate
    rev_ann = annual_rev
    cogs_ann = max(0.0, ann("cogs")) if use_ytd else max(0.0, tot["cogs"] / share)
    cred_ann = sum(o["y1"] for o in opex if o["creditors"]) / (1.0 + p["cost_adj"] / 100.0) if opex else 0.0
    days = lambda bal, base: max(0.0, min(720.0, 365.0 * bal / base)) if base > 0 else 0.0
    rec_days = days(opening["receivables"], rev_ann)
    inv_days = days(opening["inventory"], cogs_ann)
    pay_days = days(opening["payables"], cogs_ann + cred_ann)

    # tax, dividends
    pbt = f(A["vec"]["pbt"]) if use_ytd else sum(f(M.pl_vector(structure, mon[i]["values"])["pbt"]) for i in window)
    tax_act = tot["tax"]
    if p["tax_rate"] is not None:
        tax_rate = p["tax_rate"]
    elif pbt > 1 and 0 <= tax_act <= 0.5 * pbt:
        tax_rate = 100.0 * tax_act / pbt
        notes.append(f"Tax is projected at {tax_rate:.1f}% of profit, the rate in the actuals.")
    else:
        tax_rate = E.TAX_RATE_DEFAULT
        notes.append(f"Tax is projected at the standard rate ({E.TAX_RATE_DEFAULT}%).")
    if p["payout"] is not None:
        payout = p["payout"]
    else:
        pat = pbt - tax_act
        payout = max(0.0, min(100.0, 100.0 * tot["div"] / pat)) if pat > 1 and tot["div"] > 0 else 0.0

    a = E.default_assumptions(p["years"], currency, "blank", M.shift_month(A["month"], 1))
    a.update({
        "years": p["years"], "start_month": M.shift_month(A["month"], 1), "currency": currency, "tax_rate": tax_rate,
        "seasonality": season, "revenue": streams, "cogs_pct": [cogs_pct] * p["years"], "opex": opex,
        "capex": [capex] * p["years"], "dep_rate": dep_rate, "rec_days": [rec_days] * p["years"],
        "inv_days": [inv_days] * p["years"], "pay_days": [pay_days] * p["years"], "opening": opening, "loans": loans,
        "equity_issue": [0.0] * p["years"], "dividend_payout": [payout] * p["years"],
        "deposit_rate": dep_rate_cash, "overdraft_rate": max(rate, 0.0) if f(bs.get("overdraft")) > 0 else 18.0,
    })
    a = E.normalise(a)
    info = {"notes": notes, "report_month": A["month"], "start_month": a["start_month"], "bs_month": A["bs_month"],
            "months_used": nw, "basis": "ytd" if use_ytd else "recent3", "season_from_budget": bool(w),
            "annual_revenue_runrate": annual_rev, "opening_reserves": E.opening_reserves(a),
            "actual_retained": f(bs.get("retained")), "has_other_income": has_oth, "pl_lines": len(streams) + len(opex)}
    return a, info


def overlay_forward(a_new, a_old):
    """Rolling forward: keep the forward-looking choices the preparer made in the model (growth, capex, loan terms,
    dividends, equity, interest and tax rates) while the opening position and year-1 run-rates come from the new actuals."""
    out = copy.deepcopy(a_new)
    old = E.normalise(a_old or {})
    n = out["years"]
    pad = lambda lst, fill: (list(lst) + [fill] * n)[:n]
    # growth by stream / expense name
    for sec in ("revenue", "opex"):
        by = {x["name"].strip().lower(): x for x in old[sec]}
        for x in out[sec]:
            o = by.get(x["name"].strip().lower())
            if o:
                x["growth"] = pad(o["growth"], o["growth"][-1] if o["growth"] else 0.0)
                x["growth"][0] = 0.0
    out["capex"] = pad(old["capex"], old["capex"][-1] if old["capex"] else 0.0)
    out["equity_issue"] = pad(old["equity_issue"], 0.0)
    out["dividend_payout"] = pad(old["dividend_payout"], old["dividend_payout"][-1] if old["dividend_payout"] else 0.0)
    for k in ("tax_rate", "dep_rate", "deposit_rate", "overdraft_rate", "covenants"):
        out[k] = copy.deepcopy(old[k])
    out["rec_days"] = pad(old["rec_days"], old["rec_days"][-1] if old["rec_days"] else 0.0)
    out["inv_days"] = pad(old["inv_days"], old["inv_days"][-1] if old["inv_days"] else 0.0)
    out["pay_days"] = pad(old["pay_days"], old["pay_days"][-1] if old["pay_days"] else 0.0)
    # loans: the existing borrowings take the old terms; new planned loans carry over
    oldl = {L["name"].strip().lower(): L for L in old["loans"]}
    for L in out["loans"]:
        o = oldl.get(L["name"].strip().lower())
        if o:
            for k in ("rate", "repay_type", "repay_years", "grace_years", "maturity_year"):
                L[k] = o[k]
    for L in old["loans"]:
        if L["opening_balance"] == 0 and L["drawdown_amount"] > 0:
            out["loans"].append(copy.deepcopy(L))
    return E.normalise(out)


# --------------------------------------------------------------------------- model result -> the pack's forecast

def plan_from_result(structure, result, a, fy_month_keys, n_actual, oth_stream=False):
    """The model's monthly results as pack figures for the financial year's remaining months.
    Returns {"pl": {month: {line: value}}, "bs": {month: {...}}, "months": [...]}. Streams and expense lines map by position
    (the model was built from the pack's lines in order); an 'Other income' stream goes to the pack's Other income line."""
    rkeys = [r["key"] for r in structure["revenue"]]
    okeys = [o["key"] for o in structure["opex"]]
    first = a["start_month"]
    pl, bs = {}, {}
    for i, r in enumerate(result["periods"][:12]):
        mk = M.shift_month(first, i)
        if mk not in fy_month_keys[n_actual:]:
            continue
        v = {k: 0.0 for k in M.pl_keys(structure)}
        for j, key in enumerate(rkeys):
            v[key] = f(r["streams"][j])
        if oth_stream and len(r["streams"]) > len(rkeys):
            v["oth_inc"] = f(r["streams"][len(rkeys)])
        v["cogs"] = f(r["cogs"])
        for j, key in enumerate(okeys):
            v[key] = f(r["opex_lines"][j])
        v["dep"] = f(r["dep"])
        v["int_inc"] = f(r["int_income"])
        v["int_exp"] = f(r["int_loans"]) + f(r["int_od"])
        v["tax"] = f(r["tax"])
        v["div"] = f(r["dividends"])
        pl[mk] = {k: round(x, 2) for k, x in v.items()}
        bs[mk] = {k: round(f(r[src]), 2) for k, src in (
            ("ppe", "ppe_nbv"), ("inventory", "inventory"), ("receivables", "receivables"), ("other_assets", "other_assets"),
            ("cash", "cash_positive"), ("overdraft", "overdraft"), ("payables", "payables"), ("other_liabilities", "other_liabilities"),
            ("borrowings", "borrowings"), ("share_capital", "share_capital"), ("retained", "retained"))}
    return {"pl": pl, "bs": bs, "months": sorted(pl)}


def forecast_dataset(d, plan, link, stamp=None):
    """The pack's Forecast dataset: earlier forecast months are kept, the remaining months replaced by the continuation."""
    old = d.get("forecast") or {}
    months = M.fy_months(d)
    n = M.report_index(d) + 1
    keep = set(months[:n])
    pl = {m: v for m, v in (old.get("pl") or {}).items() if m in keep}
    bs = {m: v for m, v in (old.get("bs") or {}).items() if m in keep}
    pl.update(plan["pl"])
    bs.update(plan["bs"])
    try:
        ob = anchor(d)["bs"]
    except LinkError:
        ob = old.get("opening_bs")
    note = (f"Continuation of the actuals to {M.month_label(link['built_for'], False)}: model {link.get('model_number', '')} "
            f"from {M.month_label(link['start_month'], False)}.")
    return {"source": "continuation", "pl": pl, "bs": bs, "opening_bs": ob, "model_id": link.get("model_id"),
            "model_number": link.get("model_number"), "model_name": link.get("model_name"), "scenario": link.get("scenario"),
            "note": note, "saved": stamp}


# --------------------------------------------------------------------------- link bookkeeping

def link_status(d):
    """What the pack's Budget & forecast tab (and exports) should say about the link. None when there isn't one."""
    link = d.get("forecast_link")
    if not isinstance(link, dict) or not link.get("model_id"):
        return None
    cur = M.report_month(d)
    st = dict(link)
    st["current_month"] = cur
    for k_, src_ in (("built_label", "built_for"), ("start_label", "start_month")):
        try:
            st[k_] = M.month_label(str(link.get(src_))[:7], False)
        except Exception:
            st[k_] = str(link.get(src_) or "")
    st["moved_on"] = link.get("built_for") != cur
    st["actuals_changed"] = (not st["moved_on"]) and bool(link.get("fingerprint")) and link["fingerprint"] != fingerprint(d)
    st["stale"] = st["moved_on"] or st["actuals_changed"]
    forecast = d.get("forecast") or {}
    st["pack_in_step"] = forecast.get("source") == "continuation" and forecast.get("model_id") == link.get("model_id")
    if st["moved_on"]:
        st["message"] = (f"The actuals now run to {M.month_label(cur, False)} but the forecast was built from {M.month_label(link.get('built_for'), False)}. "
                         "Roll the forecast forward so that it starts after the latest actuals.")
    elif st["actuals_changed"]:
        st["message"] = ("The actuals for the reporting month have changed since the forecast was built (for example a corrected Excel pack "
                         "was imported). Roll the forecast forward to open from the latest figures.")
    elif not st["pack_in_step"]:
        st["message"] = "The pack's forecast has been replaced or edited since it was taken from the model. Refresh it from the model to relink."
    else:
        st["message"] = ""
    return st


def links_for_model(model_id, packs):
    """Packs whose forecast link points at this model: [{pack, status}]. `packs` are objects with .data and the usual attributes."""
    out = []
    for pk in packs:
        try:
            d = pk.data or {}
        except Exception:
            continue
        lk = d.get("forecast_link")
        if isinstance(lk, dict) and lk.get("model_id") == model_id:
            d["structure"] = M.normalise_structure(d.get("structure"))
            d["settings"] = M.settings_of(d)
            out.append({"pack": pk, "status": link_status(d)})
    return out


def check_continuity(d, a, result):
    """A few facts that prove the forecast really continues the actuals, for the preparer and for the tests."""
    A = anchor(d)
    o = result["opening_reserves"]
    first = result["periods"][0]
    return {
        "opening_equals_actual_bs": abs(o - f(A["bs"].get("retained"))) < 1.0,
        "reserves_gap": o - f(A["bs"].get("retained")),
        "starts_month": a["start_month"], "expected_start": M.shift_month(A["month"], 1),
        "starts_after_actuals": a["start_month"] == M.shift_month(A["month"], 1),
        "model_balances": result["checks"]["balance_ok"], "first_period": first["label"],
    }
