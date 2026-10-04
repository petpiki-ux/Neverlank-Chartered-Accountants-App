"""Quarterly Payment Dates (QPDs) / Provisional Income Tax estimator.

See models.QPDEstimate's module comment for the full design rationale:
client-level (not tied to any one engagement), two estimation methods
offered side by side with no system preference (Trial Balance or VAT
Turnover), and a "flex" layer (estimated_annual_taxable_income/tax_rate_pct/
aids_levy_pct/estimated_annual_tax_charge) that's always directly editable
regardless of which method - or neither - last touched it.

Deliberately reuses financials.py's existing Trial Balance -> IAS 1 ->
Profit Before Tax -> Income Tax Computation pipeline rather than
reimplementing any of that maths here - see compute_tb_suggestion() below.
"""
import csv
import io
from datetime import date, datetime

import openpyxl
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    Client, QPDEstimate, QPDTrialBalanceLine, QPDAdjustmentLine, QPDMonthlyTurnover,
    QPDInstalmentRate, QPDInstalmentRecord, FxRate, INCOME_TAX_ITEM_TYPES, INCOME_TAX_ITEM_TYPE_LABELS,
    COAMapping, StandardChartOfAccounts, user_has_permission,
)
import financials as fin
import qpd_calc
from engagements import _read_tb_upload_rows, _allowed_tb_file  # same flexible TB file reader an audit engagement's own TB import uses
from standard_coa import coa_account_choices

qpd_bp = Blueprint("qpd", __name__, url_prefix="/clients")


def _ensure_qpd_access():
    if not user_has_permission(current_user, "manage_qpd"):
        abort(403)


def _parse_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _get_or_create_estimate(client_id, tax_year):
    estimate = QPDEstimate.query.filter_by(client_id=client_id, tax_year=tax_year).first()
    if not estimate:
        # QPD only applies to clients with a Tax Services engagement ticked
        # (see models.Client.qualifies_for_qpd) - this only gates CREATING a
        # brand-new estimate, so a client already grandfathered in via
        # existing QPD data keeps working even if that's the only reason
        # they still qualify.
        client = Client.query.get_or_404(client_id)
        if not client.qualifies_for_qpd:
            abort(403)
        estimate = QPDEstimate(client_id=client_id, tax_year=tax_year)
        db.session.add(estimate)
        db.session.flush()
    return estimate


def get_fx_rate_as_of(target_date):
    """The most recently logged FxRate on or before target_date - None if
    nothing has been logged yet on or before that date."""
    if target_date is None:
        return None
    return FxRate.query.filter(FxRate.rate_date <= target_date).order_by(FxRate.rate_date.desc()).first()


def _lookup_coa_mapping(client_id, account_name):
    norm = fin.normalize_account_name(account_name)
    if not norm:
        return None
    return COAMapping.query.filter_by(client_id=client_id, account_name=norm).first()


def _upsert_coa_mapping(client_id, account_name, fs_category, user_id, coa_account_number=None):
    norm = fin.normalize_account_name(account_name)
    if not norm or not fs_category:
        return
    mapping = COAMapping.query.filter_by(client_id=client_id, account_name=norm).first()
    if not mapping:
        mapping = COAMapping(client_id=client_id, account_name=norm)
        db.session.add(mapping)
    mapping.fs_category = fs_category
    mapping.coa_account_number = coa_account_number
    mapping.updated_by_id = user_id
    mapping.updated_at = datetime.utcnow()


def _resolve_coa_account(coa_account_number):
    """The active StandardChartOfAccounts row for coa_account_number, or
    None if blank or not an active standard account - used so a direct POST
    can never set a line's coa_account_number to something that doesn't (or
    no longer) exist."""
    coa_account_number = (coa_account_number or "").strip()
    if not coa_account_number:
        return None
    return StandardChartOfAccounts.query.filter_by(account_number=coa_account_number, is_active=True).first()


def compute_tb_suggestion(estimate):
    """None if there isn't enough entered yet (no lines, or no months-
    covered figure to annualize by); otherwise financials.
    build_income_tax_computation()'s own result dict (taxable_income,
    total_tax_charge, etc.), plus the YTD/annualized profit before tax it
    was built from. Uses financials.compute_totals/build_income_statement
    on estimate.tb_lines exactly as an audited engagement's own Statement
    of Profit or Loss would - see QPDTrialBalanceLine's docstring on why
    it's duck-type compatible with those functions."""
    if not estimate.tb_lines or not estimate.tb_months_covered:
        return None
    totals = fin.compute_totals(estimate.tb_lines)
    pl = fin.build_income_statement(totals)
    pbt_ytd = pl["profit_before_tax"]["current"]
    annualized_pbt = round(pbt_ytd / estimate.tb_months_covered * 12, 2)
    revenue_ytd = totals["revenue"]["current"]
    annualized_revenue = round(revenue_ytd / estimate.tb_months_covered * 12, 2)
    lines = [{"item_type": l.item_type, "description": l.description, "amount": l.amount} for l in estimate.adjustment_lines]
    result = fin.build_income_tax_computation(
        annualized_pbt, lines,
        tax_loss_brought_forward=estimate.tb_tax_loss_brought_forward or 0.0,
        tax_rate_percent=estimate.tax_rate_pct, aids_levy_percent=estimate.aids_levy_pct,
    )
    result["pbt_ytd"] = pbt_ytd
    result["annualized_pbt"] = annualized_pbt
    result["annualized_revenue"] = annualized_revenue
    return result


def compute_vat_suggestion(estimate):
    """None until at least one month's turnover and a net margin % are both
    entered. Reuses financials.build_income_tax_computation() too (with no
    add-back/deduction lines) purely so the tax-rate/AIDS-levy arithmetic
    stays identical to the Trial Balance method above - not because a VAT-
    turnover-implied figure has anything to add back."""
    annualized_turnover = estimate.annualized_turnover
    if not annualized_turnover or not estimate.vat_net_margin_pct:
        return None
    implied_taxable_income = qpd_calc.compute_taxable_income_from_vat(annualized_turnover, estimate.vat_net_margin_pct)
    result = fin.build_income_tax_computation(
        implied_taxable_income, [],
        tax_rate_percent=estimate.tax_rate_pct, aids_levy_percent=estimate.aids_levy_pct,
    )
    result["annualized_turnover"] = annualized_turnover
    result["annualized_revenue"] = annualized_turnover  # turnover IS revenue for this method - same key name as compute_tb_suggestion so use_vat_estimate/use_tb_estimate can share one code path
    return result


# ---------- Main estimator page ----------

@qpd_bp.route("/<int:client_id>/qpd")
@login_required
def view_qpd(client_id):
    client = Client.query.get_or_404(client_id)
    if not client.qualifies_for_qpd:
        flash("QPD / provisional tax applies only to clients with a Tax Services engagement ticked.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))
    tax_year = request.args.get("tax_year", type=int) or date.today().year
    estimate = _get_or_create_estimate(client_id, tax_year)
    db.session.commit()  # persist a freshly-created blank estimate so the page's own forms have a stable id to post against

    other_years = sorted({e.tax_year for e in client.qpd_estimates if e.tax_year != tax_year}, reverse=True)

    instalment_rates = QPDInstalmentRate.query.order_by(QPDInstalmentRate.order).all()

    return render_template(
        "qpd/estimate.html", client=client, estimate=estimate, tax_year=tax_year, other_years=other_years,
        tb_suggestion=compute_tb_suggestion(estimate), vat_suggestion=compute_vat_suggestion(estimate),
        fs_category_choices=fin.category_choices(), coa_accounts=coa_account_choices(),
        income_tax_item_types=INCOME_TAX_ITEM_TYPES, income_tax_item_type_labels=INCOME_TAX_ITEM_TYPE_LABELS,
        instalment_rates=instalment_rates,
        can_manage=user_has_permission(current_user, "manage_qpd"),
    )


# ---------- Trial Balance method ----------

@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/tb/upload", methods=["POST"])
@login_required
def upload_tb(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    if not _allowed_tb_file(file.filename):
        flash("Please upload a .xlsx or .csv file.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))

    original_name = secure_filename(file.filename)
    try:
        raw_rows, format_note = _read_tb_upload_rows(file, original_name)
        cleaned_rows = fin.parse_tb_rows(raw_rows)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    except Exception:
        flash("Could not read that file - make sure it's a .xlsx or .csv using the Trial Balance template's columns.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))

    # Preserve any category already set on the existing lines to the
    # reusable client-wide mapping before replacing them - same safety net
    # as engagements.upload_trial_balance.
    for existing_line in estimate.tb_lines:
        if existing_line.fs_category:
            _upsert_coa_mapping(client_id, existing_line.account_name, existing_line.fs_category, current_user.id, existing_line.coa_account_number)
    QPDTrialBalanceLine.query.filter_by(estimate_id=estimate.id).delete()

    for row in cleaned_rows:
        mapping = _lookup_coa_mapping(client_id, row["account_name"])
        db.session.add(QPDTrialBalanceLine(
            estimate_id=estimate.id,
            account_code=row["account_code"], account_name=row["account_name"],
            fs_category=mapping.fs_category if mapping else None,
            coa_account_number=mapping.coa_account_number if mapping else None,
            current_debit=row["current_debit"], current_credit=row["current_credit"],
        ))
    estimate.estimation_method = "Trial Balance"
    estimate.updated_by_id = current_user.id
    estimate.updated_at = datetime.utcnow()
    db.session.commit()
    if format_note:
        flash(format_note, "info")
    flash(f"Imported {len(cleaned_rows)} account(s) from '{original_name}'.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/tb/lines/add", methods=["POST"])
@login_required
def add_tb_line(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    account_name = request.form.get("account_name", "").strip()
    if not account_name:
        flash("Enter an account name.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    mapping = _lookup_coa_mapping(client_id, account_name)
    db.session.add(QPDTrialBalanceLine(
        estimate_id=estimate.id, account_name=account_name,
        fs_category=mapping.fs_category if mapping else None,
        coa_account_number=mapping.coa_account_number if mapping else None,
        current_debit=_parse_float(request.form.get("current_debit"), 0.0),
        current_credit=_parse_float(request.form.get("current_credit"), 0.0),
    ))
    estimate.estimation_method = "Trial Balance"
    db.session.commit()
    flash("Account added.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/tb-lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_tb_line(client_id, line_id):
    _ensure_qpd_access()
    line = QPDTrialBalanceLine.query.get_or_404(line_id)
    tax_year = line.estimate.tax_year
    db.session.delete(line)
    db.session.commit()
    flash("Account removed.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/tb-lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_tb_line(client_id, line_id):
    _ensure_qpd_access()
    line = QPDTrialBalanceLine.query.get_or_404(line_id)
    tax_year = line.estimate.tax_year
    line.account_code = request.form.get("account_code", line.account_code or "").strip()
    line.account_name = request.form.get("account_name", line.account_name).strip() or line.account_name
    category = request.form.get("fs_category", "").strip()
    if category and category not in fin.CATEGORY_BY_CODE:
        category = ""
    # Picking a Standard Chart of Accounts account is authoritative - it
    # drives BOTH the account number and the IAS 1 category together (the
    # template also auto-fills the category dropdown client-side via JS,
    # but this is enforced server-side too for a direct POST). Leaving the
    # standard-account picker blank keeps the plain category-only mapping
    # behaviour exactly as before this feature existed.
    coa_account = _resolve_coa_account(request.form.get("coa_account_number"))
    if coa_account:
        line.coa_account_number = coa_account.account_number
        category = coa_account.fs_category
    else:
        line.coa_account_number = None
    line.fs_category = category or None
    line.current_debit = _parse_float(request.form.get("current_debit"), line.current_debit or 0.0)
    line.current_credit = _parse_float(request.form.get("current_credit"), line.current_credit or 0.0)
    if category:
        _upsert_coa_mapping(client_id, line.account_name, category, current_user.id, line.coa_account_number)
    db.session.commit()
    flash("Account updated.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/tb/settings", methods=["POST"])
@login_required
def update_tb_settings(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    months = request.form.get("tb_months_covered", "").strip()
    estimate.tb_months_covered = int(months) if months.isdigit() and 1 <= int(months) <= 12 else None
    estimate.tb_tax_loss_brought_forward = _parse_float(request.form.get("tb_tax_loss_brought_forward"), 0.0)
    db.session.commit()
    flash("Trial Balance settings saved.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/tb/adjustments/add", methods=["POST"])
@login_required
def add_tb_adjustment(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    description = request.form.get("description", "").strip()
    if not description:
        flash("Enter a description for the add-back/deduction.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    item_type = request.form.get("item_type", "addback")
    if item_type not in INCOME_TAX_ITEM_TYPES:
        item_type = "addback"
    max_order = max([l.order for l in estimate.adjustment_lines], default=0)
    db.session.add(QPDAdjustmentLine(
        estimate_id=estimate.id, item_type=item_type, description=description,
        amount=_parse_float(request.form.get("amount"), 0.0), order=max_order + 1,
    ))
    estimate.estimation_method = "Trial Balance"
    db.session.commit()
    flash("Adjustment added.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/adjustments/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_tb_adjustment(client_id, line_id):
    _ensure_qpd_access()
    line = QPDAdjustmentLine.query.get_or_404(line_id)
    tax_year = line.estimate.tax_year
    db.session.delete(line)
    db.session.commit()
    flash("Adjustment removed.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/tb/use-estimate", methods=["POST"])
@login_required
def use_tb_estimate(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    result = compute_tb_suggestion(estimate)
    if result is None:
        flash("Enter the months covered and at least one mapped Trial Balance account before using this estimate.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    estimate.estimation_method = "Trial Balance"
    estimate.estimated_annual_revenue = result["annualized_revenue"]
    estimate.estimated_annual_taxable_income = result["taxable_income"]
    estimate.estimated_annual_tax_charge = result["total_tax_charge"]
    estimate.updated_by_id = current_user.id
    estimate.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Estimate updated from the Trial Balance working - still fully editable below.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


# ---------- VAT Turnover method ----------

@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/vat/turnover/save", methods=["POST"])
@login_required
def save_vat_turnover(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    month_str = request.form.get("month", "").strip()  # "YYYY-MM" from <input type="month">
    try:
        year_part, month_part = month_str.split("-")
        month_date = date(int(year_part), int(month_part), 1)
    except (ValueError, AttributeError):
        flash("Choose a valid month.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    row = QPDMonthlyTurnover.query.filter_by(estimate_id=estimate.id, month=month_date).first()
    if not row:
        row = QPDMonthlyTurnover(estimate_id=estimate.id, month=month_date)
        db.session.add(row)
    row.turnover_amount = _parse_float(request.form.get("turnover_amount"), 0.0)
    estimate.estimation_method = "VAT Turnover"
    db.session.commit()
    flash(f"Turnover for {month_date.strftime('%B %Y')} saved.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/turnover/<int:row_id>/delete", methods=["POST"])
@login_required
def delete_vat_turnover(client_id, row_id):
    _ensure_qpd_access()
    row = QPDMonthlyTurnover.query.get_or_404(row_id)
    tax_year = row.estimate.tax_year
    db.session.delete(row)
    db.session.commit()
    flash("Month removed.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/vat/settings", methods=["POST"])
@login_required
def update_vat_settings(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    margin = request.form.get("vat_net_margin_pct", "").strip()
    estimate.vat_net_margin_pct = _parse_float(margin) if margin else None
    db.session.commit()
    flash("VAT Turnover settings saved.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/vat/use-estimate", methods=["POST"])
@login_required
def use_vat_estimate(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    result = compute_vat_suggestion(estimate)
    if result is None:
        flash("Enter at least one month's VAT turnover and a net margin % before using this estimate.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    estimate.estimation_method = "VAT Turnover"
    estimate.estimated_annual_revenue = result["annualized_revenue"]
    estimate.estimated_annual_taxable_income = result["taxable_income"]
    estimate.estimated_annual_tax_charge = result["total_tax_charge"]
    estimate.updated_by_id = current_user.id
    estimate.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Estimate updated from VAT turnover - still fully editable below.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


# ---------- The "flex" layer - always directly editable ----------

@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/estimate/update", methods=["POST"])
@login_required
def update_estimate(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    revenue = request.form.get("estimated_annual_revenue", "").strip()
    estimate.estimated_annual_revenue = _parse_float(revenue) if revenue else None
    estimate.estimated_annual_taxable_income = _parse_float(
        request.form.get("estimated_annual_taxable_income"), estimate.estimated_annual_taxable_income or 0.0,
    )
    rate = request.form.get("tax_rate_pct", "").strip()
    estimate.tax_rate_pct = _parse_float(rate) if rate else None
    levy = request.form.get("aids_levy_pct", "").strip()
    estimate.aids_levy_pct = _parse_float(levy) if levy else None
    estimate.estimated_annual_tax_charge = _parse_float(
        request.form.get("estimated_annual_tax_charge"), estimate.estimated_annual_tax_charge or 0.0,
    )
    # Dual-currency (USD/ZWG) split - see models.QPDEstimate.effective_usd_pct
    # for ZIMRA's "50% rule" this drives.
    usd_pct = request.form.get("revenue_usd_pct", "").strip()
    estimate.revenue_usd_pct = _parse_float(usd_pct) if usd_pct else None
    estimate.notes = request.form.get("notes", "").strip()
    estimate.estimation_method = "Manual"
    estimate.updated_by_id = current_user.id
    estimate.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Estimate saved.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


# ---------- Instalments ----------

@qpd_bp.route("/<int:client_id>/qpd/<int:tax_year>/instalments/compute", methods=["POST"])
@login_required
def compute_instalments(client_id, tax_year):
    _ensure_qpd_access()
    estimate = _get_or_create_estimate(client_id, tax_year)
    rates = QPDInstalmentRate.query.order_by(QPDInstalmentRate.order).all()
    if not rates:
        flash("No QPD instalment structure is configured yet - set one up first.", "danger")
        return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))
    built = qpd_calc.build_instalments(estimate.estimated_annual_tax_charge or 0.0, rates)
    existing_by_order = {r.order: r for r in estimate.instalments}
    skipped_paid = 0
    fx_rate_today = get_fx_rate_as_of(date.today())
    for item in built:
        record = existing_by_order.get(item["order"])
        if record and record.is_paid:
            skipped_paid += 1
            continue  # never retroactively change an instalment already recorded as paid
        if not record:
            record = QPDInstalmentRecord(estimate_id=estimate.id, order=item["order"])
            db.session.add(record)
        record.label = item["label"]
        record.due_date = date(tax_year, item["due_month"], item["due_day"])
        record.cumulative_pct = item["cumulative_pct"]
        record.computed_amount = item["amount"]
        record.annual_tax_charge_snapshot = estimate.estimated_annual_tax_charge or 0.0
        # Freeze the USD/ZWG split in effect right now too (see
        # models.QPDEstimate.effective_usd_pct) - same "frozen snapshot"
        # treatment as annual_tax_charge_snapshot above, so later editing
        # the currency mix never silently reshuffles an instalment that's
        # already been computed (or paid).
        record.usd_pct_snapshot = estimate.effective_usd_pct
        # Freeze today's logged USD:ZWG rate too (see
        # models.QPDInstalmentRecord.fx_rate_snapshot/zwg_amount_converted)
        # - None if nothing has been logged in the FX Rates log yet.
        record.fx_rate_snapshot = fx_rate_today.rate if fx_rate_today else None
        record.computed_at = datetime.utcnow()
    db.session.commit()
    if skipped_paid:
        flash(f"QPD instalments recomputed ({skipped_paid} already-paid instalment(s) left untouched).", "success")
    else:
        flash("QPD instalments computed from the current estimate.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/instalments/<int:instalment_id>/pay", methods=["POST"])
@login_required
def pay_instalment(client_id, instalment_id):
    _ensure_qpd_access()
    record = QPDInstalmentRecord.query.get_or_404(instalment_id)
    tax_year = record.estimate.tax_year
    record.paid_amount = _parse_float(request.form.get("paid_amount"), record.computed_amount or 0.0)
    record.paid_date = _parse_date(request.form.get("paid_date")) or date.today()
    record.paid_reference = request.form.get("paid_reference", "").strip()
    # Actual currency breakdown of what was remitted - optional, independent
    # of paid_amount above (see models.QPDInstalmentRecord's docstring on
    # why these are entered separately rather than derived).
    usd_paid = request.form.get("paid_amount_usd", "").strip()
    record.paid_amount_usd = _parse_float(usd_paid) if usd_paid else None
    zwg_paid = request.form.get("paid_amount_zwg", "").strip()
    record.paid_amount_zwg = _parse_float(zwg_paid) if zwg_paid else None
    db.session.commit()
    flash(f"{record.label} marked paid.", "success")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


@qpd_bp.route("/<int:client_id>/qpd/instalments/<int:instalment_id>/unpay", methods=["POST"])
@login_required
def unpay_instalment(client_id, instalment_id):
    _ensure_qpd_access()
    record = QPDInstalmentRecord.query.get_or_404(instalment_id)
    tax_year = record.estimate.tax_year
    record.paid_amount = None
    record.paid_date = None
    record.paid_reference = None
    record.paid_amount_usd = None
    record.paid_amount_zwg = None
    db.session.commit()
    flash(f"{record.label} reopened.", "info")
    return redirect(url_for("qpd.view_qpd", client_id=client_id, tax_year=tax_year))


# ---------- Firm-wide QPD instalment structure (dates/percentages) ----------

@qpd_bp.route("/qpd/instalment-rates", methods=["GET", "POST"])
@login_required
def instalment_rates():
    _ensure_qpd_access()
    if request.method == "POST":
        label = request.form.get("label", "").strip()
        due_month = request.form.get("due_month", "").strip()
        due_day = request.form.get("due_day", "").strip()
        cumulative_pct = request.form.get("cumulative_pct", "").strip()
        if not (label and due_month.isdigit() and due_day.isdigit() and cumulative_pct):
            flash("Fill in every field to add an instalment.", "danger")
        else:
            max_order = max([r.order for r in QPDInstalmentRate.query.all()], default=-1)
            db.session.add(QPDInstalmentRate(
                label=label, due_month=int(due_month), due_day=int(due_day),
                cumulative_pct=_parse_float(cumulative_pct, 0.0), order=max_order + 1,
            ))
            db.session.commit()
            flash("Instalment added.", "success")
        return redirect(url_for("qpd.instalment_rates"))
    rates = QPDInstalmentRate.query.order_by(QPDInstalmentRate.order).all()
    return render_template("qpd/instalment_rates.html", rates=rates)


@qpd_bp.route("/qpd/instalment-rates/<int:rate_id>/update", methods=["POST"])
@login_required
def update_instalment_rate(rate_id):
    _ensure_qpd_access()
    rate = QPDInstalmentRate.query.get_or_404(rate_id)
    rate.label = request.form.get("label", "").strip() or rate.label
    month = request.form.get("due_month", "").strip()
    day = request.form.get("due_day", "").strip()
    if month.isdigit():
        rate.due_month = int(month)
    if day.isdigit():
        rate.due_day = int(day)
    pct = request.form.get("cumulative_pct", "").strip()
    if pct:
        rate.cumulative_pct = _parse_float(pct, rate.cumulative_pct)
    db.session.commit()
    flash("Instalment updated.", "success")
    return redirect(url_for("qpd.instalment_rates"))


@qpd_bp.route("/qpd/instalment-rates/<int:rate_id>/delete", methods=["POST"])
@login_required
def delete_instalment_rate(rate_id):
    _ensure_qpd_access()
    rate = QPDInstalmentRate.query.get_or_404(rate_id)
    db.session.delete(rate)
    db.session.commit()
    flash("Instalment removed.", "success")
    return redirect(url_for("qpd.instalment_rates"))


# ---------- Firm-wide FX Rate log (USD:ZWG) ----------
# Dated rates logged by a preparer (e.g. the RBZ auction/interbank rate),
# used to convert a QPD instalment's ZWG-denominated portion from a USD-
# equivalent figure into an actual ZWG currency amount - see
# models.FxRate/QPDInstalmentRecord.zwg_amount_converted.

@qpd_bp.route("/qpd/fx-rates", methods=["GET", "POST"])
@login_required
def fx_rates():
    _ensure_qpd_access()
    if request.method == "POST":
        rate_date = _parse_date(request.form.get("rate_date", "").strip())
        rate_value = request.form.get("rate", "").strip()
        if not rate_date or not rate_value:
            flash("Enter both a date and a rate.", "danger")
            return redirect(url_for("qpd.fx_rates"))
        existing = FxRate.query.filter_by(rate_date=rate_date).first()
        if not existing:
            existing = FxRate(rate_date=rate_date)
            db.session.add(existing)
        existing.rate = _parse_float(rate_value, existing.rate or 0.0)
        existing.source = request.form.get("source", "").strip()
        existing.notes = request.form.get("notes", "").strip()
        existing.created_by_id = current_user.id
        existing.created_at = datetime.utcnow()
        db.session.commit()
        flash(f"USD:ZWG rate for {rate_date.strftime('%d %b %Y')} logged.", "success")
        return redirect(url_for("qpd.fx_rates"))
    rates = FxRate.query.order_by(FxRate.rate_date.desc()).all()
    return render_template("qpd/fx_rates.html", rates=rates)


def _parse_import_date(value):
    """Accepts a real Excel date/datetime cell, or a string in one of a
    few common layouts (ISO, or day-first d/m/Y as used locally), and
    returns a date or None if it's not recognizable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _read_fx_rate_upload_rows(file_storage, filename):
    """Returns a list of {header: value} dicts from an uploaded .xlsx/.xls
    or .csv file of FX rates. Raises ValueError with a plain-English
    message if it can't find a usable header row (one containing both
    'date' and 'rate')."""
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        content = file_storage.read().decode("utf-8-sig", errors="replace")
        all_rows = list(csv.reader(io.StringIO(content)))
    else:
        workbook = openpyxl.load_workbook(file_storage, data_only=True)
        all_rows = list(workbook.active.iter_rows(values_only=True))

    header_idx = None
    for i, row in enumerate(all_rows):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if any("date" in c for c in cells) and any("rate" in c for c in cells):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Could not find a header row containing both 'Date' and 'Rate' columns in that file. "
            "Download the template below and use its column headers."
        )

    headers = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
    data_rows = []
    for row in all_rows[header_idx + 1:]:
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        data_rows.append({h: v for h, v in zip(headers, row) if h})
    return data_rows


@qpd_bp.route("/qpd/fx-rates/import", methods=["POST"])
@login_required
def import_fx_rates():
    _ensure_qpd_access()
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("qpd.fx_rates"))
    if not _allowed_tb_file(file.filename):
        flash("Please upload a .xlsx, .xls or .csv file.", "danger")
        return redirect(url_for("qpd.fx_rates"))

    try:
        rows = _read_fx_rate_upload_rows(file, secure_filename(file.filename))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("qpd.fx_rates"))

    date_key = rate_key = source_key = notes_key = None
    for h in (rows[0].keys() if rows else []):
        hl = h.strip().lower()
        if date_key is None and "date" in hl:
            date_key = h
        elif rate_key is None and "rate" in hl:
            rate_key = h
        elif source_key is None and "source" in hl:
            source_key = h
        elif notes_key is None and "note" in hl:
            notes_key = h

    created = updated = 0
    skipped = []
    for i, row in enumerate(rows, start=2):  # +1 for 1-indexing, +1 for header row
        rate_date = _parse_import_date(row.get(date_key)) if date_key else None
        rate_raw = row.get(rate_key) if rate_key else None
        if not rate_date:
            skipped.append(f"row {i}: unreadable or missing date")
            continue
        try:
            rate_value = float(rate_raw)
        except (TypeError, ValueError):
            skipped.append(f"row {i}: missing or non-numeric rate")
            continue
        if rate_value <= 0:
            skipped.append(f"row {i}: rate must be greater than zero")
            continue

        existing = FxRate.query.filter_by(rate_date=rate_date).first()
        if existing:
            updated += 1
        else:
            existing = FxRate(rate_date=rate_date)
            db.session.add(existing)
            created += 1
        existing.rate = rate_value
        existing.source = str(row.get(source_key) or "").strip() if source_key else (existing.source or "")
        existing.notes = str(row.get(notes_key) or "").strip() if notes_key else (existing.notes or "")
        existing.created_by_id = current_user.id
        existing.created_at = datetime.utcnow()

    db.session.commit()

    parts = []
    if created:
        parts.append(f"{created} rate(s) added")
    if updated:
        parts.append(f"{updated} existing date(s) updated")
    summary = ", ".join(parts) if parts else "No rates were imported"
    if skipped:
        shown = "; ".join(skipped[:5])
        more = f" (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
        flash(f"{summary}. Skipped {len(skipped)} row(s): {shown}{more}.", "warning" if parts else "danger")
    else:
        flash(f"{summary} from '{file.filename}'.", "success")
    return redirect(url_for("qpd.fx_rates"))


@qpd_bp.route("/qpd/fx-rates/<int:rate_id>/delete", methods=["POST"])
@login_required
def delete_fx_rate(rate_id):
    _ensure_qpd_access()
    rate = FxRate.query.get_or_404(rate_id)
    db.session.delete(rate)
    db.session.commit()
    flash("Rate removed.", "success")
    return redirect(url_for("qpd.fx_rates"))


# ---------- Top-level QPD dashboard (its own section, not nested under a
# client page) - a separate, unprefixed blueprint purely for the dashboard
# itself, so existing qpd_bp routes (all under /clients, unchanged) keep
# their URLs; only this new entry point gets a clean top-level /qpd URL. ----------

qpd_dashboard_bp = Blueprint("qpd_dashboard", __name__, url_prefix="/qpd")


@qpd_dashboard_bp.route("/")
@login_required
def dashboard():
    tax_year = request.args.get("tax_year", type=int) or date.today().year
    qualifying_clients = [c for c in Client.query.order_by(Client.name).all() if c.qualifies_for_qpd]
    rows = []
    for client in qualifying_clients:
        estimate = QPDEstimate.query.filter_by(client_id=client.id, tax_year=tax_year).first()
        instalments = estimate.instalments if estimate else []
        next_due = next((i for i in instalments if not i.is_paid), None)
        rows.append({
            "client": client,
            "estimate": estimate,
            "instalment_count": len(instalments),
            "unpaid_count": sum(1 for i in instalments if not i.is_paid),
            "next_due": next_due,
        })
    other_years = sorted(
        {e.tax_year for c in qualifying_clients for e in c.qpd_estimates if e.tax_year != tax_year},
        reverse=True,
    )
    return render_template(
        "qpd/dashboard.html", rows=rows, tax_year=tax_year, other_years=other_years,
        can_manage=user_has_permission(current_user, "manage_qpd"),
    )
