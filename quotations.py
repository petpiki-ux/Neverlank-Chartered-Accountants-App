"""Quotations - a fee quotation for a client that converts into an Invoice.

Lifecycle: Draft > Sent > Accepted / Declined / Expired > Invoiced.
A quotation has the same shape as an invoice (client, optional engagement,
currency, VAT %, manual line items), so "Convert to invoice" copies it into
a new Draft invoice, marks the quotation Invoiced, and links the two both
ways (Quotation.invoice <-> Invoice.quotation). A quotation converts once;
if the resulting invoice is deleted the quotation returns to Accepted.

Access follows the same confidentiality rule as invoices: a quotation
linked to an engagement is visible only to that engagement's Partner,
Manager and Team (or Admin); one with no engagement link is visible to all.
"""
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from invoicing import FIRM_LETTERHEAD, next_sequence_number, _next_invoice_number, _visible_engagements_for_invoicing
from models import (
    Client, Engagement, Invoice, InvoiceLineItem, Quotation, QuotationLineItem,
    INVOICE_CURRENCIES, QUOTATION_STATUSES, QUOTATION_MANUAL_STATUSES,
    QUOTATION_DEFAULT_VALIDITY_DAYS, QUOTATION_DEFAULT_TERMS,
    user_can_access_engagement,
)

quotations_bp = Blueprint("quotations", __name__, url_prefix="/quotations")


def _ensure_quotation_access(quotation):
    if quotation.engagement_id and not user_can_access_engagement(current_user, quotation.engagement):
        abort(403)


def _next_quote_number():
    return next_sequence_number(Quotation.quote_number, f"QUO-{date.today().year}-")


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


def _redirect_view(quotation_id):
    return redirect(url_for("quotations.view_quotation", quotation_id=quotation_id))


def _locked(quotation):
    """An Invoiced quotation is a closed record - it is what the invoice was
    raised from - so its details and lines can no longer be changed."""
    if quotation.status == "Invoiced":
        flash("This quotation has already been converted to an invoice, so it can no longer be edited.", "danger")
        return True
    return False


def _can_convert(quotation):
    if quotation.status == "Invoiced":
        return False
    if quotation.status == "Accepted":
        return True
    # before the client accepts, only a Partner or Admin may invoice it
    return current_user.role in ("partner", "admin")


# ------------------------------------------------------------------ list

@quotations_bp.route("/")
@login_required
def list_quotations():
    status_filter = request.args.get("status", "")
    client_filter = request.args.get("client_id", "")
    query = Quotation.query
    if client_filter:
        query = query.filter_by(client_id=int(client_filter))
    everything = query.order_by(Quotation.issue_date.desc(), Quotation.id.desc()).all()
    visible = [q for q in everything if not q.engagement_id or user_can_access_engagement(current_user, q.engagement)]

    # summary cards: counts and money by currency (ZWG and USD never added together)
    def money(quotes):
        out = {}
        for q in quotes:
            out[q.currency] = out.get(q.currency, 0.0) + q.total
        return out

    waiting = [q for q in visible if q.display_status == "Sent"]
    ready = [q for q in visible if q.display_status == "Accepted"]
    invoiced = [q for q in visible if q.display_status == "Invoiced"]
    summary = {
        "waiting": (len(waiting), money(waiting)),
        "ready": (len(ready), money(ready)),
        "invoiced": (len(invoiced), money(invoiced)),
    }
    if status_filter:
        visible = [q for q in visible if q.display_status == status_filter]
    clients = Client.query.order_by(Client.name).all()
    return render_template(
        "quotations/list.html", quotations=visible, clients=clients, statuses=QUOTATION_STATUSES,
        status_filter=status_filter, client_filter=client_filter, summary=summary,
    )


# ------------------------------------------------------------------ create

@quotations_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_quotation():
    clients = Client.query.order_by(Client.name).all()
    engagements = _visible_engagements_for_invoicing()
    ctx = dict(clients=clients, engagements=engagements, currencies=INVOICE_CURRENCIES,
               default_terms=QUOTATION_DEFAULT_TERMS)

    if request.method == "POST":
        client_id = request.form.get("client_id")
        if not client_id:
            flash("Please select a client.", "danger")
            return render_template("quotations/form.html", today=date.today().isoformat(),
                                   valid_until=(date.today() + timedelta(days=QUOTATION_DEFAULT_VALIDITY_DAYS)).isoformat(), **ctx)
        client = Client.query.get_or_404(int(client_id))
        engagement_id = request.form.get("engagement_id") or None
        if engagement_id:
            engagement = Engagement.query.get_or_404(int(engagement_id))
            if engagement.client_id != client.id:
                flash("That engagement doesn't belong to the selected client.", "danger")
                return render_template("quotations/form.html", today=date.today().isoformat(),
                                       valid_until=(date.today() + timedelta(days=QUOTATION_DEFAULT_VALIDITY_DAYS)).isoformat(), **ctx)
            if not user_can_access_engagement(current_user, engagement):
                abort(403)
        currency = request.form.get("currency", "USD")
        if currency not in INVOICE_CURRENCIES:
            currency = "USD"
        issue_date = _parse_date(request.form.get("issue_date")) or date.today()
        quotation = Quotation(
            quote_number=_next_quote_number(),
            client_id=client.id,
            engagement_id=int(engagement_id) if engagement_id else None,
            subject=request.form.get("subject", "").strip() or None,
            currency=currency,
            vat_pct=max(0.0, _parse_float(request.form.get("vat_pct"), 15.0)),
            issue_date=issue_date,
            valid_until=_parse_date(request.form.get("valid_until")) or (issue_date + timedelta(days=QUOTATION_DEFAULT_VALIDITY_DAYS)),
            bill_to=request.form.get("bill_to", "").strip() or client.name,
            notes=request.form.get("notes", "").strip() or None,
            terms=request.form.get("terms", "").strip() or None,
            created_by_id=current_user.id,
        )
        db.session.add(quotation)
        db.session.commit()
        flash(f"Quotation {quotation.quote_number} created - add the line items below.", "success")
        return _redirect_view(quotation.id)

    return render_template(
        "quotations/form.html", today=date.today().isoformat(),
        valid_until=(date.today() + timedelta(days=QUOTATION_DEFAULT_VALIDITY_DAYS)).isoformat(),
        preselect_client_id=request.args.get("client_id", type=int),
        preselect_engagement_id=request.args.get("engagement_id", type=int), **ctx,
    )


# ------------------------------------------------------------------ view / print

@quotations_bp.route("/<int:quotation_id>")
@login_required
def view_quotation(quotation_id):
    quotation = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(quotation)
    return render_template(
        "quotations/detail.html", quotation=quotation, statuses=QUOTATION_MANUAL_STATUSES,
        can_convert=_can_convert(quotation),
        default_due=(date.today() + timedelta(days=30)).isoformat(),
    )


@quotations_bp.route("/<int:quotation_id>/print")
@login_required
def print_quotation(quotation_id):
    quotation = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(quotation)
    return render_template("quotations/print.html", quotation=quotation, doc=quotation, firm=FIRM_LETTERHEAD)


# ------------------------------------------------------------------ edit

@quotations_bp.route("/<int:quotation_id>/edit", methods=["GET", "POST"])
@login_required
def edit_quotation(quotation_id):
    quotation = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(quotation)
    if _locked(quotation):
        return _redirect_view(quotation_id)
    if request.method == "POST":
        currency = request.form.get("currency", quotation.currency)
        quotation.currency = currency if currency in INVOICE_CURRENCIES else quotation.currency
        quotation.vat_pct = max(0.0, _parse_float(request.form.get("vat_pct"), quotation.vat_pct))
        quotation.subject = request.form.get("subject", "").strip() or None
        quotation.issue_date = _parse_date(request.form.get("issue_date")) or quotation.issue_date
        quotation.valid_until = _parse_date(request.form.get("valid_until"))
        quotation.bill_to = request.form.get("bill_to", "").strip() or quotation.client.name
        quotation.notes = request.form.get("notes", "").strip() or None
        quotation.terms = request.form.get("terms", "").strip() or None
        db.session.commit()
        flash("Quotation details updated.", "success")
        return _redirect_view(quotation_id)
    return render_template("quotations/edit.html", quotation=quotation, currencies=INVOICE_CURRENCIES)


# ------------------------------------------------------------------ lines

@quotations_bp.route("/<int:quotation_id>/lines/add", methods=["POST"])
@login_required
def add_quotation_line(quotation_id):
    quotation = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(quotation)
    if _locked(quotation):
        return _redirect_view(quotation_id)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please describe the line item.", "danger")
        return _redirect_view(quotation_id)
    db.session.add(QuotationLineItem(
        quotation_id=quotation.id, description=description,
        quantity=_parse_float(request.form.get("quantity"), 1.0),
        unit_price=_parse_float(request.form.get("unit_price"), 0.0),
    ))
    db.session.commit()
    flash("Line item added.", "success")
    return _redirect_view(quotation_id)


@quotations_bp.route("/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_quotation_line(line_id):
    line = QuotationLineItem.query.get_or_404(line_id)
    _ensure_quotation_access(line.quotation)
    if _locked(line.quotation):
        return _redirect_view(line.quotation_id)
    line.description = request.form.get("description", line.description).strip() or line.description
    line.quantity = _parse_float(request.form.get("quantity"), line.quantity)
    line.unit_price = _parse_float(request.form.get("unit_price"), line.unit_price)
    db.session.commit()
    flash("Line item updated.", "success")
    return _redirect_view(line.quotation_id)


@quotations_bp.route("/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_quotation_line(line_id):
    line = QuotationLineItem.query.get_or_404(line_id)
    _ensure_quotation_access(line.quotation)
    quotation_id = line.quotation_id
    if _locked(line.quotation):
        return _redirect_view(quotation_id)
    db.session.delete(line)
    db.session.commit()
    flash("Line item removed.", "info")
    return _redirect_view(quotation_id)


# ------------------------------------------------------------------ status

@quotations_bp.route("/<int:quotation_id>/status", methods=["POST"])
@login_required
def update_quotation_status(quotation_id):
    quotation = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(quotation)
    if _locked(quotation):
        return _redirect_view(quotation_id)
    status = request.form.get("status", "").strip()
    if status not in QUOTATION_MANUAL_STATUSES:
        flash("Unrecognised status.", "danger")
        return _redirect_view(quotation_id)
    quotation.status = status
    quotation.accepted_at = (quotation.accepted_at or datetime.utcnow()) if status == "Accepted" else None
    db.session.commit()
    flash(f"Quotation marked as {status}.", "success")
    return _redirect_view(quotation_id)


# ------------------------------------------------------------------ convert

@quotations_bp.route("/<int:quotation_id>/convert", methods=["POST"])
@login_required
def convert_to_invoice(quotation_id):
    """Copy the quotation into a new Draft invoice and link the two."""
    quotation = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(quotation)
    if quotation.status == "Invoiced" and quotation.invoice:
        flash("This quotation was already converted - here is its invoice.", "info")
        return redirect(url_for("invoicing.view_invoice", invoice_id=quotation.invoice_id))
    if not _can_convert(quotation):
        flash("A quotation can be converted to an invoice once the client has accepted it (mark it Accepted first). A Partner or Admin can also invoice it earlier.", "danger")
        return _redirect_view(quotation_id)
    if not quotation.lines:
        flash("Add at least one line item before converting this quotation to an invoice.", "danger")
        return _redirect_view(quotation_id)

    issue_date = date.today()
    due_date = _parse_date(request.form.get("due_date")) or (issue_date + timedelta(days=30))
    note_bits = [f"As per accepted quotation {quotation.quote_number}" + (f" - {quotation.subject}" if quotation.subject else "") + "."]
    invoice = Invoice(
        invoice_number=_next_invoice_number(),
        client_id=quotation.client_id,
        engagement_id=quotation.engagement_id,
        currency=quotation.currency,
        vat_pct=quotation.vat_pct,
        status="Draft",
        issue_date=issue_date,
        due_date=due_date,
        bill_to=quotation.bill_to or quotation.client.name,
        notes="\n".join(note_bits),
        created_by_id=current_user.id,
    )
    db.session.add(invoice)
    db.session.flush()
    for line in quotation.lines:
        db.session.add(InvoiceLineItem(
            invoice_id=invoice.id, description=line.description,
            quantity=line.quantity, unit_price=line.unit_price,
        ))
    quotation.invoice_id = invoice.id
    quotation.status = "Invoiced"
    quotation.accepted_at = quotation.accepted_at or datetime.utcnow()
    db.session.commit()
    flash(f"Quotation {quotation.quote_number} converted to invoice {invoice.invoice_number} (Draft) - review it, then mark it Sent.", "success")
    return redirect(url_for("invoicing.view_invoice", invoice_id=invoice.id))


# ------------------------------------------------------------------ duplicate / delete

@quotations_bp.route("/<int:quotation_id>/duplicate", methods=["POST"])
@login_required
def duplicate_quotation(quotation_id):
    """A fresh Draft copy - the way to re-issue an expired or declined quote."""
    original = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(original)
    today = date.today()
    copy = Quotation(
        quote_number=_next_quote_number(), client_id=original.client_id, engagement_id=original.engagement_id,
        subject=original.subject, currency=original.currency, vat_pct=original.vat_pct, status="Draft",
        issue_date=today, valid_until=today + timedelta(days=QUOTATION_DEFAULT_VALIDITY_DAYS),
        bill_to=original.bill_to, notes=original.notes, terms=original.terms, created_by_id=current_user.id,
    )
    db.session.add(copy)
    db.session.flush()
    for line in original.lines:
        db.session.add(QuotationLineItem(quotation_id=copy.id, description=line.description, quantity=line.quantity, unit_price=line.unit_price))
    db.session.commit()
    flash(f"Copied to {copy.quote_number} (Draft).", "success")
    return _redirect_view(copy.id)


@quotations_bp.route("/<int:quotation_id>/delete", methods=["POST"])
@login_required
def delete_quotation(quotation_id):
    quotation = Quotation.query.get_or_404(quotation_id)
    _ensure_quotation_access(quotation)
    if quotation.status == "Invoiced":
        flash("This quotation has been converted to an invoice, so it is kept as the record behind that invoice and can't be deleted.", "danger")
        return _redirect_view(quotation_id)
    if quotation.status != "Draft" and current_user.role not in ("partner", "admin"):
        flash("Only a Partner or Admin can delete a quotation that's already been sent.", "danger")
        return _redirect_view(quotation_id)
    db.session.delete(quotation)
    db.session.commit()
    flash("Quotation deleted.", "info")
    return redirect(url_for("quotations.list_quotations"))
