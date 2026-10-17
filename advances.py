"""Customer advances (Finance > Advances) - money a client pays BEFORE it is invoiced.

* Recording an advance issues a numbered receipt, ADV-YYYY-NNNN. The amount is
  what actually arrived (VAT-inclusive, like every receipt).
* Until it is applied, the money is a LIABILITY of the firm - the client's
  credit - not income. It appears on the client's statement as a credit on the
  day it was received (so the statement can show "In credit"), and on the
  Advances list as "held".
* It is later APPLIED to one or several invoices of the same client and
  currency, in full or in part. Applying issues an ordinary invoice receipt
  (RCT-..., method "Advance applied") through the same rules as any payment,
  so the invoice balance, Paid status and ageing follow automatically. That
  receipt is not new cash: the money was counted when the advance came in.
* The unused part can be REFUNDED (Partner or Admin).
* Nothing is deleted. An advance recorded in error is VOIDED (Partner or Admin,
  with a reason, and only while nothing has been applied or refunded); an
  application is reversed by voiding its receipt; a refund can be voided.
* Access follows the client, and the engagement when the advance is linked to
  one (a confidential engagement's advances are only seen by its team / Admin).
"""
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Client, Invoice, Receipt, Engagement, ClientAdvance, ClientAdvanceApplication, ClientAdvanceRefund,
    PAYMENT_METHODS, INVOICE_CURRENCIES, visible_clients, user_can_access_client, user_can_access_engagement,
)
from invoicing import FIRM_LETTERHEAD, next_sequence_number, _ensure_invoice_access
from receipts import settle_invoice, amount_in_words, MONEY_TOLERANCE

advances_bp = Blueprint("advances", __name__, url_prefix="/advances")

APPLIED_METHOD = "Advance applied"


# ------------------------------------------------------------------ helpers

def can_see_advance(advance):
    if not user_can_access_client(current_user, advance.client_id):
        return False
    return not advance.engagement_id or user_can_access_engagement(current_user, advance.engagement)


def _ensure_access(advance):
    if not user_can_access_client(current_user, advance.client_id):
        abort(403, description="You are not working on this advance's client, so you cannot open it.")
    if advance.engagement_id and not user_can_access_engagement(current_user, advance.engagement):
        abort(403)


def _is_senior():
    return current_user.role in ("partner", "admin")


def _parse_amount(value):
    try:
        return round(float(str(value).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def client_advances(client):
    """(advances of this client the user can see, number hidden from them)."""
    every = list(client.advances)
    shown = [a for a in every if can_see_advance(a)]
    return shown, len(every) - len(shown)


def held_by_currency(advances):
    """{currency: unapplied money held} for the given advances."""
    out = {}
    for a in advances:
        if not a.is_void and a.balance > MONEY_TOLERANCE:
            out[a.currency] = round(out.get(a.currency, 0.0) + a.balance, 2)
    return out


def available_for_invoice(invoice):
    """Advances (visible to the user) that could be applied to this invoice now:
    same client and currency, not void, with money left."""
    if invoice.status != "Sent" or invoice.receiptable_balance <= MONEY_TOLERANCE:
        return []
    return [a for a in invoice.client.advances
            if a.currency == invoice.currency and a.balance > MONEY_TOLERANCE and can_see_advance(a)]


# ------------------------------------------------------------- the operations

def record_advance(client, currency, amount, received_on, method, reference, purpose, notes, engagement, user,
                   cash_account_raw=None):
    """Create the advance and its ADV- receipt. Returns (advance, None) or (None, reason). Commits on success."""
    if currency not in INVOICE_CURRENCIES:
        return None, "Choose the currency the client paid in."
    if amount is None or amount <= 0:
        return None, "Enter the amount received (more than zero)."
    if received_on is None:
        return None, "Enter the date the money was received."
    if received_on > date.today():
        return None, "The date received cannot be in the future."
    if method not in PAYMENT_METHODS:
        return None, "Choose how the client paid."
    if engagement is not None and engagement.client_id != client.id:
        return None, "That engagement doesn't belong to the selected client."
    import cashbook
    cash_account_id, bad_account = cashbook.parse_choice(cash_account_raw, currency)
    if bad_account:
        return None, bad_account
    prefix = f"ADV-{date.today().year}-"
    for attempt in (1, 2):
        advance = ClientAdvance(
            advance_number=next_sequence_number(ClientAdvance.advance_number, prefix), client_id=client.id,
            engagement_id=engagement.id if engagement else None, currency=currency, amount=amount,
            received_on=received_on, method=method, reference=(reference or "").strip() or None,
            purpose=(purpose or "").strip() or None, notes=(notes or "").strip() or None,
            issued_by_id=user.id if user else None,
        )
        db.session.add(advance)
        try:
            db.session.flush()
            break
        except IntegrityError:
            db.session.rollback()
            if attempt == 2:
                return None, "Could not allocate an advance number - please try again."
    cashbook.set_account(f"advance:{advance.id}", cash_account_id)
    db.session.commit()
    return advance, None


def apply_advance(advance, invoice, amount, applied_on, user):
    """Set `amount` of the advance against the invoice by issuing an invoice
    receipt for it. Returns (receipt, None) or (None, reason). Commits on success."""
    if advance.is_void:
        return None, "This advance has been voided."
    if invoice.client_id != advance.client_id:
        return None, "An advance can only be applied to an invoice of the same client."
    if invoice.currency != advance.currency:
        return None, (f"The advance is in {advance.currency} but the invoice is in {invoice.currency}. "
                      f"The two currencies are never mixed.")
    if invoice.status == "Draft":
        return None, "Mark the invoice as Sent before applying an advance to it."
    if invoice.status == "Cancelled":
        return None, "This invoice is Cancelled, so nothing can be applied to it."
    owing = invoice.receiptable_balance
    if owing <= MONEY_TOLERANCE:
        return None, "This invoice has nothing left to pay."
    if amount is None or amount <= 0:
        return None, "Enter the amount to apply (more than zero)."
    held = advance.balance
    if amount > held + MONEY_TOLERANCE:
        return None, f"Only {advance.currency} {held:,.2f} of this advance is still unapplied."
    if amount > owing + MONEY_TOLERANCE:
        return None, f"The invoice only has {invoice.currency} {owing:,.2f} still owing."
    if abs(amount - held) <= MONEY_TOLERANCE:
        amount = held
    if abs(amount - owing) <= MONEY_TOLERANCE:
        amount = owing
    if applied_on is None:
        return None, "Enter the date the advance is applied."
    if applied_on > date.today():
        return None, "The date cannot be in the future."
    if applied_on < advance.received_on:
        return None, "An advance cannot be applied before the day it was received."

    prefix = f"RCT-{date.today().year}-"
    advance_id, invoice_id = advance.id, invoice.id
    for attempt in (1, 2):
        application = ClientAdvanceApplication(
            advance_id=advance_id, invoice_id=invoice_id, amount=amount, applied_on=applied_on,
            applied_by_id=user.id if user else None,
        )
        db.session.add(application)
        db.session.flush()
        receipt = Receipt(
            receipt_number=next_sequence_number(Receipt.receipt_number, prefix), invoice_id=invoice_id,
            amount=amount, currency=invoice.currency, paid_on=applied_on, method=APPLIED_METHOD,
            issued_by_id=user.id if user else None, advance_application_id=application.id,
        )
        db.session.add(receipt)
        try:
            db.session.flush()
            break
        except IntegrityError:  # two people took the same receipt number at the same moment
            db.session.rollback()
            if attempt == 2:
                return None, "Could not allocate a receipt number - please try again."
            invoice = Invoice.query.get(invoice_id)
    invoice = Invoice.query.get(invoice_id)
    db.session.refresh(invoice)
    settle_invoice(invoice)
    db.session.commit()
    return receipt, None


# ------------------------------------------------------------------ routes

@advances_bp.route("/")
@login_required
def list_advances():
    client_filter = request.args.get("client_id", "")
    state_filter = request.args.get("state", "")
    show_void = request.args.get("void") == "1"
    q = ClientAdvance.query
    if client_filter.isdigit():
        q = q.filter(ClientAdvance.client_id == int(client_filter))
    if not show_void:
        q = q.filter(ClientAdvance.voided_at.is_(None))
    advances = [a for a in q.order_by(ClientAdvance.received_on.desc(), ClientAdvance.id.desc()).all() if can_see_advance(a)]
    if state_filter == "open":
        advances = [a for a in advances if a.balance > MONEY_TOLERANCE]
    received, held = {}, {}
    for a in advances:
        if a.is_void:
            continue
        received[a.currency] = received.get(a.currency, 0.0) + (a.amount or 0)
        if a.balance > MONEY_TOLERANCE:
            held[a.currency] = held.get(a.currency, 0.0) + a.balance
    return render_template(
        "advances/list.html", advances=advances, received=received, held=held, clients=visible_clients(current_user),
        client_filter=client_filter, state_filter=state_filter, show_void=show_void,
    )


@advances_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_advance():
    from engagements import _visible_to_current_user
    clients = visible_clients(current_user)
    engagements = _visible_to_current_user(Engagement.query.order_by(Engagement.client_id, Engagement.title).all())

    def form(**extra):
        return render_template(
            "advances/new.html", clients=clients, engagements=engagements, currencies=INVOICE_CURRENCIES,
            methods=PAYMENT_METHODS, today=date.today().isoformat(), **extra)

    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        client = Client.query.get(client_id) if client_id else None
        if client is None:
            flash("Please select a client.", "danger")
            return form()
        if not user_can_access_client(current_user, client.id):
            abort(403)
        engagement = None
        if request.form.get("engagement_id"):
            engagement = Engagement.query.get(request.form.get("engagement_id", type=int) or 0)
            if engagement is None:
                flash("That engagement was not found.", "danger")
                return form(preselect_client_id=client.id)
            if not user_can_access_engagement(current_user, engagement):
                abort(403)
        advance, error = record_advance(
            client, request.form.get("currency", "USD"), _parse_amount(request.form.get("amount")),
            _parse_date(request.form.get("received_on")), request.form.get("method", ""),
            request.form.get("reference"), request.form.get("purpose"), request.form.get("notes"),
            engagement, current_user, cash_account_raw=request.form.get("cash_account_id"))
        if error:
            flash(error, "danger")
            return form(preselect_client_id=client.id)
        flash(f"Advance {advance.advance_number} recorded: {advance.currency} {advance.amount:,.2f} held for {client.name} "
              f"until it is applied to an invoice.", "success")
        return redirect(url_for("advances.view_advance", advance_id=advance.id))

    return form(preselect_client_id=request.args.get("client_id", type=int))


@advances_bp.route("/<int:advance_id>")
@login_required
def view_advance(advance_id):
    advance = ClientAdvance.query.get_or_404(advance_id)
    _ensure_access(advance)
    open_invoices = []
    if not advance.is_void and advance.balance > MONEY_TOLERANCE:
        for inv in Invoice.query.filter_by(client_id=advance.client_id, currency=advance.currency, status="Sent") \
                .order_by(Invoice.issue_date, Invoice.id).all():
            try:
                _ensure_invoice_access(inv)
            except Exception:
                continue
            if inv.receiptable_balance > MONEY_TOLERANCE:
                open_invoices.append(inv)
    return render_template(
        "advances/view.html", advance=advance, open_invoices=open_invoices, methods=PAYMENT_METHODS,
        today=date.today().isoformat(), is_senior=_is_senior(),
    )


@advances_bp.route("/<int:advance_id>/print")
@login_required
def print_advance(advance_id):
    advance = ClientAdvance.query.get_or_404(advance_id)
    _ensure_access(advance)
    return render_template(
        "advances/print.html", advance=advance, doc=advance, firm=FIRM_LETTERHEAD,
        words=amount_in_words(advance.amount, advance.currency),
    )


@advances_bp.route("/<int:advance_id>/apply", methods=["POST"])
@login_required
def apply(advance_id):
    advance = ClientAdvance.query.get_or_404(advance_id)
    _ensure_access(advance)
    invoice = Invoice.query.get_or_404(request.form.get("invoice_id", type=int) or 0)
    _ensure_invoice_access(invoice)
    back = request.form.get("next") or ""
    receipt, error = apply_advance(
        advance, invoice, _parse_amount(request.form.get("amount")),
        _parse_date(request.form.get("applied_on")) or date.today(), current_user)
    target = (url_for("invoicing.view_invoice", invoice_id=invoice.id) + "#payments") if back == "invoice" \
        else url_for("advances.view_advance", advance_id=advance.id)
    if error:
        flash(error, "danger")
        return redirect(target)
    state = "The invoice is now paid in full." if invoice.status == "Paid" else \
        f"{invoice.currency} {invoice.balance:,.2f} is still owing on it."
    flash(f"{receipt.currency} {receipt.amount:,.2f} of advance {advance.advance_number} applied to invoice "
          f"{invoice.invoice_number} (receipt {receipt.receipt_number}). {state} "
          f"{advance.currency} {advance.balance:,.2f} of the advance remains.", "success")
    return redirect(target)


@advances_bp.route("/application/<int:application_id>/reverse", methods=["POST"])
@login_required
def reverse_application(application_id):
    application = ClientAdvanceApplication.query.get_or_404(application_id)
    advance, invoice = application.advance, application.invoice
    _ensure_access(advance)
    _ensure_invoice_access(invoice)
    if not _is_senior():
        abort(403, description="Only a Partner or Admin can reverse an advance application.")
    receipt = application.receipt
    if receipt is None or receipt.is_void:
        flash("That application has already been reversed.", "info")
        return redirect(url_for("advances.view_advance", advance_id=advance.id))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please give a reason for reversing the application.", "danger")
        return redirect(url_for("advances.view_advance", advance_id=advance.id))
    receipt.voided_at = datetime.utcnow()
    receipt.voided_by_id = current_user.id
    receipt.void_reason = f"Advance application reversed: {reason}"
    db.session.flush()
    db.session.refresh(invoice)
    settle_invoice(invoice)
    db.session.commit()
    flash(f"Application reversed. {application.amount:,.2f} is back in advance {advance.advance_number} "
          f"and invoice {invoice.invoice_number} shows {invoice.currency} {invoice.balance:,.2f} owing.", "info")
    return redirect(url_for("advances.view_advance", advance_id=advance.id))


@advances_bp.route("/<int:advance_id>/refund", methods=["POST"])
@login_required
def refund(advance_id):
    advance = ClientAdvance.query.get_or_404(advance_id)
    _ensure_access(advance)
    back = redirect(url_for("advances.view_advance", advance_id=advance.id))
    if not _is_senior():
        abort(403, description="Only a Partner or Admin can refund an advance.")
    if advance.is_void:
        flash("This advance has been voided.", "danger")
        return back
    amount, when = _parse_amount(request.form.get("amount")), _parse_date(request.form.get("refunded_on"))
    method = request.form.get("method", "")
    if amount is None or amount <= 0:
        flash("Enter the amount refunded (more than zero).", "danger")
        return back
    if amount > advance.balance + MONEY_TOLERANCE:
        flash(f"Only {advance.currency} {advance.balance:,.2f} of this advance is unapplied, so that is the most that can be refunded.", "danger")
        return back
    if abs(amount - advance.balance) <= MONEY_TOLERANCE:
        amount = advance.balance
    if when is None or when > date.today() or when < advance.received_on:
        flash("Enter the date refunded (not in the future, and not before the advance was received).", "danger")
        return back
    if method not in PAYMENT_METHODS:
        flash("Choose how the refund was paid.", "danger")
        return back
    import cashbook
    cash_account_id, bad_account = cashbook.parse_choice(request.form.get("cash_account_id"), advance.currency)
    if bad_account:
        flash(bad_account, "danger")
        return back
    new_refund = ClientAdvanceRefund(
        advance_id=advance.id, amount=amount, refunded_on=when, method=method,
        reference=request.form.get("reference", "").strip() or None, notes=request.form.get("notes", "").strip() or None,
        refunded_by_id=current_user.id)
    db.session.add(new_refund)
    db.session.flush()
    cashbook.set_account(f"refund:{new_refund.id}", cash_account_id)
    db.session.commit()
    flash(f"Refund of {advance.currency} {amount:,.2f} recorded against {advance.advance_number}.", "success")
    return back


@advances_bp.route("/refund/<int:refund_id>/void", methods=["POST"])
@login_required
def void_refund(refund_id):
    refund_row = ClientAdvanceRefund.query.get_or_404(refund_id)
    advance = refund_row.advance
    _ensure_access(advance)
    if not _is_senior():
        abort(403, description="Only a Partner or Admin can void a refund.")
    back = redirect(url_for("advances.view_advance", advance_id=advance.id))
    if refund_row.is_void:
        flash("That refund is already void.", "info")
        return back
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please give a reason for voiding the refund.", "danger")
        return back
    refund_row.voided_at = datetime.utcnow()
    refund_row.voided_by_id = current_user.id
    refund_row.void_reason = reason
    db.session.commit()
    flash("Refund voided - the money is held on the advance again.", "info")
    return back


@advances_bp.route("/<int:advance_id>/void", methods=["POST"])
@login_required
def void_advance(advance_id):
    advance = ClientAdvance.query.get_or_404(advance_id)
    _ensure_access(advance)
    back = redirect(url_for("advances.view_advance", advance_id=advance.id))
    if not _is_senior():
        abort(403, description="Only a Partner or Admin can void an advance.")
    if advance.is_void:
        flash("That advance is already void.", "info")
        return back
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please give a reason for voiding the advance.", "danger")
        return back
    if advance.active_applications or advance.active_refunds:
        flash("Part of this advance has already been applied or refunded. Reverse those first, then void it.", "danger")
        return back
    advance.voided_at = datetime.utcnow()
    advance.voided_by_id = current_user.id
    advance.void_reason = reason
    db.session.commit()
    flash(f"Advance {advance.advance_number} voided.", "info")
    return back
