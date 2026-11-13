"""models, part 2 of 12.  Holds: MPSignedMixin, User, Client, Engagement.

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

# Seeded instead of the five free-text ENTITY_UNDERSTANDING_FIELDS above when
# the engagement's type is "Business Intelligence and IT Engagements" (see
# engagements.seed_entity_checklist) - same Yes/No/N-A + comment presentation
# as FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS above, but scoped to
# understanding the entity's IT environment, data holdings, prior incident
# history and AML/CFT exposure ahead of a cyber/IT/AML-CFT control
# assurance engagement, rather than ISA 315's entity/industry/accounting-
# policy prompts.
BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS = [
    # These two were originally one compound question ("What is the overall
    # IT environment (on-premise, cloud, or hybrid) and how is it organised
    # (in-house IT function, outsourced/managed service provider, or a
    # mix)?") with its answer choices spelled out in brackets rather than
    # asked/answered as a proper dropdown - split into two, each carrying
    # its own bracketed choices as response_options (see
    # EntityUnderstandingChecklistItem.response_options) so the item's own
    # dropdown offers exactly those choices instead of the generic
    # Yes/No/N-A one.
    ("IT Environment & Infrastructure Overview", "What is the overall IT environment?", ["On-premise", "Cloud", "Hybrid"]),
    ("IT Environment & Infrastructure Overview", "How is the entity's IT function organised?", ["In-house IT function", "Outsourced / Managed Service Provider", "A mix"]),
    ("IT Environment & Infrastructure Overview", "Which core business systems (ERP, banking platforms, core application systems) does the entity rely on, and who are the key vendors/service providers?"),
    ("IT Environment & Infrastructure Overview", "Has the IT environment changed materially in the period under review (new system implementations, cloud migration, mergers/acquisitions, outsourcing changes)?"),
    ("IT Environment & Infrastructure Overview", "Is there a documented IT governance structure (IT steering committee, CIO/IT Manager, board-level IT oversight)?"),
    ("IT Environment & Infrastructure Overview", "Is there up-to-date network architecture/topology documentation (network diagrams) covering the entity's on-premise and cloud environments?"),
    ("IT Environment & Infrastructure Overview", "If cloud platforms are used (e.g. AWS, Azure, Google Cloud Platform), which services/regions are in use, and is there a documented cloud architecture diagram?"),
    ("IT Environment & Infrastructure Overview", "Have the data flows between the entity's critical financial and operational applications been mapped (source systems, integration points, and where financial data ultimately resides)?"),
    ("Data Holdings & Classification", "What categories of data does the entity hold or process (customer personal information, financial data, employee data, trade secrets/intellectual property)?"),
    ("Data Holdings & Classification", "Is data classified by sensitivity, and is there a documented data retention and disposal policy?"),
    # Same compound-question split as the IT Environment items above - the
    # original bracketed "(on-premise servers, local cloud provider,
    # offshore cloud provider)" becomes this item's own dropdown choices,
    # with the Cyber and Data Protection Act follow-up kept as a separate
    # (ordinary Yes/No/N-A) question.
    ("Data Holdings & Classification", "Where is data physically/logically stored?", ["On-premise servers", "Local cloud provider", "Offshore cloud provider"]),
    ("Data Holdings & Classification", "Does the data storage location raise any cross-border data transfer considerations under the Cyber and Data Protection Act [Chapter 12:07]?"),
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
    ("ICT Governance & Operations", "Based on the above, what are the entity's baseline inherent IT risks - system downtime/availability, unauthorised or poorly controlled changes, and data integrity - and how significant is each considered to be?"),
    ("AML/CFT Framework & Regulatory Context", "Does the entity fall within a sector subject to AML/CFT obligations under the Money Laundering and Proceeds of Crime Act [Chapter 9:24], and if so, is there a designated Money Laundering Reporting Officer?"),
    ("AML/CFT Framework & Regulatory Context", "What customer due diligence (CDD)/know-your-customer procedures are in place, including for higher-risk customers or channels?"),
    ("AML/CFT Framework & Regulatory Context", "What transaction monitoring and screening arrangements are in place (sanctions screening, unusual-activity monitoring)?"),
    ("AML/CFT Framework & Regulatory Context", "Has the entity filed any suspicious transaction reports with the Financial Intelligence Unit, or received any regulatory findings/guidance relevant to AML/CFT compliance?"),
    ("AML/CFT Framework & Regulatory Context", "What record-keeping arrangements are in place for CDD information and transaction records, and for how long are they retained?"),
    ("Regulatory Landscape & Standards Alignment", "Has the entity adopted, or aligned itself to, any recognised information security/IT control framework or standard (e.g. ISO/IEC 27001, the NIST Cybersecurity Framework/NIST SP 800-53), whether formally certified or only informally applied?"),
    ("Regulatory Landscape & Standards Alignment", "Is the entity registered/licensed with, or otherwise subject to oversight by, the Postal and Telecommunications Regulatory Authority of Zimbabwe (POTRAZ) or another sector-specific ICT/telecoms regulator?"),
    ("Regulatory Landscape & Standards Alignment", "How does the entity's data protection practice map to the Cyber and Data Protection Act [Chapter 12:07] and, where the entity has cross-border operations or customers, to equivalent data protection regimes (e.g. GDPR) that may also apply?"),
    ("Regulatory Landscape & Standards Alignment", "Has the entity received any guidance, directive or finding from the Reserve Bank of Zimbabwe (RBZ), the Financial Intelligence Unit, or another regulator specifically relating to its IT, cyber security or AML/CFT systems?"),
    ("Regulatory Landscape & Standards Alignment", "Are there any pending or upcoming regulatory changes (local or, where relevant, international) that could affect the entity's IT, cyber security, information security or AML/CFT compliance posture?"),
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
    ("Constitutional Documents & Corporate Governance", "What are the specific rules and thresholds set out in the Articles regarding board powers, borrowing limits, execution of documents (Section 35), and pre-emption rights (Sections 99 and 139)?", ("constitutional", "directorship")),
    ("Constitutional Documents & Corporate Governance", "Does the company meet the director requirements of Section 195 (a private company with more than one and fewer than ten shareholders: at least two directors; ten or more shareholders: at least three; a public company: seven to fifteen), including at least one director ordinarily resident in Zimbabwe (Section 195(2))?", ("directorship",)),
    ("Constitutional Documents & Corporate Governance", "For public companies, does the board have 7 to 15 directors (Section 195(1)), including at least three non-executive or independent directors (Section 206(2))?", ("directorship",)),
    ("Constitutional Documents & Corporate Governance", "Are directors aware of their statutory duty of care, skill, and good faith (Section 54) and duty of loyalty (Section 55)?", ("directorship",)),
    ("Constitutional Documents & Corporate Governance", "Does the company have at least one company secretary who is ordinarily resident in Zimbabwe (Section 198(1)); for a public company, is the secretary qualified under Section 198(4) and not holding another office; and does the secretary certify in the annual financial statements whether the returns and notices required by the Act have been filed (Section 198(3)(f))?", ('directorship', 'incorporation', 'statutory_records')),
    ("Statutory Registers & Ownership Transparency", "Does the company maintain an up-to-date Register of Beneficial Owners under Section 72 (kept where the register of members is kept, with a custodian resident in Zimbabwe), identifying natural persons who directly or indirectly hold MORE than 20% of the shares or voting rights, can appoint or remove a majority of the directors, or otherwise exercise significant control (Section 2), and are updates filed with the Registrar within seven days of a material change?", ("statutory_records", "share_capital")),
    ("Statutory Registers & Ownership Transparency", "Does any nominee shareholding comply with Section 73, which allows shares to be held through a nominee only where the beneficial owner (with associates) holds less than 20%?", ("statutory_records", "share_capital")),
    ("Statutory Registers & Ownership Transparency", "Are the Register of Members (Section 159), Register of Directors and Secretaries (Section 217), Register of Allotments (Section 121), Register of Mortgages and Debentures (Section 155) and, for a company other than a private company, the Register of Directors' Share Holdings (Section 213) maintained at the registered office and reconciled?", ("statutory_records",)),
    ("Statutory Registers & Ownership Transparency", "Has the company appointed a person resident in Zimbabwe as custodian of the register of beneficial owners, and are the name and contact details recorded in the form filed with the Registrar (Section 72(4)-(5))?", ('statutory_records', 'share_capital')),
    ("Capital Structure, Distributions & Solvency", "Have any variations of share rights, share allotments, or capital changes been executed in accordance with board powers and properly reflected in the Memorandum/Articles?", ("share_capital",)),
    ("Capital Structure, Distributions & Solvency", "Before making any distribution, has the board authorised it and acknowledged by resolution that the Solvency and Liquidity Test is met (Sections 102 and 138); and before a share buy-back or financial assistance for the purchase of shares (Sections 128-129 and 123), has a general meeting authorised it in advance?", ("share_capital",)),
    ("Capital Structure, Distributions & Solvency", "Are there any proposed structural changes or class variations that might trigger minority shareholder appraisal remedies (Section 233, which applies to a variation of class rights under Section 143 and a merger under Section 228)?", ("share_capital", "constitutional")),
    ("Capital Structure, Distributions & Solvency", "Is the register of allotments kept at the registered office, and is the return of allotments (with the contract, for shares not paid in cash) lodged with the Registrar within one month of each allotment (Section 121)?", ('share_capital',)),
    ("Board Operations, Meetings & Documentation", "Are all loans, guarantees, or security provided by the company for directors or officers within the exceptions to the prohibition in Section 208 (for a private company, with the consent of holders of nine-tenths of the shares) and disclosed in the accounts as Section 216 requires?", ("directorship", "meetings")),
    ("Board Operations, Meetings & Documentation", "Does the board act only at meetings (in person, or by electronic or conference means where the articles allow it) or by written consent signed by ALL directors entitled to vote, as Section 196 provides, applying the board quorum in the articles (Section 204)?", ("meetings",)),
    ("Board Operations, Meetings & Documentation", "Are minute books kept up-to-date for all board meetings, committee meetings, and AGMs/EGMs, capturing key commercial decisions, risk evaluations, and conflict disclosures (Section 180 for members' meetings, prepared within 20 days; Section 205 for the board and committees)?", ("meetings",)),
    ("Statutory Filings & Registry Compliance", "Is the company compliant with filing its Annual Return within 21 days of the anniversary of its incorporation, registration or re-registration (Section 165(2)), with the private-company certificate where it applies (Section 165(3))?", ("incorporation", "statutory_records")),
    ("Statutory Filings & Registry Compliance", "Have changes in directors/secretaries (within one month, Section 217), the registered office or postal address (Section 31), share allotments (one month, Section 121), capital increases (one month, Section 141) and special resolutions (one month, Section 178) been lodged with the Registrar within the periods the Act sets?", ("statutory_records", "directorship")),
    ("Statutory Filings & Registry Compliance", "Has the entity completed its mandatory administrative re-registration process under the COBE Act (Section 303(9) and the Tenth Schedule; a company that did not re-register in time is struck off, Section 303(12))?", ("incorporation", "statutory_records", "re_registration")),
    ("Statutory Filings & Registry Compliance", "Is the date of incorporation, registration or re-registration recorded on the Secretarial Engagement Plan, so the annual return can be diarised for 21 days after each anniversary (Section 165(2)), and has an AGM been held within the last twelve months (Section 167(2))?", ('incorporation', 'statutory_records', 'meetings')),
    ("Company Re-registration", "Has the company already been re-registered on the Registrar's online system, and are the new certificate and registration details held on the client file?", ("re_registration",)),
    ("Company Re-registration", "If not yet re-registered, what is the Registrar's current re-registration deadline and fee (both have been extended and revised before; the Act's own period was twelve months from the effective date, Section 303(9)), and has the board been told that a company not re-registered by the deadline risks being treated as non-operational and struck off the register (Section 303(12))?", ("re_registration",)),
    ("Company Re-registration", "Are all annual returns filed up to date? The Registrar asks for proof of this with the application, so any outstanding returns must be cleared first.", ("re_registration", "statutory_records")),
    ("Company Re-registration", "Are the existing registration documents available as clear scans - Certificate of Incorporation, Memorandum and Articles of Association, the registered-office and directors forms (CR5, CR6) and the latest annual return?", ("re_registration",)),
    ("Company Re-registration", "Are the details of every director, secretary and shareholder (full names, ID or passport numbers, dates of birth, addresses and contact details) current and consistent with the statutory registers and the Register of Beneficial Ownership (Section 72)?", ("re_registration", "statutory_records")),
    ("Company Re-registration", "Do the Memorandum and Articles need to be amended to conform with the COBE Act, and will the members approve those amendments by special resolution (Sections 79, 81 and 175)? Section 303(11) does not allow such changes 'under the guise of' re-registration: they follow the ordinary formalities, before, after or together with it.", ("re_registration", "constitutional")),
    ("Company Re-registration", "Who is authorised to sign the board resolution, the members' resolution and any authority or affidavit the Registrar requires, and are they available to sign?", ("re_registration",)),
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
        "Review the entity's network defense architecture documentation (firewall rule sets, network segmentation, DMZ design) and assess whether it reflects the risk profile of internet-facing and critical systems.",
        "For a sample of periods/systems, obtain evidence of penetration test or vulnerability scan execution and test that critical/high findings were remediated within the entity's defined SLA.",
    ],
    "Information Security": [
        "Test the information/data classification and handling policy against a sample of actual data holdings to assess whether classification is applied in practice.",
        "Test user access and privilege management - new user provisioning, periodic access reviews, and timely revocation for a sample of leavers - for a sample of critical systems.",
        "Assess data protection and privacy compliance arrangements against the requirements of the Cyber and Data Protection Act [Chapter 12:07], including any data subject/consent processes and breach notification arrangements.",
        "Review third-party and cloud security arrangements - contracts, security clauses/SLAs, and any independent assurance reports (e.g. SOC 2) obtained over key providers - for a sample of critical third parties.",
        "Evaluate the entity's encryption policy/standard for data at rest and in transit (e.g. database/disk encryption, TLS enforcement) and confirm it has been formally approved.",
        "Test encryption configuration for a sample of critical systems/databases holding sensitive data (at rest) and key data transmission channels (in transit) against the entity's encryption standard.",
        "Evaluate Data Loss Prevention (DLP) arrangements - policy coverage and any DLP tooling over sensitive data egress channels (email, removable media, cloud upload) - for design adequacy.",
        "Test Identity & Access Management (IAM) controls for a sample of critical systems - authentication strength (including multi-factor authentication for privileged/remote access), privileged access management, and segregation of duties.",
    ],
    "Information Technology (ICT)": [
        "Review the ICT governance and strategy documentation and assess whether it is approved, current, and aligned to the entity's objectives.",
        "Test the change and release management process for a sample of system changes, including authorisation, testing, and segregation between development and production environments.",
        "For a sample of systems developed or acquired in the period, assess whether appropriate project governance, testing, and user acceptance procedures were followed.",
        "Test IT operations controls (job scheduling, incident/problem management, capacity management) for a sample period.",
        "Test backup and disaster recovery arrangements, including evidence of successful backup completion and a recent restore/recovery test.",
        "Review the business continuity plan for IT-dependent operations and assess whether it has been tested within a reasonable period.",
        "Review the entity's IT asset and software lifecycle management process - hardware/software inventory, patch management, end-of-life/end-of-support tracking, and software licensing compliance.",
        "Test a sample of IT assets against the asset register/inventory and confirm patch levels and support status, in particular for any end-of-life hardware or software identified.",
    ],
    "AML/CFT Compliance": [
        "Evaluate the entity's AML/CFT policies and procedures against the Money Laundering and Proceeds of Crime Act [Chapter 9:24] and applicable Financial Intelligence Unit guidance.",
        "Test customer due diligence (CDD) procedures for a sample of new customer onboardings, including identification, verification, and risk-rating.",
        "Test transaction monitoring and sanctions screening arrangements for a sample period, including follow-up of any alerts generated.",
        "Review suspicious transaction reporting arrangements and, for any reports filed in the period, assess timeliness and record-keeping.",
        "Test record-keeping of CDD information and transaction records for a sample of customers/transactions against the entity's retention policy and statutory requirements.",
        "Evaluate the configuration of the automated transaction monitoring and sanctions/PEP screening system - rule/scenario library, name-matching thresholds and fuzzy-matching logic - and assess whether calibration is periodically reviewed and approved.",
        "Test a sample of system-generated alerts for evidence of false-positive/false-negative tuning effectiveness, including review of any threshold or rule changes made in the period and their authorisation.",
        "Test the completeness and integrity of data ingested into the transaction monitoring/screening system from core banking or other source systems for a sample period (e.g. reconciling transaction counts/values between the source system and the monitoring system).",
        "Evaluate the system's audit trail/logging of screening decisions, alert dispositions and manual overrides for completeness, and confirm it is tamper-evident/access-restricted.",
    ],
}

# Tags each BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES entry above as either
# an "ITGC" (a test of the underlying control environment - policy/design
# review, walkthroughs, configuration standards) or a "Substantive" audit
# procedure (data-driven/technical validation - sampling, reconciliation,
# technical testing) - the Phase 3 distinction requested for the IT Audit
# methodology. Looked up by sync_substantive_procedures (engagements.py) and
# written to SubstantiveProcedureItem.procedure_kind for each Business
# Intelligence and IT Engagement procedure as it's seeded; shown as a "Kind"
# column on the Substantive Procedures tab and in the generated Word/Excel
# programme, for this engagement type only. A procedure with no entry here
# (i.e. any procedure added manually) is left untagged.
BUSINESS_IT_PROCEDURE_KIND = {
    "Evaluate the entity's threat and vulnerability management process, including the frequency and remediation of vulnerability scans/penetration tests over a sample period.": "ITGC",
    "Test perimeter and endpoint protection controls (firewalls, intrusion detection/prevention, endpoint protection/EDR) for a sample of critical systems.": "Substantive",
    "Assess security monitoring and logging arrangements - what is logged, for how long, who reviews it, and how alerts are escalated - and test operation for a sample period.": "ITGC",
    "Evaluate the incident response and recovery plan for completeness and currency, and test it against a sample of actual incidents (if any) or a walkthrough of the response process.": "ITGC",
    "Confirm whether an independent penetration test or vulnerability assessment has been performed in the period, and review the findings and remediation status.": "ITGC",
    "Review the entity's network defense architecture documentation (firewall rule sets, network segmentation, DMZ design) and assess whether it reflects the risk profile of internet-facing and critical systems.": "ITGC",
    "For a sample of periods/systems, obtain evidence of penetration test or vulnerability scan execution and test that critical/high findings were remediated within the entity's defined SLA.": "Substantive",
    "Test the information/data classification and handling policy against a sample of actual data holdings to assess whether classification is applied in practice.": "Substantive",
    "Test user access and privilege management - new user provisioning, periodic access reviews, and timely revocation for a sample of leavers - for a sample of critical systems.": "Substantive",
    "Assess data protection and privacy compliance arrangements against the requirements of the Cyber and Data Protection Act [Chapter 12:07], including any data subject/consent processes and breach notification arrangements.": "ITGC",
    "Review third-party and cloud security arrangements - contracts, security clauses/SLAs, and any independent assurance reports (e.g. SOC 2) obtained over key providers - for a sample of critical third parties.": "ITGC",
    "Evaluate the entity's encryption policy/standard for data at rest and in transit (e.g. database/disk encryption, TLS enforcement) and confirm it has been formally approved.": "ITGC",
    "Test encryption configuration for a sample of critical systems/databases holding sensitive data (at rest) and key data transmission channels (in transit) against the entity's encryption standard.": "Substantive",
    "Evaluate Data Loss Prevention (DLP) arrangements - policy coverage and any DLP tooling over sensitive data egress channels (email, removable media, cloud upload) - for design adequacy.": "ITGC",
    "Test Identity & Access Management (IAM) controls for a sample of critical systems - authentication strength (including multi-factor authentication for privileged/remote access), privileged access management, and segregation of duties.": "Substantive",
    "Review the ICT governance and strategy documentation and assess whether it is approved, current, and aligned to the entity's objectives.": "ITGC",
    "Test the change and release management process for a sample of system changes, including authorisation, testing, and segregation between development and production environments.": "Substantive",
    "For a sample of systems developed or acquired in the period, assess whether appropriate project governance, testing, and user acceptance procedures were followed.": "Substantive",
    "Test IT operations controls (job scheduling, incident/problem management, capacity management) for a sample period.": "Substantive",
    "Test backup and disaster recovery arrangements, including evidence of successful backup completion and a recent restore/recovery test.": "Substantive",
    "Review the business continuity plan for IT-dependent operations and assess whether it has been tested within a reasonable period.": "ITGC",
    "Review the entity's IT asset and software lifecycle management process - hardware/software inventory, patch management, end-of-life/end-of-support tracking, and software licensing compliance.": "ITGC",
    "Test a sample of IT assets against the asset register/inventory and confirm patch levels and support status, in particular for any end-of-life hardware or software identified.": "Substantive",
    "Evaluate the entity's AML/CFT policies and procedures against the Money Laundering and Proceeds of Crime Act [Chapter 9:24] and applicable Financial Intelligence Unit guidance.": "ITGC",
    "Test customer due diligence (CDD) procedures for a sample of new customer onboardings, including identification, verification, and risk-rating.": "Substantive",
    "Test transaction monitoring and sanctions screening arrangements for a sample period, including follow-up of any alerts generated.": "Substantive",
    "Review suspicious transaction reporting arrangements and, for any reports filed in the period, assess timeliness and record-keeping.": "ITGC",
    "Test record-keeping of CDD information and transaction records for a sample of customers/transactions against the entity's retention policy and statutory requirements.": "Substantive",
    "Evaluate the configuration of the automated transaction monitoring and sanctions/PEP screening system - rule/scenario library, name-matching thresholds and fuzzy-matching logic - and assess whether calibration is periodically reviewed and approved.": "ITGC",
    "Test a sample of system-generated alerts for evidence of false-positive/false-negative tuning effectiveness, including review of any threshold or rule changes made in the period and their authorisation.": "Substantive",
    "Test the completeness and integrity of data ingested into the transaction monitoring/screening system from core banking or other source systems for a sample period (e.g. reconciling transaction counts/values between the source system and the monitoring system).": "Substantive",
    "Evaluate the system's audit trail/logging of screening decisions, alert dispositions and manual overrides for completeness, and confirm it is tamper-evident/access-restricted.": "ITGC",
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

# A sixth workstream that only appears on a Secretarial engagement that has
# the "Company Re-registration" activity ticked (or already has the area).
SECRETARIAL_RE_REGISTRATION_AREA = "Company Re-registration"


def secretarial_substantive_areas(engagement):
    """The Execution Plan workstreams for a Secretarial engagement: the five
    standard ones, plus Company Re-registration when that service is ticked on
    the engagement (kept visible if its area already exists, so unticking the
    service later never hides work already done)."""
    areas = list(SECRETARIAL_SUBSTANTIVE_AREAS)
    wants = "re_registration" in engagement.secretarial_activity_list
    if not wants and engagement.id is not None:
        wants = SubstantiveProcedureArea.query.filter_by(
            engagement_id=engagement.id, area=SECRETARIAL_RE_REGISTRATION_AREA).first() is not None
    if wants:
        areas.append(SECRETARIAL_RE_REGISTRATION_AREA)
    return areas

SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES = {
    "Onboarding & Diagnostics": [
        "Perform initial search with the Registrar of Companies.",
        "Reconcile the Memorandum and Articles of Association for any borrowing limits and for share capital authorisation (Sec 96).",
        "Run the Secretarial Risk Assessment Matrix (see the Risk Assessment tab) to flag overdue filings or missing records.",
        "Compile the Client Statutory Diagnostic & Baseline Report.",
    ],
    "Board & General Meeting Cycle": [
        "Draft and circulate the annual governance calendar to board members.",
        "Issue formal Notice of AGM at least 21 days prior to the meeting (Sec 169(1)); a meeting to pass a special resolution also needs 21 days (Sec 175(1)).",
        "Compile, collate and dispatch board packs 7-10 days prior to scheduled meetings.",
        "Attend meetings, record proceedings, and produce draft minutes within 5 working days post-meeting (the Act allows 20 days for members' meetings, Sec 180; board minutes, Sec 205).",
        "File the signed board packs, approved minute books and action trackers.",
        "Diarise the next AGM so that no more than twelve months pass between annual general meetings (Sec 167(2)), and send members a copy of the financial statements at least 14 days before it where Sec 190(1) applies.",
    ],
    "Annual Returns & Filings Log": [
        "Prepare the annual return certificates: for a private company, the certificate signed by a director and the secretary (no invitation to the public; number of members - Sec 165(3)); have the return certified by a director and the secretary and keep a signed duplicate at the registered office (Sec 165(4)).",
        "Lodge the Annual Return with the Registrar within 21 days of the anniversary of the company's incorporation, registration or re-registration (Sec 165(2)), with the private-company certificate where it applies (Sec 165(3)).",
        "File Form CR6 for any change in directors or secretaries within one month (Sec 217(4)-(5)).",
        "Record any change of registered office or postal address in the constitutive documents and notify the Registrar on Form CR5 (Sec 31; the Act sets no number of days).",
        "File the return of allotments with the Registrar within one month of each allotment (Sec 121(2)).",
        "Confirm with the Registrar whether the 'Company Summary' return is the same document as the annual return (Sec 165(1) calls the annual return a summary) or a separate return, and reconfirm its form reference and filing deadline - it is not mentioned in the COBE Act as supplied.",
        "File the stamped registry certificates and official filing acknowledgments.",
    ],
    "Statutory Register Audit": [
        "Maintain and update the Register of Beneficial Owners (Sec 72), file updates with the Registrar within seven days of a material change (Sec 72(2)), and verify any nominee holds only for a beneficial owner with less than 20% (Sec 73(2)(a)).",
        "Update the Register of Members (Sec 159) following executed share transfers made on a proper instrument of transfer (Sec 151).",
        "Reconcile the Register of Mortgages and Debentures (Sec 155) against outstanding corporate debt facilities; each entry is due within 14 days of the hypothecation.",
        "Confirm the company has a secretary ordinarily resident in Zimbabwe (Sec 198(1)), at least one director ordinarily resident in Zimbabwe (Sec 195(2)) and the number of directors Sec 195(1) requires, and that the secretary certifies in the annual financial statements whether the returns and notices required by the Act have been filed (Sec 198(3)(f)).",
        "File the up-to-date statutory register file and the annual UBO verification declaration.",
    ],
    "Company Re-registration": [
        "Check on the Registrar's online portal whether the company has already been re-registered; if not, confirm the current re-registration deadline, fee and document list with the Registrar (these have been extended and revised before). The Act's own period was twelve months from the effective date (Sec 303(9)); a company not re-registered is struck off (Sec 303(12)).",
        "Run a Registrar search to confirm the company's registration number and status, and clear any outstanding annual returns before applying.",
        "Collect the existing registration documents: Certificate of Incorporation, Memorandum and Articles of Association, CR5, CR6 and the latest annual return.",
        "Confirm and update the details of every director, secretary and shareholder (names, ID numbers, dates of birth, addresses, contact details) and reconcile them to the statutory registers and the Register of Beneficial Ownership (Sec 72).",
        "Review the Memorandum and Articles against the COBE Act and prepare any amendments needed for approval by special resolution. Amendments follow the ordinary formalities (Secs 79, 81 and 175) and may not be made 'under the guise of' re-registration (Sec 303(11)).",
        "Prepare the board resolution and the members' special resolution approving re-registration, and the signed authority or affidavit the Registrar requires.",
        "Submit the re-registration application on the Registrar's online portal, upload the supporting documents and pay the prescribed fee.",
        "Track the application with the Registrar and respond to any query or rejection until it is approved.",
        "File the new registration certificate, the Registrar's acknowledgment and the updated company documents on the client file, and update the statutory registers and governance calendar.",
    ],
    "Corporate Actions": [
        "Coordinate board evaluation and drafting of the statutory Solvency and Liquidity Test acknowledgment (Secs 102 and 138) prior to executing distributions, and obtain general-meeting authority before any share buy-back (Secs 128-129).",
        "Draft special resolutions for constitutional amendments (memorandum Sec 79, articles Sec 81) or a change of name (Sec 26, with the Registrar's written approval first), give 21 days' notice and lodge a copy with the Registrar within one month (Secs 175 and 178).",
        "Enter each mortgage, registered security interest or debenture in the Register of Mortgages and Debentures within 14 days (Sec 155(1)(a)) and register the security as Sec 154 requires (Movable Property Security Interest Act or Deeds Registry); confirm any time limit under those Acts.",
        "File the executed solvency certificates, filed special resolutions and registered security extracts.",
    ],
}

SECRETARIAL_AREA_REFERENCES = {
    "Onboarding & Diagnostics": "W1",
    "Board & General Meeting Cycle": "W2",
    "Annual Returns & Filings Log": "W3",
    "Statutory Register Audit": "W4",
    "Corporate Actions": "W5",
    "Company Re-registration": "W6",
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
    "Reconcile the Memorandum and Articles of Association for any borrowing limits and for share capital authorisation (Sec 96).": ("Engagement start", "Senior Secretariat", "Diagnostic Report & Risk Matrix"),
    "Run the Secretarial Risk Assessment Matrix (see the Risk Assessment tab) to flag overdue filings or missing records.": ("T+14 days from onboarding", "Senior Secretariat", "Diagnostic Report & Risk Matrix"),
    "Compile the Client Statutory Diagnostic & Baseline Report.": ("T+14 days from onboarding", "Senior Secretariat", "Client Statutory Diagnostic & Baseline Report"),
    "Draft and circulate the annual governance calendar to board members.": ("FYE + 5 months", "Corporate Secretary", "Annual governance calendar"),
    "Issue formal Notice of AGM at least 21 days prior to the meeting (Sec 169(1)); a meeting to pass a special resolution also needs 21 days (Sec 175(1)).": ("FYE + 5 months", "Corporate Secretary", "Notice Pack & Proxy Forms"),
    "Compile, collate and dispatch board packs 7-10 days prior to scheduled meetings.": ("Each board/committee meeting", "Corporate Secretary", "Board pack dispatched"),
    "Attend meetings, record proceedings, and produce draft minutes within 5 working days post-meeting (the Act allows 20 days for members' meetings, Sec 180; board minutes, Sec 205).": ("FYE + 6 months (AGM) / each meeting", "Lead Secretary", "Signed AGM/board minutes"),
    "File the signed board packs, approved minute books and action trackers.": ("Post-meeting", "Minutes Secretary", "Signed Board Packs, Approved Minute Books & Action Trackers"),
    "Diarise the next AGM so that no more than twelve months pass between annual general meetings (Sec 167(2)), and send members a copy of the financial statements at least 14 days before it where Sec 190(1) applies.": ("After each AGM", "Corporate Secretary", "Updated governance calendar"),
    "Prepare the annual return certificates: for a private company, the certificate signed by a director and the secretary (no invitation to the public; number of members - Sec 165(3)); have the return certified by a director and the secretary and keep a signed duplicate at the registered office (Sec 165(4)).": ("Before the annual return is lodged", "Compliance Officer", "Signed certificates & duplicate return"),
    "Lodge the Annual Return with the Registrar within 21 days of the anniversary of the company's incorporation, registration or re-registration (Sec 165(2)), with the private-company certificate where it applies (Sec 165(3)).": ("Anniversary of incorporation / registration / re-registration (+21 days)", "Compliance Officer", "Stamped annual return receipt"),
    "File Form CR6 for any change in directors or secretaries within one month (Sec 217(4)-(5)).": ("Director/secretary resignation or appointment", "Assistant Secretary", "Certified Form CR6 copy"),
    "Record any change of registered office or postal address in the constitutive documents and notify the Registrar on Form CR5 (Sec 31; the Act sets no number of days).": ("Registered office change", "Assistant Secretary", "Certified Form CR5 copy"),
    "File the return of allotments with the Registrar within one month of each allotment (Sec 121(2)).": ("Share allotment approved", "Secretariat Staff", "Stamped Return of Allotment"),
    "Confirm with the Registrar whether the 'Company Summary' return is the same document as the annual return (Sec 165(1) calls the annual return a summary) or a separate return, and reconfirm its form reference and filing deadline - it is not mentioned in the COBE Act as supplied.": ("Annual - reconfirm trigger/deadline", "Compliance Officer", "Company Summary filing confirmation (reconfirm form reference)"),
    "File the stamped registry certificates and official filing acknowledgments.": ("On receipt from Registrar", "Compliance Officer", "Stamped Registry Certificates & Filing Acknowledgments"),
    "Maintain and update the Register of Beneficial Owners (Sec 72), file updates with the Registrar within seven days of a material change (Sec 72(2)), and verify any nominee holds only for a beneficial owner with less than 20% (Sec 73(2)(a)).": ("Annual review / update within 7 days of a material change", "Compliance Officer", "Reconciled Register of Beneficial Owners"),
    "Update the Register of Members (Sec 159) following executed share transfers made on a proper instrument of transfer (Sec 151).": ("Share transfer executed", "Compliance Officer", "Updated Register of Members"),
    "Reconcile the Register of Mortgages and Debentures (Sec 155) against outstanding corporate debt facilities; each entry is due within 14 days of the hypothecation.": ("Debt creation / annual review", "Legal/Secretariat", "Reconciled Register of Charges"),
    "Confirm the company has a secretary ordinarily resident in Zimbabwe (Sec 198(1)), at least one director ordinarily resident in Zimbabwe (Sec 195(2)) and the number of directors Sec 195(1) requires, and that the secretary certifies in the annual financial statements whether the returns and notices required by the Act have been filed (Sec 198(3)(f)).": ("Annual / on appointment", "Compliance Officer", "Director and secretary compliance note"),
    "File the up-to-date statutory register file and the annual UBO verification declaration.": ("Annual", "Compliance Officer", "Up-to-date Statutory Register File & Annual UBO Verification Declaration"),
    "Coordinate board evaluation and drafting of the statutory Solvency and Liquidity Test acknowledgment (Secs 102 and 138) prior to executing distributions, and obtain general-meeting authority before any share buy-back (Secs 128-129).": ("Distribution/buyback proposed", "Engagement Partner", "Signed Board Solvency Declaration"),
    "Draft special resolutions for constitutional amendments (memorandum Sec 79, articles Sec 81) or a change of name (Sec 26, with the Registrar's written approval first), give 21 days' notice and lodge a copy with the Registrar within one month (Secs 175 and 178).": ("Ad-hoc - constitutional amendment", "Corporate Secretary", "Filed Special Resolutions"),
    "Enter each mortgage, registered security interest or debenture in the Register of Mortgages and Debentures within 14 days (Sec 155(1)(a)) and register the security as Sec 154 requires (Movable Property Security Interest Act or Deeds Registry); confirm any time limit under those Acts.": ("Debt/charge creation", "Legal/Secretariat", "Stamped Certificate of Mortgage/Charge"),
    "File the executed solvency certificates, filed special resolutions and registered security extracts.": ("Ad-hoc", "Legal/Secretariat", "Executed Solvency Certificates, Filed Special Resolutions & Registered Security Extracts"),
    "Check on the Registrar's online portal whether the company has already been re-registered; if not, confirm the current re-registration deadline, fee and document list with the Registrar (these have been extended and revised before). The Act's own period was twelve months from the effective date (Sec 303(9)); a company not re-registered is struck off (Sec 303(12)).": ("Engagement start", "Senior Secretariat", "Re-registration status note & confirmed deadline/fee"),
    "Run a Registrar search to confirm the company's registration number and status, and clear any outstanding annual returns before applying.": ("Engagement start", "Compliance Officer", "Registrar search report & up-to-date annual returns"),
    "Collect the existing registration documents: Certificate of Incorporation, Memorandum and Articles of Association, CR5, CR6 and the latest annual return.": ("Engagement start", "Assistant Secretary", "Scanned registration document set"),
    "Confirm and update the details of every director, secretary and shareholder (names, ID numbers, dates of birth, addresses, contact details) and reconcile them to the statutory registers and the Register of Beneficial Ownership (Sec 72).": ("Before application", "Compliance Officer", "Verified director/shareholder/beneficial owner schedule"),
    "Review the Memorandum and Articles against the COBE Act and prepare any amendments needed for approval by special resolution. Amendments follow the ordinary formalities (Secs 79, 81 and 175) and may not be made 'under the guise of' re-registration (Sec 303(11)).": ("Before application", "Legal/Secretariat", "Amended Memorandum & Articles (draft)"),
    "Prepare the board resolution and the members' special resolution approving re-registration, and the signed authority or affidavit the Registrar requires.": ("Before application", "Corporate Secretary", "Signed board resolution, special resolution & authority/affidavit"),
    "Submit the re-registration application on the Registrar's online portal, upload the supporting documents and pay the prescribed fee.": ("Documents signed and complete", "Compliance Officer", "Submitted application & fee receipt"),
    "Track the application with the Registrar and respond to any query or rejection until it is approved.": ("After submission", "Compliance Officer", "Registrar approval"),
    "File the new registration certificate, the Registrar's acknowledgment and the updated company documents on the client file, and update the statutory registers and governance calendar.": ("On receipt from Registrar", "Compliance Officer", "New registration certificate & updated company file"),
}


engagement_team = db.Table(
    "engagement_team",
    db.Column("engagement_id", db.Integer, db.ForeignKey("engagement.id"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
)


class MPSignedMixin:
    """The Managing Partner's e-signature on a document: who applied it, when, and a key of the content that was signed. The
    signature is only shown while the content still matches (see brand_docs.content_key / signature_valid), so changing a
    signed document removes the signature from it until it is signed again."""
    mp_signed_by_id = db.Column(db.Integer, nullable=True)  # a user id (no foreign key, so it cannot upset the models' own user relationships)
    mp_signed_at = db.Column(db.DateTime, nullable=True)
    mp_signed_key = db.Column(db.String(64), nullable=True)


class User(UserMixin, db.Model):
    # How recently a user must have been seen (see last_seen_at/_update_last_
    # seen in app.py) to count as "online now" for the Messages "who's in the
    # app" indicator (models.User.is_online) - requested by the user to show
    # which colleagues are currently active when composing/reading a
    # message. Wider than the once-a-minute throttle on updating
    # last_seen_at, so the throttle itself never makes someone look offline
    # early.
    ONLINE_THRESHOLD_MINUTES = 5

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="staff")  # staff | supervisor | partner | admin
    is_active_flag = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Stamped on every authenticated request (throttled - see app.py's
    # _update_last_seen), purely for the is_online presence indicator below.
    # Never a precise "last activity" audit log - just recent enough to
    # drive an online/offline dot.
    last_seen_at = db.Column(db.DateTime)
    # Two-factor sign-in (see two_factor.py). The secret is the authenticator-app key; recovery codes are kept only as hashes.
    totp_secret = db.Column(db.String(64))
    totp_enabled = db.Column(db.Boolean, default=False)
    totp_last_step = db.Column(db.BigInteger)          # newest 30-second step already used, so a code cannot be replayed
    recovery_hashes = db.Column(db.Text)               # JSON list of SHA-256 hashes of the unused recovery codes

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_flag

    @property
    def is_online(self):
        """True if this user has been seen within ONLINE_THRESHOLD_MINUTES -
        the Messages "who's in the app" indicator. A user who has never
        logged in (last_seen_at is None) is never online."""
        if not self.last_seen_at:
            return False
        return (datetime.utcnow() - self.last_seen_at).total_seconds() <= self.ONLINE_THRESHOLD_MINUTES * 60

    def __repr__(self):
        return f"<User {self.username}>"


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    address = db.Column(db.String(255))
    # The entity's main bankers, one per line (entered on the client page; shown on the Financial Statements' Client Details page and in note 1).
    bankers = db.Column(db.Text)
    # Presentation currency and share capital particulars (entered on the client page; used by the share capital and shareholder loan notes).
    reporting_currency = db.Column(db.String(8))
    share_class = db.Column(db.String(60))
    authorised_shares = db.Column(db.Integer)
    issued_shares = db.Column(db.Integer)
    share_par_value = db.Column(db.Float)
    shareholder_loan_terms = db.Column(db.Text)
    industry = db.Column(db.String(120))
    # Company registration number (e.g. a companies-office registration
    # number). Optional - not every client is a registered company - but
    # when it IS given, clients.py checks it against every other client's
    # company_number before saving, so the same company can't accidentally
    # be onboarded twice under two different client records.
    company_number = db.Column(db.String(80))
    # ZIMRA identifiers, captured at onboarding. Both optional (an individual
    # or a small client may have no VAT registration - a blank VAT number
    # simply means "not VAT registered"), and each is checked for duplicates
    # across clients in clients.py the same way company_number is. Shown on
    # the client page and on the Bill-to block of printed quotations/invoices.
    tin_number = db.Column(db.String(40))   # Taxpayer Identification Number (BP number)
    vat_number = db.Column(db.String(40))   # VAT registration number
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Who onboarded the client - they keep access to it even before any
    # engagement exists (see client_scope_ids). Blank on clients created before
    # this was recorded.
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    # The client's own logo (as opposed to the firm's, which is baked into
    # every generated document's letterhead - see workpapers._LOGO_PATH) -
    # used only on the Financial Statements cover page. Stored on disk under
    # Config.CLIENT_LOGOS_DATA_DIR, one file per client (replaced, not
    # accumulated, each time a new one is uploaded), same pattern as
    # CompanyDocument's own file storage.
    logo_filename = db.Column(db.String(255))
    # Colours typed for the Financial Statements cover (6-digit hex, no '#'); blank = take them from the logo (cover_art.py).
    cover_primary = db.Column(db.String(6))
    cover_accent = db.Column(db.String(6))

    # The decision to accept (or decline) the client, made once on the client's page after the Sanctions & AML vetting (d63). Pending /
    # Accepted / Accepted with conditions / Declined (CLIENT_ACCEPT_DECISIONS). NULL on a row from before this existed: app.py
    # _migrate_client_acceptance_decisions carries it over from the engagements' old per-engagement decisions at start-up.
    acceptance_decision = db.Column(db.String(30), default="Pending")
    acceptance_decision_notes = db.Column(db.Text)
    acceptance_decided_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    acceptance_decided_at = db.Column(db.DateTime)
    acceptance_decided_by = db.relationship("User", foreign_keys=[acceptance_decided_by_id])

    engagements = db.relationship("Engagement", backref="client", lazy=True, cascade="all, delete-orphan")

    @property
    def acceptance_status(self):
        return self.acceptance_decision or "Pending"

    @property
    def is_accepted_as_client(self):
        """True once the client has been accepted (with or without conditions) on the client's page. Every engagement that requires
        acceptance stays locked until then (models.engagement_acceptance_cleared)."""
        return self.acceptance_status in CLIENT_ACCEPT_CLEARING

    @property
    def has_tax_or_accounting_engagement(self):
        """Whether this client has ever had a Tax or Accounting engagement
        (Engagement.has_tax_module/has_accounting_module, checked across
        every engagement rather than only open ones) - gates whether the
        Tax & Accounting Filing Archive card (see TaxAccountingFilingDocument
        below) is shown on this client's page at all. Checked against every
        engagement rather than just currently-open ones because the archive
        is meant to keep clearance certificates/returns/schedules findable
        for a client across tax years, long after the engagement they were
        filed under has closed."""
        return any(e.has_tax_module or e.has_accounting_module for e in self.engagements)

    @property
    def has_tax_engagement(self):
        """Whether this client has at least one engagement with a Tax
        service switched on (see Engagement.has_tax_module) - narrower than
        has_tax_or_accounting_engagement above (Tax only, not Accounting).
        Used to gate whether QPD / provisional tax applies to this client at
        all - see qualifies_for_qpd below."""
        return any(e.has_tax_module for e in self.engagements)

    @property
    def qualifies_for_qpd(self):
        """Whether the Quarterly Payment Dates (QPD) / provisional tax
        module applies to this client. QPD is only relevant to clients with
        a Tax Services engagement ticked - but any QPD data already on file
        is grandfathered in even if that engagement is later removed or
        changes type, so historical work a preparer already did here is
        never hidden or orphaned."""
        return self.has_tax_engagement or bool(self.qpd_estimates)

    def __repr__(self):
        return f"<Client {self.name}>"


class Engagement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(60), nullable=False, default="Audit")
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
    def is_whistleblower_service(self):
        return self.type == WHISTLEBLOWER_SERVICE_TYPE

    @property
    def is_training_service(self):
        return self.type == TRAINING_SERVICE_TYPE

    @property
    def is_corporate_finance(self):
        return self.type in (CORPFIN_ENGAGEMENT_TYPE, LEGACY_CONSULTING_TYPE)

    @property
    def is_standalone_service(self):
        """True for the service lines that run in their own workspace
        (no audit planning/fieldwork/finalisation/tax-computation tabs):
        Whistle-blower, Training and Corporate Finance & Business Advisory
        (including engagements still carrying the old "Consulting" type)."""
        return self.type in (WHISTLEBLOWER_SERVICE_TYPE, TRAINING_SERVICE_TYPE, CORPFIN_ENGAGEMENT_TYPE, LEGACY_CONSULTING_TYPE)

    @property
    def has_asset_schedules(self):
        """Whether this engagement shows the Asset Register and Capital Allowances tabs: every engagement that keeps a client's
        accounts (so not Secretarial, Business Intelligence and IT, or the stand-alone service lines)."""
        return self.type not in ("Secretarial", "Business Intelligence and IT Engagements") and not self.is_standalone_service

    @property
    def has_tax_module(self):
        """Whether the Tax tab should appear on this engagement at all -
        either a dedicated Tax engagement type, or any Tax service
        explicitly turned on for another engagement type (e.g. tax
        compliance work bundled into an Audit engagement)."""
        return self.type in TAX_ENGAGEMENT_TYPES or bool(self.tax_service_list)

    @property
    def shows_vat_tab(self):
        """The VAT tab (invoice register and input/output tax working paper) is shown when a Tax service is on and the client has a VAT
        number; otherwise that register stays on the Tax tab."""
        return bool(self.has_tax_module and self.client is not None and (self.client.vat_number or "").strip())

    @property
    def is_tax_advisory_type(self):
        """A dedicated Tax Advisory engagement (or one of the two legacy tax
        types), as opposed to another engagement type that merely has a Tax
        service ticked. These get the lean tax tab set - no Analytical
        Review, Risk Assessment, Trial Balance or Substantive Procedures -
        and the tax-head Execution Plan sits on the Tax tab itself. It does
        get the Accounting tab (to capture a ledger or take in a trial
        balance), the Trial Balance tab (to import one) and its own Asset
        Register tab."""
        return self.type in TAX_ENGAGEMENT_TYPES

    @property
    def has_tax_advisory(self):
        """Whether the Tax tab's advisory-only sections (Technical Research
        Log, Structuring Options Analysis, Tax Dispute Management - Module
        7B) should be shown - only when the Tax Advisory or Representation
        service is actually on, never just because some other Tax service
        is."""
        services = self.tax_service_list
        return "advisory" in services or "representation" in services or self.type in TAX_ENGAGEMENT_TYPES

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
        return self.type == "Accounting & Bookkeeping" or self.is_tax_advisory_type or bool(self.accounting_service_list)

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


# ---------- Client access (which clients a person may see) ----------
# Staff and Supervisors only see the clients they actually work on: a client
# where they are the Partner, the Manager or a team member on at least one of
# its engagements, or that they onboarded themselves. Partners and Admins see
# every client. The same rule is applied everywhere client data appears (client
# list and pages, search, invoices, quotations, receipts, ZIMRA cases, QPD,
# payroll, documents and the client dropdowns) - see app._enforce_client_scope
# and the helpers below.
CLIENT_RESTRICTED_ROLES = ("staff", "supervisor")
