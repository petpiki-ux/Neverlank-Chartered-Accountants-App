"""Routes for the Revenue & Expenses Analytics page and the firm's Expense
register. The maths lives in analytics.py. Both are gated by the
"view_firm_finances" permission (Partner/Admin by default) because they show
the firm's overall income and costs."""
from datetime import datetime, date
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import (
    Client, Engagement, Expense, EXPENSE_CATEGORIES, EXPENSE_STATUSES, INVOICE_CURRENCIES,
    user_has_permission, user_can_access_engagement,
)
import analytics

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")
expenses_bp = Blueprint("expenses", __name__, url_prefix="/expenses")


def finance_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not user_has_permission(current_user, "view_firm_finances"):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


def _parse_float(value, default=None):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ money formatting for the page

def fmt_money(value, currency, decimals=0):
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    return f"{sign}{currency} {abs(value):,.{decimals}f}"


def fmt_num(value, decimals=0, suffix=""):
    return "-" if value is None else f"{value:,.{decimals}f}{suffix}"


def fmt_change(value):
    if value is None:
        return None
    return f"{'+' if value >= 0 else '-'}{abs(value):.0f}%"


# ------------------------------------------------------------------ analytics

@analytics_bp.route("/")
@finance_required
def dashboard():
    currency = request.args.get("currency", INVOICE_CURRENCIES[0])
    report = analytics.build_report(
        currency=currency, preset=request.args.get("period", "12m"),
        date_from=_parse_date(request.args.get("from")), date_to=_parse_date(request.args.get("to")),
    )
    # an invoice linked to a confidential engagement is only linked to for people who may open it
    engagements = {}
    def can_open(engagement_id):
        if not engagement_id:
            return True
        if engagement_id not in engagements:
            engagements[engagement_id] = user_can_access_engagement(current_user, Engagement.query.get(engagement_id))
        return engagements[engagement_id]
    for row in report["overdue_list"]:
        row["can_open"] = can_open(row["engagement_id"])
    return render_template(
        "analytics/dashboard.html", r=report, k=report["kpi"], payload=analytics.chart_payload(report),
        currencies=INVOICE_CURRENCIES, presets=analytics.PERIOD_PRESETS,
        money=fmt_money, num=fmt_num, change=fmt_change,
    )


# ------------------------------------------------------------------ expenses

def _form_context():
    return dict(
        categories=EXPENSE_CATEGORIES, statuses=EXPENSE_STATUSES, currencies=INVOICE_CURRENCIES,
        clients=Client.query.order_by(Client.name).all(), today=date.today().isoformat(),
    )


def _apply_form(expense):
    """Copy the posted form onto `expense`; returns an error message or None."""
    f = request.form
    supplier = f.get("supplier", "").strip()
    amount = _parse_float(f.get("amount"))
    when = _parse_date(f.get("expense_date"))
    if not supplier or amount is None or amount < 0 or not when:
        return "Please enter the date, who it was paid to and a valid amount."
    category = f.get("category")
    currency = f.get("currency", "USD")
    status = f.get("status", "Paid")
    expense.supplier = supplier
    expense.description = f.get("description", "").strip() or None
    expense.expense_date = when
    expense.amount = amount
    expense.category = category if category in EXPENSE_CATEGORIES else "Other"
    expense.currency = currency if currency in INVOICE_CURRENCIES else "USD"
    expense.status = status if status in EXPENSE_STATUSES else "Paid"
    expense.due_date = _parse_date(f.get("due_date"))
    expense.paid_date = (_parse_date(f.get("paid_date")) or when) if expense.status == "Paid" else None
    expense.reference = f.get("reference", "").strip() or None
    expense.notes = f.get("notes", "").strip() or None
    try:
        expense.client_id = int(f.get("client_id")) if f.get("client_id") else None
    except ValueError:
        expense.client_id = None
    return None


@expenses_bp.route("/")
@finance_required
def list_expenses():
    q = Expense.query
    currency = request.args.get("currency", "")
    category = request.args.get("category", "")
    status = request.args.get("status", "")
    month = request.args.get("month", "")  # YYYY-MM
    if currency in INVOICE_CURRENCIES:
        q = q.filter(Expense.currency == currency)
    if category in EXPENSE_CATEGORIES:
        q = q.filter(Expense.category == category)
    if status in EXPENSE_STATUSES:
        q = q.filter(Expense.status == status)
    if month:
        try:
            y, m = int(month[:4]), int(month[5:7])
            lo = date(y, m, 1)
            hi = analytics.month_end(lo)
            q = q.filter(Expense.expense_date >= lo, Expense.expense_date <= hi)
        except (ValueError, IndexError):
            month = ""
    expenses = q.order_by(Expense.expense_date.desc(), Expense.id.desc()).all()
    totals = {}
    for e in expenses:
        totals[e.currency] = totals.get(e.currency, 0.0) + (e.amount or 0)
    return render_template(
        "expenses/list.html", expenses=expenses, totals=totals, money=fmt_money,
        f_currency=currency, f_category=category, f_status=status, f_month=month, today=date.today(),
        categories=EXPENSE_CATEGORIES, statuses=EXPENSE_STATUSES, currencies=INVOICE_CURRENCIES,
    )


@expenses_bp.route("/new", methods=["GET", "POST"])
@finance_required
def new_expense():
    expense = Expense(created_by_id=current_user.id)
    if request.method == "POST":
        error = _apply_form(expense)
        if error:
            flash(error, "danger")
            return render_template("expenses/form.html", expense=None, form=request.form, **_form_context())
        db.session.add(expense)
        db.session.commit()
        flash("Expense recorded.", "success")
        if request.form.get("add_another"):
            return redirect(url_for("expenses.new_expense"))
        return redirect(url_for("expenses.list_expenses"))
    return render_template("expenses/form.html", expense=None, form={}, **_form_context())


@expenses_bp.route("/<int:expense_id>/edit", methods=["GET", "POST"])
@finance_required
def edit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if request.method == "POST":
        error = _apply_form(expense)
        if error:
            flash(error, "danger")
            return render_template("expenses/form.html", expense=expense, form=request.form, **_form_context())
        db.session.commit()
        flash("Expense updated.", "success")
        return redirect(url_for("expenses.list_expenses"))
    return render_template("expenses/form.html", expense=expense, form={}, **_form_context())


@expenses_bp.route("/<int:expense_id>/paid", methods=["POST"])
@finance_required
def mark_paid(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    expense.status = "Paid"
    expense.paid_date = _parse_date(request.form.get("paid_date")) or date.today()
    db.session.commit()
    flash(f"{expense.supplier} marked as paid.", "success")
    return redirect(request.referrer or url_for("expenses.list_expenses"))


@expenses_bp.route("/<int:expense_id>/delete", methods=["POST"])
@finance_required
def delete_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    db.session.delete(expense)
    db.session.commit()
    flash("Expense deleted.", "info")
    return redirect(url_for("expenses.list_expenses"))
