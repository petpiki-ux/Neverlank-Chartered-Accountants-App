"""Internal messaging (intranet): a simple firm-wide inbox so a Partner or
anyone else can send a message to one or more team members, or broadcast to
all active staff, without leaving the app. Separate from the per-engagement
review Queries in engagements.py - those are tied to a specific workpaper
section, these are just person-to-person (or person-to-everyone) notes.

Supports Reply and Forward (both just prefill the compose form - there's no
separate server-side thread model) and Recall: a sender can pull a message
back, but only for recipients who hadn't opened it yet. Anyone who already
read it before the recall keeps it.
"""
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort

from flask_login import login_required, current_user

from extensions import db
from models import User, Message, MessageRecipient

messages_bp = Blueprint("messages", __name__, url_prefix="/messages")


def _strip_prefixes(subject):
    """Peel off any existing Re:/Fwd: prefixes so replying to a reply, or
    forwarding a forward, doesn't pile up into "Re: Re: Fwd: Re: ..."."""
    subject = (subject or "").strip()
    while True:
        lowered = subject.lower()
        if lowered.startswith("re:"):
            subject = subject[3:].strip()
        elif lowered.startswith("fwd:"):
            subject = subject[4:].strip()
        else:
            return subject


def _quoted_original(original, label):
    when = original.created_at.strftime("%d %b %Y %H:%M") if original.created_at else "—"
    who = original.sender.name if original.sender else "—"
    return (
        f"\n\n---------- {label} message ----------\n"
        f"From: {who}\n"
        f"Date: {when}\n"
        f"Subject: {original.subject}\n\n"
        f"{original.body}"
    )


def _can_view_message(message, recipient_row):
    """Same access rule as view_message below - the sender, or a recipient
    who hasn't had it recalled out from under them - re-used when preparing
    a Reply/Forward so a recalled/inaccessible message can't be quoted."""
    if message.sender_id == current_user.id:
        return True
    return recipient_row is not None and not recipient_row.is_recalled


@messages_bp.route("/")
@login_required
def inbox():
    recipient_rows = (
        MessageRecipient.query.join(Message)
        .filter(MessageRecipient.user_id == current_user.id, MessageRecipient.recalled_at.is_(None))
        .order_by(Message.created_at.desc())
        .all()
    )
    return render_template("messages/inbox.html", recipient_rows=recipient_rows)


@messages_bp.route("/sent")
@login_required
def sent():
    sent_messages = (
        Message.query.filter_by(sender_id=current_user.id)
        .order_by(Message.created_at.desc())
        .all()
    )
    return render_template("messages/sent.html", sent_messages=sent_messages)


@messages_bp.route("/new", methods=["GET", "POST"])
@login_required
def compose():
    people = User.query.filter_by(is_active_flag=True).filter(User.id != current_user.id).order_by(User.name).all()

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        broadcast_all = request.form.get("broadcast_all") == "on"
        recipient_ids = request.form.getlist("recipient_ids")

        if not subject or not body:
            flash("Please enter a subject and a message.", "danger")
            return render_template(
                "messages/compose.html",
                people=people,
                prefill_to=recipient_ids,
                prefill_subject=subject,
                prefill_body=body,
                prefill_broadcast=broadcast_all,
            )

        if broadcast_all:
            recipients = User.query.filter_by(is_active_flag=True).filter(User.id != current_user.id).all()
        else:
            recipients = User.query.filter(User.id.in_(recipient_ids)).all() if recipient_ids else []

        if not recipients:
            flash("Please choose at least one recipient, or tick \"Send to all active staff\".", "danger")
            return render_template(
                "messages/compose.html",
                people=people,
                prefill_to=recipient_ids,
                prefill_subject=subject,
                prefill_body=body,
                prefill_broadcast=broadcast_all,
            )

        message = Message(sender_id=current_user.id, subject=subject, body=body)
        db.session.add(message)
        db.session.flush()  # get message.id before adding recipients
        for person in recipients:
            db.session.add(MessageRecipient(message_id=message.id, user_id=person.id))
        db.session.commit()
        flash(f"Message sent to {len(recipients)} recipient(s).", "success")
        return redirect(url_for("messages.sent"))

    # GET: either a blank compose form, or one prefilled as a Reply/Forward
    # to an existing message via ?in_reply_to=<id>&mode=reply|forward.
    prefill_to = []
    prefill_subject = request.args.get("subject", "")
    prefill_body = ""
    prefill_broadcast = False

    to_arg = request.args.get("to", "")
    if to_arg:
        prefill_to = [to_arg]

    in_reply_to = request.args.get("in_reply_to", type=int)
    mode = request.args.get("mode", "reply")
    if in_reply_to:
        original = Message.query.get(in_reply_to)
        if original:
            recipient_row = MessageRecipient.query.filter_by(
                message_id=original.id, user_id=current_user.id
            ).first()
            if _can_view_message(original, recipient_row):
                bare_subject = _strip_prefixes(original.subject)
                if mode == "forward":
                    prefill_subject = f"Fwd: {bare_subject}"
                    prefill_body = _quoted_original(original, "Forwarded")
                    prefill_to = []
                else:
                    prefill_subject = f"Re: {bare_subject}"
                    prefill_body = _quoted_original(original, "Original")
                    if original.sender_id != current_user.id:
                        prefill_to = [str(original.sender_id)]

    return render_template(
        "messages/compose.html",
        people=people,
        prefill_to=prefill_to,
        prefill_subject=prefill_subject,
        prefill_body=prefill_body,
        prefill_broadcast=prefill_broadcast,
    )


@messages_bp.route("/<int:message_id>")
@login_required
def view_message(message_id):
    message = Message.query.get_or_404(message_id)
    recipient_row = MessageRecipient.query.filter_by(message_id=message.id, user_id=current_user.id).first()
    is_sender = message.sender_id == current_user.id
    # Messages are private: only the sender or one of its recipients may
    # view it - unlike client/engagement data, this isn't shared firm-wide.
    if not recipient_row and not is_sender:
        abort(403)
    if recipient_row and recipient_row.is_recalled:
        # Recalled before this recipient opened it - show a placeholder,
        # never the body, and don't mark it read.
        return render_template("messages/recalled.html", message=message)
    if recipient_row and not recipient_row.read_at:
        recipient_row.read_at = datetime.utcnow()
        db.session.commit()
    return render_template("messages/view.html", message=message, recipient_row=recipient_row, is_sender=is_sender)


@messages_bp.route("/<int:message_id>/recall", methods=["POST"])
@login_required
def recall_message(message_id):
    message = Message.query.get_or_404(message_id)
    if message.sender_id != current_user.id:
        abort(403)
    now = datetime.utcnow()
    recalled_count = 0
    already_read_count = 0
    for recipient in message.recipients:
        if recipient.read_at:
            already_read_count += 1
        elif not recipient.recalled_at:
            recipient.recalled_at = now
            recalled_count += 1
    db.session.commit()
    if recalled_count and already_read_count:
        flash(
            f"Recalled for {recalled_count} recipient(s) who hadn't read it yet. "
            f"{already_read_count} had already read it and will still see it.",
            "info",
        )
    elif recalled_count:
        flash(f"Recalled for all {recalled_count} recipient(s) - none had read it yet.", "success")
    else:
        flash("Too late to recall - every recipient had already read this message.", "danger")
    return redirect(url_for("messages.view_message", message_id=message.id))
