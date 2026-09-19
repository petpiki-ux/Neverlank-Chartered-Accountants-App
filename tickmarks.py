"""Firm-wide tickmark legend (Tickmark model in models.py) - a small shared
list of symbol + meaning pairs (e.g. "TB" = "Agreed to Trial Balance", "V" =
"Vouched to supporting documentation") in the standard audit-workpaper
convention. Any team member can add to it while working through a checklist
(see the inline tickmark picker on the Client Acceptance / Understanding the
Entity / Engagement Checklist / Finalisation Checklist tabs), and it's
printed as a legend page in the Engagement File Summary PDF for whichever
tickmarks were actually used on that engagement.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import (
    Tickmark, EngagementChecklistItem, ClientAcceptanceChecklistItem,
    EntityUnderstandingChecklistItem, FinalisationChecklistItem,
)

tickmarks_bp = Blueprint("tickmarks", __name__, url_prefix="/tickmarks")


@tickmarks_bp.route("/")
@login_required
def list_tickmarks():
    tickmarks = Tickmark.query.order_by(Tickmark.symbol).all()
    return render_template("tickmarks/list.html", tickmarks=tickmarks)


@tickmarks_bp.route("/add", methods=["POST"])
@login_required
def add_tickmark():
    symbol = request.form.get("symbol", "").strip()
    meaning = request.form.get("meaning", "").strip()
    if not symbol or not meaning:
        flash("Please enter both a symbol and its meaning.", "danger")
        return redirect(url_for("tickmarks.list_tickmarks"))
    if Tickmark.query.filter_by(symbol=symbol).first():
        flash(f'A tickmark "{symbol}" already exists.', "danger")
        return redirect(url_for("tickmarks.list_tickmarks"))
    db.session.add(Tickmark(symbol=symbol, meaning=meaning, created_by_id=current_user.id))
    db.session.commit()
    flash("Tickmark added.", "success")
    return redirect(url_for("tickmarks.list_tickmarks"))


@tickmarks_bp.route("/<int:tickmark_id>/update", methods=["POST"])
@login_required
def update_tickmark(tickmark_id):
    tickmark = Tickmark.query.get_or_404(tickmark_id)
    symbol = request.form.get("symbol", "").strip()
    meaning = request.form.get("meaning", "").strip()
    if not symbol or not meaning:
        flash("Please enter both a symbol and its meaning.", "danger")
        return redirect(url_for("tickmarks.list_tickmarks"))
    clash = Tickmark.query.filter(Tickmark.symbol == symbol, Tickmark.id != tickmark.id).first()
    if clash:
        flash(f'A tickmark "{symbol}" already exists.', "danger")
        return redirect(url_for("tickmarks.list_tickmarks"))
    tickmark.symbol = symbol
    tickmark.meaning = meaning
    db.session.commit()
    flash("Tickmark updated.", "success")
    return redirect(url_for("tickmarks.list_tickmarks"))


@tickmarks_bp.route("/<int:tickmark_id>/delete", methods=["POST"])
@login_required
def delete_tickmark(tickmark_id):
    tickmark = Tickmark.query.get_or_404(tickmark_id)
    # Clear the reference from any checklist item using it first, so nothing
    # is left pointing at a tickmark that no longer exists.
    for model in (EngagementChecklistItem, ClientAcceptanceChecklistItem,
                  EntityUnderstandingChecklistItem, FinalisationChecklistItem):
        model.query.filter_by(tickmark_id=tickmark.id).update({"tickmark_id": None})
    db.session.delete(tickmark)
    db.session.commit()
    flash("Tickmark deleted.", "success")
    return redirect(url_for("tickmarks.list_tickmarks"))
