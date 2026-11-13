"""[cash book balance sheet] Other payroll deductions payable (2044) is a liability and is never driven into debit by medical aid bills: bills settle it only up to what the payslips
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
    ids["bank"] = CashAccount.query.first().id
    zw = CashAccount(name="ZWG bank", kind="Bank", currency="ZWG", opening_balance=1000.0, opening_date=date(2026, 1, 1)); db.session.add(zw); db.session.commit()
    ids["zw"] = zw.id
    fl.build_ledger()          # makes sure the chart exists
    by_code = {a.code: a.id for a in FirmAccount.query.all()}
    ids.update(loan=by_code["2300"], dep=by_code["1150"], ar=by_code["1100"], re=by_code["3100"], tax=by_code["2200"], ded=by_code["2044"], inc=by_code["4500"], cap=by_code["3000"])

r = M.get(F + "/cash-book/entries/new")
check(b"Balance sheet - Liabilities" in r.data and b"2300 Long-term loans" in r.data and b"1150 Deposits paid" in r.data and b"3000 Share capital" in r.data, "the entry form lists balance sheet accounts (assets, liabilities, equity) with the expense categories")
check(b'value="acct:%d"' % ids["ar"] not in r.data and b'value="acct:%d"' % ids["re"] not in r.data and b'value="acct:%d"' % ids["ded"] not in r.data and b'value="acct:%d"' % ids["inc"] not in r.data, "...but not the accounts the app posts itself, retained earnings, the payroll payables (they have their own group) or income")

def add(**kw):
    data = {"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-05", "amount": "100", "description": "d", "reference": ""}
    data.update(kw)
    return M.post(F + "/cash-book/entries/new", data=data, follow_redirects=True)

# money in: a loan received (Dr cash / Cr Long-term loans); money out: a deposit paid (Dr Deposits / Cr cash)
r = add(kind="in", amount="5000", category_in="acct:%d" % ids["loan"], reference="LN-1")
check(b"charged to" in r.data, "money in charged to Long-term loans is accepted")
r = add(kind="out", amount="300", category_out="acct:%d" % ids["dep"], reference="DEP-1")
check(b"not to an expense" in r.data, "money out charged to Deposits paid is accepted")
with app.app_context():
    en = CashEntry.query.filter_by(reference="DEP-1").first()
    check(en.ledger_account_id == ids["dep"] and en.category.startswith("1150"), "the entry keeps the account it is charged to")
    check(Expense.query.count() == 0, "no expense is created")
    L, col = statements()
    dep = next(a for a in L.accounts.values() if a.code == "1150"); loan = next(a for a in L.accounts.values() if a.code == "2300")
    b = fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1))
    check(abs(b.get(dep.id, 0) - 300) < 0.005, "the ledger shows Deposits paid 300 debit (%s)" % b.get(dep.id))
    check(abs(b.get(loan.id, 0) + 5000) < 0.005, "...and Long-term loans 5,000 credit (%s)" % b.get(loan.id))
    check(abs(col["totals"]["other_current_assets"] - 300) < 0.005, "the statements show other current assets 300 (%s)" % col["totals"]["other_current_assets"])
    check(abs(col["totals"]["long_term_borrowings"] - 5000) < 0.005, "...and long-term borrowings 5,000 (%s)" % col["totals"]["long_term_borrowings"])
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements")
    import cashbook
    cats = {r_["category"] for r_ in cashbook.manual_summary(date(2026, 1, 1), date(2026, 12, 31))}
    check(not any(c.startswith("1150") or c.startswith("2300") for c in cats), "the year-end page does not ask for these movements to be entered again")

# refusals
r = add(category_out="acct:%d" % ids["ar"], reference="X1")
check(b"posted by the app" in r.data, "a control account (receivables) is refused")
r = add(category_out="acct:%d" % ids["re"], reference="X2")
check(b"cannot be chosen here" in r.data, "retained earnings is refused")
r = add(category_out="acct:%d" % ids["tax"], reference="X3")
check(b"cannot be chosen here" in r.data or b"posted by the app" in r.data, "tax payable is refused")
r = add(category_out="acct:%d" % ids["inc"], reference="X4")
check(b"not a balance sheet account" in r.data, "an income account is refused")
r = add(account_id=ids["zw"], category_out="acct:%d" % ids["dep"], reference="X5")
check(b"USD cash book account only" in r.data, "a ZWG cash book account cannot be charged to a balance sheet account")
with app.app_context():
    check(CashEntry.query.filter(CashEntry.reference.in_(["X1", "X2", "X3", "X4", "X5"])).count() == 0, "none of those was saved")

# a payroll payable picked as an account clears it through its heading
r = add(category_out="acct:%d" % ids["ded"], amount="200", reference="PD-1")
with app.app_context():
    en = CashEntry.query.filter_by(reference="PD-1").first()
    check(en is not None and en.category == "Medical aid and other deductions paid" and en.ledger_account_id is None, "choosing 2044 files the entry under the Payroll heading")

# edit: move the deposit to Share capital (a return of capital), then back to an expense category
with app.app_context():
    eid = CashEntry.query.filter_by(reference="DEP-1").first().id
r = M.get(F + f"/cash-book/entries/{eid}/edit")
check(b'value="acct:%d" selected' % ids["dep"] in r.data, "editing preselects the balance sheet account")
M.post(F + f"/cash-book/entries/{eid}/edit", data={"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-05", "amount": "300", "category_out": "acct:%d" % ids["cap"], "description": "d", "reference": "DEP-1"}, follow_redirects=True)
with app.app_context():
    check(CashEntry.query.get(eid).ledger_account_id == ids["cap"], "the account can be changed when editing")
    L, col = statements()
    check(abs(col["totals"].get("other_current_assets", 0)) < 0.005 and abs(col["totals"]["share_capital"] + 300) < 0.005, "...the statements follow (share capital reduced by 300: %s)" % col["totals"]["share_capital"])
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements after the change")
M.post(F + f"/cash-book/entries/{eid}/edit", data={"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-05", "amount": "300", "category_out": "Bank charges", "description": "d", "reference": "DEP-1"}, follow_redirects=True)
with app.app_context():
    check(CashEntry.query.get(eid).ledger_account_id is None, "changing it to an ordinary heading clears the account")

# a chart with no balance sheet accounts left says so, and the standard ones can be added with one click
with app.app_context():
    CashEntry.query.filter(CashEntry.ledger_account_id.isnot(None)).update({"ledger_account_id": None})
    FirmAccount.query.filter(FirmAccount.system_key.is_(None)).delete(); db.session.commit()
r = M.get(F + "/cash-book/entries/new")
check(b"No balance sheet accounts are available" in r.data and b"add the standard ones now" in r.data, "with no balance sheet accounts the form says so and offers the standard ones")
r = M.post(F + "/ledger/accounts/restore-balance-sheet", data={"next": F + "/cash-book/entries/new"}, follow_redirects=True)
check(b"standard balance sheet account(s) added" in r.data and b"Balance sheet - Liabilities" in r.data and b"No balance sheet accounts are available" not in r.data, "...and one click adds them and they appear in the list")
with app.app_context():
    n_before = FirmAccount.query.count()
M.post(F + "/ledger/accounts/restore-balance-sheet", data={}, follow_redirects=True)
with app.app_context():
    check(FirmAccount.query.count() == n_before, "pressing it again adds nothing")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
