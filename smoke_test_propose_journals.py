"""Finalisation tab: 'Propose journals' beside the PPE note's tie-out puts cost and accumulated depreciation right in both years."""
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
import financials as fin
import engagements as E_
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
def near(a, b, t=0.01): return abs(a - b) < t
with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Penanel", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.flush()
    # no property, plant and equipment in the current year at all; 799,810 (net, one line) in the comparatives
    for i, (n, c, cd, cc, pd, pc) in enumerate([
        ("Property, plant and equipment", "ppe", 0, 0, 799810, 0), ("Cash", "cash", 899810, 0, 100000, 0),
        ("Share capital", "share_capital", 0, 1000, 0, 1000), ("Retained earnings", "retained_earnings", 0, 898810, 0, 898810)]):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=str(i), account_name=n, fs_category=c, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc))
    cls = PPEAssetClass(engagement_id=e.id, name="Plant"); db.session.add(cls); db.session.flush()
    db.session.add_all([
        PPEAsset(engagement_id=e.id, asset_class_id=cls.id, description="Plant 2024", cost=1000000, opening_accumulated_depreciation=130487, current_year_depreciation=100000),
        PPEAsset(engagement_id=e.id, asset_class_id=cls.id, description="Plant 2025", cost=600000, date_acquired=date(2025, 3, 1), current_year_depreciation=56506)])
    db.session.commit()
    EID, TB, U = e.id, tb.id, u.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(U); s["_fresh"] = True
URL = f"/engagements/{EID}?tab=finalisation"
h = c.get(URL).data.decode()
check("Per trial balance: 0 (current), 799810 (prior)" in h.replace(",", "") or "Per trial balance: 0 (current), 799,810 (prior)" in h, "the tie-out shows the difference the user saw")
check("Propose journals" in h and "PPE-REG-PY" in h, "'Propose journals' is offered beside it, with the prior-year journal")
r = c.post(f"/engagements/{EID}/assets/reconcile/push", data={"open_to": "restate_prior", "rest_to": "suspense"}, headers={"Referer": URL})
check(r.status_code == 302 and "tab=finalisation" in r.headers["Location"], "it returns to the Finalisation tab")
with app.app_context():
    tbx = TrialBalance.query.get(TB)
    refs = {a.source_key: a for a in tbx.adjustments}
    check(set(refs) == {"PPE-REG-PY", "PPE-REG"} and all(a.is_balanced for a in refs.values()), "two balanced draft journals are posted")
    check(refs["PPE-REG-PY"].is_prior_year and not refs["PPE-REG"].is_prior_year, "one restates 2024, the other is 2025")
    py = {l.account_name: l for l in refs["PPE-REG-PY"].lines}
    cost = [l for n, l in py.items() if "cost" in n][0]; acc = [l for n, l in py.items() if "accumulated" in n][0]
    check(near(cost.debit, 200190) and near(acc.credit, 130487), "2024: cost taken up to 1,000,000 (+200,190) and accumulated depreciation to 130,487")
    _, _, cd, ad = E_._ppe_plain_data(EID)
    pm, pt = E_._ppe_movement_and_policy(cd, ad, date(2025, 12, 31))
    st = fin.build_all_statements(tbx.lines, tbx.adjustments, ppe_movement=pm, ppe_policy_text=pt, whole_numbers=True)
    t = st["totals"]
    check(near(t["ppe"]["prior"], 869513) and near(t["ppe"]["current"], 1313007), "final property, plant and equipment: 869,513 (2024) and 1,313,007 (2025) = the register")
    check(abs(st["sfp"]["balance_check"]["prior"]) < 0.5 and abs(st["sfp"]["balance_check"]["current"]) < 0.5, "both years' statements of financial position balance")
    note = [n for n in st["notes"] if n.get("reconciliation")]
    check(note and note[0]["reconciliation"]["ties_out"], "the PPE note now ties out to the trial balance")
    check(abs(st["equity"]["rows"][-1]["opening_variance"]) < 0.5, "2025 opening retained earnings agree to 2024 closing")
h = c.get(URL).data.decode()
check("Propose journals" not in h, "once it ties out the offer disappears")
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"); sys.exit(1 if fails else 0)
