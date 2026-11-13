"""[journal] Other payroll deductions payable (2044) is a liability and is never driven into debit by medical aid bills: bills settle it only up to what the payslips
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
    for e, (gross, paye, aids, ne, nr, ap, ded) in zip(emps, [(1000.0, 100.0, 3.0, 45.0, 45.0, 12.5, 390.0), (1200.0, 150.0, 4.5, 54.0, 54.0, 15.0, 390.0)]):
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


# September's 780 was deducted; the FBC payment of 2,340 (August, September, October) is filed by hand. August's 780 is not in the payroll.
with app.app_context():
    db.session.add(Expense(expense_date=date(2026, 11, 3), supplier="FBC Insurance", category="Medical Aid Contributions", amount=2340, currency="USD", status="Paid", description="Aug, Sep, Oct"))
    db.session.commit()
    L, col = statements()
    exp_before = sum(bal(L, k) for k in L.by_key if str(k).startswith("EXP:Medical"))
    check(abs(exp_before - 1560) < 0.005, "without August in the books only the 780 deducted clears 2044; 1,560 is expense (%s)" % exp_before)

    # August: a posted journal  Dr Retained earnings 780 / Cr Other payroll deductions payable 780
    accts = {a.system_key: a.id for a in FirmAccount.query.all() if a.system_key}
    j = FirmJournal(number="JE-2026-0001", journal_date=date(2026, 8, 31), description="August 2026 medical aid deducted from employees", status="posted")
    j.lines.append(FirmJournalLine(account_id=accts["RE"], debit=780.0, credit=0.0)); j.lines.append(FirmJournalLine(account_id=accts["PAY_DED"], debit=0.0, credit=780.0))
    db.session.add(j); db.session.commit()
    check(abs(fp.journal_deductions() - 780) < 0.005, "the journal's 780 credit to Other payroll deductions payable is counted as deducted")
    L, col = statements()
    check(abs(bal(L, "PAY_DED")) < 0.005, "the 2,340 payment now clears 2044 exactly (780 September + 780 August journalled... %s)" % bal(L, "PAY_DED"))
    m = sum(bal(L, k) for k in L.by_key if str(k).startswith("EXP:Medical"))
    check(abs(m - 780) < 0.005, "...only the third 780 is still expense, because 2044 holds 1,560 in all (%s)" % m)
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements")
    # a second journal for October's 780 (the October period is not in the app either) leaves nothing as expense
    j2 = FirmJournal(number="JE-2026-0002", journal_date=date(2026, 10, 31), description="October 2026 medical aid deducted", status="posted")
    j2.lines.append(FirmJournalLine(account_id=accts["RE"], debit=780.0, credit=0.0)); j2.lines.append(FirmJournalLine(account_id=accts["PAY_DED"], debit=0.0, credit=780.0))
    db.session.add(j2); db.session.commit()
    L, col = statements()
    m = sum(bal(L, k) for k in L.by_key if str(k).startswith("EXP:Medical"))
    check(abs(bal(L, "PAY_DED")) < 0.005 and abs(m) < 0.005, "with 2,340 credited in all, the payment clears 2044 to nil and nothing is expense (%s / %s)" % (bal(L, "PAY_DED"), m))
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements (all journalled)")
    check(not any("took only" in w for w in L.warnings), "...and no shortfall note remains")
    lines = [x["name"] for code in col["accounts"] for x in col["accounts"][code]]
    check(not any("Journal" in n or "Cash book -" in n or "Bills charged" in n for n in lines), "no line in the statements or notes is labelled as a journal, cash book or bill posting")
    ded = [x for x in col["accounts"]["other_current_liabilities"] if x["name"].startswith("Other payroll deductions payable")]
    check(len(ded) <= 1, "the journals sit on the one Other payroll deductions payable line (%s)" % ded)

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
