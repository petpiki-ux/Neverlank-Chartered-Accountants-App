"""Neverlank Training Services.

A client engagement of type "Neverlank Training Services" gets its own
workspace (this module + templates/training/*) instead of the audit tab set:

  - Sessions: each course/session delivered under the engagement - title,
    trainer, dates, venue or online link, fee, seats, CPD hours, status.
  - Per session: the attendee register (attendance, fee paid, certificate
    number, CPD hours awarded), materials (slides/handouts, stored as
    ordinary engagement Documents), and participant feedback ratings.
  - Firm-wide dashboard of upcoming and recent sessions across clients.
"""
import io
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user

from extensions import db
from models import (
    Engagement, User, TrainingSession, TrainingAttendee, TrainingMaterial,
    TRAINING_MODES, TRAINING_STATUSES, TRAINING_ATTENDANCE, TRAINING_SERVICE_TYPE,
    user_has_permission, user_can_access_engagement,
)
from engagements import _ensure_engagement_access, _save_engagement_document, _allowed_file

training_bp = Blueprint("training", __name__, url_prefix="/training")

MATERIALS_CATEGORY = "Training Materials"


# ---------------------------------------------------------------- helpers

def _ensure_training_permission():
    if not user_has_permission(current_user, "manage_training"):
        abort(403)


def _load_engagement(engagement_id):
    _ensure_training_permission()
    engagement = Engagement.query.get_or_404(engagement_id)
    if not engagement.is_training_service:
        abort(404)
    _ensure_engagement_access(engagement)
    return engagement


def _load_session(session_id):
    session = TrainingSession.query.get_or_404(session_id)
    return session, _load_engagement(session.engagement_id)


def _load_attendee(attendee_id):
    attendee = TrainingAttendee.query.get_or_404(attendee_id)
    session, engagement = _load_session(attendee.session_id)
    return attendee, session, engagement


def _parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rating(value):
    n = _parse_int(value)
    return n if n and 1 <= n <= 5 else None


def _clean_choice(value, allowed, default):
    return value if value in allowed else default


def _apply_session_form(session):
    """Copies the session form's fields onto a TrainingSession. Returns an
    error message, or None."""
    title = request.form.get("title", "").strip()
    if not title:
        return "Give the session a title."
    start, end = _parse_date(request.form.get("start_date")), _parse_date(request.form.get("end_date"))
    if start and end and end < start:
        return "The end date can't be before the start date."
    session.title = title[:200]
    session.description = request.form.get("description", "").strip()
    trainer_user = request.form.get("trainer_user_id", "").strip()
    session.trainer_user_id = int(trainer_user) if trainer_user.isdigit() else None
    session.trainer_name = request.form.get("trainer_name", "").strip()[:200] or None
    session.start_date, session.end_date = start, end or start
    session.start_time = request.form.get("start_time", "").strip()[:10] or None
    session.end_time = request.form.get("end_time", "").strip()[:10] or None
    session.mode = _clean_choice(request.form.get("mode"), TRAINING_MODES, "In person")
    session.venue = request.form.get("venue", "").strip()[:300] or None
    session.fee_per_participant = _parse_float(request.form.get("fee_per_participant"))
    session.currency = (request.form.get("currency") or "USD").strip()[:10] or "USD"
    session.seats = _parse_int(request.form.get("seats"))
    session.cpd_hours = _parse_float(request.form.get("cpd_hours"))
    session.status = _clean_choice(request.form.get("status"), TRAINING_STATUSES, "Planned")
    session.notes = request.form.get("notes", "").strip()
    return None


# ---------------------------------------------------------------- firm-wide dashboard

@training_bp.route("/")
@login_required
def dashboard():
    _ensure_training_permission()
    today = date.today()
    sessions = [
        s for s in TrainingSession.query.order_by(TrainingSession.start_date).all()
        if user_can_access_engagement(current_user, s.engagement)
    ]
    upcoming = [s for s in sessions if s.status != "Cancelled" and (s.start_date is None or (s.end_date or s.start_date) >= today) and s.status != "Completed"]
    recent = [s for s in sessions if s not in upcoming]
    recent.sort(key=lambda s: s.start_date or date.min, reverse=True)
    engagements = [
        e for e in Engagement.query.filter_by(type=TRAINING_SERVICE_TYPE).order_by(Engagement.title).all()
        if user_can_access_engagement(current_user, e)
    ]
    return render_template(
        "training/dashboard.html", upcoming=upcoming, recent=recent[:15], engagements=engagements, today=today,
    )


# ---------------------------------------------------------------- sessions

@training_bp.route("/engagement/<int:engagement_id>/sessions")
@login_required
def sessions(engagement_id):
    engagement = _load_engagement(engagement_id)
    items = engagement.training_sessions.all()
    items.sort(key=lambda s: (s.start_date or date.max), reverse=True)
    staff = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    return render_template(
        "training/sessions.html", engagement=engagement, sessions=items, staff=staff,
        modes=TRAINING_MODES, statuses=TRAINING_STATUSES, active_tab="sessions",
    )


@training_bp.route("/engagement/<int:engagement_id>/sessions/add", methods=["POST"])
@login_required
def add_session(engagement_id):
    engagement = _load_engagement(engagement_id)
    session = TrainingSession(engagement_id=engagement.id)
    error = _apply_session_form(session)
    if error:
        flash(error, "danger")
        return redirect(url_for("training.sessions", engagement_id=engagement_id))
    db.session.add(session)
    db.session.commit()
    flash(f"Session '{session.title}' created.", "success")
    return redirect(url_for("training.view_session", session_id=session.id))


@training_bp.route("/session/<int:session_id>")
@login_required
def view_session(session_id):
    session, engagement = _load_session(session_id)
    staff = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    return render_template(
        "training/session.html", ts=session, engagement=engagement, staff=staff,
        modes=TRAINING_MODES, statuses=TRAINING_STATUSES, attendance_statuses=TRAINING_ATTENDANCE,
        active_tab="sessions",
    )


@training_bp.route("/session/<int:session_id>/update", methods=["POST"])
@login_required
def update_session(session_id):
    session, engagement = _load_session(session_id)
    error = _apply_session_form(session)
    if error:
        db.session.rollback()
        flash(error, "danger")
    else:
        db.session.commit()
        flash("Session saved.", "success")
    return redirect(url_for("training.view_session", session_id=session_id))


@training_bp.route("/session/<int:session_id>/delete", methods=["POST"])
@login_required
def delete_session(session_id):
    session, engagement = _load_session(session_id)
    engagement_id = engagement.id
    db.session.delete(session)
    db.session.commit()
    flash("Session deleted.", "success")
    return redirect(url_for("training.sessions", engagement_id=engagement_id))


# ---------------------------------------------------------------- attendees

@training_bp.route("/session/<int:session_id>/attendees/add", methods=["POST"])
@login_required
def add_attendee(session_id):
    session, engagement = _load_session(session_id)
    name = request.form.get("full_name", "").strip()
    if not name:
        flash("Enter the participant's name.", "danger")
        return redirect(url_for("training.view_session", session_id=session_id))
    if session.seats and len(session.active_attendees) >= session.seats:
        flash(f"Registered anyway, but note this session's {session.seats} seat(s) are now over-subscribed.", "warning")
    db.session.add(TrainingAttendee(
        session_id=session.id, full_name=name[:200],
        email=request.form.get("email", "").strip()[:200] or None,
        phone=request.form.get("phone", "").strip()[:60] or None,
        organisation=request.form.get("organisation", "").strip()[:200] or None,
        designation=request.form.get("designation", "").strip()[:200] or None,
    ))
    db.session.commit()
    flash(f"{name} registered.", "success")
    return redirect(url_for("training.view_session", session_id=session_id))


@training_bp.route("/attendee/<int:attendee_id>/update", methods=["POST"])
@login_required
def update_attendee(attendee_id):
    attendee, session, engagement = _load_attendee(attendee_id)
    name = request.form.get("full_name", "").strip()
    if name:
        attendee.full_name = name[:200]
    attendee.email = request.form.get("email", "").strip()[:200] or None
    attendee.phone = request.form.get("phone", "").strip()[:60] or None
    attendee.organisation = request.form.get("organisation", "").strip()[:200] or None
    attendee.designation = request.form.get("designation", "").strip()[:200] or None
    new_status = _clean_choice(request.form.get("status"), TRAINING_ATTENDANCE, attendee.status)
    attendee.status = new_status
    attendee.fee_paid = _parse_float(request.form.get("fee_paid"))
    # CPD hours: an explicit value wins; otherwise attending earns the
    # session's CPD hours, and not attending earns none.
    explicit_cpd = _parse_float(request.form.get("cpd_hours_awarded"))
    if explicit_cpd is not None:
        attendee.cpd_hours_awarded = explicit_cpd
    elif new_status == "Attended" and attendee.cpd_hours_awarded is None:
        attendee.cpd_hours_awarded = session.cpd_hours
    elif new_status in ("Absent", "Cancelled"):
        attendee.cpd_hours_awarded = None
    db.session.commit()
    flash(f"{attendee.full_name} updated.", "success")
    return redirect(url_for("training.view_session", session_id=session.id))


@training_bp.route("/attendee/<int:attendee_id>/delete", methods=["POST"])
@login_required
def delete_attendee(attendee_id):
    attendee, session, engagement = _load_attendee(attendee_id)
    db.session.delete(attendee)
    db.session.commit()
    flash("Participant removed from the register.", "success")
    return redirect(url_for("training.view_session", session_id=session.id))


@training_bp.route("/session/<int:session_id>/mark-all-attended", methods=["POST"])
@login_required
def mark_all_attended(session_id):
    session, engagement = _load_session(session_id)
    count = 0
    for a in session.attendees:
        if a.status == "Registered":
            a.status = "Attended"
            if a.cpd_hours_awarded is None:
                a.cpd_hours_awarded = session.cpd_hours
            count += 1
    db.session.commit()
    flash(f"{count} registered participant(s) marked as attended.", "success")
    return redirect(url_for("training.view_session", session_id=session_id))


def _next_certificate_number(session):
    """NTS-<year>-S<session id>-<nnn>: unique per session, sequential."""
    year = (session.start_date or date.today()).year
    issued = sum(1 for a in session.attendees if a.certificate_number)
    return f"NTS-{year}-S{session.id}-{issued + 1:03d}"


@training_bp.route("/attendee/<int:attendee_id>/certificate", methods=["POST"])
@login_required
def issue_certificate(attendee_id):
    attendee, session, engagement = _load_attendee(attendee_id)
    if attendee.status != "Attended":
        flash("A certificate can only be issued to a participant marked as Attended.", "danger")
    elif attendee.certificate_number:
        flash(f"{attendee.full_name} already has certificate {attendee.certificate_number}.", "warning")
    else:
        attendee.certificate_number = _next_certificate_number(session)
        attendee.certificate_issued_at = date.today()
        if attendee.cpd_hours_awarded is None:
            attendee.cpd_hours_awarded = session.cpd_hours
        db.session.commit()
        flash(f"Certificate {attendee.certificate_number} issued to {attendee.full_name}.", "success")
    return redirect(url_for("training.view_session", session_id=session.id))


@training_bp.route("/attendee/<int:attendee_id>/feedback", methods=["POST"])
@login_required
def save_feedback(attendee_id):
    attendee, session, engagement = _load_attendee(attendee_id)
    attendee.rating_overall = _rating(request.form.get("rating_overall"))
    attendee.rating_trainer = _rating(request.form.get("rating_trainer"))
    attendee.rating_content = _rating(request.form.get("rating_content"))
    attendee.rating_materials = _rating(request.form.get("rating_materials"))
    attendee.feedback_comments = request.form.get("feedback_comments", "").strip() or None
    attendee.feedback_at = datetime.utcnow() if any(
        [attendee.rating_overall, attendee.rating_trainer, attendee.rating_content, attendee.rating_materials,
         attendee.feedback_comments]) else None
    db.session.commit()
    flash(f"Feedback recorded for {attendee.full_name}.", "success")
    return redirect(url_for("training.view_session", session_id=session.id))


# ---------------------------------------------------------------- materials

@training_bp.route("/session/<int:session_id>/materials/upload", methods=["POST"])
@login_required
def upload_material(session_id):
    session, engagement = _load_session(session_id)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Choose a file to upload.", "danger")
        return redirect(url_for("training.view_session", session_id=session_id))
    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("training.view_session", session_id=session_id))
    title = request.form.get("title", "").strip() or file.filename
    doc, version = _save_engagement_document(
        engagement.id, file, MATERIALS_CATEGORY, "", f"Training material for session: {session.title}",
    )
    db.session.flush()
    db.session.add(TrainingMaterial(session_id=session.id, document_id=doc.id, title=title[:200]))
    db.session.commit()
    flash(f"'{title}' attached to the session.", "success")
    return redirect(url_for("training.view_session", session_id=session_id))


@training_bp.route("/material/<int:material_id>/delete", methods=["POST"])
@login_required
def delete_material(material_id):
    material = TrainingMaterial.query.get_or_404(material_id)
    session, engagement = _load_session(material.session_id)
    # Only detaches it from the session - the filed Document stays under the
    # engagement's Documents tab, like any other filed paper.
    db.session.delete(material)
    db.session.commit()
    flash("Material detached from the session (the file stays on the engagement's Documents tab).", "success")
    return redirect(url_for("training.view_session", session_id=session.id))


# ---------------------------------------------------------------- attendance register export

@training_bp.route("/session/<int:session_id>/register.xlsx")
@login_required
def download_register(session_id):
    session, engagement = _load_session(session_id)
    import workpapers as wp
    wb, ws = wp._new_workbook_sheet("Training attendance register", engagement, "Register")
    ws["A6"] = f"{session.title} - {session.start_date.strftime('%d %b %Y') if session.start_date else 'date TBC'}"
    ws["A6"].font = wp.Font(bold=True)
    headers = ["#", "Name", "Organisation", "Designation", "Email", "Phone", "Status", "Fee paid",
               "CPD hours", "Certificate no.", "Signature"]
    row = wp._header_row(ws, 8, headers)
    for i, a in enumerate(session.attendees, start=1):
        for col, value in enumerate([
            i, a.full_name, a.organisation or "", a.designation or "", a.email or "", a.phone or "", a.status,
            a.fee_paid if a.fee_paid is not None else "", a.cpd_hours_awarded if a.cpd_hours_awarded is not None else "",
            a.certificate_number or "", "",
        ], start=1):
            ws.cell(row=row, column=col, value=value)
        row += 1
    wp._autofit(ws, [5, 28, 26, 22, 28, 16, 12, 10, 10, 20, 22])
    buf = wp._finish_wb(wb)
    safe = "".join(ch for ch in session.title if ch.isalnum() or ch in " _-").strip().replace(" ", "_")[:60]
    return send_file(
        buf, as_attachment=True, download_name=f"Attendance_Register_{safe}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
