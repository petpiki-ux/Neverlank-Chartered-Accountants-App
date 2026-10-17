"""The firm's Cash Book (Finance > Firm Finance > Cash book): its bank, cash and mobile-money accounts.

Where the money comes from
--------------------------
Nothing already recorded in the app is typed in again. The cash book READS it:

* Receipts issued to clients (money in), excluding the receipts created by applying a client advance (that money came
  in when the advance was received) and voided receipts;
* invoices marked Paid before receipting existed (money in, for the full invoice total, on the paid date);
* client advances received (money in) and refunds of advances (money out), voided ones excluded;
* expenses marked Paid (money out, on the paid date), at the amount recorded in the Expense register - which is
  EXCLUDING any VAT the firm can recover, so if VAT was paid to the supplier the bank line is larger than the
  register amount; record the VAT with a manual entry if the cash book must agree to the bank to the cent;
* manual entries (CashEntry): what the app has no other record of - owner contributions, loans, bank charges,
  dividends, tax paid, and transfers between accounts.

Which account a transaction lands in
------------------------------------
1. a manual move recorded in CashAssign, if any;
2. otherwise the first active account in the transaction's currency whose payment methods list contains the
   transaction's payment method (receipts, advances and refunds have one);
3. otherwise the account in that currency flagged "default for money in" (or "out");
4. otherwise it is UNASSIGNED and shown in a warning, not silently dropped.

An account counts transactions dated on or after its opening date, on top of its opening balance (the balance at the
START of that day). Earlier transactions are history from before the book started and are not counted.

Everything is a pure function of the database, so it is easy to test with known numbers.
"""
from collections import defaultdict
from datetime import date

from models import (
    CashAccount, CashEntry, CashAssign, CashClear, ClientAdvance, Expense, Invoice, Receipt,
    CASH_TRANSFER_CATEGORY, INVOICE_CURRENCIES, PAYMENT_METHODS,
)
from timeutil import to_cat

SOURCE_ORDER = {"Opening": 0, "Receipt": 1, "Invoice": 2, "Advance": 3, "Refund": 4, "Expense": 5, "Entry": 6}


def r2(v):
    return round(float(v or 0.0), 2)


# ------------------------------------------------------------------ transactions

def collect_transactions():
    """Every cash movement the app knows about, as plain dicts (account not yet decided)."""
    out = []
    for r in Receipt.query.filter(Receipt.voided_at.is_(None), Receipt.advance_application_id.is_(None)).all():
        inv = r.invoice
        out.append({
            "key": f"receipt:{r.id}", "source": "Receipt", "date": r.paid_on, "amount": r2(r.amount),
            "currency": r.currency or (inv.currency if inv else "USD"), "method": r.method,
            "ref": r.receipt_number,
            "description": f"{inv.client.name if inv and inv.client else 'Client'} - invoice {inv.invoice_number if inv else ''}".strip(),
            "link": ("invoicing.view_invoice", {"invoice_id": inv.id}) if inv else None, "entry_id": None,
        })
    for inv in Invoice.query.filter(Invoice.status == "Paid").all():
        if inv.legacy_paid and inv.issue_date:
            d = to_cat(inv.paid_at).date() if inv.paid_at else inv.issue_date
            out.append({
                "key": f"legacy:{inv.id}", "source": "Invoice", "date": d, "amount": r2(inv.total), "currency": inv.currency,
                "method": None, "ref": inv.invoice_number,
                "description": f"{inv.client.name if inv.client else 'Client'} - invoice marked paid (no receipt on file)",
                "link": ("invoicing.view_invoice", {"invoice_id": inv.id}), "entry_id": None,
            })
    for a in ClientAdvance.query.filter(ClientAdvance.voided_at.is_(None)).all():
        out.append({
            "key": f"advance:{a.id}", "source": "Advance", "date": a.received_on, "amount": r2(a.amount), "currency": a.currency,
            "method": a.method, "ref": a.advance_number,
            "description": f"{a.client.name if a.client else 'Client'} - advance received", "link": None, "entry_id": None,
        })
        for rf in a.active_refunds:
            out.append({
                "key": f"refund:{rf.id}", "source": "Refund", "date": rf.refunded_on, "amount": -r2(rf.amount), "currency": a.currency,
                "method": rf.method, "ref": rf.reference or a.advance_number,
                "description": f"{a.client.name if a.client else 'Client'} - advance refunded", "link": None, "entry_id": None,
            })
    for e in Expense.query.filter(Expense.status == "Paid").all():
        out.append({
            "key": f"expense:{e.id}", "source": "Expense", "date": e.paid_date or e.expense_date, "amount": -r2(e.amount),
            "currency": e.currency, "method": None, "ref": e.reference or "",
            "description": f"{e.supplier} - {e.category}", "link": ("expenses.edit_expense", {"expense_id": e.id}), "entry_id": None,
        })
    for en in CashEntry.query.all():
        out.append({
            "key": f"entry:{en.id}", "source": "Entry", "date": en.entry_date,
            "amount": r2(en.amount) if en.direction == "in" else -r2(en.amount),
            "currency": en.account.currency if en.account else "USD", "method": None, "ref": en.reference or "",
            "description": en.description or en.category, "category": en.category, "link": None, "entry_id": en.id,
            "fixed_account_id": en.account_id, "transfer_id": en.transfer_id,
        })
    return out


def accounts_in_order(active_only=False):
    q = CashAccount.query.order_by(CashAccount.order, CashAccount.name).all()
    return [a for a in q if a.is_active or not active_only]


def resolve_accounts(txns, accounts=None):
    """Sets txn["account_id"] on every transaction (None = unassigned). Returns the same list."""
    accounts = accounts if accounts is not None else accounts_in_order()
    by_id = {a.id: a for a in accounts}
    assigns = {a.source_key: a.account_id for a in CashAssign.query.all()}
    active = [a for a in accounts if a.is_active]
    for t in txns:
        t["account_id"] = None
        t["auto_account_id"] = None
        if t.get("fixed_account_id") in by_id:
            t["account_id"] = t["fixed_account_id"]
            continue
        # the account the rules would pick
        method = (t.get("method") or "").strip().lower()
        pick = None
        if method:
            for a in active:
                if a.currency == t["currency"] and method in [m.lower() for m in a.method_list]:
                    pick = a
                    break
        if pick is None:
            flag = "default_in" if t["amount"] >= 0 else "default_out"
            for a in active:
                if a.currency == t["currency"] and getattr(a, flag):
                    pick = a
                    break
        t["auto_account_id"] = pick.id if pick else None
        manual = assigns.get(t["key"])
        if manual in by_id and by_id[manual].currency == t["currency"]:
            t["account_id"] = manual
            t["moved"] = True
        else:
            t["account_id"] = t["auto_account_id"]
    return txns


def load_all():
    """(accounts, transactions with accounts resolved, clears {(account_id, key): date})."""
    accounts = accounts_in_order()
    txns = resolve_accounts(collect_transactions(), accounts)
    clears = {(c.account_id, c.source_key): c.cleared_on for c in CashClear.query.all()}
    return accounts, txns, clears


def _sort_key(t):
    return (t["date"], SOURCE_ORDER.get(t["source"], 9), t["key"])


def account_rows(account, txns):
    """The account's transactions on/after its opening date, oldest first."""
    rows = [t for t in txns if t["account_id"] == account.id and t["date"] >= account.opening_date]
    rows.sort(key=_sort_key)
    return rows


def balance_at(account, txns, day):
    """Balance at the END of `day` (opening balance plus everything dated from the opening date to `day`)."""
    if day < account.opening_date:
        return 0.0
    return r2((account.opening_balance or 0.0) + sum(t["amount"] for t in txns
                                                    if t["account_id"] == account.id and account.opening_date <= t["date"] <= day))


def ledger(account, txns, clears, start, end):
    """Opening balance at the start of `start`, the lines in [start, end] with running balances, and totals."""
    rows = account_rows(account, txns)
    opening = r2(account.opening_balance or 0.0)
    if start > account.opening_date:
        opening = r2(opening + sum(t["amount"] for t in rows if t["date"] < start))
    running = opening
    lines, total_in, total_out = [], 0.0, 0.0
    for t in rows:
        if t["date"] < start or t["date"] > end:
            continue
        running = r2(running + t["amount"])
        if t["amount"] >= 0:
            total_in += t["amount"]
        else:
            total_out += -t["amount"]
        lines.append(dict(t, balance=running, cleared_on=clears.get((account.id, t["key"]))))
    return {
        "opening": opening, "lines": lines, "total_in": r2(total_in), "total_out": r2(total_out), "closing": running,
        "before_book": sum(1 for t in txns if t["account_id"] == account.id and t["date"] < account.opening_date),
    }


def reconciliation(account, txns, clears, stmt_date, stmt_balance):
    """Bank reconciliation at a statement date: the statement balance plus deposits not yet on the statement less
    payments not yet on the statement should equal the cash book balance."""
    book = balance_at(account, txns, stmt_date)
    unc_in, unc_out = [], []
    for t in account_rows(account, txns):
        if t["date"] > stmt_date:
            continue
        c = clears.get((account.id, t["key"]))
        if c is not None and c <= stmt_date:
            continue
        (unc_in if t["amount"] >= 0 else unc_out).append(t)
    adj = r2(stmt_balance + sum(t["amount"] for t in unc_in) + sum(t["amount"] for t in unc_out))
    return {"book": book, "statement": r2(stmt_balance), "uncleared_in": unc_in, "uncleared_out": unc_out,
            "adjusted": adj, "difference": r2(book - adj), "date": stmt_date}


def unassigned(txns):
    return sorted([t for t in txns if t["account_id"] is None], key=_sort_key)


def summary(accounts, txns, start, end):
    """One row per account for the period, plus totals per currency."""
    rows, totals = [], defaultdict(lambda: {"opening": 0.0, "in": 0.0, "out": 0.0, "closing": 0.0})
    for a in accounts:
        lg = ledger(a, txns, {}, start, end)
        row = {"account": a, "opening": lg["opening"], "in": lg["total_in"], "out": lg["total_out"], "closing": lg["closing"],
               "lines": len(lg["lines"])}
        rows.append(row)
        if a.is_active or abs(lg["closing"]) > 0.004:
            t = totals[a.currency]
            for k in ("opening", "in", "out", "closing"):
                t[k] += row[k]
    return rows, {c: {k: r2(v) for k, v in t.items()} for c, t in totals.items()}


# ------------------------------------------------------------------ for the financial statements

def book_covers(day):
    """True if at least one Cash Book account is running on `day` (it has started by then)."""
    return any(a.opening_date <= day for a in CashAccount.query.all())


def uses_cash_book(ye, day):
    """Whether this year-end's cash and bank come from the Cash Book: yes by default, as soon as an account was running
    at the year-end, unless the year-end is switched to manual cash."""
    return (not getattr(ye, "cash_manual", False)) and book_covers(day)


def statement_balances(day, zwg_rate):
    """Cash and bank balances at the end of `day`, in USD, one item per account, for the firm's balance sheet.
    Returns {"items": [{name, usd, original}], "warnings": [...]}."""
    accounts, txns, _clears = load_all()
    items, warnings = [], []
    if not accounts:
        warnings.append("The Cash Book has no accounts yet.")
    for a in accounts:
        bal = balance_at(a, txns, day)
        started = a.opening_date <= day
        if not started and not a.is_active:
            continue
        if not started:
            warnings.append(f"Cash book account '{a.name}' only starts on {a.opening_date.strftime('%d %b %Y')}, after this year-end, so its balance here is nil.")
            continue
        if not a.is_active and abs(bal) < 0.005:
            continue
        if a.currency == "USD":
            usd, label = bal, a.name
        elif zwg_rate and zwg_rate > 0:
            usd, label = bal / zwg_rate, f"{a.name} ({a.currency} {bal:,.2f} at {zwg_rate:g})"
        else:
            if abs(bal) >= 0.005:
                warnings.append(f"Cash book account '{a.name}' is in {a.currency} but no rate is entered for this year-end, so it was left out.")
            continue
        if usd < -0.004:
            warnings.append(f"Cash book account '{a.name}' is overdrawn by USD {-usd:,.2f} at this date; it is shown as negative cash. "
                            "If it is an agreed overdraft, enter it as a short-term borrowing instead.")
        items.append({"name": label, "usd": r2(usd), "original": bal, "currency": a.currency})
    un = unassigned(txns)
    dated = [t for t in un if t["date"] <= day]
    if dated:
        warnings.append(f"{len(dated)} recorded cash transaction(s) dated up to this year-end are not in any Cash Book account "
                        "(no account matches their currency or payment method), so they are not in the cash balance.")
    return {"items": items, "warnings": warnings}


def manual_summary(start, end):
    """Manual entries in a period by category (USD accounts and ZWG kept apart): what the year-end page shows as a
    guide for figures that are entered rather than derived (loans, dividends, owner contributions ...)."""
    rows = defaultdict(lambda: {"in": 0.0, "out": 0.0})
    for en in CashEntry.query.filter(CashEntry.entry_date >= start, CashEntry.entry_date <= end).all():
        if en.category == CASH_TRANSFER_CATEGORY:
            continue
        cur = en.account.currency if en.account else "USD"
        k = (en.category, cur)
        rows[k]["in" if en.direction == "in" else "out"] += en.amount or 0.0
    return [{"category": c, "currency": cur, "in": r2(v["in"]), "out": r2(v["out"])} for (c, cur), v in sorted(rows.items())]


# ------------------------------------------------------------------ choosing the account when money is captured
# The capture forms (payment/receipt, client advance, advance refund, expense) carry a "Cash book account" picker so
# the person capturing says which bank/cash/mobile-money account the money went through. The choice is stored as a
# CashAssign (the same record the cash book's "Move" button writes), so it overrides the automatic rules. Only users
# with the "view_firm_finances" permission (Partner/Admin) see the picker; for everyone else capture is unchanged
# and the automatic rules apply.

def can_choose_account():
    from flask_login import current_user
    from models import user_has_permission
    return bool(getattr(current_user, "is_authenticated", False)) and user_has_permission(current_user, "view_firm_finances")


def choosable_accounts():
    """Active accounts the picker offers (empty if the user may not choose, or no accounts are set up yet)."""
    if not can_choose_account():
        return []
    return accounts_in_order(active_only=True)


def current_choice(key):
    """The account id manually chosen for this transaction key (or None)."""
    row = CashAssign.query.filter_by(source_key=key).first() if key else None
    return row.account_id if row else None


def parse_choice(raw, currency):
    """Validate the posted picker value. Returns (account_id or None, error or None). Blank = automatic."""
    if not can_choose_account():
        return None, None
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        acc_id = int(raw)
    except ValueError:
        return None, "Choose a cash book account from the list."
    acct = CashAccount.query.get(acc_id)
    if acct is None or not acct.is_active:
        return None, "That cash book account is not available. Choose another."
    if acct.currency != currency:
        return None, f"{acct.name} is a {acct.currency} account but this is a {currency} transaction. Choose a {currency} account."
    return acct.id, None


def set_account(key, account_id):
    """Store (or, with None, clear) the chosen account for a transaction key. Does not commit.
    Always called on capture, so a stale choice left by a deleted record whose id was reused can never apply."""
    existing = CashAssign.query.filter_by(source_key=key).first()
    if account_id is None:
        if existing:
            from extensions import db
            db.session.delete(existing)
            CashClear.query.filter_by(source_key=key).delete()
        return
    from extensions import db
    if existing:
        if existing.account_id != account_id:
            CashClear.query.filter_by(source_key=key).delete()
        existing.account_id = account_id
    else:
        db.session.add(CashAssign(source_key=key, account_id=account_id))


def forget(key):
    """Remove everything stored about a transaction key (when its record is deleted). Does not commit."""
    CashAssign.query.filter_by(source_key=key).delete()
    CashClear.query.filter_by(source_key=key).delete()


def category_total(categories, start, end, zwg_rate):
    """Money paid out (less money received back) under these manual-entry headings between two dates, in USD, for the
    tax pages (QPDs and final income tax payments). Returns (total, [payment rows], [warnings])."""
    total, rows, warnings = 0.0, [], []
    q = (CashEntry.query.filter(CashEntry.category.in_(list(categories)), CashEntry.entry_date >= start, CashEntry.entry_date <= end)
         .order_by(CashEntry.entry_date, CashEntry.id))
    for en in q.all():
        cur = en.account.currency if en.account else "USD"
        sign = 1.0 if en.direction == "out" else -1.0
        if cur == "USD":
            usd = en.amount or 0.0
        elif zwg_rate and zwg_rate > 0:
            usd = (en.amount or 0.0) / zwg_rate
        else:
            warnings.append(f"A {cur} payment of {en.amount:,.2f} on {en.entry_date.strftime('%d %b %Y')} ({en.category}) was left out: no ZWG rate is entered for this year-end.")
            continue
        total += sign * usd
        rows.append({"date": en.entry_date, "category": en.category, "usd": r2(sign * usd), "description": en.description or "", "account": en.account.name if en.account else ""})
    return r2(total), rows, warnings
