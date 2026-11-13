"""models, part 3 of 12.  Holds: ChecklistTemplate, ChecklistTemplateItem, Tickmark, FilingIndexSection, EngagementChecklistItem, RiskItem, Document, PermanentFileDocument, PermanentFileReferenceCounter, TaxAccountingFilingDocument, DocumentTemplate, EngagementTask ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

def client_scope_ids(user):
    """None if `user` may see every client; otherwise the set of client ids
    they work on (cached for the length of one web request)."""
    if user is None or getattr(user, "role", None) not in CLIENT_RESTRICTED_ROLES:
        return None
    cache = request.environ.setdefault("neverlank.client_scope", {}) if has_request_context() else None
    if cache is not None and user.id in cache:
        return cache[user.id]
    on_team = db.session.query(engagement_team.c.engagement_id).filter(engagement_team.c.user_id == user.id)
    ids = {cid for (cid,) in db.session.query(Engagement.client_id).filter(
        db.or_(Engagement.partner_id == user.id, Engagement.manager_id == user.id, Engagement.id.in_(on_team))).all()}
    ids |= {cid for (cid,) in db.session.query(Client.id).filter(Client.created_by_id == user.id).all()}
    if cache is not None:
        cache[user.id] = ids
    return ids


def user_can_access_client(user, client):
    """Whether `user` may see this client (a Client or a client id)."""
    scope = client_scope_ids(user)
    if scope is None:
        return True
    client_id = client.id if hasattr(client, "id") else client
    return client_id in scope


def visible_clients(user):
    """Every client `user` may see, by name - use for lists and dropdowns."""
    query = Client.query
    scope = client_scope_ids(user)
    if scope is not None:
        query = query.filter(Client.id.in_(scope)) if scope else query.filter(db.false())
    return query.order_by(Client.name).all()


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
    shipped - see Engagement.acceptance_required) is always cleared.

    An engagement that does require it clears the gate either of two ways,
    both needing the recorded decision to be "Accepted" (a declined
    acceptance, or one still awaiting a decision, always keeps the gate
    shut regardless of either flag below):
      - the full Engagement Partner sign-off (ClientAcceptance.
        partner_signed_at) - only possible once every item is complete; or
      - the Partner's narrower interim "authorise continuing with work"
        (ClientAcceptance.partner_authorized_continue_at) - for when the
        decision to accept is genuinely final but one item (typically the
        client's countersigned engagement letter) is still in process, so
        the team isn't blocked waiting on paperwork the Partner has already
        decided doesn't change the outcome.
    """
    if not engagement.acceptance_required:
        return True
    # d63: the decision to accept the CLIENT is made once on the client's page (after the Sanctions & AML vetting); until the client is
    # accepted (or accepted with conditions) no engagement for them is cleared, whatever its own checklists and sign-offs say.
    client = engagement.client
    if client is not None and not client.is_accepted_as_client:
        return False
    ca = engagement.client_acceptance
    if not ca or ca.decision != "Accepted":
        return False
    return bool(ca.partner_signed_at or ca.partner_authorized_continue_at)


class ChecklistTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    type = db.Column(db.String(60), nullable=False, default="Audit")
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

    @property
    def reviews(self):
        """Every dated review entry left against this checklist item - see
        WorkpaperReview. Purely additive alongside reviewed_by_id/at above
        (the single quick "mark as reviewed" toggle stays exactly as it
        was): this lets more than one reviewer - a Manager as well as a
        Supervisor, say - each leave their own note on the same item,
        without one overwriting the other. The Partner's own sign-off
        (partner_signed_by_id/at above) remains the ultimate, file-closing
        authority - this log never gates or replaces it."""
        return (
            WorkpaperReview.query
            .filter_by(reviewable_type=REVIEWABLE_TYPE_CHECKLIST_ITEM, reviewable_id=self.id)
            .order_by(WorkpaperReview.reviewed_at).all()
        )


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
    # True for a working paper made or opened in the app's own editor (Working Papers tab): it is edited in place, no download needed.
    in_app = db.Column(db.Boolean, default=False)
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
    and a P-code, not extraction.

    Two more things layered on top of that original design:

    1. Sub-references. A FilingIndexSection P-code (e.g. "P1000") names a
       whole category ("Incorporation & Statutory"), not one specific
       document - a client may have several actual documents that belong
       under it (certificate of incorporation, memorandum & articles, CR6,
       ...). Rather than every one of them sharing the literal code
       "P1000", each document filed under a P-code is given its own
       sequential sub-reference the moment it's filed - P1001, P1002,
       P1003, etc (see permanent_file._next_reference_number) - stored in
       reference_number below. Numbering is per client, per P-code, and
       (like the Filing Index's own codes) never reused even if a document
       is later deleted, so a deleted P1002 never gets silently reassigned
       to a different document later. reference_code is the computed
       display/filename form ("P1001"); a document filed with no P-code at
       all (filing_index_id is None) has no reference_code either.

    2. Auto-filing from Company Documents. Certain CompanyDocument types
       (certificate of incorporation, CR6, share register, etc - see
       models.COMPANY_DOCUMENT_TYPE_TO_FILING_CODE) are themselves exactly
       the kind of "continuing relevance" document the Permanent File
       exists for, so uploading one there also files a copy here
       automatically (see company_documents.upload_document /
       permanent_file.file_upload_to_permanent_file) - a preparer never has
       to upload the same file twice. source_company_document_id records
       where an auto-filed copy came from, purely for display ("auto-filed
       from Company Documents"); it's cleared (not cascaded) if that source
       document is later deleted, the same "detach, don't delete"
       treatment ClientKeyPerson.source_document_id gets - the Permanent
       File copy is independent and continues to exist once filed."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    filing_index_id = db.Column(db.Integer, db.ForeignKey("filing_index_section.id"))
    # The auto- or preparer-assigned sub-reference number under filing_index's
    # own code - see point 1 in the docstring above. e.g. 1001 under "P1000"
    # displays as "P1001" (see reference_code below).
    reference_number = db.Column(db.Integer)
    notes = db.Column(db.Text)
    version = db.Column(db.Integer, default=1)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Set only when this row was filed automatically from a CompanyDocument
    # upload - see point 2 in the docstring above.
    source_company_document_id = db.Column(db.Integer, db.ForeignKey("company_document.id"))

    client = db.relationship("Client", backref=db.backref("permanent_file_documents", lazy=True, order_by="PermanentFileDocument.uploaded_at.desc()", cascade="all, delete-orphan"))
    filing_index = db.relationship("FilingIndexSection")
    uploaded_by = db.relationship("User")
    source_company_document = db.relationship("CompanyDocument", backref=db.backref("permanent_file_entry", uselist=False))

    @property
    def reference_code(self):
        """"P1001"-style display/filename reference: the filing_index
        code's leading letter plus this document's own reference_number -
        see point 1 in the class docstring. Falls back to the bare
        filing_index code (e.g. "P1000") for an older row filed before
        sub-references existed (reference_number is None), and to None if
        no P-code was chosen at all."""
        if not self.filing_index:
            return None
        if self.reference_number:
            return f"{self.filing_index.code[0]}{self.reference_number}"
        return self.filing_index.code

    def __repr__(self):
        return f"<PermanentFileDocument {self.original_filename!r}>"


class PermanentFileReferenceCounter(db.Model):
    """Tracks the highest Permanent File sub-reference number ever handed
    out per (client, P-code) - see PermanentFileDocument's docstring, point
    1. Needed because PermanentFileDocument rows can be deleted: looking at
    MAX(PermanentFileDocument.reference_number) directly would happily go
    backwards and hand out an already-used number again once the document
    that had it is gone. This table remembers the high-water mark
    independently of which documents currently exist, so a number is never
    reused - the same principle the Filing Index's own codes follow (see
    FilingIndexSection's docstring), applied here to the per-client, per-
    code sequence underneath each one. Entirely internal bookkeeping - never
    shown in the UI, only read/written by
    permanent_file._next_reference_number."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    filing_index_id = db.Column(db.Integer, db.ForeignKey("filing_index_section.id"), nullable=False)
    last_number = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (db.UniqueConstraint("client_id", "filing_index_id", name="uq_permfile_refcounter_client_code"),)

    def __repr__(self):
        return f"<PermanentFileReferenceCounter client={self.client_id} filing_index={self.filing_index_id} last={self.last_number}>"


class TaxAccountingFilingDocument(db.Model):
    """One filed document in a client's Tax & Accounting Filing Archive - a
    Tax Clearance certificate, a filed Return, or a supporting Schedule (see
    FILING_ARCHIVE_SECTIONS above) - kept once per Client, exactly like
    PermanentFileDocument above, rather than tied to whichever Tax
    Compliance/Accounting & Bookkeeping engagement happened to be open when
    it was filed: clearance certificates and filed returns need to stay
    findable for a client across tax years and engagements, not just the one
    they happened to be filed under (see Client.has_tax_or_accounting_
    engagement, which gates whether this archive is even shown for a given
    client). tax_head (one of TAX_HEADS above) is optional - a Tax Clearance
    Certificate is usually issued covering all tax heads at once, and a
    Schedule may be a purely accounting one with no tax head at all - but is
    the natural way to categorise a Return, mirroring TaxReturnRecord's own
    tax_head field."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    section = db.Column(db.String(20), nullable=False)  # a FILING_ARCHIVE_SECTIONS key
    tax_head = db.Column(db.String(80))  # optional - one of TAX_HEADS, blank = not tax-head-specific
    title = db.Column(db.String(200))  # free description, e.g. "Debtors Schedule" - optional
    period_label = db.Column(db.String(80))  # e.g. "2026", "2026 Q1"
    reference = db.Column(db.String(100))  # ZIMRA clearance/acknowledgement/certificate number
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("filing_archive_documents", lazy=True, order_by="TaxAccountingFilingDocument.uploaded_at.desc()", cascade="all, delete-orphan"))
    uploaded_by = db.relationship("User")

    @property
    def section_label(self):
        return FILING_ARCHIVE_SECTION_LABELS.get(self.section, self.section)

    def __repr__(self):
        return f"<TaxAccountingFilingDocument {self.section} {self.original_filename!r}>"


class DocumentTemplate(db.Model):
    """A downloadable, fillable Word/Excel template in the Document Templates
    library (engagement letters, workpapers, etc). The actual file lives on
    disk under Config.DOCUMENT_TEMPLATES_DATA_DIR/<type>/<filename> - this
    row holds the metadata plus the filename to serve, and is what the
    in-app Edit/New/Delete screens modify.
    """
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(60), nullable=False, default="Audit")  # an ENGAGEMENT_TYPES value
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
    category = db.Column(db.String(60))  # one of TASK_CATEGORIES - see effective_task_priority
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

    # Billing: the invoice line this finished task was billed on (NULL = not
    # billed yet; the link clears itself if that line or its invoice is
    # deleted), and a "not billable" flag for completed work that should never
    # be invoiced (internal, covered by a fixed fee already billed, ...).
    invoice_line_id = db.Column(db.Integer, db.ForeignKey("invoice_line_item.id"))
    billing_waived = db.Column(db.Boolean, default=False, nullable=False, server_default="0")

    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    partner_signed_by = db.relationship("User", foreign_keys=[partner_signed_by_id])

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < date.today() and self.status != "Done")

    @property
    def invoice(self):
        """The invoice this task has been billed on, if any."""
        return self.invoice_line.invoice if self.invoice_line_id and self.invoice_line else None

    @property
    def is_ready_to_bill(self):
        """Finished, not yet invoiced and not marked not-billable."""
        return self.status == "Done" and self.invoice_line_id is None and not self.billing_waived

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def is_partner_signed(self):
        return self.partner_signed_by_id is not None


# Task categories that are the firm's own housekeeping rather than client work:
# their finished tasks are left out of "Ready to bill" unless asked for.
NON_BILLABLE_TASK_CATEGORIES = ("Admin Work", "Personal", "Other")


def ready_to_bill_tasks(engagement, include_internal=False):
    """Finished client work on `engagement` that has not been invoiced (and
    has not been marked not-billable), oldest completion first."""
    tasks = [
        t for t in engagement.tasks
        if t.is_ready_to_bill and (include_internal or (t.category or "") not in NON_BILLABLE_TASK_CATEGORIES)
    ]
    return sorted(tasks, key=lambda t: (t.completed_at or datetime.min, t.id))


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
    category = db.Column(db.String(60))  # one of TASK_CATEGORIES - see effective_task_priority
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


class PersonalSubtask(db.Model):
    """A smaller step under a general to-do (PersonalTask), each with its
    own deadline - e.g. a to-do "Prepare training day" might be broken into
    "Book venue" (due Mon), "Send invites" (due Wed), "Print handouts" (due
    Fri), so the work can be tracked piece by piece instead of one lump due
    date.

    A "pure task" in its own right for completion purposes: it carries the
    same To Do / In Progress / Review / Done status as every other task in
    the app (TASK_STATUSES), set independently of the parent PersonalTask's
    own status - moving a subtask to Done (or back) never touches the
    parent task's status, and the parent's status never touches a subtask's
    either. It's otherwise deliberately lighter than a full task - no
    assignee, priority or sign-off of its own, since it belongs to (and is
    only ever seen alongside) its parent."""
    id = db.Column(db.Integer, primary_key=True)
    personal_task_id = db.Column(db.Integer, db.ForeignKey("personal_task.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="To Do")
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    personal_task = db.relationship(
        "PersonalTask",
        backref=db.backref(
            "subtasks", lazy=True, cascade="all, delete-orphan",
            order_by="PersonalSubtask.due_date.asc().nullslast(), PersonalSubtask.id.asc()",
        ),
    )

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < date.today() and self.status != "Done")

    def __repr__(self):
        return f"<PersonalSubtask {self.id} {self.title!r}>"


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
    Procedures library ("the Firm Library") - same download/edit pattern as
    DocumentTemplate, but grouped by a free-standing category list rather
    than engagement type, and stored in its own data folder
    (Config.POLICIES_DATA_DIR).

    The fields below let this library also work as an AI-powered research
    hub, not just a static depository: text is extracted from the uploaded
    file (see file_text_extraction.py - PDF/Word/Excel/PowerPoint) and then
    summarised by AI (see policy_summary.py) into ai_summary/ai_key_points,
    the same "AI suggests, person confirms" pattern already used for
    Legislative Update Control (see LegislativeUpdate) - purely
    informational, never applied automatically. ai_suggested_category is
    only ever a suggestion shown on the add/edit form; the category a
    document actually lives under is always the person's own choice.
    Chunked (see policy_chunking.py, PolicyDocumentChunk) so "Ask the Firm
    Library" (policy_search.py) can retrieve the specific relevant part of a
    long policy rather than only ever seeing its first few pages. A document
    with no extractable text (an empty file, a slide deck of images only,
    or a legacy .doc/.xls/.ppt - see file_text_extraction.EXTRACTABLE_EXTENSIONS)
    is unaffected by any of this: it's simply not searched or summarised,
    same graceful-degradation principle as everywhere else AI is used in
    this app.
    """
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False, default="Other")
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    filename = db.Column(db.String(255), nullable=False)
    order = db.Column(db.Integer, default=0)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ---- Firm Library research hub: extraction + AI summary ----
    extracted_text = db.Column(db.Text)
    extraction_status = db.Column(db.String(20))  # "extracted", "no_text_found", "error", "unsupported_format" - see file_text_extraction.py
    page_count = db.Column(db.Integer)  # rough per-type analogue (sheet/slide count) - informational only
    text_source = db.Column(db.String(20))  # "scan:tesseract" / "scan:claude" when the text was read from a scanned page (ocr_reader.py); blank = the file's own text

    ai_summary = db.Column(db.Text)
    ai_key_points_json = db.Column(db.Text)  # JSON-encoded list of short bullet strings
    ai_suggested_category = db.Column(db.String(50))  # a suggestion only, shown on the form - never auto-applied
    ai_status = db.Column(db.String(20))  # "done", "not_configured", "error" - see policy_summary.py
    ai_error = db.Column(db.Text)
    ai_processed_at = db.Column(db.DateTime)

    updated_by = db.relationship("User")

    @property
    def file_ext(self):
        return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""

    @property
    def ai_key_points(self):
        try:
            return json.loads(self.ai_key_points_json) if self.ai_key_points_json else []
        except (TypeError, ValueError):
            return []

    def set_ai_key_points(self, value):
        self.ai_key_points_json = json.dumps(value or [])

    def __repr__(self):
        return f"<PolicyDocument {self.title}>"


class PolicyDocumentChunk(db.Model):
    """One section-sized slice of a filed policy/procedure's extracted_text
    (see policy_chunking.py, which reuses legislation_chunking.chunk_text) -
    the Firm Library research hub's counterpart to LegislativeUpdateChunk,
    letting "Ask the Firm Library" (policy_search.py) retrieve the specific
    relevant part of a long document rather than only ever seeing a
    fixed-size excerpt from its start. Chunked once per document (at
    filing/edit time, or lazily the first time it's needed for an
    already-filed document) and never re-chunked automatically afterwards.
    Purely a search index over text that's already filed - deleting the
    parent PolicyDocument deletes its chunks with it (cascade below);
    nothing here is ever shown to a person directly, only used internally
    as retrieval context."""
    __tablename__ = "policy_document_chunk"
    id = db.Column(db.Integer, primary_key=True)
    policy_document_id = db.Column(db.Integer, db.ForeignKey("policy_document.id"), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False)  # 0-based order within the document
    heading = db.Column(db.String(300))  # the nearest Part/Chapter/numbered-section heading, if any was detected
    text = db.Column(db.Text, nullable=False)

    policy_document = db.relationship(
        "PolicyDocument",
        backref=db.backref("chunks", lazy=True, cascade="all, delete-orphan", order_by="PolicyDocumentChunk.chunk_index"),
    )

    def __repr__(self):
        return f"<PolicyDocumentChunk {self.policy_document_id}#{self.chunk_index}>"


class PolicyAskCache(db.Model):
    """A fast-recall cache of "Ask the Firm Library" question/answer pairs
    (see policy_search.py) - the Firm Library's counterpart to
    LegislativeAskCache, same verbatim-recall design: asking the same or a
    very similar question again returns the earlier answer instantly and
    consistently instead of re-searching and re-calling the AI, while a
    genuinely new question is still answered fresh from the filed material
    every time. A cached answer never feeds into, or changes the answer to,
    any OTHER question, and is always visibly flagged as reused wherever
    it's shown. Only "done" and "no_answer" results are ever cached - a
    transient failure, an unconfigured API key, or "nothing filed matches at
    all" is never cached, so those always retry fresh."""
    __tablename__ = "policy_ask_cache"
    id = db.Column(db.Integer, primary_key=True)
    question_normalized = db.Column(db.String(500), nullable=False, index=True)
    question_original = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False)  # "done" or "no_answer" only
    answer = db.Column(db.Text, nullable=False)
    citation_ids_json = db.Column(db.Text)  # JSON list of PolicyDocument ids cited (status "done")
    candidate_ids_json = db.Column(db.Text)  # JSON list of PolicyDocument ids considered but not sufficient (status "no_answer")
    asked_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    hit_count = db.Column(db.Integer, default=1)
    last_hit_at = db.Column(db.DateTime)

    asked_by = db.relationship("User", foreign_keys=[asked_by_id])

    def __repr__(self):
        return f"<PolicyAskCache {self.question_original!r} ({self.status})>"


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
    def leave_entries(self):
        return [e for e in self.entries if e.leave_type_id]

    @property
    def total_leave_days(self):
        return round(sum(e.leave_days or 0 for e in self.entries if e.leave_type_id), 2)

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    def __repr__(self):
        return f"<TimeSheet {self.user_id} w/o {self.week_start}>"


class TimeEntry(db.Model):
    """One day's entry on a TimeSheet - EITHER hours worked (the original
    purpose: `hours` set, `leave_type_id`/`leave_days` left blank) OR leave
    taken (`leave_type_id`/`leave_days` set instead, `hours` left at 0) -
    never both on the same entry. A day with both hours worked and leave
    taken (e.g. a half day) is two separate entries for that same
    `work_date`, exactly like two different engagements worked in one day
    are already two separate entries today."""
    id = db.Column(db.Integer, primary_key=True)
    timesheet_id = db.Column(db.Integer, db.ForeignKey("time_sheet.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    description = db.Column(db.String(255))
    hours = db.Column(db.Float, default=0)
    # Leave taken on this day - see LeaveType/LeaveBalance below and
    # hr.add_time_entry, which is what actually moves days out of the
    # employee's LeaveBalance.taken_days when one of these entries is added
    # (and back when it's deleted).
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_type.id"))
    leave_days = db.Column(db.Float)

    engagement = db.relationship("Engagement")
    leave_type = db.relationship("LeaveType")

    @property
    def is_leave(self):
        return self.leave_type_id is not None

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


class CheckInRecord(db.Model):
    """One check-in/check-out pair, logged via the "Check In"/"Check Out"
    button in the top bar (available on every page, next to the user's
    name). On check-out, the elapsed time is used to add (or top up) a
    TimeEntry on that day's TimeSheet automatically - same self-service,
    reviewer-approved flow as an entry typed in by hand on the Time Sheets
    page, just with the hours worked out from the clock instead of typed in.
    A user can only have one OPEN record (check_out_at is None) at a time;
    checking in again while already checked in is a no-op, and checking out
    with nothing open is a no-op - both just flash a message rather than
    erroring.

    time_entry_id is left blank when check-out happens for a week whose
    timesheet is already Approved (locked) - the check-in/check-out itself
    is still recorded, but nothing is added to the timesheet until a
    reviewer reopens it and the hours are added by hand, exactly like a
    manual entry attempted against an Approved timesheet."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)  # calendar day this belongs to (= check_in_at's date)
    check_in_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    check_out_at = db.Column(db.DateTime)
    time_entry_id = db.Column(db.Integer, db.ForeignKey("time_entry.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])
    time_entry = db.relationship("TimeEntry", foreign_keys=[time_entry_id])

    @property
    def is_open(self):
        return self.check_out_at is None

    @property
    def elapsed_hours(self):
        """Hours between check-in and check-out, rounded to 2dp. None while
        still checked in (no check-out yet)."""
        if not self.check_out_at:
            return None
        return round((self.check_out_at - self.check_in_at).total_seconds() / 3600, 2)

    def __repr__(self):
        return f"<CheckInRecord user={self.user_id} {self.work_date}>"
