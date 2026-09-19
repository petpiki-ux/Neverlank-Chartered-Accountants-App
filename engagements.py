import os
import csv
import io
import uuid
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, send_from_directory, send_file, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import openpyxl

from extensions import db
from models import (
    Engagement, Client, User, ChecklistTemplate, EngagementChecklistItem,
    RiskItem, Document, EngagementTask, DocumentTemplate, StaffAllocation,
    RiskAssessment, MaterialityCalculation, EntityUnderstanding, AuditStrategy, AUDIT_STRATEGY_PHASE_STATUSES,
    AnalyticalReview, AnalyticalReviewLine,
    ClientAcceptance, CLIENT_ACCEPTANCE_DECISIONS, CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
    RISK_CATEGORIES,
    SANCTIONS_SCREENING_SOURCES, SANCTIONS_SCREENING_RESULTS,
    SANCTIONS_AUTO_SOURCES, REGULATORY_NOTICE_SOURCES, REGULATORY_NOTICE_SOURCE_LABELS,
    SanctionsListStatus, RegulatoryNotice, ClientKeyPerson,
    COAMapping, TrialBalance, TrialBalanceLine, AuditAdjustment, AuditAdjustmentLine, FinancialStatements,
    SubstantiveProcedureArea, SubstantiveProcedureItem,
    FinalisationChecklist, FinalisationChecklistItem, DEFAULT_FINALISATION_CHECKLIST_ITEMS, FORENSIC_FINALISATION_CHECKLIST_ITEMS,
    AUDIT_AREA_REFERENCES, FORENSIC_AREA_REFERENCES,
    EngagementQuery, QueryReply,
    ENGAGEMENT_TYPES, ENGAGEMENT_STATUSES, TASK_STATUSES, CHECKLIST_STATUSES, RISK_STATUSES,
    SECRETARIAL_SUBDIVISIONS, REVIEWER_ROLES, PARTNER_SIGNOFF_ROLES,
    RISK_LIKELIHOOD_QUESTIONS, RISK_IMPACT_QUESTIONS,
    FORENSIC_RISK_LIKELIHOOD_QUESTIONS, FORENSIC_RISK_IMPACT_QUESTIONS, SCOPE_SUGGESTIONS,
    ENTITY_UNDERSTANDING_FIELDS, EntityUnderstandingChecklistItem, FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS,
    AUDIT_AREAS, BASELINE_SUBSTANTIVE_PROCEDURES, HIGH_RISK_EXTRA_PROCEDURES, INDUSTRY_EXTRA_PROCEDURES,
    FORENSIC_SUBSTANTIVE_AREAS, FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES,
    QUERY_SECTIONS, QUERY_SECTION_KEYS,
    user_has_permission, user_can_access_engagement, engagement_acceptance_cleared,
    Tickmark, WORKPAPER_SECTIONS, workpaper_reference, effectively_reviewed,
    WorkpaperNarrative, WORKPAPER_NARRATIVE_KINDS, WORKPAPER_NARRATIVE_KIND_KEYS,
    DEFAULT_WORKPAPER_NARRATIVE_BODIES,
)
import financials as fin
import workpapers as wp

engagements_bp = Blueprint("engagements", __name__, url_prefix="/engagements")


def _ensure_engagement_access(engagement, require_accepted=True):
    """Confidentiality + acceptance gate for every engagement-scoped route
    below: aborts 403 unless current_user is that engagement's
    Partner/Manager/Team (or Admin, who always passes). See
    models.user_can_access_engagement.

    require_accepted additionally blocks non-Admins from every route that
    passes it as True (the default - i.e. every existing call site below,
    unchanged) until the engagement's Client Acceptance & Continuance gate
    is cleared (models.engagement_acceptance_cleared) - which is always
    True for engagements that don't require it in the first place, so this
    is a no-op for every engagement that predates the feature. Only
    view_engagement itself (so the page - including its own Acceptance tab
    - can render at all) and every route inside acceptance.py (so the gate
    can actually be worked on) pass require_accepted=False."""
    if not user_can_access_engagement(current_user, engagement):
        abort(403)
    if require_accepted and current_user.role != "admin" and not engagement_acceptance_cleared(engagement):
        abort(403)


def _visible_to_current_user(engagement_list):
    """Same confidentiality rule as _ensure_engagement_access, applied to a
    list rather than a single engagement - for the dashboard, the
    engagements list, and the firm-wide Queries board, which must not even
    reveal that a non-assigned engagement exists. Admin sees everything."""
    if current_user.role == "admin":
        return engagement_list
    return [e for e in engagement_list if user_can_access_engagement(current_user, e)]


# ---------- Dashboard ----------

@engagements_bp.route("/dashboard")
@login_required
def dashboard():
    all_engagements = _visible_to_current_user(
        Engagement.query.order_by(Engagement.deadline.asc().nullslast()).all()
    )
    active = [e for e in all_engagements if e.status != "Completed"]
    overdue = [e for e in active if e.is_overdue]
    my_tasks = (
        EngagementTask.query.filter_by(assigned_to_id=current_user.id)
        .filter(EngagementTask.status != "Done")
        .order_by(EngagementTask.due_date.asc().nullslast())
        .limit(10)
        .all()
    )
    counts_by_status = {s: 0 for s in ENGAGEMENT_STATUSES}
    for e in all_engagements:
        counts_by_status[e.status] = counts_by_status.get(e.status, 0) + 1

    return render_template(
        "dashboard.html",
        active=active,
        overdue=overdue,
        my_tasks=my_tasks,
        counts_by_status=counts_by_status,
        total_clients=Client.query.count(),
        total_engagements=len(all_engagements),
    )


# ---------- Engagement CRUD ----------

@engagements_bp.route("/")
@login_required
def list_engagements():
    status_filter = request.args.get("status", "")
    type_filter = request.args.get("type", "")
    query = Engagement.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    if type_filter:
        query = query.filter_by(type=type_filter)
    all_engagements = _visible_to_current_user(
        query.order_by(Engagement.deadline.asc().nullslast()).all()
    )
    return render_template(
        "engagements/list.html",
        engagements=all_engagements,
        statuses=ENGAGEMENT_STATUSES,
        types=ENGAGEMENT_TYPES,
        status_filter=status_filter,
        type_filter=type_filter,
    )


@engagements_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_engagement():
    clients = Client.query.order_by(Client.name).all()
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()

    if request.method == "POST":
        client_id = request.form.get("client_id")
        if not client_id:
            flash("Please select a client.", "danger")
            return render_template("engagements/form.html", engagement=None, clients=clients, users=users,
                                    templates=templates, types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                                    subdivisions=SECRETARIAL_SUBDIVISIONS)

        engagement_type = request.form.get("type", "Audit")
        engagement = Engagement(
            client_id=int(client_id),
            title=request.form.get("title", "").strip(),
            type=engagement_type,
            subdivision=(request.form.get("subdivision", "").strip() or None) if engagement_type == "Secretarial" else None,
            status=request.form.get("status", "Planning"),
            description=request.form.get("description", "").strip(),
            partner_id=request.form.get("partner_id") or None,
            manager_id=request.form.get("manager_id") or None,
            acceptance_required=request.form.get("acceptance_required") == "on",
        )
        start_date = request.form.get("start_date")
        deadline = request.form.get("deadline")
        period_end = request.form.get("period_end")
        engagement.start_date = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else date.today()
        engagement.deadline = datetime.strptime(deadline, "%Y-%m-%d").date() if deadline else None
        engagement.period_end = datetime.strptime(period_end, "%Y-%m-%d").date() if period_end else None

        team_ids = request.form.getlist("team_members")
        if team_ids:
            engagement.team_members = User.query.filter(User.id.in_(team_ids)).all()

        db.session.add(engagement)
        db.session.flush()  # get engagement.id before commit

        template_id = request.form.get("template_id")
        if template_id:
            template = ChecklistTemplate.query.get(int(template_id))
            if template:
                for item in template.items:
                    db.session.add(EngagementChecklistItem(
                        engagement_id=engagement.id,
                        section=item.section,
                        item_text=item.item_text,
                        order=item.order,
                    ))

        db.session.commit()
        flash(f"Engagement '{engagement.title}' created.", "success")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id))

    return render_template("engagements/form.html", engagement=None, clients=clients, users=users,
                            templates=templates, types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                            subdivisions=SECRETARIAL_SUBDIVISIONS)


@engagements_bp.route("/<int:engagement_id>/edit", methods=["GET", "POST"])
@login_required
def edit_engagement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement, require_accepted=False)
    clients = Client.query.order_by(Client.name).all()
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()

    if request.method == "POST":
        engagement.client_id = int(request.form.get("client_id"))
        engagement.title = request.form.get("title", "").strip()
        engagement.type = request.form.get("type", "Audit")
        engagement.subdivision = (request.form.get("subdivision", "").strip() or None) if engagement.type == "Secretarial" else None
        engagement.status = request.form.get("status", "Planning")
        engagement.description = request.form.get("description", "").strip()
        engagement.partner_id = request.form.get("partner_id") or None
        engagement.manager_id = request.form.get("manager_id") or None
        engagement.acceptance_required = request.form.get("acceptance_required") == "on"

        start_date = request.form.get("start_date")
        deadline = request.form.get("deadline")
        period_end = request.form.get("period_end")
        engagement.start_date = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else engagement.start_date
        engagement.deadline = datetime.strptime(deadline, "%Y-%m-%d").date() if deadline else None
        engagement.period_end = datetime.strptime(period_end, "%Y-%m-%d").date() if period_end else None

        team_ids = request.form.getlist("team_members")
        engagement.team_members = User.query.filter(User.id.in_(team_ids)).all() if team_ids else []

        db.session.commit()
        flash("Engagement updated.", "success")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id))

    return render_template("engagements/form.html", engagement=engagement, clients=clients, users=users,
                            templates=[], types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                            subdivisions=SECRETARIAL_SUBDIVISIONS)


@engagements_bp.route("/<int:engagement_id>/delete", methods=["POST"])
@login_required
def delete_engagement(engagement_id):
    if not user_has_permission(current_user, "delete_engagements"):
        abort(403)
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement, require_accepted=False)
    client_id = engagement.client_id
    db.session.delete(engagement)
    db.session.commit()
    flash("Engagement deleted.", "info")
    return redirect(url_for("clients.view_client", client_id=client_id))


@engagements_bp.route("/<int:engagement_id>")
@login_required
def view_engagement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    # require_accepted=False: the page itself (including its own Acceptance
    # tab) must always render for anyone with confidentiality access, even
    # before the gate clears - the gate below only decides which tabs' real
    # content vs. a "locked" placeholder the template shows.
    _ensure_engagement_access(engagement, require_accepted=False)
    client_acceptance = ClientAcceptance.query.filter_by(engagement_id=engagement_id).first()
    acceptance_cleared = engagement_acceptance_cleared(engagement)
    # Automated screening status, shown alongside the Sanctions & Adverse
    # Notice Screening card on the acceptance tab - how current the UN/OFAC/
    # EU cache is, and how many RBZ/FIU notices are on file to check against.
    sanctions_list_status = {
        row.source: row for row in SanctionsListStatus.query.filter(SanctionsListStatus.source.in_(SANCTIONS_AUTO_SOURCES)).all()
    }
    regulatory_notice_counts = {}
    for source in REGULATORY_NOTICE_SOURCES:
        notices = RegulatoryNotice.query.filter_by(source=source).all()
        regulatory_notice_counts[source] = {
            "total": len(notices),
            "searchable": sum(1 for n in notices if n.extraction_status == "extracted"),
        }
    # Confirmed Directors/Shareholders/etc already on file for this client
    # (see models.ClientKeyPerson, company_documents.py) - offered as a
    # quick-pick on the Sanctions & Adverse Notice Screening card so they
    # don't have to be retyped for every engagement. An unconfirmed
    # (AI-suggested) person isn't offered here - see
    # acceptance.add_screening_from_key_person.
    client_key_people = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, status="Confirmed").order_by(ClientKeyPerson.full_name).all()
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    tab = request.args.get("tab", "overview")
    # Document Templates matching this engagement's type (Audit/Assurance/
    # Consulting) - shown on the Documents tab so the right blank
    # letters/workpapers for this client's engagement are one click away,
    # with no need to go hunting in the separate Document Templates library.
    matching_templates = (
        DocumentTemplate.query.filter_by(type=engagement.type)
        .order_by(DocumentTemplate.ref_code)
        .all()
    )
    risk_assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
    # An Investigative Engagement gets the Fraud Triangle-based forensic
    # questionnaire instead of the ordinary audit risk-of-material-
    # misstatement one - see RiskAssessment.likelihood_questions/
    # impact_questions, which the save route below mirrors.
    is_forensic_risk = engagement.type == "Investigative Engagement"
    likelihood_questions = FORENSIC_RISK_LIKELIHOOD_QUESTIONS if is_forensic_risk else RISK_LIKELIHOOD_QUESTIONS
    impact_questions = FORENSIC_RISK_IMPACT_QUESTIONS if is_forensic_risk else RISK_IMPACT_QUESTIONS
    materiality = MaterialityCalculation.query.filter_by(engagement_id=engagement_id).first()
    audit_strategy = AuditStrategy.query.filter_by(engagement_id=engagement_id).first() if is_forensic_risk else None
    entity_understanding = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    # Public-information scans live on the Client (see models.
    # EntityPublicResearch/company_documents.run_public_research) since the
    # firm's understanding of a client's business doesn't reset between
    # engagements - only the most recent run is shown here, as a pointer
    # back to the client's page where the full history lives and new scans
    # are run from.
    latest_public_research = engagement.client.public_research_runs[0] if engagement.client.public_research_runs else None
    analytical_review = AnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
    scope_suggestion = SCOPE_SUGGESTIONS.get(risk_assessment.rating) if risk_assessment and risk_assessment.rating else None

    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    financial_statements = FinancialStatements.query.filter_by(engagement_id=engagement_id).first()
    # The Financial Statements are always built from the ADJUSTED trial
    # balance (preliminary TB + audit adjustments, current year only) -
    # Analytical Review and Substantive Procedures work from the preliminary
    # trial_balance.lines directly, unadjusted, so they're unaffected by this.
    statements = (
        fin.build_all_statements(trial_balance.lines, trial_balance.adjustments)
        if trial_balance and trial_balance.lines else None
    )

    # On an Investigative Engagement, Substantive Procedures uses the four
    # forensic evidence-type categories instead of the financial-statement
    # audit areas - see FORENSIC_SUBSTANTIVE_AREAS above.
    substantive_area_names = FORENSIC_SUBSTANTIVE_AREAS if is_forensic_risk else AUDIT_AREAS

    substantive_areas_by_name = {
        a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).all()
    }
    # Working-paper reference codes (see models.py) shown next to each area
    # heading and embedded in the generated Substantive Procedures workpapers.
    substantive_area_refs = FORENSIC_AREA_REFERENCES if is_forensic_risk else AUDIT_AREA_REFERENCES

    finalisation_checklist = FinalisationChecklist.query.filter_by(engagement_id=engagement_id).first()

    # Firm-wide tickmark legend (see tickmarks.py) - offered as a picker on
    # each Substantive Procedures item.
    tickmarks = Tickmark.query.order_by(Tickmark.symbol).all()

    # The narrative workpapers with a persistent, editable copy (Rep Letter,
    # Report to Management, Forensic Report executive summary) - keyed by
    # kind so the Finalisation tab can look each one up directly. Any kind
    # not yet saved is seeded (in memory only, not committed) with the
    # firm's default starting wording, so the edit box is never blank.
    workpaper_narratives = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
    }
    for kind, _label, _section in WORKPAPER_NARRATIVE_KINDS:
        if kind not in workpaper_narratives:
            workpaper_narratives[kind] = _get_or_seed_workpaper_narrative(engagement_id, kind)

    # Review Queries, grouped for the template: by section for every plain
    # section, and separately by audit area for "substantive" (which has one
    # query list per area rather than one for the whole tab). Newest first
    # within each group.
    all_queries = (
        EngagementQuery.query.filter_by(engagement_id=engagement_id)
        .order_by(EngagementQuery.raised_at.desc())
        .all()
    )
    queries_by_section = {}
    queries_by_area = {}
    for q in all_queries:
        if q.section == "substantive" and q.area_name:
            queries_by_area.setdefault(q.area_name, []).append(q)
        else:
            queries_by_section.setdefault(q.section, []).append(q)
    open_query_count = sum(1 for q in all_queries if q.status == "Open")

    return render_template(
        "engagements/detail.html",
        engagement=engagement,
        users=users,
        tab=tab,
        checklist_statuses=CHECKLIST_STATUSES,
        risk_statuses=RISK_STATUSES,
        task_statuses=TASK_STATUSES,
        matching_templates=matching_templates,
        risk_assessment=risk_assessment,
        likelihood_questions=likelihood_questions,
        impact_questions=impact_questions,
        is_forensic_risk=is_forensic_risk,
        materiality=materiality,
        audit_strategy=audit_strategy,
        audit_strategy_phase_statuses=AUDIT_STRATEGY_PHASE_STATUSES,
        scope_suggestion=scope_suggestion,
        entity_understanding=entity_understanding,
        entity_fields=ENTITY_UNDERSTANDING_FIELDS,
        latest_public_research=latest_public_research,
        analytical_review=analytical_review,
        trial_balance=trial_balance,
        financial_statements=financial_statements,
        statements=statements,
        category_choices=fin.category_choices(),
        category_label=fin.category_label,
        audit_areas=substantive_area_names,
        substantive_areas=substantive_areas_by_name,
        substantive_area_refs=substantive_area_refs,
        queries_by_section=queries_by_section,
        queries_by_area=queries_by_area,
        open_query_count=open_query_count,
        client_acceptance=client_acceptance,
        acceptance_cleared=acceptance_cleared,
        acceptance_decisions=CLIENT_ACCEPTANCE_DECISIONS,
        acceptance_checklist_responses=CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
        risk_categories=RISK_CATEGORIES,
        sanctions_screening_sources=SANCTIONS_SCREENING_SOURCES,
        sanctions_screening_results=SANCTIONS_SCREENING_RESULTS,
        sanctions_auto_sources=SANCTIONS_AUTO_SOURCES,
        sanctions_list_status=sanctions_list_status,
        can_manage_sanctions_lists=user_has_permission(current_user, "manage_sanctions_lists"),
        regulatory_notice_sources=REGULATORY_NOTICE_SOURCES,
        regulatory_notice_source_labels=REGULATORY_NOTICE_SOURCE_LABELS,
        regulatory_notice_counts=regulatory_notice_counts,
        client_key_people=client_key_people,
        finalisation_checklist=finalisation_checklist,
        finalisation_checklist_responses=CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
        tickmarks=tickmarks,
        workpaper_sections=WORKPAPER_SECTIONS,
        workpaper_reference=workpaper_reference,
        effectively_reviewed=effectively_reviewed,
        workpaper_narratives=workpaper_narratives,
        workpaper_narrative_kinds=WORKPAPER_NARRATIVE_KINDS,
    )


# ---------- Checklist ----------

@engagements_bp.route("/<int:engagement_id>/checklist/add", methods=["POST"])
@login_required
def add_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    max_order = max([i.order for i in engagement.checklist_items], default=0)
    item = EngagementChecklistItem(
        engagement_id=engagement.id,
        section=request.form.get("section", "").strip(),
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
    )
    db.session.add(item)
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.status = request.form.get("status", item.status)
    item.notes = request.form.get("notes", item.notes)
    if item.status in ("Done", "N/A"):
        item.completed_by_id = current_user.id
        item.completed_at = datetime.utcnow()
    else:
        item.completed_by_id = None
        item.completed_at = None
        # Re-opening a previously completed item invalidates any earlier
        # review/partner sign-off - it needs to be looked at again once re-done.
        item.reviewed_by_id = None
        item.reviewed_at = None
        item.partner_signed_by_id = None
        item.partner_signed_at = None
    db.session.commit()
    flash("Checklist item updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/review", methods=["POST"])
@login_required
def review_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if item.status not in ("Done", "N/A"):
        flash("This item needs to be prepared (marked Done or N/A) before it can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    if item.completed_by_id == current_user.id:
        flash("You can't review your own work - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    item.reviewed_by_id = current_user.id
    item.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Checklist item marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/unreview", methods=["POST"])
@login_required
def unreview_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    item.reviewed_by_id = None
    item.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if item.status not in ("Done", "N/A"):
        flash("This item needs to be prepared (marked Done or N/A) before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    if item.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on work you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    item.partner_signed_by_id = current_user.id
    item.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    item.partner_signed_by_id = None
    item.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


# ---------- Understanding the Entity (system-based, structured prompts) ----------

@engagements_bp.route("/<int:engagement_id>/entity-understanding/save", methods=["POST"])
@login_required
def save_entity_understanding(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = EntityUnderstanding(engagement_id=engagement_id)
        db.session.add(record)

    for field, _, _ in ENTITY_UNDERSTANDING_FIELDS:
        setattr(record, field, request.form.get(field, "").strip())

    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    # Changing the write-up invalidates any earlier review/partner sign-off.
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None

    db.session.commit()
    flash("Understanding of the entity saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/review", methods=["POST"])
@login_required
def review_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not record.is_complete:
        flash("All sections need to be filled in before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    if record.completed_by_id == current_user.id:
        flash("You can't review your own write-up - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/unreview", methods=["POST"])
@login_required
def unreview_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not record.is_complete:
        flash("All sections need to be filled in before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a write-up you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


def _get_or_create_entity_understanding(engagement_id):
    record = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = EntityUnderstanding(engagement_id=engagement_id)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/entity-understanding/checklist/seed", methods=["POST"])
@login_required
def seed_entity_checklist(engagement_id):
    """Populate the Understanding Business/Assignment checklist with the
    firm's Forensic Audit questions (Investigative Engagements only) -
    mirrors acceptance.seed_acceptance_checklist: only does anything the
    first time, so it's safe to expose as a single button."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_entity_understanding(engagement_id)
    if record.checklist_items:
        flash("The checklist already has items on it.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))
    for order, (section, item_text) in enumerate(FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS, start=1):
        db.session.add(EntityUnderstandingChecklistItem(
            entity_understanding_id=record.id,
            section=section,
            item_text=item_text,
            order=order,
            created_by_id=current_user.id,
        ))
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Default checklist items added - tick and comment on each one.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/<int:engagement_id>/entity-understanding/checklist/add", methods=["POST"])
@login_required
def add_entity_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_entity_understanding(engagement_id)
    item_text = request.form.get("item_text", "").strip()
    if not item_text:
        flash("Enter the checklist item's wording before adding it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))
    max_order = max([i.order for i in record.checklist_items], default=0)
    db.session.add(EntityUnderstandingChecklistItem(
        entity_understanding_id=record.id,
        section=request.form.get("section", "").strip(),
        item_text=item_text,
        order=max_order + 1,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_entity_checklist_item(item_id):
    item = EntityUnderstandingChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.entity_understanding.engagement)
    response = request.form.get("response", "").strip()
    item.response = response if response in CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES else ""
    item.comment = request.form.get("comment", "").strip()
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=item.entity_understanding.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_entity_checklist_item(item_id):
    item = EntityUnderstandingChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.entity_understanding.engagement)
    engagement_id = item.entity_understanding.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


# ---------- Analytical Review (system-based: log figures, system flags fluctuations) ----------

def _get_or_create_analytical_review(engagement_id):
    review = AnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
    if not review:
        review = AnalyticalReview(engagement_id=engagement_id)
        db.session.add(review)
        db.session.flush()
    return review


def _touch_analytical_review(review):
    """Every change to the analytical review (a line added/edited/removed,
    or the threshold changed) re-records who last worked on it and clears
    any stale review sign-off, the same convention used everywhere else."""
    review.completed_by_id = current_user.id
    review.completed_at = datetime.utcnow()
    review.reviewed_by_id = None
    review.reviewed_at = None
    review.partner_signed_by_id = None
    review.partner_signed_at = None


@engagements_bp.route("/<int:engagement_id>/analytical-review/generate-from-tb", methods=["POST"])
@login_required
def generate_analytical_review_from_trial_balance(engagement_id):
    """Fills in Analytical Review line items automatically from the
    engagement's preliminary Trial Balance (entered/imported on the
    Planning tab), instead of the auditor having to type in every current
    vs prior year figure by hand. Reuses financials.build_all_statements()
    - the same maths behind the Finalisation tab's financial statements -
    on the trial balance's PRELIMINARY (unadjusted) figures, since
    Analytical Review is a risk-assessment/planning procedure that happens
    before audit adjustments exist.

    Only ever touches lines it generated last time (source="auto"),
    matched by label - a manually-added line with the same label as a
    generated one, or an explanation already typed against a generated
    line, is left alone. Safe to run again after the trial balance changes."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the preliminary trial balance (Planning tab) before generating analytical review figures from it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))

    review = _get_or_create_analytical_review(engagement_id)
    statements = fin.build_all_statements(trial_balance.lines)
    sfp = statements["sfp"]
    total_liabilities_current = sfp["ncl_total"]["current"] + sfp["cl_total"]["current"]
    total_liabilities_prior = sfp["ncl_total"]["prior"] + sfp["cl_total"]["prior"]

    generated = [(r["label"], r["current"], r["prior"]) for r in statements["pl"]["rows"]]
    generated += [
        ("Total assets", sfp["total_assets"]["current"], sfp["total_assets"]["prior"]),
        ("Total liabilities", total_liabilities_current, total_liabilities_prior),
        ("Total equity", sfp["total_equity"]["current"], sfp["total_equity"]["prior"]),
    ]

    existing_auto = {l.label: l for l in review.lines if l.source == "auto"}
    for label, current, prior in generated:
        line = existing_auto.get(label)
        if line:
            line.current_amount = current
            line.prior_amount = prior
        else:
            db.session.add(AnalyticalReviewLine(
                analytical_review_id=review.id, label=label,
                current_amount=current, prior_amount=prior, source="auto",
            ))
    _touch_analytical_review(review)
    db.session.commit()
    flash("Analytical review figures generated from the preliminary trial balance.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/threshold", methods=["POST"])
@login_required
def save_analytical_review_threshold(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    try:
        threshold = float(request.form.get("threshold_pct", 10.0))
    except ValueError:
        threshold = 10.0
    review.threshold_pct = max(0.1, threshold)
    _touch_analytical_review(review)
    db.session.commit()
    flash("Significance threshold updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/lines/add", methods=["POST"])
@login_required
def add_analytical_review_line(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)

    label = request.form.get("label", "").strip()
    if not label:
        flash("Please name the line item (e.g. Revenue, Gross profit).", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))

    def _float_or_none(name):
        raw = request.form.get(name, "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    line = AnalyticalReviewLine(
        analytical_review_id=review.id,
        label=label,
        prior_amount=_float_or_none("prior_amount"),
        current_amount=_float_or_none("current_amount"),
        explanation=request.form.get("explanation", "").strip(),
    )
    db.session.add(line)
    _touch_analytical_review(review)
    db.session.commit()
    flash("Line added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_analytical_review_line(line_id):
    line = AnalyticalReviewLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.review.engagement)
    review = line.review

    def _float_or_none(name, current):
        raw = request.form.get(name)
        if raw is None:
            return current
        raw = raw.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return current

    line.label = request.form.get("label", line.label).strip() or line.label
    line.prior_amount = _float_or_none("prior_amount", line.prior_amount)
    line.current_amount = _float_or_none("current_amount", line.current_amount)
    line.explanation = request.form.get("explanation", line.explanation or "").strip()
    # A hand-edit "claims" this line from Generate from Trial Balance - it
    # will no longer be silently overwritten by a later regenerate.
    line.source = "manual"
    _touch_analytical_review(review)
    db.session.commit()
    flash("Line updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_analytical_review_line(line_id):
    line = AnalyticalReviewLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.review.engagement)
    review = line.review
    engagement_id = review.engagement_id
    db.session.delete(line)
    _touch_analytical_review(review)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/review", methods=["POST"])
@login_required
def review_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not review.lines:
        flash("Add at least one line item before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    if review.completed_by_id == current_user.id:
        flash("You can't review your own analytical review - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    review.reviewed_by_id = current_user.id
    review.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Analytical review marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/unreview", methods=["POST"])
@login_required
def unreview_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    review.reviewed_by_id = None
    review.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not review.lines:
        flash("Add at least one line item before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    if review.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an analytical review you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))
    review.partner_signed_by_id = current_user.id
    review.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


@engagements_bp.route("/analytical-review/<int:review_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_analytical_review(review_id):
    review = AnalyticalReview.query.get_or_404(review_id)
    _ensure_engagement_access(review.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    review.partner_signed_by_id = None
    review.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=review.engagement_id, tab="analytical"))


# ---------- Risk Assessment (system-based questionnaire) ----------

@engagements_bp.route("/<int:engagement_id>/risk-assessment/save", methods=["POST"])
@login_required
def save_risk_assessment(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
    if not assessment:
        assessment = RiskAssessment(engagement_id=engagement_id)
        db.session.add(assessment)

    # An Investigative Engagement is answered against the Fraud Triangle
    # questionnaire (FORENSIC_RISK_LIKELIHOOD_QUESTIONS/_IMPACT_QUESTIONS) -
    # every other type keeps the ordinary audit questionnaire. Must match
    # view_engagement's likelihood_questions/impact_questions above exactly,
    # or a submitted form's fields wouldn't line up with what gets saved.
    if engagement.type == "Investigative Engagement":
        questions = FORENSIC_RISK_LIKELIHOOD_QUESTIONS + FORENSIC_RISK_IMPACT_QUESTIONS
    else:
        questions = RISK_LIKELIHOOD_QUESTIONS + RISK_IMPACT_QUESTIONS

    for field, _, options in questions:
        raw = request.form.get(field)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = None
        if value is not None and 1 <= value <= len(options):
            setattr(assessment, field, value)

    assessment.notes = request.form.get("notes", "").strip()
    assessment.completed_by_id = current_user.id
    assessment.completed_at = datetime.utcnow()
    # Re-answering the questionnaire invalidates any earlier review/partner
    # sign-off - the updated assessment needs a fresh look.
    assessment.reviewed_by_id = None
    assessment.reviewed_at = None
    assessment.partner_signed_by_id = None
    assessment.partner_signed_at = None
    db.session.flush()

    # Keep the Substantive Procedures checklist in sync with the rating
    # that was just (re)computed, rather than leaving it static at whatever
    # it was the last time someone clicked "Generate suggested procedures" -
    # e.g. moving from Medium to High automatically adds the extra
    # high-risk procedures for every area already in use. Only runs if that
    # section has actually been started (see sync_substantive_procedures_if_started).
    added_count = sync_substantive_procedures_if_started(engagement_id)

    db.session.commit()
    if added_count:
        flash(
            f"Risk assessment saved - rating computed automatically below. "
            f"{added_count} suggested procedure(s) were also added to the Substantive Procedures checklist to match the updated rating.",
            "success",
        )
    else:
        flash("Risk assessment saved - rating computed automatically below.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/review", methods=["POST"])
@login_required
def review_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not assessment.is_complete:
        flash("The questionnaire needs to be fully answered before it can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    if assessment.completed_by_id == current_user.id:
        flash("You can't review your own risk assessment - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    assessment.reviewed_by_id = current_user.id
    assessment.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Risk assessment marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/unreview", methods=["POST"])
@login_required
def unreview_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    assessment.reviewed_by_id = None
    assessment.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not assessment.is_complete:
        flash("The questionnaire needs to be fully answered before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    if assessment.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a risk assessment you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))
    assessment.partner_signed_by_id = current_user.id
    assessment.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risk-assessment/<int:assessment_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_risk_assessment(assessment_id):
    assessment = RiskAssessment.query.get_or_404(assessment_id)
    _ensure_engagement_access(assessment.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    assessment.partner_signed_by_id = None
    assessment.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=assessment.engagement_id, tab="risks"))


@engagements_bp.route("/risks/<int:risk_id>/delete", methods=["POST"])
@login_required
def delete_risk(risk_id):
    risk = RiskItem.query.get_or_404(risk_id)
    _ensure_engagement_access(risk.engagement)
    engagement_id = risk.engagement_id
    db.session.delete(risk)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="risks"))


# ---------- Documents ----------

def _allowed_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def _save_engagement_document(engagement_id, file, category, reference, notes, substantive_area_id=None):
    """Shared save logic for an engagement working paper/document - used by
    both the general Documents tab upload and by filing a working paper
    directly under a Substantive Procedures area. Saves the file to disk,
    works out its version number (same name+category counts as a new
    version), and returns the new (uncommitted) Document row; the caller is
    responsible for db.session.commit()."""
    original_name = secure_filename(file.filename)

    # simple versioning: count existing docs with same original name+category in this engagement
    existing = Document.query.filter_by(
        engagement_id=engagement_id, original_filename=original_name, category=category
    ).count()
    version = existing + 1

    stored_name = f"eng{engagement_id}_{uuid.uuid4().hex[:10]}_{original_name}"
    file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name))

    doc = Document(
        engagement_id=engagement_id,
        original_filename=original_name,
        stored_filename=stored_name,
        category=category,
        reference=reference,
        version=version,
        notes=notes,
        uploaded_by_id=current_user.id,
        substantive_area_id=substantive_area_id,
    )
    db.session.add(doc)
    return doc, version


@engagements_bp.route("/<int:engagement_id>/documents/upload", methods=["POST"])
@login_required
def upload_document(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))

    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))

    category = request.form.get("category", "General").strip() or "General"
    reference = request.form.get("reference", "").strip()
    doc, version = _save_engagement_document(
        engagement_id, file, category, reference, request.form.get("notes", "").strip()
    )
    db.session.commit()
    flash(f"Uploaded '{doc.original_filename}' (v{version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))


@engagements_bp.route("/<int:engagement_id>/substantive-procedures/areas/<area_name>/documents/upload", methods=["POST"])
@login_required
def upload_substantive_area_document(engagement_id, area_name):
    """File a working paper directly under one Substantive Procedures
    section (e.g. "Cash and Bank") - the same underlying Document as the
    Documents tab, just tagged with which audit area it supports, and
    filed under a category named after that area so it's easy to spot in
    the general Documents list too."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    valid_areas = FORENSIC_SUBSTANTIVE_AREAS if engagement.type == "Investigative Engagement" else AUDIT_AREAS
    if area_name not in valid_areas:
        abort(404)

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))

    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))

    area = _get_or_create_substantive_area(engagement_id, area_name)
    reference = request.form.get("reference", "").strip()
    doc, version = _save_engagement_document(
        engagement_id, file, area_name, reference, request.form.get("notes", "").strip(),
        substantive_area_id=area.id,
    )
    db.session.commit()
    flash(f"Filed '{doc.original_filename}' (v{version}) under {area_name}.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    # require_accepted=False: the signed engagement letter (uploaded via the
    # Client Acceptance tab, itself an ordinary Document) has to be
    # downloadable/viewable WHILE acceptance is still pending, otherwise
    # nobody could ever check it before signing off the decision.
    _ensure_engagement_access(doc.engagement, require_accepted=False)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], doc.stored_filename, as_attachment=True,
        download_name=doc.original_filename,
    )


@engagements_bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_document(doc_id):
    if not user_has_permission(current_user, "delete_documents"):
        abort(403)
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    engagement_id = doc.engagement_id
    return_tab = "substantive" if doc.substantive_area_id else "documents"
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_filename))
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=return_tab))


# ---------- Tasks ----------

@engagements_bp.route("/<int:engagement_id>/tasks/add", methods=["POST"])
@login_required
def add_task(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    due_date = request.form.get("due_date")
    task = EngagementTask(
        engagement_id=engagement_id,
        title=request.form.get("title", "").strip(),
        description=request.form.get("description", "").strip(),
        assigned_to_id=request.form.get("assigned_to_id") or None,
        due_date=datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None,
        priority=request.form.get("priority", "Normal"),
        status=request.form.get("status", "To Do"),
    )
    db.session.add(task)
    db.session.commit()
    flash("Task assigned.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tasks"))


def _task_redirect(task, tab="tasks"):
    """Most task actions are reachable both from an engagement's Tasks tab
    and from the firm-wide Project Management board - a hidden
    return_to=board field on those forms sends the user back to the board
    instead of the engagement page."""
    if request.form.get("return_to") == "board":
        return redirect(url_for("hr.project_board"))
    return redirect(url_for("engagements.view_engagement", engagement_id=task.engagement_id, tab=tab))


@engagements_bp.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def update_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    task.title = request.form.get("title", task.title)
    task.description = request.form.get("description", task.description)
    task.assigned_to_id = request.form.get("assigned_to_id") or None
    due_date = request.form.get("due_date")
    task.due_date = datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None
    task.priority = request.form.get("priority", task.priority)
    task.status = request.form.get("status", task.status)
    if task.status == "Done":
        task.completed_by_id = current_user.id
        task.completed_at = datetime.utcnow()
    else:
        task.completed_by_id = None
        task.completed_at = None
        # Re-opening a previously completed task invalidates any earlier
        # review/partner sign-off - it needs to be looked at again once re-done.
        task.reviewed_by_id = None
        task.reviewed_at = None
        task.partner_signed_by_id = None
        task.partner_signed_at = None
    db.session.commit()
    flash("Task updated.", "success")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/review", methods=["POST"])
@login_required
def review_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if task.status != "Done":
        flash("This task needs to be marked Done before it can be reviewed.", "danger")
        return _task_redirect(task)
    if task.completed_by_id == current_user.id:
        flash("You can't review your own work - ask another supervisor/partner to review it.", "danger")
        return _task_redirect(task)
    task.reviewed_by_id = current_user.id
    task.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Task marked as reviewed.", "success")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/unreview", methods=["POST"])
@login_required
def unreview_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    task.reviewed_by_id = None
    task.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if task.status != "Done":
        flash("This task needs to be marked Done before the partner can sign off.", "danger")
        return _task_redirect(task)
    if task.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on work you prepared yourself - ask another partner to sign off.", "danger")
        return _task_redirect(task)
    task.partner_signed_by_id = current_user.id
    task.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    task.partner_signed_by_id = None
    task.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return _task_redirect(task)


@engagements_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    task = EngagementTask.query.get_or_404(task_id)
    _ensure_engagement_access(task.engagement)
    engagement_id = task.engagement_id
    return_to_board = request.form.get("return_to") == "board"
    db.session.delete(task)
    db.session.commit()
    if return_to_board:
        return redirect(url_for("hr.project_board"))
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tasks"))


# ---------- Checklist templates (admin) ----------

@engagements_bp.route("/templates")
@login_required
def list_templates():
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()
    return render_template("engagements/templates_list.html", templates=templates)


@engagements_bp.route("/templates/new", methods=["GET", "POST"])
@login_required
def new_template():
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    if request.method == "POST":
        template = ChecklistTemplate(
            name=request.form.get("name", "").strip(),
            type=request.form.get("type", "Audit"),
            description=request.form.get("description", "").strip(),
        )
        db.session.add(template)
        db.session.commit()
        flash("Template created. Add items to it below.", "success")
        return redirect(url_for("engagements.view_template", template_id=template.id))
    return render_template("engagements/template_form.html", template=None, types=ENGAGEMENT_TYPES)


@engagements_bp.route("/templates/<int:template_id>")
@login_required
def view_template(template_id):
    template = ChecklistTemplate.query.get_or_404(template_id)
    return render_template("engagements/template_detail.html", template=template)


@engagements_bp.route("/templates/<int:template_id>/items/add", methods=["POST"])
@login_required
def add_template_item(template_id):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    from models import ChecklistTemplateItem
    template = ChecklistTemplate.query.get_or_404(template_id)
    max_order = max([i.order for i in template.items], default=0)
    item = ChecklistTemplateItem(
        template_id=template.id,
        section=request.form.get("section", "").strip(),
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
    )
    db.session.add(item)
    db.session.commit()
    return redirect(url_for("engagements.view_template", template_id=template_id))


@engagements_bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def delete_template(template_id):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    template = ChecklistTemplate.query.get_or_404(template_id)
    db.session.delete(template)
    db.session.commit()
    flash("Template deleted.", "info")
    return redirect(url_for("engagements.list_templates"))


# ---------- Staffing (date-ranged allocations, feeds the HR & Admin Planner) ----------

@engagements_bp.route("/<int:engagement_id>/staffing/add", methods=["POST"])
@login_required
def add_staff_allocation(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    user_id = request.form.get("user_id")
    start_date = request.form.get("start_date")
    end_date = request.form.get("end_date")

    if not user_id or not start_date or not end_date:
        flash("Please choose a staff member and both dates.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        flash("End date can't be before the start date.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))

    try:
        pct = int(request.form.get("allocation_pct", 100) or 100)
    except ValueError:
        pct = 100
    pct = max(1, min(pct, 100))

    allocation = StaffAllocation(
        engagement_id=engagement_id,
        user_id=int(user_id),
        start_date=start,
        end_date=end,
        allocation_pct=pct,
        notes=request.form.get("notes", "").strip(),
    )
    db.session.add(allocation)
    db.session.commit()
    flash("Staffing allocation added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))


@engagements_bp.route("/staffing/<int:allocation_id>/delete", methods=["POST"])
@login_required
def delete_staff_allocation(allocation_id):
    allocation = StaffAllocation.query.get_or_404(allocation_id)
    _ensure_engagement_access(allocation.engagement)
    engagement_id = allocation.engagement_id
    db.session.delete(allocation)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="overview"))


# ---------- Planning: materiality calculator ----------

@engagements_bp.route("/<int:engagement_id>/materiality/save", methods=["POST"])
@login_required
def save_materiality(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    calc = MaterialityCalculation.query.filter_by(engagement_id=engagement_id).first()
    if not calc:
        calc = MaterialityCalculation(engagement_id=engagement_id)
        db.session.add(calc)

    def _float_or_none(name):
        raw = request.form.get(name, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    calc.total_revenue = _float_or_none("total_revenue")
    calc.profit_before_tax = _float_or_none("profit_before_tax")
    calc.total_assets = _float_or_none("total_assets")
    calc.revenue_pct = _float_or_none("revenue_pct") or 1.0
    calc.pbt_pct = _float_or_none("pbt_pct") or 5.0
    calc.assets_pct = _float_or_none("assets_pct") or 1.0
    calc.performance_pct = _float_or_none("performance_pct") or 75.0
    calc.trivial_pct = _float_or_none("trivial_pct") or 5.0
    basis = request.form.get("basis", "highest")
    calc.basis = basis if basis in ("revenue", "pbt", "assets", "highest", "lowest") else "highest"
    calc.notes = request.form.get("notes", "").strip()
    calc.updated_by_id = current_user.id
    # Changing the calculation invalidates any earlier review/partner sign-off.
    calc.reviewed_by_id = None
    calc.reviewed_at = None
    calc.partner_signed_by_id = None
    calc.partner_signed_at = None

    db.session.commit()
    flash("Materiality calculation saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/review", methods=["POST"])
@login_required
def review_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if calc.overall_materiality is None:
        flash("Enter at least one financial figure before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    if calc.updated_by_id == current_user.id:
        flash("You can't review a materiality calculation you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    calc.reviewed_by_id = current_user.id
    calc.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Materiality calculation marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/unreview", methods=["POST"])
@login_required
def unreview_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    calc.reviewed_by_id = None
    calc.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if calc.overall_materiality is None:
        flash("Enter at least one financial figure before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    if calc.updated_by_id == current_user.id:
        flash("You can't give the partner sign-off on a calculation you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))
    calc.partner_signed_by_id = current_user.id
    calc.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


@engagements_bp.route("/materiality/<int:calc_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_materiality(calc_id):
    calc = MaterialityCalculation.query.get_or_404(calc_id)
    _ensure_engagement_access(calc.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    calc.partner_signed_by_id = None
    calc.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=calc.engagement_id, tab="planning"))


# ---------- Audit Strategy (Investigative Engagements only - replaces the ordinary Planning tab content) ----------

@engagements_bp.route("/<int:engagement_id>/audit-strategy/save", methods=["POST"])
@login_required
def save_audit_strategy(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    strategy = AuditStrategy.query.filter_by(engagement_id=engagement_id).first()
    if not strategy:
        strategy = AuditStrategy(engagement_id=engagement_id)
        db.session.add(strategy)

    strategy.objective_statement = request.form.get("objective_statement", "").strip()
    strategy.chain_of_custody_notes = request.form.get("chain_of_custody_notes", "").strip()
    strategy.reporting_destination = request.form.get("reporting_destination", "").strip()

    strategy.potential_perpetrators = request.form.get("potential_perpetrators", "").strip()
    strategy.fraud_vulnerability = request.form.get("fraud_vulnerability", "").strip()
    strategy.evidentiary_red_flags = request.form.get("evidentiary_red_flags", "").strip()

    strategy.resource_forensic_tech_available = request.form.get("resource_forensic_tech_available") == "on"
    strategy.resource_forensic_tech_notes = request.form.get("resource_forensic_tech_notes", "").strip()
    strategy.resource_data_analysts_available = request.form.get("resource_data_analysts_available") == "on"
    strategy.resource_data_analysts_notes = request.form.get("resource_data_analysts_notes", "").strip()
    strategy.resource_interviewers_available = request.form.get("resource_interviewers_available") == "on"
    strategy.resource_interviewers_notes = request.form.get("resource_interviewers_notes", "").strip()
    strategy.resource_legal_counsel_available = request.form.get("resource_legal_counsel_available") == "on"
    strategy.resource_legal_counsel_notes = request.form.get("resource_legal_counsel_notes", "").strip()

    phase1_status = request.form.get("phase1_status", "Not Started")
    strategy.phase1_status = phase1_status if phase1_status in AUDIT_STRATEGY_PHASE_STATUSES else "Not Started"
    strategy.phase1_notes = request.form.get("phase1_notes", "").strip()
    phase2_status = request.form.get("phase2_status", "Not Started")
    strategy.phase2_status = phase2_status if phase2_status in AUDIT_STRATEGY_PHASE_STATUSES else "Not Started"
    strategy.phase2_notes = request.form.get("phase2_notes", "").strip()
    phase3_status = request.form.get("phase3_status", "Not Started")
    strategy.phase3_status = phase3_status if phase3_status in AUDIT_STRATEGY_PHASE_STATUSES else "Not Started"
    strategy.phase3_notes = request.form.get("phase3_notes", "").strip()

    strategy.completed_by_id = current_user.id
    strategy.completed_at = datetime.utcnow()
    # Re-saving the strategy invalidates any earlier review/partner sign-off.
    strategy.reviewed_by_id = None
    strategy.reviewed_at = None
    strategy.partner_signed_by_id = None
    strategy.partner_signed_at = None

    db.session.commit()
    flash("Audit strategy saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/review", methods=["POST"])
@login_required
def review_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not strategy.is_complete:
        flash("Fill in the Scope, Fraud Theory and Legal Framework fields before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    if strategy.completed_by_id == current_user.id:
        flash("You can't review an audit strategy you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    strategy.reviewed_by_id = current_user.id
    strategy.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Audit strategy marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/unreview", methods=["POST"])
@login_required
def unreview_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    strategy.reviewed_by_id = None
    strategy.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not strategy.is_complete:
        flash("Fill in the Scope, Fraud Theory and Legal Framework fields before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    if strategy.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a strategy you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))
    strategy.partner_signed_by_id = current_user.id
    strategy.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


@engagements_bp.route("/audit-strategy/<int:strategy_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_audit_strategy(strategy_id):
    strategy = AuditStrategy.query.get_or_404(strategy_id)
    _ensure_engagement_access(strategy.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    strategy.partner_signed_by_id = None
    strategy.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=strategy.engagement_id, tab="planning"))


# ---------- Finalisation checklist ("what's left to close this engagement") ----------

def _get_or_create_finalisation_checklist(engagement_id):
    record = FinalisationChecklist.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = FinalisationChecklist(engagement_id=engagement_id)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/finalisation-checklist/seed", methods=["POST"])
@login_required
def seed_finalisation_checklist(engagement_id):
    """Populate the Finalisation checklist with the firm's default items -
    the forensic set (see FORENSIC_FINALISATION_CHECKLIST_ITEMS) on an
    Investigative Engagement, the ordinary close-out set (see DEFAULT_
    FINALISATION_CHECKLIST_ITEMS) otherwise. Mirrors seed_entity_checklist:
    only does anything the first time, so it's safe to expose as a single
    button."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_finalisation_checklist(engagement_id)
    if record.checklist_items:
        flash("The checklist already has items on it.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    source_items = (
        FORENSIC_FINALISATION_CHECKLIST_ITEMS if engagement.type == "Investigative Engagement"
        else DEFAULT_FINALISATION_CHECKLIST_ITEMS
    )
    for order, (section, item_text) in enumerate(source_items, start=1):
        db.session.add(FinalisationChecklistItem(
            finalisation_checklist_id=record.id,
            section=section,
            item_text=item_text,
            order=order,
            created_by_id=current_user.id,
        ))
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Default checklist items added - tick and comment on each one.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/finalisation-checklist/add", methods=["POST"])
@login_required
def add_finalisation_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_finalisation_checklist(engagement_id)
    item_text = request.form.get("item_text", "").strip()
    if not item_text:
        flash("Enter the checklist item's wording before adding it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    max_order = max([i.order for i in record.checklist_items], default=0)
    db.session.add(FinalisationChecklistItem(
        finalisation_checklist_id=record.id,
        section=request.form.get("section", "").strip(),
        item_text=item_text,
        order=max_order + 1,
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_finalisation_checklist_item(item_id):
    item = FinalisationChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.finalisation_checklist.engagement)
    response = request.form.get("response", "").strip()
    item.response = response if response in CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES else ""
    item.comment = request.form.get("comment", "").strip()
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=item.finalisation_checklist.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_finalisation_checklist_item(item_id):
    item = FinalisationChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.finalisation_checklist.engagement)
    engagement_id = item.finalisation_checklist.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/review", methods=["POST"])
@login_required
def review_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not record.is_complete:
        flash("Every checklist item needs a response before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    if record.completed_by_id == current_user.id:
        flash("You can't review a checklist you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Finalisation checklist marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/unreview", methods=["POST"])
@login_required
def unreview_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not record.is_complete:
        flash("Every checklist item needs a response before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a checklist you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/finalisation-checklist/<int:record_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_finalisation_checklist(record_id):
    record = FinalisationChecklist.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


# ---------- Audit Finalisation: Trial Balance import + IAS 1 Financial Statements ----------

def _allowed_tb_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ("xlsx", "xls", "csv")


def _read_tb_upload_rows(file_storage, filename):
    """Returns a list of {header: value} dicts from an uploaded .xlsx/.xls
    or .csv trial balance file. Raises ValueError with a plain-English
    message if it can't find a usable header row."""
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        content = file_storage.read().decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(content)))

    workbook = openpyxl.load_workbook(file_storage, data_only=True)
    sheet = workbook.active
    all_rows = list(sheet.iter_rows(values_only=True))
    header_idx = None
    for i, row in enumerate(all_rows):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if "account name" in cells:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Could not find a header row containing 'Account Name' in that file. "
            "Download the template below and use its column headers."
        )
    headers = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
    data_rows = []
    for row in all_rows[header_idx + 1:]:
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        data_rows.append({h: v for h, v in zip(headers, row) if h})
    return data_rows


def _get_or_create_trial_balance(engagement_id, source="manual"):
    tb = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not tb:
        tb = TrialBalance(engagement_id=engagement_id, source=source)
        db.session.add(tb)
        db.session.flush()
    return tb


def _touch_trial_balance(tb):
    tb.completed_by_id = current_user.id
    tb.completed_at = datetime.utcnow()
    tb.reviewed_by_id = None
    tb.reviewed_at = None
    tb.partner_signed_by_id = None
    tb.partner_signed_at = None


def _lookup_coa_mapping(client_id, account_name):
    norm = fin.normalize_account_name(account_name)
    if not norm:
        return None
    return COAMapping.query.filter_by(client_id=client_id, account_name=norm).first()


def _upsert_coa_mapping(client_id, account_name, fs_category, user_id):
    norm = fin.normalize_account_name(account_name)
    if not norm or not fs_category:
        return
    mapping = COAMapping.query.filter_by(client_id=client_id, account_name=norm).first()
    if not mapping:
        mapping = COAMapping(client_id=client_id, account_name=norm)
        db.session.add(mapping)
    mapping.fs_category = fs_category
    mapping.updated_by_id = user_id
    mapping.updated_at = datetime.utcnow()


@engagements_bp.route("/<int:engagement_id>/trial-balance/upload", methods=["POST"])
@login_required
def upload_trial_balance(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    if not _allowed_tb_file(file.filename):
        flash("Please upload a .xlsx or .csv file.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))

    original_name = secure_filename(file.filename)
    try:
        raw_rows = _read_tb_upload_rows(file, original_name)
        cleaned_rows = fin.parse_tb_rows(raw_rows)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    except Exception:
        flash("Could not read that file - make sure it's a .xlsx or .csv using the template's columns.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))

    tb = _get_or_create_trial_balance(engagement_id, source="upload")
    tb.source = "upload"
    tb.original_filename = original_name

    # Preserve any category already set on the existing lines (in case it
    # was set only on this TB and hadn't been saved to the reusable
    # mapping yet) before replacing them with the freshly imported rows.
    for existing_line in tb.lines:
        if existing_line.fs_category:
            _upsert_coa_mapping(engagement.client_id, existing_line.account_name, existing_line.fs_category, current_user.id)
    TrialBalanceLine.query.filter_by(trial_balance_id=tb.id).delete()

    for row in cleaned_rows:
        mapping = _lookup_coa_mapping(engagement.client_id, row["account_name"])
        db.session.add(TrialBalanceLine(
            trial_balance_id=tb.id,
            account_code=row["account_code"],
            account_name=row["account_name"],
            fs_category=mapping.fs_category if mapping else None,
            current_debit=row["current_debit"], current_credit=row["current_credit"],
            prior_debit=row["prior_debit"], prior_credit=row["prior_credit"],
        ))

    _touch_trial_balance(tb)
    db.session.commit()
    flash(f"Imported {len(cleaned_rows)} account(s) from '{original_name}'.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/trial-balance/lines/add", methods=["POST"])
@login_required
def add_trial_balance_line(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    name = request.form.get("account_name", "").strip()
    if not name:
        flash("Please give the account a name.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))

    def to_float(field):
        raw = request.form.get(field, "").strip()
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    tb = _get_or_create_trial_balance(engagement_id, source="manual")
    category = request.form.get("fs_category", "").strip() or None
    db.session.add(TrialBalanceLine(
        trial_balance_id=tb.id,
        account_code=request.form.get("account_code", "").strip(),
        account_name=name,
        fs_category=category,
        current_debit=to_float("current_debit"), current_credit=to_float("current_credit"),
        prior_debit=to_float("prior_debit"), prior_credit=to_float("prior_credit"),
    ))
    if category:
        _upsert_coa_mapping(engagement.client_id, name, category, current_user.id)
    _touch_trial_balance(tb)
    db.session.commit()
    flash("Account added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/trial-balance/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_trial_balance_line(line_id):
    line = TrialBalanceLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.trial_balance.engagement)
    tb = line.trial_balance
    engagement = tb.engagement

    def to_float(field, current):
        raw = request.form.get(field)
        if raw is None:
            return current
        raw = raw.strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return current

    line.account_code = request.form.get("account_code", line.account_code or "").strip()
    line.account_name = request.form.get("account_name", line.account_name).strip() or line.account_name
    category = request.form.get("fs_category", "").strip() or None
    line.fs_category = category
    line.current_debit = to_float("current_debit", line.current_debit)
    line.current_credit = to_float("current_credit", line.current_credit)
    line.prior_debit = to_float("prior_debit", line.prior_debit)
    line.prior_credit = to_float("prior_credit", line.prior_credit)
    if category:
        _upsert_coa_mapping(engagement.client_id, line.account_name, category, current_user.id)
    _touch_trial_balance(tb)
    db.session.commit()
    flash("Account updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="finalisation"))


@engagements_bp.route("/trial-balance/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_trial_balance_line(line_id):
    line = TrialBalanceLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.trial_balance.engagement)
    tb = line.trial_balance
    engagement_id = tb.engagement_id
    db.session.delete(line)
    _touch_trial_balance(tb)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/trial-balance/<int:tb_id>/review", methods=["POST"])
@login_required
def review_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not tb.is_fully_mapped:
        flash("Every account needs an IAS 1 category (or 'Excluded') before the trial balance can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))
    if tb.completed_by_id == current_user.id:
        flash("You can't review a trial balance you imported/entered yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))
    tb.reviewed_by_id = current_user.id
    tb.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Trial balance marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))


@engagements_bp.route("/trial-balance/<int:tb_id>/unreview", methods=["POST"])
@login_required
def unreview_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    tb.reviewed_by_id = None
    tb.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))


@engagements_bp.route("/trial-balance/<int:tb_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not tb.is_fully_mapped:
        flash("Every account needs an IAS 1 category (or 'Excluded') before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))
    if tb.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a trial balance you imported/entered yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))
    tb.partner_signed_by_id = current_user.id
    tb.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))


@engagements_bp.route("/trial-balance/<int:tb_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    tb.partner_signed_by_id = None
    tb.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))


@engagements_bp.route("/trial-balance/<int:tb_id>/adjustments/add", methods=["POST"])
@login_required
def add_audit_adjustment(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    reference = request.form.get("reference", "").strip() or f"AJE {len(tb.adjustments) + 1}"
    adjustment = AuditAdjustment(
        trial_balance_id=tb.id,
        reference=reference,
        description=request.form.get("description", "").strip(),
        completed_by_id=current_user.id,
        completed_at=datetime.utcnow(),
    )
    db.session.add(adjustment)
    db.session.commit()
    flash(f"Adjustment '{reference}' added - now add its debit/credit lines below.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))


def _touch_adjustment(adjustment):
    """Every change to an adjustment (its reference/description, or a line
    added/edited/removed) re-records who last worked on it and clears any
    stale review/partner sign-off - the same convention used everywhere
    else in the app."""
    adjustment.completed_by_id = current_user.id
    adjustment.completed_at = datetime.utcnow()
    adjustment.reviewed_by_id = None
    adjustment.reviewed_at = None
    adjustment.partner_signed_by_id = None
    adjustment.partner_signed_at = None


@engagements_bp.route("/adjustments/<int:adjustment_id>/update", methods=["POST"])
@login_required
def update_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    adjustment.reference = request.form.get("reference", adjustment.reference or "").strip() or adjustment.reference
    adjustment.description = request.form.get("description", "").strip()
    _touch_adjustment(adjustment)
    db.session.commit()
    flash("Adjustment updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/delete", methods=["POST"])
@login_required
def delete_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    engagement_id = adjustment.trial_balance.engagement_id
    db.session.delete(adjustment)
    db.session.commit()
    flash("Adjustment deleted.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/lines/add", methods=["POST"])
@login_required
def add_audit_adjustment_line(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    name = request.form.get("account_name", "").strip()
    category = request.form.get("fs_category", "").strip()
    if not name or not category:
        flash("Please give the line an account name and an IAS 1 category.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))

    def to_float(field):
        raw = request.form.get(field, "").strip()
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    db.session.add(AuditAdjustmentLine(
        adjustment_id=adjustment.id,
        account_name=name,
        fs_category=category,
        debit=to_float("debit"),
        credit=to_float("credit"),
    ))
    _touch_adjustment(adjustment)
    db.session.commit()
    flash("Adjustment line added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustment-lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_audit_adjustment_line(line_id):
    line = AuditAdjustmentLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.adjustment.trial_balance.engagement)
    adjustment = line.adjustment

    def to_float(field, current):
        raw = request.form.get(field)
        if raw is None:
            return current
        raw = raw.strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return current

    line.account_name = request.form.get("account_name", line.account_name).strip() or line.account_name
    line.fs_category = request.form.get("fs_category", line.fs_category).strip() or line.fs_category
    line.debit = to_float("debit", line.debit)
    line.credit = to_float("credit", line.credit)
    _touch_adjustment(adjustment)
    db.session.commit()
    flash("Adjustment line updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustment-lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_audit_adjustment_line(line_id):
    line = AuditAdjustmentLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.adjustment.trial_balance.engagement)
    adjustment = line.adjustment
    engagement_id = adjustment.trial_balance.engagement_id
    db.session.delete(line)
    _touch_adjustment(adjustment)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/review", methods=["POST"])
@login_required
def review_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not adjustment.lines or not adjustment.is_balanced:
        flash("The adjustment needs at least one line and must balance (debits = credits) before it can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    if adjustment.completed_by_id == current_user.id:
        flash("You can't review an adjustment you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    adjustment.reviewed_by_id = current_user.id
    adjustment.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Adjustment marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/unreview", methods=["POST"])
@login_required
def unreview_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    adjustment.reviewed_by_id = None
    adjustment.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not adjustment.lines or not adjustment.is_balanced:
        flash("The adjustment needs at least one line and must balance (debits = credits) before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    if adjustment.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an adjustment you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    adjustment.partner_signed_by_id = current_user.id
    adjustment.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    adjustment.partner_signed_by_id = None
    adjustment.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/financial-statements/save", methods=["POST"])
@login_required
def save_financial_statements_notes(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    fs = FinancialStatements.query.filter_by(engagement_id=engagement_id).first()
    if not fs:
        fs = FinancialStatements(engagement_id=engagement_id)
        db.session.add(fs)
    fs.basis_of_preparation = request.form.get("basis_of_preparation", "").strip()
    # Notes to the Financial Statements are free text and editable here, same
    # as the basis of preparation - but note that the FIGURES throughout the
    # statements themselves are never set from this form: they always come
    # live from the adjusted TrialBalance (financials.py) and stay that way.
    fs.notes_to_financial_statements = request.form.get("notes_to_financial_statements", "").strip()
    fs.completed_by_id = current_user.id
    fs.completed_at = datetime.utcnow()
    fs.reviewed_by_id = None
    fs.reviewed_at = None
    fs.partner_signed_by_id = None
    fs.partner_signed_at = None
    db.session.commit()
    flash("Financial statements notes saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/review", methods=["POST"])
@login_required
def review_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    trial_balance = TrialBalance.query.filter_by(engagement_id=fs.engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Import or enter the trial balance before the financial statements can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    if fs.completed_by_id == current_user.id:
        flash("You can't review financial statements you prepared yourself - ask another supervisor/partner to review them.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    fs.reviewed_by_id = current_user.id
    fs.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Financial statements marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/unreview", methods=["POST"])
@login_required
def unreview_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    fs.reviewed_by_id = None
    fs.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    trial_balance = TrialBalance.query.filter_by(engagement_id=fs.engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Import or enter the trial balance before the partner can sign off the financial statements.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    if fs.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on financial statements you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    fs.partner_signed_by_id = current_user.id
    fs.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    fs.partner_signed_by_id = None
    fs.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


# ---------- Workpaper Narratives (persistent, editable copies of the
# Management Representation Letter, Report to Management, and the Forensic
# Investigation Report's Executive Summary - see models.WorkpaperNarrative)
# ----------

def _get_or_seed_workpaper_narrative(engagement_id, kind):
    """The existing WorkpaperNarrative row for (engagement, kind), or a new
    unsaved one pre-filled with the firm's default starting wording - so the
    edit form always has sensible text to start from rather than a blank
    box, the first time this narrative is opened on an engagement."""
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind=kind).first()
    if not narrative:
        narrative = WorkpaperNarrative(engagement_id=engagement_id, kind=kind, body=DEFAULT_WORKPAPER_NARRATIVE_BODIES.get(kind, ""))
    return narrative


@engagements_bp.route("/<int:engagement_id>/workpaper-narrative/<string:kind>/save", methods=["POST"])
@login_required
def save_workpaper_narrative(engagement_id, kind):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if kind not in WORKPAPER_NARRATIVE_KIND_KEYS:
        abort(404)
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind=kind).first()
    if not narrative:
        narrative = WorkpaperNarrative(engagement_id=engagement_id, kind=kind)
        db.session.add(narrative)
    narrative.body = request.form.get("body", "").strip()
    narrative.completed_by_id = current_user.id
    narrative.completed_at = datetime.utcnow()
    # Editing after review/sign-off invalidates them, same as every other
    # workpaper - a changed write-up needs a fresh review.
    narrative.reviewed_by_id = None
    narrative.reviewed_at = None
    narrative.partner_signed_by_id = None
    narrative.partner_signed_at = None
    db.session.commit()
    flash(f"{narrative.label} saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/review", methods=["POST"])
@login_required
def review_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if narrative.completed_by_id == current_user.id:
        flash("You can't review a write-up you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab="finalisation"))
    narrative.reviewed_by_id = current_user.id
    narrative.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f"{narrative.label} marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab="finalisation"))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/unreview", methods=["POST"])
@login_required
def unreview_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    narrative.reviewed_by_id = None
    narrative.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab="finalisation"))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if narrative.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a write-up you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab="finalisation"))
    narrative.partner_signed_by_id = current_user.id
    narrative.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab="finalisation"))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    narrative.partner_signed_by_id = None
    narrative.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab="finalisation"))


# ---------- Substantive Procedures (system-based: by audit area, driven by risk + industry) ----------

def sync_substantive_procedures(engagement):
    """Additive top-up of suggested procedures across every area, driven by
    the engagement's CURRENT Risk Assessment rating and the client's
    CURRENT industry (see AUDIT_AREAS / BASELINE_SUBSTANTIVE_PROCEDURES /
    HIGH_RISK_EXTRA_PROCEDURES / INDUSTRY_EXTRA_PROCEDURES above) - or, on
    an Investigative Engagement, the four forensic evidence-type categories
    instead (see FORENSIC_SUBSTANTIVE_AREAS / FORENSIC_BASELINE_
    SUBSTANTIVE_PROCEDURES above; the risk/industry extras are audit-area
    captions and don't apply there, so a forensic engagement only ever gets
    its baseline list). This is the shared engine behind both the manual
    "Generate suggested procedures" button and the automatic re-sync fired
    whenever the risk rating or the client's industry changes (see
    sync_substantive_procedures_if_started, save_risk_assessment, and
    clients.edit_client) - so the suggested-procedures checklist keeps
    itself current instead of only ever reflecting whatever the risk
    rating/industry happened to be the first time someone clicked the
    button. Exactly like the manual button, it only ever ADDS whatever's
    newly applicable and not already present - it never edits, reorders or
    removes an existing item (ticked-off work, sign-offs and manually added
    procedures are never touched), so it's safe to call as often as
    needed. Returns how many procedures were added."""
    is_forensic = engagement.type == "Investigative Engagement"
    if is_forensic:
        areas = FORENSIC_SUBSTANTIVE_AREAS
        baseline = FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES
        high_risk = False
        industry_map = {}
    else:
        areas = AUDIT_AREAS
        baseline = BASELINE_SUBSTANTIVE_PROCEDURES
        risk_assessment = RiskAssessment.query.filter_by(engagement_id=engagement.id).first()
        high_risk = bool(risk_assessment and risk_assessment.rating == "High")
        industry_map = INDUSTRY_EXTRA_PROCEDURES.get(engagement.client.industry, {})

    added_count = 0
    for area_name in areas:
        area = SubstantiveProcedureArea.query.filter_by(engagement_id=engagement.id, area=area_name).first()
        if not area:
            area = SubstantiveProcedureArea(engagement_id=engagement.id, area=area_name)
            db.session.add(area)
            db.session.flush()

        desired = [(t, "baseline") for t in baseline.get(area_name, [])]
        if high_risk:
            desired += [(t, "risk") for t in HIGH_RISK_EXTRA_PROCEDURES.get(area_name, [])]
        desired += [(t, "industry") for t in industry_map.get(area_name, [])]

        existing_texts = {i.procedure_text for i in area.items}
        next_order = len(area.items)
        area_changed = False
        for text, source in desired:
            if text not in existing_texts:
                db.session.add(SubstantiveProcedureItem(area_id=area.id, procedure_text=text, source=source, order=next_order))
                next_order += 1
                added_count += 1
                area_changed = True
        if area_changed:
            # Only touch (and clear any existing sign-off on) areas that
            # actually gained new suggested procedures - re-running this on
            # an already-signed-off area with nothing new to add shouldn't
            # disturb it.
            _touch_substantive_area(area)

    return added_count


def sync_substantive_procedures_if_started(engagement_id):
    """Auto-sync wrapper used by triggers OTHER than the explicit "Generate
    suggested procedures" button (i.e. saving the Risk Assessment, and
    editing the client's industry) - deliberately only does anything when
    the Substantive Procedures section has already been started for this
    engagement (at least one area/procedure already exists), so it never
    conjures the whole audit-area section out of nowhere on an engagement
    that hasn't opened that tab, e.g. an Investigative Engagement, which
    doesn't use Substantive Procedures at all. Returns how many procedures
    were added (0 if nothing to do or the section hasn't been started)."""
    engagement = Engagement.query.get(engagement_id)
    if not engagement or not engagement.client:
        return 0
    if not SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).first():
        return 0
    return sync_substantive_procedures(engagement)


@engagements_bp.route("/<int:engagement_id>/substantive-procedures/generate", methods=["POST"])
@login_required
def generate_substantive_procedures(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    added_count = sync_substantive_procedures(engagement)
    db.session.commit()
    if engagement.type == "Investigative Engagement":
        if added_count:
            flash(f"Generated {added_count} suggested forensic procedure(s) across the four investigative categories.", "success")
        else:
            flash("No new suggested procedures to add - the full forensic procedure list is already there below.", "info")
    else:
        risk_assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
        high_risk = bool(risk_assessment and risk_assessment.rating == "High")
        if added_count:
            flash(f"Generated {added_count} suggested procedure(s) across the audit areas, based on the current risk rating{' (High)' if high_risk else ''} and the client's industry.", "success")
        else:
            flash("No new suggested procedures to add - everything suggested for the current risk rating and industry is already listed below.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


def _touch_substantive_area(area):
    """Every change within an area (a procedure added/edited/removed, or its
    notes changed) re-records who last worked on it and clears any stale
    review/partner sign-off - the same convention used everywhere else."""
    area.completed_by_id = current_user.id
    area.completed_at = datetime.utcnow()
    area.reviewed_by_id = None
    area.reviewed_at = None
    area.partner_signed_by_id = None
    area.partner_signed_at = None


def _get_or_create_substantive_area(engagement_id, area_name):
    area = SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id, area=area_name).first()
    if not area:
        area = SubstantiveProcedureArea(engagement_id=engagement_id, area=area_name)
        db.session.add(area)
        db.session.flush()
    return area


@engagements_bp.route("/<int:engagement_id>/substantive-procedures/areas/<area_name>/items/add", methods=["POST"])
@login_required
def add_substantive_procedure_item(engagement_id, area_name):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    text = request.form.get("procedure_text", "").strip()
    if not text:
        flash("Please enter the procedure text.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    area = _get_or_create_substantive_area(engagement_id, area_name)
    db.session.add(SubstantiveProcedureItem(area_id=area.id, procedure_text=text, source="manual", order=len(area.items)))
    _touch_substantive_area(area)
    db.session.commit()
    flash("Procedure added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/items/<int:item_id>/update", methods=["POST"])
@login_required
def update_substantive_procedure_item(item_id):
    item = SubstantiveProcedureItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.area_record.engagement)
    area = item.area_record
    item.procedure_text = request.form.get("procedure_text", item.procedure_text).strip() or item.procedure_text
    item.status = request.form.get("status", item.status)
    item.notes = request.form.get("notes", item.notes or "").strip()
    tickmark_id = request.form.get("tickmark_id", "").strip()
    item.tickmark_id = int(tickmark_id) if tickmark_id.isdigit() else None
    _touch_substantive_area(area)
    db.session.commit()
    flash("Procedure updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_substantive_procedure_item(item_id):
    item = SubstantiveProcedureItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.area_record.engagement)
    area = item.area_record
    engagement_id = area.engagement_id
    db.session.delete(item)
    _touch_substantive_area(area)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/notes", methods=["POST"])
@login_required
def save_substantive_area_notes(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    area.notes = request.form.get("notes", "").strip()
    _touch_substantive_area(area)
    db.session.commit()
    flash("Notes saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/review", methods=["POST"])
@login_required
def review_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not area.is_complete:
        flash("Every procedure in this area needs to be marked Done or N/A before the area can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    if area.completed_by_id == current_user.id:
        flash("You can't review an area you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    area.reviewed_by_id = current_user.id
    area.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Area marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/unreview", methods=["POST"])
@login_required
def unreview_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    area.reviewed_by_id = None
    area.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not area.is_complete:
        flash("Every procedure in this area needs to be marked Done or N/A before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    if area.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an area you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))
    area.partner_signed_by_id = current_user.id
    area.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


@engagements_bp.route("/substantive-procedures/areas/<int:area_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_substantive_area(area_id):
    area = SubstantiveProcedureArea.query.get_or_404(area_id)
    _ensure_engagement_access(area.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    area.partner_signed_by_id = None
    area.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=area.engagement_id, tab="substantive"))


# ---------- Review Queries (Partner/Reviewer review points on any section) ----------

def _query_redirect(query):
    """A query always sends you back to the section it was raised on - the
    Substantive Procedures tab for an area-scoped query, otherwise the tab
    named by the query's own section key (they're the same names)."""
    return redirect(url_for("engagements.view_engagement", engagement_id=query.engagement_id, tab=query.section))


@engagements_bp.route("/<int:engagement_id>/queries/raise", methods=["POST"])
@login_required
def raise_query(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)

    section = request.form.get("section", "")
    if section not in QUERY_SECTION_KEYS:
        abort(400)

    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()
    if not subject or not message:
        flash("Please give the query a subject and a message.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=section))

    # area_name only makes sense (and is only trusted) for "substantive" -
    # for every other section it's dropped even if somehow submitted.
    area_name = request.form.get("area_name", "").strip() or None
    if section != "substantive":
        area_name = None
    elif area_name:
        valid_areas = FORENSIC_SUBSTANTIVE_AREAS if engagement.type == "Investigative Engagement" else AUDIT_AREAS
        if area_name not in valid_areas:
            abort(400)

    query = EngagementQuery(
        engagement_id=engagement_id, section=section, area_name=area_name,
        subject=subject, message=message, raised_by_id=current_user.id,
    )
    db.session.add(query)
    db.session.commit()
    flash("Query raised.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=section))


@engagements_bp.route("/queries/<int:query_id>/reply", methods=["POST"])
@login_required
def reply_to_query(query_id):
    query = EngagementQuery.query.get_or_404(query_id)
    _ensure_engagement_access(query.engagement)
    message = request.form.get("message", "").strip()
    if not message:
        flash("Please enter a reply.", "danger")
        return _query_redirect(query)
    db.session.add(QueryReply(query_id=query.id, author_id=current_user.id, message=message))
    db.session.commit()
    flash("Reply added.", "success")
    return _query_redirect(query)


@engagements_bp.route("/queries/<int:query_id>/resolve", methods=["POST"])
@login_required
def resolve_query(query_id):
    query = EngagementQuery.query.get_or_404(query_id)
    _ensure_engagement_access(query.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    query.status = "Resolved"
    query.resolved_by_id = current_user.id
    query.resolved_at = datetime.utcnow()
    db.session.commit()
    flash("Query marked as resolved.", "success")
    return _query_redirect(query)


@engagements_bp.route("/queries/<int:query_id>/reopen", methods=["POST"])
@login_required
def reopen_query(query_id):
    query = EngagementQuery.query.get_or_404(query_id)
    _ensure_engagement_access(query.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    query.status = "Open"
    query.resolved_by_id = None
    query.resolved_at = None
    db.session.commit()
    flash("Query reopened.", "info")
    return _query_redirect(query)


@engagements_bp.route("/queries")
@login_required
def queries_board():
    """Firm-wide list of review queries across every engagement, so a
    Partner/Reviewer can track what they've raised (and staff can see
    what's outstanding against their work) without opening each engagement
    in turn."""
    status_filter = request.args.get("status", "Open")
    query = EngagementQuery.query
    if status_filter in ("Open", "Resolved"):
        query = query.filter_by(status=status_filter)
    all_queries = query.order_by(EngagementQuery.raised_at.desc()).all()
    if current_user.role != "admin":
        # Don't leak the existence of, or activity on, an engagement this
        # user isn't assigned to - same confidentiality rule as everywhere
        # else, applied per-query via its parent engagement.
        all_queries = [q for q in all_queries if user_can_access_engagement(current_user, q.engagement)]
    return render_template(
        "engagements/queries_board.html", queries=all_queries, status_filter=status_filter,
    )


# ---------- Working papers: Word/Excel generation (Finalisation + Substantive Procedures tabs) ----------

def _workpaper_filename(engagement, label, ext):
    client_name = engagement.client.name if engagement.client else "Client"
    return secure_filename(f"{client_name}_{engagement.title}_{label}.{ext}") or f"workpaper.{ext}"


@engagements_bp.route("/<int:engagement_id>/workpapers/financial-statements.docx")
@login_required
def download_financial_statements_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the trial balance (Planning tab) before generating the financial statements working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    statements = fin.build_all_statements(trial_balance.lines, trial_balance.adjustments)
    financial_statements = FinancialStatements.query.filter_by(engagement_id=engagement_id).first()
    buf = wp.build_financial_statements_docx(engagement, statements, financial_statements)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Financial_Statements", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/trial-balance.xlsx")
@login_required
def download_trial_balance_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the trial balance (Planning tab) before generating this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    buf = wp.build_trial_balance_adjustments_xlsx(engagement, trial_balance)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Trial_Balance_and_Adjustments", "xlsx"),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/rep-letter.docx")
@login_required
def download_rep_letter_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    statements = (
        fin.build_all_statements(trial_balance.lines, trial_balance.adjustments)
        if trial_balance and trial_balance.lines else None
    )
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="rep_letter").first()
    buf = wp.build_rep_letter_docx(engagement, statements, narrative)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Management_Representation_Letter", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/report-to-management.docx")
@login_required
def download_report_to_management_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="report_to_management").first()
    buf = wp.build_report_to_management_docx(engagement, narrative)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Report_to_Management", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/forensic-report.docx")
@login_required
def download_forensic_report_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if engagement.type != "Investigative Engagement":
        flash("The forensic investigation report is only available on Investigative Engagements.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="forensic_executive_summary").first()
    buf = wp.build_forensic_report_docx(engagement, narrative)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Forensic_Investigation_Report", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/file-summary.pdf")
@login_required
def download_engagement_file_summary_pdf(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_engagement_file_summary_pdf(engagement)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Engagement_File_Summary", "pdf"),
        mimetype="application/pdf",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/acceptance-checklist.docx")
@login_required
def download_acceptance_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_client_acceptance_checklist_docx(engagement, engagement.client_acceptance)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Client_Acceptance_Checklist", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/entity-checklist.docx")
@login_required
def download_entity_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_entity_understanding_checklist_docx(engagement, engagement.entity_understanding)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Understanding_the_Entity_Checklist", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/engagement-checklist.docx")
@login_required
def download_engagement_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_engagement_checklist_docx(engagement)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Engagement_Checklist", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/finalisation-checklist.docx")
@login_required
def download_finalisation_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_finalisation_checklist_docx(engagement, engagement.finalisation_checklist)
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, "Finalisation_Checklist", "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _substantive_programme_context(engagement):
    """Shared setup for the two Substantive Procedures workpaper downloads
    below - the same area list/order/reference codes the tab itself shows
    (see view_engagement), so the generated file always matches the screen."""
    is_forensic = engagement.type == "Investigative Engagement"
    area_order = FORENSIC_SUBSTANTIVE_AREAS if is_forensic else AUDIT_AREAS
    area_refs = FORENSIC_AREA_REFERENCES if is_forensic else AUDIT_AREA_REFERENCES
    areas_by_name = {
        a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement.id).all()
    }
    return areas_by_name, area_order, area_refs


@engagements_bp.route("/<int:engagement_id>/workpapers/substantive-procedures.docx")
@login_required
def download_substantive_procedures_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    areas_by_name, area_order, area_refs = _substantive_programme_context(engagement)
    if not areas_by_name:
        flash("Generate suggested procedures (or add some manually) before downloading this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    buf = wp.build_substantive_procedures_docx(engagement, areas_by_name, area_order, area_refs)
    label = "Investigative_Procedures_Programme" if engagement.type == "Investigative Engagement" else "Substantive_Procedures_Programme"
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, label, "docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@engagements_bp.route("/<int:engagement_id>/workpapers/substantive-procedures.xlsx")
@login_required
def download_substantive_procedures_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    areas_by_name, area_order, area_refs = _substantive_programme_context(engagement)
    if not areas_by_name:
        flash("Generate suggested procedures (or add some manually) before downloading this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    buf = wp.build_substantive_procedures_xlsx(engagement, areas_by_name, area_order, area_refs)
    label = "Investigative_Procedures_Programme" if engagement.type == "Investigative Engagement" else "Substantive_Procedures_Programme"
    return send_file(
        buf, as_attachment=True,
        download_name=_workpaper_filename(engagement, label, "xlsx"),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
