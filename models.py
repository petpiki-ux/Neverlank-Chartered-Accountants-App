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
# Roles allowed to give the final Engagement Partner sign-off on a workpaper.
# This is a separate, discretionary tier above the ordinary Preparer/Reviewer
# sign-off - the partner can sign off any workpaper "where he sees fit"
# rather than it being a hard gate, but (like a reviewer) can never sign off
# their own work.
PARTNER_SIGNOFF_ROLES = ("partner", "admin")

# The engagement workpaper sections a Partner/Reviewer can raise a review
# Query against (see EngagementQuery below) - every substantive tab on the
# engagement detail page from Planning onward. "Overview" (plain engagement
# fields) and "Documents" (a file list, not itself a workpaper) are
# deliberately excluded. For "substantive", a query is further scoped to one
# audit area (EngagementQuery.area_name) since Substantive Procedures has one
# sign-off per area rather than one for the whole tab.
QUERY_SECTIONS = [
    ("entity", "Understanding the Entity"),
    ("risks", "Risk Assessment"),
    ("planning", "Planning (Materiality)"),
    ("analytical", "Analytical Review"),
    ("checklist", "Checklist"),
    ("substantive", "Substantive Procedures"),
    ("tasks", "Tasks"),
    ("finalisation", "Finalisation (Trial Balance / Financial Statements)"),
]
QUERY_SECTION_KEYS = {key for key, _ in QUERY_SECTIONS}
QUERY_SECTION_LABELS = dict(QUERY_SECTIONS)

# Configurable role permissions: a small set of firm-administration actions
# (managing shared libraries, deleting whole clients/engagements/documents,
# managing team members) that an admin can allow or deny per role from the
# Team > Permissions settings screen, instead of them being hardcoded. This
# is separate from - and never touches - the Preparer/Reviewer/Partner
# sign-off workflow above, which stays exactly as it is regardless of these
# settings. Each entry is (key, label, description, default_roles) where
# default_roles are the roles allowed=True the very first time this is
# seeded - chosen to exactly reproduce this app's behaviour before this
# permissions system existed, so upgrading an existing install changes
# nothing until an admin actually opens the settings screen and changes it.
PERMISSIONS = [
    ("manage_document_templates", "Manage Document Templates",
     "Add, edit or delete templates in the Document Templates library (previously admin/partner only).",
     ("partner", "admin")),
    ("manage_policies", "Manage Policies & Procedures",
     "Add, edit or delete documents in the Policies & Procedures library (previously admin/partner only).",
     ("partner", "admin")),
    ("manage_checklist_templates", "Manage Checklist Templates",
     "Create, edit or delete the checklist templates used to start new engagements (previously unrestricted).",
     tuple(USER_ROLES)),
    ("manage_users", "Manage Team Members",
     "Add or edit team members, including their role and password (previously admin only). "
     "Only an actual admin can ever grant someone the admin role, regardless of this setting.",
     ("admin",)),
    ("delete_clients", "Delete Clients",
     "Delete a client and all of its engagements (previously unrestricted).",
     tuple(USER_ROLES)),
    ("delete_engagements", "Delete Engagements",
     "Delete an engagement and all of its checklist items, risks, documents and tasks (previously unrestricted).",
     tuple(USER_ROLES)),
    ("delete_documents", "Delete Documents",
     "Delete an uploaded working paper/document from an engagement (previously unrestricted).",
     tuple(USER_ROLES)),
]
PERMISSION_KEYS = {p[0] for p in PERMISSIONS}


def user_has_permission(user, key):
    """The one place this app should ever ask "is this role allowed to do
    X?" for the administrative actions in PERMISSIONS above - routes call
    this instead of hardcoding a role check. Admin is always allowed,
    hardcoded here rather than stored, so a mistaken/mischievous toggle on
    the settings screen can never lock every admin out of fixing it."""
    if user.role == "admin":
        return True
    perm = Permission.query.filter_by(role=user.role, permission_key=key).first()
    if perm is not None:
        return perm.allowed
    # No row yet for this (role, key) - e.g. a permission added by an app
    # update before the next seed/migration runs. Fall back to the
    # registry's own default rather than silently denying everything.
    for reg_key, _, _, default_roles in PERMISSIONS:
        if reg_key == key:
            return user.role in default_roles
    return False


# Standard industry list for clients - lets engagements automatically align
# their suggested checklist/substantive-procedure content to the client's
# sector (see the Substantive Procedures module). "Other" is always available
# as a fallback with a free-text field alongside it.
INDUSTRY_OPTIONS = [
    "Agriculture & Agro-processing",
    "Manufacturing",
    "Mining & Extractives",
    "Retail & Wholesale Trade",
    "Construction & Real Estate",
    "Banking & Financial Services",
    "Insurance",
    "Insurance Brokers",
    "Microfinance & Savings/Credit Cooperatives",
    "NGOs & Non-Profit Organisations",
    "Hospitality & Tourism",
    "Transport & Logistics",
    "Telecommunications",
    "Information Technology & Software",
    "Healthcare & Pharmaceuticals",
    "Education",
    "Energy & Utilities",
    "Motor Trade",
    "Professional & Business Services",
    "Public Sector & Parastatals",
    "Media & Entertainment",
    "Other",
]

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

# "Obtain an understanding of the client's business and industry" - a
# structured set of narrative prompts (rather than a scored questionnaire,
# since this is background knowledge to document, not something with a
# numeric answer) covering the standard areas: the entity itself, its
# industry/regulatory/external environment, its accounting policies, its
# objectives/strategies/business risks, and how management measures and
# reviews its own performance. General-purpose starting prompts - adjust the
# wording to match your firm's own methodology.
ENTITY_UNDERSTANDING_FIELDS = [
    ("nature_of_entity", "Nature of the entity",
     "Operations, ownership and governance structure, key investments, financing structure, and any recent or planned changes (acquisitions, restructuring, new products/services)."),
    ("industry_environment", "Industry, regulatory and other external factors",
     "Industry conditions (competition, supply/demand, cyclicality), the regulatory environment, applicable financial reporting framework, and general economic conditions affecting the client."),
    ("accounting_policies", "Accounting policies",
     "Significant accounting policies applied, any changes since the prior period and the reasons for them, and appropriateness for the industry."),
    ("objectives_strategies_risks", "Objectives, strategies and related business risks",
     "The entity's objectives and strategies, and the business risks that could result in a material misstatement of the financial statements."),
    ("performance_measurement", "Measurement and review of financial performance",
     "Key performance indicators, budgets, variance analysis, employee performance measures, and other information management itself uses to assess results."),
]


# Standard audit areas for the system-based Substantive Procedures module -
# each becomes one section of the audit programme for an engagement, with
# its own suggested procedures (seeded from the dictionaries below) and its
# own Preparer/Reviewer/Partner sign-off (see SubstantiveProcedureArea).
AUDIT_AREAS = [
    "Cash and Bank",
    "Trade Receivables",
    "Inventories",
    "Property, Plant and Equipment",
    "Investments",
    "Trade Payables and Accruals",
    "Borrowings and Finance Costs",
    "Revenue",
    "Payroll and Employee Costs",
    "Taxation",
    "Equity and Reserves",
    "Related Party Transactions",
    "Going Concern",
]

# Baseline substantive procedures suggested for every engagement, regardless
# of risk rating or industry - general-purpose starting points covering the
# standard assertions (existence, completeness, valuation, rights and
# obligations, presentation). Always review and tailor to the engagement.
BASELINE_SUBSTANTIVE_PROCEDURES = {
    "Cash and Bank": [
        "Obtain bank confirmations for all bank accounts held during the year and agree confirmed balances to the trial balance/bank reconciliations.",
        "Review bank reconciliations for all accounts and test reconciling items, agreeing outstanding items to subsequent bank statements.",
        "Perform a cash count/confirmation for any material petty cash or cash-in-hand balances.",
        "Test cut-off of significant receipts and payments around year end.",
    ],
    "Trade Receivables": [
        "Circularise a sample of trade receivables (positive confirmation) and follow up non-replies with alternative procedures.",
        "Review the aged receivables listing and evaluate the adequacy of the allowance for expected credit losses/doubtful debts.",
        "Test a sample of post year-end receipts against year-end receivable balances.",
        "Review credit notes issued after year end for evidence of overstated revenue/receivables.",
    ],
    "Inventories": [
        "Attend (or review the results of) the year-end inventory count and reconcile count results to the inventory listing.",
        "Test the valuation of a sample of inventory items against cost records and net realisable value.",
        "Review for slow-moving, obsolete or damaged inventory and assess the adequacy of any write-down/provision.",
        "Test cut-off of goods received/despatched around year end.",
    ],
    "Property, Plant and Equipment": [
        "Agree a sample of additions to supporting invoices/contracts and confirm appropriate capitalisation.",
        "Agree a sample of disposals to sale documentation and recompute the gain/loss on disposal.",
        "Recompute depreciation for a sample of assets and assess the reasonableness of useful lives/rates applied.",
        "Consider whether indicators of impairment exist and, if so, evaluate management's impairment assessment.",
        "Physically inspect a sample of significant assets and confirm existence/condition.",
    ],
    "Investments": [
        "Agree investment holdings to third-party statements/confirmations at year end.",
        "Test the valuation basis applied (fair value, cost, equity method as applicable) and recompute for a sample.",
        "Review for any impairment indicators and assess management's impairment conclusion.",
    ],
    "Trade Payables and Accruals": [
        "Perform a search for unrecorded liabilities by reviewing post year-end payments/invoices.",
        "Circularise a sample of suppliers (or review supplier statements) and reconcile to recorded balances.",
        "Test the completeness and reasonableness of significant accruals.",
        "Test cut-off of purchases around year end.",
    ],
    "Borrowings and Finance Costs": [
        "Confirm outstanding loan/borrowing balances, terms and security directly with lenders.",
        "Recompute finance costs for the year and agree to loan agreements/amortisation schedules.",
        "Review loan covenants for compliance and consider classification (current/non-current) implications of any breach.",
    ],
    "Revenue": [
        "Test a sample of revenue transactions to supporting contracts/invoices/dispatch documentation.",
        "Perform cut-off testing on revenue recognised immediately before and after year end.",
        "Perform analytical procedures on revenue (e.g. by month/product/customer) and investigate significant fluctuations.",
        "Evaluate the appropriateness of the revenue recognition policy applied against the applicable financial reporting framework.",
    ],
    "Payroll and Employee Costs": [
        "Test a sample of payroll transactions to supporting records (contracts, timesheets, statutory returns).",
        "Recompute statutory deductions (PAYE, pension, other) for a sample of employees and agree to remittances.",
        "Perform analytical procedures on payroll costs (e.g. headcount x average pay) and investigate significant variances.",
        "Test for the existence of terminated/fictitious employees still receiving pay.",
    ],
    "Taxation": [
        "Recompute the current tax charge/liability and agree to the tax computation and return.",
        "Assess the recoverability and appropriateness of any deferred tax asset/liability recognised.",
        "Review correspondence with the tax authority for unresolved matters or additional assessments.",
    ],
    "Equity and Reserves": [
        "Agree share capital/share premium movements to statutory records (share register, resolutions).",
        "Agree dividends declared/paid to board resolutions and recompute amounts.",
        "Review the statement of changes in equity for completeness and correct classification of movements.",
    ],
    "Related Party Transactions": [
        "Review minutes, contracts and disclosures for evidence of related party relationships and transactions not otherwise identified.",
        "Test a sample of identified related party transactions for appropriate authorisation, terms and disclosure.",
        "Confirm the completeness of related party disclosures against the applicable financial reporting framework.",
    ],
    "Going Concern": [
        "Evaluate management's going concern assessment, including cash flow forecasts, for the foreseeable future (at least 12 months from year end).",
        "Review for indicators of going concern issues (recurring losses, liquidity problems, covenant breaches, negative equity).",
        "Assess the adequacy of going concern disclosures in the financial statements.",
    ],
}

# Extra procedures added on top of the baseline when the engagement's Risk
# Assessment rating is "High" - extending the nature/timing/extent of
# testing for a higher assessed risk of material misstatement.
HIGH_RISK_EXTRA_PROCEDURES = {
    "Cash and Bank": [
        "Extend bank confirmation coverage to all accounts (including dormant/nil-balance accounts) and obtain confirmations directly rather than relying on client-provided statements alone.",
    ],
    "Trade Receivables": [
        "Increase the receivables circularisation sample size and consider negative confirmations for non-responses to positive confirmations.",
        "Independently recalculate the allowance for doubtful debts using an aging/experience-based model.",
    ],
    "Inventories": [
        "Attend the inventory count in person (rather than reviewing client-performed count results) and perform more extensive test counts.",
        "Consider engaging an expert for specialised or high-value inventory valuation.",
    ],
    "Property, Plant and Equipment": [
        "Obtain independent valuations or engage an expert for significant/specialised assets.",
        "Extend physical verification to a larger sample of assets, including those at remote locations.",
    ],
    "Investments": [
        "Obtain independent valuations for level 2/3 fair value investments rather than relying solely on management's valuation.",
    ],
    "Trade Payables and Accruals": [
        "Extend the unrecorded liabilities search period further beyond year end.",
        "Increase the supplier circularisation/statement reconciliation sample size.",
    ],
    "Borrowings and Finance Costs": [
        "Obtain legal confirmation of loan terms/security in addition to lender confirmations.",
        "Extend covenant compliance testing across the full facility term, not just at year end.",
    ],
    "Revenue": [
        "Extend cut-off testing to a longer period either side of year end.",
        "Perform detailed testing of manual journal entries affecting revenue for evidence of manipulation.",
    ],
    "Payroll and Employee Costs": [
        "Extend testing for ghost/fictitious employees using independent verification (e.g. ID/attendance corroboration).",
        "Increase the sample size for statutory deduction recomputation.",
    ],
    "Taxation": [
        "Engage a tax specialist to review complex tax positions and the adequacy of provisions for uncertain tax treatments.",
    ],
    "Equity and Reserves": [
        "Obtain independent confirmation of share register details from the transfer secretary/registrar.",
    ],
    "Related Party Transactions": [
        "Extend procedures to identify related parties not disclosed by management (review of significant/unusual transactions, board minutes, legal confirmations).",
        "Confirm the terms of significant related party transactions directly with the counterparty.",
    ],
    "Going Concern": [
        "Extend the going concern assessment period and stress-test management's cash flow forecast assumptions.",
        "Consider the need for a material uncertainty related to going concern paragraph and discuss with the engagement partner.",
    ],
}

# Extra procedures added when the client's industry (Client.industry, one of
# INDUSTRY_OPTIONS) makes a particular audit area especially relevant.
# Deliberately not exhaustive - covers the combinations most likely to
# matter; any industry/area combination without an entry here just gets the
# baseline (and, if applicable, high-risk) procedures above.
INDUSTRY_EXTRA_PROCEDURES = {
    "Agriculture & Agro-processing": {
        "Inventories": ["Assess the valuation of biological assets/agricultural produce at fair value less costs to sell (or the industry-appropriate basis) and consider the need for an expert valuation."],
        "Revenue": ["Consider seasonality and weather/climate risk factors when performing analytical review of revenue."],
    },
    "Manufacturing": {
        "Inventories": ["Test standard costing/overhead absorption rates used to value work-in-progress and finished goods."],
        "Property, Plant and Equipment": ["Assess whether plant and machinery carrying values are supported given capacity utilisation and technological obsolescence risk."],
    },
    "Mining & Extractives": {
        "Property, Plant and Equipment": ["Assess the reasonableness of mine/asset useful lives against reserve estimates and life-of-mine plans, and consider the need for an expert."],
        "Going Concern": ["Evaluate commodity price assumptions and reserve/resource estimates used in going concern and impairment assessments."],
    },
    "Retail & Wholesale Trade": {
        "Inventories": ["Consider shrinkage/theft risk in inventory valuation and review the adequacy of the shrinkage provision."],
        "Revenue": ["Test point-of-sale system controls and reconcile daily takings to bank deposits for a sample of days."],
    },
    "Banking & Financial Services": {
        "Trade Receivables": ["Test the expected credit loss (ECL) model and staging of loans/advances in line with IFRS 9, including a sample of individually assessed impairments."],
        "Cash and Bank": ["Confirm statutory reserve/liquidity requirements held with the central bank and assess compliance."],
    },
    "Insurance": {
        "Trade Payables and Accruals": ["Test the adequacy of insurance/claims reserves (outstanding claims, IBNR) with reference to actuarial valuations."],
        "Revenue": ["Test premium recognition and unearned premium reserve calculations."],
    },
    "Insurance Brokers": {
        "Cash and Bank": ["Confirm that client/premium monies are held in a segregated trust or premium bank account separate from the firm's own funds, and reconcile the trust account to the underlying client/policy listing."],
        "Trade Receivables": ["Test premiums due from clients and brokerage/commission income due from insurers for recoverability and correct ageing."],
        "Revenue": ["Test the recognition of brokerage/commission income (including any profit or contingent commissions) against underlying policy placements and insurer statements."],
        "Trade Payables and Accruals": ["Test amounts held/due to insurers for premiums collected on their behalf but not yet remitted, and confirm timely remittance after year end."],
    },
    "Microfinance & Savings/Credit Cooperatives": {
        "Trade Receivables": ["Test loan portfolio at risk (PAR) classification and provisioning against the entity's credit policy and regulatory guidelines."],
    },
    "NGOs & Non-Profit Organisations": {
        "Revenue": ["Test donor/grant income recognition against grant agreement terms and conditions, and confirm restricted vs unrestricted classification."],
        "Trade Payables and Accruals": ["Confirm compliance with donor reporting and fund utilisation restrictions."],
    },
    "Construction & Real Estate": {
        "Revenue": ["Test percentage-of-completion/input-method calculations for long-term contracts and recompute contract revenue and costs for a sample."],
        "Trade Receivables": ["Assess the recoverability of retention receivables and amounts due from customers on construction contracts."],
    },
    "Hospitality & Tourism": {
        "Revenue": ["Consider seasonality in analytical review of revenue and test occupancy/average-rate based revenue calculations for a sample of periods."],
    },
    "Telecommunications": {
        "Revenue": ["Test multiple-element revenue arrangements (e.g. handset plus airtime bundles) for appropriate allocation and recognition under the applicable standard."],
    },
    "Healthcare & Pharmaceuticals": {
        "Inventories": ["Assess expiry dating and obsolescence provisioning for pharmaceutical/medical inventory."],
    },
    "Public Sector & Parastatals": {
        "Trade Payables and Accruals": ["Confirm compliance with public procurement regulations for a sample of significant payables/commitments."],
    },
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
    # Company registration number (e.g. a companies-office registration
    # number). Optional - not every client is a registered company - but
    # when it IS given, clients.py checks it against every other client's
    # company_number before saving, so the same company can't accidentally
    # be onboarded twice under two different client records.
    company_number = db.Column(db.String(80))
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

    # Whether this engagement must clear Client Acceptance & Continuance
    # (see ClientAcceptance below) before Understanding the Entity and every
    # later tab can be opened. Defaults True for every engagement created
    # from here on. Existing engagements from before this feature shipped
    # are migrated to False (see _add_missing_columns in app.py) so they are
    # completely unaffected - nobody has to retroactively fill in an
    # acceptance record for work already under way.
    acceptance_required = db.Column(db.Boolean, default=True, nullable=False)

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


def user_can_access_engagement(user, engagement):
    """Confidentiality gate: everyone except Admin can only open an
    engagement (any tab, its documents, its workpapers) if they're actually
    on it - its Partner, its Manager, or listed in its Team. Admin always
    sees everything, same as user_has_permission's own admin bypass.
    Engagements a user can't access also don't appear in their dashboard,
    engagements list, or the firm-wide Queries board."""
    if user.role == "admin":
        return True
    if engagement.partner_id == user.id or engagement.manager_id == user.id:
        return True
    return user in engagement.team_members


def engagement_acceptance_cleared(engagement):
    """Whether this engagement's Client Acceptance & Continuance gate (see
    ClientAcceptance below) has been cleared, i.e. whether Understanding the
    Entity and every later tab may be opened. An engagement that doesn't
    require acceptance at all (every engagement created before this feature
    shipped - see Engagement.acceptance_required) is always cleared. A
    engagement that does require it is only cleared once its
    ClientAcceptance record has an Engagement Partner sign-off AND the
    recorded decision is "Accepted" (a declined acceptance, or one still
    awaiting the partner, keeps the gate shut)."""
    if not engagement.acceptance_required:
        return True
    ca = engagement.client_acceptance
    return bool(ca and ca.partner_signed_at and ca.decision == "Accepted")


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
    # Engagement Partner sign-off: a third, separate tier the partner can
    # apply to any workpaper at their discretion - not gated on the
    # reviewer step, and never the same person as the preparer.
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None


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
    # Optional link to the Substantive Procedures section this working paper
    # was filed under (e.g. "Cash and Bank") - lets evidence supporting a
    # section's procedures be filed and found alongside them, rather than
    # only in the engagement's general Documents tab. Nullable: a document
    # doesn't have to belong to any particular audit area.
    substantive_area_id = db.Column(db.Integer, db.ForeignKey("substantive_procedure_area.id"))

    uploaded_by = db.relationship("User")
    substantive_area = db.relationship("SubstantiveProcedureArea", backref=db.backref("documents", lazy=True, order_by="Document.uploaded_at.desc()"))


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
    # Engagement Partner sign-off: a third, separate tier the partner can
    # apply to any workpaper at their discretion - never the same person
    # as the preparer.
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < date.today() and self.status != "Done")

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None


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
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("risk_assessment", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

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
        return all((v or "").strip() for v in self.field_values)

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<EntityUnderstanding engagement={self.engagement_id}>"


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
    label = db.Column(db.String(150), nullable=False)  # e.g. "Revenue", "Gross profit"
    prior_amount = db.Column(db.Float)
    current_amount = db.Column(db.Float)
    explanation = db.Column(db.Text)
    # "manual" (typed in by hand, the only option before this column existed)
    # or "auto" (last written by "Generate from Trial Balance" - see
    # generate_analytical_review_from_trial_balance in engagements.py).
    # Regenerating only overwrites the amounts on "auto" lines that share a
    # label with a freshly computed figure, so a manually-typed line, and
    # any explanation already typed against an auto line, both survive a
    # re-generate.
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


# ---------- Trial Balance import & IAS 1 Financial Statements ----------

class COAMapping(db.Model):
    """A remembered mapping from one of a client's trial balance account
    names to an IAS 1 financial statement category (see financials.py) -
    reusable across every engagement/period for that client, so a repeat
    engagement's trial balance mostly auto-maps itself. Keyed on the
    account name as typed/imported (matched case-insensitively)."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50), nullable=False)
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

    engagement = db.relationship("Engagement", backref=db.backref("trial_balance", uselist=False, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
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
    account_code = db.Column(db.String(50))
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50))  # code from financials.FS_CATEGORIES, or "excluded" / blank if unmapped
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
    reference = db.Column(db.String(50))  # e.g. "AJE 1"
    description = db.Column(db.Text)

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


class AuditAdjustmentLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    adjustment_id = db.Column(db.Integer, db.ForeignKey("audit_adjustment.id"), nullable=False)
    account_name = db.Column(db.String(200), nullable=False)
    fs_category = db.Column(db.String(50), nullable=False)
    debit = db.Column(db.Float, default=0.0)
    credit = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f"<AuditAdjustmentLine {self.account_name}>"


class FinancialStatements(db.Model):
    """The Financial Statements pack for an engagement - mostly a sign-off
    and notes wrapper, since the statements themselves are computed live
    from the adjusted TrialBalance (see financials.py) rather than
    stored."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)
    basis_of_preparation = db.Column(db.Text)

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

    def __repr__(self):
        return f"<SubstantiveProcedureItem area={self.area_id}>"


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

    def __repr__(self):
        return f"<Message {self.id} {self.subject!r} from={self.sender_id}>"


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

    user = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("message_id", "user_id", name="uq_message_recipient"),)

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def is_recalled(self):
        return self.recalled_at is not None

    def __repr__(self):
        return f"<MessageRecipient message={self.message_id} user={self.user_id}>"


# ---------- Client Acceptance & Continuance (pre-engagement gate) ----------

CLIENT_ACCEPTANCE_DECISIONS = ["Pending", "Accepted", "Declined"]


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

    # 6. Engagement letter - scope/timeline/responsibilities/fees, signed by
    # the client. The signed copy is filed as an ordinary Document (see
    # acceptance.py's upload route) and linked here.
    engagement_letter_notes = db.Column(db.Text)
    engagement_letter_sent_at = db.Column(db.DateTime)
    engagement_letter_signed_at = db.Column(db.DateTime)
    engagement_letter_document_id = db.Column(db.Integer, db.ForeignKey("document.id"))

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

    engagement = db.relationship("Engagement", backref=db.backref("client_acceptance", uselist=False, cascade="all, delete-orphan"))
    engagement_letter_document = db.relationship("Document")
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
    def items_complete(self):
        """All six items have at least been assessed (not necessarily
        favourably - an unfavourable background check is a reason to
        decline, not a reason the form is "incomplete"). Required before a
        Partner can record the decision as sign-off."""
        predecessor_done = self.predecessor_not_applicable or self.predecessor_contacted is not None
        return all([
            self.background_check_satisfactory is not None,
            self.independence_threats_identified is not None,
            predecessor_done,
            self.competence_confirmed is not None,
            self.aml_kyc_completed is not None,
            self.engagement_letter_signed_at is not None,
        ])

    def __repr__(self):
        return f"<ClientAcceptance engagement={self.engagement_id} decision={self.decision}>"


# ---------- Native Invoicing ----------

INVOICE_STATUSES = ["Draft", "Sent", "Paid", "Cancelled"]
INVOICE_CURRENCIES = ["USD", "ZWG"]


class Invoice(db.Model):
    """A client invoice. Line items are manual (see InvoiceLineItem) rather
    than generated from logged time in this first version. Always billed to
    a Client; the link to a specific Engagement is optional (a firm may
    invoice a client for something not tied to one particular engagement,
    e.g. a combined fee note)."""
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(30), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))

    currency = db.Column(db.String(10), default="USD", nullable=False)
    # Percentage, e.g. 15.0 - 0 is a valid, explicit choice (zero-rated /
    # exempt supply), distinct from VAT simply not having been set.
    vat_pct = db.Column(db.Float, default=15.0, nullable=False)
    status = db.Column(db.String(20), default="Draft", nullable=False)

    issue_date = db.Column(db.Date, default=date.today)
    due_date = db.Column(db.Date)
    bill_to = db.Column(db.Text)  # snapshot of the billing name/address at issue time
    notes = db.Column(db.Text)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime)

    # client_id is required (NOT NULL), so deleting a Client must cascade to
    # its invoices too (same convention as Client.engagements below) -
    # there's no valid "orphaned" invoice with no client. engagement_id is
    # optional, so deleting an Engagement instead just detaches its
    # invoices (no cascade) - the invoice, a financial record, survives
    # with engagement_id set back to NULL.
    client = db.relationship("Client", backref=db.backref("invoices", lazy=True, cascade="all, delete-orphan", order_by="Invoice.id.desc()"))
    engagement = db.relationship("Engagement", backref=db.backref("invoices", lazy=True, order_by="Invoice.id.desc()"))
    created_by = db.relationship("User")
    lines = db.relationship(
        "InvoiceLineItem", backref="invoice", lazy=True,
        cascade="all, delete-orphan", order_by="InvoiceLineItem.id",
    )

    @property
    def subtotal(self):
        return sum((l.quantity or 0) * (l.unit_price or 0) for l in self.lines)

    @property
    def vat_amount(self):
        return self.subtotal * (self.vat_pct or 0) / 100.0

    @property
    def total(self):
        return self.subtotal + self.vat_amount

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < date.today() and self.status not in ("Paid", "Cancelled"))

    def __repr__(self):
        return f"<Invoice {self.invoice_number}>"


class InvoiceLineItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Float, default=1.0)
    unit_price = db.Column(db.Float, default=0.0)

    @property
    def line_total(self):
        return (self.quantity or 0) * (self.unit_price or 0)

    def __repr__(self):
        return f"<InvoiceLineItem {self.description!r}>"
