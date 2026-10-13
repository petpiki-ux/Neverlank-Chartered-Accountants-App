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
from models import visible_clients, user_can_access_client
from models import (
    Client, Engagement, Invoice, InvoiceLineItem, EngagementTask, ready_to_bill_tasks,
    INVOICE_STATUSES, INVOICE_CURRENCIES, user_can_access_engagement,
)
from timeutil import to_cat

invoicing_bp = Blueprint("invoicing", __name__, url_prefix="/invoices")


def _invoice_visible(invoice):
    """Visible to the current user: they work on the invoice's client, and (if it
    is linked to an engagement) they are on that engagement's team."""
    if not user_can_access_client(current_user, invoice.client_id):
        return False
    return not invoice.engagement_id or user_can_access_engagement(current_user, invoice.engagement)


def _ensure_invoice_access(invoice):
    if not user_can_access_client(current_user, invoice.client_id):
        abort(403, description="You are not working on this invoice's client, so you cannot open it.")
    if invoice.engagement_id and not user_can_access_engagement(current_user, invoice.engagement):
        abort(403)


def next_sequence_number(column, prefix):
    """prefix + the next 4-digit number after the highest one already used.
    (Counting rows would hand out a number twice once any document in the
    series had been deleted.)"""
    top = 0
    for (value,) in db.session.query(column).filter(column.like(f"{prefix}%")).all():
        try:
            top = max(top, int(value[len(prefix):]))
        except ValueError:
            pass
    return f"{prefix}{top + 1:04d}"


def _next_invoice_number():
    return next_sequence_number(Invoice.invoice_number, f"INV-{date.today().year}-")


# Printed on every quotation and invoice letterhead. Fill in any of the
# blank ones (email, phone, VAT / TIN number, bank details) and they appear
# on the printed documents automatically; blank ones are simply left out.
FIRM_LETTERHEAD = {
    "name": "Neverlank Chartered Accountants",
    "tagline": "Chartered Accountants",
    "strapline": "World Class Financial Services",
    "address": "2nd Floor, Michael House, 62 Nelson Mandela Avenue, Harare, Zimbabwe",
    "web": "www.neverlank.co.zw",
    "email": "",
    "phone": "",
    "vat_number": "",
    "tin": "",
    "bank_details": "",
}


@invoicing_bp.route("/")
@login_required
def list_invoices():
    status_filter = request.args.get("status", "")
    client_filter = request.args.get("client_id", "")
    recurring_only = request.args.get("recurring") == "1"
    query = Invoice.query
    if recurring_only:
        query = query.filter(Invoice.recurring_invoice_id.isnot(None))
    if status_filter:
        query = query.filter_by(status=status_filter)
    if client_filter:
        query = query.filter_by(client_id=int(client_filter))
    all_invoices = query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).all()
    # Same confidentiality rule as everywhere else: an invoice linked to an
    # engagement is hidden from anyone not on that engagement (Admin sees
    # everything). An invoice with no engagement link is visible to all.
    visible = [inv for inv in all_invoices if _invoice_visible(inv)]
    clients = visible_clients(current_user)
    # recurring-invoice drafts the current user can see, waiting for review
    review_count = sum(
        1 for inv in Invoice.query.filter(Invoice.status == "Draft", Invoice.recurring_invoice_id.isnot(None)).all()
        if _invoice_visible(inv)
    )
    ready_count = sum(len(ready_to_bill_tasks(e)) for e in _visible_engagements_for_invoicing())
    return render_template(
        "invoicing/list.html", invoices=visible, clients=clients, ready_count=ready_count,
        statuses=INVOICE_STATUSES, status_filter=status_filter, client_filter=client_filter,
        recurring_only=recurring_only, review_count=review_count,
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
    clients = visible_clients(current_user)
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
    from models import RECURRING_FREQUENCIES
    from models import PAYMENT_METHODS
    show_internal = request.args.get("internal") == "1"
    ready_tasks = (ready_to_bill_tasks(invoice.engagement, include_internal=show_internal)
                   if invoice.engagement and invoice.status in ("Draft", "Sent") else [])
    return render_template("invoicing/detail.html", invoice=invoice, statuses=INVOICE_STATUSES, frequencies=list(RECURRING_FREQUENCIES), today_iso=date.today().isoformat(), methods=PAYMENT_METHODS, ready_tasks=ready_tasks, show_internal=show_internal)


@invoicing_bp.route("/<int:invoice_id>/print")
@login_required
def print_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    return render_template("invoicing/print.html", invoice=invoice, doc=invoice, firm=FIRM_LETTERHEAD)


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
    if status == "Paid":
        # "Paid" is no longer typed in by hand: it is what happens when the
        # money is received. Marking it Paid here records the payment of
        # whatever is still owing, and issues the receipt for it.
        from receipts import add_receipt
        if invoice.status == "Paid" and (invoice.balance <= 0.005):
            flash("This invoice is already paid.", "info")
            return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
        try:
            chosen = datetime.strptime(request.form.get("paid_on", ""), "%Y-%m-%d").date()
        except ValueError:
            chosen = date.today()
        if invoice.status == "Draft":
            invoice.status = "Sent"
        receipt, error = add_receipt(
            invoice, invoice.receiptable_balance, chosen, request.form.get("method") or "Other", current_user)
        if error:
            flash(error, "danger")
            return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
        flash(f"Payment recorded and receipt {receipt.receipt_number} issued.", "success")
        return redirect(url_for("receipts.view_receipt", receipt_id=receipt.id))
    if invoice.amount_paid > 0.005:
        flash("This invoice has receipts issued against it. Void the receipt(s) first if the payment was recorded in error.", "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    invoice.status = status
    invoice.paid_at = None
    db.session.commit()
    flash(f"Invoice marked as {status}.", "success")
    return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))


# ---------- Billing finished work (engagement tasks -> invoice lines) ----------

def _parse_fee(value):
    """A fee typed in the form: a number >= 0, or None if missing/invalid."""
    try:
        fee = round(float(str(value).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None
    return fee if fee >= 0 else None


def _task_line_description(task):
    done = to_cat(task.completed_at).date() if task.completed_at else None
    return f"{task.title} (completed {done.strftime('%d %b %Y')})" if done else task.title


@invoicing_bp.route("/<int:invoice_id>/tasks/add", methods=["POST"])
@login_required
def add_task_lines(invoice_id):
    """Turn ticked, finished engagement tasks into invoice lines - one line
    per task (each with its own fee) or one combined line. A task can only
    ever be billed once: it is linked to the line it produced."""
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    back = redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id) + "#billable-work")
    if not invoice.engagement_id:
        flash("Link this invoice to an engagement first - completed tasks are billed per engagement.", "danger")
        return back
    if invoice.status not in ("Draft", "Sent"):
        flash("Lines can only be added to a Draft or Sent invoice.", "danger")
        return back
    ids = [int(i) for i in request.form.getlist("task_id") if i.isdigit()]
    tasks = EngagementTask.query.filter(EngagementTask.id.in_(ids)).all() if ids else []
    tasks = [t for t in tasks if t.engagement_id == invoice.engagement_id and t.is_ready_to_bill]
    if not tasks:
        flash("Tick at least one completed task to bill (tasks already invoiced or marked not billable are not available).", "danger")
        return back
    tasks.sort(key=lambda t: (t.completed_at or datetime.min, t.id))

    if request.form.get("mode") == "combined":
        description = request.form.get("combined_description", "").strip()
        fee = _parse_fee(request.form.get("combined_fee"))
        if not description or fee is None:
            flash("For one combined line, enter a description and the fee (0 or more).", "danger")
            return back
        line = InvoiceLineItem(invoice_id=invoice.id, description=description, quantity=1.0, unit_price=fee)
        db.session.add(line)
        db.session.flush()
        for t in tasks:
            t.invoice_line_id = line.id
        db.session.commit()
        flash(f"Added one line covering {len(tasks)} completed task(s).", "success")
        return back

    fees = {t.id: _parse_fee(request.form.get(f"fee_{t.id}")) for t in tasks}
    missing = [t.title for t in tasks if fees[t.id] is None]
    if missing:
        flash("Enter a fee for every ticked task (0 if it is covered by another line): " + "; ".join(missing), "danger")
        return back
    for t in tasks:
        line = InvoiceLineItem(invoice_id=invoice.id, description=_task_line_description(t), quantity=1.0, unit_price=fees[t.id])
        db.session.add(line)
        db.session.flush()
        t.invoice_line_id = line.id
    db.session.commit()
    flash(f"Added {len(tasks)} line(s) from completed tasks.", "success")
    return back


@invoicing_bp.route("/tasks/<int:task_id>/billing", methods=["POST"])
@login_required
def toggle_task_billable(task_id):
    """Mark a finished task "not billable" (it drops off Ready to bill) or
    put it back."""
    task = EngagementTask.query.get_or_404(task_id)
    if not user_can_access_engagement(current_user, task.engagement):
        abort(403)
    if task.invoice_line_id:
        flash("That task has already been invoiced.", "info")
    else:
        task.billing_waived = not task.billing_waived
        db.session.commit()
        flash(f"\"{task.title}\" marked {'not billable' if task.billing_waived else 'billable again'}.", "success")
    nxt = request.form.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("invoicing.ready_to_bill"))


@invoicing_bp.route("/ready-to-bill")
@login_required
def ready_to_bill():
    """Finished work not yet invoiced, by engagement, for the engagements the
    current user may see."""
    show_internal = request.args.get("internal") == "1"
    groups = []
    for e in _visible_engagements_for_invoicing():
        tasks = ready_to_bill_tasks(e, include_internal=show_internal)
        if tasks:
            draft = Invoice.query.filter_by(engagement_id=e.id, status="Draft").order_by(Invoice.id.desc()).first()
            groups.append({"engagement": e, "tasks": tasks, "draft": draft})
    groups.sort(key=lambda g: min((t.completed_at or datetime.max) for t in g["tasks"]))
    return render_template("invoicing/ready_to_bill.html", groups=groups, show_internal=show_internal,
                           total=sum(len(g["tasks"]) for g in groups))


@invoicing_bp.route("/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
def edit_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    clients = visible_clients(current_user)

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
    if invoice.active_receipts:
        flash("This invoice has receipts issued against it, so it cannot be deleted. Void the receipt(s) first if the payment was recorded in error.", "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    quotation = invoice.quotation
    if quotation is not None:
        # the quotation it came from goes back to Accepted so it can be
        # converted again, rather than staying "Invoiced" with no invoice
        quotation.status = "Accepted"
        quotation.invoice_id = None
    db.session.delete(invoice)
    db.session.commit()
    flash("Invoice deleted.", "info")
    return redirect(url_for("invoicing.list_invoices"))
