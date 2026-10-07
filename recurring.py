"""Recurring invoices - schedules that raise the same invoice every month,
quarter, half-year or year.

When an occurrence falls due the app creates a *Draft* invoice from the
schedule and flags it for review; nothing is treated as issued until a
person marks it Sent. There is no background scheduler on this hosting, so
due occurrences are generated (a) the first time anyone uses the app each
day after the server starts (app.py's before_request hook), and (b) on
demand with the "Generate due invoices now" button. An occurrence is always
dated its scheduled date, so a late check just raises it late, never wrongly
dated; and a unique (schedule, occurrence) key on the invoice table means it
can never be raised twice.
"""
import calendar
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from invoicing import _next_invoice_number, _visible_engagements_for_invoicing
from models import (
    Client, Engagement, Invoice, InvoiceLineItem, RecurringInvoice, RecurringInvoiceLine,
    INVOICE_CURRENCIES, RECURRING_FREQUENCIES, RECURRING_STATUSES,
    user_can_access_engagement,
)

recurring_bp = Blueprint("recurring", __name__, url_prefix="/invoices/recurring")

MAX_CATCH_UP_PER_RUN = 12  # never raise more than a year of monthly invoices for one schedule in one go


# ------------------------------------------------------------------ date maths

def add_months(d, months):
    """d moved forward by whole months, clamped to the end of a shorter month."""
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def occurrence_date(schedule, index):
    """Date of the schedule's index-th invoice (0 = the first)."""
    return add_months(schedule.start_date, index * schedule.step_months)


def next_run_date(schedule):
    """When the next invoice is due, or None if the schedule is finished."""
    if schedule.status == "Completed":
        return None
    if schedule.max_count and schedule.generated_count >= schedule.max_count:
        return None
    nxt = occurrence_date(schedule, schedule.next_index)
    if schedule.end_date and nxt > schedule.end_date:
        return None
    return nxt


def upcoming_dates(schedule, how_many=4):
    out, idx = [], schedule.next_index
    raised = schedule.generated_count
    while len(out) < how_many:
        if schedule.max_count and raised >= schedule.max_count:
            break
        d = occurrence_date(schedule, idx)
        if schedule.end_date and d > schedule.end_date:
            break
        out.append(d)
        idx += 1
        raised += 1
    return out


def _finish_if_done(schedule):
    if next_run_date(schedule) is None and schedule.status == "Active":
        schedule.status = "Completed"


# ------------------------------------------------------------------ generation

def _raise_invoice(schedule, index):
    """Create the Draft invoice for occurrence `index` and advance the schedule."""
    issue = occurrence_date(schedule, index)
    period_end = occurrence_date(schedule, index + 1) - timedelta(days=1)
    notes = (schedule.notes or "").strip()
    period = f"Billing period: {issue.strftime('%d %b %Y')} to {period_end.strftime('%d %b %Y')} ({schedule.frequency})."
    invoice = Invoice(
        invoice_number=_next_invoice_number(),
        client_id=schedule.client_id,
        engagement_id=schedule.engagement_id,
        currency=schedule.currency,
        vat_pct=schedule.vat_pct,
        status="Draft",
        issue_date=issue,
        due_date=issue + timedelta(days=schedule.payment_terms_days or 0),
        bill_to=schedule.bill_to or schedule.client.name,
        notes=(notes + "\n" if notes else "") + period,
        created_by_id=schedule.created_by_id,
        recurring_invoice_id=schedule.id,
        recurrence_index=index,
    )
    db.session.add(invoice)
    db.session.flush()
    for line in schedule.lines:
        db.session.add(InvoiceLineItem(invoice_id=invoice.id, description=line.description, quantity=line.quantity, unit_price=line.unit_price))
    schedule.next_index = index + 1
    schedule.generated_count = (schedule.generated_count or 0) + 1
    schedule.last_generated_at = datetime.utcnow()
    _finish_if_done(schedule)
    return invoice


def generate_for_schedule(schedule, today=None, force_next=False):
    """Raise every occurrence of one schedule that is due (or, with
    force_next, the next one even if it isn't due yet). Returns the invoices
    created. A schedule with no lines is skipped, since it would raise empty
    invoices."""
    today = today or date.today()
    created = []
    if schedule.status != "Active" or not schedule.lines:
        return created
    while len(created) < MAX_CATCH_UP_PER_RUN:
        due = next_run_date(schedule)
        if due is None:
            break
        if due > today and not (force_next and not created):
            break
        index = schedule.next_index
        try:
            invoice = _raise_invoice(schedule, index)
            db.session.commit()  # one occurrence per transaction
            created.append(invoice)
        except IntegrityError:
            # another server instance raised this occurrence a moment ago
            db.session.rollback()
            db.session.refresh(schedule)
            break
    return created


def generate_due_invoices(today=None):
    """Raise all due invoices for every Active schedule; returns how many."""
    today = today or date.today()
    total = 0
    for schedule in RecurringInvoice.query.filter_by(status="Active").all():
        total += len(generate_for_schedule(schedule, today))
    db.session.commit()
    return total


_last_checked = {"day": None}


def daily_check(app):
    """Run generate_due_invoices at most once per calendar day per process
    (cheap enough to call from every request). Never lets a failure here
    break the page being loaded."""
    today = date.today()
    if _last_checked["day"] == today:
        return
    _last_checked["day"] = today
    try:
        n = generate_due_invoices(today)
        if n:
            app.logger.info("Recurring invoices: raised %s draft invoice(s)", n)
    except Exception:  # pragma: no cover - defensive
        db.session.rollback()
        app.logger.exception("Recurring invoice generation failed")
        _last_checked["day"] = None  # try again on the next request


# ------------------------------------------------------------------ helpers

def _ensure_access(schedule):
    if schedule.engagement_id and not user_can_access_engagement(current_user, schedule.engagement):
        abort(403)


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


def _parse_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_int(value, default=None):
    try:
        return int(value) if str(value).strip() else default
    except (TypeError, ValueError):
        return default


def _view(schedule_id):
    return redirect(url_for("recurring.view_schedule", schedule_id=schedule_id))


def _first_index_on_or_after(schedule, day):
    idx = 0
    while occurrence_date(schedule, idx) < day:
        idx += 1
    return idx


# ------------------------------------------------------------------ pages

@recurring_bp.route("/")
@login_required
def list_schedules():
    schedules = RecurringInvoice.query.order_by(RecurringInvoice.status, RecurringInvoice.id.desc()).all()
    visible = [s for s in schedules if not s.engagement_id or user_can_access_engagement(current_user, s.engagement)]
    rows = [(s, next_run_date(s) if s.status == "Active" else None) for s in visible]
    rows.sort(key=lambda r: (r[0].status != "Active", r[1] is None, r[1] or date.max))
    return render_template("recurring/list.html", rows=rows, today=date.today())


@recurring_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_schedule():
    clients = Client.query.order_by(Client.name).all()
    engagements = _visible_engagements_for_invoicing()
    ctx = dict(clients=clients, engagements=engagements, currencies=INVOICE_CURRENCIES,
               frequencies=list(RECURRING_FREQUENCIES), today=date.today().isoformat())
    if request.method == "POST":
        client = Client.query.get(_parse_int(request.form.get("client_id"), 0) or 0)
        name = request.form.get("name", "").strip()
        start = _parse_date(request.form.get("start_date"))
        frequency = request.form.get("frequency")
        if not client or not name or not start or frequency not in RECURRING_FREQUENCIES:
            flash("Please choose a client, give the schedule a name, pick a frequency and enter the first invoice date.", "danger")
            return render_template("recurring/form.html", **ctx)
        engagement_id = _parse_int(request.form.get("engagement_id"))
        if engagement_id:
            engagement = Engagement.query.get_or_404(engagement_id)
            if engagement.client_id != client.id:
                flash("That engagement doesn't belong to the selected client.", "danger")
                return render_template("recurring/form.html", **ctx)
            if not user_can_access_engagement(current_user, engagement):
                abort(403)
        currency = request.form.get("currency", "USD")
        schedule = RecurringInvoice(
            name=name, client_id=client.id, engagement_id=engagement_id, frequency=frequency,
            start_date=start, end_date=_parse_date(request.form.get("end_date")),
            max_count=_parse_int(request.form.get("max_count")),
            currency=currency if currency in INVOICE_CURRENCIES else "USD",
            vat_pct=max(0.0, _parse_float(request.form.get("vat_pct"), 15.0)),
            payment_terms_days=max(0, _parse_int(request.form.get("payment_terms_days"), 30)),
            bill_to=request.form.get("bill_to", "").strip() or client.name,
            notes=request.form.get("notes", "").strip() or None,
            created_by_id=current_user.id,
        )
        # A start date in the past: either back-bill every missed period
        # (draft invoices for each) or start from the next one due.
        if start < date.today() and not request.form.get("backbill"):
            schedule.next_index = _first_index_on_or_after(schedule, date.today())
        db.session.add(schedule)
        db.session.commit()
        flash("Recurring schedule created - add the invoice lines below. Invoices are raised as Drafts for you to review.", "success")
        return _view(schedule.id)
    return render_template(
        "recurring/form.html", preselect_client_id=request.args.get("client_id", type=int),
        preselect_engagement_id=request.args.get("engagement_id", type=int), **ctx,
    )


@recurring_bp.route("/from-invoice/<int:invoice_id>", methods=["POST"])
@login_required
def from_invoice(invoice_id):
    """Turn an existing invoice into a recurring schedule: same client,
    engagement, currency, VAT %, bill-to and lines; the first *new* invoice
    is one period after the original."""
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.engagement_id and not user_can_access_engagement(current_user, invoice.engagement):
        abort(403)
    frequency = request.form.get("frequency")
    if frequency not in RECURRING_FREQUENCIES:
        flash("Please choose how often this invoice should repeat.", "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    if not invoice.lines:
        flash("Add at least one line item to this invoice before making it recurring.", "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    base = invoice.issue_date or date.today()
    schedule = RecurringInvoice(
        name=f"{invoice.client.name} - {frequency} (from {invoice.invoice_number})",
        client_id=invoice.client_id, engagement_id=invoice.engagement_id, frequency=frequency,
        start_date=base, next_index=1,  # occurrence 0 is the invoice that already exists
        currency=invoice.currency, vat_pct=invoice.vat_pct, bill_to=invoice.bill_to,
        payment_terms_days=max(0, (invoice.due_date - invoice.issue_date).days) if invoice.due_date and invoice.issue_date else 30,
        notes=None, created_by_id=current_user.id,
    )
    db.session.add(schedule)
    db.session.flush()
    for line in invoice.lines:
        db.session.add(RecurringInvoiceLine(recurring_invoice_id=schedule.id, description=line.description, quantity=line.quantity, unit_price=line.unit_price))
    db.session.commit()
    first = occurrence_date(schedule, 1)
    flash(f"{frequency} schedule created from {invoice.invoice_number}. The next invoice will be raised as a Draft on {first.strftime('%d %b %Y')}.", "success")
    return _view(schedule.id)


@recurring_bp.route("/<int:schedule_id>")
@login_required
def view_schedule(schedule_id):
    schedule = RecurringInvoice.query.get_or_404(schedule_id)
    _ensure_access(schedule)
    return render_template(
        "recurring/detail.html", schedule=schedule, nxt=next_run_date(schedule) if schedule.status == "Active" else None,
        upcoming=upcoming_dates(schedule) if schedule.status != "Completed" else [], today=date.today(),
    )


@recurring_bp.route("/<int:schedule_id>/edit", methods=["GET", "POST"])
@login_required
def edit_schedule(schedule_id):
    schedule = RecurringInvoice.query.get_or_404(schedule_id)
    _ensure_access(schedule)
    started = schedule.generated_count > 0 or schedule.next_index > 0
    if request.method == "POST":
        schedule.name = request.form.get("name", "").strip() or schedule.name
        if not started:
            frequency = request.form.get("frequency")
            if frequency in RECURRING_FREQUENCIES:
                schedule.frequency = frequency
            schedule.start_date = _parse_date(request.form.get("start_date")) or schedule.start_date
        schedule.end_date = _parse_date(request.form.get("end_date"))
        schedule.max_count = _parse_int(request.form.get("max_count"))
        currency = request.form.get("currency", schedule.currency)
        schedule.currency = currency if currency in INVOICE_CURRENCIES else schedule.currency
        schedule.vat_pct = max(0.0, _parse_float(request.form.get("vat_pct"), schedule.vat_pct))
        schedule.payment_terms_days = max(0, _parse_int(request.form.get("payment_terms_days"), schedule.payment_terms_days))
        schedule.bill_to = request.form.get("bill_to", "").strip() or schedule.client.name
        schedule.notes = request.form.get("notes", "").strip() or None
        # a changed end/limit can finish a schedule or bring a finished one back
        if schedule.status == "Completed" and next_run_date_ignoring_status(schedule) is not None:
            schedule.status = "Active"
        _finish_if_done(schedule)
        db.session.commit()
        flash("Schedule updated - changes apply to invoices raised from now on.", "success")
        return _view(schedule_id)
    return render_template("recurring/edit.html", schedule=schedule, started=started,
                           currencies=INVOICE_CURRENCIES, frequencies=list(RECURRING_FREQUENCIES))


def next_run_date_ignoring_status(schedule):
    if schedule.max_count and schedule.generated_count >= schedule.max_count:
        return None
    nxt = occurrence_date(schedule, schedule.next_index)
    if schedule.end_date and nxt > schedule.end_date:
        return None
    return nxt


# ------------------------------------------------------------------ lines

@recurring_bp.route("/<int:schedule_id>/lines/add", methods=["POST"])
@login_required
def add_line(schedule_id):
    schedule = RecurringInvoice.query.get_or_404(schedule_id)
    _ensure_access(schedule)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please describe the line item.", "danger")
        return _view(schedule_id)
    db.session.add(RecurringInvoiceLine(
        recurring_invoice_id=schedule.id, description=description,
        quantity=_parse_float(request.form.get("quantity"), 1.0), unit_price=_parse_float(request.form.get("unit_price"), 0.0)))
    db.session.commit()
    flash("Line item added - it will appear on invoices raised from now on.", "success")
    return _view(schedule_id)


@recurring_bp.route("/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_line(line_id):
    line = RecurringInvoiceLine.query.get_or_404(line_id)
    _ensure_access(line.schedule)
    line.description = request.form.get("description", line.description).strip() or line.description
    line.quantity = _parse_float(request.form.get("quantity"), line.quantity)
    line.unit_price = _parse_float(request.form.get("unit_price"), line.unit_price)
    db.session.commit()
    flash("Line item updated.", "success")
    return _view(line.recurring_invoice_id)


@recurring_bp.route("/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_line(line_id):
    line = RecurringInvoiceLine.query.get_or_404(line_id)
    _ensure_access(line.schedule)
    sid = line.recurring_invoice_id
    db.session.delete(line)
    db.session.commit()
    flash("Line item removed.", "info")
    return _view(sid)


# ------------------------------------------------------------------ actions

@recurring_bp.route("/<int:schedule_id>/pause", methods=["POST"])
@login_required
def pause(schedule_id):
    schedule = RecurringInvoice.query.get_or_404(schedule_id)
    _ensure_access(schedule)
    if schedule.status == "Active":
        schedule.status = "Paused"
        db.session.commit()
        flash("Schedule paused - no invoices will be raised until you resume it.", "info")
    return _view(schedule_id)


@recurring_bp.route("/<int:schedule_id>/resume", methods=["POST"])
@login_required
def resume(schedule_id):
    """Resuming skips any periods that fell due while paused (they were not
    to be billed) and carries on from the next one still to come."""
    schedule = RecurringInvoice.query.get_or_404(schedule_id)
    _ensure_access(schedule)
    if schedule.status != "Paused":
        return _view(schedule_id)
    before = schedule.next_index
    schedule.next_index = max(schedule.next_index, _first_index_on_or_after(schedule, date.today()))
    skipped = schedule.next_index - before
    schedule.status = "Active"
    _finish_if_done(schedule)
    db.session.commit()
    msg = "Schedule resumed."
    if skipped:
        msg += f" {skipped} period(s) that fell due while it was paused were skipped."
    flash(msg, "success")
    return _view(schedule_id)


@recurring_bp.route("/<int:schedule_id>/generate", methods=["POST"])
@login_required
def generate_now(schedule_id):
    """Raise the next invoice right now, even if it isn't due yet."""
    schedule = RecurringInvoice.query.get_or_404(schedule_id)
    _ensure_access(schedule)
    if schedule.status != "Active":
        flash("Only an Active schedule can raise invoices. Resume it first.", "danger")
        return _view(schedule_id)
    if not schedule.lines:
        flash("Add at least one line item first - the schedule has nothing to bill yet.", "danger")
        return _view(schedule_id)
    created = generate_for_schedule(schedule, force_next=True)
    db.session.commit()
    if not created:
        flash("Nothing to raise - the schedule has reached its end date or invoice limit.", "info")
        return _view(schedule_id)
    inv = created[0]
    flash(f"Draft invoice {inv.invoice_number} raised for {inv.issue_date.strftime('%d %b %Y')} - review it, then mark it Sent.", "success")
    return redirect(url_for("invoicing.view_invoice", invoice_id=inv.id))


@recurring_bp.route("/run-due", methods=["POST"])
@login_required
def run_due():
    n = generate_due_invoices()
    if n:
        flash(f"{n} draft invoice(s) raised from recurring schedules - review them below.", "success")
        return redirect(url_for("invoicing.list_invoices", status="Draft", recurring="1"))
    flash("No recurring invoices are due right now.", "info")
    return redirect(url_for("recurring.list_schedules"))


@recurring_bp.route("/<int:schedule_id>/delete", methods=["POST"])
@login_required
def delete_schedule(schedule_id):
    schedule = RecurringInvoice.query.get_or_404(schedule_id)
    _ensure_access(schedule)
    if schedule.generated_count and current_user.role not in ("partner", "admin"):
        flash("Only a Partner or Admin can delete a schedule that has already raised invoices. You can pause it instead.", "danger")
        return _view(schedule_id)
    db.session.delete(schedule)
    db.session.commit()
    flash("Recurring schedule deleted. Invoices it already raised were kept.", "info")
    return redirect(url_for("recurring.list_schedules"))
