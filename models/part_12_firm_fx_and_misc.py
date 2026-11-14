"""models, part 12 of 12.  Holds: FirmFxConfig, FirmFxMiss, FirmRbzReading, DailyUpdate, ScanText, ValuationCertificate, CashFlowPriorLine, CoverSetting.

See __init__.py: all parts share one set of names, exactly as the single file models.py did."""

class FirmFxConfig(db.Model):
    """One row of settings for fetching exchange rates from the Reserve Bank of Zimbabwe website."""
    __tablename__ = "firm_fx_config"
    id = db.Column(db.Integer, primary_key=True)
    rbz_basis = db.Column(db.String(10), default="mid")        # which RBZ interbank figure is stored: mid | bid | ask
    auto_fetch = db.Column(db.Boolean, default=True)           # fetch the latest published rate when the rates page is opened
    last_attempt = db.Column(db.DateTime)
    last_message = db.Column(db.String(300))
    prefer = db.Column(db.String(10))                          # when RBZ and ZIMRA share a date for reporting: "zimra" puts ZIMRA first (default RBZ)


class FirmFxMiss(db.Model):
    """A working day the RBZ has no usable rate file for (public holiday, a copy of another day's file ...). Remembered so the
    automatic catch-up does not ask for it again and again; recent days are re-checked, old ones are settled."""
    __tablename__ = "firm_fx_miss"
    id = db.Column(db.Integer, primary_key=True)
    currency = db.Column(db.String(10), nullable=False, default="ZWG")
    rate_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(250))
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("currency", "rate_date", name="uq_firm_fx_miss"),)


class FirmRbzReading(db.Model):
    """What the RBZ published for one day (ZWG per 1 USD: bid, ask, mid), kept whatever rate the register ended up using for that
    date. It is the evidence the Tax-rates (ZIMRA) comparison is made against."""
    __tablename__ = "firm_rbz_reading"
    id = db.Column(db.Integer, primary_key=True)
    rate_date = db.Column(db.Date, nullable=False, unique=True)
    bid = db.Column(db.Float, nullable=False)
    ask = db.Column(db.Float, nullable=False)
    mid = db.Column(db.Float, nullable=False)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)


class DailyUpdate(db.Model):
    """One team member's Projects Daily Update for one day: what they are working on, roadblocks, help needed, problems solved, suggestions,
    and any client or advisory fire. Written by the person; the facts the app already knows (open tasks, deadlines, capacity, hours) are added
    next to it when it is shown (daily_updates.member_facts). One row per person per day."""
    __tablename__ = "daily_update"
    __table_args__ = (db.UniqueConstraint("user_id", "update_date", name="uq_daily_update_user_day"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    update_date = db.Column(db.Date, nullable=False)
    working_on = db.Column(db.Text)
    roadblocks = db.Column(db.Text)
    assistance_needed = db.Column(db.Text)
    problems_solved = db.Column(db.Text)
    suggestions = db.Column(db.Text)
    client_fires = db.Column(db.Text)
    fire_urgent = db.Column(db.Boolean, default=False, nullable=False, server_default="0")  # needs immediate cross-team support
    fire_client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])
    fire_client = db.relationship("Client", foreign_keys=[fire_client_id])

    TEXT_FIELDS = ("working_on", "roadblocks", "assistance_needed", "problems_solved", "suggestions", "client_fires")

    @property
    def has_roadblock(self):
        return bool((self.roadblocks or "").strip())

    @property
    def needs_help(self):
        return bool((self.assistance_needed or "").strip())

    @property
    def has_fire(self):
        return bool(self.fire_urgent or (self.client_fires or "").strip())



# ---------- Text read out of client files (scans included) ----------

SCAN_KINDS = ("engagement_doc", "permanent_file", "filing_archive", "zimra_corr")


class ScanText(db.Model):
    """The text of one uploaded client file, read when it was uploaded so it can be searched ("Search inside documents").
    A PDF with its own text is read directly; a scan (an image-only PDF, a JPG or PNG) is read by ocr_reader.py. One row per file
    (kind + item_id); method says how it was read: 'text' (the file's own), 'tesseract' or 'claude' (read from a picture). Text read
    from a picture can contain mistakes - the search page says so beside every such result."""
    __tablename__ = "scan_text"
    __table_args__ = (db.UniqueConstraint("kind", "item_id", name="uq_scan_text_item"),)
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)       # one of SCAN_KINDS
    item_id = db.Column(db.Integer, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), index=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), index=True)
    title = db.Column(db.String(300))
    text = db.Column(db.Text)
    status = db.Column(db.String(20))     # "reading", "extracted", "no_text_found", "not_configured", "error", "unsupported_format"
    method = db.Column(db.String(12))     # "text", "tesseract", "claude"
    pages = db.Column(db.Integer)
    error = db.Column(db.Text)
    read_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def from_picture(self):
        return self.method in ("tesseract", "claude")


class ValuationCertificate(MPSignedMixin, db.Model):
    """A valuation certificate issued by the firm (desktop valuation of a mining claim, a business, a property...). The figures (low,
    adopted, high) and the wording are kept here; valuation_docs.py lays them out on the firm letterhead as Word / PDF, and the Managing
    Partner signs it with the same 'Sign as Managing Partner' step as every other document (mpsign.py). Changing the figures or the wording
    after signing drops the signature until it is signed again (brand_docs.content_key)."""
    __tablename__ = "valuation_certificate"
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(60), nullable=False, unique=True)   # NCA/DVC/2026/001
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))        # optional link; client_name is what is printed
    client_name = db.Column(db.String(200), nullable=False, default="")
    certificate_title = db.Column(db.String(200), nullable=False, default="Desktop Valuation Certificate")
    certificate_date = db.Column(db.Date)
    valuation_date = db.Column(db.Date)
    intro = db.Column(db.Text)
    subject_asset = db.Column(db.Text)
    title_reference = db.Column(db.Text)
    asset_stage = db.Column(db.Text)
    purpose = db.Column(db.Text)
    basis_of_value = db.Column(db.Text)
    valuation_type = db.Column(db.Text)
    currency = db.Column(db.String(3), nullable=False, default="USD")    # USD | ZWG
    area_ha = db.Column(db.Float)                                         # when set, a per-hectare column is shown
    low_value = db.Column(db.Float)
    adopted_value = db.Column(db.Float)
    high_value = db.Column(db.Float)
    asset_noun = db.Column(db.String(60), default="claim")               # "claim" in "the value of the claim at ..."
    range_note = db.Column(db.Text)
    approach = db.Column(db.Text)        # paragraphs separated by a blank line; "**Lead-in.** text"
    assumptions = db.Column(db.Text)     # one per line, starting "- "; "**Lead-in.** text"
    certification = db.Column(db.Text)
    signatory_name = db.Column(db.String(200))
    signatory_title = db.Column(db.String(200))
    sources_heading = db.Column(db.String(300))
    sources = db.Column(db.Text)         # one per line, "- [Title](https://...)"
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = db.relationship("Client", foreign_keys=[client_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])



class CashFlowPriorLine(db.Model):
    """One line of the PRIOR-year comparative Statement of Cash Flows, captured by hand (the prior-year cash flow cannot be derived from the
    trial balance, which only holds two years). `section` is operating / investing / financing, or cash_open (the single line holding cash at
    the beginning of the prior year). The labels are the same as the current year's rows so each prior figure sits beside its current one."""
    __tablename__ = "cash_flow_prior_line"
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, index=True)
    section = db.Column(db.String(12), nullable=False)
    label = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    sort_order = db.Column(db.Integer, default=0)


class CoverSetting(db.Model):
    """How a document's cover page looks (cover_pages.py). One row per document: `kind` says which sort ("client" = the client's defaults for every
    cover, "fs" = an engagement's Financial Statements, "ma" = a management accounts pack, "vm" = a valuation model and its report, "fm" = a
    forecast model, "ip" = an investment appraisal) and `ref_id` is that document's id (the client's id for "client"). `data_json` holds the choices:
    design, colour theme, the wording and the status line. No row = the firm's defaults, so nothing has to be set up for a cover to appear."""
    __tablename__ = "cover_setting"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(12), nullable=False)
    ref_id = db.Column(db.Integer, nullable=False)
    data_json = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    __table_args__ = (db.UniqueConstraint("kind", "ref_id", name="uq_cover_setting"),)
