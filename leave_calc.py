"""Leave balance accrual engine.

Kept separate from hr.py (the routes) the same way payroll_calc.py is kept
separate from payroll.py - so the same balance figures shown on screen are
exactly what a leave-taken entry is validated against.

Two accrual models, matching how Zimbabwe's Labour Act [Chapter 28:01]
actually treats different leave types - see LeaveType's docstring and
LEAVE_TYPES_STATUTORY_ZW in models.py:

  - Accumulative (e.g. Annual/Vacation Leave, s.14A): `accrued_days` grows
    by `accrual_days_per_month` for every completed month of continuous
    service (from `employee.date_joined`), capped so the running balance
    never exceeds `max_accumulation_days` - once at the cap, no further
    days accrue until some are taken (exactly per the statute), and past
    months skipped at the cap are never retroactively credited later.
  - Non-accumulative (e.g. Sick, Special/Compassionate, Maternity): granted
    as a fresh lump sum of `annual_entitlement_days` at the start of each
    leave year (a calendar year here, in the absence of a firm-specified
    different leave-year start) - unused days simply lapse, they never
    carry into `accrued_days` for the next year.

Neither path ever touches `brought_forward` - that's a one-time, manually
entered opening balance (see hr.set_leave_brought_forward), left alone here
from the moment it's set.

Only `is_accumulative` leave types get a tracked per-employee LeaveBalance
at all (confirmed with the Managing Partner: Annual/Vacation Leave is the
one obligation the firm actively accrues and tracks a running balance for,
continuing to run from each employee's own date_joined). A non-accumulative
leave type (Sick/Special/Compassionate/Maternity) is a statutory PROVISION
instead - defined on the Leave Types screen and still capturable on a Time
Sheet, but with no LeaveBalance row, accrual or cap kept per employee - see
sync_all_balances_for_employee below and hr._add_leave_entry.
"""
from datetime import date

from extensions import db
from models import LeaveType, LeaveBalance


def months_between(start_date, end_date):
    """Completed whole calendar months between two dates - e.g. 15 Jan to
    10 Mar is 1 completed month (not 2, since the 10th is before the 15th
    in March); 15 Jan to 15 Mar is exactly 2. Returns 0 if end_date is on or
    before start_date, or if start_date is missing."""
    if not start_date or not end_date or end_date <= start_date:
        return 0
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if end_date.day < start_date.day:
        months -= 1
    return max(0, months)


def _advance_months(a_date, n_months):
    """a_date + n_months, clamping the day to 28 to sidestep invalid dates
    (30 Jan + 1 month, etc.) - accrual only needs month-level precision, not
    the exact day of month, so this small simplification is harmless."""
    total = a_date.year * 12 + (a_date.month - 1) + n_months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(a_date.day, 28))


def get_or_create_balance(employee, leave_type):
    """The employee's LeaveBalance row for this LeaveType, created (and
    flushed, so it has an id) on first use rather than pre-seeded for every
    employee/leave-type pair up front."""
    balance = LeaveBalance.query.filter_by(employee_id=employee.id, leave_type_id=leave_type.id).first()
    if not balance:
        balance = LeaveBalance(employee_id=employee.id, leave_type_id=leave_type.id)
        db.session.add(balance)
        db.session.flush()
    return balance


def sync_leave_balance(employee, leave_type, balance, as_of=None):
    """Bring one LeaveBalance up to date as of `as_of` (today by default).
    Safe to call as often as needed (e.g. every time a balance is viewed or
    a leave entry is about to be validated) - it only ever adds newly
    completed months (accumulative) or rolls into a newly started leave
    year (non-accumulative), never double-counts what's already been
    applied. Does NOT commit - callers batch this with whatever else
    they're doing and commit once."""
    as_of = as_of or date.today()

    if leave_type.is_accumulative:
        if not employee.date_joined or leave_type.accrual_days_per_month <= 0:
            return
        since = balance.last_accrual_date or employee.date_joined
        completed_months = months_between(since, as_of)
        if completed_months <= 0:
            return
        for _ in range(completed_months):
            if leave_type.max_accumulation_days is not None and balance.current_balance >= leave_type.max_accumulation_days:
                break  # at the cap - no further accrual until leave is taken (Labour Act s.14A)
            balance.accrued_days = round((balance.accrued_days or 0.0) + leave_type.accrual_days_per_month, 2)
        balance.last_accrual_date = _advance_months(since, completed_months)
    else:
        if balance.leave_year != as_of.year:
            balance.leave_year = as_of.year
            balance.taken_days = 0.0
            balance.accrued_days = leave_type.annual_entitlement_days or 0.0


def sync_all_balances_for_employee(employee, as_of=None, commit=True):
    """Returns [(LeaveType, LeaveBalance), ...] for every active ACCUMULATIVE
    LeaveType only (e.g. Annual/Vacation Leave), each brought up to date
    first. A non-accumulative LeaveType (Sick/Special/Maternity) never gets
    a row here - see provision_leave_types() for those instead; they're
    statutory provisions, not something the firm tracks a per-employee
    balance for. `commit=False` lets a caller that's about to make a
    further change (e.g. recording leave taken) fold the sync into its own
    single commit."""
    results = []
    for leave_type in LeaveType.query.filter_by(is_active=True, is_accumulative=True).order_by(LeaveType.order).all():
        balance = get_or_create_balance(employee, leave_type)
        sync_leave_balance(employee, leave_type, balance, as_of=as_of)
        results.append((leave_type, balance))
    if commit:
        db.session.commit()
    return results


def provision_leave_types():
    """Every active NON-accumulative LeaveType (Sick/Special/Compassionate/
    Maternity, by default) - statutory provisions the firm defines once and
    makes available to capture on a Time Sheet, but never tracks a
    per-employee LeaveBalance for (no accrual, no running total, no cap)."""
    return LeaveType.query.filter_by(is_active=True, is_accumulative=False).order_by(LeaveType.order).all()
