"""models, part 7 of 12.  Holds: ZimraCase, ZimraCorrespondence, ZimraInstalment, ZimraReply, LegislativeUpdate, StatutoryDeadline, LegislativeUpdateChunk, LegislativeProvision, LegislativeAskCache, FirmLesson, TaxChecklistItem, GLAccount ....

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

class ZimraCase(db.Model):
    """One ZIMRA audit / query / assessment / objection matter for a client."""
    __tablename__ = "zimra_case"
    id = db.Column(db.Integer, primary_key=True)
    case_number = db.Column(db.String(30), unique=True, nullable=False)  # ZC-YYYY-NNNN
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"))
    title = db.Column(db.String(200), nullable=False)
    case_type = db.Column(db.String(60), default="Desk audit")
    tax_head = db.Column(db.String(80))
    period_from = db.Column(db.Date)
    period_to = db.Column(db.Date)
    zimra_reference = db.Column(db.String(100))
    zimra_officer = db.Column(db.String(150))
    opened_on = db.Column(db.Date)
    status = db.Column(db.String(30), nullable=False, default="Open")
    closed_on = db.Column(db.Date)
    description = db.Column(db.Text)
    currency = db.Column(db.String(3), default="USD")
    amount_assessed = db.Column(db.Float)      # tax assessed by ZIMRA
    penalties_interest = db.Column(db.Float)   # penalties and interest raised
    amount_agreed = db.Column(db.Float)        # amount agreed / settled / finally payable
    amount_paid = db.Column(db.Float)          # paid to date
    outcome = db.Column(db.Text)
    # payment plan and Tax Clearance Certificate
    plan_status = db.Column(db.String(20))
    plan_notes = db.Column(db.String(300))
    tcc_status = db.Column(db.String(20))
    tcc_number = db.Column(db.String(60))
    tcc_expiry = db.Column(db.Date)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("zimra_cases", lazy=True))
    engagement = db.relationship("Engagement", backref=db.backref("zimra_cases", lazy=True))
    created_by = db.relationship("User")
    correspondence = db.relationship("ZimraCorrespondence", backref="case", cascade="all, delete-orphan",
                                     order_by="ZimraCorrespondence.corr_date.desc(), ZimraCorrespondence.id.desc()")
    instalments = db.relationship("ZimraInstalment", backref="case", cascade="all, delete-orphan",
                                  order_by="ZimraInstalment.due_date")

    @property
    def is_closed(self):
        return self.status == "Closed"

    @property
    def open_deadlines(self):
        """Incoming letters that still need a reply, soonest deadline first."""
        items = [c for c in self.correspondence if c.awaiting_response]
        return sorted(items, key=lambda c: c.response_due)

    @property
    def overdue_deadlines(self):
        return [c for c in self.open_deadlines if c.is_overdue]

    @property
    def next_deadline(self):
        items = self.open_deadlines
        return items[0] if items else None

    @property
    def overdue_instalments(self):
        return [i for i in self.instalments if i.is_overdue]

    @property
    def balance(self):
        """Still to pay: agreed amount (or assessed + penalties if nothing is agreed) less paid."""
        if self.amount_agreed is not None:
            due = self.amount_agreed
        elif self.amount_assessed is not None or self.penalties_interest is not None:
            due = (self.amount_assessed or 0.0) + (self.penalties_interest or 0.0)
        else:
            return None
        return round(due - (self.amount_paid or 0.0), 2)

    @property
    def period_label(self):
        if self.period_from and self.period_to:
            return f"{self.period_from.strftime('%b %Y')} - {self.period_to.strftime('%b %Y')}"
        if self.period_from or self.period_to:
            return (self.period_from or self.period_to).strftime('%b %Y')
        return None

    def __repr__(self):
        return f"<ZimraCase {self.case_number} {self.status}>"


class ZimraCorrespondence(db.Model):
    """One letter, notice, response, meeting or call on a ZIMRA case. An
    incoming item can carry a response deadline; it stays on the deadline
    list until it is marked as responded to."""
    __tablename__ = "zimra_correspondence"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("zimra_case.id"), nullable=False)
    corr_date = db.Column(db.Date, nullable=False)
    direction = db.Column(db.String(30), nullable=False, default="Incoming from ZIMRA")
    kind = db.Column(db.String(50))
    subject = db.Column(db.String(250), nullable=False)
    reference = db.Column(db.String(100))
    notes = db.Column(db.Text)
    response_due = db.Column(db.Date)
    responded_on = db.Column(db.Date)
    original_filename = db.Column(db.String(300))
    stored_filename = db.Column(db.String(300))
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploaded_by = db.relationship("User")

    @property
    def awaiting_response(self):
        return bool(self.response_due and not self.responded_on and not self.case.is_closed)

    @property
    def is_overdue(self):
        return bool(self.awaiting_response and self.response_due < date.today())

    @property
    def days_left(self):
        return (self.response_due - date.today()).days if self.response_due else None


class ZimraInstalment(db.Model):
    """One instalment of an agreed ZIMRA payment plan."""
    __tablename__ = "zimra_instalment"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("zimra_case.id"), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    paid_on = db.Column(db.Date)
    paid_amount = db.Column(db.Float)

    @property
    def is_paid(self):
        return self.paid_on is not None

    @property
    def is_overdue(self):
        return bool(not self.paid_on and self.due_date < date.today() and not self.case.is_closed)


ZIMRA_REPLY_STATUSES = ["Draft", "Awaiting approval", "Approved", "Sent"]
ZIMRA_SEND_METHODS = ["Hand delivery", "Email", "ZIMRA online portal", "Courier / post", "Other"]


class ZimraReply(MPSignedMixin, db.Model):
    """A letter to ZIMRA drafted in the app: usually the reply to a letter logged on the case. It is written from a
    template, submitted for a Partner's approval, downloaded on the firm letterhead and, once it has gone, marked as
    sent - which logs it as an outgoing letter on the case and clears the deadline of the letter it answers."""
    __tablename__ = "zimra_reply"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("zimra_case.id"), nullable=False)
    in_reply_to_id = db.Column(db.Integer, db.ForeignKey("zimra_correspondence.id"))   # the ZIMRA letter being answered
    correspondence_id = db.Column(db.Integer, db.ForeignKey("zimra_correspondence.id"))  # the outgoing entry created when sent
    template_key = db.Column(db.String(40))
    letter_date = db.Column(db.Date)
    our_reference = db.Column(db.String(100))
    addressee = db.Column(db.Text)
    salutation = db.Column(db.String(100), default="Dear Sir/Madam")
    subject = db.Column(db.String(300), nullable=False, default="")
    body = db.Column(db.Text, nullable=False, default="")
    signatory = db.Column(db.String(150))
    signatory_title = db.Column(db.String(200))
    status = db.Column(db.String(20), nullable=False, default="Draft")
    events = db.Column(db.Text)  # JSON list of {at, by, what, note}
    # JSON, set when the text was first drafted by AI (see zimra_ai.py): model, who/when, the drafter's instructions,
    # positions taken, sources, reviewer notes, checks flagged, the text it replaced (prev_body) and who checked it.
    ai_meta = db.Column(db.Text)
    returned_note = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    submitted_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_at = db.Column(db.DateTime)
    sent_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    sent_on = db.Column(db.Date)
    sent_method = db.Column(db.String(40))

    case = db.relationship("ZimraCase", backref=db.backref(
        "replies", cascade="all, delete-orphan", order_by="ZimraReply.created_at.desc(), ZimraReply.id.desc()"))
    in_reply_to = db.relationship("ZimraCorrespondence", foreign_keys=[in_reply_to_id])
    correspondence = db.relationship("ZimraCorrespondence", foreign_keys=[correspondence_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    sent_by = db.relationship("User", foreign_keys=[sent_by_id])

    @property
    def history(self):
        import json as _json
        try:
            return _json.loads(self.events) if self.events else []
        except ValueError:
            return []

    @property
    def ai(self):
        """The AI-draft record (a dict) or None when the letter was written by a person."""
        import json as _json
        try:
            return _json.loads(self.ai_meta) if self.ai_meta else None
        except ValueError:
            return None

    def set_ai(self, data):
        import json as _json
        self.ai_meta = _json.dumps(data) if data else None

    def log(self, user, what, note=None):
        import json as _json
        items = self.history
        items.append({"at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "by": getattr(user, "name", None) or "-",
                      "what": what, "note": note or ""})
        self.events = _json.dumps(items)

    @property
    def is_editable(self):
        return self.status == "Draft"

    @property
    def is_locked(self):
        return self.status in ("Approved", "Sent")


# The kinds of instrument that can now be FILED (as an actual document,
# rather than just logged as a text entry) against a LegislativeUpdate - see
# the "filing" fields added to that model below. Existing rows logged before
# this existed have instrument_type None, and are shown simply as "Update".
# "Case" (added alongside chunked full-text search) is for a court
# judgment/case - Zimbabwean or foreign - rather than a piece of legislation;
# see the case_* fields below and legislation_summary.summarize_case_law.
# "Pub" (added alongside Case) is for a professional publication or journal
# article - e.g. a SAIT Tax Chronicles Monthly piece, a firm's tax alert, an
# ICAZ technical note - filed for its analysis/commentary rather than as a
# primary source of Zimbabwean law. This was added after a real filing
# mistake: a South African tax journal issue (no Zimbabwean statutory
# instruments in it at all) had nowhere proper to go and ended up filed as
# though it were Zimbabwean legislation, which is exactly the kind of
# mis-framing that produces a wrong AI summary (see legislation_summary.py's
# PUBLICATION_SYSTEM_PROMPT for the same lesson CASE_SYSTEM_PROMPT already
# learned from a mis-filed South African case). A Publication is useful,
# persuasive secondary material - not primary law and not binding - and is
# summarised and searched that way; see summarize_publication_update.
LEGISLATIVE_UPDATE_TYPES = ["Act", "Notice", "SI", "Case", "Pub", "News"]
LEGISLATIVE_UPDATE_TYPE_LABELS = {
    "Act": "Act of Parliament",
    "Notice": "Government Notice",
    "SI": "Statutory Instrument",
    "Case": "Case Law",
    "Pub": "Publication",
    "News": "News Article",
}

# How a filed Case entry's authority for Zimbabwean practice is classified -
# by the AI as a first pass (ai_case_authority_status, never applied on its
# own) and then confirmed/overridden by a person (case_authority_status),
# same "suggest, then a person confirms" pattern as areas/ai_suggested_areas.
# A Zimbabwean court's decision is "binding" (subject to the ordinary court
# hierarchy); a foreign court's decision is never binding, but Zimbabwean
# courts have long treated South African case law in particular as
# persuasive authority, given the shared Roman-Dutch/common-law tradition
# and closely analogous legislation (including much of Zimbabwe's own tax
# law) - being foreign is not, on its own, a reason to call a case "not
# relevant". "not_relevant" is reserved for a case whose actual subject
# matter doesn't bear on Zimbabwean tax/legal practice at all, not merely
# because it wasn't decided by a Zimbabwean court.
CASE_AUTHORITY_STATUSES = ["binding", "persuasive", "limited", "not_relevant"]
CASE_AUTHORITY_LABELS = {
    "binding": "Binding (Zimbabwean court)",
    "persuasive": "Persuasive authority",
    "limited": "Limited/uncertain persuasive value",
    "not_relevant": "Not relevant to Zimbabwean practice",
}

# A fixed set of practice-area tags a logged/filed update can be marked
# against - used both by a person tagging an update by hand and by the AI's
# own suggested-areas guess (see legislation_summary.py). Deliberately not
# free text, so "which of our clients might this affect" stays answerable by
# filtering rather than by guessing at inconsistent wording.
LEGISLATIVE_UPDATE_AREAS = [
    "Tax", "Companies & Corporate Law", "Commercial Law", "Estate Duty & Succession",
    "Labour & Employment", "Banking & Finance", "Insurance & Pensions", "Anti-Money Laundering",
    "Data Protection & Cyber Security", "Customs & Excise", "Exchange Control",
    "Accounting & Auditing Standards", "Property & Land", "Other",
]


class LegislativeUpdate(db.Model):
    """One entry in the firm-wide Legislative Update Control log (Module 8)
    - a change in legislation/practice the firm has logged and assessed the
    impact of. Firm-wide rather than per-engagement (a Finance Act change
    isn't specific to one client) - see tax.py's legislative_updates list
    page, reachable from HR & Administration, and from a top-nav link
    alongside the other firm-wide libraries.

    Originally a manually-typed log entry (title/summary/tax_head/
    effective_date/source_reference/impact_assessment) - those fields are
    unchanged and still work exactly as before. The fields below let an
    entry instead FILE the actual instrument (an Act/Notice/SI, a court
    Case, or a professional Publication, as a PDF or image): text is
    extracted from it the same way as RegulatoryNotice/CompanyDocument (see
    sanctions_data.extract_pdf_text), and then summarised by AI (see
    legislation_summary.py) into ai_summary/ai_key_changes - purely
    informational, like every other AI-assisted feature in this app: it
    never decides anything, a person reads it and judges it. "areas" is the
    firm's own confirmed tagging of which practice areas the instrument
    touches (used to decide which clients it might be relevant to, and
    shown on each tagged client's own page - see
    LegislativeUpdateClientLink); ai_suggested_areas is only the AI's own
    guess at the same, offered as a starting point - never applied
    automatically. A logged entry with no file is unaffected: all of the
    fields below are simply blank.

    A Case entry (instrument_type == "Case") is a court judgment rather
    than a piece of legislation - Zimbabwean or foreign - and gets the
    case_* fields below in addition to the usual summary/key-holdings/areas
    treatment: see CASE_AUTHORITY_STATUSES above for why a foreign judgment
    (particularly South African) is assessed for persuasive value rather
    than dismissed outright for not being Zimbabwean law.

    A Pub entry (instrument_type == "Pub") is a professional publication or
    journal article - not a piece of Zimbabwean legislation and not a court
    judgment, so it gets neither the case_* fields nor legislation's own
    framing: see legislation_summary.PUBLICATION_SYSTEM_PROMPT. Use the
    existing source_reference field for its citation (e.g. "Tax Chronicles
    Monthly, Issue 90, January 2026, SAIT").

    A News entry (instrument_type == "News") is a general news article about
    a development the firm wants on record - typically filed by pasting a
    URL (article_url below) rather than uploading a file: the app fetches
    the page itself, extracts its text, and summarises it the same way as
    everything else, but through legislation_summary.NEWS_SYSTEM_PROMPT,
    which frames it as what it actually is - ordinary news reporting, not a
    professional/technical analysis (unlike Pub) and never primary law -
    so a practitioner treats it as an early signal of a possible change to
    watch for, not something to rely on until the actual instrument is
    filed and reviewed. has_file is False for a News entry filed this way
    (there's no uploaded file, only an extracted copy of the page); see
    has_source below for the check that covers both."""
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

    # ---- Filing an actual instrument (Act/Notice/SI) and its AI summary ----
    instrument_type = db.Column(db.String(10))  # "Act"/"Notice"/"SI" - see LEGISLATIVE_UPDATE_TYPES; None for an old text-only log entry
    gazette_date = db.Column(db.Date)  # distinct from effective_date - when it was gazetted vs when it takes effect
    areas_json = db.Column(db.Text)  # JSON-encoded list, confirmed by a person - see LEGISLATIVE_UPDATE_AREAS

    original_filename = db.Column(db.String(300))
    stored_filename = db.Column(db.String(300))
    extracted_text = db.Column(db.Text)
    extraction_status = db.Column(db.String(20))  # "extracted", "no_text_found", "error"
    page_count = db.Column(db.Integer)
    text_source = db.Column(db.String(20))  # "scan:tesseract" / "scan:claude" when read from a scanned page (ocr_reader.py)

    ai_summary = db.Column(db.Text)
    ai_key_changes_json = db.Column(db.Text)  # JSON-encoded list of short bullet strings (for a Case: its key holdings)
    ai_suggested_areas_json = db.Column(db.Text)  # JSON-encoded list - a suggestion only, never auto-applied
    ai_status = db.Column(db.String(20))  # "done", "not_configured", "error", "skipped" (see legislation_summary.skip_summary_reason - full Acts deliberately aren't auto-summarised)
    ai_error = db.Column(db.Text)
    ai_processed_at = db.Column(db.DateTime)

    # ---- Case Law filing only (instrument_type == "Case") ----
    # citation/court/jurisdiction are read straight off the judgment by the
    # AI (an extraction, not a judgement call) but stay editable by hand -
    # see tax.save_legislative_update_areas. authority_status is the actual
    # judgement call (see CASE_AUTHORITY_STATUSES above): ai_case_authority_*
    # is the AI's own first pass, never applied on its own; case_authority_
    # status is what a person has actually confirmed, same pattern as areas.
    case_citation = db.Column(db.String(300))
    case_court = db.Column(db.String(200))
    case_jurisdiction = db.Column(db.String(100))
    ai_case_authority_status = db.Column(db.String(20))
    ai_case_authority_reasoning = db.Column(db.Text)
    case_authority_status = db.Column(db.String(20))

    # ---- News filing only (instrument_type == "News") ----
    # The URL the article was fetched from - both the source citation (shown
    # as a "Read online" link) and what "Reprocess" re-fetches from, since a
    # News entry has no uploaded file of its own to reprocess from.
    article_url = db.Column(db.String(500))

    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def is_reviewed(self):
        return self.reviewed_by_id is not None

    @property
    def type_label(self):
        return LEGISLATIVE_UPDATE_TYPE_LABELS.get(self.instrument_type, "Update")

    @property
    def has_file(self):
        return bool(self.stored_filename)

    @property
    def has_source(self):
        """True if there's something to show an AI summary/areas-tagging
        box for - an uploaded file (has_file) OR a News entry's fetched
        article_url. Use this instead of has_file when deciding whether to
        render that box; keep using has_file itself for anything that
        specifically needs an actual file on disk (downloading it, checking
        file_ext, and so on)."""
        return self.has_file or bool(self.article_url)

    @property
    def areas(self):
        try:
            return json.loads(self.areas_json) if self.areas_json else []
        except (TypeError, ValueError):
            return []

    def set_areas(self, value):
        self.areas_json = json.dumps(value or [])

    @property
    def ai_key_changes(self):
        try:
            return json.loads(self.ai_key_changes_json) if self.ai_key_changes_json else []
        except (TypeError, ValueError):
            return []

    def set_ai_key_changes(self, value):
        self.ai_key_changes_json = json.dumps(value or [])

    @property
    def ai_suggested_areas(self):
        try:
            return json.loads(self.ai_suggested_areas_json) if self.ai_suggested_areas_json else []
        except (TypeError, ValueError):
            return []

    def set_ai_suggested_areas(self, value):
        self.ai_suggested_areas_json = json.dumps(value or [])

    @property
    def file_ext(self):
        return self.original_filename.rsplit(".", 1)[-1].lower() if self.original_filename and "." in self.original_filename else ""

    @property
    def authority_label(self):
        """The person-confirmed authority classification's display label, or
        None if not yet confirmed - see case_authority_status above."""
        return CASE_AUTHORITY_LABELS.get(self.case_authority_status)

    @property
    def ai_authority_label(self):
        """The AI's own first-pass authority suggestion's display label, or
        None if not yet processed - see ai_case_authority_status above."""
        return CASE_AUTHORITY_LABELS.get(self.ai_case_authority_status)

    def __repr__(self):
        return f"<LegislativeUpdate {self.title!r}>"


class StatutoryDeadline(db.Model):
    """One firm-wide statutory compliance date shown on the Legislative
    Update Calendar (tax.legislative_updates_calendar) - a QPD instalment,
    a VAT/PAYE filing or payment date, or any other recurring obligation
    the firm wants everyone to see at a glance, distinct from TaxDeadline
    above (which is a per-engagement, per-client deadline tracked on one
    client's own Statutory Tax Calendar tab).

    Deliberately a firm-maintained list a person types in and edits by
    hand, not something computed from a rule - the user was asked directly
    ("Firm-maintained list" vs. having the app compute VAT/PAYE dates
    itself) and chose this, and it fits the app's existing "nothing
    tax-specific is fabricated" rule (see PenaltyInterestRate above):
    VAT/PAYE due dates in particular are NOT a fixed day-of-month in
    practice - ZIMRA has shifted them via public notice before - so this
    app does not assume or derive one. QPD dates (25 March, 25 June,
    25 September, 20 December) are well-corroborated and pre-seeded once,
    on first startup, as an editable/deletable starting point (see
    app.py's startup seed step) - never re-seeded afterwards, so deleting
    or editing a seeded row sticks."""
    id = db.Column(db.Integer, primary_key=True)
    tax_head = db.Column(db.String(80))  # one of TAX_HEADS, or blank for something that doesn't fit the list
    description = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")

    @property
    def is_overdue(self):
        return self.due_date < date.today()

    def __repr__(self):
        return f"<StatutoryDeadline {self.description!r} due={self.due_date}>"


class LegislativeUpdateChunk(db.Model):
    """One section-sized slice of a filed instrument's extracted_text (see
    legislation_chunking.py), so "Ask the library" (legislation_search.py)
    can retrieve the specific relevant part of a long document - a full Act
    can run to hundreds of pages, far more than could ever be handed to the
    AI as a single excerpt - rather than only ever seeing a fixed-size
    excerpt from the start of the document. Chunked once per document
    (at filing/import time, or lazily the first time it's needed for an
    already-filed document - see legislation_chunking.ensure_chunks) and
    never re-chunked automatically afterwards. Purely a search index over
    text that's already filed - deleting the parent LegislativeUpdate
    deletes its chunks with it (cascade below); nothing here is ever shown
    to a person directly, only used internally as retrieval context."""
    __tablename__ = "legislative_update_chunk"
    id = db.Column(db.Integer, primary_key=True)
    legislative_update_id = db.Column(db.Integer, db.ForeignKey("legislative_update.id"), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False)  # 0-based order within the document
    heading = db.Column(db.String(300))  # the nearest Part/Chapter/numbered-section heading, if any was detected
    text = db.Column(db.Text, nullable=False)

    legislative_update = db.relationship(
        "LegislativeUpdate",
        backref=db.backref("chunks", lazy=True, cascade="all, delete-orphan", order_by="LegislativeUpdateChunk.chunk_index"),
    )

    def __repr__(self):
        return f"<LegislativeUpdateChunk {self.legislative_update_id}#{self.chunk_index}>"


class LegislativeProvision(db.Model):
    """One numbered section or schedule of an Act that has been filed in
    section-by-section form (see act_import.py), kept as its own row so a
    tax working paper can link straight to "section 16(1)(m)" and show the
    Act's own words - without a search. The same text is also in the
    parent LegislativeUpdate's chunks (one chunk per section, long ones
    split), which is what Deep Search and Ask the library read. `act_code`
    is a short slug ("ita" = Income Tax Act [Chapter 23:06]) used in
    links; `number` is the section number as printed ("15", "36G") or the
    schedule number ("4" for the Fourth Schedule) - kind says which."""
    __tablename__ = "legislative_provision"
    id = db.Column(db.Integer, primary_key=True)
    legislative_update_id = db.Column(db.Integer, db.ForeignKey("legislative_update.id"), nullable=False, index=True)
    act_code = db.Column(db.String(20), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)  # "section" or "schedule"
    number = db.Column(db.String(12), nullable=False)
    label = db.Column(db.String(40), nullable=False)  # "Section 15" / "Fourth Schedule"
    title = db.Column(db.String(300))
    part = db.Column(db.String(80))
    part_title = db.Column(db.String(200))
    refers_to = db.Column(db.String(120))  # a schedule's "(Section 15(2)(c))" note
    sort_order = db.Column(db.Integer, default=0)
    text = db.Column(db.Text, nullable=False)
    is_repealed = db.Column(db.Boolean, default=False)

    legislative_update = db.relationship(
        "LegislativeUpdate",
        backref=db.backref("provisions", lazy=True, cascade="all, delete-orphan", order_by="LegislativeProvision.sort_order"),
    )

    def __repr__(self):
        return f"<LegislativeProvision {self.act_code} {self.label}>"


class LegislativeAskCache(db.Model):
    """A fast-recall cache of "Ask the library" question/answer pairs (see
    legislation_search.py) - asking the same or a very similar question
    again returns the earlier answer instantly and consistently instead of
    re-searching and re-calling the AI, while a genuinely new question is
    still answered fresh from the filed material every time. Deliberately
    the simpler of the two designs discussed with the firm: this is a
    verbatim recall cache, not a growing shared knowledge base - a cached
    answer never feeds into, or changes the answer to, any OTHER question,
    and nothing here is reviewed/confirmed by a person before being reused
    (see legislation_search.ask for the matching logic). Every reused
    answer is always visibly flagged as such wherever it's shown, never
    presented as freshly generated. Only "done" and "no_answer" results are
    ever cached - a transient failure, an unconfigured API key, or "nothing
    filed matches at all" is never cached, so those always retry fresh."""
    __tablename__ = "legislative_ask_cache"
    id = db.Column(db.Integer, primary_key=True)
    question_normalized = db.Column(db.String(1000), nullable=False, index=True)  # lowercased/whitespace-collapsed, for exact-match lookup
    question_original = db.Column(db.String(500), nullable=False)  # as actually typed, for display/audit only
    status = db.Column(db.String(20), nullable=False)  # "done" or "no_answer" only - see legislation_search.ask
    answer = db.Column(db.Text, nullable=False)
    citation_ids_json = db.Column(db.Text)  # JSON list of LegislativeUpdate ids cited (status "done")
    candidate_ids_json = db.Column(db.Text)  # JSON list of LegislativeUpdate ids considered but not sufficient (status "no_answer")
    asked_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    hit_count = db.Column(db.Integer, default=1)  # how many times this cached answer has been reused (starts at 1 for the original answer)
    last_hit_at = db.Column(db.DateTime)
    helpful_count = db.Column(db.Integer, default=0)    # "Helpful" clicks from the people it was shown to
    unhelpful_count = db.Column(db.Integer, default=0)  # "Not right" clicks - see learning.py

    asked_by = db.relationship("User", foreign_keys=[asked_by_id])

    def __repr__(self):
        return f"<LegislativeAskCache {self.question_original!r} ({self.status})>"


# ---------- Firm learning (approved lessons the app's AI features and defaults draw on) ----------

LESSON_AREAS = [
    ("zimra_letters", "ZIMRA letters"),
    ("library", "Library answers"),
    ("fees", "Fees and time"),
    ("collections", "Collections"),
    ("audit_risk", "Audit risks and procedures"),
    ("corpfin", "Corporate finance models"),
]
LESSON_AREA_LABELS = dict(LESSON_AREAS)
LESSON_KINDS = ["Wording", "Position", "Avoid", "Verified answer", "Benchmark", "Pattern"]
LESSON_STATUSES = ["Proposed", "Approved", "Rejected", "Retired"]


class FirmLesson(db.Model):
    """One thing the firm has learned and a Partner/Admin has approved as its standard: a clause that works, a position
    to take or avoid, a verified answer to a legislation question, a fee benchmark, a recurring risk. Lessons are
    ANONYMISED (no client names, TINs or contacts) so they can serve every engagement. A lesson is only ever a
    suggestion made by a person or by an analysis; nothing changes how the app or its AI behaves until a Partner or
    Admin approves it, and an approved lesson can be retired at any time. Lessons never carry facts about a client or
    law - law still comes only from the filed library. See learning.py."""
    __tablename__ = "firm_lesson"
    id = db.Column(db.Integer, primary_key=True)
    area = db.Column(db.String(30), nullable=False)
    kind = db.Column(db.String(30), nullable=False, default="Wording")
    title = db.Column(db.String(250), nullable=False)
    content = db.Column(db.Text, nullable=False)
    tags_json = db.Column(db.Text)       # {"template_key": ["objection"], "tax_head": ["VAT"]} - when the lesson applies
    evidence = db.Column(db.Text)        # anonymised before/after or data summary the lesson came from
    source_kind = db.Column(db.String(30))  # zimra_reply | library_answer | manual | analysis
    source_id = db.Column(db.Integer)
    status = db.Column(db.String(20), nullable=False, default="Proposed")
    proposed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    proposed_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.Text)
    review_due = db.Column(db.Date)      # a lesson that depends on current law is not served after this date
    times_used = db.Column(db.Integer, default=0)
    last_used_at = db.Column(db.DateTime)
    helpful_count = db.Column(db.Integer, default=0)
    unhelpful_count = db.Column(db.Integer, default=0)
    events = db.Column(db.Text)          # JSON list of {at, by, what, note}

    proposed_by = db.relationship("User", foreign_keys=[proposed_by_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])

    @property
    def tags(self):
        import json as _json
        try:
            data = _json.loads(self.tags_json) if self.tags_json else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    @property
    def history(self):
        import json as _json
        try:
            return _json.loads(self.events) if self.events else []
        except ValueError:
            return []

    def log(self, user, what, note=None):
        import json as _json
        items = self.history
        items.append({"at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "by": getattr(user, "name", None) or "-",
                      "what": what, "note": note or ""})
        self.events = _json.dumps(items)

    @property
    def area_label(self):
        return LESSON_AREA_LABELS.get(self.area, self.area)

    @property
    def is_due_for_review(self):
        return bool(self.status == "Approved" and self.review_due and self.review_due < date.today())

    @property
    def is_servable(self):
        return self.status == "Approved" and not self.is_due_for_review

    @property
    def helpful_rate(self):
        total = (self.helpful_count or 0) + (self.unhelpful_count or 0)
        return None if total == 0 else (self.helpful_count or 0) / total

    def __repr__(self):
        return f"<FirmLesson {self.id} {self.area} {self.status}>"


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
    tax_expense_type = db.Column(db.String(60))  # the Prohibited Deductions rule code this expense account maps to (prohibited_deductions.py)
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
