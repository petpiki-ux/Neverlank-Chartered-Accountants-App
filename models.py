from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

from extensions import db


ENGAGEMENT_TYPES = ["Audit", "Assurance", "Consulting", "Secretarial", "Investigative Engagement"]
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
    "Petroleum & Fuel Distribution",
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

# Forensic-specific Risk Assessment questionnaire, used in place of
# RISK_LIKELIHOOD_QUESTIONS/RISK_IMPACT_QUESTIONS above for engagements of
# type "Investigative Engagement" (see RiskAssessment.likelihood_answers/
# impact_answers below, which branch on engagement.type). Same 1-5 scoring
# and Low/Medium/High banding, but built around the Fraud Triangle
# (Incentive/Pressure, Opportunity, Rationalisation) rather than ordinary
# audit risk of material misstatement - a financial statement audit asks
# "how likely is an honest error", a forensic engagement asks "how likely
# is someone here committing, and getting away with, fraud". Deliberately
# does not apply standard materiality thresholds to the "significance"
# question - a small number can still indicate a systemic fraud scheme.
FORENSIC_RISK_LIKELIHOOD_QUESTIONS = [
    ("q_fraud_incentive", "Incentive/Pressure - how strong is the incentive or pressure to commit fraud (financial distress, unrealistic performance targets, personal greed)?", [
        "No evident pressure - financially stable, targets are realistic",
        "Minor pressure - some ambitious targets or mild financial strain",
        "Moderate pressure - noticeable financial strain or aggressive targets on certain individuals/units",
        "Significant pressure - clear financial distress, unrealistic targets, or known personal financial difficulty on key individuals",
        "Severe pressure - the entity or key individuals face acute financial distress, personal ruin, or extreme performance pressure",
    ]),
    ("q_fraud_opportunity", "Opportunity - how much opportunity exists to commit and conceal fraud (weak internal controls, poor segregation of duties, management override capability)?", [
        "Strong controls, clear segregation of duties, no realistic override capability",
        "Generally sound controls with only minor gaps",
        "Noticeable control weaknesses or overlapping duties in some areas",
        "Significant control weaknesses, poor segregation of duties, or meaningful override capability",
        "Weak or absent controls, duties concentrated in one person, and management can override controls unchecked",
    ]),
    ("q_fraud_rationalization", "Rationalisation - how tolerant is the culture of cutting corners, and are there disgruntled or underpaid employees who might feel justified?", [
        "Strong ethical culture and clear tone at the top; no known grievances",
        "Generally sound culture; isolated, minor grumbling",
        "Some tolerance of cutting corners, or a few known grievances",
        "A noticeable \"everyone does it\" culture, or several disgruntled/underpaid employees",
        "Widespread tolerance of cutting corners and significant, well-known grievances among staff",
    ]),
    ("q_fraud_override", "Management override - how easily could executives or supervisors bypass existing system controls to perpetrate or conceal fraud?", [
        "No override capability - controls and approvals apply equally at every level",
        "Limited override capability, subject to independent review",
        "Some override capability with inconsistent independent review",
        "Considerable override capability with little independent oversight",
        "Unchecked override capability - management can bypass controls with no independent oversight at all",
    ]),
    ("q_fraud_intel", "Prior intelligence - what do whistleblower/hotline tips, past audit findings, or industry-specific fraud trends suggest about this engagement?", [
        "No tips, findings or trends suggesting a heightened risk here",
        "Isolated, unsubstantiated chatter with no supporting detail",
        "A specific tip or finding exists but is vague or unconfirmed",
        "A credible tip, finding, or well-known industry trend directly points to this area",
        "Multiple credible tips/findings, or this exact scheme is a known, active trend in this industry",
    ]),
]

FORENSIC_RISK_IMPACT_QUESTIONS = [
    ("q_scheme_revenue_gl", "Revenue & journal entries - how significant is the exposure to revenue recognition fraud or general ledger manipulation (channel stuffing, bill-and-hold, fictitious sales, manual/backdated/rounded-dollar entries)?", [
        "No plausible exposure identified",
        "Minor exposure - isolated, low-value transactions only",
        "Some exposure in a defined area or period",
        "Considerable exposure across multiple accounts/periods",
        "Severe exposure - pervasive, high-value, or long-running",
    ]),
    ("q_scheme_asset_misappropriation", "Asset misappropriation - how significant is the exposure to theft or misuse of assets (inventory shrinkage, unauthorised write-offs, missing scrap, cash theft)?", [
        "No plausible exposure identified",
        "Minor exposure - isolated, low-value items only",
        "Some exposure in a defined area or period",
        "Considerable exposure across multiple assets/locations",
        "Severe exposure - pervasive, high-value, or long-running",
    ]),
    ("q_scheme_procurement_vendor", "Procurement & vendor management - how significant is the exposure to shell companies, bid rigging/kickbacks, or duplicate payments?", [
        "No plausible exposure identified",
        "Minor exposure - a small number of vendors/contracts",
        "Some exposure across a defined category of spend",
        "Considerable exposure across multiple vendors/contracts",
        "Severe exposure - pervasive across procurement, or very high-value contracts involved",
    ]),
    ("q_scheme_payroll_expenses", "Payroll & expenses - how significant is the exposure to ghost employees or travel & entertainment fraud?", [
        "No plausible exposure identified",
        "Minor exposure - isolated, low-value items only",
        "Some exposure in a defined department or period",
        "Considerable exposure across multiple departments/individuals",
        "Severe exposure - pervasive, high-value, or long-running",
    ]),
    ("q_scheme_significance", "Overall significance - if any of the above were substantiated, how significant would the financial or reputational impact be? (Do not apply standard materiality thresholds here - a small amount can still indicate a systemic fraud scheme.)", [
        "Minor - immaterial financial impact, no reputational exposure",
        "Limited - modest financial impact, low reputational exposure",
        "Moderate - noticeable financial impact and/or reputational exposure",
        "Serious - significant financial impact and/or public/regulatory reputational exposure",
        "Severe - potential for criminal referral, major financial loss, or serious reputational damage",
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

# Seeded instead of the five free-text ENTITY_UNDERSTANDING_FIELDS above when
# the engagement's type is "Investigative Engagement" (see
# engagements.seed_entity_checklist) - understanding the business for a
# forensic investigation is about the specifics of the assignment (why now,
# what's alleged, who's involved, what data exists, who to loop in, and
# whether it's safe to proceed), not the ISA 315-style entity/industry/
# accounting-policy prompts the general fields ask for. Presented the same
# way as the Client Acceptance detailed checklist - tick Yes/No/N-A and add
# a comment on each question (see EntityUnderstandingChecklistItem below).
FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS = [
    ("Objectives & Scope of the Engagement", "What is the core trigger for this investigation (e.g. whistleblower report, regulatory red flag, internal audit finding, sudden cash shortfall)?"),
    ("Objectives & Scope of the Engagement", "What specific allegations or suspicions need to be investigated (e.g. asset misappropriation, financial statement fraud, bribery and corruption, cyber fraud)?"),
    ("Objectives & Scope of the Engagement", "What is the precise target period for the investigation?"),
    ("Objectives & Scope of the Engagement", "Which business units, physical locations, or subsidiaries are included in the scope?"),
    ("Objectives & Scope of the Engagement", "Who are the known subjects or persons of interest, if any?"),
    ("Objectives & Scope of the Engagement", "What is the ultimate goal of the deliverable - internal disciplinary action, filing an insurance claim, submission to law enforcement for criminal prosecution, or civil litigation to recover assets?"),
    ("Legal, Regulatory & Governance Context", "Is this engagement protected by attorney-client privilege - are we being retained directly by the client, or by their outside legal counsel, to protect the work product?"),
    ("Legal, Regulatory & Governance Context", "What specific laws or regulations govern the subject matter (e.g. anti-corruption legislation, local anti-money laundering laws, specific industry regulations)?"),
    ("Legal, Regulatory & Governance Context", "What are the client's internal policies regarding employee privacy - can we search corporate emails and personal devices used for work without explicit consent?"),
    ("Legal, Regulatory & Governance Context", "Are there data privacy laws (e.g. the Cyber and Data Protection Act, GDPR, or other local equivalents) that restrict data transfer across borders?"),
    ("Legal, Regulatory & Governance Context", "Is there an active or pending lawsuit related to this matter?"),
    ("Data, Systems & Access Checklist", "Financial systems: what ERP or accounting software does the company use, and can they provision read-only administrator access?"),
    ("Data, Systems & Access Checklist", "Communication channels: where do employees communicate (e.g. Microsoft Teams, Slack, corporate email, WhatsApp on company phones)?"),
    ("Data, Systems & Access Checklist", "Data preservation: has a legal hold or data preservation order been issued, and have IT backups been frozen to prevent wiping or altering logs?"),
    ("Data, Systems & Access Checklist", "Supporting documentation: where are physical or digital invoices, receipts, contracts, and bank statements stored, and who controls access to them?"),
    ("Data, Systems & Access Checklist", "External data: will bank confirmations, vendor verifications, or customer circularisations be required and permitted?"),
    ("Stakeholders, Logistics & Timeline", "Who is the primary point of contact for the investigation, and who receives updates?"),
    ("Stakeholders, Logistics & Timeline", "Who needs to be kept out of the loop to maintain confidentiality (e.g. is the CFO or Head of HR a subject of suspicion)?"),
    ("Stakeholders, Logistics & Timeline", "Will the investigation be overt (everyone knows we are there) or covert (disguised as a routine internal audit or IT upgrade)?"),
    ("Stakeholders, Logistics & Timeline", "What is the target deadline for the preliminary findings and the final report?"),
    ("Stakeholders, Logistics & Timeline", "What budget constraints or billing caps apply to this phase of the engagement?"),
    ("Risk Assessment & Safety", "Is there any physical safety risk to the investigative team (e.g. organised crime, high-level corruption, a hostile work environment)?"),
    ("Risk Assessment & Safety", "Is there a risk of collusion - could the subjects destroy evidence if they realise an investigation is underway?"),
    ("Risk Assessment & Safety", "Does the forensic team have any conflict of interest with the client, its competitors, or the suspected individuals?"),
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
    "Petroleum & Fuel Distribution": {
        "Inventories": ["Test fuel stock reconciliations (dip/meter readings vs. book stock) and assess the adequacy of provisions for evaporation, temperature variance and shrinkage losses."],
        "Revenue": ["Test compliance with regulated fuel pricing (where applicable) and reconcile pump/meter sales data to recorded revenue for a sample of days."],
        "Property, Plant and Equipment": ["Assess the condition and remaining useful life of storage tanks, pipelines and dispensing equipment, and consider environmental decommissioning/rehabilitation obligations."],
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

# Forensic equivalent of AUDIT_AREAS/BASELINE_SUBSTANTIVE_PROCEDURES above,
# used for the Substantive Procedures tab on an Investigative Engagement
# instead of the financial-statement-line-item areas, which don't apply to
# a fraud investigation. Grouped into the four buckets investigative
# procedures are generally organised into, by the type of evidence
# required, rather than by balance sheet/income statement caption.
# HIGH_RISK_EXTRA_PROCEDURES / INDUSTRY_EXTRA_PROCEDURES above are
# deliberately not applied on top of these - they're keyed to AUDIT_AREAS
# captions and don't map onto these categories - so an Investigative
# Engagement's suggested procedures are always just this baseline list.
FORENSIC_SUBSTANTIVE_AREAS = [
    "Advanced Data Analytics & Forensic Technology",
    "Asset Tracing & Financial Reconstruction",
    "Document Examination & Verification",
    "Physical & Observational Procedures",
]

FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES = {
    "Advanced Data Analytics & Forensic Technology": [
        "Perform a Benford's Law analysis on transaction data (e.g. invoice amounts, journal entries) to identify anomalies in number patterns consistent with manufactured or fabricated figures.",
        "Search emails, chat logs and deleted/recovered files for red-flag keywords (e.g. \"override\", \"off-books\", \"hide\", \"urgent wire\", \"don't tell\", \"cash only\").",
        "Run gap and duplicate-testing scripts to identify missing cheque/invoice number sequences, duplicate invoice numbers, and identical payment amounts made to different vendors.",
        "Extract and review document/file metadata (creation and modification dates, authorship, last-saved-by) on key records to identify backdating or after-the-fact alteration.",
        "Use fuzzy/similarity matching on vendor and employee master data to identify near-duplicate names that may indicate a shell company or a ghost employee.",
    ],
    "Asset Tracing & Financial Reconstruction": [
        "Apply the net worth method: reconstruct an individual's financial profile from changes in assets and liabilities over the period to identify unexplained wealth.",
        "Trace the exact path of funds from the entity's operating account(s) through to ultimate beneficiaries, including any shell companies or offshore accounts identified.",
        "Perform a lifestyle audit comparing an individual's known legal income against their actual spending, real estate purchases, vehicles and other high-value assets.",
        "Reconstruct incomplete, deleted or destroyed accounting records from surviving source documents and third-party data where the general ledger itself cannot be relied upon.",
    ],
    "Document Examination & Verification": [
        "Vouch and trace a sample of specific suspicious transactions back to physical source documents (contracts, purchase orders, shipping/delivery receipts) to verify they actually occurred.",
        "Submit external confirmation requests directly to independent third parties (banks, suppliers, customers) to confirm balances and transaction terms, bypassing internal staff entirely.",
        "Search public/corporate registries for undisclosed related parties or conflicts of interest, such as an employee or a family member secretly owning a vendor or customer.",
        "Examine questioned documents for signs of forgery, alteration, or inconsistent fonts, ink or signatures, engaging a qualified document examiner where warranted.",
    ],
    "Physical & Observational Procedures": [
        "Perform surprise (unannounced) cash counts and/or inventory counts of high-value stock or cash drawers to identify misappropriation before records can be altered.",
        "Conduct site visitations to physically verify that a vendor genuinely exists and is not a \"ghost vendor\" operating out of a P.O. Box or residential address.",
        "Physically observe and photograph key locations, assets or processes relevant to the allegations, maintaining a clear chain of custody for anything collected as evidence.",
        "Where relevant and legally permissible, conduct discreet observation of a subject's day-to-day activities to corroborate (or contradict) their stated role, access and conduct.",
    ],
}

# Working-paper reference codes, in the traditional audit-file convention
# (a short letter/code prefix per section, e.g. "B" for Cash, "B-1" for the
# first working paper within it) - shown next to each area's heading on the
# Substantive Procedures tab and embedded in the generated Word/Excel
# workpapers, so a paper reviewer can cite "see B-2" the same way they would
# in a paper file. AUDIT_AREA_REFERENCES runs B through N for the 13
# AUDIT_AREAS (A is conventionally reserved for the index/lead schedule,
# which this app doesn't have a separate tab for). FORENSIC_AREA_REFERENCES
# uses short mnemonic codes instead of single letters, since there are only
# four forensic categories and a mnemonic is easier to recall in a report.
AUDIT_AREA_REFERENCES = {area: chr(ord("B") + i) for i, area in enumerate(AUDIT_AREAS)}

FORENSIC_AREA_REFERENCES = {
    "Advanced Data Analytics & Forensic Technology": "DA",
    "Asset Tracing & Financial Reconstruction": "AT",
    "Document Examination & Verification": "DE",
    "Physical & Observational Procedures": "PO",
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
    def likelihood_questions(self):
        """The question set actually in play for this record - the Fraud
        Triangle questionnaire on an Investigative Engagement, the ordinary
        audit questionnaire otherwise. Everything below (likelihood_answers,
        is_complete, likelihood/impact/score/rating) is driven from this and
        impact_questions, so the two engagement flavours never mix."""
        return FORENSIC_RISK_LIKELIHOOD_QUESTIONS if self._is_forensic else RISK_LIKELIHOOD_QUESTIONS

    @property
    def impact_questions(self):
        return FORENSIC_RISK_IMPACT_QUESTIONS if self._is_forensic else RISK_IMPACT_QUESTIONS

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
        """On an Investigative Engagement (see FORENSIC_ENTITY_UNDERSTANDING_
        CHECKLIST_ITEMS above), the five free-text fields aren't shown at all -
        completion instead means every detailed checklist question has been
        answered. Every other engagement type keeps the original all-fields-
        filled-in check."""
        if self.engagement and self.engagement.type == "Investigative Engagement":
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

    entity_understanding = db.relationship(
        "EntityUnderstanding",
        backref=db.backref("checklist_items", lazy=True, cascade="all, delete-orphan", order_by="EntityUnderstandingChecklistItem.order"),
    )
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<EntityUnderstandingChecklistItem {self.item_text!r} response={self.response!r}>"


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


# Evidence-gathering phase statuses for AuditStrategy below - reuses the
# same vocabulary as CHECKLIST_STATUSES so the UI stays consistent, but
# named separately since the two lists are conceptually different things
# and CHECKLIST_STATUSES may not always evolve in lockstep with this one.
AUDIT_STRATEGY_PHASE_STATUSES = CHECKLIST_STATUSES


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
        """All items have at least been assessed (not necessarily
        favourably - an unfavourable background check is a reason to
        decline, not a reason the form is "incomplete"). Required before a
        Partner can record the decision as sign-off. Investigative
        Engagements use the four Forensic Audit Client Acceptance
        Questionnaire sections instead of the five generic ones (see the
        columns above) - every other engagement type keeps the original
        six-item check."""
        if self.engagement and self.engagement.type == "Investigative Engagement":
            return all([
                self.conflict_threat_clear is not None,
                self.edd_completed is not None,
                self.legal_evidence_satisfactory is not None,
                self.competence_scope_confirmed is not None,
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

    def __repr__(self):
        return f"<ClientAcceptance engagement={self.engagement_id} decision={self.decision}>"


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


class ClientAcceptanceChecklistItem(db.Model):
    """A single tick + comment line within a Client Acceptance record,
    letting the team work through a detailed list of individual
    considerations - beyond the six broad narrative areas above - and
    record a Yes/No/N-A response with a supporting comment for each. The
    aggregate of these responses is summarised in
    ClientAcceptance.checklist_assessment to help (not replace) the
    Partner's accept/decline decision."""
    id = db.Column(db.Integer, primary_key=True)
    client_acceptance_id = db.Column(db.Integer, db.ForeignKey("client_acceptance.id"), nullable=False)
    section = db.Column(db.String(100))
    item_text = db.Column(db.String(300), nullable=False)
    response = db.Column(db.String(10), default="")  # "" = not yet assessed, "Yes", "No", "N/A"
    comment = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client_acceptance = db.relationship(
        "ClientAcceptance",
        backref=db.backref("checklist_items", lazy=True, cascade="all, delete-orphan", order_by="ClientAcceptanceChecklistItem.order"),
    )
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<ClientAcceptanceChecklistItem {self.item_text!r} response={self.response!r}>"


# ---------- Finalisation checklist ("what's left to close this engagement") ----------

# Standard file-closing checklist for every engagement type EXCEPT
# Investigative Engagements (see FORENSIC_FINALISATION_CHECKLIST_ITEMS
# below) - the standard steps a financial-statement audit/assurance/
# consulting/secretarial engagement works through before the file can be
# archived. Same tick (Yes/No/N-A) + comment pattern as every other
# checklist in this app.
DEFAULT_FINALISATION_CHECKLIST_ITEMS = [
    ("Subsequent Events & Going Concern", "Has a subsequent events review been performed through to the date of the report, with nothing further requiring adjustment or disclosure?"),
    ("Subsequent Events & Going Concern", "Has the going concern conclusion been reassessed in light of the final (adjusted) financial statements?"),
    ("Overall Conclusions", "Has the summary of uncorrected misstatements been evaluated, individually and in aggregate, against overall and performance materiality?"),
    ("Overall Conclusions", "Has an overall analytical review of the final financial statements been performed, with the results consistent with our understanding of the entity?"),
    ("Overall Conclusions", "Have all significant risks and key audit/engagement matters been addressed and the conclusions documented?"),
    ("Overall Conclusions", "Has the engagement partner formed and documented an overall conclusion on the engagement?"),
    ("Management Communication", "Has the management representation letter been obtained, signed and dated on or before the date of the report?"),
    ("Management Communication", "Has communication with those charged with governance been completed (significant findings, control deficiencies, etc.)?"),
    ("File Completion & Archiving", "Have all working papers been reviewed and cleared, with every review point resolved?"),
    ("File Completion & Archiving", "Has independence and quality control compliance been reconfirmed as at the date of the report?"),
    ("File Completion & Archiving", "Is the engagement file assembled and ready for the archiving deadline?"),
]

# Forensic-specific finalisation checklist for Investigative Engagements -
# closing out an investigation looks nothing like closing out a financial
# statement audit, so this replaces the list above entirely for that
# engagement type (see acceptance/entity-understanding/risk-assessment/
# substantive-procedures above for the same type-branching pattern).
FORENSIC_FINALISATION_CHECKLIST_ITEMS = [
    ("Findings & Conclusions", "Has the preliminary fraud theory (see Audit Strategy) been tested against the evidence gathered and confirmed, revised or ruled out?"),
    ("Findings & Conclusions", "Has the quantum of loss/exposure been calculated and is it fully supported by the underlying evidence?"),
    ("Findings & Conclusions", "Have the root-cause control weaknesses been identified and documented for remediation recommendations?"),
    ("Reporting & Referral", "Has the final forensic report been drafted, reviewed and approved by the engagement partner?"),
    ("Reporting & Referral", "Has the reporting destination been reconfirmed (Audit Committee/Board/law enforcement/insurance), with the report meeting their requirements?"),
    ("Reporting & Referral", "Has a decision on referral to law enforcement, insurers, or regulators been made, documented, and taken with legal counsel's input?"),
    ("Evidence & Chain of Custody", "Is all evidence collected logged, indexed, and its chain of custody documented end to end?"),
    ("Evidence & Chain of Custody", "Has original evidence been secured or returned appropriately, with working copies retained per the firm's retention policy?"),
    ("File Completion", "Have interview notes/statements been finalised, signed where applicable, and filed?"),
    ("File Completion", "Is the engagement file assembled, reviewed, and ready for archiving, with confidentiality/access restrictions reconfirmed?"),
]


class FinalisationChecklist(db.Model):
    """One per engagement - the "what's left to close this engagement"
    checklist shown on the Finalisation tab. Seeded from DEFAULT_
    FINALISATION_CHECKLIST_ITEMS or FORENSIC_FINALISATION_CHECKLIST_ITEMS
    above depending on engagement type, then ticked through item by item
    (see FinalisationChecklistItem below) - same tick + comment + advisory
    summary pattern as ClientAcceptance/EntityUnderstanding's checklists,
    plus its own overall Preparer/Reviewer/Partner sign-off."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("finalisation_checklist", uselist=False, cascade="all, delete-orphan"))
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
        items = list(self.checklist_items)
        return bool(items) and all(i.response for i in items)

    @property
    def checklist_assessment(self):
        """Advisory-only read of the checklist, same pattern as
        ClientAcceptance.checklist_assessment / EntityUnderstanding.
        checklist_assessment above - never gates anything by itself, just a
        plain-language pointer to what still needs attention before the
        file can be archived."""
        items = list(self.checklist_items)
        total = len(items)
        if total == 0:
            return {"label": "No checklist items added yet.", "level": "muted", "flagged": 0, "outstanding": 0, "total": 0}
        flagged = sum(1 for i in items if i.response == "No")
        outstanding = sum(1 for i in items if not i.response)
        if flagged:
            label = f"{flagged} of {total} item{'s' if total != 1 else ''} flagged - not ready to close."
            level = "danger"
        elif outstanding:
            label = f"{outstanding} of {total} item{'s' if total != 1 else ''} not yet assessed."
            level = "warning"
        else:
            label = f"All {total} item{'s' if total != 1 else ''} confirmed - ready to close."
            level = "success"
        return {"label": label, "level": level, "flagged": flagged, "outstanding": outstanding, "total": total}

    def __repr__(self):
        return f"<FinalisationChecklist engagement={self.engagement_id}>"


class FinalisationChecklistItem(db.Model):
    """A single tick + comment line within a FinalisationChecklist - see
    DEFAULT_FINALISATION_CHECKLIST_ITEMS / FORENSIC_FINALISATION_CHECKLIST_
    ITEMS above."""
    id = db.Column(db.Integer, primary_key=True)
    finalisation_checklist_id = db.Column(db.Integer, db.ForeignKey("finalisation_checklist.id"), nullable=False)
    section = db.Column(db.String(150))
    item_text = db.Column(db.Text, nullable=False)
    response = db.Column(db.String(10), default="")  # "" = not yet assessed, "Yes", "No", "N/A"
    comment = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    finalisation_checklist = db.relationship(
        "FinalisationChecklist",
        backref=db.backref("checklist_items", lazy=True, cascade="all, delete-orphan", order_by="FinalisationChecklistItem.order"),
    )
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<FinalisationChecklistItem {self.item_text!r} response={self.response!r}>"


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
