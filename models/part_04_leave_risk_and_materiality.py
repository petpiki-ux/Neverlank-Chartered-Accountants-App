"""models, part 4 of 12.  Holds: LeaveType, LeaveBalance, LeaveRecord, StaffAllocation, RiskAssessment, MaterialityCalculation, QualitativeMaterialityFactor, EntityUnderstanding, EntityUnderstandingChecklistItem, AnalyticalReview, AnalyticalReviewLine, ITChangePlanItem ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

# ---------------------------------------------------------------------------
# Leave Management
#
# Ties into Time Sheets above (a TimeEntry can record leave taken instead of
# hours worked - see TimeEntry.leave_type_id/leave_days) and Payroll's
# PayrollEmployee (LeaveBalance keys off PayrollEmployee, not User directly,
# since date_joined - needed to compute accrual from completed months of
# service - lives there; see hr.py's leave routes for how a logged-in User
# is matched to their own PayrollEmployee record).
#
# Zimbabwe's Labour Act [Chapter 28:01] s.14A sets Annual/Vacation Leave's
# statutory minimum: 2.5 calendar days per completed month of continuous
# service (30/year), accumulating up to a 90-day ceiling (no further accrual
# once reached, until leave is taken), but normally only TAKEN after 1 year
# of continuous service. Sick Leave, Special/Compassionate Leave and
# Maternity Leave are each a fixed annual (or per-event) entitlement instead
# - non-accumulative, so unused days simply lapse rather than rolling into
# the next year. See LEAVE_TYPES_STATUTORY_ZW/seed.seed_leave_types for the
# exact pre-loaded figures - every field is firm-editable, since a firm's
# own policy or a collective bargaining agreement may be more generous than
# the statutory minimum (never less).
#
# Only Annual/Vacation Leave is an obligation the firm actively tracks a
# running balance for, per employee (confirmed with the Managing Partner:
# accrual continues to run from each employee's own date_joined, same as
# before). Sick/Special/Compassionate/Maternity leave are kept as statutory
# PROVISIONS - their figures are defined once on the Leave Types screen and
# can still be captured on a Time Sheet for record-keeping, but the firm
# does not maintain a per-employee LeaveBalance (no accrual, no running
# total, no cap enforcement) for them - see leave_calc.sync_all_balances_for_employee
# and hr._add_leave_entry, both of which gate this on LeaveType.is_accumulative.
LEAVE_TYPES_STATUTORY_ZW = [
    # (name, accrual_days_per_month, annual_entitlement_days, max_accumulation_days,
    #  is_accumulative, full_pay_days, half_pay_pct, min_service_months_to_take, notes)
    (
        "Annual/Vacation Leave", 2.5, 30.0, 90.0, True, None, None, 12,
        "Labour Act [Chapter 28:01] s.14A: 2.5 calendar days per completed month of continuous "
        "service (30/year). Accumulates up to a 90-day cap - no further accrual once reached, "
        "until leave is taken. Normally only TAKEN after 1 year of continuous service (it still "
        "accrues from day one) unless the firm's own policy allows earlier taking.",
    ),
    (
        "Sick Leave", 0.0, 180.0, None, False, 90.0, 50.0, 0,
        "180 days per leave year: the first 90 days on full pay, the next 90 on half pay (a medical "
        "certificate would normally be required, though this app doesn't enforce that). "
        "Non-accumulative - granted afresh each leave year, unused days lapse.",
    ),
    (
        "Special/Compassionate Leave", 0.0, 12.0, None, False, None, None, 0,
        "12 days per calendar year, for bereavement, court attendance or trade union business. "
        "Non-accumulative - granted afresh each leave year, unused days lapse.",
    ),
    (
        "Maternity Leave", 0.0, 98.0, None, False, None, None, 0,
        "98 consecutive days, full pay, regardless of tenure or number of previous pregnancies. "
        "Modelled here as a 98-day annual entitlement rather than a rolling balance - "
        "non-accumulative, since it's a per-event entitlement, not something that accrues monthly.",
    ),
]


class LeaveType(db.Model):
    """A firm-editable leave category - pre-loaded with Zimbabwe's statutory
    minimums (see LEAVE_TYPES_STATUTORY_ZW/seed.seed_leave_types) but every
    field can be edited, since a firm's own policy or a collective
    bargaining agreement may be more generous than the statutory minimum.

    `is_accumulative` distinguishes the two accrual models the Labour Act
    actually uses: Annual/Vacation Leave accrues monthly and carries over
    (up to `max_accumulation_days`), while Sick/Special/Maternity leave are
    each a fixed `annual_entitlement_days` granted afresh every leave year -
    see leave_calc.sync_leave_balance for exactly how each is kept current.
    It ALSO decides whether this leave type gets a tracked per-employee
    LeaveBalance at all: True (e.g. Annual/Vacation Leave) means the firm
    is treating it as an obligation it actively accrues and tracks a running
    balance for; False (e.g. Sick/Special/Maternity) means it's kept as a
    statutory PROVISION instead - still definable here and still capturable
    on a Time Sheet, but with no per-employee balance, accrual or cap (see
    leave_calc.sync_all_balances_for_employee and hr._add_leave_entry).

    `full_pay_days`/`half_pay_pct` model Sick Leave's 90-full-pay/90-half-pay
    split (None for a leave type that's either all full pay or doesn't
    distinguish); `min_service_months_to_take` is Annual Leave's "only after
    1 year of continuous service" rule (0 for a leave type with no such
    qualifying period)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    accrual_days_per_month = db.Column(db.Float, default=0.0)
    annual_entitlement_days = db.Column(db.Float)  # None = uncapped
    max_accumulation_days = db.Column(db.Float)  # None = no cap
    is_accumulative = db.Column(db.Boolean, default=True, nullable=False)
    full_pay_days = db.Column(db.Float)  # None = the whole annual_entitlement_days is at full pay
    half_pay_pct = db.Column(db.Float, default=50.0)
    min_service_months_to_take = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    order = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)

    def __repr__(self):
        return f"<LeaveType {self.name!r}>"


class LeaveBalance(db.Model):
    """One PayrollEmployee's running balance for one LeaveType - created
    on demand (see leave_calc.get_or_create_balance) the first time it's
    needed, not seeded up front.

    `brought_forward` is a one-time, manually-entered OPENING balance (e.g.
    migrating from a previous system or spreadsheet) - set once via the
    Leave screen and never touched again by the automatic accrual logic in
    leave_calc.sync_leave_balance, which only ever adds to `accrued_days`
    (Annual/Vacation Leave) or resets it for a new leave year (Sick/
    Special/Maternity). `leave_year` is the calendar year a NON-accumulative
    type's current `accrued_days`/`taken_days` apply to - rolled over
    automatically the first time this balance is touched in a later year.
    `last_accrual_date` is the last calendar date accrual was calculated
    through, for an accumulative type, so re-running the sync never
    double-counts an already-completed month."""
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("payroll_employee.id"), nullable=False)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_type.id"), nullable=False)
    brought_forward = db.Column(db.Float, default=0.0)
    accrued_days = db.Column(db.Float, default=0.0)
    taken_days = db.Column(db.Float, default=0.0)
    leave_year = db.Column(db.Integer)  # only meaningful for a non-accumulative LeaveType
    last_accrual_date = db.Column(db.Date)  # only meaningful for an accumulative LeaveType
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("PayrollEmployee")
    leave_type = db.relationship("LeaveType")

    @property
    def current_balance(self):
        return round((self.brought_forward or 0.0) + (self.accrued_days or 0.0) - (self.taken_days or 0.0), 2)

    def __repr__(self):
        return f"<LeaveBalance employee={self.employee_id} type={self.leave_type_id} balance={self.current_balance}>"


class LeaveRecord(db.Model):
    """Leave taken by a staff member, entered directly on HR & Admin > Leave (by the staff member for themselves, or by a
    Supervisor/Partner/Admin for anyone) - from `start_date` to `end_date`, `days` being the working days (Mon-Fri, or what the
    person typed, e.g. 0.5 for a half day). For an accumulative leave type (Annual/Vacation) the days come off the employee's
    LeaveBalance.taken_days when recorded and go back when the record is deleted (hr.record_leave / hr.delete_leave_record).
    Leave captured on a timesheet (TimeEntry) is separate and still works; the Leave page lists both so nothing is missed."""
    __tablename__ = "leave_record"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("payroll_employee.id"), nullable=False)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_type.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    days = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("PayrollEmployee")
    leave_type = db.relationship("LeaveType")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])


class StaffAllocation(db.Model):
    """A staff member booked onto an engagement for a specific date range,
    optionally at less than 100% of their time. Powers the HR & Admin >
    Planner staffing grid (who's booked where, and who's over/under
    allocated in a given week) as well as the audit timetable's clash
    detection."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    allocation_pct = db.Column(db.Integer, default=100)  # % of the staff member's time, per day, over this range
    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("allocations", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User")

    def overlaps(self, range_start, range_end):
        return self.start_date <= range_end and self.end_date >= range_start

    def __repr__(self):
        return f"<StaffAllocation user={self.user_id} engagement={self.engagement_id} {self.start_date}..{self.end_date}>"


class RiskAssessment(db.Model):
    """The system-based Risk Assessment for one engagement: answer the
    questionnaire (RISK_LIKELIHOOD_QUESTIONS / RISK_IMPACT_QUESTIONS above
    for most engagement types, or FORENSIC_RISK_LIKELIHOOD_QUESTIONS /
    FORENSIC_RISK_IMPACT_QUESTIONS above for an Investigative Engagement -
    see likelihood_answers/impact_answers below - each 1-5) and the app
    computes likelihood, impact, score and an overall Low/Medium/High
    rating - no manual likelihood/impact picking. One row per engagement;
    re-answering the questionnaire updates it in place rather than creating
    a new one, since risk should be kept current rather than accumulated as
    a history.
    """
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)

    # Likelihood factor answers (1-5 each) - see RISK_LIKELIHOOD_QUESTIONS
    q_complexity = db.Column(db.Integer)
    q_controls = db.Column(db.Integer)
    q_environment_change = db.Column(db.Integer)
    q_fraud_indicators = db.Column(db.Integer)
    q_prior_issues = db.Column(db.Integer)

    # Impact factor answers (1-5 each) - see RISK_IMPACT_QUESTIONS
    q_significance = db.Column(db.Integer)
    q_magnitude = db.Column(db.Integer)
    q_going_concern = db.Column(db.Integer)
    q_related_party = db.Column(db.Integer)
    q_consequences = db.Column(db.Integer)

    # Forensic-specific answers (1-5 each), used instead of the ten fields
    # above on an Investigative Engagement - see FORENSIC_RISK_LIKELIHOOD_
    # QUESTIONS / FORENSIC_RISK_IMPACT_QUESTIONS above. Kept as separate
    # columns (rather than reusing q_complexity etc. under a different
    # meaning) so a completed audit-style assessment and a completed
    # forensic assessment can never be confused with each other.
    q_fraud_incentive = db.Column(db.Integer)
    q_fraud_opportunity = db.Column(db.Integer)
    q_fraud_rationalization = db.Column(db.Integer)
    q_fraud_override = db.Column(db.Integer)
    q_fraud_intel = db.Column(db.Integer)

    q_scheme_revenue_gl = db.Column(db.Integer)
    q_scheme_asset_misappropriation = db.Column(db.Integer)
    q_scheme_procurement_vendor = db.Column(db.Integer)
    q_scheme_payroll_expenses = db.Column(db.Integer)
    q_scheme_significance = db.Column(db.Integer)

    # Business Intelligence and IT Engagements-specific answers (1-5 each),
    # used instead of the ten standard fields above on that engagement type -
    # see BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS / BUSINESS_IT_RISK_IMPACT_
    # QUESTIONS above. Kept as separate columns, same reasoning as the
    # forensic-specific fields above.
    q_threat_exposure = db.Column(db.Integer)
    q_control_maturity = db.Column(db.Integer)
    q_environment_complexity = db.Column(db.Integer)
    q_third_party_cloud = db.Column(db.Integer)
    q_aml_customer_channel = db.Column(db.Integer)

    q_data_sensitivity = db.Column(db.Integer)
    q_continuity_dependency = db.Column(db.Integer)
    q_regulatory_legal = db.Column(db.Integer)
    q_reputational = db.Column(db.Integer)
    q_overall_significance = db.Column(db.Integer)

    # Secretarial-specific answers (1-5 each), used instead of the ten
    # standard fields above on a "Secretarial" engagement - see
    # SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS / SECRETARIAL_RISK_IMPACT_
    # QUESTIONS above. Kept as separate columns, same reasoning as the
    # forensic/BI-IT-specific fields above. Note q_overall_significance is
    # shared with the Business Intelligence and IT Engagements impact
    # questions above (both use it as their final "overall" question, and
    # since only one engagement type's question set is ever active for a
    # given record, reusing the column is safe).
    q_filing_compliance_history = db.Column(db.Integer)
    q_governance_procedural_rigor = db.Column(db.Integer)
    q_ownership_capital_complexity = db.Column(db.Integer)
    q_transactional_activity = db.Column(db.Integer)
    q_registry_standing = db.Column(db.Integer)

    q_penalty_exposure = db.Column(db.Integer)
    q_governance_invalidity = db.Column(db.Integer)
    q_solvency_director_liability = db.Column(db.Integer)
    q_encumbrance_control_risk = db.Column(db.Integer)

    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("risk_assessment", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def _is_forensic(self):
        return bool(self.engagement and self.engagement.type == "Investigative Engagement")

    @property
    def _is_business_it(self):
        return bool(self.engagement and self.engagement.type == "Business Intelligence and IT Engagements")

    @property
    def _is_secretarial(self):
        return bool(self.engagement and self.engagement.type == "Secretarial")

    @property
    def likelihood_questions(self):
        """The question set actually in play for this record - the Fraud
        Triangle questionnaire on an Investigative Engagement, the cyber/IT/
        AML-CFT control questionnaire on a Business Intelligence and IT
        Engagement, the COBE-based four-pillar secretarial questionnaire on
        a Secretarial engagement, the ordinary audit questionnaire
        otherwise. Everything below (likelihood_answers, is_complete,
        likelihood/impact/score/rating) is driven from this and
        impact_questions, so the four engagement flavours never mix."""
        if self._is_forensic:
            return FORENSIC_RISK_LIKELIHOOD_QUESTIONS
        if self._is_business_it:
            return BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS
        if self._is_secretarial:
            return SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS
        return RISK_LIKELIHOOD_QUESTIONS

    @property
    def impact_questions(self):
        if self._is_forensic:
            return FORENSIC_RISK_IMPACT_QUESTIONS
        if self._is_business_it:
            return BUSINESS_IT_RISK_IMPACT_QUESTIONS
        if self._is_secretarial:
            return SECRETARIAL_RISK_IMPACT_QUESTIONS
        return RISK_IMPACT_QUESTIONS

    @property
    def likelihood_answers(self):
        return [getattr(self, field) for field, _, _ in self.likelihood_questions]

    @property
    def impact_answers(self):
        return [getattr(self, field) for field, _, _ in self.impact_questions]

    @property
    def is_complete(self):
        return all(a is not None for a in self.likelihood_answers + self.impact_answers)

    @property
    def likelihood(self):
        answers = [a for a in self.likelihood_answers if a is not None]
        return round(sum(answers) / len(answers)) if answers else None

    @property
    def impact(self):
        answers = [a for a in self.impact_answers if a is not None]
        return round(sum(answers) / len(answers)) if answers else None

    @property
    def score(self):
        l, i = self.likelihood, self.impact
        return (l or 0) * (i or 0)

    @property
    def rating(self):
        return _score_rating(self.score) if self.is_complete else None

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<RiskAssessment engagement={self.engagement_id} rating={self.rating}>"


class MaterialityCalculation(db.Model):
    """A simple planning materiality calculator for one engagement: enter a
    few key financial figures and a basis, and the app computes suggested
    overall materiality, performance materiality and a "clearly trivial"
    threshold using adjustable benchmark percentages. These percentages are
    common starting points in practice, not a substitute for professional
    judgement or your firm's own methodology - review and adjust them for
    each engagement.
    """
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)

    total_revenue = db.Column(db.Float)
    profit_before_tax = db.Column(db.Float)
    total_assets = db.Column(db.Float)

    revenue_pct = db.Column(db.Float, default=1.0)
    pbt_pct = db.Column(db.Float, default=5.0)
    assets_pct = db.Column(db.Float, default=1.0)

    basis = db.Column(db.String(20), default="highest")  # revenue | pbt | assets | highest | lowest
    performance_pct = db.Column(db.Float, default=75.0)  # % of overall materiality
    trivial_pct = db.Column(db.Float, default=5.0)  # % of overall materiality

    # Whether financial-figure-based (quantitative) materiality is actually
    # relevant to this engagement - defaults True (unchanged behaviour for
    # every existing engagement). A preparer can mark it False for an
    # engagement where a dollar threshold doesn't drive materiality the way
    # it does on a financial statement audit (e.g. a Business Intelligence
    # and IT Engagement, or one with unusually sparse financial figures) -
    # see the Qualitative Materiality Index (QualitativeMaterialityFactor
    # below) as the alternative basis in that case.
    quantitative_important = db.Column(db.Boolean, default=True, nullable=False)

    notes = db.Column(db.Text)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # preparer - set whenever the calculation is (re)saved
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("materiality", uselist=False, cascade="all, delete-orphan"))
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def revenue_materiality(self):
        return self.total_revenue * self.revenue_pct / 100 if self.total_revenue else None

    @property
    def pbt_materiality(self):
        return self.profit_before_tax * self.pbt_pct / 100 if self.profit_before_tax else None

    @property
    def assets_materiality(self):
        return self.total_assets * self.assets_pct / 100 if self.total_assets else None

    @property
    def overall_materiality(self):
        candidates = {
            "revenue": self.revenue_materiality,
            "pbt": self.pbt_materiality,
            "assets": self.assets_materiality,
        }
        values = [v for v in candidates.values() if v is not None]
        if not values:
            return None
        if self.basis in candidates and candidates[self.basis] is not None:
            return candidates[self.basis]
        if self.basis == "lowest":
            return min(values)
        return max(values)  # "highest" (default) or a stale/invalid basis value

    @property
    def performance_materiality(self):
        om = self.overall_materiality
        return om * self.performance_pct / 100 if om is not None else None

    @property
    def trivial_threshold(self):
        om = self.overall_materiality
        return om * self.trivial_pct / 100 if om is not None else None

    def __repr__(self):
        return f"<MaterialityCalculation engagement={self.engagement_id}>"


# The Qualitative Materiality Index - a lightweight, judgement-based
# alternative/complement to the dollar-figure-driven MaterialityCalculation
# above, for the (common in practice) cases where a matter can be material
# regardless of its size: a small misstatement that turns a profit into a
# loss, reveals a regulatory breach, or exposes a related-party dealing.
# Rated per factor (Low/Medium/High, or unrated) rather than averaged into
# a single score - qualitative materiality is conventionally driven by
# whichever factor is most significant, not by a blend of all of them, so
# the "index" (see QualitativeMaterialityFactor.overall_rating_for) is the
# highest rating recorded across the factors below rather than a mean.
QUALITATIVE_MATERIALITY_FACTORS = [
    ("nature_sensitivity", "Nature and sensitivity of the matter",
     "e.g. related party dealings, fraud or suspected fraud, an illegal or possibly illegal act, a breach of covenant or regulatory requirement."),
    ("regulatory_legal_impact", "Effect on compliance with laws, regulations or contractual requirements",
     "e.g. loan covenants, licence conditions, statutory filing requirements, AML/CFT or data protection obligations."),
    ("trend_consistency", "Effect on trends, consistency or comparability",
     "e.g. changes a profit into a loss, reverses a positive trend, or breaks comparability with the prior period."),
    ("stakeholder_reliance", "Effect on a key stakeholder's understanding or decision-making",
     "e.g. lenders, regulators, shareholders, or the investing/donor public relying on the information."),
    ("reputational_impact", "Potential reputational or public-interest impact",
     "e.g. media/public attention, loss of stakeholder confidence, or damage to the entity's standing if the matter went unaddressed."),
]
QUALITATIVE_MATERIALITY_FACTOR_KEYS = {f[0] for f in QUALITATIVE_MATERIALITY_FACTORS}
QUALITATIVE_MATERIALITY_RATINGS = ["Low", "Medium", "High"]
_QUALITATIVE_MATERIALITY_RATING_ORDER = {"Low": 1, "Medium": 2, "High": 3}


class QualitativeMaterialityFactor(db.Model):
    """One rated factor (see QUALITATIVE_MATERIALITY_FACTORS above) in an
    engagement's Qualitative Materiality Index - one row per
    (engagement, factor_key), created on demand as each factor is rated
    rather than all seeded up front, since a preparer may only ever rate
    the factors actually relevant to this engagement."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    factor_key = db.Column(db.String(50), nullable=False)
    rating = db.Column(db.String(10))  # "Low" | "Medium" | "High" | "" (not yet assessed)
    notes = db.Column(db.Text)

    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("engagement_id", "factor_key", name="uq_qual_materiality_engagement_factor"),)

    engagement = db.relationship("Engagement", backref=db.backref("qualitative_materiality_factors", lazy=True, cascade="all, delete-orphan"))
    updated_by = db.relationship("User")

    @staticmethod
    def overall_rating_for(factors):
        """The Qualitative Materiality Index for a list of
        QualitativeMaterialityFactor rows - the highest rating among them
        (see the module note above), or None if none has been rated yet."""
        rated = [f.rating for f in factors if f.rating in _QUALITATIVE_MATERIALITY_RATING_ORDER]
        if not rated:
            return None
        return max(rated, key=lambda r: _QUALITATIVE_MATERIALITY_RATING_ORDER[r])

    def __repr__(self):
        return f"<QualitativeMaterialityFactor engagement={self.engagement_id} {self.factor_key}={self.rating}>"


class EntityUnderstanding(db.Model):
    """Obtain an understanding of the client's business and industry - a
    structured, system-guided form (see ENTITY_UNDERSTANDING_FIELDS above)
    rather than one free-text box, so nothing gets missed. One row per
    engagement; saving again updates it in place."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)

    nature_of_entity = db.Column(db.Text)
    industry_environment = db.Column(db.Text)
    accounting_policies = db.Column(db.Text)
    objectives_strategies_risks = db.Column(db.Text)
    performance_measurement = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("entity_understanding", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def field_values(self):
        return [getattr(self, field) for field, _, _ in ENTITY_UNDERSTANDING_FIELDS]

    @property
    def is_complete(self):
        """On an Investigative Engagement (see FORENSIC_ENTITY_UNDERSTANDING_
        CHECKLIST_ITEMS above) or a Business Intelligence and IT Engagement
        (see BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS above), the
        five free-text fields aren't shown at all - completion instead means
        every detailed checklist question has been answered. Every other
        engagement type keeps the original all-fields-filled-in check."""
        if self.engagement and self.engagement.type in ("Investigative Engagement", "Business Intelligence and IT Engagements", "Secretarial"):
            items = list(self.checklist_items)
            return bool(items) and all(i.response for i in items)
        return all((v or "").strip() for v in self.field_values)

    @property
    def checklist_assessment(self):
        """Advisory-only read of the detailed checklist, same pattern as
        ClientAcceptance.checklist_assessment - never overrides anything,
        just a plain-language pointer to what still needs attention."""
        items = list(self.checklist_items)
        total = len(items)
        if total == 0:
            return {"label": "No checklist items added yet.", "level": "muted", "flagged": 0, "outstanding": 0, "total": 0}
        flagged = sum(1 for i in items if i.response == "No")
        outstanding = sum(1 for i in items if not i.response)
        if flagged:
            label = f"{flagged} of {total} item{'s' if total != 1 else ''} flagged for follow-up."
            level = "danger"
        elif outstanding:
            label = f"{outstanding} of {total} item{'s' if total != 1 else ''} not yet assessed."
            level = "warning"
        else:
            label = f"All {total} checklist item{'s' if total != 1 else ''} assessed."
            level = "success"
        return {"label": label, "level": level, "flagged": flagged, "outstanding": outstanding, "total": total}

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<EntityUnderstanding engagement={self.engagement_id}>"


class EntityUnderstandingChecklistItem(db.Model):
    """A single tick + comment line within an EntityUnderstanding record -
    the Understanding Business/Assignment equivalent of
    ClientAcceptanceChecklistItem, used for the Forensic Audit questions on
    Investigative Engagements (see FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_
    ITEMS above)."""
    id = db.Column(db.Integer, primary_key=True)
    entity_understanding_id = db.Column(db.Integer, db.ForeignKey("entity_understanding.id"), nullable=False)
    section = db.Column(db.String(150))
    item_text = db.Column(db.Text, nullable=False)
    response = db.Column(db.String(10), default="")  # "" = not yet assessed, "Yes", "No", "N/A"
    comment = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # A "|"-separated list of custom answer choices for this one item,
    # replacing the generic Yes/No/N-A dropdown wherever it's set - used
    # where the question itself is really "pick one of these" rather than a
    # yes/no (e.g. "What is the overall IT environment?" -> On-premise /
    # Cloud / Hybrid, seeded from BUSINESS_IT_ENTITY_UNDERSTANDING_
    # CHECKLIST_ITEMS above). Blank/None (every other checklist item, on
    # every engagement type) keeps the standard Yes/No/N-A dropdown exactly
    # as before - see response_option_list and the entity_checklist_row
    # template macro.
    response_options = db.Column(db.Text)

    entity_understanding = db.relationship(
        "EntityUnderstanding",
        backref=db.backref("checklist_items", lazy=True, cascade="all, delete-orphan", order_by="EntityUnderstandingChecklistItem.order"),
    )
    created_by = db.relationship("User")

    @property
    def response_option_list(self):
        """This item's own custom answer choices, or None to fall back to
        the standard Yes/No/N-A dropdown - see response_options above."""
        if not self.response_options:
            return None
        return [o for o in self.response_options.split("|") if o]

    def __repr__(self):
        return f"<EntityUnderstandingChecklistItem {self.item_text!r} response={self.response!r}>"


# Governance/compliance analytical points for a Secretarial engagement's
# Analytical Review - seeded as plain AnalyticalReviewLine rows (label only,
# no prior/current $ amounts, since secretarial analytical procedures look
# at compliance trends and governance metrics rather than financial
# ratios - see AnalyticalReviewLine.explanation, used here as the narrative
# write-up rather than a variance explanation) when a Secretarial engagement
# ticks Analytical Review as necessary. Mirrors the firm's Secretarial
# Analytical Review guidance under the Companies and Other Business
# Entities Act [Chapter 24:31] (COBE Act).
SECRETARIAL_ANALYTICAL_REVIEW_POINTS = [
    ("Board & Governance Activity Trends", "Meeting frequency vs. business events - compare board/committee meeting frequency against major operational events (acquisitions, debt issuances, capital expenditure)."),
    ("Board & Governance Activity Trends", "Attendance & quorum patterns - verify statutory quorum compliance (e.g. independent director presence under Section 206) and identify persistent non-attendance risks."),
    ("Board & Governance Activity Trends", "Resolution volume vs. statutory triggers - track circular/written resolutions vs. physical meetings to check management isn't bypassing formal board deliberation on restricted matters."),
    ("Ownership & Capital Structure Analytics", "Share allotment vs. authorised capital ratios - variance check between issued share capital, share premium, and authorised limits in the Memorandum/Articles before processing new allotments (Sections 96 and 121)."),
    ("Ownership & Capital Structure Analytics", "Beneficial ownership threshold analysis - analyse voting rights/shareholding aggregation to identify individuals holding more than 20% of the shares or voting rights (Sections 2 and 72)."),
    ("Ownership & Capital Structure Analytics", "Dividend & distribution vs. solvency - cross-analyse cash/reserves against proposed distributions to check compliance with the statutory Solvency and Liquidity Test (Section 102)."),
    ("Statutory Filing & Timeline Variance Analysis", "Filing lag analysis - measure the time gap between corporate triggers (director appointments, share allotments, charge creations) and their actual filing dates with the Registrar."),
    ("Statutory Filing & Timeline Variance Analysis", "Annual Return and AGM timing - track AGM dates against the statutory requirement (once in every twelve months, Section 167(2)) and Annual Return filing against the 21 days after the anniversary of incorporation, registration or re-registration (Section 165(2))."),
    ("Charge & Encumbrance Reconciliation", "Debt/security analytics - compare total registered mortgages, charges and debentures in the Register of Mortgages and Debentures (Section 155) against outstanding borrowing facilities to identify unrecorded or unregistered security (Sections 154-155)."),
]

# Same non-numeric label+narrative treatment as SECRETARIAL_ANALYTICAL_
# REVIEW_POINTS above, but for a Business Intelligence and IT Engagement -
# operational/technical trend indicators rather than financial ratios, since
# an IT Audit's analytical review looks at system-generated metrics (change
# volumes, alert counts, access-review completion) rather than P&L/balance
# sheet variances. Seeded by seed_business_it_analytical_review
# (engagements.py), the IT-Audit equivalent of seed_secretarial_analytical_
# review above.
BUSINESS_IT_ANALYTICAL_REVIEW_POINTS = [
    ("Access Review Completion & Exception Trends", "Periodic user access review completion rate - the proportion of scheduled access reviews actually completed on time this period, and the volume/nature of access removed as a result."),
    ("Access Review Completion & Exception Trends", "Privileged/admin account count trend - period-on-period movement in the number of privileged or administrative accounts, and whether growth is explained by legitimate business change."),
    ("Change/Release Volume & Failure Rate Trends", "Change volume vs. prior period - the number of production changes/releases this period compared with the prior period, and whether the trend is consistent with the entity's stated pace of IT change."),
    ("Change/Release Volume & Failure Rate Trends", "Change failure/rollback rate - the proportion of changes that failed, were rolled back, or required an emergency fix, and whether this rate is trending up or down."),
    ("Security Alert Volume & False-Positive Trends", "Security alert volume trend - period-on-period movement in the number of security alerts/incidents raised, and whether it correlates with any known change in the threat environment or control weakening."),
    ("Security Alert Volume & False-Positive Trends", "False-positive rate trend - the proportion of alerts closed as false positives, and whether persistent high false-positive rates suggest under-tuned detection controls."),
    ("Vulnerability & Patch Remediation Aging", "Open vulnerability aging - the number and age profile of open critical/high vulnerabilities, and whether remediation is keeping pace with new findings."),
    ("Vulnerability & Patch Remediation Aging", "Patch compliance rate trend - the proportion of in-scope systems patched within the entity's defined SLA, period-on-period."),
    ("AML/CFT Alert Volume & Disposition Trends", "Transaction monitoring/screening alert volume trend - period-on-period movement in system-generated AML/CFT alerts, and whether volume changes correlate with business/customer growth or a system/rule change."),
    ("AML/CFT Alert Volume & Disposition Trends", "Alert disposition and SAR/STR rate trend - the proportion of alerts escalated to a suspicious transaction report, and whether this rate has moved materially period-on-period."),
    ("System Availability & Downtime Trends", "System availability/downtime trend - unplanned downtime (hours/incidents) for critical financial or operational systems this period compared with the prior period."),
]


class AnalyticalReview(db.Model):
    """System-based analytical review for one engagement: log current vs
    prior year figures for whichever line items matter and the app computes
    the variance and flags any fluctuation at or above threshold_pct as
    significant, prompting an explanation - rather than a free-form write-up.
    One row per engagement (holds settings + sign-off); its line items live
    in AnalyticalReviewLine below.
    """
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    threshold_pct = db.Column(db.Float, default=10.0)  # flag |variance %| >= this as significant

    # Whether the preparer has ticked Analytical Review as NOT necessary for
    # this assignment - only offered as a choice on Secretarial engagements
    # (per the firm's Secretarial Analytical Review guidance: not every
    # secretarial assignment, e.g. a one-off filing, warrants a full
    # governance/compliance analytical review), but kept as a plain column
    # (default False) rather than a Secretarial-only field so it behaves
    # like every other sign-off flag on this model.
    not_necessary = db.Column(db.Boolean, default=False, nullable=False)
    not_necessary_reason = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("analytical_review", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    lines = db.relationship(
        "AnalyticalReviewLine", backref="review", lazy=True,
        cascade="all, delete-orphan", order_by="AnalyticalReviewLine.id",
    )

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def significant_lines(self):
        return [l for l in self.lines if l.is_significant]

    def __repr__(self):
        return f"<AnalyticalReview engagement={self.engagement_id}>"


class AnalyticalReviewLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    analytical_review_id = db.Column(db.Integer, db.ForeignKey("analytical_review.id"), nullable=False)
    label = db.Column(db.String(400), nullable=False)  # e.g. "Revenue", "Gross profit"
    prior_amount = db.Column(db.Float)
    current_amount = db.Column(db.Float)
    explanation = db.Column(db.Text)
    # "manual" (typed in by hand, the only option before this column existed),
    # "auto" (last written by "Generate from Trial Balance" - see
    # generate_analytical_review_from_trial_balance in engagements.py), or
    # "tb_account" (added via the "pick an account" dropdown on the Add
    # line item form - see add_analytical_review_line - its label and
    # prior/current amounts came directly from one specific
    # TrialBalanceLine rather than a rolled-up IAS 1 category or a typed
    # figure). Regenerating only overwrites the amounts on "auto" lines
    # that share a label with a freshly computed figure, so a
    # manually-typed line, a tb_account-picked line, and any explanation
    # already typed against an auto line, all survive a re-generate.
    source = db.Column(db.String(10), default="manual")

    @property
    def variance_amount(self):
        if self.prior_amount is None or self.current_amount is None:
            return None
        return self.current_amount - self.prior_amount

    @property
    def variance_pct(self):
        if self.prior_amount in (None, 0) or self.current_amount is None:
            return None
        return (self.current_amount - self.prior_amount) / abs(self.prior_amount) * 100

    @property
    def is_significant(self):
        pct = self.variance_pct
        threshold = self.review.threshold_pct if self.review else 10.0
        return pct is not None and abs(pct) >= (threshold or 10.0)

    def __repr__(self):
        return f"<AnalyticalReviewLine {self.label}>"


IT_CHANGE_PLAN_RISK_LEVELS = ["Low", "Medium", "High"]


class ITChangePlanItem(db.Model):
    """A single known or planned system change during the audit period, on a
    Business Intelligence and IT Engagement's Planning tab - the "Change
    Planning" register the team uses to log what's changing in the client's
    environment and decide which changes to pull into the ITGC change-
    management sample tested under Substantive Procedures (see the "Test the
    change and release management process for a sample of system changes..."
    procedure in BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES). Purely a
    planning aid - selecting an item here doesn't create or link to a
    SubstantiveProcedureItem automatically; the team still records the test
    itself under Substantive Procedures once performed."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    change_description = db.Column(db.Text, nullable=False)
    planned_date = db.Column(db.Date)
    risk_level = db.Column(db.String(10))  # "Low" | "Medium" | "High" | "" (not yet assessed)
    selected_for_testing = db.Column(db.Boolean, default=False, nullable=False)
    notes = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship(
        "Engagement",
        backref=db.backref("it_change_plan_items", lazy=True, cascade="all, delete-orphan", order_by="ITChangePlanItem.order"),
    )
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<ITChangePlanItem engagement={self.engagement_id}>"


# ---------- Trial Balance import & IAS 1 Financial Statements ----------

class StandardChartOfAccounts(db.Model):
    """The firm's own Standard Chart of Accounts ("Neverlank Standard") - a
    single firm-wide, numbered list of accounts (not per client), seeded
    once with a sensible draft (see seed.seed_chart_of_accounts) and fully
    firm-editable from there, same "editable starting point" convention as
    QPDInstalmentRate/StatutoryDeadline. Each account carries one of
    financials.FS_CATEGORIES's codes, so picking a standard account for a
    client's trial balance line (see COAMapping.coa_account_number below)
    both gives that line a firm-standard account number AND fills in its
    IAS 1 category in one step. account_number is kept as a string (not an
    integer) so it can carry a leading-zero or non-numeric convention if
    the structure is ever extended (e.g. sub-accounts like "1000.1")."""
    id = db.Column(db.Integer, primary_key=True)
    account_number = db.Column(db.String(20), nullable=False, unique=True)
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50), nullable=False)  # code from financials.FS_CATEGORIES
    is_active = db.Column(db.Boolean, default=True)  # inactive accounts stay on file (history) but drop out of the picker
    notes = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    # The firm's own category this account sits under (see FSSubCategory below). fs_category above stays the IAS 1 line it rolls into,
    # so the financial statements are unchanged; this only names a finer heading inside it. Free text matched to FSSubCategory.label.
    sub_category = db.Column(db.String(100))
    # Set when an account is added from an engagement's Trial Balance tab (not from the Chart of Accounts page): a Partner reviews it once
    # on the Chart of Accounts page and saving the row clears the flag.
    needs_review = db.Column(db.Boolean, default=False)
    added_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    added_from_engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))

    def __repr__(self):
        return f"<StandardChartOfAccounts {self.account_number} {self.account_name}>"


class FSSubCategory(db.Model):
    """The firm's own category for trial balance accounts, e.g. "Biological assets" or "Staff welfare", created from the Trial Balance tab or
    the Chart of Accounts page. Each one belongs to exactly one IAS 1 category (parent_code, a financials.FS_CATEGORIES code) and rolls up
    into it: a trial balance line keeps fs_category = the parent, so the statements, cash flow and notes are computed exactly as before,
    and the sub-category is a finer heading shown on the trial balance (detailed and summarised views)."""
    __tablename__ = "fs_sub_category"
    id = db.Column(db.Integer, primary_key=True)
    parent_code = db.Column(db.String(50), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("parent_code", "label", name="uq_fs_sub_category"),)

    def __repr__(self):
        return f"<FSSubCategory {self.parent_code}: {self.label}>"


class COAMapping(db.Model):
    """A remembered mapping from one of a client's trial balance account
    names to an IAS 1 financial statement category (see financials.py) -
    reusable across every engagement/period for that client, so a repeat
    engagement's trial balance mostly auto-maps itself. Keyed on the
    account name as typed/imported (matched case-insensitively).

    coa_account_number optionally links this account name to one of the
    firm's own Standard Chart of Accounts entries (see StandardChartOfAccounts
    above) - a plain string reference (not a hard foreign key) so
    renumbering or deactivating a standard account never breaks an existing
    mapping. None means this account name is only mapped to a bare IAS 1
    category, with no firm-standard account number chosen (always true for
    every mapping made before this feature existed)."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50), nullable=False)
    coa_account_number = db.Column(db.String(20))
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("coa_mappings", cascade="all, delete-orphan"))
    updated_by = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("client_id", "account_name", name="uq_coa_mapping_client_account"),)

    def __repr__(self):
        return f"<COAMapping {self.client_id}:{self.account_name}={self.fs_category}>"


class TrialBalance(db.Model):
    """One per engagement - the PRELIMINARY trial balance imported/entered
    at planning, holding both current-year and prior-year (comparative)
    columns per account. This preliminary version - before any audit
    adjustments - is what the Analytical Review and Substantive Procedures
    tabs work from. Audit adjustments (see AuditAdjustment below) are
    layered on top of it, current year only, to produce the FINAL/ADJUSTED
    trial balance that the Financial Statements are built from. See
    financials.py for the maths."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    source = db.Column(db.String(20), default="manual")  # "upload" | "manual"
    original_filename = db.Column(db.String(255))

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    # Some engagements genuinely have no trial balance (e.g. a Consulting or
    # Secretarial engagement, or an Investigative Engagement scoped to a
    # specific matter rather than the whole set of accounts) - rather than
    # leaving the Trial Balance tab looking unfinished/broken, the team can
    # explicitly record why and move on. Deliberately kept on this row
    # rather than a separate table: a TrialBalance row with no lines and
    # not_applicable=True IS the "no trial balance, and here's why" record.
    not_applicable = db.Column(db.Boolean, default=False)
    not_applicable_reason = db.Column(db.Text)
    marked_na_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    marked_na_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("trial_balance", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    marked_na_by = db.relationship("User", foreign_keys=[marked_na_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    lines = db.relationship(
        "TrialBalanceLine", backref="trial_balance", lazy=True,
        cascade="all, delete-orphan", order_by="TrialBalanceLine.id",
    )
    adjustments = db.relationship(
        "AuditAdjustment", backref="trial_balance", lazy=True,
        cascade="all, delete-orphan", order_by="AuditAdjustment.id",
    )

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def unmapped_count(self):
        return sum(1 for l in self.lines if not l.fs_category)

    @property
    def is_fully_mapped(self):
        return len(self.lines) > 0 and self.unmapped_count == 0

    @property
    def current_totals(self):
        return sum(l.current_debit or 0 for l in self.lines), sum(l.current_credit or 0 for l in self.lines)

    @property
    def prior_totals(self):
        return sum(l.prior_debit or 0 for l in self.lines), sum(l.prior_credit or 0 for l in self.lines)

    @property
    def is_current_balanced(self):
        d, c = self.current_totals
        return abs(d - c) < 0.01

    @property
    def is_prior_balanced(self):
        d, c = self.prior_totals
        return abs(d - c) < 0.01

    def __repr__(self):
        return f"<TrialBalance engagement={self.engagement_id}>"


class TrialBalanceLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trial_balance_id = db.Column(db.Integer, db.ForeignKey("trial_balance.id"), nullable=False)
    account_code = db.Column(db.String(50))  # the CLIENT's own account code, as uploaded/typed - not the firm's standard numbering below
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50))  # code from financials.FS_CATEGORIES, or "excluded" / blank if unmapped
    coa_account_number = db.Column(db.String(20))  # optional link to one of the firm's own Standard Chart of Accounts entries - see StandardChartOfAccounts/COAMapping
    sub_category = db.Column(db.String(100))  # the firm's own heading inside fs_category (FSSubCategory); follows the Standard account picked
    current_debit = db.Column(db.Float, default=0.0)
    current_credit = db.Column(db.Float, default=0.0)
    prior_debit = db.Column(db.Float, default=0.0)
    prior_credit = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f"<TrialBalanceLine {self.account_name}>"


class AuditAdjustment(db.Model):
    """One audit adjustment (journal entry) proposed against the
    preliminary trial balance, affecting the CURRENT year only - the
    trial balance plus all of its adjustments together make up the
    final/adjusted trial balance the Financial Statements are drawn from.
    Carries its own Preparer/Reviewer/Partner sign-off, same as every
    other workpaper, so adjustments get the same scrutiny as anything
    else. Its line items live in AuditAdjustmentLine below."""
    id = db.Column(db.Integer, primary_key=True)
    trial_balance_id = db.Column(db.Integer, db.ForeignKey("trial_balance.id"), nullable=False)
    reference = db.Column(db.String(50))  # the journal number, e.g. "AJE 1" - given automatically (see next_journal_reference)
    # Where a pushed journal came from (PPE-REG, PPE-REG-PY, DT-PY, DT-CUR). The app finds "its" journal by this code, so the visible
    # reference can be the next AJE number and still be refreshed - not duplicated - by the next push. Blank for journals typed by hand.
    source_key = db.Column(db.String(30))
    description = db.Column(db.Text)
    # Which column of the trial balance the journal adjusts: "current" (the year under audit, the original behaviour) or "prior" (the
    # comparative column - a restatement). A prior-year journal with carry_to_current also restates the current year's OPENING balances:
    # its balance sheet lines are repeated in the current column and its profit and loss lines go to the current retained earnings b/f.
    applies_to = db.Column(db.String(10), default="current")
    carry_to_current = db.Column(db.Boolean, default=True)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    lines = db.relationship(
        "AuditAdjustmentLine", backref="adjustment", lazy=True,
        cascade="all, delete-orphan", order_by="AuditAdjustmentLine.id",
    )

    @property
    def is_prior_year(self):
        return (self.applies_to or "current") == "prior"

    @property
    def carries_to_current(self):
        return self.is_prior_year and self.carry_to_current is not False

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def total_debit(self):
        return sum(l.debit or 0 for l in self.lines)

    @property
    def total_credit(self):
        return sum(l.credit or 0 for l in self.lines)

    @property
    def is_balanced(self):
        return abs(self.total_debit - self.total_credit) < 0.01

    def __repr__(self):
        return f"<AuditAdjustment {self.reference}>"


def adjustment_key(adj):
    """The code saying where an adjustment came from (PPE-REG, DT-CUR ...): its source_key, or - for a journal made before journals were
    numbered automatically - its reference."""
    return (getattr(adj, "source_key", None) or getattr(adj, "reference", None) or "").strip().upper()


def journal_number(reference):
    """3 for "AJE 3" (also "aje3", "AJE-3"); None for any other reference."""
    import re as _re
    m = _re.match(r"^\s*AJE\s*[-.:]?\s*(\d+)\s*$", reference or "", _re.I)
    return int(m.group(1)) if m else None


def next_journal_reference(trial_balance_id):
    """The next journal number on a trial balance: one more than the highest AJE number already used, so every journal - pushed from the
    Asset Register or Deferred Tax tab, or added by hand - gets the next number in one sequence."""
    used = [journal_number(r) for (r,) in db.session.query(AuditAdjustment.reference).filter_by(trial_balance_id=trial_balance_id)]
    return f"AJE {max([n for n in used if n], default=0) + 1}"


class AuditAdjustmentLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    adjustment_id = db.Column(db.Integer, db.ForeignKey("audit_adjustment.id"), nullable=False)
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50), nullable=False)
    debit = db.Column(db.Float, default=0.0)
    credit = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f"<AuditAdjustmentLine {self.account_name}>"
