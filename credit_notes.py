"""Credit notes - reduce what a client owes on an invoice, in full or in part
(a billing error, a discount agreed afterwards, a service not delivered).

* Issued against ONE invoice that has been issued to the client (status Sent
  or Paid), in the invoice's currency and at its VAT rate. The total credited
  can never exceed the invoice total.
* Numbered CN-YYYY-NNNN. Never deleted: one issued in error is VOIDED (it
  stays on file with who/when/why, its number is not reused, and it is ignored
  in every total), so there are no gaps in the numbering and an audit trail.
* The invoice follows its credit notes: the balance owing, its Paid / Sent
  status, the client's statement and the analytics all use "invoice total less
  credit notes". If the client had already paid more than they now owe, the
  invoice shows a refund due.
* Only people with the "issue_credit_notes" permission (Partners and Admin by
  default) can issue or void one. Everyone who can see the invoice can see its
  credit notes. Access follows the invoice, as for receipts.
"""
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, Response
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Invoice, CreditNote, CreditNoteLine, INVOICE_CURRENCIES, user_has_permission, visible_clients,
)
from invoicing import FIRM_LETTERHEAD, next_sequence_number, _ensure_invoice_access, _invoice_visible
from receipts import settle_invoice, amount_in_words
from credit_note_pdf import build_credit_note_pdf, credit_note_filename
import emailer

credit_notes_bp = Blueprint("credit_notes", __name__, url_prefix="/credit-notes")

MONEY_TOLERANCE = 0.005
MAX_LINES = 40


def _require_permission():
    if not user_has_permission(current_user, "issue_credit_notes"):
        abort(403, description="Only a Partner or Admin can issue or void a credit note.")


def _parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def invoice_is_creditable(invoice):
    """Credit notes only make sense against an invoice the client has actually been issued."""
    return invoice.status in ("Sent", "Paid")


def credit_blocker(invoice):
    """Why a credit note cannot be issued against this invoice now ('' if it can)."""
    if invoice.status == "Draft":
        return "This invoice is still a Draft. Edit the draft instead - a credit note is for an invoice already issued."
    if invoice.status == "Cancelled":
        return "This invoice is Cancelled, so there is nothing to credit."
    if invoice.creditable_balance <= MONEY_TOLERANCE:
        return "This invoice has already been credited in full."
    return ""


def _full_credit_lines(invoice):
    """Lines (description, quantity, unit_price) for crediting everything still creditable."""
    if not invoice.active_credit_notes:
        return [(l.description, l.quantity or 0, l.unit_price or 0) for l in invoice.lines]
    ex_vat = invoice.creditable_balance / (1 + (invoice.vat_pct or 0) / 100.0)
    return [(f"Balance of invoice {invoice.invoice_number} credited", 1.0, round(ex_vat, 2))]


def _parse_lines(form):
    descriptions = form.getlist("description")
    quantities = form.getlist("quantity")
    prices = form.getlist("unit_price")
    lines, problems = [], []
    for i, desc in enumerate(descriptions[:MAX_LINES]):
        desc = (desc or "").strip()
        qty = _parse_number(quantities[i]) if i < len(quantities) else None
        price = _parse_number(prices[i]) if i < len(prices) else None
        if not desc and not qty and not price:
            continue  # an empty spare row
        if not desc:
            problems.append(f"Line {i + 1} has an amount but no description.")
            continue
        if qty is None or qty <= 0 or price is None or price <= 0:
            problems.append(f"Line {i + 1} ({desc[:40]}): enter a quantity and unit price above zero.")
            continue
        lines.append((desc[:300], qty, price))
    return lines, problems


def issue_credit_note(invoice, lines, reason, issue_date, notes, user):
    """Create and number a credit note. Returns (credit_note, None) or (None, "reason refused"). Commits on success."""
    blocker = credit_blocker(invoice)
    if blocker:
        return None, blocker
    if not lines:
        return None, "Add at least one line to credit."
    if not (reason or "").strip():
        return None, "Give the reason for the credit note (it is printed on it and kept on file)."
    if issue_date is None:
        return None, "Enter the date of the credit note."
    if issue_date > date.today():
        return None, "The credit note date cannot be in the future."
    if invoice.issue_date and issue_date < invoice.issue_date:
        return None, f"The credit note cannot be dated before the invoice ({invoice.issue_date.strftime('%d %b %Y')})."
    subtotal = sum(q * p for _d, q, p in lines)
    total = subtotal * (1 + (invoice.vat_pct or 0) / 100.0)
    room = invoice.creditable_balance
    if total > room + MONEY_TOLERANCE:
        return None, (f"That credit is {invoice.currency} {total:,.2f} including VAT, which is more than the "
                      f"{invoice.currency} {room:,.2f} still creditable on this invoice. Reduce the amounts.")

    prefix = f"CN-{date.today().year}-"
    for attempt in (1, 2):
        note = CreditNote(
            credit_note_number=next_sequence_number(CreditNote.credit_note_number, prefix), invoice_id=invoice.id,
            issue_date=issue_date, currency=invoice.currency, vat_pct=invoice.vat_pct or 0.0,
            reason=reason.strip(), notes=(notes or "").strip() or None, issued_by_id=user.id if user else None,
        )
        for desc, qty, price in lines:
            note.lines.append(CreditNoteLine(description=desc, quantity=qty, unit_price=price))
        db.session.add(note)
        try:
            db.session.flush()
            break
        except IntegrityError:  # two people took the same number at the same moment
            db.session.rollback()
            invoice = Invoice.query.get(invoice.id)
            if attempt == 2:
                return None, "Could not allocate a credit note number - please try again."
    db.session.refresh(invoice)
    settle_invoice(invoice)
    db.session.commit()
    return note, None


# ------------------------------------------------------------------ routes

@credit_notes_bp.route("/")
@login_required
def list_credit_notes():
    client_filter = request.args.get("client_id", "")
    month = request.args.get("month", "")  # YYYY-MM
    show_void = request.args.get("void") == "1"
    q = CreditNote.query.join(Invoice, CreditNote.invoice_id == Invoice.id)
    if client_filter.isdigit():
        q = q.filter(Invoice.client_id == int(client_filter))
    if month:
        try:
            y, m = int(month[:4]), int(month[5:7])
            q = q.filter(CreditNote.issue_date >= date(y, m, 1),
                         CreditNote.issue_date < date(y + (m == 12), (m % 12) + 1, 1))
        except (ValueError, IndexError):
            month = ""
    if not show_void:
        q = q.filter(CreditNote.voided_at.is_(None))
    notes = [c for c in q.order_by(CreditNote.issue_date.desc(), CreditNote.id.desc()).all()
             if _invoice_visible(c.invoice)]
    totals = {}
    for c in notes:
        if not c.is_void:
            totals[c.currency] = totals.get(c.currency, 0.0) + c.total
    return render_template(
        "credit_notes/list.html", notes=notes, totals=totals, clients=visible_clients(current_user),
        client_filter=client_filter, month=month, show_void=show_void,
    )


@credit_notes_bp.route("/<int:note_id>")
@login_required
def view_credit_note(note_id):
    note = CreditNote.query.get_or_404(note_id)
    _ensure_invoice_access(note.invoice)
    return render_template(
        "credit_notes/print.html", note=note, doc=note, invoice=note.invoice, firm=FIRM_LETTERHEAD,
        words=amount_in_words(note.total, note.currency),
        can_void=user_has_permission(current_user, "issue_credit_notes") and not note.is_void,
    )


@credit_notes_bp.route("/invoice/<int:invoice_id>/new", methods=["GET", "POST"])
@login_required
def new_credit_note(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    _require_permission()
    blocker = credit_blocker(invoice)
    if blocker:
        flash(blocker, "danger")
        return redirect(url_for("invoicing.view_invoice", invoice_id=invoice.id))

    if request.method == "POST":
        mode = request.form.get("mode", "partial")
        if mode == "full":
            lines, problems = _full_credit_lines(invoice), []
        else:
            lines, problems = _parse_lines(request.form)
        issue_date = _parse_date(request.form.get("issue_date"))
        if problems:
            error = " ".join(problems)
        else:
            note, error = issue_credit_note(
                invoice, lines, request.form.get("reason", ""), issue_date, request.form.get("notes", ""), current_user)
        if not error:
            if invoice.refund_due > MONEY_TOLERANCE:
                state = (f"The client had already paid, so {invoice.currency} {invoice.refund_due:,.2f} is now due back "
                         "to them (or can be held as credit).")
            elif invoice.is_fully_credited:
                state = "The invoice is credited in full - nothing is owing."
            else:
                state = f"{invoice.currency} {invoice.balance:,.2f} is still owing on the invoice."
            flash(f"Credit note {note.credit_note_number} issued for {note.currency} {note.total:,.2f}. {state}", "success")
            return redirect(url_for("credit_notes.view_credit_note", note_id=note.id))
        flash(error, "danger")
        form_lines = lines if mode != "full" and (lines or problems) else None
        return render_template(
            "credit_notes/new.html", invoice=invoice, lines=form_lines or _prefill(invoice),
            issue_date=request.form.get("issue_date") or date.today().isoformat(),
            reason=request.form.get("reason", ""), notes=request.form.get("notes", ""),
            today_iso=date.today().isoformat(),
        )

    return render_template(
        "credit_notes/new.html", invoice=invoice, lines=_prefill(invoice), issue_date=date.today().isoformat(),
        reason="", notes="", today_iso=date.today().isoformat(),
    )


def _prefill(invoice):
    """Starting point for the partial form: the invoice's own lines (the user cuts them down)."""
    return [(l.description, l.quantity or 0, l.unit_price or 0) for l in invoice.lines]


@credit_notes_bp.route("/<int:note_id>/void", methods=["POST"])
@login_required
def void_credit_note(note_id):
    note = CreditNote.query.get_or_404(note_id)
    invoice = note.invoice
    _ensure_invoice_access(invoice)
    _require_permission()
    if note.is_void:
        flash("That credit note is already void.", "info")
        return redirect(url_for("credit_notes.view_credit_note", note_id=note.id))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please give a reason for voiding the credit note.", "danger")
        return redirect(url_for("credit_notes.view_credit_note", note_id=note.id))
    note.voided_at = datetime.utcnow()
    note.voided_by_id = current_user.id
    note.void_reason = reason
    db.session.flush()
    db.session.refresh(invoice)
    settle_invoice(invoice)
    db.session.commit()
    flash(f"Credit note {note.credit_note_number} voided. Invoice {invoice.invoice_number} now shows "
          f"{invoice.currency} {invoice.balance:,.2f} owing.", "info")
    return redirect(url_for("credit_notes.view_credit_note", note_id=note.id))


@credit_notes_bp.route("/<int:note_id>/pdf")
@login_required
def credit_note_pdf(note_id):
    note = CreditNote.query.get_or_404(note_id)
    _ensure_invoice_access(note.invoice)
    return Response(build_credit_note_pdf(note, FIRM_LETTERHEAD), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{credit_note_filename(note)}"'})


@credit_notes_bp.route("/<int:note_id>/email", methods=["POST"])
@login_required
def email_credit_note(note_id):
    """Email the credit note (PDF attached) to the client - for anyone allowed to send invoices."""
    note = CreditNote.query.get_or_404(note_id)
    invoice = note.invoice
    _ensure_invoice_access(invoice)
    if not user_has_permission(current_user, "send_invoices"):
        abort(403, description="You do not have permission to send documents to clients.")
    back = redirect(url_for("credit_notes.view_credit_note", note_id=note.id))
    if note.is_void:
        flash("A voided credit note cannot be sent.", "danger")
        return back
    if not emailer.is_configured():
        flash("Email is not set up on this system yet.", "danger")
        return back
    to = [a.strip() for a in (request.form.get("to") or invoice.client.email or "").replace(";", ",").split(",") if a.strip()]
    if not to:
        flash("Enter the email address to send it to (the client has none on file).", "danger")
        return back
    who = (invoice.client.contact_person or "").strip() or invoice.client.name
    subject = f"Credit note {note.credit_note_number} from {FIRM_LETTERHEAD['name']}"
    text = (f"Dear {who},\n\nPlease find attached credit note {note.credit_note_number} for "
            f"{note.currency} {note.total:,.2f}, issued against invoice {invoice.invoice_number}.\n\n"
            f"Reason: {note.reason}\n\nIf you have any questions, just reply to this email.\n\n"
            f"Kind regards,\n{current_user.name}\n{FIRM_LETTERHEAD['name']}")
    from invoice_send import _html_body
    ok, error = emailer.send_email(
        to, subject, text, html=_html_body(text),
        attachments=[(credit_note_filename(note), build_credit_note_pdf(note, FIRM_LETTERHEAD), "application/pdf")])
    flash(f"Credit note emailed to {', '.join(to)}." if ok else f"Could not send the email: {error}", "success" if ok else "danger")
    return back
