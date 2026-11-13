"""Paying the medical aid that was deducted from pay is not a second expense: the Cash Book heading 'Medical aid and other deductions paid'
clears Other payroll deductions payable, and a medical aid bill filed by hand in the Expense register from the date the payroll link began is treated
as a payment of that payable too (bills dated earlier stay ordinary expenses). Ledger, statements, Expense register, analytics and cash book agree."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import date
from app import create_app
from extensions import db
from models import *
import firm_ledger as fl, firm_statements as fs, firm_payroll as fp, analytics
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005):
    return a is not None and abs(a - b) <= tol
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
F = "/firm-finance"

with app.app_context():
    mp = User(username="mp", name="Managing Partner", role="admin"); mp.set_password("x")
    db.session.add(mp); db.session.commit()
    emps = [PayrollEmployee(scope="internal", full_name=n, basic_salary=s, created_by_id=mp.id) for n, s in (("Tendai Moyo", 1000.0), ("Rudo Chari", 1200.0))]
    db.session.add_all(emps); db.session.commit()
    per = PayrollPeriod(scope="internal", name="September 2026 Staff", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), pay_date=date(2026, 9, 30),
                        currency="USD", status="Finalized", created_by_id=mp.id)
    db.session.add(per); db.session.commit()
    for e, (gross, paye, aids, ne, nr, ap, ded) in zip(emps, [(1000.0, 100.0, 3.0, 45.0, 45.0, 12.5, 40.0), (1200.0, 150.0, 4.5, 54.0, 54.0, 15.0, 55.0)]):
        db.session.add(Payslip(period_id=per.id, employee_id=e.id, basic_salary=gross, gross_pay=gross, paye_tax=paye, aids_levy=aids, nssa_employee=ne,
                               nssa_employer=nr, apwcs=ap, other_deductions_total=ded, net_pay=round(gross - paye - aids - ne - ded, 2)))
    db.session.commit()
    bank = CashAccount(name="Bank (USD)", kind="Bank", currency="USD", opening_balance=10000.0, opening_date=date(2026, 1, 1), default_in=True, default_out=True)
    ye = FirmYearEnd(period_end=date(2026, 12, 31), opening_nil=True, tax_manual=True)
    db.session.add_all([bank, ye]); db.session.commit()
    ids = dict(mp=mp.id, bank=bank.id)
M = login(ids["mp"])

def bal(L, key, as_at=date(2026, 12, 31)):
    a = L.by_key.get(key)
    if a is None: return 0.0
    return round(fl.account_balances(L, as_at, date(2026, 1, 1)).get(a.id, 0.0), 2)
def statements():
    yes = FirmYearEnd.query.all()
    L = fl.build_ledger(year_ends=yes)
    cols = fs.build_chain(sorted(yes, key=lambda y: y.period_end), *fs.load_register())
    return L, cols[0]

with app.app_context():
    L = fl.build_ledger()
    check(near(bal(L, "PAY_DED"), -95), "the payroll owes 95 of deductions (medical aid etc.) to be paid over")

# 1. the cash book heading
check("Medical aid and other deductions paid" in CASH_ENTRY_CATEGORIES, "the cash book offers the 'Medical aid and other deductions paid' heading")
r = M.get(F + "/cash-book/entries/new")
check(r.status_code == 200 and b"Medical aid and other deductions paid - clears Other payroll deductions payable" in r.data, "the new-entry form names the account it clears")
with app.app_context():
    db.session.add(CashEntry(account_id=ids["bank"], entry_date=date(2026, 10, 15), direction="out", amount=95, category="Medical aid and other deductions paid", description="PSMAS"))
    db.session.commit()
    L, col = statements()
    check(near(bal(L, "PAY_DED"), 0), "paying 95 under the heading clears Other payroll deductions payable")
    check(near(bal(L, "SUSP_CASH"), 0), "...and nothing is left in the cash clearing account")
    check(not any(str(a).startswith("EXP:Medical") for a in L.by_key), "...and no medical aid expense account appears")
    check(near(col["totals"]["admin_expenses"], 2326.5), f"statement expenses stay at payroll cost 2,326.50 ({col['totals']['admin_expenses']})")
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees to the statements")
r = M.get(F + f"/cash-book?account={ids['bank']}&from=2026-01-01&to=2026-12-31")
check(r.status_code == 200 and r.data.count(b"Medical aid and other deductions paid") >= 1, "the cash book shows the line under the payroll heading")
with app.app_context():
    CashEntry.query.delete(); db.session.commit()

# 2. a bill filed by hand in the Expense register
with app.app_context():
    db.session.add(Expense(expense_date=date(2026, 10, 15), supplier="PSMAS", category="Medical Aid", amount=95, currency="USD", status="Paid", paid_date=date(2026, 10, 15)))
    db.session.commit()
    L, col = statements()
    check(near(bal(L, "PAY_DED"), 0), "a medical aid bill filed by hand (95) clears Other payroll deductions payable")
    ex = L.by_key.get("EXP:Medical Aid")
    check(ex is None or near(fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1)).get(ex.id, 0.0), 0), "...with no second expense in the ledger")
    check(any("medical aid bill" in w.lower() for w in L.warnings), "...and the ledger notes it")
    check(near(col["totals"]["admin_expenses"], 2326.5), f"statement expenses stay at 2,326.50 ({col['totals']['admin_expenses']})")
    check(any("medical aid bill" in w.lower() for w in col["warnings"]), "...and the statements say so too")
    check(fl.reconcile(L, col)["different"] == 0, "ledger and statements agree")
    rep = analytics.build_report("USD", "custom", date(2026, 9, 1), date(2026, 12, 31), date(2026, 12, 31))
    kp = rep.get("kpis") or rep.get("kpi") or rep.get("totals")
    check(near(kp["expenses"], 2326.5), f"analytics expenses exclude the medical aid bill ({kp['expenses']})")
r = M.get(F + f"/cash-book?account={ids['bank']}&from=2026-01-01&to=2026-12-31")
check(b"clears Other payroll deductions payable; not an expense" in r.data, "the cash book labels the hand-filed bill a payroll payment, not an expense")
r = M.get("/expenses/")
check(r.status_code == 200 and b"Paid via payroll" in r.data and b"Other payroll deductions payable" in r.data, "the Expense register tags it 'Paid via payroll' (Other payroll deductions payable)")

# 3. a bill dated before the link began is an ordinary expense
with app.app_context():
    db.session.add(Expense(expense_date=date(2026, 8, 20), supplier="PSMAS", category="Medical Aid", amount=60, currency="USD", status="Paid", paid_date=date(2026, 8, 20)))
    db.session.commit()
    L, col = statements()
    check(near(bal(L, "PAY_DED"), 0), "an earlier bill (before the link began) does not touch the payable")
    check(near(col["totals"]["admin_expenses"], 2386.5), f"...and is an ordinary expense: 2,326.50 + 60 ({col['totals']['admin_expenses']})")
    check(fl.reconcile(L, col)["different"] == 0, "ledger and statements still agree")

# 4. no payroll linked -> every medical aid bill is an ordinary expense
with app.app_context():
    PayrollPeriod.query.get(1).status = "Draft"; db.session.commit()
    L, col = statements()
    check(near(col["totals"]["admin_expenses"], 155), f"with no payroll posted, medical aid bills are plain expenses (155: {col['totals']['admin_expenses']})")
    check(fp.linked_medical() == {}, "...and none is treated as linked")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
