"""Entering leave days for staff (HR & Admin > Leave > Record leave): working days worked out from the dates, typed days, half days, Annual leave
coming off the balance (and back on deletion), the balance and minimum-service checks, overlaps, sick leave as a provision, a reviewer entering for
anyone, a staff member only for themselves, and the history (with timesheet leave) and totals."""
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
import leave_calc

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

# weekday counting (no database needed)
check(leave_calc.weekdays_between(date(2026, 11, 2), date(2026, 11, 6)) == 5, "Mon-Fri is 5 working days")
check(leave_calc.weekdays_between(date(2026, 11, 2), date(2026, 11, 8)) == 5, "a full week with the weekend is still 5")
check(leave_calc.weekdays_between(date(2026, 11, 7), date(2026, 11, 8)) == 0, "a weekend alone is 0")
check(leave_calc.weekdays_between(date(2026, 11, 2), date(2026, 11, 17)) == 12, "Mon 2 Nov to Tue 17 Nov is 12 working days")
check(leave_calc.weekdays_between(date(2026, 11, 5), date(2026, 11, 3)) == 0, "backwards range is 0")

with app.app_context():
    adm = User(username="mp", name="Managing Partner", role="admin"); adm.set_password("x")
    sup = User(username="sup", name="Supervisor", role="supervisor"); sup.set_password("x")
    tendai = User(username="tendai", name="Tendai Moyo", role="staff"); tendai.set_password("x")
    rudo = User(username="rudo", name="Rudo Chari", role="staff"); rudo.set_password("x")
    nolink = User(username="nolink", name="No Profile", role="staff"); nolink.set_password("x")
    db.session.add_all([adm, sup, tendai, rudo, nolink]); db.session.commit()
    today = date.today()
    e_t = PayrollEmployee(scope="internal", full_name="Tendai Moyo", user_id=tendai.id, date_joined=today - timedelta(days=800), created_by_id=adm.id)
    e_r = PayrollEmployee(scope="internal", full_name="Rudo Chari", user_id=rudo.id, date_joined=today - timedelta(days=100), created_by_id=adm.id)
    db.session.add_all([e_t, e_r]); db.session.commit()
    ids = dict(adm=adm.id, sup=sup.id, t=tendai.id, r=rudo.id, nl=nolink.id, et=e_t.id, er=e_r.id)
    annual = LeaveType.query.filter_by(name="Annual/Vacation Leave").first().id
    sick = LeaveType.query.filter_by(name="Sick Leave").first().id
    # Tendai: opening balance of 20 days
    bal = leave_calc.get_or_create_balance(e_t, LeaveType.query.get(annual)); bal.brought_forward = 20.0; bal.taken_days = 0.0
    bal.last_accrual_date = today; db.session.commit()

A, S, T, R, NL = login(ids["adm"]), login(ids["sup"]), login(ids["t"]), login(ids["r"]), login(ids["nl"])
def balance():
    with app.app_context():
        b = LeaveBalance.query.filter_by(employee_id=ids["et"], leave_type_id=annual).first()
        return b.current_balance, b.taken_days
def records():
    with app.app_context():
        return LeaveRecord.query.order_by(LeaveRecord.id).all()
def post(c, **kw):
    d = {"employee_id": ids["et"], "leave_type_id": annual, "start_date": "2026-11-02", "end_date": "2026-11-06"}
    d.update(kw)
    return c.post("/hr/leave/record", data=d, follow_redirects=True)

# the page
r = A.get("/hr/leave")
b = r.get_data(as_text=True)
check(r.status_code == 200 and "Record leave" in b and "Tendai Moyo" in b and "Rudo Chari" in b, "reviewer sees the form with every staff member")
r = T.get("/hr/leave"); b = r.get_data(as_text=True)
check(r.status_code == 200 and "Record leave" in b and "Rudo Chari</option>" not in b and "Tendai Moyo</option>" in b, "staff sees the form with only themselves")
check("No Payroll employee profile is linked" in NL.get("/hr/leave").get_data(as_text=True), "a user with no profile is told to get one linked")

# reviewer records annual leave: days default to the working days
check(balance() == (20.0, 0.0), "starts with 20 days")
r = post(A)
check(b"5 day(s) of Annual/Vacation Leave recorded for Tendai Moyo" in r.data and b"15.00 day(s) remain" in r.data, "5 working days recorded; 15 remain")
check(balance() == (15.0, 5.0), "balance reduced by 5")
rec = records()[0]
check(rec.days == 5.0 and rec.recorded_by_id == ids["adm"], "record keeps days and who entered it")

# overlap refused
r = post(A, start_date="2026-11-05", end_date="2026-11-09")
check(b"already has leave recorded" in r.data and len(records()) == 1, "overlapping dates refused")
# typed days (a public holiday left out)
r = post(A, start_date="2026-11-09", end_date="2026-11-13", days="4")
check(b"4 day(s)" in r.data and balance() == (11.0, 9.0), "typed days used (4, not 5)")
# half day
r = post(A, start_date="2026-11-16", end_date="2026-11-16", days="0.5")
check(b"0.5 day(s)" in r.data and balance() == (10.5, 9.5), "half day recorded")
# weekend only
r = post(A, start_date="2026-11-21", end_date="2026-11-22")
check(b"no working days" in r.data and len(records()) == 3, "a weekend alone is refused")
# days more than the dates
r = post(A, start_date="2026-11-23", end_date="2026-11-24", days="5")
check(b"more than the dates cover" in r.data, "more days than the dates cover refused")
# backwards
r = post(A, start_date="2026-11-30", end_date="2026-11-27")
check(b"cannot be before" in r.data, "last day before first day refused")
# more than the balance
r = post(A, start_date="2026-12-01", end_date="2027-01-29", days="40")
check(b"Only 10.50 day(s) of Annual/Vacation Leave are available" in r.data and balance() == (10.5, 9.5), "more than the balance refused, balance untouched")

# minimum service: Rudo has ~100 days of service, annual needs 12 months
r = post(A, employee_id=ids["er"], start_date="2026-12-07", end_date="2026-12-08")
check(b"12 months of continuous service" in r.data, "minimum service rule applies")

# sick leave is a provision: no balance, still recorded
n = len(records())
r = post(A, employee_id=ids["er"], leave_type_id=sick, start_date="2026-12-07", end_date="2026-12-08", reason="Flu, certificate seen")
check(b"2 day(s) of Sick Leave recorded for Rudo Chari" in r.data and len(records()) == n + 1, "sick leave recorded with no balance check")
with app.app_context():
    check(LeaveBalance.query.filter_by(employee_id=ids["er"], leave_type_id=sick).first() is None, "no balance row kept for a provision")

# staff: own only
r = post(T, employee_id=ids["r"], start_date="2026-12-14", end_date="2026-12-14", leave_type_id=sick)
check(r.status_code == 403 or b"recorded for Rudo" not in r.data, "staff cannot record for someone else")
check(T.post("/hr/leave/record", data={"employee_id": ids["er"], "leave_type_id": sick, "start_date": "2026-12-14", "end_date": "2026-12-14"}).status_code == 403,
      "403 for staff entering someone else's leave")
r = T.post("/hr/leave/record", data={"leave_type_id": sick, "start_date": "2026-12-14", "end_date": "2026-12-15"}, follow_redirects=True)
check(b"2 day(s) of Sick Leave recorded for Tendai Moyo" in r.data, "staff records their own leave (employee defaults to themselves)")
mine = [x for x in records() if x.recorded_by_id == ids["t"]]
check(len(mine) == 1, "one record by the staff member")
# staff cannot delete someone else's or a record a manager entered
check(T.post(f"/hr/leave/records/{records()[0].id}/delete").status_code == 403, "staff cannot delete a record a manager entered")
check(R.post(f"/hr/leave/records/{mine[0].id}/delete").status_code == 403, "another staff member cannot delete it")
check(len([1 for x in records()]) == n + 2, "nothing was deleted")

# history
r = A.get("/hr/leave?year=2026"); b = r.get_data(as_text=True)
check("Leave taken in 2026" in b and "Flu, certificate seen" in b and "Total days" in b, "history lists the records with a total")
r = A.get(f"/hr/leave?year=2026&employee_id={ids['er']}"); b = r.get_data(as_text=True)
check("Flu, certificate seen" in b and "Tendai Moyo</td>" not in b, "history filters by staff member")
b = T.get("/hr/leave?year=2026").get_data(as_text=True)
check("Flu, certificate seen" not in b and "Annual/Vacation Leave</td>" in b, "staff see only their own history")
check("No leave recorded for 2020" in A.get("/hr/leave?year=2020").get_data(as_text=True), "empty year says so")

# a timesheet leave entry shows in the history too
with app.app_context():
    ts = TimeSheet(user_id=ids["t"], week_start=date(2026, 9, 28), status="Submitted"); db.session.add(ts); db.session.commit()
    db.session.add(TimeEntry(timesheet_id=ts.id, work_date=date(2026, 9, 30), hours=0, leave_type_id=sick, leave_days=1.0, description="Sick Leave (1 day)")); db.session.commit()
b = A.get("/hr/leave?year=2026").get_data(as_text=True)
check("on timesheet" in b, "leave captured on a timesheet appears in the history")

# deleting gives the days back (annual) and removes the record
before = balance()
r = S.post(f"/hr/leave/records/{records()[0].id}/delete", follow_redirects=True)
check(b"5 day(s) of Annual/Vacation Leave removed" in r.data and balance() == (before[0] + 5, before[1] - 5), "supervisor deletes; 5 days go back to the balance")
check(len(records()) == n + 1, "record gone")
# deleting a sick record leaves balances alone
sick_rec = [x for x in records() if x.reason == "Flu, certificate seen"][0]
A.post(f"/hr/leave/records/{sick_rec.id}/delete")
check(balance() == (before[0] + 5, before[1] - 5), "provision record deletion touches no balance")
# staff deletes their own
r = T.post(f"/hr/leave/records/{mine[0].id}/delete", follow_redirects=True)
check(b"removed for Tendai Moyo" in r.data, "staff member deletes their own record")

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
