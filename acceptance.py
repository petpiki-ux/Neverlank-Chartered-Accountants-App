"""Client Acceptance & Continuance - a pre-engagement gate.

Before Understanding the Entity (and every later tab) can be opened on an
engagement that requires it (Engagement.acceptance_required - True for
every engagement created since this feature shipped; False, and so
unaffected, for every engagement that predates it), the firm works through
six items - background check, independence assessment, predecessor
communication, competence check, AML/KYC, and a signed engagement letter -
and an Engagement Partner records and signs off an accept/decline decision.
See models.ClientAcceptance and models.engagement_acceptance_cleared for
what "cleared" actually means, and engagements._ensure_engagement_access's
require_accepted parameter for how the rest of the app enforces it.

This module's own routes always pass require_accepted=False when checking
engagement access - the whole point of this blueprint is to let the gate be
worked on before it's cleared, so it can never gate itself.
"""
import os
import uuid
from datetime import datetime

from flask import Blueprint, redirect, url_for, request, flash, current_app, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    Engagement, Document, ClientAcceptance, CLIENT_ACCEPTANCE_DECISIONS,
    ClientAcceptanceChecklistItem, CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
    DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS, FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS,
    RISK_CATEGORIES,
    SanctionsScreening, SANCTIONS_SCREENING_SOURCES, SANCTIONS_SCREENING_RESULTS,
    REVIEWER_ROLES, PARTNER_SIGNOFF_ROLES, user_can_access_engagement,
)

acceptance_bp = Blueprint("acceptance", __name__, url_prefix="/engagements")


def _ensure_access(engagement):
    """Confidentiality-only check (no acceptance gate - see module docstring)."""
    if not user_can_access_engagement(current_user, engagement):
        abort(403)


def _get_or_create(engagement_id):
    record = ClientAcceptance.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = ClientAcceptance(engagement_id=engagement_id)
        db.session.add(record)
        db.session.flush()
    return record


def _touch(record):
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None


def _bool(name):
    return request.form.get(name) in ("on", "true", "1", "yes")


@acceptance_bp.route("/<int:engagement_id>/acceptance/save", methods=["POST"])
@login_required
def save_client_acceptance(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)

    record.background_check_satisfactory = _bool("background_check_satisfactory")
    record.background_check_notes = request.form.get("background_check_notes", "").strip()

    record.independence_threats_identified = _bool("independence_threats_identified")
    record.independence_notes = request.form.get("independence_notes", "").strip()
    record.independence_safeguards = request.form.get("independence_safeguards", "").strip()

    record.predecessor_not_applicable = _bool("predecessor_not_applicable")
    record.predecessor_auditor_name = request.form.get("predecessor_auditor_name", "").strip()
    record.client_permission_obtained = _bool("client_permission_obtained")
    record.predecessor_contacted = _bool("predecessor_contacted")
    record.predecessor_response_notes = request.form.get("predecessor_response_notes", "").strip()

    record.competence_confirmed = _bool("competence_confirmed")
    record.competence_notes = request.form.get("competence_notes", "").strip()

    record.aml_kyc_completed = _bool("aml_kyc_completed")
    record.aml_kyc_notes = request.form.get("aml_kyc_notes", "").strip()

    decision = request.form.get("decision", "Pending").strip()
    record.decision = decision if decision in CLIENT_ACCEPTANCE_DECISIONS else "Pending"
    record.decision_notes = request.form.get("decision_notes", "").strip()

    _touch(record)
    db.session.commit()
    flash("Client Acceptance & Continuance saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


# ---------- Per-section saves ----------
# save_client_acceptance above saves all six narrative sections + the
# decision together in one POST - it's kept for backward compatibility, but
# the Client Acceptance tab's own UI now uses these narrower routes instead,
# one per section, so each section (and its detailed checklist right below
# it) can be worked on and saved independently without a single shared
# <form> spanning the whole tab.

@acceptance_bp.route("/<int:engagement_id>/acceptance/background/save", methods=["POST"])
@login_required
def save_acceptance_background(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.background_check_satisfactory = _bool("background_check_satisfactory")
    record.background_check_notes = request.form.get("background_check_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Background check saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/independence/save", methods=["POST"])
@login_required
def save_acceptance_independence(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.independence_threats_identified = _bool("independence_threats_identified")
    record.independence_notes = request.form.get("independence_notes", "").strip()
    record.independence_safeguards = request.form.get("independence_safeguards", "").strip()
    _touch(record)
    db.session.commit()
    flash("Independence assessment saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/predecessor/save", methods=["POST"])
@login_required
def save_acceptance_predecessor(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.predecessor_not_applicable = _bool("predecessor_not_applicable")
    record.predecessor_auditor_name = request.form.get("predecessor_auditor_name", "").strip()
    record.client_permission_obtained = _bool("client_permission_obtained")
    record.predecessor_contacted = _bool("predecessor_contacted")
    record.predecessor_response_notes = request.form.get("predecessor_response_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Predecessor communication saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/competence/save", methods=["POST"])
@login_required
def save_acceptance_competence(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.competence_confirmed = _bool("competence_confirmed")
    record.competence_notes = request.form.get("competence_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Competence check saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/regulatory/save", methods=["POST"])
@login_required
def save_acceptance_regulatory(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.aml_kyc_completed = _bool("aml_kyc_completed")
    record.aml_kyc_notes = request.form.get("aml_kyc_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Regulatory checks saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/conflict-threat/save", methods=["POST"])
@login_required
def save_acceptance_conflict_threat(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.conflict_threat_clear = _bool("conflict_threat_clear")
    record.conflict_threat_notes = request.form.get("conflict_threat_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Conflict of Interest & Threat Assessment saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/edd/save", methods=["POST"])
@login_required
def save_acceptance_edd(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.edd_completed = _bool("edd_completed")
    record.edd_notes = request.form.get("edd_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Enhanced Due Diligence saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/legal-evidence/save", methods=["POST"])
@login_required
def save_acceptance_legal_evidence(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.legal_evidence_satisfactory = _bool("legal_evidence_satisfactory")
    record.legal_evidence_notes = request.form.get("legal_evidence_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Legal Framework & Evidence Control saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/competence-scope/save", methods=["POST"])
@login_required
def save_acceptance_competence_scope(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    record.competence_scope_confirmed = _bool("competence_scope_confirmed")
    record.competence_scope_notes = request.form.get("competence_scope_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Competence & Scope Realism saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/decision/save", methods=["POST"])
@login_required
def save_acceptance_decision(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    decision = request.form.get("decision", "Pending").strip()
    record.decision = decision if decision in CLIENT_ACCEPTANCE_DECISIONS else "Pending"
    record.decision_notes = request.form.get("decision_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Decision saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/risk-matrix/save", methods=["POST"])
@login_required
def save_acceptance_risk_matrix(engagement_id):
    """Save the Forensic Audit Risk Evaluation Matrix scores (Investigative
    Engagement type only, but not enforced here - the field is simply blank/
    unused on other engagement types). Each score is stored as 1-5, or left
    unset (None) if left blank/invalid, since risk_total_score/risk_assessment
    on the model already treat "not every category scored" as its own state."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    for field, *_rest in RISK_CATEGORIES:
        raw = request.form.get(field, "").strip()
        value = None
        if raw:
            try:
                parsed = int(raw)
            except ValueError:
                parsed = None
            if parsed is not None and 1 <= parsed <= 5:
                value = parsed
        setattr(record, field, value)
    record.risk_scoring_notes = request.form.get("risk_scoring_notes", "").strip()
    _touch(record)
    db.session.commit()
    flash("Risk evaluation matrix saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/engagement-letter/upload", methods=["POST"])
@login_required
def upload_engagement_letter(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose the signed engagement letter file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))

    from engagements import _allowed_file  # reuse the same extension allow-list as every other upload
    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))

    original_name = secure_filename(file.filename)
    stored_name = f"eng{engagement_id}_acceptance_{uuid.uuid4().hex[:10]}_{original_name}"
    file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name))

    doc = Document(
        engagement_id=engagement_id,
        original_filename=original_name,
        stored_filename=stored_name,
        category="Engagement Letter",
        uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.flush()

    record.engagement_letter_document_id = doc.id
    record.engagement_letter_signed_at = datetime.utcnow()
    _touch(record)
    db.session.commit()
    flash("Signed engagement letter uploaded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/<int:record_id>/review", methods=["POST"])
@login_required
def review_client_acceptance(record_id):
    record = ClientAcceptance.query.get_or_404(record_id)
    _ensure_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review your own preparation - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/<int:record_id>/unreview", methods=["POST"])
@login_required
def unreview_client_acceptance(record_id):
    record = ClientAcceptance.query.get_or_404(record_id)
    _ensure_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/<int:record_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_client_acceptance(record_id):
    record = ClientAcceptance.query.get_or_404(record_id)
    _ensure_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not record.items_complete:
        flash("All six items (including the signed engagement letter) need to be filled in before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))
    if record.decision == "Pending":
        flash("Record a decision (Accepted or Declined) before signing off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a preparation you did yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    if record.decision == "Accepted":
        flash("Partner sign-off recorded - the engagement is now accepted and every other tab is unlocked.", "success")
    else:
        flash("Partner sign-off recorded - decision was Declined, so the engagement stays locked.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/<int:record_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_client_acceptance(record_id):
    record = ClientAcceptance.query.get_or_404(record_id)
    _ensure_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed - the engagement is locked again until it's re-signed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))


# ---------- Acceptance checklist (tick + comment, feeds the system assessment) ----------

@acceptance_bp.route("/<int:engagement_id>/acceptance/checklist/seed", methods=["POST"])
@login_required
def seed_acceptance_checklist(engagement_id):
    """Populate the checklist with the firm's default starting items - only
    does anything the first time (i.e. while the list is still empty), so
    it's safe to expose as a single button without risking duplicates."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    if record.checklist_items:
        flash("The checklist already has items on it.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))
    default_items = (
        FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS if engagement.type == "Investigative Engagement"
        else DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS
    )
    for order, (section, item_text) in enumerate(default_items, start=1):
        db.session.add(ClientAcceptanceChecklistItem(
            client_acceptance_id=record.id,
            section=section,
            item_text=item_text,
            order=order,
            created_by_id=current_user.id,
        ))
    db.session.commit()
    flash("Default checklist items added - tick and comment on each one.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/checklist/add", methods=["POST"])
@login_required
def add_acceptance_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    item_text = request.form.get("item_text", "").strip()
    if not item_text:
        flash("Enter the checklist item's wording before adding it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))
    max_order = max([i.order for i in record.checklist_items], default=0)
    db.session.add(ClientAcceptanceChecklistItem(
        client_acceptance_id=record.id,
        section=request.form.get("section", "").strip(),
        item_text=item_text,
        order=max_order + 1,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_acceptance_checklist_item(item_id):
    item = ClientAcceptanceChecklistItem.query.get_or_404(item_id)
    _ensure_access(item.client_acceptance.engagement)
    response = request.form.get("response", "").strip()
    item.response = response if response in CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES else ""
    item.comment = request.form.get("comment", "").strip()
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=item.client_acceptance.engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_acceptance_checklist_item(item_id):
    item = ClientAcceptanceChecklistItem.query.get_or_404(item_id)
    _ensure_access(item.client_acceptance.engagement)
    engagement_id = item.client_acceptance.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


# ---------- Sanctions & adverse notice screening ----------
# Screens each key individual (directors, beneficial owners, authorised
# signatories, and - on an Investigative Engagement - suspects/targets/key
# witnesses) against the UN, OFAC and EU sanctions lists, and against
# adverse notices from the RBZ and the FIU. See models.SanctionsScreening
# and ClientAcceptance.screening_assessment - purely advisory, like the
# acceptance checklist above, so it never blocks the decision/sign-off
# workflow on its own; a confirmed hit is surfaced for the Partner to act on.

@acceptance_bp.route("/<int:engagement_id>/acceptance/screening/add", methods=["POST"])
@login_required
def add_sanctions_screening(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    individual_name = request.form.get("individual_name", "").strip()
    if not individual_name:
        flash("Enter the individual's name before adding them for screening.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))
    max_order = max([s.order for s in record.screenings], default=0)
    db.session.add(SanctionsScreening(
        client_acceptance_id=record.id,
        individual_name=individual_name,
        role_description=request.form.get("role_description", "").strip(),
        order=max_order + 1,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Individual added for sanctions/adverse notice screening.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/screening/<int:screening_id>/update", methods=["POST"])
@login_required
def update_sanctions_screening(screening_id):
    screening = SanctionsScreening.query.get_or_404(screening_id)
    _ensure_access(screening.client_acceptance.engagement)
    for field, _label in SANCTIONS_SCREENING_SOURCES:
        value = request.form.get(field, "Not Checked").strip()
        setattr(screening, field, value if value in SANCTIONS_SCREENING_RESULTS else "Not Checked")
    screening.notes = request.form.get("notes", "").strip()
    screening.role_description = request.form.get("role_description", screening.role_description or "").strip()
    screening.screened_by_id = current_user.id
    screening.screened_at = datetime.utcnow()
    db.session.commit()
    flash("Screening result saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=screening.client_acceptance.engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/screening/<int:screening_id>/delete", methods=["POST"])
@login_required
def delete_sanctions_screening(screening_id):
    screening = SanctionsScreening.query.get_or_404(screening_id)
    _ensure_access(screening.client_acceptance.engagement)
    engagement_id = screening.client_acceptance.engagement_id
    db.session.delete(screening)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))
