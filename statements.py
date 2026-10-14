"""Firm-wide statements of account (Finance > Statements).

A statement lists, per currency (USD and ZWG are never mixed), everything that
moved a client's balance in a period: the opening balance, each invoice issued
(debit), each receipt received (credit), a running balance, the closing
balance and an ageing analysis of what is still owed.

What counts:
* Invoices that have been issued (status Sent or Paid), on their issue date.
  Drafts and Cancelled invoices are left out.
* Receipts that have not been voided, on the date the money was received.
* Credit notes that have not been voided, on the credit note's date (they
  reduce the balance like a payment but are labelled as credit notes).
* An invoice marked Paid before receipting existed (no receipt on file) is
  shown as settled by a "Marked paid" credit, so the ledger still balances.

Because a statement is a complete financial record, it is only produced for a
person who can see EVERY issued invoice of the client (some invoices are
hidden by engagement confidentiality). Anyone else is told to ask a Partner or
Admin rather than being handed a statement with a silently wrong balance.
"""
from collections import OrderedDict
from datetime import date, datetime
from types import SimpleNamespace

from flask import Blueprint, render_template, request, abort, flash, redirect, url_for
from flask_login import login_required, current_user

from extensions import db
from models import (
    Client, Invoice, Receipt, user_can_access_client, user_can_access_engagement, visible_clients,
)
from timeutil import to_cat

statements_bp = Blueprint("statements", __name__, url_prefix="/statements")

TOLERANCE = 0.005
AGEING_BUCKETS = [("current", "Current"), ("d1_30", "1 - 30 days"), ("d31_60", "31 - 60 days"),
                  ("d61_90", "61 - 90 days"), ("d90", "Over 90 days")]
BUCKET_KEYS = [k for k, _ in AGEING_BUCKETS]


# ---------------------------------------------------------------- the ledger

def is_issued(invoice):
    return invoice.status in ("Sent", "Paid")


def invoice_date(invoice):
    if invoice.issue_date:
        return invoice.issue_date
    return to_cat(invoice.created_at).date() if invoice.created_at else date.today()


def credit_events(invoice):
    """Every settlement of an invoice as (date, amount, description, receipt)."""
    events = []
    if invoice.legacy_paid:
        when = to_cat(invoice.paid_at).date() if invoice.paid_at else invoice_date(invoice)
        events.append((when, round(invoice.total or 0, 2), "Marked paid (no receipt on file)", None))
    for r in invoice.active_receipts:
        events.append((r.paid_on, round(r.amount or 0, 2), "Payment received", r))
    for c in invoice.active_credit_notes:
        events.append((c.issue_date, round(c.total, 2), "Credit note", c))
    return events


def outstanding_on(invoice, as_at):
    """What was still owed on `invoice` at the end of `as_at` (0 if not yet issued)."""
    if not is_issued(invoice) or invoice_date(invoice) > as_at:
        return 0.0
    paid = sum(amount for when, amount, _d, _r in credit_events(invoice) if when <= as_at)
    return max(round((invoice.total or 0) - paid, 2), 0.0)


def bucket_for(invoice, as_at):
    """Ageing bucket by days past the due date (invoice date if no due date)."""
    reference = invoice.due_date or invoice_date(invoice)
    days = (as_at - reference).days
    if days <= 0:
        return "current"
    if days <= 30:
        return "d1_30"
    if days <= 60:
        return "d31_60"
    if days <= 90:
        return "d61_90"
    return "d90"


def ageing(invoices, as_at):
    """{currency: {"buckets": {key: amount}, "total": amount, "overdue": amount}} for what is owed at `as_at`."""
    out = OrderedDict()
    for inv in sorted(invoices, key=lambda i: (i.currency or "", invoice_date(i), i.id)):
        owed = outstanding_on(inv, as_at)
        if owed <= TOLERANCE:
            continue
        block = out.setdefault(inv.currency, {"buckets": {k: 0.0 for k in BUCKET_KEYS}, "total": 0.0, "overdue": 0.0})
        key = bucket_for(inv, as_at)
        block["buckets"][key] = round(block["buckets"][key] + owed, 2)
        block["total"] = round(block["total"] + owed, 2)
        if key != "current":
            block["overdue"] = round(block["overdue"] + owed, 2)
    return out


def build_statement(invoices, date_from, date_to):
    """Statement data per currency for the (already complete) list of `invoices`."""
    issued = [i for i in invoices if is_issued(i)]
    currencies = sorted({i.currency for i in issued})
    aged = ageing(issued, date_to)
    result = OrderedDict()
    for currency in currencies:
        subset = [i for i in issued if i.currency == currency]
        rows = []
        opening = 0.0
        for inv in subset:
            when = invoice_date(inv)
            if when > date_to:
                continue
            debit = round(inv.total or 0, 2)
            if when < date_from:
                opening += debit
            else:
                rows.append({"date": when, "order": 0, "id": inv.id, "kind": "Invoice", "ref": inv.invoice_number,
                             "description": _invoice_description(inv), "debit": debit, "credit": 0.0,
                             "due": inv.due_date, "invoice": inv, "receipt": None})
            for cdate, amount, text, receipt in credit_events(inv):
                if cdate > date_to:
                    continue
                if cdate < date_from:
                    opening -= amount
                else:
                    is_credit_note = receipt is not None and hasattr(receipt, "credit_note_number")
                    if is_credit_note:
                        ref, kind = receipt.credit_note_number, "Credit note"
                        detail = f"Credit note - {inv.invoice_number}" + (f" ({receipt.reason[:60]})" if receipt.reason else "")
                    else:
                        ref, kind = (receipt.receipt_number if receipt else "—"), "Payment"
                        detail = f"{text} - {inv.invoice_number}" + (f" ({receipt.method})" if receipt and receipt.method else "")
                    rows.append({"date": cdate, "order": 1, "id": receipt.id if receipt else inv.id, "kind": kind,
                                 "ref": ref, "description": detail,
                                 "debit": 0.0, "credit": amount, "due": None, "invoice": inv,
                                 "receipt": None if is_credit_note else receipt,
                                 "credit_note": receipt if is_credit_note else None})
        rows.sort(key=lambda r: (r["date"], r["order"], r["id"]))
        opening = round(opening, 2)
        running = opening
        for row in rows:
            running = round(running + row["debit"] - row["credit"], 2)
            row["balance"] = running
        a = aged.get(currency, {"buckets": {k: 0.0 for k in BUCKET_KEYS}, "total": 0.0, "overdue": 0.0})
        result[currency] = {
            "currency": currency, "opening": opening, "rows": rows,
            "debits": round(sum(r["debit"] for r in rows), 2), "credits": round(sum(r["credit"] for r in rows), 2),
            "closing": running, "ageing": a,
        }
    return result


def _invoice_description(inv):
    text = "Invoice"
    if inv.engagement:
        text += f" - {inv.engagement.title}"
    if inv.due_date:
        text += f" (due {inv.due_date.strftime('%d %b %Y')})"
    return text


# ----------------------------------------------------------------- visibility

def _engagement_ok(obj):
    return not obj.engagement_id or user_can_access_engagement(current_user, obj.engagement)


def invoice_visible(invoice):
    return user_can_access_client(current_user, invoice.client_id) and _engagement_ok(invoice)


def client_invoices(client):
    """(issued invoices the user can see, number of issued invoices hidden from them)."""
    issued = [i for i in client.invoices if is_issued(i)]
    shown = [i for i in issued if invoice_visible(i)]
    return shown, len(issued) - len(shown)


INCOMPLETE = ("This client has invoices on engagements you are not on, so a complete statement cannot be produced by "
              "you. Ask a Partner or Admin to produce it.")


# --------------------------------------------------------------------- routes

def _parse_date(value, default):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return default


def default_period(today=None):
    today = today or date.today()
    month = today.month - 3
    year = today.year
    while month < 1:
        month += 12
        year -= 1
    return date(year, month, 1), today


@statements_bp.route("/")
@login_required
def index():
    """Firm-wide: who owes what, in which currency, and how old it is."""
    as_at = _parse_date(request.args.get("as_at"), date.today())
    clients = visible_clients(current_user)
    rows, restricted = [], 0
    totals = OrderedDict()
    for client in clients:
        shown, hidden = client_invoices(client)
        if hidden:
            restricted += 1
            continue
        for currency, block in ageing(shown, as_at).items():
            rows.append({"client": client, "currency": currency, **block})
            t = totals.setdefault(currency, {"buckets": {k: 0.0 for k in BUCKET_KEYS}, "total": 0.0, "overdue": 0.0, "clients": 0})
            for k in BUCKET_KEYS:
                t["buckets"][k] = round(t["buckets"][k] + block["buckets"][k], 2)
            t["total"] = round(t["total"] + block["total"], 2)
            t["overdue"] = round(t["overdue"] + block["overdue"], 2)
            t["clients"] += 1
    rows.sort(key=lambda r: (r["currency"], -r["total"], r["client"].name.lower()))
    date_from, date_to = default_period()
    return render_template(
        "statements/index.html", rows=rows, totals=totals, restricted=restricted, as_of=as_at, buckets=AGEING_BUCKETS,
        clients=clients, date_from=date_from, date_to=date_to,
    )


@statements_bp.route("/pick")
@login_required
def pick():
    """Form target on the index page: open a client's statement for a period."""
    client_id = request.args.get("client_id", type=int)
    if not client_id:
        flash("Choose a client first.", "danger")
        return redirect(url_for("statements.index"))
    return redirect(url_for("statements.client_statement", client_id=client_id,
                            date_from=request.args.get("date_from", ""), date_to=request.args.get("date_to", "")))


@statements_bp.route("/client/<int:client_id>")
@login_required
def client_statement(client_id):
    from invoicing import FIRM_LETTERHEAD
    client = Client.query.get_or_404(client_id)
    if not user_can_access_client(current_user, client.id):
        abort(403)
    shown, hidden = client_invoices(client)
    if hidden:
        abort(403, description=INCOMPLETE)
    default_from, default_to = default_period()
    date_to = _parse_date(request.args.get("date_to"), default_to)
    date_from = _parse_date(request.args.get("date_from"), default_from)
    if date_from > date_to:
        flash("The 'from' date was after the 'to' date, so the dates were swapped.", "info")
        date_from, date_to = date_to, date_from
    sections = build_statement(shown, date_from, date_to)
    currencies = " · ".join(sections.keys()) if sections else "—"
    bill_to = client.name + (f"\n{client.address}" if client.address else "")
    doc = SimpleNamespace(currency=currencies, client=client, bill_to=bill_to)
    return render_template(
        "statements/print.html", client=client, doc=doc, sections=sections, buckets=AGEING_BUCKETS, firm=FIRM_LETTERHEAD,
        date_from=date_from, date_to=date_to, currencies=currencies,
        doc_number=f"STM-{client.id:04d}-{date_to.strftime('%Y%m%d')}", today=date.today(),
    )
