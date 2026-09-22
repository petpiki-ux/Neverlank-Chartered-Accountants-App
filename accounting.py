"""Financial Accounting, Cost Accounting & Management Accounting module -
the "Accounting" tab shown on any engagement with Engagement.
has_accounting_module True (a dedicated Accounting & Bookkeeping engagement,
or any other engagement type with an Accounting service turned on - see
models.ACCOUNTING_SERVICES).

Everything that already had a natural home elsewhere in this app is reused
rather than duplicated - see the module comment at the top of the
"Financial Accounting, Cost Accounting & Management Accounting module"
section in models.py for the full list (Risk Register -> RiskItem,
Execution Plan -> SubstantiveProcedureArea/Item, the General Ledger rolling
up into the existing Trial Balance/Financial Statements pipeline,
Management Accounts Report -> WorkpaperNarrative, Documents -> the existing
Documents/Filing Index). This file only adds the CRUD routes for the
genuinely new pieces: the Chart of Accounts, journal entries and General
Ledger roll-up, bank reconciliations, cost centres, job/process/standard
costing records, standard cost variances, CVP analysis, budgets, KPI
tracking, and the accounting checklist.
"""
from datetime import datetime, date

from flask import Blueprint, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import (
    Engagement, RiskItem,
    GLAccount, GL_ACCOUNT_TYPES,
    JournalEntry, JournalEntryLine, JOURNAL_ENTRY_SOURCES,
    TrialBalance, TrialBalanceLine,
    BankReconciliation, BankReconciliationItem, BANK_RECONCILIATION_ITEM_TYPES,
    CostCentre, COST_CENTRE_TYPES,
    CostingRecord, COSTING_METHODS,
    StandardCostVariance, VARIANCE_TYPES,
    CVPAnalysis,
    Budget, BudgetLine, BUDGET_TYPES,
    KPIMetric, KPI_CATEGORIES,
    AccountingChecklistItem, DEFAULT_ACCOUNTING_CHECKLIST_ITEMS,
    PARTNER_SIGNOFF_ROLES, REVIEWER_ROLES,
)
from engagements import _ensure_engagement_access

accounting_bp = Blueprint("accounting", __name__, url_prefix="/accounting")


def _accounting_redirect(engagement_id):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="accounting"))


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _parse_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _get_accounting_engagement(engagement_id):
    """Fetch + access-check an engagement for an Accounting route, same
    "no-op flash and bounce back rather than a hard 404/403" convention as
    tax._get_tax_engagement."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    return engagement


def _require_accounting_module(engagement):
    if not engagement.has_accounting_module:
        flash("Turn on an Accounting service for this engagement (Edit Engagement) before using the Accounting tab.", "danger")
        return False
    return True


def _require_cost_accounting(engagement):
    if not engagement.has_cost_accounting:
        flash("The Cost Accounting service isn't on for this engagement.", "danger")
        return False
    return True


def _require_management_accounting(engagement):
    if not engagement.has_management_accounting:
        flash("The Management Accounting service isn't on for this engagement.", "danger")
        return False
    return True


# ---------- Chart of Accounts ----------

@accounting_bp.route("/<int:engagement_id>/gl-accounts/add", methods=["POST"])
@login_required
def add_gl_account(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_accounting_module(engagement):
        return _accounting_redirect(engagement_id)
    code = request.form.get("code", "").strip()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter an account name.", "danger")
        return _accounting_redirect(engagement_id)
    max_order = db.session.query(db.func.max(GLAccount.order)).filter_by(engagement_id=engagement_id).scalar() or 0
    account = GLAccount(
        engagement_id=engagement_id,
        code=code,
        name=name,
        account_type=request.form.get("account_type", "Expense"),
        opening_balance=_parse_float(request.form.get("opening_balance")) or 0.0,
        order=max_order + 1,
        created_by_id=current_user.id,
    )
    db.session.add(account)
    db.session.commit()
    flash("Account added to the Chart of Accounts.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/gl-accounts/<int:account_id>/update", methods=["POST"])
@login_required
def update_gl_account(account_id):
    account = GLAccount.query.get_or_404(account_id)
    _ensure_engagement_access(account.engagement)
    account.code = request.form.get("code", account.code)
    account.name = request.form.get("name", "").strip() or account.name
    account.account_type = request.form.get("account_type", account.account_type)
    opening = _parse_float(request.form.get("opening_balance"))
    if opening is not None:
        account.opening_balance = opening
    account.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    flash("Account updated.", "success")
    return _accounting_redirect(account.engagement_id)


@accounting_bp.route("/gl-accounts/<int:account_id>/delete", methods=["POST"])
@login_required
def delete_gl_account(account_id):
    account = GLAccount.query.get_or_404(account_id)
    _ensure_engagement_access(account.engagement)
    engagement_id = account.engagement_id
    if JournalEntryLine.query.filter_by(gl_account_id=account.id).first():
        flash("This account has journal entry lines posted to it and can't be deleted - deactivate it instead.", "danger")
        return _accounting_redirect(engagement_id)
    db.session.delete(account)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- Journal Entries (General Ledger) ----------

@accounting_bp.route("/<int:engagement_id>/journal-entries/add", methods=["POST"])
@login_required
def add_journal_entry(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_accounting_module(engagement):
        return _accounting_redirect(engagement_id)
    entry = JournalEntry(
        engagement_id=engagement_id,
        entry_date=_parse_date(request.form.get("entry_date")) or date.today(),
        reference=request.form.get("reference", "").strip(),
        narration=request.form.get("narration", "").strip(),
        source=request.form.get("source", "Manual"),
    )
    db.session.add(entry)
    db.session.commit()
    flash("Journal entry created - now add its debit/credit lines.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/journal-entries/<int:entry_id>/update", methods=["POST"])
@login_required
def update_journal_entry(entry_id):
    entry = JournalEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    entry_date = _parse_date(request.form.get("entry_date"))
    if entry_date:
        entry.entry_date = entry_date
    entry.reference = request.form.get("reference", "").strip()
    entry.narration = request.form.get("narration", "").strip()
    entry.source = request.form.get("source", entry.source)
    db.session.commit()
    flash("Journal entry updated.", "success")
    return _accounting_redirect(entry.engagement_id)


@accounting_bp.route("/journal-entries/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_journal_entry(entry_id):
    entry = JournalEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    engagement_id = entry.engagement_id
    db.session.delete(entry)
    db.session.commit()
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/journal-entries/<int:entry_id>/lines/add", methods=["POST"])
@login_required
def add_journal_entry_line(entry_id):
    entry = JournalEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    gl_account_id = request.form.get("gl_account_id")
    if not gl_account_id:
        flash("Please select an account for this line.", "danger")
        return _accounting_redirect(entry.engagement_id)
    db.session.add(JournalEntryLine(
        journal_entry_id=entry.id,
        gl_account_id=int(gl_account_id),
        description=request.form.get("description", "").strip(),
        debit=_parse_float(request.form.get("debit")) or 0.0,
        credit=_parse_float(request.form.get("credit")) or 0.0,
    ))
    # Editing the lines of an already-posted entry invalidates its
    # sign-off, same "changed workpaper needs fresh review" convention as
    # every other working paper in this app.
    entry.completed_by_id = None
    entry.completed_at = None
    entry.reviewed_by_id = None
    entry.reviewed_at = None
    entry.partner_signed_by_id = None
    entry.partner_signed_at = None
    db.session.commit()
    flash("Journal entry line added.", "success")
    return _accounting_redirect(entry.engagement_id)


@accounting_bp.route("/journal-entry-lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_journal_entry_line(line_id):
    line = JournalEntryLine.query.get_or_404(line_id)
    entry = line.journal_entry
    _ensure_engagement_access(entry.engagement)
    engagement_id = entry.engagement_id
    db.session.delete(line)
    entry.completed_by_id = None
    entry.completed_at = None
    entry.reviewed_by_id = None
    entry.reviewed_at = None
    entry.partner_signed_by_id = None
    entry.partner_signed_at = None
    db.session.commit()
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/journal-entries/<int:entry_id>/post", methods=["POST"])
@login_required
def post_journal_entry(entry_id):
    entry = JournalEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    if not entry.is_balanced:
        flash("This journal entry doesn't balance yet (total debits must equal total credits) - it can't be posted.", "danger")
        return _accounting_redirect(entry.engagement_id)
    entry.completed_by_id = current_user.id
    entry.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Journal entry posted.", "success")
    return _accounting_redirect(entry.engagement_id)


@accounting_bp.route("/journal-entries/<int:entry_id>/review", methods=["POST"])
@login_required
def review_journal_entry(entry_id):
    entry = JournalEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not entry.completed_by_id:
        flash("This journal entry hasn't been posted yet.", "danger")
        return _accounting_redirect(entry.engagement_id)
    if entry.completed_by_id == current_user.id:
        flash("You can't review a journal entry you posted yourself.", "danger")
        return _accounting_redirect(entry.engagement_id)
    entry.reviewed_by_id = current_user.id
    entry.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Journal entry marked as reviewed.", "success")
    return _accounting_redirect(entry.engagement_id)


@accounting_bp.route("/journal-entries/<int:entry_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_journal_entry(entry_id):
    entry = JournalEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if entry.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a journal entry you posted yourself.", "danger")
        return _accounting_redirect(entry.engagement_id)
    entry.partner_signed_by_id = current_user.id
    entry.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _accounting_redirect(entry.engagement_id)


# ---------- General Ledger roll-up to Trial Balance ----------
# The key reuse point of this module: once journal entries are posted, this
# rolls every GLAccount's net balance into the existing TrialBalance/
# TrialBalanceLine models (see the module comment above in models.py) so
# the existing Trial Balance tab's mapping UI and the existing
# financials.py statement-generation pipeline take over unchanged from
# there. Safe to run repeatedly (a "refresh") - existing fs_category
# mappings are preserved across a refresh, keyed by account code, so
# re-running it after posting more journals doesn't lose prior mapping work.

@accounting_bp.route("/<int:engagement_id>/gl/roll-up-to-tb", methods=["POST"])
@login_required
def roll_up_to_trial_balance(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_accounting_module(engagement):
        return _accounting_redirect(engagement_id)

    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not trial_balance:
        trial_balance = TrialBalance(engagement_id=engagement_id, source="gl_rollup")
        db.session.add(trial_balance)
        db.session.flush()

    # Preserve any existing fs_category mapping, keyed by account code, so a
    # re-run after posting more journals doesn't force re-mapping from
    # scratch.
    existing_categories = {l.account_code: l.fs_category for l in trial_balance.lines if l.account_code}
    for line in list(trial_balance.lines):
        db.session.delete(line)
    db.session.flush()

    accounts = GLAccount.query.filter_by(engagement_id=engagement_id, is_active=True).order_by(GLAccount.order, GLAccount.code).all()
    lines_added = 0
    for account in accounts:
        total_debit = sum(l.debit or 0 for je in account.engagement.journal_entries for l in je.lines if l.gl_account_id == account.id)
        total_credit = sum(l.credit or 0 for je in account.engagement.journal_entries for l in je.lines if l.gl_account_id == account.id)
        if account.normal_balance == "Debit":
            total_debit += account.opening_balance or 0
        else:
            total_credit += account.opening_balance or 0
        net = total_debit - total_credit
        if abs(net) < 0.005 and not (account.opening_balance or 0):
            continue  # no activity and no opening balance - skip a blank account
        db.session.add(TrialBalanceLine(
            trial_balance_id=trial_balance.id,
            account_code=account.code,
            account_name=account.name,
            fs_category=existing_categories.get(account.code),
            current_debit=net if net >= 0 else 0.0,
            current_credit=-net if net < 0 else 0.0,
        ))
        lines_added += 1
    db.session.commit()
    flash(f"General Ledger rolled up to the Trial Balance ({lines_added} account(s)). Map any unmapped lines on the Trial Balance tab.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


# ---------- Bank Reconciliations ----------

@accounting_bp.route("/<int:engagement_id>/bank-reconciliations/add", methods=["POST"])
@login_required
def add_bank_reconciliation(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_accounting_module(engagement):
        return _accounting_redirect(engagement_id)
    gl_account_id = request.form.get("gl_account_id") or None
    recon = BankReconciliation(
        engagement_id=engagement_id,
        gl_account_id=int(gl_account_id) if gl_account_id else None,
        statement_date=_parse_date(request.form.get("statement_date")) or date.today(),
        bank_statement_balance=_parse_float(request.form.get("bank_statement_balance")) or 0.0,
        book_balance=_parse_float(request.form.get("book_balance")) or 0.0,
        notes=request.form.get("notes", "").strip(),
        completed_by_id=current_user.id,
        completed_at=datetime.utcnow(),
    )
    db.session.add(recon)
    db.session.commit()
    flash("Bank reconciliation started - add reconciling items below.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/bank-reconciliations/<int:recon_id>/update", methods=["POST"])
@login_required
def update_bank_reconciliation(recon_id):
    recon = BankReconciliation.query.get_or_404(recon_id)
    _ensure_engagement_access(recon.engagement)
    statement_date = _parse_date(request.form.get("statement_date"))
    if statement_date:
        recon.statement_date = statement_date
    recon.bank_statement_balance = _parse_float(request.form.get("bank_statement_balance")) or 0.0
    recon.book_balance = _parse_float(request.form.get("book_balance")) or 0.0
    recon.notes = request.form.get("notes", "").strip()
    recon.completed_by_id = current_user.id
    recon.completed_at = datetime.utcnow()
    recon.reviewed_by_id = None
    recon.reviewed_at = None
    recon.partner_signed_by_id = None
    recon.partner_signed_at = None
    db.session.commit()
    flash("Bank reconciliation updated.", "success")
    return _accounting_redirect(recon.engagement_id)


@accounting_bp.route("/bank-reconciliations/<int:recon_id>/review", methods=["POST"])
@login_required
def review_bank_reconciliation(recon_id):
    recon = BankReconciliation.query.get_or_404(recon_id)
    _ensure_engagement_access(recon.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if recon.completed_by_id == current_user.id:
        flash("You can't review a bank reconciliation you prepared yourself.", "danger")
        return _accounting_redirect(recon.engagement_id)
    recon.reviewed_by_id = current_user.id
    recon.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Bank reconciliation marked as reviewed.", "success")
    return _accounting_redirect(recon.engagement_id)


@accounting_bp.route("/bank-reconciliations/<int:recon_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_bank_reconciliation(recon_id):
    recon = BankReconciliation.query.get_or_404(recon_id)
    _ensure_engagement_access(recon.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if recon.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a bank reconciliation you prepared yourself.", "danger")
        return _accounting_redirect(recon.engagement_id)
    recon.partner_signed_by_id = current_user.id
    recon.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _accounting_redirect(recon.engagement_id)


@accounting_bp.route("/bank-reconciliations/<int:recon_id>/delete", methods=["POST"])
@login_required
def delete_bank_reconciliation(recon_id):
    recon = BankReconciliation.query.get_or_404(recon_id)
    _ensure_engagement_access(recon.engagement)
    engagement_id = recon.engagement_id
    db.session.delete(recon)
    db.session.commit()
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/bank-reconciliations/<int:recon_id>/items/add", methods=["POST"])
@login_required
def add_bank_reconciliation_item(recon_id):
    recon = BankReconciliation.query.get_or_404(recon_id)
    _ensure_engagement_access(recon.engagement)
    db.session.add(BankReconciliationItem(
        reconciliation_id=recon.id,
        side=request.form.get("side", "bank"),
        item_type=request.form.get("item_type", "Other"),
        description=request.form.get("description", "").strip(),
        item_date=_parse_date(request.form.get("item_date")),
        amount=_parse_float(request.form.get("amount")) or 0.0,
    ))
    db.session.commit()
    flash("Reconciling item added.", "success")
    return _accounting_redirect(recon.engagement_id)


@accounting_bp.route("/bank-reconciliation-items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_bank_reconciliation_item(item_id):
    item = BankReconciliationItem.query.get_or_404(item_id)
    recon = item.reconciliation
    _ensure_engagement_access(recon.engagement)
    engagement_id = recon.engagement_id
    db.session.delete(item)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- Cost Centres ----------

@accounting_bp.route("/<int:engagement_id>/cost-centres/add", methods=["POST"])
@login_required
def add_cost_centre(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_cost_accounting(engagement):
        return _accounting_redirect(engagement_id)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter a cost centre name.", "danger")
        return _accounting_redirect(engagement_id)
    db.session.add(CostCentre(
        engagement_id=engagement_id,
        code=request.form.get("code", "").strip(),
        name=name,
        centre_type=request.form.get("centre_type", "Production"),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Cost centre added.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/cost-centres/<int:centre_id>/update", methods=["POST"])
@login_required
def update_cost_centre(centre_id):
    centre = CostCentre.query.get_or_404(centre_id)
    _ensure_engagement_access(centre.engagement)
    centre.code = request.form.get("code", centre.code)
    centre.name = request.form.get("name", "").strip() or centre.name
    centre.centre_type = request.form.get("centre_type", centre.centre_type)
    centre.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Cost centre updated.", "success")
    return _accounting_redirect(centre.engagement_id)


@accounting_bp.route("/cost-centres/<int:centre_id>/delete", methods=["POST"])
@login_required
def delete_cost_centre(centre_id):
    centre = CostCentre.query.get_or_404(centre_id)
    _ensure_engagement_access(centre.engagement)
    engagement_id = centre.engagement_id
    db.session.delete(centre)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- Job/Process/Standard Costing records ----------

@accounting_bp.route("/<int:engagement_id>/costing-records/add", methods=["POST"])
@login_required
def add_costing_record(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_cost_accounting(engagement):
        return _accounting_redirect(engagement_id)
    reference = request.form.get("reference", "").strip()
    if not reference:
        flash("Please give this cost card a job/batch/process reference.", "danger")
        return _accounting_redirect(engagement_id)
    cost_centre_id = request.form.get("cost_centre_id") or None
    record = CostingRecord(
        engagement_id=engagement_id,
        costing_method=request.form.get("costing_method", "Job Order Costing"),
        reference=reference,
        cost_centre_id=int(cost_centre_id) if cost_centre_id else None,
        units_produced=_parse_float(request.form.get("units_produced")) or 1.0,
        direct_material_cost=_parse_float(request.form.get("direct_material_cost")) or 0.0,
        direct_labour_cost=_parse_float(request.form.get("direct_labour_cost")) or 0.0,
        variable_overhead_cost=_parse_float(request.form.get("variable_overhead_cost")) or 0.0,
        fixed_overhead_cost=_parse_float(request.form.get("fixed_overhead_cost")) or 0.0,
        notes=request.form.get("notes", "").strip(),
        completed_by_id=current_user.id,
        completed_at=datetime.utcnow(),
    )
    db.session.add(record)
    db.session.commit()
    flash("Cost card added.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/costing-records/<int:record_id>/update", methods=["POST"])
@login_required
def update_costing_record(record_id):
    record = CostingRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    record.costing_method = request.form.get("costing_method", record.costing_method)
    record.reference = request.form.get("reference", "").strip() or record.reference
    cost_centre_id = request.form.get("cost_centre_id") or None
    record.cost_centre_id = int(cost_centre_id) if cost_centre_id else None
    record.units_produced = _parse_float(request.form.get("units_produced")) or record.units_produced
    record.direct_material_cost = _parse_float(request.form.get("direct_material_cost")) or 0.0
    record.direct_labour_cost = _parse_float(request.form.get("direct_labour_cost")) or 0.0
    record.variable_overhead_cost = _parse_float(request.form.get("variable_overhead_cost")) or 0.0
    record.fixed_overhead_cost = _parse_float(request.form.get("fixed_overhead_cost")) or 0.0
    record.notes = request.form.get("notes", "").strip()
    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Cost card updated.", "success")
    return _accounting_redirect(record.engagement_id)


@accounting_bp.route("/costing-records/<int:record_id>/review", methods=["POST"])
@login_required
def review_costing_record(record_id):
    record = CostingRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't review a cost card you prepared yourself.", "danger")
        return _accounting_redirect(record.engagement_id)
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Cost card marked as reviewed.", "success")
    return _accounting_redirect(record.engagement_id)


@accounting_bp.route("/costing-records/<int:record_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_costing_record(record_id):
    record = CostingRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a cost card you prepared yourself.", "danger")
        return _accounting_redirect(record.engagement_id)
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _accounting_redirect(record.engagement_id)


@accounting_bp.route("/costing-records/<int:record_id>/delete", methods=["POST"])
@login_required
def delete_costing_record(record_id):
    record = CostingRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    engagement_id = record.engagement_id
    db.session.delete(record)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- Standard Cost Variances ----------

@accounting_bp.route("/<int:engagement_id>/variances/add", methods=["POST"])
@login_required
def add_standard_cost_variance(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_cost_accounting(engagement):
        return _accounting_redirect(engagement_id)
    costing_record_id = request.form.get("costing_record_id") or None
    db.session.add(StandardCostVariance(
        engagement_id=engagement_id,
        costing_record_id=int(costing_record_id) if costing_record_id else None,
        variance_type=request.form.get("variance_type", VARIANCE_TYPES[0]),
        standard_amount=_parse_float(request.form.get("standard_amount")) or 0.0,
        actual_amount=_parse_float(request.form.get("actual_amount")) or 0.0,
        explanation=request.form.get("explanation", "").strip(),
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Variance recorded.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/variances/<int:variance_id>/update", methods=["POST"])
@login_required
def update_standard_cost_variance(variance_id):
    variance = StandardCostVariance.query.get_or_404(variance_id)
    _ensure_engagement_access(variance.engagement)
    variance.variance_type = request.form.get("variance_type", variance.variance_type)
    variance.standard_amount = _parse_float(request.form.get("standard_amount")) or 0.0
    variance.actual_amount = _parse_float(request.form.get("actual_amount")) or 0.0
    variance.explanation = request.form.get("explanation", "").strip()
    db.session.commit()
    flash("Variance updated.", "success")
    return _accounting_redirect(variance.engagement_id)


@accounting_bp.route("/variances/<int:variance_id>/delete", methods=["POST"])
@login_required
def delete_standard_cost_variance(variance_id):
    variance = StandardCostVariance.query.get_or_404(variance_id)
    _ensure_engagement_access(variance.engagement)
    engagement_id = variance.engagement_id
    db.session.delete(variance)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- CVP (break-even) Analysis ----------

@accounting_bp.route("/<int:engagement_id>/cvp/add", methods=["POST"])
@login_required
def add_cvp_analysis(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_cost_accounting(engagement):
        return _accounting_redirect(engagement_id)
    segment_name = request.form.get("segment_name", "").strip()
    if not segment_name:
        flash("Please name the product/segment this analysis is for.", "danger")
        return _accounting_redirect(engagement_id)
    db.session.add(CVPAnalysis(
        engagement_id=engagement_id,
        segment_name=segment_name,
        selling_price_per_unit=_parse_float(request.form.get("selling_price_per_unit")) or 0.0,
        variable_cost_per_unit=_parse_float(request.form.get("variable_cost_per_unit")) or 0.0,
        fixed_costs=_parse_float(request.form.get("fixed_costs")) or 0.0,
        target_profit=_parse_float(request.form.get("target_profit")),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("CVP analysis added.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/cvp/<int:analysis_id>/update", methods=["POST"])
@login_required
def update_cvp_analysis(analysis_id):
    analysis = CVPAnalysis.query.get_or_404(analysis_id)
    _ensure_engagement_access(analysis.engagement)
    analysis.segment_name = request.form.get("segment_name", "").strip() or analysis.segment_name
    analysis.selling_price_per_unit = _parse_float(request.form.get("selling_price_per_unit")) or 0.0
    analysis.variable_cost_per_unit = _parse_float(request.form.get("variable_cost_per_unit")) or 0.0
    analysis.fixed_costs = _parse_float(request.form.get("fixed_costs")) or 0.0
    analysis.target_profit = _parse_float(request.form.get("target_profit"))
    analysis.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("CVP analysis updated.", "success")
    return _accounting_redirect(analysis.engagement_id)


@accounting_bp.route("/cvp/<int:analysis_id>/delete", methods=["POST"])
@login_required
def delete_cvp_analysis(analysis_id):
    analysis = CVPAnalysis.query.get_or_404(analysis_id)
    _ensure_engagement_access(analysis.engagement)
    engagement_id = analysis.engagement_id
    db.session.delete(analysis)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- Budgets (Management Accounting) ----------

@accounting_bp.route("/<int:engagement_id>/budgets/add", methods=["POST"])
@login_required
def add_budget(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_management_accounting(engagement):
        return _accounting_redirect(engagement_id)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please name this budget.", "danger")
        return _accounting_redirect(engagement_id)
    budget = Budget(
        engagement_id=engagement_id,
        name=name,
        budget_type=request.form.get("budget_type", "Operating"),
        period_start=_parse_date(request.form.get("period_start")),
        period_end=_parse_date(request.form.get("period_end")),
        notes=request.form.get("notes", "").strip(),
        completed_by_id=current_user.id,
        completed_at=datetime.utcnow(),
    )
    db.session.add(budget)
    db.session.commit()
    flash("Budget created - now add its line items.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/budgets/<int:budget_id>/update", methods=["POST"])
@login_required
def update_budget(budget_id):
    budget = Budget.query.get_or_404(budget_id)
    _ensure_engagement_access(budget.engagement)
    budget.name = request.form.get("name", "").strip() or budget.name
    budget.budget_type = request.form.get("budget_type", budget.budget_type)
    budget.period_start = _parse_date(request.form.get("period_start"))
    budget.period_end = _parse_date(request.form.get("period_end"))
    budget.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Budget updated.", "success")
    return _accounting_redirect(budget.engagement_id)


@accounting_bp.route("/budgets/<int:budget_id>/review", methods=["POST"])
@login_required
def review_budget(budget_id):
    budget = Budget.query.get_or_404(budget_id)
    _ensure_engagement_access(budget.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if budget.completed_by_id == current_user.id:
        flash("You can't review a budget you prepared yourself.", "danger")
        return _accounting_redirect(budget.engagement_id)
    budget.reviewed_by_id = current_user.id
    budget.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Budget marked as reviewed.", "success")
    return _accounting_redirect(budget.engagement_id)


@accounting_bp.route("/budgets/<int:budget_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_budget(budget_id):
    budget = Budget.query.get_or_404(budget_id)
    _ensure_engagement_access(budget.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if budget.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a budget you prepared yourself.", "danger")
        return _accounting_redirect(budget.engagement_id)
    budget.partner_signed_by_id = current_user.id
    budget.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return _accounting_redirect(budget.engagement_id)


@accounting_bp.route("/budgets/<int:budget_id>/delete", methods=["POST"])
@login_required
def delete_budget(budget_id):
    budget = Budget.query.get_or_404(budget_id)
    _ensure_engagement_access(budget.engagement)
    engagement_id = budget.engagement_id
    db.session.delete(budget)
    db.session.commit()
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/budgets/<int:budget_id>/lines/add", methods=["POST"])
@login_required
def add_budget_line(budget_id):
    budget = Budget.query.get_or_404(budget_id)
    _ensure_engagement_access(budget.engagement)
    category = request.form.get("category", "").strip()
    if not category:
        flash("Please enter a category for this budget line.", "danger")
        return _accounting_redirect(budget.engagement_id)
    db.session.add(BudgetLine(
        budget_id=budget.id,
        category=category,
        budgeted_amount=_parse_float(request.form.get("budgeted_amount")) or 0.0,
        actual_amount=_parse_float(request.form.get("actual_amount")),
        notes=request.form.get("notes", "").strip(),
    ))
    db.session.commit()
    flash("Budget line added.", "success")
    return _accounting_redirect(budget.engagement_id)


@accounting_bp.route("/budget-lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_budget_line(line_id):
    line = BudgetLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.budget.engagement)
    line.category = request.form.get("category", "").strip() or line.category
    line.budgeted_amount = _parse_float(request.form.get("budgeted_amount")) or 0.0
    line.actual_amount = _parse_float(request.form.get("actual_amount"))
    line.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Budget line updated.", "success")
    return _accounting_redirect(line.budget.engagement_id)


@accounting_bp.route("/budget-lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_budget_line(line_id):
    line = BudgetLine.query.get_or_404(line_id)
    budget = line.budget
    _ensure_engagement_access(budget.engagement)
    engagement_id = budget.engagement_id
    db.session.delete(line)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- KPI Dashboard (Management Accounting) ----------

@accounting_bp.route("/<int:engagement_id>/kpis/add", methods=["POST"])
@login_required
def add_kpi_metric(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_management_accounting(engagement):
        return _accounting_redirect(engagement_id)
    metric_name = request.form.get("metric_name", "").strip()
    if not metric_name:
        flash("Please name this KPI.", "danger")
        return _accounting_redirect(engagement_id)
    db.session.add(KPIMetric(
        engagement_id=engagement_id,
        metric_name=metric_name,
        category=request.form.get("category", "Other"),
        period_label=request.form.get("period_label", "").strip(),
        target_value=_parse_float(request.form.get("target_value")),
        actual_value=_parse_float(request.form.get("actual_value")),
        unit=request.form.get("unit", "").strip(),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("KPI added to the dashboard.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/kpis/<int:kpi_id>/update", methods=["POST"])
@login_required
def update_kpi_metric(kpi_id):
    kpi = KPIMetric.query.get_or_404(kpi_id)
    _ensure_engagement_access(kpi.engagement)
    kpi.metric_name = request.form.get("metric_name", "").strip() or kpi.metric_name
    kpi.category = request.form.get("category", kpi.category)
    kpi.period_label = request.form.get("period_label", "").strip()
    kpi.target_value = _parse_float(request.form.get("target_value"))
    kpi.actual_value = _parse_float(request.form.get("actual_value"))
    kpi.unit = request.form.get("unit", "").strip()
    kpi.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("KPI updated.", "success")
    return _accounting_redirect(kpi.engagement_id)


@accounting_bp.route("/kpis/<int:kpi_id>/delete", methods=["POST"])
@login_required
def delete_kpi_metric(kpi_id):
    kpi = KPIMetric.query.get_or_404(kpi_id)
    _ensure_engagement_access(kpi.engagement)
    engagement_id = kpi.engagement_id
    db.session.delete(kpi)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- Accounting Risk Register (reuses RiskItem, module="accounting") ----------

@accounting_bp.route("/<int:engagement_id>/risks/add", methods=["POST"])
@login_required
def add_accounting_risk(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_accounting_module(engagement):
        return _accounting_redirect(engagement_id)
    risk = RiskItem(
        engagement_id=engagement_id,
        module="accounting",
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
    flash("Risk added to the register.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/risks/<int:risk_id>/update", methods=["POST"])
@login_required
def update_accounting_risk(risk_id):
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
    flash("Risk updated.", "success")
    return _accounting_redirect(risk.engagement_id)


@accounting_bp.route("/risks/<int:risk_id>/delete", methods=["POST"])
@login_required
def delete_accounting_risk(risk_id):
    risk = RiskItem.query.get_or_404(risk_id)
    _ensure_engagement_access(risk.engagement)
    engagement_id = risk.engagement_id
    db.session.delete(risk)
    db.session.commit()
    return _accounting_redirect(engagement_id)


# ---------- Accounting Checklist / Quality Gates ----------

@accounting_bp.route("/<int:engagement_id>/checklist/seed", methods=["POST"])
@login_required
def seed_accounting_checklist(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_accounting_module(engagement):
        return _accounting_redirect(engagement_id)
    if AccountingChecklistItem.query.filter_by(engagement_id=engagement_id).count() > 0:
        flash("The accounting checklist already has items on it.", "info")
        return _accounting_redirect(engagement_id)
    for order, (area, item_text) in enumerate(DEFAULT_ACCOUNTING_CHECKLIST_ITEMS):
        db.session.add(AccountingChecklistItem(
            engagement_id=engagement_id, area=area, item_text=item_text,
            order=order, created_by_id=current_user.id,
        ))
    db.session.commit()
    flash("The firm's default accounting checklist has been added.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/<int:engagement_id>/checklist/add", methods=["POST"])
@login_required
def add_accounting_checklist_item(engagement_id):
    engagement = _get_accounting_engagement(engagement_id)
    if not _require_accounting_module(engagement):
        return _accounting_redirect(engagement_id)
    max_order = db.session.query(db.func.max(AccountingChecklistItem.order)).filter_by(engagement_id=engagement_id).scalar() or 0
    item = AccountingChecklistItem(
        engagement_id=engagement_id,
        area=request.form.get("area", "").strip() or None,
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
        created_by_id=current_user.id,
    )
    db.session.add(item)
    db.session.commit()
    flash("Checklist item added.", "success")
    return _accounting_redirect(engagement_id)


@accounting_bp.route("/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_accounting_checklist_item(item_id):
    item = AccountingChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.response = request.form.get("response", "")
    item.comment = request.form.get("comment", "").strip()
    item.completed_by_id = current_user.id
    item.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Checklist item updated.", "success")
    return _accounting_redirect(item.engagement_id)


@accounting_bp.route("/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_accounting_checklist_item(item_id):
    item = AccountingChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return _accounting_redirect(engagement_id)
