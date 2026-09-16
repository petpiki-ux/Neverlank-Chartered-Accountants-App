import os
import uuid
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, send_from_directory, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    Engagement, Client, User, ChecklistTemplate, EngagementChecklistItem,
    RiskItem, Document, EngagementTask, DocumentTemplate,
    ENGAGEMENT_TYPES, ENGAGEMENT_STATUSES, TASK_STATUSES, CHECKLIST_STATUSES, RISK_STATUSES,
    SECRETARIAL_SUBDIVISIONS,
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
    return render_template(
        "engagements/detail.html",
        engagement=engagement,
        users=users,
        tab=tab,
        checklist_statuses=CHECKLIST_STATUSES,
        risk_statuses=RISK_STATUSES,
        task_statuses=TASK_STATUSES,
        matching_templates=matching_templates,
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
    db.session.commit()
    flash("Checklist item updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


# ---------- Risks ----------

@engagements_bp.route("/<int:engagement_id>/risks/add", methods=["POST"])
@login_required
def add_risk(engagement_id):
    Engagement.query.get_or_404(engagement_id)
    risk = RiskItem(
        engagement_id=engagement_id,
        category=request.form.get("category", "").strip(),
        risk_description=request.form.get("risk_description", "").strip(),
        likelihood=int(request.form.get("likelihood", 3)),
        impact=int(request.form.get("impact", 3)),
        mitigation=request.form.get("mitigation", "").strip(),
        owner_id=request.form.get("owner_id") or None,
        status=request.form.get("status", "Open"),
    )
    db.session.add(risk)
    db.session.commit()
    flash("Risk added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="risks"))


@engagements_bp.route("/risks/<int:risk_id>/update", methods=["POST"])
@login_required
def update_risk(risk_id):
    risk = RiskItem.query.get_or_404(risk_id)
    risk.category = request.form.get("category", risk.category)
    risk.risk_description = request.form.get("risk_description", risk.risk_description)
    risk.likelihood = int(request.form.get("likelihood", risk.likelihood))
    risk.impact = int(request.form.get("impact", risk.impact))
    risk.mitigation = request.form.get("mitigation", risk.mitigation)
    risk.owner_id = request.form.get("owner_id") or None
    risk.status = request.form.get("status", risk.status)
    db.session.commit()
    flash("Risk updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=risk.engagement_id, tab="risks"))


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
    db.session.commit()
    flash("Task updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=task.engagement_id, tab="tasks"))


@engagements_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    engagement_id = task.engagement_id
    db.session.delete(task)
    db.session.commit()
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
