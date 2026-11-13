"""engagements, part 6 of 7.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file engagements.py did."""

def _ppe_movement_and_policy(class_dicts, asset_dicts, period_end):
    """The (ppe_movement, ppe_policy_text) pair fin.build_all_statements()
    takes to replace the generic PPE note/policy with the Asset Register's
    own movement schedule and the Depreciation policy's own wording - both
    come back None (no change in behaviour) when neither working paper has
    any rows yet, so an engagement that hasn't touched this feature is
    completely unaffected. Takes the plain dicts _ppe_plain_data() already
    built, rather than re-querying, since callers usually need both."""
    ppe_movement = fin.build_ppe_movement_schedule(class_dicts, asset_dicts, period_end)
    ppe_policy_text = fin.ppe_accounting_policy_text(class_dicts)
    return ppe_movement, ppe_policy_text


# ---------- Directors' Statement (Finalisation tab) ----------

def _get_or_create_directors_statement(engagement):
    record = DirectorsStatement.query.filter_by(engagement_id=engagement.id).first()
    if not record:
        directors = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, role="Director", status="Confirmed").order_by(ClientKeyPerson.id).all()
        record = DirectorsStatement(
            engagement_id=engagement.id, statement_text=fin.default_directors_statement_text(engagement),
            director1_name=directors[0].full_name if len(directors) > 0 else None,
            director2_name=directors[1].full_name if len(directors) > 1 else None,
            statement_date=engagement.period_end,
        )
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/directors-statement/save", methods=["POST"])
@login_required
def save_directors_statement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_directors_statement(engagement)
    record.statement_text = request.form.get("statement_text", "").strip()
    record.director1_name = request.form.get("director1_name", "").strip() or None
    record.director1_title = request.form.get("director1_title", "").strip() or "Director"
    record.director2_name = request.form.get("director2_name", "").strip() or None
    record.director2_title = request.form.get("director2_title", "").strip() or "Director"
    record.statement_date = _parse_date(request.form.get("statement_date")) or engagement.period_end
    record.updated_by_id = current_user.id
    record.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Directors' Statement saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/directors-statement/use-directors-on-file", methods=["POST"])
@login_required
def use_directors_on_file(engagement_id):
    """Re-pulls director names from this client's confirmed Directors &
    Shareholders list (Client detail page) - a one-click refresh for when
    that list has changed since the statement was first seeded, rather
    than the preparer having to retype names by hand."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_directors_statement(engagement)
    directors = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, role="Director", status="Confirmed").order_by(ClientKeyPerson.id).all()
    if not directors:
        flash("No confirmed Directors are on file for this client yet - add them on the client's Directors & Shareholders list first.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    record.director1_name = directors[0].full_name if len(directors) > 0 else None
    record.director2_name = directors[1].full_name if len(directors) > 1 else None
    record.updated_by_id = current_user.id
    record.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Director names refreshed from the client's Directors & Shareholders list.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


# ---------- Audit Opinion / Review Report (Finalisation tab) ----------

def _get_or_create_audit_opinion(engagement):
    record = AuditOpinion.query.filter_by(engagement_id=engagement.id).first()
    if not record:
        report_basis = "audit" if engagement.type == "Audit" else "review"
        defaults = fin.default_audit_opinion_paragraphs(
            report_basis, "unmodified", engagement.client.name, engagement.period_end,
            REPORTING_FRAMEWORK_LABELS.get(engagement.reporting_framework),
        )
        record = AuditOpinion(engagement_id=engagement.id, report_basis=report_basis, modification="unmodified", report_date=engagement.period_end, **defaults)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/audit-opinion/save", methods=["POST"])
@login_required
def save_audit_opinion(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_audit_opinion(engagement)
    record.report_basis = request.form.get("report_basis") if request.form.get("report_basis") in AUDIT_OPINION_BASES else record.report_basis
    record.modification = request.form.get("modification") if request.form.get("modification") in OPINION_MODIFICATIONS else record.modification
    record.basis_for_modification = request.form.get("basis_for_modification", "").strip()
    record.opinion_paragraph = request.form.get("opinion_paragraph", "").strip()
    record.basis_paragraph = request.form.get("basis_paragraph", "").strip()
    record.management_responsibility_paragraph = request.form.get("management_responsibility_paragraph", "").strip()
    record.auditor_responsibility_paragraph = request.form.get("auditor_responsibility_paragraph", "").strip()
    record.report_date = _parse_date(request.form.get("report_date")) or engagement.period_end
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Audit Opinion / Review Report saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/audit-opinion/regenerate-wording", methods=["POST"])
@login_required
def regenerate_audit_opinion_wording(engagement_id):
    """Rebuilds the four report paragraphs from scratch for the currently
    selected report basis/modification - for when the preparer has changed
    either of those and wants fresh standard wording to start editing from,
    rather than a stale paragraph left over from the previous choice."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_audit_opinion(engagement)
    report_basis = request.form.get("report_basis") if request.form.get("report_basis") in AUDIT_OPINION_BASES else record.report_basis
    modification = request.form.get("modification") if request.form.get("modification") in OPINION_MODIFICATIONS else record.modification
    defaults = fin.default_audit_opinion_paragraphs(
        report_basis, modification, engagement.client.name, engagement.period_end,
        REPORTING_FRAMEWORK_LABELS.get(engagement.reporting_framework),
    )
    record.report_basis = report_basis
    record.modification = modification
    for k, v in defaults.items():
        setattr(record, k, v)
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Standard wording regenerated for the selected basis/modification - review and tailor it before issuing.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/review", methods=["POST"])
@login_required
def review_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review an Audit Opinion you drafted yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Audit Opinion marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/unreview", methods=["POST"])
@login_required
def unreview_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an Audit Opinion you drafted yourself - ask another partner to sign it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded - the report is now issued.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


# ---------- Income Tax Computation tab ----------

def _get_or_create_income_tax_computation(engagement_id):
    record = IncomeTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = IncomeTaxComputation(engagement_id=engagement_id, tax_loss_brought_forward=0.0)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/income-tax/save", methods=["POST"])
@login_required
def save_income_tax_computation(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_income_tax_computation(engagement_id)
    record.tax_rate_percent = _parse_float(request.form.get("tax_rate_percent"))
    record.aids_levy_percent = _parse_float(request.form.get("aids_levy_percent"))
    record.tax_loss_brought_forward = _parse_float(request.form.get("tax_loss_brought_forward")) or 0.0
    if "profit_before_tax_manual" in request.form:
        raw = request.form.get("profit_before_tax_manual", "").strip()
        record.profit_before_tax_manual = _parse_float(raw) if raw else None
    record.notes = request.form.get("notes", "").strip()
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Income Tax Computation saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="income_tax"))


@engagements_bp.route("/<int:engagement_id>/income-tax/lines/add", methods=["POST"])
@login_required
def add_income_tax_line(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this line.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="income_tax"))
    record = _get_or_create_income_tax_computation(engagement_id)
    item_type = request.form.get("item_type") if request.form.get("item_type") in INCOME_TAX_ITEM_TYPES else "addback"
    order = IncomeTaxAdjustmentLine.query.filter_by(computation_id=record.id).count()
    db.session.add(IncomeTaxAdjustmentLine(
        computation_id=record.id, item_type=item_type, description=description,
        amount=_parse_float(request.form.get("amount")) or 0.0, order=order,
    ))
    db.session.commit()
    flash("Line added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_income_tax_line(line_id):
    line = IncomeTaxAdjustmentLine.query.get_or_404(line_id)
    engagement = line.computation.engagement
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this line.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="income_tax"))
    line.item_type = request.form.get("item_type") if request.form.get("item_type") in INCOME_TAX_ITEM_TYPES else line.item_type
    line.description = description
    line.amount = _parse_float(request.form.get("amount")) or 0.0
    db.session.commit()
    flash("Line updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="income_tax"))


@engagements_bp.route("/income-tax/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_income_tax_line(line_id):
    line = IncomeTaxAdjustmentLine.query.get_or_404(line_id)
    engagement = line.computation.engagement
    _ensure_engagement_access(engagement)
    db.session.delete(line)
    db.session.commit()
    flash("Line removed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/review", methods=["POST"])
@login_required
def review_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review an Income Tax Computation you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Income Tax Computation marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/unreview", methods=["POST"])
@login_required
def unreview_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an Income Tax Computation you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


# ---------- Deferred Tax Computation tab ----------

def _get_or_create_deferred_tax_computation(engagement_id):
    record = DeferredTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = DeferredTaxComputation(engagement_id=engagement_id)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/deferred-tax/save", methods=["POST"])
@login_required
def save_deferred_tax_computation(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_deferred_tax_computation(engagement_id)
    record.tax_rate_percent = _parse_float(request.form.get("tax_rate_percent"))
    record.prior_tax_rate_percent = _parse_float(request.form.get("prior_tax_rate_percent"))
    record.ppe_tax_base_mode = "manual" if request.form.get("ppe_tax_base_mode") == "manual" else "auto"
    if record.ppe_tax_base_mode == "manual" or request.form.get("ppe_tax_base", "").strip():
        record.ppe_tax_base = _parse_float(request.form.get("ppe_tax_base"))
    record.notes = request.form.get("notes", "").strip()
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Deferred Tax Computation saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="deferred_tax"))


@engagements_bp.route("/<int:engagement_id>/deferred-tax/items/add", methods=["POST"])
@login_required
def add_deferred_tax_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this temporary difference.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="deferred_tax"))
    record = _get_or_create_deferred_tax_computation(engagement_id)
    order = DeferredTaxItem.query.filter_by(computation_id=record.id).count()
    db.session.add(DeferredTaxItem(
        computation_id=record.id, description=description,
        accounting_amount=_parse_float(request.form.get("accounting_amount")) or 0.0,
        tax_base_amount=_parse_float(request.form.get("tax_base_amount")) or 0.0,
        prior_accounting_amount=_parse_float(request.form.get("prior_accounting_amount")),
        prior_tax_base_amount=_parse_float(request.form.get("prior_tax_base_amount")),
        order=order,
    ))
    db.session.commit()
    flash("Temporary difference added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/items/<int:item_id>/update", methods=["POST"])
@login_required
def update_deferred_tax_item(item_id):
    item = DeferredTaxItem.query.get_or_404(item_id)
    engagement = item.computation.engagement
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this temporary difference.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="deferred_tax"))
    item.description = description
    item.accounting_amount = _parse_float(request.form.get("accounting_amount")) or 0.0
    item.tax_base_amount = _parse_float(request.form.get("tax_base_amount")) or 0.0
    item.prior_accounting_amount = _parse_float(request.form.get("prior_accounting_amount"))
    item.prior_tax_base_amount = _parse_float(request.form.get("prior_tax_base_amount"))
    db.session.commit()
    flash("Temporary difference updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_deferred_tax_item(item_id):
    item = DeferredTaxItem.query.get_or_404(item_id)
    engagement = item.computation.engagement
    _ensure_engagement_access(engagement)
    db.session.delete(item)
    db.session.commit()
    flash("Temporary difference removed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/review", methods=["POST"])
@login_required
def review_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review a Deferred Tax Computation you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Deferred Tax Computation marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/unreview", methods=["POST"])
@login_required
def unreview_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a Deferred Tax Computation you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


def _parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _asset_tax_value(value):
    """Tax value brought forward: blank = rebuild; 0, 'nil' or '-' = a nil tax value (see asset_schedules._tax_value)."""
    import asset_schedules
    return asset_schedules._tax_value(value)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _ppe_tab(engagement):
    """Where the Asset Register lives: the Substantive Procedures tab, or its own Asset Register tab on a Tax Advisory engagement. A change
    made from the Capital Allowances tab goes back there."""
    if "tab=capital_allowances" in (request.referrer or ""):
        return "capital_allowances"
    if "tab=asset_register" in (request.referrer or "") and engagement.has_asset_schedules:
        return "asset_register"
    return "asset_register" if engagement.is_tax_advisory_type else "substantive"


@engagements_bp.route("/<int:engagement_id>/ppe/asset-classes/add", methods=["POST"])
@login_required
def add_ppe_asset_class(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter an asset class name.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_ppe_tab(engagement)))
    import asset_schedules as _asched
    name = _asched.canonical_class_name(name)
    order = PPEAssetClass.query.filter_by(engagement_id=engagement_id).count()
    db.session.add(PPEAssetClass(
        engagement_id=engagement_id, name=name,
        depreciation_method=request.form.get("depreciation_method") or "Straight-line",
        rate_percent=_parse_float(request.form.get("rate_percent")),
        useful_life_years=_parse_float(request.form.get("useful_life_years")),
        order=order,
    ))
    db.session.commit()
    flash("Asset class added to the Depreciation policy.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_ppe_tab(engagement)))


@engagements_bp.route("/ppe/asset-classes/<int:class_id>/update", methods=["POST"])
@login_required
def update_ppe_asset_class(class_id):
    asset_class = PPEAssetClass.query.get_or_404(class_id)
    engagement = asset_class.engagement
    _ensure_engagement_access(engagement)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter an asset class name.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=_ppe_tab(engagement)))
    import asset_schedules as _asched
    name = _asched.canonical_class_name(name)                # 'Premises' / 'Land & Buildings' are called Land and Buildings
    asset_class.depreciation_method = request.form.get("depreciation_method") or "Straight-line"
    asset_class.rate_percent = _parse_float(request.form.get("rate_percent"))
    asset_class.useful_life_years = _parse_float(request.form.get("useful_life_years"))
    same = [c for c in PPEAssetClass.query.filter_by(engagement_id=engagement.id).all()
            if c.id != asset_class.id and c.name.strip().lower() == name.lower()]
    if same:                                                  # the new name is a class this engagement already has: merge into it
        keep = same[0]
        moved = 0
        for a in PPEAsset.query.filter_by(asset_class_id=asset_class.id).all():
            a.asset_class_id = keep.id
            moved += 1
        if keep.rate_percent is None and asset_class.rate_percent is not None:
            keep.rate_percent, keep.useful_life_years = asset_class.rate_percent, asset_class.useful_life_years
        keep.tb_aliases = "|".join(sorted({k for k in ((keep.tb_aliases or "") + "|" + (asset_class.tb_aliases or "")).split("|") if k})) or None
        db.session.delete(asset_class)
        db.session.commit()
        flash(f"'{name}' already existed, so the two classes were merged: {moved} asset(s) now sit under '{keep.name}'.", "success")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=_ppe_tab(engagement)))
    asset_class.name = name
    db.session.commit()
    flash("Asset class updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=_ppe_tab(engagement)))


@engagements_bp.route("/ppe/asset-classes/<int:class_id>/delete", methods=["POST"])
@login_required
def delete_ppe_asset_class(class_id):
    asset_class = PPEAssetClass.query.get_or_404(class_id)
    engagement = asset_class.engagement
    _ensure_engagement_access(engagement)
    # Assets in this class aren't deleted - they just fall back to
    # "Unclassified" in the Asset Register/movement schedule, same as an
    # asset that was never assigned a class, rather than losing their data.
    for a in asset_class.assets:
        a.asset_class_id = None
    db.session.delete(asset_class)
    db.session.commit()
    flash("Asset class removed from the Depreciation policy. Its assets are now Unclassified in the Asset Register.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=_ppe_tab(engagement)))


@engagements_bp.route("/<int:engagement_id>/ppe/assets/add", methods=["POST"])
@login_required
def add_ppe_asset(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for the asset.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_ppe_tab(engagement)))
    asset_class_id = request.form.get("asset_class_id") or None
    order = PPEAsset.query.filter_by(engagement_id=engagement_id).count()
    db.session.add(PPEAsset(
        engagement_id=engagement_id,
        asset_class_id=int(asset_class_id) if asset_class_id else None,
        asset_code=request.form.get("asset_code", "").strip() or None,
        description=description,
        date_acquired=_parse_date(request.form.get("date_acquired")),
        cost=_parse_float(request.form.get("cost")) or 0.0,
        opening_accumulated_depreciation=_parse_float(request.form.get("opening_accumulated_depreciation")) or 0.0,
        current_year_depreciation=_parse_float(request.form.get("current_year_depreciation")) or 0.0,
        disposal_cost=_parse_float(request.form.get("disposal_cost")) or 0.0,
        disposal_accumulated_depreciation=_parse_float(request.form.get("disposal_accumulated_depreciation")) or 0.0,
        opening_tax_value=_asset_tax_value(request.form.get("opening_tax_value")),
        order=order,
    ))
    db.session.commit()
    flash("Asset added to the Asset Register.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_ppe_tab(engagement)))


@engagements_bp.route("/ppe/assets/<int:asset_id>/update", methods=["POST"])
@login_required
def update_ppe_asset(asset_id):
    asset = PPEAsset.query.get_or_404(asset_id)
    engagement = asset.engagement
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for the asset.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=_ppe_tab(engagement)))
    asset_class_id = request.form.get("asset_class_id") or None
    asset.asset_class_id = int(asset_class_id) if asset_class_id else None
    asset.asset_code = request.form.get("asset_code", "").strip() or None
    asset.description = description
    asset.date_acquired = _parse_date(request.form.get("date_acquired"))
    asset.cost = _parse_float(request.form.get("cost")) or 0.0
    asset.opening_accumulated_depreciation = _parse_float(request.form.get("opening_accumulated_depreciation")) or 0.0
    asset.current_year_depreciation = _parse_float(request.form.get("current_year_depreciation")) or 0.0
    asset.disposal_cost = _parse_float(request.form.get("disposal_cost")) or 0.0
    asset.disposal_accumulated_depreciation = _parse_float(request.form.get("disposal_accumulated_depreciation")) or 0.0
    if "opening_tax_value" in request.form:                  # forms without the field (the Capital Allowances tab) leave it alone
        asset.opening_tax_value = _asset_tax_value(request.form.get("opening_tax_value"))
    db.session.commit()
    flash("Asset updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=_ppe_tab(engagement)))


@engagements_bp.route("/ppe/assets/<int:asset_id>/delete", methods=["POST"])
@login_required
def delete_ppe_asset(asset_id):
    asset = PPEAsset.query.get_or_404(asset_id)
    engagement = asset.engagement
    _ensure_engagement_access(engagement)
    import asset_schedules
    asset_schedules.archive_asset(asset, current_user.id)             # a copy is kept so the deletion can be undone
    db.session.delete(asset)
    db.session.commit()
    flash("Asset removed from the Asset Register. It can be put back under 'Recently deleted' on the Asset Register tab.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=_ppe_tab(engagement)))


@engagements_bp.route("/<int:engagement_id>/workpapers/ppe-asset-register/generate", methods=["POST"])
@login_required
def generate_ppe_asset_register_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    classes, assets, _, _ = _ppe_plain_data(engagement_id)
    if not assets:
        flash("Add at least one asset to the Asset Register before generating this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_ppe_tab(engagement)))
    buf = wp.build_ppe_asset_register_xlsx(engagement, classes, assets)
    doc = _file_generated_workpaper(engagement, "ppe_asset_register", "Property, Plant and Equipment", "N3300", "PPE_Asset_Register", "xlsx", buf)
    flash(f"PPE Asset Register filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_ppe_tab(engagement)))


@engagements_bp.route("/<int:engagement_id>/substantive-procedures/areas/<area_name>/items/add", methods=["POST"])
@login_required
def add_substantive_procedure_item(engagement_id, area_name):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    text = request.form.get("procedure_text", "").strip()
    if not text:
        flash("Please enter the procedure text.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    area = _get_or_create_substantive_area(engagement_id, area_name)
    procedure_kind = request.form.get("procedure_kind", "").strip() or None
    if procedure_kind not in ("ITGC", "Substantive", None):
        procedure_kind = None
    db.session.add(SubstantiveProcedureItem(area_id=area.id, procedure_text=text, source="manual", order=len(area.items), procedure_kind=procedure_kind))
    _touch_substantive_area(area)
    db.session.commit()
    flash("Procedure added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/items/<int:item_id>/update", methods=["POST"])
@login_required
def update_substantive_procedure_item(item_id):
    item = SubstantiveProcedureItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.area_record.engagement)
    area = item.area_record
    item.procedure_text = request.form.get("procedure_text", item.procedure_text).strip() or item.procedure_text
    item.status = request.form.get("status", item.status)
    item.notes = request.form.get("notes", item.notes or "").strip()
    tickmark_id = request.form.get("tickmark_id", "").strip()
    item.tickmark_id = int(tickmark_id) if tickmark_id.isdigit() else None
    if "trigger_event" in request.form:
        item.trigger_event = request.form.get("trigger_event", "").strip() or None
    if "responsible_role" in request.form:
        item.responsible_role = request.form.get("responsible_role", "").strip() or None
    if "target_output" in request.form:
        item.target_output = request.form.get("target_output", "").strip() or None
    if "procedure_kind" in request.form:
        procedure_kind = request.form.get("procedure_kind", "").strip() or None
        item.procedure_kind = procedure_kind if procedure_kind in ("ITGC", "Substantive") else None
    _touch_substantive_area(area)
    db.session.commit()
    flash("Procedure updated." if area.engagement.type != "Secretarial" else "Task updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_substantive_procedure_item(item_id):
    item = SubstantiveProcedureItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.area_record.engagement)
    area = item.area_record
    engagement_id = area.engagement_id
    db.session.delete(item)
    _touch_substantive_area(area)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/notes", methods=["POST"])
@login_required
def save_substantive_area_notes(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    area.notes = request.form.get("notes", "").strip()
    _touch_substantive_area(area)
    db.session.commit()
    flash("Notes saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/review", methods=["POST"])
@login_required
def review_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not area.is_complete:
        flash("Every procedure in this area needs to be marked Done or N/A before the area can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    if area.completed_by_id == current_user.id:
        flash("You can't review an area you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    area.reviewed_by_id = current_user.id
    area.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Area marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/unreview", methods=["POST"])
@login_required
def unreview_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    area.reviewed_by_id = None
    area.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not area.is_complete:
        flash("Every procedure in this area needs to be marked Done or N/A before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    if area.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an area you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    area.partner_signed_by_id = current_user.id
    area.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    area.partner_signed_by_id = None
    area.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


# ---------- Review Queries (Partner/Reviewer review points on any section) ----------

def _query_redirect(query):
    """A query always sends you back to the section it was raised on - the
    Substantive Procedures tab for an area-scoped query, otherwise the tab
    named by the query's own section key (they're the same names)."""
    return redirect(url_for("engagements.view_engagement", engagement_id=query.engagement_id, tab=query.section))


@engagements_bp.route("/<int:engagement_id>/queries/raise", methods=["POST"])
@login_required
def raise_query(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)

    section = request.form.get("section", "")
    if section not in QUERY_SECTION_KEYS:
        abort(400)

    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()
    if not subject or not message:
        flash("Please give the query a subject and a message.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=section))

    # area_name only makes sense (and is only trusted) for "substantive" -
    # for every other section it's dropped even if somehow submitted.
    area_name = request.form.get("area_name", "").strip() or None
    if section != "substantive":
        area_name = None
    elif area_name:
        valid_areas = _substantive_areas_for(engagement)
        if area_name not in valid_areas:
            abort(400)

    query = EngagementQuery(
        engagement_id=engagement_id, section=section, area_name=area_name,
        subject=subject, message=message, raised_by_id=current_user.id,
    )
    db.session.add(query)
    db.session.commit()
    flash("Query raised.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=section))


@engagements_bp.route("/queries/<int:query_id>/reply", methods=["POST"])
@login_required
def reply_to_query(query_id):
    query = EngagementQuery.query.get_or_404(query_id)
    _ensure_engagement_access(query.engagement)
    message = request.form.get("message", "").strip()
    if not message:
        flash("Please enter a reply.", "danger")
        return _query_redirect(query)
    db.session.add(QueryReply(query_id=query.id, author_id=current_user.id, message=message))
    db.session.commit()
    flash("Reply added.", "success")
    return _query_redirect(query)


@engagements_bp.route("/queries/<int:query_id>/resolve", methods=["POST"])
@login_required
def resolve_query(query_id):
    query = EngagementQuery.query.get_or_404(query_id)
    _ensure_engagement_access(query.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not query.has_preparer_reply:
        flash("Waiting on a reply explaining how this was addressed before it can be marked resolved.", "danger")
        return _query_redirect(query)
    query.status = "Resolved"
    query.resolved_by_id = current_user.id
    query.resolved_at = datetime.utcnow()
    query.resolved_via_partner_override = False
    query.partner_override_notes = None
    db.session.commit()
    flash("Query marked as resolved.", "success")
    return _query_redirect(query)


@engagements_bp.route("/queries/<int:query_id>/resolve/partner-override", methods=["POST"])
@login_required
def partner_override_resolve_query(query_id):
    """The Engagement Partner (or Admin) is the ultimate reviewer and can
    resolve a query on their own judgement - e.g. discussed verbally with
    the team - without waiting on the written reply resolve_query above
    otherwise requires. Ordinary reviewers (Supervisor) don't get this
    override; they still need someone else's reply on record first."""
    query = EngagementQuery.query.get_or_404(query_id)
    _ensure_engagement_access(query.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    query.status = "Resolved"
    query.resolved_by_id = current_user.id
    query.resolved_at = datetime.utcnow()
    query.resolved_via_partner_override = True
    query.partner_override_notes = request.form.get("partner_override_notes", "").strip()
    db.session.commit()
    flash("Query resolved by Partner authorisation, without waiting for a written reply.", "success")
    return _query_redirect(query)


@engagements_bp.route("/queries/<int:query_id>/reopen", methods=["POST"])
@login_required
def reopen_query(query_id):
    query = EngagementQuery.query.get_or_404(query_id)
    _ensure_engagement_access(query.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    query.status = "Open"
    query.resolved_by_id = None
    query.resolved_at = None
    query.resolved_via_partner_override = False
    query.partner_override_notes = None
    db.session.commit()
    flash("Query reopened.", "info")
    return _query_redirect(query)


@engagements_bp.route("/queries")
@login_required
def queries_board():
    """Firm-wide list of review queries across every engagement, so a
    Partner/Reviewer can track what they've raised (and staff can see
    what's outstanding against their work) without opening each engagement
    in turn."""
    status_filter = request.args.get("status", "Open")
    query = EngagementQuery.query
    if status_filter in ("Open", "Resolved"):
        query = query.filter_by(status=status_filter)
    all_queries = query.order_by(EngagementQuery.raised_at.desc()).all()
    if current_user.role != "admin":
        # Don't leak the existence of, or activity on, an engagement this
        # user isn't assigned to - same confidentiality rule as everywhere
        # else, applied per-query via its parent engagement.
        all_queries = [q for q in all_queries if user_can_access_engagement(current_user, q.engagement)]
    return render_template(
        "engagements/queries_board.html", queries=all_queries, status_filter=status_filter,
    )


# ---------- Practice Dashboard (firm-wide analytics, built from data already captured - no new schema) ----------

def _engagement_quality_components(engagement):
    """Every sign-off-bearing record that currently exists for this
    engagement - whichever one-to-one workpapers have been started, plus
    each Substantive Procedures area - used to compute a lightweight
    Engagement Quality Index (see engagement_quality_index below). Nothing
    here is specific to one engagement type: it just walks whatever
    already exists, so it works unchanged for Audit, Assurance,
    Investigative, Business Intelligence and IT, Secretarial or Consulting
    engagements alike, and for any future engagement type added the same
    way (a new type simply means new records showing up in this same
    walk, not a new branch here)."""
    single_attrs = [
        "client_acceptance", "risk_assessment", "materiality", "entity_understanding",
        "analytical_review", "audit_strategy", "trial_balance", "financial_statements",
        "directors_statement", "audit_opinion", "income_tax_computation",
        "deferred_tax_computation", "finalisation_checklist",
    ]
    records = [getattr(engagement, attr) for attr in single_attrs if getattr(engagement, attr, None) is not None]
    records.extend(engagement.substantive_procedure_areas)
    return records


def engagement_quality_index(engagement):
    """A percentage: of every sign-off-bearing record that currently exists
    for this engagement, how many are at least effectively reviewed (see
    models.effectively_reviewed - a Partner's own preparation counts as
    reviewed), and separately how many are partner-signed. Returns None
    until at least one such record exists, so an engagement nobody has
    started yet shows as "not started" rather than a misleading 0%. A
    practical, always-current progress signal only - it is not a
    substitute for the firm's own quality-control judgement on an
    engagement, and it deliberately does not try to weight or score
    different sections differently (see the delivery notes for why)."""
    records = _engagement_quality_components(engagement)
    if not records:
        return None
    reviewed = sum(1 for r in records if effectively_reviewed(r))
    partner_signed = sum(1 for r in records if getattr(r, "is_partner_signed", False))
    return {
        "total": len(records),
        "reviewed": reviewed,
        "partner_signed": partner_signed,
        "reviewed_pct": round(100 * reviewed / len(records)),
        "partner_signed_pct": round(100 * partner_signed / len(records)),
    }


@engagements_bp.route("/analytics")
@login_required
def practice_dashboard():
    """Retained as a URL alias for the merged Practice Dashboard (see
    `dashboard()` above) - Dashboard and Practice Dashboard used to be two
    separate pages/nav links; they're now one page at /engagements/dashboard,
    and this old URL just renders the same thing, for anyone with it
    bookmarked."""
    return dashboard()


# ---------- Working papers: Word/Excel generation (Finalisation + Substantive Procedures tabs) ----------

@engagements_bp.route("/<int:engagement_id>/workpapers/financial-statements/generate", methods=["POST"])
@login_required
def generate_financial_statements_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the trial balance (Trial Balance tab) before generating the financial statements working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    _, _, ppe_class_dicts, ppe_asset_dicts = _ppe_plain_data(engagement_id)
    ppe_movement, ppe_policy_text = _ppe_movement_and_policy(ppe_class_dicts, ppe_asset_dicts, engagement.period_end)
    statements = fin.build_all_statements(
        trial_balance.lines, trial_balance.adjustments,
        reporting_framework=engagement.reporting_framework, cash_flow_method=engagement.cash_flow_method,
        ppe_movement=ppe_movement, ppe_policy_text=ppe_policy_text, whole_numbers=True,
    )
    import client_notes, cf_prior
    client_notes.attach_client_notes(statements, engagement)
    cf_prior.attach_prior_cash_flow(statements, engagement)
    financial_statements = FinancialStatements.query.filter_by(engagement_id=engagement_id).first()
    directors_statement = DirectorsStatement.query.filter_by(engagement_id=engagement_id).first()
    audit_opinion = AuditOpinion.query.filter_by(engagement_id=engagement_id).first()
    client_key_people = [
        {"full_name": p.full_name, "role": p.role}
        for p in ClientKeyPerson.query.filter_by(client_id=engagement.client_id, status="Confirmed").order_by(ClientKeyPerson.id).all()
    ]
    buf = wp.build_financial_statements_docx(
        engagement, statements, financial_statements,
        directors_statement=directors_statement, audit_opinion=audit_opinion, client_key_people=client_key_people,
    )
    doc = _file_generated_workpaper(engagement, "financial_statements", "Financial Statements", "N8100", "Financial_Statements", "docx", buf)
    flash(f"Financial statements filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/trial-balance/generate", methods=["POST"])
@login_required
def generate_trial_balance_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the trial balance (Trial Balance tab) before generating this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    buf = wp.build_trial_balance_adjustments_xlsx(engagement, trial_balance)
    doc = _file_generated_workpaper(engagement, "trial_balance_adjustments", "Trial Balance & Adjustments", "N1000", "Trial_Balance_and_Adjustments", "xlsx", buf)
    flash(f"Trial balance & adjustments filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
