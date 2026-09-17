"""Internal messaging (intranet): a simple firm-wide inbox so a Partner or
anyone else can send a message to one or more team members, or broadcast to
all active staff, without leaving the app. Separate from the per-engagement
review Queries in engagements.py - those are tied to a specific workpaper
section, these are just person-to-person (or person-to-everyone) notes.
"""
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import User, Message, MessageRecipient

messages_bp = Blueprint("messages", __name__, url_prefix="/messages")


@messages_bp.route("/")
@login_required
def inbox():
    recipient_rows = (
        MessageRecipient.query.join(Message)
        .filter(MessageRecipient.user_id == current_user.id)
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
    to_arg = request.args.get("to", "")
    prefill_to = [to_arg] if to_arg else []
    prefill_subject = request.args.get("subject", "")
    prefill_body = ""
    prefill_broadcast = False

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
    # Messages are private: only the sender or one of its recipients may
    # view it - unlike client/engagement data, this isn't shared firm-wide.
    if not recipient_row and message.sender_id != current_user.id:
        from flask import abort
        abort(403)
    if recipient_row and not recipient_row.read_at:
        recipient_row.read_at = datetime.utcnow()
        db.session.commit()
    return render_template("messages/view.html", message=message, recipient_row=recipient_row)
