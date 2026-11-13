"""[cash book payee] Other payroll deductions payable (2044) is a liability and is never driven into debit by medical aid bills: bills settle it only up to what the payslips
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
    sup = Supplier(code="SUP-0001", name="FBC Medical Aid", payment_terms_days=14, currency="USD")
    other = Supplier(code="SUP-0002", name="Puma Energy", payment_terms_days=14, currency="USD")
    db.session.add_all([sup, other]); db.session.commit()
    ids["sup"], ids["other"] = sup.id, other.id
    ids["bank"] = CashAccount.query.first().id

# the entry form offers the supplier on the payroll headings
r = M.get(F + "/cash-book/entries/new")
check(r.status_code == 200 and b"Paid to (supplier)" in r.data and b"FBC Medical Aid" in r.data, "the cash book entry form offers a Paid to (supplier) list")

# pay FBC under the medical aid heading, naming the supplier
r = M.post(F + "/cash-book/entries/new", data={"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-05", "amount": "780",
           "category_out": "Medical aid and other deductions paid", "supplier_id": ids["sup"], "description": "Medical aid Aug-Oct", "reference": "TT-77"}, follow_redirects=True)
check(b"paid to FBC Medical Aid" in r.data and b"Other payroll deductions payable" in r.data, "the payment is added and says where it is charged")
with app.app_context():
    en = CashEntry.query.filter_by(reference="TT-77").first()
    check(en is not None and en.supplier_id == ids["sup"] and en.category == "Medical aid and other deductions paid", "the entry keeps the supplier and the payroll heading")
    check(Expense.query.count() == 0, "no expense or bill is created")
    L, col = statements()
    lines = [l for l in L.lines if l["source"].startswith("Cash book") and "FBC Medical Aid" in (l["description"] or "")]
    check(any(l["dr"] == 780 and l["account_id"] == L.by_key["PAY_DED"].id for l in lines), "the ledger posts Dr Other payroll deductions payable 780")
    check(any(l["cr"] == 780 and l["account_id"] == L.by_key["CASH:%d" % ids["bank"]].id for l in lines), "...and Cr cash 780")
    check(abs(bal(L, "PAY_DED")) < 0.005, "2044 holds 780 deducted less 780 paid = nil (%s)" % bal(L, "PAY_DED"))
    check(not any(str(k).startswith("EXP:Medical") for k in L.by_key) or abs(medical_exp(L)) < 0.005, "no medical aid expense")
    check(abs(bal(L, "AP")) < 0.005, "nothing is owed to suppliers")
    check(fl.reconcile(L, col)["different"] == 0, "ledger agrees with the statements")
r = M.get(F + "/cash-book")
check(r.status_code == 200 and b"FBC Medical Aid" in r.data, "the cash book shows the payee")
r = M.get(f"/firm-finance/suppliers/{ids['sup']}")
check(r.status_code == 200 and b"Paid from payroll liabilities" in r.data and b"TT-77" in r.data, "the supplier's page lists it under 'Paid from payroll liabilities'")
r = M.post(f"/firm-finance/suppliers/{ids['sup']}/delete", follow_redirects=True)
with app.app_context():
    check(Supplier.query.get(ids["sup"]) is not None, "a supplier with such a payment cannot be deleted")

# edit: change the supplier, then clear it
with app.app_context():
    eid = CashEntry.query.filter_by(reference="TT-77").first().id
M.post(F + f"/cash-book/entries/{eid}/edit", data={"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-05", "amount": "780",
       "category_out": "Medical aid and other deductions paid", "supplier_id": ids["other"], "description": "x", "reference": "TT-77"}, follow_redirects=True)
with app.app_context():
    check(CashEntry.query.get(eid).supplier_id == ids["other"], "the supplier can be changed when editing")
M.post(F + f"/cash-book/entries/{eid}/edit", data={"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-05", "amount": "780",
       "category_out": "Medical aid and other deductions paid", "supplier_id": "", "description": "x", "reference": "TT-77"}, follow_redirects=True)
with app.app_context():
    check(CashEntry.query.get(eid).supplier_id is None, "...and cleared")

# a supplier on a non-payroll heading is ignored; an unknown supplier is refused
M.post(F + "/cash-book/entries/new", data={"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-06", "amount": "10",
       "category_out": "Bank charges", "supplier_id": ids["sup"], "description": "fees", "reference": "BC-1"}, follow_redirects=True)
with app.app_context():
    check(CashEntry.query.filter_by(reference="BC-1").first().supplier_id is None, "a supplier on an ordinary cash-only heading is ignored")
r = M.post(F + "/cash-book/entries/new", data={"kind": "out", "account_id": ids["bank"], "entry_date": "2026-11-06", "amount": "10",
           "category_out": "Medical aid and other deductions paid", "supplier_id": "99999", "reference": "BAD"}, follow_redirects=True)
with app.app_context():
    check(CashEntry.query.filter_by(reference="BAD").first() is None, "an unknown supplier is refused")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
