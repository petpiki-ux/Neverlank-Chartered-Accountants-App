"""Tax Advisory & Tax Compliance module - the "Tax" tab shown on any
engagement with Engagement.has_tax_module True (a dedicated Tax Compliance
/ Tax Advisory & Health Check engagement, or any other engagement type
with a Tax service turned on - see models.TAX_SERVICES).

Everything that already had a natural home elsewhere in this app is
reused rather than duplicated - see the module comment at the top of the
"Tax Advisory & Tax Compliance module" section in models.py for the full
list (Risk Register -> RiskItem, Execution Plan -> SubstantiveProcedureArea/
Item, CIT/Deferred Tax computations -> the existing working papers, Tax
Opinion/Health Check -> WorkpaperNarrative, Documents -> the existing
Documents/Filing Index). This file only adds the CRUD routes for the
genuinely new pieces: the entity profile, registrations/clearances/portal
access, the statutory calendar, the analytical review, the tax risk
register entries, the position register, the penalty & interest engine,
information requests, return records, the advisory workflow (research
log/structuring/disputes), the compliance checklist, and the firm-wide
legislative update log and penalty/interest rate table.
"""
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import (
    Engagement, User, RiskItem,
    TaxEntityProfile, TAX_ENTITY_CLASSIFICATIONS,
    TaxRegistration, TAX_REGISTRATION_RECORD_TYPES,
    TaxDeadline,
    TaxAnalyticalReview,
    TaxPosition, TAX_POSITION_CLASSIFICATIONS, TAX_POSITION_APPROVAL_STATUSES,
    PenaltyInterestRate, PenaltyInterestCalculation, PENALTY_INTEREST_RATE_TYPES,
    TaxInformationRequest, TAX_INFO_REQUEST_STATUSES,
    TaxReturnRecord, TAX_RETURN_STATUSES,
    TaxResearchLogEntry, TaxStructuringOption, TaxDispute, TAX_DISPUTE_STAGES,
    LegislativeUpdate, TaxChecklistItem, DEFAULT_TAX_CHECKLIST_ITEMS,
    TAX_HEADS, PARTNER_SIGNOFF_ROLES, REVIEWER_ROLES,
    user_has_permission,
)
from engagements import _ensure_engagement_access

tax_bp = Blueprint("tax", __name__, url_prefix="/tax")


def _tax_redirect(engagement_id):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _parse_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _get_tax_engagement(engagement_id):
    """Fetch + access-check an engagement for a Tax route, and make sure the
    Tax tab is actually turned on for it - every route below is a no-op
    "flash and bounce back" rather than a hard 404/403 if it isn't, since a
    stale bookmark/back-button to a since-disabled Tax service shouldn't
    look like a broken link."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    return engagement


def _require_tax_module(engagement):
    if not engagement.has_tax_module:
        flash("Turn on a Tax service for this engagement (Edit Engagement) before using the Tax tab.", "danger")
        return False
    return True


# ---------- Tax Entity Profile (Module 2) ----------

@tax_bp.route("/<int:engagement_id>/profile/save", methods=["POST"])
@login_required
def save_entity_profile(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    profile = TaxEntityProfile.query.filter_by(engagement_id=engagement_id).first()
    if not profile:
        profile = TaxEntityProfile(engagement_id=engagement_id)
        db.session.add(profile)
    profile.tax_classification = request.form.get("tax_classification", "").strip()
    profile.tax_year_end = _parse_date(request.form.get("tax_year_end"))
    profile.regulatory_matrix_notes = request.form.get("regulatory_matrix_notes", "").strip()
    profile.notes = request.form.get("notes", "").strip()
    profile.completed_by_id = current_user.id
    profile.completed_at = datetime.utcnow()
    profile.reviewed_by_id = None
    profile.reviewed_at = None
    profile.partner_signed_by_id = None
    profile.partner_signed_at = None
    db.session.commit()
    flash("Tax entity profile saved.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/profile/<int:profile_id>/review", methods=["POST"])
@login_required
def review_entity_profile(profile_id):
    profile = TaxEntityProfile.query.get_or_404(profile_id)
    _ensure_engagement_access(profile.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if profile.completed_by_id == current_user.id:
        flash("You can't review a tax entity profile you prepared yourself.", "danger")
        return _tax_redirect(profile.engagement_id)
    profile.reviewed_by_id = current_user.id
    profile.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Tax entity profile marked as reviewed.", "success")
    return _tax_redirect(profile.engagement_id)


@tax_bp.route("/profile/<int:profile_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_entity_profile(profile_id):
    profile = TaxEntityProfile.query.get_or_404(profile_id)
    _ensure_engagement_access(profile.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if profile.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a tax entity profile you prepared yourself.", "danger")
        return _tax_redirect(profile.engagement_id)
    profile.partner_signed_by_id = current_user.id
    profile.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _tax_redirect(profile.engagement_id)


# ---------- Registrations / Clearances / Portal Access (Module 1) ----------

@tax_bp.route("/<int:engagement_id>/registrations/add", methods=["POST"])
@login_required
def add_registration(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    record = TaxRegistration(
        engagement_id=engagement_id,
        record_type=request.form.get("record_type", "registration"),
        tax_head=request.form.get("tax_head", "").strip() or None,
        identifier=request.form.get("identifier", "").strip(),
        status=request.form.get("status", "").strip(),
        issue_date=_parse_date(request.form.get("issue_date")),
        expiry_date=_parse_date(request.form.get("expiry_date")),
        portal_username=request.form.get("portal_username", "").strip() or None,
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(record)
    db.session.commit()
    flash("Record added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/registrations/<int:record_id>/update", methods=["POST"])
@login_required
def update_registration(record_id):
    record = TaxRegistration.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    record.tax_head = request.form.get("tax_head", "").strip() or None
    record.identifier = request.form.get("identifier", "").strip()
    record.status = request.form.get("status", "").strip()
    record.issue_date = _parse_date(request.form.get("issue_date"))
    record.expiry_date = _parse_date(request.form.get("expiry_date"))
    record.portal_username = request.form.get("portal_username", "").strip() or None
    record.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Record updated.", "success")
    return _tax_redirect(record.engagement_id)


@tax_bp.route("/registrations/<int:record_id>/delete", methods=["POST"])
@login_required
def delete_registration(record_id):
    record = TaxRegistration.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    engagement_id = record.engagement_id
    db.session.delete(record)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Statutory Tax Calendar (Module 2) ----------

@tax_bp.route("/<int:engagement_id>/deadlines/add", methods=["POST"])
@login_required
def add_deadline(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    due_date = _parse_date(request.form.get("due_date"))
    if not due_date:
        flash("Please enter a due date.", "danger")
        return _tax_redirect(engagement_id)
    deadline = TaxDeadline(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        description=request.form.get("description", "").strip(),
        due_date=due_date,
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(deadline)
    db.session.commit()
    flash("Deadline added to the calendar.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/deadlines/<int:deadline_id>/update", methods=["POST"])
@login_required
def update_deadline(deadline_id):
    deadline = TaxDeadline.query.get_or_404(deadline_id)
    _ensure_engagement_access(deadline.engagement)
    deadline.tax_head = request.form.get("tax_head", "").strip() or None
    deadline.description = request.form.get("description", "").strip()
    due_date = _parse_date(request.form.get("due_date"))
    if due_date:
        deadline.due_date = due_date
    deadline.status = request.form.get("status", deadline.status)
    deadline.filed_date = _parse_date(request.form.get("filed_date"))
    deadline.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Deadline updated.", "success")
    return _tax_redirect(deadline.engagement_id)


@tax_bp.route("/deadlines/<int:deadline_id>/delete", methods=["POST"])
@login_required
def delete_deadline(deadline_id):
    deadline = TaxDeadline.query.get_or_404(deadline_id)
    _ensure_engagement_access(deadline.engagement)
    engagement_id = deadline.engagement_id
    db.session.delete(deadline)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Analytical Review & Historical Exposure (Module 3) ----------

@tax_bp.route("/<int:engagement_id>/analytical/save", methods=["POST"])
@login_required
def save_analytical_review(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    review = TaxAnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
    if not review:
        review = TaxAnalyticalReview(engagement_id=engagement_id)
        db.session.add(review)
    review.is_necessary = request.form.get("is_necessary") == "on"
    review.not_necessary_reason = request.form.get("not_necessary_reason", "").strip()
    review.effective_tax_rate_percent = _parse_float(request.form.get("effective_tax_rate_percent"))
    review.expected_tax_rate_percent = _parse_float(request.form.get("expected_tax_rate_percent"))
    review.etr_variance_notes = request.form.get("etr_variance_notes", "").strip()
    review.multi_year_trend_notes = request.form.get("multi_year_trend_notes", "").strip()
    review.vat_analytics_notes = request.form.get("vat_analytics_notes", "").strip()
    review.paye_reconciliation_notes = request.form.get("paye_reconciliation_notes", "").strip()
    review.red_flags_notes = request.form.get("red_flags_notes", "").strip()
    review.completed_by_id = current_user.id
    review.completed_at = datetime.utcnow()
    review.reviewed_by_id = None
    review.reviewed_at = None
    review.partner_signed_by_id = None
    review.partner_signed_at = None
    db.session.commit()
    flash("Tax analytical review saved.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/analytical/<int:review_id>/review", methods=["POST"])
@login_required
def review_analytical_review(review_id):
    review = TaxAnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if review.completed_by_id == current_user.id:
        flash("You can't review a tax analytical review you prepared yourself.", "danger")
        return _tax_redirect(review.engagement_id)
    review.reviewed_by_id = current_user.id
    review.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Tax analytical review marked as reviewed.", "success")
    return _tax_redirect(review.engagement_id)


@tax_bp.route("/analytical/<int:review_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_analytical_review(review_id):
    review = TaxAnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if review.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a tax analytical review you prepared yourself.", "danger")
        return _tax_redirect(review.engagement_id)
    review.partner_signed_by_id = current_user.id
    review.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _tax_redirect(review.engagement_id)


# ---------- Tax Risk Register (Module 4 - reuses RiskItem, module="tax") ----------

@tax_bp.route("/<int:engagement_id>/risks/add", methods=["POST"])
@login_required
def add_tax_risk(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    risk = RiskItem(
        engagement_id=engagement_id,
        module="tax",
        category=request.form.get("category", "").strip(),
        risk_description=request.form.get("risk_description", "").strip(),
        likelihood=int(request.form.get("likelihood", 3) or 3),
        impact=int(request.form.get("impact", 3) or 3),
        mitigation=request.form.get("mitigation", "").strip(),
        owner_id=request.form.get("owner_id") or None,
        status=request.form.get("status", "Open"),
        created_by_id=current_user.id,
    )
    db.session.add(risk)
    db.session.commit()
    flash("Tax risk added to the register.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/risks/<int:risk_id>/update", methods=["POST"])
@login_required
def update_tax_risk(risk_id):
    risk = RiskItem.query.get_or_404(risk_id)
    _ensure_engagement_access(risk.engagement)
    risk.category = request.form.get("category", "").strip()
    risk.risk_description = request.form.get("risk_description", "").strip()
    risk.likelihood = int(request.form.get("likelihood", risk.likelihood) or risk.likelihood)
    risk.impact = int(request.form.get("impact", risk.impact) or risk.impact)
    risk.mitigation = request.form.get("mitigation", "").strip()
    risk.owner_id = request.form.get("owner_id") or None
    risk.status = request.form.get("status", risk.status)
    db.session.commit()
    flash("Tax risk updated.", "success")
    return _tax_redirect(risk.engagement_id)


@tax_bp.route("/risks/<int:risk_id>/delete", methods=["POST"])
@login_required
def delete_tax_risk(risk_id):
    risk = RiskItem.query.get_or_404(risk_id)
    _ensure_engagement_access(risk.engagement)
    engagement_id = risk.engagement_id
    db.session.delete(risk)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Position Register (Module 4) ----------

@tax_bp.route("/<int:engagement_id>/positions/add", methods=["POST"])
@login_required
def add_tax_position(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    position = TaxPosition(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        position_description=request.form.get("position_description", "").strip(),
        classification=request.form.get("classification", "Possible"),
        estimated_exposure=_parse_float(request.form.get("estimated_exposure")),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(position)
    db.session.commit()
    flash("Tax position added to the register.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/positions/<int:position_id>/update", methods=["POST"])
@login_required
def update_tax_position(position_id):
    position = TaxPosition.query.get_or_404(position_id)
    _ensure_engagement_access(position.engagement)
    position.tax_head = request.form.get("tax_head", "").strip() or None
    position.position_description = request.form.get("position_description", "").strip()
    position.classification = request.form.get("classification", position.classification)
    position.estimated_exposure = _parse_float(request.form.get("estimated_exposure"))
    position.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Tax position updated.", "success")
    return _tax_redirect(position.engagement_id)


@tax_bp.route("/positions/<int:position_id>/approve", methods=["POST"])
@login_required
def approve_tax_position(position_id):
    position = TaxPosition.query.get_or_404(position_id)
    _ensure_engagement_access(position.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    action = request.form.get("action", "Approved")
    if action not in TAX_POSITION_APPROVAL_STATUSES:
        abort(400)
    position.approval_status = action
    position.approved_by_id = current_user.id
    position.approved_at = datetime.utcnow()
    db.session.commit()
    flash(f"Tax position marked {action}.", "success")
    return _tax_redirect(position.engagement_id)


@tax_bp.route("/positions/<int:position_id>/delete", methods=["POST"])
@login_required
def delete_tax_position(position_id):
    position = TaxPosition.query.get_or_404(position_id)
    _ensure_engagement_access(position.engagement)
    engagement_id = position.engagement_id
    db.session.delete(position)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Penalty & Interest Engine (Module 4) ----------
# Rates (firm-wide) are managed from their own settings screen; the
# calculator itself is engagement-scoped.

@tax_bp.route("/penalty-interest-rates")
@login_required
def list_penalty_interest_rates():
    rates = PenaltyInterestRate.query.order_by(PenaltyInterestRate.tax_head, PenaltyInterestRate.rate_type, PenaltyInterestRate.effective_from.desc()).all()
    return render_template(
        "tax/penalty_interest_rates.html", rates=rates, tax_heads=TAX_HEADS,
        rate_types=PENALTY_INTEREST_RATE_TYPES,
        can_manage=user_has_permission(current_user, "manage_checklist_templates"),
    )


@tax_bp.route("/penalty-interest-rates/add", methods=["POST"])
@login_required
def add_penalty_interest_rate(engagement_id=None):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    effective_from = _parse_date(request.form.get("effective_from"))
    rate_percent = _parse_float(request.form.get("rate_percent"))
    if not effective_from or rate_percent is None:
        flash("Please enter both an effective date and a rate.", "danger")
        return redirect(url_for("tax.list_penalty_interest_rates"))
    rate = PenaltyInterestRate(
        tax_head=request.form.get("tax_head", "").strip(),
        rate_type=request.form.get("rate_type", "Penalty"),
        rate_percent=rate_percent,
        effective_from=effective_from,
        effective_to=_parse_date(request.form.get("effective_to")),
        notes=request.form.get("notes", "").strip(),
        entered_by_id=current_user.id,
    )
    db.session.add(rate)
    db.session.commit()
    flash("Rate added.", "success")
    return redirect(url_for("tax.list_penalty_interest_rates"))


@tax_bp.route("/penalty-interest-rates/<int:rate_id>/delete", methods=["POST"])
@login_required
def delete_penalty_interest_rate(rate_id):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    rate = PenaltyInterestRate.query.get_or_404(rate_id)
    db.session.delete(rate)
    db.session.commit()
    return redirect(url_for("tax.list_penalty_interest_rates"))


@tax_bp.route("/<int:engagement_id>/penalty-interest/calculate", methods=["POST"])
@login_required
def calculate_penalty_interest(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    principal = _parse_float(request.form.get("principal_amount"))
    days_late = int(request.form.get("days_late", 0) or 0)
    rate_id = request.form.get("rate_id") or None
    rate = PenaltyInterestRate.query.get(int(rate_id)) if rate_id else None
    if principal is None:
        flash("Please enter a principal amount.", "danger")
        return _tax_redirect(engagement_id)
    # Plain simple-interest/flat-penalty arithmetic on the preparer-selected
    # rate - see the module comment above PenaltyInterestRate on why no
    # rate is ever hardcoded or pre-selected here.
    rate_percent = rate.rate_percent if rate else _parse_float(request.form.get("rate_percent_override"))
    computed = None
    if rate_percent is not None:
        if rate and rate.rate_type == "Interest":
            computed = principal * (rate_percent / 100.0) * (days_late / 365.0)
        else:
            computed = principal * (rate_percent / 100.0)
    calc = PenaltyInterestCalculation(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        principal_amount=principal,
        days_late=days_late,
        rate_id=rate.id if rate else None,
        rate_percent_used=rate_percent,
        computed_amount=computed,
        notes=request.form.get("notes", "").strip(),
        calculated_by_id=current_user.id,
    )
    db.session.add(calc)
    db.session.commit()
    flash("Penalty/interest calculation saved.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/penalty-interest/<int:calc_id>/delete", methods=["POST"])
@login_required
def delete_penalty_interest_calculation(calc_id):
    calc = PenaltyInterestCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    engagement_id = calc.engagement_id
    db.session.delete(calc)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Dynamic Information Request List (Module 5) ----------

@tax_bp.route("/<int:engagement_id>/info-requests/add", methods=["POST"])
@login_required
def add_information_request(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    item = TaxInformationRequest(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        item_description=request.form.get("item_description", "").strip(),
        requested_from=request.form.get("requested_from", "").strip(),
        date_requested=_parse_date(request.form.get("date_requested")) or date.today(),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(item)
    db.session.commit()
    flash("Information request added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/info-requests/<int:item_id>/update", methods=["POST"])
@login_required
def update_information_request(item_id):
    item = TaxInformationRequest.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.status = request.form.get("status", item.status)
    if item.status == "Received" and not item.date_received:
        item.date_received = date.today()
    item.notes = request.form.get("notes", item.notes)
    db.session.commit()
    flash("Information request updated.", "success")
    return _tax_redirect(item.engagement_id)


@tax_bp.route("/info-requests/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_information_request(item_id):
    item = TaxInformationRequest.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Return Records (Module 7 + Module 10's e-filing/assessment recon) ----------

@tax_bp.route("/<int:engagement_id>/returns/add", methods=["POST"])
@login_required
def add_return_record(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    record = TaxReturnRecord(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", TAX_HEADS[0]),
        period_label=request.form.get("period_label", "").strip(),
        due_date=_parse_date(request.form.get("due_date")),
        amount_due=_parse_float(request.form.get("amount_due")),
        reference=request.form.get("reference", "").strip(),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(record)
    db.session.commit()
    flash("Return record added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/returns/<int:record_id>/update", methods=["POST"])
@login_required
def update_return_record(record_id):
    record = TaxReturnRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    record.status = request.form.get("status", record.status)
    record.filed_date = _parse_date(request.form.get("filed_date"))
    record.amount_due = _parse_float(request.form.get("amount_due"))
    record.amount_paid = _parse_float(request.form.get("amount_paid"))
    record.assessed_amount = _parse_float(request.form.get("assessed_amount"))
    record.reference = request.form.get("reference", record.reference)
    record.reconciliation_notes = request.form.get("reconciliation_notes", "").strip()
    record.notes = request.form.get("notes", record.notes)
    db.session.commit()
    flash("Return record updated.", "success")
    return _tax_redirect(record.engagement_id)


@tax_bp.route("/returns/<int:record_id>/delete", methods=["POST"])
@login_required
def delete_return_record(record_id):
    record = TaxReturnRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    engagement_id = record.engagement_id
    db.session.delete(record)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Advisory Workflow (Module 7B - advisory-only) ----------

def _require_tax_advisory(engagement):
    if not engagement.has_tax_advisory:
        flash("The Tax Advisory service isn't on for this engagement.", "danger")
        return False
    return True


@tax_bp.route("/<int:engagement_id>/research/add", methods=["POST"])
@login_required
def add_research_log_entry(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_advisory(engagement):
        return _tax_redirect(engagement_id)
    entry = TaxResearchLogEntry(
        engagement_id=engagement_id,
        topic=request.form.get("topic", "").strip(),
        question=request.form.get("question", "").strip(),
        research_notes=request.form.get("research_notes", "").strip(),
        conclusion=request.form.get("conclusion", "").strip(),
        references=request.form.get("references", "").strip(),
        prepared_by_id=current_user.id,
    )
    db.session.add(entry)
    db.session.commit()
    flash("Research log entry added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/research/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_research_log_entry(entry_id):
    entry = TaxResearchLogEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    engagement_id = entry.engagement_id
    db.session.delete(entry)
    db.session.commit()
    return _tax_redirect(engagement_id)


@tax_bp.route("/<int:engagement_id>/structuring/add", methods=["POST"])
@login_required
def add_structuring_option(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_advisory(engagement):
        return _tax_redirect(engagement_id)
    option = TaxStructuringOption(
        engagement_id=engagement_id,
        option_name=request.form.get("option_name", "").strip(),
        description=request.form.get("description", "").strip(),
        estimated_tax_impact=_parse_float(request.form.get("estimated_tax_impact")),
        risk_rating=request.form.get("risk_rating", "Medium"),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(option)
    db.session.commit()
    flash("Structuring option added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/structuring/<int:option_id>/recommend", methods=["POST"])
@login_required
def recommend_structuring_option(option_id):
    option = TaxStructuringOption.query.get_or_404(option_id)
    _ensure_engagement_access(option.engagement)
    # Only one option can be "the" recommendation at a time - clear any
    # other recommended flag on the same engagement first.
    TaxStructuringOption.query.filter_by(engagement_id=option.engagement_id).update({"recommended": False})
    option.recommended = True
    db.session.commit()
    flash(f'"{option.option_name}" marked as the recommended option.', "success")
    return _tax_redirect(option.engagement_id)


@tax_bp.route("/structuring/<int:option_id>/delete", methods=["POST"])
@login_required
def delete_structuring_option(option_id):
    option = TaxStructuringOption.query.get_or_404(option_id)
    _ensure_engagement_access(option.engagement)
    engagement_id = option.engagement_id
    db.session.delete(option)
    db.session.commit()
    return _tax_redirect(engagement_id)


@tax_bp.route("/<int:engagement_id>/disputes/add", methods=["POST"])
@login_required
def add_dispute(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_advisory(engagement):
        return _tax_redirect(engagement_id)
    dispute = TaxDispute(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        issue_description=request.form.get("issue_description", "").strip(),
        amount_in_dispute=_parse_float(request.form.get("amount_in_dispute")),
        next_action=request.form.get("next_action", "").strip(),
        next_action_date=_parse_date(request.form.get("next_action_date")),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(dispute)
    db.session.commit()
    flash("Dispute added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/disputes/<int:dispute_id>/update", methods=["POST"])
@login_required
def update_dispute(dispute_id):
    dispute = TaxDispute.query.get_or_404(dispute_id)
    _ensure_engagement_access(dispute.engagement)
    dispute.stage = request.form.get("stage", dispute.stage)
    dispute.amount_in_dispute = _parse_float(request.form.get("amount_in_dispute"))
    dispute.next_action = request.form.get("next_action", "").strip()
    dispute.next_action_date = _parse_date(request.form.get("next_action_date"))
    dispute.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Dispute updated.", "success")
    return _tax_redirect(dispute.engagement_id)


@tax_bp.route("/disputes/<int:dispute_id>/delete", methods=["POST"])
@login_required
def delete_dispute(dispute_id):
    dispute = TaxDispute.query.get_or_404(dispute_id)
    _ensure_engagement_access(dispute.engagement)
    engagement_id = dispute.engagement_id
    db.session.delete(dispute)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Head Checklists / Quality Gates (Module 8) ----------

@tax_bp.route("/<int:engagement_id>/checklist/seed", methods=["POST"])
@login_required
def seed_tax_checklist(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    if TaxChecklistItem.query.filter_by(engagement_id=engagement_id).count() > 0:
        flash("The tax checklist already has items on it.", "info")
        return _tax_redirect(engagement_id)
    for order, (tax_head, item_text) in enumerate(DEFAULT_TAX_CHECKLIST_ITEMS):
        db.session.add(TaxChecklistItem(
            engagement_id=engagement_id, tax_head=tax_head, item_text=item_text,
            order=order, created_by_id=current_user.id,
        ))
    db.session.commit()
    flash("The firm's default tax checklist has been added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/<int:engagement_id>/checklist/add", methods=["POST"])
@login_required
def add_tax_checklist_item(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    max_order = db.session.query(db.func.max(TaxChecklistItem.order)).filter_by(engagement_id=engagement_id).scalar() or 0
    item = TaxChecklistItem(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
        created_by_id=current_user.id,
    )
    db.session.add(item)
    db.session.commit()
    flash("Checklist item added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_tax_checklist_item(item_id):
    item = TaxChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.response = request.form.get("response", "")
    item.comment = request.form.get("comment", "").strip()
    item.completed_by_id = current_user.id
    item.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Checklist item updated.", "success")
    return _tax_redirect(item.engagement_id)


@tax_bp.route("/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_tax_checklist_item(item_id):
    item = TaxChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Firm-wide Legislative Update Control (Module 8) ----------

@tax_bp.route("/legislative-updates")
@login_required
def list_legislative_updates():
    updates = LegislativeUpdate.query.order_by(LegislativeUpdate.effective_date.desc().nullslast(), LegislativeUpdate.created_at.desc()).all()
    return render_template("tax/legislative_updates.html", updates=updates, tax_heads=TAX_HEADS)


@tax_bp.route("/legislative-updates/add", methods=["POST"])
@login_required
def add_legislative_update(engagement_id=None):
    title = request.form.get("title", "").strip()
    if not title:
        flash("Please enter a title.", "danger")
        return redirect(url_for("tax.list_legislative_updates"))
    update = LegislativeUpdate(
        tax_head=request.form.get("tax_head", "").strip() or None,
        title=title,
        summary=request.form.get("summary", "").strip(),
        effective_date=_parse_date(request.form.get("effective_date")),
        source_reference=request.form.get("source_reference", "").strip(),
        impact_assessment=request.form.get("impact_assessment", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(update)
    db.session.commit()
    flash("Legislative update logged.", "success")
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/legislative-updates/<int:update_id>/review", methods=["POST"])
@login_required
def review_legislative_update(update_id):
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    update = LegislativeUpdate.query.get_or_404(update_id)
    update.reviewed_by_id = current_user.id
    update.reviewed_at = datetime.utcnow()
    update.impact_assessment = request.form.get("impact_assessment", update.impact_assessment)
    db.session.commit()
    flash("Legislative update marked as reviewed.", "success")
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/legislative-updates/<int:update_id>/delete", methods=["POST"])
@login_required
def delete_legislative_update(update_id):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    update = LegislativeUpdate.query.get_or_404(update_id)
    db.session.delete(update)
    db.session.commit()
    return redirect(url_for("tax.list_legislative_updates"))
