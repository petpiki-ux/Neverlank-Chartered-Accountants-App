"""ZIMRA Audits & Correspondence - a register of every ZIMRA audit, query,
assessment, objection and payment-plan matter, with the letters sent and
received, response deadlines, amounts assessed vs agreed, payment-plan
instalments and Tax Clearance Certificate (TCC) status.

* A case belongs to a Client and can optionally be linked to the engagement it
  is handled under. A case linked to an engagement follows that engagement's
  confidentiality: only its team (or Admin) can see it. A case with no
  engagement is visible to the whole team, like the client itself.
* Anyone who can see a case can work it (log letters, record payments). Only a
  Partner or Admin can delete a case, a letter or an instalment.
* Incoming letters can carry a response deadline; they stay on the deadline list
  (and in the sidebar badge) until marked as responded to.
* Replies to ZIMRA are drafted here from templates (zimra_letters.py): Draft -> Awaiting approval -> Approved -> Sent.
  Anyone on the case can draft and submit; only a Partner or Admin can approve or return a draft, and a letter with an
  unresolved [CONFIRM: ...] placeholder can be neither approved nor sent. Marking a reply as sent logs it as an outgoing
  letter on the case (with the file) and clears the deadline of the letter it answers.
"""
import os
import uuid
from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, abort, send_from_directory, Response,
)
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from config import Config
from extensions import db
from models import visible_clients, user_can_access_client
from models import (
    Client, Engagement, ZimraCase, ZimraCorrespondence, ZimraInstalment, ZimraReply, ZIMRA_REPLY_STATUSES, ZIMRA_SEND_METHODS,
    ZIMRA_CASE_TYPES, ZIMRA_CASE_STATUSES, ZIMRA_CORRESPONDENCE_KINDS, ZIMRA_DIRECTIONS,
    ZIMRA_PLAN_STATUSES, ZIMRA_TCC_STATUSES, ZIMRA_CURRENCIES, TAX_HEADS,
    user_can_access_engagement,
)
from invoicing import next_sequence_number
import zimra_letters as ZL
import zimra_ai as ZAI
import learning as LS

zimra_bp = Blueprint("zimra", __name__, url_prefix="/zimra")

DUE_SOON_DAYS = 7


# ------------------------------------------------------------------ helpers

def _can_see(case):
    if not user_can_access_client(current_user, case.client_id):
        return False
    return not case.engagement_id or user_can_access_engagement(current_user, case.engagement)


def _ensure_access(case):
    if not _can_see(case):
        abort(403, description=(
            "This ZIMRA case is linked to an engagement you are not on, so you cannot open it. "
            "Ask the engagement's Partner or Manager (or an Admin) to add you to the team."
        ))


def _is_senior():
    return current_user.role in ("partner", "admin")


def _require_senior(what):
    if not _is_senior():
        abort(403, description=f"Only a Partner or Admin can {what}.")


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except (TypeError, ValueError):
        return None


def _parse_amount(value):
    try:
        text = str(value).replace(",", "").strip()
        return round(float(text), 2) if text else None
    except (TypeError, ValueError):
        return None


def _choice(value, options, default=None):
    return value if value in options else default


def _files_dir():
    directory = Config.ZIMRA_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory


def _remove_file(stored_filename):
    if stored_filename:
        path = os.path.join(_files_dir(), stored_filename)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def visible_cases(include_closed=True):
    q = ZimraCase.query
    if not include_closed:
        q = q.filter(ZimraCase.status != "Closed")
    return [c for c in q.all() if _can_see(c)]


def overdue_response_count():
    """Number of letters past their response deadline on cases the current user
    can see - the sidebar badge."""
    today = date.today()
    items = (ZimraCorrespondence.query.join(ZimraCase, ZimraCorrespondence.case_id == ZimraCase.id)
             .filter(ZimraCorrespondence.response_due.isnot(None), ZimraCorrespondence.response_due < today,
                     ZimraCorrespondence.responded_on.is_(None), ZimraCase.status != "Closed").all())
    return sum(1 for c in items if _can_see(c.case))


def awaiting_approval_count():
    """Replies waiting for a Partner's approval on cases the current user can see - only counted for Partners and Admins
    (the people who can act on them): the sidebar badge."""
    if not _is_senior():
        return 0
    items = (ZimraReply.query.join(ZimraCase, ZimraReply.case_id == ZimraCase.id)
             .filter(ZimraReply.status == "Awaiting approval", ZimraCase.status != "Closed").all())
    return sum(1 for r in items if _can_see(r.case))


def _back_to_case(case_id):
    return redirect(url_for("zimra.view_case", case_id=case_id))


def _apply_form(case, form):
    """Copy the posted case fields onto `case`. Returns an error string or None."""
    title = form.get("title", "").strip()
    if not title:
        return "Give the case a short title (for example 'VAT audit 2024')."
    period_from, period_to = _parse_date(form.get("period_from")), _parse_date(form.get("period_to"))
    if period_from and period_to and period_to < period_from:
        return "The period 'to' date is before the 'from' date."
    for field in ("amount_assessed", "penalties_interest", "amount_agreed", "amount_paid"):
        raw = form.get(field, "").strip()
        if raw and _parse_amount(raw) is None:
            return "Amounts must be numbers (for example 12500.00)."
    case.title = title[:200]
    case.case_type = _choice(form.get("case_type"), ZIMRA_CASE_TYPES, "Other")
    case.tax_head = _choice(form.get("tax_head"), TAX_HEADS, None) or (form.get("tax_head", "").strip()[:80] or None)
    case.period_from, case.period_to = period_from, period_to
    case.zimra_reference = form.get("zimra_reference", "").strip()[:100] or None
    case.zimra_officer = form.get("zimra_officer", "").strip()[:150] or None
    case.opened_on = _parse_date(form.get("opened_on"))
    case.description = form.get("description", "").strip() or None
    case.currency = _choice(form.get("currency"), ZIMRA_CURRENCIES, "USD")
    case.amount_assessed = _parse_amount(form.get("amount_assessed"))
    case.penalties_interest = _parse_amount(form.get("penalties_interest"))
    case.amount_agreed = _parse_amount(form.get("amount_agreed"))
    case.amount_paid = _parse_amount(form.get("amount_paid"))
    case.outcome = form.get("outcome", "").strip() or None
    case.plan_status = _choice(form.get("plan_status"), ZIMRA_PLAN_STATUSES, None)
    case.plan_notes = form.get("plan_notes", "").strip()[:300] or None
    case.tcc_status = _choice(form.get("tcc_status"), ZIMRA_TCC_STATUSES, None)
    case.tcc_number = form.get("tcc_number", "").strip()[:60] or None
    case.tcc_expiry = _parse_date(form.get("tcc_expiry"))
    engagement_id = form.get("engagement_id", "").strip()
    if engagement_id.isdigit():
        eng = Engagement.query.get(int(engagement_id))
        if eng is None or eng.client_id != case.client_id:
            return "That engagement does not belong to this client."
        if not user_can_access_engagement(current_user, eng):
            return "You are not on that engagement's team, so you cannot link a case to it."
        case.engagement_id = eng.id
    else:
        case.engagement_id = None
    return None


def _set_status(case, status):
    case.status = status
    if status == "Closed":
        case.closed_on = case.closed_on or date.today()
    else:
        case.closed_on = None


def _form_context(case, client, error=None):
    return dict(
        case=case, client=client, error=error,
        engagements=[e for e in client.engagements if user_can_access_engagement(current_user, e)],
        case_types=ZIMRA_CASE_TYPES, tax_heads=TAX_HEADS, currencies=ZIMRA_CURRENCIES,
        plan_statuses=ZIMRA_PLAN_STATUSES, tcc_statuses=ZIMRA_TCC_STATUSES,
    )


# ------------------------------------------------------------------ routes

@zimra_bp.route("/")
@login_required
def list_cases():
    status = request.args.get("status", "")
    client_filter = request.args.get("client_id", "")
    type_filter = request.args.get("type", "")
    flag = request.args.get("flag", "")  # overdue | due_soon
    show_closed = request.args.get("closed") == "1"
    q = ZimraCase.query
    if client_filter.isdigit():
        q = q.filter(ZimraCase.client_id == int(client_filter))
    if type_filter in ZIMRA_CASE_TYPES:
        q = q.filter(ZimraCase.case_type == type_filter)
    if status in ZIMRA_CASE_STATUSES:
        q = q.filter(ZimraCase.status == status)
    elif not show_closed:
        q = q.filter(ZimraCase.status != "Closed")
    cases = [c for c in q.order_by(ZimraCase.created_at.desc()).all() if _can_see(c)]
    soon = date.today() + timedelta(days=DUE_SOON_DAYS)
    if flag == "overdue":
        cases = [c for c in cases if c.overdue_deadlines or c.overdue_instalments]
    elif flag == "due_soon":
        cases = [c for c in cases if c.next_deadline and c.next_deadline.response_due <= soon]
    elif flag == "approval":
        cases = [c for c in cases if any(r.status == "Awaiting approval" for r in c.replies)]
    # most urgent first: overdue, then nearest deadline, then newest
    cases.sort(key=lambda c: (not (c.overdue_deadlines or c.overdue_instalments),
                              c.next_deadline.response_due if c.next_deadline else date.max))
    everything = visible_cases(include_closed=False)
    stats = {
        "open": len(everything),
        "overdue": sum(1 for c in everything if c.overdue_deadlines or c.overdue_instalments),
        "due_soon": sum(1 for c in everything if c.next_deadline and not c.next_deadline.is_overdue
                        and c.next_deadline.response_due <= soon),
        "approval": sum(1 for c in everything for r in c.replies if r.status == "Awaiting approval"),
    }
    return render_template(
        "zimra/list.html", cases=cases, stats=stats, statuses=ZIMRA_CASE_STATUSES, case_types=ZIMRA_CASE_TYPES,
        clients=visible_clients(current_user), status_filter=status, client_filter=client_filter,
        type_filter=type_filter, flag=flag, show_closed=show_closed, due_soon_days=DUE_SOON_DAYS,
    )


@zimra_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_case():
    client_id = request.values.get("client_id", "")
    if not client_id.isdigit():
        return render_template("zimra/pick_client.html", clients=visible_clients(current_user))
    client = Client.query.get_or_404(int(client_id))
    case = ZimraCase(client_id=client.id, status="Open", currency="USD", opened_on=date.today())
    engagement_id = request.values.get("engagement_id", "")
    if request.method == "POST":
        error = _apply_form(case, request.form)
        if not error:
            prefix = f"ZC-{date.today().year}-"
            for attempt in (1, 2):
                case.case_number = next_sequence_number(ZimraCase.case_number, prefix)
                case.created_by_id = current_user.id
                db.session.add(case)
                try:
                    db.session.commit()
                    break
                except IntegrityError:
                    db.session.rollback()
                    if attempt == 2:
                        error = "Could not allocate a case number - please try again."
            if not error:
                flash(f"ZIMRA case {case.case_number} created.", "success")
                return _back_to_case(case.id)
    elif engagement_id.isdigit():
        eng = Engagement.query.get(int(engagement_id))
        if eng and eng.client_id == client.id and user_can_access_engagement(current_user, eng):
            case.engagement_id = eng.id
    return render_template("zimra/form.html", **_form_context(case, client, error if request.method == "POST" else None))


@zimra_bp.route("/<int:case_id>")
@login_required
def view_case(case_id):
    case = ZimraCase.query.get_or_404(case_id)
    _ensure_access(case)
    return render_template(
        "zimra/detail.html", case=case, statuses=ZIMRA_CASE_STATUSES, kinds=ZIMRA_CORRESPONDENCE_KINDS,
        directions=ZIMRA_DIRECTIONS, is_senior=_is_senior(), today=date.today(), due_soon_days=DUE_SOON_DAYS,
        reply_templates=ZL.TEMPLATES,
        replies_by_letter={i.id: [r for r in case.replies if r.in_reply_to_id == i.id] for i in case.correspondence},
    )


@zimra_bp.route("/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
def edit_case(case_id):
    case = ZimraCase.query.get_or_404(case_id)
    _ensure_access(case)
    error = None
    if request.method == "POST":
        error = _apply_form(case, request.form)
        if not error:
            db.session.commit()
            flash("Case updated.", "success")
            return _back_to_case(case.id)
        db.session.rollback()
    return render_template("zimra/form.html", **_form_context(case, case.client, error))


@zimra_bp.route("/<int:case_id>/status", methods=["POST"])
@login_required
def set_status(case_id):
    case = ZimraCase.query.get_or_404(case_id)
    _ensure_access(case)
    status = request.form.get("status", "")
    if status not in ZIMRA_CASE_STATUSES:
        flash("Choose a status from the list.", "danger")
        return _back_to_case(case_id)
    _set_status(case, status)
    db.session.commit()
    flash(f"Case marked {status}." if status != "Closed" else "Case closed. Its open deadlines no longer show as due.", "success")
    return _back_to_case(case_id)


@zimra_bp.route("/<int:case_id>/delete", methods=["POST"])
@login_required
def delete_case(case_id):
    case = ZimraCase.query.get_or_404(case_id)
    _ensure_access(case)
    _require_senior("delete a ZIMRA case")
    client_id = case.client_id
    for item in case.correspondence:
        _remove_file(item.stored_filename)
    db.session.delete(case)
    db.session.commit()
    flash("ZIMRA case and its correspondence deleted.", "info")
    return redirect(url_for("zimra.list_cases", client_id=client_id))


# ---- correspondence

@zimra_bp.route("/<int:case_id>/correspondence/add", methods=["POST"])
@login_required
def add_correspondence(case_id):
    case = ZimraCase.query.get_or_404(case_id)
    _ensure_access(case)
    form = request.form
    subject = form.get("subject", "").strip()
    corr_date = _parse_date(form.get("corr_date"))
    direction = _choice(form.get("direction"), ZIMRA_DIRECTIONS)
    if not subject or not corr_date or not direction:
        flash("Enter the date, whether it is incoming or outgoing, and a subject.", "danger")
        return _back_to_case(case_id)
    response_due = _parse_date(form.get("response_due"))
    if response_due and direction != "Incoming from ZIMRA":
        flash("A response deadline only applies to a letter received from ZIMRA.", "danger")
        return _back_to_case(case_id)
    if response_due and response_due < corr_date:
        flash("The response deadline is before the date of the letter.", "danger")
        return _back_to_case(case_id)
    file = request.files.get("file")
    original_name = stored_name = None
    if file and file.filename:
        original_name = secure_filename(file.filename)
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
        if ext not in Config.ALLOWED_EXTENSIONS:
            flash("That file type is not accepted (PDF, Word, Excel, images, text, zip or PowerPoint).", "danger")
            return _back_to_case(case_id)
    item = ZimraCorrespondence(
        case_id=case.id, corr_date=corr_date, direction=direction,
        kind=_choice(form.get("kind"), ZIMRA_CORRESPONDENCE_KINDS, "Other"),
        subject=subject[:250], reference=form.get("reference", "").strip()[:100] or None,
        notes=form.get("notes", "").strip() or None, response_due=response_due,
        uploaded_by_id=current_user.id,
    )
    db.session.add(item)
    db.session.flush()
    if original_name:
        stored_name = f"zc{case.id}_{item.id}_{uuid.uuid4().hex[:8]}_{original_name}"
        file.save(os.path.join(_files_dir(), stored_name))
        item.original_filename, item.stored_filename = original_name, stored_name
    db.session.commit()
    flash("Correspondence logged." + (f" Reply due {response_due.strftime('%d %b %Y')}." if response_due else ""), "success")
    return _back_to_case(case_id)


def _get_item(item_id):
    item = ZimraCorrespondence.query.get_or_404(item_id)
    _ensure_access(item.case)
    return item


@zimra_bp.route("/correspondence/<int:item_id>/download")
@login_required
def download_correspondence(item_id):
    item = _get_item(item_id)
    if not item.stored_filename:
        abort(404)
    return send_from_directory(_files_dir(), item.stored_filename, as_attachment=True,
                               download_name=item.original_filename or item.stored_filename)


@zimra_bp.route("/correspondence/<int:item_id>/responded", methods=["POST"])
@login_required
def mark_responded(item_id):
    item = _get_item(item_id)
    when = _parse_date(request.form.get("responded_on")) or date.today()
    if item.corr_date and when < item.corr_date:
        flash("The reply date is before the date of the letter.", "danger")
        return _back_to_case(item.case_id)
    item.responded_on = when
    db.session.commit()
    flash("Marked as responded to - it is off the deadline list.", "success")
    return _back_to_case(item.case_id)


@zimra_bp.route("/correspondence/<int:item_id>/reopen", methods=["POST"])
@login_required
def reopen_response(item_id):
    item = _get_item(item_id)
    item.responded_on = None
    db.session.commit()
    flash("Back on the deadline list.", "info")
    return _back_to_case(item.case_id)


@zimra_bp.route("/correspondence/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_correspondence(item_id):
    item = _get_item(item_id)
    _require_senior("delete a logged letter")
    case_id = item.case_id
    _remove_file(item.stored_filename)
    for r in ZimraReply.query.filter((ZimraReply.in_reply_to_id == item.id) | (ZimraReply.correspondence_id == item.id)).all():
        if r.in_reply_to_id == item.id:
            r.in_reply_to_id = None
        if r.correspondence_id == item.id:
            r.correspondence_id = None
    db.session.delete(item)
    db.session.commit()
    flash("Entry deleted.", "info")
    return _back_to_case(case_id)


# ---- payment-plan instalments

@zimra_bp.route("/<int:case_id>/instalments/add", methods=["POST"])
@login_required
def add_instalment(case_id):
    case = ZimraCase.query.get_or_404(case_id)
    _ensure_access(case)
    due = _parse_date(request.form.get("due_date"))
    amount = _parse_amount(request.form.get("amount"))
    count = request.form.get("count", "1").strip()
    count = int(count) if count.isdigit() and 1 <= int(count) <= 60 else 1
    if not due or amount is None or amount <= 0:
        flash("Enter the first due date and an instalment amount above zero.", "danger")
        return _back_to_case(case_id)
    y, m = due.year, due.month
    for n in range(count):  # monthly, same day (clamped to month end)
        yy, mm = y + (m - 1 + n) // 12, (m - 1 + n) % 12 + 1
        day = due.day
        while True:
            try:
                d = date(yy, mm, day)
                break
            except ValueError:
                day -= 1
        db.session.add(ZimraInstalment(case_id=case.id, due_date=d, amount=amount))
    db.session.commit()
    flash(f"{count} instalment(s) added.", "success")
    return _back_to_case(case_id)


def _get_instalment(inst_id):
    inst = ZimraInstalment.query.get_or_404(inst_id)
    _ensure_access(inst.case)
    return inst


@zimra_bp.route("/instalments/<int:inst_id>/paid", methods=["POST"])
@login_required
def instalment_paid(inst_id):
    inst = _get_instalment(inst_id)
    paid_on = _parse_date(request.form.get("paid_on")) or date.today()
    if paid_on > date.today():
        flash("The payment date cannot be in the future.", "danger")
        return _back_to_case(inst.case_id)
    if inst.paid_on:
        flash("That instalment is already recorded as paid.", "info")
        return _back_to_case(inst.case_id)
    amount = _parse_amount(request.form.get("paid_amount"))
    inst.paid_on = paid_on
    inst.paid_amount = amount if amount is not None else inst.amount
    case = inst.case
    case.amount_paid = round((case.amount_paid or 0.0) + inst.paid_amount, 2)
    db.session.commit()
    flash("Instalment recorded as paid; the case's amount paid to date is updated.", "success")
    return _back_to_case(inst.case_id)


@zimra_bp.route("/instalments/<int:inst_id>/delete", methods=["POST"])
@login_required
def delete_instalment(inst_id):
    inst = _get_instalment(inst_id)
    _require_senior("delete an instalment")
    case = inst.case
    if inst.paid_on:  # take a recorded payment back out of the amount paid to date
        case.amount_paid = max(0.0, round((case.amount_paid or 0.0) - (inst.paid_amount or 0.0), 2))
    db.session.delete(inst)
    db.session.commit()
    flash("Instalment deleted.", "info")
    return _back_to_case(case.id)


# ------------------------------------------------------------------ replies to ZIMRA (draft -> approve -> send)

def _get_reply(reply_id):
    reply = ZimraReply.query.get_or_404(reply_id)
    _ensure_access(reply.case)
    return reply


def _back_to_reply(reply):
    return redirect(url_for("zimra.view_reply", reply_id=reply.id))


def _incoming_letter(case, raw):
    if str(raw or "").isdigit():
        item = ZimraCorrespondence.query.get(int(raw))
        if item is not None and item.case_id == case.id and item.direction.startswith("Incoming"):
            return item
    return None


def _apply_reply_form(reply, form):
    """Copy the posted letter fields onto the draft. Returns an error string or None."""
    letter_date = _parse_date(form.get("letter_date"))
    subject = form.get("subject", "").strip()
    body = form.get("body", "").replace("\r\n", "\n").strip()
    if not letter_date:
        return "Enter the date of the letter."
    if not subject:
        return "Give the letter a subject line."
    if not body:
        return "The letter has no text."
    reply.letter_date = letter_date
    reply.subject = subject[:300]
    reply.body = body
    reply.our_reference = form.get("our_reference", "").strip()[:100] or None
    reply.addressee = form.get("addressee", "").replace("\r\n", "\n").strip() or ZL.DEFAULT_ADDRESSEE
    reply.salutation = form.get("salutation", "").strip()[:100] or ZL.DEFAULT_SALUTATION
    reply.signatory = form.get("signatory", "").strip()[:150] or None
    reply.signatory_title = form.get("signatory_title", "").strip()[:200] or None
    return None


def _reply_unresolved(reply):
    return ZL.unresolved(reply.subject, reply.body, reply.addressee, reply.salutation, reply.signatory, reply.signatory_title)


@zimra_bp.route("/<int:case_id>/replies/new", methods=["GET", "POST"])
@login_required
def new_reply(case_id):
    case = ZimraCase.query.get_or_404(case_id)
    _ensure_access(case)
    incoming = _incoming_letter(case, request.values.get("in_reply_to"))
    if case.is_closed:
        flash("This case is closed. Change its status first if you need to write to ZIMRA on it.", "danger")
        return _back_to_case(case_id)
    if request.method == "POST":
        key = request.form.get("template", "")
        if key not in ZL.TEMPLATE_BY_KEY:
            flash("Choose a letter template.", "danger")
            return redirect(url_for("zimra.new_reply", case_id=case.id, in_reply_to=incoming.id if incoming else None))
        fields = ZL.new_draft(key, case, incoming, current_user, seq=len(case.replies) + 1)
        reply = ZimraReply(case_id=case.id, in_reply_to_id=incoming.id if incoming else None, status="Draft",
                           created_by_id=current_user.id, **fields)
        reply.log(current_user, "Draft created", f"From the template '{ZL.TEMPLATE_BY_KEY[key]['label']}'")
        db.session.add(reply)
        db.session.commit()
        flash("Draft created. Complete the highlighted [CONFIRM: ...] parts, then submit it for approval.", "success")
        return redirect(url_for("zimra.edit_reply", reply_id=reply.id))
    return render_template("zimra/reply_new.html", case=case, incoming=incoming, templates=ZL.TEMPLATES,
                           suggested=ZL.suggested_template(incoming, case))


@zimra_bp.route("/replies/<int:reply_id>/edit", methods=["GET", "POST"])
@login_required
def edit_reply(reply_id):
    reply = _get_reply(reply_id)
    if not reply.is_editable:
        flash("Only a draft can be edited. Withdraw it from approval first if you need to change it.", "danger")
        return _back_to_reply(reply)
    error = None
    if request.method == "POST":
        error = _apply_reply_form(reply, request.form)
        if not error:
            action = request.form.get("action", "save")
            if action == "submit":
                return _submit(reply)
            reply.log(current_user, "Draft saved")
            db.session.commit()
            left = _reply_unresolved(reply)
            flash("Draft saved." + (f" {len(left)} [CONFIRM: ...] placeholder(s) still to complete." if left else ""), "success")
            return redirect(url_for("zimra.edit_reply", reply_id=reply.id))
        db.session.rollback()
    return render_template("zimra/reply_edit.html", reply=reply, case=reply.case, error=error, left=_reply_unresolved(reply),
                           template=ZL.TEMPLATE_BY_KEY.get(reply.template_key), ai_ready=ZAI.is_configured())


def _submit(reply):
    ai = reply.ai
    if ai is not None and not request.form.get("ai_checked"):
        db.session.commit()  # keep what was typed; only the submission is held back
        flash("Draft saved, but not submitted: this letter was first drafted by AI. Tick the box to confirm you have "
              "checked every fact, figure and legal reference against the case file, then submit.", "danger")
        return redirect(url_for("zimra.edit_reply", reply_id=reply.id))
    left = _reply_unresolved(reply)
    if ai is not None:
        ai["checked_by"], ai["checked_at"] = current_user.name, datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        reply.set_ai(ai)
    reply.status = "Awaiting approval"
    reply.submitted_by_id, reply.submitted_at = current_user.id, datetime.utcnow()
    reply.returned_note = None
    notes = []
    if left:
        notes.append(f"{len(left)} placeholder(s) unresolved")
    if ai is not None:
        notes.append("AI-assisted draft - drafter confirmed the facts and citations were checked")
    reply.log(current_user, "Submitted for approval", "; ".join(notes) or None)
    db.session.commit()
    if left:
        flash(f"Submitted, but {len(left)} [CONFIRM: ...] placeholder(s) are still in the letter. It cannot be approved until they are completed.", "warning")
    else:
        flash("Submitted for a Partner's approval.", "success")
    return _back_to_reply(reply)


@zimra_bp.route("/replies/<int:reply_id>")
@login_required
def view_reply(reply_id):
    reply = _get_reply(reply_id)
    return render_template("zimra/reply_view.html", reply=reply, case=reply.case, left=_reply_unresolved(reply),
                           blocks=ZL.paragraphs(reply.body), is_senior=_is_senior(), today=date.today(),
                           methods=ZIMRA_SEND_METHODS, template=ZL.TEMPLATE_BY_KEY.get(reply.template_key))


@zimra_bp.route("/replies/<int:reply_id>/submit", methods=["POST"])
@login_required
def submit_reply(reply_id):
    reply = _get_reply(reply_id)
    if reply.status != "Draft":
        flash("Only a draft can be submitted for approval.", "danger")
        return _back_to_reply(reply)
    return _submit(reply)


@zimra_bp.route("/replies/<int:reply_id>/approve", methods=["POST"])
@login_required
def approve_reply(reply_id):
    reply = _get_reply(reply_id)
    _require_senior("approve a letter to ZIMRA")
    if reply.status != "Awaiting approval":
        flash("This letter is not waiting for approval.", "danger")
        return _back_to_reply(reply)
    left = _reply_unresolved(reply)
    if left:
        flash(f"Cannot approve: {len(left)} [CONFIRM: ...] placeholder(s) are still in the letter. Return it for completion.", "danger")
        return _back_to_reply(reply)
    reply.status = "Approved"
    reply.approved_by_id, reply.approved_at = current_user.id, datetime.utcnow()
    notes = []
    if reply.submitted_by_id == current_user.id:
        notes.append("Approved by the same person who submitted it")
    if reply.ai is not None:
        notes.append("AI-assisted draft")
        ai = reply.ai
        if ai.get("generated_body"):
            ai["similarity"] = LS.similarity(ai["generated_body"], reply.body)
            reply.set_ai(ai)
            notes.append(f"{round(ai['similarity'] * 100)}% of the AI wording kept")
    reply.log(current_user, "Approved", "; ".join(notes) or None)
    db.session.commit()
    flash("Approved. Download the letter, send it, then mark it as sent here.", "success")
    return _back_to_reply(reply)


@zimra_bp.route("/replies/<int:reply_id>/return", methods=["POST"])
@login_required
def return_reply(reply_id):
    reply = _get_reply(reply_id)
    _require_senior("return a letter to ZIMRA for changes")
    if reply.status != "Awaiting approval":
        flash("This letter is not waiting for approval.", "danger")
        return _back_to_reply(reply)
    note = request.form.get("note", "").strip()
    if not note:
        flash("Say what needs to change so the drafter knows.", "danger")
        return _back_to_reply(reply)
    reply.status = "Draft"
    reply.returned_note = note
    reply.log(current_user, "Returned for changes", note)
    db.session.commit()
    flash("Returned to the drafter with your note.", "info")
    return _back_to_reply(reply)


@zimra_bp.route("/replies/<int:reply_id>/reopen", methods=["POST"])
@login_required
def reopen_reply(reply_id):
    """Take a letter back to Draft (withdraws any approval) so it can be changed."""
    reply = _get_reply(reply_id)
    if reply.status not in ("Awaiting approval", "Approved"):
        flash("Only a letter waiting for, or given, approval can be taken back to draft.", "danger")
        return _back_to_reply(reply)
    was = reply.status
    reply.status = "Draft"
    reply.approved_by_id = reply.approved_at = None
    reply.log(current_user, "Taken back to draft", "Approval withdrawn" if was == "Approved" else "Withdrawn from approval")
    db.session.commit()
    flash("Back to draft. It needs approval again after any change.", "info")
    return redirect(url_for("zimra.edit_reply", reply_id=reply.id))


@zimra_bp.route("/replies/<int:reply_id>/send", methods=["POST"])
@login_required
def send_reply(reply_id):
    reply = _get_reply(reply_id)
    case = reply.case
    if reply.status != "Approved":
        flash("Only an approved letter can be marked as sent.", "danger")
        return _back_to_reply(reply)
    left = _reply_unresolved(reply)
    if left:
        flash("The letter still has [CONFIRM: ...] placeholders. Take it back to draft and complete them.", "danger")
        return _back_to_reply(reply)
    sent_on = _parse_date(request.form.get("sent_on")) or date.today()
    if sent_on > date.today():
        flash("The date sent cannot be in the future.", "danger")
        return _back_to_reply(reply)
    if reply.in_reply_to is not None and sent_on < reply.in_reply_to.corr_date:
        flash("The date sent is before the date of the letter it answers.", "danger")
        return _back_to_reply(reply)
    method = _choice(request.form.get("method"), ZIMRA_SEND_METHODS, "Other")
    upload = request.files.get("file")
    original = stored = None
    if upload and upload.filename:
        original = secure_filename(upload.filename)
        ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
        if ext not in Config.ALLOWED_EXTENSIONS:
            flash("That file type is not accepted (PDF, Word, Excel, images, text, zip or PowerPoint).", "danger")
            return _back_to_reply(reply)
    template = ZL.TEMPLATE_BY_KEY.get(reply.template_key) or {}
    item = ZimraCorrespondence(
        case_id=case.id, corr_date=sent_on, direction="Outgoing to ZIMRA", kind=template.get("kind") or "Response / submission",
        subject=(reply.subject or "Letter to ZIMRA")[:250], reference=reply.our_reference,
        notes=f"Sent by {method}. Approved by {reply.approved_by.name if reply.approved_by else 'a Partner'}.",
        uploaded_by_id=current_user.id)
    db.session.add(item)
    db.session.flush()
    if original:
        stored = f"zc{case.id}_{item.id}_{uuid.uuid4().hex[:8]}_{original}"
        upload.save(os.path.join(_files_dir(), stored))
    else:  # no signed scan supplied: keep the approved letter itself as the record
        original = ZL.letter_filename(reply, "pdf")
        stored = f"zc{case.id}_{item.id}_{uuid.uuid4().hex[:8]}_{original}"
        with open(os.path.join(_files_dir(), stored), "wb") as fh:
            fh.write(ZL.build_pdf(reply))
    item.original_filename, item.stored_filename = original, stored
    reply.correspondence_id = item.id
    reply.status = "Sent"
    reply.sent_on, reply.sent_method, reply.sent_by_id = sent_on, method, current_user.id
    msg = "Marked as sent and logged on the case."
    answered = reply.in_reply_to
    if answered is not None and not answered.responded_on:
        answered.responded_on = sent_on
        msg += " The letter it answers is marked as responded to."
    if case.status in ("Open", "Awaiting client"):
        _set_status(case, "Awaiting ZIMRA")
        msg += " Case status is now Awaiting ZIMRA."
    reply.log(current_user, "Marked as sent", f"{method}, {sent_on.strftime('%d %b %Y')}")
    db.session.commit()
    flash(msg, "success")
    return _back_to_reply(reply)


@zimra_bp.route("/replies/<int:reply_id>/ai-draft", methods=["POST"])
@login_required
def ai_draft_reply(reply_id):
    """Write a first draft of the letter with AI. Replaces the text (the old text is kept so it can be restored)."""
    reply = _get_reply(reply_id)
    back = redirect(url_for("zimra.edit_reply", reply_id=reply.id))
    if not reply.is_editable:
        flash("Only a draft can be redrafted. Withdraw it from approval first.", "danger")
        return _back_to_reply(reply)
    if not request.form.get("confirm"):
        flash("Tick the box to confirm what is sent to the AI service.", "danger")
        return back
    if not ZAI.is_configured():
        flash("AI drafting is not set up yet: the ANTHROPIC_API_KEY setting is missing on the server.", "danger")
        return back
    instructions = request.form.get("instructions", "").strip()
    status, payload = ZAI.generate(reply, instructions, current_user, _files_dir())
    if status != "done":
        flash(f"The AI draft could not be made: {payload}" if status == "error" else "AI drafting is not set up yet.", "danger")
        return back
    meta = payload["meta"]
    meta["prev_body"] = reply.body
    reply.body = payload["body"]
    reply.set_ai(meta)
    LS.mark_used([x["id"] for x in meta.get("lessons_used") or []])
    reply.log(current_user, "AI first draft written",
              f"{meta['model']}; {len(meta['considered'])} library item(s) considered, {len(meta['sources'])} relied on; "
              f"{len(meta['flags'])} point(s) flagged for checking; {len(ZL.unresolved(reply.body))} placeholder(s)"
              + (f"; {len(meta['lessons_used'])} firm standard(s) applied" if meta.get("lessons_used") else ""))
    db.session.commit()
    flash("AI first draft written below. It is a draft only: check every fact, figure and legal reference, complete the "
          "[CONFIRM: ...] parts, then submit it for approval.", "success")
    return back


@zimra_bp.route("/replies/<int:reply_id>/learn", methods=["POST"])
@login_required
def learn_from_reply(reply_id):
    """Partner/Admin: look at how this approved AI-drafted letter was changed and suggest lessons (each still needs approval)."""
    reply = _get_reply(reply_id)
    _require_senior("suggest lessons from a letter")
    if reply.status not in ("Approved", "Sent"):
        flash("Lessons are suggested from a letter once it has been approved.", "info")
        return _back_to_reply(reply)
    ai = reply.ai
    if ai and ai.get("learned_at"):
        flash("Lessons were already suggested from this letter.", "info")
        return _back_to_reply(reply)
    status, payload = LS.learn_from_zimra_reply(reply, current_user)
    if status == "not_ai":
        flash("This letter was not drafted with AI, so there is nothing to compare.", "info")
    elif status == "no_change":
        flash("The approved letter is almost identical to the AI draft - nothing to learn from it.", "info")
    elif status == "not_configured":
        flash("The ANTHROPIC_API_KEY setting is missing on the server, so lessons cannot be suggested.", "danger")
    elif status == "error":
        flash(f"Lessons could not be suggested: {payload}", "danger")
    else:
        reply.log(current_user, "Lessons suggested", f"{len(payload)} new lesson(s) for the Partners to decide")
        db.session.commit()
        if payload:
            flash(f"{len(payload)} lesson(s) suggested. They change nothing until approved under Firm Learning.", "success")
            return redirect(url_for("learning.index", status="Proposed"))
        flash("No general lesson came out of the changes made to this letter.", "info")
    return _back_to_reply(reply)


@zimra_bp.route("/replies/<int:reply_id>/ai-restore", methods=["POST"])
@login_required
def ai_restore_reply(reply_id):
    """Throw the AI draft away and go back to the text that was there before."""
    reply = _get_reply(reply_id)
    ai = reply.ai
    if not reply.is_editable or ai is None:
        flash("There is no AI draft to discard on this letter.", "info")
        return _back_to_reply(reply)
    reply.body = ai.get("prev_body") or reply.body
    reply.set_ai(None)
    reply.log(current_user, "AI draft discarded", "Previous text restored")
    db.session.commit()
    flash("AI draft discarded and the previous text restored.", "info")
    return redirect(url_for("zimra.edit_reply", reply_id=reply.id))


@zimra_bp.route("/replies/<int:reply_id>/delete", methods=["POST"])
@login_required
def delete_reply(reply_id):
    reply = _get_reply(reply_id)
    case_id = reply.case_id
    if reply.status == "Sent":
        flash("A sent letter is part of the case record. A Partner can delete its logged entry under Correspondence.", "danger")
        return _back_to_reply(reply)
    if not (_is_senior() or reply.created_by_id == current_user.id):
        abort(403, description="Only the person who drafted a letter, or a Partner or Admin, can delete it.")
    db.session.delete(reply)
    db.session.commit()
    flash("Draft deleted.", "info")
    return _back_to_case(case_id)


@zimra_bp.route("/replies/<int:reply_id>/download/<fmt>")
@login_required
def download_reply(reply_id, fmt):
    reply = _get_reply(reply_id)
    if fmt == "docx":
        data = ZL.build_docx(reply)
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "pdf":
        data = ZL.build_pdf(reply)
        mime = "application/pdf"
    else:
        abort(404)
    reply.log(current_user, f"Downloaded as {fmt.upper()}")
    db.session.commit()
    return Response(data, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{ZL.letter_filename(reply, fmt)}"'})
