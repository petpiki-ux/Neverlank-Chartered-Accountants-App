"""engagements, part 4 of 7.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file engagements.py did."""

@engagements_bp.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def update_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    old_assigned_to_id = task.assigned_to_id
    old_status = task.status
    old_due_date = task.due_date
    task.title = request.form.get("title", task.title)
    task.description = request.form.get("description", task.description)
    task.assigned_to_id = int(request.form.get("assigned_to_id")) if request.form.get("assigned_to_id") else None
    due_date = request.form.get("due_date")
    task.due_date = datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None
    # A form that doesn't send a category (e.g. the status dropdown) keeps the
    # stored one; a task with none yet gets the one implied by its engagement.
    task.category = resolve_task_category(
        request.form.get("category") or task.category, task.engagement)
    task.priority = effective_task_priority(task.category, request.form.get("priority", task.priority))
    task.status = request.form.get("status", task.status)
    if task.assigned_to_id and task.assigned_to_id != old_assigned_to_id:
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task assigned: {task.title}",
            f"{current_user.name} assigned you a task on {task.engagement.client.name} - {task.engagement.title}:\n\n{task.title}"
            + (f"\nDue {task.due_date.strftime('%d %b %Y')}" if task.due_date else ""),
        )
    elif task.assigned_to_id and task.assigned_to_id == old_assigned_to_id and (task.status != old_status or task.due_date != old_due_date):
        changes = []
        if task.status != old_status:
            changes.append(f"status is now {task.status}")
        if task.due_date != old_due_date:
            changes.append(f"due date is now {task.due_date.strftime('%d %b %Y') if task.due_date else 'unset'}")
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task updated: {task.title}",
            f"{current_user.name} updated a task assigned to you on {task.engagement.client.name} - {task.engagement.title}:\n\n"
            f"{task.title} - {', '.join(changes)}.",
        )
    if task.status == "Done":
        task.completed_by_id = current_user.id
        task.completed_at = datetime.utcnow()
    else:
        task.completed_by_id = None
        task.completed_at = None
        # Re-opening a previously completed task invalidates any earlier
        # review/partner sign-off - it needs to be looked at again once re-done.
        task.reviewed_by_id = None
        task.reviewed_at = None
        task.partner_signed_by_id = None
        task.partner_signed_at = None
    db.session.commit()
    flash("Task updated.", "success")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/review", methods=["POST"])
@login_required
def review_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if task.status != "Done":
        flash("This task needs to be marked Done before it can be reviewed.", "danger")
        return _task_redirect(task)
    if task.completed_by_id == current_user.id:
        flash("You can't review your own work - ask another supervisor/partner to review it.", "danger")
        return _task_redirect(task)
    task.reviewed_by_id = current_user.id
    task.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Task marked as reviewed.", "success")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/unreview", methods=["POST"])
@login_required
def unreview_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    task.reviewed_by_id = None
    task.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if task.status != "Done":
        flash("This task needs to be marked Done before the partner can sign off.", "danger")
        return _task_redirect(task)
    if task.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on work you prepared yourself - ask another partner to sign off.", "danger")
        return _task_redirect(task)
    task.partner_signed_by_id = current_user.id
    task.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    task.partner_signed_by_id = None
    task.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    engagement_id = task.engagement_id
    return_to_board = request.form.get("return_to") == "board"
    db.session.delete(task)
    db.session.commit()
    if return_to_board:
        return redirect(url_for("hr.project_board"))
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tasks"))


# ---------- Checklist templates (admin) ----------

@engagements_bp.route("/templates")
@login_required
def list_templates():
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()
    return render_template("engagements/templates_list.html", templates=templates)


@engagements_bp.route("/templates/new", methods=["GET", "POST"])
@login_required
def new_template():
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    if request.method == "POST":
        template = ChecklistTemplate(
            name=request.form.get("name", "").strip(),
            type=canonical_engagement_type(request.form.get("type", "Audit")),
            description=request.form.get("description", "").strip(),
        )
        db.session.add(template)
        db.session.commit()
        flash("Template created. Add items to it below.", "success")
        return redirect(url_for("engagements.view_template", template_id=template.id))
    return render_template("engagements/template_form.html", template=None, types=ENGAGEMENT_TYPES)


@engagements_bp.route("/templates/<int:template_id>")
@login_required
def view_template(template_id):
    template = ChecklistTemplate.query.get_or_404(template_id)
    return render_template("engagements/template_detail.html", template=template)


@engagements_bp.route("/templates/<int:template_id>/items/add", methods=["POST"])
@login_required
def add_template_item(template_id):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    from models import ChecklistTemplateItem
    template = ChecklistTemplate.query.get_or_404(template_id)
    max_order = max([i.order for i in template.items], default=0)
    item = ChecklistTemplateItem(
        template_id=template.id,
        section=request.form.get("section", "").strip(),
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
    )
    db.session.add(item)
    db.session.commit()
    return redirect(url_for("engagements.view_template", template_id=template_id))


@engagements_bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def delete_template(template_id):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    template = ChecklistTemplate.query.get_or_404(template_id)
    db.session.delete(template)
    db.session.commit()
    flash("Template deleted.", "info")
    return redirect(url_for("engagements.list_templates"))


# ---------- Staffing (date-ranged allocations, feeds the HR & Admin Planner) ----------

@engagements_bp.route("/<int:engagement_id>/staffing/add", methods=["POST"])
@login_required
def add_staff_allocation(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    user_id = request.form.get("user_id")
    start_date = request.form.get("start_date")
    end_date = request.form.get("end_date")

    if not user_id or not start_date or not end_date:
        flash("Please choose a staff member and both dates.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        flash("End date can't be before the start date.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))

    try:
        pct = int(request.form.get("allocation_pct", 100) or 100)
    except ValueError:
        pct = 100
    pct = max(1, min(pct, 100))

    allocation = StaffAllocation(
        engagement_id=engagement_id,
        user_id=int(user_id),
        start_date=start,
        end_date=end,
        allocation_pct=pct,
        notes=request.form.get("notes", "").strip(),
    )
    db.session.add(allocation)
    db.session.commit()
    flash("Staffing allocation added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))


@engagements_bp.route("/staffing/<int:allocation_id>/delete", methods=["POST"])
@login_required
def delete_staff_allocation(allocation_id):
    allocation = StaffAllocation.query.get_or_404(allocation_id)
    _ensure_engagement_access(allocation.engagement)
    engagement_id = allocation.engagement_id
    db.session.delete(allocation)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))


# ---------- Planning: materiality calculator ----------

@engagements_bp.route("/<int:engagement_id>/materiality/save", methods=["POST"])
@login_required
def save_materiality(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    calc = MaterialityCalculation.query.filter_by(engagement_id=engagement_id).first()
    if not calc:
        calc = MaterialityCalculation(engagement_id=engagement_id)
        db.session.add(calc)

    def _float_or_none(name):
        raw = request.form.get(name, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    calc.total_revenue = _float_or_none("total_revenue")
    calc.profit_before_tax = _float_or_none("profit_before_tax")
    calc.total_assets = _float_or_none("total_assets")
    calc.revenue_pct = _float_or_none("revenue_pct") or 1.0
    calc.pbt_pct = _float_or_none("pbt_pct") or 5.0
    calc.assets_pct = _float_or_none("assets_pct") or 1.0
    calc.performance_pct = _float_or_none("performance_pct") or 75.0
    calc.trivial_pct = _float_or_none("trivial_pct") or 5.0
    basis = request.form.get("basis", "highest")
    calc.basis = basis if basis in ("revenue", "pbt", "assets", "highest", "lowest") else "highest"
    calc.notes = request.form.get("notes", "").strip()
    calc.quantitative_important = request.form.get("quantitative_important") == "on"
    calc.updated_by_id = current_user.id
    # Changing the calculation invalidates any earlier review/partner sign-off.
    calc.reviewed_by_id = None
    calc.reviewed_at = None
    calc.partner_signed_by_id = None
    calc.partner_signed_at = None

    db.session.commit()
    flash("Materiality calculation saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/review", methods=["POST"])
@login_required
def review_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if calc.overall_materiality is None:
        flash("Enter at least one financial figure before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    if calc.updated_by_id == current_user.id:
        flash("You can't review a materiality calculation you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    calc.reviewed_by_id = current_user.id
    calc.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Materiality calculation marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/unreview", methods=["POST"])
@login_required
def unreview_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    calc.reviewed_by_id = None
    calc.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if calc.overall_materiality is None:
        flash("Enter at least one financial figure before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    if calc.updated_by_id == current_user.id:
        flash("You can't give the partner sign-off on a calculation you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    calc.partner_signed_by_id = current_user.id
    calc.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    calc.partner_signed_by_id = None
    calc.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


# ---------- Planning: Qualitative Materiality Index ----------
# See models.QualitativeMaterialityFactor/QUALITATIVE_MATERIALITY_FACTORS -
# a judgement-based complement to the dollar-figure materiality calculator
# above, for matters that can be material regardless of size. One form
# saves every factor's rating/notes at once (there are only 5 of them),
# rather than a separate save action per factor.

@engagements_bp.route("/<int:engagement_id>/qualitative-materiality/save", methods=["POST"])
@login_required
def save_qualitative_materiality(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    existing = {f.factor_key: f for f in QualitativeMaterialityFactor.query.filter_by(engagement_id=engagement_id).all()}
    for factor_key, _label, _help in QUALITATIVE_MATERIALITY_FACTORS:
        rating = request.form.get(f"rating__{factor_key}", "").strip()
        if rating not in QUALITATIVE_MATERIALITY_RATINGS:
            rating = ""
        notes = request.form.get(f"notes__{factor_key}", "").strip()
        factor = existing.get(factor_key)
        if not factor:
            if not rating and not notes:
                continue  # nothing to save - don't create an empty row
            factor = QualitativeMaterialityFactor(engagement_id=engagement_id, factor_key=factor_key)
            db.session.add(factor)
        factor.rating = rating
        factor.notes = notes
        factor.updated_by_id = current_user.id
        factor.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Qualitative Materiality Index saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


# ---------- Audit Strategy (Investigative Engagements only - replaces the ordinary Planning tab content) ----------

@engagements_bp.route("/<int:engagement_id>/audit-strategy/save", methods=["POST"])
@login_required
def save_audit_strategy(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    strategy = AuditStrategy.query.filter_by(engagement_id=engagement_id).first()
    if not strategy:
        strategy = AuditStrategy(engagement_id=engagement_id)
        db.session.add(strategy)

    strategy.objective_statement = request.form.get("objective_statement", "").strip()
    strategy.chain_of_custody_notes = request.form.get("chain_of_custody_notes", "").strip()
    strategy.reporting_destination = request.form.get("reporting_destination", "").strip()

    strategy.potential_perpetrators = request.form.get("potential_perpetrators", "").strip()
    strategy.fraud_vulnerability = request.form.get("fraud_vulnerability", "").strip()
    strategy.evidentiary_red_flags = request.form.get("evidentiary_red_flags", "").strip()

    strategy.resource_forensic_tech_available = request.form.get("resource_forensic_tech_available") == "on"
    strategy.resource_forensic_tech_notes = request.form.get("resource_forensic_tech_notes", "").strip()
    strategy.resource_data_analysts_available = request.form.get("resource_data_analysts_available") == "on"
    strategy.resource_data_analysts_notes = request.form.get("resource_data_analysts_notes", "").strip()
    strategy.resource_interviewers_available = request.form.get("resource_interviewers_available") == "on"
    strategy.resource_interviewers_notes = request.form.get("resource_interviewers_notes", "").strip()
    strategy.resource_legal_counsel_available = request.form.get("resource_legal_counsel_available") == "on"
    strategy.resource_legal_counsel_notes = request.form.get("resource_legal_counsel_notes", "").strip()

    phase1_status = request.form.get("phase1_status", "Not Started")
    strategy.phase1_status = phase1_status if phase1_status in AUDIT_STRATEGY_PHASE_STATUSES else "Not Started"
    strategy.phase1_notes = request.form.get("phase1_notes", "").strip()
    phase2_status = request.form.get("phase2_status", "Not Started")
    strategy.phase2_status = phase2_status if phase2_status in AUDIT_STRATEGY_PHASE_STATUSES else "Not Started"
    strategy.phase2_notes = request.form.get("phase2_notes", "").strip()
    phase3_status = request.form.get("phase3_status", "Not Started")
    strategy.phase3_status = phase3_status if phase3_status in AUDIT_STRATEGY_PHASE_STATUSES else "Not Started"
    strategy.phase3_notes = request.form.get("phase3_notes", "").strip()

    strategy.completed_by_id = current_user.id
    strategy.completed_at = datetime.utcnow()
    # Re-saving the strategy invalidates any earlier review/partner sign-off.
    strategy.reviewed_by_id = None
    strategy.reviewed_at = None
    strategy.partner_signed_by_id = None
    strategy.partner_signed_at = None

    db.session.commit()
    flash("Audit strategy saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/review", methods=["POST"])
@login_required
def review_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not strategy.is_complete:
        flash("Fill in the Scope, Fraud Theory and Legal Framework fields before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    if strategy.completed_by_id == current_user.id:
        flash("You can't review an audit strategy you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    strategy.reviewed_by_id = current_user.id
    strategy.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Audit strategy marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/unreview", methods=["POST"])
@login_required
def unreview_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    strategy.reviewed_by_id = None
    strategy.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not strategy.is_complete:
        flash("Fill in the Scope, Fraud Theory and Legal Framework fields before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    if strategy.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a strategy you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    strategy.partner_signed_by_id = current_user.id
    strategy.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    strategy.partner_signed_by_id = None
    strategy.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


# ---------- Secretarial Engagement Plan (Secretarial engagements only - replaces the ordinary Planning tab content) ----------

@engagements_bp.route("/<int:engagement_id>/secretarial-plan/save", methods=["POST"])
@login_required
def save_secretarial_plan(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    plan = SecretarialPlan.query.filter_by(engagement_id=engagement_id).first()
    if not plan:
        plan = SecretarialPlan(engagement_id=engagement_id)
        db.session.add(plan)

    plan.entity_categorization = request.form.get("entity_categorization", "").strip()
    plan.sector_regulators = request.form.get("sector_regulators", "").strip()
    plan.constitutional_constraints = request.form.get("constitutional_constraints", "").strip()

    for field in ("registration_date", "last_agm_date", "draft_financials_target", "agm_notice_target", "agm_target", "annual_return_target"):
        raw = request.form.get(field, "").strip()
        setattr(plan, field, datetime.strptime(raw, "%Y-%m-%d").date() if raw else None)
    plan.compliance_calendar_notes = request.form.get("compliance_calendar_notes", "").strip()

    plan.meeting_schedule_notes = request.form.get("meeting_schedule_notes", "").strip()
    plan.agenda_preplanning_notes = request.form.get("agenda_preplanning_notes", "").strip()
    plan.document_deadline_notes = request.form.get("document_deadline_notes", "").strip()

    plan.transactional_trigger_notes = request.form.get("transactional_trigger_notes", "").strip()

    plan.onboarding_risk_integration_notes = request.form.get("onboarding_risk_integration_notes", "").strip()
    plan.remediation_milestones_notes = request.form.get("remediation_milestones_notes", "").strip()
    plan.register_reconciliation_notes = request.form.get("register_reconciliation_notes", "").strip()

    plan.completed_by_id = current_user.id
    plan.completed_at = datetime.utcnow()
    # Re-saving the plan invalidates any earlier review/partner sign-off.
    plan.reviewed_by_id = None
    plan.reviewed_at = None
    plan.partner_signed_by_id = None
    plan.partner_signed_at = None

    db.session.commit()
    flash("Secretarial Engagement Plan saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


# ---------- Registry Forms (Secretarial engagements only) - the Registrar's CR forms and a log of what was filed ----------

def _parse_form_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _registry_engagement_or_redirect(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if engagement.type != "Secretarial":
        abort(404)
    return engagement


@engagements_bp.route("/<int:engagement_id>/registry-forms/add", methods=["POST"])
@login_required
def add_registry_filing(engagement_id):
    import cr_forms
    engagement = _registry_engagement_or_redirect(engagement_id)
    code = request.form.get("form_code", "").strip().upper()
    back = redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="registry_forms") + "#filings")
    if code not in cr_forms.CR_FORM_BY_CODE:
        flash("Choose a form from the list.", "danger")
        return back
    event_date = _parse_form_date(request.form.get("event_date"))
    due_date = _parse_form_date(request.form.get("due_date")) or cr_forms.due_date_for(code, event_date)
    status = request.form.get("status", "Not started")
    if status not in cr_forms.FORM_STATUSES:
        status = "Not started"
    lodged = _parse_form_date(request.form.get("lodged_date"))
    if status in ("Lodged", "Registered") and not lodged:
        lodged = date.today()
    db.session.add(RegistryFormFiling(
        engagement_id=engagement_id, form_code=code,
        subject=request.form.get("subject", "").strip()[:300] or None,
        event_date=event_date, due_date=due_date, lodged_date=lodged, status=status,
        registrar_reference=request.form.get("registrar_reference", "").strip()[:100] or None,
        notes=request.form.get("notes", "").strip() or None,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash(f"{code} added to the filing log.", "success")
    return back


@engagements_bp.route("/registry-forms/<int:filing_id>/update", methods=["POST"])
@login_required
def update_registry_filing(filing_id):
    import cr_forms
    filing = RegistryFormFiling.query.get_or_404(filing_id)
    _registry_engagement_or_redirect(filing.engagement_id)
    filing.subject = request.form.get("subject", "").strip()[:300] or None
    filing.event_date = _parse_form_date(request.form.get("event_date"))
    due = _parse_form_date(request.form.get("due_date"))
    filing.due_date = due or cr_forms.due_date_for(filing.form_code, filing.event_date)
    status = request.form.get("status", filing.status)
    if status in cr_forms.FORM_STATUSES:
        filing.status = status
    filing.lodged_date = _parse_form_date(request.form.get("lodged_date"))
    if filing.status in ("Lodged", "Registered") and not filing.lodged_date:
        filing.lodged_date = date.today()
    filing.registrar_reference = request.form.get("registrar_reference", "").strip()[:100] or None
    filing.notes = request.form.get("notes", "").strip() or None
    db.session.commit()
    flash(f"{filing.form_code} updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=filing.engagement_id, tab="registry_forms") + "#filings")


@engagements_bp.route("/registry-forms/<int:filing_id>/delete", methods=["POST"])
@login_required
def delete_registry_filing(filing_id):
    filing = RegistryFormFiling.query.get_or_404(filing_id)
    _registry_engagement_or_redirect(filing.engagement_id)
    engagement_id, code = filing.engagement_id, filing.form_code
    db.session.delete(filing)
    db.session.commit()
    flash(f"{code} removed from the filing log.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="registry_forms") + "#filings")


@engagements_bp.route("/secretarial-plan/<int:plan_id>/review", methods=["POST"])
@login_required
def review_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not plan.is_complete:
        flash("Fill in the Entity Profile, Board Cycle, Trigger Mapping and Risk & Quality fields before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    if plan.completed_by_id == current_user.id:
        flash("You can't review a secretarial plan you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    plan.reviewed_by_id = current_user.id
    plan.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Secretarial Engagement Plan marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


@engagements_bp.route("/secretarial-plan/<int:plan_id>/unreview", methods=["POST"])
@login_required
def unreview_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    plan.reviewed_by_id = None
    plan.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


@engagements_bp.route("/secretarial-plan/<int:plan_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not plan.is_complete:
        flash("Fill in the Entity Profile, Board Cycle, Trigger Mapping and Risk & Quality fields before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    if plan.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a plan you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    plan.partner_signed_by_id = current_user.id
    plan.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


@engagements_bp.route("/secretarial-plan/<int:plan_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    plan.partner_signed_by_id = None
    plan.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


# ---------- IT Change & Release Testing Plan (Business Intelligence and IT
# Engagements only) - a Planning-tab register of known/planned system
# changes during the audit period, used to decide which changes to pull
# into the ITGC change-management sample tested under Substantive
# Procedures. See models.ITChangePlanItem. ----------

@engagements_bp.route("/<int:engagement_id>/it-change-plan/add", methods=["POST"])
@login_required
def add_it_change_plan_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    change_description = request.form.get("change_description", "").strip()
    if not change_description:
        flash("Describe the planned/known change before adding it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))
    planned_date_str = request.form.get("planned_date", "").strip()
    planned_date = datetime.strptime(planned_date_str, "%Y-%m-%d").date() if planned_date_str else None
    risk_level = request.form.get("risk_level", "").strip()
    if risk_level not in IT_CHANGE_PLAN_RISK_LEVELS:
        risk_level = ""
    max_order = max([i.order for i in engagement.it_change_plan_items], default=0)
    db.session.add(ITChangePlanItem(
        engagement_id=engagement_id, change_description=change_description, planned_date=planned_date,
        risk_level=risk_level, order=max_order + 1, created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Change added to the plan.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


@engagements_bp.route("/it-change-plan/<int:item_id>/update", methods=["POST"])
@login_required
def update_it_change_plan_item(item_id):
    item = ITChangePlanItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.change_description = request.form.get("change_description", item.change_description).strip() or item.change_description
    planned_date_str = request.form.get("planned_date", "").strip()
    item.planned_date = datetime.strptime(planned_date_str, "%Y-%m-%d").date() if planned_date_str else None
    risk_level = request.form.get("risk_level", "").strip()
    item.risk_level = risk_level if risk_level in IT_CHANGE_PLAN_RISK_LEVELS else ""
    item.selected_for_testing = request.form.get("selected_for_testing") == "on"
    item.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Change updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="planning"))


@engagements_bp.route("/it-change-plan/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_it_change_plan_item(item_id):
    item = ITChangePlanItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    flash("Change removed from the plan.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


# ---------- Finalisation checklist ("what's left to close this engagement") ----------

def _get_or_create_finalisation_checklist(engagement_id):
    record = FinalisationChecklist.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = FinalisationChecklist(engagement_id=engagement_id)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/finalisation-checklist/seed", methods=["POST"])
@login_required
def seed_finalisation_checklist(engagement_id):
    """Populate the Finalisation checklist with the firm's default items -
    the forensic set (see FORENSIC_FINALISATION_CHECKLIST_ITEMS) on an
    Investigative Engagement, the cyber/IT/AML-CFT set (see BUSINESS_IT_
    FINALISATION_CHECKLIST_ITEMS) on a Business Intelligence and IT
    Engagement, the COBE-compliant five-phase set (see SECRETARIAL_
    FINALISATION_CHECKLIST_ITEMS) on a Secretarial engagement, the ordinary
    close-out set (see DEFAULT_FINALISATION_CHECKLIST_ITEMS) otherwise.
    Mirrors seed_entity_checklist: only does anything the first time, so
    it's safe to expose as a single button."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_finalisation_checklist(engagement_id)
    if record.checklist_items:
        flash("The checklist already has items on it.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    if engagement.type == "Investigative Engagement":
        source_items = FORENSIC_FINALISATION_CHECKLIST_ITEMS
    elif engagement.type == "Business Intelligence and IT Engagements":
        source_items = BUSINESS_IT_FINALISATION_CHECKLIST_ITEMS
    elif engagement.type == "Secretarial":
        source_items = SECRETARIAL_FINALISATION_CHECKLIST_ITEMS
    else:
        source_items = DEFAULT_FINALISATION_CHECKLIST_ITEMS
    for order, (section, item_text) in enumerate(source_items, start=1):
        db.session.add(FinalisationChecklistItem(
            finalisation_checklist_id=record.id,
            section=section,
            item_text=item_text,
            order=order,
            created_by_id=current_user.id,
        ))
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Default checklist items added - tick and comment on each one.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/finalisation-checklist/add", methods=["POST"])
@login_required
def add_finalisation_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_finalisation_checklist(engagement_id)
    item_text = request.form.get("item_text", "").strip()
    if not item_text:
        flash("Enter the checklist item's wording before adding it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    max_order = max([i.order for i in record.checklist_items], default=0)
    db.session.add(FinalisationChecklistItem(
        finalisation_checklist_id=record.id,
        section=request.form.get("section", "").strip(),
        item_text=item_text,
        order=max_order + 1,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_finalisation_checklist_item(item_id):
    item = FinalisationChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.finalisation_checklist.engagement)
    response = request.form.get("response", "").strip()
    item.response = response if response in CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES else ""
    item.comment = request.form.get("comment", "").strip()
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=item.finalisation_checklist.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_finalisation_checklist_item(item_id):
    item = FinalisationChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.finalisation_checklist.engagement)
    engagement_id = item.finalisation_checklist.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/review", methods=["POST"])
@login_required
def review_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not record.is_complete:
        flash("Every checklist item needs a response before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    if record.completed_by_id == current_user.id:
        flash("You can't review a checklist you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Finalisation checklist marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/unreview", methods=["POST"])
@login_required
def unreview_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not record.is_complete:
        flash("Every checklist item needs a response before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a checklist you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


# ---------- Audit Finalisation: Trial Balance import + IAS 1 Financial Statements ----------

def _allowed_tb_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ("xlsx", "xls", "csv")


def _read_alt_format_tb_workbook(workbook):
    """Recognizes an alternate trial balance export layout some accounting
    systems produce instead of the app's own template: one sheet per year
    (e.g. '2024', '2025'), each with a header row like ('GL Code', 'Name',
    'Opening balance', '<year> Transactions', 'Closing balance') and a
    single signed closing balance per account rather than separate debit/
    credit columns.

    Returns (rows, note) shaped like the standard import - rows are dicts
    keyed by TB_IMPORT_COLUMNS' header names, ready for
    financials.parse_tb_rows() exactly like the standard layout's rows -
    or (None, None) if the workbook doesn't look like this format at all.

    Two or more matching sheets are treated as successive years - sheet
    names that parse as plain years (e.g. '2024') are sorted numerically
    and the latest becomes the current year, the one before it the prior
    year; otherwise workbook order is used, last sheet as current year. A
    single matching sheet is read as current year only, with prior year
    left at zero for every account. Accounts are matched between the two
    years by GL Code (falling back to the account name when no code
    column exists). Closing balances are converted to debit/credit with
    the standard accounting sign convention: a positive balance is a
    debit, a negative balance is a credit."""
    sheet_accounts = {}  # sheet name -> {code_or_name: (code, name, closing_balance)}

    for sheet_name in workbook.sheetnames:
        ws = workbook[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        header_idx = None
        headers = None
        for i, row in enumerate(rows):
            cells = [str(c).strip().lower() if c is not None else "" for c in row]
            if "closing balance" in cells and ("name" in cells or "account name" in cells):
                header_idx = i
                headers = [str(c).strip() if c is not None else "" for c in row]
                break
        if header_idx is None:
            continue

        lower_headers = [h.lower() for h in headers]
        name_col = lower_headers.index("name") if "name" in lower_headers else lower_headers.index("account name")
        closing_col = lower_headers.index("closing balance")
        code_col = next((lower_headers.index(c) for c in ("gl code", "account code", "code") if c in lower_headers), None)

        accounts = {}
        for row in rows[header_idx + 1:]:
            if row is None or len(row) <= max(name_col, closing_col):
                continue
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            name = row[name_col]
            if name is None or not str(name).strip():
                continue
            name = str(name).strip()
            code = str(row[code_col]).strip() if code_col is not None and row[code_col] is not None else ""
            closing = row[closing_col]
            try:
                closing = float(closing) if closing not in (None, "") else 0.0
            except (TypeError, ValueError):
                closing = 0.0
            accounts[code or name] = (code, name, closing)

        if accounts:
            sheet_accounts[sheet_name] = accounts

    if not sheet_accounts:
        return None, None

    def _year_sort_key(name):
        try:
            return (0, int(str(name).strip()))
        except ValueError:
            return (1, str(name))

    ordered_sheet_names = sorted(sheet_accounts.keys(), key=_year_sort_key)
    current_sheet_name = ordered_sheet_names[-1]
    prior_sheet_name = ordered_sheet_names[-2] if len(ordered_sheet_names) > 1 else None
    current_accounts = sheet_accounts[current_sheet_name]
    prior_accounts = sheet_accounts[prior_sheet_name] if prior_sheet_name else {}

    def _split(balance):
        if balance > 0:
            return balance, 0.0
        if balance < 0:
            return 0.0, abs(balance)
        return 0.0, 0.0

    combined = {}
    for key, (code, name, balance) in current_accounts.items():
        combined[key] = {"code": code, "name": name, "current": balance, "prior": 0.0}
    for key, (code, name, balance) in prior_accounts.items():
        if key in combined:
            combined[key]["prior"] = balance
        else:
            combined[key] = {"code": code, "name": name, "current": 0.0, "prior": balance}

    out_rows = []
    for acc in combined.values():
        cur_debit, cur_credit = _split(acc["current"])
        prior_debit, prior_credit = _split(acc["prior"])
        out_rows.append({
            "Account Code": acc["code"],
            "Account Name": acc["name"],
            "Current Year Debit": cur_debit,
            "Current Year Credit": cur_credit,
            "Prior Year Debit": prior_debit,
            "Prior Year Credit": prior_credit,
        })

    if prior_sheet_name:
        note = (
            f"Recognized '{current_sheet_name}' and '{prior_sheet_name}' as this year's and last year's trial "
            "balance (GL Code / Name / Closing balance format) and matched accounts between them by GL Code."
        )
    else:
        note = (
            f"Recognized '{current_sheet_name}' as this year's trial balance (GL Code / Name / Closing balance "
            "format) - no earlier year's tab was found, so prior year was left blank."
        )
    return out_rows, note
