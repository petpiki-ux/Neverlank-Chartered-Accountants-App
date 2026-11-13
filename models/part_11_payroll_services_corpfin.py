"""models, part 11 of 12.  Holds: Payslip, PayslipItem, WhistleblowerService, WhistleblowerCase, WhistleblowerCaseNote, TrainingSession, TrainingAttendee, TrainingMaterial, CFModel, CFScenario, CFValuation, CFProject ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

class Payslip(db.Model):
    """One employee's payslip for one period. Every figure is stored
    (never recomputed on the fly for display) so a payslip a Partner has
    already looked at - or downloaded - never silently changes; a
    "Recalculate" action explicitly re-derives these from the employee's
    current basic salary, this payslip's line items and the current
    PayrollTaxSettings/PayrollTaxBand rows, matching the firm's own choice
    of "auto-calculated but editable"."""
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("payroll_period.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("payroll_employee.id"), nullable=False)

    basic_salary = db.Column(db.Float, default=0.0)
    allowances_total = db.Column(db.Float, default=0.0)
    # Exempt Income line items (see PAYSLIP_ITEM_CATEGORIES/PayslipItem) -
    # counted toward gross/net pay like an allowance, but never toward
    # taxable_income - ZIMRA's own PAYE method has a dedicated "deduct
    # exempt income" step (e.g. a bonus exemption) before allowable
    # deductions/PAYE, kept separate here from allowances_total so it's
    # shown as its own line for that reason.
    exempt_income_total = db.Column(db.Float, default=0.0)
    taxable_income = db.Column(db.Float, default=0.0)
    gross_pay = db.Column(db.Float, default=0.0)

    # PAYE computed on taxable_income, before Tax Credit line items are
    # applied - kept alongside paye_tax (the figure AFTER credits, which is
    # what's actually charged) purely so the payslip can show its workings;
    # see payroll_calc.calculate_payslip.
    paye_before_credits = db.Column(db.Float, default=0.0)
    tax_credits_total = db.Column(db.Float, default=0.0)
    paye_tax = db.Column(db.Float, default=0.0)
    aids_levy = db.Column(db.Float, default=0.0)
    nssa_employee = db.Column(db.Float, default=0.0)
    nssa_employer = db.Column(db.Float, default=0.0)  # employer cost, informational - not deducted from the employee
    # Accident Prevention and Workers' Compensation Scheme (APWCS) - 1.25% of
    # Basic Salary (PayrollTaxSettings.apwcs_pct), payable to NSSA by the
    # employer alongside (but separately from) nssa_employer above;
    # informational only, like nssa_employer - never deducted from or shown
    # on the employee's own payslip.
    apwcs = db.Column(db.Float, default=0.0)
    other_deductions_total = db.Column(db.Float, default=0.0)

    net_pay = db.Column(db.Float, default=0.0)
    # False once anyone has edited a figure by hand since it was last
    # (re)calculated - so the payslip clearly shows it's no longer a fresh,
    # untouched auto-calculation.
    is_auto_calculated = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)

    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    employee = db.relationship("PayrollEmployee", backref=db.backref("payslips", lazy=True, cascade="all, delete-orphan"))
    generated_by = db.relationship("User")
    items = db.relationship("PayslipItem", backref="payslip", lazy=True, cascade="all, delete-orphan", order_by="PayslipItem.id")

    @property
    def total_deductions(self):
        return (self.paye_tax or 0.0) + (self.aids_levy or 0.0) + (self.nssa_employee or 0.0) + (self.other_deductions_total or 0.0)

    def __repr__(self):
        return f"<Payslip employee={self.employee_id} period={self.period_id}>"


class PayslipItem(db.Model):
    """An ad-hoc line on one payslip, beyond basic salary and the statutory
    PAYE/AIDS levy/NSSA lines - see PAYSLIP_ITEM_CATEGORIES:
      - Allowance: added to gross AND (if `taxable`) taxable income - e.g. a
        housing allowance. `taxable` only matters for this category.
      - Deduction: subtracted from net pay only (never touches taxable
        income, PAYE or NSSA Insurable Earnings) - e.g. an advance recovery,
        a union subscription, or the EMPLOYEE's own medical aid contribution
        if it's taken straight off their pay (NSSA is still calculated on
        gross pay before this deduction, per the NSSA Act/SI 393 of 1993 -
        see payroll_calc.py).
      - Exempt Income: added to gross/net pay like an Allowance, but NEVER
        added to taxable income, whatever `taxable` says - e.g. a bonus
        exemption (ZIMRA's own PAYE method has a dedicated "deduct exempt
        income" step for this, separate from ordinary allowable
        deductions).
      - Tax Credit: subtracted directly from PAYE payable (after the tax
        tables, before AIDS Levy) - never touches gross pay, taxable income
        or NSSA - e.g. an elderly/blind/disabled person's or medical
        expenses credit.

    `nssa_applicable` (Allowance/Exempt Income only - a Deduction/Tax Credit
    never enters NSSA Insurable Earnings regardless) controls whether THIS
    item's amount counts toward NSSA Insurable Earnings, separately from
    whether it counts toward gross/net pay or PAYE taxable income (`taxable`
    above). Defaults to True (unchanged behaviour) because most Allowance/
    Exempt Income items genuinely are "regular cash earnings" that DO count
    toward Insurable Earnings. The one case this exists for: the EMPLOYER's
    own medical aid (or similar benefit) contribution, entered here so it
    shows on the payslip and/or counts toward gross pay for PAYE purposes,
    but is a non-cash fringe benefit to the employer that must be EXCLUDED
    from NSSA Insurable Earnings (NSSA Act [Chapter 17:04] / SI 393 of 1993)
    - untick "Counts toward NSSA?" for that specific line. See
    payroll_calc.calculate_payslip for exactly where this is applied."""
    id = db.Column(db.Integer, primary_key=True)
    payslip_id = db.Column(db.Integer, db.ForeignKey("payslip.id"), nullable=False)
    category = db.Column(db.String(20), nullable=False, default="Allowance")
    label = db.Column(db.String(150), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    taxable = db.Column(db.Boolean, default=True)
    nssa_applicable = db.Column(db.Boolean, default=True)
    # "employee" = the employee's own share, taken off their pay and owed to the medical aid society;
    # "employer" = the employer's share, also owed to the society. Read by payroll_medical.py when posting the bill.
    medical_aid = db.Column(db.String(10))

    def __repr__(self):
        return f"<PayslipItem {self.label!r} {self.category} {self.amount}>"



# ======================================================================
# Neverlank Anonymous Whistle-blower Services
# ======================================================================
# One WhistleblowerService per client engagement of type "Neverlank
# Anonymous Whistle-blower Services" (the subscription: channels, contacts,
# fee, and the secret link to the client's public anonymous reporting
# page), and any number of WhistleblowerCase rows under it (one per report
# received). Reporter identity is NEVER stored: a portal report records no
# name, IP address or browser details - the reporter just gets a reference
# code plus a one-time follow-up key (only a hash of which is kept) to check
# progress and reply anonymously.

WB_CHANNELS = ["Web portal", "Email", "Phone / hotline", "WhatsApp / SMS", "In person", "Postal", "Other"]
WB_CATEGORIES = [
    ("fraud", "Fraud / financial misconduct"),
    ("corruption", "Corruption / bribery"),
    ("theft", "Theft / misappropriation of assets"),
    ("procurement", "Procurement irregularities"),
    ("reporting", "Financial reporting / accounting irregularities"),
    ("conflict", "Conflict of interest"),
    ("harassment", "Harassment / discrimination"),
    ("safety", "Health, safety & environment"),
    ("abuse_of_authority", "Abuse of authority / policy breach"),
    ("data", "Data privacy / cybersecurity breach"),
    ("other", "Other"),
]
WB_CATEGORY_LABELS = dict(WB_CATEGORIES)
WB_SEVERITIES = ["Low", "Medium", "High", "Critical"]
WB_STATUSES = [
    "New", "Under review", "Investigating", "Referred to client",
    "Closed - substantiated", "Closed - unsubstantiated", "Closed - insufficient information",
]
WB_REPORTING_FREQUENCIES = ["Quarterly", "Half-yearly", "Annual"]


class WhistleblowerService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    service_start = db.Column(db.Date)
    service_end = db.Column(db.Date)
    annual_fee = db.Column(db.Float)
    currency = db.Column(db.String(10), default="USD")
    # Secret URL segment of the client's public anonymous reporting page
    # (/speak-up/<public_token>); regenerating it kills the old link.
    public_token = db.Column(db.String(64), unique=True)
    portal_enabled = db.Column(db.Boolean, default=False)
    portal_welcome_text = db.Column(db.Text)  # shown on the public page, e.g. what to include in a report
    hotline_phone = db.Column(db.String(60))
    hotline_email = db.Column(db.String(120))
    hotline_whatsapp = db.Column(db.String(60))
    other_channels = db.Column(db.Text)
    client_contact_name = db.Column(db.String(120))  # who at the client receives reports (e.g. audit committee chair)
    client_contact_role = db.Column(db.String(120))
    client_contact_email = db.Column(db.String(120))
    reporting_frequency = db.Column(db.String(20), default="Quarterly")
    notes = db.Column(db.Text)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("whistleblower_service", uselist=False))
    updated_by = db.relationship("User")

    def __repr__(self):
        return f"<WhistleblowerService engagement={self.engagement_id}>"


class WhistleblowerCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, index=True)
    reference = db.Column(db.String(30), nullable=False, unique=True)  # anonymous, e.g. WB-3F9A21C4
    followup_key_hash = db.Column(db.String(255))  # hash of the one-time key the reporter uses to check back
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    channel = db.Column(db.String(40), default="Web portal")
    category = db.Column(db.String(30), default="other")  # WB_CATEGORIES key
    severity = db.Column(db.String(20), default="Medium")
    subject = db.Column(db.String(200))
    description = db.Column(db.Text)
    status = db.Column(db.String(40), default="New")
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    outcome = db.Column(db.Text)
    closed_at = db.Column(db.DateTime)
    source = db.Column(db.String(10), default="portal")  # "portal" (reporter) or "staff" (logged by Neverlank staff)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    engagement = db.relationship("Engagement", backref=db.backref("whistleblower_cases", lazy="dynamic"))
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    notes = db.relationship("WhistleblowerCaseNote", backref="case", cascade="all, delete-orphan",
                            order_by="WhistleblowerCaseNote.created_at")

    @property
    def is_closed(self):
        return bool(self.status) and self.status.startswith("Closed")

    @property
    def category_label(self):
        return WB_CATEGORY_LABELS.get(self.category, self.category or "Other")

    @property
    def days_to_close(self):
        if self.closed_at and self.received_at:
            return max(0, (self.closed_at - self.received_at).days)
        return None

    @property
    def has_unread_reporter_message(self):
        """True when the newest note is a follow-up from the reporter that
        staff haven't answered yet."""
        return bool(self.notes) and self.notes[-1].kind == "from_reporter"

    def __repr__(self):
        return f"<WhistleblowerCase {self.reference}>"


class WhistleblowerCaseNote(db.Model):
    """One entry in a case's log. kind: "internal" (staff-only working note),
    "to_reporter" (visible to the reporter on the anonymous status page) or
    "from_reporter" (anonymous follow-up the reporter sent)."""
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("whistleblower_case.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), default="internal")
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # NULL for reporter messages

    created_by = db.relationship("User")


# ======================================================================
# Neverlank Training Services
# ======================================================================

TRAINING_MODES = ["In person", "Online", "Hybrid"]
TRAINING_STATUSES = ["Planned", "Open for registration", "Completed", "Cancelled"]
TRAINING_ATTENDANCE = ["Registered", "Attended", "Absent", "Cancelled"]


class TrainingSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    trainer_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # internal trainer, optional
    trainer_name = db.Column(db.String(200))  # external trainer(s), or a display override
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    start_time = db.Column(db.String(10))
    end_time = db.Column(db.String(10))
    mode = db.Column(db.String(20), default="In person")
    venue = db.Column(db.String(300))  # venue, or the online meeting link
    fee_per_participant = db.Column(db.Float)
    currency = db.Column(db.String(10), default="USD")
    seats = db.Column(db.Integer)
    cpd_hours = db.Column(db.Float)  # CPD hours a participant earns by attending
    status = db.Column(db.String(30), default="Planned")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("training_sessions", lazy="dynamic"))
    trainer_user = db.relationship("User", foreign_keys=[trainer_user_id])
    attendees = db.relationship("TrainingAttendee", backref="session", cascade="all, delete-orphan",
                                order_by="TrainingAttendee.full_name")
    materials = db.relationship("TrainingMaterial", backref="session", cascade="all, delete-orphan",
                                order_by="TrainingMaterial.uploaded_at")

    @property
    def trainer_display(self):
        parts = []
        if self.trainer_user:
            parts.append(self.trainer_user.name)
        if self.trainer_name:
            parts.append(self.trainer_name)
        return ", ".join(parts) or None

    @property
    def active_attendees(self):
        return [a for a in self.attendees if a.status != "Cancelled"]

    @property
    def seats_left(self):
        if not self.seats:
            return None
        return max(0, self.seats - len(self.active_attendees))

    @property
    def attended_count(self):
        return sum(1 for a in self.attendees if a.status == "Attended")

    @property
    def certificates_issued(self):
        return sum(1 for a in self.attendees if a.certificate_number)

    @property
    def total_cpd_hours_awarded(self):
        return round(sum(a.cpd_hours_awarded or 0.0 for a in self.attendees), 2)

    @property
    def expected_revenue(self):
        if self.fee_per_participant is None:
            return None
        return round(self.fee_per_participant * len(self.active_attendees), 2)

    @property
    def fees_collected(self):
        return round(sum(a.fee_paid or 0.0 for a in self.attendees), 2)

    def average_rating(self, field):
        values = [getattr(a, field) for a in self.attendees if getattr(a, field)]
        return round(sum(values) / len(values), 2) if values else None

    @property
    def feedback_count(self):
        return sum(1 for a in self.attendees if a.rating_overall)

    def __repr__(self):
        return f"<TrainingSession {self.title}>"


class TrainingAttendee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("training_session.id"), nullable=False, index=True)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(60))
    organisation = db.Column(db.String(200))
    designation = db.Column(db.String(200))
    status = db.Column(db.String(20), default="Registered")  # TRAINING_ATTENDANCE
    fee_paid = db.Column(db.Float)
    certificate_number = db.Column(db.String(60))
    certificate_issued_at = db.Column(db.Date)
    cpd_hours_awarded = db.Column(db.Float)
    # Participant evaluation, 1 (poor) - 5 (excellent)
    rating_overall = db.Column(db.Integer)
    rating_trainer = db.Column(db.Integer)
    rating_content = db.Column(db.Integer)
    rating_materials = db.Column(db.Integer)
    feedback_comments = db.Column(db.Text)
    feedback_at = db.Column(db.DateTime)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)


class TrainingMaterial(db.Model):
    """A file (slides, handout, exercise) attached to one training session -
    stored as an ordinary engagement Document (category "Training Materials")
    so it also appears under the engagement's Documents tab."""
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("training_session.id"), nullable=False, index=True)
    document_id = db.Column(db.Integer, db.ForeignKey("document.id"), nullable=False)
    title = db.Column(db.String(200))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    document = db.relationship("Document")


# ===========================================================================
# Corporate Finance & Business Advisory
#
# Forecast models (three statements), their scenarios and valuations, and
# capital-budgeting projects. All the numbers live in JSON columns that the
# pure-Python engines (cf_engine.py / cf_tools.py) read and write; the tables
# only hold who/what/when, ownership and status. Access follows the client
# (and the engagement, when one is linked - same confidentiality rule as
# invoices and quotations).
# ===========================================================================

CF_STATUSES = ["Draft", "Final"]


def _json_load(text, default):
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


class CFModel(db.Model):
    """A driver-based financial forecast (income statement, balance sheet, cash flow) for a client."""
    __tablename__ = "cf_model"
    id = db.Column(db.Integer, primary_key=True)
    model_number = db.Column(db.String(30), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    name = db.Column(db.String(200), nullable=False)
    purpose = db.Column(db.Text)  # what the model is for / basis of preparation
    status = db.Column(db.String(20), nullable=False, default="Draft")
    assumptions_json = db.Column(db.Text)
    historical_json = db.Column(db.Text)
    actuals_json = db.Column(db.Text)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    finalised_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    finalised_at = db.Column(db.DateTime)

    client = db.relationship("Client", backref=db.backref("cf_models", lazy=True, cascade="all, delete-orphan", order_by="CFModel.id.desc()"))
    engagement = db.relationship("Engagement", backref=db.backref("cf_models", lazy=True, order_by="CFModel.id.desc()"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    finalised_by = db.relationship("User", foreign_keys=[finalised_by_id])
    scenarios = db.relationship("CFScenario", backref="model", lazy=True, cascade="all, delete-orphan",
                                order_by="CFScenario.sort_order, CFScenario.id")
    valuations = db.relationship("CFValuation", backref="model", lazy=True, cascade="all, delete-orphan",
                                 order_by="CFValuation.id")

    @property
    def is_final(self):
        return self.status == "Final"

    @property
    def assumptions(self):
        return _json_load(self.assumptions_json, {})

    @property
    def historical(self):
        return _json_load(self.historical_json, {})

    @property
    def actuals(self):
        return _json_load(self.actuals_json, {})

    def set_json(self, field, value):
        setattr(self, field + "_json", json.dumps(value))

    @property
    def currency(self):
        return (self.assumptions or {}).get("currency") or "USD"


class CFScenario(db.Model):
    """A what-if on a model: a set of adjustments ("shocks") applied to the base assumptions.
    Every model has a Base scenario with no adjustments."""
    __tablename__ = "cf_scenario"
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey("cf_model.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    is_base = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    shocks_json = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def shocks(self):
        return _json_load(self.shocks_json, {})


class CFValuation(db.Model):
    """A saved valuation (WACC, terminal value, bridge, comparables) of a model's forecast, optionally under a scenario."""
    __tablename__ = "cf_valuation"
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey("cf_model.id"), nullable=False)
    scenario_id = db.Column(db.Integer, db.ForeignKey("cf_scenario.id"))
    name = db.Column(db.String(200), nullable=False)
    valuation_date = db.Column(db.Date)
    inputs_json = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    scenario = db.relationship("CFScenario")
    created_by = db.relationship("User")

    @property
    def inputs(self):
        return _json_load(self.inputs_json, {})


class CFProject(db.Model):
    """A capital-budgeting / feasibility study: investment, cash flows, NPV / IRR / payback, plus the written feasibility notes."""
    __tablename__ = "cf_project"
    id = db.Column(db.Integer, primary_key=True)
    project_number = db.Column(db.String(30), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Draft")
    inputs_json = db.Column(db.Text)
    narrative_json = db.Column(db.Text)
    source_model_id = db.Column(db.Integer, db.ForeignKey("cf_model.id"))

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("cf_projects", lazy=True, cascade="all, delete-orphan", order_by="CFProject.id.desc()"))
    engagement = db.relationship("Engagement", backref=db.backref("cf_projects", lazy=True, order_by="CFProject.id.desc()"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    source_model = db.relationship("CFModel")

    @property
    def inputs(self):
        return _json_load(self.inputs_json, {})

    @property
    def narrative(self):
        return _json_load(self.narrative_json, {})


class CFValuationModel(MPSignedMixin, db.Model):
    """A group / sum-of-the-parts valuation made of COMPONENT valuations (uploaded, imported from the app's own valuations, or typed),
    consolidated with ownership %, discounts and premiums, currency conversion, holding-company items and group adjustments. The
    arithmetic is in cf_valmodel.py; this row holds who / what / when and the data (one JSON document). The Managing Partner can
    sign the report; changing the data after signing drops the signature (brand_docs.content_key)."""
    __tablename__ = "cf_valuation_model"
    id = db.Column(db.Integer, primary_key=True)
    vm_number = db.Column(db.String(30), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    name = db.Column(db.String(200), nullable=False)
    currency = db.Column(db.String(6), nullable=False, default="USD")
    valuation_date = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="Draft")
    data_json = db.Column(db.Text)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    finalised_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    finalised_at = db.Column(db.DateTime)

    client = db.relationship("Client", backref=db.backref("cf_valuation_models", lazy=True, cascade="all, delete-orphan", order_by="CFValuationModel.id.desc()"))
    engagement = db.relationship("Engagement", backref=db.backref("cf_valuation_models", lazy=True, order_by="CFValuationModel.id.desc()"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    finalised_by = db.relationship("User", foreign_keys=[finalised_by_id])

    @property
    def is_final(self):
        return self.status == "Final"

    @property
    def data(self):
        return _json_load(self.data_json, {})


class CFMgmtPack(db.Model):
    """A monthly management accounts reporting pack for a client: actuals (trial balance upload, manual capture or modelled from
    the prior year's accounts), budget (from a forecast model or typed in) and prior-year comparatives, all held in one JSON
    document (see cf_ma.py for its shape). The arithmetic is in cf_ma.py; this row only holds who/what/when and the data."""
    __tablename__ = "cf_mgmt_pack"
    id = db.Column(db.Integer, primary_key=True)
    pack_number = db.Column(db.String(30), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    name = db.Column(db.String(200), nullable=False)
    currency = db.Column(db.String(6), nullable=False, default="USD")
    status = db.Column(db.String(20), nullable=False, default="Draft")
    data_json = db.Column(db.Text)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    finalised_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    finalised_at = db.Column(db.DateTime)

    client = db.relationship("Client", backref=db.backref("cf_packs", lazy=True, cascade="all, delete-orphan", order_by="CFMgmtPack.id.desc()"))
    engagement = db.relationship("Engagement", backref=db.backref("cf_packs", lazy=True, order_by="CFMgmtPack.id.desc()"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    finalised_by = db.relationship("User", foreign_keys=[finalised_by_id])

    @property
    def is_final(self):
        return self.status == "Final"

    @property
    def data(self):
        return _json_load(self.data_json, {})

    def set_data(self, value):
        self.data_json = json.dumps(value)



# ---------- Section 16(1) prohibited deductions engine (prohibited_deductions.py) ----------

TAX_DEDUCTIBILITY_STATUSES = ["ALLOWABLE", "PROHIBITED_SECTION_16", "PARTIALLY_PROHIBITED", "REVIEW_REQUIRED"]


class ProhibitedDeductionRule(db.Model):
    """One expense type and how the Income Tax Act's section 16(1) treats it. The clause reference and the treatment
    are DATA, not code: the Act is amended by almost every Finance Act and published summaries disagree on lettering,
    so a Partner can correct a clause label or switch a treatment here, and must mark a rule verified against the
    current Act. Until then every schedule line that relies on the rule is flagged as unverified."""
    __tablename__ = "prohibited_deduction_rule"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False)
    group = db.Column(db.String(60))
    clause_label = db.Column(db.String(60))      # e.g. "16(1)(m)" - editable
    treatment = db.Column(db.String(30), nullable=False, default="full")  # see prohibited_deductions.TREATMENTS
    note = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    verified_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    verified_at = db.Column(db.DateTime)
    verified_note = db.Column(db.String(300))     # e.g. "Checked to Income Tax Act as amended by Finance Act 2026"
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])

    @property
    def is_verified(self):
        return self.verified_at is not None


class ProhibitedDeductionSetup(db.Model):
    """Engagement-level inputs the rules need: the passenger-motor-vehicle cap, the thin-capitalisation figures, whether
    the entity is a local mining operation of a foreign parent, and the other lines of the tax computation."""
    __tablename__ = "prohibited_deduction_setup"
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    pmv_cap = db.Column(db.Float)                 # leave blank until entered; the Act's figure is amended from time to time
    mining_foreign_parent = db.Column(db.Boolean, default=False)   # older reading of 16(1)(r); no longer used
    assoc_fee_a = db.Column(db.Float)        # 16(1)(r): A = total expenditure qualifying for deduction under section 15
    assoc_fee_b = db.Column(db.Float)        # B = fees, administration and management paid outside Zimbabwe
    assoc_fee_c = db.Column(db.Float)        # C = expenditure qualifying under section 15(2)(f)(i)
    assoc_fee_pretrade = db.Column(db.Boolean, default=False)   # 0.75% instead of 1% before trade starts / in a period of non-production
    thin_cap_ratio = db.Column(db.Float)          # debt : equity limit (3 under the rule as published) - entered, not assumed
    related_debt = db.Column(db.Float, default=0.0)        # debt advanced or guaranteed by an associate or foreign parent
    qualifying_equity = db.Column(db.Float, default=0.0)
    related_interest = db.Column(db.Float, default=0.0)    # interest charged to profit or loss on that debt
    net_profit_override = db.Column(db.Float)     # used only when there is no trial balance to take profit before tax from
    notes = db.Column(db.Text)
    engagement = db.relationship("Engagement", backref=db.backref("prohibited_setup", uselist=False, cascade="all, delete-orphan"))


class ProhibitedDeductionItem(db.Model):
    """One expense (a ledger line or a manual entry) tested against section 16(1). status/clause/addback are stored
    each time the item, the rule or the setup changes, so every expense carries its own tax_deductibility_status."""
    __tablename__ = "prohibited_deduction_item"
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    entry_date = db.Column(db.Date)
    voucher_ref = db.Column(db.String(100))
    description = db.Column(db.String(250), nullable=False)
    gl_account_id = db.Column(db.Integer, db.ForeignKey("gl_account.id"))
    journal_line_id = db.Column(db.Integer, db.ForeignKey("journal_entry_line.id"))
    expense_type = db.Column(db.String(60), nullable=False, default="ordinary_business_expense")
    amount = db.Column(db.Float, default=0.0)               # as charged to profit or loss
    charged_to_pl = db.Column(db.Boolean, default=True, nullable=False)
    is_insured = db.Column(db.Boolean, default=False)
    reimbursed_amount = db.Column(db.Float, default=0.0)
    business_necessity = db.Column(db.Boolean, default=False)     # travel between two trades
    linked_to_exempt = db.Column(db.Boolean, default=False)
    prohibited_percent = db.Column(db.Float)                      # apportionment, where the rule allows part
    vehicle_cost = db.Column(db.Float)
    source = db.Column(db.String(20), default="Manual")           # Manual | Ledger | Import
    status = db.Column(db.String(30), default="ALLOWABLE")
    clause_label = db.Column(db.String(60))
    addback_amount = db.Column(db.Float, default=0.0)
    disallowed_amount = db.Column(db.Float, default=0.0)
    eval_note = db.Column(db.String(400))
    rule_verified = db.Column(db.Boolean, default=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("prohibited_items", lazy=True, cascade="all, delete-orphan", order_by="ProhibitedDeductionItem.id"))
    gl_account = db.relationship("GLAccount")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


# ---------------------------------------------------------------------------
# Firm Finance - the firm's own Asset Register and Financial Statements
#
# The firm's statements (income statement, balance sheet, cash flow, equity
# and notes) are built by firm_statements.py from three sources:
#   * what the app already knows (invoices, credit notes, receipts, advances,
#     the Expense register) - revenue, expenses, receivables, payables,
#     customer advances - on an ACCRUAL basis;
#   * the firm's own Asset Register below (cost, depreciation, disposals);
#   * the balances a Partner/Admin enters for each year-end (FirmYearEnd +
#     FirmBalanceLine): bank and cash, loans, share capital, VAT, tax payable,
#     dividends, opening retained earnings, and anything else the app cannot
#     know.
# All figures are in USD; a ZWG item is translated at the rate entered on the
# year-end (ZWG per 1 USD) and that rate is disclosed in the notes.
# ---------------------------------------------------------------------------
FIRM_ASSET_METHODS = ["Straight-line", "Reducing balance", "Not depreciated"]
FIRM_FRAMEWORKS = [("ifrs_for_smes", "IFRS for SMEs"), ("full_ifrs", "Full IFRS")]

# The categories a year-end balance line can be filed under (financials.FS_CATEGORIES codes). Revenue, receivables from
# invoices, supplier bills, customer advances, PPE and depreciation come from the app and the Asset Register instead.
FIRM_ENTRY_CATEGORIES = [
    ("cash", "Cash and cash equivalents (bank, cash on hand, mobile money)"),
    ("trade_receivables", "Other receivables and prepayments"),
    ("inventories", "Inventories (stationery, consumables)"),
    ("other_current_assets", "Other current assets"),
    ("intangible_assets", "Intangible assets (software, licences)"),
    ("investment_property", "Investment property"),
    ("long_term_investments", "Long-term investments"),
    ("deferred_tax_asset", "Deferred tax asset"),
    ("other_noncurrent_assets", "Other non-current assets"),
    ("trade_payables", "Accruals and other payables"),
    ("other_current_liabilities", "Other current liabilities (VAT payable, PAYE, deposits)"),
    ("current_tax_payable", "Income tax payable at year-end"),
    ("short_term_borrowings", "Short-term borrowings / bank overdraft"),
    ("short_term_provisions", "Short-term provisions"),
    ("long_term_borrowings", "Long-term borrowings"),
    ("deferred_tax_liability", "Deferred tax liability"),
    ("long_term_provisions", "Long-term provisions"),
    ("other_noncurrent_liabilities", "Other non-current liabilities"),
    ("share_capital", "Share capital"),
    ("share_premium", "Share premium"),
    ("other_reserves", "Other reserves"),
    ("retained_earnings", "Retained earnings brought forward (opening)"),
    ("dividends_paid", "Dividends declared / paid in the year"),
    ("other_income", "Other income (interest received, sundry)"),
    ("admin_expenses", "Other administrative expenses (not in the Expense register)"),
    ("other_expenses", "Other expenses"),
    ("finance_costs", "Finance costs (interest on loans)"),
    ("income_tax_expense", "Income tax expense for the year"),
    ("oci_items", "Other comprehensive income, net of tax"),
]
FIRM_ENTRY_CATEGORY_CODES = [c[0] for c in FIRM_ENTRY_CATEGORIES]


class FirmAssetClass(db.Model):
    """A class of the firm's own fixed assets with its depreciation policy (e.g. Computer equipment, straight-line, 33.33%)."""
    __tablename__ = "firm_asset_class"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    depreciation_method = db.Column(db.String(30), nullable=False, default="Straight-line")
    rate_percent = db.Column(db.Float)            # per annum; if empty the useful life is used
    useful_life_years = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class FirmAsset(db.Model):
    """One item in the firm's Asset Register. Depreciation is calculated by the app (whole months, from the month of
    acquisition up to but not including the month of disposal) unless an accumulated depreciation figure brought forward
    from the old books is entered with the date it is stated at."""
    __tablename__ = "firm_asset"
    id = db.Column(db.Integer, primary_key=True)
    asset_code = db.Column(db.String(40), unique=True)
    description = db.Column(db.String(250), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey("firm_asset_class.id"))
    date_acquired = db.Column(db.Date, nullable=False, default=date.today)
    cost = db.Column(db.Float, nullable=False, default=0.0)         # USD
    residual_value = db.Column(db.Float, default=0.0)
    supplier = db.Column(db.String(150))
    location = db.Column(db.String(120))
    serial_number = db.Column(db.String(120))
    notes = db.Column(db.Text)
    acc_dep_bf = db.Column(db.Float)                # accumulated depreciation brought forward from the old books
    acc_dep_bf_date = db.Column(db.Date)            # ... stated at the end of the month containing this date
    disposed_on = db.Column(db.Date)
    disposal_proceeds = db.Column(db.Float, default=0.0)
    disposal_note = db.Column(db.String(250))
    # how it was paid for (the correcting leg of the purchase): "" = not stated (left in the clearing account), "bank" = paid from a
    # Cash Book account (the app keeps one Cash Book payment for it, acq_cash_entry_id), "credit" = owed to a supplier,
    # "shareholder" = provided by a shareholder / director, "other" = any other ledger account
    acq_method = db.Column(db.String(12))
    acq_account_id = db.Column(db.Integer, db.ForeignKey("firm_account.id"))      # ledger account for credit / shareholder / other
    acq_cash_account_id = db.Column(db.Integer, db.ForeignKey("cash_account.id"))
    acq_cash_entry_id = db.Column(db.Integer)                                     # the Cash Book payment the app made for it
    # the same for the sale proceeds: "", "bank", "credit" (owed by the buyer), "other"
    disp_method = db.Column(db.String(12))
    disp_account_id = db.Column(db.Integer, db.ForeignKey("firm_account.id"))
    disp_cash_account_id = db.Column(db.Integer, db.ForeignKey("cash_account.id"))
    disp_cash_entry_id = db.Column(db.Integer)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset_class = db.relationship("FirmAssetClass", backref=db.backref("assets", lazy=True))
    created_by = db.relationship("User")


class FirmYearEnd(db.Model):
    """A reporting date for the firm's financial statements, with the figures that are entered rather than derived."""
    __tablename__ = "firm_year_end"
    id = db.Column(db.Integer, primary_key=True)
    period_end = db.Column(db.Date, nullable=False, unique=True)
    zwg_rate = db.Column(db.Float)                  # ZWG per 1 USD, used for every ZWG item in this period
    tax_rate_percent = db.Column(db.Float, default=24.72)   # only used for the indicative-tax hint on the entry page
    reporting_framework = db.Column(db.String(20), default="ifrs_for_smes")
    opening_nil = db.Column(db.Boolean, default=False, nullable=False)   # first period: the opening position is nil
    cash_manual = db.Column(db.Boolean, default=False, nullable=False)  # True = cash entered by hand; False = taken from the Cash Book
    tax_manual = db.Column(db.Boolean, default=False, nullable=False)   # True = tax figures entered by hand; False = from the Tax pages
    tax_loss_bf = db.Column(db.Float)               # assessed loss brought forward; empty = the previous year-end's loss carried forward
    extra_note = db.Column(db.Text)                 # printed as an additional note
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship("User")
    lines = db.relationship("FirmBalanceLine", backref="year_end", cascade="all, delete-orphan",
                            order_by="FirmBalanceLine.id")


class FirmBalanceLine(db.Model):
    """One entered figure for a year-end, filed under an IAS 1 category. `amount` is the natural balance (an asset,
    liability, income or expense is entered as a positive number) in `currency`."""
    __tablename__ = "firm_balance_line"
    id = db.Column(db.Integer, primary_key=True)
    year_end_id = db.Column(db.Integer, db.ForeignKey("firm_year_end.id"), nullable=False)
    fs_category = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(10), nullable=False, default="USD")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Firm Finance - Cash Book
#
# The firm's cash and bank accounts. Money already recorded elsewhere is brought in AUTOMATICALLY (client receipts,
# client advances and refunds, paid expenses, invoices marked paid before receipting existed) and is never stored a
# second time: cashbook.py reads it when needed. Only what the app cannot know is entered here (CashEntry: owner
# contributions, loans, bank charges, transfers between accounts ...). Which account an automatic transaction lands in
# follows the payment method set on each account, then the account's "default" flags; CashAssign records a manual
# move to another account, and CashClear records that a line has appeared on the bank statement (reconciliation).
# ---------------------------------------------------------------------------
CASH_ACCOUNT_KINDS = ["Bank", "Cash", "Mobile money"]
CASH_ENTRY_CATEGORIES = [
    "Owner / shareholder contribution", "Loan received", "Loan repayment", "Dividend paid", "Interest received",
    "Bank charges", "Income tax paid", "VAT paid to ZIMRA", "PAYE and other taxes paid", "Salaries paid", "NSSA contributions paid",
    "Medical aid and other deductions paid",
    "Other receipt", "Other payment", "QPD (provisional tax) paid",
]
CASH_TRANSFER_CATEGORY = "Transfer between accounts"


class CashAccount(db.Model):
    __tablename__ = "cash_account"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    kind = db.Column(db.String(20), nullable=False, default="Bank")
    currency = db.Column(db.String(10), nullable=False, default="USD")
    opening_balance = db.Column(db.Float, nullable=False, default=0.0)   # the balance at the START of opening_date
    opening_date = db.Column(db.Date, nullable=False, default=date.today)  # earlier transactions are not counted
    methods = db.Column(db.String(200))        # payment methods that land here, comma separated (e.g. "Bank transfer,Card")
    default_in = db.Column(db.Boolean, default=False, nullable=False)    # money in with no matching method lands here
    default_out = db.Column(db.Boolean, default=False, nullable=False)   # money out (paid expenses, refunds) lands here
    reference = db.Column(db.String(120))      # bank and account number
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def method_list(self):
        return [m.strip() for m in (self.methods or "").split(",") if m.strip()]


class CashEntry(db.Model):
    """A cash book entry typed in by hand (what the app has no other record of). A transfer between two accounts is
    two entries sharing a transfer_id (out of one account, into the other)."""
    __tablename__ = "cash_entry"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("cash_account.id"), nullable=False)
    entry_date = db.Column(db.Date, nullable=False, default=date.today)
    direction = db.Column(db.String(3), nullable=False, default="in")   # "in" or "out"
    amount = db.Column(db.Float, nullable=False, default=0.0)           # always positive
    category = db.Column(db.String(60), nullable=False, default="Other receipt")
    description = db.Column(db.String(250))
    reference = db.Column(db.String(120))
    transfer_id = db.Column(db.String(36))
    # a payment under a Payroll heading (Medical aid and other deductions paid ...) can name the supplier it went to (the medical aid society):
    # it still only clears the payroll payable (Dr payable / Cr cash) - no bill, no expense, nothing owed on the supplier's account
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"))
    # the balance sheet account (asset, liability or equity) the entry is charged to: Dr it / Cr cash for money out, the reverse for money in
    ledger_account_id = db.Column(db.Integer, db.ForeignKey("firm_account.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ledger_account = db.relationship("FirmAccount", foreign_keys=[ledger_account_id])
    account = db.relationship("CashAccount", backref=db.backref("entries", lazy=True))
    created_by = db.relationship("User")
    supplier = db.relationship("Supplier", foreign_keys=[supplier_id], backref=db.backref("payroll_cash_payments", lazy=True))


class CashAssign(db.Model):
    """An automatic transaction moved by hand to a different account than the rules would choose."""
    __tablename__ = "cash_assign"
    id = db.Column(db.Integer, primary_key=True)
    source_key = db.Column(db.String(60), nullable=False, unique=True)   # e.g. "receipt:12"
    account_id = db.Column(db.Integer, db.ForeignKey("cash_account.id"), nullable=False)
    account = db.relationship("CashAccount")


class CashClear(db.Model):
    """A line that has appeared on the bank statement (reconciled), and the statement date it cleared on."""
    __tablename__ = "cash_clear"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("cash_account.id"), nullable=False)
    source_key = db.Column(db.String(60), nullable=False)
    cleared_on = db.Column(db.Date, nullable=False)
    __table_args__ = (db.UniqueConstraint("account_id", "source_key", name="uq_cash_clear"),)


# ---------------------------------------------------------------------------
# Firm Finance - Tax (capital allowances, income tax computation, QPD, deferred tax)
#
# Every rate here is DATA the Partner can edit (the published sources disagree and the Acts change); a rule starts
# "not verified" and the Partner marks it verified against the current Act. Nothing here is a hard-coded ZIMRA figure.
# ---------------------------------------------------------------------------
FIRM_TAX_METHODS = [
    ("SIA", "Special initial allowance (a % of cost a year)"),
    ("WT_SL", "Wear and tear, straight-line on cost"),
    ("WT_RB", "Wear and tear, reducing balance"),
    ("NONE", "No capital allowance"),
]
FIRM_TAX_LINE_TYPES = [
    ("addback", "Add back (expense not deductible, income taxable)"),
    ("deduction", "Deduction"),
    ("exempt", "Exempt or non-taxable income"),
]


class FirmTaxAssetRule(db.Model):
    """The capital allowance treatment of one asset class."""
    __tablename__ = "firm_tax_asset_rule"
    id = db.Column(db.Integer, primary_key=True)
    asset_class_id = db.Column(db.Integer, db.ForeignKey("firm_asset_class.id"), nullable=False, unique=True)
    method = db.Column(db.String(10), nullable=False, default="WT_SL")
    wt_rate_percent = db.Column(db.Float, default=0.0)       # wear and tear, per annum
    sia_percent = db.Column(db.Float, default=25.0)          # special initial allowance, per annum
    sia_years = db.Column(db.Integer, default=4)
    sia_first_percent = db.Column(db.Float)                  # optional: the first year's SIA rate when it differs (Fourth Schedule para 9: 50% then 25% twice)
    cost_cap = db.Column(db.Float)                           # allowances are worked on no more than this cost (e.g. passenger vehicles)
    prorata_first_year = db.Column(db.Boolean, default=False, nullable=False)  # wear and tear in the year of purchase by months owned
    verified = db.Column(db.Boolean, default=False, nullable=False)
    note = db.Column(db.String(250))
    asset_class = db.relationship("FirmAssetClass")


class FirmTaxCategoryRule(db.Model):
    """The share of an Expense-register category the Income Tax Act does not allow (0 = fully deductible)."""
    __tablename__ = "firm_tax_category_rule"
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(60), nullable=False, unique=True)
    disallowed_percent = db.Column(db.Float, nullable=False, default=0.0)
    note = db.Column(db.String(250))


class FirmTaxLine(db.Model):
    """A hand-entered line of one year-end's income tax computation."""
    __tablename__ = "firm_tax_line"
    id = db.Column(db.Integer, primary_key=True)
    year_end_id = db.Column(db.Integer, db.ForeignKey("firm_year_end.id"), nullable=False)
    item_type = db.Column(db.String(12), nullable=False, default="addback")
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    year_end = db.relationship("FirmYearEnd", backref=db.backref("tax_lines", cascade="all, delete-orphan", lazy=True))


class FirmDeferredItem(db.Model):
    """A temporary difference other than property, plant and equipment (a provision, prepaid income ...)."""
    __tablename__ = "firm_deferred_item"
    id = db.Column(db.Integer, primary_key=True)
    year_end_id = db.Column(db.Integer, db.ForeignKey("firm_year_end.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    accounting_amount = db.Column(db.Float, nullable=False, default=0.0)   # carrying amount in the statements
    tax_base_amount = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    year_end = db.relationship("FirmYearEnd", backref=db.backref("deferred_items", cascade="all, delete-orphan", lazy=True))


# ---------------------------------------------------------------------------
# Firm Finance - Ledger (double entry and journals)
#
# The firm's chart of accounts (FirmAccount) and manual journals (FirmJournal / FirmJournalLine). Everything the app
# already records - invoices, credit notes, receipts, advances, expenses, cash book entries, depreciation, disposals and
# tax - is turned into balanced debit/credit postings by firm_ledger.py when a page is opened; those derived postings are
# never stored, so nothing is keyed twice. Only the manual journals are stored. A posted journal is never edited or
# deleted: it is reversed by a new journal. FirmPeriodLock closes the books to new or changed journals up to a date.
# ---------------------------------------------------------------------------
FIRM_ACCOUNT_TYPES = ["Asset", "Liability", "Equity", "Income", "Expense"]
FIRM_JOURNAL_STATUSES = ["draft", "posted"]


class FirmAccount(db.Model):
    __tablename__ = "firm_account"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    account_type = db.Column(db.String(12), nullable=False, default="Asset")
    fs_category = db.Column(db.String(40))          # statement line it feeds; None / "excluded" = not in the statements
    system_key = db.Column(db.String(80), unique=True)   # set on accounts the posting engine writes to (e.g. "CASH:3", "EXP:Fuel")
    parent_id = db.Column(db.Integer, db.ForeignKey("firm_account.id"))   # a sub-account (e.g. one asset class under Property, plant and equipment)
    description = db.Column(db.String(250))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_system(self):
        return bool(self.system_key)

    @property
    def label(self):
        return f"{self.code} {self.name}"


class FirmJournal(db.Model):
    __tablename__ = "firm_journal"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), nullable=False, unique=True)     # JE-2026-0001
    journal_date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(250), nullable=False)
    reference = db.Column(db.String(120))
    status = db.Column(db.String(10), nullable=False, default="draft")
    reverses_id = db.Column(db.Integer, db.ForeignKey("firm_journal.id"))
    alloc_key = db.Column(db.String(120))      # set on journals made by the clearing-account auto-allocation (see firm_ledger)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    posted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    posted_at = db.Column(db.DateTime)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    posted_by = db.relationship("User", foreign_keys=[posted_by_id])
    reverses = db.relationship("FirmJournal", remote_side=[id], backref=db.backref("reversals", lazy=True))
    lines = db.relationship("FirmJournalLine", backref="journal", cascade="all, delete-orphan",
                            order_by="FirmJournalLine.order, FirmJournalLine.id")

    @property
    def total_debit(self):
        return round(sum(l.debit or 0.0 for l in self.lines), 2)

    @property
    def total_credit(self):
        return round(sum(l.credit or 0.0 for l in self.lines), 2)

    @property
    def is_balanced(self):
        return abs(self.total_debit - self.total_credit) < 0.005 and self.total_debit > 0

    @property
    def reversed_by(self):
        return next((r for r in self.reversals if r.status == "posted"), None)


class FirmJournalLine(db.Model):
    __tablename__ = "firm_journal_line"
    id = db.Column(db.Integer, primary_key=True)
    journal_id = db.Column(db.Integer, db.ForeignKey("firm_journal.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("firm_account.id"), nullable=False)
    debit = db.Column(db.Float, nullable=False, default=0.0)
    credit = db.Column(db.Float, nullable=False, default=0.0)
    narration = db.Column(db.String(250))
    order = db.Column(db.Integer, default=0)
    account = db.relationship("FirmAccount")


class FirmPeriodLock(db.Model):
    """The books are closed to journals dated on or before `locked_through`. The latest row is the one in force;
    older rows are the history (who closed or reopened the books, and when)."""
    __tablename__ = "firm_period_lock"
    id = db.Column(db.Integer, primary_key=True)
    locked_through = db.Column(db.Date)             # None = nothing locked (the books were reopened)
    note = db.Column(db.String(250))
    set_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    set_at = db.Column(db.DateTime, default=datetime.utcnow)
    set_by = db.relationship("User")


# ---------------------------------------------------------------------------
# Firm Finance - Exchange rates register
#
# Dated rates (units of a foreign currency per 1 USD) with their source. A year-end's closing rate is the latest rate dated
# on or before the year-end; the year-end keeps its own stored rate (FirmYearEnd.zwg_rate), which can be filled from the
# register or typed over it.
# ---------------------------------------------------------------------------
class FirmExchangeRate(db.Model):
    __tablename__ = "firm_exchange_rate"
    id = db.Column(db.Integer, primary_key=True)
    currency = db.Column(db.String(10), nullable=False, default="ZWG")
    rate_date = db.Column(db.Date, nullable=False)
    rate = db.Column(db.Float, nullable=False)                 # units of `currency` per 1 USD
    source = db.Column(db.String(120))                         # e.g. "RBZ interbank rate"
    note = db.Column(db.String(250))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("currency", "rate_date", name="uq_firm_fx_rate"),)
    created_by = db.relationship("User")
