"""Routes for the Revenue & Expenses Analytics page and the firm's Expense
register. The maths lives in analytics.py. Both are gated by the
"view_firm_finances" permission (Partner/Admin by default) because they show
the firm's overall income and costs."""
from datetime import datetime, date, timedelta
from functools import wraps

import os

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify
from flask_login import login_required, current_user

from extensions import db
from models import visible_clients
from models import (
    Client, Engagement, Expense, ExpenseCategory, Supplier, EXPENSE_CATEGORIES, EXPENSE_FALLBACK_CATEGORY, EXPENSE_STATUSES, INVOICE_CURRENCIES,
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


# ------------------------------------------------------------------ expense categories

def ensure_categories():
    """Seed the built-in list the first time only (a category the firm later deletes must stay deleted), and make sure
    'Other' and every category already used on an expense exist."""
    have = {c.name for c in ExpenseCategory.query.all()}
    first_time = not have
    added = False
    for i, name in enumerate(EXPENSE_CATEGORIES):
        if (first_time or name == EXPENSE_FALLBACK_CATEGORY) and name not in have:
            db.session.add(ExpenseCategory(name=name, order=i))
            have.add(name)
            added = True
    for (name,) in db.session.query(Expense.category).distinct().all():
        if name and name not in have:
            db.session.add(ExpenseCategory(name=name, order=100))
            have.add(name)
            added = True
    if added:
        db.session.commit()


def category_names(active_only=True, also=None):
    """Names for a dropdown: active ones in order, plus `also` (an expense's current category even if switched off)."""
    ensure_categories()
    rows = ExpenseCategory.query.order_by(ExpenseCategory.order, ExpenseCategory.name).all()
    names = [c.name for c in rows if c.is_active or not active_only]
    if also and also not in names:
        names.append(also)
    return names


def _clean_category_name(raw):
    return " ".join((raw or "").split())[:60]


def add_category(raw, user):
    """Create (or reactivate) a category. Returns (name, error)."""
    name = _clean_category_name(raw)
    if not name:
        return None, "Type a name for the category."
    ensure_categories()
    existing = ExpenseCategory.query.filter(db.func.lower(ExpenseCategory.name) == name.lower()).first()
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
        return existing.name, None
    last = db.session.query(db.func.max(ExpenseCategory.order)).scalar() or 0
    db.session.add(ExpenseCategory(name=name, order=last + 1, created_by_id=user.id))
    db.session.flush()
    return name, None


# ------------------------------------------------------------------ expenses

def _form_context(expense=None):
    return dict(
        categories=category_names(also=expense.category if expense is not None else None), statuses=EXPENSE_STATUSES, currencies=INVOICE_CURRENCIES,
        clients=visible_clients(current_user), today=date.today().isoformat(),
        supplier_list=Supplier.query.order_by(Supplier.name).all(),
    )


def _apply_form(expense):
    """Copy the posted form onto `expense`; returns an error message or None."""
    f = request.form
    import suppliers
    supplier = f.get("supplier", "").strip()
    sup = None
    try:
        sup = Supplier.query.get(int(f.get("supplier_id"))) if f.get("supplier_id") else None
    except ValueError:
        sup = None
    if sup is None and supplier:
        sup = suppliers.find_by_name(supplier)      # typed name matches a supplier on file: link it
    if sup is not None:
        supplier = sup.name
    amount = _parse_float(f.get("amount"))
    when = _parse_date(f.get("expense_date"))
    if not supplier or amount is None or amount < 0 or not when:
        return "Please enter the date, who it was paid to and a valid amount."
    category = f.get("category")
    new_category = _clean_category_name(f.get("new_category"))
    if new_category:
        category, err = add_category(new_category, current_user)
        if err:
            return err
    currency = f.get("currency", "USD")
    status = f.get("status", "Paid")
    if sup is not None and (expense.id is None or expense.supplier_id != sup.id):
        problem = suppliers.status_problem(sup, "bill")
        if problem:
            return problem
    if expense.id is not None and expense.supplier_payments:
        paid_so_far = suppliers.paid_on(expense)
        if amount < paid_so_far - 0.005:
            return f"{expense.currency} {paid_so_far:,.2f} has already been paid on this bill, so it cannot be reduced below that. Delete or change the payments first."
        if currency != expense.currency:
            return "Payments have been recorded in " + expense.currency + ", so the currency cannot be changed."
    expense.supplier_id = sup.id if sup is not None else None
    expense.supplier = supplier
    expense.description = f.get("description", "").strip() or None
    expense.expense_date = when
    expense.amount = amount
    valid = set(category_names(also=expense.category))
    expense.category = category if category in valid else EXPENSE_FALLBACK_CATEGORY
    expense.currency = currency if currency in INVOICE_CURRENCIES else "USD"
    expense.status = status if status in EXPENSE_STATUSES else "Paid"
    expense.due_date = _parse_date(f.get("due_date"))
    if sup is not None and expense.status == "Unpaid" and not expense.due_date and sup.payment_terms_days:
        expense.due_date = when + timedelta(days=int(sup.payment_terms_days))   # the supplier's payment terms
    expense.paid_date = (_parse_date(f.get("paid_date")) or when) if expense.status == "Paid" else None
    if expense.id is not None and expense.supplier_payments:
        suppliers.sync_expense(expense)       # the bill's status follows its payments
    expense.reference = f.get("reference", "").strip() or None
    expense.notes = f.get("notes", "").strip() or None
    import cashbook
    cash_account_id, bad_account = cashbook.parse_choice(f.get("cash_account_id"), expense.currency)
    if bad_account:
        return bad_account
    expense._cash_account_id = cash_account_id
    try:
        expense.client_id = int(f.get("client_id")) if f.get("client_id") else None
    except ValueError:
        expense.client_id = None
    return None


def _store_cash_account(expense):
    """Keep the cash book account chosen on the form (blank = automatic). The picker is only on the form for users who
    may see it, so a save by anyone else never changes an existing choice."""
    import cashbook
    if cashbook.can_choose_account():
        cashbook.set_account(f"expense:{expense.id}", getattr(expense, "_cash_account_id", None))


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
    all_categories = category_names(active_only=False)
    if category in all_categories:
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
        categories=all_categories, statuses=EXPENSE_STATUSES, currencies=INVOICE_CURRENCIES,
    )


@expenses_bp.route("/read-invoice", methods=["POST"])
@finance_required
def read_invoice():
    """Read a scanned (or photographed) supplier invoice / receipt and send back what is printed on it, to pre-fill the expense form.
    Nothing is saved and the file is not kept: a person checks the fields and saves the expense as usual."""
    import tempfile
    import ocr_reader
    from suppliers import find_by_name
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"status": "error", "message": "Choose a file first."}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ("pdf", "jpg", "jpeg", "png"):
        return jsonify({"status": "error", "message": "Only PDF, JPG or PNG files can be read."}), 400
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "invoice." + ext)
        f.save(path)
        fields, status, error = ocr_reader.read_invoice(path)
    if status != "done":
        return jsonify({"status": status, "message": error or "Could not read it."}), (200 if status == "not_configured" else 502)
    sup = find_by_name(fields.get("supplier_name"))
    if sup is None and fields.get("supplier_name"):
        want = " ".join(fields["supplier_name"].lower().split())
        hits = [x for x in Supplier.query.all() if len(x.name or "") >= 4 and (" ".join(x.name.lower().split()) in want or want in " ".join(x.name.lower().split()))]
        sup = hits[0] if len(hits) == 1 else None
    out = {"status": "done", "fields": fields, "supplier": None}
    if sup is not None:
        out["supplier"] = {"id": sup.id, "name": sup.name, "currency": sup.currency, "category": sup.default_category or "",
                           "terms": sup.payment_terms_days or 0, "status": sup.status}
    return jsonify(out)


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
        db.session.flush()
        _store_cash_account(expense)
        db.session.commit()
        flash("Expense recorded.", "success")
        if request.form.get("add_another"):
            return redirect(url_for("expenses.new_expense"))
        return redirect(url_for("expenses.list_expenses"))
    preset = {}
    try:
        sup = Supplier.query.get(int(request.args.get("supplier_id"))) if request.args.get("supplier_id") else None
    except ValueError:
        sup = None
    if sup is not None:
        preset = {"supplier_id": str(sup.id), "supplier": sup.name, "currency": sup.currency, "status": "Unpaid"}
        if sup.default_category:
            preset["category"] = sup.default_category
    return render_template("expenses/form.html", expense=None, form=preset, **_form_context())


@expenses_bp.route("/<int:expense_id>/edit", methods=["GET", "POST"])
@finance_required
def edit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if request.method == "POST":
        error = _apply_form(expense)
        if error:
            flash(error, "danger")
            db.session.rollback()
            return render_template("expenses/form.html", expense=Expense.query.get(expense_id), form=request.form, **_form_context(expense))
        _store_cash_account(expense)
        db.session.commit()
        flash("Expense updated.", "success")
        return redirect(url_for("expenses.list_expenses"))
    return render_template("expenses/form.html", expense=expense, form={}, **_form_context(expense))


@expenses_bp.route("/<int:expense_id>/paid", methods=["POST"])
@finance_required
def mark_paid(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    when = _parse_date(request.form.get("paid_date")) or date.today()
    if expense.supplier_payments:
        import suppliers
        err = suppliers.settle_remaining(expense, when, current_user)    # part-paid: record the rest as a payment
        if err:
            db.session.rollback()
            flash(err, "danger")
            return redirect(request.referrer or url_for("expenses.list_expenses"))
        db.session.commit()
        flash(f"{expense.supplier} marked as paid.", "success")
        return redirect(request.referrer or url_for("expenses.list_expenses"))
    expense.status = "Paid"
    expense.paid_date = when
    db.session.commit()
    flash(f"{expense.supplier} marked as paid.", "success")
    return redirect(request.referrer or url_for("expenses.list_expenses"))


@expenses_bp.route("/<int:expense_id>/delete", methods=["POST"])
@finance_required
def delete_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    import cashbook
    cashbook.forget(f"expense:{expense.id}")
    for p in list(expense.supplier_payments):
        cashbook.forget(f"supplierpay:{p.id}")
    db.session.delete(expense)
    db.session.commit()
    flash("Expense deleted.", "info")
    return redirect(url_for("expenses.list_expenses"))


# ------------------------------------------------------------------ manage categories

def _categories_page():
    ensure_categories()
    rows = ExpenseCategory.query.order_by(ExpenseCategory.order, ExpenseCategory.name).all()
    counts = dict(db.session.query(Expense.category, db.func.count(Expense.id)).group_by(Expense.category).all())
    return render_template("expenses/categories.html", categories=rows, counts=counts, fallback=EXPENSE_FALLBACK_CATEGORY)


@expenses_bp.route("/categories")
@finance_required
def categories():
    return _categories_page()


@expenses_bp.route("/categories/add", methods=["POST"])
@finance_required
def add_category_route():
    name, err = add_category(request.form.get("name"), current_user)
    if err:
        flash(err, "danger")
    else:
        db.session.commit()
        flash(f"Category '{name}' is ready to use.", "success")
    return redirect(url_for("expenses.categories"))


@expenses_bp.route("/categories/<int:category_id>/rename", methods=["POST"])
@finance_required
def rename_category(category_id):
    cat = ExpenseCategory.query.get_or_404(category_id)
    new = _clean_category_name(request.form.get("name"))
    if cat.name == EXPENSE_FALLBACK_CATEGORY:
        flash(f"'{EXPENSE_FALLBACK_CATEGORY}' is the catch-all and cannot be renamed.", "danger")
    elif not new:
        flash("Type a name for the category.", "danger")
    elif new.lower() != cat.name.lower() and ExpenseCategory.query.filter(db.func.lower(ExpenseCategory.name) == new.lower()).first():
        flash(f"There is already a category called '{new}'. Use that one, or switch this one off.", "danger")
    else:
        old = cat.name
        moved = Expense.query.filter_by(category=old).update({"category": new})
        cat.name = new
        db.session.commit()
        flash(f"Renamed '{old}' to '{new}'" + (f" on {moved} expense(s)." if moved else "."), "success")
    return redirect(url_for("expenses.categories"))


@expenses_bp.route("/categories/<int:category_id>/toggle", methods=["POST"])
@finance_required
def toggle_category(category_id):
    cat = ExpenseCategory.query.get_or_404(category_id)
    if cat.name == EXPENSE_FALLBACK_CATEGORY:
        flash(f"'{EXPENSE_FALLBACK_CATEGORY}' is the catch-all and stays on.", "danger")
    else:
        cat.is_active = not cat.is_active
        db.session.commit()
        flash(f"'{cat.name}' is now {'available' if cat.is_active else 'switched off for new expenses (existing ones keep it)'}.", "info")
    return redirect(url_for("expenses.categories"))


@expenses_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@finance_required
def delete_category(category_id):
    cat = ExpenseCategory.query.get_or_404(category_id)
    used = Expense.query.filter_by(category=cat.name).count()
    if cat.name == EXPENSE_FALLBACK_CATEGORY:
        flash(f"'{EXPENSE_FALLBACK_CATEGORY}' is the catch-all and cannot be deleted.", "danger")
    elif used:
        flash(f"'{cat.name}' is used by {used} expense(s), so it cannot be deleted. Switch it off instead, or rename it.", "danger")
    else:
        db.session.delete(cat)
        db.session.commit()
        flash(f"Deleted '{cat.name}'.", "info")
    return redirect(url_for("expenses.categories"))
