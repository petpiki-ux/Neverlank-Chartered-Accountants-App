"""models, part 5 of 12.  Holds: FinancialStatements, DirectorsStatement, AuditOpinion, IncomeTaxComputation, IncomeTaxAdjustmentLine, DeferredTaxComputation, DeferredTaxItem, QPDInstalmentRate, FxRate, FxAverageRate, QPDEstimate, QPDTrialBalanceLine ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

class FinancialStatements(db.Model):
    """The Financial Statements pack for an engagement - mostly a sign-off
    and notes wrapper, since the statements themselves are computed live
    from the adjusted TrialBalance (see financials.py) rather than
    stored."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    basis_of_preparation = db.Column(db.Text)
    # Notes to the Financial Statements - free text, editable in-app (unlike
    # the figures throughout the statements themselves, which are always
    # computed live from the adjusted TrialBalance via financials.py and are
    # never independently editable/overridable here). Kept as a catch-all
    # "other matters" section alongside the structured, auto-generated
    # notes (accounting policies + numbered breakdown notes, built fresh
    # from the trial balance every time - see financials.build_notes) for
    # engagements on a Full IFRS / IFRS for SMEs reporting_framework.
    notes_to_financial_statements = db.Column(db.Text)
    # The three standard closing notes every set of financial statements
    # needs but that no trial balance account can supply on its own - free
    # text, seeded with a sensible placeholder default the first time the
    # Finalisation tab is opened (see engagements._default_closing_note_text).
    related_party_note = db.Column(db.Text)
    commitments_note = db.Column(db.Text)
    subsequent_events_note = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("financial_statements", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<FinancialStatements engagement={self.engagement_id}>"


class DirectorsStatement(db.Model):
    """The Directors' Statement (Statement of Responsibility) working paper
    - content that goes INTO the client's Financial Statements for their
    own directors to sign, not an internal audit sign-off record. Placed
    right after the Client Details/Table of Contents page and before the
    Audit Opinion/Review Report (if any) in the generated Word document,
    matching normal Zimbabwean financial statement ordering. One per
    engagement, seeded with standard boilerplate the first time the
    Finalisation tab is opened (see financials.DEFAULT_DIRECTORS_STATEMENT_TEXT)."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    statement_text = db.Column(db.Text)
    director1_name = db.Column(db.String(150))
    director1_title = db.Column(db.String(100), default="Director")
    director2_name = db.Column(db.String(150))
    director2_title = db.Column(db.String(100), default="Director")
    statement_date = db.Column(db.Date)

    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("directors_statement", uselist=False, cascade="all, delete-orphan"))
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    def __repr__(self):
        return f"<DirectorsStatement engagement={self.engagement_id}>"


# The report basis the preparer picks on the Audit Opinion tab - an Audit
# engagement gets a full ISA 700 audit opinion, an Assurance engagement
# more usually gets an ISRE 2400 review conclusion (a limited-assurance
# report, not a full opinion) - kept as an explicit choice rather than
# inferred purely from Engagement.type, since a firm occasionally performs
# the other basis on either engagement type.
AUDIT_OPINION_BASES = ["audit", "review"]
AUDIT_OPINION_BASIS_LABELS = {
    "audit": "Audit opinion (ISA 700)",
    "review": "Review / limited assurance conclusion (ISRE 2400)",
}
OPINION_MODIFICATIONS = ["unmodified", "qualified", "adverse", "disclaimer"]
OPINION_MODIFICATION_LABELS = {
    "unmodified": "Unmodified (unqualified)",
    "qualified": "Qualified",
    "adverse": "Adverse",
    "disclaimer": "Disclaimer of opinion / conclusion",
}


class AuditOpinion(db.Model):
    """The Independent Auditor's Report / Independent Reviewer's Report
    working paper - one per engagement, shown only for Audit and Assurance
    engagement types (see AUDIT_OPINION_BASES above), placed before the
    primary statements in the generated Financial Statements Word document.
    The four report paragraphs are seeded with standard ISA 700/ISRE 2400
    boilerplate for the chosen basis/modification (see
    financials.default_audit_opinion_paragraphs) and are always editable -
    the same "system drafts, preparer tailors" pattern as every other
    boilerplate paragraph in this app. Only a Partner sign-off actually
    issues the report - completed_by/reviewed_by exist for drafting/review
    before it reaches that point, the same 3-stage pattern as every other
    working paper in this app."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    report_basis = db.Column(db.String(10), default="audit")  # "audit" | "review"
    modification = db.Column(db.String(15), default="unmodified")  # see OPINION_MODIFICATIONS
    basis_for_modification = db.Column(db.Text)  # only meaningful when modification != "unmodified"
    opinion_paragraph = db.Column(db.Text)
    basis_paragraph = db.Column(db.Text)
    management_responsibility_paragraph = db.Column(db.Text)
    auditor_responsibility_paragraph = db.Column(db.Text)
    report_date = db.Column(db.Date)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("audit_opinion", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<AuditOpinion engagement={self.engagement_id}>"


# Zimbabwean corporate tax rates and capital allowance rates change with
# each year's Finance Act, and reliable current figures could not be
# sourced with confidence at the time this was built (published sources
# disagreed even on the headline corporate rate) - so unlike, say,
# NOTE_DEFINITIONS' accounting policy wording, NOTHING here hardcodes a
# statutory rate as fact. tax_rate_percent/aids_levy_percent are entirely
# preparer-entered, with no pre-filled default, and every add-back/
# deduction/capital allowance line is entered by the preparer too - the
# computation only does the arithmetic once those figures are supplied.
INCOME_TAX_ITEM_TYPES = ["addback", "deduction", "capital_allowance"]
INCOME_TAX_ITEM_TYPE_LABELS = {
    "addback": "Add back (disallowable / non-taxable item)",
    "deduction": "Less: allowable deduction",
    "capital_allowance": "Less: capital allowance",
}


class IncomeTaxComputation(db.Model):
    """The Income Tax Computation working paper - reconciles accounting
    profit before tax (taken live from the Statement of Profit or Loss,
    never re-entered here) to taxable income and the resulting current tax
    charge, via preparer-entered add-back/deduction/capital allowance
    lines. See the module comment above on why no rate is pre-filled.
    One per engagement."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    tax_rate_percent = db.Column(db.Float)
    aids_levy_percent = db.Column(db.Float)
    tax_loss_brought_forward = db.Column(db.Float, default=0.0)
    # Profit before tax typed in by the preparer, used only when the engagement
    # has no Trial Balance to take it from (a Tax Advisory engagement works from
    # the client's finished accounts and has no Trial Balance tab).
    profit_before_tax_manual = db.Column(db.Float)
    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("income_tax_computation", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    items = db.relationship(
        "IncomeTaxAdjustmentLine", backref="computation", lazy=True,
        cascade="all, delete-orphan", order_by="IncomeTaxAdjustmentLine.order",
    )

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<IncomeTaxComputation engagement={self.engagement_id}>"


class IncomeTaxAdjustmentLine(db.Model):
    """One add-back, allowable deduction, or capital allowance line on the
    Income Tax Computation working paper - entirely preparer-entered (see
    the module comment above IncomeTaxComputation)."""
    id = db.Column(db.Integer, primary_key=True)
    computation_id = db.Column(db.Integer, db.ForeignKey("income_tax_computation.id"), nullable=False)
    item_type = db.Column(db.String(20), default="addback")  # see INCOME_TAX_ITEM_TYPES
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    order = db.Column(db.Integer, default=0)
    source = db.Column(db.String(30))  # "s16_engine" for lines posted from the Prohibited Deductions schedule (prohibited_deductions.py); null = preparer-entered

    def __repr__(self):
        return f"<IncomeTaxAdjustmentLine computation={self.computation_id}>"


class DeferredTaxComputation(db.Model):
    """The Deferred Tax Computation working paper - one temporary
    difference per row (DeferredTaxItem below), each contributing
    (accounting amount - tax base) x tax rate to a single net deferred tax
    asset/liability. The Property, Plant and Equipment temporary difference
    - almost always the most material one, and the one this app can help
    with directly - gets its own dedicated field (ppe_tax_base) so its
    accounting-amount side can be pulled live from the Asset Register's own
    movement schedule (financials.build_ppe_movement_schedule) rather than
    retyped; the preparer only has to supply its tax base (the cumulative
    tax written-down value), since this app has no prior-year capital
    allowance history to derive that from on its own. Every other temporary
    difference (provisions, unrealised amounts, assessed losses not
    recognised elsewhere, etc.) is a plain manual DeferredTaxItem row.
    One per engagement."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    tax_rate_percent = db.Column(db.Float)
    ppe_tax_base = db.Column(db.Float)
    # "auto": the PPE tax base is picked up from the Capital Allowance schedule class by class; "manual": the typed ppe_tax_base is used.
    # Blank on older rows: manual when a figure was typed, otherwise auto.
    ppe_tax_base_mode = db.Column(db.String(10))
    prior_tax_rate_percent = db.Column(db.Float)        # the rate for the prior-year position; blank = the same rate
    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("deferred_tax_computation", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    items = db.relationship(
        "DeferredTaxItem", backref="computation", lazy=True,
        cascade="all, delete-orphan", order_by="DeferredTaxItem.order",
    )

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<DeferredTaxComputation engagement={self.engagement_id}>"


class DeferredTaxItem(db.Model):
    """One temporary difference (other than PPE - see DeferredTaxComputation
    above) on the Deferred Tax Computation working paper: an accounting
    amount, its tax base, and the resulting (accounting - tax base) x rate
    deferred tax effect."""
    id = db.Column(db.Integer, primary_key=True)
    computation_id = db.Column(db.Integer, db.ForeignKey("deferred_tax_computation.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    accounting_amount = db.Column(db.Float, default=0.0)
    tax_base_amount = db.Column(db.Float, default=0.0)
    prior_accounting_amount = db.Column(db.Float)       # the same difference a year earlier; both blank = not available
    prior_tax_base_amount = db.Column(db.Float)
    order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<DeferredTaxItem computation={self.computation_id}>"


# ---------- Quarterly Payment Dates (QPDs) / Provisional Income Tax ----------
# Zimbabwe requires a company to pay its estimated corporate income tax in
# four instalments DURING the tax year itself (QPDs), based on an ESTIMATE
# of annual taxable income - underestimating exposes the client to ZIMRA
# interest/penalties (see PenaltyInterestCalculation above), so the whole
# point of this working paper is to let the estimate be refined quarter by
# quarter as better information comes in (an updated Trial Balance, another
# month or two of VAT turnover), not to get it right once and forget it.
#
# Deliberately CLIENT-level (client_id, not engagement_id) and one per
# (client, tax_year): a client owes QPDs every year whether or not the firm
# happens to have an open engagement for them that year - same reasoning as
# TaxAccountingFilingDocument above, and unlike IncomeTaxComputation/
# TaxReturnRecord, which are tied to one specific engagement's own file.
#
# Per the firm's own choice, NEITHER the QPD due dates NOR the statutory
# cumulative percentages (QPDInstalmentRate below) are hardcoded - both stay
# firm-editable, consistent with this app's long-standing rule of never
# hardcoding a figure that could change with a Finance Act (see
# IncomeTaxComputation's own module comment above: tax_rate_pct/
# aids_levy_pct on QPDEstimate below are preparer-entered the same way, with
# no pre-filled default).
#
# Two ways to arrive at an estimate of annual taxable income, offered side
# by side with no system preference (the firm picks per client/quarter):
#   - "Trial Balance": a YTD trial balance is entered/imported (QPDTrialBalanceLine,
#     mapped to the SAME IAS 1 categories as an audit engagement's Trial
#     Balance - reusing financials.compute_totals/build_income_statement to
#     get a YTD profit before tax), annualized by tb_months_covered, then
#     reconciled to taxable income via the SAME engine as an audited
#     engagement's Income Tax Computation (financials.build_income_tax_
#     computation, with QPDAdjustmentLine add-back/deduction rows - reusing
#     IncomeTaxAdjustmentLine's own item_type/INCOME_TAX_ITEM_TYPES values
#     so no translation layer is needed between the two). More accurate
#     once there's a TB to work from, since it's built on actual YTD
#     results rather than a margin assumption.
#   - "VAT Turnover": monthly VAT-return turnover figures (QPDMonthlyTurnover
#     - this app has no monthly VAT Return tracking model to pull from
#     automatically; VATInvoice above is an engagement-scoped invoice
#     register, not a return-filing history, so these are entered by hand)
#     are annualized and multiplied by a preparer-entered net margin % -
#     turnover is not profit, so this is inherently a rougher estimate, but
#     useful early in the year before a TB is ready.
# Whichever method was used, the result only becomes the estimate QPDs are
# actually computed from once copied into estimated_annual_taxable_income/
# estimated_annual_tax_charge below - the same "system suggests, preparer's
# save action commits it, and it stays directly editable afterwards" pattern
# as Payslip.is_auto_calculated, so a Partner can always see (and override)
# today's best figure regardless of how it was arrived at.
QPD_ESTIMATION_METHODS = ["Trial Balance", "VAT Turnover", "Manual"]


class QPDInstalmentRate(db.Model):
    """One of the firm-wide QPD instalments - ordinarily four (25 March,
    25 June, 25 September, 20 December) - firm-editable, including the date
    and cumulative_pct themselves (see module comment above on why, unlike
    most of this app's "well-corroborated" statutory dates, the firm asked
    for these to stay adjustable too). cumulative_pct is the % of the
    ESTIMATED ANNUAL tax charge that should have been paid BY this
    instalment's due date - ZIMRA publishes these as cumulative figures
    (e.g. 10%, 35%, 65%, 100%), not as four independent slices - so each
    instalment's own amount is this instalment's cumulative_pct minus the
    previous instalment's cumulative_pct, of the CURRENT estimate at the
    time it's computed (see qpd_calc.build_instalments)."""
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(60), nullable=False)
    due_month = db.Column(db.Integer, nullable=False)  # 1-12
    due_day = db.Column(db.Integer, nullable=False)  # 1-31
    cumulative_pct = db.Column(db.Float, nullable=False)
    order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<QPDInstalmentRate {self.label!r} {self.cumulative_pct}% by {self.due_month}/{self.due_day}>"


class FxRate(db.Model):
    """A firm-wide, dated USD:ZWG (ZiG) exchange rate - e.g. the RBZ
    auction/interbank rate on a given day - logged by a preparer once filed/
    observed, so QPD instalments (see QPDInstalmentRecord.fx_rate_snapshot
    below) can convert the USD-equivalent ZWG-denominated portion of an
    instalment into the actual ZWG currency amount payable, using whatever
    rate was in effect at the time, rather than this app hardcoding or
    guessing one. `rate` is expressed as ZWG per 1 USD (e.g. 35.5 means
    USD 1 = ZWG 35.50). One row per calendar date - re-logging the same date
    updates that day's rate rather than creating a duplicate."""
    id = db.Column(db.Integer, primary_key=True)
    rate_date = db.Column(db.Date, nullable=False, unique=True)
    rate = db.Column(db.Float, nullable=False)  # ZWG per 1 USD
    source = db.Column(db.String(120))  # e.g. "RBZ auction", "RBZ interbank", "Bank rate"
    notes = db.Column(db.Text)
    # Set only when this rate was converted from ZWL per USD to ZWG per USD
    # by the "Convert ZWL rates to ZWG" tool (qpd.convert_zwl_rates): holds
    # the ORIGINAL ZWL figure, so the conversion can't be applied twice and
    # can be undone. NULL for every rate entered directly in ZWG.
    original_rate_zwl = db.Column(db.Float)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")

    def __repr__(self):
        return f"<FxRate {self.rate_date} {self.rate}>"


FX_AVERAGE_PERIODS = [
    ("Q1", "Quarter 1 (Jan - Mar)"),
    ("Q2", "Quarter 2 (Apr - Jun)"),
    ("Q3", "Quarter 3 (Jul - Sep)"),
    ("Q4", "Quarter 4 (Oct - Dec)"),
    ("FY", "Annual average (full year)"),
]
FX_AVERAGE_PERIOD_LABELS = dict(FX_AVERAGE_PERIODS)


class FxAverageRate(db.Model):
    """A manually captured USD:ZWG AVERAGE exchange rate for one calendar
    quarter (Q1-Q4) or for the full year ("FY"), kept separately from the
    dated spot-rate log (FxRate above) because an average is a figure the
    preparer works out/obtains themselves (e.g. from the RBZ's published
    averages) for a whole period, not an observation on one day. `rate` is
    ZWG per 1 USD, same convention as FxRate. One row per (rate_year,
    period) - capturing the same period again updates it rather than
    duplicating."""
    id = db.Column(db.Integer, primary_key=True)
    rate_year = db.Column(db.Integer, nullable=False)
    period = db.Column(db.String(2), nullable=False)  # "Q1".."Q4" or "FY"
    rate = db.Column(db.Float, nullable=False)  # ZWG per 1 USD
    source = db.Column(db.String(120))
    notes = db.Column(db.Text)
    original_rate_zwl = db.Column(db.Float)  # see FxRate.original_rate_zwl
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("rate_year", "period", name="uq_fx_average_rate_year_period"),)

    @property
    def period_label(self):
        return FX_AVERAGE_PERIOD_LABELS.get(self.period, self.period)

    def __repr__(self):
        return f"<FxAverageRate {self.rate_year} {self.period} {self.rate}>"


class QPDEstimate(db.Model):
    """One client's provisional income tax estimate for one tax year - see
    the module comment above for the client-level scoping and the two
    estimation methods. estimated_annual_revenue/estimated_annual_taxable_
    income/tax_rate_pct/aids_levy_pct/estimated_annual_tax_charge are the
    "current best estimate" - always directly editable regardless of which
    method (or neither) last touched them. QPDInstalmentRecord rows (below)
    are computed FROM this estimate on demand, each one a frozen snapshot,
    so recomputing instalments after revising the estimate never silently
    rewrites an instalment already recorded as paid.

    Dual-currency (USD/ZWG) split: Zimbabwe taxpayers earning revenue in
    more than one currency must split their provisional tax liability
    between USD and ZWG (ZiG) - and ZIMRA's rule for HOW to split it is
    asymmetric, not a straight read of the actual currency mix both ways:
      - If more than 50% of revenue was earned in USD, the tax is split
        evenly 50:50 USD/ZWG regardless of exactly how far over 50% USD
        actually was.
      - If ZWG is instead the majority currency (USD is 50% or less), the
        split follows the ACTUAL proportion earned - e.g. 30% USD/70% ZWG
        stays 30:70, it is never forced to 50:50.
    revenue_usd_pct is the preparer-entered ACTUAL % of revenue earned in
    USD for the year (the figure a preparer reads off the client's own
    records); effective_usd_pct below applies the rule above to it, and
    that effective split is what Revenue/Expenses/Taxable Income/Tax
    Charge (and, frozen per instalment, each QPDInstalmentRecord) are
    actually divided by - see currency_split()."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    tax_year = db.Column(db.Integer, nullable=False)  # calendar tax year, e.g. 2026

    estimation_method = db.Column(db.String(20), default="Manual")  # QPD_ESTIMATION_METHODS

    # ---- Trial Balance method working fields ----
    tb_months_covered = db.Column(db.Integer)  # how many months of the tax year the TB below covers, for annualizing
    tb_tax_loss_brought_forward = db.Column(db.Float, default=0.0)

    # ---- VAT Turnover method working fields ----
    vat_net_margin_pct = db.Column(db.Float)  # preparer's estimated net profit margin on annualized turnover

    # ---- The current best estimate - the "flex" layer, see module comment ----
    estimated_annual_revenue = db.Column(db.Float)  # preparer-entered/suggested annualized revenue - see effective_usd_pct/currency_split below
    estimated_annual_taxable_income = db.Column(db.Float, default=0.0)
    tax_rate_pct = db.Column(db.Float)  # preparer-entered, never hardcoded - see module comment
    aids_levy_pct = db.Column(db.Float)
    estimated_annual_tax_charge = db.Column(db.Float, default=0.0)

    # ---- Dual-currency (USD/ZWG) split - see class docstring above ----
    revenue_usd_pct = db.Column(db.Float)  # preparer-entered ACTUAL % of revenue earned in USD; remainder assumed ZWG (ZiG)
    # Salaries & wages (annual), entered by hand: the return shows Salaries
    # and Other expenses as separate lines of the expenses total, so the
    # Currency Split table breaks the (implied) expenses figure into the two.
    # NULL = not entered yet (Other expenses then shows the whole total).
    estimated_annual_salaries = db.Column(db.Float)

    notes = db.Column(db.Text)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("qpd_estimates", lazy=True, cascade="all, delete-orphan"))
    updated_by = db.relationship("User")
    tb_lines = db.relationship("QPDTrialBalanceLine", backref="estimate", lazy=True, cascade="all, delete-orphan", order_by="QPDTrialBalanceLine.id")
    adjustment_lines = db.relationship("QPDAdjustmentLine", backref="estimate", lazy=True, cascade="all, delete-orphan", order_by="QPDAdjustmentLine.order")
    monthly_turnovers = db.relationship("QPDMonthlyTurnover", backref="estimate", lazy=True, cascade="all, delete-orphan", order_by="QPDMonthlyTurnover.month")
    instalments = db.relationship("QPDInstalmentRecord", backref="estimate", lazy=True, cascade="all, delete-orphan", order_by="QPDInstalmentRecord.order")

    __table_args__ = (db.UniqueConstraint("client_id", "tax_year", name="uq_qpd_estimate_client_year"),)

    @property
    def annualized_turnover(self):
        """Average monthly turnover entered so far x 12 - 0 if none entered
        yet, so templates can show a clear "enter at least one month" state
        rather than a misleading 0.00 estimate."""
        months = len(self.monthly_turnovers)
        if not months:
            return 0.0
        total = sum(t.turnover_amount or 0.0 for t in self.monthly_turnovers)
        return round((total / months) * 12, 2)

    @property
    def unmapped_tb_line_count(self):
        return sum(1 for l in self.tb_lines if not l.fs_category)

    @property
    def effective_usd_pct(self):
        """The USD % actually used to split Revenue/Expenses/Taxable
        Income/Tax Charge/Instalments between USD and ZWG - ZIMRA's "50%
        rule" described in the class docstring. None until revenue_usd_pct
        has been entered."""
        if self.revenue_usd_pct is None:
            return None
        return 50.0 if self.revenue_usd_pct > 50 else self.revenue_usd_pct

    @property
    def effective_zwg_pct(self):
        if self.effective_usd_pct is None:
            return None
        return 100.0 - self.effective_usd_pct

    @property
    def implied_annual_expenses(self):
        """Estimated annual revenue less estimated annual taxable income -
        a simple derived figure that exists purely so the dual-currency
        split has an "Expenses" amount to apply to alongside Revenue/
        Taxable Income/Tax Charge. Not a substitute for an actual expense
        schedule - the Trial Balance method's own expense lines are already
        folded into Taxable Income via its add-back/deduction
        reconciliation. None if either figure hasn't been entered yet."""
        if self.estimated_annual_revenue is None or self.estimated_annual_taxable_income is None:
            return None
        return round(self.estimated_annual_revenue - self.estimated_annual_taxable_income, 2)

    @property
    def other_annual_expenses(self):
        """Implied total expenses less Salaries & wages - the "Other
        expenses" line of the return. The whole expenses total when
        salaries haven't been entered; None if expenses can't be derived."""
        total = self.implied_annual_expenses
        if total is None:
            return None
        return round(total - (self.estimated_annual_salaries or 0.0), 2)

    def currency_split_table(self, zwg_rate=None):
        """Rows for the annual Currency Split table, laid out like the
        return: Revenue; Expenses broken into Salaries and Other expenses
        (each its own line, both divided with the same effective USD % as
        everything else); Taxable income; Tax charge. Each row is a dict:
        label, amount, usd, zwg (USD-equivalent, as split), zwg_local (the
        ZWG column converted into actual ZWG at zwg_rate - ZWG per 1 USD -
        or None without a rate) and sub (indented breakdown line).
        Other expenses is derived as total expenses less Salaries in EACH
        currency, so Salaries + Other always adds back to the Expenses
        line exactly, with no rounding drift."""
        def row(label, amount, usd, zwg, sub=False):
            local = round(zwg * zwg_rate, 2) if (zwg is not None and zwg_rate) else None
            return {"label": label, "amount": amount, "usd": usd, "zwg": zwg, "zwg_local": local, "sub": sub}

        def plain(label, amount, sub=False):
            usd, zwg = self.currency_split(amount)
            return row(label, amount, usd, zwg, sub)

        exp_total = self.implied_annual_expenses
        exp_usd, exp_zwg = self.currency_split(exp_total)
        rows = [plain("Revenue", self.estimated_annual_revenue),
                row("Expenses (implied: Revenue less Taxable Income)", exp_total, exp_usd, exp_zwg)]
        sal = self.estimated_annual_salaries
        sal_usd, sal_zwg = self.currency_split(sal)
        rows.append(row("Salaries & wages", sal, sal_usd, sal_zwg, sub=True))
        other_amount = self.other_annual_expenses
        other_usd = round(exp_usd - (sal_usd or 0.0), 2) if exp_usd is not None else None
        other_zwg = round(exp_zwg - (sal_zwg or 0.0), 2) if exp_zwg is not None else None
        rows.append(row("Other expenses", other_amount, other_usd, other_zwg, sub=True))
        rows.append(plain("Taxable income", self.estimated_annual_taxable_income))
        rows.append(plain("Tax charge", self.estimated_annual_tax_charge))
        return rows

    def currency_split(self, amount):
        """(usd_amount, zwg_amount) for `amount` using effective_usd_pct -
        (None, None) if no currency mix has been entered yet or amount
        itself is None."""
        if self.effective_usd_pct is None or amount is None:
            return None, None
        usd = round(amount * self.effective_usd_pct / 100.0, 2)
        return usd, round(amount - usd, 2)

    def __repr__(self):
        return f"<QPDEstimate client={self.client_id} tax_year={self.tax_year}>"


class QPDTrialBalanceLine(db.Model):
    """One account on a QPDEstimate's YTD trial balance - same shape as
    TrialBalanceLine above (duck-type compatible with financials.
    compute_totals/build_income_statement: .fs_category, .current_debit,
    .current_credit, .prior_debit, .prior_credit) so this estimate's YTD
    profit before tax is computed with the EXACT same engine as an audited
    engagement's Statement of Profit or Loss - deliberately not a full
    second trial balance implementation. prior_debit/prior_credit are
    always 0 here (a QPD estimate has no "prior year comparative" of its
    own) but kept for duck-type compatibility rather than a parallel
    current-year-only version of compute_totals."""
    id = db.Column(db.Integer, primary_key=True)
    estimate_id = db.Column(db.Integer, db.ForeignKey("qpd_estimate.id"), nullable=False)
    account_code = db.Column(db.String(50))
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50))  # code from financials.FS_CATEGORIES, or blank if unmapped
    coa_account_number = db.Column(db.String(20))  # optional link to one of the firm's own Standard Chart of Accounts entries - see StandardChartOfAccounts/COAMapping
    current_debit = db.Column(db.Float, default=0.0)
    current_credit = db.Column(db.Float, default=0.0)
    prior_debit = db.Column(db.Float, default=0.0)
    prior_credit = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f"<QPDTrialBalanceLine {self.account_name}>"


class QPDAdjustmentLine(db.Model):
    """One add-back/deduction/capital-allowance line reconciling a
    QPDEstimate's annualized Trial Balance profit to estimated taxable
    income - reuses IncomeTaxAdjustmentLine's own item_type values
    (INCOME_TAX_ITEM_TYPES above) so financials.build_income_tax_
    computation() can be called with these rows directly, unchanged, the
    same way it already is for an audited engagement's own computation."""
    id = db.Column(db.Integer, primary_key=True)
    estimate_id = db.Column(db.Integer, db.ForeignKey("qpd_estimate.id"), nullable=False)
    item_type = db.Column(db.String(20), default="addback")  # see INCOME_TAX_ITEM_TYPES
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<QPDAdjustmentLine estimate={self.estimate_id}>"


class QPDMonthlyTurnover(db.Model):
    """One month's VAT-return turnover figure, as filed with ZIMRA - entered
    by hand (see the module comment above on why this app has no monthly
    VAT Return model to pull from automatically)."""
    id = db.Column(db.Integer, primary_key=True)
    estimate_id = db.Column(db.Integer, db.ForeignKey("qpd_estimate.id"), nullable=False)
    month = db.Column(db.Date, nullable=False)  # first day of the month it covers
    turnover_amount = db.Column(db.Float, default=0.0)

    __table_args__ = (db.UniqueConstraint("estimate_id", "month", name="uq_qpd_monthly_turnover"),)

    def __repr__(self):
        return f"<QPDMonthlyTurnover estimate={self.estimate_id} {self.month}>"


class QPDInstalmentRecord(db.Model):
    """One computed QPD instalment for one QPDEstimate - a frozen snapshot
    of a QPDInstalmentRate row (label/due date/cumulative_pct) AND the
    annual estimate that was current at the moment this instalment was
    (re)computed (see qpd_calc.build_instalments), so neither editing the
    firm-wide QPDInstalmentRate table nor revising the estimate later
    silently changes an instalment already recorded as paid.
    paid_amount/paid_date/paid_reference are then recorded separately, once
    actually remitted to ZIMRA.

    usd_pct_snapshot freezes QPDEstimate.effective_usd_pct (the dual-
    currency USD/ZWG split - see QPDEstimate's class docstring) at the same
    moment, for the same reason as annual_tax_charge_snapshot: editing the
    estimate's currency mix later must never reshuffle how much of an
    already-computed (or already-paid) instalment was USD vs ZWG.

    fx_rate_snapshot freezes the firm's logged FxRate (ZWG per 1 USD) as of
    the day this instalment was (re)computed, for the same reason again -
    see zwg_amount_converted below, which is the actual ZWG currency amount
    payable (as opposed to zwg_amount, which is only the USD-equivalent
    value of the ZWG-denominated portion). None if no FxRate had been logged
    yet on or before the computation date.

    paid_amount/paid_date/paid_reference record what was actually remitted
    to ZIMRA, as a single USD-equivalent total (unchanged, original
    behaviour). paid_amount_usd/paid_amount_zwg are an optional, independent
    record of the actual currency breakdown of that payment - e.g. off the
    ZIMRA receipt - entered separately rather than derived, since the real
    payment may not exactly match the computed split (the FX rate can move
    between computation and actual payment)."""
    id = db.Column(db.Integer, primary_key=True)
    estimate_id = db.Column(db.Integer, db.ForeignKey("qpd_estimate.id"), nullable=False)
    label = db.Column(db.String(60))
    due_date = db.Column(db.Date)
    cumulative_pct = db.Column(db.Float)
    computed_amount = db.Column(db.Float, default=0.0)  # this instalment's own share (cumulative - previous), of the estimate at computation time
    annual_tax_charge_snapshot = db.Column(db.Float)  # what estimated_annual_tax_charge was when this was computed
    usd_pct_snapshot = db.Column(db.Float)  # what estimate.effective_usd_pct was when this was computed - see class docstring
    fx_rate_snapshot = db.Column(db.Float)  # ZWG per 1 USD, as of the computation date - see class docstring
    computed_at = db.Column(db.DateTime, default=datetime.utcnow)
    order = db.Column(db.Integer, default=0)

    paid_amount = db.Column(db.Float)
    paid_date = db.Column(db.Date)
    paid_reference = db.Column(db.String(100))  # e.g. a ZIMRA payment reference/receipt number
    paid_amount_usd = db.Column(db.Float)  # actual USD remitted - optional, see class docstring
    paid_amount_zwg = db.Column(db.Float)  # actual ZWG (local currency units) remitted - optional, see class docstring

    @property
    def is_paid(self):
        return self.paid_amount is not None

    @property
    def usd_amount(self):
        """This instalment's own USD portion, using the currency split
        frozen at computation time - None if no currency mix was entered
        when it was computed."""
        if self.usd_pct_snapshot is None:
            return None
        return round((self.computed_amount or 0.0) * self.usd_pct_snapshot / 100.0, 2)

    @property
    def zwg_amount(self):
        """The USD-EQUIVALENT value of the portion that must be remitted in
        ZWG - not yet converted to actual ZWG currency units. See
        zwg_amount_converted for the actual amount of ZWG to pay."""
        if self.usd_amount is None:
            return None
        return round((self.computed_amount or 0.0) - self.usd_amount, 2)

    @property
    def zwg_amount_converted(self):
        """The actual ZWG currency amount payable for this instalment's
        ZWG-denominated portion - zwg_amount (a USD-equivalent figure)
        multiplied by the USD:ZWG rate frozen at computation time
        (fx_rate_snapshot). None until both the currency split AND a logged
        FX rate were available when this instalment was computed."""
        if self.zwg_amount is None or self.fx_rate_snapshot is None:
            return None
        return round(self.zwg_amount * self.fx_rate_snapshot, 2)

    def __repr__(self):
        return f"<QPDInstalmentRecord {self.label!r} estimate={self.estimate_id}>"


class SubstantiveProcedureArea(db.Model):
    """One row per (engagement, standard audit area) - e.g. "Cash and Bank"
    for a given engagement - each forming one section of the substantive
    audit programme. Suggested procedures (SubstantiveProcedureItem below)
    are seeded into it by the "Generate suggested procedures" action, driven
    by the engagement's Risk Assessment rating and the client's industry
    (see AUDIT_AREAS / BASELINE_SUBSTANTIVE_PROCEDURES / etc. above), and
    ticked off one by one - but the whole area carries a single
    Preparer/Reviewer/Partner sign-off, like a section of an audit file
    rather than each individual procedure needing its own."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    area = db.Column(db.String(80), nullable=False)
    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("substantive_procedure_areas", lazy=True, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    items = db.relationship(
        "SubstantiveProcedureItem", backref="area_record", lazy=True,
        cascade="all, delete-orphan", order_by="SubstantiveProcedureItem.id",
    )

    __table_args__ = (db.UniqueConstraint("engagement_id", "area", name="uq_subprocedure_area_engagement"),)

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def is_complete(self):
        return len(self.items) > 0 and all(i.status in ("Done", "N/A") for i in self.items)

    def __repr__(self):
        return f"<SubstantiveProcedureArea {self.area} engagement={self.engagement_id}>"


class SubstantiveProcedureItem(db.Model):
    """A single substantive procedure within an audit area - either
    system-suggested (source "baseline"/"risk"/"industry", from the
    dictionaries above) or added by hand (source "manual")."""
    id = db.Column(db.Integer, primary_key=True)
    area_id = db.Column(db.Integer, db.ForeignKey("substantive_procedure_area.id"), nullable=False)
    procedure_text = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(20), default="manual")  # "baseline" | "risk" | "industry" | "manual"
    status = db.Column(db.String(20), default="Not Started")
    notes = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    tickmark_id = db.Column(db.Integer, db.ForeignKey("tickmark.id"))
    tickmark = db.relationship("Tickmark", foreign_keys=[tickmark_id])

    # Only populated on a Secretarial engagement's Execution Plan (see
    # SECRETARIAL_EXECUTION_TASK_DETAILS above) - the Trigger/Event, Lead
    # Responsible and Target Output columns of the firm's Practical
    # Secretarial Execution Plan Schedule. Left blank (and not shown) on
    # every other engagement type's Substantive Procedures, where `status`
    # above already serves as the "Status Tracking" column.
    trigger_event = db.Column(db.String(200))
    responsible_role = db.Column(db.String(100))
    target_output = db.Column(db.String(200))

    # Only populated on a Business Intelligence and IT Engagement (see
    # BUSINESS_IT_PROCEDURE_KIND above) - "ITGC" for a test of the underlying
    # IT general control environment, "Substantive" for a data-driven/
    # technical validation procedure. Left blank (and not shown) on every
    # other engagement type, same "reuse a generic column for one type's
    # extra detail" approach as trigger_event/responsible_role/target_output
    # above for Secretarial.
    procedure_kind = db.Column(db.String(20))

    @property
    def reviews(self):
        """Every dated review entry left against this specific procedure -
        see WorkpaperReview. Unlike the area-level Preparer/Reviewer/Partner
        sign-off on SubstantiveProcedureArea (which covers the whole area
        as one unit), any number of reviewers can each leave their own note
        on one individual procedure here."""
        return (
            WorkpaperReview.query
            .filter_by(reviewable_type=REVIEWABLE_TYPE_SUBSTANTIVE_ITEM, reviewable_id=self.id)
            .order_by(WorkpaperReview.reviewed_at).all()
        )

    def __repr__(self):
        return f"<SubstantiveProcedureItem area={self.area_id}>"


# Depreciation methods offered on the PPE Depreciation policy working paper
# (see PPEAssetClass below). Kept short and standard rather than exhaustive -
# a preparer needing something else can still describe it in the class name.
PPE_DEPRECIATION_METHODS = ["Straight-line", "Reducing balance"]


class PPEAssetClass(db.Model):
    """One row of the Property, Plant and Equipment Depreciation policy
    working paper - a single asset class (e.g. "Motor vehicles", "Office
    equipment") with the method and rate/useful life applied to it. This
    structured table is what the PPE Asset Register (PPEAsset below) draws
    its own per-asset method/rate from, and what the auto-generated PPE
    accounting policy note (financials.ppe_accounting_policy_text) is built
    from once at least one class exists here - replacing the generic
    boilerplate that note otherwise falls back to.

    `order` controls both display order here and the order asset classes
    are listed in the Asset Register/movement schedule, so the preparer's
    own ordering (usually largest/most material class first) carries
    through everywhere this data is used."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    depreciation_method = db.Column(db.String(30), default="Straight-line")
    rate_percent = db.Column(db.Float)
    useful_life_years = db.Column(db.Float)
    order = db.Column(db.Integer, default=0)
    tb_aliases = db.Column(db.String(400))        # trial balance asset-class keys matched to this class ("a|b") - see asset_recon.build_units

    engagement = db.relationship(
        "Engagement",
        backref=db.backref("ppe_asset_classes", lazy=True, cascade="all, delete-orphan", order_by="PPEAssetClass.order"),
    )

    @property
    def rate_display(self):
        parts = []
        if self.rate_percent is not None:
            parts.append(f"{self.rate_percent:g}% p.a.")
        if self.useful_life_years is not None:
            parts.append(f"{self.useful_life_years:g} yrs")
        return " / ".join(parts) if parts else "—"

    def __repr__(self):
        return f"<PPEAssetClass {self.name} engagement={self.engagement_id}>"


class DeletedPPEAsset(db.Model):
    """A copy of an Asset Register row taken at the moment it was deleted, so that a deletion can be undone ("Recently deleted" on the
    Asset Register tab). Restoring puts the row back exactly as it was and removes the copy."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, index=True)
    description = db.Column(db.String(200))
    snapshot = db.Column(db.Text, nullable=False)          # JSON of the PPEAsset fields
    deleted_at = db.Column(db.DateTime, default=datetime.utcnow)
    deleted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    deleted_by = db.relationship("User", foreign_keys=[deleted_by_id])


class PPEAsset(db.Model):
    """One row of the Property, Plant and Equipment Asset Register working
    paper - a single fixed asset, editable directly in the app (with an
    Excel export for filing) rather than only as a downloaded spreadsheet.
    The standard column set: asset code/description, category (asset_class),
    date acquired, cost, opening accumulated depreciation, depreciation
    method/rate (via asset_class), current-year depreciation charge,
    disposals (cost and accumulated depreciation), and closing NBV
    (computed, not stored - see financials.build_ppe_movement_schedule).

    Whether an asset's cost is an opening balance or a current-year
    addition for the movement schedule is inferred from date_acquired
    falling inside the engagement's reporting period or before it - see
    financials._infer_period_start for the (disclosed) assumption this
    relies on when the app doesn't otherwise record a period start date."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey("ppe_asset_class.id"))
    asset_code = db.Column(db.String(40))
    description = db.Column(db.String(200), nullable=False)
    date_acquired = db.Column(db.Date)
    cost = db.Column(db.Float, default=0.0)
    opening_accumulated_depreciation = db.Column(db.Float, default=0.0)
    current_year_depreciation = db.Column(db.Float, default=0.0)
    disposal_cost = db.Column(db.Float, default=0.0)
    disposal_accumulated_depreciation = db.Column(db.Float, default=0.0)
    # For the Capital Allowances tab only: when the asset was sold and what it
    # fetched, which the allowance schedule needs (recoupment / scrapping).
    tax_disposed_on = db.Column(db.Date)
    tax_disposal_proceeds = db.Column(db.Float)
    # The tax value brought forward at the start of the reporting period (the closing tax value of the year before, e.g. 31 Dec
    # 2024 when the period ends 31 Dec 2025). Blank means "work it out from the date acquired and cost"; a figure here is used as
    # the opening tax value instead - see firm_tax.simulate_asset.
    opening_tax_value = db.Column(db.Float)
    order = db.Column(db.Integer, default=0)

    engagement = db.relationship(
        "Engagement",
        backref=db.backref("ppe_assets", lazy=True, cascade="all, delete-orphan", order_by="PPEAsset.order"),
    )
    asset_class = db.relationship("PPEAssetClass", backref=db.backref("assets", lazy=True))

    @property
    def closing_nbv(self):
        return (self.cost or 0.0) - (self.disposal_cost or 0.0) - (
            (self.opening_accumulated_depreciation or 0.0) + (self.current_year_depreciation or 0.0)
            - (self.disposal_accumulated_depreciation or 0.0)
        )

    def __repr__(self):
        return f"<PPEAsset {self.description!r} engagement={self.engagement_id}>"


# Evidence-gathering phase statuses for AuditStrategy below - reuses the
# same vocabulary as CHECKLIST_STATUSES so the UI stays consistent, but
# named separately since the two lists are conceptually different things
# and CHECKLIST_STATUSES may not always evolve in lockstep with this one.
AUDIT_STRATEGY_PHASE_STATUSES = CHECKLIST_STATUSES



class EngagementTaxAssetRule(db.Model):
    """The capital allowance treatment of one asset class on a client
    engagement - the same fields as the firm's own FirmTaxAssetRule, so the one
    engine in firm_tax.py works the schedule for both. Starter rules are
    "not verified" until the Partner has checked them against the Income Tax
    Act. One per (engagement, class)."""
    __tablename__ = "engagement_tax_asset_rule"
    __table_args__ = (db.UniqueConstraint("engagement_id", "asset_class_id", name="uq_eng_tax_rule"),)
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey("ppe_asset_class.id"), nullable=False)
    method = db.Column(db.String(10), nullable=False, default="WT_SL")   # NONE, WT_SL, WT_RB, SIA
    wt_rate_percent = db.Column(db.Float, default=0.0)
    sia_percent = db.Column(db.Float, default=25.0)
    sia_years = db.Column(db.Integer, default=4)
    sia_first_percent = db.Column(db.Float)
    cost_cap = db.Column(db.Float)
    prorata_first_year = db.Column(db.Boolean, default=False, nullable=False)
    verified = db.Column(db.Boolean, default=False, nullable=False)
    note = db.Column(db.String(250))
    engagement = db.relationship("Engagement", backref=db.backref("tax_asset_rules", lazy=True, cascade="all, delete-orphan"))
    asset_class = db.relationship("PPEAssetClass", backref=db.backref("tax_rule", uselist=False, cascade="all, delete-orphan"))


class AuditStrategy(db.Model):
    """The forensic Audit Strategy for one Investigative Engagement - what
    the "Planning" tab is replaced with for that engagement type (every
    other type keeps the ordinary Materiality/Suggested-approach/Trial
    Balance Planning tab untouched). One row per engagement.

    Built around the four pillars of a forensic engagement plan: (1) Scope,
    Objectives & Legal Framework, (2) a Preliminary Fraud Theory - the
    working hypothesis of how the fraud was committed, by whom, and where
    the evidence is - (3) Resource Allocation & Specialised Skills, and
    (4) an Evidence Gathering Strategy sequenced periphery-to-centre
    (digital/data evidence, then third-party/public records, then
    interviews - saving direct confrontation of the target for last). The
    "Assessment summary" shown alongside this on the tab isn't stored here
    at all - it's read live off EntityUnderstanding's Objectives & Scope
    checklist answers, ClientAcceptance's legal/evidence conclusion, and
    RiskAssessment's Fraud Triangle rating, so this strategy is always
    built on top of (never duplicating) what was already captured earlier
    in the engagement.
    """
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)

    # 1. Scope, Objectives & Legal Framework
    objective_statement = db.Column(db.Text)
    chain_of_custody_notes = db.Column(db.Text)
    reporting_destination = db.Column(db.Text)

    # 2. Preliminary Fraud Theory
    potential_perpetrators = db.Column(db.Text)
    fraud_vulnerability = db.Column(db.Text)
    evidentiary_red_flags = db.Column(db.Text)

    # 3. Resource Allocation & Specialised Skills - one available/not toggle
    # plus notes per specialist role.
    resource_forensic_tech_available = db.Column(db.Boolean, default=False)
    resource_forensic_tech_notes = db.Column(db.Text)
    resource_data_analysts_available = db.Column(db.Boolean, default=False)
    resource_data_analysts_notes = db.Column(db.Text)
    resource_interviewers_available = db.Column(db.Boolean, default=False)
    resource_interviewers_notes = db.Column(db.Text)
    resource_legal_counsel_available = db.Column(db.Boolean, default=False)
    resource_legal_counsel_notes = db.Column(db.Text)

    # 4. Evidence Gathering Strategy - sequenced Phase 1 (digital & data) ->
    # Phase 2 (third-party & public records) -> Phase 3 (interviews).
    phase1_status = db.Column(db.String(20), default="Not Started")
    phase1_notes = db.Column(db.Text)
    phase2_status = db.Column(db.String(20), default="Not Started")
    phase2_notes = db.Column(db.Text)
    phase3_status = db.Column(db.String(20), default="Not Started")
    phase3_notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("audit_strategy", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def is_complete(self):
        """The strategy is considered documented once its six core
        narrative fields are filled in - the two operational checklists
        (resource availability, evidence-gathering phase status) are
        expected to keep evolving as fieldwork proceeds even after
        sign-off, so they aren't part of this gate."""
        core_fields = [
            self.objective_statement, self.chain_of_custody_notes, self.reporting_destination,
            self.potential_perpetrators, self.fraud_vulnerability, self.evidentiary_red_flags,
        ]
        return all((v or "").strip() for v in core_fields)

    def __repr__(self):
        return f"<AuditStrategy engagement={self.engagement_id}>"
