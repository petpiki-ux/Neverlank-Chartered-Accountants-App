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
from models import (
    Engagement, Client, User, ChecklistTemplate, EngagementChecklistItem,
    RiskItem, Document, EngagementTask, DocumentTemplate, StaffAllocation, TimeSheet, TimeEntry,
    RiskAssessment, MaterialityCalculation, EntityUnderstanding, AuditStrategy, AUDIT_STRATEGY_PHASE_STATUSES,
    QualitativeMaterialityFactor, QUALITATIVE_MATERIALITY_FACTORS, QUALITATIVE_MATERIALITY_RATINGS,
    SecretarialPlan,
    AnalyticalReview, AnalyticalReviewLine, SECRETARIAL_ANALYTICAL_REVIEW_POINTS, BUSINESS_IT_ANALYTICAL_REVIEW_POINTS,
    ITChangePlanItem, IT_CHANGE_PLAN_RISK_LEVELS,
    ClientAcceptance, CLIENT_ACCEPTANCE_DECISIONS, CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
    RISK_CATEGORIES,
    SANCTIONS_SCREENING_SOURCES, SANCTIONS_SCREENING_RESULTS,
    SANCTIONS_AUTO_SOURCES, REGULATORY_NOTICE_SOURCES, REGULATORY_NOTICE_SOURCE_LABELS,
    SanctionsListStatus, RegulatoryNotice, ClientKeyPerson,
    COAMapping, TrialBalance, TrialBalanceLine, AuditAdjustment, AuditAdjustmentLine, FinancialStatements,
    REPORTING_FRAMEWORKS, REPORTING_FRAMEWORK_LABELS,
    CASH_FLOW_METHODS, CASH_FLOW_METHOD_LABELS, PIE_CRITERIA, SME_ACT_SECTORS, SME_ACT_SIZE_BANDS,
    SubstantiveProcedureArea, SubstantiveProcedureItem,
    FinalisationChecklist, FinalisationChecklistItem, DEFAULT_FINALISATION_CHECKLIST_ITEMS, FORENSIC_FINALISATION_CHECKLIST_ITEMS,
    BUSINESS_IT_FINALISATION_CHECKLIST_ITEMS, SECRETARIAL_FINALISATION_CHECKLIST_ITEMS,
    AUDIT_AREA_REFERENCES, FORENSIC_AREA_REFERENCES, BUSINESS_IT_AREA_REFERENCES, SECRETARIAL_AREA_REFERENCES,
    EngagementQuery, QueryReply,
    ENGAGEMENT_TYPES, ENGAGEMENT_STATUSES, TASK_STATUSES, CHECKLIST_STATUSES, RISK_STATUSES,
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
    SECRETARIAL_SUBSTANTIVE_AREAS, SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES, SECRETARIAL_EXECUTION_TASK_DETAILS,
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
            if lo <= (today - q.raised_at.date()).days and (hi is None or (today - q.raised_at.date()).days <= hi)
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
        total_clients=Client.query.count(),
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
    if q:
        client_query = client_query.filter(
            db.or_(Client.name.ilike(f"%{q}%"), Client.company_number.ilike(f"%{q}%"))
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
    clients = Client.query.order_by(Client.name).all()
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

        engagement_type = request.form.get("type", "Audit")
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
    clients = Client.query.order_by(Client.name).all()
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()

    if request.method == "POST":
        engagement.client_id = int(request.form.get("client_id"))
        engagement.title = request.form.get("title", "").strip()
        engagement.type = request.form.get("type", "Audit")
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


def _secretarial_compliance_calendar_suggestions(period_end):
    """Suggested (never auto-filled) dates for the four Annual Statutory
    Compliance Calendar milestones on the Secretarial Engagement Plan's
    Pillar 2, anchored on the client's financial year-end - see
    SecretarialPlan.draft_financials_target etc. Returns None if the
    engagement has no period_end set yet, same "nothing to suggest until
    there's a real date to anchor on" behaviour as elsewhere in this app.
    Purely a hint shown next to each date field; the preparer always types
    (or overrides) the actual date themselves."""
    if not period_end:
        return None

    def _add_months(d, months):
        month = d.month - 1 + months
        year = d.year + month // 12
        month = month % 12 + 1
        day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return date(year, month, day)

    agm_target = _add_months(period_end, 6)
    return {
        "draft_financials_target": _add_months(period_end, 4),
        "agm_notice_target": agm_target - timedelta(days=21),
        "agm_target": agm_target,
        "annual_return_target": agm_target + timedelta(days=42),
    }


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
    # Trial Balance is removed entirely for Secretarial engagements (per the
    # firm's instruction) - there's no nav link to it (see base template),
    # but guard the tab param directly too in case an old link/bookmark
    # still points at it.
    if tab == "trial_balance" and engagement.type == "Secretarial":
        tab = "overview"
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
    # questionnaire, and a Business Intelligence and IT Engagement gets the
    # cyber/IT/AML-CFT control questionnaire, instead of the ordinary audit
    # risk-of-material-misstatement one - see RiskAssessment.
    # likelihood_questions/impact_questions, which the save route below
    # mirrors.
    is_forensic_risk = engagement.type == "Investigative Engagement"
    is_business_it_risk = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic_risk:
        likelihood_questions = FORENSIC_RISK_LIKELIHOOD_QUESTIONS
        impact_questions = FORENSIC_RISK_IMPACT_QUESTIONS
    elif is_business_it_risk:
        likelihood_questions = BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS
        impact_questions = BUSINESS_IT_RISK_IMPACT_QUESTIONS
    elif is_secretarial:
        likelihood_questions = SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS
        impact_questions = SECRETARIAL_RISK_IMPACT_QUESTIONS
    else:
        likelihood_questions = RISK_LIKELIHOOD_QUESTIONS
        impact_questions = RISK_IMPACT_QUESTIONS
    materiality = MaterialityCalculation.query.filter_by(engagement_id=engagement_id).first()
    qualitative_materiality_factors = {
        f.factor_key: f for f in QualitativeMaterialityFactor.query.filter_by(engagement_id=engagement_id).all()
    }
    qualitative_materiality_index = QualitativeMaterialityFactor.overall_rating_for(qualitative_materiality_factors.values())
    audit_strategy = AuditStrategy.query.filter_by(engagement_id=engagement_id).first() if is_forensic_risk else None
    # The Secretarial Engagement Plan - what the Planning tab is replaced
    # with on a Secretarial engagement, exactly like audit_strategy above
    # replaces it for an Investigative Engagement.
    secretarial_plan = SecretarialPlan.query.filter_by(engagement_id=engagement_id).first() if is_secretarial else None
    secretarial_compliance_suggestions = _secretarial_compliance_calendar_suggestions(engagement.period_end) if is_secretarial else None
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
    # Depreciation policy / Asset Register (Substantive Procedures >
    # Property, Plant and Equipment) - feeds both the PPE note below (in
    # place of the plain trial-balance-account list, once populated) and
    # the "Property, plant and equipment" accounting policy paragraph.
    ppe_asset_classes, ppe_assets, ppe_class_dicts, ppe_asset_dicts = _ppe_plain_data(engagement_id)
    ppe_movement, ppe_policy_text = _ppe_movement_and_policy(ppe_class_dicts, ppe_asset_dicts, engagement.period_end)
    # The Financial Statements are always built from the ADJUSTED trial
    # balance (preliminary TB + audit adjustments, current year only) -
    # Analytical Review and Substantive Procedures work from the preliminary
    # trial_balance.lines directly, unadjusted, so they're unaffected by this.
    statements = (
        fin.build_all_statements(
            trial_balance.lines, trial_balance.adjustments,
            reporting_framework=engagement.reporting_framework, cash_flow_method=engagement.cash_flow_method,
            ppe_movement=ppe_movement, ppe_policy_text=ppe_policy_text,
        )
        if trial_balance and trial_balance.lines else None
    )
    # Directors' Statement, Audit Opinion / Review Report, Income Tax
    # Computation, Deferred Tax Computation - each queried only (never
    # created here); the "not saved yet" state is rendered with sensible
    # defaults by the template, and the record is only actually created the
    # first time its own Save action is posted (same pattern as the
    # Financial Statements closing notes' own defaults).
    directors_statement = DirectorsStatement.query.filter_by(engagement_id=engagement_id).first()
    suggested_directors = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, role="Director", status="Confirmed").order_by(ClientKeyPerson.id).all()
    audit_opinion = AuditOpinion.query.filter_by(engagement_id=engagement_id).first()
    default_opinion_paragraphs = fin.default_audit_opinion_paragraphs(
        (audit_opinion.report_basis if audit_opinion else ("audit" if engagement.type == "Audit" else "review")),
        (audit_opinion.modification if audit_opinion else "unmodified"),
        engagement.client.name, engagement.period_end, REPORTING_FRAMEWORK_LABELS.get(engagement.reporting_framework),
    )
    income_tax_computation = IncomeTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    profit_before_tax = statements["pl"]["profit_before_tax"]["current"] if statements else None
    income_tax_lines = [
        {"item_type": l.item_type, "description": l.description, "amount": l.amount}
        for l in (income_tax_computation.items if income_tax_computation else [])
    ]
    income_tax_result = fin.build_income_tax_computation(
        profit_before_tax, income_tax_lines,
        tax_loss_brought_forward=income_tax_computation.tax_loss_brought_forward if income_tax_computation else 0.0,
        tax_rate_percent=income_tax_computation.tax_rate_percent if income_tax_computation else None,
        aids_levy_percent=income_tax_computation.aids_levy_percent if income_tax_computation else None,
    ) if profit_before_tax is not None else None
    income_tax_tb_current = statements["totals"]["income_tax_expense"]["current"] if statements else None
    # Same "always shown, basis for a proposed adjustment" reconciliation
    # pattern as the PPE note (financials.build_notes) - the computed
    # current tax charge vs whatever is actually recorded in the trial
    # balance's Income Tax Expense line, whether or not they agree.
    income_tax_reconciliation = None
    if income_tax_result is not None and income_tax_tb_current is not None:
        diff = income_tax_tb_current - income_tax_result["total_tax_charge"]
        income_tax_reconciliation = {
            "tb_current": income_tax_tb_current, "computed": income_tax_result["total_tax_charge"],
            "diff": diff, "ties_out": abs(diff) <= 0.01,
        }

    deferred_tax_computation = DeferredTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    ppe_accounting_nbv = ppe_movement["total"]["closing_nbv"] if ppe_movement else (statements["totals"]["ppe"]["current"] if statements else None)
    deferred_tax_other_items = [
        {"description": i.description, "accounting_amount": i.accounting_amount, "tax_base_amount": i.tax_base_amount}
        for i in (deferred_tax_computation.items if deferred_tax_computation else [])
    ]
    deferred_tax_result = fin.build_deferred_tax_computation(
        deferred_tax_computation.tax_rate_percent if deferred_tax_computation else None,
        ppe_accounting_nbv, deferred_tax_computation.ppe_tax_base if deferred_tax_computation else None,
        deferred_tax_other_items,
    )
    deferred_tax_tb_net = (statements["totals"]["deferred_tax_liability"]["current"] - statements["totals"]["deferred_tax_asset"]["current"]) if statements else None
    deferred_tax_reconciliation = None
    if deferred_tax_result is not None and deferred_tax_tb_net is not None:
        diff = deferred_tax_tb_net - deferred_tax_result["total_deferred_tax"]
        deferred_tax_reconciliation = {
            "tb_net": deferred_tax_tb_net, "computed": deferred_tax_result["total_deferred_tax"],
            "diff": diff, "ties_out": abs(diff) <= 0.01,
        }

    # Face of Trial Balance (Trial Balance tab): the summarised, by-category
    # view alongside the detailed, account-by-account one - built from the
    # same preliminary lines so the two views (and their totals) always agree.
    tb_face_summary = fin.summarise_tb_by_category(trial_balance.lines) if trial_balance and trial_balance.lines else []
    # Account Mapping setup (Trial Balance tab): a best-effort category
    # suggestion per unmapped account, from its name alone, for the
    # accountant to review and confirm in one batch rather than picking
    # every account from a blank dropdown - see fin.suggest_fs_category().
    # Never written to the trial balance on its own; only pre-fills the
    # dropdown, and only takes effect once confirmed via
    # confirm_suggested_tb_mappings below.
    suggested_categories = (
        {line.id: fin.suggest_fs_category(line.account_name) for line in trial_balance.lines if not line.fs_category}
        if trial_balance else {}
    )

    # On an Investigative Engagement, Substantive Procedures uses the four
    # forensic evidence-type categories, and on a Business Intelligence and
    # IT Engagement the four cyber/IT/AML-CFT assurance domains, instead of
    # the financial-statement audit areas - see FORENSIC_SUBSTANTIVE_AREAS/
    # BUSINESS_IT_SUBSTANTIVE_AREAS above.
    if is_forensic_risk:
        substantive_area_names = FORENSIC_SUBSTANTIVE_AREAS
        substantive_area_refs = FORENSIC_AREA_REFERENCES
    elif is_business_it_risk:
        substantive_area_names = BUSINESS_IT_SUBSTANTIVE_AREAS
        substantive_area_refs = BUSINESS_IT_AREA_REFERENCES
    elif is_secretarial:
        substantive_area_names = SECRETARIAL_SUBSTANTIVE_AREAS
        substantive_area_refs = SECRETARIAL_AREA_REFERENCES
    else:
        substantive_area_names = AUDIT_AREAS
        substantive_area_refs = AUDIT_AREA_REFERENCES

    # The Tax module's Execution Plan (Module 7) reuses this exact
    # Substantive Procedures machinery - one area per TAX_HEADS entry -
    # exactly like Secretarial's own Execution Plan does above, so tax-head
    # sections just show up as extra sections on this same tab (with their
    # own Preparer/Reviewer/Partner sign-off) whenever the Tax module is on,
    # on top of whatever this engagement type's own areas already are -
    # never replacing them, since Tax is commonly bundled onto another
    # engagement type rather than being its own.
    if engagement.has_tax_module:
        substantive_area_names = list(substantive_area_names) + [h for h in TAX_HEADS if h not in substantive_area_names]

    # Same reuse technique for the Accounting module's write-up/close-out
    # Execution Plan - one area per ACCOUNTING_AREAS entry, appended on top
    # of whatever this engagement type's own areas already are, whenever
    # Engagement.has_accounting_module is True.
    if engagement.has_accounting_module:
        substantive_area_names = list(substantive_area_names) + [a for a in ACCOUNTING_AREAS if a not in substantive_area_names]

    substantive_areas_by_name = {
        a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).all()
    }

    finalisation_checklist = FinalisationChecklist.query.filter_by(engagement_id=engagement_id).first()

    # Firm-wide tickmark legend (see tickmarks.py) - offered as a picker on
    # each Substantive Procedures item.
    tickmarks = Tickmark.query.order_by(Tickmark.symbol).all()

    # Firm-wide Filing Index (see filing_index.py) - Current File (N-series)
    # codes only, offered as a WP No. picker when uploading a Document.
    filing_index_sections = FilingIndexSection.query.filter_by(is_permanent=False, is_active=True).order_by(FilingIndexSection.order, FilingIndexSection.code).all()

    # The narrative workpapers with a persistent, editable copy (Rep Letter,
    # Report to Management, Forensic Report executive summary) - keyed by
    # kind so the Finalisation tab can look each one up directly. Any kind
    # not yet saved is seeded (in memory only, not committed) with the
    # firm's default starting wording, so the edit box is never blank.
    workpaper_narratives = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
    }
    # The Tax Opinion's 14 parts and the Tax Health Check Report's 15 parts
    # (see models.TAX_OPINION_PARTS/TAX_HEALTH_CHECK_PARTS) are only ever
    # seeded/shown on an engagement with the Tax module on - every other
    # WorkpaperNarrative kind (rep_letter etc.) is seeded for every
    # engagement, unchanged from before the Tax module existed.
    seedable_kinds = [
        (kind, label, section) for kind, label, section in WORKPAPER_NARRATIVE_KINDS
        if (engagement.has_tax_module or section not in ("tax_opinion", "tax_health_check"))
        and (engagement.has_accounting_module or section != "management_accounts_report")
    ]
    for kind, _label, _section in seedable_kinds:
        if kind not in workpaper_narratives:
            workpaper_narratives[kind] = _get_or_seed_workpaper_narrative(engagement_id, kind, engagement)

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

    # ---------- Tax tab (Tax Advisory & Tax Compliance module) ----------
    # Only actually queried when the Tax tab could be shown - a plain
    # Audit/Assurance/etc. engagement with no Tax service on pays for none
    # of this. See Engagement.has_tax_module / models.py's "Tax Advisory &
    # Tax Compliance module" section for the full picture of what's reused
    # (RiskItem, SubstantiveProcedureArea/Item, IncomeTaxComputation/
    # DeferredTaxComputation, WorkpaperNarrative) vs. genuinely new here.
    tax_entity_profile = None
    tax_registrations = []
    tax_deadlines = []
    tax_analytical_review = None
    tax_risk_items = []
    tax_positions = []
    tax_return_records = []
    tax_information_requests = []
    tax_research_log = []
    tax_structuring_options = []
    tax_disputes = []
    legislative_updates_for_import = []
    tax_checklist_items = []
    tax_checklist_by_head = {}
    tax_execution_areas_by_name = {}
    if engagement.has_tax_module:
        tax_entity_profile = TaxEntityProfile.query.filter_by(engagement_id=engagement_id).first()
        tax_registrations = TaxRegistration.query.filter_by(engagement_id=engagement_id).order_by(TaxRegistration.record_type, TaxRegistration.id).all()
        tax_deadlines = TaxDeadline.query.filter_by(engagement_id=engagement_id).order_by(TaxDeadline.due_date.asc()).all()
        tax_analytical_review = TaxAnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
        tax_risk_items = RiskItem.query.filter_by(engagement_id=engagement_id, module="tax").order_by(RiskItem.id).all()
        tax_positions = TaxPosition.query.filter_by(engagement_id=engagement_id).order_by(TaxPosition.id).all()
        tax_return_records = TaxReturnRecord.query.filter_by(engagement_id=engagement_id).order_by(TaxReturnRecord.due_date.asc().nullslast()).all()
        tax_information_requests = TaxInformationRequest.query.filter_by(engagement_id=engagement_id).order_by(TaxInformationRequest.id).all()
        if engagement.has_tax_advisory:
            tax_research_log = TaxResearchLogEntry.query.filter_by(engagement_id=engagement_id).order_by(TaxResearchLogEntry.prepared_at.desc()).all()
            tax_structuring_options = TaxStructuringOption.query.filter_by(engagement_id=engagement_id).order_by(TaxStructuringOption.id).all()
            tax_disputes = TaxDispute.query.filter_by(engagement_id=engagement_id).order_by(TaxDispute.id).all()
            # Legislative updates tagged as relevant to this engagement's own
            # client (see LegislativeUpdateClientLink) come first, since
            # those are the ones most likely worth pulling into this
            # engagement's Research Log - but every filed/logged update is
            # offered too, in case a firm-wide update matters here even
            # though no one has tagged this particular client yet.
            client_tagged_update_ids = {link.legislative_update_id for link in engagement.client.legislative_update_links} if engagement.client else set()
            all_legislative_updates = LegislativeUpdate.query.order_by(LegislativeUpdate.created_at.desc()).all()
            legislative_updates_for_import = (
                [u for u in all_legislative_updates if u.id in client_tagged_update_ids]
                + [u for u in all_legislative_updates if u.id not in client_tagged_update_ids]
            )
        tax_checklist_items = TaxChecklistItem.query.filter_by(engagement_id=engagement_id).order_by(TaxChecklistItem.order, TaxChecklistItem.id).all()
        for item in tax_checklist_items:
            tax_checklist_by_head.setdefault(item.tax_head or "General", []).append(item)
        # The Execution Plan (Module 7) - one SubstantiveProcedureArea per
        # tax head, exactly like Secretarial's own Execution Plan reuses
        # this same model (see SECRETARIAL_SUBSTANTIVE_AREAS above).
        tax_execution_areas_by_name = {
            a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).filter(SubstantiveProcedureArea.area.in_(TAX_HEADS)).all()
        }
    current_penalty_interest_rates = PenaltyInterestRate.query.order_by(PenaltyInterestRate.tax_head, PenaltyInterestRate.rate_type, PenaltyInterestRate.effective_from.desc()).all()
    tax_penalty_calculations = PenaltyInterestCalculation.query.filter_by(engagement_id=engagement_id).order_by(PenaltyInterestCalculation.calculated_at.desc()).all() if engagement.has_tax_module else []
    tax_opinion_narratives = {k: workpaper_narratives.get(k) for k, _ in TAX_OPINION_PARTS} if engagement.has_tax_module else {}
    tax_health_check_narratives = {k: workpaper_narratives.get(k) for k, _ in TAX_HEALTH_CHECK_PARTS} if engagement.has_tax_module else {}

    # ---------- Accounting tab (Financial/Cost/Management Accounting module) ----------
    # Only actually queried when the Accounting tab could be shown - see
    # Engagement.has_accounting_module / the module comment in models.py for
    # what's reused (RiskItem, SubstantiveProcedureArea/Item, TrialBalance/
    # TrialBalanceLine, WorkpaperNarrative) vs. genuinely new here.
    gl_accounts = []
    journal_entries = []
    bank_reconciliations = []
    accounting_risk_items = []
    cost_centres = []
    costing_records = []
    standard_cost_variances = []
    cvp_analyses = []
    budgets = []
    kpi_metrics = []
    accounting_checklist_items = []
    accounting_checklist_by_area = {}
    accounting_execution_areas_by_name = {}
    if engagement.has_accounting_module:
        gl_accounts = GLAccount.query.filter_by(engagement_id=engagement_id).order_by(GLAccount.order, GLAccount.code).all()
        journal_entries = JournalEntry.query.filter_by(engagement_id=engagement_id).order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).all()
        bank_reconciliations = BankReconciliation.query.filter_by(engagement_id=engagement_id).order_by(BankReconciliation.statement_date.desc()).all()
        accounting_risk_items = RiskItem.query.filter_by(engagement_id=engagement_id, module="accounting").order_by(RiskItem.id).all()
        if engagement.has_cost_accounting:
            cost_centres = CostCentre.query.filter_by(engagement_id=engagement_id).order_by(CostCentre.name).all()
            costing_records = CostingRecord.query.filter_by(engagement_id=engagement_id).order_by(CostingRecord.id.desc()).all()
            standard_cost_variances = StandardCostVariance.query.filter_by(engagement_id=engagement_id).order_by(StandardCostVariance.id.desc()).all()
            cvp_analyses = CVPAnalysis.query.filter_by(engagement_id=engagement_id).order_by(CVPAnalysis.id.desc()).all()
        if engagement.has_management_accounting:
            budgets = Budget.query.filter_by(engagement_id=engagement_id).order_by(Budget.id.desc()).all()
            kpi_metrics = KPIMetric.query.filter_by(engagement_id=engagement_id).order_by(KPIMetric.id.desc()).all()
        accounting_checklist_items = AccountingChecklistItem.query.filter_by(engagement_id=engagement_id).order_by(AccountingChecklistItem.order, AccountingChecklistItem.id).all()
        for item in accounting_checklist_items:
            accounting_checklist_by_area.setdefault(item.area or "General", []).append(item)
        # The Execution Plan - one SubstantiveProcedureArea per
        # ACCOUNTING_AREAS entry, exactly like the Tax module's own
        # Execution Plan reuses this same model (see TAX_HEADS above).
        accounting_execution_areas_by_name = {
            a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).filter(SubstantiveProcedureArea.area.in_(ACCOUNTING_AREAS)).all()
        }
    management_report_narratives = {k: workpaper_narratives.get(k) for k, _ in MANAGEMENT_ACCOUNTS_REPORT_PARTS} if engagement.has_accounting_module else {}

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
        is_secretarial=is_secretarial,
        materiality=materiality,
        qualitative_materiality_factors=qualitative_materiality_factors,
        qualitative_materiality_index=qualitative_materiality_index,
        qualitative_materiality_factor_defs=QUALITATIVE_MATERIALITY_FACTORS,
        qualitative_materiality_ratings=QUALITATIVE_MATERIALITY_RATINGS,
        audit_strategy=audit_strategy,
        audit_strategy_phase_statuses=AUDIT_STRATEGY_PHASE_STATUSES,
        secretarial_plan=secretarial_plan,
        secretarial_compliance_suggestions=secretarial_compliance_suggestions,
        secretarial_activities=SECRETARIAL_ACTIVITIES,
        scope_suggestion=scope_suggestion,
        entity_understanding=entity_understanding,
        entity_fields=ENTITY_UNDERSTANDING_FIELDS,
        latest_public_research=latest_public_research,
        analytical_review=analytical_review,
        trial_balance=trial_balance,
        tb_face_summary=tb_face_summary,
        suggested_categories=suggested_categories,
        financial_statements=financial_statements,
        statements=statements,
        reporting_frameworks=REPORTING_FRAMEWORKS,
        reporting_framework_labels=REPORTING_FRAMEWORK_LABELS,
        general_information_note=fin.general_information_note(engagement.client, engagement),
        default_basis_of_preparation=fin.REPORTING_FRAMEWORK_COMPLIANCE_TEXT.get(engagement.reporting_framework, "") + fin.DEFAULT_BASIS_OF_PREPARATION_TAIL,
        default_closing_notes=fin.DEFAULT_CLOSING_NOTE_TEXT,
        cash_flow_methods=CASH_FLOW_METHODS,
        cash_flow_method_labels=CASH_FLOW_METHOD_LABELS,
        pie_criteria=PIE_CRITERIA,
        sme_act_sectors=SME_ACT_SECTORS,
        sme_act_size_bands=SME_ACT_SIZE_BANDS,
        sme_act_size_thresholds=fin.SME_ACT_SIZE_THRESHOLDS,
        suggested_sme_size_band=fin.classify_sme_size(engagement.sme_staff_headcount, engagement.sme_annual_turnover, engagement.sme_gross_assets),
        category_choices=fin.category_choices(),
        it_change_plan_risk_levels=IT_CHANGE_PLAN_RISK_LEVELS,
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
        filing_index_sections=filing_index_sections,
        workpaper_sections=WORKPAPER_SECTIONS,
        workpaper_reference=workpaper_reference,
        filing_reference=filing_reference,
        effectively_reviewed=effectively_reviewed,
        workpaper_narratives=workpaper_narratives,
        workpaper_narrative_kinds=WORKPAPER_NARRATIVE_KINDS,
        filed_workpapers_by_kind=_filed_workpapers_by_kind(engagement_id),
        REVIEWER_ROLES=REVIEWER_ROLES,
        PARTNER_SIGNOFF_ROLES=PARTNER_SIGNOFF_ROLES,
        ppe_asset_classes=ppe_asset_classes,
        ppe_assets=ppe_assets,
        ppe_depreciation_methods=PPE_DEPRECIATION_METHODS,
        ppe_movement=ppe_movement,
        directors_statement=directors_statement,
        suggested_directors=suggested_directors,
        default_directors_statement_text=fin.default_directors_statement_text(engagement),
        audit_opinion=audit_opinion,
        default_opinion_paragraphs=default_opinion_paragraphs,
        audit_opinion_bases=AUDIT_OPINION_BASES,
        audit_opinion_basis_labels=AUDIT_OPINION_BASIS_LABELS,
        opinion_modifications=OPINION_MODIFICATIONS,
        opinion_modification_labels=OPINION_MODIFICATION_LABELS,
        income_tax_computation=income_tax_computation,
        income_tax_lines=income_tax_lines,
        income_tax_result=income_tax_result,
        income_tax_tb_current=income_tax_tb_current,
        income_tax_reconciliation=income_tax_reconciliation,
        profit_before_tax=profit_before_tax,
        income_tax_item_types=INCOME_TAX_ITEM_TYPES,
        income_tax_item_type_labels=INCOME_TAX_ITEM_TYPE_LABELS,
        deferred_tax_computation=deferred_tax_computation,
        ppe_accounting_nbv=ppe_accounting_nbv,
        deferred_tax_other_items=deferred_tax_other_items,
        deferred_tax_result=deferred_tax_result,
        deferred_tax_tb_net=deferred_tax_tb_net,
        deferred_tax_reconciliation=deferred_tax_reconciliation,
        tax_services=TAX_SERVICES,
        tax_service_labels=TAX_SERVICE_LABELS,
        tax_heads=TAX_HEADS,
        tax_entity_profile=tax_entity_profile,
        tax_entity_classifications=TAX_ENTITY_CLASSIFICATIONS,
        tax_registrations=tax_registrations,
        tax_registration_record_types=TAX_REGISTRATION_RECORD_TYPES,
        tax_deadlines=tax_deadlines,
        tax_analytical_review=tax_analytical_review,
        tax_risk_items=tax_risk_items,
        tax_positions=tax_positions,
        tax_position_classifications=TAX_POSITION_CLASSIFICATIONS,
        tax_position_approval_statuses=TAX_POSITION_APPROVAL_STATUSES,
        tax_return_records=tax_return_records,
        tax_return_statuses=TAX_RETURN_STATUSES,
        tax_information_requests=tax_information_requests,
        tax_info_request_statuses=TAX_INFO_REQUEST_STATUSES,
        tax_research_log=tax_research_log,
        legislative_updates_for_import=legislative_updates_for_import,
        tax_structuring_options=tax_structuring_options,
        tax_disputes=tax_disputes,
        tax_dispute_stages=TAX_DISPUTE_STAGES,
        tax_checklist_by_head=tax_checklist_by_head,
        tax_execution_areas_by_name=tax_execution_areas_by_name,
        current_penalty_interest_rates=current_penalty_interest_rates,
        penalty_interest_rate_types=PENALTY_INTEREST_RATE_TYPES,
        tax_penalty_calculations=tax_penalty_calculations,
        tax_opinion_parts=TAX_OPINION_PARTS,
        tax_opinion_narratives=tax_opinion_narratives,
        tax_health_check_parts=TAX_HEALTH_CHECK_PARTS,
        tax_health_check_narratives=tax_health_check_narratives,
        accounting_services=ACCOUNTING_SERVICES,
        accounting_service_labels=ACCOUNTING_SERVICE_LABELS,
        gl_accounts=gl_accounts,
        gl_account_types=GL_ACCOUNT_TYPES,
        journal_entries=journal_entries,
        journal_entry_sources=JOURNAL_ENTRY_SOURCES,
        bank_reconciliations=bank_reconciliations,
        bank_reconciliation_item_types=BANK_RECONCILIATION_ITEM_TYPES,
        accounting_risk_items=accounting_risk_items,
        cost_centres=cost_centres,
        cost_centre_types=COST_CENTRE_TYPES,
        costing_records=costing_records,
        costing_methods=COSTING_METHODS,
        standard_cost_variances=standard_cost_variances,
        variance_types=VARIANCE_TYPES,
        cvp_analyses=cvp_analyses,
        budgets=budgets,
        budget_types=BUDGET_TYPES,
        kpi_metrics=kpi_metrics,
        kpi_categories=KPI_CATEGORIES,
        accounting_checklist_by_area=accounting_checklist_by_area,
        accounting_execution_areas_by_name=accounting_execution_areas_by_name,
        management_report_parts=MANAGEMENT_ACCOUNTS_REPORT_PARTS,
        management_report_narratives=management_report_narratives,
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


def _default_entity_checklist_items_for(engagement):
    """The firm's default Understanding Business/Assignment checklist
    questions for this engagement's type - Forensic Audit questions
    (Investigative Engagements), cyber/IT/AML-CFT questions (Business
    Intelligence and IT Engagements), the COBE-based Secretarial Client
    Business Understanding Questionnaire (Secretarial, filtered to the
    engagement's selected Activities), or the general ISA 315-style
    DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS for every other type.
    Shared by seed_entity_checklist (first-time seeding) and
    sync_entity_checklist_items (topping up an already-seeded checklist
    with any new questions added to these lists since it was seeded) so
    the two can never drift apart on which list applies to which type."""
    if engagement.type == "Investigative Engagement":
        return FORENSIC_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS
    if engagement.type == "Business Intelligence and IT Engagements":
        return BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS
    if engagement.type == "Secretarial":
        selected_activities = set(engagement.secretarial_activity_list)
        return [
            (section, item_text)
            for section, item_text, activities in SECRETARIAL_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS
            if not selected_activities or selected_activities & set(activities)
        ]
    return DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS


def _unpack_entity_checklist_entry(entry):
    """Most DEFAULT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS-style entries are a
    plain (section, item_text) pair; a BUSINESS_IT_ENTITY_UNDERSTANDING_
    CHECKLIST_ITEMS entry can instead be a (section, item_text,
    response_options) triple - a list of custom answer choices for this one
    item, replacing the generic Yes/No/N-A dropdown (see
    EntityUnderstandingChecklistItem.response_options) for a question
    that's really "pick one of these" rather than yes/no. Returns
    (section, item_text, response_options), with response_options None for
    a plain pair."""
    if len(entry) == 3:
        return entry
    section, item_text = entry
    return section, item_text, None


@engagements_bp.route("/<int:engagement_id>/entity-understanding/checklist/seed", methods=["POST"])
@login_required
def seed_entity_checklist(engagement_id):
    """Populate the Understanding Business/Assignment checklist with the
    firm's default questions for this engagement's type (see
    _default_entity_checklist_items_for), seeded alongside (not instead of)
    the five free-text fields. Mirrors acceptance.seed_acceptance_checklist:
    only does anything the first time, so it's safe to expose as a single
    button - see sync_entity_checklist_items below for topping up a
    checklist that's already been seeded."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_entity_understanding(engagement_id)
    if record.checklist_items:
        flash("The checklist already has items on it.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))
    checklist_items = _default_entity_checklist_items_for(engagement)
    for order, entry in enumerate(checklist_items, start=1):
        section, item_text, response_options = _unpack_entity_checklist_entry(entry)
        db.session.add(EntityUnderstandingChecklistItem(
            entity_understanding_id=record.id,
            section=section,
            item_text=item_text,
            response_options="|".join(response_options) if response_options else None,
            order=order,
            created_by_id=current_user.id,
        ))
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Default checklist items added - tick and comment on each one.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/<int:engagement_id>/entity-understanding/checklist/sync", methods=["POST"])
@login_required
def sync_entity_checklist_items(engagement_id):
    """Top up an ALREADY-seeded checklist with any default questions that
    aren't on it yet (matched by exact item_text), without touching
    anything already there - existing items, their responses and comments
    are left completely alone. This is what lets a firm-wide update to one
    of the *_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS lists (e.g. a compound
    question later split into two, each with its own dropdown - see
    BUSINESS_IT_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS) reach an engagement
    whose checklist was seeded before that update, without wiping out
    everything the team already filled in - seed_entity_checklist above
    only ever does anything the FIRST time, by design, so without this
    there'd be no way to pick up a later addition short of deleting and
    re-seeding the whole checklist from scratch."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_entity_understanding(engagement_id)
    if not record.checklist_items:
        flash("This checklist hasn't been seeded yet - use “Add the firm's default checklist” first.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))
    existing_texts = {i.item_text for i in record.checklist_items}
    max_order = max([i.order for i in record.checklist_items], default=0)
    checklist_items = _default_entity_checklist_items_for(engagement)
    added = 0
    for entry in checklist_items:
        section, item_text, response_options = _unpack_entity_checklist_entry(entry)
        if item_text in existing_texts:
            continue
        max_order += 1
        db.session.add(EntityUnderstandingChecklistItem(
            entity_understanding_id=record.id,
            section=section,
            item_text=item_text,
            response_options="|".join(response_options) if response_options else None,
            order=max_order,
            created_by_id=current_user.id,
        ))
        added += 1
    db.session.commit()
    if added:
        flash(f"Added {added} new default question{'s' if added != 1 else ''} that weren't on this checklist yet.", "success")
    else:
        flash("This checklist already has every current default question on it - nothing to add.", "info")
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
    # A custom-dropdown item (item.response_option_list - see
    # EntityUnderstandingChecklistItem.response_options) accepts its own
    # choices instead of the standard Yes/No/N-A list, since its dropdown
    # offers those in the template instead.
    allowed_responses = item.response_option_list or CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES
    item.response = response if response in allowed_responses else ""
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
    Trial Balance tab), instead of the auditor having to type in every current
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
        flash("Enter or import the preliminary trial balance (Trial Balance tab) before generating analytical review figures from it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))
    if trial_balance.unmapped_count > 0:
        # compute_totals() (via build_all_statements) silently skips any
        # line with no IAS 1 category - generating with unmapped accounts
        # still there would file every figure as a confident-looking zero
        # instead of an honest "not ready yet", which is exactly the trap
        # this guard exists to avoid.
        flash(
            f"{trial_balance.unmapped_count} account(s) on the trial balance still need an IAS 1 category "
            "(see Account Mapping setup on the Trial Balance tab) before generating analytical review figures - "
            "until they're mapped, those accounts' amounts are left out and every figure below would come out as zero.",
            "danger",
        )
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

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


@engagements_bp.route("/<int:engagement_id>/analytical-review/necessity", methods=["POST"])
@login_required
def save_analytical_review_necessity(engagement_id):
    """Records whether Analytical Review has been ticked as NOT necessary
    for this assignment - only offered on Secretarial engagements (per the
    firm's Secretarial Analytical Review guidance: a one-off filing may not
    warrant a full governance/compliance review), but the route itself
    works for any engagement type since the underlying column is generic
    (see AnalyticalReview.not_necessary)."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    review.not_necessary = request.form.get("not_necessary") == "on"
    review.not_necessary_reason = request.form.get("not_necessary_reason", "").strip()
    _touch_analytical_review(review)
    db.session.commit()
    if review.not_necessary:
        flash("Analytical Review marked as not necessary for this assignment.", "info")
    else:
        flash("Analytical Review marked as necessary for this assignment.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/seed-secretarial", methods=["POST"])
@login_required
def seed_secretarial_analytical_review(engagement_id):
    """Populate the Analytical Review with the firm's Secretarial
    governance/compliance analytical points (see SECRETARIAL_ANALYTICAL_
    REVIEW_POINTS) - the Secretarial equivalent of generate_analytical_
    review_from_trial_balance above, seeding plain label + narrative-
    explanation lines (no $ amounts) instead of TB-derived figures, since
    secretarial analytical procedures look at compliance trends and
    governance metrics rather than financial ratios. Only adds points not
    already present (matched by label), so it's safe to run again."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    existing_labels = {l.label for l in review.lines}
    added = 0
    for section, point_text in SECRETARIAL_ANALYTICAL_REVIEW_POINTS:
        label = f"{section} - {point_text}"
        if label not in existing_labels:
            db.session.add(AnalyticalReviewLine(analytical_review_id=review.id, label=label, source="manual"))
            added += 1
    if added:
        _touch_analytical_review(review)
        db.session.commit()
        flash(f"Added {added} secretarial analytical review point(s) - write up the narrative explanation for each.", "success")
    else:
        flash("The default secretarial analytical review points are already there below.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/seed-business-it", methods=["POST"])
@login_required
def seed_business_it_analytical_review(engagement_id):
    """Populate the Analytical Review with the firm's IT-relevant analytical
    points (see BUSINESS_IT_ANALYTICAL_REVIEW_POINTS) - the Business
    Intelligence and IT Engagements equivalent of seed_secretarial_
    analytical_review above: system-generated operational/technical trend
    indicators (change volumes, alert counts, access-review completion,
    patch aging) rather than financial ratios. Only adds points not already
    present (matched by label), so it's safe to run again."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)
    existing_labels = {l.label for l in review.lines}
    added = 0
    for section, point_text in BUSINESS_IT_ANALYTICAL_REVIEW_POINTS:
        label = f"{section} - {point_text}"
        if label not in existing_labels:
            db.session.add(AnalyticalReviewLine(analytical_review_id=review.id, label=label, source="manual"))
            added += 1
    if added:
        _touch_analytical_review(review)
        db.session.commit()
        flash(f"Added {added} IT-relevant analytical review point(s) - write up the narrative explanation for each.", "success")
    else:
        flash("The default IT-relevant analytical review points are already there below.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))


@engagements_bp.route("/<int:engagement_id>/analytical-review/lines/add", methods=["POST"])
@login_required
def add_analytical_review_line(engagement_id):
    """Adds one Analytical Review line - either typed in by hand (label +
    prior/current amounts), or picked from a dropdown of the engagement's
    own trial balance accounts (tb_line_id), in which case the account's
    name and its prior/current net movement (debit minus credit, for each
    period) are used automatically instead of typing them in. Picking an
    account is the recommended path once a trial balance exists: it's the
    same account, guaranteed to agree with the trial balance, rather than
    a manually retyped figure that can drift out of step with it."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    review = _get_or_create_analytical_review(engagement_id)

    def _float_or_none(name):
        raw = request.form.get(name, "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    tb_line_id = request.form.get("tb_line_id", "").strip()
    label = request.form.get("label", "").strip()
    prior_amount = _float_or_none("prior_amount")
    current_amount = _float_or_none("current_amount")

    if tb_line_id.isdigit():
        tb_line = TrialBalanceLine.query.get(int(tb_line_id))
        if not tb_line or tb_line.trial_balance.engagement_id != engagement_id:
            flash("That account doesn't belong to this engagement's trial balance.", "danger")
            return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))
        label = label or tb_line.account_name
        prior_amount = (tb_line.prior_debit or 0.0) - (tb_line.prior_credit or 0.0)
        current_amount = (tb_line.current_debit or 0.0) - (tb_line.current_credit or 0.0)

    if not label:
        flash("Please name the line item (e.g. Revenue, Gross profit) or pick an account from the trial balance.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="analytical"))

    line = AnalyticalReviewLine(
        analytical_review_id=review.id,
        label=label,
        prior_amount=prior_amount,
        current_amount=current_amount,
        explanation=request.form.get("explanation", "").strip(),
        source="tb_account" if tb_line_id.isdigit() else "manual",
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
    # questionnaire (FORENSIC_RISK_LIKELIHOOD_QUESTIONS/_IMPACT_QUESTIONS),
    # and a Business Intelligence and IT Engagement against the cyber/IT/
    # AML-CFT control questionnaire (BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS/
    # _IMPACT_QUESTIONS) - every other type keeps the ordinary audit
    # questionnaire. Must match view_engagement's likelihood_questions/
    # impact_questions above exactly, or a submitted form's fields wouldn't
    # line up with what gets saved.
    if engagement.type == "Investigative Engagement":
        questions = FORENSIC_RISK_LIKELIHOOD_QUESTIONS + FORENSIC_RISK_IMPACT_QUESTIONS
    elif engagement.type == "Business Intelligence and IT Engagements":
        questions = BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS + BUSINESS_IT_RISK_IMPACT_QUESTIONS
    elif engagement.type == "Secretarial":
        questions = SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS + SECRETARIAL_RISK_IMPACT_QUESTIONS
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


def _save_engagement_document(engagement_id, file, category, reference, notes, substantive_area_id=None, filing_index_id=None):
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
        filing_index_id=filing_index_id,
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
    filing_index_id = request.form.get("filing_index_id", "").strip()
    filing_index_id = int(filing_index_id) if filing_index_id.isdigit() else None
    doc, version = _save_engagement_document(
        engagement_id, file, category, reference, request.form.get("notes", "").strip(),
        filing_index_id=filing_index_id,
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
    valid_areas = _substantive_areas_for(engagement)
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
    filing_index_id = request.form.get("filing_index_id", "").strip()
    filing_index_id = int(filing_index_id) if filing_index_id.isdigit() else None
    doc, version = _save_engagement_document(
        engagement_id, file, area_name, reference, request.form.get("notes", "").strip(),
        substantive_area_id=area.id, filing_index_id=filing_index_id,
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
    # When tagged with a WP number from the Filing Index, the download
    # follows the firm's N[code]_[Description]_[Year] filing convention
    # instead of the plain original filename.
    download_name = doc.original_filename
    if doc.filing_index and doc.filing_index.code:
        stem, _, ext = doc.original_filename.rpartition(".")
        description = stem or doc.original_filename
        year = doc.engagement.period_end.year if doc.engagement.period_end else date.today().year
        candidate = f"{doc.filing_index.code}_{description}_{year}.{ext}" if ext else f"{doc.filing_index.code}_{description}_{year}"
        download_name = secure_filename(candidate) or doc.original_filename
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], doc.stored_filename, as_attachment=True,
        download_name=download_name,
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


# ---------- Generated workpapers (filed under Documents, sign-off tracked) ----------
#
# Every downloadable working paper the app can build (Financial Statements,
# Trial Balance & Adjustments, the checklists, Substantive Procedures, etc)
# is generated and FILED here rather than streamed straight to the
# browser - it becomes an ordinary Document (tagged with the firm's N-code
# filing reference where one applies, exactly like a manually-uploaded and
# WP-No.-tagged file), with its own Prepared/Reviewed/Partner sign-off.
# Downloading it is then a separate, explicit action on that filed copy
# (the existing download_document route above) rather than something that
# happens automatically the moment it's generated.

# Which engagement tab each generated workpaper's routes should redirect
# back to, and its return-tab default for when something goes wrong (e.g.
# missing trial balance).
WORKPAPER_KIND_TABS = {
    "financial_statements": "finalisation",
    "trial_balance_adjustments": "finalisation",
    "rep_letter": "finalisation",
    "report_to_management": "finalisation",
    "forensic_report": "finalisation",
    "it_audit_report": "finalisation",
    "file_summary": "finalisation",
    "finalisation_checklist": "finalisation",
    "acceptance_checklist": "acceptance",
    "entity_checklist": "entity",
    "engagement_checklist": "checklist",
    "substantive_procedures_docx": "substantive",
    "substantive_procedures_xlsx": "substantive",
}


def _filed_workpapers_by_kind(engagement_id):
    """Every generated-and-filed workpaper for this engagement, grouped by
    kind and ordered newest version first - what each tab's "Filed
    versions" list renders. A dict of lists, never missing a key that's
    actually been generated at least once."""
    docs = (
        Document.query
        .filter_by(engagement_id=engagement_id, is_generated=True)
        .order_by(Document.workpaper_kind, Document.version.desc())
        .all()
    )
    by_kind = {}
    for d in docs:
        by_kind.setdefault(d.workpaper_kind, []).append(d)
    return by_kind


def _file_generated_workpaper(engagement, kind, category_label, filing_code, description_stub, ext, buf, reference_override=None):
    """Generate-and-file step shared by every "Generate & File" button: saves
    the already-built file (buf, a BytesIO from workpapers.py) to disk and
    records it as a new Document, following the same N[code]_[Description]
    filing convention as a manually-tagged upload whenever filing_code
    matches a real Filing Index entry (the download route then adds the
    year and applies it automatically, exactly like any other WP-No.-tagged
    Document) - otherwise falls back to a plain descriptive filename, same
    as the old direct-download naming did for sections with no single N-code
    (the Engagement Checklist, Substantive Procedures, the Forensic report).

    Every previous version of this SAME kind for this engagement is kept
    (per the firm's own Filing Index policy: "superseded, not overwritten")
    - only its is_current_version flag flips to False. The new version
    starts with a clean slate for Reviewed/Partner-signed, since a freshly
    generated file is new work needing its own review."""
    filing_section = FilingIndexSection.query.filter_by(code=filing_code).first() if filing_code else None

    prior_versions = Document.query.filter_by(engagement_id=engagement.id, workpaper_kind=kind).all()
    for p in prior_versions:
        p.is_current_version = False
    version = len(prior_versions) + 1

    stored_name = f"eng{engagement.id}_wp_{kind}_{uuid.uuid4().hex[:10]}.{ext}"
    with open(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name), "wb") as f:
        f.write(buf.getvalue())

    doc = Document(
        engagement_id=engagement.id,
        original_filename=f"{description_stub}.{ext}",
        stored_filename=stored_name,
        category=category_label,
        reference=reference_override or filing_code,
        version=version,
        uploaded_by_id=current_user.id,
        uploaded_at=datetime.utcnow(),
        filing_index_id=filing_section.id if filing_section else None,
        is_generated=True,
        workpaper_kind=kind,
        is_current_version=True,
    )
    db.session.add(doc)
    db.session.commit()
    return doc


@engagements_bp.route("/documents/<int:doc_id>/review", methods=["POST"])
@login_required
def review_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if doc.uploaded_by_id == current_user.id:
        flash("You can't review a working paper you generated yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))
    doc.reviewed_by_id = current_user.id
    doc.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Working paper marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


@engagements_bp.route("/documents/<int:doc_id>/unreview", methods=["POST"])
@login_required
def unreview_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    doc.reviewed_by_id = None
    doc.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


@engagements_bp.route("/documents/<int:doc_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if doc.uploaded_by_id == current_user.id:
        flash("You can't give the partner sign-off on a working paper you generated yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))
    doc.partner_signed_by_id = current_user.id
    doc.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


@engagements_bp.route("/documents/<int:doc_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_generated_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _ensure_engagement_access(doc.engagement)
    if not doc.is_generated:
        abort(404)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    doc.partner_signed_by_id = None
    doc.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab=WORKPAPER_KIND_TABS.get(doc.workpaper_kind, "documents")))


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
        assigned_to_id=int(request.form.get("assigned_to_id")) if request.form.get("assigned_to_id") else None,
        due_date=datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None,
        priority=request.form.get("priority", "Normal"),
        status=request.form.get("status", "To Do"),
    )
    db.session.add(task)
    db.session.flush()
    if task.assigned_to_id and task.assigned_to_id != current_user.id:
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task assigned: {task.title}",
            f"{current_user.name} assigned you a task on {engagement.client.name} - {engagement.title}:\n\n"
            f"{task.title}\n"
            + (f"Due {task.due_date.strftime('%d %b %Y')}\n" if task.due_date else "")
            + (f"\n{task.description}" if task.description else ""),
        )
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
    old_assigned_to_id = task.assigned_to_id
    old_status = task.status
    old_due_date = task.due_date
    task.title = request.form.get("title", task.title)
    task.description = request.form.get("description", task.description)
    task.assigned_to_id = int(request.form.get("assigned_to_id")) if request.form.get("assigned_to_id") else None
    due_date = request.form.get("due_date")
    task.due_date = datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else None
    task.priority = request.form.get("priority", task.priority)
    task.status = request.form.get("status", task.status)
    if task.assigned_to_id and task.assigned_to_id != old_assigned_to_id:
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task assigned: {task.title}",
            f"{current_user.name} assigned you a task on {task.engagement.client.name} - {task.engagement.title}:\n\n{task.title}"
            + (f"\nDue {task.due_date.strftime('%d %b %Y')}" if task.due_date else ""),
        )
    elif task.assigned_to_id and task.assigned_to_id == old_assigned_to_id and (task.status != old_status or task.due_date != old_due_date):
        changes = []
        if task.status != old_status:
            changes.append(f"status is now {task.status}")
        if task.due_date != old_due_date:
            changes.append(f"due date is now {task.due_date.strftime('%d %b %Y') if task.due_date else 'unset'}")
        notify_task_assignment(
            current_user, task.assigned_to_id,
            f"Task updated: {task.title}",
            f"{current_user.name} updated a task assigned to you on {task.engagement.client.name} - {task.engagement.title}:\n\n"
            f"{task.title} - {', '.join(changes)}.",
        )
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
    calc.quantitative_important = request.form.get("quantitative_important") == "on"
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


# ---------- Planning: Qualitative Materiality Index ----------
# See models.QualitativeMaterialityFactor/QUALITATIVE_MATERIALITY_FACTORS -
# a judgement-based complement to the dollar-figure materiality calculator
# above, for matters that can be material regardless of size. One form
# saves every factor's rating/notes at once (there are only 5 of them),
# rather than a separate save action per factor.

@engagements_bp.route("/<int:engagement_id>/qualitative-materiality/save", methods=["POST"])
@login_required
def save_qualitative_materiality(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    existing = {f.factor_key: f for f in QualitativeMaterialityFactor.query.filter_by(engagement_id=engagement_id).all()}
    for factor_key, _label, _help in QUALITATIVE_MATERIALITY_FACTORS:
        rating = request.form.get(f"rating__{factor_key}", "").strip()
        if rating not in QUALITATIVE_MATERIALITY_RATINGS:
            rating = ""
        notes = request.form.get(f"notes__{factor_key}", "").strip()
        factor = existing.get(factor_key)
        if not factor:
            if not rating and not notes:
                continue  # nothing to save - don't create an empty row
            factor = QualitativeMaterialityFactor(engagement_id=engagement_id, factor_key=factor_key)
            db.session.add(factor)
        factor.rating = rating
        factor.notes = notes
        factor.updated_by_id = current_user.id
        factor.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Qualitative Materiality Index saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


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


# ---------- Secretarial Engagement Plan (Secretarial engagements only - replaces the ordinary Planning tab content) ----------

@engagements_bp.route("/<int:engagement_id>/secretarial-plan/save", methods=["POST"])
@login_required
def save_secretarial_plan(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    plan = SecretarialPlan.query.filter_by(engagement_id=engagement_id).first()
    if not plan:
        plan = SecretarialPlan(engagement_id=engagement_id)
        db.session.add(plan)

    plan.entity_categorization = request.form.get("entity_categorization", "").strip()
    plan.sector_regulators = request.form.get("sector_regulators", "").strip()
    plan.constitutional_constraints = request.form.get("constitutional_constraints", "").strip()

    for field in ("draft_financials_target", "agm_notice_target", "agm_target", "annual_return_target"):
        raw = request.form.get(field, "").strip()
        setattr(plan, field, datetime.strptime(raw, "%Y-%m-%d").date() if raw else None)
    plan.compliance_calendar_notes = request.form.get("compliance_calendar_notes", "").strip()

    plan.meeting_schedule_notes = request.form.get("meeting_schedule_notes", "").strip()
    plan.agenda_preplanning_notes = request.form.get("agenda_preplanning_notes", "").strip()
    plan.document_deadline_notes = request.form.get("document_deadline_notes", "").strip()

    plan.transactional_trigger_notes = request.form.get("transactional_trigger_notes", "").strip()

    plan.onboarding_risk_integration_notes = request.form.get("onboarding_risk_integration_notes", "").strip()
    plan.remediation_milestones_notes = request.form.get("remediation_milestones_notes", "").strip()
    plan.register_reconciliation_notes = request.form.get("register_reconciliation_notes", "").strip()

    plan.completed_by_id = current_user.id
    plan.completed_at = datetime.utcnow()
    # Re-saving the plan invalidates any earlier review/partner sign-off.
    plan.reviewed_by_id = None
    plan.reviewed_at = None
    plan.partner_signed_by_id = None
    plan.partner_signed_at = None

    db.session.commit()
    flash("Secretarial Engagement Plan saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


@engagements_bp.route("/secretarial-plan/<int:plan_id>/review", methods=["POST"])
@login_required
def review_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not plan.is_complete:
        flash("Fill in the Entity Profile, Board Cycle, Trigger Mapping and Risk & Quality fields before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    if plan.completed_by_id == current_user.id:
        flash("You can't review a secretarial plan you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    plan.reviewed_by_id = current_user.id
    plan.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Secretarial Engagement Plan marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


@engagements_bp.route("/secretarial-plan/<int:plan_id>/unreview", methods=["POST"])
@login_required
def unreview_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    plan.reviewed_by_id = None
    plan.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


@engagements_bp.route("/secretarial-plan/<int:plan_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not plan.is_complete:
        flash("Fill in the Entity Profile, Board Cycle, Trigger Mapping and Risk & Quality fields before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    if plan.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a plan you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))
    plan.partner_signed_by_id = current_user.id
    plan.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


@engagements_bp.route("/secretarial-plan/<int:plan_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_secretarial_plan(plan_id):
    plan = SecretarialPlan.query.get_or_404(plan_id)
    _ensure_engagement_access(plan.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    plan.partner_signed_by_id = None
    plan.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=plan.engagement_id, tab="planning"))


# ---------- IT Change & Release Testing Plan (Business Intelligence and IT
# Engagements only) - a Planning-tab register of known/planned system
# changes during the audit period, used to decide which changes to pull
# into the ITGC change-management sample tested under Substantive
# Procedures. See models.ITChangePlanItem. ----------

@engagements_bp.route("/<int:engagement_id>/it-change-plan/add", methods=["POST"])
@login_required
def add_it_change_plan_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    change_description = request.form.get("change_description", "").strip()
    if not change_description:
        flash("Describe the planned/known change before adding it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))
    planned_date_str = request.form.get("planned_date", "").strip()
    planned_date = datetime.strptime(planned_date_str, "%Y-%m-%d").date() if planned_date_str else None
    risk_level = request.form.get("risk_level", "").strip()
    if risk_level not in IT_CHANGE_PLAN_RISK_LEVELS:
        risk_level = ""
    max_order = max([i.order for i in engagement.it_change_plan_items], default=0)
    db.session.add(ITChangePlanItem(
        engagement_id=engagement_id, change_description=change_description, planned_date=planned_date,
        risk_level=risk_level, order=max_order + 1, created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Change added to the plan.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


@engagements_bp.route("/it-change-plan/<int:item_id>/update", methods=["POST"])
@login_required
def update_it_change_plan_item(item_id):
    item = ITChangePlanItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.change_description = request.form.get("change_description", item.change_description).strip() or item.change_description
    planned_date_str = request.form.get("planned_date", "").strip()
    item.planned_date = datetime.strptime(planned_date_str, "%Y-%m-%d").date() if planned_date_str else None
    risk_level = request.form.get("risk_level", "").strip()
    item.risk_level = risk_level if risk_level in IT_CHANGE_PLAN_RISK_LEVELS else ""
    item.selected_for_testing = request.form.get("selected_for_testing") == "on"
    item.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Change updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="planning"))


@engagements_bp.route("/it-change-plan/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_it_change_plan_item(item_id):
    item = ITChangePlanItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    flash("Change removed from the plan.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="planning"))


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
    Investigative Engagement, the cyber/IT/AML-CFT set (see BUSINESS_IT_
    FINALISATION_CHECKLIST_ITEMS) on a Business Intelligence and IT
    Engagement, the COBE-compliant five-phase set (see SECRETARIAL_
    FINALISATION_CHECKLIST_ITEMS) on a Secretarial engagement, the ordinary
    close-out set (see DEFAULT_FINALISATION_CHECKLIST_ITEMS) otherwise.
    Mirrors seed_entity_checklist: only does anything the first time, so
    it's safe to expose as a single button."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_finalisation_checklist(engagement_id)
    if record.checklist_items:
        flash("The checklist already has items on it.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    if engagement.type == "Investigative Engagement":
        source_items = FORENSIC_FINALISATION_CHECKLIST_ITEMS
    elif engagement.type == "Business Intelligence and IT Engagements":
        source_items = BUSINESS_IT_FINALISATION_CHECKLIST_ITEMS
    elif engagement.type == "Secretarial":
        source_items = SECRETARIAL_FINALISATION_CHECKLIST_ITEMS
    else:
        source_items = DEFAULT_FINALISATION_CHECKLIST_ITEMS
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


def _read_alt_format_tb_workbook(workbook):
    """Recognizes an alternate trial balance export layout some accounting
    systems produce instead of the app's own template: one sheet per year
    (e.g. '2024', '2025'), each with a header row like ('GL Code', 'Name',
    'Opening balance', '<year> Transactions', 'Closing balance') and a
    single signed closing balance per account rather than separate debit/
    credit columns.

    Returns (rows, note) shaped like the standard import - rows are dicts
    keyed by TB_IMPORT_COLUMNS' header names, ready for
    financials.parse_tb_rows() exactly like the standard layout's rows -
    or (None, None) if the workbook doesn't look like this format at all.

    Two or more matching sheets are treated as successive years - sheet
    names that parse as plain years (e.g. '2024') are sorted numerically
    and the latest becomes the current year, the one before it the prior
    year; otherwise workbook order is used, last sheet as current year. A
    single matching sheet is read as current year only, with prior year
    left at zero for every account. Accounts are matched between the two
    years by GL Code (falling back to the account name when no code
    column exists). Closing balances are converted to debit/credit with
    the standard accounting sign convention: a positive balance is a
    debit, a negative balance is a credit."""
    sheet_accounts = {}  # sheet name -> {code_or_name: (code, name, closing_balance)}

    for sheet_name in workbook.sheetnames:
        ws = workbook[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        header_idx = None
        headers = None
        for i, row in enumerate(rows):
            cells = [str(c).strip().lower() if c is not None else "" for c in row]
            if "closing balance" in cells and ("name" in cells or "account name" in cells):
                header_idx = i
                headers = [str(c).strip() if c is not None else "" for c in row]
                break
        if header_idx is None:
            continue

        lower_headers = [h.lower() for h in headers]
        name_col = lower_headers.index("name") if "name" in lower_headers else lower_headers.index("account name")
        closing_col = lower_headers.index("closing balance")
        code_col = next((lower_headers.index(c) for c in ("gl code", "account code", "code") if c in lower_headers), None)

        accounts = {}
        for row in rows[header_idx + 1:]:
            if row is None or len(row) <= max(name_col, closing_col):
                continue
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            name = row[name_col]
            if name is None or not str(name).strip():
                continue
            name = str(name).strip()
            code = str(row[code_col]).strip() if code_col is not None and row[code_col] is not None else ""
            closing = row[closing_col]
            try:
                closing = float(closing) if closing not in (None, "") else 0.0
            except (TypeError, ValueError):
                closing = 0.0
            accounts[code or name] = (code, name, closing)

        if accounts:
            sheet_accounts[sheet_name] = accounts

    if not sheet_accounts:
        return None, None

    def _year_sort_key(name):
        try:
            return (0, int(str(name).strip()))
        except ValueError:
            return (1, str(name))

    ordered_sheet_names = sorted(sheet_accounts.keys(), key=_year_sort_key)
    current_sheet_name = ordered_sheet_names[-1]
    prior_sheet_name = ordered_sheet_names[-2] if len(ordered_sheet_names) > 1 else None
    current_accounts = sheet_accounts[current_sheet_name]
    prior_accounts = sheet_accounts[prior_sheet_name] if prior_sheet_name else {}

    def _split(balance):
        if balance > 0:
            return balance, 0.0
        if balance < 0:
            return 0.0, abs(balance)
        return 0.0, 0.0

    combined = {}
    for key, (code, name, balance) in current_accounts.items():
        combined[key] = {"code": code, "name": name, "current": balance, "prior": 0.0}
    for key, (code, name, balance) in prior_accounts.items():
        if key in combined:
            combined[key]["prior"] = balance
        else:
            combined[key] = {"code": code, "name": name, "current": 0.0, "prior": balance}

    out_rows = []
    for acc in combined.values():
        cur_debit, cur_credit = _split(acc["current"])
        prior_debit, prior_credit = _split(acc["prior"])
        out_rows.append({
            "Account Code": acc["code"],
            "Account Name": acc["name"],
            "Current Year Debit": cur_debit,
            "Current Year Credit": cur_credit,
            "Prior Year Debit": prior_debit,
            "Prior Year Credit": prior_credit,
        })

    if prior_sheet_name:
        note = (
            f"Recognized '{current_sheet_name}' and '{prior_sheet_name}' as this year's and last year's trial "
            "balance (GL Code / Name / Closing balance format) and matched accounts between them by GL Code."
        )
    else:
        note = (
            f"Recognized '{current_sheet_name}' as this year's trial balance (GL Code / Name / Closing balance "
            "format) - no earlier year's tab was found, so prior year was left blank."
        )
    return out_rows, note


def _read_tb_upload_rows(file_storage, filename):
    """Returns (rows, note): a list of {header: value} dicts from an
    uploaded .xlsx/.xls or .csv trial balance file, and an optional note
    to flash to the user (e.g. when an alternate format was auto-
    recognized). Raises ValueError with a plain-English message if it
    can't find a usable header row in any recognized format."""
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        content = file_storage.read().decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(content))), None

    workbook = openpyxl.load_workbook(file_storage, data_only=True)
    sheet = workbook.active
    all_rows = list(sheet.iter_rows(values_only=True))
    header_idx = None
    for i, row in enumerate(all_rows):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if "account name" in cells:
            header_idx = i
            break
    if header_idx is not None:
        headers = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
        data_rows = []
        for row in all_rows[header_idx + 1:]:
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            data_rows.append({h: v for h, v in zip(headers, row) if h})
        return data_rows, None

    # Not the standard single-sheet "Account Name" layout - check whether
    # this is the alternate one-sheet-per-year "GL Code/Name/Closing
    # balance" export before giving up.
    alt_rows, alt_note = _read_alt_format_tb_workbook(workbook)
    if alt_rows is not None:
        return alt_rows, alt_note

    raise ValueError(
        "Could not find a header row containing 'Account Name' in that file. "
        "Download the template below and use its column headers."
    )


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
    # Entering real trial balance data supersedes an earlier "not
    # applicable" note - the note only makes sense while there's nothing
    # else on this tab.
    tb.not_applicable = False


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
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    if not _allowed_tb_file(file.filename):
        flash("Please upload a .xlsx or .csv file.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    original_name = secure_filename(file.filename)
    try:
        raw_rows, format_note = _read_tb_upload_rows(file, original_name)
        cleaned_rows = fin.parse_tb_rows(raw_rows)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    except Exception:
        flash("Could not read that file - make sure it's a .xlsx or .csv using the template's columns.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

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
    if format_note:
        flash(format_note, "info")
    flash(f"Imported {len(cleaned_rows)} account(s) from '{original_name}'.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/<int:engagement_id>/trial-balance/confirm-suggested-mappings", methods=["POST"])
@login_required
def confirm_suggested_tb_mappings(engagement_id):
    """Batch-confirms the IAS 1 category chosen for each still-unmapped
    account on the Account Mapping setup screen - one submit instead of
    the one-account-at-a-time "Map" form, meant to be used alongside
    fin.suggest_fs_category()'s pre-filled suggestions there: the
    accountant reviews/adjusts each row's dropdown (already pre-filled
    with a suggestion where one exists), then confirms the whole batch at
    once. A row left as "Unmapped" is simply skipped - it stays on the
    list for next time, exactly as if this batch action had never run."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    tb = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not tb:
        flash("No trial balance to map yet.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    mapped_count = 0
    for line in tb.lines:
        if line.fs_category:
            continue  # already mapped - this batch only ever touches unmapped rows
        category = request.form.get(f"fs_category__{line.id}", "").strip()
        if not category or category not in fin.CATEGORY_BY_CODE:
            continue  # left as "Unmapped" (or an unrecognized value) - skip, don't guess
        line.fs_category = category
        _upsert_coa_mapping(engagement.client_id, line.account_name, category, current_user.id)
        mapped_count += 1

    if mapped_count == 0:
        flash("No mappings were confirmed - every row was left as “Unmapped”.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    _touch_trial_balance(tb)
    db.session.commit()
    remaining = tb.unmapped_count
    if remaining:
        flash(f"{mapped_count} account(s) mapped. {remaining} still need a category.", "success")
    else:
        flash(f"{mapped_count} account(s) mapped - every account now has an IAS 1 category.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/<int:engagement_id>/trial-balance/lines/add", methods=["POST"])
@login_required
def add_trial_balance_line(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    name = request.form.get("account_name", "").strip()
    if not name:
        flash("Please give the account a name.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

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
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


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
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="trial_balance"))


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
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/review", methods=["POST"])
@login_required
def review_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not tb.is_fully_mapped:
        flash("Every account needs an IAS 1 category (or 'Excluded') before the trial balance can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    if tb.completed_by_id == current_user.id:
        flash("You can't review a trial balance you imported/entered yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    tb.reviewed_by_id = current_user.id
    tb.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Trial balance marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


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
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not tb.is_fully_mapped:
        flash("Every account needs an IAS 1 category (or 'Excluded') before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    if tb.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a trial balance you imported/entered yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    tb.partner_signed_by_id = current_user.id
    tb.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


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
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


@engagements_bp.route("/<int:engagement_id>/trial-balance/mark-not-applicable", methods=["POST"])
@login_required
def mark_trial_balance_not_applicable(engagement_id):
    """Records that this engagement genuinely has no trial balance (e.g. a
    Consulting/Secretarial engagement, or an Investigative Engagement
    scoped to a specific matter) instead of leaving the tab looking
    unfinished. Refuses if real data already exists - clear the trial
    balance's lines first if you actually want to switch it to N/A."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    reason = request.form.get("not_applicable_reason", "").strip()
    if not reason:
        flash("Please explain why this engagement has no trial balance.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    tb = _get_or_create_trial_balance(engagement_id)
    if tb.lines:
        flash("This engagement already has trial balance accounts entered - remove them first if it's genuinely not applicable.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    tb.not_applicable = True
    tb.not_applicable_reason = reason
    tb.marked_na_by_id = current_user.id
    tb.marked_na_at = datetime.utcnow()
    db.session.commit()
    flash("Trial balance marked as not applicable for this engagement.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/unmark-not-applicable", methods=["POST"])
@login_required
def unmark_trial_balance_not_applicable(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    tb.not_applicable = False
    tb.not_applicable_reason = None
    tb.marked_na_by_id = None
    tb.marked_na_at = None
    db.session.commit()
    flash("Trial balance no longer marked as not applicable.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


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
    # Which framework the Financial Statements below are prepared under -
    # see models.REPORTING_FRAMEWORKS. Only "Full IFRS"/"IFRS for SMEs" get
    # the auto-generated, numbered Notes (financials.build_notes); changing
    # this takes effect immediately since the statements are always
    # rebuilt live from the trial balance, never stored.
    framework = request.form.get("reporting_framework", "").strip()
    if framework in dict(REPORTING_FRAMEWORKS):
        engagement.reporting_framework = framework
    # Which method the Cash Flow Statement's operating activities section
    # uses (IAS 7.18) - see models.CASH_FLOW_METHODS. Every other figure on
    # every statement is unaffected either way.
    cash_flow_method = request.form.get("cash_flow_method", "").strip()
    if cash_flow_method in dict(CASH_FLOW_METHODS):
        engagement.cash_flow_method = cash_flow_method
    # Public Interest Entity checklist - see models.PIE_CRITERIA. A pure
    # guidance checklist: it never sets reporting_framework itself, so an
    # unticked box here never silently changes which statements are shown.
    for code, _ in PIE_CRITERIA:
        setattr(engagement, code, request.form.get(code) == "on")
    # Small and Medium Enterprises Act classification - recorded as plain
    # client information (see models.SME_ACT_SECTORS / SME_ACT_SIZE_BANDS);
    # doesn't affect reporting_framework or the statements themselves.
    sme_sector = request.form.get("sme_sector", "").strip()
    engagement.sme_sector = sme_sector if sme_sector in SME_ACT_SECTORS else None
    sme_size_band = request.form.get("sme_size_band", "").strip()
    engagement.sme_size_band = sme_size_band if sme_size_band in dict(SME_ACT_SIZE_BANDS) else None

    def _blank_tolerant_number(field_name, cast):
        raw = request.form.get(field_name, "").strip().replace(",", "")
        if not raw:
            return None
        try:
            return cast(raw)
        except ValueError:
            return None

    engagement.sme_annual_turnover = _blank_tolerant_number("sme_annual_turnover", float)
    engagement.sme_gross_assets = _blank_tolerant_number("sme_gross_assets", float)
    engagement.sme_staff_headcount = _blank_tolerant_number("sme_staff_headcount", int)
    fs.basis_of_preparation = request.form.get("basis_of_preparation", "").strip()
    # Notes to the Financial Statements are free text and editable here, same
    # as the basis of preparation - but note that the FIGURES throughout the
    # statements themselves are never set from this form: they always come
    # live from the adjusted TrialBalance (financials.py) and stay that way.
    fs.notes_to_financial_statements = request.form.get("notes_to_financial_statements", "").strip()
    # The three standard closing notes (see financials.DEFAULT_CLOSING_NOTE_TEXT
    # for what the Finalisation tab shows before any of these have been saved).
    fs.related_party_note = request.form.get("related_party_note", "").strip()
    fs.commitments_note = request.form.get("commitments_note", "").strip()
    fs.subsequent_events_note = request.form.get("subsequent_events_note", "").strip()
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

def _get_or_seed_workpaper_narrative(engagement_id, kind, engagement=None):
    """The existing WorkpaperNarrative row for (engagement, kind), or a new
    unsaved one pre-filled with the firm's default starting wording - so the
    edit form always has sensible text to start from rather than a blank
    box, the first time this narrative is opened on an engagement. engagement
    is optional (only needed so default_workpaper_narrative_body can pick a
    type-appropriate default, e.g. "rep_letter" on a Business Intelligence
    and IT Engagement) - pass it when already in scope to avoid a query."""
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind=kind).first()
    if not narrative:
        narrative = WorkpaperNarrative(engagement_id=engagement_id, kind=kind, body=default_workpaper_narrative_body(kind, engagement))
    return narrative


def _narrative_tab(kind):
    """Which engagement-detail tab a WorkpaperNarrative's own Save/Review/
    Partner-sign actions should redirect back to - "tax" for a Tax Opinion/
    Tax Health Check part (see models.TAX_OPINION_PARTS/TAX_HEALTH_CHECK_
    PARTS), "accounting" for a Management Accounts Report part (see
    models.MANAGEMENT_ACCOUNTS_REPORT_PARTS), "finalisation" for every other
    kind (rep_letter, report_to_management, forensic_executive_summary),
    unchanged from before the Tax/Accounting modules existed."""
    if kind.startswith("tax_opinion") or kind.startswith("tax_health_check"):
        return "tax"
    if kind.startswith("mgmt_report"):
        return "accounting"
    return "finalisation"


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
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_narrative_tab(kind)))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/review", methods=["POST"])
@login_required
def review_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if narrative.completed_by_id == current_user.id:
        flash("You can't review a write-up you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))
    narrative.reviewed_by_id = current_user.id
    narrative.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f"{narrative.label} marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


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
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if narrative.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a write-up you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))
    narrative.partner_signed_by_id = current_user.id
    narrative.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


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
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


# ---------- Substantive Procedures (system-based: by audit area, driven by risk + industry) ----------

def _substantive_areas_for(engagement):
    """The area names valid for this engagement's Substantive Procedures tab
    (Execution Plan, on a Secretarial engagement) - the four forensic
    evidence-type categories on an Investigative Engagement, the four
    cyber/IT/AML-CFT assurance domains on a Business Intelligence and IT
    Engagement, the five Execution Plan workstreams on a Secretarial
    engagement, or the ordinary financial-statement audit areas otherwise.
    Used wherever an area name submitted from a form needs validating
    against whichever set is actually in play."""
    if engagement.type == "Investigative Engagement":
        return FORENSIC_SUBSTANTIVE_AREAS
    if engagement.type == "Business Intelligence and IT Engagements":
        return BUSINESS_IT_SUBSTANTIVE_AREAS
    if engagement.type == "Secretarial":
        return SECRETARIAL_SUBSTANTIVE_AREAS
    return AUDIT_AREAS


def sync_substantive_procedures(engagement):
    """Additive top-up of suggested procedures across every area, driven by
    the engagement's CURRENT Risk Assessment rating and the client's
    CURRENT industry (see AUDIT_AREAS / BASELINE_SUBSTANTIVE_PROCEDURES /
    HIGH_RISK_EXTRA_PROCEDURES / INDUSTRY_EXTRA_PROCEDURES above) - or, on
    an Investigative Engagement, the four forensic evidence-type categories,
    or on a Business Intelligence and IT Engagement, the four cyber/IT/
    AML-CFT assurance domains, instead (see FORENSIC_SUBSTANTIVE_AREAS /
    FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES / BUSINESS_IT_SUBSTANTIVE_AREAS
    / BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES above; the risk/industry
    extras are audit-area captions and don't apply there, so those two
    engagement types only ever get their baseline list). This is the shared
    engine behind both the manual "Generate suggested procedures" button
    and the automatic re-sync fired whenever the risk rating or the
    client's industry changes (see sync_substantive_procedures_if_started,
    save_risk_assessment, and clients.edit_client) - so the
    suggested-procedures checklist keeps itself current instead of only
    ever reflecting whatever the risk rating/industry happened to be the
    first time someone clicked the button. Exactly like the manual button,
    it only ever ADDS whatever's newly applicable and not already present -
    it never edits, reorders or removes an existing item (ticked-off work,
    sign-offs and manually added procedures are never touched), so it's
    safe to call as often as needed. Returns how many procedures were
    added."""
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic:
        areas = FORENSIC_SUBSTANTIVE_AREAS
        baseline = FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES
        high_risk = False
        industry_map = {}
    elif is_business_it:
        areas = BUSINESS_IT_SUBSTANTIVE_AREAS
        baseline = BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES
        high_risk = False
        industry_map = {}
    elif is_secretarial:
        areas = SECRETARIAL_SUBSTANTIVE_AREAS
        baseline = SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES
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
                item = SubstantiveProcedureItem(area_id=area.id, procedure_text=text, source=source, order=next_order)
                if is_secretarial:
                    details = SECRETARIAL_EXECUTION_TASK_DETAILS.get(text)
                    if details:
                        item.trigger_event, item.responsible_role, item.target_output = details
                if is_business_it:
                    item.procedure_kind = BUSINESS_IT_PROCEDURE_KIND.get(text)
                db.session.add(item)
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
    elif engagement.type == "Business Intelligence and IT Engagements":
        if added_count:
            flash(f"Generated {added_count} suggested procedure(s) across the four cyber/IT/AML-CFT assurance domains.", "success")
        else:
            flash("No new suggested procedures to add - the full baseline procedure list is already there below.", "info")
    elif engagement.type == "Secretarial":
        if added_count:
            flash(f"Generated {added_count} Execution Plan task(s) across the five secretarial workstreams.", "success")
        else:
            flash("No new Execution Plan tasks to add - the full baseline task list is already there below.", "info")
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


# ---------- PPE Depreciation policy / Asset Register (Substantive Procedures > Property, Plant and Equipment) ----------

def _ppe_plain_data(engagement_id):
    """Depreciation policy (PPEAssetClass) and Asset Register (PPEAsset)
    rows for an engagement, both as ORM objects (for the template to render
    and edit) and as the plain dicts financials.py's
    build_ppe_movement_schedule()/ppe_accounting_policy_text() expect -
    keeping those functions free of any ORM/session dependency, consistent
    with the rest of financials.py."""
    classes = PPEAssetClass.query.filter_by(engagement_id=engagement_id).order_by(PPEAssetClass.order, PPEAssetClass.id).all()
    assets = PPEAsset.query.filter_by(engagement_id=engagement_id).order_by(PPEAsset.order, PPEAsset.id).all()
    class_dicts = [
        {"id": c.id, "name": c.name, "depreciation_method": c.depreciation_method,
         "rate_percent": c.rate_percent, "useful_life_years": c.useful_life_years}
        for c in classes
    ]
    asset_dicts = [
        {"asset_class_id": a.asset_class_id, "cost": a.cost or 0.0,
         "opening_accumulated_depreciation": a.opening_accumulated_depreciation or 0.0,
         "current_year_depreciation": a.current_year_depreciation or 0.0,
         "disposal_cost": a.disposal_cost or 0.0,
         "disposal_accumulated_depreciation": a.disposal_accumulated_depreciation or 0.0,
         "date_acquired": a.date_acquired}
        for a in assets
    ]
    return classes, assets, class_dicts, asset_dicts


def _ppe_movement_and_policy(class_dicts, asset_dicts, period_end):
    """The (ppe_movement, ppe_policy_text) pair fin.build_all_statements()
    takes to replace the generic PPE note/policy with the Asset Register's
    own movement schedule and the Depreciation policy's own wording - both
    come back None (no change in behaviour) when neither working paper has
    any rows yet, so an engagement that hasn't touched this feature is
    completely unaffected. Takes the plain dicts _ppe_plain_data() already
    built, rather than re-querying, since callers usually need both."""
    ppe_movement = fin.build_ppe_movement_schedule(class_dicts, asset_dicts, period_end)
    ppe_policy_text = fin.ppe_accounting_policy_text(class_dicts)
    return ppe_movement, ppe_policy_text


# ---------- Directors' Statement (Finalisation tab) ----------

def _get_or_create_directors_statement(engagement):
    record = DirectorsStatement.query.filter_by(engagement_id=engagement.id).first()
    if not record:
        directors = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, role="Director", status="Confirmed").order_by(ClientKeyPerson.id).all()
        record = DirectorsStatement(
            engagement_id=engagement.id, statement_text=fin.default_directors_statement_text(engagement),
            director1_name=directors[0].full_name if len(directors) > 0 else None,
            director2_name=directors[1].full_name if len(directors) > 1 else None,
            statement_date=engagement.period_end,
        )
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/directors-statement/save", methods=["POST"])
@login_required
def save_directors_statement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_directors_statement(engagement)
    record.statement_text = request.form.get("statement_text", "").strip()
    record.director1_name = request.form.get("director1_name", "").strip() or None
    record.director1_title = request.form.get("director1_title", "").strip() or "Director"
    record.director2_name = request.form.get("director2_name", "").strip() or None
    record.director2_title = request.form.get("director2_title", "").strip() or "Director"
    record.statement_date = _parse_date(request.form.get("statement_date")) or engagement.period_end
    record.updated_by_id = current_user.id
    record.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Directors' Statement saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/directors-statement/use-directors-on-file", methods=["POST"])
@login_required
def use_directors_on_file(engagement_id):
    """Re-pulls director names from this client's confirmed Directors &
    Shareholders list (Client detail page) - a one-click refresh for when
    that list has changed since the statement was first seeded, rather
    than the preparer having to retype names by hand."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_directors_statement(engagement)
    directors = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, role="Director", status="Confirmed").order_by(ClientKeyPerson.id).all()
    if not directors:
        flash("No confirmed Directors are on file for this client yet - add them on the client's Directors & Shareholders list first.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    record.director1_name = directors[0].full_name if len(directors) > 0 else None
    record.director2_name = directors[1].full_name if len(directors) > 1 else None
    record.updated_by_id = current_user.id
    record.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Director names refreshed from the client's Directors & Shareholders list.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


# ---------- Audit Opinion / Review Report (Finalisation tab) ----------

def _get_or_create_audit_opinion(engagement):
    record = AuditOpinion.query.filter_by(engagement_id=engagement.id).first()
    if not record:
        report_basis = "audit" if engagement.type == "Audit" else "review"
        defaults = fin.default_audit_opinion_paragraphs(
            report_basis, "unmodified", engagement.client.name, engagement.period_end,
            REPORTING_FRAMEWORK_LABELS.get(engagement.reporting_framework),
        )
        record = AuditOpinion(engagement_id=engagement.id, report_basis=report_basis, modification="unmodified", report_date=engagement.period_end, **defaults)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/audit-opinion/save", methods=["POST"])
@login_required
def save_audit_opinion(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_audit_opinion(engagement)
    record.report_basis = request.form.get("report_basis") if request.form.get("report_basis") in AUDIT_OPINION_BASES else record.report_basis
    record.modification = request.form.get("modification") if request.form.get("modification") in OPINION_MODIFICATIONS else record.modification
    record.basis_for_modification = request.form.get("basis_for_modification", "").strip()
    record.opinion_paragraph = request.form.get("opinion_paragraph", "").strip()
    record.basis_paragraph = request.form.get("basis_paragraph", "").strip()
    record.management_responsibility_paragraph = request.form.get("management_responsibility_paragraph", "").strip()
    record.auditor_responsibility_paragraph = request.form.get("auditor_responsibility_paragraph", "").strip()
    record.report_date = _parse_date(request.form.get("report_date")) or engagement.period_end
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Audit Opinion / Review Report saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/audit-opinion/regenerate-wording", methods=["POST"])
@login_required
def regenerate_audit_opinion_wording(engagement_id):
    """Rebuilds the four report paragraphs from scratch for the currently
    selected report basis/modification - for when the preparer has changed
    either of those and wants fresh standard wording to start editing from,
    rather than a stale paragraph left over from the previous choice."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_audit_opinion(engagement)
    report_basis = request.form.get("report_basis") if request.form.get("report_basis") in AUDIT_OPINION_BASES else record.report_basis
    modification = request.form.get("modification") if request.form.get("modification") in OPINION_MODIFICATIONS else record.modification
    defaults = fin.default_audit_opinion_paragraphs(
        report_basis, modification, engagement.client.name, engagement.period_end,
        REPORTING_FRAMEWORK_LABELS.get(engagement.reporting_framework),
    )
    record.report_basis = report_basis
    record.modification = modification
    for k, v in defaults.items():
        setattr(record, k, v)
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Standard wording regenerated for the selected basis/modification - review and tailor it before issuing.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/review", methods=["POST"])
@login_required
def review_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review an Audit Opinion you drafted yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Audit Opinion marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/unreview", methods=["POST"])
@login_required
def unreview_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an Audit Opinion you drafted yourself - ask another partner to sign it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded - the report is now issued.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


@engagements_bp.route("/audit-opinion/<int:opinion_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_audit_opinion(opinion_id):
    record = AuditOpinion.query.get_or_404(opinion_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="finalisation"))


# ---------- Income Tax Computation tab ----------

def _get_or_create_income_tax_computation(engagement_id):
    record = IncomeTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = IncomeTaxComputation(engagement_id=engagement_id, tax_loss_brought_forward=0.0)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/income-tax/save", methods=["POST"])
@login_required
def save_income_tax_computation(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_income_tax_computation(engagement_id)
    record.tax_rate_percent = _parse_float(request.form.get("tax_rate_percent"))
    record.aids_levy_percent = _parse_float(request.form.get("aids_levy_percent"))
    record.tax_loss_brought_forward = _parse_float(request.form.get("tax_loss_brought_forward")) or 0.0
    record.notes = request.form.get("notes", "").strip()
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Income Tax Computation saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="income_tax"))


@engagements_bp.route("/<int:engagement_id>/income-tax/lines/add", methods=["POST"])
@login_required
def add_income_tax_line(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this line.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="income_tax"))
    record = _get_or_create_income_tax_computation(engagement_id)
    item_type = request.form.get("item_type") if request.form.get("item_type") in INCOME_TAX_ITEM_TYPES else "addback"
    order = IncomeTaxAdjustmentLine.query.filter_by(computation_id=record.id).count()
    db.session.add(IncomeTaxAdjustmentLine(
        computation_id=record.id, item_type=item_type, description=description,
        amount=_parse_float(request.form.get("amount")) or 0.0, order=order,
    ))
    db.session.commit()
    flash("Line added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_income_tax_line(line_id):
    line = IncomeTaxAdjustmentLine.query.get_or_404(line_id)
    engagement = line.computation.engagement
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this line.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="income_tax"))
    line.item_type = request.form.get("item_type") if request.form.get("item_type") in INCOME_TAX_ITEM_TYPES else line.item_type
    line.description = description
    line.amount = _parse_float(request.form.get("amount")) or 0.0
    db.session.commit()
    flash("Line updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="income_tax"))


@engagements_bp.route("/income-tax/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_income_tax_line(line_id):
    line = IncomeTaxAdjustmentLine.query.get_or_404(line_id)
    engagement = line.computation.engagement
    _ensure_engagement_access(engagement)
    db.session.delete(line)
    db.session.commit()
    flash("Line removed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/review", methods=["POST"])
@login_required
def review_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review an Income Tax Computation you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Income Tax Computation marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/unreview", methods=["POST"])
@login_required
def unreview_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an Income Tax Computation you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


@engagements_bp.route("/income-tax/<int:computation_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_income_tax_computation(computation_id):
    record = IncomeTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="income_tax"))


# ---------- Deferred Tax Computation tab ----------

def _get_or_create_deferred_tax_computation(engagement_id):
    record = DeferredTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = DeferredTaxComputation(engagement_id=engagement_id)
        db.session.add(record)
        db.session.flush()
    return record


@engagements_bp.route("/<int:engagement_id>/deferred-tax/save", methods=["POST"])
@login_required
def save_deferred_tax_computation(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = _get_or_create_deferred_tax_computation(engagement_id)
    record.tax_rate_percent = _parse_float(request.form.get("tax_rate_percent"))
    record.ppe_tax_base = _parse_float(request.form.get("ppe_tax_base"))
    record.notes = request.form.get("notes", "").strip()
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Deferred Tax Computation saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="deferred_tax"))


@engagements_bp.route("/<int:engagement_id>/deferred-tax/items/add", methods=["POST"])
@login_required
def add_deferred_tax_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this temporary difference.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="deferred_tax"))
    record = _get_or_create_deferred_tax_computation(engagement_id)
    order = DeferredTaxItem.query.filter_by(computation_id=record.id).count()
    db.session.add(DeferredTaxItem(
        computation_id=record.id, description=description,
        accounting_amount=_parse_float(request.form.get("accounting_amount")) or 0.0,
        tax_base_amount=_parse_float(request.form.get("tax_base_amount")) or 0.0,
        order=order,
    ))
    db.session.commit()
    flash("Temporary difference added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/items/<int:item_id>/update", methods=["POST"])
@login_required
def update_deferred_tax_item(item_id):
    item = DeferredTaxItem.query.get_or_404(item_id)
    engagement = item.computation.engagement
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for this temporary difference.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="deferred_tax"))
    item.description = description
    item.accounting_amount = _parse_float(request.form.get("accounting_amount")) or 0.0
    item.tax_base_amount = _parse_float(request.form.get("tax_base_amount")) or 0.0
    db.session.commit()
    flash("Temporary difference updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_deferred_tax_item(item_id):
    item = DeferredTaxItem.query.get_or_404(item_id)
    engagement = item.computation.engagement
    _ensure_engagement_access(engagement)
    db.session.delete(item)
    db.session.commit()
    flash("Temporary difference removed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/review", methods=["POST"])
@login_required
def review_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review a Deferred Tax Computation you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Deferred Tax Computation marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/unreview", methods=["POST"])
@login_required
def unreview_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a Deferred Tax Computation you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


@engagements_bp.route("/deferred-tax/<int:computation_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_deferred_tax_computation(computation_id):
    record = DeferredTaxComputation.query.get_or_404(computation_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="deferred_tax"))


def _parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@engagements_bp.route("/<int:engagement_id>/ppe/asset-classes/add", methods=["POST"])
@login_required
def add_ppe_asset_class(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter an asset class name.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    order = PPEAssetClass.query.filter_by(engagement_id=engagement_id).count()
    db.session.add(PPEAssetClass(
        engagement_id=engagement_id, name=name,
        depreciation_method=request.form.get("depreciation_method") or "Straight-line",
        rate_percent=_parse_float(request.form.get("rate_percent")),
        useful_life_years=_parse_float(request.form.get("useful_life_years")),
        order=order,
    ))
    db.session.commit()
    flash("Asset class added to the Depreciation policy.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/ppe/asset-classes/<int:class_id>/update", methods=["POST"])
@login_required
def update_ppe_asset_class(class_id):
    asset_class = PPEAssetClass.query.get_or_404(class_id)
    engagement = asset_class.engagement
    _ensure_engagement_access(engagement)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter an asset class name.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="substantive"))
    asset_class.name = name
    asset_class.depreciation_method = request.form.get("depreciation_method") or "Straight-line"
    asset_class.rate_percent = _parse_float(request.form.get("rate_percent"))
    asset_class.useful_life_years = _parse_float(request.form.get("useful_life_years"))
    db.session.commit()
    flash("Asset class updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="substantive"))


@engagements_bp.route("/ppe/asset-classes/<int:class_id>/delete", methods=["POST"])
@login_required
def delete_ppe_asset_class(class_id):
    asset_class = PPEAssetClass.query.get_or_404(class_id)
    engagement = asset_class.engagement
    _ensure_engagement_access(engagement)
    # Assets in this class aren't deleted - they just fall back to
    # "Unclassified" in the Asset Register/movement schedule, same as an
    # asset that was never assigned a class, rather than losing their data.
    for a in asset_class.assets:
        a.asset_class_id = None
    db.session.delete(asset_class)
    db.session.commit()
    flash("Asset class removed from the Depreciation policy. Its assets are now Unclassified in the Asset Register.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="substantive"))


@engagements_bp.route("/<int:engagement_id>/ppe/assets/add", methods=["POST"])
@login_required
def add_ppe_asset(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for the asset.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    asset_class_id = request.form.get("asset_class_id") or None
    order = PPEAsset.query.filter_by(engagement_id=engagement_id).count()
    db.session.add(PPEAsset(
        engagement_id=engagement_id,
        asset_class_id=int(asset_class_id) if asset_class_id else None,
        asset_code=request.form.get("asset_code", "").strip() or None,
        description=description,
        date_acquired=_parse_date(request.form.get("date_acquired")),
        cost=_parse_float(request.form.get("cost")) or 0.0,
        opening_accumulated_depreciation=_parse_float(request.form.get("opening_accumulated_depreciation")) or 0.0,
        current_year_depreciation=_parse_float(request.form.get("current_year_depreciation")) or 0.0,
        disposal_cost=_parse_float(request.form.get("disposal_cost")) or 0.0,
        disposal_accumulated_depreciation=_parse_float(request.form.get("disposal_accumulated_depreciation")) or 0.0,
        order=order,
    ))
    db.session.commit()
    flash("Asset added to the Asset Register.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/ppe/assets/<int:asset_id>/update", methods=["POST"])
@login_required
def update_ppe_asset(asset_id):
    asset = PPEAsset.query.get_or_404(asset_id)
    engagement = asset.engagement
    _ensure_engagement_access(engagement)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please enter a description for the asset.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="substantive"))
    asset_class_id = request.form.get("asset_class_id") or None
    asset.asset_class_id = int(asset_class_id) if asset_class_id else None
    asset.asset_code = request.form.get("asset_code", "").strip() or None
    asset.description = description
    asset.date_acquired = _parse_date(request.form.get("date_acquired"))
    asset.cost = _parse_float(request.form.get("cost")) or 0.0
    asset.opening_accumulated_depreciation = _parse_float(request.form.get("opening_accumulated_depreciation")) or 0.0
    asset.current_year_depreciation = _parse_float(request.form.get("current_year_depreciation")) or 0.0
    asset.disposal_cost = _parse_float(request.form.get("disposal_cost")) or 0.0
    asset.disposal_accumulated_depreciation = _parse_float(request.form.get("disposal_accumulated_depreciation")) or 0.0
    db.session.commit()
    flash("Asset updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="substantive"))


@engagements_bp.route("/ppe/assets/<int:asset_id>/delete", methods=["POST"])
@login_required
def delete_ppe_asset(asset_id):
    asset = PPEAsset.query.get_or_404(asset_id)
    engagement = asset.engagement
    _ensure_engagement_access(engagement)
    db.session.delete(asset)
    db.session.commit()
    flash("Asset removed from the Asset Register.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="substantive"))


@engagements_bp.route("/<int:engagement_id>/workpapers/ppe-asset-register/generate", methods=["POST"])
@login_required
def generate_ppe_asset_register_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    classes, assets, _, _ = _ppe_plain_data(engagement_id)
    if not assets:
        flash("Add at least one asset to the Asset Register before generating this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    buf = wp.build_ppe_asset_register_xlsx(engagement, classes, assets)
    doc = _file_generated_workpaper(engagement, "ppe_asset_register", "Property, Plant and Equipment", "N3300", "PPE_Asset_Register", "xlsx", buf)
    flash(f"PPE Asset Register filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


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
    procedure_kind = request.form.get("procedure_kind", "").strip() or None
    if procedure_kind not in ("ITGC", "Substantive", None):
        procedure_kind = None
    db.session.add(SubstantiveProcedureItem(area_id=area.id, procedure_text=text, source="manual", order=len(area.items), procedure_kind=procedure_kind))
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
    if "trigger_event" in request.form:
        item.trigger_event = request.form.get("trigger_event", "").strip() or None
    if "responsible_role" in request.form:
        item.responsible_role = request.form.get("responsible_role", "").strip() or None
    if "target_output" in request.form:
        item.target_output = request.form.get("target_output", "").strip() or None
    if "procedure_kind" in request.form:
        procedure_kind = request.form.get("procedure_kind", "").strip() or None
        item.procedure_kind = procedure_kind if procedure_kind in ("ITGC", "Substantive") else None
    _touch_substantive_area(area)
    db.session.commit()
    flash("Procedure updated." if area.engagement.type != "Secretarial" else "Task updated.", "success")
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
        valid_areas = _substantive_areas_for(engagement)
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


# ---------- Practice Dashboard (firm-wide analytics, built from data already captured - no new schema) ----------

def _engagement_quality_components(engagement):
    """Every sign-off-bearing record that currently exists for this
    engagement - whichever one-to-one workpapers have been started, plus
    each Substantive Procedures area - used to compute a lightweight
    Engagement Quality Index (see engagement_quality_index below). Nothing
    here is specific to one engagement type: it just walks whatever
    already exists, so it works unchanged for Audit, Assurance,
    Investigative, Business Intelligence and IT, Secretarial or Consulting
    engagements alike, and for any future engagement type added the same
    way (a new type simply means new records showing up in this same
    walk, not a new branch here)."""
    single_attrs = [
        "client_acceptance", "risk_assessment", "materiality", "entity_understanding",
        "analytical_review", "audit_strategy", "trial_balance", "financial_statements",
        "directors_statement", "audit_opinion", "income_tax_computation",
        "deferred_tax_computation", "finalisation_checklist",
    ]
    records = [getattr(engagement, attr) for attr in single_attrs if getattr(engagement, attr, None) is not None]
    records.extend(engagement.substantive_procedure_areas)
    return records


def engagement_quality_index(engagement):
    """A percentage: of every sign-off-bearing record that currently exists
    for this engagement, how many are at least effectively reviewed (see
    models.effectively_reviewed - a Partner's own preparation counts as
    reviewed), and separately how many are partner-signed. Returns None
    until at least one such record exists, so an engagement nobody has
    started yet shows as "not started" rather than a misleading 0%. A
    practical, always-current progress signal only - it is not a
    substitute for the firm's own quality-control judgement on an
    engagement, and it deliberately does not try to weight or score
    different sections differently (see the delivery notes for why)."""
    records = _engagement_quality_components(engagement)
    if not records:
        return None
    reviewed = sum(1 for r in records if effectively_reviewed(r))
    partner_signed = sum(1 for r in records if getattr(r, "is_partner_signed", False))
    return {
        "total": len(records),
        "reviewed": reviewed,
        "partner_signed": partner_signed,
        "reviewed_pct": round(100 * reviewed / len(records)),
        "partner_signed_pct": round(100 * partner_signed / len(records)),
    }


@engagements_bp.route("/analytics")
@login_required
def practice_dashboard():
    """Retained as a URL alias for the merged Practice Dashboard (see
    `dashboard()` above) - Dashboard and Practice Dashboard used to be two
    separate pages/nav links; they're now one page at /engagements/dashboard,
    and this old URL just renders the same thing, for anyone with it
    bookmarked."""
    return dashboard()


# ---------- Working papers: Word/Excel generation (Finalisation + Substantive Procedures tabs) ----------

@engagements_bp.route("/<int:engagement_id>/workpapers/financial-statements/generate", methods=["POST"])
@login_required
def generate_financial_statements_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the trial balance (Trial Balance tab) before generating the financial statements working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    _, _, ppe_class_dicts, ppe_asset_dicts = _ppe_plain_data(engagement_id)
    ppe_movement, ppe_policy_text = _ppe_movement_and_policy(ppe_class_dicts, ppe_asset_dicts, engagement.period_end)
    statements = fin.build_all_statements(
        trial_balance.lines, trial_balance.adjustments,
        reporting_framework=engagement.reporting_framework, cash_flow_method=engagement.cash_flow_method,
        ppe_movement=ppe_movement, ppe_policy_text=ppe_policy_text,
    )
    financial_statements = FinancialStatements.query.filter_by(engagement_id=engagement_id).first()
    directors_statement = DirectorsStatement.query.filter_by(engagement_id=engagement_id).first()
    audit_opinion = AuditOpinion.query.filter_by(engagement_id=engagement_id).first()
    client_key_people = [
        {"full_name": p.full_name, "role": p.role}
        for p in ClientKeyPerson.query.filter_by(client_id=engagement.client_id, status="Confirmed").order_by(ClientKeyPerson.id).all()
    ]
    buf = wp.build_financial_statements_docx(
        engagement, statements, financial_statements,
        directors_statement=directors_statement, audit_opinion=audit_opinion, client_key_people=client_key_people,
    )
    doc = _file_generated_workpaper(engagement, "financial_statements", "Financial Statements", "N8100", "Financial_Statements", "docx", buf)
    flash(f"Financial statements filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/trial-balance/generate", methods=["POST"])
@login_required
def generate_trial_balance_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Enter or import the trial balance (Trial Balance tab) before generating this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    buf = wp.build_trial_balance_adjustments_xlsx(engagement, trial_balance)
    doc = _file_generated_workpaper(engagement, "trial_balance_adjustments", "Trial Balance & Adjustments", "N1000", "Trial_Balance_and_Adjustments", "xlsx", buf)
    flash(f"Trial balance & adjustments filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/rep-letter/generate", methods=["POST"])
@login_required
def generate_rep_letter_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    statements = (
        fin.build_all_statements(trial_balance.lines, trial_balance.adjustments)
        if trial_balance and trial_balance.lines else None
    )
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="rep_letter").first()
    buf = wp.build_rep_letter_docx(engagement, statements, narrative)
    doc = _file_generated_workpaper(engagement, "rep_letter", "Management Representation Letter", "N9006", "Management_Representation_Letter", "docx", buf)
    flash(f"Management representation letter filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/report-to-management/generate", methods=["POST"])
@login_required
def generate_report_to_management_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="report_to_management").first()
    buf = wp.build_report_to_management_docx(engagement, narrative)
    doc = _file_generated_workpaper(engagement, "report_to_management", "Report to Management", "N8300", "Report_to_Management", "docx", buf)
    flash(f"Report to management filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/forensic-report/generate", methods=["POST"])
@login_required
def generate_forensic_report_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if engagement.type != "Investigative Engagement":
        flash("The forensic investigation report is only available on Investigative Engagements.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="forensic_executive_summary").first()
    buf = wp.build_forensic_report_docx(engagement, narrative)
    doc = _file_generated_workpaper(engagement, "forensic_report", "Forensic Investigation Report", None, "Forensic_Investigation_Report", "docx", buf, reference_override=filing_reference("forensic_report"))
    flash(f"Forensic investigation report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/it-audit-report/generate", methods=["POST"])
@login_required
def generate_it_audit_report_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if engagement.type != "Business Intelligence and IT Engagements":
        flash("The IT & Cyber Assurance Report is only available on Business Intelligence and IT Engagements.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="it_audit_report_summary").first()
    buf = wp.build_it_audit_report_docx(engagement, narrative)
    doc = _file_generated_workpaper(engagement, "it_audit_report", "IT & Cyber Assurance Report", None, "IT_Cyber_Assurance_Report", "docx", buf, reference_override=filing_reference("it_audit_report"))
    flash(f"IT & Cyber Assurance Report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/tax-opinion/generate", methods=["POST"])
@login_required
def generate_tax_opinion_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if not engagement.has_tax_module:
        flash("The Tax Opinion is only available on an engagement with a Tax service turned on.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))
    narratives_by_kind = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
        if wn.kind.startswith("tax_opinion")
    }
    buf = wp.build_tax_opinion_docx(engagement, narratives_by_kind)
    doc = _file_generated_workpaper(engagement, "tax_opinion", "Tax Opinion", None, "Tax_Opinion", "docx", buf, reference_override=filing_reference("tax_opinion"))
    flash(f"Tax Opinion filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))


@engagements_bp.route("/<int:engagement_id>/workpapers/tax-health-check/generate", methods=["POST"])
@login_required
def generate_tax_health_check_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if not engagement.has_tax_module:
        flash("The Tax Health Check Report is only available on an engagement with a Tax service turned on.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))
    narratives_by_kind = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
        if wn.kind.startswith("tax_health_check")
    }
    buf = wp.build_tax_health_check_docx(engagement, narratives_by_kind)
    doc = _file_generated_workpaper(engagement, "tax_health_check", "Tax Health Check Report", None, "Tax_Health_Check_Report", "docx", buf, reference_override=filing_reference("tax_health_check"))
    flash(f"Tax Health Check Report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))


@engagements_bp.route("/<int:engagement_id>/workpapers/management-accounts-report/generate", methods=["POST"])
@login_required
def generate_management_accounts_report_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if not engagement.has_accounting_module:
        flash("The Management Accounts Report is only available on an engagement with an Accounting service turned on.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="accounting"))
    narratives_by_kind = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
        if wn.kind.startswith("mgmt_report")
    }
    buf = wp.build_management_accounts_report_docx(engagement, narratives_by_kind)
    doc = _file_generated_workpaper(engagement, "management_accounts_report", "Management Accounts Report", None, "Management_Accounts_Report", "docx", buf, reference_override=filing_reference("management_accounts_report"))
    flash(f"Management Accounts Report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="accounting"))


@engagements_bp.route("/<int:engagement_id>/workpapers/file-summary/generate", methods=["POST"])
@login_required
def generate_engagement_file_summary_pdf(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_engagement_file_summary_pdf(engagement)
    doc = _file_generated_workpaper(engagement, "file_summary", "Engagement File Summary", None, "Engagement_File_Summary", "pdf", buf)
    flash(f"Engagement file summary filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/acceptance-checklist/generate", methods=["POST"])
@login_required
def generate_acceptance_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_client_acceptance_checklist_docx(engagement, engagement.client_acceptance)
    doc = _file_generated_workpaper(engagement, "acceptance_checklist", "Client Acceptance Checklist", "N1009", "Client_Acceptance_Checklist", "docx", buf)
    flash(f"Client acceptance checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@engagements_bp.route("/<int:engagement_id>/workpapers/entity-checklist/generate", methods=["POST"])
@login_required
def generate_entity_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_entity_understanding_checklist_docx(engagement, engagement.entity_understanding)
    doc = _file_generated_workpaper(engagement, "entity_checklist", "Understanding the Entity Checklist", "N1003", "Understanding_the_Entity_Checklist", "docx", buf)
    flash(f"Understanding the Entity checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/<int:engagement_id>/workpapers/engagement-checklist/generate", methods=["POST"])
@login_required
def generate_engagement_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_engagement_checklist_docx(engagement)
    doc = _file_generated_workpaper(engagement, "engagement_checklist", "Engagement Checklist", None, "Engagement_Checklist", "docx", buf, reference_override="F-1")
    flash(f"Engagement checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


@engagements_bp.route("/<int:engagement_id>/workpapers/finalisation-checklist/generate", methods=["POST"])
@login_required
def generate_finalisation_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_finalisation_checklist_docx(engagement, engagement.finalisation_checklist)
    doc = _file_generated_workpaper(engagement, "finalisation_checklist", "Finalisation Checklist", "N9009", "Finalisation_Checklist", "docx", buf)
    flash(f"Finalisation checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


def _substantive_programme_context(engagement):
    """Shared setup for the two Substantive Procedures workpaper generators
    below - the same area list/order/reference codes the tab itself shows
    (see view_engagement), so the generated file always matches the screen."""
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic:
        area_order, area_refs = FORENSIC_SUBSTANTIVE_AREAS, FORENSIC_AREA_REFERENCES
    elif is_business_it:
        area_order, area_refs = BUSINESS_IT_SUBSTANTIVE_AREAS, BUSINESS_IT_AREA_REFERENCES
    elif is_secretarial:
        area_order, area_refs = SECRETARIAL_SUBSTANTIVE_AREAS, SECRETARIAL_AREA_REFERENCES
    else:
        area_order, area_refs = AUDIT_AREAS, AUDIT_AREA_REFERENCES
    areas_by_name = {
        a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement.id).all()
    }
    return areas_by_name, area_order, area_refs


def _procedures_programme_label(engagement):
    """The filename label for the generated procedures programme - matches
    the document title chosen in workpapers.build_substantive_procedures_
    docx/xlsx for each engagement type."""
    if engagement.type == "Investigative Engagement":
        return "Investigative_Procedures_Programme"
    if engagement.type == "Business Intelligence and IT Engagements":
        return "IT_Cyber_Assurance_Procedures_Programme"
    if engagement.type == "Secretarial":
        return "Secretarial_Execution_Plan"
    return "Substantive_Procedures_Programme"


@engagements_bp.route("/<int:engagement_id>/workpapers/substantive-procedures-docx/generate", methods=["POST"])
@login_required
def generate_substantive_procedures_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    areas_by_name, area_order, area_refs = _substantive_programme_context(engagement)
    if not areas_by_name:
        flash("Generate suggested procedures (or add some manually) before filing this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    buf = wp.build_substantive_procedures_docx(engagement, areas_by_name, area_order, area_refs)
    label = _procedures_programme_label(engagement)
    doc = _file_generated_workpaper(engagement, "substantive_procedures_docx", "Substantive Procedures Programme", None, label, "docx", buf, reference_override=filing_reference("substantive"))
    flash(f"Procedures programme (Word) filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/<int:engagement_id>/workpapers/substantive-procedures-xlsx/generate", methods=["POST"])
@login_required
def generate_substantive_procedures_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    areas_by_name, area_order, area_refs = _substantive_programme_context(engagement)
    if not areas_by_name:
        flash("Generate suggested procedures (or add some manually) before filing this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    buf = wp.build_substantive_procedures_xlsx(engagement, areas_by_name, area_order, area_refs)
    label = _procedures_programme_label(engagement)
    doc = _file_generated_workpaper(engagement, "substantive_procedures_xlsx", "Substantive Procedures Programme", None, label, "xlsx", buf, reference_override=filing_reference("substantive"))
    flash(f"Procedures programme (Excel) filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
