"""Corporate Finance & Business Advisory - driver-based three-statement forecast engine.

Pure Python, no Flask / database imports, so it can be tested on its own and
so cf_excel.py can build a formula workbook that reproduces it figure for
figure.

Model shape
-----------
* 1 to 10 forecast years (``years``). Year 1 is run MONTH BY MONTH (12 periods,
  phased with a seasonality profile); years 2..N are run as ANNUAL periods.
  Every statement is therefore one continuous list of periods and the annual
  view simply adds the flows of year 1's twelve months and takes the closing
  balances of month 12.
* Income statement: revenue streams (growth by year), cost of sales (% of
  revenue), operating expense lines (fixed amount with growth, or % of revenue),
  depreciation (straight line on gross cost, never below nil NBV), finance
  income / cost, tax with assessed-loss carry forward, dividends.
* Balance sheet: PPE, inventory, receivables, other assets, cash (negative cash
  is shown as an overdraft), trade payables, other liabilities, borrowings,
  share capital, retained earnings & reserves. The opening reserves figure is
  the balancing figure of the opening balance sheet, and the forecast balance
  sheet balances in every period by construction (``checks`` reports it).
* Cash flow statement: indirect method; closing cash agrees to the balance sheet.
* Working capital balances are run-rate based: balance = annualised flow of the
  period x days / 365. Interest is charged on OPENING balances (no circular
  references, so the Excel version needs no iterative calculation). Tax and
  dividends are paid in the period they arise. The model is VAT-exclusive.

Everything here is deterministic and unrounded; presentation rounds.
"""
import copy
from datetime import date

MAX_YEARS = 10
MIN_YEARS = 1

TAX_RATE_DEFAULT = 25.75  # Zimbabwe: 25% income tax + 3% AIDS levy on the tax

BUSINESS_PRESETS = {
    "blank": "General business",
    "trading": "Trading / distribution",
    "services": "Professional / services",
    "manufacturing": "Manufacturing / processing",
}

_TOL = 0.01


# --------------------------------------------------------------------------- helpers

def f(value, default=0.0):
    """Forgiving float: '1,234.5' -> 1234.5; blank / junk -> default."""
    try:
        if value is None or value == "":
            return float(default)
        return float(str(value).replace(",", "").replace(" ", "").replace("%", ""))
    except (TypeError, ValueError):
        return float(default)


def _series(value, n, default=0.0):
    """A per-year list of length n. A scalar is repeated; a short list is padded with its last value;
    a long list is cut."""
    if isinstance(value, (list, tuple)):
        vals = [f(v, default) for v in value]
    elif value is None or value == "":
        vals = []
    else:
        vals = [f(value, default)]
    if not vals:
        vals = [float(default)]
    if len(vals) < n:
        vals = vals + [vals[-1]] * (n - len(vals))
    return vals[:n]


def clamp_years(value, default=5):
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        n = default
    return max(MIN_YEARS, min(MAX_YEARS, n))


def parse_start_month(value):
    """'2027-01' / '2027-01-01' / date -> date(2027, 1, 1)."""
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    try:
        s = str(value)[:7]
        return date(int(s[:4]), int(s[5:7]), 1)
    except (TypeError, ValueError, IndexError):
        t = date.today()
        return date(t.year + (1 if t.month > 1 else 0), 1, 1)


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------- assumptions

def default_assumptions(years=5, currency="USD", preset="blank", start_month=None):
    """A sensible, internally consistent starter model the preparer then overwrites."""
    years = clamp_years(years)
    p = {
        "blank": dict(cogs=55.0, inv=60.0, rec=45.0, pay=45.0, capex=50000.0, growth=8.0),
        "trading": dict(cogs=66.0, inv=60.0, rec=30.0, pay=45.0, capex=30000.0, growth=8.0),
        "services": dict(cogs=35.0, inv=0.0, rec=60.0, pay=30.0, capex=25000.0, growth=10.0),
        "manufacturing": dict(cogs=60.0, inv=90.0, rec=45.0, pay=45.0, capex=120000.0, growth=7.0),
    }.get(preset, None) or dict(cogs=55.0, inv=60.0, rec=45.0, pay=45.0, capex=50000.0, growth=8.0)
    a = {
        "years": years,
        "start_month": (parse_start_month(start_month) if start_month else parse_start_month(None)).isoformat()[:7],
        "currency": currency,
        "preset": preset if preset in BUSINESS_PRESETS else "blank",
        "tax_rate": TAX_RATE_DEFAULT,
        "seasonality": [100.0 / 12] * 12,
        "revenue": [{"name": "Revenue", "y1": 1000000.0, "growth": [0.0] + [p["growth"]] * (years - 1)}],
        "cogs_pct": [p["cogs"]] * years,
        "opex": [
            {"name": "Salaries and wages", "kind": "fixed", "y1": 150000.0, "growth": [0.0] + [6.0] * (years - 1),
             "pct": [0.0] * years, "creditors": False},
            {"name": "Rent and utilities", "kind": "fixed", "y1": 60000.0, "growth": [0.0] + [5.0] * (years - 1),
             "pct": [0.0] * years, "creditors": True},
            {"name": "Administration and other", "kind": "fixed", "y1": 40000.0, "growth": [0.0] + [5.0] * (years - 1),
             "pct": [0.0] * years, "creditors": True},
            {"name": "Selling and marketing", "kind": "pct", "y1": 0.0, "growth": [0.0] * years,
             "pct": [2.0] * years, "creditors": True},
        ],
        "capex": [p["capex"]] * years,
        "dep_rate": 15.0,
        "rec_days": [p["rec"]] * years,
        "inv_days": [p["inv"]] * years,
        "pay_days": [p["pay"]] * years,
        "opening": {
            "ppe_cost": 400000.0, "ppe_accdep": 150000.0, "inventory": 150000.0 if p["inv"] else 0.0,
            "receivables": 120000.0, "other_assets": 20000.0, "cash": 100000.0,
            "payables": 100000.0, "other_liabilities": 30000.0, "share_capital": 200000.0,
            "tax_loss_bf": 0.0,
        },
        "loans": [
            {"name": "Existing term loan", "opening_balance": 150000.0, "drawdown_amount": 0.0, "drawdown_year": 1,
             "rate": 12.0, "repay_type": "straight", "repay_years": 5, "grace_years": 0, "maturity_year": 5},
        ],
        "equity_issue": [0.0] * years,
        "dividend_payout": [0.0] * years,
        "deposit_rate": 2.0,
        "overdraft_rate": 18.0,
        "covenants": {"min_dscr": 1.25, "min_interest_cover": 3.0, "max_debt_equity": 1.5,
                      "min_current_ratio": 1.0, "max_net_debt_ebitda": 3.0},
    }
    return normalise(a)


def normalise(a):
    """Return a clean copy of assumptions: right-length yearly lists, floats, bounded values.
    Safe to call on partial / old / user-edited data (this is what resizes a model when the
    number of years is changed)."""
    base = a or {}
    years = clamp_years(base.get("years", 5))
    out = {
        "years": years,
        "start_month": parse_start_month(base.get("start_month")).isoformat()[:7],
        "currency": base.get("currency") or "USD",
        "preset": base.get("preset") if base.get("preset") in BUSINESS_PRESETS else "blank",
        "tax_rate": max(0.0, min(100.0, f(base.get("tax_rate"), TAX_RATE_DEFAULT))),
    }
    season = _series(base.get("seasonality"), 12, 100.0 / 12)
    total = sum(season)
    out["seasonality"] = [v * 100.0 / total for v in season] if total > 0 else [100.0 / 12] * 12

    streams = []
    for s in (base.get("revenue") or [])[:12]:
        g = _series(s.get("growth"), years, 0.0)
        g[0] = 0.0
        streams.append({"name": (s.get("name") or "Revenue").strip()[:60] or "Revenue", "y1": max(0.0, f(s.get("y1"))), "growth": g})
    out["revenue"] = streams or [{"name": "Revenue", "y1": 0.0, "growth": [0.0] * years}]

    out["cogs_pct"] = [max(0.0, v) for v in _series(base.get("cogs_pct"), years, 0.0)]

    lines = []
    for o in (base.get("opex") or [])[:30]:
        kind = "pct" if o.get("kind") == "pct" else "fixed"
        g = _series(o.get("growth"), years, 0.0)
        g[0] = 0.0
        lines.append({
            "name": (o.get("name") or "Expense").strip()[:60] or "Expense", "kind": kind,
            "y1": max(0.0, f(o.get("y1"))), "growth": g, "pct": [max(0.0, v) for v in _series(o.get("pct"), years, 0.0)],
            "creditors": bool(o.get("creditors", True)),
        })
    out["opex"] = lines

    out["capex"] = [max(0.0, v) for v in _series(base.get("capex"), years, 0.0)]
    out["dep_rate"] = max(0.0, min(100.0, f(base.get("dep_rate"), 15.0)))
    out["rec_days"] = [max(0.0, v) for v in _series(base.get("rec_days"), years, 0.0)]
    out["inv_days"] = [max(0.0, v) for v in _series(base.get("inv_days"), years, 0.0)]
    out["pay_days"] = [max(0.0, v) for v in _series(base.get("pay_days"), years, 0.0)]

    op = base.get("opening") or {}
    out["opening"] = {k: f(op.get(k)) for k in (
        "ppe_cost", "ppe_accdep", "inventory", "receivables", "other_assets", "cash",
        "payables", "other_liabilities", "share_capital", "tax_loss_bf")}
    for k in ("ppe_cost", "ppe_accdep", "inventory", "receivables", "other_assets", "payables",
              "other_liabilities", "tax_loss_bf"):
        out["opening"][k] = max(0.0, out["opening"][k])
    out["opening"]["ppe_accdep"] = min(out["opening"]["ppe_accdep"], out["opening"]["ppe_cost"])

    loans = []
    for L in (base.get("loans") or [])[:10]:
        rt = "bullet" if L.get("repay_type") == "bullet" else "straight"
        loans.append({
            "name": (L.get("name") or "Loan").strip()[:60] or "Loan",
            "opening_balance": max(0.0, f(L.get("opening_balance"))),
            "drawdown_amount": max(0.0, f(L.get("drawdown_amount"))),
            "drawdown_year": max(1, min(years, int(f(L.get("drawdown_year"), 1)))),
            "rate": max(0.0, f(L.get("rate"), 12.0)),
            "repay_type": rt,
            "repay_years": max(1, min(30, int(f(L.get("repay_years"), 5)))),
            "grace_years": max(0, min(10, int(f(L.get("grace_years"), 0)))),
            "maturity_year": max(1, min(30, int(f(L.get("maturity_year"), years)))),
        })
    out["loans"] = loans

    out["equity_issue"] = [max(0.0, v) for v in _series(base.get("equity_issue"), years, 0.0)]
    out["dividend_payout"] = [max(0.0, min(100.0, v)) for v in _series(base.get("dividend_payout"), years, 0.0)]
    out["deposit_rate"] = max(0.0, f(base.get("deposit_rate"), 0.0))
    out["overdraft_rate"] = max(0.0, f(base.get("overdraft_rate"), 0.0))
    cov = base.get("covenants") or {}
    out["covenants"] = {k: (None if cov.get(k) in (None, "") else f(cov.get(k)))
                        for k in ("min_dscr", "min_interest_cover", "max_debt_equity", "min_current_ratio", "max_net_debt_ebitda")}
    return out


# --------------------------------------------------------------------------- scenarios

SHOCK_FIELDS = [
    ("revenue_pct", "Revenue level (% change, all years)", 0.0),
    ("growth_pp", "Revenue growth (percentage points, years 2+)", 0.0),
    ("cogs_pp", "Cost of sales (percentage points of revenue)", 0.0),
    ("opex_pct", "Operating expenses (% change)", 0.0),
    ("rec_days", "Receivable days (+/- days)", 0.0),
    ("inv_days", "Inventory days (+/- days)", 0.0),
    ("pay_days", "Payable days (+/- days)", 0.0),
    ("capex_pct", "Capital expenditure (% change)", 0.0),
    ("rate_pp", "Loan interest rates (percentage points)", 0.0),
]


def apply_shocks(a, shocks):
    """Base assumptions + a scenario's adjustments -> assumptions to run."""
    a = copy.deepcopy(normalise(a))
    s = {k: f((shocks or {}).get(k)) for k, _, _ in SHOCK_FIELDS}
    n = a["years"]
    for st in a["revenue"]:
        st["y1"] = max(0.0, st["y1"] * (1 + s["revenue_pct"] / 100.0))
        st["growth"] = [0.0] + [st["growth"][i] + s["growth_pp"] for i in range(1, n)]
    a["cogs_pct"] = [max(0.0, v + s["cogs_pp"]) for v in a["cogs_pct"]]
    for o in a["opex"]:
        o["y1"] = max(0.0, o["y1"] * (1 + s["opex_pct"] / 100.0))
        o["pct"] = [max(0.0, v * (1 + s["opex_pct"] / 100.0)) for v in o["pct"]]
    a["rec_days"] = [max(0.0, v + s["rec_days"]) for v in a["rec_days"]]
    a["inv_days"] = [max(0.0, v + s["inv_days"]) for v in a["inv_days"]]
    a["pay_days"] = [max(0.0, v + s["pay_days"]) for v in a["pay_days"]]
    a["capex"] = [max(0.0, v * (1 + s["capex_pct"] / 100.0)) for v in a["capex"]]
    for L in a["loans"]:
        L["rate"] = max(0.0, L["rate"] + s["rate_pp"])
    return a


# --------------------------------------------------------------------------- periods

def build_periods(a):
    """12 monthly periods for year 1, then one annual period per further year."""
    a = normalise(a)
    start = parse_start_month(a["start_month"])
    out = []
    for m in range(12):
        d = add_months(start, m)
        out.append({"idx": len(out), "label": f"{MONTH_ABBR[d.month - 1]}-{str(d.year)[2:]}", "year_no": 1, "months": 1,
                    "is_year_start": 1 if m == 0 else 0, "is_year_end": 1 if m == 11 else 0, "share": a["seasonality"][m] / 100.0})
    for y in range(2, a["years"] + 1):
        d = add_months(start, 12 * (y - 1))
        end = add_months(start, 12 * y - 1)
        out.append({"idx": len(out), "label": year_label(a, y), "year_no": y, "months": 12,
                    "is_year_start": 1, "is_year_end": 1, "share": 1.0})
    return out


def year_label(a, y):
    start = parse_start_month(a["start_month"])
    end = add_months(start, 12 * y - 1)
    if end.month == 12:
        return f"FY{end.year}"
    return f"{MONTH_ABBR[end.month - 1]}-{str(end.year)[2:]} (Y{y})"


def annual_amounts(a):
    """Annual revenue per stream and annual fixed opex per line, by year (compounded growth)."""
    a = normalise(a)
    n = a["years"]
    rev = []
    for st in a["revenue"]:
        row = [st["y1"]]
        for y in range(1, n):
            row.append(row[-1] * (1 + st["growth"][y] / 100.0))
        rev.append(row)
    fixed = []
    for o in a["opex"]:
        row = [o["y1"]]
        for y in range(1, n):
            row.append(row[-1] * (1 + o["growth"][y] / 100.0))
        fixed.append(row)
    return rev, fixed


# --------------------------------------------------------------------------- core run

FLOW_KEYS = [
    "revenue", "cogs", "opex", "creditor_opex", "ebitda", "capex", "dep", "ebit", "int_income", "int_loans", "int_od",
    "fin_cost", "pbt", "tax", "pat", "dividends", "equity_issue",
    "d_inventory", "d_receivables", "d_payables", "cash_ops", "interest_paid", "interest_received", "tax_paid",
    "cfo", "cfi", "loan_draw", "loan_repay", "cff", "net_cash", "loss_used", "loss_added",
]
BALANCE_KEYS = [
    "inventory", "receivables", "payables", "other_assets", "other_liabilities", "ppe_gross", "ppe_accdep", "ppe_nbv",
    "borrowings", "cash", "overdraft", "cash_positive", "share_capital", "retained", "loss_close",
    "total_assets", "total_liabilities", "total_equity", "check",
]


def opening_reserves(a):
    """Retained earnings & reserves at the start = the balancing figure of the opening balance sheet."""
    a = normalise(a)
    o = a["opening"]
    loans = sum(L["opening_balance"] for L in a["loans"])
    assets = (o["ppe_cost"] - o["ppe_accdep"]) + o["inventory"] + o["receivables"] + o["other_assets"] + max(o["cash"], 0.0)
    liabilities = o["payables"] + o["other_liabilities"] + loans + max(-o["cash"], 0.0)
    return assets - liabilities - o["share_capital"]


def run(a):
    """Run the model. Returns {'a','periods','annual','checks','warnings','opening'}."""
    a = normalise(a)
    n = a["years"]
    per = build_periods(a)
    rev_year, fixed_year = annual_amounts(a)
    op = a["opening"]
    tax_rate = a["tax_rate"] / 100.0
    dep_rate = a["dep_rate"] / 100.0

    # opening position
    gross = op["ppe_cost"]
    accdep = op["ppe_accdep"]
    inventory, receivables, payables = op["inventory"], op["receivables"], op["payables"]
    cash = op["cash"]
    share_capital = op["share_capital"]
    retained = opening_reserves(a)
    loss_bf = op["tax_loss_bf"]
    loan_close = [L["opening_balance"] for L in a["loans"]]

    rows = []
    for p in per:
        y = p["year_no"] - 1
        m = p["months"]
        r = dict(p)
        # --- income statement
        streams = [rev_year[i][y] * p["share"] for i in range(len(a["revenue"]))]
        revenue = sum(streams)
        cogs = revenue * a["cogs_pct"][y] / 100.0
        opex_lines, creditor_opex = [], 0.0
        for j, o in enumerate(a["opex"]):
            v = fixed_year[j][y] * m / 12.0 if o["kind"] == "fixed" else revenue * o["pct"][y] / 100.0
            opex_lines.append(v)
            if o["creditors"]:
                creditor_opex += v
        opex = sum(opex_lines)
        ebitda = revenue - cogs - opex
        capex = a["capex"][y] * m / 12.0
        nbv_open = gross - accdep
        dep = min(nbv_open + capex, dep_rate * (gross + capex) * m / 12.0)
        ebit = ebitda - dep

        # --- loans (interest on opening balance plus any drawdown at the start of the year)
        loan_rows, int_loans, draw_total, repay_total = [], 0.0, 0.0, 0.0
        for k, L in enumerate(a["loans"]):
            open_k = loan_close[k]
            draw = L["drawdown_amount"] if (p["is_year_start"] and p["year_no"] == L["drawdown_year"]) else 0.0
            interest = L["rate"] / 100.0 * (open_k + draw) * m / 12.0
            principal0 = L["opening_balance"] + L["drawdown_amount"]
            first_repay = (L["drawdown_year"] if L["drawdown_amount"] > 0 and L["opening_balance"] == 0 else 1) + L["grace_years"]
            if L["repay_type"] == "bullet":
                repay = (open_k + draw) if (p["year_no"] == L["maturity_year"] and p["is_year_end"]) else 0.0
            else:
                inst = principal0 / L["repay_years"]
                repay = 0.0 if p["year_no"] < first_repay else min(open_k + draw, inst * m / 12.0)
            close_k = open_k + draw - repay
            loan_rows.append({"open": open_k, "draw": draw, "repay": repay, "close": close_k, "interest": interest})
            loan_close[k] = close_k
            int_loans += interest
            draw_total += draw
            repay_total += repay

        cash_open = cash
        int_income = max(cash_open, 0.0) * a["deposit_rate"] / 100.0 * m / 12.0
        int_od = max(-cash_open, 0.0) * a["overdraft_rate"] / 100.0 * m / 12.0
        fin_cost = int_loans + int_od - int_income
        pbt = ebit - fin_cost

        # --- tax with assessed-loss carry forward
        loss_used = min(loss_bf, max(pbt, 0.0))
        loss_added = max(-pbt, 0.0)
        loss_bf = loss_bf - loss_used + loss_added
        tax = (max(pbt, 0.0) - loss_used) * tax_rate
        pat = pbt - tax
        dividends = a["dividend_payout"][y] / 100.0 * max(pat, 0.0)
        equity_issue = a["equity_issue"][y] if p["is_year_start"] else 0.0

        # --- working capital (run-rate)
        ann = 12.0 / m
        inv_new = cogs * ann * a["inv_days"][y] / 365.0
        rec_new = revenue * ann * a["rec_days"][y] / 365.0
        pay_new = (cogs + creditor_opex) * ann * a["pay_days"][y] / 365.0
        d_inv, d_rec, d_pay = -(inv_new - inventory), -(rec_new - receivables), pay_new - payables
        inventory, receivables, payables = inv_new, rec_new, pay_new

        # --- cash flow
        cash_ops = ebitda + d_inv + d_rec + d_pay
        interest_paid = int_loans + int_od
        interest_received = int_income
        cfo = cash_ops - interest_paid + interest_received - tax
        cfi = -capex
        cff = draw_total - repay_total + equity_issue - dividends
        net_cash = cfo + cfi + cff
        cash = cash_open + net_cash

        # --- balance sheet roll
        gross += capex
        accdep += dep
        share_capital += equity_issue
        retained += pat - dividends
        borrowings = sum(loan_close)
        ppe_nbv = gross - accdep
        total_assets = ppe_nbv + inventory + receivables + op["other_assets"] + max(cash, 0.0)
        total_liabilities = payables + op["other_liabilities"] + borrowings + max(-cash, 0.0)
        total_equity = share_capital + retained

        r.update({
            "streams": streams, "revenue": revenue, "cogs": cogs, "gross_profit": revenue - cogs,
            "opex_lines": opex_lines, "opex": opex, "creditor_opex": creditor_opex, "ebitda": ebitda,
            "capex": capex, "dep": dep, "ebit": ebit, "int_income": int_income, "int_loans": int_loans, "int_od": int_od,
            "fin_cost": fin_cost, "pbt": pbt, "tax": tax, "pat": pat, "dividends": dividends, "equity_issue": equity_issue,
            "loss_used": loss_used, "loss_added": loss_added, "loss_close": loss_bf,
            "d_inventory": d_inv, "d_receivables": d_rec, "d_payables": d_pay, "cash_ops": cash_ops,
            "interest_paid": interest_paid, "interest_received": interest_received, "tax_paid": tax,
            "cfo": cfo, "cfi": cfi, "loan_draw": draw_total, "loan_repay": repay_total, "cff": cff, "net_cash": net_cash,
            "cash_open": cash_open, "cash": cash, "overdraft": max(-cash, 0.0), "cash_positive": max(cash, 0.0),
            "inventory": inventory, "receivables": receivables, "payables": payables,
            "other_assets": op["other_assets"], "other_liabilities": op["other_liabilities"],
            "ppe_gross": gross, "ppe_accdep": accdep, "ppe_nbv": ppe_nbv, "borrowings": borrowings,
            "loans": loan_rows, "share_capital": share_capital, "retained": retained,
            "total_assets": total_assets, "total_liabilities": total_liabilities, "total_equity": total_equity,
            "check": total_assets - total_liabilities - total_equity,
        })
        rows.append(r)

    annual = aggregate_annual(a, rows)
    warnings = []
    bad = max((abs(r["check"]) for r in rows), default=0.0)
    cf_gap = max((abs(rows[i]["cash"] - (rows[i - 1]["cash"] if i else a["opening"]["cash"]) - rows[i]["net_cash"]) for i in range(len(rows))), default=0.0)
    checks = {"balance_ok": bad < _TOL, "max_imbalance": bad, "cash_ok": cf_gap < _TOL, "cash_gap": cf_gap}
    if min((r["cash"] for r in rows), default=0.0) < -_TOL:
        first = next(r for r in rows if r["cash"] < -_TOL)
        warnings.append(f"Cash goes negative from {first['label']}: the model shows an overdraft of {abs(first['cash']):,.0f}. "
                        "Add funding (loan, equity) or slow the spending.")
    if opening_reserves(a) < -_TOL:
        warnings.append("Opening liabilities exceed opening assets plus share capital, so opening reserves are negative "
                        f"({opening_reserves(a):,.0f}). Check the opening balance sheet.")
    return {"a": a, "periods": rows, "annual": annual, "checks": checks, "warnings": warnings,
            "opening_reserves": opening_reserves(a)}


def aggregate_annual(a, rows):
    out = []
    for y in range(1, a["years"] + 1):
        rs = [r for r in rows if r["year_no"] == y]
        last, first = rs[-1], rs[0]
        d = {"year_no": y, "label": year_label(a, y) if y > 1 else year_label(a, 1)}
        for k in FLOW_KEYS:
            d[k] = sum(r[k] for r in rs)
        for k in BALANCE_KEYS:
            d[k] = last[k]
        d["gross_profit"] = d["revenue"] - d["cogs"]
        d["streams"] = [sum(r["streams"][i] for r in rs) for i in range(len(first["streams"]))]
        d["opex_lines"] = [sum(r["opex_lines"][i] for r in rs) for i in range(len(first["opex_lines"]))]
        d["cash_open"] = first["cash_open"]
        d["loans"] = [{"open": first["loans"][k]["open"], "draw": sum(r["loans"][k]["draw"] for r in rs),
                       "repay": sum(r["loans"][k]["repay"] for r in rs), "close": last["loans"][k]["close"],
                       "interest": sum(r["loans"][k]["interest"] for r in rs)} for k in range(len(first["loans"]))]
        # the opening balances this year was measured against
        prev = out[-1] if out else None
        d["open_inventory"] = prev["inventory"] if prev else a["opening"]["inventory"]
        d["open_receivables"] = prev["receivables"] if prev else a["opening"]["receivables"]
        d["open_payables"] = prev["payables"] if prev else a["opening"]["payables"]
        out.append(d)
    return out


# --------------------------------------------------------------------------- analysis on top of a run

def _div(x, y):
    return x / y if y else None


def ratios(result):
    """Key ratios per forecast year."""
    a, ann = result["a"], result["annual"]
    out = []
    for i, d in enumerate(ann):
        nxt_repay = ann[i + 1]["loan_repay"] if i + 1 < len(ann) else min(d["borrowings"], d["loan_repay"])
        current_portion = min(d["borrowings"], nxt_repay)
        cur_assets = d["inventory"] + d["receivables"] + d["other_assets"] + d["cash_positive"]
        cur_liab = d["payables"] + d["other_liabilities"] + d["overdraft"] + current_portion
        debt = d["borrowings"] + d["overdraft"]
        net_debt = debt - d["cash_positive"]
        capital = d["total_equity"] + d["borrowings"]
        service = d["interest_paid"] + d["loan_repay"]
        out.append({
            "label": d["label"],
            "current_ratio": _div(cur_assets, cur_liab),
            "quick_ratio": _div(d["receivables"] + d["cash_positive"], cur_liab),
            "gross_margin": _div(d["gross_profit"], d["revenue"]),
            "ebitda_margin": _div(d["ebitda"], d["revenue"]),
            "ebit_margin": _div(d["ebit"], d["revenue"]),
            "net_margin": _div(d["pat"], d["revenue"]),
            "roe": _div(d["pat"], d["total_equity"]),
            "roce": _div(d["ebit"], capital),
            "debt_equity": _div(debt, d["total_equity"]),
            "net_debt_ebitda": _div(net_debt, d["ebitda"]) if d["ebitda"] > 0 else None,
            "interest_cover": _div(d["ebit"], d["int_loans"] + d["int_od"]),
            "dscr": _div(d["ebitda"] - d["tax"], service),
            "receivable_days": _div(d["receivables"] * 365.0, d["revenue"]),
            "inventory_days": _div(d["inventory"] * 365.0, d["cogs"]),
            "payable_days": _div(d["payables"] * 365.0, d["cogs"] + d["creditor_opex"]),
            "asset_turnover": _div(d["revenue"], d["total_assets"]),
            "current_assets": cur_assets, "current_liabilities": cur_liab, "net_debt": net_debt,
            "debt_service": service,
        })
        r = out[-1]
        r["cash_cycle"] = None if None in (r["receivable_days"], r["inventory_days"], r["payable_days"]) else \
            r["receivable_days"] + r["inventory_days"] - r["payable_days"]
    return out


COVENANT_DEFS = [
    ("min_dscr", "Debt service cover (EBITDA less tax / interest + principal)", "dscr", "min", "x"),
    ("min_interest_cover", "Interest cover (EBIT / interest)", "interest_cover", "min", "x"),
    ("max_debt_equity", "Gearing (debt / equity)", "debt_equity", "max", "x"),
    ("min_current_ratio", "Current ratio", "current_ratio", "min", "x"),
    ("max_net_debt_ebitda", "Net debt / EBITDA", "net_debt_ebitda", "max", "x"),
]


def covenant_tests(result, ratio_rows=None):
    """Per covenant, per year: value, limit, pass / breach. A covenant with no limit set is skipped.
    A ratio that cannot be measured (e.g. no debt service, so DSCR is undefined) passes."""
    ratio_rows = ratio_rows or ratios(result)
    cov = result["a"]["covenants"]
    tests = []
    for key, label, rkey, kind, _ in COVENANT_DEFS:
        limit = cov.get(key)
        if limit is None:
            continue
        cells = []
        for r in ratio_rows:
            v = r[rkey]
            if v is None:
                ok = True
            else:
                ok = v >= limit if kind == "min" else v <= limit
                if rkey == "debt_equity" and v < 0:
                    ok = False  # negative equity
            cells.append({"label": r["label"], "value": v, "ok": ok})
        tests.append({"key": key, "label": label, "kind": kind, "limit": limit, "cells": cells,
                      "breaches": sum(1 for c in cells if not c["ok"])})
    return tests


def break_even(result):
    """Operating break-even by year: variable = cost of sales + %-of-revenue expenses; fixed = fixed expenses +
    depreciation + net finance cost."""
    a = result["a"]
    out = []
    for d in result["annual"]:
        var_opex = sum(d["opex_lines"][j] for j, o in enumerate(a["opex"]) if o["kind"] == "pct")
        fixed_opex = sum(d["opex_lines"][j] for j, o in enumerate(a["opex"]) if o["kind"] == "fixed")
        variable = d["cogs"] + var_opex
        fixed = fixed_opex + d["dep"] + d["fin_cost"]
        cm = d["revenue"] - variable
        cm_ratio = _div(cm, d["revenue"])
        be = _div(fixed, cm_ratio) if cm_ratio and cm_ratio > 0 else None
        out.append({"label": d["label"], "revenue": d["revenue"], "variable": variable, "fixed": fixed,
                    "contribution": cm, "cm_ratio": cm_ratio, "break_even_revenue": be,
                    "margin_of_safety": _div(d["revenue"] - be, d["revenue"]) if be is not None else None})
    return out


def free_cash_flows(result):
    """Unlevered free cash flow per year: EBIT x (1 - tax) + depreciation - capex - increase in working capital."""
    t = result["a"]["tax_rate"] / 100.0
    out = []
    for d in result["annual"]:
        tax_on_ebit = max(d["ebit"], 0.0) * t
        nwc = d["d_inventory"] + d["d_receivables"] + d["d_payables"]
        out.append({"label": d["label"], "year_no": d["year_no"], "ebit": d["ebit"], "tax_on_ebit": tax_on_ebit,
                    "nopat": d["ebit"] - tax_on_ebit, "dep": d["dep"], "capex": d["capex"], "nwc": nwc,
                    "fcff": d["ebit"] - tax_on_ebit + d["dep"] - d["capex"] + nwc, "ebitda": d["ebitda"]})
    return out


KEY_METRICS = [
    ("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pat", "Net profit"), ("cfo", "Operating cash flow"),
    ("cash", "Closing cash"), ("borrowings", "Borrowings"), ("total_equity", "Equity"),
]


def scenario_summary(result):
    """Headline figures used on the scenario comparison page."""
    ann = result["annual"]
    rt = ratios(result)
    min_cash = min(r["cash"] for r in result["periods"])
    dscr = [r["dscr"] for r in rt if r["dscr"] is not None]
    last = ann[-1]
    return {
        "revenue_y1": ann[0]["revenue"], "revenue_last": last["revenue"],
        "ebitda_y1": ann[0]["ebitda"], "ebitda_last": last["ebitda"],
        "pat_total": sum(d["pat"] for d in ann), "pat_last": last["pat"],
        "cash_last": last["cash"], "cash_min": min_cash,
        "borrowings_last": last["borrowings"], "equity_last": last["total_equity"],
        "dscr_min": min(dscr) if dscr else None,
        "balanced": result["checks"]["balance_ok"],
    }


def sensitivity_grid(a, x_field, x_values, y_field, y_values, metric, shocks=None):
    """Two-way what-if table: run the model for every (x, y) pair of scenario adjustments and read one headline
    metric ('cash_last', 'pat_total', 'ebitda_last', 'cash_min', ...)."""
    rows = []
    for yv in y_values:
        line = []
        for xv in x_values:
            s = dict(shocks or {})
            s[x_field] = xv
            s[y_field] = yv
            res = run(apply_shocks(a, s))
            line.append(scenario_summary(res).get(metric))
        rows.append(line)
    return rows


# --------------------------------------------------------------------------- loans

def loan_schedule(principal, annual_rate, months, repay_type="annuity", grace_months=0, start=None, balloon_pct=0.0):
    """Standalone monthly amortisation schedule (the Loan calculator).
    repay_type: annuity (equal instalments), straight (equal principal), bullet (interest only, principal at end).
    Interest is charged monthly at annual_rate / 12 on the opening balance."""
    principal, months = max(0.0, f(principal)), max(1, int(f(months, 12)))
    grace = max(0, min(int(f(grace_months)), months - 1))
    r = f(annual_rate) / 100.0 / 12.0
    balloon = principal * max(0.0, min(f(balloon_pct), 100.0)) / 100.0
    start = parse_start_month(start) if start else None
    rows, bal = [], principal
    amort_months = months - grace
    pay = 0.0
    if repay_type == "annuity":
        # level payment that leaves `balloon` outstanding at the end of the term
        if r > 0:
            pay = (principal - balloon / ((1 + r) ** amort_months)) * r / (1 - (1 + r) ** (-amort_months))
        else:
            pay = (principal - balloon) / amort_months
    for m in range(1, months + 1):
        interest = bal * r
        if m <= grace:
            principal_pay = 0.0
        elif repay_type == "bullet":
            principal_pay = bal if m == months else 0.0
        elif repay_type == "straight":
            principal_pay = (principal - balloon) / amort_months if m < months else bal
        else:
            principal_pay = min(bal, pay - interest) if m < months else bal
        if m == months:
            principal_pay = bal
        closing = bal - principal_pay
        rows.append({"n": m, "date": add_months(start, m - 1) if start else None, "opening": bal, "interest": interest,
                     "principal": principal_pay, "payment": interest + principal_pay, "closing": closing})
        bal = closing
    return {"rows": rows, "total_interest": sum(r_["interest"] for r_ in rows),
            "total_paid": sum(r_["payment"] for r_ in rows), "instalment": pay if repay_type == "annuity" else None}


def loan_schedule_annual(rows):
    """Roll a monthly schedule up into loan years."""
    out = []
    for i in range(0, len(rows), 12):
        chunk = rows[i:i + 12]
        out.append({"year": i // 12 + 1, "interest": sum(r["interest"] for r in chunk), "principal": sum(r["principal"] for r in chunk),
                    "payment": sum(r["payment"] for r in chunk), "closing": chunk[-1]["closing"]})
    return out
