"""Other payroll deductions payable (2044) is a liability and is never driven into debit by medical aid bills: bills settle it only up to what the payslips
deducted (oldest first, less any Cash Book payment under the medical aid heading); the rest is an ordinary medical aid expense, with a note explaining it.
Ledger, statements and the Expense register agree."""
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
    for e, (gross, paye, aids, ne, nr, ap, ded) in zip(emps, [(1000.0, 100.0, 3.0, 45.0, 45.0, 12.5, 0.0), (1200.0, 150.0, 4.5, 54.0, 54.0, 15.0, 0.0)]):
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
def medical_exp(L):
    return sum(bal(L, k) for k in L.by_key if str(k).startswith("EXP:Medical"))

# the reported case: the payslips took nothing off pay (no credit in 2044) but two medical aid bills were posted / filed
with app.app_context():
    per = PayrollPeriod.query.get(1)
    db.session.add(Expense(expense_date=date(2026, 10, 31), supplier="FBC Health Insurance", category="Medical aid - deducted from employees", amount=780, currency="USD", status="Unpaid",
                           description="October 2026 - employees' share of medical aid", source_key=f"payroll_medical:{per.id}:employee"))
    db.session.add(Expense(expense_date=date(2026, 11, 3), supplier="FBC Insurance", category="Medical aid - deducted from employees", amount=2340, currency="USD", status="Unpaid", description="Medical Aid Contributions"))
    db.session.commit()
    L, col = statements()
    check(bal(L, "PAY_DED") == 0, "with nothing deducted, 2044 is nil - never a debit (%s)" % bal(L, "PAY_DED"))
    check(abs(medical_exp(L) - 3120) < 0.005, "...and the 3,120 of bills is ordinary medical aid expense (%s)" % medical_exp(L))
    check(any("took only 0.00 off pay" in w for w in L.warnings), "...with a note saying why")
    check(any("took only 0.00 off pay" in w for w in col["warnings"]), "...also on the statements")
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements")
    check(abs(col["totals"]["admin_expenses"] - (2326.5 + 3120)) < 0.005, "statement expenses = payroll 2,326.50 + medical aid 3,120 (%s)" % col["totals"]["admin_expenses"])
    check(fp.expense_flags() == {}, "neither bill is tagged 'Paid via payroll'")
r = M.get("/expenses/")
check(r.status_code == 200 and b"Paid via payroll" not in r.data, "the Expense register does not call them payroll payments")

# the payroll-posted bill and the hand-filed bill both owed to the society: a possible double count is pointed out
with app.app_context():
    L, col = statements()
    check(any("Possible double count" in w for w in L.warnings) and any("Possible double count" in w for w in col["warnings"]), "a payroll-posted bill plus a hand-filed bill for the society gets a 'possible double count' note")

# the payslips now carry 780 of deductions: that clears the first bill; the other stays an expense, and 2044 is still never a debit
with app.app_context():
    for ps in Payslip.query.all():
        ps.other_deductions_total = 390.0; ps.net_pay = round(ps.net_pay - 390.0, 2)
    db.session.commit()
    L, col = statements()
    check(abs(bal(L, "PAY_DED")) < 0.005, "deductions of 780 and the first bill (780) leave 2044 at nil (%s)" % bal(L, "PAY_DED"))
    check(abs(medical_exp(L) - 2340) < 0.005, "...and only the second bill (2,340) is expense (%s)" % medical_exp(L))
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements")
    check(abs(col["totals"]["admin_expenses"] - (2326.5 + 2340)) < 0.005, "statement expenses = 2,326.50 + 2,340 (%s)" % col["totals"]["admin_expenses"])
    check(set(fp.expense_flags()) and all(v == "medical" for v in fp.expense_flags().values()) and len(fp.expense_flags()) == 1, "only the first bill is 'Paid via payroll'")

# a bill that is partly covered is split: covered part clears the payable, the rest is expense
with app.app_context():
    Expense.query.filter(Expense.supplier == "FBC Health Insurance").delete(); db.session.commit()
    ex = Expense.query.filter_by(supplier="FBC Insurance").first(); ex.amount = 1000.0; db.session.commit()
    L, col = statements()
    check(abs(bal(L, "PAY_DED")) < 0.005 and abs(medical_exp(L) - 220) < 0.005, "a 1,000 bill against 780 deducted: 780 clears 2044, 220 is expense (%s / %s)" % (bal(L, "PAY_DED"), medical_exp(L)))
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements (split bill)")
    check(abs(bal(L, "AP") + 1000) < 0.005, "the society is still owed the whole 1,000 in trade payables (%s)" % bal(L, "AP"))
    # a Cash Book payment under the heading uses up the deductions first
    ex.amount = 500.0
    db.session.add(CashEntry(account_id=ids["bank"], entry_date=date(2026, 11, 10), direction="out", amount=600, category="Medical aid and other deductions paid", description="PSMAS")); db.session.commit()
    L, col = statements()
    check(abs(bal(L, "PAY_DED")) < 0.005, "780 deducted, 600 paid by cash book and 180 cleared by the bill leave 2044 at nil, not a debit (%s)" % bal(L, "PAY_DED"))
    check(abs(medical_exp(L) - 320) < 0.005, "...a 500 bill only has the remaining 180 to clear, so 320 is expense (%s)" % medical_exp(L))
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements (cash book + bill)")

# a deduction line added without Recalculate is detected: warned about on the ledger, and the period cannot be finalized until it is recalculated
with app.app_context():
    per = PayrollPeriod.query.get(1)
    for p_ in per.payslips:       # the lines behind the 390 each payslip carries
        db.session.add(PayslipItem(payslip_id=p_.id, category="Deduction", label="Medical aid - employee", amount=390.0, medical_aid="employee"))
    db.session.commit()
    check(fp.stale_payslips(PayrollPeriod.query.get(1)) == [], "payslips whose Deduction lines match their totals are fine")
    ps1 = per.payslips[0]
    db.session.add(PayslipItem(payslip_id=ps1.id, category="Deduction", label="Medical aid top-up", amount=250.0, medical_aid="employee")); db.session.commit()
    bad = fp.stale_payslips(PayrollPeriod.query.get(1))
    check(len(bad) == 1 and abs(bad[0][1] - 640.0) < 0.005 and abs(bad[0][2] - 390.0) < 0.005, "the payslip whose lines (640) differ from its totals (390) is found")
    L, col = statements()
    check(any("click Recalculate" in w for w in L.warnings), "...and the ledger says to reopen the period and click Recalculate")
    check(any("click Recalculate" in w for w in col["warnings"]), "...also on the statements")
    pid = per.id
r = M.post(f"/payroll/periods/{pid}/finalize", follow_redirects=True)      # reopen (Finalized -> Draft): always allowed
with app.app_context():
    check(PayrollPeriod.query.get(pid).status == "Draft", "a finalized period can be reopened")
r = M.post(f"/payroll/periods/{pid}/finalize", follow_redirects=True)
with app.app_context():
    check(PayrollPeriod.query.get(pid).status == "Draft" and b"Not finalized" in r.data and b"Recalculate" in r.data, "finalizing is refused while a payslip's totals do not carry its Deduction lines")
    ps1 = PayrollPeriod.query.get(pid).payslips[0]
    ps1.other_deductions_total = 640.0; db.session.commit()
r = M.post(f"/payroll/periods/{pid}/finalize", follow_redirects=True)
with app.app_context():
    check(PayrollPeriod.query.get(pid).status == "Finalized", "once the totals carry the line, it finalizes")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
