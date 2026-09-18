"""Payroll Management - covers BOTH sides the firm asked for: Neverlank's
own staff payroll, and a payroll SERVICE the firm offers to clients (a
client's own employees, processed by this firm). See models.py for the
full design note above PayrollEmployee/PayrollPeriod/Payslip, and
payroll_calc.py for the tax engine itself.

Access is gated on the "manage_payroll" permission everywhere in this
blueprint (defaults to Partner/Admin only, since salary data is sensitive)
- there is no partial/read-only tier in this first version.
"""
from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user

from extensions import db
from models import (
    Client, User, PayrollTaxSettings, PayrollTaxBand, PayrollEmployee,
    PayrollPeriod, Payslip, PayslipItem,
    PAYROLL_SCOPES, PAYROLL_PAY_FREQUENCIES, PAYROLL_PERIOD_STATUSES,
    PAYSLIP_ITEM_CATEGORIES, PAYROLL_TAX_CAVEAT,
    user_has_permission,
)
import payroll_calc
import payroll_documents

payroll_bp = Blueprint("payroll", __name__, url_prefix="/payroll")


def _ensure_payroll_access():
    if not user_has_permission(current_user, "manage_payroll"):
        abort(403)


def _get_tax_settings():
    settings = PayrollTaxSettings.query.get(1)
    if not settings:
        settings = PayrollTaxSettings(id=1, source_notes=PAYROLL_TAX_CAVEAT)
        db.session.add(settings)
        db.session.commit()
    return settings


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------- Dashboard

@payroll_bp.route("/")
@login_required
def dashboard():
    _ensure_payroll_access()
    internal_periods = (
        PayrollPeriod.query.filter_by(scope="internal")
        .order_by(PayrollPeriod.period_start.desc().nullslast(), PayrollPeriod.id.desc())
        .limit(8).all()
    )
    client_periods = (
        PayrollPeriod.query.filter_by(scope="client")
        .order_by(PayrollPeriod.period_start.desc().nullslast(), PayrollPeriod.id.desc())
        .limit(8).all()
    )
    internal_employee_count = PayrollEmployee.query.filter_by(scope="internal", is_active=True).count()
    client_employee_count = PayrollEmployee.query.filter_by(scope="client", is_active=True).count()
    clients_with_payroll = (
        db.session.query(Client)
        .join(PayrollEmployee, PayrollEmployee.client_id == Client.id)
        .filter(PayrollEmployee.scope == "client")
        .distinct().order_by(Client.name).all()
    )
    settings = _get_tax_settings()
    bands_missing = PayrollTaxBand.query.count() == 0
    return render_template(
        "payroll/dashboard.html",
        internal_periods=internal_periods, client_periods=client_periods,
        internal_employee_count=internal_employee_count, client_employee_count=client_employee_count,
        clients_with_payroll=clients_with_payroll, settings=settings, bands_missing=bands_missing,
        caveat=PAYROLL_TAX_CAVEAT,
    )


# --------------------------------------------------------------- Tax Settings

@payroll_bp.route("/tax-settings", methods=["GET", "POST"])
@login_required
def tax_settings():
    _ensure_payroll_access()
    settings = _get_tax_settings()

    if request.method == "POST":
        settings.currency = request.form.get("currency", "USD").strip() or "USD"
        settings.aids_levy_pct = _parse_float(request.form.get("aids_levy_pct"), settings.aids_levy_pct or 0.0)
        settings.nssa_employee_pct = _parse_float(request.form.get("nssa_employee_pct"), 0.0)
        settings.nssa_employer_pct = _parse_float(request.form.get("nssa_employer_pct"), 0.0)
        ceiling = request.form.get("nssa_insurable_ceiling", "").strip()
        settings.nssa_insurable_ceiling = _parse_float(ceiling) if ceiling else None
        settings.source_notes = request.form.get("source_notes", "").strip()
        settings.updated_by_id = current_user.id
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Payroll Tax Settings updated.", "success")
        return redirect(url_for("payroll.tax_settings"))

    bands = PayrollTaxBand.query.order_by(PayrollTaxBand.lower).all()
    return render_template("payroll/tax_settings.html", settings=settings, bands=bands, caveat=PAYROLL_TAX_CAVEAT)


@payroll_bp.route("/tax-settings/band/add", methods=["POST"])
@login_required
def add_tax_band():
    _ensure_payroll_access()
    lower = _parse_float(request.form.get("lower"), 0.0)
    upper_raw = request.form.get("upper", "").strip()
    upper = _parse_float(upper_raw) if upper_raw else None
    rate_pct = _parse_float(request.form.get("rate_pct"), 0.0)
    band = PayrollTaxBand(lower=lower, upper=upper, rate_pct=rate_pct, order=PayrollTaxBand.query.count())
    db.session.add(band)
    db.session.commit()
    flash("PAYE band added.", "success")
    return redirect(url_for("payroll.tax_settings"))


@payroll_bp.route("/tax-settings/band/<int:band_id>/delete", methods=["POST"])
@login_required
def delete_tax_band(band_id):
    _ensure_payroll_access()
    band = PayrollTaxBand.query.get_or_404(band_id)
    db.session.delete(band)
    db.session.commit()
    flash("PAYE band removed.", "info")
    return redirect(url_for("payroll.tax_settings"))


# --------------------------------------------------------------- Employees

@payroll_bp.route("/employees")
@login_required
def list_employees():
    _ensure_payroll_access()
    scope = request.args.get("scope", "internal")
    client_id = request.args.get("client_id", type=int)
    query = PayrollEmployee.query.filter_by(scope=scope)
    if scope == "client" and client_id:
        query = query.filter_by(client_id=client_id)
    employees = query.order_by(PayrollEmployee.is_active.desc(), PayrollEmployee.full_name).all()
    clients = Client.query.order_by(Client.name).all()
    selected_client = Client.query.get(client_id) if client_id else None
    return render_template(
        "payroll/employees.html", employees=employees, scope=scope,
        clients=clients, selected_client=selected_client,
    )


@payroll_bp.route("/employees/new", methods=["GET", "POST"])
@login_required
def new_employee():
    _ensure_payroll_access()
    scope = request.args.get("scope", request.form.get("scope", "internal"))
    clients = Client.query.order_by(Client.name).all()
    staff_users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()

    if request.method == "POST":
        scope = request.form.get("scope", "internal")
        if scope not in PAYROLL_SCOPES:
            scope = "internal"
        client_id = request.form.get("client_id") or None
        user_id = request.form.get("user_id") or None
        full_name = request.form.get("full_name", "").strip()

        if scope == "client" and not client_id:
            flash("Please select which client this employee belongs to.", "danger")
            return render_template("payroll/employee_form.html", scope=scope, clients=clients, staff_users=staff_users, employee=None)

        if scope == "internal" and user_id:
            linked_user = User.query.get(int(user_id))
            if linked_user and not full_name:
                full_name = linked_user.name

        if not full_name:
            flash("Please provide the employee's name.", "danger")
            return render_template("payroll/employee_form.html", scope=scope, clients=clients, staff_users=staff_users, employee=None)

        employee = PayrollEmployee(
            scope=scope,
            client_id=int(client_id) if scope == "client" and client_id else None,
            user_id=int(user_id) if scope == "internal" and user_id else None,
            full_name=full_name,
            employee_number=request.form.get("employee_number", "").strip() or None,
            national_id=request.form.get("national_id", "").strip() or None,
            job_title=request.form.get("job_title", "").strip() or None,
            nssa_number=request.form.get("nssa_number", "").strip() or None,
            bank_name=request.form.get("bank_name", "").strip() or None,
            bank_account_number=request.form.get("bank_account_number", "").strip() or None,
            pay_frequency=request.form.get("pay_frequency", "Monthly"),
            basic_salary=_parse_float(request.form.get("basic_salary"), 0.0),
            date_joined=_parse_date(request.form.get("date_joined")),
            notes=request.form.get("notes", "").strip(),
            created_by_id=current_user.id,
        )
        db.session.add(employee)
        db.session.commit()
        flash(f"Added {employee.full_name} to payroll.", "success")
        return redirect(url_for("payroll.list_employees", scope=scope, client_id=employee.client_id))

    preselect_client_id = request.args.get("client_id", type=int)
    return render_template(
        "payroll/employee_form.html", scope=scope, clients=clients, staff_users=staff_users,
        employee=None, preselect_client_id=preselect_client_id,
    )


@payroll_bp.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
@login_required
def edit_employee(employee_id):
    _ensure_payroll_access()
    employee = PayrollEmployee.query.get_or_404(employee_id)
    clients = Client.query.order_by(Client.name).all()
    staff_users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()

    if request.method == "POST":
        employee.full_name = request.form.get("full_name", "").strip() or employee.full_name
        employee.employee_number = request.form.get("employee_number", "").strip() or None
        employee.national_id = request.form.get("national_id", "").strip() or None
        employee.job_title = request.form.get("job_title", "").strip() or None
        employee.nssa_number = request.form.get("nssa_number", "").strip() or None
        employee.bank_name = request.form.get("bank_name", "").strip() or None
        employee.bank_account_number = request.form.get("bank_account_number", "").strip() or None
        employee.pay_frequency = request.form.get("pay_frequency", "Monthly")
        employee.basic_salary = _parse_float(request.form.get("basic_salary"), employee.basic_salary or 0.0)
        employee.date_joined = _parse_date(request.form.get("date_joined"))
        employee.date_left = _parse_date(request.form.get("date_left"))
        employee.is_active = bool(request.form.get("is_active"))
        employee.notes = request.form.get("notes", "").strip()
        if employee.scope == "internal":
            user_id = request.form.get("user_id") or None
            employee.user_id = int(user_id) if user_id else None
        db.session.commit()
        flash("Employee record updated.", "success")
        return redirect(url_for("payroll.list_employees", scope=employee.scope, client_id=employee.client_id))

    return render_template(
        "payroll/employee_form.html", scope=employee.scope, clients=clients, staff_users=staff_users,
        employee=employee,
    )


@payroll_bp.route("/employees/<int:employee_id>/delete", methods=["POST"])
@login_required
def delete_employee(employee_id):
    _ensure_payroll_access()
    employee = PayrollEmployee.query.get_or_404(employee_id)
    scope, client_id = employee.scope, employee.client_id
    if employee.payslips:
        # Preserve payroll history - deactivate rather than delete an
        # employee who already has at least one payslip on record.
        employee.is_active = False
        db.session.commit()
        flash(f"{employee.full_name} has payslip history, so was deactivated rather than deleted.", "info")
    else:
        db.session.delete(employee)
        db.session.commit()
        flash("Employee removed.", "info")
    return redirect(url_for("payroll.list_employees", scope=scope, client_id=client_id))


# --------------------------------------------------------------- Periods

@payroll_bp.route("/periods")
@login_required
def list_periods():
    _ensure_payroll_access()
    scope = request.args.get("scope", "internal")
    client_id = request.args.get("client_id", type=int)
    query = PayrollPeriod.query.filter_by(scope=scope)
    if scope == "client" and client_id:
        query = query.filter_by(client_id=client_id)
    periods = query.order_by(PayrollPeriod.period_start.desc().nullslast(), PayrollPeriod.id.desc()).all()
    clients = Client.query.order_by(Client.name).all()
    selected_client = Client.query.get(client_id) if client_id else None
    return render_template(
        "payroll/periods.html", periods=periods, scope=scope, clients=clients, selected_client=selected_client,
    )


@payroll_bp.route("/periods/new", methods=["GET", "POST"])
@login_required
def new_period():
    _ensure_payroll_access()
    scope = request.args.get("scope", request.form.get("scope", "internal"))
    clients = Client.query.order_by(Client.name).all()

    if request.method == "POST":
        scope = request.form.get("scope", "internal")
        if scope not in PAYROLL_SCOPES:
            scope = "internal"
        client_id = request.form.get("client_id") or None
        if scope == "client" and not client_id:
            flash("Please select which client this payroll period is for.", "danger")
            return render_template("payroll/period_form.html", scope=scope, clients=clients)

        name = request.form.get("name", "").strip()
        if not name:
            flash("Please give this payroll period a name (e.g. \"September 2026\").", "danger")
            return render_template("payroll/period_form.html", scope=scope, clients=clients)

        settings = _get_tax_settings()
        period = PayrollPeriod(
            scope=scope,
            client_id=int(client_id) if scope == "client" and client_id else None,
            name=name,
            period_start=_parse_date(request.form.get("period_start")),
            period_end=_parse_date(request.form.get("period_end")),
            pay_date=_parse_date(request.form.get("pay_date")),
            currency=settings.currency,
            created_by_id=current_user.id,
        )
        db.session.add(period)
        db.session.commit()
        flash(f"Payroll period \"{period.name}\" created - generate payslips below.", "success")
        return redirect(url_for("payroll.view_period", period_id=period.id))

    preselect_client_id = request.args.get("client_id", type=int)
    return render_template("payroll/period_form.html", scope=scope, clients=clients, preselect_client_id=preselect_client_id)


@payroll_bp.route("/periods/<int:period_id>")
@login_required
def view_period(period_id):
    _ensure_payroll_access()
    period = PayrollPeriod.query.get_or_404(period_id)
    covered_employee_ids = {p.employee_id for p in period.payslips}
    eligible_query = PayrollEmployee.query.filter_by(scope=period.scope, is_active=True)
    if period.scope == "client":
        eligible_query = eligible_query.filter_by(client_id=period.client_id)
    pending_employees = [e for e in eligible_query.all() if e.id not in covered_employee_ids]
    return render_template("payroll/period_detail.html", period=period, pending_employees=pending_employees)


@payroll_bp.route("/periods/<int:period_id>/generate", methods=["POST"])
@login_required
def generate_payslips(period_id):
    _ensure_payroll_access()
    period = PayrollPeriod.query.get_or_404(period_id)
    if period.status == "Finalized":
        flash("This period is finalized - reopen it before generating more payslips.", "danger")
        return redirect(url_for("payroll.view_period", period_id=period.id))

    settings = _get_tax_settings()
    bands = PayrollTaxBand.query.all()
    covered_employee_ids = {p.employee_id for p in period.payslips}

    eligible_query = PayrollEmployee.query.filter_by(scope=period.scope, is_active=True)
    if period.scope == "client":
        eligible_query = eligible_query.filter_by(client_id=period.client_id)

    selected_ids = request.form.getlist("employee_id")
    employees = [e for e in eligible_query.all() if e.id not in covered_employee_ids]
    if selected_ids:
        selected_ids = {int(i) for i in selected_ids}
        employees = [e for e in employees if e.id in selected_ids]

    created = 0
    for employee in employees:
        result = payroll_calc.calculate_payslip(employee.basic_salary, [], [], settings, bands)
        payslip = Payslip(
            period_id=period.id, employee_id=employee.id,
            generated_by_id=current_user.id, is_auto_calculated=True,
            **result,
        )
        db.session.add(payslip)
        created += 1
    db.session.commit()
    if created:
        flash(f"Generated {created} payslip(s).", "success")
    else:
        flash("No eligible employees left to generate payslips for.", "info")
    return redirect(url_for("payroll.view_period", period_id=period.id))


@payroll_bp.route("/periods/<int:period_id>/finalize", methods=["POST"])
@login_required
def finalize_period(period_id):
    _ensure_payroll_access()
    period = PayrollPeriod.query.get_or_404(period_id)
    period.status = "Finalized" if period.status != "Finalized" else "Draft"
    db.session.commit()
    flash(f"Period marked {period.status}.", "success")
    return redirect(url_for("payroll.view_period", period_id=period.id))


@payroll_bp.route("/periods/<int:period_id>/delete", methods=["POST"])
@login_required
def delete_period(period_id):
    _ensure_payroll_access()
    period = PayrollPeriod.query.get_or_404(period_id)
    scope, client_id = period.scope, period.client_id
    db.session.delete(period)
    db.session.commit()
    flash("Payroll period deleted.", "info")
    return redirect(url_for("payroll.list_periods", scope=scope, client_id=client_id))


# --------------------------------------------------------------- Payslips

@payroll_bp.route("/payslips/<int:payslip_id>")
@login_required
def view_payslip(payslip_id):
    _ensure_payroll_access()
    payslip = Payslip.query.get_or_404(payslip_id)
    return render_template("payroll/payslip_detail.html", payslip=payslip, item_categories=PAYSLIP_ITEM_CATEGORIES)


@payroll_bp.route("/payslips/<int:payslip_id>/recalculate", methods=["POST"])
@login_required
def recalculate_payslip(payslip_id):
    _ensure_payroll_access()
    payslip = Payslip.query.get_or_404(payslip_id)
    if payslip.period.status == "Finalized":
        flash("This payslip's period is finalized - reopen it first.", "danger")
        return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))

    settings = _get_tax_settings()
    bands = PayrollTaxBand.query.all()
    allowance_items = [i for i in payslip.items if i.category == "Allowance"]
    deduction_items = [i for i in payslip.items if i.category == "Deduction"]
    result = payroll_calc.calculate_payslip(payslip.basic_salary, allowance_items, deduction_items, settings, bands)
    for key, value in result.items():
        setattr(payslip, key, value)
    payslip.is_auto_calculated = True
    payslip.generated_at = datetime.utcnow()
    payslip.generated_by_id = current_user.id
    db.session.commit()
    flash("Payslip recalculated from current salary, line items and tax settings.", "success")
    return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))


@payroll_bp.route("/payslips/<int:payslip_id>/update", methods=["POST"])
@login_required
def update_payslip(payslip_id):
    _ensure_payroll_access()
    payslip = Payslip.query.get_or_404(payslip_id)
    if payslip.period.status == "Finalized":
        flash("This payslip's period is finalized - reopen it first.", "danger")
        return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))

    payslip.basic_salary = _parse_float(request.form.get("basic_salary"), payslip.basic_salary or 0.0)
    payslip.taxable_income = _parse_float(request.form.get("taxable_income"), payslip.taxable_income or 0.0)
    payslip.gross_pay = _parse_float(request.form.get("gross_pay"), payslip.gross_pay or 0.0)
    payslip.paye_tax = _parse_float(request.form.get("paye_tax"), payslip.paye_tax or 0.0)
    payslip.aids_levy = _parse_float(request.form.get("aids_levy"), payslip.aids_levy or 0.0)
    payslip.nssa_employee = _parse_float(request.form.get("nssa_employee"), payslip.nssa_employee or 0.0)
    payslip.nssa_employer = _parse_float(request.form.get("nssa_employer"), payslip.nssa_employer or 0.0)
    payslip.other_deductions_total = _parse_float(request.form.get("other_deductions_total"), payslip.other_deductions_total or 0.0)
    payslip.net_pay = _parse_float(request.form.get("net_pay"), payslip.net_pay or 0.0)
    payslip.notes = request.form.get("notes", "").strip()
    payslip.is_auto_calculated = False
    db.session.commit()
    flash("Payslip updated (marked as manually edited).", "success")
    return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))


@payroll_bp.route("/payslips/<int:payslip_id>/items/add", methods=["POST"])
@login_required
def add_payslip_item(payslip_id):
    _ensure_payroll_access()
    payslip = Payslip.query.get_or_404(payslip_id)
    if payslip.period.status == "Finalized":
        flash("This payslip's period is finalized - reopen it first.", "danger")
        return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))

    label = request.form.get("label", "").strip()
    if not label:
        flash("Please describe the allowance/deduction.", "danger")
        return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))

    category = request.form.get("category", "Allowance")
    if category not in PAYSLIP_ITEM_CATEGORIES:
        category = "Allowance"
    item = PayslipItem(
        payslip_id=payslip.id, category=category, label=label,
        amount=_parse_float(request.form.get("amount"), 0.0),
        taxable=bool(request.form.get("taxable")) if category == "Allowance" else False,
    )
    db.session.add(item)
    db.session.commit()
    flash(f"{category} added - click Recalculate to reflect it in the totals.", "success")
    return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))


@payroll_bp.route("/payslips/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_payslip_item(item_id):
    _ensure_payroll_access()
    item = PayslipItem.query.get_or_404(item_id)
    payslip_id = item.payslip_id
    if item.payslip.period.status == "Finalized":
        flash("This payslip's period is finalized - reopen it first.", "danger")
        return redirect(url_for("payroll.view_payslip", payslip_id=payslip_id))
    db.session.delete(item)
    db.session.commit()
    flash("Line item removed - click Recalculate to reflect it in the totals.", "info")
    return redirect(url_for("payroll.view_payslip", payslip_id=payslip_id))


@payroll_bp.route("/payslips/<int:payslip_id>/delete", methods=["POST"])
@login_required
def delete_payslip(payslip_id):
    _ensure_payroll_access()
    payslip = Payslip.query.get_or_404(payslip_id)
    period_id = payslip.period_id
    if payslip.period.status == "Finalized":
        flash("This payslip's period is finalized - reopen it first.", "danger")
        return redirect(url_for("payroll.view_payslip", payslip_id=payslip.id))
    db.session.delete(payslip)
    db.session.commit()
    flash("Payslip deleted.", "info")
    return redirect(url_for("payroll.view_period", period_id=period_id))


@payroll_bp.route("/payslips/<int:payslip_id>/download/docx")
@login_required
def download_payslip_docx(payslip_id):
    _ensure_payroll_access()
    payslip = Payslip.query.get_or_404(payslip_id)
    buf = payroll_documents.generate_payslip_docx(payslip)
    filename = f"Payslip_{payslip.employee.full_name.replace(' ', '_')}_{payslip.period.name.replace(' ', '_')}.docx"
    return send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@payroll_bp.route("/payslips/<int:payslip_id>/download/pdf")
@login_required
def download_payslip_pdf(payslip_id):
    _ensure_payroll_access()
    payslip = Payslip.query.get_or_404(payslip_id)
    buf = payroll_documents.generate_payslip_pdf(payslip)
    filename = f"Payslip_{payslip.employee.full_name.replace(' ', '_')}_{payslip.period.name.replace(' ', '_')}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")
