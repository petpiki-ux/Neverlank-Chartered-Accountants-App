"""Medical aid due to the society, posted from the Managing Partner's own-staff payroll to Firm Finance > Suppliers: payslip lines marked as
medical aid are totalled for a FINALIZED period and posted as unpaid supplier bill(s) (employer's share and employees' share), never twice,
visible to the ledger's Trade payables and the supplier account, paid from the supplier page, removable until paid."""
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
from datetime import date, timedelta
from app import create_app
from extensions import db
from models import *
import suppliers as sp
import payroll_medical as pm
import firm_ledger as fl
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

with app.app_context():
    mp = User(username="mp", name="Managing Partner", role="admin"); mp.set_password("x")
    ptn = User(username="ptn", name="Other Partner", role="partner"); ptn.set_password("x")
    db.session.add_all([mp, ptn]); db.session.commit()
    cl = Client(name="Acme", created_by_id=mp.id); db.session.add(cl); db.session.commit()
    emps = [PayrollEmployee(scope="internal", full_name=n, basic_salary=s, created_by_id=mp.id) for n, s in (("Tendai Moyo", 1000.0), ("Rudo Chari", 1200.0), ("Farai Dube", 900.0))]
    db.session.add_all(emps); db.session.commit()
    per = PayrollPeriod(scope="internal", name="September 2026 Staff", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), pay_date=date(2026, 9, 30), currency="USD", created_by_id=mp.id)
    cper = PayrollPeriod(scope="client", client_id=cl.id, name="September 2026 Acme", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), currency="USD", created_by_id=mp.id)
    db.session.add_all([per, cper]); db.session.commit()
    pss = []
    for e in emps:
        ps = Payslip(period_id=per.id, employee_id=e.id, basic_salary=e.basic_salary, gross_pay=e.basic_salary, net_pay=e.basic_salary); db.session.add(ps); pss.append(ps)
    cps = Payslip(period_id=cper.id, employee_id=emps[0].id, basic_salary=1.0, gross_pay=1.0, net_pay=1.0); db.session.add(cps)
    db.session.commit()
    ids = dict(mp=mp.id, ptn=ptn.id, per=per.id, cper=cper.id, ps=[p.id for p in pss], cps=cps.id)
    ok_sup = Supplier(code="SUP-0001", name="PSMAS", payment_terms_days=14, currency="USD")
    bad_sup = Supplier(code="SUP-0002", name="Blocked Society", status="Blocked")
    db.session.add_all([ok_sup, bad_sup]); db.session.commit()
    ids["sup"], ids["bad"] = ok_sup.id, bad_sup.id
M, PT = login(ids["mp"]), login(ids["ptn"])
P = f"/payroll/periods/{ids['per']}"

# ---------------------------------------------------------------- marking the lines on payslips
def add_item(ps, **kw):
    data = {"category": "Deduction", "label": "x", "amount": "0"}; data.update(kw)
    return M.post(f"/payroll/payslips/{ps}/items/add", data=data, follow_redirects=True)
r = add_item(ids["ps"][0], category="Deduction", label="Medical aid - employee", amount="40", medical_aid="employee")
r = add_item(ids["ps"][0], category="Allowance", label="Medical aid - employer", amount="60", medical_aid="employer", nssa_applicable="")
r = add_item(ids["ps"][1], category="Deduction", label="Medical aid - employee", amount="55", medical_aid="employee")
r = add_item(ids["ps"][1], category="Allowance", label="Medical aid - employer", amount="75", medical_aid="employer", nssa_applicable="")
r = add_item(ids["ps"][2], category="Tax Credit", label="Medical expenses credit", amount="20", medical_aid="employee")     # a credit is never owed to the society
r = add_item(ids["ps"][2], category="Deduction", label="Medical aid top-up", amount="10")                                       # mentions medical aid but not marked
with app.app_context():
    its = {i.label: i for i in PayslipItem.query.all()}
    check(its["Medical aid - employee"].medical_aid == "employee" and its["Medical expenses credit"].medical_aid is None, "a Tax Credit line cannot be marked as medical aid")
    check(its["Medical aid top-up"].medical_aid is None, "an unmarked line stays unmarked")
r = M.get(f"/payroll/payslips/{ids['ps'][0]}")
check(b"Employee's share" in r.data and b"Employer's share" in r.data and b"Medical aid owed to the society?" in r.data, "the payslip shows the marker and the add form offers it")

with app.app_context():
    t = pm.medical_lines(PayrollPeriod.query.get(ids["per"]))
    check(near(t["employee_total"], 95) and near(t["employer_total"], 135), f"totals: employees 40+55 = 95, employer 60+75 = 135 ({t['employee_total']}, {t['employer_total']})")
    check(len(t["rows"]) == 2 and len(t["unmarked"]) == 1 and t["unmarked"][0]["label"] == "Medical aid top-up", "the credit is ignored and the unmarked 'Medical aid' line is reported, not counted")

# ---------------------------------------------------------------- the period page
r = M.get(P)
check(r.status_code == 200 and b"Medical aid due to the society" in r.data and b"95.00" in r.data and b"135.00" in r.data and b"230.00" in r.data, "the period page shows the totals per employee and in total")
check(b"Not counted:" in r.data and b"Medical aid top-up" in r.data, "...and warns about the unmarked line")
check(b"Finalize the period first" in r.data, "...and says to finalize before posting")
check(b"Medical aid due to the society" not in M.get(f"/payroll/periods/{ids['cper']}").data, "a client payroll period has no such card")

# ---------------------------------------------------------------- access
check(PT.get(P).status_code == 403, "another partner cannot open the staff period")
check(PT.post(P + "/medical-aid/post", data={"supplier_id": ids["sup"], "part": ["employer"]}).status_code == 403, "...nor post from it")
check(M.post(f"/payroll/periods/{ids['cper']}/medical-aid/post", data={"supplier_id": ids["sup"], "part": ["employer"]}, follow_redirects=True).status_code == 200, "a client period is handled without error")
with app.app_context():
    check(Expense.query.count() == 0, "...and posts nothing")

# ---------------------------------------------------------------- posting
r = M.post(P + "/medical-aid/post", data={"supplier_id": ids["sup"], "part": ["employer", "employee"]}, follow_redirects=True)
check(b"Finalize the period first" in r.data, "posting is refused while the period is not finalized")
with app.app_context():     # the payslips here carry hand-typed figures: make the deductions the lines describe part of their totals, as Recalculate does
    for pid_, amt_ in ((ids["ps"][0], 40.0), (ids["ps"][1], 55.0), (ids["ps"][2], 10.0)):
        x_ = Payslip.query.get(pid_); x_.other_deductions_total = amt_; x_.net_pay = (x_.net_pay or 0.0) - amt_
    db.session.commit()
M.post(P + "/finalize")
r = M.get(P)
check(b"Post to Suppliers" in r.data and b"+ Add the society as a supplier" in r.data, "once finalized the post form appears")
r = M.post(P + "/medical-aid/post", data={"part": ["employer"]}, follow_redirects=True)
check(b"Choose the medical aid society" in r.data, "a supplier is required")
r = M.post(P + "/medical-aid/post", data={"supplier_id": ids["bad"], "part": ["employer"]}, follow_redirects=True)
check(b"is blocked" in r.data, "a blocked supplier is refused")
r = M.post(P + "/medical-aid/post", data={"supplier_id": ids["sup"]}, follow_redirects=True)
check(b"Choose at least one part" in r.data, "at least one part must be chosen")
with app.app_context():
    check(Expense.query.count() == 0, "nothing was posted by the refused attempts")
r = M.post(P + "/medical-aid/post", data={"supplier_id": ids["sup"], "part": ["employer", "employee"], "due_date": "2026-10-14"}, follow_redirects=True)
check(b"Posted to Suppliers" in r.data and b"USD 135.00" in r.data and b"USD 95.00" in r.data, "both parts are posted")
with app.app_context():
    er = Expense.query.filter_by(source_key=f"payroll_medical:{ids['per']}:employer").first()
    ee = Expense.query.filter_by(source_key=f"payroll_medical:{ids['per']}:employee").first()
    check(er and near(er.amount, 135) and er.status == "Unpaid" and er.supplier_id == ids["sup"] and er.category == pm.EMPLOYER_CATEGORY, "the employer's bill: 135, unpaid, to the society")
    check(ee and near(ee.amount, 95) and ee.category == pm.EMPLOYEE_CATEGORY and ee.due_date == date(2026, 10, 14) and ee.expense_date == date(2026, 9, 30), "the employees' bill: 95, due 14 Oct, dated at the pay date")
    check(near(sp.balance(Supplier.query.get(ids["sup"])), 230), "the society is owed 230")
    check(PayrollTaxSettings.query.get(1).medical_aid_supplier_id == ids["sup"], "the society is remembered for next month")
    L = fl.build_ledger()
    bal = fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1))
    check(near(-bal.get(L.by_key["AP"].id, 0.0), 230), "the ledger's Trade payables carry the 230")
    # the period is finalized, so it is in the firm's ledger (firm_payroll.py): the bills clear 'Other payroll deductions payable' instead of being a second expense
    # d65: the payable is only cleared by what the payslips took off pay (95); the employer's share (135) is already in gross pay, so it settles net pay instead
    check(near(bal.get(L.by_key["PAY_DED"].id, 0.0), 0), "...the employees' 95 clears Other payroll deductions payable, which is never driven into debit")
    check(near(bal.get(L.by_key["PAY_NET"].id, 0.0), -(3100 - 105) + 125), "...and the employer's share (135 less the 10 of other deductions still left in the payable) settles Net salaries payable")
    check(near(bal.get(L.by_key[f"EXP:{pm.EMPLOYER_CATEGORY}"].id, 0.0), 0) and near(bal.get(L.by_key[f"EXP:{pm.EMPLOYEE_CATEGORY}"].id, 0.0), 0), "...so neither medical aid expense account carries them")
r = M.get(P)
check(b"Posted to Suppliers:" in r.data and b"Remove the posted bill" in r.data and b"Post to Suppliers</button>" not in r.data, "the page now shows what was posted instead of the form")
r = M.post(P + "/medical-aid/post", data={"supplier_id": ids["sup"], "part": ["employer"]}, follow_redirects=True)
check(b"Already posted" in r.data, "a period cannot be posted twice")
with app.app_context():
    check(Expense.query.count() == 2, "...and no duplicate bill exists")

# ---------------------------------------------------------------- the society is paid from the supplier page
with app.app_context():
    erid, eeid = Expense.query.filter_by(source_key=f"payroll_medical:{ids['per']}:employer").first().id, Expense.query.filter_by(source_key=f"payroll_medical:{ids['per']}:employee").first().id
r = M.post(f"/firm-finance/suppliers/{ids['sup']}/pay", data={"currency": "USD", "payment_date": "2026-10-10", "method": "Bank transfer", "reference": "TT-9",
           f"amt_{erid}": "135"}, follow_redirects=True)
check(b"Remittance advice" in r.data, "the employer's bill is paid from the supplier page")
r = M.post(P + "/medical-aid/remove", follow_redirects=True)
check(b"A payment has been recorded" in r.data, "posted bills cannot be removed once something has been paid on them")
with app.app_context():
    check(Expense.query.count() == 2, "...both bills are still there")
    pay = SupplierPayment.query.first(); pid = pay.id
M.post(f"/firm-finance/suppliers/payments/{pid}/delete")

# ---------------------------------------------------------------- payslips edited after posting
with app.app_context():
    db.session.add(PayslipItem(payslip_id=ids["ps"][2], category="Deduction", label="Medical aid - employee", amount=30, medical_aid="employee")); db.session.commit()
r = M.get(P)
check(b"now say a different amount" in r.data and b"employee" in r.data, "the page warns when the payslips no longer match the posted bill")
r = M.post(P + "/medical-aid/remove", follow_redirects=True)
check(b"were removed from Suppliers" in r.data, "the bills can be removed while nothing is paid on them")
with app.app_context():
    check(Expense.query.count() == 0, "...they are gone")
r = M.post(P + "/medical-aid/post", data={"supplier_id": ids["sup"], "part": ["employee"]}, follow_redirects=True)
with app.app_context():
    ee = Expense.query.filter_by(source_key=f"payroll_medical:{ids['per']}:employee").first()
    check(ee and near(ee.amount, 125) and Expense.query.count() == 1, "posting again takes the new total (95 + 30 = 125) and only the part chosen")
    check(near(sp.balance(Supplier.query.get(ids["sup"])), 125), "the society is owed 125")

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
