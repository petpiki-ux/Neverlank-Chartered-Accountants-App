"""Monthly trial balances for Accounting & Bookkeeping engagements: uploads (movements / closing / opening), cumulative consolidation,
reconciliation to the year-end Trial Balance, the Excel download, and loading the months into a Management Accounts pack."""
import os, sys, tempfile, io
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
from datetime import date as D
from app import create_app
from extensions import db
from models import *
import monthly_tb as MT
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def R(code, name, v): return {"code": code, "name": name, "net_dr": v}

# ---- pure arithmetic
check(MT.period_months(D(2026,1,1), D(2026,10,31)) == ["2026-%02d" % m for m in range(1, 11)], "ten-month period -> ten months")
check(len(MT.period_months(None, D(2026,12,31))) == 12 and MT.period_months(None, D(2026,12,31))[0] == "2026-01", "no period start -> twelve months ending at period end")
opening = [R("1000", "Bank", 5000), R("3000", "Capital", -5000)]
jan = [R("1000", "Bank", 1000), R("4000", "Sales", -1000)]
feb = [R("1000", "Bank", 700), R("4000", "Sales", -1200), R("5000", "Rent", 500)]
ups = [{"id": 1, "period": "2026-01", "basis": "opening", "rows": opening}, {"id": 2, "period": "2026-01", "basis": "movement", "rows": jan},
       {"id": 3, "period": "2026-02", "basis": "movement", "rows": feb}]
c = MT.consolidate(ups)
check([m["period"] for m in c["months"]] == ["2026-01", "2026-02"], "one column per month")
k = lambda code: "c:" + code
check(c["months"][0]["cum"][k("1000")] == 6000 and c["months"][0]["cum"][k("4000")] == -1000, "January = opening + January movements")
check(c["months"][1]["cum"][k("1000")] == 6700 and c["months"][1]["cum"][k("4000")] == -2200 and c["months"][1]["cum"][k("5000")] == 500, "February cumulative adds the February movements")
check(c["months"][1]["mov"][k("4000")] == -1200, "February movement is kept separately")
check(c["months"][1]["cum_sum"] == 0 and not c["warnings"], "cumulative TB balances, no warnings")
# closing-balance upload replaces the running total; the month's movement is the difference
mar_close = [R("1000", "Bank", 7000), R("3000", "Capital", -5000), R("4000", "Sales", -3000), R("5000", "Rent", 1000)]
c2 = MT.consolidate(ups + [{"id": 4, "period": "2026-03", "basis": "closing", "rows": mar_close}])
m3 = c2["months"][2]
check(m3["cum"][k("1000")] == 7000 and m3["mov"][k("1000")] == 300 and m3["mov"][k("4000")] == -800, "closing upload: cumulative = file, movement = file less the running total")
# gap + unbalanced + no opening
gap = MT.consolidate([{"id": 1, "period": "2026-01", "basis": "movement", "rows": jan}, {"id": 2, "period": "2026-03", "basis": "movement", "rows": [R("1000", "Bank", 5), R("4000", "Sales", -4)]}])
w = " ".join(gap["warnings"])
check("Feb 2026" in w and "does not balance" in w and "opening" in w.lower(), "warns about a missing month, a file that does not balance and no opening balances: %s" % gap["warnings"])
# a file without codes lands on the coded account by name
mixed = MT.consolidate([{"id": 1, "period": "2026-01", "basis": "movement", "rows": [R("1000", "Bank", 10), R("4000", "Sales", -10)]},
                        {"id": 2, "period": "2026-02", "basis": "movement", "rows": [R("", "bank", 5), R("", "SALES", -5)]}])
check(len(mixed["accounts"]) == 2 and mixed["months"][1]["cum"][k("1000")] == 15, "an upload with no codes joins the coded account by name")
rows = MT.rows_for_month(c, "2026-02")
check(len(rows) == 4 and sum(r["net_dr"] for r in rows) == 0 and MT.rows_for_month(c, "2025-12") is None, "rows_for_month gives the cumulative TB; None before the first upload")
# reconciliation
ye = [R("1000", "Bank", 6700), R("3000", "Capital", -5000), R("4000", "Sales", -2200), R("5000", "Rent", 500)]
rec = MT.reconcile(rows, ye)
check(rec["reconciled"] and rec["totals"]["diff"] == 0, "reconciles when every account agrees")
ye2 = [R("1000", "Bank", 6650), R("3000", "Capital", -5000), R("4000", "Sales", -2200), R("5000", "Rent", 500), R("6000", "Audit fees", 50)]
rec2 = MT.reconcile(rows, ye2)
check(not rec2["reconciled"] and len(rec2["off"]) == 2 and rec2["yearend_only"][0]["name"] == "Audit fees" and rec2["lines"][0]["diff"] == 50, "differences and a year-end-only account are reported")
rec3 = MT.reconcile([R("", "Bank Account", 10)], [R("1000", "bank account", 10)])
check(rec3["reconciled"], "matches by name when the codes differ or are missing")

# ---- through the app
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Bookkeeping Client (Private) Limited", created_by_id=aid, reporting_currency="USD"); db.session.add(cl); db.session.commit(); cid = cl.id
cl_ = app.test_client()
with cl_.session_transaction() as s:
    s["_user_id"] = str(aid); s["_fresh"] = True
form = {"client_id": str(cid), "title": "Bookkeeping 2026", "type": "Accounting & Bookkeeping", "partner_id": str(aid), "manager_id": str(aid), "period_end": "2026-12-31",
        "start_date": "2026-01-01", "status": "In Progress", "reporting_framework": "full_ifrs", "acceptance_required": ""}
cl_.post("/engagements/new", data=form)
with app.app_context():
    e = Engagement.query.filter_by(title="Bookkeeping 2026").first(); eid = e.id
    check(e is not None and e.has_accounting_module, "Accounting & Bookkeeping engagement created")
def csv(rows):
    body = "Account Code,Account Name,Debit,Credit\n" + "".join("%s,%s,%s,%s\n" % (r["code"], r["name"], r["net_dr"] if r["net_dr"] > 0 else "", -r["net_dr"] if r["net_dr"] < 0 else "") for r in rows)
    return io.BytesIO(body.encode())
def up(month, basis, rows, name="tb.csv"):
    return cl_.post(f"/monthly-tb/{eid}/upload", data={"month": month, "basis": basis, "file": (csv(rows), name)}, content_type="multipart/form-data", follow_redirects=True)
page = cl_.get(f"/engagements/{eid}?tab=accounting").get_data(as_text=True)
check("Monthly Trial Balances" in page and f"/monthly-tb/{eid}" in page, "the Accounting tab links to the monthly trial balances page")
pg = cl_.get(f"/monthly-tb/{eid}").get_data(as_text=True)
check("Upload a trial balance" in pg and "Nothing uploaded yet" in pg, "the page opens with no uploads")
r = up("2026-01", "opening", opening); check("loaded: 2 accounts" in r.get_data(as_text=True), "opening balances uploaded")
up("2026-01", "movement", jan); r = up("2026-02", "movement", feb)
txt = r.get_data(as_text=True)
check("Cumulative trial balance" in txt and "6,700.00" in txt and "-2,200.00" in txt, "the cumulative table shows February's cumulative figures")
r = up("2026-02", "movement", [R("1000", "Bank", 50)]); txt = r.get_data(as_text=True)
check("replaced an earlier upload" in txt and "does not balance" in txt, "re-uploading the same month replaces it, and an unbalanced file is flagged")
up("2026-02", "movement", feb)
with app.app_context():
    check(MonthlyTB.query.filter_by(engagement_id=eid).count() == 3, "three uploads stored (opening, Jan, Feb)")
check("There is no year-end Trial Balance" in cl_.get(f"/monthly-tb/{eid}").get_data(as_text=True), "reconciliation waits for the year-end trial balance")
with app.app_context():
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for code, nm, cd, cc in [("1000", "Bank", 6700, 0), ("3000", "Capital", 0, 5000), ("4000", "Sales", 0, 2250), ("5000", "Rent", 550, 0)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=code, account_name=nm, current_debit=cd, current_credit=cc))
    db.session.commit()
txt = cl_.get(f"/monthly-tb/{eid}?month=2026-02").get_data(as_text=True)
check("Not reconciled" in txt and "2 account(s) differ" in txt, "differences against the year-end TB are shown")
with app.app_context():
    for l in TrialBalanceLine.query.filter_by(account_code="4000"): l.current_credit = 2200
    for l in TrialBalanceLine.query.filter_by(account_code="5000"): l.current_debit = 500
    db.session.commit()
txt = cl_.get(f"/monthly-tb/{eid}?month=2026-02").get_data(as_text=True)
check("<strong>Reconciled.</strong>" in txt, "reconciled once the year-end TB agrees")
x = cl_.get(f"/monthly-tb/{eid}/download?month=2026-02")
check(x.status_code == 200 and x.data[:2] == b"PK", "Excel download")
import openpyxl
wb = openpyxl.load_workbook(io.BytesIO(x.data))
check(wb.sheetnames == ["Cumulative", "Monthly movements", "Reconciliation"], "workbook sheets: %s" % wb.sheetnames)
# Management accounts
r = cl_.get(f"/corporate-finance/engagements/{eid}/management-accounts")
check(r.status_code == 200, "Accounting & Bookkeeping engagement can open its management accounts packs: %s" % r.status_code)
r = cl_.post("/corporate-finance/management-accounts/new", data={"client_id": str(cid), "engagement_id": str(eid), "name": "MA 2026", "fy_start_month": "1", "fy_start_year": "2026", "currency": "USD", "basis": "simple"}, follow_redirects=False)
with app.app_context():
    pk = CFMgmtPack.query.filter_by(engagement_id=eid).first()
    check(pk is not None, "a management accounts pack can be created on the engagement"); pid = pk.id
r = cl_.get(f"/corporate-finance/management-accounts/{pid}/actuals")
check("Load from the monthly trial balances" in r.get_data(as_text=True), "the Actuals tab offers the monthly trial balances")
r = cl_.post(f"/corporate-finance/management-accounts/{pid}/tb/from-monthly", data={"month": "2026-05"}, follow_redirects=True)
check("No monthly trial balance has been uploaded for May 2026" in r.get_data(as_text=True), "a month with no upload is refused")
r = cl_.post(f"/corporate-finance/management-accounts/{pid}/tb/from-monthly", data={"month": "2026-02"})
t = r.get_data(as_text=True)
check(r.status_code == 200 and "Sales" in t and "Rent" in t and "tb/confirm" in t, "one month opens the mapping page with the cumulative accounts")
r = cl_.post(f"/corporate-finance/management-accounts/{pid}/tb/from-monthly-all", follow_redirects=True)
t = r.get_data(as_text=True)
check(r.status_code == 200, "load all months responds")
import json as _json
rows2 = MT.rows_for_month(MT.consolidate(ups), "2026-02")
tgt = {"Bank": "cash", "Capital": "share_capital", "Sales": "rev_1", "Rent": "opx_1"}
data = {"target": "actual", "month": "2026-02", "rows_json": _json.dumps(rows2), "filename": "Monthly trial balances to Feb 2026"}
for i_, r_ in enumerate(rows2): data["map_%d" % i_] = tgt[r_["name"]]
r = cl_.post(f"/corporate-finance/management-accounts/{pid}/tb/confirm", data=data, follow_redirects=True)
with app.app_context():
    d = CFMgmtPack.query.get(pid).data
    check("2026-02" in (d.get("entries") or {}) and d["entries"]["2026-02"]["bs"]["cash"] == 6700 and d["entries"]["2026-02"]["pl"]["rev_1"] == 2200, "confirming the mapping saves February's cumulative actuals into the pack")
    check(d["mapping"].get("bank") == "cash", "and remembers the mapping")
r = cl_.post(f"/corporate-finance/management-accounts/{pid}/tb/from-monthly-all", follow_redirects=True)
with app.app_context():
    d = CFMgmtPack.query.get(pid).data
    check(sorted(d["entries"]) == ["2026-01", "2026-02"] and d["entries"]["2026-01"]["pl"]["rev_1"] == 1000, "load every month now saves January too using the saved mapping: %s" % sorted(d["entries"]))
check("Loaded Jan 2026" in r.get_data(as_text=True), "and says what it loaded")
print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
