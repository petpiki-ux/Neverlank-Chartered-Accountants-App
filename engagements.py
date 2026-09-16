import os
import uuid
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, send_from_directory, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    Engagement, Client, User, ChecklistTemplate, EngagementChecklistItem,
    RiskItem, Document, EngagementTask, DocumentTemplate, StaffAllocation,
    RiskAssessment, MaterialityCalculation, EntityUnderstanding,
    AnalyticalReview, AnalyticalReviewLine,
    ENGAGEMENT_TYPES, ENGAGEMENT_STATUSES, TASK_STATUSES, CHECKLIST_STATUSES, RISK_STATUSES,
    SECRETARIAL_SUBDIVISIONS, REVIEWER_ROLES,
    RISK_LIKELIHOOD_QUESTIONS, RISK_IMPACT_QUESTIONS, SCOPE_SUGGESTIONS,
    ENTITY_UNDERSTANDING_FIELDS,
)

engagements_bp = Blueprint("engagements", __name__, url_prefix="/engagements")


# ---------- Dashboard ----------

@engagements_bp.route("/dashboard")
@login_required
def dashboard():
    all_engagements = Engagement.query.order_by(Engagement.deadline.asc().nullslast()).all()
    active = [e for e in all_engagements if e.status != "Completed"]
    overdue = [e for e in active if e.is_overdue]
    my_tasks = (
        EngagementTask.query.filter_by(assigned_to_id=current_user.id)
        .filter(EngagementTask.status != "Done")
        .order_by(EngagementTask.due_date.asc().nullslast())
        .limit(10)
        .all()
    )
    counts_by_status = {s: 0 for s in ENGAGEMENT_STATUSES}
    for e in all_engagements:
        counts_by_status[e.status] = counts_by_status.get(e.status, 0) + 1

    return render_template(
        "dashboard.html",
        active=active,
        overdue=overdue,
        my_tasks=my_tasks,
        counts_by_status=counts_by_status,
        total_clients=Client.query.count(),
        total_engagements=len(all_engagements),
    )


# ---------- Engagement CRUD ----------

@engagements_bp.route("/")
@login_required
def list_engagements():
    status_filter = request.args.get("status", "")
    type_filter = request.args.get("type", "")
    query = Engagement.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    if type_filter:
        query = query.filter_by(type=type_filter)
    all_engagements = query.order_by(Engagement.deadline.asc().nullslast()).all()
    return render_template(
        "engagements/list.html",
        engagements=all_engagements,
        statuses=ENGAGEMENT_STATUSES,
        types=ENGAGEMENT_TYPES,
        status_filter=status_filter,
        type_filter=type_filter,
    )


@engagements_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_engagement():
    clients = Client.query.order_by(Client.name).all()
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()

    if request.method == "POST":
        client_id = request.form.get("client_id")
        if not client_id:
            flash("Please select a client.", "danger")
            return render_template("engagements/form.html", engagement=None, clients=clients, users=users,
                                    templates=templates, types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                                    subdivisions=SECRETARIAL_SUBDIVISIONS)

        engagement_type = request.form.get("type", "Audit")
        engagement = Engagement(
            client_id=int(client_id),
            title=request.form.get("title", "").strip(),
            type=engagement_type,
            subdivision=(request.form.get("subdivision", "").strip() or None) if engagement_type == "Secretarial" else None,
            status=request.form.get("status", "Planning"),
            description=request.form.get("description", "").strip(),
            partner_id=request.form.get("partner_id") or None,
            manager_id=request.form.get("manager_id") or None,
        )
        start_date = request.form.get("start_date")
        deadline = request.form.get("deadline")
        period_end = request.form.get("period_end")
        engagement.start_date = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else date.today()
        engagement.deadline = datetime.strptime(deadline, "%Y-%m-%d").date() if deadline else None
        engagement.period_end = datetime.strptime(period_end, "%Y-%m-%d").date() if period_end else None

        team_ids = request.form.getlist("team_members")
        if team_ids:
            engagement.team_members = User.query.filter(User.id.in_(team_ids)).all()

        db.session.add(engagement)
        db.session.flush()  # get engagement.id before commit

        template_id = request.form.get("template_id")
        if template_id:
            template = ChecklistTemplate.query.get(int(template_id))
            if template:
                for item in template.items:
                    db.session.add(EngagementChecklistItem(
                        engagement_id=engagement.id,
                        section=item.section,
                        item_text=item.item_text,
                        order=item.order,
                    ))

        db.session.commit()
        flash(f"Engagement '{engagement.title}' created.", "success")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id))

    return render_template("engagements/form.html", engagement=None, clients=clients, users=users,
                            templates=templates, types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                            subdivisions=SECRETARIAL_SUBDIVISIONS)


@engagements_bp.route("/<int:engagement_id>/edit", methods=["GET", "POST"])
@login_required
def edit_engagement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    clients = Client.query.order_by(Client.name).all()
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()

    if request.method == "POST":
        engagement.client_id = int(request.form.get("client_id"))
        engagement.title = request.form.get("title", "").strip()
        engagement.type = request.form.get("type", "Audit")
        engagement.subdivision = (request.form.get("subdivision", "").strip() or None) if engagement.type == "Secretarial" else None
        engagement.status = request.form.get("status", "Planning")
        engagement.description = request.form.get("description", "").strip()
        engagement.partner_id = request.form.get("partner_id") or None
        engagement.manager_id = request.form.get("manager_id") or None

        start_date = request.form.get("start_date")
        deadline = request.form.get("deadline")
        period_end = request.form.get("period_end")
        engagement.start_date = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else engagement.start_date
        engagement.deadline = datetime.strptime(deadline, "%Y-%m-%d").date() if deadline else None
        engagement.period_end = datetime.strptime(period_end, "%Y-%m-%d").date() if period_end else None

        team_ids = request.form.getlist("team_members")
        engagement.team_members = User.query.filter(User.id.in_(team_ids)).all() if team_ids else []

        db.session.commit()
        flash("Engagement updated.", "success")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id))

    return render_template("engagements/form.html", engagement=engagement, clients=clients, users=users,
                            templates=[], types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                            subdivisions=SECRETARIAL_SUBDIVISIONS)


@engagements_bp.route("/<int:engagement_id>/delete", methods=["POST"])
@login_required
def delete_engagement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    client_id = engagement.client_id
    db.session.delete(engagement)
    db.session.commit()
    flash("Engagement deleted.", "info")
    return redirect(url_for("clients.view_client", client_id=client_id))


@engagements_bp.route("/<int:engagement_id>")
@login_required
def view_engagement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    tab = request.args.get("tab", "overview")
    # Document Templates matching this engagement's type (Audit/Assurance/
    # Consulting) - shown on the Documents tab so the right blank
    # letters/workpapers for this client's engagement are one click away,
    # with no need to go hunting in the separate Document Templates library.
    matching_templates = (
        DocumentTemplate.query.filter_by(type=engagement.type)
        .order_by(DocumentTemplate.ref_code)
        .all()
    )
    risk_assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
    materiality = MaterialityCalculation.query.filter_by(engagement_id=engagement_id).first()
    entity_understanding = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    analytical_review = AnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
    scope_suggestion = SCOPE_SUGGESTIONS.get(risk_assessment.rating) if risk_assessment and risk_assessment.rating else None
    return render_template(
        "engagements/detail.html",
        engagement=engagement,
        users=users,
        tab=tab,
        checklist_statuses=CHECKLIST_STATUSES,
        risk_statuses=RISK_STATUSES,
        task_statuses=TASK_STATUSES,
        matching_templates=matching_templates,
        risk_assessment=risk_assessment,
        likelihood_questions=RISK_LIKELIHOOD_QUESTIONS,
        impact_questions=RISK_IMPACT_QUESTIONS,
        materiality=materiality,
        scope_suggestion=scope_suggestion,
        entity_understanding=entity_understanding,
        entity_fields=ENTITY_UNDERSTANDING_FIELDS,
        analytical_review=analytical_review,
    )


# ---------- Checklist ----------

@engagements_bp.route("/<int:engagement_id>/checklist/add", methods=["POST"])
@login_required
def add_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    max_order = max([i.order for i in engagement.checklist_items], default=0)
    item = EngagementChecklistItem(
        engagement_id=engagement.id,
        section=request.form.get("section", "").strip(),
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
    )
    db.session.add(item)
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    item.status = request.form.get("status", item.status)
    item.notes = request.form.get("notes", item.notes)
    if item.status in ("Done", "N/A"):
        item.completed_by_id = current_user.id
        item.completed_at = datetime.utcnow()
    else:
        item.completed_by_id = None
        item.completed_at = None
        # Re-opening a previously completed item invalidates any earlier
        # review sign-off - it needs to be looked at again once re-done.
        item.reviewed_by_id = None
        item.reviewed_at = None
    db.session.commit()
    flash("Checklist item updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/review", methods=["POST"])
@login_required
def review_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if item.status not in ("Done", "N/A"):
        flash("This item needs to be prepared (marked Done or N/A) before it can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    if item.completed_by_id == current_user.id:
        flash("You can't review your own work - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    item.reviewed_by_id = current_user.id
    item.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Checklist item marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/unreview", methods=["POST"])
@login_required
def unreview_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    item.reviewed_by_id = None
    item.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


# ---------- Understanding the Entity (system-based, structured prompts) ----------

@engagements_bp.route("/<int:engagement_id>/entity-understanding/save", methods=["POST"])
@login_required
def save_entity_understanding(engagement_id):
    Engagement.query.get_or_404(engagement_id)
    record = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = EntityUnderstanding(engagement_id=engagement_id)
        db.session.add(record)

    for field, _, _ in ENTITY_UNDERSTANDING_FIELDS:
        setattr(record, field, request.form.get(field, "").strip())

    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    # Changing the write-up invalidates any earlier review sign-off.
    record.reviewed_by_id = None
    record.reviewed_at = None

    db.session.commit()
    flash("Understanding of the entity saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/review", methods=["POST"])
@login_required
def review_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not record.is_complete:
        flash("All sections need to be filled in before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    if record.completed_by_id == current_user.id:
        flash("You can't review your own write-up - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/unreview", methods=["POST"])
@login_required
def unreview_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


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


@engagements_bp.route("/<int:engagement_id>/analytical-review/threshold", methods=["POST"])
@login_required
def save_analytical_review_threshold(engagement_id):
    Engagement.query.get_or_404(engagement_id)
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


@engagements_bp.route("/<int:engagement_id>/analytical-review/lines/add", methods=["POST"])
@login_required
def add_analytical_review_line(engagement_id):
    Engagement.query.get_or_404(engagement_id)
    review = _get_or_create_analytical_review(engagement_id)

    label = request.form.get("label", "").strip()
    if not label:
        flash("Please name the line item (e.g. Revenue, Gross profit).", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))

    def _float_or_none(name):
        raw = request.form.get(name, "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    line = AnalyticalReviewLine(
        analytical_review_id=review.id,
        label=label,
        prior_amount=_float_or_none("prior_amount"),
        current_amount=_float_or_none("current_amount"),
        explanation=request.form.get("explanation", "").strip(),
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
    _touch_analytical_review(review)
    db.session.commit()
    flash("Line updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_analytical_review_line(line_id):
    line = AnalyticalReviewLine.query.get_or_404(line_id)
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
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    review.reviewed_by_id = None
    review.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


# ---------- Risk Assessment (system-based questionnaire) ----------

@engagements_bp.route("/<int:engagement_id>/risk-assessment/save", methods=["POST"])
@login_required
def save_risk_assessment(engagement_id):
    Engagement.query.get_or_404(engagement_id)
    assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
    if not assessment:
        assessment = RiskAssessment(engagement_id=engagement_id)
        db.session.add(assessment)

    for field, _, options in RISK_LIKELIHOOD_QUESTIONS + RISK_IMPACT_QUESTIONS:
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
    # Re-answering the questionnaire invalidates any earlier review - the
    # updated assessment needs a fresh look.
    assessment.reviewed_by_id = None
    assessment.reviewed_at = None

    db.session.commit()
    flash("Risk assessment saved - rating computed automatically below.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/review", methods=["POST"])
@login_required
def review_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
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
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    assessment.reviewed_by_id = None
    assessment.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risks/<int:risk_id>/delete", methods=["POST"])
@login_required
def delete_risk(risk_id):
    risk = RiskItem.query.get_or_404(risk_id)
    engagement_id = risk.engagement_id
    db.session.delete(risk)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="risks"))


# ---------- Documents ----------

def _allowed_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


@engagements_bp.route("/<int:engagement_id>/documents/upload", methods=["POST"])
@login_required
def upload_document(engagement_id):
    Engagement.query.get_or_404(engagement_id)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))

    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))

    original_name = secure_filename(file.filename)
    category = request.form.get("category", "General").strip() or "General"
    reference = request.form.get("reference", "").strip()

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
        notes=request.form.get("notes", "").strip(),
        uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.commit()
    flash(f"Uploaded '{original_name}' (v{version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))


@engagements_bp.route("/documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], doc.stored_filename, as_attachment=True,
        download_name=doc.original_filename,
    )


@engagements_bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    engagement_id = doc.engagement_id
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_filename))
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))


# ---------- Tasks ----------

@engagements_bp.route("/<int:engagement_id>/tasks/add", methods=["POST"])
@login_required
def add_task(engagement_id):
    Engagement.query.get_or_404(engagement_id)
    due_date = request.form.get("due_date")
    task = EngagementTask(
        engagement_id=engagement_id,
        title=request.form.get("title", "").strip(),
        description=request.form.get("description", "").strip(),
        assigned_to_id=request.form.get("assigned_to_id") or None,
        due_date=datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None,
        priority=request.form.get("priority", "Normal"),
        status=request.form.get("status", "To Do"),
    )
    db.session.add(task)
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


@engagements_bp.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def update_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    task.title = request.form.get("title", task.title)
    task.description = request.form.get("description", task.description)
    task.assigned_to_id = request.form.get("assigned_to_id") or None
    due_date = request.form.get("due_date")
    task.due_date = datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None
    task.priority = request.form.get("priority", task.priority)
    task.status = request.form.get("status", task.status)
    if task.status == "Done":
        task.completed_by_id = current_user.id
        task.completed_at = datetime.utcnow()
    else:
        task.completed_by_id = None
        task.completed_at = None
        # Re-opening a previously completed task invalidates any earlier
        # review sign-off - it needs to be looked at again once re-done.
        task.reviewed_by_id = None
        task.reviewed_at = None
    db.session.commit()
    flash("Task updated.", "success")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/review", methods=["POST"])
@login_required
def review_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
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
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    task.reviewed_by_id = None
    task.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
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
    if request.method == "POST":
        template = ChecklistTemplate(
            name=request.form.get("name", "").strip(),
            type=request.form.get("type", "Audit"),
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
    template = ChecklistTemplate.query.get_or_404(template_id)
    db.session.delete(template)
    db.session.commit()
    flash("Template deleted.", "info")
    return redirect(url_for("engagements.list_templates"))


# ---------- Staffing (date-ranged allocations, feeds the HR & Admin Planner) ----------

@engagements_bp.route("/<int:engagement_id>/staffing/add", methods=["POST"])
@login_required
def add_staff_allocation(engagement_id):
    Engagement.query.get_or_404(engagement_id)
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
    engagement_id = allocation.engagement_id
    db.session.delete(allocation)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))


# ---------- Planning: materiality calculator ----------

@engagements_bp.route("/<int:engagement_id>/materiality/save", methods=["POST"])
@login_required
def save_materiality(engagement_id):
    Engagement.query.get_or_404(engagement_id)
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
    calc.updated_by_id = current_user.id

    db.session.commit()
    flash("Materiality calculation saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))
