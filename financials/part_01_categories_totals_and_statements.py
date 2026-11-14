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
from decimal import Decimal, ROUND_HALF_UP

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

    ("admin_expenses", ["fuel", "motor vehicle running", "motor vehicles running", "vehicle running", "motor vehicle repair", "motor vehicles - repair",
                        "motor vehicle insurance", "vehicle licence", "vehicle license", "vehicle maintenance"]),
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
        # the firm's own heading inside the category (models.FSSubCategory), shown as indented rows under it
        sub = (getattr(line, "sub_category", None) or "").strip() if code in label_by_code else ""
        if sub:
            s = b.setdefault("subs", {}).setdefault(sub, {"label": sub, "count": 0, "current_debit": 0.0, "current_credit": 0.0, "prior_debit": 0.0, "prior_credit": 0.0})
            s["count"] += 1
            s["current_debit"] += line.current_debit or 0.0
            s["current_credit"] += line.current_credit or 0.0
            s["prior_debit"] += line.prior_debit or 0.0
            s["prior_credit"] += line.prior_credit or 0.0

    ordered_codes = ([UNMAPPED] if UNMAPPED in buckets else []) + [c[0] for c in FS_CATEGORIES if c[0] in buckets]
    out = [buckets[code] for code in ordered_codes]
    for b in out:
        b["subs"] = sorted(b.get("subs", {}).values(), key=lambda s: s["label"].lower())
    return out


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



# ---------------------------------------------------------------------------
# Whole-number presentation. The statements are built from the trial balance to the cent; for the Financial Statements pack and the
# Finalisation tab every IAS 1 line is rounded to a whole number FIRST and every subtotal is then added up from the rounded lines, so
# a reader who adds the column gets the printed total. When the rounded statement of financial position is out by a few units while
# the unrounded one balanced, the difference is a rounding difference: it is put on the largest working-capital or non-current
# balance (so it is not hidden in cash or in profit) and reported back in statements["rounding"] so the preparer can see it.
# A genuine imbalance (the unrounded statement does not balance either) is never touched.
# ---------------------------------------------------------------------------

def whole(x):
    """x rounded to a whole number, halves away from zero (an accountant's rounding, not Python's round-half-even), never -0."""
    try:
        return float(Decimal(repr(float(x or 0.0))).quantize(Decimal(1), rounding=ROUND_HALF_UP)) + 0.0
    except Exception:
        return 0.0


def round_totals(totals):
    return {code: {"current": whole(v["current"]), "prior": whole(v["prior"])} for code, v in totals.items()}


_ABSORB_ASSETS = ["trade_receivables", "inventories", "other_current_assets", "ppe", "intangible_assets", "investment_property",
                  "long_term_investments", "other_noncurrent_assets"]
_ABSORB_LIABILITIES = ["trade_payables", "other_current_liabilities", "short_term_borrowings", "short_term_provisions",
                       "long_term_borrowings", "long_term_provisions", "other_noncurrent_liabilities"]
MAX_ROUNDING_ABSORBED = 25.0


def _balance_of(totals):
    pl = build_income_statement(totals)
    equity = build_equity_statement(totals, pl)
    return build_financial_position(totals, equity)["balance_check"]


def round_totals_balanced(raw):
    """(rounded totals, [what was absorbed]). See the comment above."""
    rounded = round_totals(raw)
    raw_bc = _balance_of(raw)
    absorbed = []
    for period in ("current", "prior"):
        if abs(raw_bc[period]) > 0.01:
            continue                                   # the trial balance itself does not balance: leave it showing
        d = whole(_balance_of(rounded)[period])
        if d == 0 or abs(d) > MAX_ROUNDING_ABSORBED:
            continue
        side = [(abs(rounded[c][period]), c, +1) for c in _ABSORB_LIABILITIES if rounded.get(c) and abs(rounded[c][period]) > 0]
        side += [(abs(rounded[c][period]), c, -1) for c in _ABSORB_ASSETS if rounded.get(c) and abs(rounded[c][period]) > 0]
        if not side:
            continue
        _, code, sign = max(side, key=lambda t: t[0])
        change = sign * d                               # asset: -d; liability: +d
        rounded[code][period] += change
        absorbed.append({"period": period, "category": code, "label": category_label(code), "amount": change})
    return rounded, absorbed


_PPE_MOVEMENT_KEYS = ("opening_cost", "additions", "disposals_cost", "closing_cost", "opening_acc_dep", "charge",
                      "disposals_acc_dep", "closing_acc_dep", "opening_nbv", "closing_nbv",
                      "revaluation_cost", "revaluation_acc_dep", "revaluation")


def round_ppe_movement(mv):
    """The Asset Register's movement schedule in whole numbers, each class row adding across and the total row adding down."""
    if not mv:
        return mv

    def fix(src):
        r = {k: whole(src.get(k)) for k in _PPE_MOVEMENT_KEYS}
        r["revaluation"] = r["revaluation_cost"] + r["revaluation_acc_dep"]
        r["closing_cost"] = r["opening_cost"] + r["additions"] - r["disposals_cost"] + r["revaluation_cost"]
        r["closing_acc_dep"] = r["opening_acc_dep"] + r["charge"] - r["disposals_acc_dep"] - r["revaluation_acc_dep"]
        r["opening_nbv"] = r["opening_cost"] - r["opening_acc_dep"]
        r["closing_nbv"] = r["closing_cost"] - r["closing_acc_dep"]
        return r

    by_class = []
    for row in mv["by_class"]:
        new = dict(row)
        new.update(fix(row))
        by_class.append(new)
    total = {k: sum(r[k] for r in by_class) for k in _PPE_MOVEMENT_KEYS}
    out = {"by_class": by_class, "total": total}
    if mv.get("revaluation_note"):
        out["revaluation_note"] = mv["revaluation_note"]
    return out


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
            oci = (pl["total_comprehensive_income"]["current"] - pl["profit_for_year"]["current"]) if code == "other_reserves" else 0.0
            row = {
                "label": label, "opening": opening, "movement": closing - opening,
                "movement_desc": ("Other comprehensive income for the year (net of deferred tax)" if abs(oci) >= 0.5 else "Movement during the year"),
                "closing": closing,
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
