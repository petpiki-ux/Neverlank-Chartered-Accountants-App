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
    group equity (pre)     = sum of parts + additions - deductions - eliminations - capitalised central costs
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

DEFAULT_DATA = {
    "basis_of_value": "Fair value", "purpose": "", "standard": "", "premise": "Going concern",
    "fx": {}, "components": [], "holding": [],
    "group": {"control_pct": 0.0, "minority_pct": 0.0, "dlom_pct": 0.0, "stake_pct": 100.0, "shares": None},
    "narrative": {"scope": "", "assumptions": "", "limiting": ""},
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
    pre = tuple(parts[i] + add_t[i] + ded_t[i] + elim_t[i] + cap_t[i] for i in range(3))
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
    if any(r["problem"] in ("no value", "no rate") for r in rows):
        warnings.insert(0, "The total is INCOMPLETE until the components flagged below are fixed.")
    return {"data": d, "currency": reporting_currency, "rows": rows, "holding_rows": hold_rows, "parts": parts,
            "additions": tuple(add_t), "deductions": tuple(ded_t), "eliminations": tuple(elim_t), "capcost": tuple(cap_t),
            "pre": pre, "group_factor": gf, "group_equity": group_eq, "stake": stake, "per_share": per_share,
            "warnings": warnings, "count": len(live), "complete": not any(r["problem"] in ("no value", "no rate") for r in rows)}


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
    for x in d["components"] + d["holding"]:
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
