"""Revenue & Expenses Analytics - debtors days, ageing, revenue and expense
trends and profit, from the firm's own invoices (Invoicing module) and its
Expense register (expenses.py).

How the numbers are defined (shown in plain words on the page too):

* An invoice counts once it has been issued - status Sent or Paid. Drafts
  and Cancelled invoices are ignored.
* Revenue is measured EXCLUDING VAT, by invoice date. Debtors (money owed
  to the firm) are measured INCLUDING VAT, because that is what a client
  actually owes.
* "Collected" is money actually received, on the date of each receipt (so an
  invoice paid in instalments counts as each instalment arrives; the VAT
  share of a payment is left out, to compare like with like with revenue).
  An invoice marked Paid before receipts existed counts in full on its paid date.
* Debtors are what is STILL OWING: invoice total less the receipts issued up
  to that date.
* Debtors days (DSO) = money owed by clients / invoiced in the last 90 days
  x 90, measured at a given date. It is "roughly how many days of sales are
  still sitting unpaid" - lower is better. Each month's point on the trend
  chart uses what was owed at that month-end, so history is honest even
  though invoices are only ever marked paid once.
* Expenses are measured by the bill date, in the currency they were paid in.
* USD and ZWG are NEVER added together: every figure is for one currency.

Everything here is a pure function of the database plus a "today" date, so it
is easy to test with known numbers (smoke_test98_analytics.py).
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta

from models import Invoice, Expense, INVOICE_CURRENCIES
from timeutil import to_cat

DSO_WINDOW_DAYS = 90
AGEING_BUCKETS = ["Not yet due", "1-30 days overdue", "31-60 days overdue", "61-90 days overdue", "Over 90 days overdue"]
NO_ENGAGEMENT_LABEL = "Not linked to an engagement"

PERIOD_PRESETS = [
    ("12m", "Last 12 months"),
    ("6m", "Last 6 months"),
    ("3m", "Last 3 months"),
    ("ytd", "This year to date"),
    ("last_year", "Last calendar year"),
]


# ------------------------------------------------------------------ dates

def month_start(d):
    return date(d.year, d.month, 1)


def add_months(d, n):
    total = d.year * 12 + (d.month - 1) + n
    y, m = divmod(total, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def month_end(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


def month_label(d):
    return d.strftime("%b %y")


def resolve_period(preset, date_from=None, date_to=None, today=None):
    """(start, end, label, preset_used). A custom from/to wins over a preset."""
    today = today or date.today()
    if date_from and date_to and date_from <= date_to:
        end = min(date_to, today) if date_to > today else date_to
        return date_from, end, f"{date_from.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}", "custom"
    if preset == "6m":
        return month_start(add_months(today, -5)), today, "Last 6 months", "6m"
    if preset == "3m":
        return month_start(add_months(today, -2)), today, "Last 3 months", "3m"
    if preset == "ytd":
        return date(today.year, 1, 1), today, f"{today.year} to date", "ytd"
    if preset == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), f"Calendar year {today.year - 1}", "last_year"
    return month_start(add_months(today, -11)), today, "Last 12 months", "12m"


def months_between(start, end):
    out, cur = [], month_start(start)
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


# ------------------------------------------------------------------ data rows

def _invoice_rows(currency):
    """Plain dicts for every issued invoice in one currency (so the maths
    below never touches the ORM again)."""
    rows = []
    for inv in Invoice.query.filter(Invoice.currency == currency, Invoice.status.in_(["Sent", "Paid"])).all():
        if not inv.issue_date:
            continue
        net = inv.subtotal
        gross = net * (1 + (inv.vat_pct or 0) / 100.0)
        if inv.legacy_paid:
            # marked Paid before receipting existed: one payment of everything
            payments = [(to_cat(inv.paid_at).date() if inv.paid_at else inv.issue_date, gross)]
        else:
            payments = [(r.paid_on, r.amount or 0.0) for r in inv.active_receipts]
        paid = None  # the day it was settled in full (None while anything is owing)
        if payments and sum(a for _, a in payments) >= gross - 0.005:
            paid = max(d for d, _ in payments)
        elif inv.status == "Paid" and not payments:
            paid = inv.issue_date
        eng = inv.engagement
        rows.append({
            "id": inv.id, "number": inv.invoice_number, "client_id": inv.client_id,
            "client": inv.client.name if inv.client else "(unknown)",
            "engagement_id": inv.engagement_id,
            "service": (eng.type if eng else None) or NO_ENGAGEMENT_LABEL,
            "issue": inv.issue_date, "due": inv.due_date or inv.issue_date,
            "paid": paid, "net": net, "gross": gross, "payments": payments,
        })
    return rows


def _expense_rows(currency):
    rows = []
    for e in Expense.query.filter(Expense.currency == currency).all():
        paid = None
        if e.status == "Paid":
            paid = e.paid_date or e.expense_date
        rows.append({
            "id": e.id, "date": e.expense_date, "supplier": e.supplier, "category": e.category or "Other",
            "amount": e.amount or 0.0, "paid": paid, "due": e.due_date or e.expense_date,
            "description": e.description or "",
        })
    return rows


# ------------------------------------------------------------------ measures

def _owed_on(row, day):
    """What the client still owed on this invoice at the end of `day`."""
    received = sum(a for d, a in row["payments"] if d <= day)
    left = row["gross"] - received
    return left if left > 0.005 else 0.0


def outstanding_at(rows, day):
    """Invoices issued on/before `day` with something still owing at that
    point; each returned row carries "owed", the amount still owing."""
    out = []
    for r in rows:
        if r["issue"] <= day:
            owed = _owed_on(r, day)
            if owed > 0:
                out.append(dict(r, owed=owed))
    return out


def collected_net(rows, lo, hi):
    """Money received between lo and hi, ex-VAT (each receipt scaled by the
    invoice's net/gross ratio)."""
    total = 0.0
    for r in rows:
        if r["gross"] <= 0:
            continue
        ratio = r["net"] / r["gross"]
        total += sum(a for d, a in r["payments"] if lo <= d <= hi) * ratio
    return total


def dso_at(rows, day, window=DSO_WINDOW_DAYS):
    owed = sum(r["owed"] for r in outstanding_at(rows, day))
    sales = sum(r["gross"] for r in rows if day - timedelta(days=window) < r["issue"] <= day)
    if sales <= 0:
        return None
    return owed / sales * window


def creditor_days_at(exp_rows, day, window=DSO_WINDOW_DAYS):
    owed = sum(e["amount"] for e in exp_rows if e["date"] <= day and (e["paid"] is None or e["paid"] > day))
    spend = sum(e["amount"] for e in exp_rows if day - timedelta(days=window) < e["date"] <= day)
    if spend <= 0:
        return None
    return owed / spend * window


def ageing(rows, today):
    """Debtors ageing as at `today`, by days past the due date."""
    buckets = [{"label": l, "amount": 0.0, "count": 0} for l in AGEING_BUCKETS]
    for r in outstanding_at(rows, today):
        late = (today - r["due"]).days
        idx = 0 if late <= 0 else 1 if late <= 30 else 2 if late <= 60 else 3 if late <= 90 else 4
        buckets[idx]["amount"] += r["owed"]
        buckets[idx]["count"] += 1
    return buckets


def _sum(rows, key, lo, hi, date_key):
    return sum(r[key] for r in rows if r[date_key] is not None and lo <= r[date_key] <= hi)


def _top_with_other(totals, how_many=8):
    """[(label, value)] sorted high to low, tail folded into 'Other'."""
    items = sorted(((k, v) for k, v in totals.items() if v), key=lambda kv: -kv[1])
    if len(items) > how_many:
        tail = sum(v for _, v in items[how_many - 1:])
        items = items[:how_many - 1] + [("Other", tail)]
    return items


def _shares(items):
    total = sum(v for _, v in items) or 0
    return [{"label": k, "value": round(v, 2), "share": (v / total * 100 if total else 0)} for k, v in items]


def _pct_change(now, before):
    if before in (None, 0) or now is None:
        return None
    return (now - before) / abs(before) * 100


# ------------------------------------------------------------------ the report

def build_report(currency="USD", preset="12m", date_from=None, date_to=None, today=None):
    today = today or date.today()
    if currency not in INVOICE_CURRENCIES:
        currency = INVOICE_CURRENCIES[0]
    start, end, label, preset_used = resolve_period(preset, date_from, date_to, today)
    end_for_balances = min(end, today)

    inv = _invoice_rows(currency)
    exp = _expense_rows(currency)

    # previous period of the same length, immediately before
    span = (end - start).days + 1
    p_end = start - timedelta(days=1)
    p_start = p_end - timedelta(days=span - 1)

    revenue = _sum(inv, "net", start, end, "issue")
    revenue_prev = _sum(inv, "net", p_start, p_end, "issue")
    billed_gross = _sum(inv, "gross", start, end, "issue")
    collected = collected_net(inv, start, end)
    collected_prev = collected_net(inv, p_start, p_end)
    expenses = _sum(exp, "amount", start, end, "date")
    expenses_prev = _sum(exp, "amount", p_start, p_end, "date")
    profit = revenue - expenses
    profit_prev = revenue_prev - expenses_prev

    owed_now = outstanding_at(inv, end_for_balances)
    owed_total = sum(r["owed"] for r in owed_now)
    overdue_rows = [r for r in owed_now if (end_for_balances - r["due"]).days > 0]
    overdue_total = sum(r["owed"] for r in overdue_rows)
    dso = dso_at(inv, end_for_balances)
    dso_prev = dso_at(inv, p_end)

    paid_in_period = [r for r in inv if r["paid"] and start <= r["paid"] <= end]
    avg_days_to_pay = (sum((r["paid"] - r["issue"]).days for r in paid_in_period) / len(paid_in_period)) if paid_in_period else None
    issued_in_period = [r for r in inv if start <= r["issue"] <= end]
    avg_terms = (sum((r["due"] - r["issue"]).days for r in issued_in_period) / len(issued_in_period)) if issued_in_period else None
    # share of the period's billing (incl. VAT) that has already been collected
    collection_rate = (sum(r["gross"] - _owed_on(r, end_for_balances) for r in issued_in_period) / billed_gross * 100) if billed_gross else None

    unpaid_exp = [e for e in exp if e["date"] <= end_for_balances and (e["paid"] is None or e["paid"] > end_for_balances)]
    creditors_total = sum(e["amount"] for e in unpaid_exp)
    creditor_days = creditor_days_at(exp, end_for_balances)

    # ---- monthly series
    months = months_between(start, end)
    m_rev, m_billed, m_coll, m_exp, m_profit, m_prior, m_dso = [], [], [], [], [], [], []
    for m in months:
        lo, hi = max(m, start), min(month_end(m), end)
        r = _sum(inv, "net", lo, hi, "issue")
        e = _sum(exp, "amount", lo, hi, "date")
        m_rev.append(round(r, 2))
        m_coll.append(round(collected_net(inv, lo, hi), 2))
        m_exp.append(round(e, 2))
        m_profit.append(round(r - e, 2))
        py = add_months(m, -12)
        m_prior.append(round(_sum(inv, "net", py, month_end(py), "issue"), 2))
        m_dso.append(None if hi > today else _round_or_none(dso_at(inv, hi)))
    # the current (unfinished) month's debtors-days point is "as at today"
    if months and month_end(months[-1]) > today and months[-1] <= today:
        m_dso[-1] = _round_or_none(dso_at(inv, today))

    # ---- where the revenue comes from
    by_service, by_client = defaultdict(float), defaultdict(float)
    for r in issued_in_period:
        by_service[r["service"]] += r["net"]
        by_client[r["client"]] += r["net"]
    client_items_all = sorted(((k, v) for k, v in by_client.items() if v), key=lambda kv: -kv[1])
    top5_share = (sum(v for _, v in client_items_all[:5]) / revenue * 100) if revenue and client_items_all else None

    # ---- expenses by category
    by_cat = defaultdict(float)
    for e in exp:
        if start <= e["date"] <= end:
            by_cat[e["category"]] += e["amount"]

    # ---- client payment behaviour (all-time days to pay, current position)
    clients = {}
    for r in inv:
        c = clients.setdefault(r["client_id"], {"client_id": r["client_id"], "client": r["client"], "invoiced": 0.0,
                                                "outstanding": 0.0, "overdue": 0.0, "oldest_overdue": 0, "_days": []})
        if start <= r["issue"] <= end:
            c["invoiced"] += r["net"]
        if r["paid"]:
            c["_days"].append((r["paid"] - r["issue"]).days)
    for r in owed_now:
        c = clients[r["client_id"]]
        c["outstanding"] += r["owed"]
        late = (end_for_balances - r["due"]).days
        if late > 0:
            c["overdue"] += r["owed"]
            c["oldest_overdue"] = max(c["oldest_overdue"], late)
    client_rows = []
    for c in clients.values():
        days = c.pop("_days")
        c["avg_days_to_pay"] = (sum(days) / len(days)) if days else None
        if c["invoiced"] or c["outstanding"]:
            client_rows.append(c)
    client_rows.sort(key=lambda c: (-c["overdue"], -c["outstanding"], -c["invoiced"]))

    overdue_list = sorted(
        ({"id": r["id"], "number": r["number"], "client": r["client"], "engagement_id": r["engagement_id"],
          "due": r["due"], "days_overdue": (end_for_balances - r["due"]).days, "amount": r["owed"]} for r in overdue_rows),
        key=lambda r: -r["days_overdue"])

    unpaid_list = sorted(
        ({"id": e["id"], "supplier": e["supplier"], "category": e["category"], "date": e["date"], "due": e["due"],
          "days_overdue": (end_for_balances - e["due"]).days, "amount": e["amount"]} for e in unpaid_exp),
        key=lambda e: -e["days_overdue"])

    return {
        "currency": currency, "today": today, "preset": preset_used, "label": label,
        "start": start, "end": end, "prev_start": p_start, "prev_end": p_end,
        "has_invoices": bool(inv), "has_expenses": bool(exp),
        "kpi": {
            "revenue": revenue, "revenue_change": _pct_change(revenue, revenue_prev),
            "collected": collected, "collected_change": _pct_change(collected, collected_prev),
            "expenses": expenses, "expenses_change": _pct_change(expenses, expenses_prev),
            "profit": profit, "profit_prev": profit_prev,
            "margin": (profit / revenue * 100) if revenue else None,
            "owed": owed_total, "owed_count": len(owed_now),
            "overdue": overdue_total, "overdue_count": len(overdue_rows),
            "overdue_pct": (overdue_total / owed_total * 100) if owed_total else None,
            "dso": dso, "dso_prev": dso_prev,
            "avg_days_to_pay": avg_days_to_pay, "avg_terms": avg_terms,
            "collection_rate": collection_rate,
            "creditors": creditors_total, "creditor_days": creditor_days,
            "top5_share": top5_share,
        },
        "months": [{"key": month_key(m), "label": month_label(m)} for m in months],
        "series": {"revenue": m_rev, "collected": m_coll, "expenses": m_exp, "profit": m_profit,
                   "prior_year": m_prior, "dso": m_dso},
        "ageing": [{"label": b["label"], "value": round(b["amount"], 2), "count": b["count"]} for b in ageing(inv, end_for_balances)],
        "by_service": _shares(_top_with_other(by_service)),
        "by_client": _shares(_top_with_other(by_client)),
        "by_category": _shares(_top_with_other(by_cat, 10)),
        "clients": client_rows, "overdue_list": overdue_list, "unpaid_list": unpaid_list,
    }


def _round_or_none(v):
    return None if v is None else round(v, 1)


def chart_payload(report):
    """The slice of the report the browser-side charts need (JSON-safe)."""
    return {
        "currency": report["currency"],
        "months": report["months"],
        "series": report["series"],
        "ageing": report["ageing"],
        "by_service": report["by_service"],
        "by_client": report["by_client"],
        "by_category": report["by_category"],
        "dso_window": DSO_WINDOW_DAYS,
    }
