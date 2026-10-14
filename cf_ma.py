"""Management accounts engine for the Corporate Finance & Business Advisory module (pure Python, no Flask / DB).

A management accounts PACK is a monthly reporting pack for a client: income statement, balance sheet and cash flow
(month, year-to-date and full-year outlook) set against the budget and the prior year, with KPIs, variance analysis
and commentary. Unlike statutory accounts the layout is fixed by us, so every client's pack looks the same.

Where the numbers come from (all stored in one JSON document, `data`):
  * entries[month]  - actuals for a month: a trial balance upload (cumulative year-to-date, kind "ytd"), manual capture
                      (the month's own figures, kind "month") or MODELLED figures (kind "month", source "modelled": e.g. 9/12
                      of last year's accounts grown at 5%, so a funding model can be built before real books exist).
  * prior           - the prior year's annual accounts (P&L for the year, balance sheet at year end). They are the
                      comparatives and the opening position, and the starting point for modelled months.
  * budget          - monthly budget, taken from a forecast model (snapshot) or typed in.

Sign convention: every line is held as a POSITIVE amount in its natural direction (revenue, costs, assets, liabilities,
equity all positive). A trial balance is converted using each line's nature (see CREDIT_NATURAL).
"""
import csv
import io
import re
from datetime import date

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_TOL = 0.5

# --------------------------------------------------------------------------- line definitions

FIXED_PL = [  # key, label, income-like?
    ("cogs", "Cost of sales", False), ("oth_inc", "Other income", True), ("dep", "Depreciation", False),
    ("int_inc", "Interest income", True), ("int_exp", "Interest expense", False), ("tax", "Income tax", False),
    ("div", "Dividends declared", False),
]
FIXED_PL_LABEL = {k: lab for k, lab, _ in FIXED_PL}

BS_LINES = [  # key, label, side
    ("ppe", "Non-current assets (net)", "A"), ("inventory", "Inventory", "A"), ("receivables", "Trade receivables", "A"),
    ("other_assets", "Other current assets", "A"), ("cash", "Cash and bank", "A"),
    ("overdraft", "Bank overdraft", "L"), ("payables", "Trade payables", "L"), ("other_liabilities", "Other liabilities", "L"),
    ("borrowings", "Borrowings", "L"),
    ("share_capital", "Share capital", "E"), ("retained", "Retained earnings and reserves", "E"),
]
BS_LABEL = {k: lab for k, lab, _ in BS_LINES}
BS_KEYS = [k for k, _, _ in BS_LINES]
BS_INPUT_KEYS = [k for k in BS_KEYS if k != "retained"]  # retained is entered optionally; otherwise rolled forward

# lines whose natural balance is a credit (a trial balance holds debit minus credit, so these flip sign)
CREDIT_NATURAL = {"oth_inc", "int_inc", "overdraft", "payables", "other_liabilities", "borrowings", "share_capital", "retained"}

DEFAULT_SETTINGS = {"fy_start_month": 1, "fy_start_year": None, "report_month": None, "outlook": "auto",
                    "rag_amber": 5.0, "rag_red": 10.0}


def f(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace(",", "").replace(" ", "")
            if value.startswith("(") and value.endswith(")"):
                value = "-" + value[1:-1]
        return float(value)
    except (TypeError, ValueError):
        return default


def default_structure():
    return {"revenue": [{"key": "rev_1", "label": "Revenue"}], "opex": [{"key": "opx_1", "label": "Operating expenses"}]}


def normalise_structure(s):
    """Keep keys stable, drop blanks, make sure there is at least one revenue and one expense line."""
    out = {"revenue": [], "opex": []}
    for sec, pre in (("revenue", "rev"), ("opex", "opx")):
        items = [x for x in (s or {}).get(sec, []) if isinstance(x, dict) and (x.get("label") or "").strip()]
        used = set()
        for item in items:
            k = item.get("key")
            if isinstance(k, str) and re.fullmatch(pre + r"_\d+", k) and k not in used:
                used.add(k)
        n = 0
        for item in items:
            k = item.get("key")
            if not (isinstance(k, str) and re.fullmatch(pre + r"_\d+", k) and k in used and k not in [o["key"] for o in out[sec]]):
                n += 1
                while f"{pre}_{n}" in used:
                    n += 1
                k = f"{pre}_{n}"
                used.add(k)
            out[sec].append({"key": k, "label": item["label"].strip()[:60]})
    if not out["revenue"]:
        out["revenue"] = default_structure()["revenue"]
    if not out["opex"]:
        out["opex"] = default_structure()["opex"]
    return out


def pl_line_defs(structure):
    """Every input P&L line in statement order: [{key,label,income}]"""
    d = [{"key": r["key"], "label": r["label"], "income": True} for r in structure["revenue"]]
    d.append({"key": "cogs", "label": "Cost of sales", "income": False})
    d.append({"key": "oth_inc", "label": "Other income", "income": True})
    d += [{"key": o["key"], "label": o["label"], "income": False} for o in structure["opex"]]
    for k in ("dep", "int_inc", "int_exp", "tax", "div"):
        d.append({"key": k, "label": FIXED_PL_LABEL[k], "income": k == "int_inc"})
    return d


def pl_keys(structure):
    return [x["key"] for x in pl_line_defs(structure)]


def line_label(structure, key):
    for x in pl_line_defs(structure):
        if x["key"] == key:
            return x["label"]
    return BS_LABEL.get(key, key)


# --------------------------------------------------------------------------- dates

def parse_month(key):
    """'2026-03' -> (2026, 3)"""
    m = re.fullmatch(r"(\d{4})-(\d{2})", str(key or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"bad month {key!r}")
    return int(m.group(1)), int(m.group(2))


def mkey(y, m):
    return f"{y:04d}-{m:02d}"


def shift_month(key, n):
    y, m = parse_month(key)
    t = y * 12 + (m - 1) + n
    return mkey(t // 12, t % 12 + 1)


def month_label(key, short=True):
    y, m = parse_month(key)
    return f"{MONTH_ABBR[m - 1]}-{str(y)[2:]}" if short else f"{MONTH_ABBR[m - 1]} {y}"


def settings_of(data):
    s = dict(DEFAULT_SETTINGS)
    s.update((data or {}).get("settings") or {})
    try:
        s["fy_start_month"] = min(12, max(1, int(s["fy_start_month"])))
    except (TypeError, ValueError):
        s["fy_start_month"] = 1
    if not s.get("fy_start_year"):
        s["fy_start_year"] = date.today().year
    s["fy_start_year"] = int(s["fy_start_year"])
    return s


def fy_months(data):
    s = settings_of(data)
    start = mkey(s["fy_start_year"], s["fy_start_month"])
    return [shift_month(start, i) for i in range(12)]


def fy_label(data):
    s = settings_of(data)
    months = fy_months(data)
    end_y, end_m = parse_month(months[-1])
    return f"FY{end_y}" if s["fy_start_month"] != 1 else f"FY{s['fy_start_year']}"


def prior_fy_label(data):
    p = (data or {}).get("prior") or {}
    if p.get("label"):
        return p["label"]
    lab = fy_label(data)
    try:
        return "FY" + str(int(lab[2:]) - 1)
    except ValueError:
        return "Prior year"


def report_index(data):
    """0-based index (within the financial year) of the reporting month."""
    months = fy_months(data)
    rm = settings_of(data).get("report_month")
    if rm in months:
        return months.index(rm)
    # default: the latest month with any actual data, else the first month
    have = [i for i, m in enumerate(months) if m in (data.get("entries") or {})]
    return max(have) if have else 0


def report_month(data):
    return fy_months(data)[report_index(data)]


# --------------------------------------------------------------------------- P&L vectors

def pl_vector(structure, values):
    """Line values (any missing = 0) plus the subtotals and margins."""
    v = {k: f((values or {}).get(k)) for k in pl_keys(structure)}
    rev = sum(v[r["key"]] for r in structure["revenue"])
    opex = sum(v[o["key"]] for o in structure["opex"])
    v["revenue"] = rev
    v["gross_profit"] = rev - v["cogs"]
    v["opex_total"] = opex
    v["ebitda"] = v["gross_profit"] + v["oth_inc"] - opex
    v["ebit"] = v["ebitda"] - v["dep"]
    v["pbt"] = v["ebit"] + v["int_inc"] - v["int_exp"]
    v["pat"] = v["pbt"] - v["tax"]
    v["gross_margin"] = v["gross_profit"] / rev if abs(rev) > 1e-9 else None
    v["ebitda_margin"] = v["ebitda"] / rev if abs(rev) > 1e-9 else None
    v["net_margin"] = v["pat"] / rev if abs(rev) > 1e-9 else None
    return v


def _vec_add(structure, a, b, wb=1.0):
    return {k: f((a or {}).get(k)) + wb * f((b or {}).get(k)) for k in pl_keys(structure)}


def _vec_scale(structure, a, w):
    return {k: f((a or {}).get(k)) * w for k in pl_keys(structure)}


# --------------------------------------------------------------------------- actuals timeline

def cumulative_actuals(data):
    """Cumulative year-to-date P&L by month of the financial year.

    A TB entry (kind 'ytd') gives the cumulative figure directly; a manual/modelled entry (kind 'month') adds to the
    previous month's cumulative. Months with no entry that sit between two known points are interpolated evenly and flagged
    estimated. Returns a list of 12 dicts {month, cum (line->value) or None, est, source}."""
    structure = normalise_structure(data.get("structure"))
    keys = pl_keys(structure)
    months = fy_months(data)
    entries = data.get("entries") or {}
    known = [None] * 12     # cumulative dict per month where known (not interpolated)
    src = [None] * 12
    cum_prev = {k: 0.0 for k in keys}
    prev_ok = True          # cumulative at the previous month is known (or interpolated later)
    # pass 1: place entries
    last = {k: 0.0 for k in keys}
    last_idx = -1
    for i, m in enumerate(months):
        e = entries.get(m)
        if not e or e.get("kind") not in ("ytd", "month"):
            continue
        vals = {k: f((e.get("pl") or {}).get(k)) for k in keys}
        if e.get("kind") == "ytd":
            known[i] = vals
        else:
            # needs the cumulative at i-1: known directly, or interpolatable from earlier + we can't look ahead -> require adjacency
            if i == 0:
                base = {k: 0.0 for k in keys}
            elif known[i - 1] is not None:
                base = known[i - 1]
            else:
                # previous months missing: carry the last known cumulative flat (the missing months are treated as nil)
                base = last
            known[i] = {k: base[k] + vals[k] for k in keys}
        src[i] = e.get("source") or "manual"
        last = known[i]
        last_idx = i
    # pass 2: interpolate gaps that sit before a known point
    out = []
    prev_i, prev_cum = -1, {k: 0.0 for k in keys}
    nxt = [None] * 12
    nxt_i = None
    for i in range(11, -1, -1):
        if known[i] is not None:
            nxt_i = i
        nxt[i] = nxt_i
    for i, m in enumerate(months):
        if known[i] is not None:
            out.append({"month": m, "cum": known[i], "est": False, "source": src[i]})
            prev_i, prev_cum = i, known[i]
        elif nxt[i] is not None:
            j = nxt[i]
            w = (i - prev_i) / float(j - prev_i)
            cum = {k: prev_cum[k] + (known[j][k] - prev_cum[k]) * w for k in keys}
            out.append({"month": m, "cum": cum, "est": True, "source": "interpolated"})
        else:
            out.append({"month": m, "cum": None, "est": False, "source": None})
    return out


def monthly_actuals(data):
    """Month-by-month P&L values (from the cumulative timeline): list of {month, values, est, source} (values None if no data)."""
    structure = normalise_structure(data.get("structure"))
    keys = pl_keys(structure)
    cum = cumulative_actuals(data)
    out = []
    prev = {k: 0.0 for k in keys}
    for i, c in enumerate(cum):
        if c["cum"] is None:
            out.append({"month": c["month"], "values": None, "est": False, "source": None})
            continue
        vals = {k: c["cum"][k] - prev[k] for k in keys}
        out.append({"month": c["month"], "values": vals, "est": c["est"], "source": c["source"]})
        prev = c["cum"]
    return out


def actual_ytd(data, upto_idx):
    cum = cumulative_actuals(data)
    c = cum[upto_idx]["cum"]
    return c


def actual_month(data, idx):
    return monthly_actuals(data)[idx]["values"]


# --------------------------------------------------------------------------- balance sheets

def bs_total(bs):
    a = sum(f(bs.get(k)) for k in ("ppe", "inventory", "receivables", "other_assets", "cash"))
    liab = sum(f(bs.get(k)) for k in ("overdraft", "payables", "other_liabilities", "borrowings"))
    eq = f(bs.get("share_capital")) + f(bs.get("retained"))
    return {"assets": a, "liabilities": liab, "equity": eq, "check": a - liab - eq,
            "net_assets": a - liab}


def prior_bs(data):
    p = (data.get("prior") or {}).get("bs")
    if not p:
        return None
    bs = {k: f(p.get(k)) for k in BS_KEYS}
    return bs


def bs_series(data):
    """Closing balance sheet for each month with data: dict month -> bs. Retained earnings, when not entered, is rolled
    forward from the previous month (or the prior year-end) using the month's profit and dividends."""
    structure = normalise_structure(data.get("structure"))
    entries = data.get("entries") or {}
    months = fy_months(data)
    monthly = {m["month"]: m for m in monthly_actuals(data)}
    pb = prior_bs(data)
    out = {}
    prev_retained = pb["retained"] if pb else None
    for m in months:
        e = entries.get(m)
        if not e or not e.get("bs"):
            prev_retained = out[m]["retained"] if m in out else prev_retained
            continue
        bs = {k: f(e["bs"].get(k)) for k in BS_KEYS}
        if e["bs"].get("retained") in (None, ""):
            vals = (monthly[m]["values"] or {})
            pl = pl_vector(structure, vals) if monthly[m]["values"] is not None else None
            if prev_retained is not None and pl is not None:
                bs["retained"] = prev_retained + pl["pat"] - pl["div"]
            else:
                # no base to roll from: make the balance sheet balance (flagged by the check on the data page)
                t = bs_total(dict(bs, retained=0.0))
                bs["retained"] = t["assets"] - t["liabilities"] - bs["share_capital"]
                bs["_plug"] = True
        out[m] = bs
        prev_retained = bs["retained"]
    return out


def cash_flow(structure, bs0, bs1, pl):
    """Indirect cash flow between two balance sheets for a period whose P&L vector is `pl`. None if a balance sheet is missing."""
    if bs0 is None or bs1 is None or pl is None:
        return None
    d = lambda k: f(bs1.get(k)) - f(bs0.get(k))
    wc = -d("inventory") - d("receivables") - d("other_assets") + d("payables") + d("other_liabilities")
    cash_ops = pl["ebitda"] + wc
    cfo = cash_ops + pl["int_inc"] - pl["int_exp"] - pl["tax"]
    capex = d("ppe") + pl["dep"]
    cfi = -capex
    loans = d("borrowings")
    equity_issue = d("share_capital")
    other_equity = d("retained") - (pl["pat"] - pl["div"])
    cff = loans + equity_issue - pl["div"] + other_equity
    net = cfo + cfi + cff
    open_net = f(bs0.get("cash")) - f(bs0.get("overdraft"))
    close_net = f(bs1.get("cash")) - f(bs1.get("overdraft"))
    return {"ebitda": pl["ebitda"], "d_inventory": -d("inventory"), "d_receivables": -d("receivables"),
            "d_other_assets": -d("other_assets"), "d_payables": d("payables"), "d_other_liabilities": d("other_liabilities"),
            "wc": wc, "cash_ops": cash_ops, "interest": pl["int_inc"] - pl["int_exp"], "tax": -pl["tax"], "cfo": cfo,
            "capex": -capex, "cfi": cfi, "loans": loans, "equity_issue": equity_issue, "dividends": -pl["div"],
            "other_equity": other_equity, "cff": cff, "net": net, "open_net_cash": open_net, "close_net_cash": close_net,
            "unreconciled": close_net - open_net - net}


CF_ROWS = [  # key, label, kind
    ("ebitda", "EBITDA", "line"), ("d_inventory", "Change in inventory", "indent"), ("d_receivables", "Change in receivables", "indent"),
    ("d_other_assets", "Change in other current assets", "indent"), ("d_payables", "Change in payables", "indent"),
    ("d_other_liabilities", "Change in other liabilities", "indent"), ("cash_ops", "Cash generated from operations", "total"),
    ("interest", "Net interest", "line"), ("tax", "Tax", "line"), ("cfo", "Net cash from operating activities", "total"),
    ("capex", "Capital expenditure (net)", "line"), ("cfi", "Net cash from investing activities", "total"),
    ("loans", "Net loan drawdown / (repayment)", "line"), ("equity_issue", "Share capital issued", "line"),
    ("dividends", "Dividends", "line"), ("other_equity", "Other equity movements", "line"),
    ("cff", "Net cash from financing activities", "total"), ("net", "Net change in cash", "total"),
    ("open_net_cash", "Opening cash (net of overdraft)", "line"), ("close_net_cash", "Closing cash (net of overdraft)", "grand"),
]


# --------------------------------------------------------------------------- budget & prior

def _dataset(data, name):
    return (data.get(name) or {})


def budget_months(data, name="budget"):
    """month -> P&L dict for a plan dataset ('budget' or 'forecast'), only months that have figures."""
    return {m: v for m, v in (_dataset(data, name).get("pl") or {}).items() if v is not None}


def budget_bs(data, name="budget"):
    return {m: {k: f(v.get(k)) for k in BS_KEYS} for m, v in (_dataset(data, name).get("bs") or {}).items() if v}


def budget_opening_bs(data, name="budget"):
    ob = _dataset(data, name).get("opening_bs")
    return {k: f(ob.get(k)) for k in BS_KEYS} if ob else None


def forecast_months(data):
    return budget_months(data, "forecast")


def forecast_bs(data):
    return budget_bs(data, "forecast")


def phase_annual(structure, annual, months, weights=None):
    """Spread annual budget figures over the 12 months (even unless weights given). Returns month -> values."""
    w = weights if weights and len(weights) == 12 and sum(weights) > 0 else [1.0] * 12
    tot = float(sum(w))
    return {m: {k: f(annual.get(k)) * w[i] / tot for k in pl_keys(structure)} for i, m in enumerate(months)}


def prior_pl(data):
    p = (data.get("prior") or {}).get("pl")
    structure = normalise_structure(data.get("structure"))
    if not p:
        return None
    return {k: f(p.get(k)) for k in pl_keys(structure)}


# --------------------------------------------------------------------------- trial balance upload

_HDR = {
    "code": ("account code", "code", "acc code", "account no", "account number", "acc no", "gl code", "a/c code", "a/c no"),
    "name": ("account name", "account", "description", "name", "gl name", "account description", "particulars", "a/c name"),
    "debit": ("debit", "dr", "debits", "current year debit", "debit balance", "closing debit"),
    "credit": ("credit", "cr", "credits", "current year credit", "credit balance", "closing credit"),
    "balance": ("balance", "closing balance", "net", "net balance", "amount", "net amount", "ytd balance", "ytd"),
}


def _to_num(v):
    return f(v, 0.0)


def parse_tb_table(rows):
    """rows: list of lists (raw cells). Finds the header row and returns [{code,name,net_dr}] (debit positive).
    Raises ValueError with a plain-English message when no usable layout is found."""
    hdr_i, cols = None, {}
    for i, row in enumerate(rows[:40]):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        found = {}
        for field, names in _HDR.items():
            for j, c in enumerate(cells):
                if c in names and field not in found:
                    found[field] = j
        if "name" in found and (("debit" in found and "credit" in found) or "balance" in found):
            hdr_i, cols = i, found
            break
    if hdr_i is None:
        raise ValueError("Could not find the columns. The file needs an 'Account Name' column and either 'Debit' and 'Credit' columns or one 'Balance' column "
                         "(debit positive, credit negative) on the first rows.")
    out = []
    for row in rows[hdr_i + 1:]:
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        get = lambda j: row[j] if j is not None and j < len(row) else None
        name = str(get(cols.get("name")) or "").strip()
        if not name or name.lower().startswith(("total", "grand total", "net profit", "difference")):
            continue
        code = str(get(cols.get("code")) or "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        if "debit" in cols and "credit" in cols:
            net = _to_num(get(cols["debit"])) - _to_num(get(cols["credit"]))
            if "balance" in cols and net == 0:
                net = _to_num(get(cols["balance"]))
        else:
            net = _to_num(get(cols["balance"]))
        out.append({"code": code, "name": name[:200], "net_dr": net})
    if not out:
        raise ValueError("No account rows found under the header row.")
    return out


def read_tb_file(file_storage, filename):
    """Read an uploaded .csv/.xlsx into raw rows then parse. `file_storage` is a file-like object (bytes)."""
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext == "csv":
        text = file_storage.read()
        text = text.decode("utf-8-sig", errors="replace") if isinstance(text, bytes) else text
        rows = list(csv.reader(io.StringIO(text)))
    elif ext in ("xlsx", "xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(file_storage, data_only=True)
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    else:
        raise ValueError("Please upload a .xlsx or .csv file.")
    return parse_tb_table(rows)


def norm_name(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


_CAT_TARGET = {
    "revenue": "REV", "cost_of_sales": "cogs", "other_income": "oth_inc", "distribution_costs": "OPEX", "admin_expenses": "OPEX",
    "other_expenses": "OPEX", "depreciation_amortisation": "dep", "finance_costs": "int_exp", "income_tax_expense": "tax",
    "dividends_paid": "div", "oci_items": "ignore",
    "ppe": "ppe", "intangible_assets": "ppe", "investment_property": "ppe", "long_term_investments": "ppe", "deferred_tax_asset": "ppe",
    "other_noncurrent_assets": "ppe", "inventories": "inventory", "trade_receivables": "receivables", "other_current_assets": "other_assets",
    "cash": "cash", "share_capital": "share_capital", "share_premium": "share_capital", "retained_earnings": "retained",
    "other_reserves": "retained", "long_term_borrowings": "borrowings", "short_term_borrowings": "borrowings",
    "deferred_tax_liability": "other_liabilities", "long_term_provisions": "other_liabilities",
    "other_noncurrent_liabilities": "other_liabilities", "trade_payables": "payables", "current_tax_payable": "other_liabilities",
    "short_term_provisions": "other_liabilities", "other_current_liabilities": "other_liabilities", "excluded": "ignore",
}


def category_to_target(category, name, structure):
    """Map an IAS 1 category (from the firm's existing account-mapping logic) to a pack line for this structure."""
    t = _CAT_TARGET.get(category)
    nm = norm_name(name)
    if t is None:
        return None
    if t == "REV":
        for r in structure["revenue"]:
            if norm_name(r["label"]) and norm_name(r["label"]) in nm:
                return r["key"]
        return structure["revenue"][0]["key"]
    if t == "OPEX":
        for o in structure["opex"]:
            if norm_name(o["label"]) and norm_name(o["label"]) in nm:
                return o["key"]
        cat_label = {"distribution_costs": "distribution", "admin_expenses": "admin", "other_expenses": "other"}.get(category, "")
        for o in structure["opex"]:
            if cat_label and cat_label in norm_name(o["label"]):
                return o["key"]
        return structure["opex"][0]["key"]
    if t == "oth_inc" and ("interest" in nm):
        return "int_inc"
    if t == "borrowings" and "overdraft" in nm:
        return "overdraft"
    return t


_BS_CATEGORIES = {"ppe", "intangible_assets", "investment_property", "other_noncurrent_assets", "other_current_assets", "inventories",
                  "trade_receivables", "trade_payables", "other_current_liabilities", "cash"}
_EXPENSE_WORDS = re.compile(r"\b(expenses?|rent|rentals?|fees?|commissions?|licen[cs]es?|consult\w*|water|levy|levies|rates|charges?|running costs?|"
                            r"postage|courier|cleaning|uniforms?|donations?|bad debts?)\b")
_BALANCE_WORDS = re.compile(r"(accumulated|provision|prepa|payable|receivable|deposit|accrual|owing|due (to|from)|loan|capital|reserve|retained|vat|tax control)")


def suggest_target(name, structure, pack_map=None, client_map=None, suggest_category=None):
    """Best guess at the pack line for a TB account: remembered pack mapping > client's IAS 1 mapping > keyword suggestion.
    Returns (target, how) where how in {'remembered','client','suggested',None}."""
    nm = norm_name(name)
    valid = set(pl_keys(structure)) | set(BS_KEYS) | {"ignore"}
    if pack_map and pack_map.get(nm) in valid:
        return pack_map[nm], "remembered"
    if client_map and client_map.get(nm):
        t = category_to_target(client_map[nm], name, structure)
        if t:
            return t, "client"
    if suggest_category:
        cat = suggest_category(name)
        # the firm-wide keyword rules miss a few everyday running costs (rent, fees, licences) and read "motor vehicle expenses"
        # as an asset: an account that sounds like a cost and not like a balance goes to operating expenses
        if (cat is None or cat in _BS_CATEGORIES) and _EXPENSE_WORDS.search(nm) and not _BALANCE_WORDS.search(nm):
            cat = "admin_expenses"
        if cat:
            t = category_to_target(cat, name, structure)
            if t:
                return t, "suggested"
    return None, None


def target_options(structure):
    """[(group, [(key,label)])] for the mapping dropdown."""
    pl = [(x["key"], x["label"]) for x in pl_line_defs(structure)]
    bs = [(k, lab) for k, lab, _ in BS_LINES]
    return [("Income statement", pl), ("Balance sheet", bs), ("Other", [("ignore", "Ignore (exclude)")])]


def entry_from_tb(rows, mapping, structure):
    """Build an entry from TB rows and a {row index (str) -> target} mapping.
    Returns {entry (kind ytd), stats}. P&L values are cumulative year-to-date, balances are closing."""
    keys = set(pl_keys(structure))
    pl = {k: 0.0 for k in keys}
    bs = {k: 0.0 for k in BS_KEYS}
    unmapped, ignored_total, mapped_n = [], 0.0, 0
    total_net = sum(r["net_dr"] for r in rows)
    for i, r in enumerate(rows):
        t = mapping.get(str(i))
        if not t:
            unmapped.append({"name": r["name"], "net_dr": r["net_dr"]})
            continue
        if t == "ignore":
            ignored_total += r["net_dr"]
            continue
        amt = -r["net_dr"] if (t in CREDIT_NATURAL or t.startswith("rev_")) else r["net_dr"]
        mapped_n += 1
        if t in keys:
            pl[t] += amt
        elif t in bs:
            bs[t] += amt
    # cash: a net credit balance on the bank lines is an overdraft
    if bs["cash"] < 0:
        bs["overdraft"] += -bs["cash"]
        bs["cash"] = 0.0
    vec = pl_vector(structure, pl)
    bs["retained"] = bs["retained"] + vec["pat"] - vec["div"]
    tot = bs_total(bs)
    stats = {"rows": len(rows), "mapped": mapped_n, "unmapped": unmapped, "ignored_net": ignored_total, "tb_out_by": total_net,
             "bs_check": tot["check"], "pat": vec["pat"], "revenue": vec["revenue"]}
    return {"kind": "ytd", "source": "tb", "pl": pl, "bs": bs}, stats


def to_prior(entry):
    """A TB (full prior year) becomes the prior-year accounts."""
    return {"source": entry.get("source", "tb"), "pl": dict(entry["pl"]), "bs": dict(entry["bs"])}


# --------------------------------------------------------------------------- modelled months

def modelled_entries(data, n_months, growth_pl=5.0, growth_bs=5.0, line_growth=None, start_idx=0):
    """Model months [start_idx, start_idx+n_months) of the financial year from the prior year's accounts.

    P&L: each month = prior-year annual figure x (1 + growth) / 12 (so 9 months = 9/12 of last year grown). Balance sheet: each
    line moves from the prior year-end towards year-end x (1 + growth) in proportion to the months elapsed; retained earnings
    rolls forward with the modelled profit and dividends; cash/overdraft is the balancing figure, so a funding gap shows as an
    overdraft. Returns {month: entry}, raises ValueError without prior accounts."""
    structure = normalise_structure(data.get("structure"))
    p = data.get("prior") or {}
    if not p.get("pl") or not p.get("bs"):
        raise ValueError("Enter the prior year's accounts first (Prior year tab): the modelled months are built from them.")
    keys = pl_keys(structure)
    pl0 = {k: f(p["pl"].get(k)) for k in keys}
    bs0 = {k: f(p["bs"].get(k)) for k in BS_KEYS}
    lg = line_growth or {}
    months = fy_months(data)
    n_months = max(1, min(12, int(n_months)))
    out = {}
    retained = bs0["retained"]
    # balances below the opening point for the first modelled month
    base_month = start_idx
    steps = n_months
    for i in range(start_idx, min(12, start_idx + n_months)):
        g = lambda k: f(lg.get(k), growth_pl) / 100.0
        vals = {k: pl0[k] * (1.0 + g(k)) / 12.0 for k in keys}
        vec = pl_vector(structure, vals)
        retained = retained + vec["pat"] - vec["div"]
        frac = (i - start_idx + 1) / float(steps)
        bs = {}
        for k in ("ppe", "inventory", "receivables", "other_assets", "payables", "other_liabilities", "borrowings", "share_capital"):
            gb = f(lg.get(k), growth_bs) / 100.0
            bs[k] = bs0[k] * (1.0 + gb * frac)
        bs["retained"] = retained
        noncash = bs["ppe"] + bs["inventory"] + bs["receivables"] + bs["other_assets"]
        funding = bs["payables"] + bs["other_liabilities"] + bs["borrowings"] + bs["share_capital"] + retained
        net_cash = funding - noncash   # cash less overdraft
        bs["cash"] = max(net_cash, 0.0)
        bs["overdraft"] = max(-net_cash, 0.0)
        out[months[i]] = {"kind": "month", "source": "modelled", "pl": vals, "bs": bs,
                          "note": f"Modelled: prior-year accounts x (1 + {growth_pl:g}%) / 12"}
    return out


# --------------------------------------------------------------------------- the statement tables

def variance(actual, budget, income_like):
    """(favourable-positive variance, % of budget) or (None, None)."""
    if actual is None or budget is None:
        return None, None
    var = (actual - budget) if income_like else (budget - actual)
    pct = var / abs(budget) if abs(budget) > 1e-9 else None
    return var, pct


def rag(pct, amber=5.0, red=10.0):
    """Traffic light for a favourable-positive % variance."""
    if pct is None:
        return "na"
    if pct >= -amber / 100.0:
        return "green"
    if pct >= -red / 100.0:
        return "amber"
    return "red"


def _sum_vectors(structure, vecs):
    out = {k: 0.0 for k in pl_keys(structure)}
    for v in vecs:
        for k in out:
            out[k] += f(v.get(k))
    return out


def vectors(data):
    """All the P&L vectors the pack needs. Each is a pl_vector or None when the source is missing."""
    structure = normalise_structure(data.get("structure"))
    months = fy_months(data)
    idx = report_index(data)
    n = idx + 1
    mon = monthly_actuals(data)
    cum = cumulative_actuals(data)
    s = settings_of(data)
    V = {"idx": idx, "n": n, "month": months[idx], "months": months}
    vec = lambda d: pl_vector(structure, d) if d is not None else None
    am = mon[idx]["values"]
    ay = cum[idx]["cum"]
    V["actual_month"] = vec(am)
    V["actual_ytd"] = vec(ay)
    V["actual_est"] = mon[idx]["est"]
    V["last_month"] = vec(mon[idx - 1]["values"]) if idx > 0 else None
    bud = budget_months(data)
    V["has_budget"] = bool(bud)
    V["budget_month"] = vec(bud[months[idx]]) if months[idx] in bud else None
    ytd_months = [bud[m] for m in months[:n] if m in bud]
    V["budget_ytd"] = vec(_sum_vectors(structure, ytd_months)) if len(ytd_months) == n else None
    V["budget_fy"] = vec(_sum_vectors(structure, [bud[m] for m in months])) if all(m in bud for m in months) else None
    rest_b = [bud[m] for m in months[n:] if m in bud]
    V["budget_rest_complete"] = len(rest_b) == 12 - n
    fc = forecast_months(data)
    V["has_forecast"] = bool(fc)
    V["forecast_month"] = vec(fc[months[idx]]) if months[idx] in fc else None
    rest_f = [fc[m] for m in months[n:] if m in fc]
    V["forecast_rest_complete"] = len(rest_f) == 12 - n and bool(fc)
    pp = prior_pl(data)
    V["has_prior"] = pp is not None
    V["prior_fy"] = vec(pp) if pp else None
    V["prior_month"] = vec(_vec_scale(structure, pp, 1.0 / 12.0)) if pp else None
    V["prior_ytd"] = vec(_vec_scale(structure, pp, n / 12.0)) if pp else None
    # full-year forecast ("outlook"): YTD actual + latest forecast for the remaining months, else + budget, else run-rate
    outlook, basis = None, None
    if ay is not None:
        mode = s["outlook"]
        if mode in ("auto", "forecast") and V["forecast_rest_complete"]:
            outlook = vec(_vec_add(structure, ay, _sum_vectors(structure, rest_f)))
            basis = "Year-to-date actual plus the latest forecast for the remaining months"
        elif mode in ("auto", "forecast", "budget_remaining") and V["budget_rest_complete"] and bud:
            outlook = vec(_vec_add(structure, ay, _sum_vectors(structure, rest_b)))
            basis = "Year-to-date actual plus the budget for the remaining months"
        else:
            outlook = vec(_vec_scale(structure, ay, 12.0 / n))
            basis = "Year-to-date actual annualised (run-rate)"
            if mode != "run_rate":
                basis += " - no forecast or budget covers every remaining month"
    V["outlook"], V["outlook_basis"] = outlook, basis
    V["forecast_fy"] = outlook
    return V


def balance_vectors(data):
    structure = normalise_structure(data.get("structure"))
    months = fy_months(data)
    idx = report_index(data)
    series = bs_series(data)
    bb = budget_bs(data)
    pb = prior_bs(data)
    prev_m = months[idx - 1] if idx > 0 else None
    B = {"actual": series.get(months[idx]), "budget": bb.get(months[idx]), "prior_year_end": pb,
         "prev_month": series.get(prev_m) if prev_m else pb, "series": series, "budget_series": bb}
    return B


def month_cash_flow(data, idx):
    """Cash flow for month idx: actual, budget."""
    structure = normalise_structure(data.get("structure"))
    months = fy_months(data)
    series = bs_series(data)
    mon = monthly_actuals(data)
    pb = prior_bs(data)
    start = series.get(months[idx - 1]) if idx > 0 else pb
    end = series.get(months[idx])
    pl = pl_vector(structure, mon[idx]["values"]) if mon[idx]["values"] is not None else None
    actual = cash_flow(structure, start, end, pl)
    bud = budget_months(data)
    bb = budget_bs(data)
    bstart = bb.get(months[idx - 1]) if idx > 0 else (budget_opening_bs(data) or pb)
    bend = bb.get(months[idx])
    bpl = pl_vector(structure, bud[months[idx]]) if months[idx] in bud else None
    budget = cash_flow(structure, bstart, bend, bpl)
    return actual, budget


def ytd_cash_flow(data):
    structure = normalise_structure(data.get("structure"))
    months = fy_months(data)
    idx = report_index(data)
    series = bs_series(data)
    pb = prior_bs(data)
    ay = cumulative_actuals(data)[idx]["cum"]
    end = series.get(months[idx])
    actual = cash_flow(structure, pb, end, pl_vector(structure, ay) if ay is not None else None)
    bud = budget_months(data)
    bb = budget_bs(data)
    ytdm = [bud[m] for m in months[:idx + 1] if m in bud]
    budget = None
    if len(ytdm) == idx + 1 and months[idx] in bb:
        budget = cash_flow(structure, budget_opening_bs(data) or pb, bb[months[idx]], pl_vector(structure, _sum_vectors(structure, ytdm)))
    return actual, budget


# --- income statement rows

def _rows_template(structure):
    rows = []
    for r in structure["revenue"]:
        rows.append({"key": r["key"], "label": r["label"], "kind": "indent", "income": True})
    rows += [
        {"key": "revenue", "label": "Revenue", "kind": "total", "income": True},
        {"key": "cogs", "label": "Cost of sales", "kind": "line", "income": False},
        {"key": "gross_profit", "label": "Gross profit", "kind": "total", "income": True},
        {"key": "gross_margin", "label": "Gross margin %", "kind": "pct", "income": True},
        {"key": "oth_inc", "label": "Other income", "kind": "line", "income": True},
    ]
    for o in structure["opex"]:
        rows.append({"key": o["key"], "label": o["label"], "kind": "indent", "income": False})
    rows += [
        {"key": "opex_total", "label": "Total operating expenses", "kind": "line", "income": False},
        {"key": "ebitda", "label": "EBITDA", "kind": "total", "income": True},
        {"key": "ebitda_margin", "label": "EBITDA margin %", "kind": "pct", "income": True},
        {"key": "dep", "label": "Depreciation", "kind": "line", "income": False},
        {"key": "ebit", "label": "Operating profit (EBIT)", "kind": "total", "income": True},
        {"key": "int_inc", "label": "Interest income", "kind": "line", "income": True},
        {"key": "int_exp", "label": "Interest expense", "kind": "line", "income": False},
        {"key": "pbt", "label": "Profit before tax", "kind": "total", "income": True},
        {"key": "tax", "label": "Income tax", "kind": "line", "income": False},
        {"key": "pat", "label": "Profit after tax", "kind": "grand", "income": True},
        {"key": "net_margin", "label": "Net margin %", "kind": "pct", "income": True},
        {"key": "div", "label": "Dividends declared", "kind": "memo", "income": False},
    ]
    return rows


def _cell(a, b, p, income, pct_row=False, f_=None):
    """One comparison block: actual, forecast, budget, variance to budget, variance %, last year, variance to last year."""
    if pct_row:
        var = (a - b) if (a is not None and b is not None) else None
        pvar = (a - p) if (a is not None and p is not None) else None
        return {"a": a, "f": f_, "b": b, "var": var, "varpct": None, "p": p, "pvar": pvar, "pvarpct": None}
    var, vp = variance(a, b, income)
    pvar, pvp = variance(a, p, income)
    return {"a": a, "f": f_, "b": b, "var": var, "varpct": vp, "p": p, "pvar": pvar, "pvarpct": pvp}


def income_rows(data):
    """Rows for the income statement page / PDF / Excel in the firm's layout:
    LAST MONTH | CURRENT MONTH (actual, forecast, budget, last year) | FULL YEAR (forecast, budget, last year),
    plus a year-to-date block. Each row: lm, month{a,f,b,p,var,varpct}, ytd{a,b,p,var,varpct}, fy{f,b,p,var,varpct}."""
    structure = normalise_structure(data.get("structure"))
    V = vectors(data)
    s = settings_of(data)
    rows = []
    g = lambda vec, k: (vec.get(k) if vec else None)
    for t in _rows_template(structure):
        k = t["key"]
        pct = t["kind"] == "pct"
        r = dict(t)
        r["lm"] = g(V["last_month"], k)
        r["month"] = _cell(g(V["actual_month"], k), g(V["budget_month"], k), g(V["prior_month"], k), t["income"], pct, g(V["forecast_month"], k))
        r["ytd"] = _cell(g(V["actual_ytd"], k), g(V["budget_ytd"], k), g(V["prior_ytd"], k), t["income"], pct)
        # full year: the forecast column is the outlook; the "actual" side of the variance is the forecast
        fy = _cell(g(V["outlook"], k), g(V["budget_fy"], k), g(V["prior_fy"], k), t["income"], pct)
        fy["f"] = fy.pop("a")
        r["fy"] = fy
        for blk in ("month", "ytd", "fy"):
            c = r[blk]
            c["rag"] = "na" if pct else rag(c["varpct"], s["rag_amber"], s["rag_red"])
        rows.append(r)
    return {"rows": rows, "V": V}


def _dv(a, b):
    return (a - b) if (a is not None and b is not None) else None


def balance_rows(data):
    """Balance sheet rows in the same layout. Each row: lm (previous month-end), a (actual), f (forecast), b (budget),
    py (prior year-end), ffy / bfy (forecast / budget at the financial year end), var (actual - budget)."""
    months = fy_months(data)
    idx = report_index(data)
    series, fser, bser = bs_series(data), forecast_bs(data), budget_bs(data)
    pb = prior_bs(data)
    prev = series.get(months[idx - 1]) if idx > 0 else pb
    A, F, Bg = series.get(months[idx]), fser.get(months[idx]), bser.get(months[idx])
    FFY, BFY = fser.get(months[-1]), bser.get(months[-1])
    sets = {"lm": prev, "a": A, "f": F, "b": Bg, "py": pb, "ffy": FFY, "bfy": BFY}
    tot = {k: (bs_total(v) if v else None) for k, v in sets.items()}

    def row(key, label, kind, total=None):
        r = {"key": key, "label": label, "kind": kind}
        for c, bs in sets.items():
            if bs is None:
                r[c] = None
            elif total:
                r[c] = tot[c][total] if total in ("assets", "equity", "liabilities", "check") else (tot[c]["equity"] + tot[c]["liabilities"])
            else:
                r[c] = f(bs.get(key))
        r["var"] = _dv(r["a"], r["b"])
        r["chg_year"] = _dv(r["a"], r["py"])
        r["chg_month"] = _dv(r["a"], r["lm"])
        return r

    rows = [row("ppe", BS_LABEL["ppe"], "line")]
    for k in ("inventory", "receivables", "other_assets", "cash"):
        rows.append(row(k, BS_LABEL[k], "indent"))
    rows.append(row("assets", "Total assets", "total", "assets"))
    rows.append(row("share_capital", BS_LABEL["share_capital"], "line"))
    rows.append(row("retained", BS_LABEL["retained"], "line"))
    rows.append(row("equity", "Total equity", "total", "equity"))
    for k in ("borrowings", "overdraft", "payables", "other_liabilities"):
        rows.append(row(k, BS_LABEL[k], "line"))
    rows.append(row("liabilities", "Total liabilities", "total", "liabilities"))
    rows.append(row("el", "Total equity and liabilities", "grand", "el"))
    rows.append(row("check", "Balance check (should be nil)", "memo", "check"))
    for key, label, fn in (
        ("wc", "Net working capital (excl. cash and debt)", lambda bs: f(bs["inventory"]) + f(bs["receivables"]) + f(bs["other_assets"]) - f(bs["payables"]) - f(bs["other_liabilities"])),
        ("netdebt", "Net debt / (net cash)", lambda bs: f(bs["borrowings"]) + f(bs["overdraft"]) - f(bs["cash"])),
    ):
        r = {"key": key, "label": label, "kind": "memo"}
        for c, bs in sets.items():
            r[c] = fn(bs) if bs else None
        r["var"] = _dv(r["a"], r["b"])
        r["chg_year"] = _dv(r["a"], r["py"])
        r["chg_month"] = _dv(r["a"], r["lm"])
        rows.append(r)
    return {"rows": rows, "month": months[idx], "has_forecast": F is not None or FFY is not None}


def cashflow_rows(data):
    """Cash flow rows in the same layout (flows for the month, year to date and full year).
    Each row: lm, a, f, b (current month), ya, yb (year to date), ffy, bfy (full year) - None where a balance sheet is missing."""
    structure = normalise_structure(data.get("structure"))
    months = fy_months(data)
    idx = report_index(data)
    a, b = month_cash_flow(data, idx)
    lm = month_cash_flow(data, idx - 1)[0] if idx > 0 else None
    fser, series = forecast_bs(data), bs_series(data)
    fc = forecast_months(data)
    pb = prior_bs(data)
    fstart = fser.get(months[idx - 1]) if idx > 0 else (budget_opening_bs(data, "forecast") or pb)
    fcf = cash_flow(structure, fstart, fser.get(months[idx]), pl_vector(structure, fc[months[idx]]) if months[idx] in fc else None)
    ya, yb = ytd_cash_flow(data)
    bud, bb = budget_months(data), budget_bs(data)
    bfy = None
    if all(m in bud for m in months) and months[-1] in bb:
        bfy = cash_flow(structure, budget_opening_bs(data) or pb, bb[months[-1]], pl_vector(structure, _sum_vectors(structure, [bud[m] for m in months])))
    V = vectors(data)
    ffy = None
    if months[-1] in fser and V["outlook"] is not None and V["forecast_rest_complete"]:
        ffy = cash_flow(structure, pb, fser[months[-1]], V["outlook"])
    rows = []
    for key, label, kind in CF_ROWS:
        g = lambda cf: (cf.get(key) if cf else None)
        r = {"key": key, "label": label, "kind": kind, "lm": g(lm), "a": g(a), "f": g(fcf), "b": g(b), "ya": g(ya), "yb": g(yb), "ffy": g(ffy), "bfy": g(bfy)}
        r["var"] = _dv(r["a"], r["b"])
        r["yvar"] = _dv(r["ya"], r["yb"])
        rows.append(r)
    return {"rows": rows, "month": months[idx], "unreconciled": (a or {}).get("unreconciled")}


# --------------------------------------------------------------------------- KPIs

def _days_in(n_months):
    return 365.0 * n_months / 12.0


def kpi_values(vec, bs, n_months, cf=None):
    """KPI dict from a P&L vector (period of n_months), a closing balance sheet and an optional cash flow."""
    if vec is None:
        return None
    out = {}
    rev = vec["revenue"]
    days = _days_in(n_months)
    out["gross_margin"] = vec["gross_margin"]
    out["ebitda_margin"] = vec["ebitda_margin"]
    out["net_margin"] = vec["net_margin"]
    out["opex_ratio"] = vec["opex_total"] / rev if abs(rev) > 1e-9 else None
    if bs:
        cur_a = f(bs["inventory"]) + f(bs["receivables"]) + f(bs["other_assets"]) + f(bs["cash"])
        cur_l = f(bs["payables"]) + f(bs["other_liabilities"]) + f(bs["overdraft"])
        out["current_ratio"] = cur_a / cur_l if cur_l > 1e-9 else None
        out["quick_ratio"] = (cur_a - f(bs["inventory"])) / cur_l if cur_l > 1e-9 else None
        out["receivable_days"] = f(bs["receivables"]) / rev * days if abs(rev) > 1e-9 else None
        cost = vec["cogs"] if vec["cogs"] > 1e-9 else vec["opex_total"]
        out["inventory_days"] = f(bs["inventory"]) / vec["cogs"] * days if vec["cogs"] > 1e-9 else None
        out["payable_days"] = f(bs["payables"]) / cost * days if cost > 1e-9 else None
        if None not in (out["receivable_days"], out["payable_days"]):
            out["cash_cycle"] = out["receivable_days"] + (out["inventory_days"] or 0.0) - out["payable_days"]
        else:
            out["cash_cycle"] = None
        debt = f(bs["borrowings"]) + f(bs["overdraft"])
        out["net_debt"] = debt - f(bs["cash"])
        eq = f(bs["share_capital"]) + f(bs["retained"])
        out["gearing"] = debt / eq if eq > 1e-9 else None
        ann_ebitda = vec["ebitda"] * 12.0 / n_months
        out["net_debt_ebitda"] = out["net_debt"] / ann_ebitda if ann_ebitda > 1e-9 else (None if out["net_debt"] <= 0 else float("inf"))
        out["working_capital"] = f(bs["inventory"]) + f(bs["receivables"]) + f(bs["other_assets"]) - f(bs["payables"]) - f(bs["other_liabilities"])
        out["cash"] = f(bs["cash"]) - f(bs["overdraft"])
    ie = vec["int_exp"]
    out["interest_cover"] = vec["ebit"] / ie if ie > 1e-9 else None
    if cf:
        burn = -cf["net"] / n_months if cf["net"] < 0 else 0.0
        out["cash_runway"] = (out.get("cash", 0.0) / burn) if burn > 1e-9 and out.get("cash", 0.0) > 0 else (0.0 if burn > 1e-9 else None)
    return out


KPI_DEFS = [
    # key, label, fmt, rule
    ("gross_margin", "Gross margin", "pct", ("vs", "hi")),
    ("ebitda_margin", "EBITDA margin", "pct", ("vs", "hi")),
    ("net_margin", "Net margin", "pct", ("vs", "hi")),
    ("opex_ratio", "Operating expenses / revenue", "pct", ("vs", "lo")),
    ("current_ratio", "Current ratio", "x", ("abs", "hi", 1.5, 1.0)),
    ("quick_ratio", "Quick ratio", "x", ("abs", "hi", 1.0, 0.7)),
    ("working_capital", "Net working capital", "money", None),
    ("receivable_days", "Receivable days", "days", ("vs", "lo")),
    ("inventory_days", "Inventory days", "days", ("vs", "lo")),
    ("payable_days", "Payable days", "days", None),
    ("cash_cycle", "Cash conversion cycle (days)", "days", ("vs", "lo")),
    ("cash", "Cash, net of overdraft", "money", None),
    ("net_debt", "Net debt / (net cash)", "money", None),
    ("net_debt_ebitda", "Net debt / EBITDA (annualised)", "x", ("abs", "lo", 2.0, 3.5)),
    ("gearing", "Gearing (debt / equity)", "x", ("abs", "lo", 1.0, 2.0)),
    ("interest_cover", "Interest cover (EBIT / interest)", "x", ("abs", "hi", 3.0, 1.5)),
    ("cash_runway", "Cash runway (months, if burning cash)", "months", ("abs", "hi", 6.0, 3.0)),
]


def _kpi_rag(rule, actual, budget, prior, amber):
    if rule is None or actual is None:
        return "na"
    if actual == float("inf"):
        return "red"
    if rule[0] == "abs":
        _, direction, good, ok = rule
        if direction == "hi":
            return "green" if actual >= good else ("amber" if actual >= ok else "red")
        return "green" if actual <= good else ("amber" if actual <= ok else "red")
    ref = budget if budget is not None else prior
    if ref is None:
        return "na"
    direction = rule[1]
    tol = amber / 100.0
    scale = abs(ref) if abs(ref) > 1e-9 else 1.0
    diff = (actual - ref) / scale if direction == "hi" else (ref - actual) / scale
    if diff >= -tol:
        return "green"
    return "amber" if diff >= -2 * tol else "red"


def kpi_table(data):
    structure = normalise_structure(data.get("structure"))
    V = vectors(data)
    B = balance_vectors(data)
    s = settings_of(data)
    idx, n = V["idx"], V["n"]
    cf_a, cf_b = ytd_cash_flow(data)
    act = kpi_values(V["actual_ytd"], B["actual"], n, cf_a)
    bud = kpi_values(V["budget_ytd"], B["budget"], n, cf_b) if V["budget_ytd"] is not None else None
    pri = kpi_values(V["prior_fy"], B["prior_year_end"], 12) if V["prior_fy"] is not None else None
    rows = []
    for key, label, fmt, rule in KPI_DEFS:
        a = (act or {}).get(key)
        b = (bud or {}).get(key)
        p = (pri or {}).get(key)
        rows.append({"key": key, "label": label, "fmt": fmt, "actual": a, "budget": b, "prior": p,
                     "rag": _kpi_rag(rule, a, b, p, s["rag_amber"]) if act else "na"})
    return rows


# --------------------------------------------------------------------------- variance list & commentary

def variance_list(data):
    """Every input P&L line ranked by size of variance (YTD), with month figures."""
    structure = normalise_structure(data.get("structure"))
    V = vectors(data)
    s = settings_of(data)
    out = []
    for d in pl_line_defs(structure):
        if d["key"] == "div":
            continue
        k = d["key"]
        am = V["actual_month"][k] if V["actual_month"] else None
        bm = V["budget_month"][k] if V["budget_month"] else None
        ay = V["actual_ytd"][k] if V["actual_ytd"] else None
        by = V["budget_ytd"][k] if V["budget_ytd"] else None
        py = V["prior_ytd"][k] if V["prior_ytd"] else None
        vm, vmp = variance(am, bm, d["income"])
        vy, vyp = variance(ay, by, d["income"])
        pv, pvp = variance(ay, py, d["income"])
        out.append({"key": k, "label": d["label"], "income": d["income"], "month_actual": am, "month_budget": bm, "month_var": vm, "month_pct": vmp,
                    "ytd_actual": ay, "ytd_budget": by, "ytd_var": vy, "ytd_pct": vyp, "ytd_prior": py, "prior_var": pv, "prior_pct": pvp,
                    "rag": rag(vyp, s["rag_amber"], s["rag_red"])})
    return out


def _m(v, cur=""):
    if v is None:
        return "n/a"
    s = f"{abs(v):,.0f}"
    return f"({cur}{s})" if v < 0 else f"{cur}{s}"


def key_messages(data, cur=""):
    """Plain-English headlines, written from the numbers."""
    structure = normalise_structure(data.get("structure"))
    V = vectors(data)
    B = balance_vectors(data)
    lab = month_label(V["month"], short=False)
    msgs = []
    am, ay = V["actual_month"], V["actual_ytd"]
    if ay is None:
        return ["No actual figures have been captured for this period yet."]
    def vs(label, a, b, income=True):
        var, pct = variance(a, b, income)
        if var is None:
            return None
        word = "ahead of" if var >= 0 else "behind"
        if not income:
            word = "below" if var >= 0 else "above"
        pc = f" ({abs(pct) * 100:.1f}%)" if (pct is not None and abs(pct) < 10) else ""
        return f"{word} budget by {cur}{abs(var):,.0f}{pc}"
    if am is not None:
        t = vs("rev", am["revenue"], V["budget_month"]["revenue"] if V["budget_month"] else None)
        msgs.append(f"{lab}: revenue was {cur}{am['revenue']:,.0f}" + (f", {t}." if t else "."))
        t = vs("ebitda", am["ebitda"], V["budget_month"]["ebitda"] if V["budget_month"] else None)
        msgs.append(f"EBITDA for the month was {_m(am['ebitda'], cur)}" + (f", {t}." if t else "."))
    t = vs("rev", ay["revenue"], V["budget_ytd"]["revenue"] if V["budget_ytd"] else None)
    msgs.append(f"Year to date, revenue is {cur}{ay['revenue']:,.0f}" + (f", {t}." if t else "."))
    t = vs("pat", ay["pat"], V["budget_ytd"]["pat"] if V["budget_ytd"] else None)
    msgs.append(f"Year-to-date profit after tax is {_m(ay['pat'], cur)}" + (f", {t}." if t else "."))
    if V["prior_ytd"] is not None and V["prior_ytd"]["revenue"]:
        g = (ay["revenue"] / V["prior_ytd"]["revenue"] - 1) * 100
        msgs.append(f"Revenue is {abs(g):.1f}% {'up' if g >= 0 else 'down'} on the same period of {prior_fy_label(data)} (pro-rata).")
    if ay["gross_margin"] is not None:
        msgs.append(f"Gross margin is {ay['gross_margin'] * 100:.1f}% and EBITDA margin {(ay['ebitda_margin'] or 0) * 100:.1f}% year to date.")
    A = B["actual"]
    if A:
        netcash = f(A["cash"]) - f(A["overdraft"])
        bt = f" against a budget of {_m((B['budget']['cash'] - B['budget']['overdraft']) if B['budget'] else None, cur)}" if B["budget"] else ""
        msgs.append(f"Closing cash (net of overdraft) is {_m(netcash, cur)}{bt}.")
        if f(A["overdraft"]) > _TOL:
            msgs.append(f"The business is overdrawn by {cur}{f(A['overdraft']):,.0f}: a funding requirement to address.")
    ser = B["series"]
    peak = max((f(b["overdraft"]) for b in ser.values()), default=0.0)
    if peak > _TOL:
        msgs.append(f"Peak overdraft / funding requirement across the months captured: {cur}{peak:,.0f}.")
    vl = [x for x in variance_list(data) if x["ytd_pct"] is not None]
    bad = [x for x in vl if x["rag"] == "red"]
    if bad:
        msgs.append("Lines more than " + f"{settings_of(data)['rag_red']:g}% adverse to budget year to date: " + ", ".join(x["label"] for x in bad[:6]) + ".")
    if V["outlook"] is not None and V["budget_fy"] is not None:
        var, pct = variance(V["outlook"]["pat"], V["budget_fy"]["pat"], True)
        if var is not None:
            msgs.append(f"Full-year outlook: profit after tax of {_m(V['outlook']['pat'], cur)}, {'ahead of' if var >= 0 else 'behind'} the full-year budget by {cur}{abs(var):,.0f}.")
    if not V["has_budget"]:
        msgs.append("No budget is loaded, so there are no variances. Add one on the Budget tab.")
    return msgs


def data_status(data):
    """Per-month status for the Actuals tab."""
    months = fy_months(data)
    entries = data.get("entries") or {}
    cum = cumulative_actuals(data)
    series = bs_series(data)
    out = []
    for i, m in enumerate(months):
        e = entries.get(m)
        bs = series.get(m)
        chk = bs_total(bs)["check"] if bs else None
        out.append({"month": m, "label": month_label(m, short=False), "has_entry": bool(e), "kind": (e or {}).get("kind"),
                    "source": (e or {}).get("source") or (cum[i]["source"] if cum[i]["est"] else None), "est": cum[i]["est"],
                    "has_pl": cum[i]["cum"] is not None, "has_bs": bs is not None, "bs_check": chk,
                    "note": (e or {}).get("note"), "saved": (e or {}).get("saved")})
    return out


def warnings(data):
    w = []
    for st in data_status(data):
        if st["has_bs"] and st["bs_check"] is not None and abs(st["bs_check"]) > _TOL:
            w.append(f"{st['label']}: the balance sheet is out by {st['bs_check']:,.0f}.")
    if (data.get("prior") or {}).get("bs"):
        t = bs_total(prior_bs(data))
        if abs(t["check"]) > _TOL:
            w.append(f"The prior-year balance sheet is out by {t['check']:,.0f}.")
    return w


# --------------------------------------------------------------------------- budget from a forecast model

def budget_from_model(structure, result, a, fy_month_keys):
    """Snapshot of a forecast model's year-1 monthly figures mapped onto the pack's lines.
    Returns (budget dict {pl, bs, opening_bs, note}, notes)."""
    notes = []
    start = a["start_month"]
    try:
        sy, sm = parse_month(str(start)[:7])
    except ValueError:
        sy, sm = date.today().year, 1
    first = mkey(sy, sm)
    rev_names = [r["label"] for r in structure["revenue"]]
    opx_names = [o["label"] for o in structure["opex"]]
    def find(names, keys, name):
        for i, nm in enumerate(names):
            if norm_name(nm) == norm_name(name):
                return keys[i]
        return keys[0]
    rkeys = [r["key"] for r in structure["revenue"]]
    okeys = [o["key"] for o in structure["opex"]]
    pl, bs = {}, {}
    rows = result["periods"][:12]
    for i, r in enumerate(rows):
        mk = shift_month(first, i)
        v = {k: 0.0 for k in pl_keys(structure)}
        for j, st in enumerate(a["revenue"]):
            v[find(rev_names, rkeys, st["name"])] += f(r["streams"][j])
        v["cogs"] = f(r["cogs"])
        for j, o in enumerate(a["opex"]):
            v[find(opx_names, okeys, o["name"])] += f(r["opex_lines"][j])
        v["dep"] = f(r["dep"])
        v["int_inc"] = f(r["int_income"])
        v["int_exp"] = f(r["int_loans"]) + f(r["int_od"])
        v["tax"] = f(r["tax"])
        v["div"] = f(r["dividends"])
        pl[mk] = v
        bs[mk] = {"ppe": r["ppe_nbv"], "inventory": r["inventory"], "receivables": r["receivables"], "other_assets": r["other_assets"],
                  "cash": r["cash_positive"], "overdraft": r["overdraft"], "payables": r["payables"], "other_liabilities": r["other_liabilities"],
                  "borrowings": r["borrowings"], "share_capital": r["share_capital"], "retained": r["retained"]}
    o = a["opening"]
    loans = sum(L["opening_balance"] for L in a["loans"])
    res0 = result.get("opening_reserves", 0.0)
    opening = {"ppe": o["ppe_cost"] - o["ppe_accdep"], "inventory": o["inventory"], "receivables": o["receivables"],
               "other_assets": o["other_assets"], "cash": max(o["cash"], 0.0), "overdraft": max(-o["cash"], 0.0),
               "payables": o["payables"], "other_liabilities": o["other_liabilities"], "borrowings": loans,
               "share_capital": o["share_capital"], "retained": res0}
    covered = [m for m in fy_month_keys if m in pl]
    if len(covered) < 12:
        notes.append(f"The model covers {len(covered)} of this financial year's 12 months (it starts {month_label(first, False)}).")
    if len(a["revenue"]) > len(rkeys) or len(a["opex"]) > len(okeys):
        pass
    return {"pl": pl, "bs": bs, "opening_bs": opening}, notes


def structure_from_model(a):
    """Pack lines named after the model's revenue streams and expense lines."""
    rev = [{"key": f"rev_{i + 1}", "label": (s["name"] or f"Revenue {i + 1}").strip()[:60]} for i, s in enumerate(a["revenue"])]
    opx = [{"key": f"opx_{i + 1}", "label": (o["name"] or f"Expense {i + 1}").strip()[:60]} for i, o in enumerate(a["opex"])]
    return normalise_structure({"revenue": rev, "opex": opx})
