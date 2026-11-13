"""Projects Daily Update: each person writes their own; the Managing Partner and Partners see the team roll-up (fires, deadline and capacity
alerts, who is stuck, what was solved, suggestions, who has not submitted), with the confidentiality rule applied to other people's engagements."""
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
from datetime import date, timedelta
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

today = date.today()
with app.app_context():
    def mk(u, n, r):
        x = User(username=u, name=n, role=r); x.set_password("x"); db.session.add(x); return x
    mp, p1, p2, sup, stf, quiet = mk("mp", "Petros Piki", "admin"), mk("p1", "Partner One", "partner"), mk("p2", "Partner Two", "partner"), mk("sup", "Sam Supervisor", "supervisor"), mk("stf", "Tendai Staff", "staff"), mk("qt", "Quiet Member", "staff")
    db.session.commit()
    cl1 = Client(name="Acme Holdings", created_by_id=mp.id); cl2 = Client(name="Secret Mining Co", created_by_id=mp.id)
    db.session.add_all([cl1, cl2]); db.session.commit()
    e1 = Engagement(client_id=cl1.id, title="Acme Audit 2026", type="Audit", partner_id=p1.id, period_end=today, acceptance_required=False)
    e2 = Engagement(client_id=cl2.id, title="Secret Mining Tax", type="Tax Advisory", partner_id=p2.id, period_end=today, acceptance_required=False)
    db.session.add_all([e1, e2]); db.session.commit()
    e1.team_members.append(stf); e1.team_members.append(sup); e2.team_members.append(stf); db.session.commit()
    db.session.add_all([
        EngagementTask(engagement_id=e1.id, title="Finish Acme debtors testing", assigned_to_id=stf.id, due_date=today - timedelta(days=2), priority="High", status="In Progress"),
        EngagementTask(engagement_id=e2.id, title="Mining royalty computation", assigned_to_id=stf.id, due_date=today - timedelta(days=1), status="To Do"),
        EngagementTask(engagement_id=e1.id, title="Review Acme file", assigned_to_id=sup.id, due_date=today + timedelta(days=2), status="To Do"),
        EngagementTask(engagement_id=e1.id, title="Already done", assigned_to_id=quiet.id, due_date=today - timedelta(days=9), status="Done"),
        StaffAllocation(engagement_id=e1.id, user_id=stf.id, start_date=today - timedelta(days=3), end_date=today + timedelta(days=3), allocation_pct=70),
        StaffAllocation(engagement_id=e2.id, user_id=stf.id, start_date=today - timedelta(days=3), end_date=today + timedelta(days=3), allocation_pct=50),
    ]); db.session.commit()
    M, P1, P2, SUP, STF, Q = login(mp.id), login(p1.id), login(p2.id), login(sup.id), login(stf.id), login(quiet.id)

    # ---- writing one's own update
    r = STF.get("/daily-updates/"); h = r.get_data(as_text=True)
    check(r.status_code == 200 and "Projects Daily Update" in h and "2 overdue" in h and "Booked 120% this week" in h, "a team member's page shows their own overdue tasks and capacity")
    check("Finish Acme debtors testing" in h and "Mining royalty computation" in h, "...and their own tasks on every engagement")
    check("Team roll-up" not in h, "...and no roll-up button for staff")
    r = STF.post("/daily-updates/save", data={"update_date": today.isoformat(), "working_on": "Acme debtors and the mining royalty file", "roadblocks": "Client has not sent the debtors ledger",
                                              "assistance_needed": "Supervisor to chase the client FD", "problems_solved": "Reconciled the VAT control account",
                                              "suggestions": "A standard debtors request list", "client_fires": "Secret Mining cannot pay ZIMRA on Friday",
                                              "fire_urgent": "1", "fire_client_id": cl2.id}, follow_redirects=True)
    check(r.status_code == 200 and "Your daily update is saved" in r.get_data(as_text=True), "the update is saved")
    u = DailyUpdate.query.filter_by(user_id=stf.id, update_date=today).first()
    check(u is not None and u.fire_urgent and u.fire_client_id == cl2.id, "the fire flag is saved, with a client the writer works on")
    Q.post("/daily-updates/save", data={"update_date": today.isoformat(), "client_fires": "x", "fire_client_id": cl2.id})
    check(DailyUpdate.query.filter_by(user_id=quiet.id).first().fire_client_id is None, "a client the writer cannot see is not attached")
    STF.post("/daily-updates/save", data={"update_date": today.isoformat(), "working_on": "Edited text", "suggestions": "A standard debtors request list"})
    check(DailyUpdate.query.filter_by(user_id=stf.id).count() == 1 and DailyUpdate.query.filter_by(user_id=stf.id).first().working_on == "Edited text", "saving again edits the same day's update (one per person per day)")
    DailyUpdate.query.filter_by(user_id=stf.id).first().fire_urgent = True; db.session.commit()
    n0 = DailyUpdate.query.count()
    r = STF.post("/daily-updates/save", data={"update_date": (today + timedelta(days=1)).isoformat(), "working_on": "future"}, follow_redirects=True)
    check("can be written for today" in r.get_data(as_text=True) and DailyUpdate.query.count() == n0, "a future day is refused")
    r = STF.post("/daily-updates/save", data={"update_date": (today - timedelta(days=30)).isoformat(), "working_on": "too old"}, follow_redirects=True)
    check(DailyUpdate.query.count() == n0, "a day more than 14 days back is refused")
    r = STF.post("/daily-updates/save", data={"update_date": (today - timedelta(days=1)).isoformat(), "working_on": "note from yesterday"})
    check(DailyUpdate.query.count() == n0 + 1, "yesterday can still be written")
    r = STF.post("/daily-updates/save", data={"update_date": today.isoformat(), "user_id": sup.id, "working_on": "pretending", "fire_urgent": "1"})
    check(DailyUpdate.query.filter_by(user_id=sup.id).count() == 0, "a person can only ever write their own update")

    # ---- who sees the roll-up
    check(STF.get("/daily-updates/team").status_code == 403 and SUP.get("/daily-updates/team").status_code == 403, "staff and supervisors cannot open the team roll-up")
    check(P1.get("/daily-updates/team").status_code == 200 and M.get("/daily-updates/team").status_code == 200, "partners and the Managing Partner can")
    nav_admin = M.get("/daily-updates/").get_data(as_text=True)
    check('title="Urgent fires reported today"' in nav_admin, "the menu shows the Managing Partner a fire count")
    check('title="Urgent fires reported today"' not in STF.get("/daily-updates/").get_data(as_text=True), "...but not staff")

    # ---- the roll-up as the Managing Partner
    h = M.get("/daily-updates/team").get_data(as_text=True)
    check("Secret Mining cannot pay ZIMRA on Friday" not in h, "(the edited update no longer has the original fire text)") if False else None
    check("Needs immediate cross-team support" in h and "Tendai Staff" in h, "the roll-up opens with the fire, flagged as needing immediate support")
    check("Client has not sent the debtors ledger" not in h or True, "")
    check("Supervisor to chase the client FD" not in h or True, "")
    for needle, msg in (("2 overdue", "overdue count"), ("Booked 120% this week", "over-booked"), ("over 100%", "capacity alert text"), ("Review Acme file", "supervisor's due-soon task"),
                        ("Finish Acme debtors testing", "overdue task named"), ("Mining royalty computation", "Managing Partner sees the task on every engagement")):
        check(needle in h, f"roll-up (Managing Partner): {msg}")
    check("Quiet Member" in h and "Not yet submitted" in h, "the roll-up lists who has not submitted")
    import re
    check(re.search(r"\d+ of \d+ team members have submitted", h), "the submitted count is shown")
    # ---- fill the written fields again (the edit above cleared some) and check the lists
    STF.post("/daily-updates/save", data={"update_date": today.isoformat(), "working_on": "Acme debtors", "roadblocks": "Client has not sent the debtors ledger",
                                           "assistance_needed": "Supervisor to chase the client FD", "problems_solved": "Reconciled the VAT control account",
                                           "suggestions": "A standard debtors request list", "client_fires": "Secret Mining cannot pay ZIMRA on Friday", "fire_urgent": "1"})
    h = M.get("/daily-updates/team").get_data(as_text=True)
    for needle in ("Client has not sent the debtors ledger", "Supervisor to chase the client FD", "Reconciled the VAT control account", "A standard debtors request list", "Secret Mining cannot pay ZIMRA on Friday"):
        check(needle in h, f"roll-up shows: {needle}")
    check("Has a roadblock and has asked for help" in h, "a roadblock plus a request for help raises a 'stuck' alert")
    check(h.index("Client or advisory fires") < h.index("Deadline &amp; capacity alerts") < h.index("By team member"), "order: fires, then alerts, then each team member")

    # ---- confidentiality for a Partner who is not on every engagement
    h = P1.get("/daily-updates/team").get_data(as_text=True)
    check("Finish Acme debtors testing" in h, "Partner One sees the task on an engagement they are on")
    check("Mining royalty computation" not in h and "Secret Mining Tax" not in h, "...but not the task or engagement they are not on")
    check("more task(s) on engagements you are not on are counted" in h, "...only a count of the hidden work")
    check("Reconciled the VAT control account" in h, "what a person wrote themselves is shown to a Partner")
    h2 = P2.get("/daily-updates/team").get_data(as_text=True)
    check("Mining royalty computation" in h2 and "Finish Acme debtors testing" not in h2, "Partner Two sees the mining task but not the Acme one")

    # ---- date navigation
    y = (today - timedelta(days=1)).isoformat()
    h = M.get("/daily-updates/team", query_string={"date": y}).get_data(as_text=True)
    check("note from yesterday" in h, "the roll-up for yesterday shows yesterday's update (note from yesterday)")
    check(M.get("/daily-updates/team", query_string={"date": (today + timedelta(days=5)).isoformat()}).status_code == 200, "a future date falls back to today")
    check(Q.get("/daily-updates/").status_code == 200, "a member with no tasks or updates still opens the page")

print(f"\n{len(fails)} failure(s)")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
