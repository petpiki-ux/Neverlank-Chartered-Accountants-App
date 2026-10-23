"""Pages for the Suppliers Management Engine (Firm Finance > Suppliers). The maths is in suppliers.py.
Gated by the same "view_firm_finances" permission as the rest of Firm Finance."""
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

import suppliers as sp
from extensions import db
from finance import _parse_date, _parse_float, category_names, finance_required, fmt_money
from models import (
    Expense, Supplier, SupplierAdvance, SupplierAdvanceRefund, SupplierPayment, INVOICE_CURRENCIES, PAYMENT_METHODS, SUPPLIER_STATUSES, SUPPLIER_STATUS_HELP,
)

suppliers_bp = Blueprint("suppliers", __name__, url_prefix="/firm-finance/suppliers")

FIRM_NAME = "Neverlank Chartered Accountants"


def _ctx(**kw):
    kw.setdefault("ye", None)
    kw.setdefault("active", "suppliers")
    kw.setdefault("today", date.today())
    kw.setdefault("money", fmt_money)
    return kw


def _currency_arg(default="USD"):
    c = request.args.get("currency") or default
    return c if c in INVOICE_CURRENCIES else "USD"


# ------------------------------------------------------------------ list

@suppliers_bp.route("/")
@finance_required
def index():
    q = (request.args.get("q") or "").strip().lower()
    status = request.args.get("status", "")
    rows = []
    owed = {}
    overdue = {}
    today = date.today()
    for e in sp._bills():
        left = sp.outstanding(e)
        if left <= 0 or not e.supplier_id:
            continue
        owed.setdefault(e.supplier_id, {}).setdefault(e.currency, 0.0)
        owed[e.supplier_id][e.currency] += left
        if sp.due_date(e) < today:
            overdue.setdefault(e.supplier_id, {}).setdefault(e.currency, 0.0)
            overdue[e.supplier_id][e.currency] += left
    for s in Supplier.query.order_by(Supplier.name).all():
        if status in SUPPLIER_STATUSES and s.status != status:
            continue
        hay = " ".join(x or "" for x in (s.name, s.trading_name, s.code, s.contact_person, s.email, s.tin, s.vat_number)).lower()
        if q and q not in hay:
            continue
        clr, left_days = sp.clearance_state(s, today)
        rows.append({"s": s, "advance": sp.unapplied_by_currency(s), "owed": {c: sp.r2(v) for c, v in owed.get(s.id, {}).items()},
                     "overdue": {c: sp.r2(v) for c, v in overdue.get(s.id, {}).items()},
                     "spend": sp.year_spend(s, today), "clearance": clr, "clearance_days": left_days})
    unlinked = (db.session.query(Expense.supplier, db.func.count(Expense.id))
                .filter(Expense.supplier_id.is_(None)).group_by(Expense.supplier).all())
    return render_template("suppliers/list.html", **_ctx(
        rows=rows, ov=sp.overview(today), q=request.args.get("q", ""), f_status=status, statuses=SUPPLIER_STATUSES,
        unlinked_names=len(unlinked), unlinked_bills=sum(n for _s, n in unlinked), total=Supplier.query.count()))


@suppliers_bp.route("/import", methods=["POST"])
@finance_required
def import_register():
    created, linked = sp.import_from_register()
    db.session.commit()
    flash(f"{created} supplier(s) created and {linked} bill(s) linked from the Expense register. Open each supplier to add its contact, tax and bank details.", "success")
    return redirect(url_for("suppliers.index"))


# ------------------------------------------------------------------ master record

def _apply_form(s):
    f = request.form
    name = " ".join(f.get("name", "").split())
    if not name:
        return "Enter the supplier's name."
    dup = sp.find_by_name(name, exclude_id=s.id)
    if dup is not None:
        return f"{dup.name} ({dup.code}) is already on file."
    terms = _parse_float(f.get("payment_terms_days"), sp.DEFAULT_TERMS_DAYS)
    if terms is None or terms < 0 or terms > 365:
        return "Payment terms must be between 0 and 365 days."
    wht = _parse_float(f.get("wht_rate"))
    if wht is not None and not (0 <= wht <= 100):
        return "The withholding rate must be between 0 and 100."
    old_name = s.name
    s.name = name
    for field, limit in (("trading_name", 200), ("contact_person", 120), ("phone", 60), ("email", 160), ("country", 60), ("tin", 40),
                         ("vat_number", 40), ("bank_name", 120), ("bank_branch", 120), ("bank_account_name", 160), ("bank_account_number", 60)):
        setattr(s, field, f.get(field, "").strip()[:limit] or None)
    s.address = f.get("address", "").strip() or None
    s.notes = f.get("notes", "").strip() or None
    s.is_vat_registered = bool(f.get("is_vat_registered"))
    s.payment_terms_days = int(terms)
    s.currency = f.get("currency") if f.get("currency") in INVOICE_CURRENCIES else "USD"
    s.default_category = f.get("default_category") or None
    s.tax_clearance_expiry = _parse_date(f.get("tax_clearance_expiry"))
    s.wht_applicable = bool(f.get("wht_applicable"))
    s.wht_rate = wht if s.wht_applicable else None
    s._renamed_from = old_name if s.id and old_name != name else None
    return None


def _form_page(supplier, form):
    return render_template("suppliers/form.html", **_ctx(
        supplier=supplier, form=form or {}, currencies=INVOICE_CURRENCIES, categories=category_names(also=supplier.default_category if supplier else None)))


@suppliers_bp.route("/new", methods=["GET", "POST"])
@finance_required
def new_supplier():
    if request.method == "POST":
        s = Supplier(code=sp.next_code(), created_by_id=current_user.id)
        err = _apply_form(s)
        if err:
            flash(err, "danger")
            return _form_page(None, request.form)
        db.session.add(s)
        db.session.commit()
        flash(f"{s.name} added as {s.code}.", "success")
        return redirect(url_for("suppliers.detail", supplier_id=s.id))
    return _form_page(None, {})


@suppliers_bp.route("/<int:supplier_id>/edit", methods=["GET", "POST"])
@finance_required
def edit_supplier(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    if request.method == "POST":
        err = _apply_form(s)
        if err:
            db.session.rollback()
            flash(err, "danger")
            return _form_page(Supplier.query.get(supplier_id), request.form)
        renamed = getattr(s, "_renamed_from", None)
        if renamed:
            for e in s.bills:          # the bills carry the supplier's name too, so the register and ledger read the same
                e.supplier = s.name
        db.session.commit()
        flash("Supplier updated.", "success")
        return redirect(url_for("suppliers.detail", supplier_id=s.id))
    return _form_page(s, {})


@suppliers_bp.route("/<int:supplier_id>/status", methods=["POST"])
@finance_required
def set_status(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    new = request.form.get("status")
    if new not in SUPPLIER_STATUSES:
        abort(400)
    s.status = new
    s.status_note = request.form.get("status_note", "").strip()[:300] or None if new != "Active" else None
    db.session.commit()
    flash(f"{s.name} is now {new.lower()}.", "info")
    return redirect(url_for("suppliers.detail", supplier_id=s.id))


@suppliers_bp.route("/<int:supplier_id>/delete", methods=["POST"])
@finance_required
def delete_supplier(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    if s.bills or s.payments:
        flash("This supplier has bills or payments, so it cannot be deleted. Put it on hold or block it instead.", "danger")
        return redirect(url_for("suppliers.detail", supplier_id=s.id))
    db.session.delete(s)
    db.session.commit()
    flash("Supplier deleted.", "info")
    return redirect(url_for("suppliers.index"))


# ------------------------------------------------------------------ one supplier

@suppliers_bp.route("/<int:supplier_id>")
@finance_required
def detail(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    today = date.today()
    bills = sorted(s.bills, key=lambda e: (e.expense_date, e.id), reverse=True)
    view = []
    for e in bills:
        view.append({"e": e, "paid": sp.paid_on(e), "left": sp.outstanding(e), "due": sp.due_date(e), "state": sp.bill_state(e, today)})
    pays = sorted([p for p in s.payments if not p.advance_id], key=lambda p: (p.payment_date, p.id), reverse=True)
    advances = sorted(s.advances, key=lambda a: (a.advance_date, a.id), reverse=True)
    clr, left_days = sp.clearance_state(s, today)
    return render_template("suppliers/detail.html", **_ctx(
        s=s, bills=view, pays=pays, advances=[{"a": a, "left": sp.advance_unapplied(a)} for a in advances], held=sp.unapplied_by_currency(s),
        net=sp.net_by_currency(s), owed=sp.balances_by_currency(s), spend=sp.year_spend(s, today), clearance=clr, clearance_days=left_days,
        statuses=SUPPLIER_STATUSES, status_help=SUPPLIER_STATUS_HELP, can_pay=sp.can_be_paid(s), can_bill=sp.can_receive_bills(s)))


# ------------------------------------------------------------------ pay a supplier

def _open_bills(s, currency):
    return [e for e in sorted(s.bills, key=lambda e: (sp.due_date(e), e.expense_date, e.id)) if e.currency == currency and sp.outstanding(e) > 0]


@suppliers_bp.route("/<int:supplier_id>/pay", methods=["GET", "POST"])
@finance_required
def pay(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    bill_id = request.args.get("bill", type=int)
    pre = Expense.query.get(bill_id) if bill_id else None
    currency = request.form.get("currency") if request.method == "POST" else (pre.currency if pre is not None and pre.supplier_id == s.id else _currency_arg(s.currency))
    if currency not in INVOICE_CURRENCIES:
        currency = "USD"
    open_bills = _open_bills(s, currency)

    if request.method == "POST":
        allocations = []
        for e in open_bills:
            amt = _parse_float(request.form.get(f"amt_{e.id}"))
            if amt is not None and amt > 0:
                allocations.append((e, amt))
        batch, made, err = sp.record_payment(
            s, allocations, _parse_date(request.form.get("payment_date")), request.form.get("method"),
            request.form.get("reference"), request.form.get("notes"), current_user, request.form.get("cash_account_id"))
        if err:
            db.session.rollback()
            flash(err, "danger")
        else:
            db.session.commit()
            total = sum(p.amount for p in made)
            flash(f"Payment of {currency} {total:,.2f} recorded to {s.name} ({batch}).", "success")
            return redirect(url_for("suppliers.remittance", batch=batch))
    problem = sp.status_problem(s, "pay")
    return render_template("suppliers/pay.html", **_ctx(
        advance_available=sp.unapplied_total(s, None, currency),
        s=s, currency=currency, currencies=INVOICE_CURRENCIES, open_bills=[{"e": e, "left": sp.outstanding(e), "due": sp.due_date(e)} for e in open_bills],
        methods=PAYMENT_METHODS, pre_id=pre.id if pre is not None and pre.supplier_id == s.id else None, problem=problem,
        form=request.form if request.method == "POST" else {}))


@suppliers_bp.route("/payments")
@finance_required
def payments():
    q = SupplierPayment.query.filter(SupplierPayment.advance_id.is_(None))
    sid = request.args.get("supplier", type=int)
    if sid:
        q = q.filter(SupplierPayment.supplier_id == sid)
    lo, hi = _parse_date(request.args.get("from")), _parse_date(request.args.get("to"))
    if lo:
        q = q.filter(SupplierPayment.payment_date >= lo)
    if hi:
        q = q.filter(SupplierPayment.payment_date <= hi)
    rows = q.order_by(SupplierPayment.payment_date.desc(), SupplierPayment.id.desc()).all()
    totals = {}
    for p in rows:
        totals[p.currency] = totals.get(p.currency, 0.0) + (p.amount or 0.0)
    return render_template("suppliers/payments.html", **_ctx(
        rows=rows, totals=totals, supplier_list=Supplier.query.order_by(Supplier.name).all(), f_supplier=sid,
        f_from=request.args.get("from", ""), f_to=request.args.get("to", "")))


@suppliers_bp.route("/payments/<int:payment_id>/delete", methods=["POST"])
@finance_required
def delete_payment(payment_id):
    p = SupplierPayment.query.get_or_404(payment_id)
    sid = p.supplier_id
    sp.delete_payment(p)
    db.session.commit()
    flash("Payment deleted; the bill shows what is still owed.", "info")
    return redirect(request.referrer or url_for("suppliers.detail", supplier_id=sid))


@suppliers_bp.route("/remittance/<batch>")
@finance_required
def remittance(batch):
    rows = SupplierPayment.query.filter_by(batch_ref=batch).order_by(SupplierPayment.id).all()
    if not rows:
        abort(404)
    s = rows[0].supplier
    return render_template("suppliers/remittance.html", **_ctx(
        s=s, rows=rows, batch=batch, total=sp.r2(sum(p.amount for p in rows)), currency=rows[0].currency, firm_title=FIRM_NAME,
        on=rows[0].payment_date, method=rows[0].method, reference=rows[0].reference))


# ------------------------------------------------------------------ statement

@suppliers_bp.route("/<int:supplier_id>/statement")
@finance_required
def statement(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    today = date.today()
    owed = sp.balances_by_currency(s)
    billed_in = {e.currency for e in s.bills} | {a.currency for a in s.advances}
    default_cur = s.currency if s.currency in billed_in or not billed_in else sorted(billed_in)[0]
    currency = _currency_arg(default_cur)
    end = _parse_date(request.args.get("to")) or today
    start = _parse_date(request.args.get("from")) or date(end.year, 1, 1)
    if start > end:
        start, end = end, start
    st = sp.statement(s, start, end, currency)
    theirs = _parse_float(request.args.get("their_balance"))
    diff = sp.r2(theirs - st["closing"]) if theirs is not None else None
    if request.args.get("csv"):
        return Response(sp.statement_csv(s, start, end, currency, st), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=statement-{s.code}-{end.isoformat()}.csv"})
    tpl = "suppliers/statement_print.html" if request.args.get("print") else "suppliers/statement.html"
    return render_template(tpl, **_ctx(s=s, st=st, currency=currency, currencies=INVOICE_CURRENCIES, start=start, end=end, theirs=theirs, diff=diff,
                                       firm_title=FIRM_NAME, owed=owed))


# ------------------------------------------------------------------ ageing

@suppliers_bp.route("/ageing")
@finance_required
def ageing():
    currency = _currency_arg("USD")
    as_of = _parse_date(request.args.get("as_of")) or date.today()
    data = sp.ageing(as_of, currency)
    if request.args.get("csv"):
        return Response(sp.ageing_csv(as_of, currency, data), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=creditors-ageing-{as_of.isoformat()}-{currency}.csv"})
    return render_template("suppliers/ageing.html", **_ctx(data=data, currency=currency, currencies=INVOICE_CURRENCIES, as_of=as_of))


# ------------------------------------------------------------------ advances paid to suppliers

@suppliers_bp.route("/<int:supplier_id>/advance/new", methods=["GET", "POST"])
@finance_required
def new_advance(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    problem = sp.status_problem(s, "pay")
    if request.method == "POST":
        adv, err = sp.record_advance(
            s, _parse_float(request.form.get("amount"), 0.0), request.form.get("currency"), _parse_date(request.form.get("advance_date")),
            request.form.get("method"), request.form.get("reference"), request.form.get("purpose"), current_user, request.form.get("cash_account_id"))
        if err:
            db.session.rollback()
            flash(err, "danger")
        else:
            db.session.commit()
            flash(f"Advance of {adv.currency} {adv.amount:,.2f} to {s.name} recorded. It stays an asset until it is applied to a bill or refunded.", "success")
            return redirect(url_for("suppliers.advance", advance_id=adv.id))
    return render_template("suppliers/advance_form.html", **_ctx(
        s=s, form=request.form if request.method == "POST" else {}, currencies=INVOICE_CURRENCIES, methods=PAYMENT_METHODS, problem=problem))


@suppliers_bp.route("/advances")
@finance_required
def advances():
    rows = []
    totals = {}
    for a in SupplierAdvance.query.order_by(SupplierAdvance.advance_date.desc(), SupplierAdvance.id.desc()).all():
        left = sp.advance_unapplied(a)
        rows.append({"a": a, "left": left})
        if left:
            totals[a.currency] = totals.get(a.currency, 0.0) + left
    return render_template("suppliers/advances.html", **_ctx(rows=rows, totals=totals, active="suppliers-advances"))


@suppliers_bp.route("/advances/<int:advance_id>")
@finance_required
def advance(advance_id):
    a = SupplierAdvance.query.get_or_404(advance_id)
    s = a.supplier
    open_bills = [{"e": e, "left": sp.outstanding(e), "due": sp.due_date(e)}
                  for e in sorted(s.bills, key=lambda e: (sp.due_date(e), e.expense_date, e.id)) if e.currency == a.currency and sp.outstanding(e) > 0]
    apps = sorted(a.applications, key=lambda p: (p.payment_date, p.id))
    return render_template("suppliers/advance.html", **_ctx(
        a=a, s=s, left=sp.advance_unapplied(a), apps=apps, refunds=sorted(a.refunds, key=lambda r: (r.refunded_on, r.id)), open_bills=open_bills,
        methods=PAYMENT_METHODS, form={}))


@suppliers_bp.route("/advances/<int:advance_id>/apply", methods=["POST"])
@finance_required
def apply_advance(advance_id):
    a = SupplierAdvance.query.get_or_404(advance_id)
    allocations = []
    for e in a.supplier.bills:
        amt = _parse_float(request.form.get(f"amt_{e.id}"))
        if amt is not None and amt > 0:
            allocations.append((e, amt))
    _b, made, err = sp.apply_advance(a, allocations, _parse_date(request.form.get("applied_on")), current_user)
    if err:
        db.session.rollback()
        flash(err, "danger")
    else:
        db.session.commit()
        flash(f"{a.currency} {sum(p.amount for p in made):,.2f} of the advance applied to {len(made)} bill(s).", "success")
    return redirect(url_for("suppliers.advance", advance_id=a.id))


@suppliers_bp.route("/<int:supplier_id>/advances/auto-apply", methods=["POST"])
@finance_required
def auto_apply(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    currency = request.form.get("currency") if request.form.get("currency") in INVOICE_CURRENCIES else "USD"
    done, err = sp.auto_apply(s, currency, _parse_date(request.form.get("applied_on")) or date.today(), current_user)
    if err:
        db.session.rollback()
        flash(err, "danger")
    elif done <= 0:
        flash("Nothing to apply: no unused advance, or no open bill dated on or after the advance.", "info")
    else:
        db.session.commit()
        flash(f"{currency} {done:,.2f} of advances applied to the oldest open bills.", "success")
    return redirect(url_for("suppliers.detail", supplier_id=s.id))


@suppliers_bp.route("/advances/<int:advance_id>/refund", methods=["POST"])
@finance_required
def refund_advance(advance_id):
    a = SupplierAdvance.query.get_or_404(advance_id)
    rf, err = sp.record_refund(a, _parse_float(request.form.get("amount"), 0.0), _parse_date(request.form.get("refunded_on")),
                               request.form.get("method"), request.form.get("reference"), current_user, request.form.get("cash_account_id"))
    if err:
        db.session.rollback()
        flash(err, "danger")
    else:
        db.session.commit()
        flash(f"Refund of {a.currency} {rf.amount:,.2f} from {a.supplier.name} recorded.", "success")
    return redirect(url_for("suppliers.advance", advance_id=a.id))


@suppliers_bp.route("/advances/refunds/<int:refund_id>/delete", methods=["POST"])
@finance_required
def delete_refund(refund_id):
    rf = SupplierAdvanceRefund.query.get_or_404(refund_id)
    aid = rf.advance_id
    sp.delete_refund(rf)
    db.session.commit()
    flash("Refund deleted.", "info")
    return redirect(url_for("suppliers.advance", advance_id=aid))


@suppliers_bp.route("/advances/<int:advance_id>/delete", methods=["POST"])
@finance_required
def delete_advance(advance_id):
    a = SupplierAdvance.query.get_or_404(advance_id)
    sid = a.supplier_id
    err = sp.delete_advance(a)
    if err:
        db.session.rollback()
        flash(err, "danger")
        return redirect(url_for("suppliers.advance", advance_id=advance_id))
    db.session.commit()
    flash("Advance deleted.", "info")
    return redirect(url_for("suppliers.detail", supplier_id=sid))
