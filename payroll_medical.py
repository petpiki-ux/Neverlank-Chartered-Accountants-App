"""Medical aid due to the society, posted from Neverlank's own payroll to Firm Finance > Suppliers.

Payroll and Firm Finance are separate in this app. A payslip line never creates a bill, so the amount owed to the medical aid
society would otherwise be typed a second time. This reads the medical aid lines on a FINALIZED internal payroll period and creates
the supplier bill(s) from them:

* employer's share  - payslip lines marked "Medical aid - employer's share"  -> one bill, category "Medical aid - employer share";
* employees' share  - payslip lines marked "Medical aid - employee's share (taken off pay)" -> one bill, category
  "Medical aid - deducted from employees".

Both are ordinary Expense-register bills for the chosen supplier, unpaid, so they flow to Trade payables, the supplier ledger, the
ageing and the cash book exactly as any bill does, and are paid from the supplier's page. Each bill carries a source key
(payroll_medical:<period>:<part>) so a period can never be posted twice.

Whether the employees' share should be an expense at all depends on how salaries are recorded in the ledger (net of the deduction, or gross).
That is the firm's accounting decision, so each part can be posted or left out. Nothing here decides a tax treatment.
"""
from datetime import date, timedelta

from extensions import db
from models import Expense, PayrollTaxSettings, Supplier, INVOICE_CURRENCIES

EMPLOYER_CATEGORY = "Medical aid - employer share"
EMPLOYEE_CATEGORY = "Medical aid - deducted from employees"
PARTS = {"employer": EMPLOYER_CATEGORY, "employee": EMPLOYEE_CATEGORY}
CENT = 0.005


def r2(v):
    return round(float(v or 0.0), 2)


def source_key(period_id, part):
    return f"payroll_medical:{period_id}:{part}"


def medical_lines(period):
    """Per employee: the employee's and employer's medical aid amounts on this period's payslips, and the lines that mention
    medical aid without being marked (so the Managing Partner can see what was NOT counted)."""
    rows, unmarked = [], []
    for ps in period.payslips:
        ee = er = 0.0
        for it in ps.items:
            if it.category == "Tax Credit":
                continue       # a medical expenses credit reduces PAYE; it is not owed to the society
            if it.medical_aid == "employee":
                ee += it.amount or 0.0
            elif it.medical_aid == "employer":
                er += it.amount or 0.0
            elif "medical" in (it.label or "").lower():
                unmarked.append({"employee": ps.employee.full_name, "label": it.label, "category": it.category, "amount": r2(it.amount)})
        if ee or er:
            rows.append({"employee": ps.employee.full_name, "payslip_id": ps.id, "employee_share": r2(ee), "employer_share": r2(er)})
    return {"rows": rows, "employee_total": r2(sum(r["employee_share"] for r in rows)),
            "employer_total": r2(sum(r["employer_share"] for r in rows)), "unmarked": unmarked}


def posted_bills(period):
    """{part: Expense} already created for this period."""
    out = {}
    for part in PARTS:
        e = Expense.query.filter_by(source_key=source_key(period.id, part)).first()
        if e is not None:
            out[part] = e
    return out


def default_supplier():
    s = PayrollTaxSettings.query.get(1)
    return Supplier.query.get(s.medical_aid_supplier_id) if s is not None and s.medical_aid_supplier_id else None


def _category(name, user):
    from finance import add_category, category_names
    if name in category_names(active_only=False):
        return name
    cat, err = add_category(name, user)
    return cat or "Other"


def post_period(period, supplier, due, parts, user):
    """Create the bill(s) for a finalized internal period. parts = subset of {"employer", "employee"}.
    Returns (list of Expense, error). Nothing is committed; the caller commits."""
    from suppliers import status_problem
    if period.scope != "internal":
        return [], "Only Neverlank's own staff payroll is posted to the firm's books."
    if period.status != "Finalized":
        return [], "Finalize the period first, so the figures cannot change after the bill is posted."
    if period.currency not in INVOICE_CURRENCIES:
        return [], f"The period is in {period.currency}, which the Expense register does not handle."
    if supplier is None:
        return [], "Choose the medical aid society (a supplier)."
    problem = status_problem(supplier, "bill")
    if problem:
        return [], problem
    parts = [p for p in ("employer", "employee") if p in set(parts or ())]
    if not parts:
        return [], "Choose at least one part to post."
    already = posted_bills(period)
    clash = [p for p in parts if p in already]
    if clash:
        return [], "Already posted for this period: remove the existing bill first if the figures have changed."
    tot = medical_lines(period)
    amounts = {"employer": tot["employer_total"], "employee": tot["employee_total"]}
    parts = [p for p in parts if amounts[p] > CENT]
    if not parts:
        return [], "There is no medical aid amount marked on this period's payslips for the parts chosen."
    when = period.pay_date or period.period_end or date.today()
    due = due or (when + timedelta(days=int(supplier.payment_terms_days or 0)))
    made = []
    for part in parts:
        label = "employer's share of medical aid" if part == "employer" else "employees' share of medical aid (deducted from pay, owed to the society)"
        e = Expense(expense_date=when, supplier=supplier.name, supplier_id=supplier.id, category=_category(PARTS[part], user),
                    amount=amounts[part], currency=period.currency, status="Unpaid", due_date=due,
                    description=f"{period.name} - {label}", reference=f"PAYROLL-{period.id}-MED-{'ER' if part == 'employer' else 'EE'}",
                    source_key=source_key(period.id, part), created_by_id=getattr(user, "id", None))
        db.session.add(e)
        made.append(e)
    db.session.flush()
    return made, None


def remove_posted(period):
    """Delete this period's medical aid bills so they can be posted again. Refused if anything has been paid on them.
    Returns an error message or None."""
    import cashbook
    bills = posted_bills(period)
    if not bills:
        return "Nothing has been posted for this period."
    for e in bills.values():
        if e.status == "Paid" or e.supplier_payments:
            return "A payment has been recorded on one of these bills. Delete the payment first."
    for e in bills.values():
        cashbook.forget(f"expense:{e.id}")
        db.session.delete(e)
    return None


def out_of_date(period):
    """Parts whose posted bill no longer equals what the payslips now say (payslips edited after posting)."""
    tot = medical_lines(period)
    now = {"employer": tot["employer_total"], "employee": tot["employee_total"]}
    return [p for p, e in posted_bills(period).items() if abs(r2(e.amount) - now[p]) > CENT]
