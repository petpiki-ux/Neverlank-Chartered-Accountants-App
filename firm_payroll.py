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

The payables are cleared by the Cash Book, with three headings for money out:
    "Salaries paid"              -> Net salaries payable
    "PAYE and other taxes paid"  -> PAYE and AIDS levy payable
    "NSSA contributions paid"    -> NSSA contributions payable
and the medical aid bills posted from a period (payroll_medical.py) clear Other payroll deductions payable instead of being a
second expense: the employer's share is already in the gross pay, the employees' share is money taken off their pay.

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
PAYMENT_HEADINGS = {"Salaries paid": NET, "PAYE and other taxes paid": ZIMRA, "NSSA contributions paid": NSSA}
PAYABLE_NAMES = {NET: "Net salaries payable", ZIMRA: "PAYE and AIDS levy payable", NSSA: "NSSA contributions payable",
                 DED: "Other payroll deductions payable"}
SALARY_WORDS = ("salar", "wage", "payroll")


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


def linked_medical(rows=None):
    """{expense id: period id} of the medical aid bills posted from a period that is itself posted to the books. Their cost is not a
    second expense: it clears Other payroll deductions payable."""
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
    return out


def payments(start):
    """Cash book entries (money out) under the three payroll headings dated from the link start: [{date, heading, key, amount, currency}]."""
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


def overlap_warnings(start):
    """A note (not an error) when Expense-register bills are being treated as salary payments."""
    bills = salary_bills(start)
    if not bills:
        return []
    cats = sorted({b.category for b in bills.values()})
    total = sum(b.amount or 0.0 for b in bills.values())
    return [f"{len(bills)} bill(s) in the Expense register under {', '.join(repr(c) for c in cats)} (total {total:,.2f}) are dated {start.strftime('%d %b %Y')} or later. "
            "Salaries come from the finalized payroll, so these are not counted as a second expense: each is treated as a payment of net salaries and clears "
            "'Net salaries payable'. Check that Net salaries payable is not left in debit; if a bill is not net pay, file it under another category."]


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
        for e in Expense.query.filter(Expense.id.in_(list(med))).all():
            if e.expense_date and e.expense_date <= end:
                f = factor(e.currency, zwg_rate)
                if f is not None:
                    acc[DED] -= (e.amount or 0.0) * f
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
