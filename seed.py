"""Seed the database with an admin user and starter checklist templates.

Run with:  flask --app app seed
Safe to re-run any time, including on a database that already has clients,
engagements and users in it: it only ever adds a template if one with that
exact name doesn't already exist, and never touches anything else.
"""
import os
import shutil

from extensions import db
from models import User, ChecklistTemplate, ChecklistTemplateItem, DocumentTemplate
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
        "Audit",
        "Checklist for forensic audit and fraud investigation engagements.",
        FORENSIC_PROGRAM,
    ),
]


# (type, filename in document_templates/<type>/, ref_code, title, description)
DOCUMENT_LIBRARY = [
    ("Audit", "SA-01 Engagement Letter.docx", "SA-01", "Engagement Letter (Standard Audit)",
     "Standard statutory audit engagement letter."),
    ("Audit", "SA-02 Management Representation Letter.docx", "SA-02", "Management Representation Letter (Standard)",
     "Representation letter to obtain from management at completion."),
    ("Audit", "SA-03 Audit Completion Checklist.docx", "SA-03", "Audit Completion Checklist",
     "Final sign-off checklist before issuing the audit report."),
    ("Audit", "FA-01 Engagement Letter.docx", "FA-01", "Engagement Letter (ISA-Aligned)",
     "Full statutory audit engagement letter referencing ISAs and independence."),
    ("Audit", "FA-02 Audit Planning Memorandum.docx", "FA-02", "Audit Planning Memorandum",
     "Understanding the entity, preliminary analytics, materiality, risks and strategy."),
    ("Audit", "FA-03 Materiality and Risk Assessment Workpaper.docx", "FA-03", "Materiality & Risk Assessment Workpaper",
     "Materiality calculation and risk register for a full ISA-aligned audit."),
    ("Audit", "FA-04 Management Representation Letter.docx", "FA-04", "Management Representation Letter (Full)",
     "Detailed representation letter for a full ISA-aligned audit."),
    ("Audit", "FA-05 Audit Completion Memorandum.docx", "FA-05", "Audit Completion Memorandum",
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
    ("Consulting", "TX-01 Engagement Letter.docx", "TX-01", "Engagement Letter (Tax / CGT Advisory)",
     "Engagement letter for tax advisory and CGT engagements."),
    ("Consulting", "TX-02 Fact Finding Information Request Schedule.docx", "TX-02", "Fact-Finding / Information Request Schedule",
     "Checklist of documents to request from the client for a CGT computation."),
    ("Consulting", "TX-03 CGT Computation Workpaper.xlsx", "TX-03", "CGT Computation Workpaper (Excel)",
     "Live Capital Gains Tax computation with working formulas."),
    ("Consulting", "TX-04 Technical Position Memo.docx", "TX-04", "Technical Position Memo",
     "Document the technical analysis and position adopted for a tax matter."),
    ("Consulting", "TX-05 Client Outcome Letter.docx", "TX-05", "Client Outcome Letter",
     "Letter communicating the tax outcome and obligations to the client."),
]


def seed_document_templates():
    """Populate the writable Document Templates library from the read-only
    bundled originals. Safe to re-run any time: a template only gets created
    if no row with that (type, ref_code) already exists, so it never
    overwrites a file or row the user has since edited via the app, and
    re-running after an app update only adds newly-bundled templates.
    """
    for i, (eng_type, filename, ref_code, title, description) in enumerate(DOCUMENT_LIBRARY):
        if DocumentTemplate.query.filter_by(type=eng_type, ref_code=ref_code).first():
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


if __name__ == "__main__":
    from app import app
    with app.app_context():
        run_seed()
        print("Seed complete. Admin login -> username: admin / password: changeme123")
