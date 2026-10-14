"""Corporate Finance & Business Advisory - calculators that sit on top of the forecast engine:
valuation (WACC, DCF, trading multiples), capital budgeting (NPV / IRR / payback), budget vs actual,
and historical financial analysis with suggested forecast assumptions.

Pure Python (no Flask / database) so every figure can be unit tested and reproduced in the Excel export.
"""
import copy

from cf_engine import f, _div, free_cash_flows, ratios, normalise, run


# =========================================================================== VALUATION

DEFAULT_VALUATION = {
    "wacc": {"rf": 4.2, "erp": 5.5, "beta": 1.0, "crp": 10.0, "size": 3.0, "specific": 2.0,
             "kd": 12.0, "debt_weight": 30.0, "override": None},
    "terminal": {"method": "gordon", "growth": 3.0, "exit_multiple": 5.0},
    "mid_year": True,
    "bridge": {"debt": None, "cash": None, "other": 0.0, "minorities": 0.0},
    "dlom": 0.0,
    "stake_pct": 100.0,
    "comparables": [],
    "ltm": {"revenue": None, "ebitda": None, "pat": None},
    "notes": "",
}


def normalise_valuation(v):
    v = v or {}
    base = copy.deepcopy(DEFAULT_VALUATION)
    w = v.get("wacc") or {}
    for k in ("rf", "erp", "beta", "crp", "size", "specific", "kd", "debt_weight"):
        base["wacc"][k] = f(w.get(k), base["wacc"][k])
    base["wacc"]["debt_weight"] = max(0.0, min(95.0, base["wacc"]["debt_weight"]))
    base["wacc"]["override"] = None if w.get("override") in (None, "") else f(w.get("override"))
    t = v.get("terminal") or {}
    base["terminal"]["method"] = "exit_multiple" if t.get("method") == "exit_multiple" else "gordon"
    base["terminal"]["growth"] = f(t.get("growth"), base["terminal"]["growth"])
    base["terminal"]["exit_multiple"] = max(0.0, f(t.get("exit_multiple"), base["terminal"]["exit_multiple"]))
    base["mid_year"] = bool(v.get("mid_year", True))
    b = v.get("bridge") or {}
    base["bridge"] = {"debt": None if b.get("debt") in (None, "") else f(b.get("debt")),
                      "cash": None if b.get("cash") in (None, "") else f(b.get("cash")),
                      "other": f(b.get("other")), "minorities": f(b.get("minorities"))}
    base["dlom"] = max(0.0, min(90.0, f(v.get("dlom"))))
    base["stake_pct"] = max(0.0, min(100.0, f(v.get("stake_pct"), 100.0)))
    comps = []
    for c in (v.get("comparables") or [])[:30]:
        name = (c.get("name") or "").strip()
        vals = {k: (None if c.get(k) in (None, "") else f(c.get(k))) for k in ("ev_ebitda", "ev_revenue", "pe")}
        if name or any(x is not None for x in vals.values()):
            comps.append({"name": name or "Comparable", **vals})
    base["comparables"] = comps
    ltm = v.get("ltm") or {}
    base["ltm"] = {k: (None if ltm.get(k) in (None, "") else f(ltm.get(k))) for k in ("revenue", "ebitda", "pat")}
    base["notes"] = (v.get("notes") or "")[:4000]
    return base


def wacc(w):
    """CAPM cost of equity (with country, size and company-specific premia), after-tax cost of debt, and the
    blended rate. `tax` is passed in via w['tax']."""
    ke = w["rf"] + w["beta"] * w["erp"] + w["crp"] + w["size"] + w["specific"]
    kd_after = w["kd"] * (1 - w.get("tax", 25.75) / 100.0)
    dw = w["debt_weight"] / 100.0
    calc = ke * (1 - dw) + kd_after * dw
    used = w["override"] if w.get("override") is not None else calc
    return {"ke": ke, "kd_after_tax": kd_after, "debt_weight": w["debt_weight"], "calculated": calc, "used": used}


def net_debt_at_start(result, bridge):
    a = result["a"]
    debt = sum(L["opening_balance"] for L in a["loans"])
    cash = a["opening"]["cash"]
    if cash < 0:
        debt += -cash
        cash = 0.0
    if bridge.get("debt") is not None:
        debt = bridge["debt"]
    if bridge.get("cash") is not None:
        cash = bridge["cash"]
    return debt, cash


def dcf(result, val, wacc_rate=None, growth=None, exit_multiple=None):
    """Discounted cash flow of the unlevered free cash flows of the forecast.
    Gordon growth terminal value is discounted with the same timing as the final cash flow; an exit-multiple
    terminal value is discounted from the end of the final year."""
    v = normalise_valuation(val)
    w = dict(v["wacc"], tax=result["a"]["tax_rate"])
    rate = (wacc_rate if wacc_rate is not None else wacc(w)["used"]) / 100.0
    g = (growth if growth is not None else v["terminal"]["growth"]) / 100.0
    mult = exit_multiple if exit_multiple is not None else v["terminal"]["exit_multiple"]
    fc = free_cash_flows(result)
    n = len(fc)
    rows, pv_sum = [], 0.0
    for d in fc:
        t = d["year_no"] - (0.5 if v["mid_year"] else 0.0)
        df = 1.0 / ((1 + rate) ** t)
        rows.append({**d, "t": t, "df": df, "pv": d["fcff"] * df})
        pv_sum += d["fcff"] * df
    last = fc[-1]
    err = None
    tv_gordon = None
    if rate > g:
        tv_gordon = last["fcff"] * (1 + g) / (rate - g)
    else:
        err = "The discount rate must be higher than the terminal growth rate for a growing-perpetuity value."
    t_g = n - (0.5 if v["mid_year"] else 0.0)
    pv_tv_gordon = tv_gordon / ((1 + rate) ** t_g) if tv_gordon is not None else None
    tv_exit = last["ebitda"] * mult
    pv_tv_exit = tv_exit / ((1 + rate) ** n)
    method = v["terminal"]["method"]
    tv, pv_tv = (tv_exit, pv_tv_exit) if method == "exit_multiple" or tv_gordon is None else (tv_gordon, pv_tv_gordon)
    ev = pv_sum + pv_tv
    debt, cash = net_debt_at_start(result, v["bridge"])
    equity = ev - debt + cash + v["bridge"]["other"] - v["bridge"]["minorities"]
    equity_stake = equity * v["stake_pct"] / 100.0
    equity_after_dlom = equity_stake * (1 - v["dlom"] / 100.0)
    y1 = result["annual"][0]
    return {
        "rows": rows, "wacc": rate * 100.0, "growth": g * 100.0, "exit_multiple": mult, "mid_year": v["mid_year"],
        "pv_fcff": pv_sum, "tv": tv, "pv_tv": pv_tv, "tv_gordon": tv_gordon, "tv_exit": tv_exit,
        "pv_tv_gordon": pv_tv_gordon, "pv_tv_exit": pv_tv_exit, "method": "gordon" if tv is tv_gordon else "exit_multiple",
        "ev": ev, "debt": debt, "cash": cash, "other": v["bridge"]["other"], "minorities": v["bridge"]["minorities"],
        "equity": equity, "equity_stake": equity_stake, "stake_pct": v["stake_pct"], "dlom": v["dlom"],
        "equity_after_dlom": equity_after_dlom,
        "tv_share": _div(pv_tv, ev) if ev else None,
        "ev_ebitda_y1": _div(ev, y1["ebitda"]) if y1["ebitda"] > 0 else None,
        "ev_revenue_y1": _div(ev, y1["revenue"]),
        "implied_exit_multiple": _div(tv_gordon, last["ebitda"]) if tv_gordon is not None and last["ebitda"] > 0 else None,
        "implied_growth": ((tv_exit * rate - last["fcff"]) / (tv_exit + last["fcff"]) * 100.0) if (tv_exit + last["fcff"]) else None,
        "error": err,
    }


def dcf_sensitivity(result, val, wacc_steps=(-2, -1, 0, 1, 2), growth_steps=(-1, -0.5, 0, 0.5, 1)):
    """Enterprise value across WACC (rows) and terminal growth (columns), or exit multiple when that method is selected."""
    v = normalise_valuation(val)
    base = dcf(result, v)
    exit_mode = v["terminal"]["method"] == "exit_multiple"
    cols = [v["terminal"]["exit_multiple"] + s for s in (-1, -0.5, 0, 0.5, 1)] if exit_mode else [v["terminal"]["growth"] + s for s in growth_steps]
    rows = []
    for ws in wacc_steps:
        rate = base["wacc"] + ws
        line = []
        for c in cols:
            d = dcf(result, v, wacc_rate=rate, **({"exit_multiple": c} if exit_mode else {"growth": c}))
            line.append(d["ev"] if (d["method"] == base["method"] or exit_mode) else None)
        rows.append({"wacc": rate, "values": line})
    return {"cols": cols, "rows": rows, "exit_mode": exit_mode}


def _stats(vals):
    vals = sorted(x for x in vals if x is not None and x > 0)
    if not vals:
        return None
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    return {"n": n, "low": vals[0], "median": med, "mean": sum(vals) / n, "high": vals[-1]}


def multiples_valuation(result, val):
    """Trading-multiples valuation. Applies the comparables' EV/EBITDA, EV/Revenue and P/E to the chosen
    earnings base (the first forecast year, or last-twelve-months figures if entered)."""
    v = normalise_valuation(val)
    y1 = result["annual"][0]
    ltm = v["ltm"]
    base = {"ebitda": ltm["ebitda"] if ltm["ebitda"] is not None else y1["ebitda"],
            "revenue": ltm["revenue"] if ltm["revenue"] is not None else y1["revenue"],
            "pat": ltm["pat"] if ltm["pat"] is not None else y1["pat"]}
    debt, cash = net_debt_at_start(result, v["bridge"])
    adj = v["bridge"]["other"] - v["bridge"]["minorities"]
    out = {"base": base, "net_debt": debt - cash, "methods": []}
    for key, label, metric, equity_direct in (("ev_ebitda", "EV / EBITDA", "ebitda", False),
                                              ("ev_revenue", "EV / Revenue", "revenue", False),
                                              ("pe", "Price / Earnings", "pat", True)):
        st = _stats([c[key] for c in v["comparables"]])
        if not st:
            continue
        m = {"key": key, "label": label, "metric": metric, "metric_value": base[metric], "stats": st, "equity": {}, "ev": {}}
        for nm in ("low", "median", "mean", "high"):
            if equity_direct:
                eq = st[nm] * base[metric] + adj
                m["equity"][nm] = eq
                m["ev"][nm] = eq + debt - cash
            else:
                ev = st[nm] * base[metric]
                m["ev"][nm] = ev
                m["equity"][nm] = ev - debt + cash + adj
        out["methods"].append(m)
    return out


def football_field(result, val):
    """Equity-value ranges by method for the summary chart / table."""
    v = normalise_valuation(val)
    base = dcf(result, v)
    lo = dcf(result, v, wacc_rate=base["wacc"] + 1.0, growth=v["terminal"]["growth"] - 0.5,
             exit_multiple=max(0.0, v["terminal"]["exit_multiple"] - 0.5))
    hi = dcf(result, v, wacc_rate=max(0.1, base["wacc"] - 1.0), growth=v["terminal"]["growth"] + 0.5,
             exit_multiple=v["terminal"]["exit_multiple"] + 0.5)
    rows = [{"label": "DCF (WACC +/- 1%, growth +/- 0.5%)", "low": min(lo["equity"], hi["equity"]), "mid": base["equity"],
             "high": max(lo["equity"], hi["equity"])}]
    for m in multiples_valuation(result, v)["methods"]:
        rows.append({"label": m["label"] + " (low - high)", "low": m["equity"]["low"], "mid": m["equity"]["median"], "high": m["equity"]["high"]})
    return rows


# =========================================================================== CAPITAL BUDGETING

DEFAULT_PROJECT = {
    "currency": "USD", "discount_rate": 15.0, "reinvest_rate": 10.0, "initial": 500000.0,
    "residual": 0.0, "cash_flows": [150000.0, 180000.0, 200000.0, 200000.0, 200000.0],
    "name_unit": "Year",
}

FEASIBILITY_SECTIONS = [
    ("summary", "Executive summary"),
    ("market", "Market and demand"),
    ("technical", "Technical and operational"),
    ("financial", "Financial assumptions"),
    ("risks", "Risks and mitigations"),
    ("recommendation", "Recommendation"),
]


def normalise_project(p):
    p = p or {}
    flows = [f(x) for x in (p.get("cash_flows") or [])][:20]
    return {
        "currency": p.get("currency") or "USD",
        "discount_rate": max(0.0, f(p.get("discount_rate"), 15.0)),
        "reinvest_rate": max(0.0, f(p.get("reinvest_rate"), 10.0)),
        "initial": max(0.0, f(p.get("initial"))),
        "residual": f(p.get("residual")),
        "cash_flows": flows or [0.0],
    }


def _npv(rate, flows):
    return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(flows))


def irr(flows):
    """Internal rate of return by bisection on (-99.9%, 1000%); None when the flows never change sign."""
    if not (any(x < 0 for x in flows) and any(x > 0 for x in flows)):
        return None
    lo, hi = -0.999, 10.0
    flo, fhi = _npv(lo, flows), _npv(hi, flows)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = _npv(mid, flows)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def mirr(flows, finance_rate, reinvest_rate):
    n = len(flows) - 1
    pos = sum(cf * (1 + reinvest_rate) ** (n - t) for t, cf in enumerate(flows) if cf > 0)
    neg = sum(cf / (1 + finance_rate) ** t for t, cf in enumerate(flows) if cf < 0)
    if pos <= 0 or neg >= 0 or n <= 0:
        return None
    return (pos / -neg) ** (1.0 / n) - 1.0


def _payback(flows):
    """Years until cumulative cash turns positive (linear within the year); None if never."""
    cum = 0.0
    for t, cf in enumerate(flows):
        prev = cum
        cum += cf
        if t > 0 and cum >= 0 > prev:
            return (t - 1) + (-prev / cf if cf else 0.0)
    return None


def project_analysis(p):
    p = normalise_project(p)
    rate = p["discount_rate"] / 100.0
    flows = [-p["initial"]] + list(p["cash_flows"])
    flows[-1] += p["residual"]
    n = len(flows) - 1
    dflows = [cf / ((1 + rate) ** t) for t, cf in enumerate(flows)]
    cum, dcum, rows, c, d = 0.0, 0.0, [], 0.0, 0.0
    for t, cf in enumerate(flows):
        c += cf
        d += dflows[t]
        rows.append({"year": t, "cash_flow": cf, "cumulative": c, "df": 1 / ((1 + rate) ** t), "pv": dflows[t], "cum_pv": d})
    npv = sum(dflows)
    pv_inflows = sum(x for t, x in enumerate(dflows) if t > 0)
    r_irr = irr(flows)
    eaa = npv * rate / (1 - (1 + rate) ** (-n)) if rate > 0 and n > 0 else (npv / n if n else None)
    return {
        "rows": rows, "npv": npv, "irr": None if r_irr is None else r_irr * 100.0,
        "mirr": None if mirr(flows, rate, p["reinvest_rate"] / 100.0) is None else mirr(flows, rate, p["reinvest_rate"] / 100.0) * 100.0,
        "payback": _payback(flows), "discounted_payback": _payback(dflows),
        "pi": _div(pv_inflows, p["initial"]), "eaa": eaa, "pv_inflows": pv_inflows,
        "total_inflows": sum(flows[1:]), "accept": npv > 0, "flows": flows, "p": p,
    }


def project_sensitivity(p):
    """NPV across discount rates, and NPV when cash inflows / the investment move by +/- 10-30%."""
    p = normalise_project(p)
    base_rate = p["discount_rate"]
    rates = [max(0.0, base_rate + d) for d in (-6, -4, -2, 0, 2, 4, 6)]
    by_rate = [{"rate": r, "npv": project_analysis(dict(p, discount_rate=r))["npv"]} for r in rates]
    steps = (-30, -20, -10, 0, 10, 20, 30)
    by_inflow = [{"change": s, "npv": project_analysis(dict(p, cash_flows=[x * (1 + s / 100.0) for x in p["cash_flows"]],
                                                           residual=p["residual"] * (1 + s / 100.0)))["npv"]} for s in steps]
    by_invest = [{"change": s, "npv": project_analysis(dict(p, initial=p["initial"] * (1 + s / 100.0)))["npv"]} for s in steps]
    return {"by_rate": by_rate, "by_inflow": by_inflow, "by_invest": by_invest}


def project_from_model(result, exclude_terminal=True):
    """Free cash flows of a forecast as project cash flows (year 1..N)."""
    fc = free_cash_flows(result)
    return [d["fcff"] for d in fc]


# =========================================================================== BUDGET vs ACTUAL

BVA_LINES = [
    # key, label, kind (income: higher is favourable; cost: lower is favourable; balance: informational)
    ("revenue", "Revenue", "income"),
    ("cogs", "Cost of sales", "cost"),
    ("gross_profit", "Gross profit", "income"),
    ("opex", "Operating expenses", "cost"),
    ("ebitda", "EBITDA", "income"),
    ("dep", "Depreciation", "cost"),
    ("fin_cost", "Net finance cost", "cost"),
    ("pbt", "Profit before tax", "income"),
    ("tax", "Tax", "cost"),
    ("pat", "Net profit", "income"),
    ("cash", "Closing cash", "income"),
]
BVA_INPUT_KEYS = ["revenue", "cogs", "opex", "dep", "fin_cost", "tax", "cash"]


def normalise_actuals(act):
    out = {}
    for key, vals in (act or {}).items():
        if not str(key).startswith("p"):
            continue
        row = {k: (None if (vals or {}).get(k) in (None, "") else f(vals.get(k))) for k in BVA_INPUT_KEYS}
        if any(x is not None for x in row.values()):
            out[str(key)] = row
    return out


def _actual_row(vals):
    """Fill derived lines (gross profit, EBITDA, PBT, net profit) from the entered ones."""
    g = lambda k: vals.get(k)
    row = dict(vals)
    if g("revenue") is not None and g("cogs") is not None:
        row["gross_profit"] = g("revenue") - g("cogs")
    if row.get("gross_profit") is not None and g("opex") is not None:
        row["ebitda"] = row["gross_profit"] - g("opex")
    if row.get("ebitda") is not None and g("dep") is not None:
        ebit = row["ebitda"] - g("dep")
        if g("fin_cost") is not None:
            row["pbt"] = ebit - g("fin_cost")
            if g("tax") is not None:
                row["pat"] = row["pbt"] - g("tax")
    return row


def budget_vs_actual(result, actuals):
    """Compare the forecast (budget) with actuals entered for periods p0..pN.
    Returns per-period variance tables and a cumulative total over the periods that have actuals."""
    actuals = normalise_actuals(actuals)
    periods = []
    cum_b, cum_a = {}, {}
    for r in result["periods"]:
        key = f"p{r['idx']}"
        if key not in actuals:
            continue
        act = _actual_row(actuals[key])
        lines = []
        for k, label, kind in BVA_LINES:
            b, a_ = r.get(k), act.get(k)
            lines.append(_variance(k, label, kind, b, a_))
            if a_ is not None and k != "cash":
                cum_b[k] = cum_b.get(k, 0.0) + b
                cum_a[k] = cum_a.get(k, 0.0) + a_
        periods.append({"key": key, "label": r["label"], "lines": lines})
    cumulative = []
    for k, label, kind in BVA_LINES:
        if k == "cash":
            if periods:
                last = periods[-1]
                cumulative.append(next(x for x in last["lines"] if x["key"] == "cash"))
            continue
        if k in cum_a:
            cumulative.append(_variance(k, label, kind, cum_b[k], cum_a[k]))
    return {"periods": periods, "cumulative": cumulative, "n": len(periods)}


def _variance(key, label, kind, budget, actual):
    if actual is None or budget is None:
        return {"key": key, "label": label, "kind": kind, "budget": budget, "actual": actual, "var": None, "pct": None, "fav": None}
    var = actual - budget
    pct = (var / abs(budget) * 100.0) if abs(budget) > 1e-9 else None
    fav = (var >= 0) if kind == "income" else (var <= 0)
    if abs(var) < 0.005:
        fav = None
    return {"key": key, "label": label, "kind": kind, "budget": budget, "actual": actual, "var": var, "pct": pct, "fav": fav}


# =========================================================================== HISTORICAL ANALYSIS

HIST_IS = [("revenue", "Revenue"), ("cogs", "Cost of sales"), ("opex", "Operating expenses (excl. depreciation)"),
           ("dep", "Depreciation and amortisation"), ("fin_cost", "Finance costs (net)"), ("tax", "Income tax")]
HIST_BS = [("ppe", "Property, plant and equipment (net)"), ("inventory", "Inventory"), ("receivables", "Trade receivables"),
           ("other_ca", "Other current assets"), ("cash", "Cash and cash equivalents"), ("payables", "Trade payables"),
           ("other_cl", "Other current liabilities"), ("borrowings", "Borrowings"), ("equity", "Total equity")]


def normalise_hist(h):
    h = h or {}
    years = [str(y).strip()[:12] for y in (h.get("years") or [])][:6]
    years = [y for y in years if y] or []
    n = len(years)
    out = {"years": years, "is": {}, "bs": {}}
    for k, _ in HIST_IS:
        vals = (h.get("is") or {}).get(k) or []
        out["is"][k] = [f(vals[i]) if i < len(vals) else 0.0 for i in range(n)]
    for k, _ in HIST_BS:
        vals = (h.get("bs") or {}).get(k) or []
        out["bs"][k] = [f(vals[i]) if i < len(vals) else 0.0 for i in range(n)]
    return out


def historical_analysis(h):
    h = normalise_hist(h)
    n = len(h["years"])
    rows = []
    for i in range(n):
        g = lambda k: h["is"][k][i]
        b = lambda k: h["bs"][k][i]
        rev = g("revenue")
        gp = rev - g("cogs")
        ebitda = gp - g("opex")
        ebit = ebitda - g("dep")
        pbt = ebit - g("fin_cost")
        pat = pbt - g("tax")
        assets = b("ppe") + b("inventory") + b("receivables") + b("other_ca") + b("cash")
        liabilities = b("payables") + b("other_cl") + b("borrowings")
        equity = b("equity")
        cur_assets = b("inventory") + b("receivables") + b("other_ca") + b("cash")
        cur_liab = b("payables") + b("other_cl")
        prev = rows[-1] if rows else None
        r = {
            "label": h["years"][i], "revenue": rev, "gross_profit": gp, "ebitda": ebitda, "ebit": ebit, "pbt": pbt, "pat": pat,
            "cogs": g("cogs"), "opex": g("opex"), "dep": g("dep"), "fin_cost": g("fin_cost"), "tax": g("tax"),
            "total_assets": assets, "total_liabilities": liabilities, "equity": equity,
            "bs_gap": assets - liabilities - equity,
            "growth": _pct(rev, prev["revenue"]) if prev else None,
            "profit_growth": _pct(pat, prev["pat"]) if prev and prev["pat"] > 0 else None,
            "gross_margin": _div(gp, rev), "ebitda_margin": _div(ebitda, rev), "ebit_margin": _div(ebit, rev), "net_margin": _div(pat, rev),
            "roe": _div(pat, equity), "roa": _div(pat, assets), "roce": _div(ebit, equity + b("borrowings")),
            "asset_turnover": _div(rev, assets), "equity_multiplier": _div(assets, equity),
            "current_ratio": _div(cur_assets, cur_liab), "quick_ratio": _div(b("receivables") + b("cash"), cur_liab),
            "debt_equity": _div(b("borrowings"), equity),
            "net_debt_ebitda": _div(b("borrowings") - b("cash"), ebitda) if ebitda > 0 else None,
            "interest_cover": _div(ebit, g("fin_cost")) if g("fin_cost") > 0 else None,
            "receivable_days": _div(b("receivables") * 365.0, rev), "inventory_days": _div(b("inventory") * 365.0, g("cogs")),
            "payable_days": _div(b("payables") * 365.0, g("cogs")),
            "cs": {k: _div(v, rev) for k, v in (("cogs", g("cogs")), ("opex", g("opex")), ("dep", g("dep")), ("fin_cost", g("fin_cost")),
                                                   ("tax", g("tax")), ("pat", pat))},
        }
        r["cash_cycle"] = None if None in (r["receivable_days"], r["inventory_days"], r["payable_days"]) else \
            r["receivable_days"] + r["inventory_days"] - r["payable_days"]
        rows.append(r)
    return {"h": h, "rows": rows}


def _pct(x, y):
    return ((x - y) / abs(y) * 100.0) if y else None


def suggest_assumptions(h, years=5):
    """Turn history into starting forecast drivers. Returns {} when there is no usable history."""
    an = historical_analysis(h)
    rows = an["rows"]
    if not rows or rows[-1]["revenue"] <= 0:
        return {}
    last = rows[-1]
    growths = [r["growth"] for r in rows if r["growth"] is not None]
    g = max(-30.0, min(60.0, sum(growths) / len(growths))) if growths else 5.0
    cogs_pct = 100.0 * last["cogs"] / last["revenue"]
    opex_growth = 5.0
    if len(rows) > 1 and rows[0]["opex"] > 0:
        opex_growth = max(0.0, min(30.0, _pct(last["opex"], rows[0]["opex"]) / (len(rows) - 1)))
    hb = an["h"]["bs"]
    L = len(rows) - 1
    out = {
        "revenue": [{"name": "Revenue", "y1": last["revenue"] * (1 + g / 100.0), "growth": [0.0] + [g] * (years - 1)}],
        "cogs_pct": [cogs_pct] * years,
        "opex": [{"name": "Operating expenses", "kind": "fixed", "y1": last["opex"] * (1 + opex_growth / 100.0),
                  "growth": [0.0] + [opex_growth] * (years - 1), "pct": [0.0] * years, "creditors": True}],
        "rec_days": [last["receivable_days"] or 0.0] * years,
        "inv_days": [last["inventory_days"] or 0.0] * years,
        "pay_days": [last["payable_days"] or 0.0] * years,
        "opening": {"ppe_cost": hb["ppe"][L], "ppe_accdep": 0.0, "inventory": hb["inventory"][L], "receivables": hb["receivables"][L],
                    "other_assets": hb["other_ca"][L], "cash": hb["cash"][L], "payables": hb["payables"][L],
                    "other_liabilities": hb["other_cl"][L],
                    "share_capital": max(0.0, hb["equity"][L]), "tax_loss_bf": 0.0},
        "loans": ([{"name": "Existing borrowings", "opening_balance": hb["borrowings"][L], "drawdown_amount": 0.0, "drawdown_year": 1,
                    "rate": 12.0, "repay_type": "straight", "repay_years": 5, "grace_years": 0, "maturity_year": 5}]
                  if hb["borrowings"][L] > 0 else []),
    }
    return out
