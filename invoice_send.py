"""Sending invoices to clients from the app, and overdue-payment reminders.

* Email: the invoice PDF is built here and attached (see emailer.py for the
  email-service settings) - nobody has to download or print anything first.
* WhatsApp: a click-to-chat link. One click opens WhatsApp to the client's number
  with the message ready, including a private link to the invoice PDF. The app
  cannot press Send inside WhatsApp, so this is logged as "opened".
* Every send is logged on the invoice (who, how, to what address, when).
* Reminders: an invoice that is 30 or more days past its due date and still
  unpaid is listed on Finance > Reminders, and again every 7 days after a
  reminder goes out. Nothing is sent automatically - staff send each one (or
  email all that are due, after a confirmation).
"""
import re
import secrets
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import emailer
from extensions import db
from invoice_pdf import build_invoice_pdf, invoice_filename
from invoicing import FIRM_LETTERHEAD, _ensure_invoice_access, _invoice_visible
from models import Invoice, InvoiceSend, user_has_permission
from timeutil import to_cat

invoice_send_bp = Blueprint("invoice_send", __name__, url_prefix="/invoices")
public_invoice_bp = Blueprint("public_invoice", __name__)

REMINDER_AFTER_DAYS = 30   # first reminder once this many days past the due date
REMINDER_EVERY_DAYS = 7    # then weekly
DEFAULT_COUNTRY_CODE = "263"  # Zimbabwe - used for numbers typed as 077 123 4567


# ------------------------------------------------------------------ helpers

def _require_send_permission():
    if not user_has_permission(current_user, "send_invoices"):
        abort(403, description="You do not have permission to send invoices to clients. Ask an Admin (Team > Permissions).")


def _guard(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    _require_send_permission()
    return invoice


def public_base_url():
    """The address clients use to reach the app (PUBLIC_BASE_URL if set, else this request's)."""
    import os
    base = (os.environ.get("PUBLIC_BASE_URL") or request.url_root).rstrip("/")
    if base.startswith("http://") and not re.match(r"http://(localhost|127\.|\[::1\])", base):
        base = "https://" + base[len("http://"):]
    return base


def normalize_phone(raw, country_code=DEFAULT_COUNTRY_CODE):
    """A phone number as the digits WhatsApp wants (country code first, no +), or None.
    '077 123 4567' -> '263771234567'; '+263 77 123 4567' and '00263771234567' -> '263771234567'."""
    text = (raw or "").strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = country_code + digits[1:]
    elif len(digits) <= 9:
        digits = country_code + digits
    return digits if 9 <= len(digits) <= 15 else None


def ensure_share_token(invoice):
    if not invoice.share_token:
        invoice.share_token = secrets.token_urlsafe(24)
        db.session.flush()
    return invoice.share_token


def share_url(invoice):
    return f"{public_base_url()}/share/invoice/{ensure_share_token(invoice)}"


def invoice_date_of(invoice):
    if invoice.issue_date:
        return invoice.issue_date
    return to_cat(invoice.created_at).date() if invoice.created_at else date.today()


def due_date_of(invoice):
    """The due date, or issue date + 30 days for an old invoice that has none."""
    return invoice.due_date or (invoice_date_of(invoice) + timedelta(days=30))


def money(invoice, amount):
    return f"{invoice.currency} {amount:,.2f}"


def _greeting_name(invoice):
    contact = (invoice.client.contact_person or "").strip()
    return contact or invoice.client.name


def _sender_signature():
    return f"{current_user.name}\n{FIRM_LETTERHEAD['name']}"


def default_invoice_email(invoice):
    due = due_date_of(invoice).strftime("%d %b %Y")
    lines = [
        f"Dear {_greeting_name(invoice)},", "",
        f"Please find attached invoice {invoice.invoice_number} for {money(invoice, invoice.total)}, due on {due}.", "",
    ]
    if FIRM_LETTERHEAD.get("bank_details"):
        lines += ["Payment details:", FIRM_LETTERHEAD["bank_details"], ""]
    lines += [f"Please quote {invoice.invoice_number} with your payment. If you have any questions, just reply to this email.", "",
              "Kind regards,", _sender_signature()]
    return (f"Invoice {invoice.invoice_number} from {FIRM_LETTERHEAD['name']}", "\n".join(lines))


def default_invoice_whatsapp(invoice):
    due = due_date_of(invoice).strftime("%d %b %Y")
    return (f"Dear {_greeting_name(invoice)}, please find invoice {invoice.invoice_number} for "
            f"{money(invoice, invoice.total)}, due on {due}. You can view and download it here: {{link}}\n\n"
            f"Kind regards, {current_user.name}, {FIRM_LETTERHEAD['name']}")


def days_overdue(invoice, today=None):
    return ((today or date.today()) - due_date_of(invoice)).days


def last_reminder(invoice):
    for send in invoice.sends:  # newest first
        if send.kind == "reminder" and send.status in ("sent", "opened"):
            return send
    return None


def reminder_state(invoice, today=None):
    """None if no reminder applies; otherwise a dict describing where it stands."""
    today = today or date.today()
    if invoice.status != "Sent" or invoice.balance <= 0.005:
        return None
    overdue = days_overdue(invoice, today)
    if overdue < REMINDER_AFTER_DAYS:
        return None
    last = last_reminder(invoice)
    last_day = to_cat(last.sent_at).date() if last else None
    next_due = (last_day + timedelta(days=REMINDER_EVERY_DAYS)) if last_day else None
    due_now = (not invoice.reminders_paused) and (last_day is None or today >= next_due)
    return {"overdue": overdue, "last": last, "last_day": last_day, "next_due": next_due,
            "due_now": due_now, "paused": invoice.reminders_paused}


def reminder_rows(today=None, only_visible=True):
    """Every invoice in the reminder cycle (visible to the current user), most overdue first."""
    today = today or date.today()
    candidates = Invoice.query.filter(Invoice.status == "Sent").all()
    rows = []
    for inv in candidates:
        if only_visible and not _invoice_visible(inv):
            continue
        state = reminder_state(inv, today)
        if state:
            rows.append({"invoice": inv, **state})
    rows.sort(key=lambda r: (not r["due_now"], -r["overdue"], r["invoice"].invoice_number))
    return rows


def due_reminder_count():
    """How many reminders are waiting to be sent (for the menu badge); 0 if the user cannot send."""
    if not current_user.is_authenticated or not user_has_permission(current_user, "send_invoices"):
        return 0
    cutoff = date.today() - timedelta(days=REMINDER_AFTER_DAYS)
    count = 0
    for inv in Invoice.query.filter(Invoice.status == "Sent").all():
        if inv.due_date and inv.due_date > cutoff:
            continue
        if not _invoice_visible(inv):
            continue
        state = reminder_state(inv)
        if state and state["due_now"]:
            count += 1
    return count


def _log(invoice, kind, channel, recipient, subject, status, error=None):
    entry = InvoiceSend(invoice_id=invoice.id, kind=kind, channel=channel, recipient=(recipient or "")[:200],
                        subject=(subject or "")[:300], status=status, error=error, sent_by_id=current_user.id)
    db.session.add(entry)
    return entry


def _sendable(invoice):
    """None if this invoice can go out, else the reason it cannot."""
    if invoice.status == "Cancelled":
        return "This invoice is cancelled, so it cannot be sent."
    if not invoice.lines or invoice.total <= 0:
        return "Add at least one line item to the invoice before sending it."
    return None


def _html_body(text):
    paragraphs = "".join(f"<p style='margin:0 0 10px'>{escape(p).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", text.strip()))
    return f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2933'>{paragraphs}</div>"


def _back(invoice_id, anchor="send"):
    return redirect(url_for("invoicing.view_invoice", invoice_id=invoice_id) + f"#{anchor}")


def send_context(invoice):
    """Everything the invoice page's 'Send to client' card needs."""
    subject, message = default_invoice_email(invoice)
    return {
        "email_ready": emailer.is_configured(), "send_allowed": user_has_permission(current_user, "send_invoices"),
        "send_to": invoice.client.email or "", "send_subject": subject, "send_message": message,
        "send_phone": invoice.client.phone or "", "send_whatsapp": default_invoice_whatsapp(invoice),
        "reminder": reminder_state(invoice), "sendable_reason": _sendable(invoice),
    }


# -------------------------------------------------------------- the PDF itself

@invoice_send_bp.route("/<int:invoice_id>/pdf")
@login_required
def invoice_pdf(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    _ensure_invoice_access(invoice)
    return Response(build_invoice_pdf(invoice, FIRM_LETTERHEAD), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{invoice_filename(invoice)}"'})


@public_invoice_bp.route("/share/invoice/<token>")
def public_pdf(token):
    """The private link sent to clients (WhatsApp): the invoice PDF, no sign-in."""
    if not token or len(token) < 20:
        abort(404)
    invoice = Invoice.query.filter_by(share_token=token).first()
    if not invoice or invoice.status == "Cancelled":
        abort(404)
    return Response(build_invoice_pdf(invoice, FIRM_LETTERHEAD), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{invoice_filename(invoice)}"',
                             "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "private, no-store"})


# -------------------------------------------------------------------- sending

def _send_invoice_email(invoice, to, cc, subject, message, kind="invoice"):
    ok, error = emailer.send_email(
        to, subject, message, html=_html_body(message), cc=cc,
        attachments=[(invoice_filename(invoice), build_invoice_pdf(invoice, FIRM_LETTERHEAD), "application/pdf")])
    _log(invoice, kind, "email", ", ".join(to + cc), subject, "sent" if ok else "failed", None if ok else error)
    return ok, error


@invoice_send_bp.route("/<int:invoice_id>/send/email", methods=["POST"])
@login_required
def send_email(invoice_id):
    invoice = _guard(invoice_id)
    if not emailer.is_configured():
        flash("Email is not set up yet. Ask an Admin to add the email service settings (see the note on this card).", "danger")
        return _back(invoice_id)
    reason = _sendable(invoice)
    if reason:
        flash(reason, "danger")
        return _back(invoice_id)
    to = emailer.split_addresses(request.form.get("to"))
    cc = emailer.split_addresses(request.form.get("cc"))
    subject = (request.form.get("subject") or "").strip() or default_invoice_email(invoice)[0]
    message = (request.form.get("message") or "").strip()
    if not to or not all(emailer.valid_address(a) for a in to + cc):
        flash("Enter a valid email address for the client.", "danger")
        return _back(invoice_id)
    if not message:
        flash("The message cannot be empty.", "danger")
        return _back(invoice_id)
    ok, error = _send_invoice_email(invoice, to, cc, subject, message)
    if ok:
        if invoice.status == "Draft" and request.form.get("mark_sent"):
            invoice.status = "Sent"
        db.session.commit()
        flash(f"Invoice {invoice.invoice_number} emailed to {', '.join(to)}.", "success")
    else:
        db.session.commit()
        flash(f"The email was not sent. {error}", "danger")
    return _back(invoice_id)


@invoice_send_bp.route("/<int:invoice_id>/send/whatsapp", methods=["POST"])
@login_required
def send_whatsapp(invoice_id):
    invoice = _guard(invoice_id)
    reason = _sendable(invoice)
    if reason:
        flash(reason, "danger")
        return _back(invoice_id)
    phone = normalize_phone(request.form.get("phone"))
    if not phone:
        flash("Enter the client's WhatsApp number, with the country code if it is not a Zimbabwe number.", "danger")
        return _back(invoice_id)
    message = (request.form.get("message") or "").strip() or default_invoice_whatsapp(invoice)
    link = share_url(invoice)
    text = message.replace("{link}", link) if "{link}" in message else f"{message}\n\n{link}"
    _log(invoice, "invoice", "whatsapp", f"+{phone}", f"Invoice {invoice.invoice_number}", "opened")
    if invoice.status == "Draft" and request.form.get("mark_sent"):
        invoice.status = "Sent"
    db.session.commit()
    return redirect(f"https://wa.me/{phone}?text={quote(text)}")


# ------------------------------------------------------------------ reminders

def default_reminder_email(invoice):
    overdue = days_overdue(invoice)
    due = due_date_of(invoice).strftime("%d %b %Y")
    lines = [
        f"Dear {_greeting_name(invoice)},", "",
        f"Our records show that invoice {invoice.invoice_number} for {money(invoice, invoice.total)}, due on {due}, "
        f"is now {overdue} days overdue. The amount still outstanding is {money(invoice, invoice.balance)}.", "",
        "A copy of the invoice is attached. If you have already paid, please send us the proof of payment so we can update "
        "your account. If there is a query on the invoice, please let us know and we will resolve it promptly.", "",
    ]
    if FIRM_LETTERHEAD.get("bank_details"):
        lines += ["Payment details:", FIRM_LETTERHEAD["bank_details"], ""]
    lines += ["Kind regards,", _sender_signature()]
    return (f"Reminder: invoice {invoice.invoice_number} is {overdue} days overdue", "\n".join(lines))


def default_reminder_whatsapp(invoice):
    return (f"Dear {_greeting_name(invoice)}, a friendly reminder that invoice {invoice.invoice_number} "
            f"({money(invoice, invoice.balance)} outstanding) was due on {due_date_of(invoice).strftime('%d %b %Y')} and is "
            f"now {days_overdue(invoice)} days overdue. You can view it here: {{link}}\n\nIf you have already paid, please "
            f"send us the proof of payment. Thank you. {current_user.name}, {FIRM_LETTERHEAD['name']}")


@invoice_send_bp.route("/reminders")
@login_required
def reminders():
    _require_send_permission()
    rows = reminder_rows()
    for r in rows:
        inv = r["invoice"]
        r["wa_text"] = default_reminder_whatsapp(inv)
    return render_template("invoicing/reminders.html", rows=rows, email_ready=emailer.is_configured(),
                           due_count=sum(1 for r in rows if r["due_now"]), after_days=REMINDER_AFTER_DAYS,
                           every_days=REMINDER_EVERY_DAYS, with_email=sum(1 for r in rows if r["due_now"] and r["invoice"].client.email))


def _reminder_guard(invoice_id):
    invoice = _guard(invoice_id)
    if reminder_state(invoice) is None:
        flash("That invoice is not in the reminder cycle (it is paid, not yet issued, or less than 30 days overdue).", "info")
        return invoice, redirect(url_for("invoice_send.reminders"))
    return invoice, None


@invoice_send_bp.route("/<int:invoice_id>/reminder/email", methods=["POST"])
@login_required
def reminder_email(invoice_id):
    invoice, stop = _reminder_guard(invoice_id)
    if stop:
        return stop
    if not emailer.is_configured():
        flash("Email is not set up yet. Ask an Admin to add the email service settings.", "danger")
        return redirect(url_for("invoice_send.reminders"))
    to = emailer.split_addresses(request.form.get("to") or invoice.client.email)
    if not to or not all(emailer.valid_address(a) for a in to):
        flash(f"{invoice.client.name} has no valid email address on file. Add one on the client page, or use WhatsApp.", "danger")
        return redirect(url_for("invoice_send.reminders"))
    subject, message = default_reminder_email(invoice)
    ok, error = _send_invoice_email(invoice, to, [], subject, message, kind="reminder")
    db.session.commit()
    flash(f"Reminder for {invoice.invoice_number} emailed to {', '.join(to)}." if ok else f"The reminder was not sent. {error}",
          "success" if ok else "danger")
    return redirect(url_for("invoice_send.reminders"))


@invoice_send_bp.route("/<int:invoice_id>/reminder/whatsapp", methods=["POST"])
@login_required
def reminder_whatsapp(invoice_id):
    invoice, stop = _reminder_guard(invoice_id)
    if stop:
        return stop
    phone = normalize_phone(request.form.get("phone") or invoice.client.phone)
    if not phone:
        flash(f"{invoice.client.name} has no usable WhatsApp number on file. Add a phone number on the client page.", "danger")
        return redirect(url_for("invoice_send.reminders"))
    text = default_reminder_whatsapp(invoice).replace("{link}", share_url(invoice))
    _log(invoice, "reminder", "whatsapp", f"+{phone}", f"Reminder {invoice.invoice_number}", "opened")
    db.session.commit()
    return redirect(f"https://wa.me/{phone}?text={quote(text)}")


@invoice_send_bp.route("/<int:invoice_id>/reminder/mark", methods=["POST"])
@login_required
def reminder_mark(invoice_id):
    """Record a reminder given another way (a phone call, a visit) so the weekly clock restarts."""
    invoice, stop = _reminder_guard(invoice_id)
    if stop:
        return stop
    _log(invoice, "reminder", "manual", "", f"Reminder {invoice.invoice_number} (done outside the app)", "sent")
    db.session.commit()
    flash(f"Recorded as reminded. The next reminder for {invoice.invoice_number} is due in {REMINDER_EVERY_DAYS} days.", "success")
    return redirect(url_for("invoice_send.reminders"))


@invoice_send_bp.route("/<int:invoice_id>/reminder/pause", methods=["POST"])
@login_required
def reminder_pause(invoice_id):
    invoice = _guard(invoice_id)
    invoice.reminders_paused = not invoice.reminders_paused
    db.session.commit()
    flash(("Reminders paused for " if invoice.reminders_paused else "Reminders resumed for ") + invoice.invoice_number + ".", "success")
    return redirect(request.referrer or url_for("invoice_send.reminders"))


@invoice_send_bp.route("/reminders/send-all", methods=["POST"])
@login_required
def reminders_send_all():
    """Email every reminder that is due now (to clients with an email address)."""
    _require_send_permission()
    if not emailer.is_configured():
        flash("Email is not set up yet. Ask an Admin to add the email service settings.", "danger")
        return redirect(url_for("invoice_send.reminders"))
    sent = failed = no_email = 0
    for row in reminder_rows():
        if not row["due_now"]:
            continue
        inv = row["invoice"]
        to = emailer.split_addresses(inv.client.email)
        if not to or not all(emailer.valid_address(a) for a in to):
            no_email += 1
            continue
        subject, message = default_reminder_email(inv)
        ok, _error = _send_invoice_email(inv, to, [], subject, message, kind="reminder")
        sent += ok
        failed += (not ok)
        db.session.commit()
    parts = [f"{sent} reminder{'s' if sent != 1 else ''} emailed"]
    if failed:
        parts.append(f"{failed} failed (see each invoice's history)")
    if no_email:
        parts.append(f"{no_email} skipped because the client has no email address")
    flash(". ".join(parts) + ".", "danger" if failed and not sent else "success")
    return redirect(url_for("invoice_send.reminders"))
