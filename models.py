from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

from extensions import db


ENGAGEMENT_TYPES = ["Audit", "Assurance", "Consulting", "Secretarial"]
SECRETARIAL_SUBDIVISIONS = ["Company Registrations", "Trusts", "PVOs"]
ENGAGEMENT_STATUSES = ["Planning", "Fieldwork", "Review", "Completed", "On Hold"]
TASK_STATUSES = ["To Do", "In Progress", "Review", "Done"]
CHECKLIST_STATUSES = ["Not Started", "In Progress", "Done", "N/A"]
RISK_STATUSES = ["Open", "Mitigated", "Accepted", "Closed"]

USER_ROLES = ["staff", "supervisor", "partner", "admin"]
# Roles allowed to sign off work as "Reviewed" on checklist items, tasks and
# timesheets. A reviewer can never sign off their own work - that check is
# enforced in the route, not here.
REVIEWER_ROLES = ("supervisor", "partner", "admin")

POLICY_CATEGORIES = ["HR Policy", "Firm Procedure", "Quality Control", "IT & Security", "Other"]
TIMESHEET_STATUSES = ["Submitted", "Approved"]
STANDARD_HOURS_PER_DAY = 8  # anything beyond this on a given day counts as overtime


def _score_rating(score):
    """Shared Low/Medium/High banding for a likelihood x impact score (1-25),
    used by both the legacy RiskItem model and the questionnaire-driven
    RiskAssessment below, so the two ratings always mean the same thing."""
    if score >= 15:
        return "High"
    if score >= 8:
        return "Medium"
    return "Low"


# The system-based Risk Assessment questionnaire: each question is answered
# on a 1-5 scale via the labelled options below (index 0 = score 1, ... index
# 4 = score 5). Likelihood questions assess how likely a misstatement/risk
# event is; impact questions assess how significant it would be if it
# happened. This is a general-purpose starting questionnaire covering common
# audit risk factors - review the wording and, if useful, adjust it to match
# your firm's own risk assessment methodology; it isn't a substitute for
# professional judgement or a specific standard's requirements.
RISK_LIKELIHOOD_QUESTIONS = [
    ("q_complexity", "How complex are this client's transactions and accounting estimates?", [
        "Simple, routine transactions",
        "Mostly routine, some judgement",
        "Moderate complexity/estimates",
        "Significant judgement/estimates involved",
        "Highly complex and/or highly judgemental",
    ]),
    ("q_controls", "How reliable is the client's internal control environment?", [
        "Strong, well-documented controls",
        "Generally sound controls",
        "Some control weaknesses",
        "Significant control weaknesses",
        "Weak or no meaningful controls",
    ]),
    ("q_environment_change", "Has the client's business, industry or regulatory environment changed significantly since the last engagement?", [
        "No significant change",
        "Minor changes",
        "Some notable changes",
        "Significant changes",
        "Major upheaval (new regulation, restructuring, new systems, etc.)",
    ]),
    ("q_fraud_indicators", "Are there indicators of fraud risk (incentive/pressure, opportunity, rationalisation, management override potential)?", [
        "None noted",
        "Minimal indicators",
        "Some indicators present",
        "Several indicators present",
        "Significant fraud risk concerns",
    ]),
    ("q_prior_issues", "Did the prior engagement identify unresolved issues, control deficiencies or misstatements?", [
        "None",
        "Minor items, all resolved",
        "Some unresolved items",
        "Several unresolved/recurring items",
        "Significant unresolved issues",
    ]),
]

RISK_IMPACT_QUESTIONS = [
    ("q_significance", "How significant/high-profile is this engagement (public interest, regulatory scrutiny, stakeholder reliance)?", [
        "Low - private, low scrutiny",
        "Below average",
        "Average",
        "Above average",
        "High - public interest/heavy scrutiny",
    ]),
    ("q_magnitude", "What is the potential monetary magnitude of a misstatement in this engagement, relative to materiality?", [
        "Well below materiality",
        "Below materiality",
        "Around materiality",
        "Above materiality",
        "Well above materiality",
    ]),
    ("q_going_concern", "Are there going concern indicators (recurring losses, liquidity issues, covenant breaches, negative equity)?", [
        "None",
        "Minor concerns",
        "Some concerns",
        "Significant concerns",
        "Substantial doubt over going concern",
    ]),
    ("q_related_party", "What is the extent of related-party or unusual/non-recurring transactions?", [
        "None",
        "Minimal",
        "Some",
        "Considerable",
        "Extensive",
    ]),
    ("q_consequences", "What are the potential consequences of an undetected material misstatement (legal, regulatory, reputational)?", [
        "Minor",
        "Limited",
        "Moderate",
        "Serious",
        "Severe",
    ]),
]

# A starting-point suggested audit approach per overall risk rating, shown on
# the Planning tab. Deliberately general - always apply professional
# judgement and your firm's own methodology on top of this.
SCOPE_SUGGESTIONS = {
    "High": (
        "Consider a lower tolerance for detection risk: extend substantive testing and sample sizes, "
        "reduce reliance on controls unless independently tested, assign more experienced staff with "
        "closer supervision/review, and design specific procedures for each identified fraud risk factor "
        "and significant risk area."
    ),
    "Medium": (
        "A standard risk-based approach is likely appropriate: a blend of tests of controls (where reliance "
        "is intended) and substantive procedures, with sample sizes and staffing set at your firm's normal "
        "benchmarks, and extra attention on any specific factors flagged as higher risk in the questionnaire."
    ),
    "Low": (
        "A reduced level of substantive testing may be appropriate, with greater reliance on analytical "
        "procedures and (if reliance is intended) tests of controls - but still confirm materiality and any "
        "individually significant risk areas are specifically addressed, and revisit this if circumstances change."
    ),
}


engagement_team = db.Table(
    "engagement_team",
    db.Column("engagement_id", db.Integer, db.ForeignKey("engagement.id"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="staff")  # staff | supervisor | partner | admin
    is_active_flag = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_flag

    def __repr__(self):
        return f"<User {self.username}>"


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    address = db.Column(db.String(255))
    industry = db.Column(db.String(120))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagements = db.relationship("Engagement", backref="client", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Client {self.name}>"


class Engagement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(30), nullable=False, default="Audit")
    subdivision = db.Column(db.String(50))  # only meaningful when type == "Secretarial"
    status = db.Column(db.String(30), nullable=False, default="Planning")
    period_end = db.Column(db.Date)
    start_date = db.Column(db.Date, default=date.today)
    deadline = db.Column(db.Date)
    description = db.Column(db.Text)

    partner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    manager_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    partner = db.relationship("User", foreign_keys=[partner_id])
    manager = db.relationship("User", foreign_keys=[manager_id])
    team_members = db.relationship("User", secondary=engagement_team, backref="engagements")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    checklist_items = db.relationship("EngagementChecklistItem", backref="engagement", lazy=True, cascade="all, delete-orphan", order_by="EngagementChecklistItem.order")
    risk_items = db.relationship("RiskItem", backref="engagement", lazy=True, cascade="all, delete-orphan")
    documents = db.relationship("Document", backref="engagement", lazy=True, cascade="all, delete-orphan")
    tasks = db.relationship("EngagementTask", backref="engagement", lazy=True, cascade="all, delete-orphan")

    @property
    def checklist_progress(self):
        items = self.checklist_items
        if not items:
            return 0
        done = sum(1 for i in items if i.status in ("Done", "N/A"))
        return round(100 * done / len(items))

    @property
    def is_overdue(self):
        return bool(self.deadline and self.deadline < date.today() and self.status != "Completed")

    def __repr__(self):
        return f"<Engagement {self.title}>"


class ChecklistTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    type = db.Column(db.String(30), nullable=False, default="Audit")
    description = db.Column(db.Text)

    items = db.relationship("ChecklistTemplateItem", backref="template", lazy=True, cascade="all, delete-orphan", order_by="ChecklistTemplateItem.order")


class ChecklistTemplateItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("checklist_template.id"), nullable=False)
    section = db.Column(db.String(150))
    item_text = db.Column(db.String(500), nullable=False)
    order = db.Column(db.Integer, default=0)


class EngagementChecklistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    section = db.Column(db.String(150))
    item_text = db.Column(db.String(500), nullable=False)
    order = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="Not Started")
    notes = db.Column(db.Text)
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    # Review sign-off: separate from completed_by/completed_at (the preparer)
    # so a supervisor/partner/admin can independently confirm the work -
    # never the same person as the preparer.
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)

    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None


class RiskItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    category = db.Column(db.String(120))
    risk_description = db.Column(db.Text, nullable=False)
    likelihood = db.Column(db.Integer, default=3)  # 1-5
    impact = db.Column(db.Integer, default=3)  # 1-5
    mitigation = db.Column(db.Text)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    status = db.Column(db.String(20), default="Open")

    owner = db.relationship("User")

    @property
    def score(self):
        return (self.likelihood or 0) * (self.impact or 0)

    @property
    def rating(self):
        return _score_rating(self.score)


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), default="General")
    reference = db.Column(db.String(100))  # e.g. a filing/working-paper reference like "A-1" or "SA-01/2026"
    version = db.Column(db.Integer, default=1)
    notes = db.Column(db.Text)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploaded_by = db.relationship("User")


class DocumentTemplate(db.Model):
    """A downloadable, fillable Word/Excel template in the Document Templates
    library (engagement letters, workpapers, etc). The actual file lives on
    disk under Config.DOCUMENT_TEMPLATES_DATA_DIR/<type>/<filename> - this
    row holds the metadata plus the filename to serve, and is what the
    in-app Edit/New/Delete screens modify.
    """
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(30), nullable=False, default="Audit")  # Audit | Assurance | Consulting
    ref_code = db.Column(db.String(20))  # e.g. "SA-01" - shown on the document itself
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    filename = db.Column(db.String(255), nullable=False)  # stored filename on disk, within type subfolder
    order = db.Column(db.Integer, default=0)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    updated_by = db.relationship("User")

    @property
    def file_ext(self):
        return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""

    def __repr__(self):
        return f"<DocumentTemplate {self.ref_code} {self.title}>"


class EngagementTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    due_date = db.Column(db.Date)
    priority = db.Column(db.String(20), default="Normal")  # Low | Normal | High
    status = db.Column(db.String(20), default="To Do")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Preparer sign-off: auto-recorded when the task is marked Done.
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    # Reviewer sign-off: a supervisor/partner/admin confirming the completed
    # work - always someone other than the preparer.
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)

    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < date.today() and self.status != "Done")

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None


# ---------- HR & Administration ----------

class PolicyDocument(db.Model):
    """A firm policy/procedure/form in the HR & Administration > Policies and
    Procedures library - same download/edit pattern as DocumentTemplate, but
    grouped by a free-standing category list rather than engagement type,
    and stored in its own data folder (Config.POLICIES_DATA_DIR).
    """
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False, default="Other")
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    filename = db.Column(db.String(255), nullable=False)
    order = db.Column(db.Integer, default=0)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    updated_by = db.relationship("User")

    @property
    def file_ext(self):
        return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""

    def __repr__(self):
        return f"<PolicyDocument {self.title}>"


class TimeSheet(db.Model):
    """One staff member's timesheet for a given week, filled in directly in
    the app (see TimeEntry). Kept separate from TimeSheetUpload, which is for
    a filled-in copy of the downloadable Excel template instead."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    week_start = db.Column(db.Date, nullable=False)  # Monday of the week
    status = db.Column(db.String(20), default="Submitted")  # Submitted | Approved
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Reviewer sign-off - a supervisor/partner/admin approving the timesheet,
    # never the timesheet's own owner.
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)

    user = db.relationship("User", foreign_keys=[user_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    entries = db.relationship(
        "TimeEntry", backref="timesheet", lazy=True,
        cascade="all, delete-orphan", order_by="TimeEntry.work_date",
    )

    @property
    def total_hours(self):
        return round(sum(e.hours or 0 for e in self.entries), 2)

    @property
    def regular_hours(self):
        return round(sum(min(e.hours or 0, STANDARD_HOURS_PER_DAY) for e in self.entries), 2)

    @property
    def overtime_hours(self):
        return round(sum(max((e.hours or 0) - STANDARD_HOURS_PER_DAY, 0) for e in self.entries), 2)

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    def __repr__(self):
        return f"<TimeSheet {self.user_id} w/o {self.week_start}>"


class TimeEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timesheet_id = db.Column(db.Integer, db.ForeignKey("time_sheet.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    description = db.Column(db.String(255))
    hours = db.Column(db.Float, default=0)

    engagement = db.relationship("Engagement")

    @property
    def regular_hours(self):
        return min(self.hours or 0, STANDARD_HOURS_PER_DAY)

    @property
    def overtime_hours(self):
        return max((self.hours or 0) - STANDARD_HOURS_PER_DAY, 0)


class TimeSheetUpload(db.Model):
    """A completed copy of the downloadable Excel timesheet template,
    uploaded as-is rather than entered into the app - for staff who prefer
    filling it in Excel offline."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    period_label = db.Column(db.String(100))  # free text, e.g. "Week of 14 Sep 2026"
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


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
    questionnaire (RISK_LIKELIHOOD_QUESTIONS / RISK_IMPACT_QUESTIONS above,
    each 1-5) and the app computes likelihood, impact, score and an overall
    Low/Medium/High rating - no manual likelihood/impact picking. One row
    per engagement; re-answering the questionnaire updates it in place
    rather than creating a new one, since risk should be kept current
    rather than accumulated as a history.
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

    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("risk_assessment", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    @property
    def likelihood_answers(self):
        return [getattr(self, field) for field, _, _ in RISK_LIKELIHOOD_QUESTIONS]

    @property
    def impact_answers(self):
        return [getattr(self, field) for field, _, _ in RISK_IMPACT_QUESTIONS]

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

    notes = db.Column(db.Text)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("materiality", uselist=False, cascade="all, delete-orphan"))
    updated_by = db.relationship("User")

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
