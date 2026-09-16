"""Trial Balance import and IAS 1 financial statement generation.

This module is deliberately self-contained (no Flask/db imports) so the
maths can be tested in isolation - it takes a list of TrialBalanceLine-like
rows in and returns plain nested dicts/lists the template can render.

CONVENTION THIS MODULE ASSUMES: the trial balance is a *working* trial
balance (the normal form used for an audit) - i.e. revenue and expense
accounts for the current year are still open, and the "Retained earnings"
account balance therefore represents the OPENING balance brought forward,
not a closing balance that already includes the current year's profit.
Closing retained earnings, and the rest of the Statement of Changes in
Equity, are computed from that opening balance plus the profit for the
year (and any dividends/movements mapped), not read directly off the TB -
see build_equity_statement() below.
"""

# Each entry: (code, label, statement, section, normal_balance, cash_flow_class)
#   statement: "SFP" | "PL" | "EQUITY" | "EXCLUDED"
#   normal_balance: "debit" | "credit" | None
#   cash_flow_class: "operating_wc" | "operating_noncash" | "operating_tax" |
#                     "financing" | "investing" | "cash" | None
FS_CATEGORIES = [
    ("ppe", "Property, plant and equipment", "SFP", "Non-current assets", "debit", "investing"),
    ("intangible_assets", "Intangible assets", "SFP", "Non-current assets", "debit", "investing"),
    ("investment_property", "Investment property", "SFP", "Non-current assets", "debit", "investing"),
    ("long_term_investments", "Long-term investments", "SFP", "Non-current assets", "debit", "investing"),
    ("deferred_tax_asset", "Deferred tax asset", "SFP", "Non-current assets", "debit", "operating_noncash"),
    ("other_noncurrent_assets", "Other non-current assets", "SFP", "Non-current assets", "debit", "investing"),

    ("inventories", "Inventories", "SFP", "Current assets", "debit", "operating_wc"),
    ("trade_receivables", "Trade and other receivables", "SFP", "Current assets", "debit", "operating_wc"),
    ("other_current_assets", "Other current assets", "SFP", "Current assets", "debit", "operating_wc"),
    ("cash", "Cash and cash equivalents", "SFP", "Current assets", "debit", "cash"),

    ("share_capital", "Share capital", "SFP", "Equity", "credit", "financing"),
    ("share_premium", "Share premium", "SFP", "Equity", "credit", "financing"),
    ("retained_earnings", "Retained earnings (opening balance b/f)", "SFP", "Equity", "credit", None),
    ("other_reserves", "Other reserves", "SFP", "Equity", "credit", None),

    ("long_term_borrowings", "Long-term borrowings", "SFP", "Non-current liabilities", "credit", "financing"),
    ("deferred_tax_liability", "Deferred tax liability", "SFP", "Non-current liabilities", "credit", "operating_noncash"),
    ("long_term_provisions", "Long-term provisions", "SFP", "Non-current liabilities", "credit", "operating_noncash"),
    ("other_noncurrent_liabilities", "Other non-current liabilities", "SFP", "Non-current liabilities", "credit", "financing"),

    ("trade_payables", "Trade and other payables", "SFP", "Current liabilities", "credit", "operating_wc"),
    ("short_term_borrowings", "Short-term borrowings", "SFP", "Current liabilities", "credit", "financing"),
    ("current_tax_payable", "Current tax payable", "SFP", "Current liabilities", "credit", "operating_tax"),
    ("short_term_provisions", "Short-term provisions", "SFP", "Current liabilities", "credit", "operating_wc"),
    ("other_current_liabilities", "Other current liabilities", "SFP", "Current liabilities", "credit", "operating_wc"),

    ("revenue", "Revenue", "PL", "Trading", "credit", None),
    ("cost_of_sales", "Cost of sales", "PL", "Trading", "debit", None),
    ("other_income", "Other income", "PL", "Operating", "credit", None),
    ("distribution_costs", "Distribution costs", "PL", "Operating", "debit", None),
    ("admin_expenses", "Administrative expenses", "PL", "Operating", "debit", None),
    ("other_expenses", "Other expenses", "PL", "Operating", "debit", None),
    ("depreciation_amortisation", "Depreciation & amortisation (already included in expenses above)", "PL", "Operating", "debit", "operating_noncash"),
    ("finance_costs", "Finance costs", "PL", "Finance", "debit", "operating_finance"),
    ("income_tax_expense", "Income tax expense", "PL", "Tax", "debit", None),
    ("oci_items", "Other comprehensive income, net of tax", "PL", "OCI", "credit", None),

    ("dividends_paid", "Dividends declared / paid during the year", "EQUITY", "Movements", "debit", "financing"),

    ("excluded", "Not applicable - exclude from financial statements", "EXCLUDED", "", None, None),
]

CATEGORY_BY_CODE = {c[0]: {
    "code": c[0], "label": c[1], "statement": c[2], "section": c[3],
    "normal": c[4], "cf": c[5],
} for c in FS_CATEGORIES}

TB_IMPORT_COLUMNS = [
    "Account Code", "Account Name",
    "Current Year Debit", "Current Year Credit",
    "Prior Year Debit", "Prior Year Credit",
]


def category_label(code):
    cat = CATEGORY_BY_CODE.get(code)
    return cat["label"] if cat else "Unmapped"


def category_choices():
    """Categories grouped for a <select><optgroup> - in FS_CATEGORIES order,
    grouped by (statement, section)."""
    groups = []
    seen = {}
    for code, label, statement, section, normal, cf in FS_CATEGORIES:
        if statement == "EXCLUDED":
            continue
        key = (statement, section)
        if key not in seen:
            seen[key] = []
            groups.append((f"{'Statement of Financial Position' if statement == 'SFP' else 'Profit or Loss' if statement == 'PL' else 'Equity movements'} - {section}" if section else ("Statement of Financial Position" if statement == "SFP" else "Profit or Loss"), seen[key]))
        seen[key].append((code, label))
    groups.append(("Other", [("excluded", "Not applicable - exclude from financial statements")]))
    return groups


def normalize_account_name(name):
    return (name or "").strip().lower()


def compute_totals(lines):
    """lines: iterable of objects/rows with .fs_category, .current_debit,
    .current_credit, .prior_debit, .prior_credit (None treated as 0).
    Returns {code: {'current': float, 'prior': float}} for every mappable
    category (i.e. excluding 'excluded')."""
    totals = {code: {"current": 0.0, "prior": 0.0} for code in CATEGORY_BY_CODE if code != "excluded"}
    for line in lines:
        cat = CATEGORY_BY_CODE.get(line.fs_category)
        if not cat or cat["normal"] is None:
            continue
        cd = line.current_debit or 0.0
        cc = line.current_credit or 0.0
        pd = line.prior_debit or 0.0
        pc = line.prior_credit or 0.0
        if cat["normal"] == "debit":
            cur, pri = cd - cc, pd - pc
        else:
            cur, pri = cc - cd, pc - pd
        totals[cat["code"]]["current"] += cur
        totals[cat["code"]]["prior"] += pri
    return totals


def _row(label, current, prior=None, bold=False, memo=False):
    r = {"label": label, "current": current, "bold": bold, "memo": memo}
    if prior is not None:
        r["prior"] = prior
    return r


def build_income_statement(totals):
    t = totals
    def v(code, period):
        return t[code][period]

    admin_cur = v("admin_expenses", "current") + v("depreciation_amortisation", "current")
    admin_pri = v("admin_expenses", "prior") + v("depreciation_amortisation", "prior")

    gross_profit_cur = v("revenue", "current") - v("cost_of_sales", "current")
    gross_profit_pri = v("revenue", "prior") - v("cost_of_sales", "prior")

    operating_profit_cur = gross_profit_cur + v("other_income", "current") - v("distribution_costs", "current") - admin_cur - v("other_expenses", "current")
    operating_profit_pri = gross_profit_pri + v("other_income", "prior") - v("distribution_costs", "prior") - admin_pri - v("other_expenses", "prior")

    pbt_cur = operating_profit_cur - v("finance_costs", "current")
    pbt_pri = operating_profit_pri - v("finance_costs", "prior")

    profit_cur = pbt_cur - v("income_tax_expense", "current")
    profit_pri = pbt_pri - v("income_tax_expense", "prior")

    tci_cur = profit_cur + v("oci_items", "current")
    tci_pri = profit_pri + v("oci_items", "prior")

    rows = [
        _row("Revenue", v("revenue", "current"), v("revenue", "prior")),
        _row("Cost of sales", -v("cost_of_sales", "current"), -v("cost_of_sales", "prior")),
        _row("Gross profit", gross_profit_cur, gross_profit_pri, bold=True),
        _row("Other income", v("other_income", "current"), v("other_income", "prior")),
        _row("Distribution costs", -v("distribution_costs", "current"), -v("distribution_costs", "prior")),
        _row("Administrative expenses", -admin_cur, -admin_pri),
        _row("Other expenses", -v("other_expenses", "current"), -v("other_expenses", "prior")),
        _row("Operating profit", operating_profit_cur, operating_profit_pri, bold=True),
        _row("Finance costs", -v("finance_costs", "current"), -v("finance_costs", "prior")),
        _row("Profit before tax", pbt_cur, pbt_pri, bold=True),
        _row("Income tax expense", -v("income_tax_expense", "current"), -v("income_tax_expense", "prior")),
        _row("Profit for the year", profit_cur, profit_pri, bold=True),
        _row("Other comprehensive income, net of tax", v("oci_items", "current"), v("oci_items", "prior")),
        _row("Total comprehensive income for the year", tci_cur, tci_pri, bold=True),
    ]
    return {
        "rows": rows,
        "profit_before_tax": {"current": pbt_cur, "prior": pbt_pri},
        "profit_for_year": {"current": profit_cur, "prior": profit_pri},
        "income_tax_expense": {"current": v("income_tax_expense", "current"), "prior": v("income_tax_expense", "prior")},
        "finance_costs": {"current": v("finance_costs", "current"), "prior": v("finance_costs", "prior")},
        "total_comprehensive_income": {"current": tci_cur, "prior": tci_pri},
    }


def build_equity_statement(totals, pl):
    """Retained earnings is the one account this app treats specially: in
    BOTH the current and prior columns, the trial balance figure is the
    OPENING balance brought forward for that column's year (the standard
    working-trial-balance convention, needed because both columns also
    carry that year's own open revenue/expense accounts for the
    comparative income statement). So the closing retained earnings shown
    on the face of the Statement of Financial Position - for both years -
    is always computed as opening + profit for the year - dividends, never
    read directly off the TB. The prior year's computed closing is then
    compared with the current year's opening as a continuity check: they
    represent the same point in time and should agree."""
    t = totals

    def roll_forward(period):
        opening = t["retained_earnings"][period]
        profit = pl["profit_for_year"][period]
        dividends = t["dividends_paid"][period]
        return opening, profit, dividends, opening + profit - dividends

    cur_opening, cur_profit, cur_dividends, cur_closing = roll_forward("current")
    pri_opening, pri_profit, pri_dividends, pri_closing = roll_forward("prior")
    opening_agreement_variance = cur_opening - pri_closing

    rows = []
    total_opening = 0.0
    total_closing = 0.0
    for code, label in (
        ("share_capital", "Share capital"),
        ("share_premium", "Share premium"),
        ("other_reserves", "Other reserves"),
        ("retained_earnings", "Retained earnings"),
    ):
        if code == "retained_earnings":
            row = {
                "label": label, "opening": cur_opening, "movement": cur_profit - cur_dividends,
                "movement_desc": "Profit for the year, less dividends", "closing": cur_closing,
                "opening_check": pri_closing, "opening_variance": opening_agreement_variance,
            }
            opening, closing = cur_opening, cur_closing
        else:
            opening = t[code]["prior"]
            closing = t[code]["current"]
            row = {
                "label": label, "opening": opening, "movement": closing - opening,
                "movement_desc": "Movement during the year", "closing": closing,
            }
        total_opening += opening
        total_closing += closing
        rows.append(row)

    return {
        "rows": rows,
        "total_opening": total_opening,
        "total_closing": total_closing,
        "retained_earnings_closing": cur_closing,
        "retained_earnings_closing_prior_year": pri_closing,
    }


def build_financial_position(totals, equity):
    t = totals
    retained_earnings_closing = equity["retained_earnings_closing"]

    def section(codes):
        rows = [_row(category_label(c), t[c]["current"], t[c]["prior"]) for c in codes]
        sub_cur = sum(t[c]["current"] for c in codes)
        sub_pri = sum(t[c]["prior"] for c in codes)
        return rows, sub_cur, sub_pri

    nca_codes = ["ppe", "intangible_assets", "investment_property", "long_term_investments", "deferred_tax_asset", "other_noncurrent_assets"]
    ca_codes = ["inventories", "trade_receivables", "other_current_assets", "cash"]
    ncl_codes = ["long_term_borrowings", "deferred_tax_liability", "long_term_provisions", "other_noncurrent_liabilities"]
    cl_codes = ["trade_payables", "short_term_borrowings", "current_tax_payable", "short_term_provisions", "other_current_liabilities"]

    nca_rows, nca_cur, nca_pri = section(nca_codes)
    ca_rows, ca_cur, ca_pri = section(ca_codes)
    total_assets_cur, total_assets_pri = nca_cur + ca_cur, nca_pri + ca_pri

    retained_earnings_closing_prior = equity["retained_earnings_closing_prior_year"]
    equity_rows = [
        _row("Share capital", t["share_capital"]["current"], t["share_capital"]["prior"]),
        _row("Share premium", t["share_premium"]["current"], t["share_premium"]["prior"]),
        _row("Other reserves", t["other_reserves"]["current"], t["other_reserves"]["prior"]),
        _row("Retained earnings", retained_earnings_closing, retained_earnings_closing_prior),
    ]
    total_equity_cur = t["share_capital"]["current"] + t["share_premium"]["current"] + t["other_reserves"]["current"] + retained_earnings_closing
    # The prior year's trial balance also carries that year's own open
    # revenue/expense accounts (for the comparative income statement), so
    # - exactly like the current year - its retained earnings closing must
    # be computed (opening + that year's profit - dividends) rather than
    # read straight off the prior TB column.
    total_equity_pri = t["share_capital"]["prior"] + t["share_premium"]["prior"] + t["other_reserves"]["prior"] + retained_earnings_closing_prior

    ncl_rows, ncl_cur, ncl_pri = section(ncl_codes)
    cl_rows, cl_cur, cl_pri = section(cl_codes)
    total_eq_liab_cur = total_equity_cur + ncl_cur + cl_cur
    total_eq_liab_pri = total_equity_pri + ncl_pri + cl_pri

    return {
        "non_current_assets": nca_rows, "nca_total": {"current": nca_cur, "prior": nca_pri},
        "current_assets": ca_rows, "ca_total": {"current": ca_cur, "prior": ca_pri},
        "total_assets": {"current": total_assets_cur, "prior": total_assets_pri},
        "equity": equity_rows, "total_equity": {"current": total_equity_cur, "prior": total_equity_pri},
        "non_current_liabilities": ncl_rows, "ncl_total": {"current": ncl_cur, "prior": ncl_pri},
        "current_liabilities": cl_rows, "cl_total": {"current": cl_cur, "prior": cl_pri},
        "total_equity_and_liabilities": {"current": total_eq_liab_cur, "prior": total_eq_liab_pri},
        "balance_check": {"current": total_assets_cur - total_eq_liab_cur, "prior": total_assets_pri - total_eq_liab_pri},
    }


def build_cash_flow(totals, pl):
    t = totals

    def move(code):
        return t[code]["current"] - t[code]["prior"]

    dep = t["depreciation_amortisation"]["current"]
    prov_move = move("long_term_provisions")
    dtl_move = move("deferred_tax_liability")
    dta_move = move("deferred_tax_asset")
    finance_costs = pl["finance_costs"]["current"]
    pbt = pl["profit_before_tax"]["current"]

    # Note: depreciation and amortisation is NOT added back here. Investing
    # activities below are approximated as the net movement in each
    # non-current asset's carrying value (additions less depreciation less
    # disposals, netted) rather than gross additions/disposals separately -
    # that net movement already absorbs the depreciation charge, so adding
    # it back here too would double-count it. It's shown as a memo line
    # only, for information/tie-back to the fixed asset register.
    memo_rows = [_row("Depreciation and amortisation for the year (memo only - see note)", dep, memo=True)]
    adjustments = [
        _row("Profit before tax", pbt),
        _row("Finance costs", finance_costs),
        _row("Movement in long-term provisions", prov_move),
        _row("Movement in deferred tax", dtl_move - dta_move),
    ]
    op_before_wc = pbt + finance_costs + prov_move + dtl_move - dta_move

    wc_rows = [
        _row("(Increase)/decrease in inventories", -move("inventories")),
        _row("(Increase)/decrease in trade and other receivables", -move("trade_receivables")),
        _row("(Increase)/decrease in other current assets", -move("other_current_assets")),
        _row("Increase/(decrease) in trade and other payables", move("trade_payables")),
        _row("Increase/(decrease) in short-term provisions", move("short_term_provisions")),
        _row("Increase/(decrease) in other current liabilities", move("other_current_liabilities")),
    ]
    cash_from_ops = op_before_wc + sum(r["current"] for r in wc_rows)

    finance_costs_paid = -finance_costs
    tax_paid = -(t["current_tax_payable"]["prior"] + pl["income_tax_expense"]["current"] - t["current_tax_payable"]["current"])
    operating_rows = (
        adjustments
        + memo_rows
        + [_row("Operating cash flow before working capital changes", op_before_wc, bold=True)]
        + wc_rows
        + [_row("Cash generated from operations", cash_from_ops, bold=True)]
        + [_row("Finance costs paid", finance_costs_paid), _row("Income tax paid", tax_paid)]
    )
    net_operating = cash_from_ops + finance_costs_paid + tax_paid

    investing_codes = ["ppe", "intangible_assets", "investment_property", "long_term_investments", "other_noncurrent_assets"]
    investing_rows = [_row(f"Net movement in {category_label(c).lower()}", -move(c)) for c in investing_codes]
    net_investing = sum(r["current"] for r in investing_rows)

    shares_move = move("share_capital") + move("share_premium")
    borrowings_move = move("long_term_borrowings") + move("short_term_borrowings")
    dividends_paid = -t["dividends_paid"]["current"]
    financing_rows = [
        _row("Proceeds from shares issued", shares_move),
        _row("Proceeds from/(repayment of) borrowings", borrowings_move),
        _row("Dividends paid", dividends_paid),
    ]
    net_financing = shares_move + borrowings_move + dividends_paid

    net_movement = net_operating + net_investing + net_financing
    cash_open = t["cash"]["prior"]
    cash_close_computed = cash_open + net_movement
    cash_close_actual = t["cash"]["current"]

    return {
        "operating_rows": operating_rows, "net_operating": net_operating,
        "investing_rows": investing_rows, "net_investing": net_investing,
        "financing_rows": financing_rows, "net_financing": net_financing,
        "net_movement": net_movement,
        "cash_open": cash_open, "cash_close_computed": cash_close_computed,
        "cash_close_actual": cash_close_actual,
        "variance": cash_close_actual - cash_close_computed,
    }


def build_all_statements(lines):
    totals = compute_totals(lines)
    pl = build_income_statement(totals)
    equity = build_equity_statement(totals, pl)
    sfp = build_financial_position(totals, equity)
    cf = build_cash_flow(totals, pl)
    return {"totals": totals, "pl": pl, "equity": equity, "sfp": sfp, "cf": cf}


def parse_tb_rows(rows):
    """rows: iterable of dicts with (case-insensitive, whitespace-tolerant)
    keys matching TB_IMPORT_COLUMNS. Returns a list of cleaned dicts:
    {account_code, account_name, current_debit, current_credit,
    prior_debit, prior_credit}. Rows with no account name are skipped.
    Raises ValueError with a human-readable message if a required header
    is missing."""
    def norm_key(k):
        return (k or "").strip().lower().replace("  ", " ")

    key_map = {
        "account code": "account_code", "account name": "account_name",
        "current year debit": "current_debit", "current year credit": "current_credit",
        "prior year debit": "prior_debit", "prior year credit": "prior_credit",
    }

    cleaned = []
    for raw_row in rows:
        row = {}
        for k, val in raw_row.items():
            mapped = key_map.get(norm_key(k))
            if mapped:
                row[mapped] = val
        name = (row.get("account_name") or "").strip()
        if not name:
            continue

        def to_float(v):
            if v in (None, ""):
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).strip().replace(",", "")
            if not s:
                return 0.0
            try:
                return float(s)
            except ValueError:
                return 0.0

        cleaned.append({
            "account_code": (row.get("account_code") or "").strip(),
            "account_name": name,
            "current_debit": to_float(row.get("current_debit")),
            "current_credit": to_float(row.get("current_credit")),
            "prior_debit": to_float(row.get("prior_debit")),
            "prior_credit": to_float(row.get("prior_credit")),
        })
    if not cleaned:
        raise ValueError(
            "No account rows found. Make sure the file has a header row with "
            "'Account Name' and at least one debit/credit column, and at "
            "least one row below it with an account name filled in."
        )
    return cleaned
