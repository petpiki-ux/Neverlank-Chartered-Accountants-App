"""Suppliers Management Engine (Finance > Firm Finance > Suppliers).

What it adds
------------
* a Supplier master (contact, tax numbers, payment terms, bank details, status, tax-clearance expiry);
* supplier bills - these are the existing Expense-register records, now linked to a supplier (Expense.supplier_id);
* payments to suppliers against bills, in full or in part (SupplierPayment), with a remittance advice;
* a supplier ledger: statements, ageing by due date, and a check of a supplier's own statement against ours.

Nothing here posts to the ledger by itself. A bill already posts Dr Expense / Cr Trade payables from the Expense
register, and a payment reaches the ledger through the cash book (cashbook.collect_transactions reads SupplierPayment
as source "Expense": Dr Trade payables / Cr Bank). So the supplier ledger, the cash book, the analytics and the
financial statements are one set of numbers.

A bill's settlement history
---------------------------
* bills with payment rows: each row settles part of the bill on its own date;
* bills marked Paid the old way (no rows): settled in full on the paid date;
* otherwise unsettled.
The bill's stored status follows its payment rows (Paid when fully settled, otherwise Unpaid), so every older screen
that only knows Paid / Unpaid keeps working.

Withholding tax and VAT are NOT calculated or posted here. The master only records whether the firm treats a supplier
as subject to withholding and the rate the firm typed - for reference, not verified by the app.
"""
import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from extensions import db
from models import (
    Expense, Supplier, SupplierAdvance, SupplierAdvanceRefund, SupplierPayment, INVOICE_CURRENCIES, PAYMENT_METHODS, SUPPLIER_STATUSES,
)

CENT = 0.005
AGEING_LABELS = ["Not yet due", "1-30 days overdue", "31-60 days overdue", "61-90 days overdue", "Over 90 days overdue"]
CLEARANCE_WARN_DAYS = 30
DEFAULT_TERMS_DAYS = 30


def r2(v):
    return round(float(v or 0.0), 2)


# ------------------------------------------------------------------ master data

def next_code():
    """SUP-0001, SUP-0002 ... one past the highest number already used."""
    top = 0
    for (code,) in db.session.query(Supplier.code).all():
        digits = "".join(ch for ch in (code or "") if ch.isdigit())
        if digits:
            top = max(top, int(digits))
    return f"SUP-{top + 1:04d}"


def find_by_name(name, exclude_id=None):
    name = " ".join((name or "").split()).lower()
    if not name:
        return None
    for s in Supplier.query.all():
        if s.id != exclude_id and " ".join((s.name or "").split()).lower() == name:
            return s
    return None


def clearance_state(supplier, today=None):
    """('none'|'ok'|'soon'|'expired', days left or None) for the supplier's tax-clearance expiry."""
    today = today or date.today()
    d = supplier.tax_clearance_expiry
    if not d:
        return "none", None
    left = (d - today).days
    if left < 0:
        return "expired", left
    if left <= CLEARANCE_WARN_DAYS:
        return "soon", left
    return "ok", left


def can_receive_bills(supplier):
    return supplier is None or supplier.status != "Blocked"


def can_be_paid(supplier):
    return supplier is None or supplier.status == "Active"


def status_problem(supplier, action):
    """A message when `action` ('bill' or 'pay') is not allowed for this supplier's status, else None."""
    if supplier is None:
        return None
    if action == "bill" and supplier.status == "Blocked":
        return f"{supplier.name} is blocked, so no new bills can be recorded against it." + (f" ({supplier.status_note})" if supplier.status_note else "")
    if action == "pay" and supplier.status != "Active":
        return f"{supplier.name} is {supplier.status.lower()}, so payments are refused." + (f" ({supplier.status_note})" if supplier.status_note else "")
    return None


# ------------------------------------------------------------------ a bill's settlement history

def settlements(expense):
    """[(date, amount, label, SupplierPayment or None)] - how this bill has been settled, oldest first."""
    rows = sorted(expense.supplier_payments, key=lambda p: (p.payment_date, p.id))
    if rows:
        return [(p.payment_date, r2(p.amount), "Advance applied" if p.advance_id else (p.reference or "Payment"), p) for p in rows]
    if expense.status == "Paid":
        return [(expense.paid_date or expense.expense_date, r2(expense.amount), "Marked paid", None)]
    return []


def paid_on(expense, as_of=None):
    return r2(sum(a for d, a, _l, _p in settlements(expense) if as_of is None or d <= as_of))


def outstanding(expense, as_of=None):
    """What is still owed on the bill at the end of `as_of` (today's position when None). 0 before the bill's own date."""
    if as_of is not None and expense.expense_date > as_of:
        return 0.0
    left = r2((expense.amount or 0.0) - paid_on(expense, as_of))
    return left if left > CENT else 0.0


def due_date(expense):
    """The bill's due date; else the bill date plus the supplier's terms; else the bill date."""
    if expense.due_date:
        return expense.due_date
    sup = expense.supplier_rec
    if sup is not None and sup.payment_terms_days:
        return expense.expense_date + timedelta(days=int(sup.payment_terms_days))
    return expense.expense_date


def bill_state(expense, today=None):
    """'Paid' | 'Part-paid' | 'Overdue' | 'Unpaid' for display."""
    today = today or date.today()
    left = outstanding(expense)
    if left <= 0:
        return "Paid"
    if due_date(expense) < today:
        return "Overdue"
    return "Part-paid" if paid_on(expense) > CENT else "Unpaid"


def sync_expense(expense):
    """Make the bill's Paid/Unpaid status and paid date follow its payment rows (no-op for a bill with no rows)."""
    rows = list(expense.supplier_payments)
    if not rows:
        return
    total = sum(r2(p.amount) for p in rows)
    if total >= r2(expense.amount) - CENT:
        expense.status = "Paid"
        expense.paid_date = max(p.payment_date for p in rows)
    else:
        expense.status = "Unpaid"
        expense.paid_date = None


# ------------------------------------------------------------------ recording a payment

def _batch_ref(on):
    stamp = f"PAY-{on.strftime('%y%m%d')}-"
    n = SupplierPayment.query.filter(SupplierPayment.batch_ref.like(stamp + "%")).with_entities(SupplierPayment.batch_ref).distinct().count()
    return f"{stamp}{n + 1:03d}"


def record_payment(supplier, allocations, on, method, reference, notes, user, cash_account_id=None):
    """Pay `supplier` against bills. allocations = [(Expense, amount)]. Returns (batch_ref, [SupplierPayment], error).
    Nothing is committed on error. The caller commits."""
    import cashbook
    problem = status_problem(supplier, "pay")
    if problem:
        return None, [], problem
    allocations = [(e, r2(a)) for e, a in allocations if a is not None and r2(a) > 0]
    if not allocations:
        return None, [], "Enter an amount against at least one bill."
    currencies = {e.currency for e, _a in allocations}
    if len(currencies) > 1:
        return None, [], "A payment is in one currency. Pay the USD and ZWG bills separately."
    currency = currencies.pop()
    if on is None:
        return None, [], "Enter the payment date."
    if method and method not in PAYMENT_METHODS:
        method = "Other"
    for e, amt in allocations:
        if e.supplier_id != supplier.id:
            return None, [], "A bill chosen does not belong to this supplier."
        if on < e.expense_date:
            return None, [], f"The payment is dated before the bill of {e.expense_date.strftime('%d %b %Y')} ({e.reference or e.description or 'bill'})."
        left = outstanding(e)
        if amt > left + CENT:
            return None, [], f"{e.currency} {amt:,.2f} is more than the {e.currency} {left:,.2f} still owed on {e.reference or e.description or 'that bill'}."
    acct_id, bad = cashbook.parse_choice(cash_account_id, currency)
    if bad:
        return None, [], bad
    batch = _batch_ref(on)
    made = []
    for e, amt in allocations:
        p = SupplierPayment(supplier=supplier, expense=e, payment_date=on, amount=amt, currency=currency,
                            method=method or None, reference=(reference or "").strip() or None, batch_ref=batch,
                            notes=(notes or "").strip()[:300] or None, created_by_id=getattr(user, "id", None))
        db.session.add(p)
        db.session.flush()
        if cashbook.can_choose_account():
            cashbook.set_account(f"supplierpay:{p.id}", acct_id)
        made.append(p)
    for e, _a in allocations:
        sync_expense(e)
    return batch, made, None


def delete_payment(payment):
    """Remove one payment and put its bill back to what the remaining payments say. Does not commit."""
    import cashbook
    expense = payment.expense
    cashbook.forget(f"supplierpay:{payment.id}")
    if payment in expense.supplier_payments:
        expense.supplier_payments.remove(payment)
    db.session.delete(payment)
    db.session.flush()
    if expense.supplier_payments:
        sync_expense(expense)
    elif expense.status == "Paid":
        # the status was derived from the rows that are now gone
        expense.status = "Unpaid"
        expense.paid_date = None


def settle_remaining(expense, on, user):
    """'Mark paid' on a bill that already has part-payments: record the rest as a payment. Returns error or None."""
    if expense.supplier_rec is None:
        return "This bill is not linked to a supplier."
    left = outstanding(expense)
    if left <= 0:
        return None
    _b, _made, err = record_payment(expense.supplier_rec, [(expense, left)], on, None, "Settled", None, user)
    return err


# ------------------------------------------------------------------ advances paid to suppliers

def advance_unapplied(adv, as_of=None):
    """What is left of an advance at the end of `as_of` (today's position when None): paid less applied to bills less refunded."""
    if as_of is not None and adv.advance_date > as_of:
        return 0.0
    used = sum(x.amount or 0.0 for x in adv.applications if as_of is None or x.payment_date <= as_of)
    back = sum(x.amount or 0.0 for x in adv.refunds if as_of is None or x.refunded_on <= as_of)
    left = r2((adv.amount or 0.0) - used - back)
    return left if left > CENT else 0.0


def unapplied_total(supplier, as_of=None, currency=None):
    return r2(sum(advance_unapplied(a, as_of) for a in supplier.advances if currency is None or a.currency == currency))


def unapplied_by_currency(supplier, as_of=None):
    out = defaultdict(float)
    for a in supplier.advances:
        out[a.currency] += advance_unapplied(a, as_of)
    return {c: r2(v) for c, v in out.items() if v > CENT}


def net_by_currency(supplier, as_of=None):
    """Owed on bills less advances not yet used, per currency. Negative = the supplier holds our money."""
    owed = balances_by_currency(supplier, as_of)
    adv = unapplied_by_currency(supplier, as_of)
    out = {}
    for c in set(owed) | set(adv):
        v = r2(owed.get(c, 0.0) - adv.get(c, 0.0))
        if abs(v) > CENT:
            out[c] = v
    return out


def advance_label(adv):
    return f"{adv.reference or 'ADV-' + str(adv.id)} ({adv.advance_date.strftime('%d %b %Y')})"


def record_advance(supplier, amount, currency, on, method, reference, purpose, user, cash_account_id=None):
    """Pay `supplier` in advance. Returns (SupplierAdvance, error). The caller commits."""
    import cashbook
    problem = status_problem(supplier, "pay")
    if problem:
        return None, problem
    amount = r2(amount)
    if amount <= 0:
        return None, "Enter the amount paid in advance."
    if on is None:
        return None, "Enter the date paid."
    if currency not in INVOICE_CURRENCIES:
        return None, "Choose the currency."
    if method and method not in PAYMENT_METHODS:
        method = "Other"
    acct_id, bad = cashbook.parse_choice(cash_account_id, currency)
    if bad:
        return None, bad
    adv = SupplierAdvance(supplier=supplier, advance_date=on, amount=amount, currency=currency, method=method or None,
                          reference=(reference or "").strip() or None, purpose=(purpose or "").strip()[:300] or None,
                          created_by_id=getattr(user, "id", None))
    db.session.add(adv)
    db.session.flush()
    if cashbook.can_choose_account():
        cashbook.set_account(f"supplieradv:{adv.id}", acct_id)
    return adv, None


def apply_advance(adv, allocations, on, user):
    """Settle bills from an advance (no cash moves). allocations = [(Expense, amount)]. Returns (batch_ref, [rows], error)."""
    supplier = adv.supplier
    if supplier.status == "Blocked":
        return None, [], status_problem(supplier, "bill").replace("no new bills can be recorded against it", "advances cannot be applied")
    allocations = [(e, r2(a)) for e, a in allocations if a is not None and r2(a) > 0]
    if not allocations:
        return None, [], "Enter an amount against at least one bill."
    if on is None:
        return None, [], "Enter the date the advance is applied."
    if on < adv.advance_date:
        return None, [], "An advance cannot be applied before the date it was paid."
    total = r2(sum(a for _e, a in allocations))
    left = advance_unapplied(adv)
    if total > left + CENT:
        return None, [], f"{adv.currency} {total:,.2f} is more than the {adv.currency} {left:,.2f} left on this advance."
    for e, amt in allocations:
        if e.supplier_id != supplier.id:
            return None, [], "A bill chosen does not belong to this supplier."
        if e.currency != adv.currency:
            return None, [], f"The advance is in {adv.currency} but a bill is in {e.currency}."
        if on < e.expense_date:
            return None, [], f"The advance is applied before the bill of {e.expense_date.strftime('%d %b %Y')} ({e.reference or e.description or 'bill'})."
        owed = outstanding(e)
        if amt > owed + CENT:
            return None, [], f"{e.currency} {amt:,.2f} is more than the {e.currency} {owed:,.2f} still owed on {e.reference or e.description or 'that bill'}."
    batch = _batch_ref(on).replace("PAY-", "APP-", 1)
    made = []
    for e, amt in allocations:
        p = SupplierPayment(supplier=supplier, expense=e, advance=adv, payment_date=on, amount=amt, currency=adv.currency,
                            method="Advance applied", reference=adv.reference, batch_ref=batch, created_by_id=getattr(user, "id", None))
        db.session.add(p)
        db.session.flush()
        made.append(p)
    for e, _a in allocations:
        sync_expense(e)
    return batch, made, None


def auto_apply(supplier, currency, on, user):
    """Use the supplier's unapplied advances (oldest first) on its open bills (earliest due first). Returns (amount applied, error)."""
    applied = 0.0
    bills = sorted([e for e in supplier.bills if e.currency == currency and outstanding(e) > 0], key=lambda e: (due_date(e), e.expense_date, e.id))
    for adv in sorted([a for a in supplier.advances if a.currency == currency], key=lambda a: (a.advance_date, a.id)):
        for e in bills:
            room, owed = advance_unapplied(adv), outstanding(e)
            if room <= CENT:
                break
            use = min(room, owed)
            if use <= CENT or on < adv.advance_date or on < e.expense_date:
                continue
            _b, _m, err = apply_advance(adv, [(e, use)], on, user)
            if err:
                return applied, err
            applied += use
    return r2(applied), None


def record_refund(adv, amount, on, method, reference, user, cash_account_id=None):
    """The supplier pays back part of an advance. Returns (SupplierAdvanceRefund, error). The caller commits."""
    import cashbook
    amount = r2(amount)
    left = advance_unapplied(adv)
    if amount <= 0:
        return None, "Enter the amount refunded."
    if on is None or on < adv.advance_date:
        return None, "Enter a refund date on or after the advance."
    if amount > left + CENT:
        return None, f"{adv.currency} {amount:,.2f} is more than the {adv.currency} {left:,.2f} left on this advance."
    if method and method not in PAYMENT_METHODS:
        method = "Other"
    acct_id, bad = cashbook.parse_choice(cash_account_id, adv.currency)
    if bad:
        return None, bad
    rf = SupplierAdvanceRefund(advance=adv, refunded_on=on, amount=amount, method=method or None,
                               reference=(reference or "").strip() or None, created_by_id=getattr(user, "id", None))
    db.session.add(rf)
    db.session.flush()
    if cashbook.can_choose_account():
        cashbook.set_account(f"supplierref:{rf.id}", acct_id)
    return rf, None


def delete_refund(rf):
    import cashbook
    cashbook.forget(f"supplierref:{rf.id}")
    db.session.delete(rf)


def delete_advance(adv):
    """Only an advance nothing has been taken from. Returns an error message or None. Does not commit."""
    import cashbook
    if adv.applications or adv.refunds:
        return "Part of this advance has been applied or refunded. Delete those first."
    cashbook.forget(f"supplieradv:{adv.id}")
    db.session.delete(adv)
    return None


# ------------------------------------------------------------------ links to the Expense register

def link_expense(expense, supplier):
    expense.supplier_id = supplier.id if supplier else None
    if supplier is not None:
        expense.supplier = supplier.name


def import_from_register():
    """Create a supplier for every distinct 'Paid to' name in the Expense register that has none, and link the bills.
    Returns (created, linked)."""
    created = linked = 0
    by_name = {" ".join((s.name or "").split()).lower(): s for s in Supplier.query.all()}
    last_cat = {}
    for e in Expense.query.order_by(Expense.expense_date, Expense.id).all():
        key = " ".join((e.supplier or "").split()).lower()
        if not key:
            continue
        last_cat[key] = e.category
    for e in Expense.query.order_by(Expense.expense_date, Expense.id).all():
        if e.supplier_id:
            continue
        key = " ".join((e.supplier or "").split()).lower()
        if not key:
            continue
        s = by_name.get(key)
        if s is None:
            s = Supplier(code=next_code(), name=" ".join(e.supplier.split()), currency=e.currency if e.currency in INVOICE_CURRENCIES else "USD",
                         default_category=last_cat.get(key), payment_terms_days=DEFAULT_TERMS_DAYS)
            db.session.add(s)
            db.session.flush()
            by_name[key] = s
            created += 1
        e.supplier_id = s.id
        linked += 1
    return created, linked


# ------------------------------------------------------------------ supplier ledger

def _bills(supplier=None, currency=None):
    from sqlalchemy.orm import selectinload
    q = Expense.query.options(selectinload(Expense.supplier_payments), selectinload(Expense.supplier_rec))
    if supplier is not None:
        q = q.filter(Expense.supplier_id == supplier.id)
    if currency:
        q = q.filter(Expense.currency == currency)
    return q.order_by(Expense.expense_date, Expense.id).all()


def balance(supplier, as_of=None, currency=None):
    return r2(sum(outstanding(e, as_of) for e in _bills(supplier, currency)))


def balances_by_currency(supplier, as_of=None):
    out = defaultdict(float)
    for e in _bills(supplier):
        out[e.currency] += outstanding(e, as_of)
    return {c: r2(v) for c, v in out.items() if abs(v) > CENT}


def statement(supplier, start, end, currency):
    """Supplier statement for one currency between two dates (inclusive).
    Returns dict(opening, lines, closing, billed, paid). Each line: date, kind ('Bill'|'Payment'), ref, description,
    bill (increase), payment (decrease), balance, expense_id."""
    events = []
    for e in _bills(supplier, currency):
        events.append((e.expense_date, 0, e.id, "Bill", e.reference or f"EXP-{e.id}",
                       " - ".join(x for x in (e.category, e.description) if x), r2(e.amount), 0.0, e.id))
        for d, a, label, p in settlements(e):
            if p is not None and p.advance_id:
                events.append((d, 1, p.id, "Memo", p.reference or p.batch_ref or "", f"Advance applied to bill {e.reference or 'EXP-' + str(e.id)} (already counted when paid)", 0.0, 0.0, e.id))
                continue
            events.append((d, 1, p.id if p else 0, "Payment", (p.reference or p.batch_ref) if p else (e.reference or f"EXP-{e.id}"),
                           f"{label} - bill {e.reference or 'EXP-' + str(e.id)}", 0.0, a, e.id))
    # money paid to the supplier in advance reduces what we owe (the account can go negative: they hold our money);
    # applying it to a bill moves nothing again, so those lines are memo lines and a refund puts the money back
    for a in supplier.advances:
        if a.currency != currency:
            continue
        events.append((a.advance_date, 2, a.id, "Advance", a.reference or f"ADV-{a.id}", "Advance paid" + (f" - {a.purpose}" if a.purpose else ""), 0.0, r2(a.amount), 0))
        for rf in a.refunds:
            events.append((rf.refunded_on, 3, rf.id, "Refund", rf.reference or "Refund", f"Advance refunded to us - {a.reference or 'ADV-' + str(a.id)}", r2(rf.amount), 0.0, 0))
    events.sort(key=lambda t: (t[0], t[1], t[2]))
    opening = r2(sum(b - p for d, _o, _i, _k, _r, _ds, b, p, _e in events if d < start))
    lines, running = [], opening
    billed = paid = 0.0
    for d, _o, _i, kind, ref, desc, b, p, eid in events:
        if d < start or d > end:
            continue
        running = r2(running + b - p)
        billed += b
        paid += p
        lines.append({"date": d, "kind": kind, "ref": ref or "", "description": desc, "bill": b, "payment": p, "balance": running, "expense_id": eid})
    return {"opening": opening, "lines": lines, "closing": running, "billed": r2(billed), "paid": r2(paid)}


def ageing(as_of, currency):
    """Creditors ageing by due date at `as_of`, one row per supplier (bills not linked to a supplier are grouped by the
    name typed). Returns dict(rows, totals, grand). Each row: supplier (or None), name, buckets[5], total, bills."""
    groups = {}
    for e in _bills(currency=currency):
        left = outstanding(e, as_of)
        if left <= 0:
            continue
        key = ("s", e.supplier_id) if e.supplier_id else ("n", " ".join((e.supplier or "").split()).lower())
        g = groups.setdefault(key, {"supplier": e.supplier_rec if e.supplier_id else None, "name": e.supplier_rec.name if e.supplier_id else e.supplier,
                                    "buckets": [0.0] * 5, "total": 0.0, "bills": []})
        days = (as_of - due_date(e)).days
        idx = 0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4
        g["buckets"][idx] += left
        g["total"] += left
        g["bills"].append({"expense": e, "left": left, "due": due_date(e), "days_over": max(days, 0), "bucket": idx})
    for sup in Supplier.query.all():            # money already paid to the supplier in advance, not yet used
        held = unapplied_total(sup, as_of, currency)
        if held > 0:
            g = groups.setdefault(("s", sup.id), {"supplier": sup, "name": sup.name, "buckets": [0.0] * 5, "total": 0.0, "bills": []})
            g["advance"] = held
    rows = sorted(groups.values(), key=lambda g: (-g["total"], g["name"].lower()))
    totals = [r2(sum(g["buckets"][i] for g in rows)) for i in range(5)]
    for g in rows:
        g["buckets"] = [r2(x) for x in g["buckets"]]
        g["total"] = r2(g["total"])
        g["advance"] = r2(g.get("advance", 0.0))
        g["net"] = r2(g["total"] - g["advance"])
    adv_total = r2(sum(g["advance"] for g in rows))
    return {"rows": rows, "totals": totals, "grand": r2(sum(totals)), "labels": AGEING_LABELS, "advance_total": adv_total,
            "net_total": r2(sum(totals) - adv_total)}


def overview(today=None):
    """Numbers for the supplier list header, per currency."""
    today = today or date.today()
    owed = defaultdict(float)
    overdue = defaultdict(float)
    due_week = defaultdict(float)
    for e in _bills():
        left = outstanding(e)
        if left <= 0:
            continue
        owed[e.currency] += left
        d = due_date(e)
        if d < today:
            overdue[e.currency] += left
        elif d <= today + timedelta(days=7):
            due_week[e.currency] += left
    cleared = {"expired": 0, "soon": 0}
    for s in Supplier.query.filter(Supplier.status != "Blocked").all():
        st, _ = clearance_state(s, today)
        if st in cleared:
            cleared[st] += 1
    held = defaultdict(float)
    for a in SupplierAdvance.query.all():
        held[a.currency] += advance_unapplied(a)
    fix = lambda d: {c: r2(v) for c, v in d.items() if v > CENT}
    return {"owed": fix(owed), "overdue": fix(overdue), "due_week": fix(due_week), "clearance": cleared, "advances": fix(held)}


def year_spend(supplier, today=None):
    """Billed this calendar year, per currency."""
    today = today or date.today()
    out = defaultdict(float)
    for e in supplier.bills:
        if e.expense_date.year == today.year:
            out[e.currency] += e.amount or 0.0
    return {c: r2(v) for c, v in out.items()}


# ------------------------------------------------------------------ CSV

def statement_csv(supplier, start, end, currency, st):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"Supplier statement - {supplier.name} ({supplier.code})", currency, start.isoformat(), end.isoformat()])
    w.writerow(["Date", "Type", "Reference", "Description", "Bills", "Payments", "Balance"])
    w.writerow([start.isoformat(), "", "", "Balance brought forward", "", "", f"{st['opening']:.2f}"])
    for ln in st["lines"]:
        w.writerow([ln["date"].isoformat(), ln["kind"], ln["ref"], ln["description"], f"{ln['bill']:.2f}" if ln["bill"] else "",
                    f"{ln['payment']:.2f}" if ln["payment"] else "", f"{ln['balance']:.2f}"])
    w.writerow([end.isoformat(), "", "", "Balance carried forward", "", "", f"{st['closing']:.2f}"])
    return buf.getvalue()


def ageing_csv(as_of, currency, data):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"Creditors ageing at {as_of.isoformat()}", currency])
    w.writerow(["Supplier"] + AGEING_LABELS + ["Total owed on bills", "Advances paid, not yet used", "Net"])
    for g in data["rows"]:
        w.writerow([g["name"]] + [f"{x:.2f}" for x in g["buckets"]] + [f"{g['total']:.2f}", f"{g['advance']:.2f}", f"{g['net']:.2f}"])
    w.writerow(["Total"] + [f"{x:.2f}" for x in data["totals"]] + [f"{data['grand']:.2f}", f"{data['advance_total']:.2f}", f"{data['net_total']:.2f}"])
    return buf.getvalue()
