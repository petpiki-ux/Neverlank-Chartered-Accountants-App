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
from datetime import date, timedelta

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


# Checked in order for suggest_fs_category() below - most specific / least
# ambiguous phrases first, so e.g. "Bank Overdraft" is suggested as a
# short-term borrowing rather than cash, and "Bank Charges" as a finance
# cost rather than cash, before the generic "bank" keyword under cash is
# ever reached. Each entry is (category code, [keyword phrases]) - the
# phrases are checked as substrings of the lowercased account name.
_CATEGORY_SUGGESTION_RULES = [
    ("short_term_borrowings", ["bank overdraft", "overdraft", "short term loan", "short-term loan", "loan payable", "bank loan", "borrowings"]),
    ("long_term_borrowings", ["long term loan", "long-term loan", "mortgage", "debenture", "term loan"]),
    ("current_tax_payable", ["tax payable", "paye payable", "vat payable", "income tax payable", "withholding tax payable"]),
    ("trade_payables", ["accounts payable", "trade payable", "creditors", "accrued expense", "accrual", "payable"]),
    ("finance_costs", ["bank charges", "bank fees", "interest expense", "interest paid", "finance cost", "loan interest"]),

    ("share_capital", ["share capital", "ordinary shares", "issued capital", "stated capital"]),
    ("share_premium", ["share premium"]),
    ("retained_earnings", ["retained earnings", "retained income", "accumulated profit", "accumulated loss"]),
    ("other_reserves", ["revaluation reserve", "general reserve", "capital reserve", "reserve"]),

    ("trade_receivables", ["accounts receivable", "trade receivable", "debtors", "receivable"]),
    ("other_current_assets", ["prepaid", "prepayment", "staff loan", "employee loan", "vat receivable", "vat control", "deposit paid", "advance to"]),
    ("inventories", ["inventory", "inventories", "stock on hand", "finished goods", "raw materials", "work in progress", "merchandise"]),
    ("cash", ["vault", "petty cash", "cash on hand", "cash in hand", "cash float", "till float", "money market", "current account", "call account", "bank account", "bank balance", "bank -", "bank"]),

    ("ppe", ["property, plant", "property plant", "motor vehicle", "furniture and fittings", "furniture & fittings", "plant and machinery", "office equipment", "computer equipment", "land and buildings", "buildings", "fixed asset", "equipment"]),
    ("intangible_assets", ["goodwill", "intangible", "software licence", "software license", "patent", "trademark"]),
    ("investment_property", ["investment property"]),
    ("long_term_investments", ["long term investment", "long-term investment", "investment in subsidiary", "investment in associate"]),
    ("deferred_tax_asset", ["deferred tax asset"]),
    ("deferred_tax_liability", ["deferred tax liability"]),

    ("cost_of_sales", ["cost of sales", "cost of goods sold", "cogs", "purchases"]),
    ("revenue", ["revenue", "sales", "turnover", "fees earned", "service income"]),
    ("other_income", ["other income", "interest received", "interest income", "sundry income", "rental income", "gain on disposal"]),
    ("distribution_costs", ["distribution cost", "selling expense", "marketing expense", "advertising"]),
    ("income_tax_expense", ["income tax expense", "tax expense", "corporate tax"]),
    ("depreciation_amortisation", ["depreciation", "amortisation", "amortization"]),
    ("admin_expenses", [
        "salaries", "wages", "rent expense", "rent paid", "electricity", "water and", "telephone",
        "internet", "stationery", "insurance", "repairs and maintenance", "audit fees",
        "professional fees", "legal fees", "subscriptions", "printing", "cleaning", "security",
        "fuel", "travel", "staff welfare", "training", "administrative expense", "general expense",
        "sundry expense", "postage", "licence fees", "license fees",
    ]),

    ("dividends_paid", ["dividend"]),
]


def suggest_fs_category(account_name):
    """Best-effort keyword suggestion for an unmapped account's IAS 1
    category, from its name alone - a starting point for the Account
    Mapping setup screen to pre-fill, for the accountant to review and
    confirm (or override) rather than picking every account from a blank
    dropdown. Never applied on its own - only ever written once a human
    explicitly confirms it (see engagements.confirm_suggested_tb_mappings).

    Matches the lowercased account name against _CATEGORY_SUGGESTION_RULES
    in order, so more specific phrases (e.g. "bank overdraft") are checked
    before more generic ones (e.g. "bank") that would otherwise shadow
    them. Returns a category code, or None if nothing matched - left for
    a manual pick rather than guessing at something with no signal."""
    name = normalize_account_name(account_name)
    if not name:
        return None
    for code, keywords in _CATEGORY_SUGGESTION_RULES:
        for kw in keywords:
            if kw in name:
                return code
    return None


# General Small and Medium Enterprises Act [Chapter 24:12] size thresholds
# (Fourth Schedule) - staff headcount, maximum annual turnover, and maximum
# gross assets excluding real estate, checked in this order (an entity
# qualifies for the first/smallest band whose every threshold it meets; if
# it exceeds even Medium's, it's Large). The Act's actual schedule varies
# these numbers slightly by sector; this is the general table that applies
# across most major sectors (Manufacturing, Agriculture, Arts, Services,
# etc.) - a deliberately practical simplification, not a substitute for
# checking the sector-specific schedule on a borderline case. Where the
# general table gives a range for staff headcount (e.g. "6 to 30/40" for
# Small), the higher figure is used here so a company isn't understated
# into a smaller band than its sector might actually allow.
SME_ACT_SIZE_THRESHOLDS = [
    ("micro", 5, 30_000, 30_000),
    ("small", 40, 500_000, 500_000),
    ("medium", 100, 1_000_000, 1_000_000),
]


def classify_sme_size(staff_headcount, annual_turnover, gross_assets):
    """Suggests a Small and Medium Enterprises Act size band from the three
    figures a preparer enters on the Finalisation tab's Company
    classification card - see SME_ACT_SIZE_THRESHOLDS. Returns None if any
    of the three figures is missing (nothing to compute from yet, leaving
    the band unclassified rather than guessing), otherwise "micro",
    "small", "medium", or "large" (exceeds every band's thresholds).
    A recommendation only - the preparer's own selection in the Finalisation
    tab's size band dropdown always has the final say."""
    if staff_headcount is None or annual_turnover is None or gross_assets is None:
        return None
    for code, max_staff, max_turnover, max_assets in SME_ACT_SIZE_THRESHOLDS:
        if staff_headcount <= max_staff and annual_turnover <= max_turnover and gross_assets <= max_assets:
            return code
    return "large"


def summarise_tb_by_category(lines):
    """Groups trial balance lines by their IAS 1 category for the Trial
    Balance tab's "Summarised" Face of Trial Balance view - one row per
    category actually used (Unmapped first, since that's what needs
    attention, then everything else in FS_CATEGORIES order), each with its
    account count and the RAW current/prior debit & credit totals (not
    netted - this is a presentation of the trial balance itself, not the
    financial statements built from it, so both sides of every account
    stay visible and the summarised and detailed totals always agree).
    Categories with no lines are left out entirely."""
    label_by_code = {c[0]: c[1] for c in FS_CATEGORIES}
    UNMAPPED = "__unmapped__"
    buckets = {}

    def _bucket(code, label):
        if code not in buckets:
            buckets[code] = {
                "code": code, "label": label, "count": 0,
                "current_debit": 0.0, "current_credit": 0.0,
                "prior_debit": 0.0, "prior_credit": 0.0,
            }
        return buckets[code]

    for line in lines:
        code = line.fs_category
        b = _bucket(code, label_by_code[code]) if code in label_by_code else _bucket(UNMAPPED, "Unmapped")
        b["count"] += 1
        b["current_debit"] += line.current_debit or 0.0
        b["current_credit"] += line.current_credit or 0.0
        b["prior_debit"] += line.prior_debit or 0.0
        b["prior_credit"] += line.prior_credit or 0.0

    ordered_codes = ([UNMAPPED] if UNMAPPED in buckets else []) + [c[0] for c in FS_CATEGORIES if c[0] in buckets]
    return [buckets[code] for code in ordered_codes]


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


def _row(label, current, prior=None, bold=False, memo=False, category=None):
    r = {"label": label, "current": current, "bold": bold, "memo": memo}
    if prior is not None:
        r["prior"] = prior
    if category:
        # Which IAS 1 category this line came from - lets build_all_statements()
        # attach the right Notes to the Financial Statements number onto this
        # row afterwards (see _attach_note_numbers below). Left unset on a
        # subtotal/total row (e.g. "Gross profit") - those aren't accounts,
        # so they never get their own note. Usually a single category code;
        # a Cash Flow Statement line that nets together more than one SFP
        # category (e.g. "Proceeds from shares issued" = share capital +
        # share premium) instead passes a list, and picks up every matching
        # note number (see _attach_note_numbers).
        r["category"] = category
    return r


def _is_nil(value, tol=0.005):
    return abs(value or 0.0) < tol


def _filter_nil_rows(rows):
    """Drops a line item whose current AND prior amounts are both nil (an
    account that never had a balance in either year) - per the rule that
    the Financial Statements shouldn't clutter the face with a zero line.
    Never drops a bold subtotal/total row or a memo line: those are
    structural to the statement's layout, not individual accounts, so IAS 1
    requires them regardless of whether every line above nets to zero."""
    return [r for r in rows if r.get("bold") or r.get("memo") or not (_is_nil(r.get("current")) and _is_nil(r.get("prior", 0.0)))]


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

    rows = _filter_nil_rows([
        _row("Revenue", v("revenue", "current"), v("revenue", "prior"), category="revenue"),
        _row("Cost of sales", -v("cost_of_sales", "current"), -v("cost_of_sales", "prior"), category="cost_of_sales"),
        _row("Gross profit", gross_profit_cur, gross_profit_pri, bold=True),
        _row("Other income", v("other_income", "current"), v("other_income", "prior"), category="other_income"),
        _row("Distribution costs", -v("distribution_costs", "current"), -v("distribution_costs", "prior"), category="distribution_costs"),
        _row("Administrative expenses", -admin_cur, -admin_pri, category="admin_expenses"),
        _row("Other expenses", -v("other_expenses", "current"), -v("other_expenses", "prior"), category="other_expenses"),
        _row("Operating profit", operating_profit_cur, operating_profit_pri, bold=True),
        _row("Finance costs", -v("finance_costs", "current"), -v("finance_costs", "prior"), category="finance_costs"),
        _row("Profit before tax", pbt_cur, pbt_pri, bold=True),
        _row("Income tax expense", -v("income_tax_expense", "current"), -v("income_tax_expense", "prior"), category="income_tax_expense"),
        _row("Profit for the year", profit_cur, profit_pri, bold=True),
        _row("Other comprehensive income, net of tax", v("oci_items", "current"), v("oci_items", "prior"), category="oci_items"),
        _row("Total comprehensive income for the year", tci_cur, tci_pri, bold=True),
    ])
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

    # Retained earnings (the profit/loss roll-forward) is never suppressed;
    # a share capital/premium/reserves row with no opening, movement or
    # closing balance in either year is dropped, same nil-line rule as the
    # other statements.
    rows = [
        r for r in rows
        if r["label"] == "Retained earnings" or not (_is_nil(r["opening"]) and _is_nil(r["movement"]) and _is_nil(r["closing"]))
    ]

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
        # Subtotals are always summed over the FULL codes list, before any
        # nil-line filtering - a hidden nil line contributes 0 either way,
        # so this doesn't change the subtotal, it just keeps it correct
        # regardless of which individual lines end up on screen.
        rows = _filter_nil_rows([_row(category_label(c), t[c]["current"], t[c]["prior"], category=c) for c in codes])
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
    equity_rows = _filter_nil_rows([
        _row("Share capital", t["share_capital"]["current"], t["share_capital"]["prior"], category="share_capital"),
        _row("Share premium", t["share_premium"]["current"], t["share_premium"]["prior"], category="share_premium"),
        _row("Other reserves", t["other_reserves"]["current"], t["other_reserves"]["prior"], category="other_reserves"),
    ]) + [
        # Retained earnings is never suppressed even if nil in both years -
        # it's the profit/loss roll-forward every entity has, not a
        # discretionary account balance, and IAS 1 requires it regardless.
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


def build_cash_flow(totals, pl, method="indirect"):
    """`method` ("indirect", the default, or "direct" - see
    models.CASH_FLOW_METHODS) controls only how the OPERATING activities
    section is presented, per IAS 7.18: the indirect method reconciles
    profit before tax to operating cash flow through non-cash and working
    capital adjustments; the direct method instead shows the gross cash
    receipts from customers and cash payments to suppliers/employees
    directly. Investing and financing activities - and every subtotal from
    "Cash generated from operations" down to the closing cash balance - are
    IDENTICAL either way: IAS 7 requires both methods to arrive at exactly
    the same net cash from operating activities, and the direct-method
    figures below are deliberately built (see the algebra in the comments)
    to net to that same "Cash generated from operations" figure rather than
    being a separate, potentially-inconsistent calculation."""
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

    if method == "direct":
        # Cash received from customers = revenue, adjusted for the same
        # movement in trade receivables the indirect method uses.
        receipts_customers = t["revenue"]["current"] - move("trade_receivables")
        # Other income is taken as received in cash as-is (no separate
        # receivable category to adjust it against at this level of detail).
        other_income_received = t["other_income"]["current"]
        # Everything else that feeds "cash generated from operations" -
        # cost of sales, distribution/admin/other expenses (including the
        # non-cash depreciation & amortisation charge, consistent with how
        # the indirect method above also nets it into operating rather than
        # adding it back - see the note on investing activities), plus every
        # remaining working-capital and non-cash movement the indirect
        # method uses - is bundled into one "paid to suppliers and
        # employees" figure. This is deliberately the plug that makes
        # receipts_customers + other_income_received + paid_suppliers
        # algebraically equal cash_from_ops above, term for term - the two
        # methods cannot disagree on "Cash generated from operations".
        paid_suppliers = (
            -(t["cost_of_sales"]["current"] + t["distribution_costs"]["current"]
              + t["admin_expenses"]["current"] + t["depreciation_amortisation"]["current"]
              + t["other_expenses"]["current"])
            - move("inventories") - move("other_current_assets")
            + move("trade_payables") + move("short_term_provisions") + move("other_current_liabilities")
            + prov_move + dtl_move - dta_move
        )
        operating_rows = [
            _row("Cash received from customers", receipts_customers, category="revenue"),
            _row("Other income received", other_income_received, category="other_income"),
            _row("Cash paid to suppliers and employees", paid_suppliers),
            _row("Cash generated from operations", receipts_customers + other_income_received + paid_suppliers, bold=True),
            _row("Finance costs paid", finance_costs_paid),
            _row("Income tax paid", tax_paid),
        ]
    else:
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
    investing_rows = [_row(f"Net movement in {category_label(c).lower()}", -move(c), category=c) for c in investing_codes]
    net_investing = sum(r["current"] for r in investing_rows)

    shares_move = move("share_capital") + move("share_premium")
    borrowings_move = move("long_term_borrowings") + move("short_term_borrowings")
    dividends_paid = -t["dividends_paid"]["current"]
    financing_rows = [
        _row("Proceeds from shares issued", shares_move, category=["share_capital", "share_premium"]),
        _row("Proceeds from/(repayment of) borrowings", borrowings_move, category=["long_term_borrowings", "short_term_borrowings"]),
        _row("Dividends paid", dividends_paid, category="dividends_paid"),
    ]
    net_financing = shares_move + borrowings_move + dividends_paid

    net_movement = net_operating + net_investing + net_financing
    cash_open = t["cash"]["prior"]
    cash_close_computed = cash_open + net_movement
    cash_close_actual = t["cash"]["current"]

    return {
        "method": method,
        # operating_rows deliberately isn't nil-filtered: it's a fixed
        # reconciliation format (adjustments, subtotals, working capital
        # movements, or - under the direct method - a fixed set of
        # receipts/payments lines), not a list of trial balance accounts, so
        # every line stays for the reconciliation to read correctly start to
        # finish.
        "operating_rows": operating_rows, "net_operating": net_operating,
        "investing_rows": _filter_nil_rows(investing_rows), "net_investing": net_investing,
        "financing_rows": _filter_nil_rows(financing_rows), "net_financing": net_financing,
        "net_movement": net_movement,
        "cash_open": cash_open, "cash_close_computed": cash_close_computed,
        "cash_close_actual": cash_close_actual,
        "variance": cash_close_actual - cash_close_computed,
    }


def apply_adjustments(totals, adjustments):
    """Layers audit adjustments (journal entries) on top of the preliminary
    trial balance's computed totals, to produce the FINAL/ADJUSTED totals the
    Financial Statements are built from. Returns a NEW dict in the same
    shape as compute_totals()'s output - `totals` itself is left untouched,
    so the caller can still use the unadjusted (preliminary) totals
    elsewhere (e.g. Analytical Review, Substantive Procedures).

    Only the CURRENT year figures are ever adjusted - audit adjustments
    relate to the year under audit, never to the prior year's comparative
    column, which stays exactly as originally trial-balanced.

    `adjustments` is an iterable of objects with a `.lines` relationship of
    objects carrying `.fs_category`, `.debit`, `.credit` (see
    models.AuditAdjustment / AuditAdjustmentLine). Lines mapped to a
    category with no normal balance (or not present in `totals`, e.g. an
    unmapped/blank category) are skipped rather than raising."""
    adjusted = {code: dict(vals) for code, vals in totals.items()}
    for adj in adjustments:
        for line in adj.lines:
            cat = CATEGORY_BY_CODE.get(line.fs_category)
            if not cat or cat["normal"] is None or cat["code"] not in adjusted:
                continue
            debit = line.debit or 0.0
            credit = line.credit or 0.0
            amount = (debit - credit) if cat["normal"] == "debit" else (credit - debit)
            adjusted[cat["code"]]["current"] += amount
    return adjusted


# ---------------------------------------------------------------------------
# Notes to the Financial Statements (Finalisation tab) - for engagements on
# a "full_ifrs" or "ifrs_for_smes" reporting_framework (see
# models.REPORTING_FRAMEWORKS). "other"/local-GAAP engagements keep the
# original plain statements + free-text notes box, unchanged.
#
# The two frameworks share the exact same note numbering, breakdown-note
# and accounting-policy engine below - the app's trial balance only ever
# carries one net current/prior balance per account per IAS 1 category
# (never a full movement schedule), which is inherently SME-scale detail
# regardless of which of the two frameworks is chosen, so there's nothing
# to meaningfully build differently between them at that level. The one
# real difference is the statement-of-compliance wording in Note 2 (see
# REPORTING_FRAMEWORK_COMPLIANCE_TEXT) - "Full IFRS" cites IFRS Accounting
# Standards as issued by the IASB, "IFRS for SMEs" cites the IFRS for
# Small and Medium-sized Entities Standard specifically.
# ---------------------------------------------------------------------------

REPORTING_FRAMEWORK_COMPLIANCE_TEXT = {
    "full_ifrs": (
        "The financial statements have been prepared in accordance with International Financial "
        "Reporting Standards (IFRS Accounting Standards) as issued by the International Accounting "
        "Standards Board (IASB), and comply with the requirements of the Companies Act applicable to "
        "companies reporting under that framework."
    ),
    "ifrs_for_smes": (
        "The financial statements have been prepared in accordance with the International Financial "
        "Reporting Standard for Small and Medium-sized Entities (IFRS for SMEs), as issued by the "
        "International Accounting Standards Board (IASB)."
    ),
}
DEFAULT_BASIS_OF_PREPARATION_TAIL = (
    " The financial statements are prepared on the historical cost basis, except where stated "
    "otherwise, and are presented in [functional/presentation currency]. The financial statements "
    "are prepared on a going concern basis - the directors have no reason to believe the entity will "
    "not continue in operational existence for the foreseeable future."
)

# Seeded the first time the Finalisation tab's closing notes are shown for
# an engagement (see engagements.py) - plain, editable placeholder text the
# preparer fills in or clears, exactly like DEFAULT_WORKPAPER_NARRATIVE_BODIES
# elsewhere in this app. Not stored until the preparer saves the form, so
# nothing is written to the database just by viewing the tab.
DEFAULT_CLOSING_NOTE_TEXT = {
    "related_party": (
        "No related party transactions requiring disclosure were identified during the year, other "
        "than [describe any key management personnel compensation, and any balances or transactions "
        "with directors, shareholders, or other related entities]."
    ),
    "commitments": (
        "There were no material capital commitments or contingent liabilities at the reporting date, "
        "other than [describe any guarantees, legal claims, or capital expenditure contracted for but "
        "not yet incurred]."
    ),
    "subsequent_events": (
        "No material events occurred between the reporting date and the date these financial "
        "statements were authorised for issue, other than [describe any subsequent events requiring "
        "adjustment to, or disclosure in, these financial statements]."
    ),
}


def general_information_note(client, engagement):
    """Note 1 (General information) - composed from data already on file
    (the client's name, industry and this engagement's period end) rather
    than typed in fresh each time. The entity's jurisdiction of
    incorporation is left as a bracketed placeholder for the preparer to
    fill in, since the app doesn't currently record one."""
    sentences = [f"{client.name} (\"the entity\") is a company incorporated in [jurisdiction of incorporation]."]
    if client.industry:
        sentences.append(f"The entity's principal activity is {client.industry.lower()}.")
    if engagement.period_end:
        sentences.append(f"These financial statements are for the year ended {engagement.period_end.strftime('%d %B %Y')}.")
    return " ".join(sentences)


# Seeded the first time the Finalisation tab's Directors' Statement is
# shown for an engagement - plain, editable placeholder text, exactly like
# DEFAULT_CLOSING_NOTE_TEXT above. Based on the standard IAS 1/Companies
# Act management-responsibility wording that already underlies
# DEFAULT_BASIS_OF_PREPARATION_TAIL's going concern sentence above, not
# retyped from scratch.
DEFAULT_DIRECTORS_STATEMENT_TEXT = (
    "The directors are responsible for the preparation and fair presentation of the accompanying "
    "financial statements, which comprise the statement of financial position, the statement of "
    "profit or loss and other comprehensive income, the statement of changes in equity, the "
    "statement of cash flows, and the notes to the financial statements, in accordance with the "
    "basis of preparation stated in the notes. This responsibility includes maintaining adequate "
    "accounting records and an effective system of internal control, selecting and applying "
    "appropriate accounting policies, and making accounting estimates that are reasonable in the "
    "circumstances. The directors have made an assessment of the entity's ability to continue as a "
    "going concern and have no reason to believe the business will not be a going concern in the "
    "year ahead. The financial statements have been approved by the board of directors and are "
    "signed on its behalf by:"
)

# The Business Intelligence and IT Engagements variant of the Directors'
# Statement above - this engagement type has no financial statements (see
# the IT & Cyber Assurance Report instead), so the standard IAS 1/Companies
# Act wording above (which refers to "the accompanying financial
# statements") doesn't fit. This is the equivalent statement of
# responsibility for the IT governance, cyber security and AML/CFT control
# environment the IT & Cyber Assurance Report reports on.
DEFAULT_DIRECTORS_STATEMENT_TEXT_IT = (
    "The directors are responsible for establishing and maintaining an effective system of information "
    "technology governance, cyber security and information security controls, and for the design, "
    "implementation and operation of internal controls over the entity's IT environment relevant to the "
    "scope of this engagement. This responsibility includes maintaining adequate change management, "
    "access control and data protection practices, ensuring compliance with applicable AML/CFT and other "
    "regulatory requirements, and promptly disclosing to us any known cyber security incidents, breaches "
    "or material control weaknesses. The directors have made an assessment of the entity's ability to "
    "continue to operate its IT environment and related controls effectively and have no reason to "
    "believe otherwise in the year ahead. This statement has been approved by the board of directors and "
    "is signed on its behalf by:"
)


def default_directors_statement_text(engagement):
    """Picks the Directors' Statement default wording for this engagement's
    type - see DEFAULT_DIRECTORS_STATEMENT_TEXT_IT above for why Business
    Intelligence and IT Engagements needs its own wording rather than the
    financial-statements-worded default."""
    if engagement and engagement.type == "Business Intelligence and IT Engagements":
        return DEFAULT_DIRECTORS_STATEMENT_TEXT_IT
    return DEFAULT_DIRECTORS_STATEMENT_TEXT


# The four report paragraphs, and their standard ISA 700 (audit)/ISRE 2400
# (review) wording by modification type - see models.AuditOpinion. Kept as
# a function rather than a static dict because the Opinion and Basis
# paragraphs both need the client name, period end and framework label
# substituted in; the two Responsibilities paragraphs don't vary by any of
# that; and the Basis paragraph for anything other than "unmodified" needs
# a bracketed placeholder for the preparer to actually describe the matter
# (never fabricated here) rather than reading as if none exists. Every
# paragraph returned is a starting draft, same as every other boilerplate
# paragraph in this app - the preparer edits it to the real, engagement-
# specific position before the report is issued.
def default_audit_opinion_paragraphs(report_basis, modification, client_name, period_end, framework_label):
    period_str = period_end.strftime("%d %B %Y") if period_end else "[period end]"
    framework_label = framework_label or "[the applicable financial reporting framework]"
    is_audit = report_basis != "review"
    noun = "audit" if is_audit else "review"
    fair_presentation = (
        f"present fairly, in all material respects, the financial position of {client_name} as at "
        f"{period_str}, and its financial performance and cash flows for the year then ended in "
        f"accordance with {framework_label}"
    )

    if modification == "unmodified":
        if is_audit:
            opinion = f"In our opinion, the accompanying financial statements {fair_presentation}."
        else:
            opinion = (
                f"Based on our review, nothing has come to our attention that causes us to believe that "
                f"the accompanying financial statements do not {fair_presentation}."
            )
        basis_matter = ""
    elif modification == "qualified":
        lead = "In our opinion, except for the effects of the matter(s)" if is_audit else "Based on our review, except for the matter(s)"
        opinion = f"{lead} described in the Basis for Qualified {'Opinion' if is_audit else 'Conclusion'} section below, the accompanying financial statements {fair_presentation}."
        basis_matter = "[Describe the matter giving rise to the qualification, and its financial effect if determinable.]"
    elif modification == "adverse":
        lead = "In our opinion, because of the significance of the matter(s)" if is_audit else "Based on our review, because of the significance of the matter(s)"
        opinion = (
            f"{lead} described in the Basis for Adverse {'Opinion' if is_audit else 'Conclusion'} section below, the accompanying financial "
            f"statements do not present fairly, in all material respects, the financial position of {client_name} "
            f"as at {period_str} and its financial performance and cash flows for the year then ended in "
            f"accordance with {framework_label}."
        )
        basis_matter = "[Describe the matter giving rise to the adverse opinion/conclusion, and its financial effect if determinable.]"
    else:  # disclaimer
        if is_audit:
            opinion = (
                f"We do not express an opinion on the accompanying financial statements of {client_name}. "
                "Because of the significance of the matter(s) described in the Basis for Disclaimer of "
                "Opinion section below, we have not been able to obtain sufficient appropriate audit "
                "evidence to provide a basis for an audit opinion on these financial statements."
            )
        else:
            opinion = (
                f"We are unable to obtain sufficient appropriate evidence to provide a basis for a review "
                f"conclusion, and we do not express a conclusion on the accompanying financial statements "
                f"of {client_name}. See the Basis for Disclaimer of Conclusion section below."
            )
        basis_matter = "[Describe the matter giving rise to the disclaimer, and why sufficient appropriate evidence could not be obtained.]"

    if is_audit:
        basis = (
            f"We conducted our {noun} in accordance with International Standards on Auditing (ISAs). Our "
            "responsibilities under those standards are further described in the Auditor's Responsibilities "
            "section below. We are independent of the entity in accordance with the International Ethics "
            "Standards Board for Accountants' International Code of Ethics for Professional Accountants "
            "(IESBA Code), and we have fulfilled our other ethical responsibilities in accordance with the "
            "IESBA Code. We believe that the audit evidence we have obtained is sufficient and appropriate "
            f"to provide a basis for our {modification} opinion."
        )
        responsibility = (
            "Our objectives are to obtain reasonable assurance about whether the financial statements as a "
            "whole are free from material misstatement, whether due to fraud or error, and to issue an "
            "auditor's report that includes our opinion. Reasonable assurance is a high level of assurance, "
            "but is not a guarantee that an audit conducted in accordance with ISAs will always detect a "
            "material misstatement when it exists. As part of an audit in accordance with ISAs, we exercise "
            "professional judgement and maintain professional scepticism throughout the audit, identify and "
            "assess the risks of material misstatement, obtain an understanding of internal control relevant "
            "to the audit, evaluate the appropriateness of accounting policies used and the reasonableness "
            "of accounting estimates, and evaluate the overall presentation, structure and content of the "
            "financial statements."
        )
    else:
        basis = (
            f"We conducted our {noun} in accordance with International Standard on Review Engagements "
            "(ISRE) 2400 (Revised), Engagements to Review Historical Financial Statements. We are "
            "independent of the entity in accordance with the IESBA Code, and we have fulfilled our other "
            "ethical responsibilities in accordance with the Code."
        )
        responsibility = (
            "A review of financial statements in accordance with ISRE 2400 (Revised) is a limited assurance "
            "engagement. We perform procedures, primarily consisting of making inquiries of management and "
            "others within the entity, as appropriate, and applying analytical procedures, and evaluate the "
            "evidence obtained. The procedures performed in a review are substantially less than those "
            "performed in an audit conducted in accordance with International Standards on Auditing. "
            "Accordingly, we do not express an audit opinion on these financial statements."
        )
    if basis_matter:
        basis = basis + " " + basis_matter

    management = (
        f"The directors are responsible for the preparation and fair presentation of the financial "
        f"statements in accordance with {framework_label}, and for such internal control as the "
        "directors determine is necessary to enable the preparation of financial statements that are "
        "free from material misstatement, whether due to fraud or error. In preparing the financial "
        "statements, the directors are responsible for assessing the entity's ability to continue as a "
        "going concern, disclosing, as applicable, matters related to going concern, and using the going "
        "concern basis of accounting unless the directors either intend to liquidate the entity or to "
        "cease operations, or have no realistic alternative but to do so."
    )

    return {
        "opinion_paragraph": opinion,
        "basis_paragraph": basis,
        "management_responsibility_paragraph": management,
        "auditor_responsibility_paragraph": responsibility,
    }


def build_income_tax_computation(profit_before_tax, lines, tax_loss_brought_forward=0.0, tax_rate_percent=None, aids_levy_percent=None):
    """Reconciles accounting profit before tax to taxable income and the
    resulting current tax charge. `lines`: plain dicts [{"item_type":
    "addback"|"deduction"|"capital_allowance", "description", "amount"},
    ...] - entirely preparer-entered (see models.IncomeTaxComputation's
    module comment on why no ZIMRA rate/allowance figure is hardcoded
    anywhere in this app). `tax_rate_percent`/`aids_levy_percent` left as
    None (rather than defaulting to a guessed figure) compute a nil tax
    charge - the caller/template should prompt for them rather than treat
    a nil result as a real answer.

    A tax loss brought forward is utilised only up to the amount of
    positive taxable income before losses; any of it left over is carried
    forward, added to by a current-year loss if taxable income before
    losses is itself negative."""
    addbacks = [l for l in lines if l.get("item_type") == "addback"]
    deductions = [l for l in lines if l.get("item_type") in ("deduction", "capital_allowance")]
    total_addbacks = sum(l.get("amount") or 0.0 for l in addbacks)
    total_deductions = sum(l.get("amount") or 0.0 for l in deductions)
    taxable_income_before_losses = (profit_before_tax or 0.0) + total_addbacks - total_deductions

    loss_available = tax_loss_brought_forward or 0.0
    loss_utilised = min(loss_available, max(taxable_income_before_losses, 0.0))
    taxable_income = max(taxable_income_before_losses - loss_utilised, 0.0)
    current_year_loss = abs(min(taxable_income_before_losses, 0.0))
    tax_loss_carried_forward = (loss_available - loss_utilised) + current_year_loss

    rate = (tax_rate_percent or 0.0) / 100.0
    levy = (aids_levy_percent or 0.0) / 100.0
    base_tax = taxable_income * rate
    aids_levy_amount = base_tax * levy
    total_tax_charge = base_tax + aids_levy_amount
    effective_rate_percent = (total_tax_charge / profit_before_tax * 100.0) if profit_before_tax else None

    return {
        "profit_before_tax": profit_before_tax or 0.0,
        "addbacks": addbacks, "deductions": deductions,
        "total_addbacks": total_addbacks, "total_deductions": total_deductions,
        "taxable_income_before_losses": taxable_income_before_losses,
        "tax_loss_brought_forward": loss_available, "loss_utilised": loss_utilised,
        "taxable_income": taxable_income, "tax_loss_carried_forward": tax_loss_carried_forward,
        "tax_rate_percent": tax_rate_percent, "aids_levy_percent": aids_levy_percent,
        "base_tax": base_tax, "aids_levy_amount": aids_levy_amount, "total_tax_charge": total_tax_charge,
        "effective_rate_percent": effective_rate_percent,
    }


def build_deferred_tax_computation(tax_rate_percent, ppe_accounting_nbv, ppe_tax_base, other_items):
    """Nets every temporary difference into a single deferred tax asset/
    liability at `tax_rate_percent`. The PPE row (if both
    `ppe_accounting_nbv` - normally the Asset Register's own closing NBV,
    see build_ppe_movement_schedule() - and `ppe_tax_base` are given) is
    kept separate from `other_items` (plain dicts: [{"description",
    "accounting_amount", "tax_base_amount"}, ...], every other temporary
    difference the preparer has entered by hand) purely so the caller can
    label the PPE row distinctly; the maths is identical either way.

    A positive (accounting amount > tax base) difference is a taxable
    temporary difference - net effect a deferred tax LIABILITY (the classic
    case: accelerated capital allowances have reduced the tax base below
    the accounting carrying amount). A negative difference is a deductible
    temporary difference - net effect a deferred tax ASSET."""
    rate = (tax_rate_percent or 0.0) / 100.0
    rows = []
    if ppe_accounting_nbv is not None and ppe_tax_base is not None:
        diff = ppe_accounting_nbv - ppe_tax_base
        rows.append({
            "description": "Property, plant and equipment", "accounting_amount": ppe_accounting_nbv,
            "tax_base_amount": ppe_tax_base, "temporary_difference": diff, "deferred_tax": diff * rate,
        })
    for item in other_items:
        acc = item.get("accounting_amount") or 0.0
        base = item.get("tax_base_amount") or 0.0
        diff = acc - base
        rows.append({
            "description": item.get("description", ""), "accounting_amount": acc,
            "tax_base_amount": base, "temporary_difference": diff, "deferred_tax": diff * rate,
        })
    total_deferred_tax = sum(r["deferred_tax"] for r in rows)
    classification = "liability" if total_deferred_tax > 0.01 else ("asset" if total_deferred_tax < -0.01 else "nil")
    return {"rows": rows, "total_deferred_tax": total_deferred_tax, "classification": classification}


def _infer_period_start(period_end):
    """Best-effort start of the 12-month reporting period ending on
    period_end. The app doesn't record a separate period-start date on
    Engagement (only period_end), so this assumes a standard 12-month
    period - the ordinary case - purely to decide, in
    build_ppe_movement_schedule() below, whether an Asset Register entry's
    date_acquired falls inside the current year (an addition) or before it
    (an opening balance). A genuinely short or long period should have its
    Asset Register entries reviewed with that in mind - this is a
    disclosed approximation, not a stored fact about the engagement."""
    if not period_end:
        return None
    year = period_end.year - 1
    month, day = period_end.month, period_end.day
    while True:
        try:
            return date(year, month, day) + timedelta(days=1)
        except ValueError:
            # e.g. period_end of 29 Feb in a leap year, prior year isn't one
            day -= 1


def build_ppe_movement_schedule(asset_classes, assets, period_end):
    """Builds the Property, Plant and Equipment movement schedule (IAS
    16.73(e): a reconciliation of the carrying amount at the beginning and
    end of the period) from the Asset Register, grouped by Depreciation
    policy asset class - both plain lists of dicts (see PPEAssetClass/
    PPEAsset in models.py; the caller converts the ORM rows), kept this way
    so the maths can be unit tested without a database, exactly like every
    other function in this module.

    asset_classes: [{"id", "name"}, ...] (id may be None for "Unclassified")
    assets: [{"asset_class_id", "cost", "opening_accumulated_depreciation",
              "current_year_depreciation", "disposal_cost",
              "disposal_accumulated_depreciation", "date_acquired"}, ...]

    Whether an asset's cost is an opening balance or a current-year
    addition is decided by date_acquired against _infer_period_start()
    above; an asset with no date_acquired is treated as an opening balance
    (the conservative assumption - nothing marks it as new this year).

    Returns None if `assets` is empty, so the caller can fall back to the
    plain trial-balance-account note instead. Otherwise returns
    {"by_class": [...], "total": {...}}, each row holding opening_cost,
    additions, disposals_cost, closing_cost, opening_acc_dep, charge,
    disposals_acc_dep, closing_acc_dep, opening_nbv, closing_nbv - built so
    that, for every row, opening_nbv + additions - disposals (cost less
    accumulated depreciation eliminated) - charge always equals
    closing_nbv exactly, term for term."""
    if not assets:
        return None
    period_start = _infer_period_start(period_end)

    def _blank():
        return {
            "opening_cost": 0.0, "additions": 0.0, "disposals_cost": 0.0, "closing_cost": 0.0,
            "opening_acc_dep": 0.0, "charge": 0.0, "disposals_acc_dep": 0.0, "closing_acc_dep": 0.0,
            "opening_nbv": 0.0, "closing_nbv": 0.0,
        }

    def _accumulate(row, a):
        cost = a.get("cost") or 0.0
        disposal_cost = a.get("disposal_cost") or 0.0
        charge = a.get("current_year_depreciation") or 0.0
        disposal_acc_dep = a.get("disposal_accumulated_depreciation") or 0.0
        date_acquired = a.get("date_acquired")
        is_addition = bool(
            date_acquired and period_start and period_start <= date_acquired <= (period_end or date_acquired)
        )
        opening_cost = 0.0 if is_addition else cost
        addition = cost if is_addition else 0.0
        # An asset added this year can't have brought-forward accumulated
        # depreciation, whatever the field holds - zeroed here so a stray
        # figure can't distort the opening NBV.
        opening_acc_dep = 0.0 if is_addition else (a.get("opening_accumulated_depreciation") or 0.0)
        closing_cost = cost - disposal_cost
        closing_acc_dep = opening_acc_dep + charge - disposal_acc_dep
        row["opening_cost"] += opening_cost
        row["additions"] += addition
        row["disposals_cost"] += disposal_cost
        row["closing_cost"] += closing_cost
        row["opening_acc_dep"] += opening_acc_dep
        row["charge"] += charge
        row["disposals_acc_dep"] += disposal_acc_dep
        row["closing_acc_dep"] += closing_acc_dep
        row["opening_nbv"] += opening_cost - opening_acc_dep
        row["closing_nbv"] += closing_cost - closing_acc_dep

    class_order = {c.get("id"): i for i, c in enumerate(asset_classes)}
    class_name = {c.get("id"): c.get("name") for c in asset_classes}

    rows_by_class = {}
    total = _blank()
    for a in assets:
        cid = a.get("asset_class_id")
        rows_by_class.setdefault(cid, _blank())
        _accumulate(rows_by_class[cid], a)
        _accumulate(total, a)

    by_class = [
        {"class_id": cid, "class_name": class_name.get(cid, "Unclassified") or "Unclassified", **row}
        for cid, row in rows_by_class.items()
    ]
    by_class.sort(key=lambda r: (class_order.get(r["class_id"], len(class_order)), r["class_name"]))

    return {"by_class": by_class, "total": total}


def ppe_accounting_policy_text(asset_classes):
    """Builds the Property, plant and equipment accounting policy note from
    the Depreciation policy working paper (models.PPEAssetClass, converted
    to plain dicts by the caller: [{"name", "depreciation_method",
    "rate_percent", "useful_life_years"}, ...]) instead of the generic
    boilerplate in NOTE_DEFINITIONS, once at least one asset class has been
    defined. Returns None if `asset_classes` is empty, so the caller falls
    back to the standard boilerplate untouched."""
    if not asset_classes:
        return None
    intro = (
        "Property, plant and equipment are stated at cost less accumulated depreciation and any "
        "accumulated impairment losses. Depreciation is charged so as to write down the cost of each "
        "class of asset to its estimated residual value over its estimated useful life, on the "
        "following bases:"
    )
    bases = []
    for c in asset_classes:
        method = c.get("depreciation_method") or "Straight-line"
        details = []
        if c.get("rate_percent") is not None:
            details.append(f"{c['rate_percent']:g}% per annum")
        if c.get("useful_life_years") is not None:
            details.append(f"{c['useful_life_years']:g} years")
        detail_str = " (" + ", ".join(details) + ")" if details else ""
        bases.append(f"{c.get('name', 'Unclassified')} - {method.lower()}{detail_str}")
    return intro + " " + "; ".join(bases) + "."


# Checked in the order line items appear on the face of the primary
# statements (Statement of Financial Position: non-current assets, current
# assets, equity, non-current liabilities, current liabilities; then the
# Statement of Profit or Loss) - this is also the order breakdown notes are
# numbered in, so "Note 6" against a line on the face and "Note 6" heading
# a note always refer to the same thing. Each entry is
# (category codes, note title, accounting policy paragraph or None) - more
# than one code in a note (only "Administrative expenses" needs this) is
# for a note that mirrors a single combined face line rather than two.
# `policy` is standard, generic IFRS-consistent wording for a category at
# this level of detail - the preparer should tailor anything client-specific
# (e.g. an inventory costing method, a revaluation vs. cost model election)
# before filing, exactly as with any other boilerplate in this app.
NOTE_DEFINITIONS = [
    (["ppe"], "Property, plant and equipment", (
        "Property, plant and equipment are stated at cost less accumulated depreciation and any "
        "accumulated impairment losses. Depreciation is charged on a straight-line basis over the "
        "estimated useful life of each asset so as to write down its cost to its estimated residual "
        "value. Useful lives and residual values are reviewed, and adjusted if appropriate, at each "
        "reporting date.")),
    (["intangible_assets"], "Intangible assets", (
        "Intangible assets acquired separately are measured on initial recognition at cost and are "
        "subsequently carried at cost less accumulated amortisation and accumulated impairment losses. "
        "Intangible assets with finite useful lives are amortised on a straight-line basis over their "
        "useful economic lives.")),
    (["investment_property"], "Investment property", (
        "Investment property is property held to earn rental income and/or for capital appreciation, "
        "rather than for use in the production or supply of goods or services, or for administrative "
        "purposes, or for sale in the ordinary course of business.")),
    (["long_term_investments"], "Long-term investments", (
        "Long-term investments are non-current financial assets that the entity does not intend to "
        "realise within twelve months of the reporting date, and are carried at cost less any "
        "accumulated impairment losses unless a more appropriate measurement basis is stated.")),
    (["deferred_tax_asset"], "Deferred tax", (
        "Deferred tax is recognised on temporary differences between the carrying amounts of assets "
        "and liabilities for financial reporting purposes and the amounts used for taxation purposes, "
        "and on unused tax losses and credits. A deferred tax asset is recognised only to the extent "
        "it is probable that future taxable profit will be available against which it can be utilised.")),
    (["other_noncurrent_assets"], "Other non-current assets", None),
    (["inventories"], "Inventories", (
        "Inventories are stated at the lower of cost and net realisable value. Cost includes "
        "expenditure incurred in acquiring the inventories and bringing them to their existing "
        "location and condition, and is determined on a [FIFO/weighted average] basis.")),
    (["trade_receivables"], "Trade and other receivables", (
        "Trade and other receivables are recognised initially at fair value and subsequently measured "
        "at amortised cost, less any allowance for expected credit losses.")),
    (["other_current_assets"], "Other current assets", None),
    (["cash"], "Cash and cash equivalents", (
        "Cash and cash equivalents comprise cash on hand, deposits held at call with banks, and other "
        "short-term, highly liquid investments with original maturities of three months or less that "
        "are subject to an insignificant risk of changes in value.")),
    (["share_capital"], "Share capital", None),
    (["share_premium"], "Share premium", None),
    (["other_reserves"], "Other reserves", None),
    (["long_term_borrowings"], "Long-term borrowings", (
        "Borrowings are recognised initially at fair value, net of transaction costs incurred, and "
        "subsequently measured at amortised cost using the effective interest method.")),
    (["deferred_tax_liability"], "Deferred tax liabilities", None),
    (["long_term_provisions"], "Provisions", (
        "Provisions are recognised when the entity has a present legal or constructive obligation as "
        "a result of a past event, it is probable that an outflow of resources will be required to "
        "settle the obligation, and the amount can be reliably estimated.")),
    (["other_noncurrent_liabilities"], "Other non-current liabilities", None),
    (["trade_payables"], "Trade and other payables", (
        "Trade and other payables are obligations to pay for goods or services that have been acquired "
        "in the ordinary course of business. They are recognised initially at fair value and "
        "subsequently measured at amortised cost.")),
    (["short_term_borrowings"], "Short-term borrowings", None),
    (["current_tax_payable"], "Current tax liabilities", (
        "Current tax is the expected tax payable on the taxable income for the year, using tax rates "
        "enacted or substantively enacted at the reporting date, and any adjustment to tax payable in "
        "respect of previous years.")),
    (["short_term_provisions"], "Provisions", None),
    (["other_current_liabilities"], "Other current liabilities", None),
    (["revenue"], "Revenue", (
        "Revenue is recognised when control of the promised goods or services is transferred to the "
        "customer, at an amount that reflects the consideration the entity expects to be entitled to "
        "in exchange for those goods or services.")),
    (["cost_of_sales"], "Cost of sales", None),
    (["other_income"], "Other income", None),
    (["distribution_costs"], "Distribution costs", None),
    (["admin_expenses", "depreciation_amortisation"], "Administrative expenses", None),
    (["other_expenses"], "Other expenses", None),
    (["finance_costs"], "Finance costs", (
        "Finance costs comprise interest expense on borrowings and lease liabilities, and are "
        "recognised in profit or loss using the effective interest method.")),
    (["income_tax_expense"], "Income tax expense", (
        "Income tax expense comprises current and deferred tax. It is recognised in profit or loss "
        "except to the extent that it relates to items recognised directly in equity or in other "
        "comprehensive income.")),
    (["dividends_paid"], "Dividends", None),
]

def _signed_category_value(code, debit, credit):
    # Notes always show the natural, normal-balance-positive magnitude of an
    # expense or income item (e.g. Cost of sales as a positive 180,000),
    # matching how the illustrative IFRS/IFRS-for-SMEs templates present
    # note breakdowns - it's the face of the Statement of Profit or Loss,
    # not the note, that then negates/deducts expense categories.
    normal = CATEGORY_BY_CODE[code]["normal"]
    return (debit or 0.0) - (credit or 0.0) if normal == "debit" else (credit or 0.0) - (debit or 0.0)


def build_notes(lines, totals, ppe_movement=None, ppe_policy_text=None):
    """Builds the numbered Notes to the Financial Statements: one breakdown
    note per IAS 1 category (or small group of categories - see
    NOTE_DEFINITIONS) with a non-nil current or prior balance, each listing
    the individual trial balance accounts making up that total, in the
    same order categories appear on the face of the statements. A category
    with a nil balance in both years gets no note at all - matches the
    statements' own nil-line suppression, so a hidden face line never
    leaves a dangling, empty note behind.

    Numbering starts at 4, after the three fixed introductory notes (1
    General information, 2 Basis of preparation, 3 Significant accounting
    policies - composed by the caller, not here).

    `ppe_movement` (see build_ppe_movement_schedule() above), when given,
    replaces the "Property, plant and equipment" note's flat account list
    with the movement schedule instead - the note's own total then comes
    from the Asset Register's closing/opening NBV rather than the trial
    balance category total, and a `variance` key is added if the two
    disagree by more than a cent, so a register that hasn't been kept in
    step with the trial balance is flagged for follow-up rather than
    silently presented as if it ties out. This still only affects a note
    that would otherwise be built - if the "ppe" category itself is nil in
    both years and there's no register data either, no PPE note appears at
    all, exactly as before. `ppe_policy_text` (see
    ppe_accounting_policy_text() above), when given, replaces that note's
    accounting policy paragraph too.

    Returns (notes, note_number_by_category): `notes` is the ordered list
    of note dicts ready to render (each with `number`, `title`, `policy`,
    `accounts` - one row per underlying trial balance line - and `total`);
    `note_number_by_category` maps every category code that got a note to
    that note's number, for build_all_statements() to attach onto the
    matching statement rows so the face and the note cross-reference each
    other, exactly like the illustrative templates this was modelled on."""
    lines_by_category = {}
    for line in lines:
        lines_by_category.setdefault(line.fs_category, []).append(line)

    notes = []
    note_number_by_category = {}
    next_number = 4

    for codes, title, policy in NOTE_DEFINITIONS:
        is_ppe_note = codes == ["ppe"]
        has_register = bool(is_ppe_note and ppe_movement and ppe_movement.get("by_class"))

        cur = sum(totals[c]["current"] for c in codes)
        pri = sum(totals[c]["prior"] for c in codes)
        if _is_nil(cur) and _is_nil(pri) and not has_register:
            continue  # nothing to disclose - matches the statements' own nil-line suppression

        account_rows = []
        for code in codes:
            for l in lines_by_category.get(code, []):
                current = _signed_category_value(code, l.current_debit, l.current_credit)
                prior = _signed_category_value(code, l.prior_debit, l.prior_credit)
                if _is_nil(current) and _is_nil(prior):
                    continue
                account_rows.append({
                    "account_code": l.account_code, "account_name": l.account_name,
                    "current": current, "prior": prior,
                })

        number = next_number
        next_number += 1
        for code in codes:
            note_number_by_category[code] = number
        note = {
            "number": number, "title": title, "policy": policy,
            "accounts": account_rows, "total": {"current": cur, "prior": pri},
        }

        if has_register:
            reg_total = ppe_movement["total"]
            note["accounts"] = []
            note["movement"] = ppe_movement["by_class"]
            note["movement_total"] = reg_total
            note["total"] = {"current": reg_total["closing_nbv"], "prior": reg_total["opening_nbv"]}
            if ppe_policy_text:
                note["policy"] = ppe_policy_text
            # Always shown at the foot of the note - links the register's
            # own closing/opening NBV back to the trial balance's PPE
            # category total, on both years, whether or not they actually
            # agree. A non-nil difference is the whole point of doing this:
            # it's the figure that becomes the basis for a proposed audit
            # adjustment (either to the trial balance, or to correct the
            # Asset Register), never silently absorbed either way.
            diff_current = cur - reg_total["closing_nbv"]
            diff_prior = pri - reg_total["opening_nbv"]
            ties_out = abs(diff_current) <= 0.01 and abs(diff_prior) <= 0.01
            note["reconciliation"] = {
                "tb_current": cur, "tb_prior": pri,
                "register_current": reg_total["closing_nbv"], "register_prior": reg_total["opening_nbv"],
                "diff_current": diff_current, "diff_prior": diff_prior, "ties_out": ties_out,
            }
            if not ties_out:
                # Kept alongside `reconciliation` (not instead of it) purely
                # for backward compatibility with anything that only checks
                # for a genuine mismatch via `note["variance"]`.
                note["variance"] = {
                    "tb_current": cur, "tb_prior": pri,
                    "register_current": reg_total["closing_nbv"], "register_prior": reg_total["opening_nbv"],
                }

        notes.append(note)

    return notes, note_number_by_category


def _attach_note_numbers(pl, sfp, cf, note_number_by_category):
    """Sets row['note'] on every statement row that has a `category` (see
    _row()) and a matching entry in note_number_by_category - every such
    row survived nil-filtering, so a category with `category` set always
    resolves to a real note number here, never a dangling reference.

    `category` is usually a single code, giving a single note number
    (e.g. "5"). A Cash Flow Statement investing/financing row can instead
    carry a list of codes when it nets together more than one SFP category
    (e.g. "Proceeds from shares issued" = share capital + share premium) -
    row['note'] then becomes a comma-separated list of every distinct note
    number involved (e.g. "8, 9"), in the order NOTE_DEFINITIONS lists them.
    The Cash Flow Statement's operating activities section is deliberately
    left out here regardless of method (indirect or direct) - its lines are
    a fixed reconciliation/receipts-and-payments presentation, not
    individual trial balance accounts, so they never carry a note number."""
    all_row_lists = (
        [pl["rows"]]
        + [sfp[k] for k in ("non_current_assets", "current_assets", "equity", "non_current_liabilities", "current_liabilities")]
        + [cf["investing_rows"], cf["financing_rows"]]
    )
    for rows in all_row_lists:
        for row in rows:
            code = row.get("category")
            if not code:
                continue
            codes = code if isinstance(code, list) else [code]
            numbers = sorted({note_number_by_category[c] for c in codes if c in note_number_by_category})
            if numbers:
                row["note"] = ", ".join(str(n) for n in numbers)


def build_all_statements(lines, adjustments=None, reporting_framework="full_ifrs", cash_flow_method="indirect",
                          ppe_movement=None, ppe_policy_text=None):
    """`adjustments`, when given (a TrialBalance's .adjustments), are applied
    on top of the preliminary trial balance's totals before the statements
    are built - see apply_adjustments() above. Leave it out (the default)
    to build statements straight from the preliminary trial balance, e.g.
    for a quick preliminary view before any adjustments are proposed.

    `reporting_framework` (see models.REPORTING_FRAMEWORKS) controls only
    whether the numbered Notes to the Financial Statements are built and
    cross-referenced onto the face (build_notes() above) - "full_ifrs" and
    "ifrs_for_smes" both get them, "other"/local-GAAP engagements get
    `notes=[]` and no note numbers on the face, leaving the plain
    statements exactly as before this feature.

    `cash_flow_method` ("indirect", the default, or "direct" - see
    models.CASH_FLOW_METHODS and build_cash_flow()) controls only how the
    Cash Flow Statement's operating activities section is presented; every
    other statement, and every Cash Flow subtotal, is unaffected.

    `ppe_movement`/`ppe_policy_text` (see build_ppe_movement_schedule() and
    ppe_accounting_policy_text() above) are passed straight through to
    build_notes() - see there for what they change. Leaving both out (the
    default) leaves the PPE note exactly as it was before this feature."""
    totals = compute_totals(lines)
    if adjustments:
        totals = apply_adjustments(totals, adjustments)
    pl = build_income_statement(totals)
    equity = build_equity_statement(totals, pl)
    sfp = build_financial_position(totals, equity)
    cf = build_cash_flow(totals, pl, method=cash_flow_method)
    if reporting_framework in ("full_ifrs", "ifrs_for_smes"):
        notes, note_number_by_category = build_notes(lines, totals, ppe_movement=ppe_movement, ppe_policy_text=ppe_policy_text)
        _attach_note_numbers(pl, sfp, cf, note_number_by_category)
    else:
        notes = []
    return {"totals": totals, "pl": pl, "equity": equity, "sfp": sfp, "cf": cf, "notes": notes}


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
