"""models, part 12 of 12.  Holds: FirmFxConfig, FirmFxMiss, FirmRbzReading, DailyUpdate, ScanText, ValuationCertificate, CashFlowPriorLine, CoverSetting, PPERevaluation, PPERevaluationLine, MarketingLead, LeadTouch, SocialPost, SocialVersion, SocialImage, SocialVideo, SocialSchedule, SocialDispatch, SocialAccount, WhatsAppContact, WhatsAppMessage.

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


class PPERevaluation(db.Model):
    """The independent revaluation of an engagement's property, plant and equipment at the year end (ppe_revaluation.py): who valued, on what
    basis, as at when, and whether it has been applied. While `status` is "Draft" nothing in the statements changes; "Applied" means the REVAL
    journal is on the trial balance and the Asset Register / PPE note carry the revalued amounts. One per engagement."""
    __tablename__ = "ppe_revaluation"
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, unique=True, index=True)
    valuation_date = db.Column(db.Date)
    valuer_name = db.Column(db.String(200))
    valuer_qualification = db.Column(db.String(200))            # e.g. professional body and registration number
    report_reference = db.Column(db.String(120))
    report_date = db.Column(db.Date)
    basis = db.Column(db.String(80))                            # fair value: market value / depreciated replacement cost / ...
    independent = db.Column(db.Boolean, default=True)
    policy_confirmed = db.Column(db.Boolean, default=False)     # the revaluation model is adopted for the whole of each class revalued (IAS 16.36)
    method_note = db.Column(db.Text)                            # methods and significant assumptions (IAS 16.77)
    status = db.Column(db.String(12), default="Draft")
    applied_at = db.Column(db.DateTime)
    applied_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = db.relationship("PPERevaluationLine", backref="revaluation", cascade="all, delete-orphan", order_by="PPERevaluationLine.order")
    applied_by = db.relationship("User", foreign_keys=[applied_by_id])


class PPERevaluationLine(db.Model):
    """One asset's fair value from the valuer's report. `prior_reserve` is the revaluation surplus already held for this asset from an
    earlier valuation and `prior_deficit_pl` an earlier revaluation decrease charged to profit or loss: they decide where this valuation's
    increase or decrease goes (IAS 16.39 and 16.40). `dt_rate` is the deferred tax rate (percent) on the surplus."""
    __tablename__ = "ppe_revaluation_line"
    id = db.Column(db.Integer, primary_key=True)
    revaluation_id = db.Column(db.Integer, db.ForeignKey("ppe_revaluation.id"), nullable=False, index=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("ppe_asset.id"), nullable=False)
    fair_value = db.Column(db.Float)
    prior_reserve = db.Column(db.Float, default=0.0)
    prior_deficit_pl = db.Column(db.Float, default=0.0)
    dt_rate = db.Column(db.Float)
    order = db.Column(db.Integer, default=0)

    asset = db.relationship("PPEAsset", foreign_keys=[asset_id])


# ---------- News Watch (news_watch.py): tax and business news for the team ----------

class NewsSource(db.Model):
    """Somewhere News Watch looks for news: a Google News search (`target` holds the search words), a website's own RSS/Atom feed or a Google Alerts
    feed (`target` holds the web address). Managed by Partners and Admin on the News Watch sources page."""
    __tablename__ = "news_source"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(20), nullable=False)          # google_news / feed / google_alert / x_account / x_search
    target = db.Column(db.String(500), nullable=False)
    region = db.Column(db.String(20), default="Zimbabwe")     # Zimbabwe / South Africa / International
    active = db.Column(db.Boolean, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_fetched_at = db.Column(db.DateTime)                  # last attempt, successful or not
    last_status = db.Column(db.String(300))
    last_new_count = db.Column(db.Integer, default=0)

    created_by = db.relationship("User", foreign_keys=[created_by_id])


class NewsItem(db.Model):
    """One news story found by News Watch (or added by a team member from a link). `status`: new (waiting to be read by the AI), relevant, irrelevant
    (not about tax or business that matters to the firm, or marked so by a person) or skipped (failed the keyword filter, never sent to the AI).
    Only a headline, a short snippet and the AI's own short summary are kept, never the article. News is an unconfirmed signal, never law."""
    __tablename__ = "news_item"
    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("news_source.id"))
    title = db.Column(db.String(400), nullable=False)
    url = db.Column(db.String(1000), nullable=False)
    region = db.Column(db.String(20), default="Zimbabwe")
    publisher = db.Column(db.String(200))
    published_at = db.Column(db.DateTime)
    snippet = db.Column(db.Text)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)
    fingerprint = db.Column(db.String(40), index=True)
    status = db.Column(db.String(12), default="new")
    urgency = db.Column(db.String(12))                       # Urgent / Important / FYI
    topics_json = db.Column(db.Text)
    summary = db.Column(db.Text)
    why = db.Column(db.Text)
    sectors_json = db.Column(db.Text)
    confidence = db.Column(db.String(10))                     # how well the headline and snippet support the summary
    ai_used = db.Column(db.Boolean, default=False)
    ai_tries = db.Column(db.Integer, default=0)
    ai_error = db.Column(db.String(300))
    unconfirmed = db.Column(db.Boolean, default=False)       # social media or a link a person added: not checked by anyone
    shared_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    pinned = db.Column(db.Boolean, default=False)
    filed_update_id = db.Column(db.Integer)                  # the Legislative Update Control News entry made from this story (no foreign key on purpose)

    source = db.relationship("NewsSource", foreign_keys=[source_id])
    shared_by = db.relationship("User", foreign_keys=[shared_by_id])
    __table_args__ = (db.Index("ix_news_item_status_fetched", "status", "fetched_at"),)

    @property
    def topics(self):
        try:
            return list(json.loads(self.topics_json or "[]"))
        except ValueError:
            return []

    @property
    def sectors(self):
        try:
            return list(json.loads(self.sectors_json or "[]"))
        except ValueError:
            return []


class NewsSeen(db.Model):
    """When each person last opened News Watch, so the menu badge counts only what is new to them."""
    __tablename__ = "news_seen"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    seen_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- Leads (marketing sign-ups)

LEAD_STATUSES = ("New", "Contacted", "Replied", "Booked", "Won", "Lost", "Unsubscribed")
LEAD_STOP_STATUSES = ("Replied", "Booked", "Won", "Lost", "Unsubscribed")     # once a lead is in one of these, the follow-up sequence stops


class MarketingLead(db.Model):
    """One person who asked for a lead magnet (e.g. the Compliance Calendar) on the landing page."""
    __tablename__ = "marketing_lead"
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), default="")
    company = db.Column(db.String(160), default="")
    job_role = db.Column(db.String(60), default="")
    email = db.Column(db.String(200), nullable=False, index=True)
    mobile = db.Column(db.String(40), default="")
    headache = db.Column(db.String(80), default="")
    source = db.Column(db.String(120), default="Magnet 1: Compliance Calendar")
    utm_source = db.Column(db.String(80), default="")
    utm_medium = db.Column(db.String(80), default="")
    utm_campaign = db.Column(db.String(80), default="")
    consent_email = db.Column(db.Boolean, default=False)
    consent_whatsapp = db.Column(db.Boolean, default=False)
    consent_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default="New", index=True)
    priority = db.Column(db.String(10), default="Normal")        # High: an owner, director or CEO, or a company that already asked about audit or tax
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    notes = db.Column(db.Text, default="")
    unsub_token = db.Column(db.String(40), unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_touch_at = db.Column(db.DateTime)

    owner = db.relationship("User", foreign_keys=[owner_id])
    touches = db.relationship("LeadTouch", backref="lead", cascade="all, delete-orphan", order_by="LeadTouch.due_at")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class LeadTouch(db.Model):
    """One planned follow-up for a lead: an automatic email, or a personal note or WhatsApp message for a person to send."""
    __tablename__ = "lead_touch"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("marketing_lead.id"), nullable=False, index=True)
    step = db.Column(db.String(20), nullable=False)               # note24h, email1, email2, whatsapp5, email3, reengage
    channel = db.Column(db.String(12), default="email")           # email / whatsapp
    mode = db.Column(db.String(8), default="manual")              # auto: the app sends it; manual: a person sends it
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(10), default="pending", index=True)   # pending / sent / done / skipped / failed
    tries = db.Column(db.Integer, default=0)
    done_at = db.Column(db.DateTime)
    done_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    error = db.Column(db.String(300))

    done_by = db.relationship("User", foreign_keys=[done_by_id])


# --------------------------------------------------------------------------- Social media management

POST_STATUSES = ("Idea", "Draft", "Pending approval", "Approved", "Posted", "Rejected")


class SocialPost(db.Model):
    """One piece of content for the firm's social media, written once and adapted for each platform (see SocialVersion)."""
    __tablename__ = "social_post"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    brief = db.Column(db.Text, default="")                      # what the post is about, in the author's words (the AI works only from this)
    pillar = db.Column(db.String(40), default="")               # Authority / Education / Proof / Offer ...
    status = db.Column(db.String(20), default="Draft", index=True)
    planned_date = db.Column(db.Date, index=True)
    link_url = db.Column(db.String(500), default="")            # where the post sends people; each platform gets its own tracked copy
    campaign = db.Column(db.String(80), default="", index=True)  # utm_campaign used on the tracked links
    news_item_id = db.Column(db.Integer)                        # the News Watch story it came from (no foreign key on purpose)
    ai_used = db.Column(db.Boolean, default=False)
    ai_flags = db.Column(db.Text, default="")                   # facts the AI said a person must check, one per line
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approver_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_at = db.Column(db.DateTime)
    checks_json = db.Column(db.Text, default="[]")              # the checklist items the approver ticked
    review_note = db.Column(db.Text, default="")                # why it was sent back
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship("User", foreign_keys=[author_id])
    approver = db.relationship("User", foreign_keys=[approver_id])
    versions = db.relationship("SocialVersion", backref="post", cascade="all, delete-orphan", order_by="SocialVersion.id")
    images = db.relationship("SocialImage", backref="post", cascade="all, delete-orphan", order_by="SocialImage.id")
    videos = db.relationship("SocialVideo", backref="post", cascade="all, delete-orphan", order_by="SocialVideo.id")

    @property
    def checks(self):
        try:
            return list(json.loads(self.checks_json or "[]"))
        except ValueError:
            return []


class SocialVersion(db.Model):
    """The wording of a post for one platform, whether it has been posted, and how it did."""
    __tablename__ = "social_version"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("social_post.id"), nullable=False, index=True)
    platform = db.Column(db.String(20), nullable=False)
    headline = db.Column(db.String(150), default="")            # YouTube title
    body = db.Column(db.Text, default="")                       # {link} stands for the tracked link
    media_note = db.Column(db.Text, default="")                 # the picture, carousel or video this needs (Instagram, TikTok, YouTube ...)
    status = db.Column(db.String(10), default="pending")        # pending / posted / skipped
    posted_at = db.Column(db.DateTime)
    posted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    post_url = db.Column(db.String(500), default="")
    impressions = db.Column(db.Integer)
    likes = db.Column(db.Integer)
    comments = db.Column(db.Integer)
    shares = db.Column(db.Integer)
    clicks = db.Column(db.Integer)
    metrics_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint("post_id", "platform", name="uq_social_version_platform"),)

    posted_by = db.relationship("User", foreign_keys=[posted_by_id])


class SocialVideo(db.Model):
    """A short captioned video (TikTok, YouTube Shorts) for a post. Only the description of the video (spec_json) is stored in the database; the MP4 is
    made on request and kept on the server's disk (file_name), and is made again when the description changes."""
    __tablename__ = "social_video"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("social_post.id"), nullable=False, index=True)
    label = db.Column(db.String(80), default="")
    spec_json = db.Column(db.Text, default="{}")
    ai_used = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(12), default="none")           # none / rendering / ready / failed
    error = db.Column(db.String(500), default="")
    file_name = db.Column(db.String(120), default="")           # the MP4 on disk
    file_key = db.Column(db.String(64), default="")             # fingerprint of the description the file was made from
    seconds = db.Column(db.Float)
    rendered_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def spec(self):
        try:
            data = json.loads(self.spec_json or "{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}


class SocialSchedule(db.Model):
    """When an approved post is to be sent automatically to the firm's scheduling service (Make, Buffer ...). publish_at is stored in UTC."""
    __tablename__ = "social_schedule"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("social_post.id"), nullable=False, unique=True)
    publish_at = db.Column(db.DateTime, nullable=False, index=True)
    enabled = db.Column(db.Boolean, default=True)               # send automatically (off = a reminder of the date only)
    note = db.Column(db.String(300), default="")                # why it is held, or the last problem
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    post = db.relationship("SocialPost", backref=db.backref("schedule", uselist=False, cascade="all, delete-orphan"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class SocialDispatch(db.Model):
    """The record of sending one platform's wording to the scheduling service: sent, or failed (with the reason and the number of tries)."""
    __tablename__ = "social_dispatch"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("social_post.id"), nullable=False, index=True)
    version_id = db.Column(db.Integer, db.ForeignKey("social_version.id"), nullable=False, unique=True)
    platform = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(10), default="failed")         # sent / failed
    attempts = db.Column(db.Integer, default=0)
    error = db.Column(db.String(500), default="")
    sent_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    post = db.relationship("SocialPost", backref=db.backref("dispatches", cascade="all, delete-orphan"))
    version = db.relationship("SocialVersion", backref=db.backref("dispatch", uselist=False, cascade="all, delete-orphan"))


class SocialAccount(db.Model):
    """The firm's page or profile on one platform."""
    __tablename__ = "social_account"
    platform = db.Column(db.String(20), primary_key=True)
    handle = db.Column(db.String(120), default="")
    profile_url = db.Column(db.String(300), default="")
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    notes = db.Column(db.Text, default="")

    owner = db.relationship("User", foreign_keys=[owner_id])


class WhatsAppContact(db.Model):
    """A person the firm may message on WhatsApp, with the proof that they agreed to it. Messages are sent by a person, one by one, from the firm's own WhatsApp."""
    __tablename__ = "whatsapp_contact"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40), nullable=False, index=True)    # digits with country code, e.g. 263771234567
    company = db.Column(db.String(160), default="")
    list_name = db.Column(db.String(60), default="Clients")         # Clients / Leads / Broadcast ...
    consent = db.Column(db.Boolean, default=False)
    consent_source = db.Column(db.String(60), default="")
    consent_at = db.Column(db.DateTime)
    opted_out = db.Column(db.Boolean, default=False)
    lead_id = db.Column(db.Integer)                                 # the marketing lead it came from (no foreign key on purpose)
    notes = db.Column(db.Text, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_message_at = db.Column(db.DateTime)

    messages = db.relationship("WhatsAppMessage", backref="contact", cascade="all, delete-orphan", order_by="WhatsAppMessage.at")


class WhatsAppMessage(db.Model):
    """One message sent to (out) or received from (in) a contact, logged by a person."""
    __tablename__ = "whatsapp_message"
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("whatsapp_contact.id"), nullable=False, index=True)
    direction = db.Column(db.String(3), default="out")
    body = db.Column(db.Text, default="")
    template = db.Column(db.String(30), default="")
    post_id = db.Column(db.Integer)                                 # when it was a broadcast of a social post
    by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    at = db.Column(db.DateTime, default=datetime.utcnow)

    by_user = db.relationship("User", foreign_keys=[by_user_id])


class SocialImage(db.Model):
    """A branded graphic for a post. Only its description (the style, colours and short texts) is kept; the picture is drawn when it is asked for (graphics.py)."""
    __tablename__ = "social_image"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("social_post.id"), nullable=False, index=True)
    label = db.Column(db.String(80), default="")
    spec_json = db.Column(db.Text, default="{}")
    ai_used = db.Column(db.Boolean, default=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def spec(self):
        try:
            data = json.loads(self.spec_json or "{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}
