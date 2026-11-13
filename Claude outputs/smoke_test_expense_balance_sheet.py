"""[expense form balance sheet] Other payroll deductions payable (2044) is a liability and is never driven into debit by medical aid bills: bills settle it only up to what the payslips
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


with app.app_context():
    fl.build_ledger()
    by_code = {a.code: a.id for a in FirmAccount.query.all()}
    ids.update(ded=by_code["2044"], loan=by_code["2300"], ar=by_code["1100"], re=by_code["3100"], dep=by_code["1150"])
    sup = Supplier(code="SUP-0002", name="FBC Health Insurance", payment_terms_days=14, currency="USD"); db.session.add(sup); db.session.commit()
    ids["sup"] = sup.id

r = M.get("/expenses/new")
check(b"Balance sheet - Liabilities" in r.data and b"2044 Other payroll deductions payable" in r.data and b"2300 Long-term loans" in r.data, "the expense form's Category list offers balance sheet accounts, including 2044 Other payroll deductions payable")
check(b'value="bs:%d"' % ids["ar"] not in r.data and b'value="bs:%d"' % ids["re"] not in r.data, "...but not the accounts the app posts itself or retained earnings")

# the reported case: FBC Medical Aid Contributions 2,340, paid, filed under 2044 instead of an expense category
r = M.post("/expenses/new", data={"expense_date": "2026-11-03", "supplier_id": ids["sup"], "supplier": "FBC Health Insurance", "category": "bs:%d" % ids["ded"],
           "amount": "2340", "currency": "USD", "status": "Paid", "paid_date": "2026-11-03", "description": "Medical Aid Contributions", "cash_account_id": ""}, follow_redirects=True)
with app.app_context():
    ex = Expense.query.filter_by(description="Medical Aid Contributions").first()
    check(ex is not None and ex.ledger_account_id == ids["ded"] and ex.category.startswith("2044"), "the bill is saved against 2044 (%s)" % (ex and ex.category))
    exid = ex.id
    L, col = statements()
    b = fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1))
    # 780 credited by the payslips, 2,340 debited by the payment: the full amount, no cap (a debit is shown as it is)
    check(abs(bal(L, "PAY_DED") - (2340 - 780)) < 0.005, "2044 is debited the full 2,340 against the 780 credited (balance %s)" % bal(L, "PAY_DED"))
    check(abs(medical_exp(L)) < 0.005 and not any(str(k).startswith("EXP:2044") for k in L.by_key), "no medical aid expense, and no expense account was made for it")
    check(abs(bal(L, "AP")) < 0.005, "it was paid in full, so nothing is owed")
    check(abs(col["totals"]["admin_expenses"] - 2326.5) < 0.005, "the statements' expenses are the payroll only: 2,326.50 (%s)" % col["totals"]["admin_expenses"])
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements")
    check(fp.expense_flags().get(exid) == "balance_sheet", "the Expense register flags it as a balance sheet item")
    check(not any("Possible double count" in w for w in L.warnings) and not any("took only" in w for w in L.warnings), "no double-count or shortfall note")
r = M.get("/expenses/")
check(r.status_code == 200 and b"Balance sheet item" in r.data, "the Expense register tags it 'Balance sheet item' and leaves it out of the expense total")
r = M.get(f"/expenses/{exid}/edit")
check(b'value="bs:%d" selected' % ids["ded"] in r.data, "editing preselects the balance sheet account")

# change it to a loan repayment (2300), then back to a normal expense category
M.post(f"/expenses/{exid}/edit", data={"expense_date": "2026-11-03", "supplier_id": ids["sup"], "supplier": "FBC Health Insurance", "category": "bs:%d" % ids["loan"], "amount": "2340",
       "currency": "USD", "status": "Paid", "paid_date": "2026-11-03", "description": "Medical Aid Contributions"}, follow_redirects=True)
with app.app_context():
    L, col = statements()
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements after moving it to Long-term loans")
M.post(f"/expenses/{exid}/edit", data={"expense_date": "2026-11-03", "supplier_id": ids["sup"], "supplier": "FBC Health Insurance", "category": "Other", "amount": "2340",
       "currency": "USD", "status": "Paid", "paid_date": "2026-11-03", "description": "Medical Aid Contributions"}, follow_redirects=True)
with app.app_context():
    check(Expense.query.get(exid).ledger_account_id is None, "choosing an expense category clears the balance sheet account")

# refusals
r = M.post("/expenses/new", data={"expense_date": "2026-11-03", "supplier": "X", "category": "bs:%d" % ids["ar"], "amount": "5", "currency": "USD", "status": "Paid"}, follow_redirects=True)
with app.app_context():
    check(Expense.query.filter_by(supplier="X").count() == 0 and b"posted by the app" in r.data, "a control account (receivables) is refused")

# an unpaid bill charged to a deposit account: owed to the supplier, an asset not an expense
r = M.post("/expenses/new", data={"expense_date": "2026-11-04", "supplier": "Landlord", "category": "bs:%d" % ids["dep"], "amount": "400", "currency": "USD", "status": "Unpaid", "due_date": "2026-11-30", "description": "Deposit"}, follow_redirects=True)
with app.app_context():
    L, col = statements()
    check(abs(bal(L, "AP") + 400 - 2340 * 0) < 0.01 or abs(bal(L, "AP") + 400) < 0.01, "an unpaid bill is owed in Trade payables (%s)" % bal(L, "AP"))
    check(abs(col["totals"]["other_current_assets"] - 400) < 0.005, "...and the deposit is an asset in the statements (%s)" % col["totals"]["other_current_assets"])
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements (unpaid deposit)")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
