"""Sign as Managing Partner: the confirmation step before the Managing Partner's e-signature appears on a document.

A document prints unsigned until an Admin (the firm's Managing Partner signs in as an Admin) presses "Sign as Managing Partner" on it. The
signature then shows on the printed page, the PDF and the Word file, with the date it was applied. If the document's amount or wording is changed
afterwards the signature stops showing until it is signed again (brand_docs.content_key). Signing can be undone by an Admin.
"""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

import brand_docs
from extensions import db
from models import ClientAdvance, CreditNote, Invoice, Quotation, Receipt, WorkpaperNarrative, ZimraReply

mpsign_bp = Blueprint("mpsign", __name__, url_prefix="/sign")

# kind -> (model, label, where to go back to when the request has no referrer)
KINDS = {
    "quotation": (Quotation, "quotation", lambda d: url_for("quotations.view_quotation", quotation_id=d.id)),
    "invoice": (Invoice, "invoice", lambda d: url_for("invoicing.view_invoice", invoice_id=d.id)),
    "credit_note": (CreditNote, "credit note", lambda d: url_for("invoicing.view_invoice", invoice_id=d.invoice_id)),
    "receipt": (Receipt, "receipt", lambda d: url_for("receipts.view_receipt", receipt_id=d.id)),
    "advance": (ClientAdvance, "advance receipt", lambda d: url_for("advances.view_advance", advance_id=d.id)),
    "zimra_reply": (ZimraReply, "letter", lambda d: url_for("zimra.view_reply", reply_id=d.id)),
    "report_to_management": (WorkpaperNarrative, "Report to Management letter",
                             lambda d: url_for("engagements.view_engagement", engagement_id=d.engagement_id, tab="finalisation")),
}


def is_signer(user):
    """Only an Admin (the Managing Partner) can apply or remove the signature."""
    return bool(user and user.is_authenticated and user.role == "admin")


def can_sign(doc):
    """(ok, reason) - whether this document can be signed right now."""
    if getattr(doc, "is_void", False):
        return False, "A voided document cannot be signed."
    if type(doc).__name__ == "ZimraReply" and not brand_docs.is_managing_partner(doc.signatory):
        return False, "This letter is signed by someone other than the Managing Partner. Change 'Signed by' to the Managing Partner first."
    if type(doc).__name__ == "WorkpaperNarrative" and doc.kind != "report_to_management":
        return False, "Only the Report to Management letter carries the signature."
    if not brand_docs.signature_bytes():
        return False, "The signature image is missing from the server."
    return True, ""


def _load(kind, doc_id):
    if kind not in KINDS:
        abort(404)
    model = KINDS[kind][0]
    return model.query.get_or_404(doc_id)


def _back(kind, doc):
    return request.referrer or KINDS[kind][2](doc)


@mpsign_bp.route("/<kind>/<int:doc_id>", methods=["POST"])
@login_required
def sign(kind, doc_id):
    if not is_signer(current_user):
        abort(403)
    doc = _load(kind, doc_id)
    ok, reason = can_sign(doc)
    if not ok:
        flash(reason, "danger")
        return redirect(_back(kind, doc))
    doc.mp_signed_by_id = current_user.id
    doc.mp_signed_at = datetime.utcnow()
    doc.mp_signed_key = brand_docs.content_key(doc)
    db.session.commit()
    flash(f"The {KINDS[kind][1]} now carries the Managing Partner's signature.", "success")
    return redirect(_back(kind, doc))


@mpsign_bp.route("/report_to_management/engagement/<int:engagement_id>", methods=["POST"])
@login_required
def sign_report_to_management(engagement_id):
    """The Report to Management letter's wording may not have been saved yet (it is seeded with default wording), so create its row first."""
    if not is_signer(current_user):
        abort(403)
    from engagements import _get_or_seed_workpaper_narrative
    from models import Engagement
    engagement = Engagement.query.get_or_404(engagement_id)
    narrative = _get_or_seed_workpaper_narrative(engagement_id, "report_to_management", engagement)
    if narrative.id is None:
        db.session.add(narrative)
        db.session.flush()
    ok, reason = can_sign(narrative)
    if not ok:
        db.session.rollback()
        flash(reason, "danger")
    else:
        narrative.mp_signed_by_id = current_user.id
        narrative.mp_signed_at = datetime.utcnow()
        narrative.mp_signed_key = brand_docs.content_key(narrative)
        db.session.commit()
        flash("The Report to Management letter now carries the Managing Partner's signature. Generate it again to file a signed copy.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@mpsign_bp.route("/<kind>/<int:doc_id>/remove", methods=["POST"])
@login_required
def unsign(kind, doc_id):
    if not is_signer(current_user):
        abort(403)
    doc = _load(kind, doc_id)
    doc.mp_signed_by_id = None
    doc.mp_signed_at = None
    doc.mp_signed_key = None
    db.session.commit()
    flash(f"The signature was removed from the {KINDS[kind][1]}.", "info")
    return redirect(_back(kind, doc))
