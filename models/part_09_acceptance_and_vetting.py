"""models, part 9 of 12.  Holds: ClientAcceptanceChecklistItem, CompanyDocument, ClientKeyPerson, EntityPublicResearch, SanctionsScreening, ClientSanctionsScreening, ClientVettingDocument, SanctionsWatchlistEntry, SanctionsListStatus, RegulatoryNotice, LegislativeUpdateClientLink, FinalisationChecklist ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

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
    ("KYC & Ultimate Beneficial Ownership", "Who are the Ultimate Beneficial Owners (UBOs) - natural persons holding more than 20% of the shares or voting rights, able to appoint or remove a majority of the directors, or otherwise in significant control (COBE Act Sec 2)?"),
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


# Seeded instead of DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS when the engagement's type is "Tax Advisory" (see acceptance.seed_acceptance_checklist).
# Section names match the five Tax Client Acceptance sections that REPLACE the generic narrative sections for this engagement type (see
# ClientAcceptance.tax_kyc_satisfactory etc. and items_complete above). They are questions to work through, not statements of law.
TAX_ACCEPTANCE_CHECKLIST_ITEMS = [
    ('Taxpayer Identity, KYC & Registrations', "Has the client's Taxpayer Identification Number (TIN) been obtained and checked against its ZIMRA registration documents?"),
    ('Taxpayer Identity, KYC & Registrations', 'Which taxes is the client registered for (income tax, VAT, PAYE, withholding tax, other) - are the registration numbers on file, correct and current?'),
    ('Taxpayer Identity, KYC & Registrations', 'Who are the directors, beneficial owners and authorised signatories, and has their identity been verified?'),
    ('Taxpayer Identity, KYC & Registrations', 'Has the Sanctions & AML Vetting below been completed for the client, its directors and its beneficial owners?'),
    ('Taxpayer Identity, KYC & Registrations', "Who in the client's business will give us instructions and information, and do they have authority to bind the client?"),
    ('Tax Compliance History & Standing', 'Does the client hold a current tax clearance certificate? If not, why not?'),
    ('Tax Compliance History & Standing', 'Are any returns (income tax, VAT, PAYE, provisional tax) outstanding or filed late, and for which periods?'),
    ('Tax Compliance History & Standing', 'Are there open ZIMRA audits, assessments, objections, appeals or payment plans, and what stage has each reached?'),
    ('Tax Compliance History & Standing', 'What penalties and interest has ZIMRA charged in the last three years, and what caused them?'),
    ('Tax Compliance History & Standing', 'Who was the previous tax adviser, why is the client changing, and has the client allowed us to ask for the handover?'),
    ('Tax Compliance History & Standing', "Are the client's books and records good enough to support the returns or advice for the periods in question?"),
    ('Scope & Nature of Tax Work', 'Which tax services are requested (compliance, advisory and planning, health check or due diligence, representation) and for which tax heads and periods?'),
    ('Scope & Nature of Tax Work', 'Is the scope clear and in writing, and are the deadlines realistic against the ZIMRA due dates?'),
    ('Scope & Nature of Tax Work', 'Will we rely on information supplied by the client without verifying it, and has the client accepted responsibility for its completeness and accuracy?'),
    ('Scope & Nature of Tax Work', 'For planning or structuring advice: are the facts, the documents and the proposed transaction available to us in enough detail?'),
    ('Scope & Nature of Tax Work', 'Are we expected to represent the client before ZIMRA, and is the authority to do so in place?'),
    ('Scope & Nature of Tax Work', 'Are there USD and ZWG (ZiG) amounts that need an exchange rate treatment agreed with the client?'),
    ('Integrity & Tax Risk Appetite', 'How would the client describe its appetite for tax risk, and has it asked for a position we would not be comfortable defending?'),
    ('Integrity & Tax Risk Appetite', 'Is there any sign of undisclosed income, unrecorded sales or understated VAT or PAYE?'),
    ('Integrity & Tax Risk Appetite', 'Does any arrangement seem to have tax saving as its main purpose rather than a commercial one?'),
    ('Integrity & Tax Risk Appetite', 'If errors are found in earlier returns, is the client willing to correct them and make a voluntary disclosure?'),
    ('Integrity & Tax Risk Appetite', 'Are there cross-border or related-party transactions, and is there support for the pricing?'),
    ('Integrity & Tax Risk Appetite', 'Does the client operate in a cash-intensive or high-risk sector?'),
    ('Conflicts, Independence & Competence', "Do we act for another party to the same transaction, or for a party whose interests conflict with this client's?"),
    ('Conflicts, Independence & Competence', "Does the firm also audit or review this client's financial statements? If so, have the threats (self-review, advocacy) been assessed and the safeguards recorded?"),
    ('Conflicts, Independence & Competence', 'Does the team have the technical knowledge of the tax heads involved, and the time to meet the deadlines?'),
    ('Conflicts, Independence & Competence', 'Is our professional indemnity cover suitable for the nature and size of the advice?'),
    ('Conflicts, Independence & Competence', 'Is the fee arrangement free of any fee that depends on the tax result and could threaten our objectivity?'),
    ('Engagement letter', 'Does the engagement letter set out the tax services, tax heads, periods, deliverables, deadlines and what is excluded?'),
    ('Engagement letter', "Does it state the client's responsibility to give us complete and accurate information and to pay tax on time?"),
    ('Engagement letter', 'Does it record our authority to deal with ZIMRA for the client, where that is part of the work?'),
    ('Engagement letter', "Does it set the fee basis and say that our advice is for the client's own use and based on the law and facts at its date?"),
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
# and so is Sanctions & AML Vetting (ClientSanctionsScreening), which applies to
# every engagement for the client. See company_documents.py and
# entity_extraction.py for the upload/AI-extraction routes and logic.

COMPANY_DOCUMENT_TYPES = [
    "Certificate of Incorporation",
    "Memorandum & Articles of Association",
    "CR6 - Return of Directors",
    "CR5 - Notice of Situation of Registered Office",
    "Company Summary",
    "Share Register / Share Certificates",
    "CR8 - Special Resolution",
    "CR9 - Share Transfer Form",
    "CR9 - Share Buy Back",
    "CR11 - Allotment of Shares",
    "CR17 & Annual Return",
    "Beneficial Ownership Declaration",
    "Director/Shareholder ID Documents",
    "Other",
]

# Document types that are filed (Company Documents list and Permanent File) but NOT read for people. A share transfer form names
# the person giving up shares as well as the person receiving them; suggesting both as shareholders would put someone who no longer
# holds shares on the list, so a person updates the shareholders by hand from it (the transferee takes the shares, the transferor's
# holding falls or ends).
COMPANY_DOCUMENT_TYPES_NOT_READ_FOR_PEOPLE = {"CR8 - Special Resolution", "CR9 - Share Transfer Form", "CR9 - Share Buy Back", "CR11 - Allotment of Shares", "CR17 & Annual Return"}
# Why each is not read, and the badge shown on the list: (badge, why). CR8, CR11 and CR17 (d61) are filed the same way as the CR9: a resolution may
# remove or appoint people, an allotment adds to what existing members hold, and the AGM declaration is signed by whoever were directors that day,
# so suggesting everyone named as a director or shareholder could put the wrong people on the list. A person updates the key people by hand.
COMPANY_DOCUMENT_NOT_READ_NOTES = {
    "CR8 - Special Resolution": ("Filed - not read for people", "a resolution can appoint, remove or name people, so reading it could put the wrong people on the list"),
    "CR9 - Share Transfer Form": ("Filed - update shareholders by hand", "a transfer names the person giving up shares as well as the one receiving them"),
    "CR9 - Share Buy Back": ("Filed - update shareholders by hand", "a buy back cancels shares held by the person selling them, so the register needs a person's judgement"),
    "CR11 - Allotment of Shares": ("Filed - update shareholders by hand", "an allotment adds to what members already hold, so the register needs a person's judgement"),
    "CR17 & Annual Return": ("Filed - not read for people", "it is signed by whoever were directors that day, which may not be today's directors"),
}

# Which Permanent File (P-series) FilingIndexSection a Company Document is
# automatically filed under the moment it's uploaded (see
# company_documents.upload_document / permanent_file.
# file_upload_to_permanent_file) - so a preparer never has to upload the
# same incorporation document twice, once here and once to the Permanent
# File below. Matches the firm's own Filing Index typical_contents for each
# P-code (see seed.FILING_INDEX_PERMANENT_SEED): statutory
# incorporation/registry documents go to P1000 "Incorporation & Statutory",
# documents about who holds/controls the company go to P5000 "Structure &
# Governance". "Other" is deliberately left unmapped (None) - a
# miscellaneous upload is left for a preparer to file by hand if it belongs
# in the Permanent File at all, rather than guessing a P-code for it. If the
# matching code hasn't been seeded/exists yet on a given install, auto-
# filing is simply skipped - this never blocks the Company Document upload
# itself.
COMPANY_DOCUMENT_TYPE_TO_FILING_CODE = {
    "Certificate of Incorporation": "P1000",
    "Memorandum & Articles of Association": "P1000",
    "CR6 - Return of Directors": "P1000",
    "CR5 - Notice of Situation of Registered Office": "P1000",
    "Company Summary": "P1000",
    "Share Register / Share Certificates": "P5000",
    "CR8 - Special Resolution": "P1000",
    "CR9 - Share Transfer Form": "P5000",
    "CR9 - Share Buy Back": "P5000",
    "CR11 - Allotment of Shares": "P5000",
    "CR17 & Annual Return": "P1000",
    "Beneficial Ownership Declaration": "P5000",
    "Director/Shareholder ID Documents": "P5000",
    "Other": None,
}

PERSON_ROLES = ["Director", "Shareholder", "Beneficial Owner", "Company Secretary", "Other"]
PERSON_STATUSES = ["Suggested", "Confirmed"]


class CompanyDocument(db.Model):
    """One company registration document (certificate of incorporation,
    CR6, share register, etc.) uploaded once against a Client and reused
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
    # Screening now lives on the Client (see ClientSanctionsScreening below) because it
    # applies to every engagement. Rows here are the old per-engagement records; once one
    # has been merged into its client's list this is set so the migration never repeats.
    migrated_to_client = db.Column(db.Boolean, default=False)

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


class ClientSanctionsScreening(db.Model):
    """Sanctions, PEP and AML vetting of one key individual (director, beneficial owner,
    authorised signatory, or - on an Investigative Engagement - a suspect/target/key witness),
    kept on the CLIENT rather than on an engagement: the vetting applies to every engagement the
    firm does for the client, so it is done once and shown wherever it matters (the client's
    page, and as a summary on each engagement's Acceptance tab). Same five lists and the same
    conservative automatic matching as the old per-engagement SanctionsScreening (which this
    replaces; its rows were merged into here on upgrade)."""
    __tablename__ = "client_sanctions_screening"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    individual_name = db.Column(db.String(200), nullable=False)
    role_description = db.Column(db.String(150))

    un_result = db.Column(db.String(20), default="Not Checked")
    ofac_result = db.Column(db.String(20), default="Not Checked")
    eu_result = db.Column(db.String(20), default="Not Checked")
    rbz_result = db.Column(db.String(20), default="Not Checked")
    fiu_result = db.Column(db.String(20), default="Not Checked")

    notes = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)

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

    client = db.relationship(
        "Client",
        backref=db.backref("sanctions_screenings", lazy=True, cascade="all, delete-orphan", order_by="ClientSanctionsScreening.order"),
    )
    screened_by = db.relationship("User", foreign_keys=[screened_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    auto_screened_by = db.relationship("User", foreign_keys=[auto_screened_by_id])

    # identical behaviour to the legacy model
    auto_notes_for = SanctionsScreening.auto_notes_for
    is_screened = SanctionsScreening.is_screened
    overall_status = SanctionsScreening.overall_status

    def __repr__(self):
        return f"<ClientSanctionsScreening {self.individual_name!r} status={self.overall_status!r}>"


class ClientVettingDocument(db.Model):
    """Documentation kept with the Sanctions & AML vetting on the client's page: screening print-outs, identity and PEP checks, source of funds,
    adverse media searches, the Partner/MLRO's note. Stored beside the company documents. Filed once per client, whatever engagements follow."""
    __tablename__ = "client_vetting_document"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    doc_type = db.Column(db.String(60), default="Other")
    title = db.Column(db.String(200), nullable=False)
    individual_name = db.Column(db.String(200))  # who it relates to, if one person
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(300), nullable=False)
    notes = db.Column(db.Text)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("vetting_documents", lazy=True, cascade="all, delete-orphan", order_by="ClientVettingDocument.uploaded_at.desc()"))
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])


def screening_assessment_for(individuals):
    """Plain-language, advisory-only read of a client's vetting list (see ClientSanctionsScreening).
    A confirmed hit on even one list, for even one individual, is the most severe outcome."""
    individuals = list(individuals)
    total = len(individuals)
    if total == 0:
        return {"label": "No key individuals have been added for sanctions and AML vetting on the client's page yet.", "level": "muted", "hits": 0, "potential": 0, "outstanding": 0, "total": 0}
    hits = sum(1 for i in individuals if i.overall_status == "Confirmed Hit")
    potential = sum(1 for i in individuals if i.overall_status == "Potential Match")
    outstanding = sum(1 for i in individuals if not i.is_screened)
    s = "s" if total != 1 else ""
    if hits:
        label, level = f"{hits} of {total} individual{s} returned a confirmed hit - escalate to the Partner/MLRO before proceeding.", "danger"
    elif potential:
        label, level = f"{potential} of {total} individual{s} returned a potential match - resolve before proceeding.", "warning"
    elif outstanding:
        label, level = f"{outstanding} of {total} individual{s} not yet screened against all five lists.", "warning"
    else:
        label, level = f"All {total} individual{s} screened clear against the UN, OFAC, EU, RBZ and FIU lists.", "success"
    return {"label": label, "level": level, "hits": hits, "potential": potential, "outstanding": outstanding, "total": total}



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
    text_source = db.Column(db.String(20))  # "scan:tesseract" / "scan:claude" when read from a scanned page (ocr_reader.py)
    notes = db.Column(db.Text)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploaded_by = db.relationship("User")

    @property
    def file_ext(self):
        return self.original_filename.rsplit(".", 1)[-1] if self.original_filename and "." in self.original_filename else ""

    def __repr__(self):
        return f"<RegulatoryNotice {self.source} {self.title!r}>"


class LegislativeUpdateClientLink(db.Model):
    """Tags a LegislativeUpdate (see the existing LegislativeUpdate model
    and its new filing/AI-summary fields, above in the "Firm-wide
    Legislative Update Control (Module 8)" section) as relevant to a
    specific Client - "taggable relevance": filing an update doesn't push
    anything to anyone by itself, a person decides which clients it
    actually affects (see tax.py's link_client_to_update/unlink_client
    routes), and each tagged client then shows it on their own page (see
    clients/detail.html's "Relevant Legislative Updates" card). From there,
    a Tax Advisory engagement can pull the update's AI summary straight
    into its Technical Research Log (see tax.py's
    import_research_log_entry_from_legislative_update)."""
    id = db.Column(db.Integer, primary_key=True)
    legislative_update_id = db.Column(db.Integer, db.ForeignKey("legislative_update.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    notes = db.Column(db.Text)
    linked_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    linked_at = db.Column(db.DateTime, default=datetime.utcnow)

    legislative_update = db.relationship(
        "LegislativeUpdate",
        backref=db.backref("client_links", lazy=True, cascade="all, delete-orphan", order_by="LegislativeUpdateClientLink.linked_at.desc()"),
    )
    client = db.relationship(
        "Client",
        backref=db.backref("legislative_update_links", lazy=True, cascade="all, delete-orphan", order_by="LegislativeUpdateClientLink.linked_at.desc()"),
    )
    linked_by = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("legislative_update_id", "client_id", name="uq_legislative_update_client"),)

    def __repr__(self):
        return f"<LegislativeUpdateClientLink update={self.legislative_update_id} client={self.client_id}>"


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
    ("Registry & Reconciliations", "Final registry search made (Form CR35, application to inspect entity documents and registers) to verify all lodgments processed during the period reflect on the public register? [SEC-FIN-01]"),
    ("Registry & Reconciliations", "Annual return reconciled and stamped - filed within 21 days of the anniversary of incorporation, registration or re-registration (Sec 165(2); the Fourth Schedule form, which has no CR number on the Registrar's list), and the AGM held within twelve months of the last one (Sec 167(2)) with Form CR17 (declaration that the AGM was held) lodged where the Registrar asks for it? For a company other than a private company, check the statutory report went in on Form CR23 within one month of being certified (Sec 166(5)). [SEC-FIN-03]"),
    ("Registry & Reconciliations", "Director/Secretary changes confirmed - all appointments, resignations, or address changes lodged on Form CR6 within one month (Sec 217(4)-(5))? [SEC-FIN-04]"),
    ("Registry & Reconciliations", "Registered office and postal address changes verified on Form CR5 (Notice of Registered Office) under Sec 31? [SEC-FIN-05]"),
    ("Registry & Reconciliations", "Share allotments verified on Form CR11 (Return of Allotments), lodged within one month of each allotment under Sec 121?"),
    ("Registry & Reconciliations", "Company Summary - confirmed with the Registrar whether it is the same document as the annual return (Sec 165(1) calls the annual return a summary) or a separate return, and filed on the current form and deadline (not mentioned in the COBE Act as supplied)?"),
    ("Board & Governance Audit Trail", "Board/AGM minutes signed and bound - all draft minutes from Board, Committee, and General Meetings reviewed, signed by the Board Chairperson, and entered into the master minute book (Sec 180 for members' meetings; Sec 205 for the board)? [SEC-FIN-02]"),
    ("Board & Governance Audit Trail", "Special resolutions indexed and filed - Form CR8 returns bear CIPO stamp confirmations for all Special Resolutions passed during the year, each lodged within one month of being passed (Sec 175 & 178)? [SEC-FIN-07]"),
    ("Board & Governance Audit Trail", "Board action tracker closed out - a final summary of all action items compiled, completed tasks noted and unresolved matters rolled into the next period's planning cycle?"),
    ("UBO & Capital Reconciliation", "UBO register and declarations reconciled - the Register of Beneficial Owners (Sec 72) reconciled and Form CR16 (Beneficial Ownership Declaration) confirmed submitted, with updates within seven days of any material change, for any natural person holding more than 20% voting rights, direct/indirect interest, or control? [SEC-FIN-06]"),
    ("UBO & Capital Reconciliation", "Register of Members and capital structure reconciled - share certificates issued or transferred (Sec 151 & 159) reconciled against authorised nominal share capital changes filed via Form CR10 (Registration of any increase in shares, within one month, Sec 141) or Form CR9 (Conversion/Buy-Back of Shares, Sec 140)?"),
    ("UBO & Capital Reconciliation", "Register of Mortgages and Debentures reconciled - all charges, mortgages, or satisfactions created or discharged during the year (Sec 154-155) matched against the entries in the register, and Form CR12 (notice of the place where the register is kept, or of a change to it, within one month - Sec 155(3)) lodged if the register is not at the registered office?"),
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
    ("it_audit_report_summary", "IT & Cyber Assurance Report - Executive Summary of Findings & Recommendations", "it_audit_report"),
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
    "it_audit_report_summary": "\n".join([
        "[Summarise, in a few sentences, the scope (Cyber Security, Information Security, IT/ICT, AML/CFT), the overall control environment observed, the key findings and their significance, and the overall conclusion - the detail behind each point is set out in the sections that follow.]",
    ]),
    # The Business Intelligence and IT Engagements variant of "rep_letter"
    # above - see default_workpaper_narrative_body below for why this
    # engagement type needs its own set of management representations
    # rather than the financial-statements-worded default (which refers to
    # "the financial statements", "fair value", "uncorrected misstatements",
    # none of which apply to an IT & Cyber Assurance engagement).
    "rep_letter_business_it": "\n".join([
        "We have fulfilled our responsibilities for establishing and maintaining an effective system of IT governance, cyber security and information security controls relevant to the scope of this engagement.",
        "We have disclosed to you all known cyber security incidents, data breaches, and material control weaknesses identified during the period, whether or not previously reported to a regulator.",
        "We have provided you with full and unrestricted access to system and security logs, source code repositories, system configuration documentation, database schemas, and all other records and information relevant to your procedures.",
        "We have disclosed to you all known instances of non-compliance or suspected non-compliance with applicable AML/CFT laws and regulations, and with other laws and regulations whose effects should be considered in the scope of this engagement.",
        "The access controls, change and release management, and data protection policies and procedures described to you are the same as those actually in operation during the period.",
        "Related party access privileges and any use of privileged/administrative access outside the normal course of business have been appropriately disclosed.",
        "We have disclosed to you the identity of all known regulatory findings, notices or correspondence (POTRAZ, RBZ, FIU or equivalent) relevant to the scope of this engagement.",
        "There have been no irregularities involving management or employees who have a significant role in IT, security or AML/CFT controls that could have a material effect on the control environment reported on.",
    ]),
    # The Accounting & Bookkeeping variant of "rep_letter" above - this
    # engagement type is a compilation/write-up engagement, not an audit or
    # review (see financials.DEFAULT_DIRECTORS_STATEMENT_TEXT_ACCOUNTING for
    # the same idea applied to the Directors' Statement), so the ISA 700
    # audit-worded default ("financial statements", "fair value",
    # "uncorrected misstatements") doesn't fit here either. Covers the four
    # work areas an Accounting & Bookkeeping engagement can involve -
    # Financial Accounting, Management Accounting, Cost Accounting and Tax
    # (see models.ACCOUNTING_SERVICES/has_tax_module).
    "rep_letter_accounting": "\n".join([
        "We have provided you with all accounting records, source documents, bank statements, invoices and other information relevant to the preparation of the financial, management and cost accounting records and reports covered by this engagement.",
        "The financial information provided is complete and accurately reflects all transactions of the entity for the period, to the best of our knowledge and belief.",
        "We acknowledge that this is a compilation/bookkeeping engagement and that Neverlank Chartered Accountants has not performed an audit or review, and accordingly no assurance is expressed on the financial or management information prepared.",
        "We are responsible for the proper maintenance of accounting records, for Financial Accounting, Management Accounting, Cost Accounting and Tax compliance matters, and for the safeguarding of the entity's assets.",
        "We have disclosed to you all known instances of non-compliance or suspected non-compliance with tax laws and other regulations relevant to the entity's accounting and tax obligations.",
        "Related party transactions and balances have been identified and disclosed to you.",
        "All known liabilities, whether billed or unbilled, and commitments have been disclosed to you for inclusion in the accounting records.",
        "There have been no irregularities involving management or employees with a significant role in the accounting or financial reporting process.",
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


def default_workpaper_narrative_body(kind, engagement=None):
    """Picks the default starting wording for a narrative workpaper - same
    as looking it up in DEFAULT_WORKPAPER_NARRATIVE_BODIES directly, except
    for "rep_letter" on a Business Intelligence and IT Engagements or an
    Accounting & Bookkeeping engagement, each of which gets its own
    tailored wording above instead of the financial-statements/audit-worded
    default (see financials.default_directors_statement_text for the same
    idea applied to the Directors' Statement)."""
    if kind == "rep_letter" and engagement and engagement.type == "Business Intelligence and IT Engagements":
        return DEFAULT_WORKPAPER_NARRATIVE_BODIES.get("rep_letter_business_it", "")
    if kind == "rep_letter" and engagement and engagement.type == "Accounting & Bookkeeping":
        return DEFAULT_WORKPAPER_NARRATIVE_BODIES.get("rep_letter_accounting", "")
    return DEFAULT_WORKPAPER_NARRATIVE_BODIES.get(kind, "")


class WorkpaperNarrative(MPSignedMixin, db.Model):
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
