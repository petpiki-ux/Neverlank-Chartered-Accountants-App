"""Native Invoicing - a client invoice with manual line items (no logged-time
billing in this first version - see the README shipped with this update).

An invoice is always billed to a Client; the link to a specific Engagement
is optional. Access follows the same confidentiality convention as
everywhere else in the app: if an invoice IS linked to an engagement, only
that engagement's Partner/Manager/Team (or Admin) can see it - otherwise
(no engagement link) any logged-in team member can see it, same as the
Clients directory itself.
"""
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import (
    Client, Engagement, Invoice, InvoiceLineItem,
    INVOICE_STATUSES, INVOICE_CURRENCIES, user_can_access_engagement,
)

invoicing_bp = Blueprint("invoicing", __name__, url_prefix="/invoices")


def _ensure_invoice_access(invoice):
    if invoice.engagement_id and not user_can_access_engagement(current_user, invoice.engagement):
        abort(403)


def _next_invoice_number():
    year = date.today().year
    prefix = f"INV-{year}-"
    count = Invoice.query.filter(Invoice.invoice_number.like(f"{prefix}%")).count()
    return f"{prefix}{count + 1:04d}"


@invoicing_bp.route("/")
@login_required
def list_invoices():
    status_filter = request.args.get("status", "")
    client_filter = request.args.get("client_id", "")
    query = Invoice.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    if client_filter:
        query = query.filter_by(client_id=int(client_filter))
    all_invoices = query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).all()
    # Same confidentiality rule as everywhere else: an invoice linked to an
    # engagement is hidden from anyone not on that engagement (Admin sees
    # everything). An invoice with no engagement link is visible to all.
    visible = [
        inv for inv in all_invoices
        if not inv.engagement_id or user_can_access_engagement(current_user, inv.engagement)
    ]
    clients = Client.query.order_by(Client.name).all()
    return render_template(
        "invoicing/list.html", invoices=visible, clients=clients,
        statuses=INVOICE_STATUSES, status_filter=status_filter, client_filter=client_filter,
    )


def _visible_engagements_for_invoicing():
    """Every engagement the current user is allowed to link an invoice to -
    same confidentiality rule as the rest of the app (Admin sees all)."""
    from engagements import _visible_to_current_user  # reuse rather than duplicate the rule
    return _visible_to_current_user(
        Engagement.query.order_by(Engagement.client_id, Engagement.title).all()
    )


@invoicing_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_invoice():
    clients = Client.query.order_by(Client.name).all()
    engagements = _visible_engagements_for_invoicing()

    if request.method == "POST":
        client_id = request.form.get("client_id")
        if not client_id:
            flash("Please select a client.", "danger")
            return render_template("invoicing/form.html", clients=clients, engagements=engagements, currencies=INVOICE_CURRENCIES)

        client = Client.query.get_or_404(int(client_id))
        engagement_id = request.form.get("engagement_id") or None
        if engagement_id:
            engagement = Engagement.query.get_or_404(int(engagement_id))
            if engagement.client_id != client.id:
                flash("That engagement doesn't belong to the selected client.", "danger")
                return render_template("invoicing/form.html", clients=clients, engagements=engagements, currencies=INVOICE_CURRENCIES)
            if not user_can_access_engagement(current_user, engagement):
                abort(403)

        try:
            vat_pct = float(request.form.get("vat_pct", 15.0))
        except ValueError:
            vat_pct = 15.0

        issue_date = request.form.get("issue_date")
        due_date = request.form.get("due_date")

        invoice = Invoice(
            invoice_number=_next_invoice_number(),
            client_id=client.id,
            engagement_id=int(engagement_id) if engagement_id else None,
            currency=request.form.get("currency", "USD"),
            vat_pct=max(0.0, vat_pct),
            issue_date=datetime.strptime(issue_date, "%Y-%m-%d").date() if issue_date else date.today(),
            due_date=datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None,
            bill_to=request.form.get("bill_to", "").strip() or client.name,
            notes=request.form.get("notes", "").strip(),
            created_by_id=current_user.id,
        )
        db.session.add(invoice)
        db.session.commit()
        flash(f"Invoice {invoice.invoice_number} created - add line items below.", "success")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice.id))

    preselect_client_id = request.args.get("client_id", type=int)
    preselect_engagement_id = request.args.get("engagement_id", type=int)
    return render_template(
        "invoicing/form.html", clients=clients, engagements=engagements, currencies=INVOICE_CURRENCIES,
        preselect_client_id=preselect_client_id, preselect_engagement_id=preselect_engagement_id,
        today=date.today().isoformat(),
    )


@invoicing_bp.route("/<int:invoice_id>")
@login_required
def view_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    return render_template("invoicing/detail.html", invoice=invoice, statuses=INVOICE_STATUSES)


@invoicing_bp.route("/<int:invoice_id>/print")
@login_required
def print_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    return render_template("invoicing/print.html", invoice=invoice)


@invoicing_bp.route("/<int:invoice_id>/lines/add", methods=["POST"])
@login_required
def add_invoice_line(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please describe the line item.", "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    try:
        quantity = float(request.form.get("quantity", 1.0))
    except ValueError:
        quantity = 1.0
    try:
        unit_price = float(request.form.get("unit_price", 0.0))
    except ValueError:
        unit_price = 0.0
    db.session.add(InvoiceLineItem(
        invoice_id=invoice.id, description=description, quantity=quantity, unit_price=unit_price,
    ))
    db.session.commit()
    flash("Line item added.", "success")
    return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))


@invoicing_bp.route("/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_invoice_line(line_id):
    line = InvoiceLineItem.query.get_or_404(line_id)
    _ensure_invoice_access(line.invoice)
    line.description = request.form.get("description", line.description).strip() or line.description
    try:
        line.quantity = float(request.form.get("quantity", line.quantity))
    except ValueError:
        pass
    try:
        line.unit_price = float(request.form.get("unit_price", line.unit_price))
    except ValueError:
        pass
    db.session.commit()
    flash("Line item updated.", "success")
    return redirect(url_for("invoicing.view_invoice", invoice_id=line.invoice_id))


@invoicing_bp.route("/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_invoice_line(line_id):
    line = InvoiceLineItem.query.get_or_404(line_id)
    _ensure_invoice_access(line.invoice)
    invoice_id = line.invoice_id
    db.session.delete(line)
    db.session.commit()
    flash("Line item removed.", "info")
    return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))


@invoicing_bp.route("/<int:invoice_id>/status", methods=["POST"])
@login_required
def update_invoice_status(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    status = request.form.get("status", "").strip()
    if status not in INVOICE_STATUSES:
        flash("Unrecognised status.", "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    invoice.status = status
    invoice.paid_at = (invoice.paid_at or datetime.utcnow()) if status == "Paid" else None
    db.session.commit()
    flash(f"Invoice marked as {status}.", "success")
    return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))


@invoicing_bp.route("/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
def edit_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    clients = Client.query.order_by(Client.name).all()

    if request.method == "POST":
        try:
            vat_pct = float(request.form.get("vat_pct", invoice.vat_pct))
        except ValueError:
            vat_pct = invoice.vat_pct
        invoice.currency = request.form.get("currency", invoice.currency)
        invoice.vat_pct = max(0.0, vat_pct)
        issue_date = request.form.get("issue_date")
        due_date = request.form.get("due_date")
        invoice.issue_date = datetime.strptime(issue_date, "%Y-%m-%d").date() if issue_date else invoice.issue_date
        invoice.due_date = datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None
        invoice.bill_to = request.form.get("bill_to", invoice.bill_to or "").strip()
        invoice.notes = request.form.get("notes", invoice.notes or "").strip()
        db.session.commit()
        flash("Invoice details updated.", "success")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice.id))

    return render_template("invoicing/edit.html", invoice=invoice, currencies=INVOICE_CURRENCIES)


@invoicing_bp.route("/<int:invoice_id>/delete", methods=["POST"])
@login_required
def delete_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    if invoice.status != "Draft" and current_user.role not in ("partner", "admin"):
        flash("Only a Partner or Admin can delete an invoice that's already been sent.", "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    db.session.delete(invoice)
    db.session.commit()
    flash("Invoice deleted.", "info")
    return redirect(url_for("invoicing.list_invoices"))
