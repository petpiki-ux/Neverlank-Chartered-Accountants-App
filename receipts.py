"""Receipting - issue a numbered receipt (RCT-YYYY-NNNN) each time a client pays
towards an invoice.

* One receipt per payment, so an invoice can be paid in instalments. The
  invoice shows amount paid and balance, and becomes Paid automatically once
  its receipts add up to the invoice total (and goes back to Sent if a receipt
  is voided).
* A receipt is never deleted. A receipt issued in error is VOIDED (Partner or
  Admin only): it stays on file with who/when/why, its number is not reused,
  and it is ignored in every total.
* Access follows the invoice: a receipt for an invoice linked to a
  confidential engagement is only visible to that engagement's team (or Admin).
"""
from datetime import datetime, date, time

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Client, Invoice, Receipt, PAYMENT_METHODS, INVOICE_CURRENCIES, user_can_access_engagement,
)
from invoicing import FIRM_LETTERHEAD, next_sequence_number, _ensure_invoice_access

receipts_bp = Blueprint("receipts", __name__, url_prefix="/receipts")

MONEY_TOLERANCE = 0.005


# ------------------------------------------------------------------ helpers

def _can_see(invoice):
    return not invoice.engagement_id or user_can_access_engagement(current_user, invoice.engagement)


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


def settle_invoice(invoice):
    """Bring the invoice's status in line with its receipts: Paid once they
    cover the total (paid_at = the day the last one was received), back to
    Sent if a receipt has been voided and it is no longer fully covered."""
    active = invoice.active_receipts
    total = invoice.total or 0
    if active and total > 0 and invoice.amount_paid >= round(total, 2) - MONEY_TOLERANCE:
        invoice.status = "Paid"
        last = max(r.paid_on for r in active)
        invoice.paid_at = datetime.combine(last, time(0, 0))
    elif invoice.status == "Paid" and invoice.receipts:
        invoice.status = "Sent"
        invoice.paid_at = None


def add_receipt(invoice, amount, paid_on, method, user):
    """Record a payment and issue its receipt. Returns (receipt, None) or
    (None, "reason it was refused"). Commits on success."""
    if invoice.status == "Draft":
        return None, "Mark the invoice as Sent before recording a payment against it."
    if invoice.status == "Cancelled":
        return None, "This invoice is Cancelled, so no payment can be recorded against it."
    balance = invoice.receiptable_balance
    if invoice.status == "Paid" and invoice.receipts and balance <= MONEY_TOLERANCE:
        return None, "This invoice is already paid in full."
    if amount is None or amount <= 0:
        return None, "Enter the amount received (more than zero)."
    if amount > balance + MONEY_TOLERANCE:
        return None, (f"That is more than the {invoice.currency} {balance:,.2f} still owing on this invoice. "
                      f"Enter {invoice.currency} {balance:,.2f} or less.")
    if abs(amount - balance) <= MONEY_TOLERANCE:
        amount = balance  # no pennies left behind by rounding
    if paid_on is None:
        return None, "Enter the date the payment was received."
    if paid_on > date.today():
        return None, "The payment date cannot be in the future."
    if method not in PAYMENT_METHODS:
        return None, "Choose how the payment was made."

    prefix = f"RCT-{date.today().year}-"
    for attempt in (1, 2):
        receipt = Receipt(
            receipt_number=next_sequence_number(Receipt.receipt_number, prefix), invoice_id=invoice.id,
            amount=amount, currency=invoice.currency, paid_on=paid_on, method=method,
            issued_by_id=user.id if user else None,
        )
        db.session.add(receipt)
        try:
            db.session.flush()
            break
        except IntegrityError:  # two people took the same number at the same moment
            db.session.rollback()
            invoice = Invoice.query.get(invoice.id)
            if attempt == 2:
                return None, "Could not allocate a receipt number - please try again."
    db.session.refresh(invoice)
    settle_invoice(invoice)
    db.session.commit()
    return receipt, None


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_CURRENCY_WORDS = {"USD": "US dollars", "ZWG": "Zimbabwe Gold (ZiG)"}


def _words_below_1000(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " hundred")
        n %= 100
        if n:
            out.append("and")
    if n >= 20:
        out.append(_TENS[n // 10] + (("-" + _ONES[n % 10]) if n % 10 else ""))
    elif n:
        out.append(_ONES[n])
    return " ".join(out)


def amount_in_words(amount, currency):
    """e.g. 1250.50 USD -> 'One thousand two hundred and fifty US dollars and 50 cents only'."""
    whole = int(amount)
    cents = int(round((amount - whole) * 100))
    if cents == 100:
        whole, cents = whole + 1, 0
    parts, scale = [], [(10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")]
    n = whole
    for size, name in scale:
        if n >= size:
            parts.append(_words_below_1000(n // size) + " " + name)
            n %= size
    if n or not parts:
        chunk = _words_below_1000(n) if n else "zero"
        parts.append(("and " + chunk) if parts and n < 100 else chunk)
    text = " ".join(parts) + " " + _CURRENCY_WORDS.get(currency, currency)
    if cents:
        text += f" and {cents} cents"
    text += " only"
    return text[0].upper() + text[1:]


# ------------------------------------------------------------------ routes

@receipts_bp.route("/")
@login_required
def list_receipts():
    client_filter = request.args.get("client_id", "")
    method_filter = request.args.get("method", "")
    month = request.args.get("month", "")  # YYYY-MM
    show_void = request.args.get("void") == "1"
    q = Receipt.query.join(Invoice, Receipt.invoice_id == Invoice.id)
    if client_filter.isdigit():
        q = q.filter(Invoice.client_id == int(client_filter))
    if method_filter in PAYMENT_METHODS:
        q = q.filter(Receipt.method == method_filter)
    if month:
        try:
            y, m = int(month[:4]), int(month[5:7])
            lo = date(y, m, 1)
            hi = date(y + (m == 12), (m % 12) + 1, 1)
            q = q.filter(Receipt.paid_on >= lo, Receipt.paid_on < hi)
        except (ValueError, IndexError):
            month = ""
    if not show_void:
        q = q.filter(Receipt.voided_at.is_(None))
    receipts = [r for r in q.order_by(Receipt.paid_on.desc(), Receipt.id.desc()).all() if _can_see(r.invoice)]
    totals = {}
    for r in receipts:
        if not r.is_void:
            totals[r.currency] = totals.get(r.currency, 0.0) + (r.amount or 0)
    return render_template(
        "receipts/list.html", receipts=receipts, totals=totals,
        clients=Client.query.order_by(Client.name).all(), methods=PAYMENT_METHODS,
        client_filter=client_filter, method_filter=method_filter, month=month, show_void=show_void,
    )


@receipts_bp.route("/<int:receipt_id>")
@login_required
def view_receipt(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
    _ensure_invoice_access(receipt.invoice)
    return render_template(
        "receipts/print.html", receipt=receipt, doc=receipt, invoice=receipt.invoice, firm=FIRM_LETTERHEAD,
        words=amount_in_words(receipt.amount, receipt.currency),
    )


@receipts_bp.route("/invoice/<int:invoice_id>/add", methods=["POST"])
@login_required
def record_payment(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    receipt, error = add_receipt(
        invoice, _parse_amount(request.form.get("amount")), _parse_date(request.form.get("paid_on")),
        request.form.get("method", ""), current_user,
    )
    if error:
        flash(error, "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id))
    state = "The invoice is now paid in full." if invoice.status == "Paid" else \
        f"{invoice.currency} {invoice.balance:,.2f} is still owing."
    flash(f"Receipt {receipt.receipt_number} issued for {receipt.currency} {receipt.amount:,.2f}. {state}", "success")
    return redirect(url_for("receipts.view_receipt", receipt_id=receipt.id))


@receipts_bp.route("/<int:receipt_id>/void", methods=["POST"])
@login_required
def void_receipt(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
    invoice = receipt.invoice
    _ensure_invoice_access(invoice)
    if current_user.role not in ("partner", "admin"):
        abort(403, description="Only a Partner or Admin can void a receipt.")
    if receipt.is_void:
        flash("That receipt is already void.", "info")
        return redirect(url_for("receipts.view_receipt", receipt_id=receipt.id))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please give a reason for voiding the receipt.", "danger")
        return redirect(url_for("receipts.view_receipt", receipt_id=receipt.id))
    receipt.voided_at = datetime.utcnow()
    receipt.voided_by_id = current_user.id
    receipt.void_reason = reason
    db.session.flush()
    db.session.refresh(invoice)
    settle_invoice(invoice)
    db.session.commit()
    flash(f"Receipt {receipt.receipt_number} voided. Invoice {invoice.invoice_number} now shows "
          f"{invoice.currency} {invoice.balance:,.2f} owing.", "info")
    return redirect(url_for("receipts.view_receipt", receipt_id=receipt.id))
