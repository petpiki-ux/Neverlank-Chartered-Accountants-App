"""financials, part 2 of 3.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file financials.py did."""

def build_cash_flow(totals, pl, method="indirect", noncash=None):
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
    # `noncash`: movements that changed the balance sheet without any cash (a revaluation of property, plant and equipment, with the
    # deferred tax on its surplus, see revaluation_noncash()): {"ppe": the revaluation's effect on PPE, "dtl": the deferred tax charged to OCI}.
    # They are taken out of the investing movement and out of the deferred tax add-back, so closing cash still agrees to the balance sheet.
    noncash = noncash or {}
    nc_ppe, nc_dtl = float(noncash.get("ppe") or 0.0), float(noncash.get("dtl") or 0.0)
    nc_pl = float(noncash.get("pl") or 0.0)        # the revaluation's gain (loss) in profit or loss: not cash, so taken back out of operating cash flow

    def move(code):
        return t[code]["current"] - t[code]["prior"]

    dep = t["depreciation_amortisation"]["current"]
    prov_move = move("long_term_provisions")
    dtl_move = move("deferred_tax_liability") - nc_dtl
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
    if abs(nc_pl) >= 0.5:
        adjustments.append(_row("Revaluation (gain)/loss on property, plant and equipment, not a cash flow", -nc_pl))
    op_before_wc = pbt + finance_costs + prov_move + dtl_move - dta_move - nc_pl

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
            + prov_move + dtl_move - dta_move - nc_pl
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
    investing_rows = [_row(f"Net movement in {category_label(c).lower()}", -(move(c) - (nc_ppe if c == "ppe" else 0.0)), category=c) for c in investing_codes]
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
        "investing_rows": _filter_nil_rows(investing_rows + (
            [_row("Non-cash: revaluation of property, plant and equipment (not a cash flow; see the property, plant and equipment note)", nc_ppe, memo=True)]
            if abs(nc_ppe) >= 0.5 else [])), "net_investing": net_investing,
        "financing_rows": _filter_nil_rows(financing_rows), "net_financing": net_financing,
        "net_movement": net_movement,
        "cash_open": cash_open, "cash_close_computed": cash_close_computed,
        "cash_close_actual": cash_close_actual,
        "variance": cash_close_actual - cash_close_computed,
    }


def adjustment_source(adj):
    """The source code of a journal made by the app ('REVAL', 'PPE-REG', 'DT-CUR' ...), else its reference, upper case."""
    return ((getattr(adj, "source_key", None) or getattr(adj, "reference", None) or "")).strip().upper()


def revaluation_noncash(adjustments):
    """What the revaluation journal (REVAL, ppe_revaluation.py) did to the balance sheet without any cash: {"ppe": its net effect on
    property, plant and equipment, "dtl": the deferred tax charged to OCI on it, "pl": the gain (loss) it took to profit or loss}. The Cash Flow Statement takes both out of its investing
    movement and its deferred tax add-back. Empty when there is no such journal."""
    ppe = dtl = pl = 0.0
    found = False
    for adj in adjustments or []:
        if adjustment_source(adj) != "REVAL" or is_prior_adjustment(adj):
            continue
        found = True
        for line in adj.lines:
            net = (line.debit or 0.0) - (line.credit or 0.0)
            if line.fs_category == "ppe":
                ppe += net
            elif line.fs_category == "oci_items" and "deferred" in (getattr(line, "account_name", "") or "").casefold():
                dtl += net
            elif line.fs_category in ("other_income", "other_expenses"):
                pl -= net                                   # a credit to income, or a debit to expense, is read as a gain / loss
    return {"ppe": ppe, "dtl": dtl, "pl": pl} if found else {}


def roll_oci_into_reserves(totals):
    """Other comprehensive income of a year is a movement in equity: the trial balance's own 'Other reserves' is what was held before it, so
    the closing reserves on the Statement of Financial Position and in the Statement of Changes in Equity are those plus the year's OCI.
    (Without this, a revaluation surplus in OCI raised total comprehensive income and the assets but not equity, and the balance sheet
    did not balance.) Returns a NEW totals dict; `oci_items` itself stays, it is what the Statement of Comprehensive Income shows."""
    oci, res = totals.get("oci_items"), totals.get("other_reserves")
    if not oci or not res or (abs(oci.get("current", 0.0)) < 1e-9 and abs(oci.get("prior", 0.0)) < 1e-9):
        return totals
    out = {c: dict(v) for c, v in totals.items()}
    for period in ("current", "prior"):
        out["other_reserves"][period] = res[period] + oci[period]
    return out


def is_prior_adjustment(adj):
    return (getattr(adj, "applies_to", None) or "current") == "prior"


def adjustment_effects(adj):
    """What one adjustment does to the trial balance totals: [(period, category code, amount in that category's own normal sign)].

    A current-year adjustment adjusts the current column only. A PRIOR-YEAR adjustment (a restatement of the comparatives, IAS 8)
    adjusts the prior column and, unless it is marked not to (`carry_to_current` False), the current year's OPENING position as well:
    its balance sheet lines are repeated in the current column (the balance is still there at the start of this year) and its profit
    and loss, OCI and dividend lines go to the current retained earnings brought forward (last year's profit is this year's opening
    reserves). So after a prior-year journal the prior closing retained earnings and the current opening still agree."""
    prior = is_prior_adjustment(adj)
    carry = prior and getattr(adj, "carry_to_current", True) is not False
    out = []
    for line in adj.lines:
        cat = CATEGORY_BY_CODE.get(line.fs_category)
        if not cat or cat["normal"] is None:
            continue
        debit = line.debit or 0.0
        credit = line.credit or 0.0
        amount = (debit - credit) if cat["normal"] == "debit" else (credit - debit)
        out.append(("prior" if prior else "current", cat["code"], amount))
        if carry:
            if cat["statement"] == "SFP":
                out.append(("current", cat["code"], amount))
            else:
                out.append(("current", "retained_earnings", credit - debit))
    return out


def adjustment_line_effects(adj):
    """The same effects as adjustment_effects(), line by line and with the adjustment line's account name:
    [(period, category code, amount in the category's normal sign, account name)]. The notes use it to put an adjustment
    on the account line it belongs to (a depreciation adjustment on the Depreciation line), never on a line of its own."""
    prior = is_prior_adjustment(adj)
    carry = prior and getattr(adj, "carry_to_current", True) is not False
    out = []
    for line in adj.lines:
        cat = CATEGORY_BY_CODE.get(line.fs_category)
        if not cat or cat["normal"] is None:
            continue
        debit = line.debit or 0.0
        credit = line.credit or 0.0
        amount = (debit - credit) if cat["normal"] == "debit" else (credit - debit)
        name = (getattr(line, "account_name", None) or "").strip() or "Adjustment"
        out.append(("prior" if prior else "current", cat["code"], amount, name))
        if carry:
            if cat["statement"] == "SFP":
                out.append(("current", cat["code"], amount, name))
            else:
                out.append(("current", "retained_earnings", credit - debit, "Retained earnings brought forward (restated)"))
    return out


def apply_adjustments(totals, adjustments):
    """Layers audit adjustments (journal entries) on top of the preliminary
    trial balance's computed totals, to produce the FINAL/ADJUSTED totals the
    Financial Statements are built from. Returns a NEW dict in the same
    shape as compute_totals()'s output - `totals` itself is left untouched,
    so the caller can still use the unadjusted (preliminary) totals
    elsewhere (e.g. Analytical Review, Substantive Procedures).

    An adjustment adjusts the CURRENT year unless it is marked as a
    PRIOR-YEAR adjustment (AuditAdjustment.applies_to == "prior"), which
    restates the comparative column and, by default, the current year's
    opening balances - see adjustment_effects().

    `adjustments` is an iterable of objects with a `.lines` relationship of
    objects carrying `.fs_category`, `.debit`, `.credit` (see
    models.AuditAdjustment / AuditAdjustmentLine). Lines mapped to a
    category with no normal balance (or not present in `totals`, e.g. an
    unmapped/blank category) are skipped rather than raising."""
    adjusted = {code: dict(vals) for code, vals in totals.items()}
    for adj in adjustments:
        for period, code, amount in adjustment_effects(adj):
            if code in adjusted:
                adjusted[code][period] += amount
    return adjusted


def prior_year_adjustments(adjustments):
    """The prior-year (restating) adjustments among `adjustments`, as [{reference, description, carry}] for the disclosure."""
    return [{"reference": a.reference or "", "description": a.description or "", "carry": getattr(a, "carry_to_current", True) is not False}
            for a in (adjustments or []) if is_prior_adjustment(a)]


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
    bankers = bankers_list(client)
    if bankers:
        sentences.append("The entity's main banker is " + bankers[0] + "." if len(bankers) == 1 else
                         "The entity's main bankers are " + ", ".join(bankers[:-1]) + " and " + bankers[-1] + ".")
    if engagement.period_end:
        sentences.append(f"These financial statements are for the year ended {engagement.period_end.strftime('%d %B %Y')}.")
    return " ".join(sentences)


def bankers_list(client):
    """The client's main bankers entered on the client page (one per line), as a clean list."""
    raw = getattr(client, "bankers", None) or ""
    return [b.strip() for b in raw.replace(";", "\n").splitlines() if b.strip()]


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



# The Accounting & Bookkeeping variant of the Directors' Statement above -
# this engagement type is NOT an audit or review (Neverlank prepares/
# compiles the figures itself, across Financial Accounting, Management
# Accounting, Cost Accounting and Tax compliance matters, rather than
# forming an opinion on figures someone else prepared - see
# models.ACCOUNTING_SERVICES/has_tax_module), so the standard wording above
# (which assumes an audit/review is being performed on management-prepared
# statements, and talks about "the directors ... make an assessment ...
# going concern") doesn't fit. Worded along the lines of an ISRS 4410
# compilation engagement's management-responsibility statement instead.
DEFAULT_DIRECTORS_STATEMENT_TEXT_ACCOUNTING = (
    "Management is responsible for the accuracy and completeness of the accounting records, source "
    "documents and other information provided to Neverlank Chartered Accountants, and for the underlying "
    "transactions reflected in the Financial Accounting, Management Accounting, Cost Accounting and Tax "
    "records and reports covered by this engagement. This responsibility includes maintaining adequate "
    "accounting records and an effective system of internal control, ensuring transactions are properly "
    "authorised and recorded, and safeguarding the entity's assets. Neverlank Chartered Accountants has "
    "prepared/compiled the attached financial, management and cost accounting information from this "
    "information without undertaking an audit or review, and accordingly does not express an audit "
    "opinion or review conclusion on it. This statement has been approved by management and is signed on "
    "its behalf by:"
)


def default_directors_statement_text(engagement):
    """Picks the Directors' Statement default wording for this engagement's
    type - see DEFAULT_DIRECTORS_STATEMENT_TEXT_IT/_ACCOUNTING above for why
    Business Intelligence and IT Engagements and Accounting & Bookkeeping
    each need their own wording rather than the audit/review-worded
    default."""
    if engagement and engagement.type == "Business Intelligence and IT Engagements":
        return DEFAULT_DIRECTORS_STATEMENT_TEXT_IT
    if engagement and engagement.type == "Accounting & Bookkeeping":
        return DEFAULT_DIRECTORS_STATEMENT_TEXT_ACCOUNTING
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


def build_deferred_tax_computation(tax_rate_percent, ppe_accounting_nbv, ppe_tax_base, other_items, ppe_rows=None):
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
    if ppe_rows:
        # property, plant and equipment class by class (carrying amount from the Asset Register, tax base from the Capital Allowance schedule)
        for pr in ppe_rows:
            diff = (pr["accounting_amount"] or 0.0) - (pr["tax_base_amount"] or 0.0)
            rows.append({"description": pr["description"], "accounting_amount": pr["accounting_amount"] or 0.0,
                         "tax_base_amount": pr["tax_base_amount"] or 0.0, "temporary_difference": diff, "deferred_tax": diff * rate})
    elif ppe_accounting_nbv is not None and ppe_tax_base is not None:
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
