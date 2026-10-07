"""Neverlank Anonymous Whistle-blower Services.

A client engagement of type "Neverlank Anonymous Whistle-blower Services"
gets its own workspace (this module + templates/whistleblower/*) instead of
the audit tab set:

  - Subscription & Channels: service period, fee, hotline details, who at
    the client receives reports, and the secret link to the client's public
    anonymous reporting page.
  - Cases: every report received - logged by staff (phone, email, in
    person...) or submitted by a reporter through the public page - with
    status, severity, assignee, outcome and a case log.
  - Client Reports: a periodic (quarterly / half-yearly / annual) summary
    for the client's audit committee, on screen and as a Word download.

Reporter anonymity is the point of the service, so the PUBLIC pages
(whistleblower_public_bp, no login) record NO name, IP address or browser
details. A reporter is given a reference code and a one-time follow-up key
(only its hash is stored) so they can check progress and reply anonymously
without ever identifying themselves.
"""
import secrets
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db
from timeutil import to_cat
from models import (
    Engagement, User, WhistleblowerService, WhistleblowerCase, WhistleblowerCaseNote,
    WB_CHANNELS, WB_CATEGORIES, WB_CATEGORY_LABELS, WB_SEVERITIES, WB_STATUSES, WB_REPORTING_FREQUENCIES,
    WHISTLEBLOWER_SERVICE_TYPE, user_has_permission, user_can_access_engagement,
)
from engagements import _ensure_engagement_access

wb_bp = Blueprint("whistleblower", __name__, url_prefix="/whistleblower")
wb_public_bp = Blueprint("whistleblower_public", __name__, url_prefix="/speak-up")

MIN_REPORT_LENGTH = 20
MAX_REPORT_LENGTH = 8000
REPORT_PERIODS = [
    ("Q1", "Quarter 1 (Jan - Mar)"), ("Q2", "Quarter 2 (Apr - Jun)"),
    ("Q3", "Quarter 3 (Jul - Sep)"), ("Q4", "Quarter 4 (Oct - Dec)"),
    ("H1", "Half-year 1 (Jan - Jun)"), ("H2", "Half-year 2 (Jul - Dec)"),
    ("FY", "Full year"),
]
REPORT_PERIOD_LABELS = dict(REPORT_PERIODS)
_PERIOD_MONTHS = {"Q1": (1, 3), "Q2": (4, 6), "Q3": (7, 9), "Q4": (10, 12), "H1": (1, 6), "H2": (7, 12), "FY": (1, 12)}


# ---------------------------------------------------------------- helpers

def _ensure_wb_permission():
    if not user_has_permission(current_user, "manage_whistleblower"):
        abort(403)


def _load_engagement(engagement_id):
    """The whistle-blower engagement, with every gate applied: right
    engagement type, the Manage Whistle-blower permission, and the usual
    engagement-team confidentiality/acceptance gate."""
    _ensure_wb_permission()
    engagement = Engagement.query.get_or_404(engagement_id)
    if not engagement.is_whistleblower_service:
        abort(404)
    _ensure_engagement_access(engagement)
    return engagement


def _load_case(case_id):
    case = WhistleblowerCase.query.get_or_404(case_id)
    return case, _load_engagement(case.engagement_id)


def _new_token():
    return secrets.token_urlsafe(18)


def _get_or_create_service(engagement):
    service = engagement.whistleblower_service
    if not service:
        service = WhistleblowerService(engagement_id=engagement.id, public_token=_new_token())
        db.session.add(service)
        db.session.commit()
    elif not service.public_token:
        service.public_token = _new_token()
        db.session.commit()
    return service


def _new_reference():
    while True:
        ref = "WB-" + secrets.token_hex(4).upper()
        if not WhistleblowerCase.query.filter_by(reference=ref).first():
            return ref


def _issue_followup_key(case):
    """Sets a new follow-up key on the case and returns the plain key - the
    only time it exists in readable form (only its hash is stored)."""
    key = secrets.token_urlsafe(9)
    case.followup_key_hash = generate_password_hash(key)
    return key


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


def _clean_choice(value, allowed, default):
    return value if value in allowed else default


def _service_context(engagement):
    return {"engagement": engagement, "service": _get_or_create_service(engagement)}


# ---------------------------------------------------------------- firm-wide dashboard

@wb_bp.route("/")
@login_required
def dashboard():
    _ensure_wb_permission()
    engagements = (
        Engagement.query.filter_by(type=WHISTLEBLOWER_SERVICE_TYPE).order_by(Engagement.title).all()
    )
    rows = []
    for e in engagements:
        if not user_can_access_engagement(current_user, e):
            continue
        cases = e.whistleblower_cases.all()
        open_cases = [c for c in cases if not c.is_closed]
        rows.append({
            "engagement": e,
            "service": e.whistleblower_service,
            "total": len(cases),
            "open": len(open_cases),
            "new": sum(1 for c in cases if c.status == "New"),
            "awaiting_reply": sum(1 for c in open_cases if c.has_unread_reporter_message),
            "critical": sum(1 for c in open_cases if c.severity in ("High", "Critical")),
        })
    return render_template("whistleblower/dashboard.html", rows=rows)


# ---------------------------------------------------------------- subscription & channels

@wb_bp.route("/engagement/<int:engagement_id>/subscription", methods=["GET", "POST"])
@login_required
def subscription(engagement_id):
    engagement = _load_engagement(engagement_id)
    service = _get_or_create_service(engagement)
    if request.method == "POST":
        service.service_start = _parse_date(request.form.get("service_start"))
        service.service_end = _parse_date(request.form.get("service_end"))
        service.annual_fee = _parse_float(request.form.get("annual_fee"))
        service.currency = (request.form.get("currency") or "USD").strip()[:10] or "USD"
        service.portal_enabled = bool(request.form.get("portal_enabled"))
        service.portal_welcome_text = request.form.get("portal_welcome_text", "").strip()
        service.hotline_phone = request.form.get("hotline_phone", "").strip()
        service.hotline_email = request.form.get("hotline_email", "").strip()
        service.hotline_whatsapp = request.form.get("hotline_whatsapp", "").strip()
        service.other_channels = request.form.get("other_channels", "").strip()
        service.client_contact_name = request.form.get("client_contact_name", "").strip()
        service.client_contact_role = request.form.get("client_contact_role", "").strip()
        service.client_contact_email = request.form.get("client_contact_email", "").strip()
        service.reporting_frequency = _clean_choice(request.form.get("reporting_frequency"), WB_REPORTING_FREQUENCIES, "Quarterly")
        service.notes = request.form.get("notes", "").strip()
        service.updated_by_id = current_user.id
        service.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Subscription & channels saved.", "success")
        return redirect(url_for("whistleblower.subscription", engagement_id=engagement_id))
    public_url = url_for("whistleblower_public.submit", token=service.public_token, _external=True)
    return render_template(
        "whistleblower/subscription.html", engagement=engagement, service=service, public_url=public_url,
        frequencies=WB_REPORTING_FREQUENCIES, active_tab="subscription",
    )


@wb_bp.route("/engagement/<int:engagement_id>/regenerate-link", methods=["POST"])
@login_required
def regenerate_link(engagement_id):
    engagement = _load_engagement(engagement_id)
    service = _get_or_create_service(engagement)
    service.public_token = _new_token()
    service.updated_by_id = current_user.id
    service.updated_at = datetime.utcnow()
    db.session.commit()
    flash("New anonymous reporting link generated - the old link no longer works. Send the new one to the client.", "success")
    return redirect(url_for("whistleblower.subscription", engagement_id=engagement_id))


# ---------------------------------------------------------------- cases

@wb_bp.route("/engagement/<int:engagement_id>/cases")
@login_required
def cases(engagement_id):
    engagement = _load_engagement(engagement_id)
    service = _get_or_create_service(engagement)
    status_filter = request.args.get("status", "open")
    category_filter = request.args.get("category", "")
    query = engagement.whistleblower_cases
    if status_filter == "open":
        items = [c for c in query.all() if not c.is_closed]
    elif status_filter == "closed":
        items = [c for c in query.all() if c.is_closed]
    elif status_filter in WB_STATUSES:
        items = [c for c in query.all() if c.status == status_filter]
    else:
        items = query.all()
    if category_filter:
        items = [c for c in items if c.category == category_filter]
    items.sort(key=lambda c: c.received_at or datetime.min, reverse=True)
    staff = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    return render_template(
        "whistleblower/cases.html", engagement=engagement, service=service, cases=items, staff=staff,
        status_filter=status_filter, category_filter=category_filter, channels=WB_CHANNELS,
        categories=WB_CATEGORIES, severities=WB_SEVERITIES, statuses=WB_STATUSES, active_tab="cases",
    )


@wb_bp.route("/engagement/<int:engagement_id>/cases/add", methods=["POST"])
@login_required
def add_case(engagement_id):
    """Log a report that reached Neverlank by phone, email, in person etc.
    The reporter's identity is never asked for - if they want to check back
    they're handed the reference and one-time follow-up key shown after
    saving."""
    engagement = _load_engagement(engagement_id)
    description = request.form.get("description", "").strip()
    if len(description) < 5:
        flash("Describe what was reported.", "danger")
        return redirect(url_for("whistleblower.cases", engagement_id=engagement_id))
    received_date = _parse_date(request.form.get("received_date")) or date.today()
    case = WhistleblowerCase(
        engagement_id=engagement_id, reference=_new_reference(),
        received_at=datetime.combine(received_date, datetime.utcnow().time()),
        channel=_clean_choice(request.form.get("channel"), WB_CHANNELS, "Other"),
        category=_clean_choice(request.form.get("category"), dict(WB_CATEGORIES), "other"),
        severity=_clean_choice(request.form.get("severity"), WB_SEVERITIES, "Medium"),
        subject=request.form.get("subject", "").strip()[:200] or None,
        description=description[:MAX_REPORT_LENGTH], status="New", source="staff", created_by_id=current_user.id,
    )
    key = _issue_followup_key(case)
    db.session.add(case)
    db.session.commit()
    flash(
        f"Case {case.reference} logged. If the reporter wants to check back anonymously, give them reference "
        f"{case.reference} and follow-up key {key} - the key is shown only now and cannot be recovered.",
        "success",
    )
    return redirect(url_for("whistleblower.view_case", case_id=case.id))


@wb_bp.route("/case/<int:case_id>")
@login_required
def view_case(case_id):
    case, engagement = _load_case(case_id)
    staff = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    return render_template(
        "whistleblower/case.html", case=case, engagement=engagement, service=_get_or_create_service(engagement),
        staff=staff, categories=WB_CATEGORIES, severities=WB_SEVERITIES, statuses=WB_STATUSES,
        active_tab="cases",
    )


@wb_bp.route("/case/<int:case_id>/update", methods=["POST"])
@login_required
def update_case(case_id):
    case, engagement = _load_case(case_id)
    case.status = _clean_choice(request.form.get("status"), WB_STATUSES, case.status)
    case.severity = _clean_choice(request.form.get("severity"), WB_SEVERITIES, case.severity)
    case.category = _clean_choice(request.form.get("category"), dict(WB_CATEGORIES), case.category)
    assignee = request.form.get("assigned_to_id", "").strip()
    case.assigned_to_id = int(assignee) if assignee.isdigit() else None
    case.outcome = request.form.get("outcome", "").strip()
    if case.is_closed and not case.closed_at:
        case.closed_at = datetime.utcnow()
    elif not case.is_closed:
        case.closed_at = None
    db.session.commit()
    flash("Case updated.", "success")
    return redirect(url_for("whistleblower.view_case", case_id=case_id))


@wb_bp.route("/case/<int:case_id>/note", methods=["POST"])
@login_required
def add_note(case_id):
    case, engagement = _load_case(case_id)
    body = request.form.get("body", "").strip()
    kind = _clean_choice(request.form.get("kind"), ("internal", "to_reporter"), "internal")
    if not body:
        flash("Write something first.", "danger")
        return redirect(url_for("whistleblower.view_case", case_id=case_id))
    db.session.add(WhistleblowerCaseNote(case_id=case.id, kind=kind, body=body, created_by_id=current_user.id))
    db.session.commit()
    flash("Message sent to the reporter's status page." if kind == "to_reporter" else "Note added.", "success")
    return redirect(url_for("whistleblower.view_case", case_id=case_id))


@wb_bp.route("/case/<int:case_id>/new-key", methods=["POST"])
@login_required
def new_followup_key(case_id):
    case, engagement = _load_case(case_id)
    key = _issue_followup_key(case)
    db.session.commit()
    flash(
        f"New follow-up key for {case.reference}: {key} - shown only now. The reporter's old key no longer works.",
        "success",
    )
    return redirect(url_for("whistleblower.view_case", case_id=case_id))


# ---------------------------------------------------------------- periodic client reports

def _period_bounds(year, period):
    first_month, last_month = _PERIOD_MONTHS[period]
    start = datetime(year, first_month, 1)
    end = datetime(year + 1, 1, 1) if last_month == 12 else datetime(year, last_month + 1, 1)
    return start, end


def build_period_summary(engagement, year, period):
    """Counts and breakdowns of the engagement's cases for one reporting
    period - deliberately reference-level only (no reporter details exist,
    and descriptions stay out of what goes to the client)."""
    start, end = _period_bounds(year, period)
    all_cases = engagement.whistleblower_cases.all()
    received = [c for c in all_cases if c.received_at and start <= c.received_at < end]
    closed_in_period = [c for c in all_cases if c.closed_at and start <= c.closed_at < end]
    open_at_end = [
        c for c in all_cases
        if c.received_at and c.received_at < end and not (c.closed_at and c.closed_at < end)
    ]

    def tally(items, key):
        out = {}
        for c in items:
            k = key(c)
            out[k] = out.get(k, 0) + 1
        return out

    days = [c.days_to_close for c in closed_in_period if c.days_to_close is not None]
    return {
        "year": year, "period": period, "period_label": REPORT_PERIOD_LABELS[period],
        "start": start.date(), "end": (end - timedelta(days=1)).date(),
        "received": sorted(received, key=lambda c: c.received_at),
        "received_count": len(received),
        "closed_count": len(closed_in_period),
        "open_at_end": len(open_at_end),
        "substantiated": sum(1 for c in closed_in_period if c.status == "Closed - substantiated"),
        "unsubstantiated": sum(1 for c in closed_in_period if c.status == "Closed - unsubstantiated"),
        "insufficient": sum(1 for c in closed_in_period if c.status == "Closed - insufficient information"),
        "avg_days_to_close": round(sum(days) / len(days), 1) if days else None,
        "by_category": sorted(tally(received, lambda c: c.category_label).items(), key=lambda kv: -kv[1]),
        "by_severity": [(s, tally(received, lambda c: c.severity).get(s, 0)) for s in WB_SEVERITIES],
        "by_channel": sorted(tally(received, lambda c: c.channel or "Other").items(), key=lambda kv: -kv[1]),
        "by_status": [(s, tally(received, lambda c: c.status).get(s, 0)) for s in WB_STATUSES if tally(received, lambda c: c.status).get(s, 0)],
    }


def _report_params():
    year = request.args.get("year", type=int) or date.today().year
    period = request.args.get("period", "")
    if period not in REPORT_PERIOD_LABELS:
        quarter = (date.today().month - 1) // 3 + 1
        period = f"Q{quarter}"
    include_subjects = request.args.get("include_subjects") == "1"
    return year, period, include_subjects


@wb_bp.route("/engagement/<int:engagement_id>/reports")
@login_required
def reports(engagement_id):
    engagement = _load_engagement(engagement_id)
    year, period, include_subjects = _report_params()
    summary = build_period_summary(engagement, year, period)
    return render_template(
        "whistleblower/reports.html", engagement=engagement, service=_get_or_create_service(engagement),
        summary=summary, periods=REPORT_PERIODS, year=year, period=period, include_subjects=include_subjects,
        active_tab="reports",
    )


@wb_bp.route("/engagement/<int:engagement_id>/reports/download")
@login_required
def download_report(engagement_id):
    engagement = _load_engagement(engagement_id)
    year, period, include_subjects = _report_params()
    summary = build_period_summary(engagement, year, period)
    buf = build_report_docx(engagement, _get_or_create_service(engagement), summary, include_subjects)
    safe_client = "".join(ch for ch in engagement.client.name if ch.isalnum() or ch in " _-").strip().replace(" ", "_")
    return send_file(
        buf, as_attachment=True,
        download_name=f"Whistleblower_Report_{safe_client}_{year}_{period}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def build_report_docx(engagement, service, summary, include_subjects=False):
    """The periodic whistle-blower report for the client's audit committee,
    as a Word document with the firm letterhead."""
    import workpapers as wp
    doc = wp._new_document(
        "Anonymous Whistle-blower Services - Periodic Report", engagement,
        subtitle=f"{summary['period_label']} {summary['year']} ({wp._fmt_date(summary['start'])} to {wp._fmt_date(summary['end'])})",
    )
    wp._add_heading(doc, "1. Summary", 1)
    doc.add_paragraph(
        f"During the period, {summary['received_count']} report(s) were received through the whistle-blowing service. "
        f"{summary['closed_count']} case(s) were closed in the period and {summary['open_at_end']} remained open at "
        f"{wp._fmt_date(summary['end'])}."
        + (f" Cases closed in the period took {summary['avg_days_to_close']} day(s) on average to resolve." if summary["avg_days_to_close"] is not None else "")
    )
    t = doc.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Measure", "Number"
    for label, value in (
        ("Reports received", summary["received_count"]), ("Cases closed", summary["closed_count"]),
        ("  - substantiated", summary["substantiated"]), ("  - unsubstantiated", summary["unsubstantiated"]),
        ("  - insufficient information", summary["insufficient"]), ("Open at end of period", summary["open_at_end"]),
    ):
        row = t.add_row().cells
        row[0].text, row[1].text = label, str(value)
    wp._style_table(t)

    def breakdown(title, pairs):
        wp._add_heading(doc, title, 1)
        if not any(n for _, n in pairs):
            doc.add_paragraph("No reports in this period.")
            return
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Category" if "category" in title.lower() else "Item", "Reports"
        for label, n in pairs:
            row = table.add_row().cells
            row[0].text, row[1].text = str(label), str(n)
        wp._style_table(table)

    breakdown("2. Reports by category", summary["by_category"])
    breakdown("3. Reports by severity", summary["by_severity"])
    breakdown("4. Reports by channel", summary["by_channel"])
    breakdown("5. Status of reports received in the period", summary["by_status"])

    wp._add_heading(doc, "6. Case register", 1)
    if summary["received"]:
        headers = ["Reference", "Received", "Category", "Severity", "Status"] + (["Subject"] if include_subjects else [])
        table = doc.add_table(rows=1, cols=len(headers))
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = h
        for c in summary["received"]:
            cells = table.add_row().cells
            values = [c.reference, wp._fmt_date(to_cat(c.received_at).date()), c.category_label, c.severity, c.status]
            if include_subjects:
                values.append(c.subject or "")
            for i, v in enumerate(values):
                cells[i].text = v
        wp._style_table(table)
    else:
        doc.add_paragraph("No reports were received in this period.")

    doc.add_paragraph()
    note = doc.add_paragraph(
        "Reporter identities are not recorded by this service. Case descriptions and investigation notes are held "
        "confidentially by Neverlank Chartered Accountants and are not reproduced in this report."
    )
    note.runs[0].italic = True
    return wp._finish(doc)


# ---------------------------------------------------------------- PUBLIC anonymous pages (no login)

def _service_for_token(token):
    service = WhistleblowerService.query.filter_by(public_token=token).first()
    if not service or not service.portal_enabled or service.engagement.status == "Completed":
        abort(404)
    return service


def _public_context(service):
    return {
        "client_name": service.engagement.client.name, "service": service, "token": service.public_token,
        "categories": WB_CATEGORIES,
    }


@wb_public_bp.route("/<token>", methods=["GET", "POST"])
def submit(token):
    service = _service_for_token(token)
    ctx = _public_context(service)
    if request.method == "POST":
        # Honeypot: real people never see/fill this field.
        if request.form.get("website"):
            return render_template("whistleblower/public_submit.html", error=None, form={}, **ctx), 200
        description = request.form.get("description", "").strip()
        form = {"category": request.form.get("category", ""), "subject": request.form.get("subject", ""),
                "description": description}
        if len(description) < MIN_REPORT_LENGTH:
            return render_template(
                "whistleblower/public_submit.html", form=form, **ctx,
                error=f"Please describe what happened in at least {MIN_REPORT_LENGTH} characters so it can be looked into.",
            )
        if len(description) > MAX_REPORT_LENGTH:
            return render_template(
                "whistleblower/public_submit.html", form=form, **ctx,
                error=f"Please keep the report under {MAX_REPORT_LENGTH:,} characters - you can add more later via your follow-up key.",
            )
        # Nothing about the submitter is recorded: no name, no IP, no user agent.
        case = WhistleblowerCase(
            engagement_id=service.engagement_id, reference=_new_reference(), channel="Web portal",
            category=_clean_choice(form["category"], dict(WB_CATEGORIES), "other"), severity="Medium",
            subject=(form["subject"].strip()[:200] or None), description=description, status="New", source="portal",
        )
        key = _issue_followup_key(case)
        db.session.add(case)
        db.session.commit()
        return render_template("whistleblower/public_submitted.html", reference=case.reference, key=key, **ctx)
    return render_template("whistleblower/public_submit.html", error=None, form={}, **ctx)


def _verified_case(service, reference, key):
    case = WhistleblowerCase.query.filter_by(
        reference=(reference or "").strip().upper(), engagement_id=service.engagement_id,
    ).first()
    if case and case.followup_key_hash and check_password_hash(case.followup_key_hash, (key or "").strip()):
        return case
    return None


@wb_public_bp.route("/<token>/status", methods=["GET", "POST"])
def status(token):
    service = _service_for_token(token)
    ctx = _public_context(service)
    if request.method == "GET":
        return render_template("whistleblower/public_status.html", case=None, error=None, reference="", key="", **ctx)
    reference, key = request.form.get("reference", ""), request.form.get("key", "")
    case = _verified_case(service, reference, key)
    if not case:
        return render_template(
            "whistleblower/public_status.html", case=None, reference=reference, key="", **ctx,
            error="That reference and key don't match a report. Check both and try again.",
        )
    sent = False
    message = request.form.get("message", "").strip()
    if message:
        db.session.add(WhistleblowerCaseNote(case_id=case.id, kind="from_reporter", body=message[:MAX_REPORT_LENGTH]))
        db.session.commit()
        sent = True
    visible = [n for n in case.notes if n.kind in ("to_reporter", "from_reporter")]
    return render_template(
        "whistleblower/public_status.html", case=case, error=None, reference=reference, key=key,
        messages=visible, sent=sent, **ctx,
    )
