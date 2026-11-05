"""Neverlank's own staff payroll in the firm's books (Firm Finance > Ledger and the firm's financial statements).

Payroll and Firm Finance used to be separate. A finalized INTERNAL payroll period now posts itself - nothing is keyed twice
and nothing is stored: like invoices and bills, the postings are derived each time the ledger or the statements are built.
Client payrolls (scope "client") never touch the firm's books.

For each finalized period, dated its pay date (else the period end):

    Dr Salaries and wages                         gross pay of all payslips
    Dr Employer NSSA and APWCS contributions      the employer's NSSA contribution + APWCS
        Cr PAYE and AIDS levy payable             PAYE + AIDS levy (owed to ZIMRA)
        Cr NSSA contributions payable             employees' NSSA + employer's NSSA + APWCS (owed to NSSA)
        Cr Other payroll deductions payable       deductions taken off pay (medical aid, advances, union dues ...)
        Cr Net salaries payable                   what the staff are paid

Only totals are posted (never a name or an individual's pay), because the ledger can be seen by people who may not see payslips.

The payables are cleared by the Cash Book, with four headings for money out:
    "Salaries paid"                          -> Net salaries payable
    "PAYE and other taxes paid"              -> PAYE and AIDS levy payable
    "NSSA contributions paid"                -> NSSA contributions payable
    "Medical aid and other deductions paid"  -> Other payroll deductions payable
and the medical aid bills posted from a period (payroll_medical.py), and medical aid bills filed in the Expense register from the
link start, clear Other payroll deductions payable instead of being a second expense: the employer's share is already in the gross
pay, the employees' share is money taken off their pay.

The link starts with the earliest finalized period (or the date set in Payroll Tax Settings). A cash book entry or a period
before that date is treated as before: nothing already in the books is changed unless it is dated from then on.
"""
from collections import defaultdict
from datetime import date

from extensions import db
from models import CashEntry, Expense, PayrollPeriod, PayrollTaxSettings, INVOICE_CURRENCIES

CENT = 0.005

SAL, ER, ZIMRA, NSSA, DED, NET = "PAY_SAL", "PAY_ER", "PAY_ZIMRA", "PAY_NSSA", "PAY_DED", "PAY_NET"
# cash book heading (money out) -> the payable it clears
PAYMENT_HEADINGS = {"Salaries paid": NET, "PAYE and other taxes paid": ZIMRA, "NSSA contributions paid": NSSA,
                    "Medical aid and other deductions paid": DED}
PAYABLE_NAMES = {NET: "Net salaries payable", ZIMRA: "PAYE and AIDS levy payable", NSSA: "NSSA contributions payable",
                 DED: "Other payroll deductions payable"}
SALARY_WORDS = ("salar", "wage", "payroll")
MEDICAL_WORDS = ("medical aid", "medical scheme", "medical insurance")


def r2(v):
    return round(float(v or 0.0), 2)


def period_date(p):
    return p.pay_date or p.period_end or p.period_start


def ledger_from():
    s = PayrollTaxSettings.query.get(1)
    return getattr(s, "ledger_from", None) if s is not None else None


def figures(p):
    """Totals of one period's payslips. `net` is gross less every deduction, so the posting always balances; `net_stored` is what
    the payslips say (it differs only if someone typed a net pay by hand)."""
    gross = r2(sum(x.gross_pay or 0.0 for x in p.payslips))
    paye_aids = r2(sum((x.paye_tax or 0.0) + (x.aids_levy or 0.0) for x in p.payslips))
    nssa_ee = r2(sum(x.nssa_employee or 0.0 for x in p.payslips))
    nssa_er = r2(sum(x.nssa_employer or 0.0 for x in p.payslips))
    apwcs = r2(sum(x.apwcs or 0.0 for x in p.payslips))
    ded = r2(sum(x.other_deductions_total or 0.0 for x in p.payslips))
    return {"gross": gross, "paye_aids": paye_aids, "nssa_ee": nssa_ee, "nssa_er": nssa_er, "apwcs": apwcs, "ded": ded,
            "net": r2(gross - paye_aids - nssa_ee - ded), "net_stored": r2(sum(x.net_pay or 0.0 for x in p.payslips)),
            "nssa_total": r2(nssa_ee + nssa_er + apwcs), "employer_cost": r2(nssa_er + apwcs)}


def periods():
    """The finalized internal periods that post to the books, oldest first: [{period, id, name, date, currency, **figures}]."""
    floor = ledger_from()
    out = []
    for p in PayrollPeriod.query.filter_by(scope="internal", status="Finalized").all():
        d = period_date(p)
        if d is None or (floor and d < floor) or not p.payslips:
            continue
        row = {"period": p, "id": p.id, "name": p.name, "date": d, "currency": p.currency or "USD"}
        row.update(figures(p))
        out.append(row)
    out.sort(key=lambda r: (r["date"], r["id"]))
    return out


def link_start(rows=None):
    """The date the link begins: the earliest posted period (never before the date set in Payroll Tax Settings). None = no payroll posted."""
    rows = periods() if rows is None else rows
    return rows[0]["date"] if rows else None


def cash_key(category, day, start):
    """The payable a cash book heading clears, or None (not a payroll heading, or dated before the link began)."""
    if start is None or day is None or day < start:
        return None
    return PAYMENT_HEADINGS.get(category)


def medical_register_bills(start):
    """{expense id: Expense} of medical aid bills filed by hand in the Expense register (category with 'medical aid', 'medical scheme' or
    'medical insurance') dated from the link start, other than those posted from a payroll period. The payroll already carries the
    cost (the employer's share is in gross pay, the employees' share was taken off pay), so paying the society settles Other payroll
    deductions payable: it is not a second expense."""
    if start is None:
        return {}
    return {e.id: e for e in Expense.query.filter(Expense.expense_date >= start).all()
            if not (e.source_key or "").startswith("payroll_medical:") and any(w in (e.category or "").lower() for w in MEDICAL_WORDS)}


def linked_medical(rows=None):
    """{expense id: period id (None for a bill filed by hand)} of the medical aid bills whose cost the payroll already carries: those posted
    from a period that is itself posted to the books, and medical aid bills filed in the Expense register from the link start. They are not
    a second expense: each clears Other payroll deductions payable."""
    rows = periods() if rows is None else rows
    ids = {r["id"] for r in rows}
    out = {}
    if not ids:
        return out
    for e in Expense.query.filter(Expense.source_key.like("payroll_medical:%")).all():
        parts = (e.source_key or "").split(":")
        try:
            pid = int(parts[1])
        except (IndexError, ValueError):
            continue
        if pid in ids:
            out[e.id] = pid
    for eid in medical_register_bills(link_start(rows)):
        out.setdefault(eid, None)
    return out


def medical_allocation(rows=None):
    """{expense id: (to_payable, to_expense, to_net)} in the bill's own currency, for every medical aid bill the payroll carries.

    Other payroll deductions payable is a LIABILITY: it can only be cleared by what the payslips actually took off pay. A bill (or a run of
    bills) that is more than the deductions posted is not covered by it, so only the covered part settles the payable and the account is
    never driven into debit by bills. The rest depends on the bill:
      - the employer's share posted from payroll: it is already in gross pay (and so in net pay) as an allowance, so the rest settles
        Net salaries payable (to_net) - not a second expense;
      - any other medical aid bill (the employees' share, or one filed by hand): the payroll does not carry it, so the rest is an
        ordinary medical aid expense (to_expense).
    Bills are taken oldest first against the deductions of all posted periods in the same currency, less what the Cash Book has already
    paid under the medical aid heading."""
    rows = periods() if rows is None else rows
    bills = linked_medical(rows)
    if not bills:
        return {}
    cap = defaultdict(float)
    for r in rows:
        cap[r["currency"]] += r["ded"] or 0.0
    for p in payments(link_start(rows)):
        if p["key"] == DED:
            cap[p["currency"]] -= p["amount"]
    out = {}
    for e in sorted(Expense.query.filter(Expense.id.in_(list(bills))).all(), key=lambda x: (x.expense_date or date.min, 1 if (x.source_key or '').endswith(':employer') else 0, x.id)):   # on one date, what was taken off pay is cleared first
        cur = e.currency or "USD"
        amt = r2(e.amount)
        take = min(amt, max(r2(cap[cur]), 0.0))
        cap[cur] = r2(cap[cur] - take)
        rest = r2(amt - take)
        employer = (e.source_key or "").startswith("payroll_medical:") and (e.source_key or "").endswith(":employer")
        out[e.id] = (r2(take), 0.0 if employer else rest, rest if employer else 0.0)
    return out


def medical_shortfall(rows=None):
    """[(currency, bills total, deducted, treated as expense, settles net pay)] when the medical aid bills are more than the payroll deducted."""
    rows = periods() if rows is None else rows
    alloc = medical_allocation(rows)
    if not alloc:
        return []
    cur_of = {e.id: (e.currency or "USD") for e in Expense.query.filter(Expense.id.in_(list(alloc))).all()}
    out = []
    for cur in sorted(set(cur_of.values())):
        ids = [i for i, c in cur_of.items() if c == cur]
        bills = r2(sum(sum(alloc[i]) for i in ids))
        expense = r2(sum(alloc[i][1] for i in ids))
        net = r2(sum(alloc[i][2] for i in ids))
        if expense >= CENT or net >= CENT:
            out.append((cur, bills, r2(sum(r["ded"] for r in rows if r["currency"] == cur)), expense, net))
    return out


def payments(start):
    """Cash book entries (money out) under the four payroll headings dated from the link start: [{date, heading, key, amount, currency}]."""
    if start is None:
        return []
    out = []
    for en in CashEntry.query.filter(CashEntry.category.in_(list(PAYMENT_HEADINGS)), CashEntry.entry_date >= start).all():
        cur = en.account.currency if en.account else "USD"
        amt = (en.amount or 0.0) * (1 if en.direction == "out" else -1)
        out.append({"date": en.entry_date, "heading": en.category, "key": PAYMENT_HEADINGS[en.category], "amount": r2(amt), "currency": cur})
    return out


def salary_bills(start):
    """{expense id: Expense} of bills filed in the Expense register under a salary / wage / payroll category from the link start (a salary
    payment recorded before the payroll was linked). They are not a second expense: each is treated as a payment of net salaries and
    clears Net salaries payable, so nothing needs deleting."""
    if start is None:
        return {}
    return {e.id: e for e in Expense.query.filter(Expense.expense_date >= start).all()
            if not (e.source_key or "").startswith("payroll_medical:") and any(w in (e.category or "").lower() for w in SALARY_WORDS)}


def stale_payslips(period):
    """[(payslip, lines total, stored total)] for the payslips of one period whose Deduction lines are not in the totals the payslip carries:
    a line was added or removed after the last Recalculate, so the deduction never reached Other payroll deductions payable. Payslips whose
    figures were typed by hand are left alone."""
    out = []
    for x in period.payslips:
        if not x.is_auto_calculated:
            continue
        lines = r2(sum((i.amount or 0.0) for i in x.items if i.category == "Deduction"))
        stored = r2(x.other_deductions_total or 0.0)
        if abs(lines - stored) >= CENT:
            out.append((x, lines, stored))
    return out


def stale_warnings(rows=None):
    """Notes for finalized, posted periods that hold payslips whose deduction lines were never recalculated."""
    rows = periods() if rows is None else rows
    out = []
    for r in rows:
        bad = stale_payslips(r["period"])
        if bad:
            names = ", ".join((x.employee.full_name if getattr(x, "employee", None) and hasattr(x.employee, "full_name") else f"payslip {x.id}") for x, _, _ in bad[:5])
            more = f" and {len(bad) - 5} more" if len(bad) > 5 else ""
            out.append(f"{r['name']}: {len(bad)} payslip(s) ({names}{more}) have Deduction lines worth {sum(b[1] for b in bad):,.2f} but the payslip totals carry {sum(b[2] for b in bad):,.2f}, "
                       "so those deductions never reached 'Other payroll deductions payable'. Reopen the period, click Recalculate on each of those payslips, then finalize it again.")
    return out


def overlap_warnings(start):
    """Notes (not errors) when Expense-register bills are being treated as payments of what the payroll owes."""
    out = []
    bills = salary_bills(start)
    if bills:
        cats = sorted({b.category for b in bills.values()})
        total = sum(b.amount or 0.0 for b in bills.values())
        out.append(f"{len(bills)} bill(s) in the Expense register under {', '.join(repr(c) for c in cats)} (total {total:,.2f}) are dated {start.strftime('%d %b %Y')} or later. "
                   "Salaries come from the finalized payroll, so these are not counted as a second expense: each is treated as a payment of net salaries and clears "
                   "'Net salaries payable'. Check that Net salaries payable is not left in debit; if a bill is not net pay, file it under another category.")
    med = medical_register_bills(start)
    if med:
        cats = sorted({b.category for b in med.values()})
        total = sum(b.amount or 0.0 for b in med.values())
        out.append(f"{len(med)} medical aid bill(s) in the Expense register under {', '.join(repr(c) for c in cats)} (total {total:,.2f}) are dated {start.strftime('%d %b %Y')} or later. "
                   "The medical aid is already in the payroll (the employer's share in gross pay, the employees' share taken off pay), so these are not counted as a second expense: "
                   "each is treated as a payment of 'Other payroll deductions payable'. Check that account is not left in debit; if a bill is for something else, file it under another category.")
    out.extend(stale_warnings())
    for cur, bills_total, deducted, expense, net in medical_shortfall():
        parts = []
        if expense >= CENT:
            parts.append(f"{expense:,.2f} is booked as an ordinary medical aid expense")
        if net >= CENT:
            parts.append(f"{net:,.2f} (the employer's share, already in gross pay) settles 'Net salaries payable'")
        out.append(f"Medical aid bills in {cur} total {bills_total:,.2f} but the payslips took only {deducted:,.2f} off pay, and Other payroll deductions payable can only be cleared by what was deducted: "
                   + "; ".join(parts) + ". If a part of that really was deducted from staff, reopen the payroll period and enter it as a Deduction line "
                   "(not an Allowance) marked as medical aid; once the payslips carry it, the bills settle the payable instead.")
    return out


def factor(currency, rate):
    if currency in (None, "USD"):
        return 1.0
    return 1.0 / rate if rate and rate > 0 else None


def statement_items(start, end, zwg_rate):
    """What payroll adds to one year-end column of the statements: ([(fs_category, label, usd)], warnings, set of medical bill ids).
    Expenses are the periods dated inside [start, end]; the payables are everything posted up to `end` less what the Cash Book has
    paid to `end`. The ledger posts the same figures, so the two agree."""
    rows = periods()
    if not rows:
        return [], [], set()
    pstart = rows[0]["date"]
    warnings = []
    left_out = set()
    sal = er = 0.0
    acc = defaultdict(float)
    for r in rows:
        if r["date"] > end:
            continue
        f = factor(r["currency"], zwg_rate)
        if f is None or r["currency"] not in INVOICE_CURRENCIES:
            left_out.add(r["currency"])
            continue
        if start <= r["date"]:
            sal += r["gross"] * f
            er += r["employer_cost"] * f
        acc[NET] += r["net"] * f
        acc[ZIMRA] += r["paye_aids"] * f
        acc[NSSA] += r["nssa_total"] * f
        acc[DED] += r["ded"] * f
    for p in payments(pstart):
        if p["date"] > end:
            continue
        f = factor(p["currency"], zwg_rate)
        if f is None:
            left_out.add(p["currency"])
            continue
        acc[p["key"]] -= p["amount"] * f
    med = linked_medical(rows)
    if med:
        alloc = medical_allocation(rows)
        for e in Expense.query.filter(Expense.id.in_(list(med))).all():
            if e.expense_date and e.expense_date <= end:
                f = factor(e.currency, zwg_rate)
                if f is not None:
                    a = alloc.get(e.id, (e.amount or 0.0, 0.0, 0.0))
                    acc[DED] -= a[0] * f
                    acc[NET] -= a[2] * f
    for e in salary_bills(pstart).values():
        if e.expense_date and e.expense_date <= end:
            f = factor(e.currency, zwg_rate)
            if f is not None:
                acc[NET] -= (e.amount or 0.0) * f
    if left_out:
        warnings.append(f"Payroll in {', '.join(sorted(left_out))} was left out of the statements: no rate is entered for this year-end.")
    warnings.extend(overlap_warnings(pstart))
    items = []
    if abs(sal) >= CENT:
        items.append(("admin_expenses", "Salaries and wages (payroll)", r2(sal)))
    if abs(er) >= CENT:
        items.append(("admin_expenses", "Employer NSSA and APWCS contributions (payroll)", r2(er)))
    for key in (NET, ZIMRA, NSSA, DED):
        if abs(acc[key]) >= CENT:
            items.append(("other_current_liabilities", PAYABLE_NAMES[key] + " (payroll)", r2(acc[key])))
    return items, warnings, set(med)


def net_pay_differences():
    """Periods whose payslips' stored net pay is not gross less deductions (a net pay typed by hand)."""
    return [(r["name"], r["net_stored"], r["net"]) for r in periods() if abs(r["net_stored"] - r["net"]) >= 0.01]


def posting_preview(row):
    """The legs a period posts, as [(account name, debit, credit)] for the period page."""
    names = {SAL: "Salaries and wages", ER: "Employer NSSA and APWCS contributions", ZIMRA: PAYABLE_NAMES[ZIMRA],
             NSSA: PAYABLE_NAMES[NSSA], DED: PAYABLE_NAMES[DED], NET: PAYABLE_NAMES[NET]}
    legs = [(names[SAL], row["gross"], 0.0), (names[ER], row["employer_cost"], 0.0),
            (names[ZIMRA], 0.0, row["paye_aids"]), (names[NSSA], 0.0, row["nssa_total"]),
            (names[DED], 0.0, row["ded"]), (names[NET], 0.0, row["net"])]
    return [l for l in legs if abs(l[1]) >= CENT or abs(l[2]) >= CENT]


def row_for(period):
    """The posting row of one period if it posts to the books, else None."""
    for r in periods():
        if r["id"] == period.id:
            return r
    return None


def cash_line_notes():
    """{cash book line key: (badge, note)} for payments of bills that are NOT an expense because the payroll carries the cost: salary bills
    filed in the Expense register (they pay off Net salaries payable) and medical aid bills posted from payroll (they pay off Other payroll
    deductions payable). The cash book shows these as payroll payments instead of expenses."""
    from models import SupplierPayment
    rows = periods()
    salary = salary_bills(link_start(rows))
    alloc = medical_allocation(rows)
    medical = {i for i in linked_medical(rows) if (alloc.get(i, (0.0, 0.0, 0.0))[1] < CENT)}
    out = {}
    def mark(ids, badge, note):
        for i in ids:
            out[f"expense:{i}"] = (badge, note)
        if ids:
            for sp in SupplierPayment.query.filter(SupplierPayment.expense_id.in_(list(ids))).all():
                out[f"supplierpay:{sp.id}"] = (badge, note)
    mark(set(salary), "Salaries paid", f"Payroll payment - clears {PAYABLE_NAMES[NET]}; not an expense")
    mark(set(medical), "Medical aid", f"Payroll payment - clears {PAYABLE_NAMES[DED]}; not an expense")
    return out


def expense_flags():
    """{expense id: "salary" | "medical"} for bills that are NOT an expense because the payroll carries the cost."""
    rows = periods()
    alloc = medical_allocation(rows)
    out = {i: "medical" for i in linked_medical(rows) if (alloc.get(i, (0.0, 0.0, 0.0))[1] < CENT)}   # a bill the payroll does not fully cover stays an expense
    out.update({i: "salary" for i in salary_bills(link_start(rows))})
    return out


FLAG_TAGS = {"salary": ("Paid via payroll", f"Not an expense: a payment of {PAYABLE_NAMES[NET]}"),
             "medical": ("Paid via payroll", f"Not a second expense: settles {PAYABLE_NAMES[DED]}")}


def cost_rows(currency):
    """The payroll's own cost as expense-shaped rows for the analytics (salaries and the employer's NSSA/APWCS), in `currency`."""
    out = []
    for r in periods():
        if r["currency"] != currency:
            continue
        for cat, amt in (("Salaries and wages (payroll)", r["gross"]), ("Employer NSSA and APWCS (payroll)", r["employer_cost"])):
            if abs(amt) >= CENT:
                out.append({"id": None, "date": r["date"], "supplier": "Staff payroll", "category": cat, "amount": amt, "paid": r["date"],
                            "due": r["date"], "description": r["name"], "settle": [], "payroll": "cost"})
    return out
