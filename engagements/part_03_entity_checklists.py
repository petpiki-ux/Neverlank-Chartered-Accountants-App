"""engagements, part 3 of 7.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file engagements.py did."""

def _default_entity_checklist_items_for(engagement):
    """The firm's default Understanding Business/Assignment checklist
    questions for this engagement's type - Forensic Audit questions
    (Investigative Engagements), cyber/IT/AML-CFT questions (Business
    Intelligence and IT Engagements), the COBE-based Secretarial Client
    Business Understanding Questionnaire (Secretarial, filtered to the
    engagement's selected Activities), or the general ISA 315-style
    DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS for every other type.
    Shared by seed_entity_checklist (first-time seeding) and
    sync_entity_checklist_items (topping up an already-seeded checklist
    with any new questions added to these lists since it was seeded) so
    the two can never drift apart on which list applies to which type."""
    if engagement.type == "Investigative Engagement":
        return FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS
    if engagement.type == "Business Intelligence and IT Engagements":
        return BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS
    if engagement.type == "Secretarial":
        selected_activities = set(engagement.secretarial_activity_list)
        return [
            (section, item_text)
            for section, item_text, activities in SECRETARIAL_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS
            if not selected_activities or selected_activities & set(activities)
        ]
    return DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS


def _unpack_entity_checklist_entry(entry):
    """Most DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS-style entries are a
    plain (section, item_text) pair; a BUSINESS_IT_ENTITY_UNDERSTANDING_
    CHECKLIST_ITEMS entry can instead be a (section, item_text,
    response_options) triple - a list of custom answer choices for this one
    item, replacing the generic Yes/No/N-A dropdown (see
    EntityUnderstandingChecklistItem.response_options) for a question
    that's really "pick one of these" rather than yes/no. Returns
    (section, item_text, response_options), with response_options None for
    a plain pair."""
    if len(entry) == 3:
        return entry
    section, item_text = entry
    return section, item_text, None


@engagements_bp.route("/<int:engagement_id>/entity-understanding/checklist/seed", methods=["POST"])
@login_required
def seed_entity_checklist(engagement_id):
    """Populate the Understanding Business/Assignment checklist with the
    firm's default questions for this engagement's type (see
    _default_entity_checklist_items_for), seeded alongside (not instead of)
    the five free-text fields. Mirrors acceptance.seed_acceptance_checklist:
    only does anything the first time, so it's safe to expose as a single
    button - see sync_entity_checklist_items below for topping up a
    checklist that's already been seeded."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_entity_understanding(engagement_id)
    if record.checklist_items:
        flash("The checklist already has items on it.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))
    checklist_items = _default_entity_checklist_items_for(engagement)
    for order, entry in enumerate(checklist_items, start=1):
        section, item_text, response_options = _unpack_entity_checklist_entry(entry)
        db.session.add(EntityUnderstandingChecklistItem(
            entity_understanding_id=record.id,
            section=section,
            item_text=item_text,
            response_options="|".join(response_options) if response_options else None,
            order=order,
            created_by_id=current_user.id,
        ))
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Default checklist items added - tick and comment on each one.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/<int:engagement_id>/entity-understanding/checklist/sync", methods=["POST"])
@login_required
def sync_entity_checklist_items(engagement_id):
    """Top up an ALREADY-seeded checklist with any default questions that
    aren't on it yet (matched by exact item_text), without touching
    anything already there - existing items, their responses and comments
    are left completely alone. This is what lets a firm-wide update to one
    of the *_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS lists (e.g. a compound
    question later split into two, each with its own dropdown - see
    BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS) reach an engagement
    whose checklist was seeded before that update, without wiping out
    everything the team already filled in - seed_entity_checklist above
    only ever does anything the FIRST time, by design, so without this
    there'd be no way to pick up a later addition short of deleting and
    re-seeding the whole checklist from scratch."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_entity_understanding(engagement_id)
    if not record.checklist_items:
        flash("This checklist hasn't been seeded yet - use “Add the firm's default checklist” first.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))
    existing_texts = {i.item_text for i in record.checklist_items}
    max_order = max([i.order for i in record.checklist_items], default=0)
    checklist_items = _default_entity_checklist_items_for(engagement)
    added = 0
    for entry in checklist_items:
        section, item_text, response_options = _unpack_entity_checklist_entry(entry)
        if item_text in existing_texts:
            continue
        max_order += 1
        db.session.add(EntityUnderstandingChecklistItem(
            entity_understanding_id=record.id,
            section=section,
            item_text=item_text,
            response_options="|".join(response_options) if response_options else None,
            order=max_order,
            created_by_id=current_user.id,
        ))
        added += 1
    db.session.commit()
    if added:
        flash(f"Added {added} new default question{'s' if added != 1 else ''} that weren't on this checklist yet.", "success")
    else:
        flash("This checklist already has every current default question on it - nothing to add.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/<int:engagement_id>/entity-understanding/checklist/add", methods=["POST"])
@login_required
def add_entity_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_entity_understanding(engagement_id)
    item_text = request.form.get("item_text", "").strip()
    if not item_text:
        flash("Enter the checklist item's wording before adding it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))
    max_order = max([i.order for i in record.checklist_items], default=0)
    db.session.add(EntityUnderstandingChecklistItem(
        entity_understanding_id=record.id,
        section=request.form.get("section", "").strip(),
        item_text=item_text,
        order=max_order + 1,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_entity_checklist_item(item_id):
    item = EntityUnderstandingChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.entity_understanding.engagement)
    response = request.form.get("response", "").strip()
    # A custom-dropdown item (item.response_option_list - see
    # EntityUnderstandingChecklistItem.response_options) accepts its own
    # choices instead of the standard Yes/No/N-A list, since its dropdown
    # offers those in the template instead.
    allowed_responses = item.response_option_list or CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES
    item.response = response if response in allowed_responses else ""
    item.comment = request.form.get("comment", "").strip()
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=item.entity_understanding.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_entity_checklist_item(item_id):
    item = EntityUnderstandingChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.entity_understanding.engagement)
    engagement_id = item.entity_understanding.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


# ---------- Analytical Review (system-based: log figures, system flags fluctuations) ----------

def _get_or_create_analytical_review(engagement_id):
    review = AnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
    if not review:
        review = AnalyticalReview(engagement_id=engagement_id)
        db.session.add(review)
        db.session.flush()
    return review


def _touch_analytical_review(review):
    """Every change to the analytical review (a line added/edited/removed,
    or the threshold changed) re-records who last worked on it and clears
    any stale review sign-off, the same convention used everywhere else."""
    review.completed_by_id = current_user.id
    review.completed_at = datetime.utcnow()
    review.reviewed_by_id = None
    review.reviewed_at = None
    review.partner_signed_by_id = None
    review.partner_signed_at = None


@engagements_bp.route("/<int:engagement_id>/analytical-review/generate-from-tb", methods=["POST"])
@login_required
def generate_analytical_review_from_trial_balance(engagement_id):
    """Fills in Analytical Review line items automatically from the
    engagement's preliminary Trial Balance (entered/imported on the
    Trial Balance tab), instead of the auditor having to type in every current
    vs prior year figure by hand. Reuses financials.build_all_statements()
    - the same maths behind the Finalisation tab's financial statements -
    on the trial balance's PRELIMINARY (unadjusted) figures, since
    Analytical Review is a risk-assessment/planning procedure that happens
    before audit adjustments exist.

    Only ever touches lines it generated last time (source="auto"),
    matched by label - a manually-added line with the same label as a
    generated one, or an explanation already typed against a generated
    line, is left alone. Safe to run again after the trial balance changes."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the preliminary trial balance (Trial Balance tab) before generating analytical review figures from it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))
    if trial_balance.unmapped_count > 0:
        # compute_totals() (via build_all_statements) silently skips any
        # line with no IAS 1 category - generating with unmapped accounts
        # still there would file every figure as a confident-looking zero
        # instead of an honest "not ready yet", which is exactly the trap
        # this guard exists to avoid.
        flash(
            f"{trial_balance.unmapped_count} account(s) on the trial balance still need an IAS 1 category "
            "(see Account Mapping setup on the Trial Balance tab) before generating analytical review figures - "
            "until they're mapped, those accounts' amounts are left out and every figure below would come out as zero.",
            "danger",
        )
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    review = _get_or_create_analytical_review(engagement_id)
    statements = fin.build_all_statements(trial_balance.lines)
    sfp = statements["sfp"]
    total_liabilities_current = sfp["ncl_total"]["current"] + sfp["cl_total"]["current"]
    total_liabilities_prior = sfp["ncl_total"]["prior"] + sfp["cl_total"]["prior"]

    generated = [(r["label"], r["current"], r["prior"]) for r in statements["pl"]["rows"]]
    generated += [
        ("Total assets", sfp["total_assets"]["current"], sfp["total_assets"]["prior"]),
        ("Total liabilities", total_liabilities_current, total_liabilities_prior),
        ("Total equity", sfp["total_equity"]["current"], sfp["total_equity"]["prior"]),
    ]

    existing_auto = {l.label: l for l in review.lines if l.source == "auto"}
    for label, current, prior in generated:
        line = existing_auto.get(label)
        if line:
            line.current_amount = current
            line.prior_amount = prior
        else:
            db.session.add(AnalyticalReviewLine(
                analytical_review_id=review.id, label=label,
                current_amount=current, prior_amount=prior, source="auto",
            ))
    _touch_analytical_review(review)
    db.session.commit()
    flash("Analytical review figures generated from the preliminary trial balance.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/threshold", methods=["POST"])
@login_required
def save_analytical_review_threshold(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    try:
        threshold = float(request.form.get("threshold_pct", 10.0))
    except ValueError:
        threshold = 10.0
    review.threshold_pct = max(0.1, threshold)
    _touch_analytical_review(review)
    db.session.commit()
    flash("Significance threshold updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/necessity", methods=["POST"])
@login_required
def save_analytical_review_necessity(engagement_id):
    """Records whether Analytical Review has been ticked as NOT necessary
    for this assignment - only offered on Secretarial engagements (per the
    firm's Secretarial Analytical Review guidance: a one-off filing may not
    warrant a full governance/compliance review), but the route itself
    works for any engagement type since the underlying column is generic
    (see AnalyticalReview.not_necessary)."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    review.not_necessary = request.form.get("not_necessary") == "on"
    review.not_necessary_reason = request.form.get("not_necessary_reason", "").strip()
    _touch_analytical_review(review)
    db.session.commit()
    if review.not_necessary:
        flash("Analytical Review marked as not necessary for this assignment.", "info")
    else:
        flash("Analytical Review marked as necessary for this assignment.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/seed-secretarial", methods=["POST"])
@login_required
def seed_secretarial_analytical_review(engagement_id):
    """Populate the Analytical Review with the firm's Secretarial
    governance/compliance analytical points (see SECRETARIAL_ANALYTICAL_
    REVIEW_POINTS) - the Secretarial equivalent of generate_analytical_
    review_from_trial_balance above, seeding plain label + narrative-
    explanation lines (no $ amounts) instead of TB-derived figures, since
    secretarial analytical procedures look at compliance trends and
    governance metrics rather than financial ratios. Only adds points not
    already present (matched by label), so it's safe to run again."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    existing_labels = {l.label for l in review.lines}
    added = 0
    for section, point_text in SECRETARIAL_ANALYTICAL_REVIEW_POINTS:
        label = f"{section} - {point_text}"
        if label not in existing_labels:
            db.session.add(AnalyticalReviewLine(analytical_review_id=review.id, label=label, source="manual"))
            added += 1
    if added:
        _touch_analytical_review(review)
        db.session.commit()
        flash(f"Added {added} secretarial analytical review point(s) - write up the narrative explanation for each.", "success")
    else:
        flash("The default secretarial analytical review points are already there below.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/seed-business-it", methods=["POST"])
@login_required
def seed_business_it_analytical_review(engagement_id):
    """Populate the Analytical Review with the firm's IT-relevant analytical
    points (see BUSINESS_IT_ANALYTICAL_REVIEW_POINTS) - the Business
    Intelligence and IT Engagements equivalent of seed_secretarial_
    analytical_review above: system-generated operational/technical trend
    indicators (change volumes, alert counts, access-review completion,
    patch aging) rather than financial ratios. Only adds points not already
    present (matched by label), so it's safe to run again."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    existing_labels = {l.label for l in review.lines}
    added = 0
    for section, point_text in BUSINESS_IT_ANALYTICAL_REVIEW_POINTS:
        label = f"{section} - {point_text}"
        if label not in existing_labels:
            db.session.add(AnalyticalReviewLine(analytical_review_id=review.id, label=label, source="manual"))
            added += 1
    if added:
        _touch_analytical_review(review)
        db.session.commit()
        flash(f"Added {added} IT-relevant analytical review point(s) - write up the narrative explanation for each.", "success")
    else:
        flash("The default IT-relevant analytical review points are already there below.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/lines/add", methods=["POST"])
@login_required
def add_analytical_review_line(engagement_id):
    """Adds one Analytical Review line - either typed in by hand (label +
    prior/current amounts), or picked from a dropdown of the engagement's
    own trial balance accounts (tb_line_id), in which case the account's
    name and its prior/current net movement (debit minus credit, for each
    period) are used automatically instead of typing them in. Picking an
    account is the recommended path once a trial balance exists: it's the
    same account, guaranteed to agree with the trial balance, rather than
    a manually retyped figure that can drift out of step with it."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)

    def _float_or_none(name):
        raw = request.form.get(name, "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    tb_line_id = request.form.get("tb_line_id", "").strip()
    label = request.form.get("label", "").strip()
    prior_amount = _float_or_none("prior_amount")
    current_amount = _float_or_none("current_amount")

    if tb_line_id.isdigit():
        tb_line = TrialBalanceLine.query.get(int(tb_line_id))
        if not tb_line or tb_line.trial_balance.engagement_id != engagement_id:
            flash("That account doesn't belong to this engagement's trial balance.", "danger")
            return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))
        label = label or tb_line.account_name
        prior_amount = (tb_line.prior_debit or 0.0) - (tb_line.prior_credit or 0.0)
        current_amount = (tb_line.current_debit or 0.0) - (tb_line.current_credit or 0.0)

    if not label:
        flash("Please name the line item (e.g. Revenue, Gross profit) or pick an account from the trial balance.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))

    line = AnalyticalReviewLine(
        analytical_review_id=review.id,
        label=label,
        prior_amount=prior_amount,
        current_amount=current_amount,
        explanation=request.form.get("explanation", "").strip(),
        source="tb_account" if tb_line_id.isdigit() else "manual",
    )
    db.session.add(line)
    _touch_analytical_review(review)
    db.session.commit()
    flash("Line added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_analytical_review_line(line_id):
    line = AnalyticalReviewLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.review.engagement)
    review = line.review

    def _float_or_none(name, current):
        raw = request.form.get(name)
        if raw is None:
            return current
        raw = raw.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return current

    line.label = request.form.get("label", line.label).strip() or line.label
    line.prior_amount = _float_or_none("prior_amount", line.prior_amount)
    line.current_amount = _float_or_none("current_amount", line.current_amount)
    line.explanation = request.form.get("explanation", line.explanation or "").strip()
    # A hand-edit "claims" this line from Generate from Trial Balance - it
    # will no longer be silently overwritten by a later regenerate.
    line.source = "manual"
    _touch_analytical_review(review)
    db.session.commit()
    flash("Line updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_analytical_review_line(line_id):
    line = AnalyticalReviewLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.review.engagement)
    review = line.review
    engagement_id = review.engagement_id
    db.session.delete(line)
    _touch_analytical_review(review)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/review", methods=["POST"])
@login_required
def review_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not review.lines:
        flash("Add at least one line item before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    if review.completed_by_id == current_user.id:
        flash("You can't review your own analytical review - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    review.reviewed_by_id = current_user.id
    review.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Analytical review marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/unreview", methods=["POST"])
@login_required
def unreview_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    review.reviewed_by_id = None
    review.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not review.lines:
        flash("Add at least one line item before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    if review.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an analytical review you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    review.partner_signed_by_id = current_user.id
    review.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    review.partner_signed_by_id = None
    review.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


# ---------- Risk Assessment (system-based questionnaire) ----------

@engagements_bp.route("/<int:engagement_id>/risk-assessment/save", methods=["POST"])
@login_required
def save_risk_assessment(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
    if not assessment:
        assessment = RiskAssessment(engagement_id=engagement_id)
        db.session.add(assessment)

    # An Investigative Engagement is answered against the Fraud Triangle
    # questionnaire (FORENSIC_RISK_LIKELIHOOD_QUESTIONS/_IMPACT_QUESTIONS),
    # and a Business Intelligence and IT Engagement against the cyber/IT/
    # AML-CFT control questionnaire (BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS/
    # _IMPACT_QUESTIONS) - every other type keeps the ordinary audit
    # questionnaire. Must match view_engagement's likelihood_questions/
    # impact_questions above exactly, or a submitted form's fields wouldn't
    # line up with what gets saved.
    if engagement.type == "Investigative Engagement":
        questions = FORENSIC_RISK_LIKELIHOOD_QUESTIONS + FORENSIC_RISK_IMPACT_QUESTIONS
    elif engagement.type == "Business Intelligence and IT Engagements":
        questions = BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS + BUSINESS_IT_RISK_IMPACT_QUESTIONS
    elif engagement.type == "Secretarial":
        questions = SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS + SECRETARIAL_RISK_IMPACT_QUESTIONS
    else:
        questions = RISK_LIKELIHOOD_QUESTIONS + RISK_IMPACT_QUESTIONS

    for field, _, options in questions:
        raw = request.form.get(field)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = None
        if value is not None and 1 <= value <= len(options):
            setattr(assessment, field, value)

    assessment.notes = request.form.get("notes", "").strip()
    assessment.completed_by_id = current_user.id
    assessment.completed_at = datetime.utcnow()
    # Re-answering the questionnaire invalidates any earlier review/partner
    # sign-off - the updated assessment needs a fresh look.
    assessment.reviewed_by_id = None
    assessment.reviewed_at = None
    assessment.partner_signed_by_id = None
    assessment.partner_signed_at = None
    db.session.flush()

    # Keep the Substantive Procedures checklist in sync with the rating
    # that was just (re)computed, rather than leaving it static at whatever
    # it was the last time someone clicked "Generate suggested procedures" -
    # e.g. moving from Medium to High automatically adds the extra
    # high-risk procedures for every area already in use. Only runs if that
    # section has actually been started (see sync_substantive_procedures_if_started).
    added_count = sync_substantive_procedures_if_started(engagement_id)

    db.session.commit()
    if added_count:
        flash(
            f"Risk assessment saved - rating computed automatically below. "
            f"{added_count} suggested procedure(s) were also added to the Substantive Procedures checklist to match the updated rating.",
            "success",
        )
    else:
        flash("Risk assessment saved - rating computed automatically below.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/review", methods=["POST"])
@login_required
def review_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not assessment.is_complete:
        flash("The questionnaire needs to be fully answered before it can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    if assessment.completed_by_id == current_user.id:
        flash("You can't review your own risk assessment - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    assessment.reviewed_by_id = current_user.id
    assessment.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Risk assessment marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/unreview", methods=["POST"])
@login_required
def unreview_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    assessment.reviewed_by_id = None
    assessment.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not assessment.is_complete:
        flash("The questionnaire needs to be fully answered before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    if assessment.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a risk assessment you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    assessment.partner_signed_by_id = current_user.id
    assessment.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    assessment.partner_signed_by_id = None
    assessment.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risks/<int:risk_id>/delete", methods=["POST"])
@login_required
def delete_risk(risk_id):
    risk = RiskItem.query.get_or_404(risk_id)
    _ensure_engagement_access(risk.engagement)
    engagement_id = risk.engagement_id
    db.session.delete(risk)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="risks"))


# ---------- Documents ----------

def _allowed_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def _save_engagement_document(engagement_id, file, category, reference, notes, substantive_area_id=None, filing_index_id=None):
    """Shared save logic for an engagement working paper/document - used by
    both the general Documents tab upload and by filing a working paper
    directly under a Substantive Procedures area. Saves the file to disk,
    works out its version number (same name+category counts as a new
    version), and returns the new (uncommitted) Document row; the caller is
    responsible for db.session.commit()."""
    original_name = secure_filename(file.filename)

    # simple versioning: count existing docs with same original name+category in this engagement
    existing = Document.query.filter_by(
        engagement_id=engagement_id, original_filename=original_name, category=category
    ).count()
    version = existing + 1

    stored_name = f"eng{engagement_id}_{uuid.uuid4().hex[:10]}_{original_name}"
    file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name))

    doc = Document(
        engagement_id=engagement_id,
        original_filename=original_name,
        stored_filename=stored_name,
        category=category,
        reference=reference,
        version=version,
        notes=notes,
        uploaded_by_id=current_user.id,
        substantive_area_id=substantive_area_id,
        filing_index_id=filing_index_id,
    )
    db.session.add(doc)
    return doc, version


def _read_new_file(kind, row):
    """Read the text of a file just uploaded (its own text, or read from a scan) so it can be searched. Never blocks or fails an upload."""
    try:
        import scan_text
        scan_text.start(kind, row)
    except Exception:
        pass


@engagements_bp.route("/<int:engagement_id>/documents/upload", methods=["POST"])
@login_required
def upload_document(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))

    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))

    category = request.form.get("category", "General").strip() or "General"
    reference = request.form.get("reference", "").strip()
    filing_index_id = request.form.get("filing_index_id", "").strip()
    filing_index_id = int(filing_index_id) if filing_index_id.isdigit() else None
    doc, version = _save_engagement_document(
        engagement_id, file, category, reference, request.form.get("notes", "").strip(),
        filing_index_id=filing_index_id,
    )
    db.session.commit()
    _read_new_file("engagement_doc", doc)
    flash(f"Uploaded '{doc.original_filename}' (v{version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))


@engagements_bp.route("/<int:engagement_id>/substantive-procedures/areas/<area_name>/documents/upload", methods=["POST"])
@login_required
def upload_substantive_area_document(engagement_id, area_name):
    """File a working paper directly under one Substantive Procedures
    section (e.g. "Cash and Bank") - the same underlying Document as the
    Documents tab, just tagged with which audit area it supports, and
    filed under a category named after that area so it's easy to spot in
    the general Documents list too."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    valid_areas = _substantive_areas_for(engagement)
    if area_name not in valid_areas:
        abort(404)

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))

    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))

    area = _get_or_create_substantive_area(engagement_id, area_name)
    reference = request.form.get("reference", "").strip()
    filing_index_id = request.form.get("filing_index_id", "").strip()
    filing_index_id = int(filing_index_id) if filing_index_id.isdigit() else None
    doc, version = _save_engagement_document(
        engagement_id, file, area_name, reference, request.form.get("notes", "").strip(),
        substantive_area_id=area.id, filing_index_id=filing_index_id,
    )
    db.session.commit()
    _read_new_file("engagement_doc", doc)
    flash(f"Filed '{doc.original_filename}' (v{version}) under {area_name}.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    # require_accepted=False: the signed engagement letter (uploaded via the
    # Client Acceptance tab, itself an ordinary Document) has to be
    # downloadable/viewable WHILE acceptance is still pending, otherwise
    # nobody could ever check it before signing off the decision.
    _ensure_engagement_access(doc.engagement, require_accepted=False)
    # When tagged with a WP number from the Filing Index, the download
    # follows the firm's N[code]_[Description]_[Year] filing convention
    # instead of the plain original filename.
    download_name = doc.original_filename
    if doc.filing_index and doc.filing_index.code:
        stem, _, ext = doc.original_filename.rpartition(".")
        description = stem or doc.original_filename
        year = doc.engagement.period_end.year if doc.engagement.period_end else date.today().year
        candidate = f"{doc.filing_index.code}_{description}_{year}.{ext}" if ext else f"{doc.filing_index.code}_{description}_{year}"
        download_name = secure_filename(candidate) or doc.original_filename
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], doc.stored_filename, as_attachment=True,
        download_name=download_name,
    )


@engagements_bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_document(doc_id):
    if not user_has_permission(current_user, "delete_documents"):
        abort(403)
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    engagement_id = doc.engagement_id
    return_tab = "substantive" if doc.substantive_area_id else "documents"
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_filename))
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=return_tab))


# ---------- Generated workpapers (filed under Documents, sign-off tracked) ----------
#
# Every downloadable working paper the app can build (Financial Statements,
# Trial Balance & Adjustments, the checklists, Substantive Procedures, etc)
# is generated and FILED here rather than streamed straight to the
# browser - it becomes an ordinary Document (tagged with the firm's N-code
# filing reference where one applies, exactly like a manually-uploaded and
# WP-No.-tagged file), with its own Prepared/Reviewed/Partner sign-off.
# Downloading it is then a separate, explicit action on that filed copy
# (the existing download_document route above) rather than something that
# happens automatically the moment it's generated.

# Which engagement tab each generated workpaper's routes should redirect
# back to, and its return-tab default for when something goes wrong (e.g.
# missing trial balance).
WORKPAPER_KIND_TABS = {
    "financial_statements": "finalisation",
    "trial_balance_adjustments": "finalisation",
    "rep_letter": "finalisation",
    "report_to_management": "finalisation",
    "forensic_report": "finalisation",
    "it_audit_report": "finalisation",
    "file_summary": "finalisation",
    "finalisation_checklist": "finalisation",
    "acceptance_checklist": "acceptance",
    "entity_checklist": "entity",
    "engagement_checklist": "checklist",
    "substantive_procedures_docx": "substantive",
    "substantive_procedures_xlsx": "substantive",
}


def _filed_workpapers_by_kind(engagement_id):
    """Every generated-and-filed workpaper for this engagement, grouped by
    kind and ordered newest version first - what each tab's "Filed
    versions" list renders. A dict of lists, never missing a key that's
    actually been generated at least once."""
    docs = (
        Document.query
        .filter_by(engagement_id=engagement_id, is_generated=True)
        .order_by(Document.workpaper_kind, Document.version.desc())
        .all()
    )
    by_kind = {}
    for d in docs:
        by_kind.setdefault(d.workpaper_kind, []).append(d)
    return by_kind


def _file_generated_workpaper(engagement, kind, category_label, filing_code, description_stub, ext, buf, reference_override=None):
    """Generate-and-file step shared by every "Generate & File" button: saves
    the already-built file (buf, a BytesIO from workpapers.py) to disk and
    records it as a new Document, following the same N[code]_[Description]
    filing convention as a manually-tagged upload whenever filing_code
    matches a real Filing Index entry (the download route then adds the
    year and applies it automatically, exactly like any other WP-No.-tagged
    Document) - otherwise falls back to a plain descriptive filename, same
    as the old direct-download naming did for sections with no single N-code
    (the Engagement Checklist, Substantive Procedures, the Forensic report).

    Every previous version of this SAME kind for this engagement is kept
    (per the firm's own Filing Index policy: "superseded, not overwritten")
    - only its is_current_version flag flips to False. The new version
    starts with a clean slate for Reviewed/Partner-signed, since a freshly
    generated file is new work needing its own review."""
    filing_section = FilingIndexSection.query.filter_by(code=filing_code).first() if filing_code else None

    prior_versions = Document.query.filter_by(engagement_id=engagement.id, workpaper_kind=kind).all()
    for p in prior_versions:
        p.is_current_version = False
    version = len(prior_versions) + 1

    stored_name = f"eng{engagement.id}_wp_{kind}_{uuid.uuid4().hex[:10]}.{ext}"
    with open(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name), "wb") as f:
        f.write(buf.getvalue())

    doc = Document(
        engagement_id=engagement.id,
        original_filename=f"{description_stub}.{ext}",
        stored_filename=stored_name,
        category=category_label,
        reference=reference_override or filing_code,
        version=version,
        uploaded_by_id=current_user.id,
        uploaded_at=datetime.utcnow(),
        filing_index_id=filing_section.id if filing_section else None,
        is_generated=True,
        workpaper_kind=kind,
        is_current_version=True,
    )
    db.session.add(doc)
    db.session.commit()
    return doc


@engagements_bp.route("/documents/<int:doc_id>/review", methods=["POST"])
@login_required
def review_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if doc.uploaded_by_id == current_user.id:
        flash("You can't review a working paper you generated yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))
    doc.reviewed_by_id = current_user.id
    doc.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Working paper marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


@engagements_bp.route("/documents/<int:doc_id>/unreview", methods=["POST"])
@login_required
def unreview_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    doc.reviewed_by_id = None
    doc.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


@engagements_bp.route("/documents/<int:doc_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if doc.uploaded_by_id == current_user.id:
        flash("You can't give the partner sign-off on a working paper you generated yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))
    doc.partner_signed_by_id = current_user.id
    doc.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


@engagements_bp.route("/documents/<int:doc_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    doc.partner_signed_by_id = None
    doc.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


# ---------- Tasks ----------

@engagements_bp.route("/<int:engagement_id>/tasks/add", methods=["POST"])
@login_required
def add_task(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    due_date = request.form.get("due_date")
    category = resolve_task_category(request.form.get("category"), engagement)
    task = EngagementTask(
        engagement_id=engagement_id,
        title=request.form.get("title", "").strip(),
        description=request.form.get("description", "").strip(),
        assigned_to_id=int(request.form.get("assigned_to_id")) if request.form.get("assigned_to_id") else None,
        due_date=datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None,
        category=category,
        # Tax Services is always High (penalties) - see effective_task_priority.
        priority=effective_task_priority(category, request.form.get("priority", "Normal")),
        status=request.form.get("status", "To Do"),
    )
    db.session.add(task)
    db.session.flush()
    if task.assigned_to_id and task.assigned_to_id != current_user.id:
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task assigned: {task.title}",
            f"{current_user.name} assigned you a task on {engagement.client.name} - {engagement.title}:\n\n"
            f"{task.title}\n"
            + (f"Due {task.due_date.strftime('%d %b %Y')}\n" if task.due_date else "")
            + (f"\n{task.description}" if task.description else ""),
        )
    db.session.commit()
    flash("Task assigned.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tasks"))


def _task_redirect(task, tab="tasks"):
    """Most task actions are reachable both from an engagement's Tasks tab
    and from the firm-wide Project Management board - a hidden
    return_to=board field on those forms sends the user back to the board
    instead of the engagement page."""
    if request.form.get("return_to") == "board":
        return redirect(url_for("hr.project_board"))
    return redirect(url_for("engagements.view_engagement", engagement_id=task.engagement_id, tab=tab))
