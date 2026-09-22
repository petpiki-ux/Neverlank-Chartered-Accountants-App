import json
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

from extensions import db


ENGAGEMENT_TYPES = [
    "Audit", "Assurance", "Consulting", "Secretarial", "Investigative Engagement",
    "Business Intelligence and IT Engagements", "Tax Compliance", "Tax Advisory & Health Check",
    "Accounting & Bookkeeping",
]

# The financial reporting framework the client's Financial Statements are
# prepared under (Finalisation tab). Only "full_ifrs" and "ifrs_for_smes"
# get the auto-generated, numbered Notes to the Financial Statements (see
# financials.build_notes) - "other" keeps the original plain statements +
# free-text notes box, unchanged, since a local-GAAP framework isn't
# something this app has any basis to generate IFRS-style notes for.
REPORTING_FRAMEWORKS = [
    ("full_ifrs", "Full IFRS"),
    ("ifrs_for_smes", "IFRS for SMEs"),
    ("other", "Other / local GAAP"),
]
REPORTING_FRAMEWORK_LABELS = dict(REPORTING_FRAMEWORKS)

# Which method the Cash Flow Statement's operating activities section uses
# (IAS 7.18) - see financials.build_cash_flow. Every other line on every
# statement is identical regardless of which is chosen; only how operating
# cash flow is presented changes, and both always reconcile to the same
# net cash from operating activities.
CASH_FLOW_METHODS = [
    ("indirect", "Indirect method"),
    ("direct", "Direct method"),
]
CASH_FLOW_METHOD_LABELS = dict(CASH_FLOW_METHODS)

# Public Interest Entity checklist (Finalisation tab, "Company
# classification"): PAAB Pronouncement 2/2016's own criteria for presuming
# an entity to be a Public Interest Entity (public accountability) -
# https://paab.org.zw, "Definition of a Public Interest Entity". PAAB and
# the IASB draw the Full IFRS / IFRS for SMEs line on exactly this test
# (public accountability), not on a numeric size threshold - Zimbabwean
# company law itself sets none for this purpose (the Companies and Other
# Business Entities Act just requires "generally accepted accounting
# practice" as recognised by PAAB). Each tuple is
# (Engagement boolean column name, checklist label). Any "yes" makes the
# entity a Public Interest Entity - see Engagement.is_public_interest_entity.
PIE_CRITERIA = [
    ("pie_listed", "Listed on a licensed securities exchange"),
    ("pie_financial_institution", "Bank, building society, or deposit-taking microfinance institution"),
    ("pie_insurer", "Insurer (life or general)"),
    ("pie_asset_manager", "Asset manager or collective investment scheme"),
    ("pie_pension_fund", "Pension fund open to a large number and wide range of employees"),
    ("pie_medical_aid", "Registered medical aid society"),
    ("pie_debt_equity_issuer", "Issues debt or equity instruments to the public"),
]

# Separately, Zimbabwe's Small and Medium Enterprises Act [Chapter 24:12]
# does classify enterprises by size (Micro/Small/Medium, by sector) - a
# different, business-development/tax classification, not an accounting-
# standards one (a Large non-PIE company remains free to use IFRS for
# SMEs). Recorded on the Finalisation tab as preparer-entered client
# information alongside the Public Interest Entity test above, not as
# something the app computes or that changes which statements are
# prepared - see Engagement.sme_sector / sme_size_band.
SME_ACT_SECTORS = [
    "Agriculture", "Mining", "Manufacturing", "Construction", "Energy",
    "Financial Services", "Transport", "Retail and Wholesale",
    "Tourism and Hospitality", "Arts and Entertainment", "Services", "Other",
]
SME_ACT_SIZE_BANDS = [
    ("micro", "Micro"), ("small", "Small"), ("medium", "Medium"), ("large", "Large"),
]
SME_ACT_SIZE_BAND_LABELS = dict(SME_ACT_SIZE_BANDS)

SECRETARIAL_SUBDIVISIONS = ["Company Registrations", "Trusts", "PVOs"]

# Selectable "Activities" for a Secretarial engagement (Engagement.
# secretarial_activities, a comma-separated list of the keys below) - what
# the client has actually engaged the firm to do, under the Companies and
# Other Business Entities Act [Chapter 24:31] (COBE Act). Used to filter
# which Understanding the Business/Assignment questionnaire questions are
# seeded for the engagement (see SECRETARIAL_ENTITY_UNDERSTANDING_CHECKLIST_
# ITEMS below and engagements.seed_entity_checklist) - so a client engaged
# only for annual compliance work isn't shown questionnaire sections about
# share capital restructuring, and vice versa. Leaving none selected seeds
# every section (the safe, unfiltered default).
SECRETARIAL_ACTIVITIES = [
    ("incorporation", "Incorporation & Entity Maintenance"),
    ("directorship", "Directorship & Governance Structure"),
    ("share_capital", "Share Capital & Ownership Transactions"),
    ("meetings", "Board & General Meeting Secretarial Support"),
    ("constitutional", "Constitutional Amendments & Corporate Restructuring"),
    ("statutory_records", "Statutory Record Management & Good Standing"),
]
SECRETARIAL_ACTIVITY_LABELS = dict(SECRETARIAL_ACTIVITIES)

# Selectable "Services" for the Tax Advisory & Tax Compliance module
# (Engagement.tax_services, a comma-separated list of the keys below) -
# what the firm has actually been engaged to do on the tax side. Unlike
# SECRETARIAL_ACTIVITIES above (only meaningful on a Secretarial
# engagement), this is deliberately combinable onto ANY engagement type -
# a firm often bundles tax compliance work into an ordinary Audit
# engagement rather than raising it separately - as well as being the
# natural default for the two dedicated Tax engagement types above. The
# whole Tax tab (see tax.py / templates/tax/*) only appears on an
# engagement once at least one of these is selected - see
# Engagement.has_tax_module. Advisory-only sections (Technical Research
# Log, Structuring Options, Tax Dispute Management - Module 7B of the tax
# module spec) only appear when "advisory" or "representation" is among
# them - see Engagement.has_tax_advisory.
TAX_SERVICES = [
    ("compliance", "Tax Compliance (returns & statutory filings)"),
    ("advisory", "Tax Advisory (planning & structuring)"),
    ("health_check", "Tax Health Check / Due Diligence"),
    ("representation", "Tax Representation & Dispute Support"),
]
TAX_SERVICE_LABELS = dict(TAX_SERVICES)

# Selectable "Services" for the Financial Accounting, Cost Accounting &
# Management Accounting module (Engagement.accounting_services, a
# comma-separated list of the keys below) - what the firm has been engaged
# to do on the accounting side, for a client whose books/costing/management
# reporting Neverlank itself prepares (as distinct from an Audit/Assurance
# engagement, which forms an opinion on figures someone else prepared).
# Deliberately combinable onto ANY engagement type, exactly like
# TAX_SERVICES above - a firm might bundle a bookkeeping clean-up into an
# Audit engagement, as well as this being the natural default for the
# dedicated "Accounting & Bookkeeping" engagement type. The whole
# Accounting tab (see accounting.py / templates referenced from
# templates/engagements/detail.html) only appears once at least one of
# these is selected, or the engagement type is dedicated - see
# Engagement.has_accounting_module. "costing"/"budgeting" each gate their
# own sections of that tab further - see has_cost_accounting/
# has_management_accounting below, since not every bookkeeping client also
# needs job costing or a formal budget.
ACCOUNTING_SERVICES = [
    ("bookkeeping", "Financial Accounting / Bookkeeping (write-up & GL maintenance)"),
    ("statements", "Financial Statement Preparation (compiled / management accounts)"),
    ("costing", "Cost Accounting (job/process/standard costing & variance analysis)"),
    ("budgeting", "Management Accounting (budgets, forecasts & management reports)"),
]
ACCOUNTING_SERVICE_LABELS = dict(ACCOUNTING_SERVICES)

# The tax heads tracked throughout the Tax module (Registrations, the
# Statutory Compliance Calendar, the Execution Plan, Return Records, the
# Penalty & Interest Engine, and the Exposure Dashboard all key off this
# same list) - deliberately a plain, editable-in-code list rather than
# anything hardcoded into a rate or a form, per the module's own "nothing
# tax-specific is fabricated" rule (see PenaltyInterestRate below).
TAX_HEADS = [
    "Corporate Income Tax (CIT)", "Value Added Tax (VAT)", "PAYE",
    "Withholding Tax (WHT)", "Capital Gains Tax (CGT)", "Customs & Excise",
    "Provisional Tax (QPDs)",
]

# The write-up/close-out work areas tracked on the Accounting module's
# Execution Plan - reuses SubstantiveProcedureArea/Item exactly like
# TAX_HEADS does above (one area per entry, appended onto whatever this
# engagement type's own areas already are, whenever Engagement.
# has_accounting_module is True) - see the append in engagements.
# view_engagement(). Kept as a plain, editable-in-code list for the same
# reason TAX_HEADS is.
ACCOUNTING_AREAS = [
    "Bank & Cash Reconciliations", "Receivables & Payables Write-up",
    "Inventory & Costing Records", "Payroll Postings",
    "Fixed Asset Register Maintenance", "Management Reporting Pack",
]

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
    ("tax", "Tax Advisory & Compliance"),
    ("accounting", "Financial, Cost & Management Accounting"),
]
QUERY_SECTION_KEYS = {key for key, _ in QUERY_SECTIONS}
QUERY_SECTION_LABELS = dict(QUERY_SECTIONS)

# Fixed workpaper index - one lettered reference per major engagement
# section/tab, in file order, so every part of the engagement can be
# referred to (and filed under, in the Engagement File Summary PDF) the
# way a paper audit file would be indexed. This is intentionally a fixed
# firm-standard registry rather than something edited per engagement - a
# firm's workpaper index rarely changes engagement to engagement. "forensic"
# entries only appear in the file summary for an "Investigative Engagement".
#
# The 4th element, filing_code, is the matching reference from the firm's
# Audit Working Paper Indexing & Filing Policy (see FilingIndexSection / the
# Filing Index & Manual page) - None where this app section doesn't
# correspond to a single numbered working paper in that scheme (the main
# Engagement Checklist and Substantive Procedures each span many separate
# working papers/areas, already given their own codes elsewhere; the
# Forensic Investigation Report has no equivalent in a filing policy scoped
# to statutory audits). Where a filing_code exists, it's shown in place of
# the old letter reference on downloads and the File Summary PDF; where it
# doesn't, the letter reference is kept as the fallback.
WORKPAPER_SECTIONS = [
    ("A", "acceptance", "Client Acceptance & Continuance", "N1009"),
    ("B", "entity", "Understanding the Entity's Business", "N1003"),
    ("C", "risks", "Risk Assessment", "N1004"),
    ("D", "planning", "Planning (Materiality)", "N1001"),
    ("E", "analytical", "Analytical Review", "N1006"),
    ("F", "checklist", "Engagement Checklist", None),
    ("G", "substantive", "Substantive Procedures", None),
    ("H", "financials", "Financial Statements", "N8100"),
    ("I", "finalisation", "Finalisation Checklist", "N9009"),
    ("J", "rep_letter", "Management Representation Letter", "N9006"),
    ("K", "report_to_management", "Report to Management", "N8300"),
    ("L", "forensic_report", "Forensic Investigation Report", None),
    ("M", "tax_opinion", "Tax Opinion / Advisory Report", None),
    ("N", "tax_health_check", "Tax Health Check Report", None),
    ("O", "management_accounts_report", "Management Accounts Report", None),
]
WORKPAPER_SECTION_BY_KEY = {key: (code, label, filing_code) for code, key, label, filing_code in WORKPAPER_SECTIONS}


def workpaper_reference(section_key):
    """The fixed "A", "B", "C"... index letter for a workpaper section key
    (see WORKPAPER_SECTIONS), or None if the key isn't recognised."""
    entry = WORKPAPER_SECTION_BY_KEY.get(section_key)
    return entry[0] if entry else None


def filing_reference(section_key):
    """The firm's actual filing-index reference (e.g. "N1003") for a
    workpaper section key, falling back to the old letter reference (e.g.
    "B") where this section doesn't correspond to a single numbered working
    paper in the filing policy. Always returns something displayable."""
    entry = WORKPAPER_SECTION_BY_KEY.get(section_key)
    if not entry:
        return None
    code, _, filing_code = entry
    return filing_code or code


def effectively_reviewed(record):
    """True if `record` (any workpaper carrying the standard Preparer/
    Reviewer/Partner sign-off fields - completed_by/is_reviewed) either has
    an independent Reviewer sign-off, or its preparer is already a Partner
    or Admin - in which case a separate Reviewer isn't required, since a
    Partner's own preparation of a section is treated as definitive.

    This is read-only/display-only: it never sets reviewed_by_id/reviewed_at
    on the record, and it never changes the existing rule (enforced in the
    routes) that a reviewer/partner sign-off can never be the same person as
    the preparer - a Partner still can't click "Review" on their own work.
    It only affects whether work is treated as "reviewed enough" for things
    like the Engagement File Summary PDF and the checklist tab's display.
    """
    if getattr(record, "is_reviewed", False):
        return True
    # Almost every workpaper names its preparer relationship "completed_by",
    # but MaterialityCalculation (re-saved rather than "completed") instead
    # calls it "updated_by" - check both rather than special-casing it.
    preparer = getattr(record, "completed_by", None) or getattr(record, "updated_by", None)
    return bool(preparer and getattr(preparer, "role", None) in PARTNER_SIGNOFF_ROLES)

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
    ("manage_regulatory_notices", "Manage Regulatory Notices",
     "Upload or delete RBZ/FIU adverse notice PDFs in the Regulatory Notices library, used by automated sanctions/adverse-notice screening.",
     ("partner", "admin")),
    ("manage_sanctions_lists", "Refresh Sanctions Lists",
     "Trigger a manual refresh of the cached UN, OFAC and EU sanctions lists used by automated screening.",
     ("partner", "admin")),
    ("manage_company_documents", "Manage Company Documents",
     "Upload or delete a client's company documents (incorporation certificate, CR14, share register, etc.), confirm/edit/delete the Directors & Shareholders picked up from them, and run the public-information scan on a client's business.",
     tuple(USER_ROLES)),
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
    ("manage_payroll", "Manage Payroll",
     "Open the Payroll module: view/add employees (Neverlank staff or client payroll), run payroll periods, "
     "edit payslips, and change the firm's Payroll Tax Settings. Salary data is sensitive, so this defaults "
     "to Partner/Admin only.",
     ("partner", "admin")),
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

# Business Intelligence and IT Engagements-specific Risk Assessment
# questionnaire, used in place of RISK_LIKELIHOOD_QUESTIONS/
# RISK_IMPACT_QUESTIONS above (the same way FORENSIC_RISK_*_QUESTIONS are,
# just for the new type) - see RiskAssessment.likelihood_questions/
# impact_questions below. A cyber/IT/AML-CFT control assurance engagement
# asks "how likely is a control failure or breach, and how bad would it be
# if one happened" rather than "how likely is a financial misstatement",
# so this questionnaire is built around threat exposure, control maturity,
# environment complexity, third-party/cloud dependency, and AML/CFT
# customer & channel risk - deliberately general, same as every other risk
# questionnaire in this app; apply your own methodology and professional
# judgement on top of it.
BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS = [
    ("q_threat_exposure", "How exposed is the entity to external cyber threats (internet-facing systems, remote access, prior incidents, industry targeting)?", [
        "Minimal external footprint, no history of incidents or targeting",
        "Limited exposure, no known incidents",
        "Moderate exposure - some internet-facing systems, industry occasionally targeted",
        "Significant exposure - substantial internet-facing footprint or a known prior incident",
        "Severe exposure - heavily targeted sector/entity, and/or repeated prior incidents",
    ]),
    ("q_control_maturity", "How mature are the entity's IT general controls (patching, access reviews, segregation of IT duties, logging/monitoring)?", [
        "Mature, well-documented and independently tested controls",
        "Generally sound controls with only minor gaps",
        "Noticeable control weaknesses in some areas",
        "Significant control weaknesses across multiple areas",
        "Weak or largely absent IT general controls",
    ]),
    ("q_environment_complexity", "How much change or complexity is there in the IT environment (new systems, cloud migration, M&A/integration, outsourced IT)?", [
        "Stable, unchanged environment",
        "Minor changes underway",
        "Some notable changes (a system replacement or cloud move in progress)",
        "Significant changes (major migration, integration, or outsourcing under way)",
        "Extensive, concurrent change across multiple systems/providers",
    ]),
    ("q_third_party_cloud", "How dependent is the entity on third-party service providers and cloud platforms for critical systems or data?", [
        "Minimal reliance - systems mostly in-house",
        "Limited reliance, well-managed vendor relationships",
        "Moderate reliance across several providers",
        "Significant reliance, including on providers with limited oversight",
        "Extensive reliance, including on critical, unaudited third parties",
    ]),
    ("q_aml_customer_channel", "What is the entity's AML/CFT customer and channel risk profile (cash-intensive activity, cross-border transactions, high-risk customer types, correspondent relationships)?", [
        "Low-risk customer base and channels, minimal cash/cross-border activity",
        "Mostly low-risk, limited higher-risk activity",
        "Some higher-risk customers, channels, or cross-border activity",
        "Considerable higher-risk activity (cash-intensive, cross-border, or PEP exposure)",
        "Extensive higher-risk activity across customers, channels and jurisdictions",
    ]),
]

BUSINESS_IT_RISK_IMPACT_QUESTIONS = [
    ("q_data_sensitivity", "How sensitive is the data at risk (personal information under the Cyber and Data Protection Act [Chapter 12:07], financial data, trade secrets)?", [
        "No sensitive data held",
        "Limited sensitive data, low volume",
        "Moderate volume of sensitive/personal data",
        "Significant volume of sensitive/personal or regulated data",
        "Extensive, highly sensitive data (large-scale personal data, payment data, or similar)",
    ]),
    ("q_continuity_dependency", "How critical are the affected systems to the entity's ongoing operations (business continuity dependency)?", [
        "Non-critical - operations continue largely unaffected",
        "Limited dependency - brief disruption tolerable",
        "Moderate dependency - a short outage would be disruptive",
        "High dependency - an outage of more than a few hours would be serious",
        "Mission-critical - even a brief outage stops operations",
    ]),
    ("q_regulatory_legal", "What is the potential regulatory/legal exposure (Cyber and Data Protection Act, sector regulator such as POTRAZ/RBZ, Money Laundering and Proceeds of Crime Act [Chapter 9:24], FIU guidance)?", [
        "Minimal - no plausible regulatory exposure",
        "Limited - unlikely to attract regulatory attention",
        "Moderate - plausible regulatory scrutiny or a minor penalty",
        "Significant - likely regulatory action or a material penalty",
        "Severe - potential for major penalties, licence risk, or criminal referral",
    ]),
    ("q_reputational", "What would the reputational impact be if a control failure or breach became public?", [
        "Minimal - unlikely to attract attention",
        "Limited - some client/stakeholder concern",
        "Moderate - noticeable reputational impact",
        "Serious - significant client/public/media attention",
        "Severe - potential for lasting reputational or business damage",
    ]),
    ("q_overall_significance", "Overall, if a control failure or breach in this area were to occur, how significant would the combined financial, operational and reputational impact be?", [
        "Minor",
        "Limited",
        "Moderate",
        "Serious",
        "Severe",
    ]),
]

# Secretarial-specific answers (1-5 each), used instead of the ten standard
# fields above on a "Secretarial" engagement - built around the four pillars
# of secretarial risk under the Companies and Other Business Entities Act
# [Chapter 24:31] (COBE Act): (1) Statutory Non-Compliance & Penalty Risk,
# (2) Procedural & Governance Invalidity Risk, (3) Ownership, Capital &
# Solvency Risk, and (4) Transactional & Change-of-Control Risk. Kept as
# separate columns, same reasoning as the forensic/BI-IT-specific fields
# above.
SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS = [
    ("q_filing_compliance_history", "What is this entity's track record of lodging Annual Returns (Sec 165) and notifying director/officer changes (Sec 215) within the statutory windows?", [
        "Consistently on time, no history of late or missed filings",
        "Mostly on time, isolated minor delays",
        "Some history of late filings",
        "Frequent late filings, or at least one lapse into defunct/struck-off status previously remedied",
        "Currently overdue on Annual Returns, or at imminent risk of being struck off (Sec 310)",
    ]),
    ("q_governance_procedural_rigor", "How rigorous are the entity's board/general meeting procedures (quorum compliance under Sec 195/206, notice periods under Sec 170, no director proxies under Sec 196)?", [
        "Well-documented, consistently compliant meeting procedures",
        "Generally sound, only minor procedural gaps",
        "Noticeable procedural weaknesses (e.g. occasional quorum or notice issues)",
        "Significant procedural weaknesses across multiple meetings",
        "Minute books incomplete/unsigned, or meetings routinely held without proper quorum or notice",
    ]),
    ("q_ownership_capital_complexity", "How complex or frequent are this entity's ownership and capital events (share allotments, transfers, beneficial ownership changes under Sec 72, distributions or buybacks under Sec 102/104)?", [
        "Simple, stable ownership structure, no capital events pending",
        "Occasional, straightforward capital events",
        "Moderate complexity - several shareholders/classes, or an event in progress",
        "Significant complexity - multiple classes, nominee arrangements, or frequent capital events",
        "Highly complex ownership/capital structure with unresolved beneficial ownership questions",
    ]),
    ("q_transactional_activity", "How much transactional or change-of-control activity (charge/mortgage creation under Sec 163, constitutional amendments, restructurings under Sec 227-232) is anticipated or under way?", [
        "None anticipated",
        "Limited, routine activity only",
        "Some non-routine activity anticipated (e.g. a single charge registration)",
        "Significant activity anticipated (e.g. a restructuring or multiple charges)",
        "Major, time-critical transactional activity already under way",
    ]),
    ("q_registry_standing", "What is this entity's current standing with the Registrar of Companies (up to date vs at risk of deregistration/striking-off under Sec 310)?", [
        "Fully up to date and in good standing",
        "Up to date, one historical lapse since remedied",
        "One or more filings currently outstanding but within a curable window",
        "Multiple filings outstanding, approaching the striking-off threshold",
        "Already subject to (or at imminent risk of) Registrar deregistration proceedings",
    ]),
]

SECRETARIAL_RISK_IMPACT_QUESTIONS = [
    ("q_penalty_exposure", "If a filing or deadline is missed, what is the likely exposure to administrative fines, late-filing penalties, or Registrar default notices?", [
        "Minimal - unlikely to attract any penalty",
        "Limited - a small, one-off penalty at most",
        "Moderate - a meaningful penalty or formal Registrar notice",
        "Significant - substantial penalties across multiple lapses",
        "Severe - deregistration/striking-off exposure (Sec 310)",
    ]),
    ("q_governance_invalidity", "If a board or shareholder resolution were later challenged for a quorum, notice, or proxy defect (Sec 170/196), how significant would the consequences be?", [
        "Minimal - no material decisions at risk",
        "Limited - only routine matters potentially affected",
        "Moderate - some commercially significant decisions could be challenged",
        "Significant - key resolutions (allotments, borrowings) could be voided",
        "Severe - ultra vires exposure across major corporate actions, litigation risk",
    ]),
    ("q_solvency_director_liability", "If a distribution, share buyback, or director loan were found not to have followed the statutory Solvency and Liquidity Test (Sec 102) or disclosure requirements (Sec 216), what is the exposure?", [
        "Minimal - no distributions/buybacks/director loans in the period",
        "Limited - small, well-documented transactions only",
        "Moderate - some transactions without full contemporaneous documentation",
        "Significant - material distributions/buybacks with weak solvency evidence",
        "Severe - unlawful distribution exposure and personal director liability",
    ]),
    ("q_encumbrance_control_risk", "If a charge/mortgage were left unregistered past the 30-day window (Sec 163), or a change-of-control step were taken without required consents, what is the exposure?", [
        "Minimal - no charges or change-of-control steps in the period",
        "Limited - registrations well within statutory deadlines",
        "Moderate - some risk of a registration slipping past deadline",
        "Significant - security could be rendered void against liquidators/competing creditors",
        "Severe - a material charge or change-of-control step already outside the statutory window",
    ]),
    ("q_overall_significance", "Overall, if a secretarial compliance failure in this area were to occur, how significant would the combined statutory, financial and reputational impact be for the entity and its directors?", [
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

# Seeded ALONGSIDE the five free-text ENTITY_UNDERSTANDING_FIELDS above (not
# instead of them, unlike the three type-specific checklists below) for
# every engagement type that isn't "Investigative Engagement", "Business
# Intelligence and IT Engagements" or "Secretarial" - i.e. Audit, Assurance,
# Consulting, the two Tax types, and "Accounting & Bookkeeping". Same
# presentation and same ISA 315 (Revised)-style five headings as
# ENTITY_UNDERSTANDING_FIELDS, but broken into individual Yes/No/N-A +
# comment questions - the "Client Acceptance" tab pairs a single top-level
# narrative/checkbox with a per-section "Detailed checklist" underneath it
# (see DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS below); this gives Understanding
# the Entity the same two-layer treatment: the five free-text write-ups
# stay as the narrative record, and this is the itemised, tickable version
# underneath, so nothing gets missed and progress is visible at a glance
# (see EntityUnderstanding.checklist_assessment).
DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS = [
    ("Nature of the Entity", "Have the entity's principal business activities, products/services and key markets been identified and documented?"),
    ("Nature of the Entity", "Has the ownership structure been established, including parent/subsidiary/related entities and any recent changes in ownership or group structure?"),
    ("Nature of the Entity", "Has the entity's governance structure been documented (board composition, audit/risk committees, key management personnel)?"),
    ("Nature of the Entity", "Have the entity's sources of financing been identified, including related-party financing and any recent or planned debt/equity transactions?"),
    ("Nature of the Entity", "Have any recent or planned significant transactions been identified (acquisitions, disposals, restructurings, new product/service lines)?"),
    ("Industry, Regulatory and Other External Factors", "Has the competitive and market environment been considered (demand, competition, cyclicality, seasonality)?"),
    ("Industry, Regulatory and Other External Factors", "Has the applicable financial reporting framework been confirmed, along with any recent or pending changes to it?"),
    ("Industry, Regulatory and Other External Factors", "Have the specific laws, regulations and licensing requirements applicable to the entity's industry been identified?"),
    ("Industry, Regulatory and Other External Factors", "Have general economic conditions relevant to the entity been considered (inflation, exchange rate volatility, interest rates)?"),
    ("Industry, Regulatory and Other External Factors", "Have any industry-specific risks been identified (e.g. technological change, supply chain disruption)?"),
    ("Accounting Policies", "Have the entity's significant accounting policies been identified and assessed for appropriateness to the industry?"),
    ("Accounting Policies", "Have any changes in accounting policy since the prior period been identified, along with the reasons for the change?"),
    ("Accounting Policies", "Have areas involving significant management judgement or estimation uncertainty been identified?"),
    ("Accounting Policies", "Has the accounting treatment of unusual or complex transactions during the period been considered?"),
    ("Accounting Policies", "Has consistency of accounting policy application across the group (if applicable) been assessed?"),
    ("Objectives, Strategies and Related Business Risks", "Have the entity's key business objectives and strategies been identified?"),
    ("Objectives, Strategies and Related Business Risks", "Have the business risks that could result in a material misstatement of the financial statements been identified?"),
    ("Objectives, Strategies and Related Business Risks", "Has management's process for identifying and responding to business risk been considered?"),
    ("Objectives, Strategies and Related Business Risks", "Have any new products, services or markets being entered been identified, along with the associated risks?"),
    ("Objectives, Strategies and Related Business Risks", "Has the entity's going concern status and any related uncertainties been considered?"),
    ("Measurement and Review of Financial Performance", "Have the key performance indicators management uses to assess financial performance been identified?"),
    ("Measurement and Review of Financial Performance", "Has the entity's budgeting and forecasting process been considered?"),
    ("Measurement and Review of Financial Performance", "Have significant period-on-period variances in financial performance been identified and explained?"),
    ("Measurement and Review of Financial Performance", "Have any performance measures tied to management or employee incentive schemes been identified?"),
    ("Measurement and Review of Financial Performance", "Has external benchmarking against industry peers been considered, where relevant?"),
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

# Seeded instead of the five free-text ENTITY_UNDERSTANDING_FIELDS above when
# the engagement's type is "Business Intelligence and IT Engagements" (see
# engagements.seed_entity_checklist) - same Yes/No/N-A + comment presentation
# as FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS above, but scoped to
# understanding the entity's IT environment, data holdings, prior incident
# history and AML/CFT exposure ahead of a cyber/IT/AML-CFT control
# assurance engagement, rather than ISA 315's entity/industry/accounting-
# policy prompts.
BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS = [
    ("IT Environment & Infrastructure Overview", "What is the overall IT environment (on-premise, cloud, or hybrid) and how is it organised (in-house IT function, outsourced/managed service provider, or a mix)?"),
    ("IT Environment & Infrastructure Overview", "Which core business systems (ERP, banking platforms, core application systems) does the entity rely on, and who are the key vendors/service providers?"),
    ("IT Environment & Infrastructure Overview", "Has the IT environment changed materially in the period under review (new system implementations, cloud migration, mergers/acquisitions, outsourcing changes)?"),
    ("IT Environment & Infrastructure Overview", "Is there a documented IT governance structure (IT steering committee, CIO/IT Manager, board-level IT oversight)?"),
    ("Data Holdings & Classification", "What categories of data does the entity hold or process (customer personal information, financial data, employee data, trade secrets/intellectual property)?"),
    ("Data Holdings & Classification", "Is data classified by sensitivity, and is there a documented data retention and disposal policy?"),
    ("Data Holdings & Classification", "Where is data physically/logically stored (on-premise servers, local cloud provider, offshore cloud provider), and does this raise any cross-border data transfer considerations under the Cyber and Data Protection Act [Chapter 12:07]?"),
    ("Cyber Security Posture & Incident History", "Has the entity experienced any cyber security incidents, breaches, or significant near-misses in recent years, and how were they handled?"),
    ("Cyber Security Posture & Incident History", "Does the entity have documented policies for threat and vulnerability management, perimeter/endpoint protection, and security monitoring and logging?"),
    ("Cyber Security Posture & Incident History", "Is there a documented and tested incident response and recovery plan?"),
    ("Cyber Security Posture & Incident History", "Has the entity engaged any third party (internal audit, external specialist) to perform a penetration test, vulnerability assessment, or security review in recent years?"),
    ("Information Security & Access Management", "How are user access and privileges granted, reviewed, and revoked (including for third parties and departing staff)?"),
    ("Information Security & Access Management", "What third-party and cloud service providers have access to the entity's systems or data, and how is that access governed (contracts, SLAs, security clauses)?"),
    ("Information Security & Access Management", "Is there a documented information/data classification and handling policy, and how is compliance monitored?"),
    ("ICT Governance & Operations", "Is there a documented ICT strategy aligned to the entity's business objectives, and how is it approved and monitored?"),
    ("ICT Governance & Operations", "What change and release management process is followed for system changes, and how are new systems developed or acquired?"),
    ("ICT Governance & Operations", "What backup and disaster recovery arrangements are in place, and have they been tested?"),
    ("ICT Governance & Operations", "Is there a documented and tested business continuity plan covering IT-dependent operations?"),
    ("AML/CFT Framework & Regulatory Context", "Does the entity fall within a sector subject to AML/CFT obligations under the Money Laundering and Proceeds of Crime Act [Chapter 9:24], and if so, is there a designated Money Laundering Reporting Officer?"),
    ("AML/CFT Framework & Regulatory Context", "What customer due diligence (CDD)/know-your-customer procedures are in place, including for higher-risk customers or channels?"),
    ("AML/CFT Framework & Regulatory Context", "What transaction monitoring and screening arrangements are in place (sanctions screening, unusual-activity monitoring)?"),
    ("AML/CFT Framework & Regulatory Context", "Has the entity filed any suspicious transaction reports with the Financial Intelligence Unit, or received any regulatory findings/guidance relevant to AML/CFT compliance?"),
    ("AML/CFT Framework & Regulatory Context", "What record-keeping arrangements are in place for CDD information and transaction records, and for how long are they retained?"),
]

# Seeded instead of the five free-text ENTITY_UNDERSTANDING_FIELDS above when
# the engagement's type is "Secretarial" - the firm's Secretarial Client
# Business Understanding Questionnaire, mapped to the Companies and Other
# Business Entities Act [Chapter 24:31] (COBE Act). Same Yes/No/N-A +
# comment presentation as FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS /
# BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS above, with one addition:
# each item also carries a tuple of SECRETARIAL_ACTIVITIES keys it's
# relevant to, so engagements.seed_entity_checklist can seed only the
# sections relevant to the Activities selected for this engagement (see
# Engagement.secretarial_activities) - or every item, if none were selected.
SECRETARIAL_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS = [
    ("Entity Classification & Scope", "Is the entity registered as a Private Limited Company, Public Limited Company, Company Limited by Guarantee, Private Business Corporation (PBC), or Foreign Company under Section 5?", ("incorporation",)),
    ("Entity Classification & Scope", "Does the entity operate in banking, building societies, insurance, or microfinance - noting that these financial institutions are excluded from the COBE Act under Section 4 and governed by sector-specific statutes?", ("incorporation",)),
    ("Constitutional Documents & Corporate Governance", "Does the Memorandum clearly specify all authorised share types and classes pursuant to the disclosure standards for share capital?", ("constitutional", "share_capital")),
    ("Constitutional Documents & Corporate Governance", "What are the specific rules and thresholds set out in the Articles regarding board powers, borrowing limits (Section 196), execution of documents, and pre-emption rights?", ("constitutional", "directorship")),
    ("Constitutional Documents & Corporate Governance", "Does a private company meet the minimum statutory director requirements under Section 195 (minimum 2 directors if under 10 members; minimum 3 directors if over 10 members)?", ("directorship",)),
    ("Constitutional Documents & Corporate Governance", "For public companies, does the board meet the requirement of 7 to 15 directors, including at least 3 independent non-executive directors (Section 206)?", ("directorship",)),
    ("Constitutional Documents & Corporate Governance", "Are directors aware of their statutory duty of care, skill, and good faith (Section 54) and duty of loyalty (Section 55)?", ("directorship",)),
    ("Statutory Registers & Ownership Transparency", "Does the company maintain an up-to-date Register of Beneficial Ownership under Section 72, identifying natural persons who directly or indirectly hold 20% or more of shares or voting rights?", ("statutory_records", "share_capital")),
    ("Statutory Registers & Ownership Transparency", "Does any nominee shareholding comply with the statutory cap under Section 72, which restricts nominee holdings to no more than 20% of shares on behalf of a beneficial owner?", ("statutory_records", "share_capital")),
    ("Statutory Registers & Ownership Transparency", "Are the Register of Members (Section 157), Register of Directors and Secretaries (Section 215), Register of Directors' Interests and Conflicts (Section 211), and Register of Mortgages and Debentures/Charges (Section 162) maintained at the registered office and reconciled?", ("statutory_records",)),
    ("Capital Structure, Distributions & Solvency", "Have any variations of share rights, share allotments, or capital changes been executed in accordance with board powers and properly reflected in the Memorandum/Articles?", ("share_capital",)),
    ("Capital Structure, Distributions & Solvency", "Before making any distribution, share buyback, or financial assistance, has the board formally evaluated and certified that the company meets the statutory Solvency and Liquidity Test (Section 102)?", ("share_capital",)),
    ("Capital Structure, Distributions & Solvency", "Are there any proposed structural changes or class variations that might trigger minority shareholder appraisal remedies (Section 143 or Section 228)?", ("share_capital", "constitutional")),
    ("Board Operations, Meetings & Documentation", "Are all loans, guarantees, or security provided by the company for directors or officers fully disclosed and approved under Section 216?", ("directorship", "meetings")),
    ("Board Operations, Meetings & Documentation", "Is the board aligned with Section 196, which prohibits directors from acting or voting by proxy at board meetings?", ("meetings",)),
    ("Board Operations, Meetings & Documentation", "Are minute books kept up-to-date for all board meetings, committee meetings, and AGMs/EGMs, capturing key commercial decisions, risk evaluations, and conflict disclosures?", ("meetings",)),
    ("Statutory Filings & Registry Compliance", "Is the company compliant with filing its Annual Return (Section 165) within the statutory timeframe following its financial year-end?", ("incorporation", "statutory_records")),
    ("Statutory Filings & Registry Compliance", "Have changes in directors/secretaries, registered office address, or share capital updates been lodged with the Registrar within the required statutory notification periods?", ("statutory_records", "directorship")),
    ("Statutory Filings & Registry Compliance", "Has the entity completed its mandatory administrative re-registration process under the COBE Act framework?", ("incorporation", "statutory_records")),
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

# Working-paper reference codes shown next to each area's heading on the
# Substantive Procedures tab and embedded in the generated Word/Excel
# workpapers, so a reviewer can cite "see N3100" the same way they would in
# a paper file. AUDIT_AREA_REFERENCES uses the firm's actual Current File
# (N-series) codes from the Audit Working Paper Indexing & Filing Policy
# (see FilingIndexSection / the Filing Index & Manual page) - N3100 through
# N6200, matching the policy's Statement of Financial Position / Statement
# of Comprehensive Income / Other Audit Areas sections one-for-one, with one
# addition: the policy's tables don't include a code for Inventories, so
# N3700 has been added here (and in the Filing Index) to fill that gap,
# extending the Assets range immediately after Prepayments & Other Assets
# (N3600). FORENSIC_AREA_REFERENCES keeps its own short mnemonic codes,
# since the filing policy is scoped to statutory audits and doesn't cover
# investigative/forensic engagement areas at all.
AUDIT_AREA_REFERENCES = {
    "Cash and Bank": "N3100",
    "Trade Receivables": "N3200",
    "Inventories": "N3700",
    "Property, Plant and Equipment": "N3300",
    "Investments": "N3500",
    "Trade Payables and Accruals": "N4100",
    "Borrowings and Finance Costs": "N4200",
    "Revenue": "N5100",
    "Payroll and Employee Costs": "N5200",
    "Taxation": "N4300",
    "Equity and Reserves": "N4600",
    "Related Party Transactions": "N6100",
    "Going Concern": "N6200",
}

FORENSIC_AREA_REFERENCES = {
    "Advanced Data Analytics & Forensic Technology": "DA",
    "Asset Tracing & Financial Reconstruction": "AT",
    "Document Examination & Verification": "DE",
    "Physical & Observational Procedures": "PO",
}

# Equivalent of AUDIT_AREAS/BASELINE_SUBSTANTIVE_PROCEDURES above, used for
# the Substantive Procedures tab on a "Business Intelligence and IT
# Engagements" engagement instead of the financial-statement-line-item
# areas - this engagement type gives assurance over an entity's cyber
# security, information security, IT/ICT, and AML/CFT control environment,
# not its financial statements. Grouped into the same four assurance
# domains the engagement type itself was scoped around. Same as the
# forensic areas above, HIGH_RISK_EXTRA_PROCEDURES/INDUSTRY_EXTRA_
# PROCEDURES are not applied on top of these (they're keyed to AUDIT_AREAS
# captions), so this engagement type's suggested procedures are always just
# this baseline list.
BUSINESS_IT_SUBSTANTIVE_AREAS = [
    "Cyber Security",
    "Information Security",
    "Information Technology (ICT)",
    "AML/CFT Compliance",
]

BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES = {
    "Cyber Security": [
        "Evaluate the entity's threat and vulnerability management process, including the frequency and remediation of vulnerability scans/penetration tests over a sample period.",
        "Test perimeter and endpoint protection controls (firewalls, intrusion detection/prevention, endpoint protection/EDR) for a sample of critical systems.",
        "Assess security monitoring and logging arrangements - what is logged, for how long, who reviews it, and how alerts are escalated - and test operation for a sample period.",
        "Evaluate the incident response and recovery plan for completeness and currency, and test it against a sample of actual incidents (if any) or a walkthrough of the response process.",
        "Confirm whether an independent penetration test or vulnerability assessment has been performed in the period, and review the findings and remediation status.",
    ],
    "Information Security": [
        "Test the information/data classification and handling policy against a sample of actual data holdings to assess whether classification is applied in practice.",
        "Test user access and privilege management - new user provisioning, periodic access reviews, and timely revocation for a sample of leavers - for a sample of critical systems.",
        "Assess data protection and privacy compliance arrangements against the requirements of the Cyber and Data Protection Act [Chapter 12:07], including any data subject/consent processes and breach notification arrangements.",
        "Review third-party and cloud security arrangements - contracts, security clauses/SLAs, and any independent assurance reports (e.g. SOC 2) obtained over key providers - for a sample of critical third parties.",
    ],
    "Information Technology (ICT)": [
        "Review the ICT governance and strategy documentation and assess whether it is approved, current, and aligned to the entity's objectives.",
        "Test the change and release management process for a sample of system changes, including authorisation, testing, and segregation between development and production environments.",
        "For a sample of systems developed or acquired in the period, assess whether appropriate project governance, testing, and user acceptance procedures were followed.",
        "Test IT operations controls (job scheduling, incident/problem management, capacity management) for a sample period.",
        "Test backup and disaster recovery arrangements, including evidence of successful backup completion and a recent restore/recovery test.",
        "Review the business continuity plan for IT-dependent operations and assess whether it has been tested within a reasonable period.",
    ],
    "AML/CFT Compliance": [
        "Evaluate the entity's AML/CFT policies and procedures against the Money Laundering and Proceeds of Crime Act [Chapter 9:24] and applicable Financial Intelligence Unit guidance.",
        "Test customer due diligence (CDD) procedures for a sample of new customer onboardings, including identification, verification, and risk-rating.",
        "Test transaction monitoring and sanctions screening arrangements for a sample period, including follow-up of any alerts generated.",
        "Review suspicious transaction reporting arrangements and, for any reports filed in the period, assess timeliness and record-keeping.",
        "Test record-keeping of CDD information and transaction records for a sample of customers/transactions against the entity's retention policy and statutory requirements.",
    ],
}

BUSINESS_IT_AREA_REFERENCES = {
    "Cyber Security": "CS",
    "Information Security": "IS",
    "Information Technology (ICT)": "IT",
    "AML/CFT Compliance": "AC",
}

# The Secretarial Execution Plan - what the Substantive Procedures tab is
# replaced with on a "Secretarial" engagement (per the firm's instruction to
# change Substantive Procedures under Secretarial to an Execution Plan).
# Reuses the SubstantiveProcedureArea/SubstantiveProcedureItem machinery
# exactly like FORENSIC_SUBSTANTIVE_AREAS/BUSINESS_IT_SUBSTANTIVE_AREAS
# above (same sync/generate/sign-off/Word-Excel-export engine) - only the
# five "areas" (here, Operational Workstreams) and their baseline tasks
# differ. Each workstream becomes one section of the Execution Plan, built
# around statutory filing deadlines and deliverables under the Companies
# and Other Business Entities Act [Chapter 24:31] (COBE Act) rather than
# financial-statement assertions.
SECRETARIAL_SUBSTANTIVE_AREAS = [
    "Onboarding & Diagnostics",
    "Board & General Meeting Cycle",
    "Annual Returns & Filings Log",
    "Statutory Register Audit",
    "Corporate Actions",
]

SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES = {
    "Onboarding & Diagnostics": [
        "Perform initial search with the Registrar of Companies.",
        "Reconcile the Memorandum and Articles of Association for borrowing limits (Sec 196) and share capital authorisation.",
        "Run the Secretarial Risk Assessment Matrix (see the Risk Assessment tab) to flag overdue filings or missing records.",
        "Compile the Client Statutory Diagnostic & Baseline Report.",
    ],
    "Board & General Meeting Cycle": [
        "Draft and circulate the annual governance calendar to board members.",
        "Issue formal Notice of AGM (Sec 170) at least 21 days prior to the meeting.",
        "Compile, collate and dispatch board packs 7-10 days prior to scheduled meetings.",
        "Attend meetings, record proceedings, and produce draft minute books (Sec 176) within 5 working days post-meeting.",
        "File the signed board packs, approved minute books and action trackers.",
    ],
    "Annual Returns & Filings Log": [
        "Lodge the Annual Return (Form CR11) with the Registrar within 42 days of the AGM (Sec 165).",
        "File Form CR6 for any change in directors or secretaries within 14 days (Sec 215).",
        "Lodge Form CR14 for changes in registered office address within 14 days (Sec 160).",
        "File returns of allotment of shares within statutory timelines following board approval (Sec 93 & 95).",
        "File the stamped registry certificates and official filing acknowledgments.",
    ],
    "Statutory Register Audit": [
        "Maintain and update the Register of Beneficial Ownership (Sec 72), verifying no unapproved nominee shareholding exceeds the 20% statutory cap.",
        "Update the Register of Members (Sec 157) following executed share transfers (Sec 149).",
        "Reconcile the Register of Charges and Mortgages (Sec 162) against outstanding corporate debt facilities.",
        "File the up-to-date statutory register file and the annual UBO verification declaration.",
    ],
    "Corporate Actions": [
        "Coordinate board evaluation and drafting of the statutory Solvency and Liquidity Test Certificate (Sec 102) prior to executing distributions or share buybacks (Sec 104).",
        "Draft special resolutions for constitutional amendments (Sec 16 & 25) or company name changes.",
        "File mortgage/debenture registration forms with the Registrar within 30 days of creation (Sec 163).",
        "File the executed solvency certificates, filed special resolutions and registered security extracts.",
    ],
}

SECRETARIAL_AREA_REFERENCES = {
    "Onboarding & Diagnostics": "W1",
    "Board & General Meeting Cycle": "W2",
    "Annual Returns & Filings Log": "W3",
    "Statutory Register Audit": "W4",
    "Corporate Actions": "W5",
}

# Trigger/Event, Lead Responsible and Target Output/Deliverable metadata for
# each SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES task above, from the
# firm's Practical Secretarial Execution Plan Schedule - keyed by the exact
# procedure_text so sync_substantive_procedures (engagements.py) can attach
# it to the SubstantiveProcedureItem it creates, populating that item's
# trigger_event/responsible_role/target_output columns (see
# SubstantiveProcedureItem below). Only used for Secretarial engagements;
# every other engagement type leaves those three columns blank.
SECRETARIAL_EXECUTION_TASK_DETAILS = {
    "Perform initial search with the Registrar of Companies.": ("Engagement start", "Senior Secretariat", "Diagnostic Report & Risk Matrix"),
    "Reconcile the Memorandum and Articles of Association for borrowing limits (Sec 196) and share capital authorisation.": ("Engagement start", "Senior Secretariat", "Diagnostic Report & Risk Matrix"),
    "Run the Secretarial Risk Assessment Matrix (see the Risk Assessment tab) to flag overdue filings or missing records.": ("T+14 days from onboarding", "Senior Secretariat", "Diagnostic Report & Risk Matrix"),
    "Compile the Client Statutory Diagnostic & Baseline Report.": ("T+14 days from onboarding", "Senior Secretariat", "Client Statutory Diagnostic & Baseline Report"),
    "Draft and circulate the annual governance calendar to board members.": ("FYE + 5 months", "Corporate Secretary", "Annual governance calendar"),
    "Issue formal Notice of AGM (Sec 170) at least 21 days prior to the meeting.": ("FYE + 5 months", "Corporate Secretary", "Notice Pack & Proxy Forms"),
    "Compile, collate and dispatch board packs 7-10 days prior to scheduled meetings.": ("Each board/committee meeting", "Corporate Secretary", "Board pack dispatched"),
    "Attend meetings, record proceedings, and produce draft minute books (Sec 176) within 5 working days post-meeting.": ("FYE + 6 months (AGM) / each meeting", "Lead Secretary", "Signed AGM/board minutes"),
    "File the signed board packs, approved minute books and action trackers.": ("Post-meeting", "Minutes Secretary", "Signed Board Packs, Approved Minute Books & Action Trackers"),
    "Lodge the Annual Return (Form CR11) with the Registrar within 42 days of the AGM (Sec 165).": ("Post-AGM", "Compliance Officer", "Stamped Form CR11 receipt"),
    "File Form CR6 for any change in directors or secretaries within 14 days (Sec 215).": ("Director/secretary resignation or appointment", "Assistant Secretary", "Certified Form CR6 copy"),
    "Lodge Form CR14 for changes in registered office address within 14 days (Sec 160).": ("Registered office change", "Assistant Secretary", "Certified Form CR14 copy"),
    "File returns of allotment of shares within statutory timelines following board approval (Sec 93 & 95).": ("Share allotment approved", "Secretariat Staff", "Stamped Return of Allotment"),
    "File the stamped registry certificates and official filing acknowledgments.": ("On receipt from Registrar", "Compliance Officer", "Stamped Registry Certificates & Filing Acknowledgments"),
    "Maintain and update the Register of Beneficial Ownership (Sec 72), verifying no unapproved nominee shareholding exceeds the 20% statutory cap.": ("Annual review / update within 14 days of change", "Compliance Officer", "Reconciled Register of Beneficial Owners"),
    "Update the Register of Members (Sec 157) following executed share transfers (Sec 149).": ("Share transfer executed", "Compliance Officer", "Updated Register of Members"),
    "Reconcile the Register of Charges and Mortgages (Sec 162) against outstanding corporate debt facilities.": ("Debt creation / annual review", "Legal/Secretariat", "Reconciled Register of Charges"),
    "File the up-to-date statutory register file and the annual UBO verification declaration.": ("Annual", "Compliance Officer", "Up-to-date Statutory Register File & Annual UBO Verification Declaration"),
    "Coordinate board evaluation and drafting of the statutory Solvency and Liquidity Test Certificate (Sec 102) prior to executing distributions or share buybacks (Sec 104).": ("Distribution/buyback proposed", "Engagement Partner", "Signed Board Solvency Declaration"),
    "Draft special resolutions for constitutional amendments (Sec 16 & 25) or company name changes.": ("Ad-hoc - constitutional amendment", "Corporate Secretary", "Filed Special Resolutions"),
    "File mortgage/debenture registration forms with the Registrar within 30 days of creation (Sec 163).": ("Debt/charge creation", "Legal/Secretariat", "Stamped Certificate of Mortgage/Charge"),
    "File the executed solvency certificates, filed special resolutions and registered security extracts.": ("Ad-hoc", "Legal/Secretariat", "Executed Solvency Certificates, Filed Special Resolutions & Registered Security Extracts"),
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
    # The client's own logo (as opposed to the firm's, which is baked into
    # every generated document's letterhead - see workpapers._LOGO_PATH) -
    # used only on the Financial Statements cover page. Stored on disk under
    # Config.CLIENT_LOGOS_DATA_DIR, one file per client (replaced, not
    # accumulated, each time a new one is uploaded), same pattern as
    # CompanyDocument's own file storage.
    logo_filename = db.Column(db.String(255))

    engagements = db.relationship("Engagement", backref="client", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Client {self.name}>"


class Engagement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(30), nullable=False, default="Audit")
    subdivision = db.Column(db.String(50))  # only meaningful when type == "Secretarial"

    # Comma-separated SECRETARIAL_ACTIVITIES keys - which corporate
    # secretarial activities this engagement covers (only meaningful when
    # type == "Secretarial"). Used to filter which Understanding the
    # Business/Assignment questionnaire sections get seeded - see
    # SECRETARIAL_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS and
    # engagements.seed_entity_checklist. Blank/None means "no filter", i.e.
    # every section is seeded.
    secretarial_activities = db.Column(db.Text)

    # Comma-separated TAX_SERVICES keys - which Tax Advisory & Tax
    # Compliance services this engagement covers. Unlike
    # secretarial_activities, meaningful (and settable) on ANY engagement
    # type - see TAX_SERVICES above and has_tax_module/has_tax_advisory
    # below. Blank/None means the Tax tab is hidden entirely for this
    # engagement.
    tax_services = db.Column(db.Text)

    # Comma-separated ACCOUNTING_SERVICES keys - which Financial Accounting,
    # Cost Accounting & Management Accounting services this engagement
    # covers. Combinable onto ANY engagement type, exactly like tax_services
    # above - see ACCOUNTING_SERVICES and has_accounting_module/
    # has_cost_accounting/has_management_accounting below.
    accounting_services = db.Column(db.Text)
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

    # Which financial reporting framework the Financial Statements
    # (Finalisation tab) are prepared under - see REPORTING_FRAMEWORKS
    # above. Defaults to "full_ifrs" for every engagement (existing ones
    # included, via _add_missing_columns in app.py), matching how the
    # Financial Statements have always been presented here; change it on
    # the Finalisation tab if a given engagement is on IFRS for SMEs or a
    # local framework instead.
    reporting_framework = db.Column(db.String(20), default="full_ifrs", nullable=False)

    # Which method the Cash Flow Statement's operating activities section
    # uses - see CASH_FLOW_METHODS above and financials.build_cash_flow.
    # Defaults to "indirect" for every engagement (existing ones included),
    # matching how the Cash Flow Statement has always been presented here.
    cash_flow_method = db.Column(db.String(10), default="indirect", nullable=False)

    # Public Interest Entity checklist - see PIE_CRITERIA above. Any one of
    # these true makes the entity a Public Interest Entity for the purposes
    # of the "Company classification" guidance on the Finalisation tab.
    # All default False, i.e. "not (yet) known to be a PIE" - matching every
    # existing engagement, none of which is retroactively assumed to be one.
    pie_listed = db.Column(db.Boolean, default=False, nullable=False)
    pie_financial_institution = db.Column(db.Boolean, default=False, nullable=False)
    pie_insurer = db.Column(db.Boolean, default=False, nullable=False)
    pie_asset_manager = db.Column(db.Boolean, default=False, nullable=False)
    pie_pension_fund = db.Column(db.Boolean, default=False, nullable=False)
    pie_medical_aid = db.Column(db.Boolean, default=False, nullable=False)
    pie_debt_equity_issuer = db.Column(db.Boolean, default=False, nullable=False)

    # Small and Medium Enterprises Act classification - recorded, informational
    # client information only (see SME_ACT_SECTORS / SME_ACT_SIZE_BANDS
    # above); it does not drive reporting_framework. sme_size_band is the
    # preparer's own final selection - annual_turnover/gross_assets/staff_
    # headcount, when all three are entered, feed financials.classify_sme_size()
    # for a recommended band shown alongside the dropdown (see the
    # Finalisation tab's "Company classification" card), never overwriting
    # the preparer's own choice on their own.
    sme_sector = db.Column(db.String(50))
    sme_size_band = db.Column(db.String(10))
    sme_annual_turnover = db.Column(db.Float)
    sme_gross_assets = db.Column(db.Float)
    sme_staff_headcount = db.Column(db.Integer)

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

    @property
    def is_public_interest_entity(self):
        """Whether any Public Interest Entity checklist box (see
        PIE_CRITERIA) is ticked - PAAB's own test for which entities must
        report under Full IFRS rather than being eligible for IFRS for
        SMEs. Recomputed from the checklist every time, never stored
        separately, so it can never drift out of sync with the checklist."""
        return any(getattr(self, code) for code, _ in PIE_CRITERIA)

    @property
    def recommended_reporting_framework(self):
        """A hint only - see the "Company classification" card on the
        Finalisation tab. The preparer always makes the final call in the
        Financial reporting framework dropdown below it; this never changes
        reporting_framework on its own."""
        return "full_ifrs" if self.is_public_interest_entity else "ifrs_for_smes"

    @property
    def secretarial_activity_list(self):
        """The selected SECRETARIAL_ACTIVITIES keys for this engagement, as
        a plain list - only meaningful when type == "Secretarial". Empty
        list means "no filter selected" (every questionnaire section is
        seeded)."""
        if not self.secretarial_activities:
            return []
        return [a for a in self.secretarial_activities.split(",") if a]

    @property
    def tax_service_list(self):
        """The selected TAX_SERVICES keys for this engagement, as a plain
        list. Empty on every engagement until Tax services are explicitly
        turned on (see form.html) - unlike secretarial_activities, an empty
        list here means "no Tax module at all", not "no filter"."""
        if not self.tax_services:
            return []
        return [s for s in self.tax_services.split(",") if s]

    @property
    def has_tax_module(self):
        """Whether the Tax tab should appear on this engagement at all -
        either a dedicated Tax engagement type, or any Tax service
        explicitly turned on for another engagement type (e.g. tax
        compliance work bundled into an Audit engagement)."""
        return self.type in ("Tax Compliance", "Tax Advisory & Health Check") or bool(self.tax_service_list)

    @property
    def has_tax_advisory(self):
        """Whether the Tax tab's advisory-only sections (Technical Research
        Log, Structuring Options Analysis, Tax Dispute Management - Module
        7B) should be shown - only when the Tax Advisory or Representation
        service is actually on, never just because some other Tax service
        is."""
        services = self.tax_service_list
        return "advisory" in services or "representation" in services or self.type == "Tax Advisory & Health Check"

    @property
    def accounting_service_list(self):
        """The selected ACCOUNTING_SERVICES keys for this engagement, as a
        plain list. Empty on every engagement until an Accounting service is
        explicitly turned on (see form.html) - same "empty means off"
        convention as tax_service_list, not secretarial_activity_list's
        "empty means unfiltered"."""
        if not self.accounting_services:
            return []
        return [s for s in self.accounting_services.split(",") if s]

    @property
    def has_accounting_module(self):
        """Whether the Accounting tab should appear on this engagement at
        all - either the dedicated Accounting & Bookkeeping engagement type,
        or any Accounting service explicitly turned on for another
        engagement type (e.g. a bookkeeping clean-up bundled into an Audit
        engagement)."""
        return self.type == "Accounting & Bookkeeping" or bool(self.accounting_service_list)

    @property
    def has_cost_accounting(self):
        """Whether the Accounting tab's Cost Accounting sections (Cost
        Centres, Job/Process/Standard Costing, Variance Analysis, CVP
        Analysis) should be shown - only when the Cost Accounting service is
        actually on, never just because some other Accounting service is."""
        return "costing" in self.accounting_service_list

    @property
    def has_management_accounting(self):
        """Whether the Accounting tab's Management Accounting sections
        (Budgets, KPI Dashboard, the Management Accounts Report) should be
        shown - only when the Management Accounting service is actually
        on."""
        return "budgeting" in self.accounting_service_list

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


class Tickmark(db.Model):
    """One entry in the firm's tickmark legend - a short symbol (e.g. "TB",
    "PY", "V", "CB") plus its meaning, in the standard audit-workpaper
    convention. Firm-wide (not per-engagement), managed from the Tickmarks
    settings screen, and attachable to any Substantive Procedures item so a
    procedure can point at which tie-out/agreement a tick represents. The
    Engagement File Summary PDF prints the legend for whichever tickmarks
    were actually used in that engagement's file. Matches the filing
    policy's own rule (see FilingIndexSection below) that tick marks are
    defined once per working paper and never redefined elsewhere in the
    same file - this firm-wide legend is that one definition."""
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), nullable=False, unique=True)
    meaning = db.Column(db.String(300), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")

    def __repr__(self):
        return f"<Tickmark {self.symbol!r}>"


class FilingIndexSection(db.Model):
    """One numbered entry in the firm's Audit Working Paper Indexing &
    Filing Policy (see FILING_INDEX_MANUAL below for the full written
    policy) - a Current File (N-series, is_permanent=False) or Permanent
    File (P-series, is_permanent=True) working-paper reference, e.g.
    "N1001" = Materiality, "P1000" = Incorporation & Statutory. Firm-wide
    (not per-engagement), seeded once from the firm's filing index document
    (see seed.seed_filing_index) and editable from the Filing Index page -
    including retiring a number as "not used" (is_active=False) rather than
    deleting it outright, per the policy's own rule that a number, once
    used, is never reused even after the working paper it named is no
    longer needed."""
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), nullable=False, unique=True)  # "N1001", "P1000"
    is_permanent = db.Column(db.Boolean, default=False)  # False = Current File (N-series), True = Permanent File (P-series)
    category = db.Column(db.String(150))  # e.g. "Planning & Risk Assessment", "Incorporation & Statutory"
    section = db.Column(db.String(200), nullable=False)  # e.g. "Materiality"
    typical_contents = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)  # False = retired/"not used" - see docstring above
    order = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @property
    def display_label(self):
        return f"{self.code} — {self.section}"

    def __repr__(self):
        return f"<FilingIndexSection {self.code!r} {self.section!r}>"


# The firm's Audit Working Paper Indexing & Filing Policy, written out for
# the in-app Filing Index & Manual page - each entry is a section heading,
# its explanatory paragraph(s), and (where the policy itself uses a list)
# its bullet points. This is the policy as documented, plus a closing
# section explaining how this app implements it - it isn't itself the
# editable index of codes (see FilingIndexSection / seed.seed_filing_index
# for that); it's the "why" and "how" that goes with it.
FILING_INDEX_MANUAL = [
    {
        "heading": "Purpose",
        "paragraphs": [
            "To establish a single, consistent numbering and filing convention for audit working papers "
            "across all engagements, so that any file can be located, cross-referenced and reviewed "
            "without ambiguity, and so that files remain audit-trail compliant under ISA 230 (Audit "
            "Documentation).",
        ],
    },
    {
        "heading": "1. File Structure Overview",
        "paragraphs": ["Every client audit file is split into two parts:"],
        "bullets": [
            "Permanent File (P-series) — information of continuing relevance across audit years "
            "(incorporation documents, engagement letters, accounting policies, group structure, "
            "standing instructions).",
            "Current File (N-series) — working papers specific to the year under audit, organised by "
            "the numbered sections below.",
        ],
        "paragraphs_after": [
            "Each working paper is assigned a single, unique reference in the form:",
            "N [4-digit code] _ [Short Description] _ [Year]",
            "Example: N1001_Determination_of_Materiality_2025.xlsx",
            "The leading digit of the 4-digit code identifies the section (see the Filing Index page "
            "for the full, current list); the remaining three digits identify the specific working "
            "paper within that section, allocated sequentially and never reused, even if a working "
            "paper is later deleted or merged.",
        ],
    },
    {
        "heading": "2. Current File — Section Index",
        "paragraphs": [
            "The section ranges extend the numbering already in use on existing files into a complete "
            "scheme covering every audit area. The full, editable list of every working paper reference "
            "within each range is on the Filing Index page - the ranges themselves are:",
        ],
        "bullets": [
            "1000–1999: Planning & Risk Assessment",
            "2000–2999: Internal Control & Systems",
            "3000–3999: Statement of Financial Position — Assets",
            "4000–4999: Statement of Financial Position — Liabilities & Equity",
            "5000–5999: Statement of Comprehensive Income",
            "6000–6999: Other Audit Areas",
            "7000–7999: Compliance & Statutory",
            "8000–8999: Reporting & Disclosure",
            "9000–9999: Completion & Review",
        ],
    },
    {
        "heading": "3. Permanent File (P-series)",
        "paragraphs": [
            "Five fixed sections (P1000–P5000) covering incorporation & statutory documents, engagement "
            "administration, accounting policies, prior year financial statements, and structure & "
            "governance - see the Permanent File section of the Filing Index page for the complete "
            "list, and the Permanent File tab on each client's page to file documents under them.",
        ],
    },
    {
        "heading": "4. Filing Conventions",
        "bullets": [
            "Folder structure: [Client Name] / [Financial Year] / Permanent File | Current File, "
            "mirroring the sections above.",
            "File naming: N[code]_[Short Description]_[Year].[ext] — no spaces other than underscores, "
            "no client name repeated inside the current-year folder.",
            "Every working paper carries a header block: Client, Year End, Prepared By/Date, Reviewed "
            "By/Date, Objective, and Conclusion.",
            "Tick marks are defined once per working paper (on a 'Tickmarks' tab or footnote) and never "
            "redefined elsewhere in the same file.",
            "Cross-references between working papers use the WP number only (e.g. \"agreed to N1000\"), "
            "never a page number or free-text description.",
            "Numbers are never reused. If a working paper is no longer required, its number is left "
            "vacant and noted as \"not used\" in the index, not reassigned.",
            "Each file is superseded, not overwritten, when revised after initial sign-off; the review "
            "trail (e.g. queries raised and cleared) is retained, not deleted.",
        ],
    },
    {
        "heading": "How this app applies the policy",
        "paragraphs": [
            "The Filing Index page is the firm-wide, editable list of every N-series and P-series code - "
            "add a code, correct one, or mark a number \"not used\" (per the convention above, numbers "
            "are never deleted or reassigned, only retired).",
            "Every Substantive Procedures area already carries its matching N-code (shown on its heading "
            "and in the generated Word/Excel workpapers), and the fixed workpaper sections that have a "
            "direct equivalent in the index (Materiality, Understanding the Entity, the Management "
            "Representation Letter, Report to Management, and others) show that code on their downloads "
            "and in the Engagement File Summary PDF, in place of the app's older lettered references.",
            "Uploading a working paper under Documents (or under a Substantive Procedures area) lets you "
            "tag it with its WP number from the index; its download name then follows the "
            "N[code]_[Description]_[Year] convention automatically.",
            "Permanent File items (P-series) are filed once per client, on that client's own Permanent "
            "File tab, rather than being re-uploaded to every engagement.",
            "The firm's shared Tickmark legend (its own page) is exactly the \"defined once per working "
            "paper, never redefined elsewhere\" tick mark convention above, applied firm-wide rather than "
            "file-by-file.",
        ],
    },
]


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
    """A single likelihood x impact risk register entry. Used by the Tax
    module's Risk Register ("tax") and the Accounting module's Risk
    Register ("accounting") - `module` tags which register a row belongs to
    so the two (and any future register on the same engagement) can never
    mix rows with each other."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    module = db.Column(db.String(20))  # "tax" | "accounting" - see docstring above
    category = db.Column(db.String(120))
    risk_description = db.Column(db.Text, nullable=False)
    likelihood = db.Column(db.Integer, default=3)  # 1-5
    impact = db.Column(db.Integer, default=3)  # 1-5
    mitigation = db.Column(db.Text)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    status = db.Column(db.String(20), default="Open")
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("User", foreign_keys=[owner_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

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
    # Optional tag to a specific Current File (N-series) working-paper
    # number from the firm's Filing Index (see FilingIndexSection) - when
    # set, the document's download name follows the firm's
    # N[code]_[Description]_[Year] filing convention instead of its
    # original filename. Nullable: tagging is optional, same as the older
    # free-text `reference` field above (kept for anything the index
    # doesn't cover, or for firms/engagements not using it).
    filing_index_id = db.Column(db.Integer, db.ForeignKey("filing_index_section.id"))

    # System-generated workpapers (see engagements.py's
    # _file_generated_workpaper and the "Generate & File" buttons on the
    # Finalisation/Checklist/Substantive Procedures tabs) are filed as
    # ordinary Documents - reusing the same storage, versioning-by-count,
    # download and Filing-Index tagging already built for uploads - plus
    # these extra fields so a generated workpaper carries its own
    # Prepared/Reviewed/Partner sign-off, same as every other workpaper in
    # the app (Trial Balance, Analytical Review, Audit Adjustments, ...).
    # is_generated distinguishes these from a person's own upload (which
    # never has this sign-off asked of it); workpaper_kind is the stable
    # machine key (e.g. "financial_statements") used to group every
    # version of the SAME generated workpaper together and to work out the
    # next version number - regenerating never overwrites or deletes a
    # prior version (the firm's own Filing Index policy: "superseded, not
    # overwritten"), it only flips is_current_version to False on the ones
    # it replaces.
    is_generated = db.Column(db.Boolean, default=False)
    workpaper_kind = db.Column(db.String(40))
    is_current_version = db.Column(db.Boolean, default=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    substantive_area = db.relationship("SubstantiveProcedureArea", backref=db.backref("documents", lazy=True, order_by="Document.uploaded_at.desc()"))
    filing_index = db.relationship("FilingIndexSection")

    # Aliases so the standard effectively_reviewed()/sign-off display
    # pattern used everywhere else in the app (which reads
    # completed_by/completed_by_id as "who prepared this") works unchanged
    # on a generated Document too, without a duplicate pair of columns -
    # "prepared" a generated workpaper IS "uploaded" it, from the system's
    # point of view.
    @property
    def completed_by(self):
        return self.uploaded_by

    @property
    def completed_by_id(self):
        return self.uploaded_by_id

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None


class PermanentFileDocument(db.Model):
    """A Permanent File (P-series) working paper, filed once against a
    Client rather than any one engagement - the firm's Audit Working Paper
    Indexing & Filing Policy describes this as "information of continuing
    relevance across audit years" (incorporation documents, the standing
    engagement letter, the accounting policy manual, prior-year financial
    statements, structure & governance records) as distinct from an
    engagement's own Current File (N-series) documents, which are specific
    to the year under audit. Deliberately a simple file + tag + notes
    record (unlike CompanyDocument, which runs AI extraction to find
    Directors/Shareholders) - the Permanent File just needs a place to live
    and a P-code, not extraction."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    filing_index_id = db.Column(db.Integer, db.ForeignKey("filing_index_section.id"))
    notes = db.Column(db.Text)
    version = db.Column(db.Integer, default=1)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("permanent_file_documents", lazy=True, order_by="PermanentFileDocument.uploaded_at.desc()", cascade="all, delete-orphan"))
    filing_index = db.relationship("FilingIndexSection")
    uploaded_by = db.relationship("User")

    def __repr__(self):
        return f"<PermanentFileDocument {self.original_filename!r}>"


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


class PersonalTask(db.Model):
    """A to-do item that isn't tied to any specific engagement - the "upload
    work and tasks they are working on" side of the firm-wide Task / To-Do
    List tab (see hr.project_board, which combines these with EngagementTask
    rows into one view). Kept as its own table rather than making
    EngagementTask.engagement_id nullable, so nothing about the existing
    per-engagement Tasks tab or Project Management board changes for any
    engagement already using it.

    Deliberately lighter than EngagementTask: general/admin work ("book my
    leave", "read the updated AML policy", "prep the training slides") isn't
    audit-file evidence, so it gets a simple Preparer sign-off (completed_by/
    completed_at, set automatically when marked Done) rather than the full
    Reviewer/Partner sign-off chain.
    """
    id = db.Column(db.Integer, primary_key=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    # Optional context link - e.g. "chase the PBC list for Client X" without
    # this being a formal audit-file task on that engagement's own Tasks tab.
    # Confidentiality is still respected: see hr.project_board's filtering.
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    due_date = db.Column(db.Date)
    priority = db.Column(db.String(20), default="Normal")  # Low | Normal | High
    status = db.Column(db.String(20), default="To Do")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    engagement = db.relationship("Engagement")

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < date.today() and self.status != "Done")

    def __repr__(self):
        return f"<PersonalTask {self.id} {self.title!r}>"


def notify_task_assignment(actor, recipient_id, subject, body):
    """Sends a one-off internal Message (see Message/MessageRecipient) from
    `actor` to `recipient_id` - used whenever a task (EngagementTask or
    PersonalTask) is created or changed in a way that affects someone other
    than the person making the change, e.g. assigning a task to a colleague,
    or updating the due date/status of a task assigned to someone else. This
    is the notification mechanism for the Task / To-Do List tab: task
    updates surface to the affected person the same way any other internal
    message does (their Messages inbox, and its unread-count badge).

    No-op if there's no recipient, or the recipient is the actor themselves
    - nothing to notify anyone about in that case. Does not commit; the
    caller's own db.session.commit() picks this up along with the task
    change itself."""
    if not recipient_id or recipient_id == actor.id:
        return
    message = Message(sender_id=actor.id, subject=subject, body=body)
    db.session.add(message)
    db.session.flush()
    db.session.add(MessageRecipient(message_id=message.id, user_id=recipient_id))


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

    entity_understanding = db.relationship(
        "EntityUnderstanding",
        backref=db.backref("checklist_items", lazy=True, cascade="all, delete-orphan", order_by="EntityUnderstandingChecklistItem.order"),
    )
    created_by = db.relationship("User")

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
    ("Ownership & Capital Structure Analytics", "Share allotment vs. authorised capital ratios - variance check between issued share capital, share premium, and authorised limits in the Memorandum/Articles before processing new allotments (Section 93 & 95)."),
    ("Ownership & Capital Structure Analytics", "Beneficial ownership threshold analysis - analyse voting rights/shareholding aggregation to identify individuals crossing the 20% beneficial ownership threshold (Section 72)."),
    ("Ownership & Capital Structure Analytics", "Dividend & distribution vs. solvency - cross-analyse cash/reserves against proposed distributions to check compliance with the statutory Solvency and Liquidity Test (Section 102)."),
    ("Statutory Filing & Timeline Variance Analysis", "Filing lag analysis - measure the time gap between corporate triggers (director appointments, share allotments, charge creations) and their actual filing dates with the Registrar."),
    ("Statutory Filing & Timeline Variance Analysis", "Annual Return vs. financial year-end deadlines - track AGM dates against the statutory requirement (within 6 months of financial year-end) and Annual Return submission deadlines (Section 165)."),
    ("Charge & Encumbrance Reconciliation", "Debt/security analytics - compare total registered mortgages, charges and debentures in the Register of Charges (Section 162) against outstanding borrowing facilities to identify unregistered or unfiled charges (Section 163-164)."),
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
    label = db.Column(db.String(150), nullable=False)  # e.g. "Revenue", "Gross profit"
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
    order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<DeferredTaxItem computation={self.computation_id}>"


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
    draft_financials_target = db.Column(db.Date)  # FYE + 3-4 months
    agm_notice_target = db.Column(db.Date)  # FYE + 5 months (21 days before AGM, Sec 170)
    agm_target = db.Column(db.Date)  # FYE + 6 months (Sec 167)
    annual_return_target = db.Column(db.Date)  # AGM + 42 days (Sec 165, Form CR11/CR23)
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


class LegislativeUpdate(db.Model):
    """One entry in the firm-wide Legislative Update Control log (Module 8)
    - a change in tax legislation/practice the firm has logged and
    assessed the impact of. Firm-wide rather than per-engagement (a Finance
    Act change isn't specific to one client) - see tax.py's
    legislative_updates list page, reachable from the Tax tab of any
    engagement with the Tax module on, and from HR & Administration."""
    id = db.Column(db.Integer, primary_key=True)
    tax_head = db.Column(db.String(80))
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text)
    effective_date = db.Column(db.Date)
    source_reference = db.Column(db.String(200))
    impact_assessment = db.Column(db.Text)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    def __repr__(self):
        return f"<LegislativeUpdate {self.title!r}>"


class TaxChecklistItem(db.Model):
    """One tick + comment line of the configurable Tax Head Checklists /
    Quality Gates (Module 8) - a lightweight, single-preparer-tick list
    (unlike FinalisationChecklist, this doesn't carry its own separate
    3-tier sign-off - the engagement's own Finalisation checklist and the
    Tax Opinion/Health Check's own sign-offs already cover formal
    sign-off for tax work; this is the working checklist itself). Grouped
    by tax_head on the Tax tab; add freely per engagement rather than being
    seeded from a fixed template, since which quality gates matter varies
    by which Tax services are on."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    tax_head = db.Column(db.String(80))
    item_text = db.Column(db.Text, nullable=False)
    response = db.Column(db.String(10), default="")  # "" = not yet assessed, "Yes", "No", "N/A"
    comment = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("tax_checklist_items", lazy=True, cascade="all, delete-orphan", order_by="TaxChecklistItem.order"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<TaxChecklistItem {self.item_text!r} response={self.response!r} engagement={self.engagement_id}>"


# Seeded onto every engagement's Tax Head Checklist the first time its Tax
# tab is opened (see tax._seed_tax_checklist) - a starting quality-gate
# checklist a preparer tailors from there, same "system drafts, preparer
# tailors" pattern as every other seeded checklist in this app. Kept
# general rather than ZIMRA-form-specific, since exact form numbers/steps
# change with legislation (same reasoning as PenaltyInterestRate above).
DEFAULT_TAX_CHECKLIST_ITEMS = [
    (None, "Has the client's registration status been confirmed current for every applicable tax head?"),
    (None, "Have all returns due for the period been identified against the Statutory Tax Calendar?"),
    ("Corporate Income Tax (CIT)", "Has the CIT computation been agreed to the trial balance/financial statements?"),
    ("Value Added Tax (VAT)", "Has output VAT been reconciled to recorded revenue for the period?"),
    ("Value Added Tax (VAT)", "Has input VAT claimed been supported by valid tax invoices?"),
    ("PAYE", "Has PAYE deducted been reconciled to the payroll records for the period?"),
    ("Withholding Tax (WHT)", "Has withholding tax been correctly applied and remitted on applicable payments?"),
    (None, "Have all known tax positions been logged on the Tax Position Register with an approval status?"),
    (None, "Has any legislative change logged since the last review been assessed for impact on this engagement?"),
    (None, "Has the Quality Control review of the tax working papers been completed before sign-off?"),
]


# ---------- Financial Accounting, Cost Accounting & Management Accounting module ----------
#
# Shown on an engagement's "Accounting" tab whenever Engagement.
# has_accounting_module is True (a dedicated Accounting & Bookkeeping
# engagement, or any other engagement type with an Accounting service
# explicitly turned on - see ACCOUNTING_SERVICES above) - see accounting.py
# for the routes. This module covers work where Neverlank itself prepares
# a client's books/costing/management reports, as distinct from an Audit/
# Assurance engagement forming an opinion on figures someone else prepared.
#
# Reuses existing machinery wherever it already fits, exactly like the Tax
# module does (see the module comment above "Tax Advisory & Tax Compliance
# module"):
#   - the Accounting Risk Register reuses RiskItem (module="accounting")
#   - the write-up/close-out Execution Plan reuses SubstantiveProcedureArea/
#     Item, one area per ACCOUNTING_AREAS entry
#   - the General Ledger rolls up into the existing TrialBalance/
#     TrialBalanceLine models, so the existing Trial Balance tab's mapping
#     UI and the existing financials.py statement-generation pipeline
#     (Statement of Financial Position, Comprehensive Income, Cash Flow,
#     IFRS Notes) take over unchanged from there - see
#     accounting.roll_up_to_trial_balance
#   - Accounting Document Management reuses the existing Documents/Filing
#     Index rather than a new document model
#   - the Management Accounts Report's 10 parts reuse WorkpaperNarrative,
#     one row per named part - see MANAGEMENT_ACCOUNTS_REPORT_PARTS below
#   - the Accounting Checklist / Quality Gates reuses the same lightweight
#     response/comment/completed_by shape as TaxChecklistItem above
# The models below are the genuinely new pieces that nothing existing
# already covers: a General Ledger (this app previously had no double-entry
# bookkeeping model at all - Trial Balance was always a flat imported
# balances list), Bank Reconciliations, and the Cost/Management Accounting
# working papers (Cost Centres, Job/Process/Standard Costing, Variance
# Analysis, CVP Analysis, Budgets, KPI tracking).

GL_ACCOUNT_TYPES = ["Asset", "Liability", "Equity", "Income", "Expense"]
GL_DEBIT_NORMAL_TYPES = ("Asset", "Expense")  # the rest (Liability/Equity/Income) are credit-normal


class GLAccount(db.Model):
    """One line of an engagement's Chart of Accounts (Financial Accounting/
    Bookkeeping). Engagement-scoped, like everything else in this app,
    rather than a continuous multi-period ledger - each accounting
    engagement/period gets its own chart, opened with whatever
    opening_balance the preparer brings forward from the prior period's
    closing trial balance. normal_balance is derived from account_type
    (never stored separately, so it can never drift out of sync) and is
    only ever used to decide which side of the rolled-up Trial Balance line
    an account's net balance lands on - see roll_up_to_trial_balance."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    code = db.Column(db.String(30), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    account_type = db.Column(db.String(20), nullable=False, default="Expense")
    opening_balance = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    order = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("gl_accounts", lazy=True, cascade="all, delete-orphan", order_by="GLAccount.order, GLAccount.code"))
    created_by = db.relationship("User")

    @property
    def normal_balance(self):
        return "Debit" if self.account_type in GL_DEBIT_NORMAL_TYPES else "Credit"

    def __repr__(self):
        return f"<GLAccount {self.code} {self.name!r} engagement={self.engagement_id}>"


JOURNAL_ENTRY_SOURCES = ["Manual", "Bank", "Sales", "Purchases", "Payroll", "Adjustment"]


class JournalEntry(db.Model):
    """One double-entry journal entry (header) posted to the General
    Ledger - the genuinely new bookkeeping primitive this app didn't have
    before (Trial Balance was always a flat imported balances list; see the
    module comment above). Carries the standard Preparer/Reviewer/Partner
    sign-off, same as every other workpaper in this app - reviewing a
    journal here is a bookkeeping quality-control step, distinct from an
    audit adjustment (AuditAdjustment), which is an auditor's proposed
    correction to a client-prepared trial balance."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    entry_date = db.Column(db.Date, nullable=False, default=date.today)
    reference = db.Column(db.String(100))
    narration = db.Column(db.Text)
    source = db.Column(db.String(20), default="Manual")

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("journal_entries", lazy=True, cascade="all, delete-orphan", order_by="JournalEntry.entry_date, JournalEntry.id"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    lines = db.relationship("JournalEntryLine", backref="journal_entry", lazy=True, cascade="all, delete-orphan", order_by="JournalEntryLine.id")

    @property
    def total_debit(self):
        return sum(l.debit or 0 for l in self.lines)

    @property
    def total_credit(self):
        return sum(l.credit or 0 for l in self.lines)

    @property
    def is_balanced(self):
        return len(self.lines) > 0 and abs(self.total_debit - self.total_credit) < 0.01

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<JournalEntry {self.reference!r} engagement={self.engagement_id}>"


class JournalEntryLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), nullable=False)
    gl_account_id = db.Column(db.Integer, db.ForeignKey("gl_account.id"), nullable=False)
    description = db.Column(db.String(200))
    debit = db.Column(db.Float, default=0.0)
    credit = db.Column(db.Float, default=0.0)

    gl_account = db.relationship("GLAccount")

    def __repr__(self):
        return f"<JournalEntryLine account={self.gl_account_id} dr={self.debit} cr={self.credit}>"


BANK_RECONCILIATION_ITEM_TYPES = [
    "Outstanding Deposit", "Outstanding Cheque", "Bank Error", "Book Error", "Other",
]


class BankReconciliation(db.Model):
    """One statement-date bank reconciliation working paper - one per
    engagement per statement date (an engagement typically has several over
    its history, one per period/month reconciled), rather than one per
    engagement, since reconciling a bank account is a recurring monthly
    procedure, not a single point-in-time record like TaxEntityProfile."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    gl_account_id = db.Column(db.Integer, db.ForeignKey("gl_account.id"))
    statement_date = db.Column(db.Date, nullable=False, default=date.today)
    bank_statement_balance = db.Column(db.Float, default=0.0)
    book_balance = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("bank_reconciliations", lazy=True, cascade="all, delete-orphan", order_by="BankReconciliation.statement_date.desc()"))
    gl_account = db.relationship("GLAccount")
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])
    items = db.relationship("BankReconciliationItem", backref="reconciliation", lazy=True, cascade="all, delete-orphan", order_by="BankReconciliationItem.id")

    @property
    def adjusted_bank_balance(self):
        return (self.bank_statement_balance or 0) + sum(i.amount or 0 for i in self.items if i.side == "bank")

    @property
    def adjusted_book_balance(self):
        return (self.book_balance or 0) + sum(i.amount or 0 for i in self.items if i.side == "book")

    @property
    def is_reconciled(self):
        return abs(self.adjusted_bank_balance - self.adjusted_book_balance) < 0.01

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<BankReconciliation {self.statement_date} engagement={self.engagement_id}>"


class BankReconciliationItem(db.Model):
    """One reconciling item - side is which balance it adjusts ("bank" for
    an item explaining the gap on the bank statement's side, e.g. an
    outstanding cheque/deposit or a bank error; "book" for an item
    explaining the gap on the client's own books, e.g. an unrecorded bank
    charge). amount is signed by the preparer (positive to add, negative to
    subtract) rather than the model inferring a sign from item_type, since
    which way an item moves the balance is a judgement call the preparer
    is best placed to make for that specific item."""
    id = db.Column(db.Integer, primary_key=True)
    reconciliation_id = db.Column(db.Integer, db.ForeignKey("bank_reconciliation.id"), nullable=False)
    side = db.Column(db.String(10), nullable=False, default="bank")  # "bank" | "book"
    item_type = db.Column(db.String(30), default="Other")
    description = db.Column(db.String(200))
    item_date = db.Column(db.Date)
    amount = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f"<BankReconciliationItem {self.item_type} {self.amount} side={self.side}>"


COST_CENTRE_TYPES = ["Production", "Service", "Administration"]


class CostCentre(db.Model):
    """One cost centre/department for Cost Accounting - a light grouping
    tag that CostingRecord entries can optionally be attributed to, not a
    full departmental-allocation engine."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    code = db.Column(db.String(30))
    name = db.Column(db.String(150), nullable=False)
    centre_type = db.Column(db.String(20), default="Production")
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("cost_centres", lazy=True, cascade="all, delete-orphan", order_by="CostCentre.name"))
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<CostCentre {self.name!r} engagement={self.engagement_id}>"


COSTING_METHODS = ["Job Order Costing", "Process Costing", "Standard Costing"]


class CostingRecord(db.Model):
    """One job/batch/process cost card - the Cost Accounting service's core
    working paper. Which of the three classical costing methods it's using
    is costing_method (a label only - all three share the same "four cost
    components -> total -> unit cost" shape, so there's no reason to give
    them three separate tables, same reasoning as TaxRegistration's
    record_type above)."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    costing_method = db.Column(db.String(30), nullable=False, default="Job Order Costing")
    reference = db.Column(db.String(150), nullable=False)  # job/batch number or product/process name
    cost_centre_id = db.Column(db.Integer, db.ForeignKey("cost_centre.id"))
    units_produced = db.Column(db.Float, default=1.0)
    direct_material_cost = db.Column(db.Float, default=0.0)
    direct_labour_cost = db.Column(db.Float, default=0.0)
    variable_overhead_cost = db.Column(db.Float, default=0.0)
    fixed_overhead_cost = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("costing_records", lazy=True, cascade="all, delete-orphan", order_by="CostingRecord.id"))
    cost_centre = db.relationship("CostCentre")
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def total_cost(self):
        return (self.direct_material_cost or 0) + (self.direct_labour_cost or 0) + (self.variable_overhead_cost or 0) + (self.fixed_overhead_cost or 0)

    @property
    def unit_cost(self):
        return self.total_cost / self.units_produced if self.units_produced else None

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    def __repr__(self):
        return f"<CostingRecord {self.reference!r} {self.costing_method} engagement={self.engagement_id}>"


VARIANCE_TYPES = [
    "Material Price", "Material Usage", "Labour Rate", "Labour Efficiency",
    "Variable Overhead Expenditure", "Variable Overhead Efficiency",
    "Fixed Overhead Expenditure", "Fixed Overhead Volume",
]


class StandardCostVariance(db.Model):
    """One standard-costing variance calculation. variance is always
    actual_amount - standard_amount (never the other way round) so its sign
    is consistent across every variance type; for every type in
    VARIANCE_TYPES, a cost that came in at or under standard (variance <=
    0) is favourable - see is_favourable. Optionally linked to a
    CostingRecord (the job/batch the variance relates to), but can also
    stand alone (e.g. a period-level overhead variance not tied to one
    specific job)."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    costing_record_id = db.Column(db.Integer, db.ForeignKey("costing_record.id"))
    variance_type = db.Column(db.String(40), nullable=False)
    standard_amount = db.Column(db.Float, nullable=False, default=0.0)
    actual_amount = db.Column(db.Float, nullable=False, default=0.0)
    explanation = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("standard_cost_variances", lazy=True, cascade="all, delete-orphan", order_by="StandardCostVariance.id"))
    costing_record = db.relationship("CostingRecord")
    created_by = db.relationship("User")

    @property
    def variance(self):
        return (self.actual_amount or 0) - (self.standard_amount or 0)

    @property
    def is_favourable(self):
        return self.variance <= 0

    def __repr__(self):
        return f"<StandardCostVariance {self.variance_type} {self.variance} engagement={self.engagement_id}>"


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
    def screening_assessment(self):
        """A plain-language, advisory-only read of the Sanctions & Adverse
        Notice Screening list below (see SanctionsScreening) - same pattern
        as checklist_assessment above: it never sets or overrides the
        `decision` field, it just surfaces what still needs attention or
        escalation. A "Confirmed Hit" on even one list, for even one
        individual, is treated as the most severe outcome regardless of how
        many others are clear."""
        individuals = list(self.screenings)
        total = len(individuals)
        if total == 0:
            return {"label": "No key individuals added for sanctions screening yet.", "level": "muted", "hits": 0, "potential": 0, "outstanding": 0, "total": 0}
        hits = sum(1 for i in individuals if i.overall_status == "Confirmed Hit")
        potential = sum(1 for i in individuals if i.overall_status == "Potential Match")
        outstanding = sum(1 for i in individuals if not i.is_screened)
        if hits:
            label = f"{hits} of {total} individual{'s' if total != 1 else ''} returned a confirmed hit - escalate to the Partner/MLRO before proceeding."
            level = "danger"
        elif potential:
            label = f"{potential} of {total} individual{'s' if total != 1 else ''} returned a potential match - resolve before proceeding."
            level = "warning"
        elif outstanding:
            label = f"{outstanding} of {total} individual{'s' if total != 1 else ''} not yet screened against all five lists."
            level = "warning"
        else:
            label = f"All {total} individual{'s' if total != 1 else ''} screened clear against the UN, OFAC, EU, RBZ and FIU lists."
            level = "success"
        return {"label": label, "level": level, "hits": hits, "potential": potential, "outstanding": outstanding, "total": total}

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

        for screening in self.screenings:
            if screening.overall_status in ("Confirmed Hit", "Potential Match"):
                detail = screening.overall_status + (f" ({screening.role_description})" if screening.role_description else "")
                issues.append({"key": f"screening:{screening.id}", "label": f"Sanctions screening — {screening.individual_name}", "detail": detail})

        is_forensic = self.engagement and self.engagement.type == "Investigative Engagement"
        is_secretarial = self.engagement and self.engagement.type == "Secretarial"
        if is_forensic:
            section_checks = [
                ("conflict_threat_clear", False, "Conflict of Interest & Threat Assessment", "conflict_threat_notes"),
                ("edd_completed", False, "Enhanced Due Diligence", "edd_notes"),
                ("legal_evidence_satisfactory", False, "Legal Framework & Evidence Control", "legal_evidence_notes"),
                ("competence_scope_confirmed", False, "Competence & Scope Realism", "competence_scope_notes"),
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

        if not is_forensic and not is_secretarial and not self.predecessor_not_applicable:
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
    ("Independence", "Does the firm or any engagement team member have a financial interest in, or a close relationship with, the entity's IT service providers, cloud vendors, or cyber security vendors?"),
    ("Independence", "Has the firm previously provided IT implementation, cyber security remediation, or AML/CFT advisory services to this entity that would create a self-review threat over the same subject matter now being assessed?"),
    ("Independence", "Are the proposed engagement team members free of any relationship that could compromise objectivity when assessing the entity's own IT/security/AML controls?"),
    ("Predecessor", "If a predecessor firm previously performed IT, cyber security or AML/CFT assurance work for this entity, has permission been obtained to make appropriate enquiries of them?"),
    ("Predecessor", "Were there any disagreements or concerns raised by a predecessor regarding the entity's IT general controls, cyber security posture, or AML/CFT compliance?"),
    ("Competence", "Does the engagement team include (or have access to) specialists with genuine cyber security, information security, ICT audit, and AML/CFT expertise appropriate to this entity's environment?"),
    ("Competence", "Is the team familiar with the relevant Zimbabwean legal/regulatory framework - the Cyber and Data Protection Act [Chapter 12:07], the Money Laundering and Proceeds of Crime Act [Chapter 9:24], and applicable FIU guidance?"),
    ("Competence", "Has the logistics of accessing the entity's systems been agreed (read-only access levels, supervised access, remote vs on-site testing, timing to avoid disrupting live operations)?"),
    ("Competence", "Can the firm resource this engagement without overextending the specialists required, given other concurrent commitments?"),
    ("Regulatory", "Have appropriate confidentiality and data-handling arrangements been agreed for any highly sensitive data (personal information, security configurations, vulnerability findings) that will be accessed or generated during the engagement?"),
    ("Regulatory", "Does the scope of testing (e.g. any vulnerability scanning or penetration testing) require a formal rules-of-engagement/authorisation letter to avoid any suggestion of unauthorised system access?"),
    ("Regulatory", "Are there sector-specific regulatory considerations (e.g. POTRAZ, RBZ) that apply to this entity's IT/cyber/AML obligations and should shape the scope?"),
    ("Regulatory", "Will any findings need to be reported to, or may trigger obligations with, a regulator or the Financial Intelligence Unit, and has this been discussed with the client in advance?"),
    ("Fee & quality", "The expected fee is commensurate with the specialist skill and time required, without compromising the quality of the engagement."),
    ("Engagement letter", "The client has agreed to the scope (which domains - cyber security, information security, ICT, AML/CFT - are covered), timeline, responsibilities, access arrangements, and fee basis set out in the engagement letter."),
]

# Seeded instead of DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS when the engagement's
# type is "Secretarial" (see acceptance.seed_acceptance_checklist) -
# corporate secretarial / company registration work raises acceptance
# considerations a financial-statement checklist doesn't cover (Ultimate
# Beneficial Ownership, filing-agent authority, registry/company-formation
# risk). Section names match the five Secretarial Client Acceptance
# Checklist sections that REPLACE the five generic narrative sections for
# this engagement type (see ClientAcceptance.kyc_ubo_satisfactory etc. and
# items_complete above).
SECRETARIAL_ACCEPTANCE_CHECKLIST_ITEMS = [
    ("KYC & Ultimate Beneficial Ownership", "Who is the Ultimate Beneficial Owner (UBO) holding more than 20-25% of shares or voting rights?"),
    ("KYC & Ultimate Beneficial Ownership", "Are any of the directors, shareholders, or UBOs classified as a Politically Exposed Person (PEP) or close associates of a PEP?"),
    ("KYC & Ultimate Beneficial Ownership", "Do any names appear on international sanctions lists, anti-money laundering (AML) watchlists, or terrorist financing databases?"),
    ("KYC & Ultimate Beneficial Ownership", "What is the source of wealth and source of funds being used to capitalize the new company or fund the transaction?"),
    ("KYC & Ultimate Beneficial Ownership", "Have certified/notarized government-issued IDs and proof of residential address been obtained for all key stakeholders?"),
    ("Authority, Integrity & Client Background", "Does the individual providing the instructions have the legal capacity and explicit authority (e.g. Board Resolution or Power of Attorney) to act on behalf of the entity?"),
    ("Authority, Integrity & Client Background", "What is the client's reputation and history - are there past involvements in bankruptcies, corporate fraud, or regulatory penalties?"),
    ("Authority, Integrity & Client Background", "Why is the client looking to register a new company or change structures at this specific time?"),
    ("Authority, Integrity & Client Background", "If taking over an existing company, why are they changing service providers, and are there outstanding fees or disputes with the previous agent?"),
    ("Nature of Business & Risk Profile", "What is the detailed intended principal activity of the company?"),
    ("Nature of Business & Risk Profile", "Does the business operate in a high-risk sector (e.g. cryptocurrency, gambling, arms trade, precious metals, offshore banking)?"),
    ("Nature of Business & Risk Profile", "Will the company require a specialized regulatory license to operate (e.g. financial services, telecom, mining)?"),
    ("Nature of Business & Risk Profile", "What is the geographical footprint of the business - where are its main markets, suppliers, and bank accounts located?"),
    ("Conflict of Interest & Dispute Assessment", "Do we currently represent any competitors, adversaries, or conflicting parties related to this client or transaction?"),
    ("Conflict of Interest & Dispute Assessment", "Is there an internal dispute or deadlock among the current shareholders or directors regarding the proposed changes?"),
    ("Conflict of Interest & Dispute Assessment", "Will executing these corporate changes benefit one group of shareholders to the detriment of another?"),
    ("Conflict of Interest & Dispute Assessment", "Is there any risk that this entity is being created or altered to evade taxes, existing legal liabilities, or court orders?"),
    ("Operational Capacity & Commercial Feasibility", "Do we have the technical expertise and local licensing to handle this specific corporate structure (e.g. public listed companies, trusts, offshore entities)?"),
    ("Operational Capacity & Commercial Feasibility", "Is the timeline demanded by the client realistic given local registry (e.g. Companies Office) processing times?"),
    ("Operational Capacity & Commercial Feasibility", "Has the client agreed to our fee structure, billing terms, and retainer requirements?"),
    ("Operational Capacity & Commercial Feasibility", "Are the client's expectations regarding our role clearly defined - specifically, do they understand we are acting as a filing agent and not providing formal tax or legal advice?"),
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


# ---------- Company documents & key people (directors/shareholders) ----------
# Lives on the Client itself, not on any one Engagement, so it's filled in
# once per client and reused by every engagement that client ever has -
# unlike Sanctions & Adverse Notice Screening below, which is per-engagement
# (an engagement's own record of who was screened and when for THAT
# engagement's acceptance decision). See company_documents.py and
# entity_extraction.py for the upload/AI-extraction routes and logic.

COMPANY_DOCUMENT_TYPES = [
    "Certificate of Incorporation",
    "Memorandum & Articles of Association",
    "CR14 - Return of Directors",
    "CR6 - Notice of Situation of Registered Office",
    "Share Register / Share Certificates",
    "Beneficial Ownership Declaration",
    "Director/Shareholder ID Documents",
    "Other",
]

PERSON_ROLES = ["Director", "Shareholder", "Beneficial Owner", "Company Secretary", "Other"]
PERSON_STATUSES = ["Suggested", "Confirmed"]


class CompanyDocument(db.Model):
    """One company registration document (certificate of incorporation,
    CR14, share register, etc.) uploaded once against a Client and reused
    across every one of that client's engagements. Text is extracted on
    upload the same way as RegulatoryNotice (see sanctions_data.
    extract_pdf_text); an image upload (a photographed/scanned page saved
    directly as a JPG/PNG rather than a PDF) skips straight to the AI
    extraction step below since there's no PDF text layer to try first.
    Directors/Shareholders/etc are then extracted from that document by
    entity_extraction.extract_people (see ClientKeyPerson) - ai_status
    records whether that step itself succeeded, separately from whether any
    people were actually found in a document that genuinely doesn't list
    any (e.g. a certificate of incorporation with no director names on it)."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    document_type = db.Column(db.String(60), nullable=False, default="Other")
    title = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(300))
    stored_filename = db.Column(db.String(300))
    extracted_text = db.Column(db.Text)
    extraction_status = db.Column(db.String(20))  # "extracted", "no_text_found", "error" - PDF uploads only
    page_count = db.Column(db.Integer)

    ai_status = db.Column(db.String(20))  # "done", "not_configured", "error"
    ai_error = db.Column(db.Text)
    ai_processed_at = db.Column(db.DateTime)

    notes = db.Column(db.Text)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("company_documents", lazy=True, cascade="all, delete-orphan", order_by="CompanyDocument.uploaded_at.desc()"))
    uploaded_by = db.relationship("User")

    @property
    def file_ext(self):
        return self.original_filename.rsplit(".", 1)[-1].lower() if self.original_filename and "." in self.original_filename else ""

    def __repr__(self):
        return f"<CompanyDocument {self.document_type} {self.title!r}>"


class ClientKeyPerson(db.Model):
    """One director/shareholder/beneficial owner/company secretary
    associated with a Client - either suggested automatically by AI
    extraction from an uploaded CompanyDocument (status "Suggested",
    source_document set) or added/edited by a person directly (status
    "Confirmed", source_document None, or a Suggested row a reviewer has
    since confirmed). An AI suggestion is never treated as fact on its own -
    the same conservative principle as automated sanctions matching
    (SanctionsScreening/sanctions_data.py): it's surfaced for a person to
    review, correct if needed, and confirm. Feeds Sanctions & Adverse Notice
    Screening (see acceptance.py's add-to-screening action) so the people
    found here don't have to be retyped into that per-engagement list."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="Other")
    # Number of shares, shareholding %, ID/passport number, nationality and
    # address each get their own field (previously all jumbled into one
    # free-text "details" box) so they can be read, sorted and reused
    # individually - e.g. nationality feeding a future PEP/adverse-media
    # check without having to re-parse a sentence. Number of shares and
    # percentage are kept separate rather than one combined field since a
    # document/register may state either or both, and they're not always
    # simple to derive from each other (e.g. the total shares in issue
    # isn't always stated). "details" is kept for anything else stated that
    # doesn't fit one of those fields (date of appointment, a second role
    # also held, etc).
    number_of_shares = db.Column(db.String(50))
    shareholding_percentage = db.Column(db.String(50))
    id_number = db.Column(db.String(100))
    nationality = db.Column(db.String(100))
    address = db.Column(db.String(300))
    details = db.Column(db.Text)  # other notes not covered by the fields above
    status = db.Column(db.String(20), nullable=False, default="Confirmed")  # "Suggested" or "Confirmed" - see PERSON_STATUSES
    # True only for a person who already existed before the four fields
    # above did, whose old free-text "details" note was AI-split into them -
    # a separate, narrower review flag from `status` above: it never flips
    # an already-Confirmed identity back to Suggested, it just marks that
    # the *split itself* (not the person's identity) hasn't been checked by
    # a human yet. See entity_extraction.split_legacy_details.
    needs_detail_review = db.Column(db.Boolean, nullable=False, default=False)

    source_document_id = db.Column(db.Integer, db.ForeignKey("company_document.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # null for an AI-suggested row until confirmed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    confirmed_at = db.Column(db.DateTime)

    client = db.relationship("Client", backref=db.backref("key_people", lazy=True, cascade="all, delete-orphan", order_by="ClientKeyPerson.full_name"))
    source_document = db.relationship("CompanyDocument", backref=db.backref("extracted_people", lazy=True))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    confirmed_by = db.relationship("User", foreign_keys=[confirmed_by_id])

    def __repr__(self):
        return f"<ClientKeyPerson {self.full_name!r} role={self.role!r} status={self.status!r}>"


# ---------- Understanding the Entity's Business - public information scan ----------
# A manual, on-demand check of what's publicly available (news, social media,
# etc.) about a client's business, directors/shareholders and products - see
# entity_research.py and company_documents.py's run_public_research. Lives on
# the Client, same reasoning as company documents/key people above: the
# firm's understanding of a client's business doesn't reset between
# engagements, so it's run and kept once per client rather than once per
# engagement. Never triggered automatically (each run is a billed AI web
# search) and never overwritten - each run adds a new dated row, the same
# keep-a-record philosophy as SanctionsScreening's auto_* notes.

PUBLIC_RESEARCH_SCOPES = [
    ("profile", "Business profile only"),
    ("risk", "Risk / adverse-media check only"),
    ("both", "Both"),
]


class EntityPublicResearch(db.Model):
    """One run of the public-information scan against a Client - purely
    informational, like every other AI-assisted feature in this app it never
    marks anything Confirmed or decides anything on its own; a person reads
    the findings (and the sources listed) and judges them."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    scope = db.Column(db.String(10), nullable=False, default="both")  # "profile" | "risk" | "both" - see PUBLIC_RESEARCH_SCOPES
    status = db.Column(db.String(20), nullable=False, default="done")  # "done", "not_configured", "error"
    business_profile = db.Column(db.Text)
    risk_findings = db.Column(db.Text)
    sources_json = db.Column(db.Text)  # JSON-encoded list of {"title", "url"}
    error_message = db.Column(db.Text)

    requested_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship(
        "Client",
        backref=db.backref(
            "public_research_runs", lazy=True, cascade="all, delete-orphan",
            order_by="EntityPublicResearch.requested_at.desc()",
        ),
    )
    requested_by = db.relationship("User")

    @property
    def sources(self):
        try:
            return json.loads(self.sources_json) if self.sources_json else []
        except (TypeError, ValueError):
            return []

    def set_sources(self, value):
        self.sources_json = json.dumps(value or [])

    @property
    def scope_label(self):
        return dict(PUBLIC_RESEARCH_SCOPES).get(self.scope, self.scope)

    def __repr__(self):
        return f"<EntityPublicResearch client={self.client_id} scope={self.scope!r} status={self.status!r}>"


# ---------- Sanctions & adverse notice screening ----------

# The five lists/registers every key individual is screened against, in
# display order - the three international sanctions lists first, then the
# two Zimbabwean regulators. Each is its own column/field on
# SanctionsScreening below (un_result, ofac_result, eu_result, rbz_result,
# fiu_result) rather than one combined result, since a hit on one list but
# not another is itself a meaningful finding worth recording separately.
SANCTIONS_SCREENING_SOURCES = [
    ("un_result", "UN Sanctions List"),
    ("ofac_result", "OFAC (US Treasury)"),
    ("eu_result", "EU Sanctions List"),
    ("rbz_result", "Reserve Bank of Zimbabwe (RBZ) adverse notices"),
    ("fiu_result", "Financial Intelligence Unit (FIU) adverse notices"),
]

SANCTIONS_SCREENING_RESULTS = ["Not Checked", "Clear", "Potential Match", "Confirmed Hit"]

# The three sources with a free, official, machine-readable feed the app can
# fetch and cache itself (see sanctions_data.py) - matched automatically
# against SanctionsWatchlistEntry rows cached from that feed. Maps each
# source's short code to its result-field/auto-notes-field prefix on
# SanctionsScreening and to its SanctionsListStatus row.
SANCTIONS_AUTO_SOURCES = ["UN", "OFAC", "EU"]

# RBZ and FIU Zimbabwe publish no such feed (RBZ posts occasional PDF
# "Public Notices"; FIU Zimbabwe has no searchable list at all) - so instead
# the firm uploads the PDFs it receives to the Regulatory Notices library
# (see RegulatoryNotice below) and an auto-screen searches their extracted
# text for a name match, in place of a live API call.
REGULATORY_NOTICE_SOURCES = ["RBZ", "FIU"]
REGULATORY_NOTICE_SOURCE_LABELS = {
    "RBZ": "Reserve Bank of Zimbabwe (RBZ)",
    "FIU": "Financial Intelligence Unit (FIU) Zimbabwe",
}


class SanctionsScreening(db.Model):
    """One row per key individual (director, beneficial owner, authorised
    signatory, or - on an Investigative Engagement - a suspect/target/key
    witness) screened against the UN, OFAC and EU sanctions lists and
    against adverse notices from the Reserve Bank of Zimbabwe (RBZ) and the
    Financial Intelligence Unit (FIU), as part of Client Acceptance's
    regulatory/AML checks (ordinary engagements) or Enhanced Due Diligence
    (Investigative Engagements) - see ClientAcceptance.screening_assessment
    for the aggregate, advisory-only read of this list. Each of the five
    lists gets its own result rather than one combined field, since a hit
    on one list but a clear result on the others is itself worth recording."""
    id = db.Column(db.Integer, primary_key=True)
    client_acceptance_id = db.Column(db.Integer, db.ForeignKey("client_acceptance.id"), nullable=False)
    individual_name = db.Column(db.String(200), nullable=False)
    role_description = db.Column(db.String(150))  # e.g. "Director", "Beneficial Owner (30%)", "Authorised Signatory", "Investigation target"

    un_result = db.Column(db.String(20), default="Not Checked")
    ofac_result = db.Column(db.String(20), default="Not Checked")
    eu_result = db.Column(db.String(20), default="Not Checked")
    rbz_result = db.Column(db.String(20), default="Not Checked")
    fiu_result = db.Column(db.String(20), default="Not Checked")

    notes = db.Column(db.Text)  # match details, reference/search numbers, follow-up/escalation actions
    order = db.Column(db.Integer, default=0)

    # Populated by the auto-screen action (acceptance.auto_screen_individual)
    # alongside the *_result columns above - a short, human-readable note on
    # what the automated match (or lack of one) against the cached UN/OFAC/EU
    # lists or the uploaded RBZ/FIU notice library actually found, kept
    # separate from the shared `notes` field above so a reviewer's own
    # manual notes are never silently overwritten by a later auto-screen.
    un_auto_notes = db.Column(db.Text)
    ofac_auto_notes = db.Column(db.Text)
    eu_auto_notes = db.Column(db.Text)
    rbz_auto_notes = db.Column(db.Text)
    fiu_auto_notes = db.Column(db.Text)
    auto_screened_at = db.Column(db.DateTime)
    auto_screened_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    screened_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    screened_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client_acceptance = db.relationship(
        "ClientAcceptance",
        backref=db.backref("screenings", lazy=True, cascade="all, delete-orphan", order_by="SanctionsScreening.order"),
    )
    screened_by = db.relationship("User", foreign_keys=[screened_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    auto_screened_by = db.relationship("User", foreign_keys=[auto_screened_by_id])

    def auto_notes_for(self, field):
        """auto_notes_for('un_result') -> self.un_auto_notes, etc - lets the
        template loop over SANCTIONS_SCREENING_SOURCES once and look up the
        matching auto-note without a second parallel list to keep in sync."""
        return getattr(self, field.replace("_result", "_auto_notes"), None)

    @property
    def is_screened(self):
        """Whether every one of the five lists has actually been checked
        (as opposed to left at its "Not Checked" default)."""
        return all(getattr(self, field) != "Not Checked" for field, _ in SANCTIONS_SCREENING_SOURCES)

    @property
    def overall_status(self):
        results = [getattr(self, field) for field, _ in SANCTIONS_SCREENING_SOURCES]
        if "Confirmed Hit" in results:
            return "Confirmed Hit"
        if "Potential Match" in results:
            return "Potential Match"
        if self.is_screened:
            return "Clear"
        return "Not Checked"

    def __repr__(self):
        return f"<SanctionsScreening {self.individual_name!r} status={self.overall_status!r}>"


class SanctionsWatchlistEntry(db.Model):
    """One cached name from the UN, OFAC or EU sanctions list (see
    SANCTIONS_AUTO_SOURCES and sanctions_data.py), refreshed periodically
    from each source's own free, official, machine-readable feed and stored
    here so screening a name is an instant local lookup rather than a live
    call to three different government/international-body servers on every
    keystroke. The whole table for a given source is replaced wholesale on
    each refresh (see sanctions_data.refresh_source) rather than diffed, since
    these feeds don't expose an incremental/delta API."""
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(10), nullable=False)  # "UN", "OFAC", "EU" - see SANCTIONS_AUTO_SOURCES
    name = db.Column(db.String(300), nullable=False)
    aliases = db.Column(db.Text)  # other known names/spellings, if the feed provides them, newline-separated
    reference = db.Column(db.String(150))  # the source's own reference/ID number for this listing, if provided
    programme = db.Column(db.String(300))  # sanctions programme/regime this listing falls under, if provided
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<SanctionsWatchlistEntry {self.source} {self.name!r}>"


class SanctionsListStatus(db.Model):
    """One row per SANCTIONS_AUTO_SOURCES entry, tracking when that source's
    cache (SanctionsWatchlistEntry rows) was last refreshed, how many entries
    it holds, and the last error (if the most recent refresh attempt failed) -
    shown on the Sanctions & Adverse Notice Screening card so staff can see
    how current the automated screening actually is, and surfaced per-source
    since one feed being temporarily unreachable shouldn't hide the status of
    the other two (see sanctions_data.refresh_all_sources)."""
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(10), nullable=False, unique=True)
    last_refreshed_at = db.Column(db.DateTime)
    entry_count = db.Column(db.Integer, default=0)
    last_error = db.Column(db.Text)
    last_refreshed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    last_refreshed_by = db.relationship("User")

    def __repr__(self):
        return f"<SanctionsListStatus {self.source} entries={self.entry_count}>"


class RegulatoryNotice(db.Model):
    """One RBZ or FIU Zimbabwe adverse-notice PDF the firm has uploaded to
    the firm-wide Regulatory Notices library (see regulatory_notices.py) -
    the practical substitute for the automated feed those two regulators
    don't publish (see REGULATORY_NOTICE_SOURCES). Text is extracted from
    the PDF on upload (see sanctions_data.extract_pdf_text) and stored here
    so an auto-screen can search it for a name match without re-reading the
    PDF from disk every time; extraction_status records whether that
    actually worked (a scanned, image-only PDF has no selectable text to
    extract, so it's left searchable only by its title/notes)."""
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(10), nullable=False)  # "RBZ" or "FIU" - see REGULATORY_NOTICE_SOURCES
    title = db.Column(db.String(300), nullable=False)
    notice_date = db.Column(db.Date)
    original_filename = db.Column(db.String(300))
    stored_filename = db.Column(db.String(300))
    extracted_text = db.Column(db.Text)
    extraction_status = db.Column(db.String(20))  # "extracted", "no_text_found", "error"
    page_count = db.Column(db.Integer)
    notes = db.Column(db.Text)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploaded_by = db.relationship("User")

    @property
    def file_ext(self):
        return self.original_filename.rsplit(".", 1)[-1] if self.original_filename and "." in self.original_filename else ""

    def __repr__(self):
        return f"<RegulatoryNotice {self.source} {self.title!r}>"


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

# Finalisation checklist for "Business Intelligence and IT Engagements" -
# closing out a cyber/IT/AML-CFT control assurance engagement is closer in
# shape to closing out an investigation than a financial-statement audit
# (findings and recommendations rather than a materiality-based opinion on
# numbers), so this replaces DEFAULT_FINALISATION_CHECKLIST_ITEMS entirely
# for this engagement type, same pattern as FORENSIC_FINALISATION_
# CHECKLIST_ITEMS above.
BUSINESS_IT_FINALISATION_CHECKLIST_ITEMS = [
    ("Findings & Recommendations", "Have control gaps/exceptions identified across Cyber Security, Information Security, IT (ICT), and AML/CFT Compliance been evaluated for significance and consolidated?"),
    ("Findings & Recommendations", "Have practical remediation recommendations been drafted for each significant finding, with input from the relevant specialists?"),
    ("Findings & Recommendations", "Has management been given the opportunity to respond to the findings before the report is finalised?"),
    ("Reporting & Communication", "Has the final report/opinion been drafted, reviewed and approved by the engagement partner?"),
    ("Reporting & Communication", "Has communication with those charged with governance been completed, covering significant findings and control deficiencies identified?"),
    ("Reporting & Communication", "Where a finding could give rise to a regulatory notification obligation (e.g. under the Cyber and Data Protection Act or AML/CFT legislation), has this been discussed with the client and, where appropriate, legal counsel?"),
    ("Evidence & Documentation", "Is all evidence gathered (system configurations, logs, screenshots, test results, interview notes) indexed and retained in line with the firm's retention policy?"),
    ("Evidence & Documentation", "Have any highly sensitive artefacts (e.g. vulnerability details, access credentials used during testing) been securely disposed of or returned once no longer required?"),
    ("File Completion", "Have all working papers been reviewed and cleared, with every review point resolved?"),
    ("File Completion", "Is the engagement file assembled and ready for the archiving deadline, with confidentiality/access restrictions reconfirmed?"),
]

# Finalisation checklist for "Secretarial" engagements - the COBE-compliant
# five-phase Secretarial Finalisation Stage (Registry & Reconciliations;
# Board & Governance Audit Trail; UBO & Capital Reconciliation; Final
# Deliverable Reporting Pack; Billing & Archiving), using the updated
# statutory form names under the Companies and Other Business Entities Act
# [Chapter 24:31] (COBE Act) - replaces DEFAULT_FINALISATION_CHECKLIST_ITEMS
# entirely for this engagement type, same pattern as FORENSIC_FINALISATION_
# CHECKLIST_ITEMS / BUSINESS_IT_FINALISATION_CHECKLIST_ITEMS above.
SECRETARIAL_FINALISATION_CHECKLIST_ITEMS = [
    ("Registry & Reconciliations", "Final registry search executed via Form CR2 (Search Application) to verify all lodgments processed during the period reflect on the public register? [SEC-FIN-01]"),
    ("Registry & Reconciliations", "Annual/Statutory Return reconciled and stamped - Form CR23 (Statutory/Annual Return) and Form CR17 (Declaration of AGM) filed under Sec 165/166? [SEC-FIN-03]"),
    ("Registry & Reconciliations", "Director/Secretary changes confirmed - all appointments, resignations, or address changes lodged on Form CR6 under Sec 217/241? [SEC-FIN-04]"),
    ("Registry & Reconciliations", "Registered office changes verified on Form CR5 (Notice of Registered Office) under Sec 240? [SEC-FIN-05]"),
    ("Registry & Reconciliations", "Share allotments verified on Form CR11 (Return of Allotments) under Sec 93 & 95?"),
    ("Board & Governance Audit Trail", "Board/AGM minutes signed and bound - all draft minutes from Board, Committee, and General Meetings reviewed, signed by the Board Chairperson, and entered into the master minute book (Sec 170 & 176)? [SEC-FIN-02]"),
    ("Board & Governance Audit Trail", "Special resolutions indexed and filed - Form CR8 returns bear CIPO stamp confirmations for all Special Resolutions passed during the year (Sec 176 & 178)? [SEC-FIN-07]"),
    ("Board & Governance Audit Trail", "Board action tracker closed out - a final summary of all action items compiled, completed tasks noted and unresolved matters rolled into the next period's planning cycle?"),
    ("UBO & Capital Reconciliation", "UBO register and declarations reconciled - the confidential Register of Beneficial Ownership (Sec 72) reconciled and Form CR16 (Beneficial Ownership Declaration) confirmed submitted for any natural person holding more than 20% voting rights, direct/indirect interest, or control? [SEC-FIN-06]"),
    ("UBO & Capital Reconciliation", "Register of Members and capital structure reconciled - share certificates issued or transferred (Sec 149 & 157) reconciled against authorised nominal share capital changes filed via Form CR10 (Increase of Nominal Capital) or Form CR9 (Conversion/Buy-Back of Shares)?"),
    ("UBO & Capital Reconciliation", "Register of Charges and Debentures reconciled - all charges, mortgages, or satisfactions created or discharged during the year (Sec 162-164) matched against filings on Form CR12 (Satisfaction/Register of Mortgages & Charges)?"),
    ("Final Deliverable Reporting Pack", "Annual Corporate Governance & Compliance Report issued (COBE statutory returns completed; governance meeting attendance matrix; solvency and liquidity compliance checks performed under Sec 102; high-risk governance or non-compliance gaps identified)? [SEC-FIN-08]"),
    ("Final Deliverable Reporting Pack", "Secretarial Management Letter issued, documenting administrative weaknesses and corrective action recommendations?"),
    ("Final Deliverable Reporting Pack", "Annual Governance Calendar draft provided to management for the upcoming financial year?"),
    ("Billing & Archiving", "Document archiving and custody handover completed - original documents returned under formal receipt and electronic files securely archived per the firm's record retention guidelines?"),
    ("Billing & Archiving", "WIP and billing closed out - time recordings finalised, disbursements (CIPO registry fees, stamp duties, statutory filing fees) passed, and the final fee note issued?"),
    ("Billing & Archiving", "Quality Assurance & Engagement Sign-Off - the Engagement Quality Control Review checklist completed, with final sign-off by the Engagement Partner/Lead Corporate Secretary? [SEC-FIN-09]"),
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


# The narrative workpapers that get a persistent, editable copy in-app
# (rather than being generated fresh, throwaway, on every download) - see
# WorkpaperNarrative below. Each is a (kind, label, workpaper section key)
# tuple; the section key looks up its lettered reference in
# WORKPAPER_SECTIONS above.
WORKPAPER_NARRATIVE_KINDS = [
    ("rep_letter", "Management Representation Letter", "rep_letter"),
    ("report_to_management", "Report to Management", "report_to_management"),
    ("forensic_executive_summary", "Forensic Investigation Report - Executive Summary", "forensic_report"),
]

# The Tax Opinion's 14 parts and the Tax Health Check Report's 15 parts
# (Tax module, Module 10 - Finalisation & Deliverables) are each just more
# WorkpaperNarrative kinds - every part gets its own persistent, editable
# body of text with its own independent Preparer/Reviewer/Partner sign-off,
# exactly like rep_letter/report_to_management/forensic_executive_summary
# above, with zero new model or route code needed (see
# engagements._get_or_seed_workpaper_narrative / save_workpaper_narrative /
# review_workpaper_narrative / etc., and workpapers.build_tax_opinion_docx /
# build_tax_health_check_docx for the combined Word exports). Both are only
# ever seeded/shown when Engagement.has_tax_module is True - see tax.py.
TAX_OPINION_PARTS = [
    ("tax_opinion_01_scope", "Engagement Scope & Instructions"),
    ("tax_opinion_02_exec_summary", "Executive Summary"),
    ("tax_opinion_03_background", "Background & Facts"),
    ("tax_opinion_04_legislation", "Applicable Legislation & Authorities"),
    ("tax_opinion_05_issues", "Issue(s) for Opinion"),
    ("tax_opinion_06_analysis", "Technical Analysis"),
    ("tax_opinion_07_alternatives", "Alternative Interpretations Considered"),
    ("tax_opinion_08_authority_position", "Tax Authority Position (if known)"),
    ("tax_opinion_09_risk", "Risk Assessment of the Position"),
    ("tax_opinion_10_conclusion", "Conclusion & Recommended Position"),
    ("tax_opinion_11_reporting", "Reporting & Disclosure Implications"),
    ("tax_opinion_12_implementation", "Implementation Steps"),
    ("tax_opinion_13_limitations", "Limitations & Reliance"),
    ("tax_opinion_14_signoff", "Sign-off & Distribution"),
]
TAX_HEALTH_CHECK_PARTS = [
    ("tax_health_check_01_exec_summary", "Executive Summary"),
    ("tax_health_check_02_scope", "Scope & Methodology"),
    ("tax_health_check_03_entity_status", "Entity & Registration Status Review"),
    ("tax_health_check_04_cit", "Corporate Income Tax (CIT) Review"),
    ("tax_health_check_05_vat", "Value Added Tax (VAT) Review"),
    ("tax_health_check_06_paye", "PAYE Review"),
    ("tax_health_check_07_wht", "Withholding Taxes Review"),
    ("tax_health_check_08_cgt", "Capital Gains Tax Review"),
    ("tax_health_check_09_customs", "Customs & Excise Review (if applicable)"),
    ("tax_health_check_10_transfer_pricing", "Transfer Pricing / Related Party Review"),
    ("tax_health_check_11_penalties", "Penalties, Interest & Historical Exposure"),
    ("tax_health_check_12_findings", "Key Findings & Risk Ratings"),
    ("tax_health_check_13_recommendations", "Recommendations & Remediation Plan"),
    ("tax_health_check_14_exposure_summary", "Quantified Exposure Summary"),
    ("tax_health_check_15_conclusion", "Conclusion & Next Steps"),
]
for _key, _label in TAX_OPINION_PARTS:
    WORKPAPER_NARRATIVE_KINDS.append((_key, f"Tax Opinion - {_label}", "tax_opinion"))
for _key, _label in TAX_HEALTH_CHECK_PARTS:
    WORKPAPER_NARRATIVE_KINDS.append((_key, f"Tax Health Check - {_label}", "tax_health_check"))

# The Management Accounts Report's 10 parts (Accounting module) are, same as
# the Tax Opinion/Health Check above, just more WorkpaperNarrative kinds -
# zero new model or route code needed. Only ever seeded/shown when
# Engagement.has_accounting_module is True - see accounting.py.
MANAGEMENT_ACCOUNTS_REPORT_PARTS = [
    ("mgmt_report_01_exec_summary", "Executive Summary"),
    ("mgmt_report_02_basis", "Basis of Preparation & Scope"),
    ("mgmt_report_03_performance", "Financial Performance Review"),
    ("mgmt_report_04_position", "Financial Position Review"),
    ("mgmt_report_05_budget_variance", "Budget vs Actual Analysis"),
    ("mgmt_report_06_cost_variance", "Cost & Variance Analysis"),
    ("mgmt_report_07_cash_flow", "Cash Flow & Working Capital Commentary"),
    ("mgmt_report_08_kpi", "KPI Dashboard Commentary"),
    ("mgmt_report_09_risks", "Risks, Issues & Recommendations"),
    ("mgmt_report_10_conclusion", "Conclusion & Next Steps"),
]
for _key, _label in MANAGEMENT_ACCOUNTS_REPORT_PARTS:
    WORKPAPER_NARRATIVE_KINDS.append((_key, f"Management Accounts Report - {_label}", "management_accounts_report"))

WORKPAPER_NARRATIVE_KIND_KEYS = {kind for kind, _, _ in WORKPAPER_NARRATIVE_KINDS}
WORKPAPER_NARRATIVE_KIND_LABELS = {kind: label for kind, label, _ in WORKPAPER_NARRATIVE_KINDS}
TAX_OPINION_KIND_KEYS = [k for k, _ in TAX_OPINION_PARTS]
TAX_HEALTH_CHECK_KIND_KEYS = [k for k, _ in TAX_HEALTH_CHECK_PARTS]
MANAGEMENT_ACCOUNTS_REPORT_KIND_KEYS = [k for k, _ in MANAGEMENT_ACCOUNTS_REPORT_PARTS]

# Starting wording for each narrative workpaper the first time it's opened on
# an engagement - editable from there on (see WorkpaperNarrative above). Kept
# as plain text with one representation/point per line, rendered as a
# bulleted list in the generated Word document.
DEFAULT_WORKPAPER_NARRATIVE_BODIES = {
    "rep_letter": "\n".join([
        "We have fulfilled our responsibilities for the preparation of the financial statements in accordance with the applicable financial reporting framework, and they are fairly presented.",
        "The significant assumptions used by us in making accounting estimates, including those measured at fair value, are reasonable.",
        "Related party relationships and transactions have been appropriately accounted for and disclosed.",
        "All events subsequent to the date of the financial statements and for which the applicable financial reporting framework requires adjustment or disclosure have been adjusted or disclosed.",
        "The effects of uncorrected misstatements are immaterial, both individually and in the aggregate, to the financial statements as a whole. A list of the uncorrected misstatements is attached (if any).",
        "We have disclosed to you the results of our assessment of the risk that the financial statements may be materially misstated as a result of fraud.",
        "We have disclosed to you all known instances of non-compliance or suspected non-compliance with laws and regulations whose effects should be considered when preparing financial statements.",
        "We have disclosed to you the identity of the entity's related parties and all the related party relationships and transactions of which we are aware.",
        "We have provided you with access to all information of which we are aware that is relevant to the preparation of the financial statements, and access to all records, documentation and other matters requested.",
        "There have been no irregularities involving management or employees who have a significant role in internal control that could have a material effect on the financial statements.",
    ]),
    "report_to_management": "\n".join([
        "During the course of our engagement, we identified the following matters relating to internal control and other operational matters which we bring to the attention of management.",
        "[Add each observation as its own line - e.g. \"Observation: ... / Implication: ... / Recommendation: ...\"]",
        "This report is provided for the sole use of management and is not a comprehensive statement of all weaknesses that may exist, since it is based on matters that came to our attention during the course of our normal engagement procedures rather than a review specifically designed to identify all such matters.",
    ]),
    "forensic_executive_summary": "\n".join([
        "[Summarise, in a few sentences, the mandate, the key findings, and the overall conclusion of this investigation - the detail behind each point is set out in the numbered sections that follow.]",
    ]),
}
# Short "[to be completed: ...]" placeholder wording for each of the Tax
# Opinion's 14 parts and the Tax Health Check Report's 15 parts (see
# TAX_OPINION_PARTS/TAX_HEALTH_CHECK_PARTS above) - deliberately just a
# prompt rather than boilerplate legal/technical wording, since (unlike the
# ISA 700 rep-letter/RTM wording above) there is no safe firm-wide default
# position to pre-fill for a specific client's tax facts and conclusion.
for _key, _label in TAX_OPINION_PARTS:
    DEFAULT_WORKPAPER_NARRATIVE_BODIES[_key] = f"[To be completed: {_label}.]"
for _key, _label in TAX_HEALTH_CHECK_PARTS:
    DEFAULT_WORKPAPER_NARRATIVE_BODIES[_key] = f"[To be completed: {_label}.]"
# Same "[to be completed: ...]" placeholder approach for the Management
# Accounts Report's 10 parts - no safe firm-wide default position to
# pre-fill for a specific client's figures/commentary either.
for _key, _label in MANAGEMENT_ACCOUNTS_REPORT_PARTS:
    DEFAULT_WORKPAPER_NARRATIVE_BODIES[_key] = f"[To be completed: {_label}.]"


class WorkpaperNarrative(db.Model):
    """A persistent, editable body of free text for one of the "narrative"
    workpapers (see WORKPAPER_NARRATIVE_KINDS) - generated once with sensible
    default wording, then edited and updated in-app like any other
    workpaper, rather than being recomputed from scratch every time it's
    downloaded. One row per (engagement, kind); carries its own Preparer/
    Reviewer/Partner sign-off, same pattern as every other workpaper.
    Editing the body after review resets reviewed_by/partner_signed_by, same
    as elsewhere - a changed write-up needs a fresh review."""
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False)
    kind = db.Column(db.String(50), nullable=False)
    body = db.Column(db.Text)

    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    partner_signed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_signed_at = db.Column(db.DateTime)

    engagement = db.relationship("Engagement", backref=db.backref("workpaper_narratives", lazy=True, cascade="all, delete-orphan"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    __table_args__ = (db.UniqueConstraint("engagement_id", "kind", name="uq_workpaper_narrative_engagement_kind"),)

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None

    @property
    def label(self):
        return WORKPAPER_NARRATIVE_KIND_LABELS.get(self.kind, self.kind)

    def __repr__(self):
        return f"<WorkpaperNarrative engagement={self.engagement_id} kind={self.kind!r}>"


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


# ---------------------------------------------------------------------------
# Payroll Management
#
# Covers BOTH sides the firm asked for: Neverlank's own staff payroll, and a
# payroll SERVICE offered to clients (a client's own employees, processed by
# this firm). One shared set of models/engine drives both - a PayrollEmployee
# or PayrollPeriod's `scope` ("internal" vs "client") plus an optional
# client_id is the only thing that distinguishes them; the tax calculation,
# payslip layout and download formats are identical either way.
#
# Zimbabwean PAYE bands, the AIDS levy % and the NSSA employee/employer
# rate+ceiling could NOT be reliably confirmed from public sources at the
# time this was built - independent tax-calculator sites returned mutually
# inconsistent figures for the current bands/credits/NSSA ceiling. Rather
# than hard-code a number that might be wrong, EVERY rate here lives in the
# editable PayrollTaxSettings/PayrollTaxBand records below (per the firm's
# own choice: "auto-calculated but editable") and starts blank/zero except
# for the one figure that WAS consistently corroborated across sources
# (AIDS levy = 3% of PAYE payable). The Payroll > Tax Settings screen and
# every payslip carry a clear caveat to verify current rates against ZIMRA
# and NSSA directly before relying on the auto-calculation.
PAYROLL_SCOPES = ["internal", "client"]
PAYROLL_PAY_FREQUENCIES = ["Monthly", "Fortnightly", "Weekly"]
PAYROLL_PERIOD_STATUSES = ["Draft", "Finalized"]
PAYSLIP_ITEM_CATEGORIES = ["Allowance", "Deduction"]
PAYROLL_TAX_CAVEAT = (
    "Zimbabwean PAYE bands, the AIDS levy % and NSSA rates/ceiling change from "
    "time to time and could not be reliably verified from public sources when "
    "this module was built. Please confirm the figures below against the "
    "current ZIMRA tax tables and NSSA notice before relying on any "
    "auto-calculated payslip."
)


class PayrollTaxSettings(db.Model):
    """A single editable settings record (id=1, created on first use) holding
    every rate the payroll tax engine needs, other than the PAYE bands
    themselves (see PayrollTaxBand). Deliberately NOT seeded with specific
    NSSA figures - see the caveat above."""
    id = db.Column(db.Integer, primary_key=True)
    currency = db.Column(db.String(10), default="USD", nullable=False)
    # Of PAYE payable - the one figure consistently corroborated across the
    # sources checked, so this is the only rate given a non-zero default.
    aids_levy_pct = db.Column(db.Float, default=3.0)
    nssa_employee_pct = db.Column(db.Float, default=0.0)
    nssa_employer_pct = db.Column(db.Float, default=0.0)
    nssa_insurable_ceiling = db.Column(db.Float)  # None = no ceiling applied
    source_notes = db.Column(db.Text)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    updated_by = db.relationship("User")

    def __repr__(self):
        return f"<PayrollTaxSettings {self.currency}>"


class PayrollTaxBand(db.Model):
    """One PAYE bracket, firm-editable: taxable income from `lower` up to
    (not including) `upper` is taxed at `rate_pct`. `upper` left blank means
    this is the open-ended top band. Progressive calculation - see
    payroll_calc.calculate_paye - so bands should be entered as the
    marginal-rate table exactly as ZIMRA publishes it, not as cumulative
    amounts."""
    id = db.Column(db.Integer, primary_key=True)
    lower = db.Column(db.Float, nullable=False, default=0.0)
    upper = db.Column(db.Float)
    rate_pct = db.Column(db.Float, nullable=False, default=0.0)
    order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<PayrollTaxBand {self.lower}-{self.upper} @ {self.rate_pct}%>"


class PayrollEmployee(db.Model):
    """A person on a payroll - either one of Neverlank's own staff (scope
    'internal', optionally linked to their User login) or an employee of a
    client the firm processes payroll for (scope 'client', tied to
    client_id). full_name/job_title/etc. are always stored directly here
    (rather than only read off the linked User) so a payslip stays a stable
    historical record even if the underlying User record is later edited."""
    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(20), nullable=False, default="internal")
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    full_name = db.Column(db.String(200), nullable=False)
    employee_number = db.Column(db.String(50))
    national_id = db.Column(db.String(50))
    job_title = db.Column(db.String(150))
    nssa_number = db.Column(db.String(50))
    bank_name = db.Column(db.String(120))
    bank_account_number = db.Column(db.String(50))
    pay_frequency = db.Column(db.String(20), default="Monthly")
    basic_salary = db.Column(db.Float, default=0.0)
    date_joined = db.Column(db.Date)
    date_left = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("payroll_employees", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User", foreign_keys=[user_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<PayrollEmployee {self.full_name!r} ({self.scope})>"


class PayrollPeriod(db.Model):
    """One payroll run - e.g. "September 2026" - scoped the same way as
    PayrollEmployee. currency is snapshotted from PayrollTaxSettings at
    creation time so a period's figures stay meaningful even if the firm's
    default currency setting changes later."""
    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(20), nullable=False, default="internal")
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    name = db.Column(db.String(120), nullable=False)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    pay_date = db.Column(db.Date)
    currency = db.Column(db.String(10), default="USD")
    status = db.Column(db.String(20), default="Draft", nullable=False)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("payroll_periods", lazy=True, cascade="all, delete-orphan"))
    created_by = db.relationship("User")
    payslips = db.relationship("Payslip", backref="period", lazy=True, cascade="all, delete-orphan", order_by="Payslip.id")

    @property
    def total_net_pay(self):
        return sum(p.net_pay or 0.0 for p in self.payslips)

    @property
    def total_gross_pay(self):
        return sum(p.gross_pay or 0.0 for p in self.payslips)

    def __repr__(self):
        return f"<PayrollPeriod {self.name!r} ({self.scope})>"


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
    taxable_income = db.Column(db.Float, default=0.0)
    gross_pay = db.Column(db.Float, default=0.0)

    paye_tax = db.Column(db.Float, default=0.0)
    aids_levy = db.Column(db.Float, default=0.0)
    nssa_employee = db.Column(db.Float, default=0.0)
    nssa_employer = db.Column(db.Float, default=0.0)  # employer cost, informational - not deducted from the employee
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
    """An ad-hoc allowance or deduction line on one payslip (e.g. a housing
    allowance, an advance recovery, a union subscription) beyond basic
    salary and the statutory PAYE/AIDS levy/NSSA lines. `taxable` only
    matters for an Allowance - whether it is added to taxable income before
    PAYE is computed, or paid tax-free."""
    id = db.Column(db.Integer, primary_key=True)
    payslip_id = db.Column(db.Integer, db.ForeignKey("payslip.id"), nullable=False)
    category = db.Column(db.String(20), nullable=False, default="Allowance")
    label = db.Column(db.String(150), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    taxable = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<PayslipItem {self.label!r} {self.category} {self.amount}>"
