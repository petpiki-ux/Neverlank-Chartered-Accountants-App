"""The firm's Standard Chart of Accounts ("Neverlank Standard") - a single
firm-wide, numbered list of accounts (see models.StandardChartOfAccounts),
seeded once with a drafted starting point (seed.seed_chart_of_accounts) and
fully firm-editable from here afterward - same "editable starting point"
convention as QPD Instalment Rates/Statutory Deadlines.

This is deliberately separate from the Accounting module's own per-
engagement "Chart of Accounts" (models.GLAccount, in accounting.py) - that
one is a live General Ledger structure a preparer builds up for one
bookkeeping engagement/period. This one is a firm-wide reference list used
to speed up mapping a CLIENT'S OWN trial balance account names (however
they're actually named) to both an IAS 1 category and a firm-standard
account number, remembered per client via COAMapping - see qpd.py and
engagements.py's trial balance mapping routes, which both offer this list
as a picker alongside the plain IAS 1 category dropdown.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import StandardChartOfAccounts, user_has_permission
import financials as fin

standard_coa_bp = Blueprint("standard_coa", __name__, url_prefix="/chart-of-accounts")


def _ensure_coa_access():
    if not user_has_permission(current_user, "manage_chart_of_accounts"):
        abort(403)


def coa_account_choices():
    """Active Standard Chart of Accounts entries, ordered for a <select> -
    (account_number, "<number> - <name>", fs_category) tuples so a template
    can both display and JS-autofill the matching IAS 1 category when one
    is picked. Used by qpd.py/engagements.py's trial balance mapping forms,
    not just this module's own management page."""
    accounts = StandardChartOfAccounts.query.filter_by(is_active=True).order_by(StandardChartOfAccounts.order, StandardChartOfAccounts.account_number).all()
    return [(a.account_number, f"{a.account_number} - {a.account_name}", a.fs_category) for a in accounts]


@standard_coa_bp.route("/", methods=["GET", "POST"])
@login_required
def list_accounts():
    _ensure_coa_access()
    if request.method == "POST":
        account_number = request.form.get("account_number", "").strip()
        account_name = request.form.get("account_name", "").strip()
        fs_category = request.form.get("fs_category", "").strip()
        if not (account_number and account_name and fs_category in fin.CATEGORY_BY_CODE):
            flash("Enter an account number, a name, and a valid IAS 1 category.", "danger")
        elif StandardChartOfAccounts.query.filter_by(account_number=account_number).first():
            flash(f"Account number {account_number} already exists.", "danger")
        else:
            max_order = max([a.order for a in StandardChartOfAccounts.query.all()], default=-1)
            db.session.add(StandardChartOfAccounts(
                account_number=account_number, account_name=account_name, fs_category=fs_category,
                notes=request.form.get("notes", "").strip(), order=max_order + 1,
            ))
            db.session.commit()
            flash(f"Account {account_number} added.", "success")
        return redirect(url_for("standard_coa.list_accounts"))
    accounts = StandardChartOfAccounts.query.order_by(StandardChartOfAccounts.order, StandardChartOfAccounts.account_number).all()
    return render_template(
        "standard_coa/list.html", accounts=accounts, fs_category_choices=fin.category_choices(),
        can_manage=user_has_permission(current_user, "manage_chart_of_accounts"),
    )


@standard_coa_bp.route("/<int:account_id>/update", methods=["POST"])
@login_required
def update_account(account_id):
    _ensure_coa_access()
    account = StandardChartOfAccounts.query.get_or_404(account_id)
    new_number = request.form.get("account_number", "").strip()
    if new_number and new_number != account.account_number:
        if StandardChartOfAccounts.query.filter(StandardChartOfAccounts.account_number == new_number, StandardChartOfAccounts.id != account.id).first():
            flash(f"Account number {new_number} is already used by another account.", "danger")
            return redirect(url_for("standard_coa.list_accounts"))
        account.account_number = new_number
    account.account_name = request.form.get("account_name", "").strip() or account.account_name
    category = request.form.get("fs_category", "").strip()
    if category in fin.CATEGORY_BY_CODE:
        account.fs_category = category
    account.notes = request.form.get("notes", "").strip()
    account.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    flash(f"Account {account.account_number} updated.", "success")
    return redirect(url_for("standard_coa.list_accounts"))


@standard_coa_bp.route("/<int:account_id>/delete", methods=["POST"])
@login_required
def delete_account(account_id):
    _ensure_coa_access()
    account = StandardChartOfAccounts.query.get_or_404(account_id)
    db.session.delete(account)
    db.session.commit()
    flash("Account removed.", "success")
    return redirect(url_for("standard_coa.list_accounts"))
