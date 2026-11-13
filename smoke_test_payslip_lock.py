"""Neverlank's OWN staff payslips (and the staff payroll employees and periods they sit in) can be seen by the Managing Partner only - the Admin
role. Partners, Supervisors and Staff are locked out of every route that shows or changes them, even with the manage_payroll permission and even
for their own payslip, and the Payroll dashboard shows them a lock instead of the staff figures. The payroll service the firm runs for clients is
unaffected."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
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
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

with app.app_context():
    mp = User(username="mp", name="Managing Partner", role="admin"); mp.set_password("x")
    ptn = User(username="ptn", name="Other Partner", role="partner"); ptn.set_password("x")
    sup = User(username="sup", name="Supervisor", role="supervisor"); sup.set_password("x")
    stf = User(username="stf", name="Staff Member", role="staff"); stf.set_password("x")
    db.session.add_all([mp, ptn, sup, stf]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=mp.id); db.session.add(cl); db.session.commit()
    # staff payroll
    e_int = PayrollEmployee(scope="internal", user_id=stf.id, full_name="Tendai Staffer", basic_salary=4321.0, created_by_id=mp.id)
    p_int = PayrollPeriod(scope="internal", name="September 2026 Staff", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), pay_date=date(2026, 9, 30), created_by_id=mp.id)
    # client payroll
    e_cli = PayrollEmployee(scope="client", client_id=cl.id, full_name="Client Employee", basic_salary=900.0, created_by_id=mp.id)
    p_cli = PayrollPeriod(scope="client", client_id=cl.id, name="September 2026 Acme", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), pay_date=date(2026, 9, 30), created_by_id=mp.id)
    db.session.add_all([e_int, p_int, e_cli, p_cli]); db.session.commit()
    s_int = Payslip(period_id=p_int.id, employee_id=e_int.id, basic_salary=4321.0, gross_pay=4321.0, net_pay=3999.0)
    s_cli = Payslip(period_id=p_cli.id, employee_id=e_cli.id, basic_salary=900.0, gross_pay=900.0, net_pay=850.0)
    db.session.add_all([s_int, s_cli]); db.session.commit()
    item = PayslipItem(payslip_id=s_int.id, category="Allowance", label="Transport", amount=50.0); db.session.add(item); db.session.commit()
    ids = dict(mp=mp.id, ptn=ptn.id, sup=sup.id, stf=stf.id, ei=e_int.id, pi=p_int.id, si=s_int.id, ec=e_cli.id, pc=p_cli.id, sc=s_cli.id, item=item.id, cl=cl.id)
    # a Tax Advisory engagement the staff member is on (this normally grants manage_payroll on its own)
    eng = Engagement(client_id=cl.id, title="Tax advice", type=TAX_ADVISORY_TYPE, partner_id=ptn.id, acceptance_required=False)
    db.session.add(eng); db.session.commit()

    def get(c, url): return c.get(url)

    # --- the Managing Partner (Admin) sees everything
    M = login(ids["mp"])
    check(get(M, f"/payroll/payslips/{ids['si']}").status_code == 200, "the Managing Partner opens a staff payslip")
    check(get(M, f"/payroll/payslips/{ids['si']}/download/pdf").status_code == 200 and get(M, f"/payroll/payslips/{ids['si']}/download/docx").status_code == 200, "...and downloads it as PDF and Word")
    check(get(M, "/payroll/employees?scope=internal").status_code == 200 and b"Tendai Staffer" in get(M, "/payroll/employees?scope=internal").data, "...sees the staff employee list")
    check(get(M, "/payroll/periods?scope=internal").status_code == 200 and get(M, f"/payroll/periods/{ids['pi']}").status_code == 200, "...the staff periods and a staff period")
    r = get(M, "/payroll/")
    check(r.status_code == 200 and b"Tendai" not in r.data and b"September 2026 Staff" in r.data and b"can only be seen by the Managing Partner" not in r.data, "...and the dashboard shows the staff payroll")
    check(get(M, "/payroll/employees/new?scope=internal").status_code == 200 and b'value="internal"' in get(M, "/payroll/employees/new").data, "...can add a staff employee, the form offering Neverlank staff")
    r = M.post(f"/payroll/payslips/{ids['si']}/update", data={"basic_salary": "4400"})
    db.session.expire_all()
    check(r.status_code == 302 and db.session.get(Payslip, ids["si"]).basic_salary == 4400.0, "...and can change a staff payslip")

    # --- everyone else is locked out of the staff payroll
    # give the Supervisor and the Staff member the Payroll permission, so the lock is tested on its own and not hidden behind a missing permission
    for name in ("sup", "stf"):
        db.session.add(UserPermissionOverride(user_id=ids[name], permission_key="manage_payroll", allowed=True))
    db.session.commit()
    for name in ("ptn", "sup", "stf"):
        C = login(ids[name])
        tag = "[%s] " % name
        for url in (f"/payroll/payslips/{ids['si']}", f"/payroll/payslips/{ids['si']}/download/pdf", f"/payroll/payslips/{ids['si']}/download/docx",
                    "/payroll/employees?scope=internal", "/payroll/employees", "/payroll/periods?scope=internal", "/payroll/periods", f"/payroll/periods/{ids['pi']}",
                    f"/payroll/employees/{ids['ei']}/edit", "/payroll/employees/new?scope=internal", "/payroll/periods/new?scope=internal", "/payroll/employees?scope=other"):
            r = get(C, url)
            check(r.status_code in (403, 404) and b"Tendai" not in r.data and b"4400" not in r.data and b"4321" not in r.data, tag + "GET %s is refused" % url)
        before = db.session.get(Payslip, ids["si"]).basic_salary
        posts = [(f"/payroll/payslips/{ids['si']}/update", {"basic_salary": "1"}), (f"/payroll/payslips/{ids['si']}/recalculate", {}),
                 (f"/payroll/payslips/{ids['si']}/items/add", {"category": "Allowance", "label": "x", "amount": "5"}), (f"/payroll/payslips/items/{ids['item']}/delete", {}),
                 (f"/payroll/payslips/{ids['si']}/delete", {}), (f"/payroll/periods/{ids['pi']}/generate", {}), (f"/payroll/periods/{ids['pi']}/finalize", {}),
                 (f"/payroll/periods/{ids['pi']}/backfill-apwcs", {}), (f"/payroll/periods/{ids['pi']}/delete", {}), (f"/payroll/employees/{ids['ei']}/delete", {}),
                 (f"/payroll/employees/{ids['ei']}/edit", {"scope": "internal", "full_name": "Hacked"}),
                 ("/payroll/employees/new", {"scope": "internal", "full_name": "Sneaky"}), ("/payroll/employees/new", {"scope": "bogus", "full_name": "Sneaky"}),
                 ("/payroll/periods/new", {"scope": "internal", "name": "Sneaky"}), ("/payroll/periods/new", {"scope": "bogus", "name": "Sneaky"})]
        for url, data in posts:
            r = C.post(url, data=data)
            check(r.status_code in (403, 404), tag + "POST %s is refused (got %s)" % (url, r.status_code))
        db.session.expire_all()
        check(db.session.get(Payslip, ids["si"]).basic_salary == before and db.session.get(PayrollPeriod, ids["pi"]).status == "Draft" and db.session.get(PayrollEmployee, ids["ei"]).full_name == "Tendai Staffer"
              and db.session.get(PayslipItem, ids["item"]) is not None and PayrollEmployee.query.filter_by(full_name="Sneaky").count() == 0 and PayrollPeriod.query.filter_by(name="Sneaky").count() == 0,
              tag + "nothing was changed")
        r = get(C, "/payroll/")
        check(r.status_code == 200 and b"Tendai" not in r.data and b"September 2026 Staff" not in r.data and b"can only be seen by the Managing Partner" in r.data and b"Client Payroll Service" in r.data,
              tag + "the dashboard shows a lock instead of the staff payroll, and still shows the client service")
        check(b"/payroll/employees?scope=internal" not in r.data and b"scope=internal" not in r.data, tag + "the dashboard has no links into the staff payroll")

    # --- client payroll is untouched for a Partner
    P = login(ids["ptn"])
    check(get(P, f"/payroll/payslips/{ids['sc']}").status_code == 200 and b"Client Employee" in get(P, f"/payroll/payslips/{ids['sc']}").data, "a Partner still opens a client payslip")
    check(get(P, f"/payroll/payslips/{ids['sc']}/download/pdf").status_code == 200, "...and downloads it")
    check(get(P, "/payroll/employees?scope=client").status_code == 200 and b"Client Employee" in get(P, "/payroll/employees?scope=client").data, "...sees the client employee list")
    check(get(P, f"/payroll/periods/{ids['pc']}").status_code == 200 and get(P, "/payroll/periods?scope=client").status_code == 200, "...and the client periods")
    r = get(P, "/payroll/employees/new")
    check(r.status_code == 200 and b'value="internal"' not in r.data and b'value="client"' in r.data, "the new-employee form (no scope given) opens for a Partner and offers client employees only")
    r = get(P, "/payroll/periods/new")
    check(r.status_code == 200 and b'value="internal"' not in r.data and b'value="client"' in r.data, "the new-period form opens for a Partner and offers client payroll only")
    r = P.post("/payroll/periods/new", data={"scope": "client", "client_id": str(ids["cl"]), "name": "October 2026 Acme", "period_start": "2026-10-01", "period_end": "2026-10-31", "pay_date": "2026-10-31"})
    check(r.status_code == 302 and PayrollPeriod.query.filter_by(name="October 2026 Acme", scope="client").count() == 1, "a Partner can still create a client period")
    r = P.post(f"/payroll/payslips/{ids['sc']}/update", data={"basic_salary": "950"}); db.session.expire_all()
    check(r.status_code == 302 and db.session.get(Payslip, ids["sc"]).basic_salary == 950.0, "...and change a client payslip")
    check(get(P, "/payroll/tax-settings").status_code == 200, "the firm-wide Payroll Tax Settings are not part of the lock")

    # --- the staff payroll is the only thing a permission cannot give: an explicit override does not bypass it
    db.session.add(UserPermissionOverride(user_id=ids["ptn"], permission_key="manage_payroll", allowed=True)); db.session.commit()
    check(get(login(ids["ptn"]), f"/payroll/payslips/{ids['si']}").status_code == 403, "an explicit manage_payroll override does not open the staff payslips")
    # a user without manage_payroll at all
    ov = UserPermissionOverride.query.filter_by(user_id=ids["stf"], permission_key="manage_payroll").first(); ov.allowed = False; db.session.commit()
    check(get(login(ids["stf"]), "/payroll/").status_code == 403, "without manage_payroll the Payroll area stays closed")

print("\n%d failure(s)" % len(fails))
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
