"""Wording of the Secretarial module that was corrected against the Companies and Other Business Entities Act
[Chapter 24:31] (COBE Act) - see cobe_checks.py for the reasons.

Each list holds (old text, new text) pairs. The constants in models.py and seed.py already carry the NEW wording; these
pairs let migrate_cobe_wording() (seed.py) bring rows already stored in an existing database up to date. A row is changed
only when its text still equals the OLD text exactly, so anything a staff member has edited by hand is left alone. Running
it again once everything is updated does nothing.
"""

# SecretarialEntityUnderstandingChecklistItem rows (EntityUnderstandingChecklistItem.item_text)
ENTITY_PAIRS = [
    ("What are the specific rules and thresholds set out in the Articles regarding board powers, borrowing limits (Section 196), execution of documents, and pre-emption rights?",
     "What are the specific rules and thresholds set out in the Articles regarding board powers, borrowing limits, execution of documents (Section 35), and pre-emption rights (Sections 99 and 139)?"),
    ("Does a private company meet the minimum statutory director requirements under Section 195 (minimum 2 directors if under 10 members; minimum 3 directors if over 10 members)?",
     "Does the company meet the director requirements of Section 195 (a private company with more than one and fewer than ten shareholders: at least two directors; ten or more shareholders: at least three; a public company: seven to fifteen), including at least one director ordinarily resident in Zimbabwe (Section 195(2))?"),
    ("For public companies, does the board meet the requirement of 7 to 15 directors, including at least 3 independent non-executive directors (Section 206)?",
     "For public companies, does the board have 7 to 15 directors (Section 195(1)), including at least three non-executive or independent directors (Section 206(2))?"),
    ("Does the company maintain an up-to-date Register of Beneficial Ownership under Section 72, identifying natural persons who directly or indirectly hold 20% or more of shares or voting rights?",
     "Does the company maintain an up-to-date Register of Beneficial Owners under Section 72 (kept where the register of members is kept, with a custodian resident in Zimbabwe), identifying natural persons who directly or indirectly hold MORE than 20% of the shares or voting rights, can appoint or remove a majority of the directors, or otherwise exercise significant control (Section 2), and are updates filed with the Registrar within seven days of a material change?"),
    ("Does any nominee shareholding comply with the statutory cap under Section 72, which restricts nominee holdings to no more than 20% of shares on behalf of a beneficial owner?",
     "Does any nominee shareholding comply with Section 73, which allows shares to be held through a nominee only where the beneficial owner (with associates) holds less than 20%?"),
    ("Are the Register of Members (Section 157), Register of Directors and Secretaries (Section 215), Register of Directors' Interests and Conflicts (Section 211), and Register of Mortgages and Debentures/Charges (Section 162) maintained at the registered office and reconciled?",
     "Are the Register of Members (Section 159), Register of Directors and Secretaries (Section 217), Register of Allotments (Section 121), Register of Mortgages and Debentures (Section 155) and, for a company other than a private company, the Register of Directors' Share Holdings (Section 213) maintained at the registered office and reconciled?"),
    ("Before making any distribution, share buyback, or financial assistance, has the board formally evaluated and certified that the company meets the statutory Solvency and Liquidity Test (Section 102)?",
     "Before making any distribution, has the board authorised it and acknowledged by resolution that the Solvency and Liquidity Test is met (Sections 102 and 138); and before a share buy-back or financial assistance for the purchase of shares (Sections 128-129 and 123), has a general meeting authorised it in advance?"),
    ("Are there any proposed structural changes or class variations that might trigger minority shareholder appraisal remedies (Section 143 or Section 228)?",
     "Are there any proposed structural changes or class variations that might trigger minority shareholder appraisal remedies (Section 233, which applies to a variation of class rights under Section 143 and a merger under Section 228)?"),
    ("Are all loans, guarantees, or security provided by the company for directors or officers fully disclosed and approved under Section 216?",
     "Are all loans, guarantees, or security provided by the company for directors or officers within the exceptions to the prohibition in Section 208 (for a private company, with the consent of holders of nine-tenths of the shares) and disclosed in the accounts as Section 216 requires?"),
    ("Is the board aligned with Section 196, which prohibits directors from acting or voting by proxy at board meetings?",
     "Does the board act only at meetings (in person, or by electronic or conference means where the articles allow it) or by written consent signed by ALL directors entitled to vote, as Section 196 provides, applying the board quorum in the articles (Section 204)?"),
    ("Are minute books kept up-to-date for all board meetings, committee meetings, and AGMs/EGMs, capturing key commercial decisions, risk evaluations, and conflict disclosures?",
     "Are minute books kept up-to-date for all board meetings, committee meetings, and AGMs/EGMs, capturing key commercial decisions, risk evaluations, and conflict disclosures (Section 180 for members' meetings, prepared within 20 days; Section 205 for the board and committees)?"),
    ("Is the company compliant with filing its Annual Return (Section 165) within the statutory timeframe following its financial year-end?",
     "Is the company compliant with filing its Annual Return within 21 days of the anniversary of its incorporation, registration or re-registration (Section 165(2)), with the private-company certificate where it applies (Section 165(3))?"),
    ("Have changes in directors/secretaries, registered office address, or share capital updates been lodged with the Registrar within the required statutory notification periods?",
     "Have changes in directors/secretaries (within one month, Section 217), the registered office or postal address (Section 31), share allotments (one month, Section 121), capital increases (one month, Section 141) and special resolutions (one month, Section 178) been lodged with the Registrar within the periods the Act sets?"),
    ("Has the entity completed its mandatory administrative re-registration process under the COBE Act framework?",
     "Has the entity completed its mandatory administrative re-registration process under the COBE Act (Section 303(9) and the Tenth Schedule; a company that did not re-register in time is struck off, Section 303(12))?"),
    ("If not yet re-registered, what is the Registrar's current re-registration deadline and fee (both have been extended and revised before), and has the board been told that a company not re-registered by the deadline risks being treated as non-operational and struck off the register?",
     "If not yet re-registered, what is the Registrar's current re-registration deadline and fee (both have been extended and revised before; the Act's own period was twelve months from the effective date, Section 303(9)), and has the board been told that a company not re-registered by the deadline risks being treated as non-operational and struck off the register (Section 303(12))?"),
    ("Do the Memorandum and Articles need to be amended to conform with the COBE Act, and will the members approve those amendments by special resolution as part of the re-registration?",
     "Do the Memorandum and Articles need to be amended to conform with the COBE Act, and will the members approve those amendments by special resolution (Sections 79, 81 and 175)? Section 303(11) does not allow such changes 'under the guise of' re-registration: they follow the ordinary formalities, before, after or together with it."),
]

# Entity-understanding questions added by this update (section, text, activity keys). Appended to existing Secretarial
# engagements' checklists (only those whose activities match, or all if none were chosen) by migrate_cobe_wording().
NEW_ENTITY_ITEMS = [
    ("Constitutional Documents & Corporate Governance",
     "Does the company have at least one company secretary who is ordinarily resident in Zimbabwe (Section 198(1)); for a public company, is the secretary qualified under Section 198(4) and not holding another office; and does the secretary certify in the annual financial statements whether the returns and notices required by the Act have been filed (Section 198(3)(f))?",
     ("directorship", "incorporation", "statutory_records")),
    ("Statutory Registers & Ownership Transparency",
     "Has the company appointed a person resident in Zimbabwe as custodian of the register of beneficial owners, and are the name and contact details recorded in the form filed with the Registrar (Section 72(4)-(5))?",
     ("statutory_records", "share_capital")),
    ("Capital Structure, Distributions & Solvency",
     "Is the register of allotments kept at the registered office, and is the return of allotments (with the contract, for shares not paid in cash) lodged with the Registrar within one month of each allotment (Section 121)?",
     ("share_capital",)),
    ("Statutory Filings & Registry Compliance",
     "Is the date of incorporation, registration or re-registration recorded on the Secretarial Engagement Plan, so the annual return can be diarised for 21 days after each anniversary (Section 165(2)), and has an AGM been held within the last twelve months (Section 167(2))?",
     ("incorporation", "statutory_records", "meetings")),
]

# SubstantiveProcedureItem rows of a Secretarial Execution Plan:
# (old text, new text, old trigger, new trigger, old target output, new target output). Trigger/output are changed only if
# they still equal the old values.
EXEC_PAIRS = [
    ("Reconcile the Memorandum and Articles of Association for borrowing limits (Sec 196) and share capital authorisation.",
     "Reconcile the Memorandum and Articles of Association for any borrowing limits and for share capital authorisation (Sec 96).",
     None, None, None, None),
    ("Issue formal Notice of AGM (Sec 170) at least 21 days prior to the meeting.",
     "Issue formal Notice of AGM at least 21 days prior to the meeting (Sec 169(1)); a meeting to pass a special resolution also needs 21 days (Sec 175(1)).",
     None, None, None, None),
    ("Attend meetings, record proceedings, and produce draft minute books (Sec 176) within 5 working days post-meeting.",
     "Attend meetings, record proceedings, and produce draft minutes within 5 working days post-meeting (the Act allows 20 days for members' meetings, Sec 180; board minutes, Sec 205).",
     None, None, None, None),
    ("Lodge the Annual Return (Form CR11) with the Registrar within 42 days of the AGM (Sec 165).",
     "Lodge the Annual Return with the Registrar within 21 days of the anniversary of the company's incorporation, registration or re-registration (Sec 165(2)), with the private-company certificate where it applies (Sec 165(3)).",
     "Post-AGM", "Anniversary of incorporation / registration / re-registration (+21 days)",
     "Stamped Form CR11 receipt", "Stamped annual return receipt"),
    ("File Form CR6 for any change in directors or secretaries within 14 days (Sec 215).",
     "File Form CR6 for any change in directors or secretaries within one month (Sec 217(4)-(5)).",
     None, None, None, None),
    ("Lodge Form CR5 for changes in registered office address within 14 days (Sec 160).",
     "Record any change of registered office or postal address in the constitutive documents and notify the Registrar on Form CR5 (Sec 31; the Act sets no number of days).",
     None, None, None, None),
    ("File returns of allotment of shares within statutory timelines following board approval (Sec 93 & 95).",
     "File the return of allotments with the Registrar within one month of each allotment (Sec 121(2)).",
     None, None, None, None),
    ("Lodge the Company Summary return introduced under the new COBE Act - reconfirm the prescribed form reference and filing deadline with the Registrar, as these were not yet confirmed when this item was added.",
     "Confirm with the Registrar whether the 'Company Summary' return is the same document as the annual return (Sec 165(1) calls the annual return a summary) or a separate return, and reconfirm its form reference and filing deadline - it is not mentioned in the COBE Act as supplied.",
     "Annual - reconfirm trigger/deadline (new COBE requirement)", "Annual - reconfirm trigger/deadline",
     None, None),
    ("Maintain and update the Register of Beneficial Ownership (Sec 72), verifying no unapproved nominee shareholding exceeds the 20% statutory cap.",
     "Maintain and update the Register of Beneficial Owners (Sec 72), file updates with the Registrar within seven days of a material change (Sec 72(2)), and verify any nominee holds only for a beneficial owner with less than 20% (Sec 73(2)(a)).",
     "Annual review / update within 14 days of change", "Annual review / update within 7 days of a material change",
     None, None),
    ("Update the Register of Members (Sec 157) following executed share transfers (Sec 149).",
     "Update the Register of Members (Sec 159) following executed share transfers made on a proper instrument of transfer (Sec 151).",
     None, None, None, None),
    ("Reconcile the Register of Charges and Mortgages (Sec 162) against outstanding corporate debt facilities.",
     "Reconcile the Register of Mortgages and Debentures (Sec 155) against outstanding corporate debt facilities; each entry is due within 14 days of the hypothecation.",
     None, None, None, None),
    ("Coordinate board evaluation and drafting of the statutory Solvency and Liquidity Test Certificate (Sec 102) prior to executing distributions or share buybacks (Sec 104).",
     "Coordinate board evaluation and drafting of the statutory Solvency and Liquidity Test acknowledgment (Secs 102 and 138) prior to executing distributions, and obtain general-meeting authority before any share buy-back (Secs 128-129).",
     None, None, None, None),
    ("Draft special resolutions for constitutional amendments (Sec 16 & 25) or company name changes.",
     "Draft special resolutions for constitutional amendments (memorandum Sec 79, articles Sec 81) or a change of name (Sec 26, with the Registrar's written approval first), give 21 days' notice and lodge a copy with the Registrar within one month (Secs 175 and 178).",
     None, None, None, None),
    ("File mortgage/debenture registration forms with the Registrar within 30 days of creation (Sec 163).",
     "Enter each mortgage, registered security interest or debenture in the Register of Mortgages and Debentures within 14 days (Sec 155(1)(a)) and register the security as Sec 154 requires (Movable Property Security Interest Act or Deeds Registry); confirm any time limit under those Acts.",
     None, None, None, None),
    ("Check on the Registrar's online portal whether the company has already been re-registered; if not, confirm the current re-registration deadline, fee and document list with the Registrar (these have been extended and revised before).",
     "Check on the Registrar's online portal whether the company has already been re-registered; if not, confirm the current re-registration deadline, fee and document list with the Registrar (these have been extended and revised before). The Act's own period was twelve months from the effective date (Sec 303(9)); a company not re-registered is struck off (Sec 303(12)).",
     None, None, None, None),
    ("Review the Memorandum and Articles against the COBE Act and prepare any amendments needed for approval by special resolution.",
     "Review the Memorandum and Articles against the COBE Act and prepare any amendments needed for approval by special resolution. Amendments follow the ordinary formalities (Secs 79, 81 and 175) and may not be made 'under the guise of' re-registration (Sec 303(11)).",
     None, None, None, None),
]

# Tasks added to the Execution Plan by this update (area, text, (trigger, role, output)). sync_substantive_procedures adds
# them on its own; they are listed in models.SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES.

# ChecklistTemplateItem / EngagementChecklistItem rows made from seed.SECRETARIAL_PROGRAM
PROGRAM_PAIRS = [
    ("Complete KYC and identify the ultimate beneficial owners of the client (natural persons holding 20% or more of shares or voting rights).",
     "Complete KYC and identify the ultimate beneficial owners of the client (natural persons holding more than 20% of shares or voting rights, or otherwise in significant control - Sec 2)."),
    ("Review the Memorandum and Articles for authorised share classes, board powers, borrowing limits (Sec 196) and pre-emption rights.",
     "Review the Memorandum and Articles for authorised share classes, board powers, borrowing limits and pre-emption rights (Secs 99 and 139)."),
    ("Confirm the board meets the minimum director requirements for the company type (Sec 195; public companies Sec 206).",
     "Confirm the board meets the director requirements for the company type, including one director ordinarily resident in Zimbabwe (Sec 195; public companies Sec 206)."),
    ("Confirm the company secretary is appointed and the appointment is lodged with the Registrar.",
     "Confirm the company secretary is appointed, is ordinarily resident in Zimbabwe, is qualified if the company is public, and that the appointment is notified to the Registrar within one month (Secs 198 and 217)."),
    ("Confirm directors' interests, conflicts and loans to directors or officers are disclosed and approved (Secs 211 and 216).",
     "Confirm directors' conflicts of interest (Secs 56-57) are disclosed and loans to directors or officers are within the exceptions in Sec 208 and disclosed as Sec 216 requires."),
    ("Update and reconcile the Register of Members (Sec 157) to executed share transfers and allotments.",
     "Update and reconcile the Register of Members (Sec 159) to executed share transfers and allotments."),
    ("Update the Register of Directors and Secretaries (Sec 215) and the Register of Directors' Interests (Sec 211).",
     "Update the Register of Directors and Secretaries (Sec 217) and, for a company other than a private company, the Register of Directors' Share Holdings (Sec 213)."),
    ("Update the Register of Beneficial Ownership (Sec 72) and confirm no nominee holding exceeds the 20% cap.",
     "Update the Register of Beneficial Owners (Sec 72), file updates within seven days of a material change, and confirm nominee holdings meet Sec 73(2)(a)."),
    ("Update the Register of Mortgages, Debentures and Charges (Sec 162) and reconcile it to outstanding facilities.",
     "Update the Register of Mortgages and Debentures within 14 days of each hypothecation (Sec 155) and reconcile it to outstanding facilities."),
    ("Issue the Notice of AGM at least 21 days before the meeting (Sec 170) with proxy forms and the financial statements.",
     "Issue the Notice of AGM at least 21 days before the meeting (Sec 169(1)) with proxy forms and the financial statements."),
    ("Attend meetings, record proceedings and issue draft minutes within 5 working days (Sec 176).",
     "Attend meetings, record proceedings and issue draft minutes within 5 working days (the Act allows 20 days for members' meetings, Sec 180; board minutes Sec 205)."),
    ("Lodge the Annual Return (Form CR11) with the Registrar within 42 days of the AGM (Sec 165).",
     "Lodge the Annual Return with the Registrar within 21 days of the anniversary of the company's incorporation, registration or re-registration (Sec 165(2))."),
    ("File Form CR6 within 14 days of any change in directors or secretaries (Sec 215).",
     "File Form CR6 within one month of any change in directors or secretaries (Sec 217)."),
    ("File Form CR5 within 14 days of any change of registered office (Sec 160).",
     "Record and notify any change of registered office or postal address on Form CR5 (Sec 31; the Act sets no number of days)."),
    ("Lodge the Company Summary return required under the COBE Act - reconfirm the prescribed form and deadline with the Registrar.",
     "Confirm whether the Company Summary return is the same as the annual return (Sec 165(1)) or a separate return, and reconfirm its prescribed form and deadline with the Registrar."),
    ("File returns of allotment after board approval of any share issue (Secs 93 and 95).",
     "File the return of allotments within one month of any share issue (Sec 121)."),
    ("Process share transfers and update the Register of Members (Sec 149).",
     "Process share transfers on a proper instrument of transfer and update the Register of Members (Secs 151 and 159)."),
    ("Before any distribution, buyback or financial assistance, obtain the board's Solvency and Liquidity Test certificate (Secs 102 and 104).",
     "Before any distribution, obtain the board's resolution acknowledging the Solvency and Liquidity Test (Secs 102 and 138); a buy-back also needs general-meeting authority (Secs 128-129)."),
    ("Draft and file special resolutions for constitutional amendments or name changes (Secs 16 and 25).",
     "Draft and file special resolutions for constitutional amendments or name changes (Secs 79, 81, 26, 175 and 178); a copy goes to the Registrar within one month."),
    ("Register mortgages and debentures with the Registrar within 30 days of creation (Sec 163).",
     "Enter mortgages, security interests and debentures in the register within 14 days and register the security as the Act directs (Secs 154-155)."),
    ("Check on the Registrar's online portal whether the company has already been re-registered; if not, confirm the current deadline, fee and document list with the Registrar.",
     "Check on the Registrar's online portal whether the company has already been re-registered; if not, confirm the current deadline, fee and document list with the Registrar (the Act's own period was twelve months from the effective date, Sec 303(9))."),
]

# Tasks added to SECRETARIAL_PROGRAM by this update are not back-filled onto existing checklists.

# AnalyticalReviewLine.label ("<section> - <point>")
ANALYTICAL_PAIRS = [
    ("Ownership & Capital Structure Analytics - Share allotment vs. authorised capital ratios - variance check between issued share capital, share premium, and authorised limits in the Memorandum/Articles before processing new allotments (Section 93 & 95).",
     "Ownership & Capital Structure Analytics - Share allotment vs. authorised capital ratios - variance check between issued share capital, share premium, and authorised limits in the Memorandum/Articles before processing new allotments (Sections 96 and 121)."),
    ("Ownership & Capital Structure Analytics - Beneficial ownership threshold analysis - analyse voting rights/shareholding aggregation to identify individuals crossing the 20% beneficial ownership threshold (Section 72).",
     "Ownership & Capital Structure Analytics - Beneficial ownership threshold analysis - analyse voting rights/shareholding aggregation to identify individuals holding more than 20% of the shares or voting rights (Sections 2 and 72)."),
    ("Statutory Filing & Timeline Variance Analysis - Annual Return vs. financial year-end deadlines - track AGM dates against the statutory requirement (within 6 months of financial year-end) and Annual Return submission deadlines (Section 165).",
     "Statutory Filing & Timeline Variance Analysis - Annual Return and AGM timing - track AGM dates against the statutory requirement (once in every twelve months, Section 167(2)) and Annual Return filing against the 21 days after the anniversary of incorporation, registration or re-registration (Section 165(2))."),
    ("Charge & Encumbrance Reconciliation - Debt/security analytics - compare total registered mortgages, charges and debentures in the Register of Charges (Section 162) against outstanding borrowing facilities to identify unregistered or unfiled charges (Section 163-164).",
     "Charge & Encumbrance Reconciliation - Debt/security analytics - compare total registered mortgages, charges and debentures in the Register of Mortgages and Debentures (Section 155) against outstanding borrowing facilities to identify unrecorded or unregistered security (Sections 154-155)."),
]

# ClientAcceptanceChecklistItem.item_text
ACCEPTANCE_PAIRS = [
    ("Who is the Ultimate Beneficial Owner (UBO) holding more than 20-25% of shares or voting rights?",
     "Who are the Ultimate Beneficial Owners (UBOs) - natural persons holding more than 20% of the shares or voting rights, able to appoint or remove a majority of the directors, or otherwise in significant control (COBE Act Sec 2)?"),
]

# FinalisationChecklistItem.item_text
FINAL_PAIRS = [
    ("Annual/Statutory Return reconciled and stamped - Form CR23 (Statutory/Annual Return) and Form CR17 (Declaration of AGM) filed under Sec 165/166? [SEC-FIN-03]",
     "Annual return reconciled and stamped - filed within 21 days of the anniversary of incorporation, registration or re-registration (Sec 165(2)), and the AGM held within twelve months of the last one (Sec 167(2))? Form CR23 / Form CR17 references to be confirmed against the Registrar's current forms. [SEC-FIN-03]"),
    ("Director/Secretary changes confirmed - all appointments, resignations, or address changes lodged on Form CR6 under Sec 217/241? [SEC-FIN-04]",
     "Director/Secretary changes confirmed - all appointments, resignations, or address changes lodged on Form CR6 within one month (Sec 217(4)-(5))? [SEC-FIN-04]"),
    ("Registered office changes verified on Form CR5 (Notice of Registered Office) under Sec 240? [SEC-FIN-05]",
     "Registered office and postal address changes verified on Form CR5 (Notice of Registered Office) under Sec 31? [SEC-FIN-05]"),
    ("Share allotments verified on Form CR11 (Return of Allotments) under Sec 93 & 95?",
     "Share allotments verified on Form CR11 (Return of Allotments), lodged within one month of each allotment under Sec 121?"),
    ("Company Summary filed with the Registrar under the new COBE Act - form reference and filing deadline reconfirmed against current CIPO guidance (newly introduced requirement, not yet finalised when this item was added)?",
     "Company Summary - confirmed with the Registrar whether it is the same document as the annual return (Sec 165(1) calls the annual return a summary) or a separate return, and filed on the current form and deadline (not mentioned in the COBE Act as supplied)?"),
    ("Board/AGM minutes signed and bound - all draft minutes from Board, Committee, and General Meetings reviewed, signed by the Board Chairperson, and entered into the master minute book (Sec 170 & 176)? [SEC-FIN-02]",
     "Board/AGM minutes signed and bound - all draft minutes from Board, Committee, and General Meetings reviewed, signed by the Board Chairperson, and entered into the master minute book (Sec 180 for members' meetings; Sec 205 for the board)? [SEC-FIN-02]"),
    ("Special resolutions indexed and filed - Form CR8 returns bear CIPO stamp confirmations for all Special Resolutions passed during the year (Sec 176 & 178)? [SEC-FIN-07]",
     "Special resolutions indexed and filed - Form CR8 returns bear CIPO stamp confirmations for all Special Resolutions passed during the year, each lodged within one month of being passed (Sec 175 & 178)? [SEC-FIN-07]"),
    ("UBO register and declarations reconciled - the confidential Register of Beneficial Ownership (Sec 72) reconciled and Form CR16 (Beneficial Ownership Declaration) confirmed submitted for any natural person holding more than 20% voting rights, direct/indirect interest, or control? [SEC-FIN-06]",
     "UBO register and declarations reconciled - the Register of Beneficial Owners (Sec 72) reconciled and Form CR16 (Beneficial Ownership Declaration) confirmed submitted, with updates within seven days of any material change, for any natural person holding more than 20% voting rights, direct/indirect interest, or control? [SEC-FIN-06]"),
    ("Register of Members and capital structure reconciled - share certificates issued or transferred (Sec 149 & 157) reconciled against authorised nominal share capital changes filed via Form CR10 (Increase of Nominal Capital) or Form CR9 (Conversion/Buy-Back of Shares)?",
     "Register of Members and capital structure reconciled - share certificates issued or transferred (Sec 151 & 159) reconciled against authorised nominal share capital changes filed via Form CR10 (Increase of Nominal Capital, within one month of the special resolution, Sec 141) or Form CR9 (Conversion/Buy-Back of Shares, Sec 140)?"),
    ("Register of Charges and Debentures reconciled - all charges, mortgages, or satisfactions created or discharged during the year (Sec 162-164) matched against filings on Form CR12 (Satisfaction/Register of Mortgages & Charges)?",
     "Register of Mortgages and Debentures reconciled - all charges, mortgages, or satisfactions created or discharged during the year (Sec 154-155) matched against filings on Form CR12 (Satisfaction/Register of Mortgages & Charges)?"),
    # d25: the Registrar's list of forms (CR1 to CR35) was supplied, so the labels below are corrected.
    # Each starts from the wording as delivered in d24, so a row already migrated is updated again.
    ('Final registry search executed via Form CR2 (Search Application) to verify all lodgments processed during the period reflect on the public register? [SEC-FIN-01]',
     'Final registry search made (Form CR35, application to inspect entity documents and registers) to verify all lodgments processed during the period reflect on the public register? [SEC-FIN-01]'),
    ("Annual return reconciled and stamped - filed within 21 days of the anniversary of incorporation, registration or re-registration (Sec 165(2)), and the AGM held within twelve months of the last one (Sec 167(2))? Form CR23 / Form CR17 references to be confirmed against the Registrar's current forms. [SEC-FIN-03]",
     "Annual return reconciled and stamped - filed within 21 days of the anniversary of incorporation, registration or re-registration (Sec 165(2); the Fourth Schedule form, which has no CR number on the Registrar's list), and the AGM held within twelve months of the last one (Sec 167(2)) with Form CR17 (declaration that the AGM was held) lodged where the Registrar asks for it? For a company other than a private company, check the statutory report went in on Form CR23 within one month of being certified (Sec 166(5)). [SEC-FIN-03]"),
    ('Register of Mortgages and Debentures reconciled - all charges, mortgages, or satisfactions created or discharged during the year (Sec 154-155) matched against filings on Form CR12 (Satisfaction/Register of Mortgages & Charges)?',
     'Register of Mortgages and Debentures reconciled - all charges, mortgages, or satisfactions created or discharged during the year (Sec 154-155) matched against the entries in the register, and Form CR12 (notice of the place where the register is kept, or of a change to it, within one month - Sec 155(3)) lodged if the register is not at the registered office?'),
    ('Register of Members and capital structure reconciled - share certificates issued or transferred (Sec 151 & 159) reconciled against authorised nominal share capital changes filed via Form CR10 (Increase of Nominal Capital, within one month of the special resolution, Sec 141) or Form CR9 (Conversion/Buy-Back of Shares, Sec 140)?',
     'Register of Members and capital structure reconciled - share certificates issued or transferred (Sec 151 & 159) reconciled against authorised nominal share capital changes filed via Form CR10 (Registration of any increase in shares, within one month, Sec 141) or Form CR9 (Conversion/Buy-Back of Shares, Sec 140)?'),
]
