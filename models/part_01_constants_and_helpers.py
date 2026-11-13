import json
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from flask import has_request_context, request
from flask_login import UserMixin

from extensions import db


# "Corporate Finance & Business Advisory" replaced the old "Consulting" service line. Old rows are
# renamed on startup (app._rename_consulting_service) and the old name is still recognised by
# canonical_engagement_type so a stale bookmark or form cannot recreate it. The Document Templates for
# this service stay in their original on-disk folder - see template_folder().
CORPFIN_ENGAGEMENT_TYPE = "Corporate Finance & Business Advisory"
LEGACY_CONSULTING_TYPE = "Consulting"
CORPFIN_TASK_CATEGORY = "Corporate Finance & Advisory"
IT_TASK_CATEGORY = "IT & Business Intelligence"

ENGAGEMENT_TYPES = [
    "Audit", "Assurance", CORPFIN_ENGAGEMENT_TYPE, "Secretarial", "Investigative Engagement",
    "Business Intelligence and IT Engagements", "Tax Advisory",
    "Accounting & Bookkeeping",
    "Neverlank Anonymous Whistle-blower Services", "Neverlank Training Services",
]

# "Tax Advisory" is the single umbrella engagement type for ALL of the firm's
# tax work - compliance (returns & filings), advisory (planning & structuring),
# health checks and representation. It used to be two separate types ("Tax
# Compliance" and "Tax Advisory & Health Check"); those two names are kept
# here only so old rows can be recognised and moved across on startup (see
# app._merge_tax_engagement_types) and so nothing breaks if one is met before
# that has run. Which tax services are actually being delivered on a given
# engagement is chosen with the "Tax services" ticks (TAX_SERVICES).
TAX_ADVISORY_TYPE = "Tax Advisory"
LEGACY_TAX_ENGAGEMENT_TYPES = ("Tax Compliance", "Tax Advisory & Health Check")

# The two stand-alone service lines (not audit-style engagements): each gets
# its own trimmed workspace instead of the audit tab set - see
# Engagement.is_whistleblower_service/is_training_service, whistleblower.py
# and training.py.
WHISTLEBLOWER_SERVICE_TYPE = "Neverlank Anonymous Whistle-blower Services"
TRAINING_SERVICE_TYPE = "Neverlank Training Services"

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
    ("re_registration", "Company Re-registration (COBE Act)"),
]
SECRETARIAL_ACTIVITY_LABELS = dict(SECRETARIAL_ACTIVITIES)

# Selectable "Services" for the Tax Advisory module (compliance, advisory, health check, representation)
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

# The three sections of a client's Tax & Accounting Filing Archive (see
# TaxAccountingFilingDocument below) - kept as a plain, ordered list like
# every other section registry in this file (TAX_SERVICES, WORKPAPER_SECTIONS,
# etc.) rather than hardcoded into the template, so a section can be
# relabelled here in one place.
FILING_ARCHIVE_SECTIONS = [
    ("tax_clearance", "Tax Clearances"),
    ("return", "Returns"),
    ("schedule", "Schedules"),
]
FILING_ARCHIVE_SECTION_LABELS = dict(FILING_ARCHIVE_SECTIONS)

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

# Task categories (EngagementTask and PersonalTask) - one per firm service
# line, plus Admin Work, Other and Personal. Tax Services tasks are ALWAYS High priority
# (penalties and interest accrue on late ZIMRA filings/payments), enforced
# server-side by effective_task_priority() so no form, status change or
# URL edit can leave one at Low/Normal.
TASK_CATEGORIES = [
    "Audit", "Forensic Audit", "Tax Services", CORPFIN_TASK_CATEGORY, IT_TASK_CATEGORY, "Accounting",
    "Secretarial", "Neverlank Anonymous Tipoff Services", "Training",
    "Admin Work", "Other", "Personal",
]
TAX_TASK_CATEGORY = "Tax Services"

# Engagement type -> the task category its tasks default to.
ENGAGEMENT_TYPE_TASK_CATEGORY = {
    "Audit": "Audit",
    "Assurance": "Audit",
    "Investigative Engagement": "Forensic Audit",
    "Tax Advisory": "Tax Services",
    "Tax Compliance": "Tax Services",  # legacy name, see LEGACY_TAX_ENGAGEMENT_TYPES
    "Tax Advisory & Health Check": "Tax Services",  # legacy name
    CORPFIN_ENGAGEMENT_TYPE: CORPFIN_TASK_CATEGORY,
    LEGACY_CONSULTING_TYPE: CORPFIN_TASK_CATEGORY,  # legacy name
    "Business Intelligence and IT Engagements": IT_TASK_CATEGORY,
    "Accounting & Bookkeeping": "Accounting",
    "Secretarial": "Secretarial",
    "Neverlank Anonymous Whistle-blower Services": "Neverlank Anonymous Tipoff Services",
    "Neverlank Training Services": "Training",
}


def canonical_engagement_type(value):
    """The current name for an engagement type: the two retired tax names
    ("Tax Compliance", "Tax Advisory & Health Check") become "Tax Advisory";
    anything else is returned unchanged. Used wherever a type arrives from a
    form so an old bookmark or stale page can't recreate a retired type."""
    if value in LEGACY_TAX_ENGAGEMENT_TYPES:
        return TAX_ADVISORY_TYPE
    if value == LEGACY_CONSULTING_TYPE:
        return CORPFIN_ENGAGEMENT_TYPE
    return value


def template_folder(eng_type):
    """Folder name (under the Document Templates folders) that holds this type's files. Corporate Finance &
    Business Advisory keeps the folder the Consulting templates were always stored in, so nothing on disk
    has to move."""
    return LEGACY_CONSULTING_TYPE if eng_type in (CORPFIN_ENGAGEMENT_TYPE, LEGACY_CONSULTING_TYPE) else eng_type


def resolve_task_category(requested, engagement=None, fallback="Other"):
    """The category to store: the one asked for if it's valid, otherwise the
    one implied by the task's engagement type, otherwise `fallback`."""
    if requested in TASK_CATEGORIES:
        return requested
    if engagement is not None:
        mapped = ENGAGEMENT_TYPE_TASK_CATEGORY.get(getattr(engagement, "type", None))
        if mapped:
            return mapped
    return fallback if fallback in TASK_CATEGORIES else "Other"


def effective_task_priority(category, priority):
    """Tax Services tasks are always High; anything else keeps a valid
    requested priority (default Normal)."""
    if category == TAX_TASK_CATEGORY:
        return "High"
    return priority if priority in ("Low", "Normal", "High") else "Normal"
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

# The two line-item workpaper types WorkpaperReview (below, near
# EngagementQuery) can be attached to, so far - the user's own requested
# starting point ("start with the actual line-item workpapers - Substantive
# Procedures / checklist items - and expand later"). Adding a further
# workpaper type later is just one more entry here (plus its own `reviews`
# property and a redirect case in engagements._reviewable_engagement/
# _reviewable_redirect) - no new table or migration needed, since
# WorkpaperReview is generic.
REVIEWABLE_TYPE_SUBSTANTIVE_ITEM = "substantive_item"
REVIEWABLE_TYPE_CHECKLIST_ITEM = "checklist_item"
REVIEWABLE_TYPES = (REVIEWABLE_TYPE_SUBSTANTIVE_ITEM, REVIEWABLE_TYPE_CHECKLIST_ITEM)

# The engagement workpaper sections a Partner/Reviewer can raise a review
# Query against (see EngagementQuery below) - every tab on the engagement
# detail page, including Overview and Client Acceptance & Continuance, so a
# reviewer can flag something anywhere in the file, from acceptance right
# through to finalisation. For "substantive", a query is further scoped to
# one audit area (EngagementQuery.area_name) since Substantive Procedures
# has one sign-off per area rather than one for the whole tab.
QUERY_SECTIONS = [
    ("overview", "Overview"),
    ("acceptance", "Client Acceptance & Continuance"),
    ("entity", "Understanding the Entity"),
    ("risks", "Risk Assessment"),
    ("planning", "Planning (Materiality)"),
    ("analytical", "Analytical Review"),
    ("checklist", "Checklist"),
    ("substantive", "Substantive Procedures"),
    ("documents", "Documents"),
    ("tasks", "Tasks"),
    ("income_tax", "Income Tax Computation"),
    ("deferred_tax", "Deferred Tax Computation"),
    ("finalisation", "Finalisation (Trial Balance / Financial Statements)"),
    ("tax", "Tax Advisory"),
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
    ("P", "it_audit_report", "IT & Cyber Assurance Report", None),
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
     "Upload or delete a client's company documents (incorporation certificate, CR6, share register, etc.), confirm/edit/delete the Directors & Shareholders picked up from them, and run the public-information scan on a client's business.",
     tuple(USER_ROLES)),
    ("manage_qpd", "Manage QPD / Provisional Tax Estimates",
     "Create or edit a client's Quarterly Payment Date (provisional income tax) estimate - import/enter a Trial Balance or monthly VAT turnover, adjust the estimate, and record what's been paid.",
     tuple(USER_ROLES)),
    ("manage_vat_schedules", "Upload & Map VAT Schedules",
     "On an engagement's Tax tab: upload an Output VAT or Input VAT schedule (sales, exports, credit notes, purchases, Bills of Entry) from Excel/CSV, map its columns, confirm the import, and delete an imported batch. Limited to supervisors and above by default (and still only on engagements the person is staffed on, unless Admin); typing in a single VAT document by hand is unaffected.",
     ("supervisor", "partner", "admin")),
    ("reopen_vat_periods", "Reopen a Completed VAT Month",
     "On an engagement's Tax tab: reopen a VAT month that has been marked 'Computation done' so documents dated in it can be added, deleted or imported again. Anyone on the engagement can mark a month as done; only supervisors and above can reopen it by default (a reason is recorded).",
     ("supervisor", "partner", "admin")),
    ("send_invoices", "Send Invoices & Reminders to Clients",
     "Email an invoice (with the PDF attached) or open a WhatsApp message with its link straight from the invoice page, and send overdue-payment reminders. Open to everyone who can see the invoice by default; remove it from a role to stop staff contacting clients themselves.",
     tuple(USER_ROLES)),
    ("issue_credit_notes", "Issue & Void Credit Notes",
     "Finance > Credit notes: issue a credit note against a client invoice (in full or in part) to correct or reduce what the client owes, and void one issued in error. Partners and Admin only by default.",
     ("partner", "admin")),
    ("view_attendance", "View Attendance (Check In / Check Out)",
     "HR & Admin > Attendance: see who is in right now, when everyone checked in and out each day, hours worked, people who have not checked in or forgot to check out, and export the log. Management only by default.",
     ("partner", "admin")),
    ("manage_corporate_finance", "Use Corporate Finance & Business Advisory models",
     "Corporate Finance workspace: build and edit financial forecasts (income statement, balance sheet, cash flow), loans and covenants, valuations, capital-budgeting projects and budget-vs-actual reviews for the clients the person is working on. Making a model Final, and re-opening a Final model, is always limited to Partners and Admin.",
     ("supervisor", "partner", "admin")),
    ("manage_whistleblower", "Manage Whistle-blower Service cases",
     "See and work the confidential reports received through Neverlank's Anonymous Whistle-blower Services - log cases, investigate, update status, reply to reporters and produce client reports. Reports can be highly sensitive, so this is limited to supervisors and above by default (and still only on engagements the person is staffed on, unless Admin).",
     ("supervisor", "partner", "admin")),
    ("manage_training", "Manage Training Services",
     "Set up training sessions, keep attendee registers, upload materials, record feedback and issue certificates under Neverlank Training Services.",
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
    ("manage_legislative_updates", "Manage Legislative Update Control",
     "Upload or delete Acts, Government Notices and Statutory Instruments in the Legislative Update Control "
     "library, used to drive AI summarisation and client-relevance tagging.",
     ("partner", "admin")),
    ("manage_chart_of_accounts", "Manage the Standard Chart of Accounts",
     "Add, edit, deactivate or delete entries in the firm's Standard Chart of Accounts (Neverlank Standard) - the "
     "numbered account list used to speed up mapping a client's trial balance accounts to an IAS 1 category.",
     ("partner", "admin")),
    ("view_firm_finances", "View Firm Finances & Analytics",
     "Open the Revenue & Expenses Analytics page (debtors days, ageing, revenue, expenses, profit) and the firm's "
     "Expense register. These show the firm's overall income and costs, so this defaults to Partner/Admin only.",
     ("partner", "admin")),
    ("research_firm_library", "Research the Firm Library",
     "Use \"Ask the Firm Library\" to query the Policies & Procedures library with AI and see the AI-generated "
     "summary/key points on each document. Viewing and downloading policies is unaffected by this setting and "
     "stays open to everyone - this only gates the AI research features, rolled out firm-wide after the pilot.",
     tuple(USER_ROLES)),
]
PERMISSION_KEYS = {p[0] for p in PERMISSIONS}

# The two engagement types that involve PAYE work (see TAX_HEADS below,
# which includes "PAYE" as one of the areas every Tax engagement's
# Execution Plan covers) - used by the "manage_payroll" carve-out in
# user_has_permission below: staff actually staffed on a Tax engagement
# need the Payroll module's PAYE tax bands/settings for that work, so they
# get it automatically even where their role wouldn't otherwise grant it.
TAX_ENGAGEMENT_TYPES = (TAX_ADVISORY_TYPE,) + LEGACY_TAX_ENGAGEMENT_TYPES


def user_has_permission(user, key):
    """The one place this app should ever ask "is this role allowed to do
    X?" for the administrative actions in PERMISSIONS above - routes call
    this instead of hardcoding a role check. Admin is always allowed,
    hardcoded here rather than stored, so a mistaken/mischievous toggle on
    the settings screen can never lock every admin out of fixing it.

    Below that, three layers are checked in order:
    1. A per-user override (UserPermissionOverride, "Access Rights" on a
       team member's own profile) - an explicit, named decision about this
       one person, so it wins over everything else.
    2. For "manage_payroll" specifically, whether the user is staffed on an
       active Tax Advisory engagement (see
       TAX_ENGAGEMENT_TYPES above) - PAYE review work on a Tax engagement
       needs the Payroll module's PAYE tax settings, granted automatically
       rather than requiring every such person to get an explicit override.
    3. The ordinary role-level Permission toggle (Team > Permissions),
       falling back to the registry's own default_roles if no row exists
       yet for this (role, key) - e.g. a permission added by an app update
       before the next seed/migration runs."""
    if user.role == "admin":
        return True
    override = UserPermissionOverride.query.filter_by(user_id=user.id, permission_key=key).first()
    if override is not None:
        return override.allowed
    if key == "manage_payroll" and _user_has_active_tax_engagement(user):
        return True
    perm = Permission.query.filter_by(role=user.role, permission_key=key).first()
    if perm is not None:
        return perm.allowed
    for reg_key, _, _, default_roles in PERMISSIONS:
        if reg_key == key:
            return user.role in default_roles
    return False


def _user_has_active_tax_engagement(user):
    """True if `user` is a team member (Engagement.team_members) on at
    least one Tax Advisory engagement that
    isn't yet Completed - see the "manage_payroll" carve-out above."""
    return any(
        e.type in TAX_ENGAGEMENT_TYPES and e.status != "Completed"
        for e in user.engagements
    )


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
    ("q_filing_compliance_history", "What is this entity's track record of lodging Annual Returns (Sec 165: within 21 days of the registration anniversary) and notifying director/officer changes (Sec 217: within one month) within the statutory windows?", [
        "Consistently on time, no history of late or missed filings",
        "Mostly on time, isolated minor delays",
        "Some history of late filings",
        "Frequent late filings, or at least one lapse into defunct/struck-off status previously remedied",
        "Currently overdue on Annual Returns, or at imminent risk of being struck off (Sec 52)",
    ]),
    ("q_governance_procedural_rigor", "How rigorous are the entity's board/general meeting procedures (quorum under Sec 170 for members and Sec 204 for the board, notice periods under Secs 169 and 175, board decisions by written consent or electronic meeting only as Sec 196 allows)?", [
        "Well-documented, consistently compliant meeting procedures",
        "Generally sound, only minor procedural gaps",
        "Noticeable procedural weaknesses (e.g. occasional quorum or notice issues)",
        "Significant procedural weaknesses across multiple meetings",
        "Minute books incomplete/unsigned, or meetings routinely held without proper quorum or notice",
    ]),
    ("q_ownership_capital_complexity", "How complex or frequent are this entity's ownership and capital events (share allotments, transfers, beneficial ownership changes under Sec 72, distributions under Sec 102/138 or buy-backs under Sec 128-129)?", [
        "Simple, stable ownership structure, no capital events pending",
        "Occasional, straightforward capital events",
        "Moderate complexity - several shareholders/classes, or an event in progress",
        "Significant complexity - multiple classes, nominee arrangements, or frequent capital events",
        "Highly complex ownership/capital structure with unresolved beneficial ownership questions",
    ]),
    ("q_transactional_activity", "How much transactional or change-of-control activity (charge/mortgage creation under Sec 154-155, constitutional amendments, restructurings under Sec 227-232) is anticipated or under way?", [
        "None anticipated",
        "Limited, routine activity only",
        "Some non-routine activity anticipated (e.g. a single charge registration)",
        "Significant activity anticipated (e.g. a restructuring or multiple charges)",
        "Major, time-critical transactional activity already under way",
    ]),
    ("q_registry_standing", "What is this entity's current standing with the Registrar of Companies (up to date vs at risk of deregistration/striking-off under Sec 52)?", [
        "Fully up to date and in good standing",
        "Up to date, one historical lapse since remedied",
        "One or more filings currently outstanding but within a curable window",
        "Multiple filings outstanding, approaching the striking-off threshold",
        "Already subject to (or at imminent risk of) Registrar deregistration proceedings",
    ]),
]

SECRETARIAL_RISK_IMPACT_QUESTIONS = [
    ("q_penalty_exposure", "If a filing or deadline is missed, what is the likely exposure to civil penalty orders (Sec 294), late-filing penalties, or Registrar default notices?", [
        "Minimal - unlikely to attract any penalty",
        "Limited - a small, one-off penalty at most",
        "Moderate - a meaningful penalty or formal Registrar notice",
        "Significant - substantial penalties across multiple lapses",
        "Severe - deregistration/striking-off exposure (Sec 52)",
    ]),
    ("q_governance_invalidity", "If a board or shareholder resolution were later challenged for a quorum, notice, or proxy defect (Secs 169-171, 175, 196), how significant would the consequences be?", [
        "Minimal - no material decisions at risk",
        "Limited - only routine matters potentially affected",
        "Moderate - some commercially significant decisions could be challenged",
        "Significant - key resolutions (allotments, borrowings) could be voided",
        "Severe - ultra vires exposure across major corporate actions, litigation risk",
    ]),
    ("q_solvency_director_liability", "If a distribution, share buyback, or director loan were found not to have followed the statutory Solvency and Liquidity Test (Secs 102 and 138), the restriction on loans to directors (Sec 208) or the disclosure requirements (Sec 216), what is the exposure?", [
        "Minimal - no distributions/buybacks/director loans in the period",
        "Limited - small, well-documented transactions only",
        "Moderate - some transactions without full contemporaneous documentation",
        "Significant - material distributions/buybacks with weak solvency evidence",
        "Severe - unlawful distribution exposure and personal director liability",
    ]),
    ("q_encumbrance_control_risk", "If a mortgage, security interest or debenture were left out of the register beyond the 14-day period (Sec 155) or not registered as Sec 154 requires, or a change-of-control step were taken without required consents, what is the exposure?", [
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
