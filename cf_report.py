"""Presentation helpers for the Corporate Finance & Business Advisory module: turns a forecast run into
printable statement tables (used by the web pages and the PDF report, so both always show the same lines)."""
from cf_engine import ratios, covenant_tests, break_even, free_cash_flows, SHOCK_FIELDS


def _line(label, values, kind="line", fmt="money"):
    return {"label": label, "values": values, "kind": kind, "fmt": fmt}


def _col(rows, fn):
    return [fn(r) for r in rows]


def income_statement(a, rows):
    out = [_line("Income statement", [None] * len(rows), "head")]
    if len(a["revenue"]) > 1:
        for i, st in enumerate(a["revenue"]):
            out.append(_line(st["name"], _col(rows, lambda r, i=i: r["streams"][i]), "indent"))
    out.append(_line("Revenue", _col(rows, lambda r: r["revenue"]), "total"))
    out.append(_line("Cost of sales", _col(rows, lambda r: -r["cogs"])))
    out.append(_line("Gross profit", _col(rows, lambda r: r["gross_profit"]), "total"))
    for j, o in enumerate(a["opex"]):
        out.append(_line(o["name"], _col(rows, lambda r, j=j: -r["opex_lines"][j]), "indent"))
    out.append(_line("Total operating expenses", _col(rows, lambda r: -r["opex"])))
    out.append(_line("EBITDA", _col(rows, lambda r: r["ebitda"]), "total"))
    out.append(_line("Depreciation", _col(rows, lambda r: -r["dep"])))
    out.append(_line("Operating profit (EBIT)", _col(rows, lambda r: r["ebit"]), "total"))
    out.append(_line("Interest income", _col(rows, lambda r: r["int_income"])))
    out.append(_line("Interest on loans", _col(rows, lambda r: -r["int_loans"])))
    out.append(_line("Interest on overdraft", _col(rows, lambda r: -r["int_od"])))
    if any(abs(r.get("loan_fees", 0.0)) > 1e-9 for r in rows):
        out.append(_line("Loan establishment fees", _col(rows, lambda r: -r["loan_fees"])))
    out.append(_line("Profit before tax", _col(rows, lambda r: r["pbt"]), "total"))
    out.append(_line("Income tax", _col(rows, lambda r: -r["tax"])))
    out.append(_line("Profit after tax", _col(rows, lambda r: r["pat"]), "grand"))
    out.append(_line("Dividends", _col(rows, lambda r: -r["dividends"]), "memo"))
    return out


def balance_sheet(a, rows):
    out = [_line("Balance sheet", [None] * len(rows), "head")]
    out.append(_line("Property, plant and equipment (net)", _col(rows, lambda r: r["ppe_nbv"])))
    out.append(_line("Inventory", _col(rows, lambda r: r["inventory"])))
    out.append(_line("Trade receivables", _col(rows, lambda r: r["receivables"])))
    out.append(_line("Other assets", _col(rows, lambda r: r["other_assets"])))
    out.append(_line("Cash and bank", _col(rows, lambda r: r["cash_positive"])))
    out.append(_line("Total assets", _col(rows, lambda r: r["total_assets"]), "total"))
    out.append(_line("Share capital", _col(rows, lambda r: r["share_capital"])))
    out.append(_line("Retained earnings and reserves", _col(rows, lambda r: r["retained"])))
    out.append(_line("Total equity", _col(rows, lambda r: r["total_equity"]), "total"))
    out.append(_line("Borrowings", _col(rows, lambda r: r["borrowings"])))
    out.append(_line("Bank overdraft", _col(rows, lambda r: r["overdraft"])))
    out.append(_line("Trade payables", _col(rows, lambda r: r["payables"])))
    out.append(_line("Other liabilities", _col(rows, lambda r: r["other_liabilities"])))
    out.append(_line("Total liabilities", _col(rows, lambda r: r["total_liabilities"]), "total"))
    out.append(_line("Total equity and liabilities", _col(rows, lambda r: r["total_equity"] + r["total_liabilities"]), "grand"))
    out.append(_line("Balance check (should be nil)", _col(rows, lambda r: r["check"]), "memo"))
    return out


def cash_flow(a, rows):
    out = [_line("Cash flow statement", [None] * len(rows), "head")]
    out.append(_line("EBITDA", _col(rows, lambda r: r["ebitda"])))
    out.append(_line("Change in inventory", _col(rows, lambda r: r["d_inventory"]), "indent"))
    out.append(_line("Change in receivables", _col(rows, lambda r: r["d_receivables"]), "indent"))
    out.append(_line("Change in payables", _col(rows, lambda r: r["d_payables"]), "indent"))
    out.append(_line("Cash generated from operations", _col(rows, lambda r: r["cash_ops"]), "total"))
    out.append(_line("Interest paid", _col(rows, lambda r: -r["interest_paid"])))
    if any(abs(r.get("loan_fees", 0.0)) > 1e-9 for r in rows):
        out.append(_line("Loan establishment fees paid", _col(rows, lambda r: -r["loan_fees"])))
    out.append(_line("Interest received", _col(rows, lambda r: r["interest_received"])))
    out.append(_line("Tax paid", _col(rows, lambda r: -r["tax_paid"])))
    out.append(_line("Net cash from operating activities", _col(rows, lambda r: r["cfo"]), "total"))
    out.append(_line("Capital expenditure", _col(rows, lambda r: -r["capex"])))
    out.append(_line("Net cash from investing activities", _col(rows, lambda r: r["cfi"]), "total"))
    out.append(_line("Loan drawdowns", _col(rows, lambda r: r["loan_draw"])))
    out.append(_line("Loan repayments", _col(rows, lambda r: -r["loan_repay"])))
    out.append(_line("Share capital issued", _col(rows, lambda r: r["equity_issue"])))
    out.append(_line("Dividends paid", _col(rows, lambda r: -r["dividends"])))
    out.append(_line("Net cash from financing activities", _col(rows, lambda r: r["cff"]), "total"))
    out.append(_line("Net change in cash", _col(rows, lambda r: r["net_cash"]), "total"))
    out.append(_line("Opening cash", _col(rows, lambda r: r["cash_open"])))
    out.append(_line("Closing cash", _col(rows, lambda r: r["cash"]), "grand"))
    return out


def cashflow_forecast(result, later_years=True):
    """The cash flow FORECAST in the firm's layout: year 1 by month then a Year 1 total (and later years), listing every cash
    movement under its heading - loan finance, revenue (with the monthly distribution), cost of sales, operating expenditure by line,
    working capital, finance charges (establishment fees, loan principal, loan interest), tax, capex, shareholders - to a closing
    cash balance. It is built independently from the cash flow STATEMENT and then reconciled to it, line by line.
    Returns {'columns', 'lines', 'max_diff', 'ok'}."""
    a = result["a"]
    per = result["periods"][:12]
    ann = result["annual"] if later_years else result["annual"][:1]
    src = list(per) + list(ann)
    columns = [r["label"] for r in per] + ["Year 1"] + [d["label"] for d in ann[1:]]
    lines = []

    def vals(fn):
        return [fn(r) for r in src]

    def row(label, fn, kind="line", fmt="money"):
        v = vals(fn)
        lines.append(_line(label, v, kind, fmt))
        return v

    def blank():
        lines.append(_line("", [None] * len(src), "gap"))

    def any_nonzero(fn):
        return any(abs(fn(r)) > 0.005 for r in src)

    loans = a["loans"]
    sums = {}   # section -> values, to build the net movement from the rows themselves

    opening = row("Opening cash balance", lambda r: r["cash_open"], "total")
    blank()
    draws = [any_nonzero(lambda r, k=k: r["loans"][k]["draw"]) for k in range(len(loans))]
    if any(draws):
        sums["loan"] = row("Loan finance", lambda r: r["loan_draw"], "total")
        for k, L in enumerate(loans):
            if draws[k]:
                row(L["name"], lambda r, k=k: r["loans"][k]["draw"], "indent")
        blank()
    share = [r["share"] for r in per] + [1.0] + [None] * (len(ann) - 1)
    lines.append(_line("Revenue distribution during year 1", share, "memo", "pct"))
    sums["rev"] = row("Revenue", lambda r: r["revenue"], "total")
    for i, st in enumerate(a["revenue"]):
        row(st["name"], lambda r, i=i: r["streams"][i], "indent")
    blank()
    sums["cogs"] = row("Cost of sales", lambda r: -r["cogs"], "total")
    blank()
    sums["opex"] = row("Operating expenditure", lambda r: -r["opex"], "total")
    for j, o in enumerate(a["opex"]):
        row(o["name"], lambda r, j=j: -r["opex_lines"][j], "indent")
    blank()
    sums["wc"] = row("Working capital movements", lambda r: r["d_inventory"] + r["d_receivables"] + r["d_payables"], "total")
    row("Change in inventory", lambda r: r["d_inventory"], "indent")
    row("Change in receivables", lambda r: r["d_receivables"], "indent")
    row("Change in payables", lambda r: r["d_payables"], "indent")
    blank()
    fin = lambda r: (-sum(x["fee"] for x in r["loans"]) - r["loan_repay"] - r["int_loans"] - r["int_od"] + r["int_income"])
    sums["fin"] = row("Finance charges", fin, "total")
    for k, L in enumerate(loans):
        fee_txt = []
        if L["fee_pct"] > 0:
            fee_txt.append(f"{L['fee_pct']:g}%")
        if L["fee_amount"] > 0:
            fee_txt.append(f"{L['fee_amount']:,.0f} fixed")
        if any_nonzero(lambda r, k=k: r["loans"][k]["fee"]):
            row(f"Establishment fee @ {' + '.join(fee_txt) or '0%'} - {L['name']}", lambda r, k=k: -r["loans"][k]["fee"], "indent")
    for k, L in enumerate(loans):
        if any_nonzero(lambda r, k=k: r["loans"][k]["repay"]):
            row(f"Loan repayment - principal - {L['name']}", lambda r, k=k: -r["loans"][k]["repay"], "indent")
    for k, L in enumerate(loans):
        if any_nonzero(lambda r, k=k: r["loans"][k]["interest"]):
            row(f"Loan repayment - interest @ {L['rate']:g}% - {L['name']}", lambda r, k=k: -r["loans"][k]["interest"], "indent")
    if any_nonzero(lambda r: r["int_od"]):
        row("Overdraft interest", lambda r: -r["int_od"], "indent")
    if any_nonzero(lambda r: r["int_income"]):
        row("Interest received", lambda r: r["int_income"], "indent")
    blank()
    sums["tax"] = row("Taxation", lambda r: -r["tax"], "total")
    blank()
    sums["capex"] = row("Capital expenditure", lambda r: -r["capex"], "total")
    if any_nonzero(lambda r: r["equity_issue"]) or any_nonzero(lambda r: r["dividends"]):
        blank()
        sums["owners"] = row("Shareholders", lambda r: r["equity_issue"] - r["dividends"], "total")
        if any_nonzero(lambda r: r["equity_issue"]):
            row("Share capital issued", lambda r: r["equity_issue"], "indent")
        if any_nonzero(lambda r: r["dividends"]):
            row("Dividends paid", lambda r: -r["dividends"], "indent")
    blank()
    net = [sum(x[i] for x in sums.values()) for i in range(len(src))]
    lines.append(_line("Net cash movement", net, "total"))
    closing = [opening[i] + net[i] for i in range(len(src))]
    lines.append(_line("Closing cash balance", closing, "grand"))
    blank()
    lines.append(_line("Reconciliation to the cash flow statement", [None] * len(src), "head"))
    row("Net cash from operating activities", lambda r: r["cfo"], "indent")
    row("Net cash from investing activities", lambda r: r["cfi"], "indent")
    row("Net cash from financing activities", lambda r: r["cff"], "indent")
    stmt_net = row("Net change in cash per the cash flow statement", lambda r: r["net_cash"], "total")
    diff_net = [net[i] - stmt_net[i] for i in range(len(src))]
    lines.append(_line("Difference: forecast less cash flow statement (nil)", diff_net, "memo"))
    stmt_close = row("Closing cash per the balance sheet (cash less overdraft)", lambda r: r["cash"], "total")
    diff_close = [closing[i] - stmt_close[i] for i in range(len(src))]
    lines.append(_line("Difference: closing cash less balance sheet (nil)", diff_close, "memo"))
    worst = max([abs(x) for x in diff_net + diff_close] or [0.0])
    return {"columns": columns, "lines": lines, "max_diff": worst, "ok": worst < 0.01}


def statement_tables(result, view="annual"):
    """{'columns': [labels], 'statements': [(title, lines)]} for the annual view or the monthly year-1 view."""
    a = result["a"]
    if view == "monthly":
        rows = result["periods"][:12]
        # year-1 total column for the flows, closing balances for the balance sheet
        total = dict(result["annual"][0])
        cols = [r["label"] for r in rows] + ["Year 1"]
        rows = list(rows) + [total]
    else:
        rows = result["annual"]
        cols = [d["label"] for d in rows]
    return {"columns": cols, "statements": [("Income statement", income_statement(a, rows)),
                                            ("Balance sheet", balance_sheet(a, rows)),
                                            ("Cash flow statement", cash_flow(a, rows))]}


RATIO_ROWS = [
    # key, label, fmt
    ("gross_margin", "Gross margin", "pct"), ("ebitda_margin", "EBITDA margin", "pct"), ("net_margin", "Net margin", "pct"),
    ("roe", "Return on equity", "pct"), ("roce", "Return on capital employed", "pct"),
    ("current_ratio", "Current ratio", "x"), ("quick_ratio", "Quick ratio", "x"),
    ("debt_equity", "Debt / equity", "x"), ("net_debt_ebitda", "Net debt / EBITDA", "x"),
    ("interest_cover", "Interest cover", "x"), ("dscr", "Debt service cover", "x"),
    ("receivable_days", "Receivable days", "days"), ("inventory_days", "Inventory days", "days"),
    ("payable_days", "Payable days", "days"), ("cash_cycle", "Cash conversion cycle", "days"),
    ("asset_turnover", "Asset turnover", "x"),
]


def ratio_table(result):
    rt = ratios(result)
    return {"columns": [r["label"] for r in rt],
            "rows": [{"label": lab, "fmt": fmt, "values": [r[key] for r in rt]} for key, lab, fmt in RATIO_ROWS]}


def fmt_value(v, fmt="money", dp=0):
    """Plain-text formatting shared by the PDF (templates have their own filters)."""
    if v is None:
        return "-"
    if fmt == "pct":
        return f"{v * 100:,.1f}%"
    if fmt == "x":
        return f"{v:,.2f}x"
    if fmt == "days":
        return f"{v:,.0f}"
    if abs(v) < 0.5 and dp == 0:
        return "-"
    s = f"{abs(v):,.{dp}f}"
    return f"({s})" if v < 0 else s


def headline(result):
    """Short facts for the overview page."""
    ann = result["annual"]
    first, last = ann[0], ann[-1]
    n = len(ann)
    cagr = None
    if n > 1 and first["revenue"] > 0 and last["revenue"] > 0:
        cagr = (last["revenue"] / first["revenue"]) ** (1.0 / (n - 1)) - 1.0
    rt = ratios(result)
    cov = covenant_tests(result, rt)
    return {
        "revenue_first": first["revenue"], "revenue_last": last["revenue"], "cagr": cagr,
        "ebitda_margin_first": first["ebitda"] / first["revenue"] if first["revenue"] else None,
        "ebitda_margin_last": last["ebitda"] / last["revenue"] if last["revenue"] else None,
        "pat_total": sum(d["pat"] for d in ann), "cash_last": last["cash"],
        "cash_min": min(r["cash"] for r in result["periods"]),
        "borrowings_last": last["borrowings"], "equity_last": last["total_equity"],
        "breaches": sum(t["breaches"] for t in cov), "covenants_tested": len(cov),
    }
