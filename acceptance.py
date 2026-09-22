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
    BUSINESS_IT_ACCEPTANCE_CHECKLIST_ITEMS,
    RISK_CATEGORIES,
    SanctionsScreening, SANCTIONS_SCREENING_SOURCES, SANCTIONS_SCREENING_RESULTS,
    SANCTIONS_AUTO_SOURCES, REGULATORY_NOTICE_SOURCES, RegulatoryNotice,
    ClientKeyPerson, AcceptanceFlagReview, ACCEPTANCE_FLAG_STATUSES,
    REVIEWER_ROLES, PARTNER_SIGNOFF_ROLES, user_can_access_engagement, user_has_permission,
)
import sanctions_data

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


@acceptance_bp.route("/acceptance/<int:record_id>/flag/review", methods=["POST"])
@login_required
def review_acceptance_flag(record_id):
    """Record a Disregard/Consider call on one specific issue in the
    Decision section's flagged-issues summary (see ClientAcceptance.
    flagged_issues / AcceptanceFlagReview). Purely a record for the file -
    it never touches `decision` and never gates the partner sign-off."""
    record = ClientAcceptance.query.get_or_404(record_id)
    _ensure_access(record.engagement)
    flag_key = request.form.get("flag_key", "").strip()
    status = request.form.get("status", "").strip()
    if not flag_key or status not in ACCEPTANCE_FLAG_STATUSES:
        flash("Couldn't record that review - try again.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))
    review = AcceptanceFlagReview.query.filter_by(client_acceptance_id=record.id, flag_key=flag_key).first()
    if not review:
        review = AcceptanceFlagReview(client_acceptance_id=record.id, flag_key=flag_key)
        db.session.add(review)
    review.status = status
    review.note = request.form.get("note", "").strip()
    review.reviewed_by_id = current_user.id
    review.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f"Marked \"{status}\".", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/flag/<int:review_id>/clear", methods=["POST"])
@login_required
def clear_acceptance_flag_review(review_id):
    """Undo a Disregard/Consider call, putting the issue back to "not yet
    reviewed" - e.g. after a mis-click."""
    review = AcceptanceFlagReview.query.get_or_404(review_id)
    _ensure_access(review.client_acceptance.engagement)
    engagement_id = review.client_acceptance.engagement_id
    db.session.delete(review)
    db.session.commit()
    flash("Review cleared.", "info")
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
    if engagement.type == "Investigative Engagement":
        default_items = FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS
    elif engagement.type == "Business Intelligence and IT Engagements":
        default_items = BUSINESS_IT_ACCEPTANCE_CHECKLIST_ITEMS
    else:
        default_items = DEFAULT_ACCEPTANCE_CHECKLIST_ITEMS
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


@acceptance_bp.route("/<int:engagement_id>/acceptance/screening/add-from-key-person", methods=["POST"])
@login_required
def add_screening_from_key_person(engagement_id):
    """Pull a Director/Shareholder/etc already on file for this client (see
    models.ClientKeyPerson, company_documents.py) into THIS engagement's own
    Sanctions & Adverse Notice Screening list, instead of retyping their
    name - the whole point of that list living on the Client rather than
    the engagement (see models.py's "Company documents & key people"
    section). Only a Confirmed person can be added this way - an
    AI-suggested one hasn't been checked by a person yet, so pulling it
    into a compliance screening list unreviewed would defeat the point of
    the Suggested/Confirmed distinction."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    person = ClientKeyPerson.query.get_or_404(request.form.get("key_person_id", type=int))
    if person.client_id != engagement.client_id:
        abort(403)
    if person.status != "Confirmed":
        flash(f"{person.full_name} hasn't been confirmed yet on the client's Directors & Shareholders list - review and confirm them there first.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))
    record = _get_or_create(engagement_id)
    already = next((s for s in record.screenings if s.individual_name == person.full_name), None)
    if already:
        flash(f"{person.full_name} is already on this engagement's screening list.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))
    max_order = max([s.order for s in record.screenings], default=0)
    db.session.add(SanctionsScreening(
        client_acceptance_id=record.id,
        individual_name=person.full_name,
        role_description=person.role,
        order=max_order + 1,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash(f"{person.full_name} added for screening on this engagement.", "success")
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


# ---------- Automated screening (UN/OFAC/EU cache + RBZ/FIU uploaded notices) ----------
# See sanctions_data.py for the matching itself. Automated matching here only
# ever sets a result to "Clear" or "Potential Match" - it never sets
# "Confirmed Hit", and it never downgrades a result a human has already set
# to "Confirmed Hit" (that stays until a person changes it via the manual
# dropdown on the row). A "Potential Match" always needs a person to look at
# it and decide, same as before this feature existed.

def auto_screen_individual(screening):
    """Run one SanctionsScreening row through the UN/OFAC/EU cache and the
    uploaded RBZ/FIU notice library, updating its five *_result fields (per
    the conservative rule above) and their matching *_auto_notes fields.
    Does not commit - callers do that once, after looping over every row
    they're screening, so "screen all individuals" is a single transaction."""
    for source in SANCTIONS_AUTO_SOURCES:  # UN, OFAC, EU
        field = f"{source.lower()}_result"
        notes_field = f"{source.lower()}_auto_notes"
        matches = sanctions_data.find_matches(screening.individual_name, source)
        if matches:
            setattr(screening, field, "Potential Match")
            setattr(screening, notes_field, "; ".join(
                f"{m['name']} (similarity {m['score']:.0%}{', ref ' + m['reference'] if m['reference'] else ''}{', ' + m['programme'] if m['programme'] else ''})"
                for m in matches
            ))
        else:
            if getattr(screening, field) != "Confirmed Hit":
                setattr(screening, field, "Clear")
            setattr(screening, notes_field, "No match found in the cached list as of the last refresh.")

    for source in REGULATORY_NOTICE_SOURCES:  # RBZ, FIU
        field = f"{source.lower()}_result"
        notes_field = f"{source.lower()}_auto_notes"
        notices = RegulatoryNotice.query.filter_by(source=source).all()
        if not notices:
            setattr(screening, notes_field, "No RBZ/FIU notices uploaded yet to check against - see the Regulatory Notices library.")
            continue  # leave the result field as-is rather than falsely marking it "Clear"
        matches = sanctions_data.search_notices_for_name(screening.individual_name, notices)
        if matches:
            setattr(screening, field, "Potential Match")
            setattr(screening, notes_field, "; ".join(
                f"\"{m['excerpt']}\" in {m['notice'].title} (similarity {m['score']:.0%})" for m in matches[:3]
            ))
        else:
            searchable = [n for n in notices if n.extraction_status == "extracted"]
            if not searchable:
                setattr(screening, notes_field, f"{len(notices)} {source} notice(s) on file, but none have readable text (scanned PDFs) - not automatically searchable.")
                continue
            if getattr(screening, field) != "Confirmed Hit":
                setattr(screening, field, "Clear")
            setattr(screening, notes_field, f"No match found across {len(searchable)} searchable {source} notice(s) on file.")

    screening.auto_screened_at = datetime.utcnow()
    screening.auto_screened_by_id = current_user.id


@acceptance_bp.route("/acceptance/screening/<int:screening_id>/auto-screen", methods=["POST"])
@login_required
def auto_screen_screening(screening_id):
    screening = SanctionsScreening.query.get_or_404(screening_id)
    _ensure_access(screening.client_acceptance.engagement)
    auto_screen_individual(screening)
    db.session.commit()
    flash(f"Auto-screened {screening.individual_name} against UN/OFAC/EU and the uploaded RBZ/FIU notices - review any Potential Match below.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=screening.client_acceptance.engagement_id, tab="acceptance"))


@acceptance_bp.route("/<int:engagement_id>/acceptance/screening/auto-screen-all", methods=["POST"])
@login_required
def auto_screen_all(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_access(engagement)
    record = _get_or_create(engagement_id)
    if not record.screenings:
        flash("No key individuals added for screening yet.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))
    for screening in record.screenings:
        auto_screen_individual(screening)
    db.session.commit()
    flash(f"Auto-screened all {len(record.screenings)} individual(s) - review any Potential Match below.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@acceptance_bp.route("/acceptance/lists/refresh", methods=["POST"])
@login_required
def refresh_sanctions_lists():
    """Manually refresh the cached UN/OFAC/EU sanctions lists (see
    sanctions_data.refresh_all_sources) - the same refresh a scheduled
    `flask refresh-sanctions-lists` job would trigger, available here for
    an on-demand check. Gated by the configurable "Refresh Sanctions Lists"
    permission (partner/admin by default) since it's a firm-wide action,
    not scoped to one engagement."""
    if not user_has_permission(current_user, "manage_sanctions_lists"):
        abort(403)
    results = sanctions_data.refresh_all_sources(user_id=current_user.id)
    ok_sources = [s for s, (ok, _count, _err) in results.items() if ok]
    failed_sources = [s for s, (ok, _count, _err) in results.items() if not ok]
    if ok_sources:
        flash(f"Refreshed: {', '.join(ok_sources)}.", "success")
    if failed_sources:
        flash(f"Could not refresh: {', '.join(failed_sources)} - see the error shown against each below (the previous cache is kept in place).", "danger")
    referrer = request.referrer or url_for("engagements.dashboard")
    return redirect(referrer)
