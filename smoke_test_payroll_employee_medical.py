"""The employee's own medical aid as a payslip Deduction: a standing amount on the employee's record is added (marked as medical aid, totals included) to every payslip
generated; the payslip has a Medical aid box that sets / changes / removes the marked line and recalculates; finalized periods and non-Admin users are refused; the
period page and the ledger pick the deduction up (2044 is credited)."""
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
import payroll_medical as pm, firm_ledger as fl
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
    ids = dict(mp=mp.id, ptn=ptn.id)
M, PT = login(ids["mp"]), login(ids["ptn"])

# ---- the employee record carries the standing amount
r = M.post("/payroll/employees/new?scope=internal", data={"scope": "internal", "full_name": "Tendai Moyo", "basic_salary": "1000", "pay_frequency": "Monthly", "medical_aid_employee": "65"}, follow_redirects=True)
r2_ = M.post("/payroll/employees/new?scope=internal", data={"scope": "internal", "full_name": "Rudo Chari", "basic_salary": "1200", "pay_frequency": "Monthly"}, follow_redirects=True)
with app.app_context():
    e1 = PayrollEmployee.query.filter_by(full_name="Tendai Moyo").first(); e2 = PayrollEmployee.query.filter_by(full_name="Rudo Chari").first()
    check(e1 is not None and near(e1.medical_aid_employee, 65), "an employee is created with a standing medical aid deduction of 65")
    check(e2 is not None and near(e2.medical_aid_employee or 0, 0), "...and one without defaults to none")
    eid1, eid2 = e1.id, e2.id
r = M.get(f"/payroll/employees/{eid1}/edit")
check(r.status_code == 200 and b"medical_aid_employee" in r.data and b"65.00" in r.data, "the employee form shows the standing amount")
M.post(f"/payroll/employees/{eid1}/edit", data={"full_name": "Tendai Moyo", "basic_salary": "1000", "pay_frequency": "Monthly", "is_active": "1", "medical_aid_employee": "70"}, follow_redirects=True)
with app.app_context():
    check(near(PayrollEmployee.query.get(eid1).medical_aid_employee, 70), "...and it can be changed")

# ---- generating payslips adds it, already calculated
with app.app_context():
    per = PayrollPeriod(scope="internal", name="September 2026 Staff", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), pay_date=date(2026, 9, 30), currency="USD", created_by_id=ids["mp"])
    db.session.add(per); db.session.commit(); pid = per.id
M.post(f"/payroll/periods/{pid}/generate", data={}, follow_redirects=True)
with app.app_context():
    ps = {p.employee_id: p for p in Payslip.query.filter_by(period_id=pid).all()}
    p1, p2 = ps[eid1], ps[eid2]
    lines = [i for i in p1.items if i.medical_aid == "employee"]
    check(len(lines) == 1 and lines[0].category == "Deduction" and near(lines[0].amount, 70), "the generated payslip has a Deduction line of 70 marked as the employee's medical aid")
    check(near(p1.other_deductions_total, 70), "...and the payslip's deductions total includes it (%s)" % p1.other_deductions_total)
    check(near(p1.net_pay, p1.gross_pay - p1.paye_tax - p1.aids_levy - p1.nssa_employee - 70), "...and net pay is after it")
    check(len(p2.items) == 0 and near(p2.other_deductions_total or 0, 0), "an employee with no standing amount gets no line")
    sid1, sid2 = p1.id, p2.id
    net_before = p2.net_pay

# ---- the payslip box
r = M.get(f"/payroll/payslips/{sid2}")
check(r.status_code == 200 and b"Medical aid deduction" in r.data and b"Save and recalculate" in r.data, "the payslip shows the Medical aid box")
M.post(f"/payroll/payslips/{sid2}/medical-aid", data={"employee_share": "55"}, follow_redirects=True)
with app.app_context():
    p2 = Payslip.query.get(sid2)
    lines = [i for i in p2.items if i.medical_aid == "employee"]
    check(len(lines) == 1 and near(lines[0].amount, 55) and lines[0].category == "Deduction", "the box adds a marked Deduction of 55")
    check(near(p2.other_deductions_total, 55) and near(p2.net_pay, net_before - 55), "...and recalculates the payslip at once (deductions 55, net pay down by 55)")
    check(pm.medical_lines(PayrollPeriod.query.get(pid))["employee_total"] == 125, "the period's medical aid due for employees is 70 + 55 = 125")
M.post(f"/payroll/payslips/{sid2}/medical-aid", data={"employee_share": "60"}, follow_redirects=True)
with app.app_context():
    p2 = Payslip.query.get(sid2)
    check(len([i for i in p2.items if i.medical_aid == "employee"]) == 1 and near(p2.other_deductions_total, 60), "saving again changes the same line (60), it does not add another")
M.post(f"/payroll/payslips/{sid2}/medical-aid", data={"employee_share": "0"}, follow_redirects=True)
with app.app_context():
    p2 = Payslip.query.get(sid2)
    check(not [i for i in p2.items if i.medical_aid == "employee"] and near(p2.other_deductions_total, 0) and near(p2.net_pay, net_before), "0 removes the line and the payslip goes back to its original net pay")
r = M.post(f"/payroll/payslips/{sid2}/medical-aid", data={"employee_share": "-5"}, follow_redirects=True)
check(b"cannot be negative" in r.data, "a negative amount is refused")
r = M.post(f"/payroll/payslips/{sid2}/medical-aid", data={"use_standing": "1", "employee_share": "0"}, follow_redirects=True)
with app.app_context():
    check(near(Payslip.query.get(sid2).other_deductions_total, 0), "(an employee with no standing amount stays at none)")
# several marked lines: nothing is changed
with app.app_context():
    db.session.add(PayslipItem(payslip_id=sid1, category="Deduction", label="Medical aid top-up", amount=5.0, medical_aid="employee")); db.session.commit()
r = M.post(f"/payroll/payslips/{sid1}/medical-aid", data={"employee_share": "10"}, follow_redirects=True)
with app.app_context():
    check(b"more than one" in r.data and near(Payslip.query.get(sid1).other_deductions_total, 70), "with several marked lines the box changes nothing and says so")
    PayslipItem.query.filter_by(payslip_id=sid1, label="Medical aid top-up").delete(); db.session.commit()

# ---- access and finalizing
check(PT.post(f"/payroll/payslips/{sid2}/medical-aid", data={"employee_share": "5"}).status_code == 403, "another partner cannot use it on a staff payslip")
M.post(f"/payroll/payslips/{sid2}/medical-aid", data={"employee_share": "55"}, follow_redirects=True)
M.post(f"/payroll/periods/{pid}/finalize", follow_redirects=True)
with app.app_context():
    check(PayrollPeriod.query.get(pid).status == "Finalized", "the period finalizes (no stale totals)")
r = M.post(f"/payroll/payslips/{sid2}/medical-aid", data={"employee_share": "1"}, follow_redirects=True)
with app.app_context():
    check(b"finalized" in r.data and near(Payslip.query.get(sid2).other_deductions_total, 55), "a finalized payslip is refused")
check(b"Medical aid deduction" not in M.get(f"/payroll/payslips/{sid2}").data or b"Save and recalculate" not in M.get(f"/payroll/payslips/{sid2}").data, "...and the box is not offered")

# ---- the books: 2044 is credited with the 125 deducted
with app.app_context():
    L = fl.build_ledger()
    bal = fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1))
    check(near(-bal.get(L.by_key["PAY_DED"].id, 0.0), 125), "Other payroll deductions payable is credited 125 (%s)" % bal.get(L.by_key["PAY_DED"].id, 0.0))

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
