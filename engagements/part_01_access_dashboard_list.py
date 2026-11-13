import os
import csv
import io
import uuid
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, send_from_directory, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import openpyxl

from extensions import db
from models import visible_clients, client_scope_ids
from timeutil import to_cat
from models import (
    Engagement, Client, User, ChecklistTemplate, EngagementChecklistItem,
    RiskItem, Document, EngagementTask, DocumentTemplate, StaffAllocation, TimeSheet, TimeEntry,
    RiskAssessment, MaterialityCalculation, EntityUnderstanding, AuditStrategy, AUDIT_STRATEGY_PHASE_STATUSES,
    QualitativeMaterialityFactor, QUALITATIVE_MATERIALITY_FACTORS, QUALITATIVE_MATERIALITY_RATINGS,
    SecretarialPlan, RegistryFormFiling,
    AnalyticalReview, AnalyticalReviewLine, SECRETARIAL_ANALYTICAL_REVIEW_POINTS, BUSINESS_IT_ANALYTICAL_REVIEW_POINTS,
    ITChangePlanItem, IT_CHANGE_PLAN_RISK_LEVELS,
    ClientAcceptance, CLIENT_ACCEPTANCE_DECISIONS, CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
    RISK_CATEGORIES,
    SANCTIONS_SCREENING_SOURCES, SANCTIONS_SCREENING_RESULTS,
    SANCTIONS_AUTO_SOURCES, REGULATORY_NOTICE_SOURCES, REGULATORY_NOTICE_SOURCE_LABELS,
    SanctionsListStatus, RegulatoryNotice, ClientKeyPerson,
    COAMapping, StandardChartOfAccounts, TrialBalance, TrialBalanceLine, AuditAdjustment, AuditAdjustmentLine, next_journal_reference, FinancialStatements,
    REPORTING_FRAMEWORKS, REPORTING_FRAMEWORK_LABELS,
    CASH_FLOW_METHODS, CASH_FLOW_METHOD_LABELS, PIE_CRITERIA, SME_ACT_SECTORS, SME_ACT_SIZE_BANDS,
    SubstantiveProcedureArea, SubstantiveProcedureItem,
    FinalisationChecklist, FinalisationChecklistItem, DEFAULT_FINALISATION_CHECKLIST_ITEMS, FORENSIC_FINALISATION_CHECKLIST_ITEMS,
    BUSINESS_IT_FINALISATION_CHECKLIST_ITEMS, SECRETARIAL_FINALISATION_CHECKLIST_ITEMS,
    AUDIT_AREA_REFERENCES, FORENSIC_AREA_REFERENCES, BUSINESS_IT_AREA_REFERENCES, SECRETARIAL_AREA_REFERENCES,
    EngagementQuery, QueryReply,
    WorkpaperReview, REVIEWABLE_TYPES, REVIEWABLE_TYPE_SUBSTANTIVE_ITEM, REVIEWABLE_TYPE_CHECKLIST_ITEM,
    ENGAGEMENT_TYPES, canonical_engagement_type, ENGAGEMENT_STATUSES, TASK_STATUSES, TASK_CATEGORIES, resolve_task_category, effective_task_priority, CHECKLIST_STATUSES, RISK_STATUSES,
    SECRETARIAL_SUBDIVISIONS, SECRETARIAL_ACTIVITIES, SECRETARIAL_ACTIVITY_LABELS, REVIEWER_ROLES, PARTNER_SIGNOFF_ROLES,
    RISK_LIKELIHOOD_QUESTIONS, RISK_IMPACT_QUESTIONS,
    FORENSIC_RISK_LIKELIHOOD_QUESTIONS, FORENSIC_RISK_IMPACT_QUESTIONS, SCOPE_SUGGESTIONS,
    BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS, BUSINESS_IT_RISK_IMPACT_QUESTIONS,
    SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS, SECRETARIAL_RISK_IMPACT_QUESTIONS,
    ENTITY_UNDERSTANDING_FIELDS, EntityUnderstandingChecklistItem, FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS,
    DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS,
    BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS, SECRETARIAL_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS,
    AUDIT_AREAS, BASELINE_SUBSTANTIVE_PROCEDURES, HIGH_RISK_EXTRA_PROCEDURES, INDUSTRY_EXTRA_PROCEDURES,
    FORENSIC_SUBSTANTIVE_AREAS, FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES,
    BUSINESS_IT_SUBSTANTIVE_AREAS, BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES, BUSINESS_IT_PROCEDURE_KIND,
    SECRETARIAL_SUBSTANTIVE_AREAS, secretarial_substantive_areas, SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES, SECRETARIAL_EXECUTION_TASK_DETAILS,
    QUERY_SECTIONS, QUERY_SECTION_KEYS,
    user_has_permission, user_can_access_engagement, engagement_acceptance_cleared,
    Tickmark, WORKPAPER_SECTIONS, workpaper_reference, filing_reference, effectively_reviewed,
    WorkpaperNarrative, WORKPAPER_NARRATIVE_KINDS, WORKPAPER_NARRATIVE_KIND_KEYS,
    DEFAULT_WORKPAPER_NARRATIVE_BODIES, default_workpaper_narrative_body, FilingIndexSection,
    PPEAssetClass, PPEAsset, PPE_DEPRECIATION_METHODS,
    DirectorsStatement, AuditOpinion, AUDIT_OPINION_BASES, AUDIT_OPINION_BASIS_LABELS,
    OPINION_MODIFICATIONS, OPINION_MODIFICATION_LABELS,
    IncomeTaxComputation, IncomeTaxAdjustmentLine, INCOME_TAX_ITEM_TYPES, INCOME_TAX_ITEM_TYPE_LABELS,
    DeferredTaxComputation, DeferredTaxItem,
    notify_task_assignment,
    TAX_SERVICES, TAX_SERVICE_LABELS, TAX_HEADS,
    TAX_OPINION_PARTS, TAX_HEALTH_CHECK_PARTS,
    TaxEntityProfile, TaxRegistration, TaxDeadline, TaxAnalyticalReview, TaxPosition,
    TAX_POSITION_CLASSIFICATIONS, TAX_POSITION_APPROVAL_STATUSES,
    PenaltyInterestRate, PenaltyInterestCalculation, PENALTY_INTEREST_RATE_TYPES,
    VATRate, VAT_RATE_TYPES,
    VATInvoice, VATPeriod, vat_period_key, vat_period_label, parse_vat_period_key, vat_period_of, VAT_INVOICE_DIRECTIONS, VAT_INVOICE_DIRECTION_LABELS, VAT_DOCUMENT_KINDS, VAT_KIND_LABELS, vat_working_paper_summary, vat_working_paper_by_currency, VAT_CURRENCIES, VAT_DEFAULT_CURRENCY, VAT_DENIAL_GROUNDS, VAT_DENIAL_LABELS,
    VATImportBatch,
    TaxInformationRequest, TAX_INFO_REQUEST_STATUSES,
    TaxReturnRecord, TAX_RETURN_STATUSES,
    TaxResearchLogEntry, TaxStructuringOption, TaxDispute, TAX_DISPUTE_STAGES,
    LegislativeUpdate, TaxChecklistItem, DEFAULT_TAX_CHECKLIST_ITEMS,
    TAX_REGISTRATION_RECORD_TYPES, TAX_ENTITY_CLASSIFICATIONS,
    ACCOUNTING_SERVICES, ACCOUNTING_SERVICE_LABELS, ACCOUNTING_AREAS,
    MANAGEMENT_ACCOUNTS_REPORT_PARTS,
    GLAccount, GL_ACCOUNT_TYPES,
    JournalEntry, JournalEntryLine, JOURNAL_ENTRY_SOURCES,
    BankReconciliation, BankReconciliationItem, BANK_RECONCILIATION_ITEM_TYPES,
    CostCentre, COST_CENTRE_TYPES,
    CostingRecord, COSTING_METHODS,
    StandardCostVariance, VARIANCE_TYPES,
    CVPAnalysis,
    Budget, BudgetLine, BUDGET_TYPES,
    KPIMetric, KPI_CATEGORIES,
    AccountingChecklistItem, DEFAULT_ACCOUNTING_CHECKLIST_ITEMS,
)
import financials as fin
import workpapers as wp
from standard_coa import coa_account_choices

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
        abort(403, description=(
            "You are not on this engagement's team, so you cannot open or change anything inside it. "
            "Ask the engagement's Partner or Manager (or an Admin) to add you to the team."
        ))
    if require_accepted and current_user.role != "admin" and not engagement_acceptance_cleared(engagement):
        abort(403, description=(
            "This engagement is still waiting for Client Acceptance & Continuance sign-off, so work "
            "(including uploading VAT schedules) is locked until the Partner signs off an Accepted decision "
            "on the engagement's Client Acceptance tab."
        ))


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
    """The Practice Dashboard - the firm's single home page. This used to
    be two separate pages/nav links (a personal "my work" dashboard and a
    firm-wide analytics dashboard); they're combined here into one: this
    user's own active engagements and open tasks, alongside firm-wide
    portfolio health, review query aging, staff booking for the current
    week, recent logged hours, and an Engagement Quality Index per active
    engagement - all built entirely from data the app already captures,
    no new database tables. There is deliberately no $-based WIP,
    realization, or recovery figure here: no billing rate is captured
    anywhere in this app (TimeEntry records hours only), so a $ figure
    here would have to be invented rather than computed - see the
    architecture roadmap document for what a full Practice/Audit
    Intelligence platform with real billing data would add. Every list
    below is filtered through the same confidentiality rule as the rest
    of the app: a non-admin sees only what they're Partner/Manager/Team
    on; Admin sees everything. `practice_dashboard()` below (the old
    /engagements/analytics URL) just renders this same page, kept as an
    alias for anyone with the old link bookmarked."""
    today = date.today()
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
    counts_by_type = {t: 0 for t in ENGAGEMENT_TYPES}
    for e in all_engagements:
        counts_by_status[e.status] = counts_by_status.get(e.status, 0) + 1
        counts_by_type[e.type] = counts_by_type.get(e.type, 0) + 1
    counts_by_type = {t: c for t, c in counts_by_type.items() if c}

    # ---- Engagement Quality Index, across every active engagement visible to this user
    eqi_rows = []
    for e in active:
        idx = engagement_quality_index(e)
        if idx:
            eqi_rows.append({"engagement": e, "index": idx})
    eqi_rows.sort(key=lambda r: r["index"]["reviewed_pct"])
    firm_avg_reviewed_pct = round(sum(r["index"]["reviewed_pct"] for r in eqi_rows) / len(eqi_rows)) if eqi_rows else None

    # ---- Review query aging (EngagementQuery - "no new data capture needed", it's all already there)
    open_queries = EngagementQuery.query.filter_by(status="Open").order_by(EngagementQuery.raised_at.asc()).all()
    if current_user.role != "admin":
        open_queries = [q for q in open_queries if user_can_access_engagement(current_user, q.engagement)]
    age_bucket_defs = [("0-7 days", 0, 7), ("8-14 days", 8, 14), ("15-30 days", 15, 30), ("30+ days", 31, None)]
    query_aging = []
    for label, lo, hi in age_bucket_defs:
        matching = [
            q for q in open_queries
            if lo <= (today - to_cat(q.raised_at).date()).days and (hi is None or (today - to_cat(q.raised_at).date()).days <= hi)
        ]
        query_aging.append({"label": label, "count": len(matching)})
    oldest_queries = sorted(open_queries, key=lambda q: q.raised_at)[:10]

    # ---- Staffing: this week's booked capacity per active user (StaffAllocation - already captured for the HR Planner)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    people = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    allocations_this_week = StaffAllocation.query.filter(
        StaffAllocation.end_date >= week_start, StaffAllocation.start_date <= week_end
    ).all()
    if current_user.role != "admin":
        allocations_this_week = [a for a in allocations_this_week if user_can_access_engagement(current_user, a.engagement)]
    staffing = []
    for person in people:
        total_pct = sum(
            a.allocation_pct for a in allocations_this_week
            if a.user_id == person.id and a.overlaps(week_start, week_end)
        )
        if total_pct:
            staffing.append({"user": person, "pct": total_pct})
    staffing.sort(key=lambda r: r["pct"], reverse=True)
    over_booked = [r for r in staffing if r["pct"] > 100]
    under_booked = [r for r in staffing if r["pct"] < 50]

    # ---- Hours logged in the last 30 days - a workload/activity signal only, never a $ figure (no billing rate exists anywhere in this app)
    hours_window_days = 30
    since = today - timedelta(days=hours_window_days)
    recent_entries = (
        db.session.query(TimeEntry, TimeSheet)
        .join(TimeSheet, TimeEntry.timesheet_id == TimeSheet.id)
        .filter(TimeEntry.work_date >= since)
        .all()
    )
    if current_user.role != "admin":
        recent_entries = [
            (te, ts) for te, ts in recent_entries
            if te.engagement is None or user_can_access_engagement(current_user, te.engagement)
        ]
    hours_by_user, hours_by_type = {}, {}
    for te, ts in recent_entries:
        hours_by_user[ts.user_id] = hours_by_user.get(ts.user_id, 0) + (te.hours or 0)
        type_label = te.engagement.type if te.engagement else "Non-engagement / admin"
        hours_by_type[type_label] = hours_by_type.get(type_label, 0) + (te.hours or 0)
    user_by_id = {p.id: p for p in people}
    hours_leaderboard = sorted(
        [{"user": user_by_id[uid], "hours": round(h, 1)} for uid, h in hours_by_user.items() if uid in user_by_id],
        key=lambda r: r["hours"], reverse=True,
    )[:10]
    hours_by_type_rows = sorted(
        [{"type": t, "hours": round(h, 1)} for t, h in hours_by_type.items()],
        key=lambda r: r["hours"], reverse=True,
    )

    return render_template(
        "dashboard.html",
        active=active,
        overdue=overdue,
        my_tasks=my_tasks,
        all_engagements=all_engagements,
        counts_by_status=counts_by_status,
        counts_by_type=counts_by_type,
        total_clients=len(visible_clients(current_user)),
        total_engagements=len(all_engagements),
        eqi_rows=eqi_rows, firm_avg_reviewed_pct=firm_avg_reviewed_pct,
        query_aging=query_aging, open_query_count=len(open_queries), oldest_queries=oldest_queries,
        staffing=staffing, over_booked=over_booked, under_booked=under_booked,
        week_start=week_start, week_end=week_end,
        hours_leaderboard=hours_leaderboard, hours_by_type_rows=hours_by_type_rows,
        hours_window_days=hours_window_days,
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


@engagements_bp.route("/directory")
@login_required
def directory():
    """Clients & Engagements - a single combined page with a tab switcher,
    replacing what used to be two separate top-level nav pages (Clients
    and Engagements). The original two pages (`clients.list_clients` /
    `list_engagements` above) still exist and still work exactly as
    before - they're what "Cancel" links, post-delete redirects, and
    other internal navigation use - this route is purely the new combined
    nav landing page, built from the same two queries."""
    view = request.args.get("view", "clients")
    if view not in ("clients", "engagements"):
        view = "clients"

    q = request.args.get("q", "").strip()
    client_query = Client.query
    scope = client_scope_ids(current_user)
    if scope is not None:
        client_query = client_query.filter(Client.id.in_(scope)) if scope else client_query.filter(db.false())
    if q:
        client_query = client_query.filter(
            db.or_(
                Client.name.ilike(f"%{q}%"), Client.company_number.ilike(f"%{q}%"),
                Client.tin_number.ilike(f"%{q}%"), Client.vat_number.ilike(f"%{q}%"),
            )
        )
    all_clients = client_query.order_by(Client.name).all()

    status_filter = request.args.get("status", "")
    type_filter = request.args.get("type", "")
    eng_query = Engagement.query
    if status_filter:
        eng_query = eng_query.filter_by(status=status_filter)
    if type_filter:
        eng_query = eng_query.filter_by(type=type_filter)
    all_engagements = _visible_to_current_user(
        eng_query.order_by(Engagement.deadline.asc().nullslast()).all()
    )

    return render_template(
        "engagements/directory.html",
        view=view,
        clients=all_clients, q=q,
        engagements=all_engagements,
        statuses=ENGAGEMENT_STATUSES,
        types=ENGAGEMENT_TYPES,
        status_filter=status_filter,
        type_filter=type_filter,
    )


@engagements_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_engagement():
    clients = visible_clients(current_user)
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()

    if request.method == "POST":
        client_id = request.form.get("client_id")
        if not client_id:
            flash("Please select a client.", "danger")
            return render_template("engagements/form.html", engagement=None, clients=clients, users=users,
                                    templates=templates, types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                                    subdivisions=SECRETARIAL_SUBDIVISIONS, secretarial_activity_options=SECRETARIAL_ACTIVITIES,
                                    tax_service_options=TAX_SERVICES, accounting_service_options=ACCOUNTING_SERVICES)

        engagement_type = canonical_engagement_type(request.form.get("type", "Audit"))
        selected_activities = request.form.getlist("secretarial_activities")
        selected_tax_services = request.form.getlist("tax_services")
        selected_accounting_services = request.form.getlist("accounting_services")
        engagement = Engagement(
            client_id=int(client_id),
            title=request.form.get("title", "").strip(),
            type=engagement_type,
            subdivision=(request.form.get("subdivision", "").strip() or None) if engagement_type == "Secretarial" else None,
            secretarial_activities=(",".join(selected_activities) or None) if engagement_type == "Secretarial" else None,
            # Combinable on ANY engagement type (see TAX_SERVICES above) -
            # not gated by engagement_type the way secretarial_activities
            # is. A dedicated Tax engagement type with nothing explicitly
            # ticked still gets the Tax tab (see Engagement.has_tax_module),
            # so this is purely "which services", never "whether at all".
            tax_services=",".join(selected_tax_services) or None,
            # Combinable on ANY engagement type, same as tax_services above.
            accounting_services=",".join(selected_accounting_services) or None,
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
                            subdivisions=SECRETARIAL_SUBDIVISIONS, secretarial_activity_options=SECRETARIAL_ACTIVITIES,
                            tax_service_options=TAX_SERVICES, accounting_service_options=ACCOUNTING_SERVICES)


@engagements_bp.route("/<int:engagement_id>/edit", methods=["GET", "POST"])
@login_required
def edit_engagement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement, require_accepted=False)
    clients = visible_clients(current_user)
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()

    if request.method == "POST":
        engagement.client_id = int(request.form.get("client_id"))
        engagement.title = request.form.get("title", "").strip()
        engagement.type = canonical_engagement_type(request.form.get("type", "Audit"))
        engagement.subdivision = (request.form.get("subdivision", "").strip() or None) if engagement.type == "Secretarial" else None
        engagement.secretarial_activities = (",".join(request.form.getlist("secretarial_activities")) or None) if engagement.type == "Secretarial" else None
        engagement.tax_services = ",".join(request.form.getlist("tax_services")) or None
        engagement.accounting_services = ",".join(request.form.getlist("accounting_services")) or None
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
        # Ticking Company Re-registration on a Secretarial engagement whose
        # Execution Plan is already under way adds that workstream's tasks
        # (additive only - nothing existing is touched).
        if engagement.type == "Secretarial" and "re_registration" in engagement.secretarial_activity_list:
            if sync_substantive_procedures_if_started(engagement.id):
                db.session.commit()
        flash("Engagement updated.", "success")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id))

    return render_template("engagements/form.html", engagement=engagement, clients=clients, users=users,
                            templates=[], types=ENGAGEMENT_TYPES, statuses=ENGAGEMENT_STATUSES,
                            subdivisions=SECRETARIAL_SUBDIVISIONS, secretarial_activity_options=SECRETARIAL_ACTIVITIES,
                            tax_service_options=TAX_SERVICES, accounting_service_options=ACCOUNTING_SERVICES)


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


def _secretarial_compliance_calendar_suggestions(period_end, plan=None):
    """Suggested (never auto-filled) dates for the four Annual Statutory
    Compliance Calendar milestones on the Secretarial Engagement Plan's
    Pillar 2 - see SecretarialPlan.draft_financials_target etc. Worked out
    from the Companies and Other Business Entities Act in cobe_checks.
    calendar_suggestions: the annual return is due 21 days after the
    anniversary of the company's incorporation / registration /
    re-registration (s165(2)), an AGM is needed once in every twelve months
    (s167(2)) and needs 21 days' notice (s169(1)). Returns None if the
    engagement has no period_end set yet, same "nothing to suggest until
    there's a real date to anchor on" behaviour as elsewhere in this app.
    Purely a hint shown next to each date field; the preparer always types
    (or overrides) the actual date themselves."""
    import cobe_checks
    return cobe_checks.calendar_suggestions(
        period_end,
        registration_date=plan.registration_date if plan else None,
        last_agm_date=plan.last_agm_date if plan else None,
    )
