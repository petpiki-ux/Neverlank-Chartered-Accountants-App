"""Adding to the set-up from the Trial Balance tab: new Standard Chart accounts (with the firm's own category, flagged for review), the client's
remembered mappings (add / change / delete / paste many), and trial balance accounts pasted from Excel."""
import os, sys, tempfile, io
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from app import create_app
from extensions import db
from models import *
import financials as fin
import tb_setup
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
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    for t, cols in (("standard_chart_of_accounts", ("sub_category", "needs_review", "added_by_id", "added_from_engagement_id")), ("trial_balance_line", ("sub_category",))):
        have = [c["name"] for c in insp.get_columns(t)]
        check(all(c in have for c in cols), f"{t} has {', '.join(cols)}")
    check("fs_sub_category" in insp.get_table_names(), "fs_sub_category table exists")
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    stranger = User(username="str", name="Stranger", role="staff"); stranger.set_password("x")
    db.session.add_all([partner, staff, stranger]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=staff.id); cl2 = Client(name="Other Co", created_by_id=stranger.id)
    db.session.add_all([cl, cl2]); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Audit 2026", type="Audit", partner_id=partner.id, manager_id=staff.id, acceptance_required=False)
    eng2 = Engagement(client_id=cl2.id, title="Other audit", type="Audit", partner_id=stranger.id, manager_id=stranger.id, acceptance_required=False)
    db.session.add_all([eng, eng2]); db.session.commit()
    ids = dict(admin=admin.id, partner=partner.id, staff=staff.id, stranger=stranger.id, cl=cl.id, cl2=cl2.id, eng=eng.id, eng2=eng2.id)
    check(StandardChartOfAccounts.query.count() > 20, "the seeded Standard Chart is there")
    admin_numbers = {a.account_number for a in StandardChartOfAccounts.query.all()}
A = login(ids["admin"]); S = login(ids["staff"]); P = login(ids["partner"]); O = login(ids["stranger"])
eid = ids["eng"]; T = f"/engagements/{eid}/trial-balance"

# ---------------------------------------------------------------- the tab renders before any trial balance exists
r = S.get(f"/engagements/{eid}?tab=trial_balance"); h = r.data.decode()
check(r.status_code == 200 and "Add a new Standard Chart account" in h and "remembered mappings" in h and "Paste accounts from Excel" in h, "set-up cards show on a Trial Balance tab with no trial balance yet")
check(h.count("Add a new Standard Chart account") == 1, "shown once")

# ---------------------------------------------------------------- pure helpers
check(tb_setup.resolve_category("Administrative expenses") == "admin_expenses" and tb_setup.resolve_category("admin_expenses") == "admin_expenses", "resolve_category: label or code")
check(tb_setup.resolve_category("administrative") == "admin_expenses", "resolve_category: a unique start of a label")
check(tb_setup.resolve_category("other") is None and tb_setup.resolve_category("nonsense") is None and tb_setup.resolve_category("") is None, "resolve_category: ambiguous / unknown / blank -> None")
check(tb_setup._num("1,234.50") == 1234.5 and tb_setup._num("(500)") == -500 and tb_setup._num("") == 0.0 and tb_setup._num("-") == 0.0 and tb_setup._num("abc") is None, "_num parses Excel-style amounts")
with app.app_context():
    nn = tb_setup.suggested_numbers()
    check(nn["ppe"] and nn["ppe"] not in admin_numbers, "next number for a category is free: %s" % nn["ppe"])

# ---------------------------------------------------------------- a trial balance to work on (CSV import)
csv = "Account Code,Account Name,Current Year Debit,Current Year Credit,Prior Year Debit,Prior Year Credit\n1,Cash at bank,1000,0,800,0\n2,Staff welfare,300,0,200,0\n3,Sundry income,0,1300,0,1000\n"
r = S.post(f"{T}/upload", data={"file": (io.BytesIO(csv.encode()), "tb.csv")}, content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=eid).first()
    check(tb is not None and len(tb.lines) == 3, "trial balance imported")
    welfare_id = [l for l in tb.lines if l.account_name == "Staff welfare"][0].id

# ---------------------------------------------------------------- 1. a new Standard Chart account
r = S.post(f"{T}/standard-accounts/add", data={"fs_category": "admin_expenses", "account_name": "Staff welfare", "sub_category": "People costs", "apply_line_id": welfare_id}, follow_redirects=True)
with app.app_context():
    a = StandardChartOfAccounts.query.filter_by(account_name="Staff welfare").first()
    check(a is not None and a.fs_category == "admin_expenses" and a.needs_review and a.added_by_id == ids["staff"] and a.added_from_engagement_id == eid, "account created, flagged for review, remembers who and where")
    check(a.account_number and a.account_number not in admin_numbers, "number left blank -> next free one chosen: %s" % a.account_number)
    check(a.sub_category == "People costs" and FSSubCategory.query.filter_by(parent_code="admin_expenses", label="People costs").count() == 1, "own category created under the IAS 1 line")
    line = db.session.get(TrialBalanceLine, welfare_id)
    check(line.fs_category == "admin_expenses" and line.coa_account_number == a.account_number and line.sub_category == "People costs", "the chosen trial balance account is mapped to it, with the own category")
    m = COAMapping.query.filter_by(client_id=ids["cl"], account_name="staff welfare").first()
    check(m and m.coa_account_number == a.account_number and m.fs_category == "admin_expenses", "and the mapping is remembered for the client")
    tb = TrialBalance.query.filter_by(engagement_id=eid).first()
    new_no = a.account_number
check(b"Staff welfare" in r.data and b"added" in r.data, "a confirmation is shown")
r = S.post(f"{T}/standard-accounts/add", data={"fs_category": "admin_expenses", "account_name": "Staff welfare"}, follow_redirects=True)
check(b"already exists" in r.data, "the same name in the same category is refused with a pointer to the existing one")
r = S.post(f"{T}/standard-accounts/add", data={"fs_category": "admin_expenses", "account_name": "Other thing", "account_number": new_no}, follow_redirects=True)
check(b"already in the Standard Chart" in r.data, "a number already used is refused")
r = S.post(f"{T}/standard-accounts/add", data={"fs_category": "", "account_name": "No category"}, follow_redirects=True)
check(b"choose its IAS 1 category" in r.data, "a missing category is refused")
r = O.post(f"{T}/standard-accounts/add", data={"fs_category": "ppe", "account_name": "Intruder"})
check(r.status_code == 403, "someone not on the engagement cannot add")
with app.app_context():
    check(StandardChartOfAccounts.query.filter_by(account_name="Intruder").first() is None, "and nothing was created")
r = S.post(f"{T}/standard-accounts/add", data={"fs_category": "ppe", "account_name": "Drones", "account_number": "9900", "sub_category": "  Aerial   assets "}, follow_redirects=True)
with app.app_context():
    d = StandardChartOfAccounts.query.filter_by(account_number="9900").first()
    check(d and d.sub_category == "Aerial assets", "an own category's spacing is tidied")
r = S.post(f"{T}/standard-accounts/add", data={"fs_category": "ppe", "account_name": "Drones 2", "account_number": "9901", "sub_category": "aerial ASSETS"}, follow_redirects=True)
with app.app_context():
    check(FSSubCategory.query.filter_by(parent_code="ppe").count() == 1 and StandardChartOfAccounts.query.filter_by(account_number="9901").first().sub_category == "Aerial assets", "an own category typed in other case reuses the existing one")
# it shows in the pickers and the face
h = S.get(f"/engagements/{eid}?tab=trial_balance").data.decode()
check(f"{new_no} - Staff welfare" in h, "the new account is in the Standard Account lists")
check("&rsaquo; People costs" in h, "the own category is shown on the trial balance")
check("1 account(s) added from engagements are waiting" in h or "account(s) added from engagements" in h, "the review note shows on the tab")

# ---------------------------------------------------------------- the Chart of Accounts page: review and own category
r = A.get("/chart-of-accounts/"); h = r.data.decode()
check(r.status_code == 200 and "Added from an engagement - review" in h and "Own category" in h and 'value="People costs"' in h, "the Chart of Accounts page marks it for review and shows the own category")
with app.app_context():
    aid = StandardChartOfAccounts.query.filter_by(account_name="Staff welfare").first().id
r = A.post(f"/chart-of-accounts/{aid}/update", data={"account_number": new_no, "account_name": "Staff welfare", "fs_category": "admin_expenses", "sub_category": "Staff costs", "notes": "ok", "is_active": "on"}, follow_redirects=True)
with app.app_context():
    a = db.session.get(StandardChartOfAccounts, aid)
    check(not a.needs_review and a.sub_category == "Staff costs", "saving the row clears the review flag and takes the new own category")
    check(FSSubCategory.query.filter_by(parent_code="admin_expenses", label="Staff costs").count() == 1, "the new own category exists")
r = A.post("/chart-of-accounts/", data={"account_number": "7777", "account_name": "Brand new", "fs_category": "revenue", "sub_category": "Fees"}, follow_redirects=True)
with app.app_context():
    n = StandardChartOfAccounts.query.filter_by(account_number="7777").first()
    check(n and n.sub_category == "Fees" and not n.needs_review, "an account added on the Chart page carries its own category and needs no review")
r = S.get("/chart-of-accounts/"); check(r.status_code == 403, "staff still cannot manage the Chart page")

# ---------------------------------------------------------------- 2. the client's remembered mappings
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=eid).first()
    for l in tb.lines:
        if l.account_name == "Sundry income": sundry = l.id
    cash_id = [l for l in tb.lines if l.account_name == "Cash at bank"][0].id
r = S.post(f"{T}/mappings/add", data={"account_name": "  Sundry   Income ", "fs_category": "other_income"}, follow_redirects=True)
with app.app_context():
    line = db.session.get(TrialBalanceLine, sundry)
    check(line.fs_category == "other_income", "a new mapping maps the matching unmapped account on this trial balance")
    m = COAMapping.query.filter_by(client_id=ids["cl"], account_name="sundry income").first()
    check(m is not None, "stored under the normalised name")
    mid = m.id
r = S.post(f"{T}/mappings/add", data={"account_name": "Cash at bank", "coa_account_number": "1100"}, follow_redirects=True)
with app.app_context():
    std = StandardChartOfAccounts.query.filter_by(account_number="1100").first()
    line = db.session.get(TrialBalanceLine, cash_id)
    check(std and line.fs_category == std.fs_category and line.coa_account_number == "1100", "a Standard account decides the category")
r = S.post(f"{T}/mappings/add", data={"account_name": "Nothing chosen"}, follow_redirects=True)
check(b"choose a Standard Account or an IAS 1 category" in r.data, "a mapping with no target is refused")
# changing a mapping does not touch an already-mapped line
r = S.post(f"{T}/mappings/{mid}/update", data={"fs_category": "revenue"}, follow_redirects=True)
with app.app_context():
    check(db.session.get(COAMapping, mid).fs_category == "revenue" and db.session.get(TrialBalanceLine, sundry).fs_category == "other_income", "changing a mapping leaves lines that are already mapped alone")
    tb = TrialBalance.query.filter_by(engagement_id=eid).first(); tb.completed_by_id = None
    tb.reviewed_by_id = ids["partner"]; tb.reviewed_at = __import__("datetime").datetime.utcnow(); db.session.commit()
r = S.post(f"{T}/mappings/bulk", data={"mappings_text": "Wages\tAdministrative expenses\nRent;7777\nBad line only\nBank loan\tnot a category\nDebtors => Trade and other receivables\n"}, follow_redirects=True)
with app.app_context():
    names = {m.account_name: m for m in COAMapping.query.filter_by(client_id=ids["cl"]).all()}
    check(names["wages"].fs_category == "admin_expenses" and names["rent"].coa_account_number == "7777" and names["rent"].fs_category == "revenue" and names["debtors"].fs_category == "trade_receivables", "pasted mappings saved (tab, semicolon and => separators; number or category)")
    check("bank loan" not in names and "bad line only" not in names, "bad lines not saved")
check(b"Not saved:" in r.data and b"bad line only" in r.data.lower() or b"Bad line only" in r.data, "bad lines are reported")
r = O.post(f"{T}/mappings/{mid}/delete"); check(r.status_code == 403, "outsider cannot delete a mapping")
r = login(ids["partner"]).post(f"/engagements/{ids['eng2']}/trial-balance/mappings/{mid}/delete")
check(r.status_code in (403, 404), "a mapping of another client cannot be reached through another engagement: %s" % r.status_code)
r = S.post(f"{T}/mappings/{mid}/delete", follow_redirects=True)
with app.app_context():
    check(db.session.get(COAMapping, mid) is None and db.session.get(TrialBalanceLine, sundry).fs_category == "other_income", "delete removes the mapping, not the line's category")
h = S.get(f"/engagements/{eid}?tab=trial_balance").data.decode()
check("remembered mapping(s)" in h and "wages" in h, "the client's mappings are listed on the tab")

# ---------------------------------------------------------------- 3. paste from Excel
rows = "Code\tAccount name\tCurrent Dr\tCurrent Cr\tPrior Dr\tPrior Cr\n" \
       "10\tWages\t5,000.00\t\t4,000\t\n" \
       "11\tDebtors\t2,500\t0\t(100)\t\n" \
       "Motor expenses\t750.50\t\t600\t\n" \
       "12\tBroken\tabc\t\t\t\n" \
       "\t\t5\t\t\t\n"
before = 0
with app.app_context(): before = TrialBalanceLine.query.count()
r = S.post(f"{T}/lines/bulk-add", data={"rows_text": rows}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=eid).first()
    by = {l.account_name: l for l in tb.lines}
    check(TrialBalanceLine.query.count() == before + 3, "three good rows added (header, a bad number and a nameless row skipped)")
    check(by["Wages"].current_debit == 5000 and by["Wages"].prior_debit == 4000 and by["Wages"].account_code == "10", "amounts and code read")
    check(by["Wages"].fs_category == "admin_expenses" and by["Debtors"].fs_category == "trade_receivables", "rows the client's mappings recognise are mapped automatically")
    check(by["Debtors"].prior_debit == -100, "brackets are a negative")
    check(by["Motor expenses"].current_debit == 750.5 and by["Motor expenses"].prior_debit == 600 and by["Motor expenses"].account_code == "" and not by["Motor expenses"].fs_category, "a row with no code column is read, left unmapped")
    check(tb.reviewed_by_id is None, "adding accounts resets the review sign-off")
check(b"Skipped:" in r.data and b"Broken" in r.data, "skipped rows are explained")
r = S.post(f"{T}/lines/bulk-add", data={"rows_text": "  \n"}, follow_redirects=True); check(b"Nothing to add" in r.data, "empty paste is a polite no-op")
r = O.post(f"{T}/lines/bulk-add", data={"rows_text": "x\t1"}); check(r.status_code == 403, "outsider cannot paste into the trial balance")
# an account whose name is a word like 'Accounts payable' in the first row is not mistaken for a header
r = S.post(f"{T}/lines/bulk-add", data={"rows_text": "20\tAccounts payable\t0\t900\t0\t700"}, follow_redirects=True)
with app.app_context():
    check(TrialBalanceLine.query.filter_by(account_name="Accounts payable").count() == 1, "'Accounts payable' is a row, not a header")
# first-ever rows create the trial balance
with app.app_context():
    eng3 = Engagement(client_id=ids["cl"], title="Review 2026", type="Audit", partner_id=ids["partner"], manager_id=ids["staff"], acceptance_required=False)
    db.session.add(eng3); db.session.commit(); e3 = eng3.id
r = S.post(f"/engagements/{e3}/trial-balance/lines/bulk-add", data={"rows_text": "Wages\t10\t\t\t"}, follow_redirects=True)
with app.app_context():
    tb3 = TrialBalance.query.filter_by(engagement_id=e3).first()
    check(tb3 and len(tb3.lines) == 1 and tb3.lines[0].fs_category == "admin_expenses", "pasting into an engagement with no trial balance creates it, and the client's mappings apply across engagements")

# ---------------------------------------------------------------- existing routes carry the own category
csv2 = "Account Code,Account Name,Current Year Debit,Current Year Credit,Prior Year Debit,Prior Year Credit\n5,Staff Welfare,10,0,0,0\n"
S.post(f"/engagements/{e3}/trial-balance/upload", data={"file": (io.BytesIO(csv2.encode()), "tb.csv")}, content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    l = TrialBalance.query.filter_by(engagement_id=e3).first().lines[0]
    check(l.fs_category == "admin_expenses" and l.sub_category == "Staff costs", "an import maps by the remembered mapping and brings the own category")
    lid = l.id
S.post(f"/engagements/{e3}/trial-balance/lines/add", data={"account_name": "Staff welfare", "current_debit": "5"}, follow_redirects=True)
with app.app_context():
    l2 = [x for x in TrialBalance.query.filter_by(engagement_id=e3).first().lines if x.id != lid][0]
    check(l2.fs_category == "admin_expenses" and l2.sub_category == "Staff costs", "adding one account by hand with nothing chosen uses the remembered mapping")
S.post(f"/engagements/trial-balance/lines/{lid}/update", data={"account_name": "Staff Welfare", "fs_category": "admin_expenses", "coa_account_number": ""}, follow_redirects=True)
with app.app_context():
    check(db.session.get(TrialBalanceLine, lid).sub_category is None, "changing a line to a bare category (no Standard account) drops the own category")
S.post(f"/engagements/trial-balance/lines/{lid}/update", data={"account_name": "Staff Welfare", "coa_account_number": new_no}, follow_redirects=True)
with app.app_context():
    check(db.session.get(TrialBalanceLine, lid).sub_category == "Staff costs", "picking the Standard account brings it back")

# ---------------------------------------------------------------- statements are not changed by an own category
class L:  # a stand-in line
    def __init__(s, cat, d, c, sub=None): s.fs_category, s.current_debit, s.current_credit, s.prior_debit, s.prior_credit, s.sub_category = cat, d, c, 0, 0, sub
plain = fin.compute_totals([L("admin_expenses", 100, 0), L("admin_expenses", 50, 0)])
withsub = fin.compute_totals([L("admin_expenses", 100, 0, "People costs"), L("admin_expenses", 50, 0, "Other")])
check(plain == withsub, "financial statement totals are identical with or without an own category")
summ = fin.summarise_tb_by_category([L("admin_expenses", 100, 0, "People costs"), L("admin_expenses", 50, 0, "Other"), L("admin_expenses", 10, 0), L(None, 5, 0, "Ghost")])
g = [x for x in summ if x["code"] == "admin_expenses"][0]
check(g["count"] == 3 and [s["label"] for s in g["subs"]] == ["Other", "People costs"] and g["subs"][1]["current_debit"] == 100, "the summarised view groups own categories under their IAS 1 line")
check([x for x in summ if x["code"] == "__unmapped__"][0]["subs"] == [], "an unmapped line never shows an own category")
h = S.get(f"/engagements/{eid}?tab=trial_balance").data.decode()
check("&rsaquo; People costs" in h, "the summarised and detailed views render with own categories")

print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
