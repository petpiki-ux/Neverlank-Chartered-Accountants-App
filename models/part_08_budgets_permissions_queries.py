"""models, part 8 of 12.  Holds: CVPAnalysis, Budget, BudgetLine, KPIMetric, AccountingChecklistItem, Permission, UserPermissionOverride, WorkpaperReview, EngagementQuery, QueryReply, Message, MessageAttachment ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

class CVPAnalysis(db.Model):
    """One Cost-Volume-Profit (break-even) analysis for a product/segment -
    every downstream figure (contribution margin, break-even point, units
    needed for a target profit) is computed live from the four inputs
    below, never stored separately, so it can never drift out of sync with
    them."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    segment_name = db.Column(db.String(150), nullable=False)
    selling_price_per_unit = db.Column(db.Float, nullable=False, default=0.0)
    variable_cost_per_unit = db.Column(db.Float, nullable=False, default=0.0)
    fixed_costs = db.Column(db.Float, nullable=False, default=0.0)
    target_profit = db.Column(db.Float)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("cvp_analyses", lazy=True, cascade="all, delete-orphan", order_by="CVPAnalysis.id"))
    created_by = db.relationship("User")

    @property
    def contribution_margin_per_unit(self):
        return (self.selling_price_per_unit or 0) - (self.variable_cost_per_unit or 0)

    @property
    def contribution_margin_ratio(self):
        cm = self.contribution_margin_per_unit
        return (cm / self.selling_price_per_unit) if self.selling_price_per_unit else None

    @property
    def break_even_units(self):
        cm = self.contribution_margin_per_unit
        return (self.fixed_costs / cm) if cm else None

    @property
    def break_even_revenue(self):
        units = self.break_even_units
        return (units * self.selling_price_per_unit) if units is not None else None

    @property
    def required_units_for_target_profit(self):
        cm = self.contribution_margin_per_unit
        if not cm or self.target_profit is None:
            return None
        return (self.fixed_costs + self.target_profit) / cm

    def __repr__(self):
        return f"<CVPAnalysis {self.segment_name!r} engagement={self.engagement_id}>"


BUDGET_TYPES = ["Operating", "Capital", "Cash"]


class Budget(db.Model):
    """One budget (header) for the Management Accounting service - Budget/
    BudgetLine mirrors TrialBalance/TrialBalanceLine's header+lines shape.
    An engagement can hold more than one budget over time (e.g. an original
    budget and a mid-year reforecast), unlike the single-per-engagement
    working papers elsewhere in this app."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    budget_type = db.Column(db.String(20), default="Operating")
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("budgets", lazy=True, cascade="all, delete-orphan", order_by="Budget.id"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    lines = db.relationship("BudgetLine", backref="budget", lazy=True, cascade="all, delete-orphan", order_by="BudgetLine.id")

    @property
    def total_budgeted(self):
        return sum(l.budgeted_amount or 0 for l in self.lines)

    @property
    def total_actual(self):
        return sum(l.actual_amount or 0 for l in self.lines if l.actual_amount is not None)

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<Budget {self.name!r} engagement={self.engagement_id}>"


class BudgetLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    budget_id = db.Column(db.Integer, db.ForeignKey("budget.id"), nullable=False)
    category = db.Column(db.String(150), nullable=False)
    budgeted_amount = db.Column(db.Float, default=0.0)
    actual_amount = db.Column(db.Float)
    notes = db.Column(db.String(200))

    @property
    def variance(self):
        return (self.actual_amount - self.budgeted_amount) if self.actual_amount is not None else None

    @property
    def variance_pct(self):
        v = self.variance
        return (v / self.budgeted_amount * 100) if v is not None and self.budgeted_amount else None

    def __repr__(self):
        return f"<BudgetLine {self.category!r} budgeted={self.budgeted_amount}>"


KPI_CATEGORIES = ["Liquidity", "Profitability", "Efficiency", "Leverage", "Other"]


class KPIMetric(db.Model):
    """One tracked KPI on the Management Accounting KPI Dashboard - a plain
    preparer-entered target/actual pair, not an auto-computed ratio (no
    figure here is derived from the Trial Balance automatically - the
    preparer decides which KPIs matter for this client and enters both
    sides), consistent with this app's "system prompts, preparer concludes"
    pattern used throughout the analytical review tabs."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    metric_name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(20), default="Other")
    period_label = db.Column(db.String(50))
    target_value = db.Column(db.Float)
    actual_value = db.Column(db.Float)
    unit = db.Column(db.String(20))  # e.g. "%", "days", "x", "$" - display only
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("kpi_metrics", lazy=True, cascade="all, delete-orphan", order_by="KPIMetric.id"))
    created_by = db.relationship("User")

    @property
    def variance(self):
        if self.target_value is None or self.actual_value is None:
            return None
        return self.actual_value - self.target_value

    def __repr__(self):
        return f"<KPIMetric {self.metric_name!r} engagement={self.engagement_id}>"


class AccountingChecklistItem(db.Model):
    """One tick + comment line of the Accounting module's configurable
    Checklist / Quality Gates - the accounting-module equivalent of
    TaxChecklistItem above (same lightweight shape, same reasoning: the
    engagement's own Finalisation checklist already covers formal overall
    sign-off, so this is the working checklist itself, not a second layer
    of formal sign-off). Grouped by `area` on the Accounting tab."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    area = db.Column(db.String(80))
    item_text = db.Column(db.Text, nullable=False)
    response = db.Column(db.String(10), default="")  # "" = not yet assessed, "Yes", "No", "N/A"
    comment = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("accounting_checklist_items", lazy=True, cascade="all, delete-orphan", order_by="AccountingChecklistItem.order"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<AccountingChecklistItem {self.item_text!r} response={self.response!r} engagement={self.engagement_id}>"


# Seeded onto every engagement's Accounting Checklist the first time its
# Accounting tab is opened (see accounting._seed_accounting_checklist) -
# same "system drafts, preparer tailors" pattern as DEFAULT_TAX_CHECKLIST_
# ITEMS above.
DEFAULT_ACCOUNTING_CHECKLIST_ITEMS = [
    (None, "Has the Chart of Accounts been agreed with the client and is it appropriate for the business?"),
    ("Bookkeeping", "Have all journal entries for the period been posted and reviewed?"),
    ("Bookkeeping", "Has the General Ledger been rolled up to the Trial Balance and does it balance?"),
    ("Bookkeeping", "Has the bank account been reconciled for every month in the period?"),
    ("Costing", "Has each job/batch cost card been agreed to source documents (material requisitions, timesheets)?"),
    ("Costing", "Have significant standard cost variances been investigated and explained?"),
    ("Management Accounting", "Has actual performance been compared to budget and material variances explained?"),
    ("Management Accounting", "Has the Management Accounts Report been reviewed for consistency with the underlying figures?"),
    (None, "Has the Quality Control review of the accounting working papers been completed before sign-off?"),
]


class Permission(db.Model):
    """One (role, permission_key) toggle - see PERMISSIONS/user_has_permission
    above. Seeded with defaults on first install/upgrade (see seed.py); an
    admin can change them from Team > Permissions. There is deliberately no
    row needed for admin - user_has_permission() always returns True for
    admin regardless of what's stored here."""
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    permission_key = db.Column(db.String(50), nullable=False)
    allowed = db.Column(db.Boolean, default=False)

    __table_args__ = (db.UniqueConstraint("role", "permission_key", name="uq_permission_role_key"),)

    def __repr__(self):
        return f"<Permission {self.role}:{self.permission_key}={self.allowed}>"


class UserPermissionOverride(db.Model):
    """A per-user override on top of the role-level Permission toggle above
    - "Access Rights" on one team member's own profile (Team > edit a
    person), for a firm that wants a named individual to have or lack a
    capability regardless of what their role defaults to (e.g. restricting
    one particular Partner from Payroll, or granting one Staff member
    access to Manage Policies without promoting their role). Checked FIRST
    in user_has_permission - an explicit per-user decision always wins over
    both the role default and the "manage_payroll" Tax Engagement carve-out.
    One row per (user, permission_key); absence means "use the role
    default" rather than any stored allow/deny."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    permission_key = db.Column(db.String(50), nullable=False)
    allowed = db.Column(db.Boolean, default=False, nullable=False)

    __table_args__ = (db.UniqueConstraint("user_id", "permission_key", name="uq_user_permission_override"),)

    user = db.relationship("User", backref=db.backref("permission_overrides", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<UserPermissionOverride user={self.user_id} {self.permission_key}={self.allowed}>"


# ---------- Multiple reviews on a line-item workpaper ----------

class WorkpaperReview(db.Model):
    """One dated review entry against a single line-item workpaper (see
    REVIEWABLE_TYPES above for which ones) - the generic "multiple reviews"
    log requested by the user: "Every workpaper must have an option to
    enable multiple reviews but the Partner is the ultimate reviewer." Any
    number of these can exist on the same item, each left by a different
    Supervisor/Partner/Admin reviewer (a Manager sits under one of those
    roles too - there's no separate "Manager" login role in the app, see
    Engagement.manager_id) at their own time, with an optional note.

    This is deliberately generic/"polymorphic" rather than its own foreign
    key per workpaper type: reviewable_type is a short fixed string (one of
    REVIEWABLE_TYPES) naming which model the review is against, and
    reviewable_id is that row's own primary key. That's so the SAME table
    can be reused for further workpaper types later (the user's own
    request: start with Substantive Procedures/checklist items, expand from
    there) without a new child table or migration each time - just one more
    entry in REVIEWABLE_TYPES, a `reviews` property on that model, and a
    case in engagements._reviewable_engagement/_reviewable_redirect.

    Purely additive, same philosophy as EngagementQuery above: adding a
    review here never blocks, clears, or replaces that workpaper's own
    Preparer/Reviewer/Partner sign-off fields (where it has them) - the
    Partner's own sign-off remains the ultimate, file-closing authority.
    """
    id = db.Column(db.Integer, primary_key=True)
    reviewable_type = db.Column(db.String(40), nullable=False)
    reviewable_id = db.Column(db.Integer, nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reviewed_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    reviewer = db.relationship("User")

    __table_args__ = (db.Index("ix_workpaper_review_target", "reviewable_type", "reviewable_id"),)

    def __repr__(self):
        return f"<WorkpaperReview {self.reviewable_type}:{self.reviewable_id} by user={self.reviewer_id}>"


# ---------- Review Queries ----------

class EngagementQuery(db.Model):
    """A review query/point raised by a Partner or other Reviewer against
    one workpaper section of an engagement (see QUERY_SECTIONS) - the audit
    equivalent of a reviewer's review note, e.g. "please clarify the
    variance in Revenue" or "confirm the bank confirmation has been
    obtained". Distinct from the Preparer/Reviewer/Partner sign-off used
    elsewhere in the app: raising or resolving a query never blocks or
    clears a sign-off, it's a lightweight, visible way for a reviewer to
    flag something and for the team to discuss and close it out.
    """
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    section = db.Column(db.String(20), nullable=False)  # one of QUERY_SECTION_KEYS
    area_name = db.Column(db.String(80))  # only meaningful when section == "substantive"
    subject = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Open")  # "Open" | "Resolved"

    raised_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    raised_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    resolved_at = db.Column(db.DateTime)
    # True only when this resolution came through the Partner-only override
    # below (partner_override_resolve_query) rather than the ordinary
    # resolve_query path - i.e. the Partner is the ultimate reviewer and
    # closed it on their own judgement (e.g. discussed verbally with the
    # team) without waiting on a written reply. Kept as its own flag rather
    # than inferred from has_preparer_reply so the file still shows how it
    # was actually resolved even if a reply gets added afterwards.
    resolved_via_partner_override = db.Column(db.Boolean, default=False, nullable=False)
    partner_override_notes = db.Column(db.Text)

    engagement = db.relationship("Engagement", backref=db.backref("queries", lazy=True, cascade="all, delete-orphan"))
    raised_by = db.relationship("User", foreign_keys=[raised_by_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])
    replies = db.relationship(
        "QueryReply", backref="parent_query", lazy=True,
        # NOTE: backref must not be named "query" - that would shadow
        # Flask-SQLAlchemy's own QueryReply.query class attribute (the
        # Model.query convenience property used everywhere else in the
        # app, e.g. QueryReply.query.filter_by(...)).
        cascade="all, delete-orphan", order_by="QueryReply.created_at",
    )

    @property
    def is_resolved(self):
        return self.status == "Resolved"

    @property
    def has_preparer_reply(self):
        """Whether someone other than whoever raised the query has replied
        yet - i.e. whether the preparer (or whoever picked the query up)
        has explained how they addressed it. Required before resolve_query
        will let a reviewer mark the query resolved (see
        engagements.resolve_query) - a reviewer can't close out their own
        review point without hearing back first. A query has no separate
        "assigned preparer" field, so "a reply from someone other than the
        reviewer who raised it" is the check - it's exactly what that
        reviewer is actually waiting to hear before resolving."""
        return any(r.author_id != self.raised_by_id for r in self.replies)

    @property
    def section_label(self):
        return QUERY_SECTION_LABELS.get(self.section, self.section)

    def __repr__(self):
        return f"<EngagementQuery {self.id} {self.section} engagement={self.engagement_id}>"


class QueryReply(db.Model):
    """One reply in a query's discussion thread - deliberately flat (no
    nested replies-to-replies) to keep clearing a query simple: read the
    thread top to bottom, then Resolve."""
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey("engagement_query.id"), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship("User")

    def __repr__(self):
        return f"<QueryReply {self.id} query={self.query_id}>"


# ---------- Internal Messaging (intranet) ----------

class Message(db.Model):
    """One internal message from one sender to one or more recipients (see
    MessageRecipient) - a simple firm intranet inbox, separate from the
    per-engagement review Queries above. Not threaded server-side: a
    "Reply" is just a new Message with the same subject prefixed "Re:" and
    the original sender/recipients swapped, which keeps the data model
    simple while still reading like a conversation in the UI.
    """
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship("User")
    recipients = db.relationship(
        "MessageRecipient", backref="message", lazy=True,
        cascade="all, delete-orphan", order_by="MessageRecipient.id",
    )
    attachments = db.relationship(
        "MessageAttachment", backref="message", lazy=True,
        cascade="all, delete-orphan", order_by="MessageAttachment.id",
    )

    def __repr__(self):
        return f"<Message {self.id} {self.subject!r} from={self.sender_id}>"


class MessageAttachment(db.Model):
    """One file attached to a Message - requested by the user so a message
    can carry a document instead of only text ("messages to allow for
    attachment of documents for easier communication"). Saved the same way
    as an engagement Document (see engagements._save_engagement_document):
    to the shared Config.UPLOAD_FOLDER, under a unique stored_filename, with
    the same ALLOWED_EXTENSIONS/size-limit rules - this isn't a firm-wide
    "library" like Policies or Legislative Updates, just a file riding along
    with one message, so it gets its own small table rather than reusing
    Document (which is engagement-specific).

    Downloading one follows the exact same access rule as viewing the
    message itself (see messages._can_view_message): the sender, or a
    recipient who hasn't had the message recalled out from under them.
    Deleting the Message cascades to its attachments (row and file both -
    see messages.py), and a Forward does NOT carry attachments forward,
    matching the existing text-only quoting behaviour for Reply/Forward."""
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id"), nullable=False)
    original_filename = db.Column(db.String(300), nullable=False)
    stored_filename = db.Column(db.String(300), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<MessageAttachment {self.original_filename!r} message={self.message_id}>"


class MessageRecipient(db.Model):
    """One recipient of a Message, and whether/when they've read it - a
    message with several recipients (e.g. a "send to all staff" broadcast)
    gets one row per recipient here, each tracked as read independently."""
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    read_at = db.Column(db.DateTime)
    # Set only when the sender recalls the message WHILE this recipient
    # still hadn't read it (see messages.py recall_message) - once set, this
    # recipient can no longer see the message content, but a recipient who
    # already had read_at set before the recall keeps full access, so
    # recalled_at and read_at are never both set on the same row.
    recalled_at = db.Column(db.DateTime)
    # Set when the recipient deletes the message from their own inbox (or uses
    # "Clear all read"). Only hides it for them - the sender's copy and every
    # other recipient's copy are untouched.
    deleted_at = db.Column(db.DateTime)

    user = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("message_id", "user_id", name="uq_message_recipient"),)

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def is_recalled(self):
        return self.recalled_at is not None

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def __repr__(self):
        return f"<MessageRecipient message={self.message_id} user={self.user_id}>"


# ---------- Client Acceptance & Continuance (pre-engagement gate) ----------

CLIENT_ACCEPTANCE_DECISIONS = ["Pending", "Accepted", "Declined"]

# The decision on the CLIENT (Client.acceptance_decision), made on the client's page. "Accepted with conditions" clears the gate like "Accepted"
# but must carry the conditions in the notes. On each engagement's ClientAcceptance it is shown as Accepted / Declined / Pending.
CLIENT_ACCEPT_DECISIONS = ["Pending", "Accepted", "Accepted with conditions", "Declined"]
CLIENT_ACCEPT_CLEARING = ("Accepted", "Accepted with conditions")
VETTING_DOCUMENT_TYPES = ["Screening print-out / search result", "Identity document check", "PEP check", "Source of funds / wealth", "Adverse media search", "Partner / MLRO note", "Other"]

# Forensic Audit Risk Evaluation Matrix - used only on engagements of type
# "Investigative Engagement" (see ClientAcceptance.risk_* fields/properties
# below). Each tuple is (field_name, category_label, low_description,
# medium_description, high_description); score each category 1 (low) to 5
# (high) against these descriptions, then sum all five (range 5-25) to get
# the risk tier via ClientAcceptance.risk_assessment.
RISK_CATEGORIES = [
    (
        "risk_conflicts_score",
        "Conflicts & Objectivity",
        "No matches found in the database. Firm has never serviced the target. No court testimony threats.",
        "Minor past relationship with a witness/subsidiary, but completely mitigated by separate teams.",
        "Direct conflict identified. Firm previously audited the target or the exact system under investigation.",
    ),
    (
        "risk_security_score",
        "Physical & Cyber Security",
        "Standard office environment. No threat of physical retaliation or complex cyber-attacks.",
        "Target may be uncooperative or defensive, but there is no history of violence or severe cyber threats.",
        "Hostile environment, organized crime links, high threat of physical retaliation, or advanced cyber warfare.",
    ),
    (
        "risk_integrity_edd_score",
        "Client Integrity & EDD",
        "Established client or reputable entity. Clean background checks and clear corporate structure.",
        "New client with minor regulatory friction in the past, or slightly complex corporate structure.",
        "History of bad faith/lawsuits, hidden beneficial owners, or suspected involvement in the fraud themselves.",
    ),
    (
        "risk_evidence_legal_score",
        "Evidence & Legal Risk",
        "Client owns all data. Evidence is untouched. Retained via external counsel under privilege.",
        "Data ownership is mostly clear, but evidence may have been partially handled or looked at by internal IT.",
        "High risk of privacy law breaches. Evidence is already corrupted/wiped. Client refuses to use outside legal counsel.",
    ),
    (
        "risk_scope_capabilities_score",
        "Scope & Capabilities",
        "Exact objectives defined (e.g. specific asset tracing). In-house CFEs and digital experts available immediately.",
        "Scope is slightly broad but manageable. May need to contract a niche external specialist for a short time.",
        "Vague \"fishing expedition\" requested. Firm lacks the necessary forensic tools or certified experts for this industry.",
    ),
]


class ClientAcceptance(db.Model):
    """One per engagement (for engagements where Engagement.acceptance_required
    is True) - the pre-engagement work a firm is expected to do before
    starting an audit/assurance/consulting engagement (ISQM 1 / ISA 220):
    a background check, an independence assessment, contacting the
    predecessor auditor, confirming the team's competence, AML/KYC checks,
    and a signed engagement letter - ending in an Engagement Partner
    accept/decline decision. See engagement_acceptance_cleared() above for
    exactly what "cleared" means, and _ensure_engagement_access /
    _ensure_acceptance_route_access in engagements.py / acceptance.py for
    how the gate is actually enforced."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)

    # 1. Background check - management integrity, reputation, financial stability.
    background_check_notes = db.Column(db.Text)
    background_check_satisfactory = db.Column(db.Boolean)  # None = not yet assessed

    # 2. Independence assessment - conflicts of interest / ethical threats.
    independence_notes = db.Column(db.Text)
    independence_threats_identified = db.Column(db.Boolean)
    independence_safeguards = db.Column(db.Text)  # only meaningful if threats were identified

    # 3. Predecessor communication - with the CLIENT's permission, contact
    # the previous auditor for any disagreements / reasons for leaving.
    predecessor_not_applicable = db.Column(db.Boolean, default=False)  # first audit / no predecessor
    predecessor_auditor_name = db.Column(db.String(200))
    client_permission_obtained = db.Column(db.Boolean)
    predecessor_contacted = db.Column(db.Boolean)
    predecessor_response_notes = db.Column(db.Text)

    # 4. Competence check - the team has the industry expertise and time.
    competence_notes = db.Column(db.Text)
    competence_confirmed = db.Column(db.Boolean)

    # 5. Regulatory checks - AML / KYC.
    aml_kyc_notes = db.Column(db.Text)
    aml_kyc_completed = db.Column(db.Boolean)

    # Forensic Audit Client Acceptance Questionnaire - on engagements of type
    # "Investigative Engagement" these four sections REPLACE sections 1-5
    # above (Background check / Independence / Predecessor / Competence /
    # Regulatory) in the Client Acceptance tab - the underlying questions
    # are different enough (conflicts & threats, enhanced due diligence,
    # evidence/legal control, scope realism) that reusing the generic
    # fields above would mean showing fields like "predecessor auditor
    # name" under a heading about evidence control, which doesn't make
    # sense. Both sets of columns exist side by side; only one set is shown
    # (and required for items_complete, below) depending on the engagement's
    # type, so switching an engagement's type never loses whichever set was
    # already filled in.
    conflict_threat_clear = db.Column(db.Boolean)  # 1. Conflict of Interest & Threat Assessment
    conflict_threat_notes = db.Column(db.Text)
    edd_completed = db.Column(db.Boolean)  # 2. Enhanced Due Diligence (EDD)
    edd_notes = db.Column(db.Text)
    legal_evidence_satisfactory = db.Column(db.Boolean)  # 3. Legal Framework & Evidence Control
    legal_evidence_notes = db.Column(db.Text)
    competence_scope_confirmed = db.Column(db.Boolean)  # 4. Competence & Scope Realism
    competence_scope_notes = db.Column(db.Text)

    # Secretarial Client Acceptance Checklist - on engagements of type
    # "Secretarial" these five sections REPLACE sections 1-5 above
    # (Background check / Independence / Predecessor / Competence /
    # Regulatory), same reasoning and pattern as the forensic columns
    # above: KYC & Ultimate Beneficial Ownership, Authority Integrity &
    # Client Background, Nature of Business & Risk Profile, Conflict of
    # Interest & Dispute Assessment, and Operational Capacity & Commercial
    # Feasibility - the corporate-secretarial-specific considerations a
    # generic financial-statement acceptance checklist doesn't cover.
    kyc_ubo_satisfactory = db.Column(db.Boolean)  # 1. KYC & Ultimate Beneficial Ownership
    kyc_ubo_notes = db.Column(db.Text)
    authority_background_satisfactory = db.Column(db.Boolean)  # 2. Authority, Integrity & Client Background
    authority_background_notes = db.Column(db.Text)
    nature_of_business_risk_acceptable = db.Column(db.Boolean)  # 3. Nature of Business & Risk Profile
    nature_of_business_risk_notes = db.Column(db.Text)
    conflict_dispute_clear = db.Column(db.Boolean)  # 4. Conflict of Interest & Dispute Assessment
    conflict_dispute_notes = db.Column(db.Text)
    operational_capacity_confirmed = db.Column(db.Boolean)  # 5. Operational Capacity & Commercial Feasibility
    operational_capacity_notes = db.Column(db.Text)

    # Tax Client Acceptance - on engagements of type "Tax Advisory" these five
    # sections REPLACE sections 1-5 above (same pattern as the forensic and
    # secretarial sets): the audit questions on predecessor auditors and
    # independence from the audit client do not fit tax work; what matters is
    # who the taxpayer is and what it is registered for, its standing with
    # ZIMRA, exactly what tax work is asked for, the client's integrity and
    # appetite for tax risk, and conflicts and competence.
    tax_kyc_satisfactory = db.Column(db.Boolean)            # 1. Taxpayer Identity, KYC & Registrations
    tax_kyc_notes = db.Column(db.Text)
    tax_history_satisfactory = db.Column(db.Boolean)        # 2. Tax Compliance History & Standing
    tax_history_notes = db.Column(db.Text)
    tax_scope_clear = db.Column(db.Boolean)                 # 3. Scope & Nature of Tax Work
    tax_scope_notes = db.Column(db.Text)
    tax_integrity_acceptable = db.Column(db.Boolean)        # 4. Integrity & Tax Risk Appetite
    tax_integrity_notes = db.Column(db.Text)
    tax_conflict_competence_clear = db.Column(db.Boolean)   # 5. Conflicts, Independence & Competence
    tax_conflict_competence_notes = db.Column(db.Text)

    # 6. Engagement letter - scope/timeline/responsibilities/fees, signed by
    # the client. The signed copy is filed as an ordinary Document (see
    # acceptance.py's upload route) and linked here.
    engagement_letter_notes = db.Column(db.Text)
    engagement_letter_sent_at = db.Column(db.DateTime)
    engagement_letter_signed_at = db.Column(db.DateTime)
    engagement_letter_document_id = db.Column(db.Integer, db.ForeignKey("document.id"))

    # Forensic Audit Risk Evaluation Matrix - only shown/used on engagements
    # of type "Investigative Engagement" (see RISK_CATEGORIES below and the
    # risk_total_score/risk_tier properties). Each is 1 (low) to 5 (high);
    # None means that category hasn't been scored yet.
    risk_conflicts_score = db.Column(db.Integer)
    risk_security_score = db.Column(db.Integer)
    risk_integrity_edd_score = db.Column(db.Integer)
    risk_evidence_legal_score = db.Column(db.Integer)
    risk_scope_capabilities_score = db.Column(db.Integer)
    risk_scoring_notes = db.Column(db.Text)

    # The decision itself - this plus the partner sign-off below is what
    # actually clears (or permanently blocks) the gate.
    decision = db.Column(db.String(20), default="Pending", nullable=False)
    decision_notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    # Interim "continue working" authorisation: a narrower, separate power
    # that lets the Engagement Partner unlock the rest of the engagement
    # (see engagement_acceptance_cleared below) BEFORE the full sign-off
    # above is possible - e.g. the decision to accept has genuinely been
    # made but one item is still physically in process (typically the
    # client hasn't yet returned the countersigned engagement letter).
    # Unlike the full partner sign-off, this does NOT require
    # items_complete, and it never lets a "Declined"/"Pending" decision
    # through - only "Accepted". It's superseded by (and should still be
    # followed up with) a proper partner sign-off once every item is
    # actually complete. Cleared automatically by _touch() in acceptance.py
    # whenever any underlying field changes, same as the review/sign-off
    # timestamps above, so a stale authorisation can never linger past the
    # facts it was based on.
    partner_authorized_continue_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_authorized_continue_at = db.Column(db.DateTime)
    partner_authorized_continue_notes = db.Column(db.Text)

    engagement = db.relationship("Engagement", backref=db.backref("client_acceptance", uselist=False, cascade="all, delete-orphan"))
    engagement_letter_document = db.relationship("Document")
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    partner_authorized_continue_by = db.relationship("User", foreign_keys=[partner_authorized_continue_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def is_authorized_to_continue(self):
        return self.partner_authorized_continue_at is not None

    @property
    def items_complete(self):
        """All items have at least been assessed (not necessarily
        favourably - an unfavourable background check is a reason to
        decline, not a reason the form is "incomplete"). Required before a
        Partner can record the decision as sign-off. Investigative
        Engagements use the four Forensic Audit Client Acceptance
        Questionnaire sections instead of the five generic ones (see the
        columns above), and Secretarial engagements use the five-section
        Secretarial Client Acceptance Checklist - every other engagement
        type keeps the original six-item check."""
        if self.engagement and self.engagement.type == "Investigative Engagement":
            return all([
                self.conflict_threat_clear is not None,
                self.edd_completed is not None,
                self.legal_evidence_satisfactory is not None,
                self.competence_scope_confirmed is not None,
                self.engagement_letter_signed_at is not None,
            ])
        if self.engagement and self.engagement.is_tax_advisory_type:
            return all([
                self.tax_kyc_satisfactory is not None,
                self.tax_history_satisfactory is not None,
                self.tax_scope_clear is not None,
                self.tax_integrity_acceptable is not None,
                self.tax_conflict_competence_clear is not None,
                self.engagement_letter_signed_at is not None,
            ])
        if self.engagement and self.engagement.type == "Secretarial":
            return all([
                self.kyc_ubo_satisfactory is not None,
                self.authority_background_satisfactory is not None,
                self.nature_of_business_risk_acceptable is not None,
                self.conflict_dispute_clear is not None,
                self.operational_capacity_confirmed is not None,
                self.engagement_letter_signed_at is not None,
            ])
        predecessor_done = self.predecessor_not_applicable or self.predecessor_contacted is not None
        return all([
            self.background_check_satisfactory is not None,
            self.independence_threats_identified is not None,
            predecessor_done,
            self.competence_confirmed is not None,
            self.aml_kyc_completed is not None,
            self.engagement_letter_signed_at is not None,
        ])

    @property
    def checklist_assessment(self):
        """A plain-language, advisory-only read of the tick + comment
        checklist below, surfaced next to the Decision field to help the
        Partner judge whether to accept, decline, or keep looking into an
        engagement - it never sets or overrides the `decision` field
        itself, that's always a deliberate choice made by the Partner.
        A "No" response on any checklist item is treated as a flagged
        concern; an item with no response yet is "outstanding"."""
        items = list(self.checklist_items)
        total = len(items)
        if total == 0:
            return {"label": "No checklist items added yet.", "level": "muted", "flagged": 0, "outstanding": 0, "total": 0}
        flagged = sum(1 for i in items if i.response == "No")
        outstanding = sum(1 for i in items if not i.response)
        if flagged:
            label = f"{flagged} of {total} item{'s' if total != 1 else ''} flagged as a concern - recommend reviewing these before accepting."
            level = "danger"
        elif outstanding:
            label = f"{outstanding} of {total} item{'s' if total != 1 else ''} not yet assessed."
            level = "warning"
        else:
            label = f"No concerns identified across all {total} checklist item{'s' if total != 1 else ''}."
            level = "success"
        return {"label": label, "level": level, "flagged": flagged, "outstanding": outstanding, "total": total}

    @property
    def client_screenings(self):
        """The client's sanctions / AML vetting list (see ClientSanctionsScreening) - it applies to every engagement."""
        client = self.engagement.client if self.engagement is not None else None
        return list(client.sanctions_screenings) if client is not None else []

    @property
    def screening_assessment(self):
        """Advisory-only summary of the client's vetting list, shown on this tab with a link to the client's page, where it is managed.
        It never sets or overrides the `decision` field, same as checklist_assessment above."""
        return screening_assessment_for(self.client_screenings)

    @property
    def risk_scores(self):
        """The five Forensic Audit Risk Evaluation Matrix scores, in a
        fixed order matching RISK_CATEGORIES, as (field_name, value)
        pairs - value is None where that category hasn't been scored."""
        return [(field, getattr(self, field)) for field, *_ in RISK_CATEGORIES]

    @property
    def risk_total_score(self):
        """Sum of the five category scores, or None until every category
        has been scored (a partial sum would be misleading against the
        5-25 scale the risk tiers below are calibrated to)."""
        scores = [v for _, v in self.risk_scores]
        if any(v is None for v in scores):
            return None
        return sum(scores)

    @property
    def risk_assessment(self):
        """The risk tier (Low/Medium/High) implied by risk_total_score,
        with its decision guidance and recommended action, exactly as set
        out in the firm's Forensic Audit Risk Evaluation Matrix. Purely
        advisory, like checklist_assessment above - it never sets the
        `decision` field itself."""
        total = self.risk_total_score
        if total is None:
            scored = sum(1 for _, v in self.risk_scores if v is not None)
            return {
                "total": None, "tier": None, "level": "muted",
                "label": f"{scored} of {len(RISK_CATEGORIES)} risk categories scored so far.",
                "decision": None, "action": None,
            }
        if total <= 10:
            return {
                "total": total, "tier": "Low Risk", "level": "success",
                "label": f"Total score {total}/25 - Low Risk (Fast-Track Acceptance).",
                "decision": "Accept. Standard engagement setup.",
                "action": "Draft the engagement letter and assign the team.",
            }
        if total <= 17:
            return {
                "total": total, "tier": "Medium Risk", "level": "warning",
                "label": f"Total score {total}/25 - Medium Risk (Conditional Acceptance).",
                "decision": "Review. Requires approval from the Managing Partner or Risk Committee.",
                "action": "Put safeguards in place (e.g. a higher upfront retainer, strict information barriers, or mandating outside counsel).",
            }
        return {
            "total": total, "tier": "High Risk", "level": "danger",
            "label": f"Total score {total}/25 - High Risk (Decline or Heavy Mitigation).",
            "decision": "High Alert. Recommend declining the engagement unless extraordinary risk mitigations are implemented.",
            "action": "If accepted, requires formal sign-off from the Board/Global Risk Head, specialised insurance riders, and independent third-party oversight.",
        }

    @property
    def flagged_issues(self):
        """Every concern currently flagged anywhere on this tab, pulled
        together into one list for the Decision section - a "No" checklist
        response, a sanctions Potential Match/Confirmed Hit, an unfavourable
        section answer, an unresolved predecessor-communication step, or a
        Medium/High forensic risk tier. This is computed fresh from the live
        data every time (not stored), so an issue that's since been fixed
        (e.g. a checklist response changed from No to Yes) simply stops
        appearing - it never needs separate cleanup. Each entry carries
        whichever AcceptanceFlagReview already exists for it (None if it
        hasn't been looked at yet) - see that model for what reviewing one
        does (and, just as importantly, doesn't do)."""
        issues = []
        for item in self.checklist_items:
            if item.response == "No":
                label = f"Checklist - {item.section}" if item.section else "Checklist item"
                detail = item.item_text + (f" — {item.comment}" if item.comment else "")
                issues.append({"key": f"checklist:{item.id}", "label": label, "detail": detail})

        for screening in self.client_screenings:
            if screening.overall_status in ("Confirmed Hit", "Potential Match"):
                detail = screening.overall_status + (f" ({screening.role_description})" if screening.role_description else "")
                issues.append({"key": f"screening:{screening.id}", "label": f"Sanctions screening — {screening.individual_name}", "detail": detail})

        is_forensic = self.engagement and self.engagement.type == "Investigative Engagement"
        is_secretarial = self.engagement and self.engagement.type == "Secretarial"
        is_tax = bool(self.engagement and self.engagement.is_tax_advisory_type)
        if is_forensic:
            section_checks = [
                ("conflict_threat_clear", False, "Conflict of Interest & Threat Assessment", "conflict_threat_notes"),
                ("edd_completed", False, "Enhanced Due Diligence", "edd_notes"),
                ("legal_evidence_satisfactory", False, "Legal Framework & Evidence Control", "legal_evidence_notes"),
                ("competence_scope_confirmed", False, "Competence & Scope Realism", "competence_scope_notes"),
            ]
        elif is_tax:
            section_checks = [
                ("tax_kyc_satisfactory", False, "Taxpayer Identity, KYC & Registrations", "tax_kyc_notes"),
                ("tax_history_satisfactory", False, "Tax Compliance History & Standing", "tax_history_notes"),
                ("tax_scope_clear", False, "Scope & Nature of Tax Work", "tax_scope_notes"),
                ("tax_integrity_acceptable", False, "Integrity & Tax Risk Appetite", "tax_integrity_notes"),
                ("tax_conflict_competence_clear", False, "Conflicts, Independence & Competence", "tax_conflict_competence_notes"),
            ]
        elif is_secretarial:
            section_checks = [
                ("kyc_ubo_satisfactory", False, "KYC & Ultimate Beneficial Ownership", "kyc_ubo_notes"),
                ("authority_background_satisfactory", False, "Authority, Integrity & Client Background", "authority_background_notes"),
                ("nature_of_business_risk_acceptable", False, "Nature of Business & Risk Profile", "nature_of_business_risk_notes"),
                ("conflict_dispute_clear", False, "Conflict of Interest & Dispute Assessment", "conflict_dispute_notes"),
                ("operational_capacity_confirmed", False, "Operational Capacity & Commercial Feasibility", "operational_capacity_notes"),
            ]
        else:
            section_checks = [
                ("background_check_satisfactory", False, "Background check", "background_check_notes"),
                ("independence_threats_identified", True, "Independence assessment — threats identified", "independence_notes"),
                ("competence_confirmed", False, "Competence check", "competence_notes"),
                ("aml_kyc_completed", False, "Regulatory checks (AML/KYC)", "aml_kyc_notes"),
            ]
        for field, flag_value, label, notes_field in section_checks:
            if getattr(self, field) == flag_value:
                detail = (getattr(self, notes_field) or "").strip() or "No further notes recorded."
                issues.append({"key": f"section:{field}", "label": label, "detail": detail})

        if not is_forensic and not is_secretarial and not is_tax and not self.predecessor_not_applicable:
            if self.client_permission_obtained is False:
                issues.append({"key": "section:predecessor_permission", "label": "Predecessor communication", "detail": "Client permission to contact the predecessor auditor was not obtained."})
            elif self.predecessor_contacted is False:
                issues.append({"key": "section:predecessor_contacted", "label": "Predecessor communication", "detail": "The predecessor auditor was not (or could not be) contacted."})

        if is_forensic:
            tier = self.risk_assessment.get("tier")
            if tier in ("Medium Risk", "High Risk"):
                issues.append({"key": "risk:total", "label": "Forensic Risk Evaluation Matrix", "detail": self.risk_assessment["label"]})

        reviews_by_key = {r.flag_key: r for r in self.flag_reviews}
        for issue in issues:
            issue["review"] = reviews_by_key.get(issue["key"])
        return issues

    @property
    def flagged_issues_summary(self):
        """Plain-language roll-up of flagged_issues above, for the one-line
        summary at the top of the Decision section - same advisory-only,
        never-gates-anything pattern as checklist_assessment/
        screening_assessment/risk_assessment."""
        issues = self.flagged_issues
        total = len(issues)
        if total == 0:
            return {"label": "No issues currently flagged elsewhere on this tab.", "level": "success", "total": 0, "open": 0, "consider": 0}
        open_count = sum(1 for i in issues if not i["review"])
        consider_count = sum(1 for i in issues if i["review"] and i["review"].status == "Consider")
        if open_count:
            label = f"{total} issue{'s' if total != 1 else ''} flagged — {open_count} not yet reviewed below."
            level = "danger"
        elif consider_count:
            label = f"{total} issue{'s' if total != 1 else ''} flagged — all reviewed, {consider_count} marked to still Consider."
            level = "warning"
        else:
            label = f"{total} issue{'s' if total != 1 else ''} flagged — all reviewed and Disregarded."
            level = "success"
        return {"label": label, "level": level, "total": total, "open": open_count, "consider": consider_count}

    def __repr__(self):
        return f"<ClientAcceptance engagement={self.engagement_id} decision={self.decision}>"


ACCEPTANCE_FLAG_STATUSES = ["Disregard", "Consider"]


class AcceptanceFlagReview(db.Model):
    """A reviewer's call on one specific issue flagged in
    ClientAcceptance.flagged_issues, shown in the Decision section's summary.
    Purely a record for the file of who looked at a flagged concern and what
    they made of it - "Disregard" it, or still "Consider" it - with an
    optional note. Like every other advisory signal on this tab
    (checklist_assessment, screening_assessment, risk_assessment), this
    NEVER blocks the decision or the partner sign-off; it exists only so the
    file shows that someone actually looked at each flagged item rather than
    it just sitting there unaddressed.

    flag_key identifies which specific issue this is about (see
    ClientAcceptance.flagged_issues for how each key is built - a checklist
    item, a screening, a named section, or the risk matrix total). If the
    underlying issue stops applying (e.g. a checklist response changes from
    No back to Yes) this row is simply no longer shown - it isn't deleted,
    so if the same concern reappears later its previous review is still
    here for reference."""
    id = db.Column(db.Integer, primary_key=True)
    client_acceptance_id = db.Column(db.Integer, db.ForeignKey("client_acceptance.id"), nullable=False)
    flag_key = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), nullable=False)  # "Disregard" or "Consider" - see ACCEPTANCE_FLAG_STATUSES
    note = db.Column(db.Text)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime, default=datetime.utcnow)

    client_acceptance = db.relationship("ClientAcceptance", backref=db.backref("flag_reviews", lazy=True, cascade="all, delete-orphan"))
    reviewed_by = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("client_acceptance_id", "flag_key", name="uq_acceptance_flag_review"),)

    def __repr__(self):
        return f"<AcceptanceFlagReview {self.flag_key!r} status={self.status!r}>"


CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES = ["Yes", "No", "N/A"]

# Seeded the first time someone opens a fresh Client Acceptance checklist
# (see acceptance.seed_acceptance_checklist) - a starting point covering the
# same six areas above at a finer grain, editable/deletable/extendable like
# any other item.
DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS = [
    ("Background check", "What is the professional background, market reputation, and regulatory track record of key officers (CEO, CFO, Audit Committee Chair)?"),
    ("Background check", "Have management or principal owners been involved in financial litigation, fraud investigations, tax disputes, or regulatory enforcement action?"),
    ("Background check", "What is the company's history regarding aggressive accounting choices, internal control overrides, or frequent restatements?"),
    ("Background check", "Are there indicators of financial stress, such as declining liquidity ratios, impending debt covenant breaches, or reliance on short-term financing to cover operating expenses?"),
    ("Background check", "What are management's key compensation incentives, and do they link heavily to short-term earnings targets or equity metrics?"),
    ("Independence", "Does the firm or any engagement team member hold direct or indirect financial interests (e.g., shares, loans) in the prospective client?"),
    ("Independence", "Do key personnel have close family or personal relationships with client executives, directors, or financial accounting staff?"),
    ("Independence", "Are non-audit services (tax, advisory, IT implementation) currently provided that would create a self-review threat or act in a management capacity?"),
    ("Independence", "Does the anticipated audit fee represent a significant percentage of the firm's total revenue, creating an over-reliance or economic self-interest threat?"),
    ("Independence", "Are key audit team members former employees of the prospective client within the cooling-off period required by professional standards?"),
    ("Predecessor auditor", "What was the predecessor auditor's understanding of the primary reasons for the change in auditors?"),
    ("Predecessor auditor", "Were there any significant disagreements regarding accounting principles, auditing procedures, or financial statement disclosures?"),
    ("Predecessor auditor", "Did management attempt to restrict the scope of the prior audit or withhold relevant information?"),
    ("Predecessor auditor", "Did the predecessor auditor identify any instances of actual or suspected fraud, non-compliance with laws, or significant internal control deficiencies?"),
    ("Predecessor auditor", "Were there any communication issues with those charged with governance or the audit committee?"),
    ("Competence", "Does the firm possess specialists (e.g., IT auditors, valuation experts, actuarial analysts) with relevant experience in this specific industry?"),
    ("Competence", "Is the engagement team familiar with industry-specific reporting frameworks, complex transactions, and regulatory requirements?"),
    ("Competence", "Can the firm allocate sufficient staff and partner hours during the required engagement window without overextending personnel?"),
    ("Competence", "Are there geographic considerations, foreign subsidiaries, or joint ventures that require specialized local audit coverage or network partner support?"),
    ("Regulatory / AML", "Who are the Ultimate Beneficial Owners (UBOs) holding direct or indirect ownership stakes above the regulatory threshold (e.g., 10% or 25%)?"),
    ("Regulatory / AML", "Have background screenings been performed against global watchlists, sanctions lists (OFAC, UN, EU), and Politically Exposed Persons (PEP) databases?"),
    ("Regulatory / AML", "What is the nature, source, and legitimacy of the organization's funding and capital contributions?"),
    ("Regulatory / AML", "Is the entity incorporated in or operating out of high-risk jurisdictions or tax havens identified by FATF?"),
    ("Regulatory / AML", "Has management provided verified primary identification documents and corporate registration certificates?"),
    ("Fee & quality", "The expected fee is commensurate with the work required, without compromising the quality of the engagement."),
    ("Engagement letter", "The client has agreed to the scope, timeline, responsibilities, and fee basis set out in the engagement letter."),
]

# Seeded instead of DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS when the engagement's
# type is "Investigative Engagement" (see acceptance.seed_acceptance_checklist)
# - forensic/fraud investigation work raises acceptance considerations a
# standard audit/assurance checklist doesn't cover (evidence chain of
# custody, legal privilege, adversarial parties). Section names match the
# four Forensic Audit Client Acceptance Questionnaire sections that REPLACE
# the five generic narrative sections for this engagement type (see
# ClientAcceptance.conflict_threat_clear etc. and items_complete above).
# Grounded in the Zimbabwean legal/regulatory environment the firm
# operates in - the Money Laundering and Proceeds of Crime Act [Chapter
# 9:24], the Companies and Other Business Entities Act [Chapter 24:31],
# the Cyber and Data Protection Act [Chapter 11:12], the Exchange Control
# Act [Chapter 22:05], FIU Zimbabwe, RBZ, ZIMRA, ZACC, PAAB/ICAZ, and the
# multicurrency (ZWG/USD) operating environment - rather than generic or
# foreign-law references.
FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS = [
    ("Conflict of Interest & Threat Assessment", "Have we screened all suspects, target entities, key witnesses, and related parties against our firm's active and past client database, including engagements handled by associated offices?"),
    ("Conflict of Interest & Threat Assessment", "Have we previously provided audit, accounting, tax, or advisory services to this client or target that could create a self-review or advocacy threat, having regard to the ICAZ Code of Professional Conduct and PAAB independence requirements?"),
    ("Conflict of Interest & Threat Assessment", "Is any partner, manager, or staff member on the proposed team a former employee, director, or close family member of the client, the target, or a related party, within the cooling-off period required by PAAB/ICAZ?"),
    ("Conflict of Interest & Threat Assessment", "Does the investigation touch on a politically exposed person (PEP), a state enterprise, or an individual/entity connected to ZACC or other politically sensitive networks, requiring heightened partner-level oversight?"),
    ("Conflict of Interest & Threat Assessment", "Does this investigation involve organised crime, cross-border smuggling (e.g. gold, chrome, tobacco), corporate retaliation, or a hostile environment in a high-risk part of the country, requiring specialised physical security for our staff?"),
    ("Conflict of Interest & Threat Assessment", "Have we assessed cyber-security threats common in Zimbabwe (SIM-swap fraud, mobile money hijacking, compromised email accounts) that could expose our staff, systems, or evidence to interference?"),
    ("Enhanced Due Diligence (EDD)", "Have we verified the identity of the engaging entity and its directors through the Companies and Other Business Entities Act [Chapter 24:31] register (Deeds and Companies Office), applying standard KYC/AML protocols under the Money Laundering and Proceeds of Crime Act [Chapter 9:24] and FIU Zimbabwe guidance?"),
    ("Enhanced Due Diligence (EDD)", "Have we identified the Ultimate Beneficial Owners (UBOs) of both the client and the target, cross-checked against the COBE Act's beneficial ownership register requirements, to rule out hidden conflicts or nominee arrangements?"),
    ("Enhanced Due Diligence (EDD)", "Have we screened all key individuals against the UN, OFAC and EU sanctions lists, and against any adverse notices issued by the Reserve Bank of Zimbabwe (RBZ) or the Financial Intelligence Unit (FIU)?"),
    ("Enhanced Due Diligence (EDD)", "Does the source of funds or the transactions under review involve externalisation of foreign currency or RTGS/ZWG-to-USD conversions that may raise issues under the Exchange Control Act [Chapter 22:05] or RBZ exchange control directives?"),
    ("Enhanced Due Diligence (EDD)", "Do background checks with the High Court of Zimbabwe civil register, the Master of the High Court, ZIMRA (tax clearance status), and media reports reveal a history of bad faith, fraud, tax evasion, or vexatious litigation by any key player?"),
    ("Enhanced Due Diligence (EDD)", "Has the client itself been the subject of an investigation, freezing order, or adverse finding by ZACC, the ZRP Fraud Section, or a sector regulator (RBZ, IPEC, SECZim, POTRAZ, ZERA) with a bearing on this engagement?"),
    ("Legal Framework & Evidence Control", "Does the client have the legal authority to grant us access to the target's emails, mobile devices, and financial records without breaching the Cyber and Data Protection Act [Chapter 11:12], the Interception of Communications Act [Chapter 11:20], or the target's constitutional right to privacy?"),
    ("Legal Framework & Evidence Control", "Has the client or a third party already accessed, copied, altered, or deleted data before engaging us, in a way that could compromise the chain of custody or its admissibility under the Evidence Act [Chapter 8:01] and Zimbabwean court practice?"),
    ("Legal Framework & Evidence Control", "Will evidence, device images, or personal data need to leave Zimbabwe for analysis, and if so, have we considered the cross-border data transfer restrictions under the Cyber and Data Protection Act and any Data Protection Authority notification requirements?"),
    ("Legal Framework & Evidence Control", "Should we be retained directly by the client, or engaged through a legal practitioner registered with the Law Society of Zimbabwe, so our work product is shielded, where possible, by legal professional privilege?"),
    ("Legal Framework & Evidence Control", "If the matter may proceed to the ZRP, the National Prosecuting Authority, ZACC, or the High Court, have we agreed evidence-handling and reporting protocols with the client and their lawyers that will meet those bodies' standards?"),
    ("Legal Framework & Evidence Control", "Are there Labour Act [Chapter 28:01] considerations around interviewing or suspending implicated employees that need to be cleared with the client's legal counsel before we proceed?"),
    ("Competence & Scope Realism", "Do we have Certified Fraud Examiners (CFEs), digital forensics specialists, and staff registered with ICAZ/PAAB with genuine experience in this type of fraud and in the relevant Zimbabwean industry?"),
    ("Competence & Scope Realism", "Is the engagement team familiar with the reporting expectations of the specific Zimbabwean regulator involved (RBZ, IPEC, SECZim, POTRAZ, ZERA, or the Minerals Marketing Corporation of Zimbabwe), where relevant?"),
    ("Competence & Scope Realism", "Is the scope clearly defined (e.g. quantifying a specific loss, tracing assets domestically or externalised abroad, or preparing a report for possible criminal referral), or is the client asking for an open-ended \"fishing expedition\" our resourcing cannot support?"),
    ("Competence & Scope Realism", "Given Zimbabwe's multicurrency environment (ZWG, USD, and residual RTGS balances) and exchange rate volatility, can we realistically trace and value the transactions, and have we agreed which currency our findings and fee will be expressed in?"),
    ("Competence & Scope Realism", "Does the client understand that building court-ready evidence in Zimbabwe takes time (bank statements, court orders, ZIMRA records), and are they willing to pay an upfront retainer - ideally in hard currency - to mitigate non-payment and currency risk?"),
    ("Competence & Scope Realism", "Will the investigation require statements or affidavits from Shona- or Ndebele-speaking witnesses, and have we budgeted for qualified translation/interpretation so the evidence stays accurate and admissible?"),
]

# Seeded instead of DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS when the engagement's
# type is "Business Intelligence and IT Engagements" (see
# acceptance.seed_acceptance_checklist) - a cyber/IT/AML-CFT control
# assurance engagement raises acceptance considerations the standard
# financial-statement checklist doesn't cover (competence to assess
# technical controls, logistics of accessing client systems, handling of
# highly confidential data, and the Cyber and Data Protection Act/AML
# regulatory backdrop). Unlike FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS, this
# type keeps the standard six narrative sections (Background check/
# Independence/Predecessor/Competence/Regulatory/Engagement letter) rather
# than replacing them - only the detailed tick + comment questions under
# each section change, so the section names below match those six exactly.
BUSINESS_IT_ACCEPTANCE_CHECKLIST_ITEMS = [
    ("Background check", "Has the entity had any publicly reported cyber security incidents, data breaches, or regulatory findings relating to IT, information security or AML/CFT compliance?"),
    ("Background check", "Is there evidence of a mature (or, conversely, an absent/ad hoc) IT governance and control environment based on preliminary enquiry?"),
    ("Background check", "Are there indicators of significant IT change (system migrations, outsourcing, M&A/integration) that could heighten engagement risk?"),
    ("Background check", "Does the entity rely on legacy or end-of-life systems for any critical financial or operational processes?"),
    ("Background check", "Does the entity rely on third-party vendor hosting (SaaS/cloud) for critical systems, and if so, is there visibility over that vendor's own security/compliance posture?"),
    ("Independence", "Does the firm or any engagement team member have a financial interest in, or a close relationship with, the entity's IT service providers, cloud vendors, or cyber security vendors?"),
    ("Independence", "Has the firm previously provided IT implementation, cyber security remediation, or AML/CFT advisory services to this entity that would create a self-review threat over the same subject matter now being assessed?"),
    ("Independence", "Are the proposed engagement team members free of any relationship that could compromise objectivity when assessing the entity's own IT/security/AML controls?"),
    ("Predecessor", "If a predecessor firm previously performed IT, cyber security or AML/CFT assurance work for this entity, has permission been obtained to make appropriate enquiries of them?"),
    ("Predecessor", "Were there any disagreements or concerns raised by a predecessor regarding the entity's IT general controls, cyber security posture, or AML/CFT compliance?"),
    ("Competence", "Does the engagement team include (or have access to) specialists with genuine cyber security, information security, ICT audit, and AML/CFT expertise appropriate to this entity's environment?"),
    ("Competence", "Is the team familiar with the relevant Zimbabwean legal/regulatory framework - the Cyber and Data Protection Act [Chapter 12:07], the Money Laundering and Proceeds of Crime Act [Chapter 9:24], and applicable FIU guidance?"),
    ("Competence", "Has the logistics of accessing the entity's systems been agreed (read-only access levels, supervised access, remote vs on-site testing, timing to avoid disrupting live operations)?"),
    ("Competence", "Can the firm resource this engagement without overextending the specialists required, given other concurrent commitments?"),
    ("Competence - Management Integrity & Cooperativeness", "Has management indicated a willingness to grant the engagement team full read-only administrative access to system and security logs (e.g. SIEM, firewall, access control logs) for the period under review?"),
    ("Competence - Management Integrity & Cooperativeness", "Has management indicated a willingness to provide access to relevant source code repositories (for in-house developed systems) for review, where applicable?"),
    ("Competence - Management Integrity & Cooperativeness", "Has management indicated a willingness to provide system configuration exports/documentation for key infrastructure and application components?"),
    ("Competence - Management Integrity & Cooperativeness", "Has management indicated a willingness to provide database schema documentation and appropriate read-only database access for testing purposes?"),
    ("Competence - Management Integrity & Cooperativeness", "Based on preliminary engagement with management, is there any indication of reluctance, evasiveness, or resistance to independent scrutiny of the IT environment that should factor into the accept/decline decision?"),
    ("Regulatory", "Have appropriate confidentiality and data-handling arrangements been agreed for any highly sensitive data (personal information, security configurations, vulnerability findings) that will be accessed or generated during the engagement?"),
    ("Regulatory", "Does the scope of testing (e.g. any vulnerability scanning or penetration testing) require a formal rules-of-engagement/authorisation letter to avoid any suggestion of unauthorised system access?"),
    ("Regulatory", "Are there sector-specific regulatory considerations (e.g. POTRAZ, RBZ) that apply to this entity's IT/cyber/AML obligations and should shape the scope?"),
    ("Regulatory", "Will any findings need to be reported to, or may trigger obligations with, a regulator or the Financial Intelligence Unit, and has this been discussed with the client in advance?"),
    ("Fee & quality", "The expected fee is commensurate with the specialist skill and time required, without compromising the quality of the engagement."),
    ("Engagement letter", "The client has agreed to the scope (which domains - cyber security, information security, ICT, AML/CFT - are covered), timeline, responsibilities, access arrangements, and fee basis set out in the engagement letter."),
]
