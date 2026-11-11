"""Corporate Finance - Valuation Model: a group / sum-of-the-parts valuation built from COMPONENT valuations.

A component is one valuation of a subsidiary, associate, business unit or asset - typed in, uploaded (Excel / Word / PDF), taken from the
app's own forecast-model valuations or valuation certificates. Each carries a low / adopted / high value, a currency, whether the figure is an
equity value or an enterprise value (then its net debt is taken off), and the group's ownership percentage with any minority, marketability
(DLOM) or control adjustment. The model converts everything to one reporting currency, adds holding-company items (cash, debt, capitalised
central costs, eliminations of cross-holdings and intercompany balances), applies group-level adjustments and the stake being valued.

Order of the arithmetic, per component and per case (low / adopted / high):
    equity (own currency)  = value                       (equity basis)
                           = value - net debt            (enterprise basis)
    in reporting currency  = equity x rate
    attributable           = in reporting currency x ownership %
    adjusted               = attributable x (1 + control premium %) x (1 - minority discount %) x (1 - DLOM %)
Group:
    sum of parts           = sum of the adjusted components that are included
    going-concern value    = sum over going-concern items of  sign x value x exchange rate x probability %   (production-plan DCF, synergies,
                             development options, less capex still to spend, ramp-up costs and any overlap with the component values)
    group equity (pre)     = sum of parts + additions - deductions - eliminations - capitalised central costs + going-concern value
    group equity           = group equity (pre) x (1 + control %) x (1 - minority %) x (1 - DLOM %)    (group-level adjustments)
    value of the stake     = group equity x stake %          (per share when a share count is given)

Pure Python (no Flask / database) so every figure is unit-tested and reproduced by the live-formula Excel export.
"""
import copy
import re
import uuid
from datetime import date, datetime

CASES = ("low", "adopted", "high")

COMPONENT_TYPES = ["Subsidiary", "Associate / joint venture", "Business unit", "Property", "Mining / resource asset", "Investment / securities",
                   "Intangible asset", "Plant and equipment", "Other asset"]
BASES = [("equity", "Equity value"), ("enterprise", "Enterprise value (less net debt)")]
HOLDING_KINDS = [("add", "Add: holding-company asset (cash, investments, receivables)"), ("deduct", "Less: holding-company liability (debt, payables)"),
                 ("eliminate", "Less: elimination (cross-holding / intercompany balance)"), ("capcost", "Less: capitalised central costs (annual cost x multiple)")]
METHODS = ["Discounted cash flow", "Market multiples", "Net asset value", "Independent valuation report", "Valuation certificate", "Forecast model valuation",
           "Cost approach", "Dividend / income approach", "Other"]
STATUSES = ["Draft", "Final"]

# Going-concern / development value: value the components do not already carry because the business is moving into production.
# (key, label, sign): amounts are entered as positives; the kind decides the sign.
GC_KINDS = [("dcf", "Production-plan DCF (cash flows entered)", 1), ("synergy", "Synergies between the assets (present value)", 1),
            ("development", "Development / expansion option value (present value)", 1), ("other_add", "Other addition (present value)", 1),
            ("capex", "Less: capex still to be spent (present value)", -1), ("rampup", "Less: ramp-up losses and working capital (present value)", -1),
            ("overlap", "Less: value already inside the component values (avoids double counting)", -1), ("other_deduct", "Other deduction (present value)", -1)]
GC_SIGN = {k: sg for k, _, sg in GC_KINDS}
GC_STAGES = ["Exploration / pre-feasibility", "Feasibility / financing", "Construction / commissioning", "Ramp-up", "Steady-state production"]
# Indicative range of the probability of reaching steady state, by stage - a starting point for professional judgement, to be supported by evidence.
GC_STAGE_HINT = {"Exploration / pre-feasibility": "10% - 30%", "Feasibility / financing": "35% - 60%", "Construction / commissioning": "65% - 85%",
                 "Ramp-up": "85% - 95%", "Steady-state production": "95% - 100%"}
GC_MAX_YEARS = 15

DEFAULT_DATA = {
    "basis_of_value": "Fair value", "purpose": "", "standard": "", "premise": "Going concern",
    "fx": {}, "components": [], "holding": [],
    "group": {"control_pct": 0.0, "minority_pct": 0.0, "dlom_pct": 0.0, "stake_pct": 100.0, "shares": None},
    "narrative": {"scope": "", "assumptions": "", "limiting": ""},
    "goingconcern": {"enabled": False, "stage": "", "items": [], "basis": ""},
    "report": {"background": "", "audience": "", "instructions": "", "sections": {}, "generated_at": ""},
    "import_log": [],
}


# --------------------------------------------------------------------------- small helpers

def f(v, default=0.0):
    """Lenient number: '1,234.5', '(500)', '12%', None -> float."""
    if v is None or isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace(" ", "").replace(" ", "")
    if s == "" or s == "-":
        return default
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("%", "")
    s = re.sub(r"^[A-Za-z$€£]+", "", s)
    try:
        n = float(s)
    except ValueError:
        return default
    return -n if neg else n


def opt(v):
    """None when blank, else a float."""
    if v is None or (isinstance(v, str) and v.strip() in ("", "-")):
        return None
    if isinstance(v, str):
        n = f(v, None)
        return n
    return f(v)


def new_id():
    return uuid.uuid4().hex[:8]


def parse_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def iso(v):
    d = parse_date(v)
    return d.isoformat() if d else ""


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- normalise

def normalise_component(c):
    c = c or {}
    ty = c.get("type") if c.get("type") in COMPONENT_TYPES else (c.get("type") or "Subsidiary")
    low, ad, high = opt(c.get("low")), opt(c.get("adopted")), opt(c.get("high"))
    src = c.get("source") or {}
    return {
        "id": c.get("id") or new_id(),
        "name": (str(c.get("name") or "").strip() or "Component")[:200],
        "type": str(ty)[:60],
        "method": str(c.get("method") or "")[:120],
        "currency": (str(c.get("currency") or "USD").strip().upper() or "USD")[:6],
        "basis": "enterprise" if c.get("basis") == "enterprise" else "equity",
        "date": iso(c.get("date")),
        "low": low, "adopted": ad, "high": high,
        "net_debt": f(c.get("net_debt")),
        "ownership_pct": _clamp(f(c.get("ownership_pct"), 100.0), 0.0, 100.0),
        "minority_pct": _clamp(f(c.get("minority_pct")), 0.0, 99.0),
        "dlom_pct": _clamp(f(c.get("dlom_pct")), 0.0, 99.0),
        "control_pct": _clamp(f(c.get("control_pct")), 0.0, 200.0),
        "include": bool(c.get("include", True)),
        "notes": str(c.get("notes") or "")[:1000],
        "source": {"kind": str(src.get("kind") or "manual")[:20], "label": str(src.get("label") or "")[:300], "ref": str(src.get("ref") or "")[:100],
                   "document_id": src.get("document_id"), "at": str(src.get("at") or "")[:30]},
    }


def normalise_holding(h):
    h = h or {}
    kind = h.get("kind") if h.get("kind") in [k for k, _ in HOLDING_KINDS] else "add"
    return {"id": h.get("id") or new_id(), "label": (str(h.get("label") or "").strip() or "Item")[:200], "kind": kind,
            "currency": (str(h.get("currency") or "").strip().upper() or "")[:6],
            "low": opt(h.get("low")), "adopted": opt(h.get("adopted")), "high": opt(h.get("high")),
            "annual": f(h.get("annual")), "multiple": f(h.get("multiple")), "notes": str(h.get("notes") or "")[:500]}


def _cash_flows(v):
    """A list of yearly cash flows from a list or a comma / newline separated string; blanks inside the list count as 0, trailing blanks are dropped."""
    if isinstance(v, str):
        v = [x for x in re.split(r"[;\n\t]|,(?=\s*[-(\d])", v)]
    out = []
    for x in (v or [])[:GC_MAX_YEARS]:
        out.append(opt(x))
    while out and out[-1] is None:
        out.pop()
    return [0.0 if x is None else x for x in out]


def normalise_gc_item(x):
    x = x or {}
    kind = x.get("kind") if x.get("kind") in GC_SIGN else "synergy"
    prob = opt(x.get("probability_pct"))
    return {"id": x.get("id") or new_id(), "label": (str(x.get("label") or "").strip() or "Item")[:200], "kind": kind,
            "currency": (str(x.get("currency") or "").strip().upper() or "")[:6],
            "low": opt(x.get("low")), "adopted": opt(x.get("adopted")), "high": opt(x.get("high")),
            "cash_flows": _cash_flows(x.get("cash_flows")), "rate_pct": opt(x.get("rate_pct")), "rate_range": _clamp(f(x.get("rate_range"), 1.0), 0.0, 20.0),
            "growth_pct": opt(x.get("growth_pct")), "mid_year": bool(x.get("mid_year", True)),
            "probability_pct": 100.0 if prob is None else _clamp(prob, 0.0, 100.0), "include": bool(x.get("include", True)),
            "notes": str(x.get("notes") or "")[:1000]}


def dcf_value(cash_flows, rate_pct, growth_pct=None, mid_year=True):
    """(present value, present value of the terminal value) of yearly cash flows 1..N at the valuation date. Year-end flows unless mid_year
    (flows arrive half-way through each year). The terminal value is the Gordon growth value of the last flow, used only when growth < rate.
    None when there are no flows or no usable rate."""
    if not cash_flows or rate_pct is None:
        return None
    r = rate_pct / 100.0
    if r <= -0.99:
        return None
    n = len(cash_flows)
    shift = 0.5 if mid_year else 0.0
    pv = sum(cf / (1 + r) ** (t - shift) for t, cf in enumerate(cash_flows, start=1))
    tv_pv = 0.0
    if growth_pct is not None and r > growth_pct / 100.0:
        g = growth_pct / 100.0
        tv_pv = cash_flows[-1] * (1 + g) / (r - g) / (1 + r) ** (n - shift)
    return pv + tv_pv, tv_pv


def normalise(data, reporting_currency="USD"):
    data = data or {}
    out = copy.deepcopy(DEFAULT_DATA)
    out["basis_of_value"] = str(data.get("basis_of_value") or out["basis_of_value"])[:80]
    out["purpose"] = str(data.get("purpose") or "")[:600]
    out["standard"] = str(data.get("standard") or "")[:300]
    out["premise"] = str(data.get("premise") or out["premise"])[:80]
    out["fx"] = {}
    for k, v in (data.get("fx") or {}).items():
        code = str(k).strip().upper()[:6]
        n = opt(v)
        if code and n is not None and n > 0:
            out["fx"][code] = n
    out["fx"][reporting_currency] = 1.0
    out["components"] = [normalise_component(c) for c in (data.get("components") or [])][:200]
    out["holding"] = [normalise_holding(h) for h in (data.get("holding") or [])][:100]
    g = data.get("group") or {}
    out["group"] = {"control_pct": _clamp(f(g.get("control_pct")), 0.0, 200.0), "minority_pct": _clamp(f(g.get("minority_pct")), 0.0, 99.0),
                    "dlom_pct": _clamp(f(g.get("dlom_pct")), 0.0, 99.0), "stake_pct": _clamp(f(g.get("stake_pct"), 100.0), 0.0, 100.0),
                    "shares": (lambda s: s if s and s > 0 else None)(opt(g.get("shares")))}
    nar = data.get("narrative") or {}
    out["narrative"] = {k: str(nar.get(k) or "")[:6000] for k in ("scope", "assumptions", "limiting")}
    gc = data.get("goingconcern") or {}
    out["goingconcern"] = {"enabled": bool(gc.get("enabled")), "stage": str(gc.get("stage") or "")[:80], "basis": str(gc.get("basis") or "")[:4000],
                           "items": [normalise_gc_item(x) for x in (gc.get("items") or [])][:60]}
    rp = data.get("report") or {}
    secs = {}
    for k, v in (rp.get("sections") or {}).items():
        if isinstance(v, dict):
            secs[str(k)[:30]] = {"text": str(v.get("text") or "")[:20000], "origin": str(v.get("origin") or "template")[:12],
                                 "include": bool(v.get("include", True)), "at": str(v.get("at") or "")[:20],
                                 "flags": [str(x)[:60] for x in (v.get("flags") or [])][:12]}
    out["report"] = {"background": str(rp.get("background") or "")[:8000], "audience": str(rp.get("audience") or "")[:300],
                     "instructions": str(rp.get("instructions") or "")[:2000], "sections": secs, "generated_at": str(rp.get("generated_at") or "")[:20]}
    out["import_log"] = list(data.get("import_log") or [])[-30:]
    return out


# --------------------------------------------------------------------------- consolidation

def case_values(low, adopted, high):
    """A value with missing cases filled in: only an adopted figure -> low = high = adopted; a low and a high without an adopted
    figure -> adopted is the midpoint. Returns (low, adopted, high) or None when nothing is given."""
    if adopted is None and low is not None and high is not None:
        adopted = (low + high) / 2.0
    if adopted is None:
        only = [x for x in (low, high) if x is not None]
        if not only:
            return None
        adopted = only[0]
    return (adopted if low is None else low, adopted, adopted if high is None else high)


def factor(c):
    return (1 + c["control_pct"] / 100.0) * (1 - c["minority_pct"] / 100.0) * (1 - c["dlom_pct"] / 100.0)


def holding_value(h, case, rate):
    """Signed effect (+ adds, - deducts) of a holding item in the reporting currency for one case."""
    if h["kind"] == "capcost":
        return -(h["annual"] * h["multiple"]) * rate
    vals = case_values(h["low"], h["adopted"], h["high"])
    if vals is None:
        return 0.0
    v = vals[CASES.index(case)] * rate
    return v if h["kind"] == "add" else -v


def _rate(fx, cur, reporting):
    if not cur or cur == reporting:
        return 1.0
    return fx.get(cur)


def consolidate(data, reporting_currency="USD", valuation_date=None):
    d = normalise(data, reporting_currency)
    fx = d["fx"]
    warnings, rows = [], []
    for c in d["components"]:
        rate = _rate(fx, c["currency"], reporting_currency)
        vals = case_values(c["low"], c["adopted"], c["high"])
        row = {"c": c, "rate": rate, "problem": None, "factor": factor(c), "local": None, "equity_local": None, "equity": None, "attributable": None, "adjusted": None}
        if not c["include"]:
            row["problem"] = "excluded"
        elif vals is None:
            row["problem"] = "no value"
            warnings.append(f"{c['name']}: no value entered - it is not in the total.")
        elif rate is None:
            row["problem"] = "no rate"
            warnings.append(f"{c['name']}: no exchange rate from {c['currency']} to {reporting_currency} - it is not in the total.")
        else:
            nd = c["net_debt"] if c["basis"] == "enterprise" else 0.0
            eq_local = tuple(v - nd for v in vals)
            eq = tuple(v * rate for v in eq_local)
            att = tuple(v * c["ownership_pct"] / 100.0 for v in eq)
            adj = tuple(v * row["factor"] for v in att)
            row.update(local=vals, equity_local=eq_local, equity=eq, attributable=att, adjusted=adj)
            if not (vals[0] <= vals[1] <= vals[2]):
                warnings.append(f"{c['name']}: low / adopted / high are not in order.")
            if any(v < 0 for v in eq_local):
                warnings.append(f"{c['name']}: equity value is negative after net debt.")
            if c["basis"] == "equity" and c["net_debt"]:
                warnings.append(f"{c['name']}: net debt is entered but the figure is an equity value - it was not deducted.")
        rows.append(row)
    live = [r for r in rows if r["adjusted"] is not None]
    parts = tuple(sum(r["adjusted"][i] for r in live) for i in range(3))
    for r in rows:
        r["share"] = (r["adjusted"][1] / parts[1] * 100.0) if r["adjusted"] is not None and parts[1] else None

    hold_rows, add_t, ded_t, elim_t, cap_t = [], [0.0] * 3, [0.0] * 3, [0.0] * 3, [0.0] * 3
    for h in d["holding"]:
        rate = _rate(fx, h["currency"], reporting_currency)
        if rate is None:
            warnings.append(f"{h['label']}: no exchange rate from {h['currency']} to {reporting_currency} - it is not in the total.")
            hold_rows.append({"h": h, "rate": None, "effect": None})
            continue
        eff = tuple(holding_value(h, k, rate) for k in CASES)
        hold_rows.append({"h": h, "rate": rate, "effect": eff})
        tgt = {"add": add_t, "deduct": ded_t, "eliminate": elim_t, "capcost": cap_t}[h["kind"]]
        for i in range(3):
            tgt[i] += eff[i]
    gc_rows, gc_t, gc_raw = _going_concern(d, fx, reporting_currency, warnings)
    pre = tuple(parts[i] + add_t[i] + ded_t[i] + elim_t[i] + cap_t[i] + gc_t[i] for i in range(3))
    g = d["group"]
    gf = (1 + g["control_pct"] / 100.0) * (1 - g["minority_pct"] / 100.0) * (1 - g["dlom_pct"] / 100.0)
    group_eq = tuple(v * gf for v in pre)
    stake = tuple(v * g["stake_pct"] / 100.0 for v in group_eq)
    per_share = tuple(v / g["shares"] for v in stake) if g["shares"] else None
    if not live:
        warnings.append("No component has a value yet.")
    if len({r["c"]["name"].strip().lower() for r in rows}) < len(rows):
        warnings.append("Two components have the same name - check that one is not entered twice.")
    ref = parse_date(valuation_date)
    if ref:
        for r in rows:
            cd = parse_date(r["c"]["date"])
            if r["c"]["include"] and cd and (ref - cd).days > 366:
                warnings.append(f"{r['c']['name']}: valued on {cd.strftime('%d %b %Y')}, more than a year before the model date.")
            elif r["c"]["include"] and cd and cd > ref:
                warnings.append(f"{r['c']['name']}: valued on {cd.strftime('%d %b %Y')}, after the model date.")
    gc_bad = any(r["problem"] for r in gc_rows if r["counted_item"])
    complete = not any(r["problem"] in ("no value", "no rate") for r in rows) and not gc_bad
    if not complete:
        warnings.insert(0, "The total is INCOMPLETE until the components and going-concern items flagged below are fixed.")
    return {"data": d, "currency": reporting_currency, "rows": rows, "holding_rows": hold_rows, "parts": parts,
            "additions": tuple(add_t), "deductions": tuple(ded_t), "eliminations": tuple(elim_t), "capcost": tuple(cap_t),
            "gc": tuple(gc_t), "gc_unrisked": tuple(gc_raw), "gc_rows": gc_rows, "gc_on": d["goingconcern"]["enabled"],
            "pre": pre, "group_factor": gf, "group_equity": group_eq, "stake": stake, "per_share": per_share,
            "warnings": warnings, "count": len(live), "complete": complete}


def _going_concern(d, fx, reporting, warnings):
    """(rows, risk-adjusted net effect per case, un-risked net effect per case). Items count only when the section is switched on, the item is
    included and it has what it needs; an included item that lacks a value, a rate or a discount rate is flagged and makes the model incomplete."""
    gc = d["goingconcern"]
    rows, tot, raw = [], [0.0] * 3, [0.0] * 3
    on = gc["enabled"]
    for it in gc["items"]:
        rate = _rate(fx, it["currency"], reporting)
        sign = GC_SIGN[it["kind"]]
        row = {"g": it, "rate": rate, "sign": sign, "vals": None, "risked": None, "effect": None, "problem": None, "tv_share": None, "counted": False,
               "counted_item": bool(on and it["include"])}
        if it["kind"] == "dcf":
            if not it["cash_flows"]:
                row["problem"] = "no cash flows"
            elif it["rate_pct"] is None:
                row["problem"] = "no discount rate"
            else:
                rr = it["rate_range"]
                res3 = [dcf_value(it["cash_flows"], it["rate_pct"] + dr, it["growth_pct"], it["mid_year"]) for dr in (rr, 0.0, -rr)]
                row["vals"] = tuple(sorted(x[0] for x in res3))
                mid = res3[1]
                row["tv_share"] = (mid[1] / mid[0] * 100.0) if mid and mid[0] > 0 else None
                if it["growth_pct"] is not None and it["rate_pct"] <= it["growth_pct"]:
                    warnings.append(f"{it['label']}: terminal growth is not below the discount rate, so no terminal value was included.") if row["counted_item"] else None
                if row["tv_share"] is not None and row["tv_share"] > 75 and row["counted_item"]:
                    warnings.append(f"{it['label']}: the terminal value is {row['tv_share']:.0f}% of the DCF value - support the long-term assumptions.")
        else:
            row["vals"] = case_values(it["low"], it["adopted"], it["high"])
            if row["vals"] is None:
                row["problem"] = "no value"
        if row["problem"] is None and rate is None:
            row["problem"] = "no rate"
        if row["problem"] and row["counted_item"]:
            msg = {"no value": "no value entered", "no rate": f"no exchange rate from {it['currency']} to {reporting}",
                   "no cash flows": "no cash flows entered", "no discount rate": "no discount rate entered"}[row["problem"]]
            warnings.append(f"{it['label']}: {msg} - it is not in the total.")
        if row["problem"] is None:
            row["risked"] = tuple(v * rate * it["probability_pct"] / 100.0 for v in row["vals"])
            row["effect"] = tuple(sign * v for v in row["risked"])
            row["unrisked"] = tuple(sign * v * rate for v in row["vals"])
            if row["counted_item"]:
                row["counted"] = True
                for i in range(3):
                    tot[i] += row["effect"][i]
                    raw[i] += row["unrisked"][i]
        rows.append(row)
    if on:
        adds = [r for r in rows if r["counted"] and r["sign"] > 0 and r["g"]["kind"] in ("dcf", "synergy", "development")]
        has_overlap = any(r["counted"] and r["g"]["kind"] == "overlap" for r in rows)
        if adds and not has_overlap:
            dcf_comps = [c["name"] for c in d["components"] if c["include"] and (c["method"] in ("Discounted cash flow", "Forecast model valuation") or c["type"] == "Mining / resource asset")]
            if dcf_comps:
                warnings.append("Check for double counting: " + ", ".join(dcf_comps[:4]) + (" and others" if len(dcf_comps) > 4 else "")
                                + " may already include the production value added here. Add a 'value already inside the component values' deduction if so.")
        if adds and not gc["stage"]:
            warnings.append("Going-concern value: state the stage of development so the probabilities can be explained in the report.")
        if not gc["items"]:
            warnings.append("The going-concern section is switched on but has no items.")
    return rows, tot, raw


def football(res):
    """Bars for the chart / report: every included component's adjusted range, then the group equity and the stake value."""
    bars = []
    for r in res["rows"]:
        if r["adjusted"] is not None:
            bars.append({"label": r["c"]["name"], "low": r["adjusted"][0], "mid": r["adjusted"][1], "high": r["adjusted"][2], "kind": "component"})
    bars.append({"label": "Group equity value", "low": res["group_equity"][0], "mid": res["group_equity"][1], "high": res["group_equity"][2], "kind": "group"})
    if res["data"]["group"]["stake_pct"] != 100.0:
        bars.append({"label": f"Stake valued ({res['data']['group']['stake_pct']:g}%)", "low": res["stake"][0], "mid": res["stake"][1], "high": res["stake"][2], "kind": "stake"})
    return bars


def currencies_needed(data, reporting_currency="USD"):
    """Currencies used by components / holding items other than the reporting currency (so the rate table lists them)."""
    d = normalise(data, reporting_currency)
    seen = []
    for x in d["components"] + d["holding"] + d["goingconcern"]["items"]:
        cur = x.get("currency")
        if cur and cur != reporting_currency and cur not in seen:
            seen.append(cur)
    for cur in d["fx"]:
        if cur != reporting_currency and cur not in seen:
            seen.append(cur)
    return seen


# --------------------------------------------------------------------------- editing helpers

def add_component(data, comp):
    d = data if isinstance(data, dict) else {}
    d.setdefault("components", []).append(normalise_component(comp))
    return d


def next_ref(existing, prefix, year=None):
    year = year or date.today().year
    used = [int(m.group(1)) for x in existing if x for m in [re.search(rf"{re.escape(prefix)}-{year}-(\d+)$", x)] if m]
    return f"{prefix}-{year}-{(max(used) + 1 if used else 1):04d}"
