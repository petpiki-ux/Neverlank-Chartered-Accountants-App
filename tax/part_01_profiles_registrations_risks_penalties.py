"""Tax Advisory module - the "Tax" tab shown on any
engagement with Engagement.has_tax_module True (a Tax Advisory engagement -
the single umbrella type for all tax work - or any other engagement type
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
import os
import re
import csv
import io
import json
import uuid
import calendar as calendar_module

from datetime import datetime, date

import requests
import openpyxl
from bs4 import BeautifulSoup

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import visible_clients
from models import (
    Engagement, User, RiskItem, Client,
    TaxEntityProfile, TAX_ENTITY_CLASSIFICATIONS,
    TaxRegistration, TAX_REGISTRATION_RECORD_TYPES,
    TaxDeadline,
    TaxAnalyticalReview,
    TaxPosition, TAX_POSITION_CLASSIFICATIONS, TAX_POSITION_APPROVAL_STATUSES,
    PenaltyInterestRate, PenaltyInterestCalculation, PENALTY_INTEREST_RATE_TYPES,
    TaxInformationRequest, TAX_INFO_REQUEST_STATUSES,
    TaxReturnRecord, TAX_RETURN_STATUSES,
    TaxResearchLogEntry, TaxStructuringOption, TaxDispute, TAX_DISPUTE_STAGES,
    VATRate, VAT_RATE_TYPES,
    VAT_CURRENCIES, VAT_DEFAULT_CURRENCY, normalize_vat_currency, VAT_DENIAL_GROUNDS, VAT_DENIAL_LABELS,
    VATInvoice, VAT_INVOICE_DIRECTIONS, VAT_INVOICE_DIRECTION_LABELS, VAT_INVOICE_SOURCES,
    VATImportBatch, VAT_DOCUMENT_KINDS, VAT_KIND_LABELS, resolve_vat_kind, vat_kind_code,
    VATPeriod, vat_period_key, vat_period_label, vat_period_of, parse_vat_period_key, vat_locked_months_among,
    LegislativeUpdate, LegislativeUpdateClientLink, LegislativeProvision,
    LEGISLATIVE_UPDATE_TYPES, LEGISLATIVE_UPDATE_TYPE_LABELS, LEGISLATIVE_UPDATE_AREAS,
    CASE_AUTHORITY_STATUSES, CASE_AUTHORITY_LABELS,
    StatutoryDeadline,
    TaxChecklistItem, DEFAULT_TAX_CHECKLIST_ITEMS,
    TAX_HEADS, PARTNER_SIGNOFF_ROLES, REVIEWER_ROLES,
    user_has_permission,
)
from engagements import _ensure_engagement_access
from config import Config
import sanctions_data
import legislation_summary
import legislation_search
import legislation_index
import legislation_chunking
import act_import
import vat_checks

tax_bp = Blueprint("tax", __name__, url_prefix="/tax")

LEGISLATIVE_UPDATE_FILE_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
LEGISLATIVE_UPDATE_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}


def _legislative_updates_dir():
    directory = Config.LEGISLATIVE_UPDATES_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory


NEWS_FETCH_TIMEOUT = 20  # seconds - a slow/unresponsive news site should never hang the request
NEWS_MAX_TEXT_CHARS = 60000  # matches legislation_summary.MAX_TEXT_CHARS; keeps a very long page bounded before it even reaches the AI step
NEWS_FETCH_HEADERS = {
    # Some news sites block the default python-requests user agent outright;
    # a plain browser-like UA is enough to get past that without doing
    # anything deceptive - the app identifies itself honestly to the extent
    # any browser does.
    "User-Agent": "Mozilla/5.0 (compatible; NeverlankAuditApp/1.0; +https://neverlank-audit-app.onrender.com)"
}


def _fetch_and_extract_news_article(url):
    """Fetch a news article by URL and extract its readable text, for a News
    Legislative Update entry (instrument_type == "News") filed by pasting a
    link rather than uploading a file - see models.LegislativeUpdate's
    docstring. Returns (text, error): on success, text is a non-empty
    string and error is None; on failure, text is None and error is a
    short, person-readable message suitable for a flash message. Never
    raises - a bad/unreachable URL should degrade cleanly, the same way a
    bad PDF upload degrades to an extraction_status rather than a crash.

    Extraction is deliberately simple (strip script/style/nav/footer/header
    tags, then take the page's visible text) rather than a full
    readability/boilerplate-removal algorithm - good enough for an AI
    summarisation step that only needs the article's substance, not a
    publication-quality reformatting of it."""
    if not url:
        return None, "No URL was provided."
    if not (url.startswith("http://") or url.startswith("https://")):
        return None, "That doesn't look like a valid web address - it should start with http:// or https://."
    try:
        response = requests.get(url, headers=NEWS_FETCH_HEADERS, timeout=NEWS_FETCH_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        return None, f"Couldn't fetch that page: {exc}"[:500]

    try:
        soup = BeautifulSoup(response.content, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
    except Exception as exc:
        return None, f"Couldn't read that page's content: {exc}"[:500]

    if not text or len(text) < 50:
        return None, "Couldn't find any readable article text on that page - it may require a login, or not be a plain article page."
    return text[:NEWS_MAX_TEXT_CHARS], None


def legislative_update_editor_required(f):
    """Filing/reprocessing/deleting a Legislative Update Control entry (with
    or without an attached instrument) is gated by the configurable "Manage
    Legislative Update Control" permission (Team > Permissions) - defaults
    to partner/admin only, the same governed-library treatment as
    Regulatory Notices, since this now files actual source documents rather
    than just a free-text log anyone could add to. Viewing, confirming
    practice areas, tagging a client as affected, and importing into a Tax
    Advisory Research Log stay open to anyone logged in."""
    from functools import wraps

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not user_has_permission(current_user, "manage_legislative_updates"):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def _tax_redirect(engagement_id, vat_period=None):
    """Back to the Tax tab; `vat_period` ('2026-09' or 'undated') reopens the
    VAT month the person was working in rather than the month list."""
    tab = "tax"
    if "vat" in (request.endpoint or ""):
        # a VAT route: back to the VAT tab when the client has a VAT number, which is when that register has its own tab
        eng = db.session.get(Engagement, engagement_id)
        if eng is not None and eng.shows_vat_tab:
            tab = "vat"
    if vat_period:
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=tab, vat_period=vat_period))
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=tab))


def _vat_doc_period_arg(day):
    """The ?vat_period= value for a document dated `day`."""
    return vat_period_key(*vat_period_of(day)) if day else "undated"


def _locked_message(months, what):
    """Plain-language refusal for touching a VAT month marked 'Computation done'."""
    names = ", ".join(vat_period_label(y, m) for y, m in months)
    many = len(months) > 1
    return (f"{names} {'are' if many else 'is'} marked 'Computation done' and locked, so {what}. "
            f"A Supervisor, Partner or Admin can reopen {'them' if many else 'it'} from the VAT months list on the Tax tab.")


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


# ---------- VAT Invoice Register & Input/Output Tax Working Paper ----------
# Requested by the user: capture sales (Output) and purchase/expense (Input)
# invoices, compute Input Tax and Output Tax, allow a rate lower than the
# standard rate to be captured, and import an already-prepared Output/Input
# Tax schedule. Rates (firm-wide) are managed from their own settings screen,
# exactly like Penalty & Interest rates above; invoice capture and schedule
# import are engagement-scoped. See models.py's module comment above VATRate
# for why rates are never hardcoded.

def _vat_imports_dir():
    directory = Config.VAT_IMPORTS_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory
