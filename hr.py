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
    PolicyDocument, TimeSheet, TimeEntry, TimeSheetUpload, CheckInRecord, User, Engagement, Client,
    EngagementTask, PersonalTask, PersonalSubtask, StaffAllocation, POLICY_CATEGORIES, REVIEWER_ROLES, TASK_STATUSES,
    PayrollEmployee, LeaveType, LeaveBalance,
    user_has_permission, user_can_access_engagement, notify_task_assignment,
)
from config import Config
import leave_calc

import file_text_extraction
import policy_summary
import policy_chunking
import policy_search

hr_bp = Blueprint("hr", __name__, url_prefix="/hr")

ALLOWED_POLICY_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"}
ALLOWED_TIMESHEET_UPLOAD_EXTENSIONS = {"xlsx", "xls", "pdf"}


def research_required(f):
    """Gates the Firm Library's AI research features ("Ask the Firm
    Library", and seeing the AI-generated summary/key points on a document)
    behind the configurable "Research the Firm Library" permission (Team >
    Permissions) - piloted as Partner/Admin only. Viewing and downloading a
    policy/procedure is unaffected: that stays open to everyone, same as
    before this feature existed."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not user_has_permission(current_user, "research_firm_library"):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def _process_policy_file(policy, filepath, original_name):
    """Shared by new_policy/edit_policy: extracts text from the just-saved
    file (file_text_extraction.py - PDF/Word/Excel/PowerPoint), gets an AI
    summary (policy_summary.py) and chunks the extracted text for "Ask the
    Firm Library" (policy_chunking.py). Mirrors tax.py's handling of a filed
    Legislative Update: the upload itself is never blocked or lost if
    extraction/AI fails or isn't configured - every outcome degrades
    gracefully and is simply shown (or not) on the list page. Does not
    commit - the caller commits."""
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    text, extraction_status, page_count = file_text_extraction.extract_text_for_file(filepath, ext)
    policy.extracted_text = text or None
    policy.extraction_status = extraction_status
    policy.page_count = page_count

    result, ai_status, ai_error = policy_summary.summarize_policy_document(filepath, text, extraction_status)
    policy.ai_status = ai_status
    policy.ai_error = ai_error
    policy.ai_processed_at = datetime.utcnow()
    if ai_status == "done":
        policy.ai_summary = result["summary"]
        policy.set_ai_key_points(result["key_points"])
        policy.ai_suggested_category = result["suggested_category"]
    else:
        policy.ai_summary = None
        policy.set_ai_key_points([])
        policy.ai_suggested_category = None

    if policy.extracted_text:
        # Clear any stale chunks from a previous file before re-chunking -
        # relevant on edit_policy, where the file (and so the text) can
        # change; ensure_chunks() itself is a no-op if chunks already exist,
        # so old chunks from a replaced file would otherwise linger. Clearing
        # via the collection itself (rather than deleting each chunk
        # directly) is what actually empties policy.chunks in memory too -
        # cascade="all, delete-orphan" then deletes the orphaned rows -
        # deleting them directly leaves the already-loaded `chunks`
        # collection looking non-empty, which would make ensure_chunks()
        # below wrongly think chunks already exist and skip re-chunking.
        policy.chunks = []
        db.session.flush()
        policy_chunking.ensure_chunks(policy)
    else:
        policy.chunks = []


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


def _render_policies_list(**extra):
    library = {}
    for category in POLICY_CATEGORIES:
        items = PolicyDocument.query.filter_by(category=category).order_by(
            PolicyDocument.order, PolicyDocument.title
        ).all()
        if items:
            library[category] = items
    context = dict(
        library=library,
        can_edit=user_has_permission(current_user, "manage_policies"),
        can_research=user_has_permission(current_user, "research_firm_library"),
        ask_question=None, ask_status=None, ask_answer=None, ask_citations=None, ask_candidates=None,
        ask_from_cache=False, ask_cache_hit_count=None,
    )
    context.update(extra)
    return render_template("hr/policies_list.html", **context)


@hr_bp.route("/policies")
@login_required
def list_policies():
    return _render_policies_list()


@hr_bp.route("/policies/ask", methods=["POST"])
@login_required
@research_required
def ask_policy_library():
    """"Ask the Firm Library" - a plain-English question answered from the
    firm's own filed Policies & Procedures library (see policy_search.py),
    never from the model's general knowledge. Gated by the "Research the
    Firm Library" permission - it only reads, it never files or changes
    anything, but the AI-research surface itself is piloted as Partner/
    Admin only (see models.PERMISSIONS)."""
    question = request.form.get("question", "").strip()
    if not question:
        flash("Please enter a question to ask.", "danger")
        return _render_policies_list()

    status, payload = policy_search.ask(question, asked_by_id=current_user.id)
    if status == "not_configured":
        flash("Automatic question-answering isn't set up yet (ANTHROPIC_API_KEY is not set) - see the README.", "danger")
        return _render_policies_list(ask_question=question, ask_status=status)
    if status == "no_candidates":
        return _render_policies_list(ask_question=question, ask_status=status)
    if status == "no_answer":
        return _render_policies_list(
            ask_question=question, ask_status=status,
            ask_answer=payload["answer"], ask_candidates=payload["candidates"],
            ask_from_cache=payload.get("from_cache", False), ask_cache_hit_count=payload.get("cache_hit_count"),
        )
    if status == "error":
        flash(f"Couldn't answer that: {payload}", "danger")
        return _render_policies_list(ask_question=question, ask_status=status)
    # status == "done"
    return _render_policies_list(
        ask_question=question, ask_status=status,
        ask_answer=payload["answer"], ask_citations=payload["citations"],
        ask_from_cache=payload.get("from_cache", False), ask_cache_hit_count=payload.get("cache_hit_count"),
    )


@hr_bp.route("/policies/<int:policy_id>/reprocess", methods=["POST"])
@login_required
@editor_required
def reprocess_policy_document(policy_id):
    """Re-run text extraction and AI summarisation against an already-filed
    policy/procedure, without re-uploading the file - for when the API key
    wasn't configured yet at filing time, or the previous attempt failed
    transiently. Mirrors tax.reprocess_legislative_update."""
    policy = PolicyDocument.query.get_or_404(policy_id)
    filepath = os.path.join(_policies_dir(), policy.filename)
    if not os.path.exists(filepath):
        flash("The original file can no longer be found on disk - re-upload it.", "danger")
        return redirect(url_for("hr.list_policies"))

    _process_policy_file(policy, filepath, policy.filename)
    db.session.commit()
    if policy.ai_status == "done":
        flash("Reprocessed - the AI summary below has been refreshed.", "success")
    else:
        flash(f"Reprocessing failed: {policy.ai_error}", "danger")
    return redirect(url_for("hr.list_policies"))


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
        filepath = os.path.join(_policies_dir(), stored_name)
        file.save(filepath)
        policy.filename = stored_name
        db.session.commit()  # save the upload itself before attempting extraction/AI, so a slow/failed step never loses the file

        _process_policy_file(policy, filepath, original_name)
        db.session.commit()

        if policy.ai_status == "done":
            flash(f"'{policy.title}' added to the Policies library - AI summary generated below.", "success")
        elif policy.ai_status == "not_configured":
            flash(f"'{policy.title}' added to the Policies library, but automatic summarisation isn't set up yet ({policy.ai_error}).", "warning")
        else:
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
        replaced_file = False
        if file and file.filename != "":
            if not _allowed_policy_file(file.filename):
                flash("Only PDF, Word, Excel and PowerPoint files are allowed.", "danger")
                return render_template("hr/policy_form.html", policy=policy, categories=POLICY_CATEGORIES)
            old_path = os.path.join(_policies_dir(), policy.filename)
            if os.path.exists(old_path):
                os.remove(old_path)
            original_name = secure_filename(file.filename)
            stored_name = f"pol{policy.id}_{original_name}"
            new_path = os.path.join(_policies_dir(), stored_name)
            file.save(new_path)
            policy.filename = stored_name
            replaced_file = True

        policy.category = category
        policy.title = title
        policy.description = request.form.get("description", "").strip()
        policy.updated_by_id = current_user.id
        db.session.commit()

        if replaced_file:
            # The new file's text/summary/chunks replace whatever the old
            # file had - same reasoning as tax.py's reprocess: nothing about
            # the previous file's extraction is still meaningful once it's
            # been swapped out.
            _process_policy_file(policy, new_path, original_name)
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
    my_checkins = (
        CheckInRecord.query.filter_by(user_id=current_user.id)
        .order_by(CheckInRecord.check_in_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "hr/timesheets_list.html",
        my_sheets=my_sheets,
        team_sheets=team_sheets,
        uploads=uploads,
        my_checkins=my_checkins,
        default_week=_monday_of(date.today()).isoformat(),
    )


@hr_bp.route("/checkin", methods=["POST"])
@login_required
def check_in():
    """Starts the clock for today, via the Check In button in the top bar
    (available on every page). A no-op (just a flash) if already checked
    in - see check_out below for where the elapsed time actually lands."""
    open_existing = CheckInRecord.query.filter_by(user_id=current_user.id, check_out_at=None).first()
    if open_existing:
        flash(f"You're already checked in since {open_existing.check_in_at.strftime('%H:%M')}.", "info")
    else:
        now = datetime.utcnow()
        record = CheckInRecord(user_id=current_user.id, work_date=now.date(), check_in_at=now)
        db.session.add(record)
        db.session.commit()
        flash(f"Checked in at {now.strftime('%H:%M')}.", "success")
    return redirect(request.referrer or url_for("engagements.dashboard"))


@hr_bp.route("/checkout", methods=["POST"])
@login_required
def check_out():
    """Stops the clock and adds the elapsed hours as a TimeEntry on that
    day's TimeSheet (auto-starting the week's timesheet if it doesn't exist
    yet) - unless that week is already Approved, in which case the
    check-in/check-out is still recorded but nothing is added automatically,
    same guard as add_time_entry."""
    record = CheckInRecord.query.filter_by(user_id=current_user.id, check_out_at=None).first()
    if not record:
        flash("You're not checked in.", "danger")
        return redirect(request.referrer or url_for("engagements.dashboard"))

    record.check_out_at = datetime.utcnow()
    hours = record.elapsed_hours

    week_start = _monday_of(record.work_date)
    sheet = TimeSheet.query.filter_by(user_id=current_user.id, week_start=week_start).first()
    if not sheet:
        sheet = TimeSheet(user_id=current_user.id, week_start=week_start)
        db.session.add(sheet)
        db.session.flush()

    if sheet.status == "Approved":
        db.session.commit()
        flash(
            f"Checked out at {record.check_out_at.strftime('%H:%M')} ({hours} hrs) - "
            "that week's timesheet is already approved, so it wasn't added automatically. "
            "Ask your reviewer to reopen it, then add the hours by hand.",
            "danger",
        )
        return redirect(request.referrer or url_for("engagements.dashboard"))

    if hours and hours > 0:
        entry = TimeEntry(
            timesheet_id=sheet.id,
            work_date=record.work_date,
            description=f"Checked in {record.check_in_at.strftime('%H:%M')} - checked out {record.check_out_at.strftime('%H:%M')}",
            hours=hours,
        )
        db.session.add(entry)
        db.session.flush()
        record.time_entry_id = entry.id

    db.session.commit()
    flash(f"Checked out at {record.check_out_at.strftime('%H:%M')} - {hours} hrs added to this week's timesheet.", "success")
    return redirect(request.referrer or url_for("engagements.dashboard"))


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

    # Leave capture on the timesheet is only possible if this SHEET'S OWNER
    # has a linked Payroll employee profile (see _add_leave_entry) - looked
    # up by the sheet's own user_id, not current_user, so a reviewer viewing
    # someone else's timesheet still sees the right balances (read-only for
    # them, since can_edit is false in that case).
    sheet_employee = PayrollEmployee.query.filter_by(user_id=sheet.user_id, scope="internal").first()
    leave_balances = leave_calc.sync_all_balances_for_employee(sheet_employee) if sheet_employee else []

    return render_template(
        "hr/timesheet_detail.html",
        sheet=sheet,
        engagements=engagements,
        can_edit=can_edit,
        can_review=can_review,
        week_dates=week_dates,
        sheet_employee=sheet_employee,
        leave_balances=leave_balances,
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

    entry_kind = request.form.get("entry_kind", "work")

    if entry_kind == "leave":
        return _add_leave_entry(sheet, work_date)

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


def _add_leave_entry(sheet, work_date):
    """Records leave taken on one day of `sheet` - the leave-capture half of
    add_time_entry above, split out for readability. Validates against the
    employee's own LeaveBalance (synced first, so the check uses up-to-date
    figures) before creating the entry, and moves the days into
    LeaveBalance.taken_days on success - see delete_time_entry for the
    reverse when such an entry is removed."""
    timesheet_id = sheet.id
    leave_type_id = request.form.get("leave_type_id")
    leave_type = LeaveType.query.get(leave_type_id) if leave_type_id else None
    if not leave_type:
        flash("Please choose a leave type.", "danger")
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    try:
        leave_days = float(request.form.get("leave_days", 0) or 0)
    except ValueError:
        leave_days = 0

    if leave_days <= 0:
        flash("Please enter a number of leave days greater than zero.", "danger")
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    employee = PayrollEmployee.query.filter_by(user_id=current_user.id, scope="internal").first()
    if not employee:
        flash(
            "No Payroll employee profile is linked to your login yet, so leave can't be tracked against a "
            "balance - ask an Admin/Partner to link one on Payroll > Employees first.",
            "danger",
        )
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    balance = leave_calc.get_or_create_balance(employee, leave_type)
    leave_calc.sync_leave_balance(employee, leave_type, balance)

    if leave_type.min_service_months_to_take:
        served_months = leave_calc.months_between(employee.date_joined, work_date) if employee.date_joined else 0
        if served_months < leave_type.min_service_months_to_take:
            flash(
                f"{leave_type.name} normally can't be TAKEN until {employee.full_name} has completed "
                f"{leave_type.min_service_months_to_take} months of continuous service (it still accrues "
                "from day one) - only override this if your own firm policy allows earlier taking.",
                "danger",
            )
            return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    available = balance.current_balance
    if leave_days > available:
        flash(
            f"Only {available:.2f} day(s) of {leave_type.name} are available for {employee.full_name} "
            f"(brought forward {balance.brought_forward or 0:.2f} + accrued {balance.accrued_days or 0:.2f} "
            f"- taken {balance.taken_days or 0:.2f}) - reduce the days, or top up the balance on the Leave "
            "screen first.",
            "danger",
        )
        return redirect(url_for("hr.view_timesheet", timesheet_id=timesheet_id))

    entry = TimeEntry(
        timesheet_id=timesheet_id,
        work_date=work_date,
        description=f"{leave_type.name} ({leave_days:g} day{'s' if leave_days != 1 else ''})",
        hours=0,
        leave_type_id=leave_type.id,
        leave_days=leave_days,
    )
    db.session.add(entry)
    balance.taken_days = round((balance.taken_days or 0.0) + leave_days, 2)
    db.session.commit()
    flash(f"{leave_days:g} day(s) of {leave_type.name} added - {balance.current_balance:.2f} day(s) remain.", "success")
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
    if entry.leave_type_id and entry.leave_days:
        # Reverse the leave taken by this entry before removing it, so
        # deleting a leave entry gives the days back to the balance it was
        # taken from - the same employee lookup add_time_entry used to take
        # them in the first place.
        employee = PayrollEmployee.query.filter_by(user_id=sheet.user_id, scope="internal").first()
        if employee:
            balance = LeaveBalance.query.filter_by(employee_id=employee.id, leave_type_id=entry.leave_type_id).first()
            if balance:
                balance.taken_days = round(max(0.0, (balance.taken_days or 0.0) - entry.leave_days), 2)
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


# --------------------------------------------------------------- Leave Management

def _to_float_or_none(value, default=None):
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


@hr_bp.route("/leave")
@login_required
def list_leave_balances():
    """Self-service: the current user's own leave balances, synced up to
    date on every view (see leave_calc.sync_all_balances_for_employee).
    Supervisor/Partner/Admin also see every internal employee's balances
    below, with a form to set/adjust each one's brought-forward balance."""
    my_employee = PayrollEmployee.query.filter_by(user_id=current_user.id, scope="internal").first()
    my_balances = leave_calc.sync_all_balances_for_employee(my_employee) if my_employee else []

    team_balances = None
    if current_user.role in REVIEWER_ROLES:
        employees = (
            PayrollEmployee.query.filter_by(scope="internal", is_active=True)
            .order_by(PayrollEmployee.full_name)
            .all()
        )
        team_balances = [(emp, leave_calc.sync_all_balances_for_employee(emp)) for emp in employees]

    return render_template(
        "hr/leave_balances.html",
        my_employee=my_employee,
        my_balances=my_balances,
        team_balances=team_balances,
        can_manage=current_user.role in REVIEWER_ROLES,
    )


@hr_bp.route("/leave/balances/<int:balance_id>/brought-forward", methods=["POST"])
@login_required
@reviewer_required
def set_leave_brought_forward(balance_id):
    balance = LeaveBalance.query.get_or_404(balance_id)
    balance.brought_forward = _to_float_or_none(request.form.get("brought_forward"), 0.0) or 0.0
    db.session.commit()
    flash(f"Brought-forward balance updated for {balance.employee.full_name} - {balance.leave_type.name}.", "success")
    return redirect(url_for("hr.list_leave_balances"))


@hr_bp.route("/leave/types", methods=["GET", "POST"])
@login_required
@reviewer_required
def leave_types():
    """Firm-editable leave type definitions - pre-loaded with Zimbabwe's
    statutory minimums (see LEAVE_TYPES_STATUTORY_ZW/seed.seed_leave_types)
    but every figure can be adjusted if the firm's own policy or a
    collective bargaining agreement is more generous."""
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Please name the leave type.", "danger")
                return redirect(url_for("hr.leave_types"))
            if LeaveType.query.filter_by(name=name).first():
                flash("A leave type with that name already exists.", "danger")
                return redirect(url_for("hr.leave_types"))
            db.session.add(LeaveType(
                name=name,
                accrual_days_per_month=_to_float_or_none(request.form.get("accrual_days_per_month"), 0.0) or 0.0,
                annual_entitlement_days=_to_float_or_none(request.form.get("annual_entitlement_days")),
                max_accumulation_days=_to_float_or_none(request.form.get("max_accumulation_days")),
                is_accumulative=bool(request.form.get("is_accumulative")),
                full_pay_days=_to_float_or_none(request.form.get("full_pay_days")),
                half_pay_pct=_to_float_or_none(request.form.get("half_pay_pct"), 50.0),
                min_service_months_to_take=int(_to_float_or_none(request.form.get("min_service_months_to_take"), 0) or 0),
                notes=request.form.get("notes", "").strip(),
                order=LeaveType.query.count(),
            ))
            db.session.commit()
            flash("Leave type added.", "success")
        elif action == "delete":
            lt = LeaveType.query.get_or_404(request.form.get("leave_type_id"))
            if LeaveBalance.query.filter_by(leave_type_id=lt.id).first():
                flash(f"Can't remove {lt.name} - it already has leave balances/history against it. Mark it inactive instead.", "danger")
            else:
                db.session.delete(lt)
                db.session.commit()
                flash("Leave type removed.", "success")
        else:
            lt = LeaveType.query.get_or_404(request.form.get("leave_type_id"))
            lt.accrual_days_per_month = _to_float_or_none(request.form.get("accrual_days_per_month"), lt.accrual_days_per_month) or 0.0
            lt.annual_entitlement_days = _to_float_or_none(request.form.get("annual_entitlement_days"))
            lt.max_accumulation_days = _to_float_or_none(request.form.get("max_accumulation_days"))
            lt.is_accumulative = bool(request.form.get("is_accumulative"))
            lt.full_pay_days = _to_float_or_none(request.form.get("full_pay_days"))
            lt.half_pay_pct = _to_float_or_none(request.form.get("half_pay_pct"), lt.half_pay_pct)
            lt.min_service_months_to_take = int(_to_float_or_none(request.form.get("min_service_months_to_take"), 0) or 0)
            lt.is_active = bool(request.form.get("is_active"))
            lt.notes = request.form.get("notes", "").strip()
            db.session.commit()
            flash(f"{lt.name} updated.", "success")
        return redirect(url_for("hr.leave_types"))

    all_leave_types = LeaveType.query.order_by(LeaveType.order).all()
    return render_template("hr/leave_types.html", leave_types=all_leave_types)


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
    search_query = request.args.get("q", "").strip()

    # Firm-wide visibility on this board is Partner-only: a Partner sees
    # everything, while Supervisor and Admin (and ordinary staff) only ever
    # see tasks they're directly involved in - assigned to them, or a
    # general to-do they created. This deliberately does NOT follow
    # REVIEWER_ROLES (which still governs review/sign-off elsewhere, e.g.
    # Team Time Sheets) - the Partner is the only role with the "see
    # everyone's work" view here, by the user's own instruction. This
    # overrides whatever the "view"/"assigned_to" query params say, so it
    # can't be bypassed by editing the URL.
    is_full_access = current_user.role == "partner"
    if not is_full_access:
        view = "mine"
        assignee_filter = ""

    eng_query = EngagementTask.query.join(Engagement).join(Client, Engagement.client_id == Client.id)
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
    if search_query:
        # Matches the task's own title/description, plus - for an
        # engagement task - the client/engagement it's on, so typing a
        # client name finds their tasks even if the task title itself
        # doesn't mention the client.
        like = f"%{search_query}%"
        eng_query = eng_query.filter(
            db.or_(
                EngagementTask.title.ilike(like),
                EngagementTask.description.ilike(like),
                Engagement.title.ilike(like),
                Client.name.ilike(like),
            )
        )
        personal_query = personal_query.filter(
            db.or_(
                PersonalTask.title.ilike(like),
                PersonalTask.description.ilike(like),
            )
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
        search_query=search_query,
        is_reviewer=is_full_access,  # template kwarg name kept as-is; means "sees everyone's tasks" (Partner only) here
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


@hr_bp.route("/projects/personal/<int:task_id>/subtasks/add", methods=["POST"])
@login_required
def add_subtask(task_id):
    """Adds a subtask (its own title + optional deadline) under a general
    to-do - see models.PersonalSubtask. Same visibility rule as editing the
    parent task itself: open to anyone who can see the task, gated only by
    the engagement-confidentiality check when the task references one."""
    task = PersonalTask.query.get_or_404(task_id)
    if task.engagement and current_user.role != "admin" and not user_can_access_engagement(current_user, task.engagement):
        abort(403)
    title = request.form.get("title", "").strip()
    if not title:
        flash("Please enter a subtask title.", "danger")
        return redirect(url_for("hr.project_board"))
    due_date_raw = request.form.get("due_date")
    try:
        due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date() if due_date_raw else None
    except ValueError:
        due_date = None
    subtask = PersonalSubtask(personal_task_id=task.id, title=title, due_date=due_date)
    db.session.add(subtask)
    db.session.commit()
    flash("Subtask added.", "success")
    return redirect(url_for("hr.project_board"))


@hr_bp.route("/projects/personal/subtasks/<int:subtask_id>/status", methods=["POST"])
@login_required
def update_subtask_status(subtask_id):
    """Changes a subtask's own status (To Do/In Progress/Review/Done) -
    purely its own, never the parent PersonalTask's status (see
    PersonalSubtask's docstring: subtasks are "pure tasks", each completed
    independently of the to-do they sit under)."""
    subtask = PersonalSubtask.query.get_or_404(subtask_id)
    task = subtask.personal_task
    if task.engagement and current_user.role != "admin" and not user_can_access_engagement(current_user, task.engagement):
        abort(403)
    subtask.status = request.form.get("status", subtask.status)
    subtask.completed_at = datetime.utcnow() if subtask.status == "Done" else None
    db.session.commit()
    return redirect(url_for("hr.project_board"))


@hr_bp.route("/projects/personal/subtasks/<int:subtask_id>/delete", methods=["POST"])
@login_required
def delete_subtask(subtask_id):
    subtask = PersonalSubtask.query.get_or_404(subtask_id)
    task = subtask.personal_task
    if task.created_by_id != current_user.id and current_user.role != "admin":
        abort(403)
    db.session.delete(subtask)
    db.session.commit()
    flash("Subtask deleted.", "info")
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
