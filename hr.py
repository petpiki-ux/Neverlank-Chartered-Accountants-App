"""HR & Administration: Policies and Procedures (a firm document library,
same pattern as Document Templates), Time Sheets (in-app weekly hours entry
with automatic overtime calculation, plus a downloadable Excel template and
an upload option for staff who prefer filling it in offline), and a
firm-wide Project Management task board pulling together every engagement's
Tasks tab in one place.
"""
import os
import uuid
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash,
    send_from_directory, abort, current_app,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    PolicyDocument, TimeSheet, TimeEntry, TimeSheetUpload, User, Engagement,
    EngagementTask, PersonalTask, StaffAllocation, POLICY_CATEGORIES, REVIEWER_ROLES, TASK_STATUSES,
    user_has_permission, user_can_access_engagement, notify_task_assignment,
)
from config import Config

hr_bp = Blueprint("hr", __name__, url_prefix="/hr")

ALLOWED_POLICY_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"}
ALLOWED_TIMESHEET_UPLOAD_EXTENSIONS = {"xlsx", "xls", "pdf"}


def _visible_engagements_for(user, engagement_list):
    """Same confidentiality rule as engagements.py's _visible_to_current_user
    (a non-admin only sees engagements they're Partner/Manager/Team on) -
    duplicated here rather than imported since it's a plain list filter with
    no request/route context of its own. Used everywhere in this module
    that lists engagements or engagement-linked rows firm-wide (the
    timesheet engagement picker, the Projects board, and the Planner)."""
    if user.role == "admin":
        return engagement_list
    return [e for e in engagement_list if user_can_access_engagement(user, e)]


def editor_required(f):
    """Editing the Policies library is gated by the configurable "Manage
    Policies & Procedures" permission (Team > Permissions) - defaults to
    admin/partner only, same as before this was made configurable -
    everyone can still view and download regardless."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not user_has_permission(current_user, "manage_policies"):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def reviewer_required(f):
    """Approving timesheets and reviewing checklist items/tasks is
    restricted to supervisor/partner/admin."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if current_user.role not in REVIEWER_ROLES:
            abort(403)
        return f(*args, **kwargs)
    return wrapped


@hr_bp.route("/")
@login_required
def index():
    return render_template("hr/index.html")


# ---------- Policies and Procedures ----------

def _allowed_policy_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_POLICY_EXTENSIONS


def _policies_dir():
    os.makedirs(Config.POLICIES_DATA_DIR, exist_ok=True)
    return Config.POLICIES_DATA_DIR


@hr_bp.route("/policies")
@login_required
def list_policies():
    library = {}
    for category in POLICY_CATEGORIES:
        items = PolicyDocument.query.filter_by(category=category).order_by(
            PolicyDocument.order, PolicyDocument.title
        ).all()
        if items:
            library[category] = items
    can_edit = user_has_permission(current_user, "manage_policies")
    return render_template("hr/policies_list.html", library=library, can_edit=can_edit)


@hr_bp.route("/policies/download/<int:policy_id>")
@login_required
def download_policy(policy_id):
    policy = PolicyDocument.query.get_or_404(policy_id)
    directory = _policies_dir()
    if not os.path.exists(os.path.join(directory, policy.filename)):
        abort(404)
    return send_from_directory(directory, policy.filename, as_attachment=True)


@hr_bp.route("/policies/new", methods=["GET", "POST"])
@login_required
@editor_required
def new_policy():
    if request.method == "POST":
        category = request.form.get("category", "Other")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        file = request.files.get("file")

        if category not in POLICY_CATEGORIES:
            category = "Other"
        if not title:
            flash("Please give the policy/procedure a name.", "danger")
            return render_template("hr/policy_form.html", policy=None, categories=POLICY_CATEGORIES)
        if not file or file.filename == "":
            flash("Please choose a file to upload.", "danger")
            return render_template("hr/policy_form.html", policy=None, categories=POLICY_CATEGORIES)
        if not _allowed_policy_file(file.filename):
            flash("Only PDF, Word, Excel and PowerPoint files are allowed.", "danger")
            return render_template("hr/policy_form.html", policy=None, categories=POLICY_CATEGORIES)

        policy = PolicyDocument(
            category=category,
            title=title,
            description=description,
            filename="pending",
            updated_by_id=current_user.id,
        )
        db.session.add(policy)
        db.session.flush()

        original_name = secure_filename(file.filename)
        stored_name = f"pol{policy.id}_{original_name}"
        file.save(os.path.join(_policies_dir(), stored_name))
        policy.filename = stored_name

        db.session.commit()
        flash(f"'{policy.title}' added to the Policies library.", "success")
        return redirect(url_for("hr.list_policies"))

    return render_template("hr/policy_form.html", policy=None, categories=POLICY_CATEGORIES)


@hr_bp.route("/policies/<int:policy_id>/edit", methods=["GET", "POST"])
@login_required
@editor_required
def edit_policy(policy_id):
    policy = PolicyDocument.query.get_or_404(policy_id)
    if request.method == "POST":
        category = request.form.get("category", policy.category)
        title = request.form.get("title", "").strip()
        if category not in POLICY_CATEGORIES:
            category = "Other"
        if not title:
            flash("Please give the policy/procedure a name.", "danger")
            return render_template("hr/policy_form.html", policy=policy, categories=POLICY_CATEGORIES)

        file = request.files.get("file")
        if file and file.filename != "":
            if not _allowed_policy_file(file.filename):
                flash("Only PDF, Word, Excel and PowerPoint files are allowed.", "danger")
                return render_template("hr/policy_form.html", policy=policy, categories=POLICY_CATEGORIES)
            old_path = os.path.join(_policies_dir(), policy.filename)
            if os.path.exists(old_path):
                os.remove(old_path)
            original_name = secure_filename(file.filename)
            stored_name = f"pol{policy.id}_{original_name}"
            file.save(os.path.join(_policies_dir(), stored_name))
            policy.filename = stored_name

        policy.category = category
        policy.title = title
        policy.description = request.form.get("description", "").strip()
        policy.updated_by_id = current_user.id
        db.session.commit()
        flash(f"'{policy.title}' updated.", "success")
        return redirect(url_for("hr.list_policies"))

    return render_template("hr/policy_form.html", policy=policy, categories=POLICY_CATEGORIES)


@hr_bp.route("/policies/<int:policy_id>/delete", methods=["POST"])
@login_required
@editor_required
def delete_policy(policy_id):
    policy = PolicyDocument.query.get_or_404(policy_id)
    file_path = os.path.join(_policies_dir(), policy.filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    title = policy.title
    db.session.delete(policy)
    db.session.commit()
    flash(f"'{title}' deleted.", "info")
    return redirect(url_for("hr.list_policies"))


# ---------- Time Sheets ----------

def _monday_of(d):
    return d - timedelta(days=d.weekday())


@hr_bp.route("/timesheets")
@login_required
def list_timesheets():
    my_sheets = (
        TimeSheet.query.filter_by(user_id=current_user.id)
        .order_by(TimeSheet.week_start.desc())
        .all()
    )
    team_sheets = []
    if current_user.role in REVIEWER_ROLES:
        team_sheets = (
            TimeSheet.query.filter(TimeSheet.user_id != current_user.id)
            .order_by(TimeSheet.status.asc(), TimeSheet.week_start.desc())
            .all()
        )
    uploads = (
        TimeSheetUpload.query.filter_by(user_id=current_user.id).order_by(TimeSheetUpload.uploaded_at.desc()).all()
        if current_user.role not in REVIEWER_ROLES else
        TimeSheetUpload.query.order_by(TimeSheetUpload.uploaded_at.desc()).all()
    )
    return render_template(
        "hr/timesheets_list.html",
        my_sheets=my_sheets,
        team_sheets=team_sheets,
        uploads=uploads,
        default_week=_monday_of(date.today()).isoformat(),
    )


@hr_bp.route("/timesheets/new", methods=["POST"])
@login_required
def new_timesheet():
    week_start_raw = request.form.get("week_start")
    try:
        week_start = _monday_of(datetime.strptime(week_start_raw, "%Y-%m-%d").date()) if week_start_raw else _monday_of(date.today())
    except ValueError:
        week_start = _monday_of(date.today())

    existing = TimeSheet.query.filter_by(user_id=current_user.id, week_start=week_start).first()
    if existing:
        flash("You already have a timesheet for that week - opening it below.", "info")
        return redirect(url_for("hr.view_timesheet", timesheet_id=existing.id))

    sheet = TimeSheet(user_id=current_user.id, week_start=week_start)
    db.session.add(sheet)
    db.session.commit()
    flash("Timesheet started - add your hours below.", "success")
    return redirect(url_for("hr.view_timesheet", timesheet_id=sheet.id))


@hr_bp.route("/timesheets/<int:timesheet_id>")
@login_required
def view_timesheet(timesheet_id):
    sheet = TimeSheet.query.get_or_404(timesheet_id)
    if sheet.user_id != current_user.id and current_user.role not in REVIEWER_ROLES:
        abort(403)
    engagements = _visible_engagements_for(
        current_user,
        Engagement.query.filter(Engagement.status != "Completed").order_by(Engagement.title).all(),
    )
    can_edit = (sheet.user_id == current_user.id) and sheet.status != "Approved"
    can_review = current_user.role in REVIEWER_ROLES and sheet.user_id != current_user.id
    week_dates = [sheet.week_start + timedelta(days=i) for i in range(7)]
    return render_template(
        "hr/timesheet_detail.html",
        sheet=sheet,
        engagements=engagements,
        can_edit=can_edit,
        can_review=can_review,
        week_dates=week_dates,
    )


@hr_bp.route("/timesheets/<int:timesheet_id>/entries/add", methods=["POST"])
@login_required
def add_time_entry(timesheet_id):
    sheet = TimeSheet.query.get_or_404(timesheet_id)
    if sheet.user_id != current_user.id:
        abort(403)
    if sheet.status == "Approved":
        flash("This timesheet is already approved - ask your reviewer to reopen it before changing entries.", "danger")
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    work_date_raw = request.form.get("work_date")
    try:
        work_date = datetime.strptime(work_date_raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        flash("Please choose a valid date.", "danger")
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    try:
        hours = float(request.form.get("hours", 0) or 0)
    except ValueError:
        hours = 0

    if hours <= 0:
        flash("Please enter hours greater than zero.", "danger")
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    entry = TimeEntry(
        timesheet_id=sheet.id,
        work_date=work_date,
        engagement_id=request.form.get("engagement_id") or None,
        description=request.form.get("description", "").strip(),
        hours=hours,
    )
    db.session.add(entry)
    db.session.commit()
    flash("Entry added.", "success")
    return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))


@hr_bp.route("/timesheets/entries/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_time_entry(entry_id):
    entry = TimeEntry.query.get_or_404(entry_id)
    sheet = entry.timesheet
    if sheet.user_id != current_user.id:
        abort(403)
    if sheet.status == "Approved":
        flash("This timesheet is already approved - ask your reviewer to reopen it before changing entries.", "danger")
        return redirect(url_for("hr.view_timesheet", timesheet_id=sheet.id))
    db.session.delete(entry)
    db.session.commit()
    return redirect(url_for("hr.view_timesheet", timesheet_id=sheet.id))


@hr_bp.route("/timesheets/<int:timesheet_id>/approve", methods=["POST"])
@login_required
@reviewer_required
def approve_timesheet(timesheet_id):
    sheet = TimeSheet.query.get_or_404(timesheet_id)
    if sheet.user_id == current_user.id:
        flash("You can't approve your own timesheet.", "danger")
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))
    sheet.status = "Approved"
    sheet.reviewed_by_id = current_user.id
    sheet.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f"Timesheet approved for {sheet.user.name}.", "success")
    return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))


@hr_bp.route("/timesheets/<int:timesheet_id>/unapprove", methods=["POST"])
@login_required
@reviewer_required
def unapprove_timesheet(timesheet_id):
    sheet = TimeSheet.query.get_or_404(timesheet_id)
    sheet.status = "Submitted"
    sheet.reviewed_by_id = None
    sheet.reviewed_at = None
    db.session.commit()
    flash("Timesheet reopened for editing.", "info")
    return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))


@hr_bp.route("/timesheets/upload", methods=["POST"])
@login_required
def upload_timesheet():
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("hr.list_timesheets"))
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_TIMESHEET_UPLOAD_EXTENSIONS:
        flash("Only .xlsx, .xls or .pdf files are allowed for timesheet uploads.", "danger")
        return redirect(url_for("hr.list_timesheets"))

    original_name = secure_filename(file.filename)
    stored_name = f"ts_{current_user.id}_{uuid.uuid4().hex[:10]}_{original_name}"
    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "timesheets")
    os.makedirs(upload_dir, exist_ok=True)
    file.save(os.path.join(upload_dir, stored_name))

    record = TimeSheetUpload(
        user_id=current_user.id,
        period_label=request.form.get("period_label", "").strip(),
        original_filename=original_name,
        stored_filename=stored_name,
    )
    db.session.add(record)
    db.session.commit()
    flash(f"Uploaded '{original_name}'.", "success")
    return redirect(url_for("hr.list_timesheets"))


@hr_bp.route("/timesheets/uploads/<int:upload_id>/download")
@login_required
def download_timesheet_upload(upload_id):
    record = TimeSheetUpload.query.get_or_404(upload_id)
    if record.user_id != current_user.id and current_user.role not in REVIEWER_ROLES:
        abort(403)
    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "timesheets")
    return send_from_directory(
        upload_dir, record.stored_filename, as_attachment=True, download_name=record.original_filename,
    )


@hr_bp.route("/timesheets/uploads/<int:upload_id>/delete", methods=["POST"])
@login_required
def delete_timesheet_upload(upload_id):
    record = TimeSheetUpload.query.get_or_404(upload_id)
    if record.user_id != current_user.id and current_user.role not in REVIEWER_ROLES:
        abort(403)
    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "timesheets")
    try:
        os.remove(os.path.join(upload_dir, record.stored_filename))
    except OSError:
        pass
    db.session.delete(record)
    db.session.commit()
    return redirect(url_for("hr.list_timesheets"))


# ---------- Tasks / To-Do List (firm-wide task board) ----------
#
# One place that combines two kinds of task:
#   - EngagementTask: work tied to a specific engagement (its own Tasks tab
#     there too - this board just collates them across every engagement).
#   - PersonalTask: general/admin work an employee is doing that isn't tied
#     to any engagement ("upload work and tasks they are working on").
# A task assigned to (or reassigned/updated for) someone other than the
# person making the change sends them a notification via the existing
# internal Messages system (see models.notify_task_assignment) - answering
# "give notifications on tasks uploaded by other team members affecting the
# employee" without building a second, parallel notification channel.

@hr_bp.route("/projects")
@login_required
def project_board():
    status_filter = request.args.get("status", "")
    assignee_filter = request.args.get("assigned_to", "")
    view = request.args.get("view", "")  # "mine" narrows to tasks assigned to me

    eng_query = EngagementTask.query.join(Engagement)
    personal_query = PersonalTask.query
    if status_filter:
        eng_query = eng_query.filter(EngagementTask.status == status_filter)
        personal_query = personal_query.filter(PersonalTask.status == status_filter)
    if assignee_filter:
        eng_query = eng_query.filter(EngagementTask.assigned_to_id == assignee_filter)
        personal_query = personal_query.filter(PersonalTask.assigned_to_id == assignee_filter)
    if view == "mine":
        eng_query = eng_query.filter(EngagementTask.assigned_to_id == current_user.id)
        personal_query = personal_query.filter(
            (PersonalTask.assigned_to_id == current_user.id) | (PersonalTask.created_by_id == current_user.id)
        )

    engagement_tasks = eng_query.order_by(EngagementTask.due_date.asc().nullslast()).all()
    if current_user.role != "admin":
        engagement_tasks = [t for t in engagement_tasks if user_can_access_engagement(current_user, t.engagement)]

    personal_tasks = personal_query.order_by(PersonalTask.due_date.asc().nullslast()).all()
    if current_user.role != "admin":
        # A personal task is visible firm-wide (it's a to-do, not a
        # confidential audit workpaper) unless it references an engagement
        # the viewer can't access, in which case it's hidden like any other
        # engagement-linked content.
        personal_tasks = [t for t in personal_tasks if not t.engagement or user_can_access_engagement(current_user, t.engagement)]

    people = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    engagements = Engagement.query.order_by(Engagement.title).all()
    if current_user.role != "admin":
        engagements = [e for e in engagements if user_can_access_engagement(current_user, e)]

    return render_template(
        "projects/board.html",
        engagement_tasks=engagement_tasks,
        personal_tasks=personal_tasks,
        people=people,
        engagements=engagements,
        statuses=TASK_STATUSES,
        status_filter=status_filter,
        assignee_filter=assignee_filter,
        view=view,
    )


@hr_bp.route("/projects/personal/add", methods=["POST"])
@login_required
def add_personal_task():
    due_date = request.form.get("due_date")
    engagement_id = request.form.get("engagement_id") or None
    if engagement_id:
        engagement = Engagement.query.get_or_404(int(engagement_id))
        if current_user.role != "admin" and not user_can_access_engagement(current_user, engagement):
            abort(403)
    task = PersonalTask(
        created_by_id=current_user.id,
        assigned_to_id=int(request.form.get("assigned_to_id")) if request.form.get("assigned_to_id") else current_user.id,
        engagement_id=int(engagement_id) if engagement_id else None,
        title=request.form.get("title", "").strip(),
        description=request.form.get("description", "").strip(),
        due_date=datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None,
        priority=request.form.get("priority", "Normal"),
        status=request.form.get("status", "To Do"),
    )
    if not task.title:
        flash("Please enter a task title.", "danger")
        return redirect(url_for("hr.project_board"))
    db.session.add(task)
    db.session.flush()
    if task.assigned_to_id and task.assigned_to_id != current_user.id:
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task assigned: {task.title}",
            f"{current_user.name} assigned you a task:\n\n{task.title}"
            + (f"\nDue {task.due_date.strftime('%d %b %Y')}" if task.due_date else "")
            + (f"\n\n{task.description}" if task.description else ""),
        )
    db.session.commit()
    flash("Task added.", "success")
    return redirect(url_for("hr.project_board"))


@hr_bp.route("/projects/personal/<int:task_id>/update", methods=["POST"])
@login_required
def update_personal_task(task_id):
    task = PersonalTask.query.get_or_404(task_id)
    if task.engagement and current_user.role != "admin" and not user_can_access_engagement(current_user, task.engagement):
        abort(403)
    old_assigned_to_id = task.assigned_to_id
    old_status = task.status
    old_due_date = task.due_date
    task.title = request.form.get("title", task.title)
    task.description = request.form.get("description", task.description)
    task.assigned_to_id = int(request.form.get("assigned_to_id")) if request.form.get("assigned_to_id") else task.assigned_to_id
    due_date = request.form.get("due_date")
    task.due_date = datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None
    task.priority = request.form.get("priority", task.priority)
    task.status = request.form.get("status", task.status)
    if task.assigned_to_id and task.assigned_to_id != old_assigned_to_id:
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task assigned: {task.title}",
            f"{current_user.name} assigned you a task:\n\n{task.title}"
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
            f"{current_user.name} updated a task assigned to you:\n\n{task.title} - {', '.join(changes)}.",
        )
    if task.status == "Done":
        task.completed_by_id = current_user.id
        task.completed_at = datetime.utcnow()
    else:
        task.completed_by_id = None
        task.completed_at = None
    db.session.commit()
    flash("Task updated.", "success")
    return redirect(url_for("hr.project_board"))


@hr_bp.route("/projects/personal/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_personal_task(task_id):
    task = PersonalTask.query.get_or_404(task_id)
    if task.created_by_id != current_user.id and current_user.role != "admin":
        abort(403)
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.", "info")
    return redirect(url_for("hr.project_board"))


# ---------- Planner: audit timetable + staffing grid ----------

def _monday(d):
    return d - timedelta(days=d.weekday())


@hr_bp.route("/planner")
@login_required
def planner():
    today = date.today()

    # --- Audit timetable: every active engagement's key dates, sorted by
    # whichever is soonest, with a "clash" flag on weeks that have more than
    # one deadline landing in them. ---
    engagements = _visible_engagements_for(
        current_user,
        Engagement.query.filter(Engagement.status != "Completed")
        .order_by(Engagement.deadline.asc().nullslast(), Engagement.start_date.asc().nullslast())
        .all(),
    )
    deadline_week_counts = {}
    for e in engagements:
        if e.deadline:
            wk = _monday(e.deadline)
            deadline_week_counts[wk] = deadline_week_counts.get(wk, 0) + 1
    timetable = [
        {"engagement": e, "clash": bool(e.deadline and deadline_week_counts.get(_monday(e.deadline), 0) > 1)}
        for e in engagements
    ]

    # --- Staffing grid: staff x week, showing who's booked on what and how
    # much of their time is committed that week. ---
    try:
        num_weeks = max(1, min(int(request.args.get("weeks", 8)), 16))
    except ValueError:
        num_weeks = 8
    start_param = request.args.get("start")
    try:
        grid_start = _monday(datetime.strptime(start_param, "%Y-%m-%d").date()) if start_param else _monday(today)
    except ValueError:
        grid_start = _monday(today)

    weeks = [grid_start + timedelta(weeks=i) for i in range(num_weeks)]
    people = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    allocations = StaffAllocation.query.filter(
        StaffAllocation.end_date >= weeks[0], StaffAllocation.start_date <= weeks[-1] + timedelta(days=6)
    ).all()
    if current_user.role != "admin":
        # A non-admin's staffing grid shouldn't reveal that a colleague is
        # booked on an engagement they themselves aren't assigned to -
        # their cells simply show less than the full picture; Admin always
        # sees every allocation.
        allocations = [a for a in allocations if user_can_access_engagement(current_user, a.engagement)]

    grid = {}
    for person in people:
        row = []
        for week_start in weeks:
            week_end = week_start + timedelta(days=6)
            cell_allocs = [
                a for a in allocations
                if a.user_id == person.id and a.overlaps(week_start, week_end)
            ]
            total_pct = sum(a.allocation_pct for a in cell_allocs)
            row.append({"allocations": cell_allocs, "total_pct": total_pct})
        grid[person.id] = row

    prev_start = (grid_start - timedelta(weeks=num_weeks)).isoformat()
    next_start = (grid_start + timedelta(weeks=num_weeks)).isoformat()

    return render_template(
        "hr/planner.html",
        timetable=timetable,
        people=people,
        weeks=weeks,
        grid=grid,
        num_weeks=num_weeks,
        prev_start=prev_start,
        next_start=next_start,
        today=today,
    )
