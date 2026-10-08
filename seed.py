"""Seed the database with an admin user and starter checklist templates.

Run with:  flask --app app seed
Safe to re-run any time, including on a database that already has clients,
engagements and users in it: it only ever adds a template if one with that
exact name doesn't already exist, and never touches anything else.
"""
import os
import shutil
from datetime import date

from extensions import db
from models import (
    User, ChecklistTemplate, ChecklistTemplateItem, DocumentTemplate, Permission, PERMISSIONS, USER_ROLES,
    FilingIndexSection, StatutoryDeadline, PayrollTaxBand, PayrollTaxSettings, PAYROLL_PAY_FREQUENCIES,
    LeaveType, LEAVE_TYPES_STATUTORY_ZW, CompanyDocument, SubstantiveProcedureItem, FinalisationChecklist,
    FinalisationChecklistItem, QPDInstalmentRate, StandardChartOfAccounts,
)
from config import Config


AUDIT_PROGRAM = [
    ("Planning", "Obtain understanding of the client's business and industry"),
    ("Planning", "Perform preliminary analytical review"),
    ("Planning", "Assess materiality and set performance materiality"),
    ("Planning", "Document risk assessment and audit strategy"),
    ("Cash & Bank", "Obtain bank confirmations for all accounts"),
    ("Cash & Bank", "Perform bank reconciliations review"),
    ("Revenue", "Test revenue recognition for sample of transactions"),
    ("Revenue", "Perform cut-off testing around year end"),
    ("Debtors", "Circularise trade receivables"),
    ("Debtors", "Review ageing and assess provision for doubtful debts"),
    ("Fixed Assets", "Verify additions and disposals for the period"),
    ("Fixed Assets", "Recalculate depreciation charge"),
    ("Payroll", "Test payroll calculations for sample of employees"),
    ("Completion", "Review subsequent events"),
    ("Completion", "Obtain management representation letter"),
    ("Completion", "Finalise and review financial statement disclosures"),
]

ASSURANCE_PROGRAM = [
    ("Engagement Setup", "Agree scope and criteria with the engagement party"),
    ("Engagement Setup", "Confirm independence and competence to accept engagement"),
    ("Evidence Gathering", "Identify and obtain sufficient appropriate evidence"),
    ("Evidence Gathering", "Perform inquiries of relevant personnel"),
    ("Reporting", "Draft assurance report and conclusion"),
    ("Reporting", "Partner review and sign-off"),
]

FULL_AUDIT_PROGRAM = [
    ("Engagement Acceptance & Independence", "Perform client acceptance/continuance procedures"),
    ("Engagement Acceptance & Independence", "Confirm independence and ethical requirements (IESBA code)"),
    ("Engagement Acceptance & Independence", "Agree and document engagement letter terms"),

    ("Planning & Risk Assessment", "Obtain understanding of the entity and its environment"),
    ("Planning & Risk Assessment", "Understand the entity's internal control environment"),
    ("Planning & Risk Assessment", "Perform risk assessment procedures (inquiry, analytical review, observation)"),
    ("Planning & Risk Assessment", "Identify and assess risks of material misstatement, including fraud risk"),
    ("Planning & Risk Assessment", "Determine materiality and performance materiality"),
    ("Planning & Risk Assessment", "Develop overall audit strategy and detailed audit plan"),
    ("Planning & Risk Assessment", "Assess going concern risk at the planning stage"),

    ("Internal Controls & Systems", "Document key business processes and controls (revenue, purchases, payroll, treasury)"),
    ("Internal Controls & Systems", "Test operating effectiveness of key controls, where reliance is placed on them"),
    ("Internal Controls & Systems", "Assess IT general controls where relevant"),

    ("Fraud & Compliance", "Discuss fraud risk with the engagement team"),
    ("Fraud & Compliance", "Make inquiries of management regarding fraud"),
    ("Fraud & Compliance", "Consider compliance with relevant laws and regulations"),

    ("Revenue & Receivables", "Perform substantive analytical procedures on revenue"),
    ("Revenue & Receivables", "Test revenue recognition for a sample of transactions"),
    ("Revenue & Receivables", "Perform cut-off testing around year end"),
    ("Revenue & Receivables", "Circularise trade receivables"),
    ("Revenue & Receivables", "Review ageing and assess the provision for doubtful debts"),

    ("Cash & Bank", "Obtain bank confirmations for all accounts"),
    ("Cash & Bank", "Perform bank reconciliations review"),
    ("Cash & Bank", "Test for unrecorded liabilities / cash sweep"),

    ("Inventory", "Attend/observe the physical inventory count"),
    ("Inventory", "Test inventory valuation and obsolescence provisions"),

    ("Fixed Assets & Investments", "Verify additions and disposals for the period"),
    ("Fixed Assets & Investments", "Recalculate the depreciation charge"),
    ("Fixed Assets & Investments", "Assess impairment indicators"),

    ("Liabilities & Payroll", "Test payables completeness (search for unrecorded liabilities)"),
    ("Liabilities & Payroll", "Test payroll calculations for a sample of employees"),
    ("Liabilities & Payroll", "Confirm statutory obligations (PAYE, NSSA, VAT) reconciled and settled"),

    ("Equity & Related Parties", "Verify share capital and reserves movements"),
    ("Equity & Related Parties", "Identify and evaluate related party transactions"),

    ("Going Concern & Subsequent Events", "Reassess the going concern assumption at completion"),
    ("Going Concern & Subsequent Events", "Review subsequent events up to the report date"),

    ("Completion & Reporting", "Perform overall analytical review of the financial statements"),
    ("Completion & Reporting", "Evaluate uncorrected misstatements"),
    ("Completion & Reporting", "Obtain the management representation letter"),
    ("Completion & Reporting", "Partner review and sign-off of the audit file"),
    ("Completion & Reporting", "Finalise and review financial statement disclosures"),
    ("Completion & Reporting", "Draft and issue the audit report"),
]

CONSULTING_PROGRAM = [
    ("Engagement Setup", "Understand and document the client's objectives"),
    ("Engagement Setup", "Agree scope, deliverables and timeline with the client"),
    ("Engagement Setup", "Issue engagement letter / proposal confirming terms and fees"),
    ("Engagement Setup", "Confirm independence / conflict of interest check"),

    ("Diagnostic & Data Gathering", "Collect relevant background documents and data"),
    ("Diagnostic & Data Gathering", "Conduct interviews with key stakeholders"),
    ("Diagnostic & Data Gathering", "Perform site visits/observations where relevant"),

    ("Analysis", "Analyse findings against agreed objectives/benchmarks"),
    ("Analysis", "Identify key issues, risks and opportunities"),
    ("Analysis", "Develop recommendations"),

    ("Deliverable & Reporting", "Draft the report / recommendations document"),
    ("Deliverable & Reporting", "Internal quality review of the deliverable"),
    ("Deliverable & Reporting", "Present findings to the client"),
    ("Deliverable & Reporting", "Incorporate client feedback"),

    ("Engagement Close-out", "Obtain client sign-off/acceptance of the deliverable"),
    ("Engagement Close-out", "Complete the engagement file and billing"),
    ("Engagement Close-out", "Conduct a post-engagement review / lessons learned"),
]

TAX_CGT_PROGRAM = [
    ("Engagement Setup", "Confirm client's tax registration details and compliance status"),
    ("Engagement Setup", "Agree scope of advice (e.g. CGT computation, transaction structuring)"),
    ("Engagement Setup", "Issue engagement letter"),

    ("Fact Finding", "Obtain details of the asset(s)/transaction(s) in question"),
    ("Fact Finding", "Obtain proof of original cost/base cost and acquisition date"),
    ("Fact Finding", "Obtain details of improvements/capital expenditure incurred"),
    ("Fact Finding", "Confirm disposal proceeds and disposal date"),

    ("Computation & Technical Analysis", "Determine applicable CGT rate and any exemptions/rollover relief"),
    ("Computation & Technical Analysis", "Compute capital gain/loss and inflation allowance where applicable"),
    ("Computation & Technical Analysis", "Consider withholding tax on disposal, where applicable"),
    ("Computation & Technical Analysis", "Review any relevant double tax treaty implications"),

    ("Compliance & Filing", "Prepare the CGT return / computation schedule"),
    ("Compliance & Filing", "Confirm supporting documents are retained for ZIMRA"),
    ("Compliance & Filing", "File the return within the statutory deadline"),
    ("Compliance & Filing", "Confirm payment of tax due"),

    ("Advisory & Sign-off", "Document the technical position/opinion for the client file"),
    ("Advisory & Sign-off", "Communicate the outcome and obligations to the client in writing"),
    ("Advisory & Sign-off", "Partner review and sign-off"),
]

AUP_PROGRAM = [
    ("Engagement Setup", "Agree the specific procedures to be performed with the engagement party"),
    ("Engagement Setup", "Confirm the intended users and restriction on use of the report"),
    ("Engagement Setup", "Issue engagement letter referencing the agreed-upon procedures standard"),
    ("Engagement Setup", "Confirm independence and competence to accept the engagement"),

    ("Performing Procedures", "Perform each agreed-upon procedure exactly as specified"),
    ("Performing Procedures", "Document factual findings for each procedure (no opinion/conclusion)"),
    ("Performing Procedures", "Obtain supporting evidence/documentation for each finding"),

    ("Reporting", "Draft the factual findings report (no assurance conclusion expressed)"),
    ("Reporting", "Partner review of the report and findings"),
    ("Reporting", "Issue the report to the specified/intended users only"),
]

FORENSIC_PROGRAM = [
    ("Engagement Setup & Scoping", "Clarify the allegation/suspicion and objectives of the investigation"),
    ("Engagement Setup & Scoping", "Agree scope, timeframe and reporting format with the instructing party"),
    ("Engagement Setup & Scoping", "Confirm independence and any legal privilege considerations"),
    ("Engagement Setup & Scoping", "Secure and preserve relevant records/evidence (chain of custody)"),

    ("Evidence Gathering", "Identify and collect relevant financial records and documents"),
    ("Evidence Gathering", "Identify and interview relevant personnel/witnesses"),
    ("Evidence Gathering", "Obtain and analyse electronic evidence (emails, system logs) where relevant"),
    ("Evidence Gathering", "Perform data analytics / trend and anomaly analysis on transactions"),

    ("Investigation & Analysis", "Trace and quantify suspected irregular transactions"),
    ("Investigation & Analysis", "Reconcile findings against supporting documentation"),
    ("Investigation & Analysis", "Identify control weaknesses that enabled the irregularity"),
    ("Investigation & Analysis", "Assess quantum of loss/exposure"),

    ("Reporting & Follow-up", "Draft findings report with supporting evidence schedule"),
    ("Reporting & Follow-up", "Partner/legal review of the report prior to issue"),
    ("Reporting & Follow-up", "Present findings to the instructing party"),
    ("Reporting & Follow-up", "Recommend control improvements to prevent recurrence"),
    ("Reporting & Follow-up", "Support any legal/disciplinary/insurance claim process as required"),
]

# (name, type, description, procedure list)
TEMPLATES = [
    (
        "Standard Statutory Audit Program",
        "Audit",
        "General-purpose audit program covering planning through completion.",
        AUDIT_PROGRAM,
    ),
    (
        "Standard Assurance Engagement",
        "Assurance",
        "Baseline procedures for an assurance engagement.",
        ASSURANCE_PROGRAM,
    ),
    (
        "Full Statutory Audit Program (ISA-Aligned)",
        "Audit",
        "Detailed audit program following ISA structure: acceptance, risk assessment, "
        "controls, fraud, substantive testing by area, going concern and completion.",
        FULL_AUDIT_PROGRAM,
    ),
    (
        "Consulting Engagement (General)",
        "Consulting",
        "General-purpose consulting checklist: scoping, diagnostic, analysis, deliverable and close-out.",
        CONSULTING_PROGRAM,
    ),
    (
        "Tax / CGT Advisory",
        "Consulting",
        "Checklist for tax advisory and Capital Gains Tax engagements.",
        TAX_CGT_PROGRAM,
    ),
    (
        "Agreed-Upon Procedures Engagement",
        "Assurance",
        "Detailed checklist for agreed-upon-procedures / special purpose assurance engagements.",
        AUP_PROGRAM,
    ),
    (
        "Forensic Audit / Investigation",
        "Investigative Engagement",
        "Checklist for forensic audit and fraud investigation engagements.",
        FORENSIC_PROGRAM,
    ),
]


# (type, filename in document_templates/<type>/, ref_code, title, description)
#
# The 8 Standard-Audit / Full-Audit templates below (SA-* / FA-* originally)
# have their ref_code set to the matching Filing Index N-code instead, per
# the firm's Audit Working Paper Indexing & Filing Policy - these templates
# produce a working paper that has a direct home in that index, so the
# reference shown in the app should be the one staff will actually file the
# finished document under, not the old pre-Filing-Index catalogue code.
# The forensic (FR-*), Assurance (AS-*/AUP-*), Consulting (CE-*) and Tax Advisory
# (TX-*) templates are left as-is: the filing policy is explicitly scoped to
# statutory audits, so those engagement types have no N-code equivalent.
#
# Two of the eight are judgment calls, since a template can bundle content
# that spans more than one N-code section - flagged here so the firm can
# reassign either one from the Filing Index page if a different code fits
# their practice better:
#   - FA-03 "Materiality and Risk Assessment Workpaper" -> N1001. Its own
#     description covers both materiality and a risk register; N1001
#     (Materiality) was chosen as the primary because materiality is named
#     first and is this workpaper's core calculation.
#   - FA-05 "Audit Completion Memorandum" -> N9003. Its description spans
#     misstatements, going concern and opinion type, none of which is a
#     perfect single match; N9003 (Summary of Misstatements) was chosen
#     since that's the completion memorandum's usual centrepiece.
# Where a template's own ref_code was NOT changed, it's because either it
# is out of the filing policy's scope (see above) or is intentionally
# unassigned, and any DocumentTemplate row the firm has already renamed
# via the app's Edit screen is left untouched - see
# migrate_document_template_ref_codes() below.
DOCUMENT_LIBRARY = [
    ("Audit", "SA-01 Engagement Letter.docx", "N1008", "Engagement Letter (Standard Audit)",
     "Standard statutory audit engagement letter."),
    ("Audit", "SA-02 Management Representation Letter.docx", "N9006", "Management Representation Letter (Standard)",
     "Representation letter to obtain from management at completion."),
    ("Audit", "SA-03 Audit Completion Checklist.docx", "N9009", "Audit Completion Checklist",
     "Final sign-off checklist before issuing the audit report."),
    ("Audit", "FA-01 Engagement Letter.docx", "N1008", "Engagement Letter (ISA-Aligned)",
     "Full statutory audit engagement letter referencing ISAs and independence."),
    ("Audit", "FA-02 Audit Planning Memorandum.docx", "N1002", "Audit Planning Memorandum",
     "Understanding the entity, preliminary analytics, materiality, risks and strategy."),
    ("Audit", "FA-03 Materiality and Risk Assessment Workpaper.docx", "N1001", "Materiality & Risk Assessment Workpaper",
     "Materiality calculation and risk register for a full ISA-aligned audit."),
    ("Audit", "FA-04 Management Representation Letter.docx", "N9006", "Management Representation Letter (Full)",
     "Detailed representation letter for a full ISA-aligned audit."),
    ("Audit", "FA-05 Audit Completion Memorandum.docx", "N9003", "Audit Completion Memorandum",
     "Summary of findings, uncorrected misstatements, going concern and opinion type."),
    ("Audit", "FR-01 Engagement Letter Terms of Reference.docx", "FR-01", "Terms of Reference (Forensic)",
     "Scope, confidentiality and reporting terms for a forensic investigation."),
    ("Audit", "FR-02 Evidence Chain of Custody Log.docx", "FR-02", "Evidence / Chain of Custody Log",
     "Register of evidence items and custody transfers for an investigation."),
    ("Audit", "FR-03 Findings Report.docx", "FR-03", "Forensic Investigation Findings Report",
     "Findings, root cause and recommendations report for an investigation."),
    ("Assurance", "AS-01 Engagement Letter.docx", "AS-01", "Engagement Letter (Assurance)",
     "Standard assurance engagement letter."),
    ("Assurance", "AS-02 Assurance Report.docx", "AS-02", "Assurance Report",
     "Independent assurance report / conclusion template."),
    ("Assurance", "AUP-01 Engagement Letter.docx", "AUP-01", "Engagement Letter (Agreed-Upon Procedures)",
     "Engagement letter referencing the agreed-upon-procedures standard."),
    ("Assurance", "AUP-02 Factual Findings Report.docx", "AUP-02", "Factual Findings Report",
     "Report of factual findings for an agreed-upon-procedures engagement."),
    ("Consulting", "CE-01 Engagement Letter Proposal.docx", "CE-01", "Engagement Letter / Proposal",
     "General consulting engagement letter and proposal."),
    ("Consulting", "CE-02 Findings and Recommendations Report.docx", "CE-02", "Findings & Recommendations Report",
     "Deliverable report template: findings, recommendations, next steps."),
    ("Consulting", "CE-03 Client Sign-off Acceptance Form.docx", "CE-03", "Client Sign-off / Acceptance Form",
     "Client acceptance form for a consulting deliverable."),
    ("Tax Advisory", "TX-01 Engagement Letter.docx", "TX-01", "Engagement Letter (Tax / CGT Advisory)",
     "Engagement letter for tax advisory and CGT engagements."),
    ("Tax Advisory", "TX-02 Fact Finding Information Request Schedule.docx", "TX-02", "Fact-Finding / Information Request Schedule",
     "Checklist of documents to request from the client for a CGT computation."),
    ("Tax Advisory", "TX-03 CGT Computation Workpaper.xlsx", "TX-03", "CGT Computation Workpaper (Excel)",
     "Live Capital Gains Tax computation with working formulas."),
    ("Tax Advisory", "TX-04 Technical Position Memo.docx", "TX-04", "Technical Position Memo",
     "Document the technical analysis and position adopted for a tax matter."),
    ("Tax Advisory", "TX-05 Client Outcome Letter.docx", "TX-05", "Client Outcome Letter",
     "Letter communicating the tax outcome and obligations to the client."),
]


def seed_document_templates():
    """Populate the writable Document Templates library from the read-only
    bundled originals. Safe to re-run any time: a template only gets created
    if no row with that (type, filename) already exists, so it never
    overwrites a file or row the user has since edited via the app, and
    re-running after an app update only adds newly-bundled templates.
    Keyed on filename rather than ref_code so that two templates can
    legitimately share the same Filing Index N-code (e.g. SA-01 and FA-01
    are both variants of the Terms of Engagement letter, N1008) without
    either one being skipped as "already seeded".
    """
    for i, (eng_type, filename, ref_code, title, description) in enumerate(DOCUMENT_LIBRARY):
        if DocumentTemplate.query.filter_by(type=eng_type, filename=filename).first():
            continue

        src_dir = os.path.join(Config.DOCUMENT_TEMPLATES_SEED_DIR, eng_type)
        dst_dir = os.path.join(Config.DOCUMENT_TEMPLATES_DATA_DIR, eng_type)
        os.makedirs(dst_dir, exist_ok=True)
        src_path = os.path.join(src_dir, filename)
        dst_path = os.path.join(dst_dir, filename)
        if os.path.exists(src_path) and not os.path.exists(dst_path):
            shutil.copy2(src_path, dst_path)

        db.session.add(DocumentTemplate(
            type=eng_type,
            ref_code=ref_code,
            title=title,
            description=description,
            filename=filename,
            order=i,
        ))
    db.session.commit()


# (filename, old ref_code, new ref_code) - only for DocumentTemplate rows
# already seeded on a live database before the Filing Index N-codes above
# replaced the old SA-*/FA-* catalogue codes. seed_document_templates() only
# ever adds new rows, so an existing installation's already-seeded rows keep
# their original ref_code forever unless something updates them - this does
# that, once, the first time the app starts after this change.
DOCUMENT_TEMPLATE_REF_CODE_MIGRATIONS = [
    ("SA-01 Engagement Letter.docx", "SA-01", "N1008"),
    ("SA-02 Management Representation Letter.docx", "SA-02", "N9006"),
    ("SA-03 Audit Completion Checklist.docx", "SA-03", "N9009"),
    ("FA-01 Engagement Letter.docx", "FA-01", "N1008"),
    ("FA-02 Audit Planning Memorandum.docx", "FA-02", "N1002"),
    ("FA-03 Materiality and Risk Assessment Workpaper.docx", "FA-03", "N1001"),
    ("FA-04 Management Representation Letter.docx", "FA-04", "N9006"),
    ("FA-05 Audit Completion Memorandum.docx", "FA-05", "N9003"),
]


def migrate_document_template_ref_codes():
    """Bring already-seeded DocumentTemplate rows' ref_code up to date with
    the Filing Index N-codes now used in DOCUMENT_LIBRARY. Matches on
    filename (which never changes) AND the exact old ref_code, so a row the
    firm has since renamed by hand via the Edit Template screen is left
    alone rather than being silently overwritten. Safe to call on every
    startup: once a row is migrated (or was never on the old code to begin
    with), there is nothing left to match and this is a no-op.
    """
    changed = False
    for filename, old_code, new_code in DOCUMENT_TEMPLATE_REF_CODE_MIGRATIONS:
        row = DocumentTemplate.query.filter_by(filename=filename, ref_code=old_code).first()
        if row:
            row.ref_code = new_code
            changed = True
    if changed:
        db.session.commit()


# (old, new) exact-text pairs for CompanyDocument.document_type rows already
# stored under the old Companies Act [Chapter 24:03] CR-form labels, now
# renumbered under the Companies and Other Business Entities Act [Chapter
# 24:31] (COBE Act): old CR14 (Return of Directors) is now CR6, and old CR6
# (Notice of Situation of Registered Office) is now CR5. COMPANY_DOCUMENT_
# TYPES in models.py already uses the new labels for anything uploaded from
# now on - this corrects documents uploaded before that change.
COMPANY_DOCUMENT_TYPE_CR_CODE_MIGRATIONS = [
    ("CR14 - Return of Directors", "CR6 - Return of Directors"),
    ("CR6 - Notice of Situation of Registered Office", "CR5 - Notice of Situation of Registered Office"),
]

# (old procedure_text, new procedure_text, old target_output, new
# target_output) for SubstantiveProcedureItem rows already synced onto a
# Secretarial engagement's Execution Plan under the old CR14 label for
# registered office changes - see SECRETARIAL_BASELINE_SUBSTANTIVE_
# PROCEDURES["Annual Returns & Filings Log"] in models.py. The "CR6 for
# directors" line is left out here because it was already correct under the
# old Companies Act and remains correct under COBE - nothing to migrate.
SUBSTANTIVE_PROCEDURE_CR_CODE_MIGRATIONS = [
    (
        "Lodge Form CR14 for changes in registered office address within 14 days (Sec 160).",
        "Lodge Form CR5 for changes in registered office address within 14 days (Sec 160).",
        "Certified Form CR14 copy",
        "Certified Form CR5 copy",
    ),
]


def migrate_cr_form_codes():
    """Bring already-stored CR-form references up to date with the
    Companies and Other Business Entities Act [Chapter 24:31] (COBE Act)
    renumbering: old CR14 (Return of Directors) is now CR6, and old CR6
    (Notice of Situation of Registered Office) is now CR5. Covers:
      - CompanyDocument.document_type (see COMPANY_DOCUMENT_TYPE_CR_CODE_
        MIGRATIONS above).
      - SubstantiveProcedureItem.procedure_text/target_output already synced
        onto a Secretarial engagement's Execution Plan (see
        SUBSTANTIVE_PROCEDURE_CR_CODE_MIGRATIONS above) - the "Company
        Summary" line added to the same baseline list needs no migration
        here, since sync_substantive_procedures (engagements.py) is purely
        additive and will pick up the new line on its own next re-sync.
    Matches on exact old text only, so a document type or procedure a firm
    member has since renamed/customised by hand is left alone - same safety
    property as migrate_document_template_ref_codes() above. Safe to call on
    every startup: once a row is migrated (or was never on the old text to
    begin with), there is nothing left to match and this is a no-op.
    """
    changed = False
    for old_type, new_type in COMPANY_DOCUMENT_TYPE_CR_CODE_MIGRATIONS:
        for row in CompanyDocument.query.filter_by(document_type=old_type).all():
            row.document_type = new_type
            changed = True
    for old_text, new_text, old_output, new_output in SUBSTANTIVE_PROCEDURE_CR_CODE_MIGRATIONS:
        for row in SubstantiveProcedureItem.query.filter_by(procedure_text=old_text).all():
            row.procedure_text = new_text
            if row.target_output == old_output:
                row.target_output = new_output
            changed = True
    if changed:
        db.session.commit()


# The new "Company Summary" line added to SECRETARIAL_FINALISATION_
# CHECKLIST_ITEMS["Registry & Reconciliations"] in models.py - unlike
# SubstantiveProcedureItem above, FinalisationChecklistItem rows are only
# ever seeded once per engagement (seed_finalisation_checklist in
# engagements.py refuses to run again once the checklist has items), so an
# engagement that already seeded its Finalisation checklist before this
# line existed would never pick it up on its own. This adds it, once, to
# every Secretarial engagement's checklist that's missing it.
SECRETARIAL_FINALISATION_COMPANY_SUMMARY_ITEM = (
    "Registry & Reconciliations",
    "Company Summary filed with the Registrar under the new COBE Act - form reference and filing deadline "
    "reconfirmed against current CIPO guidance (newly introduced requirement, not yet finalised when this item "
    "was added)?",
)


def migrate_secretarial_finalisation_company_summary():
    """Adds the new Company Summary line (see SECRETARIAL_FINALISATION_
    COMPANY_SUMMARY_ITEM above) to every Secretarial engagement's
    Finalisation checklist that already has items but doesn't have this one
    yet. Leaves a checklist with no items at all untouched - that engagement
    will get the line the normal way, the first time someone clicks "seed
    default items". Safe to call on every startup: once every existing
    checklist has the line (or never had any items to begin with), there is
    nothing left to add and this is a no-op."""
    section, item_text = SECRETARIAL_FINALISATION_COMPANY_SUMMARY_ITEM
    changed = False
    for record in FinalisationChecklist.query.all():
        if not record.engagement or record.engagement.type != "Secretarial":
            continue
        items = record.checklist_items
        if not items:
            continue
        if any(i.item_text == item_text for i in items):
            continue
        max_order = max((i.order for i in items), default=0)
        db.session.add(FinalisationChecklistItem(
            finalisation_checklist_id=record.id,
            section=section,
            item_text=item_text,
            order=max_order + 1,
        ))
        changed = True
    if changed:
        db.session.commit()


# The firm's Audit Working Paper Indexing & Filing Policy, transcribed from
# the source document, as (code, category, section, typical_contents)
# tuples - Current File (N-series) first, then Permanent File (P-series,
# marked is_permanent below). Two corrections and one addition were made
# versus the source document, each flagged where it happens:
#
#   - The document's own introduction, and its worked example for an actual
#     client file, both use N1000 for Trial Balance and N1001 for
#     Materiality - but the document's own section-index table instead
#     lists N1001 twice (once for Trial Balance, once for Materiality) and
#     N1000 for Terms of Engagement. N1000/N1001 are assigned here to match
#     the introduction and worked example (the more authoritative, doubly-
#     consistent source), and Terms of Engagement - the one item bumped out
#     - is placed at N1008, the next free number in the 1000s range.
#   - N1009 (Client Acceptance & Continuance) has been added: the document
#     doesn't list a dedicated code for it, and while its content overlaps
#     with N1007 Independence & Ethics, the app treats Client Acceptance &
#     Continuance as its own distinct working paper, so it gets its own
#     number rather than being folded into N1007.
#   - N3700 (Inventories) has been added: the document's Assets range
#     (N3100-N3600) has no code for inventories at all, so this fills that
#     gap immediately after N3600 Prepayments & Other Assets.
#
# All three are ordinary rows in the seeded table, not hard-coded - correct
# them from the Filing Index page if the firm's own numbering differs.
FILING_INDEX_SEED = [
    # ---- Current File (N-series) ----
    # 1000-1999: Planning & Risk Assessment
    ("N1000", "Planning & Risk Assessment", "Trial Balance", "Client trial balance, lead schedule, cast and cross-reference to draft financial statements."),
    ("N1001", "Planning & Risk Assessment", "Materiality", "Determination of overall and performance materiality, and clearly trivial threshold."),
    ("N1002", "Planning & Risk Assessment", "Audit Strategy & Plan", "Overall audit strategy memorandum, engagement timetable, staffing and budget."),
    ("N1003", "Planning & Risk Assessment", "Understanding the Entity", "Business, industry, regulatory environment, accounting policies."),
    ("N1004", "Planning & Risk Assessment", "Fraud Risk Assessment", "Engagement team discussion, fraud risk factors, response to risk of management override."),
    ("N1005", "Planning & Risk Assessment", "Planning & Legal Findings", "Laws & regulations assessment, legal confirmations planning, litigation register."),
    ("N1006", "Planning & Risk Assessment", "Preliminary Analytical Review", "Ratio analysis, trend analysis, expectation setting for the year."),
    ("N1007", "Planning & Risk Assessment", "Independence & Ethics", "Independence confirmations, conflict checks, ethical requirements (IESBA/ICAZ)."),
    ("N1008", "Planning & Risk Assessment", "Terms of Engagement", "Engagement/re-appointment letter, fee agreement."),
    ("N1009", "Planning & Risk Assessment", "Client Acceptance & Continuance", "Client acceptance/continuance decision and supporting checklist. (Added - not in the source document; see the note above this list.)"),
    # 2000-2999: Internal Control & Systems
    ("N2001", "Internal Control & Systems", "Control Environment", "Walkthroughs of key business cycles (revenue, payroll, procurement)."),
    ("N2002", "Internal Control & Systems", "Control Risk Assessment", "Design and implementation testing, control deficiencies noted."),
    ("N2003", "Internal Control & Systems", "IT & General Controls", "Systems in use (e.g. QuickBooks), access controls, backup procedures."),
    ("N2004", "Internal Control & Systems", "Deficiencies in Internal Control", "Matters for the deficiencies letter to management."),
    # 3000-3999: Statement of Financial Position - Assets
    ("N3100", "Statement of Financial Position — Assets", "Cash & Bank", "Bank confirmations, bank reconciliations, cash counts."),
    ("N3200", "Statement of Financial Position — Assets", "Accounts Receivable", "Circularisation, aged analysis, subsequent receipts, impairment assessment."),
    ("N3300", "Statement of Financial Position — Assets", "Property, Plant & Equipment", "Asset register, additions/disposals testing, depreciation schedule, physical verification."),
    ("N3400", "Statement of Financial Position — Assets", "Intangible Assets", "Software licences, amortisation testing."),
    ("N3500", "Statement of Financial Position — Assets", "Investments", "Investment schedule, valuation support."),
    ("N3600", "Statement of Financial Position — Assets", "Prepayments & Other Assets", "Prepayment schedule, sundry debtors/loans."),
    ("N3700", "Statement of Financial Position — Assets", "Inventories", "Inventory count observation, costing and net realisable value testing, cut-off. (Added - not in the source document; see the note above this list.)"),
    # 4000-4999: Statement of Financial Position - Liabilities & Equity
    ("N4100", "Statement of Financial Position — Liabilities & Equity", "Payables & Accruals", "Trade payables listing, supplier reconciliations, accruals testing."),
    ("N4200", "Statement of Financial Position — Liabilities & Equity", "Loans & Borrowings", "Shareholder/director loan schedules, loan agreements, interest testing."),
    ("N4300", "Statement of Financial Position — Liabilities & Equity", "Taxation", "Current and deferred tax computation, tax returns reconciliation, ZIMRA correspondence."),
    ("N4400", "Statement of Financial Position — Liabilities & Equity", "Provisions & Contingencies", "Provision schedules, legal claims and contingent liability assessment."),
    ("N4500", "Statement of Financial Position — Liabilities & Equity", "Share Capital", "Share register, share capital movements, statutory filings (CR forms)."),
    ("N4600", "Statement of Financial Position — Liabilities & Equity", "Equity & Reserves", "Statement of changes in equity working paper, retained earnings roll-forward."),
    # 5000-5999: Statement of Comprehensive Income
    ("N5100", "Statement of Comprehensive Income", "Revenue", "Revenue recognition testing (IFRS 15), cut-off, fee income analysis."),
    ("N5200", "Statement of Comprehensive Income", "Payroll & Staff Costs", "Payroll reconciliation, PAYE/NSSA compliance, staff cost analytics."),
    ("N5300", "Statement of Comprehensive Income", "Operating Expenses", "Expense analytics, vouching, related working papers per expense line."),
    ("N5400", "Statement of Comprehensive Income", "Other Income / Expenses & FX", "Exchange gains/losses, other income, non-operating items."),
    # 6000-6999: Other Audit Areas
    ("N6100", "Other Audit Areas", "Related Parties", "Related party identification, transactions and balances, disclosure testing."),
    ("N6200", "Other Audit Areas", "Going Concern", "Going concern assessment, cash flow forecasts, directors' representations."),
    ("N6300", "Other Audit Areas", "Subsequent Events", "Review of events after the reporting period."),
    ("N6400", "Other Audit Areas", "Litigation & Claims", "Legal confirmation letters and responses."),
    ("N6500", "Other Audit Areas", "Group / Component Instructions", "Where applicable - group audit instructions and component reporting."),
    # 7000-7999: Compliance & Statutory
    ("N7100", "Compliance & Statutory", "Companies Act / COBE Act", "Section 193 reporting matters, statutory compliance checklist."),
    ("N7200", "Compliance & Statutory", "Tax Compliance", "Income tax, VAT and PAYE compliance review."),
    ("N7300", "Compliance & Statutory", "Other Regulatory", "ICAZ/PAAB and sector-specific regulatory matters."),
    # 8000-8999: Reporting & Disclosure
    ("N8100", "Reporting & Disclosure", "Draft Financial Statements", "Draft FS with cross-references to supporting working papers."),
    ("N8200", "Reporting & Disclosure", "Disclosure Checklist", "IFRS presentation and disclosure checklist."),
    ("N8300", "Reporting & Disclosure", "Reports to Management", "Management letter / report to those charged with governance."),
    # 9000-9999: Completion & Review
    ("N9001", "Completion & Review", "Report Items – Equity", "Final check of equity note/statement against underlying records. (Per the source document: file the equity movement schedule itself under N4600, and reserve this number for the final completion-stage cross-check that the equity note agrees to the ledger.)"),
    ("N9002", "Completion & Review", "Dividends Test", "Test of dividend declarations, approvals and statutory compliance."),
    ("N9003", "Completion & Review", "Summary of Misstatements", "Corrected and uncorrected misstatements, evaluated against materiality."),
    ("N9004", "Completion & Review", "Final Analytical Review", "Overall review of financial statements as a whole."),
    ("N9005", "Completion & Review", "Final Subsequent Events Review", "Review up to the date of the auditor's report."),
    ("N9006", "Completion & Review", "Management Representation Letter", "Signed representation letter from directors."),
    ("N9007", "Completion & Review", "Partner / EQCR Review Notes", "Engagement partner and (where applicable) quality control reviewer notes."),
    ("N9008", "Completion & Review", "Audit Opinion & Sign-off", "Signed independent auditor's report, ICAZ/PAAB sign-off details."),
    ("N9009", "Completion & Review", "File Completion Checklist", "Confirmation that the file is complete and ready for archiving."),
]

FILING_INDEX_PERMANENT_SEED = [
    # ---- Permanent File (P-series) ----
    ("P1000", "Incorporation & Statutory", "Incorporation & Statutory", "Certificate of incorporation, CR6/CR5, memorandum & articles."),
    ("P2000", "Engagement Administration", "Engagement Administration", "Standing engagement letter, independence declarations, fee history."),
    ("P3000", "Accounting Policies", "Accounting Policies", "Group/entity accounting policy manual, significant IFRS elections."),
    ("P4000", "Prior Year Financial Statements", "Prior Year Financial Statements", "Signed financial statements and auditor's reports, prior years."),
    ("P5000", "Structure & Governance", "Structure & Governance", "Organisation chart, directors register, shareholding structure."),
]


def seed_filing_index():
    """Populate the firm-wide Filing Index (FilingIndexSection) from the
    firm's Audit Working Paper Indexing & Filing Policy - see
    FILING_INDEX_SEED / FILING_INDEX_PERMANENT_SEED above for the data and
    the corrections/additions made versus the source document. Safe to
    re-run any time: only called when the table is empty (see app.py), and
    only ever adds a code that doesn't already exist, so it never overwrites
    anything the firm has since edited (including marking a code "not
    used") from the Filing Index page."""
    order = 0
    for code, category, section, typical_contents in FILING_INDEX_SEED:
        if FilingIndexSection.query.filter_by(code=code).first():
            continue
        db.session.add(FilingIndexSection(
            code=code, is_permanent=False, category=category, section=section,
            typical_contents=typical_contents, order=order,
        ))
        order += 1
    for code, category, section, typical_contents in FILING_INDEX_PERMANENT_SEED:
        if FilingIndexSection.query.filter_by(code=code).first():
            continue
        db.session.add(FilingIndexSection(
            code=code, is_permanent=True, category=category, section=section,
            typical_contents=typical_contents, order=order,
        ))
        order += 1
    db.session.commit()


def seed_statutory_deadlines():
    """Pre-load the 2026 QPD (Provisional Tax) instalment dates onto the
    firm-wide Statutory Deadlines list shown on the Legislative Update
    Calendar - see StatutoryDeadline in models.py for why this is a
    firm-maintained list rather than something computed. Only called once,
    when the table is completely empty (see app.py) - never re-run after
    that, so a row someone has since edited or deleted here stays edited
    or deleted, and the firm's own VAT/PAYE entries (which this app never
    assumes a fixed date for) aren't disturbed by a later restart.

    The four QPD dates below (25 March, 25 June, 25 September, 20
    December) are the standard ZIMRA due dates for the 1st-4th quarterly
    payment dates and are well-corroborated across multiple ZIMRA public
    notices - unlike VAT/PAYE due dates, which genuinely are NOT a fixed
    day-of-month in practice (ZIMRA has shifted them by notice before), so
    this app does not guess at those and leaves them for the firm to add
    themselves."""
    year = date.today().year
    qpd_dates = [
        (date(year, 3, 25), "QPD 1 (Q1) due date"),
        (date(year, 6, 25), "QPD 2 (Q2) due date"),
        (date(year, 9, 25), "QPD 3 (Q3) due date"),
        (date(year, 12, 20), "QPD 4 (Q4) due date"),
    ]
    for due_date, description in qpd_dates:
        db.session.add(StatutoryDeadline(
            tax_head="Provisional Tax (QPDs)", description=description, due_date=due_date,
        ))
    db.session.commit()


def seed_qpd_instalment_rates():
    """Pre-load the standard ZIMRA QPD instalment structure onto the
    firm-wide, firm-editable QPDInstalmentRate table used by the client-
    level QPD (provisional income tax) estimator - see QPDInstalmentRate's
    own docstring in models.py. 10%/35%/65%/100% cumulative by 25 March/
    25 June/25 September/20 December is the same well-corroborated
    structure already pre-loaded as plain due-date reminders in
    seed_statutory_deadlines() above - the firm asked for these to stay
    editable here too (unlike the due dates in StatutoryDeadline), so this
    only pre-loads a sensible starting point. Only called once, when the
    table is completely empty (see app.py) - never re-run after that, so a
    row the firm has since edited (including the dates/percentages
    themselves, or adding/removing an instalment) stays exactly as edited."""
    rows = [
        ("1st QPD", 3, 25, 10.0, 0),
        ("2nd QPD", 6, 25, 35.0, 1),
        ("3rd QPD", 9, 25, 65.0, 2),
        ("4th QPD", 12, 20, 100.0, 3),
    ]
    for label, due_month, due_day, cumulative_pct, order in rows:
        db.session.add(QPDInstalmentRate(
            label=label, due_month=due_month, due_day=due_day, cumulative_pct=cumulative_pct, order=order,
        ))
    db.session.commit()


# The Neverlank Standard Chart of Accounts - a drafted starting point (not
# an externally-mandated numbering) organised in the usual SFP/PL block
# order: 1000s non-current then current assets, 2000s equity, 2400s/2800s
# non-current then current liabilities, 4000s revenue, 5000s cost of sales,
# 6000s other income, 7000s distribution/admin/other expenses/depreciation/
# finance costs, 8000s income tax, 9000s OCI/equity movements. Each entry
# is (account_number, account_name, fs_category code from financials.
# FS_CATEGORIES) - fully firm-editable from the Chart of Accounts settings
# page once seeded (see seed_chart_of_accounts's own docstring below).
CHART_OF_ACCOUNTS_STANDARD = [
    # Non-current assets
    ("1000", "Land and Buildings", "ppe"),
    ("1010", "Plant and Machinery", "ppe"),
    ("1020", "Motor Vehicles", "ppe"),
    ("1030", "Furniture, Fittings and Equipment", "ppe"),
    ("1040", "Computer Equipment", "ppe"),
    ("1050", "Capital Work in Progress", "ppe"),
    ("1100", "Goodwill", "intangible_assets"),
    ("1110", "Software and Licences", "intangible_assets"),
    ("1120", "Patents and Trademarks", "intangible_assets"),
    ("1200", "Investment Property", "investment_property"),
    ("1300", "Investments in Subsidiaries/Associates", "long_term_investments"),
    ("1310", "Other Long-term Investments", "long_term_investments"),
    ("1400", "Deferred Tax Asset", "deferred_tax_asset"),
    ("1500", "Long-term Loans Receivable", "other_noncurrent_assets"),
    ("1510", "Other Non-current Assets", "other_noncurrent_assets"),
    # Current assets
    ("1600", "Raw Materials Inventory", "inventories"),
    ("1610", "Work in Progress Inventory", "inventories"),
    ("1620", "Finished Goods Inventory", "inventories"),
    ("1630", "Consumable Stores", "inventories"),
    ("1700", "Trade Debtors", "trade_receivables"),
    ("1710", "Allowance for Credit Losses", "trade_receivables"),
    ("1720", "Other Receivables", "trade_receivables"),
    ("1730", "Staff Loans and Advances", "trade_receivables"),
    ("1800", "Prepayments", "other_current_assets"),
    ("1810", "VAT Receivable/Control", "other_current_assets"),
    ("1820", "Withholding Tax Receivable", "other_current_assets"),
    ("1900", "Cash on Hand (Petty Cash)", "cash"),
    ("1910", "Bank Accounts - USD", "cash"),
    ("1920", "Bank Accounts - ZWG", "cash"),
    ("1930", "Short-term Deposits/Money Market", "cash"),
    # Equity
    ("2000", "Ordinary Share Capital", "share_capital"),
    ("2010", "Preference Share Capital", "share_capital"),
    ("2100", "Share Premium", "share_premium"),
    ("2200", "Retained Earnings", "retained_earnings"),
    ("2300", "Revaluation Reserve", "other_reserves"),
    ("2310", "Other Reserves", "other_reserves"),
    # Non-current liabilities
    ("2400", "Long-term Bank Loans", "long_term_borrowings"),
    ("2410", "Finance Lease Liabilities (non-current)", "long_term_borrowings"),
    ("2420", "Shareholder Loans (non-current)", "long_term_borrowings"),
    ("2500", "Deferred Tax Liability", "deferred_tax_liability"),
    ("2600", "Long-term Provisions (e.g. Leave Pay)", "long_term_provisions"),
    ("2700", "Other Non-current Liabilities", "other_noncurrent_liabilities"),
    # Current liabilities
    ("2800", "Trade Creditors", "trade_payables"),
    ("2810", "Accrued Expenses", "trade_payables"),
    ("2820", "Other Payables", "trade_payables"),
    ("2900", "Bank Overdraft", "short_term_borrowings"),
    ("2910", "Short-term Loans", "short_term_borrowings"),
    ("2920", "Current Portion of Long-term Loans", "short_term_borrowings"),
    ("3000", "Income Tax Payable", "current_tax_payable"),
    ("3010", "QPD / Provisional Tax Payable", "current_tax_payable"),
    ("3100", "Short-term Provisions", "short_term_provisions"),
    ("3200", "VAT Payable/Control", "other_current_liabilities"),
    ("3210", "PAYE Payable", "other_current_liabilities"),
    ("3220", "NSSA Payable", "other_current_liabilities"),
    ("3230", "NEC/Pension Payable", "other_current_liabilities"),
    ("3240", "Withholding Tax Payable", "other_current_liabilities"),
    ("3250", "Dividends Payable", "other_current_liabilities"),
    # Revenue
    ("4000", "Sale of Goods", "revenue"),
    ("4010", "Rendering of Services", "revenue"),
    ("4020", "Rental Income (trading)", "revenue"),
    ("4030", "Export Sales", "revenue"),
    # Cost of sales
    ("5000", "Opening Inventory", "cost_of_sales"),
    ("5010", "Purchases", "cost_of_sales"),
    ("5020", "Direct Labour", "cost_of_sales"),
    ("5030", "Manufacturing Overheads", "cost_of_sales"),
    ("5040", "Closing Inventory (contra)", "cost_of_sales"),
    ("5050", "Freight and Carriage Inwards", "cost_of_sales"),
    # Other income
    ("6000", "Interest Received", "other_income"),
    ("6010", "Dividends Received", "other_income"),
    ("6020", "Profit on Disposal of Assets", "other_income"),
    ("6030", "Foreign Exchange Gains", "other_income"),
    ("6040", "Sundry Income", "other_income"),
    # Distribution costs
    ("7000", "Sales Salaries and Commissions", "distribution_costs"),
    ("7010", "Advertising and Marketing", "distribution_costs"),
    ("7020", "Freight and Carriage Outwards", "distribution_costs"),
    ("7030", "Travel and Entertainment (Sales)", "distribution_costs"),
    # Administrative expenses
    ("7100", "Salaries and Wages", "admin_expenses"),
    ("7110", "Directors' Emoluments", "admin_expenses"),
    ("7120", "Rent and Rates", "admin_expenses"),
    ("7130", "Electricity and Water", "admin_expenses"),
    ("7140", "Telephone and Internet", "admin_expenses"),
    ("7150", "Printing and Stationery", "admin_expenses"),
    ("7160", "Insurance", "admin_expenses"),
    ("7170", "Repairs and Maintenance", "admin_expenses"),
    ("7180", "Audit and Accounting Fees", "admin_expenses"),
    ("7190", "Legal and Professional Fees", "admin_expenses"),
    ("7200", "Bank Charges", "admin_expenses"),
    ("7210", "Motor Vehicle Running Expenses", "admin_expenses"),
    ("7220", "Subscriptions and Licences", "admin_expenses"),
    ("7230", "Staff Welfare and Training", "admin_expenses"),
    ("7240", "NSSA and NEC Contributions (Employer)", "admin_expenses"),
    ("7250", "Security Costs", "admin_expenses"),
    ("7260", "Donations", "admin_expenses"),
    # Other expenses
    ("7400", "Loss on Disposal of Assets", "other_expenses"),
    ("7410", "Foreign Exchange Losses", "other_expenses"),
    ("7420", "Impairment Losses", "other_expenses"),
    ("7430", "Sundry Expenses", "other_expenses"),
    # Depreciation & amortisation
    ("7500", "Depreciation - Property, Plant and Equipment", "depreciation_amortisation"),
    ("7510", "Amortisation - Intangible Assets", "depreciation_amortisation"),
    # Finance costs
    ("7600", "Interest on Loans", "finance_costs"),
    ("7610", "Interest on Finance Leases", "finance_costs"),
    ("7620", "Bank Overdraft Interest", "finance_costs"),
    # Income tax expense
    ("8000", "Current Income Tax Expense", "income_tax_expense"),
    ("8010", "AIDS Levy", "income_tax_expense"),
    ("8020", "Deferred Tax Expense/(Credit)", "income_tax_expense"),
    # OCI / equity movements
    ("9000", "Revaluation Surplus/(Deficit), net of tax", "oci_items"),
    ("9100", "Dividends Declared/Paid", "dividends_paid"),
]


def seed_chart_of_accounts():
    """Pre-load the Neverlank Standard Chart of Accounts (see
    CHART_OF_ACCOUNTS_STANDARD above) onto the firm-wide, firm-editable
    StandardChartOfAccounts table - same "editable starting point" convention as
    seed_qpd_instalment_rates above: this is a drafted numbering, not an
    externally-mandated one, so the firm is expected to adjust it to its
    own real numbering/naming over time. Only called once, when the table
    is completely empty (see app.py) - never re-run after that, so an
    account the firm has since edited, renumbered, deactivated or added
    stays exactly as the firm left it."""
    for order, (account_number, account_name, fs_category) in enumerate(CHART_OF_ACCOUNTS_STANDARD):
        db.session.add(StandardChartOfAccounts(
            account_number=account_number, account_name=account_name, fs_category=fs_category, order=order,
        ))
    db.session.commit()


def seed_permissions():
    """Create any missing (role, permission_key) rows from the PERMISSIONS
    registry in models.py, using that entry's own default_roles. Safe to
    re-run any time (including after a later update adds a new permission
    key): it only ever adds a row that doesn't already exist, so it never
    overwrites a toggle an admin has since changed on the settings screen.
    Admin doesn't need a row (user_has_permission() always allows admin),
    but one is still seeded (allowed=True) so the settings screen has
    something consistent to show in that column.
    """
    for key, _, _, default_roles in PERMISSIONS:
        for role in USER_ROLES:
            if Permission.query.filter_by(role=role, permission_key=key).first():
                continue
            db.session.add(Permission(role=role, permission_key=key, allowed=role in default_roles))
    db.session.commit()


# ZIMRA PAYE (Foreign Currency / USD) tax tables, Jan-Dec 2024, as supplied
# by the firm (attached ZIMRA table image). Bands are entered EDGE-TO-EDGE
# (band N's upper == band N+1's lower) rather than ZIMRA's own "x.00 / x.01"
# pair style - see PayrollTaxBand's docstring in models.py for why: the
# progressive calculate_paye() formula taxes each band as [lower, upper), so
# edge-to-edge boundaries are what reproduce ZIMRA's own published worked
# examples exactly (verified by hand for all three frequencies below: Monthly
# $1,800 -> $455.00; Weekly $65 -> $8.38; Fortnightly $420 -> $88.85 - using
# the literal .01-offset pairs would silently under-tax by a cent or two).
# upper=None marks the open-ended top band.
PAYROLL_TAX_BANDS_2024_USD = {
    "Monthly": [
        (0, 100, 0),
        (100, 300, 20),
        (300, 1000, 25),
        (1000, 2000, 30),
        (2000, 3000, 35),
        (3000, None, 40),
    ],
    "Fortnightly": [
        (0, 46.15, 0),
        (46.15, 138.46, 20),
        (138.46, 461.54, 25),
        (461.54, 923.08, 30),
        (923.08, 1384.62, 35),
        (1384.62, None, 40),
    ],
    "Weekly": [
        (0, 23.08, 0),
        (23.08, 69.23, 20),
        (69.23, 230.77, 25),
        (230.77, 461.54, 30),
        (461.54, 692.31, 35),
        (692.31, None, 40),
    ],
}


def seed_payroll_tax_bands():
    """Pre-load ZIMRA's 2024 USD PAYE band tables (PAYROLL_TAX_BANDS_2024_USD
    above), one independent set per pay frequency - see PayrollTaxBand's
    docstring in models.py for why a frequency can't just reuse another
    frequency's bands divided down.

    Gated PER-FREQUENCY (only seeds a frequency that currently has ZERO
    PayrollTaxBand rows), not on the table being empty overall - so this is
    safe to call on every startup: it will never overwrite a frequency the
    firm has already customised (even if another frequency still needs
    seeding), and it still closes the gap for a frequency - typically
    Fortnightly/Weekly, since the old code only ever had one global list -
    that has never had bands of its own.
    """
    for frequency, bands in PAYROLL_TAX_BANDS_2024_USD.items():
        if PayrollTaxBand.query.filter_by(frequency=frequency).first():
            continue
        for order, (lower, upper, rate_pct) in enumerate(bands):
            db.session.add(PayrollTaxBand(
                frequency=frequency, lower=lower, upper=upper, rate_pct=rate_pct, order=order,
            ))
    db.session.commit()


def seed_payroll_nssa_defaults():
    """Backfill the firm-confirmed NSSA employee/employer % and Insurable
    Earnings ceiling (4.5% / 4.5% / USD 700.00, per the NSSA Act [Chapter
    17:04] and SI 393 of 1993 - see PAYROLL_TAX_CAVEAT in models.py) onto an
    EXISTING PayrollTaxSettings row that's still sitting at the old
    unconfirmed 0%/0%/no-ceiling defaults.

    Gated on all three fields still being untouched, not on the row simply
    existing - so this is safe to call on every startup: a firm that has
    already entered its own NSSA % or ceiling (even just one of the three
    fields) is left alone, exactly like seed_payroll_tax_bands above never
    overwrites a frequency the firm has customised. A brand-new install
    doesn't need this at all - PayrollTaxSettings' own column defaults
    already create a fresh row with these confirmed figures.
    """
    settings = PayrollTaxSettings.query.get(1)
    if not settings:
        return
    if settings.nssa_employee_pct == 0.0 and settings.nssa_employer_pct == 0.0 and settings.nssa_insurable_ceiling is None:
        settings.nssa_employee_pct = 4.5
        settings.nssa_employer_pct = 4.5
        settings.nssa_insurable_ceiling = 700.0
        db.session.commit()


def seed_leave_types():
    """Pre-load Zimbabwe's statutory leave types (LEAVE_TYPES_STATUTORY_ZW in
    models.py - Annual/Vacation, Sick, Special/Compassionate, Maternity),
    per the Labour Act [Chapter 28:01] s.14A and related provisions.

    Gated PER-NAME (only adds a LeaveType that doesn't already exist by that
    exact name), not on the table being empty overall - same pattern as
    seed_payroll_tax_bands above - so this is safe to call on every startup:
    it never touches a leave type the firm has already customised (renamed,
    retired, or edited the figures on), and it still closes the gap for a
    brand new statutory type added to LEAVE_TYPES_STATUTORY_ZW in a later
    update.
    """
    for order, (
        name, accrual_days_per_month, annual_entitlement_days, max_accumulation_days,
        is_accumulative, full_pay_days, half_pay_pct, min_service_months_to_take, notes,
    ) in enumerate(LEAVE_TYPES_STATUTORY_ZW):
        if LeaveType.query.filter_by(name=name).first():
            continue
        db.session.add(LeaveType(
            name=name,
            accrual_days_per_month=accrual_days_per_month,
            annual_entitlement_days=annual_entitlement_days,
            max_accumulation_days=max_accumulation_days,
            is_accumulative=is_accumulative,
            full_pay_days=full_pay_days,
            half_pay_pct=half_pay_pct if half_pay_pct is not None else 50.0,
            min_service_months_to_take=min_service_months_to_take,
            order=order,
            notes=notes,
        ))
    db.session.commit()


def run_seed():
    # Admin user
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", name="Petros Piki", email="pet.piki@gmail.com", role="admin")
        admin.set_password("changeme123")
        db.session.add(admin)

    # Checklist templates - each only gets created if a template with that
    # exact name doesn't already exist, so this is safe to re-run any time.
    for name, eng_type, description, items in TEMPLATES:
        if ChecklistTemplate.query.filter_by(name=name).first():
            continue
        template = ChecklistTemplate(name=name, type=eng_type, description=description)
        db.session.add(template)
        db.session.flush()
        for i, (section, text) in enumerate(items):
            db.session.add(
                ChecklistTemplateItem(template_id=template.id, section=section, item_text=text, order=i)
            )

    db.session.commit()
    seed_document_templates()
    seed_permissions()


if __name__ == "__main__":
    from app import app
    with app.app_context():
        run_seed()
        print("Seed complete. Admin login -> username: admin / password: changeme123")
