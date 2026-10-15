"""Variance model and reforecast for a management accounts pack (pure Python, no Flask / DB).

Three tools that sit on top of cf_ma:

  1. VARIANCE BRIDGE   - walks profit from a comparator (budget, forecast or prior year) to the actual / full-year outlook, one
                         step per P&L line, for the month, year to date or full year. Renders as a waterfall.
  2. DRIVERS           - explains each line's variance by cause: volume, mix and price (when units are entered for a revenue line),
                         activity versus spending (a flexed budget for variable costs), and tagged amounts for timing, one-offs
                         and anything else. Whatever is not explained is shown as unexplained, never hidden.
  3. REFORECAST        - locks the actuals to date and rebuilds the remaining months from a chosen starting point (current outlook,
                         budget, forecast, prior year or run-rate) with what-if adjustments, then compares the full year with the
                         budget, the prior year and the current outlook, and projects the balance sheet and cash for the funding view.

Sign convention (as in cf_ma): every variance is FAVOURABLE-POSITIVE - more income or less cost is positive.
"""
import html as _html

import cf_ma as M

f = M.f
_TOL = 0.5

BASES = [("month", "Month"), ("ytd", "Year to date"), ("fy", "Full year")]
AGAINST = [("budget", "Budget"), ("forecast", "Forecast"), ("prior", "Prior year")]
MEASURES = [("pat", "Profit after tax"), ("pbt", "Profit before tax"), ("ebitda", "EBITDA")]
TAG_TYPES = [("timing", "Timing (will reverse)"), ("one_off", "One-off"), ("volume", "Volume"), ("price", "Price / rate"), ("other", "Other, explained")]
DRIVER_COLS = [("volume", "Volume"), ("mix", "Mix"), ("price", "Price / rate"), ("timing", "Timing"), ("one_off", "One-off"), ("other", "Other"),
               ("unexplained", "Unexplained")]

_OPEX_MEASURE = {"ebitda": {"dep", "int_inc", "int_exp", "tax", "div"}, "pbt": {"tax", "div"}, "pat": {"div"}}


def _lab(x):
    return dict(x)


# --------------------------------------------------------------------------- comparator pairs

def _sum_months(structure, vecs):
    return M._sum_vectors(structure, vecs)


def pair(data, basis="ytd", against="budget"):
    """(actual vector, comparator vector, labels) for a basis and a comparator. Either vector is None when its source is missing."""
    structure = M.normalise_structure(data.get("structure"))
    V = M.vectors(data)
    months, idx, n = V["months"], V["idx"], V["n"]
    vec = lambda d: M.pl_vector(structure, d) if d is not None else None
    fc = M.forecast_months(data)
    ml = M.month_label(V["month"], False)
    if basis == "month":
        act = V["actual_month"]
        comp = {"budget": V["budget_month"], "forecast": V["forecast_month"], "prior": V["prior_month"]}[against]
        alabel = f"Actual {ml}"
    elif basis == "ytd":
        act = V["actual_ytd"]
        fy_ytd = [fc[m] for m in months[:n] if m in fc]
        comp = {"budget": V["budget_ytd"], "forecast": vec(M._sum_vectors(structure, fy_ytd)) if len(fy_ytd) == n else None, "prior": V["prior_ytd"]}[against]
        alabel = f"Actual YTD {ml}"
    else:
        act = V["outlook"]
        fy_all = [fc[m] for m in months if m in fc]
        comp = {"budget": V["budget_fy"], "forecast": vec(M._sum_vectors(structure, fy_all)) if len(fy_all) == 12 else None, "prior": V["prior_fy"]}[against]
        alabel = "Full-year outlook"
    clabel = dict(AGAINST)[against] + {"month": f" {ml}", "ytd": f" YTD {ml}", "fy": " full year"}[basis]
    if against == "prior":
        clabel = M.prior_fy_label(data) + {"month": " (one twelfth)", "ytd": f" (to {ml}, pro rata)", "fy": ""}[basis]
    return act, comp, {"actual": alabel, "comparator": clabel, "basis": dict(BASES)[basis], "against": dict(AGAINST)[against]}


def availability(data):
    """Which (basis, comparator) combinations have the figures to compare: {basis: {against: bool}}."""
    out = {}
    for b, _ in BASES:
        out[b] = {}
        for a, _ in AGAINST:
            act, comp, _l = pair(data, b, a)
            out[b][a] = act is not None and comp is not None
    return out


# --------------------------------------------------------------------------- bridge

def _lines_for_measure(structure, measure):
    skip = _OPEX_MEASURE[measure]
    return [d for d in M.pl_line_defs(structure) if d["key"] not in skip]


def bridge(data, basis="ytd", against="budget", measure="pat", max_steps=11):
    """Waterfall from the comparator's profit to the actual / outlook profit. None when either side is missing."""
    structure = M.normalise_structure(data.get("structure"))
    act, comp, labels = pair(data, basis, against)
    if act is None or comp is None:
        return None
    steps = []
    for d in _lines_for_measure(structure, measure):
        k = d["key"]
        impact = (f(act[k]) - f(comp[k])) if d["income"] else (f(comp[k]) - f(act[k]))
        if abs(impact) >= _TOL:
            steps.append({"key": k, "label": d["label"], "value": impact})
    if len(steps) > max_steps:
        order = sorted(steps, key=lambda s: -abs(s["value"]))
        keep = {s["key"] for s in order[:max_steps - 1]}
        rest = [s for s in steps if s["key"] not in keep]
        steps = [s for s in steps if s["key"] in keep] + [{"key": "_other", "label": f"Other lines ({len(rest)})", "value": sum(s["value"] for s in rest)}]
    start, end = f(comp[measure]), f(act[measure])
    mlabel = dict(MEASURES)[measure]
    return {"basis": basis, "against": against, "measure": measure, "measure_label": mlabel, "labels": labels,
            "start": {"label": f"{mlabel}: {labels['comparator']}", "value": start}, "end": {"label": f"{mlabel}: {labels['actual']}", "value": end},
            "steps": steps, "total": end - start, "check": start + sum(s["value"] for s in steps) - end}


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines[:3] if len(lines) <= 3 else lines[:2] + [lines[2][:width - 1] + "…"]


def _short(v):
    a = abs(v)
    s = f"{a / 1e6:.1f}M" if a >= 1e6 else (f"{a / 1e3:.1f}K" if a >= 1e4 else f"{a:,.0f}")
    return f"({s})" if v < 0 else s


def waterfall_svg(b, width=880, height=340):
    """Inline SVG waterfall for a bridge(). All text is escaped."""
    if not b:
        return ""
    esc = _html.escape
    bars = [{"label": b["start"]["label"], "kind": "total", "lo": min(0, b["start"]["value"]), "hi": max(0, b["start"]["value"]), "value": b["start"]["value"]}]
    run = b["start"]["value"]
    for s in b["steps"]:
        new = run + s["value"]
        bars.append({"label": s["label"], "kind": "up" if s["value"] >= 0 else "down", "lo": min(run, new), "hi": max(run, new), "value": s["value"]})
        run = new
    bars.append({"label": b["end"]["label"], "kind": "total", "lo": min(0, b["end"]["value"]), "hi": max(0, b["end"]["value"]), "value": b["end"]["value"]})
    lo = min(x["lo"] for x in bars + [{"lo": 0}])
    hi = max(x["hi"] for x in bars + [{"hi": 0}])
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.10
    lo, hi = lo - (pad if lo < 0 else 0), hi + pad
    ml, mr, mt, mb = 56, 14, 22, 78
    pw, ph = width - ml - mr, height - mt - mb
    y = lambda v: mt + (hi - v) / (hi - lo) * ph
    n = len(bars)
    slot = pw / n
    bw = min(58.0, slot * 0.62)
    col = {"total": "#2a78d6", "up": "#1baf7a", "down": "#e34948"}
    out = [f'<svg class="ma-waterfall" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{esc(b["measure_label"])} bridge from {esc(b["labels"]["comparator"])} to {esc(b["labels"]["actual"])}" xmlns="http://www.w3.org/2000/svg">']
    # horizontal grid
    step = _nice_step((hi - lo) / 4)
    t = (int(lo / step) - 1) * step
    while t <= hi + step / 2:
        if lo - 1e-9 <= t <= hi + 1e-9:
            yy = y(t)
            out.append(f'<line x1="{ml}" x2="{width - mr}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{"#52514e" if abs(t) < 1e-9 else "#e1e0d9"}" stroke-width="{1.2 if abs(t) < 1e-9 else 1}"/>')
            out.append(f'<text x="{ml - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="11" fill="#6f6d67">{esc(_short(t))}</text>')
        t += step
    run = None
    for i, bar in enumerate(bars):
        cx = ml + slot * (i + 0.5)
        x0 = cx - bw / 2
        top, bot = y(bar["hi"]), y(bar["lo"])
        h = max(1.5, bot - top)
        out.append(f'<rect x="{x0:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{col[bar["kind"]]}" rx="2"><title>{esc(bar["label"])}: {esc(f"{bar["value"]:,.0f}")}</title></rect>')
        above = bar["value"] >= 0 or bar["kind"] != "total"
        ly = top - 5 if (bar["value"] >= 0) else bot + 13
        out.append(f'<text x="{cx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="11.5" font-weight="600" fill="#0b0b0b">{esc(_short(bar["value"]))}</text>')
        if i < n - 1:
            nxt = bars[i + 1]
            level = bar["value"] if bar["kind"] == "total" else None
        for j, ln in enumerate(_wrap(bar["label"], max(8, int(slot / 6.2)))):
            out.append(f'<text x="{cx:.1f}" y="{height - mb + 16 + j * 13}" text-anchor="middle" font-size="11" fill="#52514e">{esc(ln)}</text>')
    out.append("</svg>")
    return "".join(out)


def _nice_step(raw):
    import math
    if raw <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(raw))
    for m_ in (1, 2, 2.5, 5, 10):
        if raw <= m_ * p:
            return m_ * p
    return 10 * p


# --------------------------------------------------------------------------- drivers

def vm_of(data):
    v = data.get("var_model")
    return v if isinstance(v, dict) else {}


def _key(basis, against):
    return f"{basis}:{against}"


def default_variable_pct(key):
    return 100.0 if key == "cogs" else 0.0


def drivers(data, basis="ytd", against="budget"):
    """Split every line's variance (favourable-positive) into drivers. None when a side is missing."""
    structure = M.normalise_structure(data.get("structure"))
    act, comp, labels = pair(data, basis, against)
    if act is None or comp is None:
        return None
    cfg = vm_of(data)
    units = (cfg.get("units") or {}).get(_key(basis, against)) or {}
    vpct = cfg.get("variable_pct") or {}
    tags = [t for t in ((cfg.get("tags") or {}).get(_key(basis, against)) or []) if isinstance(t, dict)]
    defs = [d for d in M.pl_line_defs(structure) if d["key"] != "div"]
    rev_keys = [r["key"] for r in structure["revenue"]]
    # activity: physical when units were entered, else revenue
    usable = [k for k in rev_keys if f((units.get(k) or {}).get("a")) > 0 and f((units.get(k) or {}).get("c")) > 0 and f(act[k]) > 0 and f(comp[k]) > 0]
    if usable:
        qa = sum(f(units[k]["a"]) for k in usable)
        qc = sum(f(units[k]["c"]) for k in usable)
        ratio = qa / qc
        basis_txt = "Activity is measured by the units you entered."
    else:
        ratio = (f(act["revenue"]) / f(comp["revenue"])) if abs(f(comp["revenue"])) > 1e-9 else 1.0
        basis_txt = "Activity is measured by revenue (enter units against the revenue lines for a price / volume / mix split)."
    tag_by = {}
    for t in tags:
        tag_by.setdefault(t.get("line"), []).append(t)
    rows = []
    for d in defs:
        k = d["key"]
        a_, c_ = f(act[k]), f(comp[k])
        total = (a_ - c_) if d["income"] else (c_ - a_)
        r = {"key": k, "label": d["label"], "income": d["income"], "actual": a_, "comparator": c_, "total": total,
             "volume": 0.0, "mix": 0.0, "price": 0.0, "timing": 0.0, "one_off": 0.0, "other": 0.0, "unexplained": 0.0, "method": ""}
        if d["income"] and k in usable:
            qa_, qc_ = f(units[k]["a"]), f(units[k]["c"])
            pa, pc = a_ / qa_, c_ / qc_
            r["volume"] = (ratio * qc_ - qc_) * pc
            r["mix"] = (qa_ - ratio * qc_) * pc
            r["price"] = (pa - pc) * qa_
            r["method"] = "price / volume / mix from units"
        elif not d["income"]:
            vp = f(vpct.get(k), default_variable_pct(k)) / 100.0
            if vp:
                flexed = c_ * (1.0 + vp * (ratio - 1.0))
                r["volume"] = c_ - flexed
                r["price"] = flexed - a_
                r["method"] = f"flexed budget ({vp * 100:g}% variable with activity)"
        explained = r["volume"] + r["mix"] + r["price"]
        resid = total - explained
        for t in tag_by.get(k, []):
            amt = f(t.get("amount"))
            typ = t.get("type") if t.get("type") in dict(TAG_TYPES) else "other"
            r[typ] += amt
            resid -= amt
        r["unexplained"] = resid
        rows.append(r)
    totals = {c: sum(r[c] for r in rows) for c, _ in DRIVER_COLS}
    totals["total"] = sum(r["total"] for r in rows)
    gross = sum(abs(r["total"]) for r in rows)
    unexp = sum(abs(r["unexplained"]) for r in rows)
    return {"basis": basis, "against": against, "labels": labels, "rows": rows, "totals": totals, "ratio": ratio, "activity_note": basis_txt,
            "explained_share": (1.0 - unexp / gross) if gross > 1e-9 else None, "units": units, "usable": usable, "tags": tags,
            "variable_pct": {d["key"]: f(vpct.get(d["key"]), default_variable_pct(d["key"])) for d in defs if not d["income"]},
            "rev_keys": rev_keys}


def narrative(b, dr, cur=""):
    """Plain-English reading of a bridge (and its drivers), written from the numbers."""
    if not b:
        return []
    msgs = []
    lab = b["labels"]
    tot = b["total"]
    word = "ahead of" if tot >= 0 else "behind"
    msgs.append(f"{b['measure_label']} for {('actual ' + lab['actual'][7:]) if lab['actual'].startswith('Actual ') else lab['actual']} is {cur}{b['end']['value']:,.0f}, {cur}{abs(tot):,.0f} {word} {lab['comparator']} ({cur}{b['start']['value']:,.0f}).")
    pos = sorted([s for s in b["steps"] if s["value"] > 0 and s["key"] != "_other"], key=lambda s: -s["value"])[:3]
    neg = sorted([s for s in b["steps"] if s["value"] < 0 and s["key"] != "_other"], key=lambda s: s["value"])[:3]
    if pos:
        msgs.append("Helping: " + "; ".join(f"{s['label']} +{cur}{s['value']:,.0f}" for s in pos) + ".")
    if neg:
        msgs.append("Hurting: " + "; ".join(f"{s['label']} -{cur}{abs(s['value']):,.0f}" for s in neg) + ".")
    if dr:
        t = dr["totals"]
        parts = []
        for c, name in DRIVER_COLS[:-1]:
            if abs(t[c]) >= _TOL:
                parts.append(f"{name.lower()} {'+' if t[c] >= 0 else '-'}{cur}{abs(t[c]):,.0f}")
        if parts:
            msgs.append("By cause: " + ", ".join(parts) + (f"; {cur}{abs(t['unexplained']):,.0f} {'favourable' if t['unexplained'] >= 0 else 'adverse'} is still unexplained." if abs(t["unexplained"]) >= _TOL else "; everything is explained."))
        if dr["explained_share"] is not None:
            msgs.append(f"{dr['explained_share'] * 100:.0f}% of the line-by-line variance has been explained.")
    return msgs


# --------------------------------------------------------------------------- reforecast

BASE_CHOICES = [("current", "The pack's current outlook"), ("budget", "Budget"), ("forecast", "Latest forecast"), ("prior", "Prior year (one twelfth a month)"),
                ("run_rate", "Run-rate of recent actual months")]
SCENARIO_DEFAULTS = {"name": "", "base": "current", "rr_months": 3, "rev_pct": 0.0, "cogs_follows": True, "cogs_pct": 0.0, "opex_pct": 0.0,
                     "from_month": "", "ramp": 0, "tax_mode": "keep", "tax_rate": 25.0, "line_adj": {}}


def _clamp(v, lo, hi, default=0.0):
    v = f(v, default)
    return max(lo, min(hi, v))


def normalise_scenario(raw, data):
    structure = M.normalise_structure(data.get("structure"))
    raw = raw if isinstance(raw, dict) else {}
    keys = set(M.pl_keys(structure))
    sc = dict(SCENARIO_DEFAULTS)
    sc["name"] = str(raw.get("name") or "").strip()[:60]
    sc["base"] = raw.get("base") if raw.get("base") in dict(BASE_CHOICES) else "current"
    try:
        sc["rr_months"] = max(1, min(12, int(raw.get("rr_months") or 3)))
    except (TypeError, ValueError):
        sc["rr_months"] = 3
    for k in ("rev_pct", "cogs_pct", "opex_pct"):
        sc[k] = _clamp(raw.get(k), -100.0, 500.0)
    sc["cogs_follows"] = raw.get("cogs_follows") not in (False, "0", 0, "off", "")
    sc["from_month"] = raw.get("from_month") if raw.get("from_month") in M.fy_months(data) else ""
    try:
        sc["ramp"] = max(0, min(12, int(raw.get("ramp") or 0)))
    except (TypeError, ValueError):
        sc["ramp"] = 0
    sc["tax_mode"] = "rate" if raw.get("tax_mode") == "rate" else "keep"
    sc["tax_rate"] = _clamp(raw.get("tax_rate"), 0.0, 100.0, 25.0)
    adj = {}
    for k, v in (raw.get("line_adj") or {}).items():
        if k in keys and isinstance(v, dict):
            pct, ab = _clamp(v.get("pct"), -100.0, 500.0), _clamp(v.get("abs"), -1e12, 1e12)
            if pct or ab:
                adj[k] = {"pct": pct, "abs": ab}
    sc["line_adj"] = adj
    return sc


def scenario_from_form(form, data):
    structure = M.normalise_structure(data.get("structure"))
    raw = {k: form.get(f"sc_{k}") for k in ("name", "base", "rr_months", "rev_pct", "cogs_pct", "opex_pct", "from_month", "ramp", "tax_mode", "tax_rate")}
    raw["cogs_follows"] = form.get("sc_cogs_follows") == "1"
    raw["line_adj"] = {k: {"pct": form.get(f"adj_pct_{k}"), "abs": form.get(f"adj_abs_{k}")} for k in M.pl_keys(structure)}
    return normalise_scenario(raw, data)


def _base_vectors(data, sc, remaining):
    """({month: line dict}, description, [notes]) or (None, error, [])."""
    structure = M.normalise_structure(data.get("structure"))
    V = M.vectors(data)
    bud, fc = M.budget_months(data), M.forecast_months(data)
    base, notes = sc["base"], []
    if base == "current":
        if V["forecast_rest_complete"] and V["has_forecast"]:
            base, notes = "forecast", ["Starting from the latest forecast for the remaining months."]
        elif V["budget_rest_complete"] and V["has_budget"]:
            base, notes = "budget", ["No complete forecast, so starting from the budget for the remaining months."]
        else:
            base, notes = "run_rate", ["No forecast or budget covers every remaining month, so starting from the run-rate."]
    if base == "forecast":
        if not all(m in fc for m in remaining):
            return None, "The forecast does not cover every remaining month: choose another starting point or load a forecast on the Budget & forecast tab.", []
        return {m: dict(fc[m]) for m in remaining}, "Latest forecast", notes
    if base == "budget":
        if not all(m in bud for m in remaining):
            return None, "The budget does not cover every remaining month: choose another starting point.", []
        return {m: dict(bud[m]) for m in remaining}, "Budget", notes
    if base == "prior":
        pp = M.prior_pl(data)
        if not pp:
            return None, "There are no prior-year accounts to start from. Load them on the Prior year tab.", []
        return {m: M._vec_scale(structure, pp, 1.0 / 12.0) for m in remaining}, f"{M.prior_fy_label(data)} (one twelfth a month)", notes
    # run rate
    mon = [x for x in M.monthly_actuals(data)[: V["n"]] if x["values"] is not None]
    use = mon[-sc["rr_months"]:]
    if not use:
        return None, "There are no actual months to take a run-rate from.", []
    avg = M._vec_scale(structure, M._sum_vectors(structure, [x["values"] for x in use]), 1.0 / len(use))
    notes = notes + [f"Run-rate is the average of the last {len(use)} actual month(s)."] + (["Some of those months are estimated (no figures were captured)."] if any(x["est"] for x in use) else [])
    return {m: dict(avg) for m in remaining}, f"Run-rate (last {len(use)} month(s) average)", notes


def project_balance_sheet(data, monthly_pl):
    """Indicative month-end balance sheets for the remaining months from the reporting month's closing balances.
    Working-capital balances keep their relationship to revenue / cost of sales at the reporting month; fixed assets fall by
    depreciation; borrowings and share capital are held; retained earnings rolls forward; cash (or overdraft) balances the sheet.
    Returns {month: bs} (empty when there is no closing balance sheet to start from)."""
    structure = M.normalise_structure(data.get("structure"))
    V = M.vectors(data)
    series = M.bs_series(data)
    start = series.get(V["month"])
    if not start:
        return {}
    ytd = V["actual_ytd"]
    cur_m = V["actual_month"]
    rev_m = f((cur_m or {}).get("revenue")) or (f((ytd or {}).get("revenue")) / max(1, V["n"]))
    cogs_m = f((cur_m or {}).get("cogs")) or (f((ytd or {}).get("cogs")) / max(1, V["n"]))
    opex_m = f((cur_m or {}).get("opex_total")) or (f((ytd or {}).get("opex_total")) / max(1, V["n"]))
    ratio = lambda bal, base: (f(bal) / base) if abs(base) > 1e-9 else None
    r_rec, r_inv = ratio(start["receivables"], rev_m), ratio(start["inventory"], cogs_m)
    pay_base = cogs_m + opex_m
    r_pay = ratio(start["payables"], pay_base)
    out, retained, ppe = {}, f(start["retained"]), f(start["ppe"])
    for m, vals in monthly_pl.items():
        vec = M.pl_vector(structure, vals)
        retained += vec["pat"] - vec["div"]
        ppe = max(0.0, ppe - vec["dep"])
        bs = {"ppe": ppe, "other_assets": f(start["other_assets"]), "other_liabilities": f(start["other_liabilities"]), "borrowings": f(start["borrowings"]),
              "share_capital": f(start["share_capital"]), "retained": retained}
        bs["receivables"] = r_rec * vec["revenue"] if r_rec is not None else f(start["receivables"])
        bs["inventory"] = r_inv * vec["cogs"] if r_inv is not None else f(start["inventory"])
        bs["payables"] = r_pay * (vec["cogs"] + vec["opex_total"]) if r_pay is not None else f(start["payables"])
        funding = bs["payables"] + bs["other_liabilities"] + bs["borrowings"] + bs["share_capital"] + retained
        noncash = bs["ppe"] + bs["inventory"] + bs["receivables"] + bs["other_assets"]
        net = funding - noncash
        bs["cash"], bs["overdraft"] = max(net, 0.0), max(-net, 0.0)
        out[m] = bs
    return out


def _apply(sc, structure, base_vals, remaining):
    """Scenario adjustments applied to the base monthly vectors."""
    keys = M.pl_keys(structure)
    rev_keys = {r["key"] for r in structure["revenue"]}
    opx_keys = {o["key"] for o in structure["opex"]}
    start_i = remaining.index(sc["from_month"]) if sc["from_month"] in remaining else 0
    out = {}
    for j, m in enumerate(remaining):
        v = {k: f(base_vals[m].get(k)) for k in keys}
        if j >= start_i:
            r = min(1.0, (j - start_i + 1) / float(sc["ramp"])) if sc["ramp"] > 0 else 1.0
            for k in keys:
                mult = 1.0
                if k in rev_keys:
                    mult *= 1.0 + sc["rev_pct"] / 100.0 * r
                elif k == "cogs":
                    if sc["cogs_follows"]:
                        mult *= 1.0 + sc["rev_pct"] / 100.0 * r
                    mult *= 1.0 + sc["cogs_pct"] / 100.0 * r
                elif k in opx_keys:
                    mult *= 1.0 + sc["opex_pct"] / 100.0 * r
                la = sc["line_adj"].get(k)
                if la:
                    mult *= 1.0 + la["pct"] / 100.0 * r
                v[k] = v[k] * mult + (la["abs"] * r if la else 0.0)
        out[m] = v
    if sc["tax_mode"] == "rate":
        for m in remaining:
            pbt = M.pl_vector(structure, out[m])["pbt"]
            out[m]["tax"] = max(0.0, pbt) * sc["tax_rate"] / 100.0
    return out


METRIC_ROWS = [("revenue", "Revenue", True), ("gross_profit", "Gross profit", True), ("opex_total", "Operating expenses", False), ("ebitda", "EBITDA", True),
               ("pbt", "Profit before tax", True), ("pat", "Profit after tax", True)]


def reforecast(data, raw_scenario=None):
    """Lock the actuals to date, rebuild the remaining months from the scenario, and compare. Returns a result dict; on a problem
    {"error": text}. `raw_scenario` may be a form-normalised scenario or saved params."""
    structure = M.normalise_structure(data.get("structure"))
    sc = normalise_scenario(raw_scenario, data)
    V = M.vectors(data)
    if V["actual_ytd"] is None:
        return {"error": "There are no actual figures for the reporting month yet, so there is nothing to lock in. Capture or upload the actuals first.", "scenario": sc}
    months, idx, n = V["months"], V["idx"], V["n"]
    remaining = months[n:]
    if not remaining:
        return {"error": "The reporting month is the last month of the year, so there is nothing left to forecast.", "scenario": sc}
    base_vals, base_desc, notes = _base_vectors(data, sc, remaining)
    if base_vals is None:
        return {"error": base_desc, "scenario": sc}
    plan = _apply(sc, structure, base_vals, remaining)
    cur_plan = _apply(normalise_scenario({"base": sc["base"], "rr_months": sc["rr_months"], "tax_mode": "keep"}, data), structure, base_vals, remaining)
    ytd = V["actual_ytd"]
    vec = lambda d: M.pl_vector(structure, d)
    rest = vec(M._sum_vectors(structure, [plan[m] for m in remaining]))
    fy = vec(M._vec_add(structure, ytd, M._sum_vectors(structure, [plan[m] for m in remaining])))
    base_fy = vec(M._vec_add(structure, ytd, M._sum_vectors(structure, [cur_plan[m] for m in remaining])))
    current = V["outlook"] or base_fy
    comp = {"budget": V["budget_fy"], "prior": V["prior_fy"], "current": current, "starting": base_fy}
    table = []
    for key, label, income in METRIC_ROWS:
        row = {"key": key, "label": label, "income": income, "scenario": fy[key]}
        for name, cv in comp.items():
            row[name] = cv[key] if cv else None
            var = None
            if cv is not None:
                var = (fy[key] - cv[key]) if income else (cv[key] - fy[key])
            row["var_" + name] = var
        table.append(row)
    # month by month
    mon = M.monthly_actuals(data)
    bud = M.budget_months(data)
    rows, cum_s, cum_b = [], 0.0, 0.0
    bud_have = bool(bud)
    for i, m in enumerate(months):
        if i < n:
            vals, kind, est = (mon[i]["values"] or {}), "actual", mon[i]["est"]
        else:
            vals, kind, est = plan[m], "reforecast", False
        v = vec(vals) if (vals or kind == "reforecast") else None
        b = vec(bud[m]) if m in bud else None
        cum_s += v["pat"] if v else 0.0
        cum_b = cum_b + (b["pat"] if b else 0.0)
        rows.append({"month": m, "label": M.month_label(m), "kind": kind, "est": est, "revenue": v["revenue"] if v else None, "ebitda": v["ebitda"] if v else None,
                     "pat": v["pat"] if v else None, "b_revenue": b["revenue"] if b else None, "b_ebitda": b["ebitda"] if b else None, "b_pat": b["pat"] if b else None,
                     "cum_pat": cum_s, "cum_b_pat": cum_b if bud_have else None})
    # what has to happen to reach the budget
    required = None
    if V["budget_fy"] is not None:
        need_rev = V["budget_fy"]["revenue"] - ytd["revenue"]
        need_pat = V["budget_fy"]["pat"] - V["actual_ytd"]["pat"]
        k = len(remaining)
        required = {"months": k, "revenue_needed": need_rev, "pat_needed": need_pat, "revenue_per_month": need_rev / k, "pat_per_month": need_pat / k,
                    "scenario_revenue_per_month": rest["revenue"] / k, "scenario_pat_per_month": rest["pat"] / k,
                    "ytd_revenue_per_month": ytd["revenue"] / n, "ytd_pat_per_month": ytd["pat"] / n}
    # balance sheet / cash
    bs = project_balance_sheet(data, plan)
    bs_cur = project_balance_sheet(data, cur_plan)
    cash = None
    if bs:
        net = lambda b: f(b["cash"]) - f(b["overdraft"])
        last = months[-1]
        cash = {"year_end": net(bs[last]), "peak_overdraft": max(f(b["overdraft"]) for b in bs.values()), "current_year_end": net(bs_cur[last]),
                "current_peak_overdraft": max(f(b["overdraft"]) for b in bs_cur.values()), "bs": bs,
                "months": [{"month": m, "label": M.month_label(m), "net_cash": net(bs[m]), "current_net_cash": net(bs_cur[m]),
                            "budget_net_cash": (f(M.budget_bs(data)[m]["cash"]) - f(M.budget_bs(data)[m]["overdraft"])) if m in M.budget_bs(data) else None}
                           for m in remaining]}
    return {"scenario": sc, "base_desc": base_desc, "notes": notes, "remaining": remaining, "months": rows, "fy": fy, "rest": rest, "ytd": ytd, "comp": comp, "table": table,
            "required": required, "cash": cash, "plan": plan, "idx": idx, "n": n, "month": V["month"], "error": None}


def forecast_dataset(data, res, stamp=None):
    """The pack's Forecast dataset rebuilt from a reforecast: earlier months are kept, the remaining months replaced, and the
    balance sheet for the remaining months comes from the projection."""
    old = data.get("forecast") or {}
    months = M.fy_months(data)
    pl = {m: v for m, v in (old.get("pl") or {}).items() if m in months[: res["n"]]}
    bs = {m: v for m, v in (old.get("bs") or {}).items() if m in months[: res["n"]]}
    for m in res["remaining"]:
        pl[m] = {k: round(v, 2) for k, v in res["plan"][m].items()}
        if res["cash"]:
            bs[m] = {k: round(v, 2) for k, v in res["cash"]["bs"][m].items()}
    nm = res["scenario"].get("name") or "What-if reforecast"
    return {"source": "reforecast", "pl": pl, "bs": bs, "opening_bs": old.get("opening_bs"), "note": f"{nm}: actuals to {M.month_label(res['month'], False)}, "
            f"remaining months from {res['base_desc']}", "saved": stamp, "annual": old.get("annual"), "weights": old.get("weights")}


def compare_scenarios(data, saved):
    """Side-by-side of the pack's current outlook, the budget and each saved scenario: list of column dicts."""
    cols = []
    first = None
    for s in saved:
        r = reforecast(data, s.get("params"))
        if r.get("error"):
            cols.append({"name": s.get("name") or "Scenario", "id": s.get("id"), "error": r["error"]})
            continue
        first = first or r
        cols.append({"name": s.get("name") or f"Scenario {s.get('id')}", "id": s.get("id"), "fy": r["fy"], "cash": r["cash"], "error": None})
    return cols, first


# --------------------------------------------------------------------------- for the PDF / Excel exports

def export_sections(data, cur=""):
    """What the exports add: the variance bridge for the most useful comparison, its drivers and reading, and the saved scenarios.
    Returns {"bridge", "drivers", "narr", "scen"} with each part None / empty when the pack has no figures for it."""
    out = {"bridge": None, "drivers": None, "narr": [], "scen": None}
    for basis, against in (("ytd", "budget"), ("ytd", "forecast"), ("ytd", "prior")):
        try:
            b = bridge(data, basis, against, "pat")
        except Exception:
            b = None
        if b:
            out["bridge"] = b
            out["drivers"] = drivers(data, basis, against)
            out["narr"] = narrative(b, out["drivers"], cur)
            break
    saved = [s for s in (data.get("scenarios") or []) if isinstance(s, dict)]
    if saved:
        cols, first = compare_scenarios(data, saved)
        ok = [c for c in cols if not c.get("error")]
        if ok and first:
            out["scen"] = {"cols": ok, "table": first["table"], "label": first.get("month"), "n": first["n"]}
    return out
