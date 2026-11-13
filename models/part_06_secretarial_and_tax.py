"""models, part 6 of 12.  Holds: SecretarialPlan, RegistryFormFiling, TaxRegistration, TaxEntityProfile, TaxDeadline, TaxAnalyticalReview, TaxPosition, PenaltyInterestRate, PenaltyInterestCalculation, VATRate, VATImportBatch, VATInvoice ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

class SecretarialPlan(db.Model):
    """The Secretarial Engagement Plan for one Secretarial engagement -
    what the "Planning" tab is replaced with for that engagement type
    (mirrors AuditStrategy above, which does the same job for Investigative
    Engagements). One row per engagement.

    Built around the five pillars of a secretarial engagement plan under
    the Companies and Other Business Entities Act [Chapter 24:31] (COBE
    Act): (1) Entity Profile & Scope Map, (2) the Annual Statutory
    Compliance Calendar anchored on the client's financial year-end, (3)
    Board & Committee Meeting Cycle planning, (4) Operational &
    Transactional Trigger Mapping, and (5) Risk Assessment & Quality
    Control - unlike audit planning (financial materiality, sampling,
    account balances), secretarial planning centres on statutory deadlines,
    governance calendars, capital events and registry filings, so it does
    not reuse MaterialityCalculation at all.
    """
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)

    # Pillar 1: Entity Profile & Scope Map
    entity_categorization = db.Column(db.Text)
    sector_regulators = db.Column(db.Text)
    constitutional_constraints = db.Column(db.Text)

    # Pillar 2: The Annual Statutory Compliance Calendar - four milestones
    # anchored on the client's financial year-end (Engagement.period_end).
    # Left blank by default (never auto-filled) - the Planning tab shows a
    # suggested date computed live from period_end next to each field (see
    # engagements._secretarial_compliance_calendar_suggestions), which the
    # preparer can accept by typing it in, same "suggested, never
    # fabricated" pattern as scope_suggestion/suggested_categories
    # elsewhere in this app.
    draft_financials_target = db.Column(db.Date)  # firm target: FYE + 4 months (statements laid before the AGM, Sec 183)
    agm_notice_target = db.Column(db.Date)  # 21 days before the AGM (Sec 169(1))
    agm_target = db.Column(db.Date)  # firm target FYE + 6 months; the Act requires an AGM once in every 12 months (Sec 167(2))
    annual_return_target = db.Column(db.Date)  # 21 days after the anniversary of incorporation / registration / re-registration (Sec 165(2))
    # The date of incorporation, registration or re-registration whose anniversary the annual return is tied to (Sec 165(2)),
    # and the date of the last AGM (Sec 167(2): one AGM in every twelve months). Both feed cobe_checks.calendar_suggestions.
    registration_date = db.Column(db.Date)
    last_agm_date = db.Column(db.Date)
    compliance_calendar_notes = db.Column(db.Text)

    # Pillar 3: Board & Committee Meeting Cycle
    meeting_schedule_notes = db.Column(db.Text)
    agenda_preplanning_notes = db.Column(db.Text)
    document_deadline_notes = db.Column(db.Text)

    # Pillar 4: Operational & Transactional Trigger Mapping
    transactional_trigger_notes = db.Column(db.Text)

    # Pillar 5: Risk Assessment & Quality Control
    onboarding_risk_integration_notes = db.Column(db.Text)
    remediation_milestones_notes = db.Column(db.Text)
    register_reconciliation_notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("secretarial_plan", uselist=False, cascade="all, delete-orphan"))
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
        """One representative narrative field per pillar filled in - the
        compliance calendar dates and the more operational notes fields are
        expected to keep evolving through the engagement so aren't part of
        this gate, same reasoning as AuditStrategy.is_complete above."""
        core_fields = [
            self.entity_categorization, self.sector_regulators,
            self.meeting_schedule_notes, self.transactional_trigger_notes,
            self.onboarding_risk_integration_notes,
        ]
        return all((v or "").strip() for v in core_fields)

    def __repr__(self):
        return f"<SecretarialPlan engagement={self.engagement_id}>"


# ---------- Tax Advisory & Tax Compliance module ----------
#
# Shown on an engagement's "Tax" tab whenever Engagement.has_tax_module is
# True (a dedicated Tax Compliance/Tax Advisory & Health Check engagement,
# or any other engagement type with a Tax service explicitly turned on -
# see TAX_SERVICES above) - see tax.py for the routes and
# templates/tax/*.html for the tab itself. Deliberately reuses existing
# machinery wherever it already fits rather than duplicating it:
#   - the Tax Risk Register reuses RiskItem (module="tax") - see above
#   - the tax-head-by-tax-head Execution Plan (Module 7) reuses
#     SubstantiveProcedureArea/SubstantiveProcedureItem, exactly like the
#     Secretarial engagement type's own Execution Plan does - one area per
#     TAX_HEADS entry
#   - the CIT computation (Module 6) reuses the existing
#     IncomeTaxComputation/DeferredTaxComputation working papers
#   - Tax Document Management (Module 9) reuses the existing
#     Documents/Filing Index rather than a new document model
#   - the Tax Opinion and Tax Health Check Report (Module 10) reuse
#     WorkpaperNarrative, one row per named part - see TAX_OPINION_PARTS/
#     TAX_HEALTH_CHECK_PARTS above
# The models below are the genuinely new pieces that nothing existing
# already covers.

TAX_REGISTRATION_RECORD_TYPES = [
    ("registration", "Tax head registration"),
    ("clearance", "Tax clearance / representation status"),
    ("portal_access", "Tax authority e-services portal access"),
]


class RegistryFormFiling(db.Model):
    """One filing with the Registrar of Companies on a Secretarial engagement: which CR form (cr_forms.CR_FORMS), what it is
    about, the date of the event that triggers it, the date it must be lodged by (worked out from the Act's time limit where
    there is one, or typed by the preparer), and where it stands. A log for the preparer. It does not file anything and nothing
    in it is marked verified."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, index=True)
    form_code = db.Column(db.String(10), nullable=False)  # "CR6", "AR", "RR" ...
    subject = db.Column(db.String(300))  # e.g. "Appointment of J. Moyo as director"
    event_date = db.Column(db.Date)  # the date the time limit runs from
    due_date = db.Column(db.Date)
    lodged_date = db.Column(db.Date)
    status = db.Column(db.String(30), nullable=False, default="Not started")
    registrar_reference = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("registry_form_filings", cascade="all, delete-orphan"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def form(self):
        import cr_forms
        return cr_forms.CR_FORM_BY_CODE.get(self.form_code)

    @property
    def is_overdue(self):
        import cr_forms
        return cr_forms.is_overdue(self.status, self.due_date)

    def __repr__(self):
        return f"<RegistryFormFiling {self.form_code} engagement={self.engagement_id}>"


class TaxRegistration(db.Model):
    """One row of the Tax Client Profile's registration matrix, tax
    clearance & representation status list, or tax authority portal access
    log (Module 1) - which of the three it is is `record_type` (see
    TAX_REGISTRATION_RECORD_TYPES above); all three share the same simple
    shape (an identifier/reference, a status, and a couple of dates) so
    there's no good reason to give them three separate tables.

    SECURITY: portal_username is the only credential-shaped field this
    model has, and deliberately the ONLY one - there is no password field,
    and there must never be one added here. The tax authority portal
    passwords/secrets themselves are explicitly never stored in this app;
    see the "do not store passwords/secrets" note on the Tax tab's Portal
    Access card."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    record_type = db.Column(db.String(20), nullable=False, default="registration")
    tax_head = db.Column(db.String(80))  # one of TAX_HEADS, or blank for a general/whole-entity record
    identifier = db.Column(db.String(150))  # registration number / clearance certificate number / portal name
    status = db.Column(db.String(50))  # free text - e.g. "Registered", "Valid", "Expired", "Active", "Suspended"
    issue_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date)
    portal_username = db.Column(db.String(150))  # only meaningful when record_type == "portal_access" - NEVER a password, see docstring
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_registrations", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < date.today())

    def __repr__(self):
        return f"<TaxRegistration {self.record_type} {self.identifier!r} engagement={self.engagement_id}>"


TAX_ENTITY_CLASSIFICATIONS = [
    "Private Company", "Public Company", "Trust", "Partnership", "Sole Trader",
    "Private Voluntary Organisation (PVO)", "Statutory Body", "Branch of a Foreign Entity", "Other",
]


class TaxEntityProfile(db.Model):
    """The Tax Entity Understanding & Regulatory Profile (Module 2) - one
    per engagement. tax_classification/tax_year_end drive nothing
    automatically elsewhere in the app (there's no basis to infer statutory
    consequences from them without current legislation to hand); they're
    recorded here as the working paper of record. regulatory_matrix_notes
    is the free-text "which regulators/obligations apply" note - a fixed
    Zimbabwean-tax-authority matrix was deliberately not hardcoded here,
    since which obligations apply is itself a judgement call that varies by
    entity and changes with legislation, not a fixed lookup table."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    tax_classification = db.Column(db.String(60))
    tax_year_end = db.Column(db.Date)
    regulatory_matrix_notes = db.Column(db.Text)
    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("tax_entity_profile", uselist=False, cascade="all, delete-orphan"))
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
        return f"<TaxEntityProfile engagement={self.engagement_id}>"


class TaxDeadline(db.Model):
    """One entry on the Statutory Tax Calendar (Module 2) - a filing/payment
    deadline for a given tax head, with a 30/14/7/3-day reminder hierarchy
    computed live from due_date (never stored, so it's never stale) -
    see reminder_level below."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    tax_head = db.Column(db.String(80))
    description = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="Upcoming")  # "Upcoming" | "Filed" | "Not Applicable"
    filed_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_deadlines", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")

    @property
    def days_until_due(self):
        return (self.due_date - date.today()).days

    @property
    def is_overdue(self):
        return self.status == "Upcoming" and self.days_until_due < 0

    @property
    def reminder_level(self):
        """Which rung of the 30/14/7/3-day reminder hierarchy this deadline
        is currently at, or None if it's more than 30 days out, already
        filed, or marked not applicable - purely a display hint (an
        "Overdue"/"Due in N days" badge on the calendar), never anything
        that sends an actual reminder on its own."""
        if self.status != "Upcoming":
            return None
        days = self.days_until_due
        if days < 0:
            return "Overdue"
        if days <= 3:
            return "3-day"
        if days <= 7:
            return "7-day"
        if days <= 14:
            return "14-day"
        if days <= 30:
            return "30-day"
        return None

    def __repr__(self):
        return f"<TaxDeadline {self.description!r} due={self.due_date} engagement={self.engagement_id}>"


class TaxAnalyticalReview(db.Model):
    """The Tax Analytical Review & Historical Exposure working paper
    (Module 3) - one per engagement. Covers the Effective Tax Rate
    analysis, a multi-year trend note, VAT input/output analytics, the PAYE
    reconciliation, and a Red-Flag Engine note - every figure/finding here
    is preparer-entered analysis, explicitly a review flag for further
    investigation rather than a conclusion on its own (see red_flags_notes
    below), same "system prompts, preparer concludes" pattern as the rest
    of this app's analytical review tabs. is_necessary mirrors the
    Secretarial Analytical Review's own necessity toggle - not every tax
    engagement (e.g. a narrow single-issue Tax Opinion) needs a full
    analytical review."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    is_necessary = db.Column(db.Boolean, default=True, nullable=False)
    not_necessary_reason = db.Column(db.Text)

    effective_tax_rate_percent = db.Column(db.Float)
    expected_tax_rate_percent = db.Column(db.Float)
    etr_variance_notes = db.Column(db.Text)
    multi_year_trend_notes = db.Column(db.Text)
    vat_analytics_notes = db.Column(db.Text)
    paye_reconciliation_notes = db.Column(db.Text)
    red_flags_notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("tax_analytical_review", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def etr_variance(self):
        if self.effective_tax_rate_percent is None or self.expected_tax_rate_percent is None:
            return None
        return self.effective_tax_rate_percent - self.expected_tax_rate_percent

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<TaxAnalyticalReview engagement={self.engagement_id}>"


TAX_POSITION_CLASSIFICATIONS = ["Certain", "Probable", "Possible", "Remote"]
TAX_POSITION_APPROVAL_STATUSES = ["Draft", "Pending Approval", "Approved", "Rejected"]


class TaxPosition(db.Model):
    """One entry in the Tax Position Register (Module 4) - a specific
    technical position the firm/client is taking (e.g. "R&D costs treated
    as fully deductible in the year incurred"), classified by likelihood of
    the position being sustained and carrying its own approval workflow -
    distinct from the Tax Risk Register (RiskItem, module="tax"), which
    tracks entity-level risk factors rather than specific technical
    positions."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    tax_head = db.Column(db.String(80))
    position_description = db.Column(db.Text, nullable=False)
    classification = db.Column(db.String(20), default="Possible")
    estimated_exposure = db.Column(db.Float)
    approval_status = db.Column(db.String(20), default="Draft")
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_positions", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def __repr__(self):
        return f"<TaxPosition {self.tax_head} {self.classification} engagement={self.engagement_id}>"


PENALTY_INTEREST_RATE_TYPES = ["Penalty", "Interest"]


class PenaltyInterestRate(db.Model):
    """One effective-dated penalty or interest rate, firm-wide (not
    per-engagement - a statutory rate doesn't vary by client) - the
    Penalty & Interest Engine (Module 4) is built entirely on rates entered
    here by a preparer, never a hardcoded percentage, for the same reason
    IncomeTaxComputation.tax_rate_percent is never pre-filled (see the
    module comment above IncomeTaxComputation): reliable current
    Zimbabwean penalty/interest rates could not be sourced with confidence
    at the time this was built, and they change with legislation. Managed
    from the Tax > Penalty & Interest Rates settings screen; effective_to
    left blank means "still the current rate" for that (tax_head,
    rate_type)."""
    id = db.Column(db.Integer, primary_key=True)
    tax_head = db.Column(db.String(80), nullable=False)
    rate_type = db.Column(db.String(10), nullable=False, default="Penalty")
    rate_percent = db.Column(db.Float, nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date)
    notes = db.Column(db.Text)
    entered_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    entered_at = db.Column(db.DateTime, default=datetime.utcnow)

    entered_by = db.relationship("User")

    @property
    def is_current(self):
        today = date.today()
        return self.effective_from <= today and (self.effective_to is None or self.effective_to >= today)

    def __repr__(self):
        return f"<PenaltyInterestRate {self.tax_head} {self.rate_type} {self.rate_percent}% from={self.effective_from}>"


class PenaltyInterestCalculation(db.Model):
    """One saved run of the Penalty & Interest Engine (Module 4) for a
    given engagement - a simple-interest/flat-penalty calculation from a
    principal amount, days late, and the PenaltyInterestRate the preparer
    selected, kept as a log entry (rather than overwritten) so a prior
    exposure estimate isn't lost when circumstances/rates are recalculated
    later. Purely arithmetic on preparer-supplied figures - see
    financials.calculate_penalty_interest."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    tax_head = db.Column(db.String(80))
    principal_amount = db.Column(db.Float, nullable=False)
    days_late = db.Column(db.Integer, nullable=False, default=0)
    rate_id = db.Column(db.Integer, db.ForeignKey("penalty_interest_rate.id"))
    rate_percent_used = db.Column(db.Float)  # snapshotted at calculation time, in case the rate record is later changed/retired
    computed_amount = db.Column(db.Float)
    notes = db.Column(db.Text)
    calculated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    calculated_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("penalty_interest_calculations", lazy=True, cascade="all, delete-orphan"))
    rate = db.relationship("PenaltyInterestRate")
    calculated_by = db.relationship("User")

    def __repr__(self):
        return f"<PenaltyInterestCalculation {self.tax_head} {self.computed_amount} engagement={self.engagement_id}>"


# ---------- VAT Invoice Register & Input/Output Tax Working Paper ----------
#
# Requested by the user: capture sales (Output) and purchase/expense (Input)
# invoices on an engagement, compute Input Tax and Output Tax, allow a rate
# lower than the standard rate to be captured (a reduced rate, or a
# zero-rated/exempt supply), and import an already-prepared Output/Input Tax
# schedule (Excel/CSV) rather than only capturing invoices one at a time.
# Lives on the Tax tab (VAT is already one of TAX_HEADS above) so it's
# available on any engagement with the Tax module switched on - a dedicated
# Tax engagement, or Tax compliance bundled into an Accounting/Audit
# engagement - exactly like every other Tax module section (Penalty &
# Interest Engine, Dynamic Information Request List) rather than a separate
# copy under the Accounting tab.
#
# VATRate is firm-wide and effective-dated, for the exact same reason
# PenaltyInterestRate above is: Zimbabwe's VAT rate has changed over time
# and reliable current rates couldn't be sourced with confidence at build
# time, so nothing here is ever hardcoded or guessed - a partner enters/
# confirms each rate (including the standard rate itself, any lower/reduced
# rate, and zero-rated) on the VAT Rates settings screen, and every invoice
# picks one of these confirmed rates rather than the app assuming a
# percentage on its own.
VAT_RATE_TYPES = ["Standard", "Reduced", "Zero-rated", "Exempt"]


class VATRate(db.Model):
    """One effective-dated VAT rate, firm-wide (not per-engagement - a
    statutory rate doesn't vary by client) - see the module comment above
    for why this is never hardcoded. "Reduced" is any confirmed rate lower
    than the firm's current Standard rate (the user's own "VAT less than
    the standard rate" case) - captured as its own effective-dated entry
    rather than a single extra field, so more than one lower rate can exist
    over time (or even concurrently, for different classes of supply) just
    like Standard can. "Zero-rated" and "Exempt" are both 0% in practice,
    but kept as distinct rate_type values because they're treated
    differently on a VAT return (a zero-rated supply is still a taxable
    supply reported at 0%; an exempt supply falls outside the VAT system
    entirely) - effective_to left blank means "still the current rate" for
    that rate_type."""
    id = db.Column(db.Integer, primary_key=True)
    rate_type = db.Column(db.String(20), nullable=False, default="Standard")
    rate_percent = db.Column(db.Float, nullable=False, default=0.0)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date)
    notes = db.Column(db.Text)
    entered_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    entered_at = db.Column(db.DateTime, default=datetime.utcnow)

    entered_by = db.relationship("User")

    @property
    def is_current(self):
        today = date.today()
        return self.effective_from <= today and (self.effective_to is None or self.effective_to >= today)

    @property
    def label(self):
        return f"{self.rate_type} ({self.rate_percent:g}%)"

    def __repr__(self):
        return f"<VATRate {self.rate_type} {self.rate_percent}% from={self.effective_from}>"


class VATImportBatch(db.Model):
    """One confirmed import of an already-prepared Output/Input Tax
    schedule (Excel/CSV) into VATInvoice rows below - the user's own
    "importing already prepared output tax and input tax schedule"
    requirement. The uploaded file's column headers rarely match this app's
    field names exactly, so the import is a two-step flow (see
    tax.stage_vat_schedule_import/confirm_vat_schedule_import): the file is
    staged and a best-effort column mapping is suggested (never applied on
    its own - the same "AI/heuristic suggests, person confirms" pattern
    used throughout this app, e.g. financials.suggest_fs_category), the
    preparer reviews/adjusts that mapping, and only on confirmation is this
    batch (and its VATInvoice rows) actually created. Kept as its own
    record - rather than only tagging each row's source - so a batch's
    column_mapping_json and original file stay available for the audit
    trail, and so an entire mistaken import can be identified and reversed
    in one place (see tax.delete_vat_import_batch, which deletes every
    VATInvoice row it produced via the cascade below)."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    direction = db.Column(db.String(10), nullable=False)  # "Output" or "Input" - see VAT_INVOICE_DIRECTIONS
    doc_type = db.Column(db.String(20), default="Invoice")  # see VAT_DOC_TYPES - applies to every row of the batch
    currency = db.Column(db.String(3), default="USD")  # currency of trade the schedule is in (a Currency column in the file can override per row)
    original_filename = db.Column(db.String(300))
    stored_filename = db.Column(db.String(300))  # kept in Config.VAT_IMPORTS_DATA_DIR for the audit trail
    column_mapping_json = db.Column(db.Text)  # the confirmed {field: source_column} mapping, for reference
    row_count = db.Column(db.Integer, default=0)
    skipped_row_count = db.Column(db.Integer, default=0)
    imported_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("vat_import_batches", lazy=True, cascade="all, delete-orphan"))
    imported_by = db.relationship("User")

    @property
    def kind(self):
        return vat_kind_code(self.direction, self.doc_type)

    @property
    def kind_label(self):
        return VAT_KIND_LABELS.get(self.kind) or VAT_INVOICE_DIRECTION_LABELS.get(self.direction, self.direction)

    def __repr__(self):
        return f"<VATImportBatch {self.direction} rows={self.row_count} engagement={self.engagement_id}>"


VAT_INVOICE_DIRECTIONS = ["Output", "Input"]
VAT_INVOICE_DIRECTION_LABELS = {
    "Output": "Output Tax (sales invoice)",
    "Input": "Input Tax (purchase/expense invoice)",
}
# Where a VATInvoice row came from - a person capturing it directly on the
# form, or one row of a confirmed VATImportBatch above. Purely informational
# (both are treated identically everywhere else); shown on the register so
# a preparer can tell an imported row apart from one they typed themselves.
VAT_INVOICE_SOURCES = ["manual", "import"]

# What kind of document a VATInvoice row is. Everything used to be a plain
# invoice; the firm asked for VAT credit notes, Bills of Entry (imports) and
# export sales to be handled - and shown - separately:
#   - Credit Note: a credit note issued to a customer (Output direction) or
#     received from a supplier (Input direction). Captured as the positive
#     figures printed on the credit note and SUBTRACTED in the totals.
#   - Bill of Entry: an import declaration cleared through ZIMRA Customs; the
#     VAT paid on import is Input Tax (Input direction only). taxable_amount is
#     the VAT value for customs purposes; customs_duty is informational.
#   - Export Sale: a zero-rated supply (Output direction only) - the taxable
#     value is reported but the VAT is always 0.
VAT_DOC_TYPES = ["Invoice", "Credit Note", "Bill of Entry", "Export Sale"]
# (kind code, label, direction, doc_type) - the single list the add form and the
# schedule import use, so a person picks ONE thing and direction/doc type
# follow from it.
VAT_DOCUMENT_KINDS = [
    ("output_invoice", "Sales invoice (Output Tax)", "Output", "Invoice"),
    ("export_sale", "Export sale - zero-rated (Output Tax)", "Output", "Export Sale"),
    ("output_credit_note", "Credit note issued to a customer (reduces Output Tax)", "Output", "Credit Note"),
    ("input_invoice", "Purchase/expense invoice (Input Tax)", "Input", "Invoice"),
    ("bill_of_entry", "Bill of Entry - imports (Input Tax)", "Input", "Bill of Entry"),
    ("input_credit_note", "Credit note received from a supplier (reduces Input Tax)", "Input", "Credit Note"),
]
VAT_KIND_LABELS = {k: label for k, label, _d, _t in VAT_DOCUMENT_KINDS}


def resolve_vat_kind(kind=None, direction=None):
    """(direction, doc_type) for a posted `kind` code; or, for older forms that
    only post `direction`, (direction, "Invoice"). None if neither is valid."""
    for code, _label, d, t in VAT_DOCUMENT_KINDS:
        if kind == code:
            return d, t
    if not kind and direction in VAT_INVOICE_DIRECTIONS:
        return direction, "Invoice"
    return None


def vat_kind_code(direction, doc_type):
    for code, _label, d, t in VAT_DOCUMENT_KINDS:
        if d == direction and t == (doc_type or "Invoice"):
            return code
    return None


# ZIMRA requires VAT to be accounted for and paid in the currency of trade, so
# every VAT document carries a currency and the working paper is produced
# SEPARATELY for each one - ZWG and USD are never added together or netted off.
VAT_CURRENCIES = ["ZWG", "USD"]
VAT_DEFAULT_CURRENCY = "USD"  # also what rows captured before currencies existed are treated as
_VAT_CURRENCY_ALIASES = {
    "zwg": "ZWG", "zig": "ZWG", "zimgold": "ZWG", "zimbabwe gold": "ZWG", "zwl": "ZWG", "zwg$": "ZWG",
    "usd": "USD", "us$": "USD", "us dollar": "USD", "us dollars": "USD", "$": "USD", "usd$": "USD",
}


def normalize_vat_currency(value, default=None):
    """'ZWG' or 'USD' for whatever a person typed or a schedule contained
    ("ZiG", "zwg", "US$", "USD "...); `default` if it isn't recognised."""
    text = " ".join(str(value or "").split()).lower()
    if not text:
        return default
    return _VAT_CURRENCY_ALIASES.get(text, default)


def vat_working_paper_by_currency(invoices):
    """{currency: vat_working_paper_summary(...)} for each currency that has
    documents, in VAT_CURRENCIES order - one independent working paper per
    currency of trade. A row with no currency counts as VAT_DEFAULT_CURRENCY."""
    out = {}
    for cur in VAT_CURRENCIES:
        subset = [i for i in invoices if (i.currency or VAT_DEFAULT_CURRENCY) == cur]
        if subset:
            out[cur] = vat_working_paper_summary(subset)
    return out


# Why input tax is not deductible, with the VAT Act reference (section 16(2) of the Value Added Tax Act [Chapter 23:12]).
VAT_DENIAL_GROUNDS = [
    ("entertainment", "Entertainment", "16(2)(a)"),
    ("club", "Club, association or society subscription", "16(2)(b)"),
    ("vehicle", "Motor vehicle (not within the proviso)", "16(2)(d)"),
    ("export_tax", "Export tax on unbeneficiated minerals / hides", "16(2)(e)"),
    ("scheme", "Medical scheme or superannuation fund supplies", "16(2)(c)"),
    ("other", "Other - see the document's notes", "16"),
]
VAT_DENIAL_LABELS = {k: label for k, label, _r in VAT_DENIAL_GROUNDS}
VAT_DENIAL_REFS = {k: r for k, _l, r in VAT_DENIAL_GROUNDS}


def vat_working_paper_summary(invoices):
    """Totals for the VAT working paper, each document kind kept SEPARATE:
    {kind_code: {"count", "taxable", "vat"}} plus net Output/Input tax and
    taxable values (credit notes subtracted) and the net VAT position.
    Positive net position = payable to ZIMRA, negative = refundable."""
    buckets = {code: {"count": 0, "taxable": 0.0, "vat": 0.0, "duty": 0.0} for code, *_ in VAT_DOCUMENT_KINDS}
    for inv in invoices:
        code = vat_kind_code(inv.direction, inv.doc_type)
        if code is None:
            continue
        b = buckets[code]
        b["count"] += 1
        b["taxable"] += inv.taxable_amount or 0.0
        b["vat"] += inv.vat_amount or 0.0
        b["duty"] += inv.customs_duty or 0.0
    out = {"buckets": buckets}
    out["output_taxable"] = (buckets["output_invoice"]["taxable"] + buckets["export_sale"]["taxable"]
                             - buckets["output_credit_note"]["taxable"])
    out["output_tax"] = buckets["output_invoice"]["vat"] + buckets["export_sale"]["vat"] - buckets["output_credit_note"]["vat"]
    out["input_taxable"] = (buckets["input_invoice"]["taxable"] + buckets["bill_of_entry"]["taxable"]
                            - buckets["input_credit_note"]["taxable"])
    out["input_tax"] = buckets["input_invoice"]["vat"] + buckets["bill_of_entry"]["vat"] - buckets["input_credit_note"]["vat"]
    # Section 16(2): input tax the Act does not allow. Denied documents stay in the register; their VAT is
    # taken out of the claimable amount (a denied credit note received is taken out the other way round).
    denied = 0.0
    denied_count = 0
    exempt_taxable = 0.0
    zero_rated_taxable = 0.0
    for inv in invoices:
        code = vat_kind_code(inv.direction, inv.doc_type)
        if code in ("input_invoice", "bill_of_entry") and getattr(inv, "input_tax_denied", False):
            denied += inv.vat_amount or 0.0
            denied_count += 1
        elif code == "input_credit_note" and getattr(inv, "input_tax_denied", False):
            denied -= inv.vat_amount or 0.0
        rt = getattr(getattr(inv, "vat_rate", None), "rate_type", None)
        sign = -1.0 if code == "output_credit_note" else 1.0
        if code in ("output_invoice", "output_credit_note"):
            if rt == "Exempt":
                exempt_taxable += sign * (inv.taxable_amount or 0.0)
            elif rt == "Zero-rated":
                zero_rated_taxable += sign * (inv.taxable_amount or 0.0)
        elif code == "export_sale":
            zero_rated_taxable += inv.taxable_amount or 0.0
    out["input_tax_denied"] = denied
    out["input_tax_denied_count"] = denied_count
    out["input_tax_claimable"] = out["input_tax"] - denied
    out["output_exempt_taxable"] = exempt_taxable      # exempt supplies (section 11) are not taxable supplies
    out["output_zero_rated_taxable"] = zero_rated_taxable   # zero-rated supplies (section 10) are taxable supplies at 0%
    out["output_taxable_excl_exempt"] = out["output_taxable"] - exempt_taxable
    out["net_position"] = out["output_tax"] - out["input_tax_claimable"]
    return out


class VATInvoice(db.Model):
    """One sales (Output) or purchase/expense (Input) invoice line captured
    for an engagement's VAT working paper - either typed in directly or
    produced by a confirmed VATImportBatch above. taxable_amount is always
    the value excluding VAT; vat_amount is the actual VAT charged/claimed on
    the invoice, captured directly (an invoice's own stated VAT amount can
    differ from a plain rate x amount calculation by a cent or two of
    rounding, and this is a register of what the invoice actually says, not
    a recalculation of it) rather than only ever derived from vat_rate.
    vat_rate/vat_rate_percent_used are still recorded for every line (the
    rate actually used, snapshotted the same way
    PenaltyInterestCalculation.rate_percent_used is, in case the firm-wide
    VATRate record is later changed or retired) so the working paper can
    show which invoices used a rate other than Standard - the user's own
    "VAT less than the standard rate" case."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    direction = db.Column(db.String(10), nullable=False)  # "Output" or "Input"
    # Invoice | Credit Note | Bill of Entry | Export Sale - see VAT_DOC_TYPES.
    # NULL (rows from before this existed) means a plain Invoice.
    doc_type = db.Column(db.String(20), default="Invoice")
    # Currency of trade (ZWG | USD) - VAT is declared and paid per currency.
    # NULL (rows from before this existed) is treated as VAT_DEFAULT_CURRENCY.
    currency = db.Column(db.String(3), default="USD")
    # Credit note: the original invoice number it credits. Bill of Entry /
    # Export Sale: the customs entry / export document reference.
    related_reference = db.Column(db.String(100))
    customs_duty = db.Column(db.Float)  # Bill of Entry only - duty paid on the import (informational; not VAT)
    invoice_date = db.Column(db.Date)
    invoice_number = db.Column(db.String(100))
    counterparty_name = db.Column(db.String(200))  # the customer (Output) or supplier (Input) named on the invoice
    description = db.Column(db.String(300))
    taxable_amount = db.Column(db.Float, nullable=False, default=0.0)
    vat_rate_id = db.Column(db.Integer, db.ForeignKey("vat_rate.id"))
    vat_rate_percent_used = db.Column(db.Float)  # snapshot of the rate actually applied at capture/import time
    vat_amount = db.Column(db.Float, nullable=False, default=0.0)
    source = db.Column(db.String(10), nullable=False, default="manual")
    import_batch_id = db.Column(db.Integer, db.ForeignKey("vat_import_batch.id"))
    notes = db.Column(db.Text)
    # Input tax the Act does not allow to be deducted (section 16(2)): the document stays in the register
    # but its VAT is left out of the claimable Input Tax. denial_ground is one of VAT_DENIAL_GROUNDS.
    input_tax_denied = db.Column(db.Boolean, nullable=False, default=False)
    denial_ground = db.Column(db.String(30))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("vat_invoices", lazy=True, cascade="all, delete-orphan"))
    vat_rate = db.relationship("VATRate")
    import_batch = db.relationship("VATImportBatch", backref=db.backref("invoices", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")

    @property
    def total_amount(self):
        return (self.taxable_amount or 0.0) + (self.vat_amount or 0.0)

    @property
    def kind(self):
        return vat_kind_code(self.direction, self.doc_type)

    @property
    def is_credit_note(self):
        return (self.doc_type or "Invoice") == "Credit Note"

    @property
    def kind_label(self):
        return VAT_KIND_LABELS.get(self.kind) or VAT_INVOICE_DIRECTION_LABELS.get(self.direction, self.direction)

    def __repr__(self):
        return f"<VATInvoice {self.direction} {self.invoice_number or ''} {self.vat_amount} engagement={self.engagement_id}>"


class VATPeriod(db.Model):
    """One VAT month of an engagement's VAT register (e.g. September 2026) that
    the team has finished computing. A row exists only once a month has been
    marked "Computation done"; a month with no row is "In progress".

    A month that is done is LOCKED: no VAT document dated in it can be added,
    deleted, re-tagged or imported until someone with the reopen permission
    (Supervisor, Partner or Admin by default) reopens it. Reopening keeps the
    row (is_done False) with who/when/why, so the history of a month that was
    reopened is not lost; marking it done again overwrites the completion
    details, while `times_reopened` keeps the running count.

    The month a document belongs to is the month of its own invoice_date;
    documents with no date cannot be placed in a month and are listed apart."""
    __tablename__ = "vat_period"
    __table_args__ = (db.UniqueConstraint("engagement_id", "year", "month", name="uq_vat_period"),)
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    is_done = db.Column(db.Boolean, nullable=False, default=True)
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reopened_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reopened_at = db.Column(db.DateTime)
    reopen_reason = db.Column(db.String(300))
    times_reopened = db.Column(db.Integer, nullable=False, default=0)

    engagement = db.relationship("Engagement", backref=db.backref("vat_periods", lazy=True, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reopened_by = db.relationship("User", foreign_keys=[reopened_by_id])

    @property
    def key(self):
        return vat_period_key(self.year, self.month)

    @property
    def label(self):
        return vat_period_label(self.year, self.month)

    def __repr__(self):
        return f"<VATPeriod {self.key} done={self.is_done} engagement={self.engagement_id}>"


def vat_period_key(year, month):
    """'2026-09' - the form used in URLs and as the lookup key."""
    return f"{int(year):04d}-{int(month):02d}"


def vat_period_label(year, month):
    return date(int(year), int(month), 1).strftime("%B %Y")


def parse_vat_period_key(text):
    """(year, month) for '2026-09', else None."""
    try:
        y, m = str(text).split("-")
        y, m = int(y), int(m)
        date(y, m, 1)
        return y, m
    except (ValueError, TypeError, AttributeError):
        return None


def vat_period_of(day):
    """(year, month) a document dated `day` belongs to, or None if undated."""
    return (day.year, day.month) if day else None


def locked_vat_months(engagement_id):
    """{(year, month)} of this engagement's months marked 'Computation done'."""
    rows = VATPeriod.query.filter_by(engagement_id=engagement_id, is_done=True).all()
    return {(r.year, r.month) for r in rows}


def vat_locked_months_among(engagement_id, days):
    """Sorted list of the locked (year, month) pairs that any of `days` falls in."""
    locked = locked_vat_months(engagement_id)
    if not locked:
        return []
    return sorted({vat_period_of(d) for d in days if d and vat_period_of(d) in locked})


TAX_INFO_REQUEST_STATUSES = ["Outstanding", "Received", "Not Applicable"]


class TaxInformationRequest(db.Model):
    """One line of the Dynamic Information Request List (Module 5) - the
    tax equivalent of a PBC (prepared-by-client) list: an item requested
    from the client, who it was requested from, and whether it's come back
    yet. "Dynamic" in the module spec means preparer-curated per engagement
    rather than a single fixed checklist - hence a plain add/edit/delete
    list here rather than a seeded template."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    tax_head = db.Column(db.String(80))
    item_description = db.Column(db.String(300), nullable=False)
    requested_from = db.Column(db.String(150))
    status = db.Column(db.String(20), default="Outstanding")
    date_requested = db.Column(db.Date)
    date_received = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_information_requests", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<TaxInformationRequest {self.item_description!r} status={self.status} engagement={self.engagement_id}>"


TAX_RETURN_STATUSES = ["Not Started", "In Preparation", "Filed", "Assessed", "Paid", "Overdue"]


class TaxReturnRecord(db.Model):
    """One tax return/filing being tracked through Tax Execution & Working
    Papers (Module 7) - CIT, VAT, WHT, PAYE, Customs, or a Provisional Tax
    (QPD) instalment, all sharing this one model via the `tax_head`
    discriminator rather than five/six separate bespoke tables (mirrors how
    Secretarial's Execution Plan reuses SubstantiveProcedureItem rather
    than a new model - see the module comment at the top of this section).
    assessed_amount/reconciliation_notes cover the E-Filing & Assessment
    Reconciliation workflow (Module 10) - comparing what was filed/paid
    against the tax authority's own assessment, on the same row rather than
    a separate model."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    tax_head = db.Column(db.String(80), nullable=False)
    period_label = db.Column(db.String(80))  # e.g. "2026 Q1", "May 2026", "2026 Annual"
    due_date = db.Column(db.Date)
    filed_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="Not Started")
    amount_due = db.Column(db.Float)
    amount_paid = db.Column(db.Float)
    assessed_amount = db.Column(db.Float)
    reference = db.Column(db.String(100))  # ZIMRA/e-filing acknowledgement reference
    reconciliation_notes = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_return_records", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < date.today() and self.status not in ("Filed", "Assessed", "Paid"))

    @property
    def assessment_variance(self):
        if self.assessed_amount is None or self.amount_due is None:
            return None
        return self.assessed_amount - self.amount_due

    def __repr__(self):
        return f"<TaxReturnRecord {self.tax_head} {self.period_label} status={self.status} engagement={self.engagement_id}>"


class TaxResearchLogEntry(db.Model):
    """One entry in the Technical Research Log (Module 7B - Tax Advisory
    Workflow, only shown when Engagement.has_tax_advisory is True): a
    technical question researched, the conclusion reached, and the
    authorities relied on."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    question = db.Column(db.Text)
    research_notes = db.Column(db.Text)
    conclusion = db.Column(db.Text)
    references = db.Column(db.Text)  # legislation/case law/rulings relied on
    prepared_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    prepared_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_research_log", lazy=True, cascade="all, delete-orphan"))
    prepared_by = db.relationship("User")

    def __repr__(self):
        return f"<TaxResearchLogEntry {self.topic!r} engagement={self.engagement_id}>"


class TaxStructuringOption(db.Model):
    """One option within a Structuring Options Analysis scenario comparison
    (Module 7B, advisory-only) - several rows on the same engagement, one
    per option under consideration, compared side by side on the Tax tab."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    option_name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    estimated_tax_impact = db.Column(db.Float)
    risk_rating = db.Column(db.String(10))  # "Low" | "Medium" | "High"
    recommended = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_structuring_options", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<TaxStructuringOption {self.option_name!r} engagement={self.engagement_id}>"


TAX_DISPUTE_STAGES = [
    "Notice Received", "Objection Lodged", "Appeal - Fiscal Appeal Court",
    "Appeal - Higher Court", "Resolved - Favourable", "Resolved - Unfavourable", "Withdrawn",
]


class TaxDispute(db.Model):
    """One tax dispute being tracked through the Tax Dispute Management
    workflow (Module 7B, advisory-only) - from the first notice received
    through objection/appeal to resolution."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    tax_head = db.Column(db.String(80))
    issue_description = db.Column(db.Text, nullable=False)
    stage = db.Column(db.String(40), default="Notice Received")
    amount_in_dispute = db.Column(db.Float)
    next_action = db.Column(db.String(300))
    next_action_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_disputes", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")

    @property
    def is_resolved(self):
        return self.stage.startswith("Resolved") or self.stage == "Withdrawn"

    def __repr__(self):
        return f"<TaxDispute {self.tax_head} stage={self.stage} engagement={self.engagement_id}>"


# ---------- ZIMRA Audits & Correspondence ----------
# A firm-wide register of every ZIMRA audit, query, assessment, objection and
# payment-plan matter a client has, with the letters sent and received. A case
# belongs to a Client and can optionally be linked to the engagement it is being
# handled under (then it follows that engagement's confidentiality).

ZIMRA_CASE_TYPES = [
    "Desk audit", "Field audit (comprehensive)", "Issue-specific audit", "VAT audit / refund verification",
    "PAYE / payroll audit", "Withholding tax review", "Query / information request", "Assessment",
    "Objection", "Appeal", "Condonation / waiver of penalties", "Payment plan / TCC", "Other",
]
ZIMRA_CASE_STATUSES = ["Open", "Awaiting client", "Awaiting ZIMRA", "Objection lodged", "Appeal lodged", "Closed"]
ZIMRA_CORRESPONDENCE_KINDS = [
    "Notice of audit", "Information request", "Assessment / findings", "Objection", "Response / submission",
    "Meeting / call note", "Payment plan", "Tax Clearance Certificate (TCC)", "Other",
]
ZIMRA_DIRECTIONS = ["Incoming from ZIMRA", "Outgoing to ZIMRA", "Internal note"]
ZIMRA_PLAN_STATUSES = ["Proposed", "Agreed", "In default", "Completed"]
ZIMRA_TCC_STATUSES = ["Not applied", "Applied", "Issued", "Declined", "Expired"]
ZIMRA_CURRENCIES = ["USD", "ZWG"]
