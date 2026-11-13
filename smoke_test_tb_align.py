"""Align accounts uploaded in different years: the same account spelt differently on two lines becomes one line, only when ticked."""
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
import tb_align as ta
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

check(ta.name_key("Land & Buildings Accumulated Depreciation") == ta.name_key("Land and Buildings - Accumulated Depreciation"), "spelling: & / and and a dash are ignored")
check(ta.name_key("Furniture & Fittings Acc. Depn") == ta.name_key("Furniture and Fittings - Accumulated Depreciation"), "spelling: acc / depn are spelt out")

with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Penanel", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.commit()
    def add(name, cat, cd=0, cc=0, pd=0, pc=0, code=""):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=code, account_name=name, fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc))
    add("Land and Buildings - Accumulated Depreciation", "ppe", pc=408.17)
    add("Land & Buildings Accumulated Depreciation", "ppe", cc=764.42)
    add("Furniture & Fittings - Accumulated Depreciation", "ppe", pc=86619.73, code="1030")
    add("Furniture & Fittings Accumulated Depreciation", "ppe", cc=97699.9)
    add("Ecobank ZWL - 2778", "cash", pd=560061)
    add("Ecobank GRA ZWL- 5754900002778", "cash", cd=389026.71)
    add("Banc ABC ZWL - 2015", "short_term_borrowings", pc=34882)
    add("BancAbc GRA ZWL-11669515502015", "cash", cc=68418.24)          # a different category: offered but not ticked
    add("Motor Vehicles", "ppe", cd=1115906, pd=843406)                  # has both years: left alone
    add("Brand new loan", "short_term_borrowings", cc=150000)           # no partner
    add("Old supplier", "trade_payables", pc=500)                       # no partner
    db.session.commit()
    adj = AuditAdjustment(trial_balance_id=tb.id, reference="AJE 1", description="x")
    adj.lines.append(AuditAdjustmentLine(account_name="Land and Buildings - Accumulated Depreciation", fs_category="ppe", debit=0, credit=10))
    db.session.add(adj); db.session.commit()
    EID, UID = e.id, u.id
    pairs = ta.candidates(tb)
    got = {(p["cur"].account_name, p["pri"].account_name): p for p in pairs}
    check(len(pairs) == 4, "four pairs are found: two by name, two by account number (%d)" % len(pairs))
    check(got[("Land & Buildings Accumulated Depreciation", "Land and Buildings - Accumulated Depreciation")]["strength"] == "strong", "Land and Buildings accumulated depreciation pair found")
    check(("Ecobank GRA ZWL- 5754900002778", "Ecobank ZWL - 2778") in got and "2778" in got[("Ecobank GRA ZWL- 5754900002778", "Ecobank ZWL - 2778")]["why"], "the bank account is matched by its number")
    banc = got[("BancAbc GRA ZWL-11669515502015", "Banc ABC ZWL - 2015")]
    check(banc["strength"] == "check" and banc["warn"], "a pair whose categories differ is flagged, not trusted")
    check(not any("Motor" in p["cur"].account_name or "Brand new" in p["cur"].account_name or "Old supplier" in p["pri"].account_name for p in pairs), "lines that have both years, or no partner, are left alone")

c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(UID); s["_fresh"] = True
r = c.get(f"/engagements/{EID}/trial-balance/align")
h = r.data.decode()
check(r.status_code == 200 and "Land &amp; Buildings Accumulated Depreciation" in h and "Merge the ticked pairs" in h, "the align page lists the pairs")
check(h.count('name="pair"') == 4 and h.count('name="pair" value') == 4 and h.count(" checked>") == 3, "the sure pairs are ticked, the category-clash one is not (%d)" % h.count(" checked>"))
# nothing ticked: nothing merged
c.post(f"/engagements/{EID}/trial-balance/align", data={}, follow_redirects=True)
with app.app_context():
    check(TrialBalanceLine.query.count() == 11, "with nothing ticked nothing is merged")
    tb = TrialBalance.query.first()
    keys = {p["cur"].account_name: p["key"] for p in ta.candidates(tb)}
    k1 = keys["Land & Buildings Accumulated Depreciation"]; k2 = keys["Furniture & Fittings Accumulated Depreciation"]; k3 = keys["Ecobank GRA ZWL- 5754900002778"]
c.post(f"/engagements/{EID}/trial-balance/align", data={"pair": [k1, k2, k3], f"keep_{k2}": "pri"}, follow_redirects=True)
with app.app_context():
    L = {l.account_name: l for l in TrialBalanceLine.query.all()}
    check(len(L) == 8, "three pairs merged: 11 lines become 8 (%d)" % len(L))
    lb = L["Land & Buildings Accumulated Depreciation"]
    check(lb.current_credit == 764.42 and lb.prior_credit == 408.17, "Land and Buildings: 764.42 current and 408.17 prior on one line")
    ff = L["Furniture & Fittings - Accumulated Depreciation"]
    check(ff.prior_credit == 86619.73 and ff.current_credit == 97699.9 and ff.account_code == "1030", "keeping the prior name keeps its code and takes the current figure")
    eb = L["Ecobank GRA ZWL- 5754900002778"]
    check(eb.current_debit == 389026.71 and eb.prior_debit == 560061, "the bank account carries both years")
    check("Banc ABC ZWL - 2015" in L and "BancAbc GRA ZWL-11669515502015" in L, "the unticked pair was left as it was")
    al = AuditAdjustmentLine.query.first()
    check(al.account_name == "Land & Buildings Accumulated Depreciation", "an adjustment naming the removed line now names the kept one")

# an upload of one year matches a line spelt differently instead of adding a second one
import tb_year_import as ty
with app.app_context():
    tb = TrialBalance.query.first()
    s = ty.merge_year(tb, [{"code": "", "name": "Land and Buildings Accumulated Depreciation", "debit": 0.0, "credit": 500.0}], "prior", cl.id if False else tb.engagement.client_id, lambda *_: None, {})
    db.session.flush()
    names = [l.account_name for l in tb.lines if "Land" in l.account_name]
    check(len(names) == 1 and s["added"] == 0, "re-uploading a year onto a differently spelt account does not add a second line (%s)" % names)
    db.session.rollback()

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
