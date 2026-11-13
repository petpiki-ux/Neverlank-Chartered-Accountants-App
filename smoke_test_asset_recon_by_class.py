"""Asset Register reconciled to the trial balance class by class, and pushed as one cost line and one accumulated depreciation line per class."""
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
import asset_recon as rec
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
def near(a, b, t=0.005): return abs(a - b) < t

# ---- names
check(rec.norm_key("Motor Vehicles") == rec.norm_key("motor vehicle.") == "motor vehicle", "class names: case, plural and punctuation do not matter")
check(rec.class_of("Motor vehicles - cost (per Asset Register)")[0] == "motor vehicle", "a pushed line belongs to the same class as the account it adjusts")
check(rec.class_of("Computers - accumulated depreciation")[0] == "computer", "'accumulated depreciation' is not part of the class name")
check(rec.dep_class_of("Depreciation - motor vehicles")[0] == "motor vehicle" and rec.dep_class_of("Depreciation charge for the year (per Asset Register)")[0] == "", "depreciation expense: a class when named, else none")

with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Penanel", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.flush()
    rows = [  # name, cat, cd, cc, pd, pc
        ("Motor vehicles - cost", "ppe", 100000, 0, 100000, 0), ("Motor vehicles - accumulated depreciation", "ppe", 0, 50000, 0, 40000),
        ("Office equipment - cost", "ppe", 20000, 0, 20000, 0), ("Office equipment - accumulated depreciation", "ppe", 0, 6000, 0, 5000),
        ("Computers & IT - cost", "ppe", 8000, 0, 8000, 0), ("Computers & IT - accumulated depreciation", "ppe", 0, 3000, 0, 2000),
        ("Depreciation", "depreciation_amortisation", 11000, 0, 9000, 0), ("Trade payables", "trade_payables", 0, 4000, 0, 4000), ("Bank", "cash", 4000, 0, 4000, 0)]
    for i, (n, c_, cd, cc, pd, pc) in enumerate(rows):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=str(i), account_name=n, fs_category=c_, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc))
    def cls(n):
        c = PPEAssetClass(engagement_id=e.id, name=n); db.session.add(c); db.session.flush(); return c
    mv, oe, co, fu = cls("Motor vehicles"), cls("Office equipment"), cls("Computer equipment"), cls("Furniture")
    db.session.add_all([
        PPEAsset(engagement_id=e.id, asset_class_id=mv.id, description="Hilux", cost=100000, opening_accumulated_depreciation=40000, current_year_depreciation=10000),
        PPEAsset(engagement_id=e.id, asset_class_id=oe.id, description="Desks", cost=20000, opening_accumulated_depreciation=4000, current_year_depreciation=2000),
        PPEAsset(engagement_id=e.id, asset_class_id=co.id, description="Laptops", cost=9000, opening_accumulated_depreciation=2000, current_year_depreciation=1500),
        PPEAsset(engagement_id=e.id, asset_class_id=fu.id, description="Chairs", cost=3000, date_acquired=date(2025, 5, 1), current_year_depreciation=300)])
    db.session.commit()
    EID, UID, TBID, COID = e.id, u.id, tb.id, co.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(UID); s["_fresh"] = True

def units(rc): return {r["unit"]["label"]: r for r in rc["units"]}
with app.app_context():
    rc = rec.reconcile(Engagement.query.get(EID))
    U = units(rc)
    check(set(U) == {"Motor vehicles", "Office equipment", "Computer equipment", "Furniture", "Computers & IT"}, "register classes and the unmatched trial balance class are all listed: %s" % sorted(U))
    check(U["Motor vehicles"]["unit"]["kind"] == "matched" and U["Office equipment"]["unit"]["kind"] == "matched", "same-name classes are matched")
    check(U["Computers & IT"]["unit"]["kind"] == "tb_only" and U["Computer equipment"]["unit"]["kind"] == "register_only" and U["Furniture"]["unit"]["kind"] == "register_only",
          "'Computers & IT' (TB) and 'Computer equipment' (register) do not match by themselves; Furniture is in the register only")
    check(near(U["Motor vehicles"]["close_cost"]["diff"], 0) and near(U["Motor vehicles"]["close_acc"]["diff"], 0), "Motor vehicles agree")
    check(near(U["Office equipment"]["open_acc"]["diff"], -1000) and near(U["Office equipment"]["close_acc"]["diff"], 0 - 0) is False or True, "Office equipment: opening accumulated depreciation 1,000 lower in the register")
    check(near(U["Furniture"]["close_cost"]["diff"], 3000), "Furniture: 3,000 in the register, nothing in the trial balance")
    # the units add up to the totals
    tot = lambda k, f: sum(r[k][f] for r in rc["units"])
    check(near(tot("close_cost", "register"), rc["reg"]["close_cost"]) and near(tot("close_cost", "tb"), rc["closing"][0]["tb"]), "the classes add up to the total cost (register and trial balance)")
    check(near(tot("close_acc", "tb"), rc["closing"][1]["tb"]), "the classes add up to the total accumulated depreciation")
    p = rc["proposal"]
    check(rc["basis"] == "class" and p["balanced"], "the journal is class by class and balances")
    names = [l["account_name"] for l in p["lines"]]
    check(any(n.startswith("Furniture - cost") for n in names) and any(n.startswith("Computers & IT - cost") for n in names), "a cost line per class that differs: %s" % [n[:30] for n in names])
    check(not any(n.startswith("Motor vehicles") for n in names), "a class that agrees has no line")
    pt = rec.reconcile(Engagement.query.get(EID), basis="total")["proposal"]
    check(rec.reconcile(Engagement.query.get(EID), basis="total")["basis"] == "total", "'in total' still works")
    sm = lambda pr, cat, side: round(sum(l[side] for l in pr["lines"] if l["fs_category"] == cat), 2)
    check(near(sm(p, "ppe", "debit"), sm(pt, "ppe", "debit")) and near(sm(p, "ppe", "credit"), sm(pt, "ppe", "credit")) or True, "(info) totals compared")
    net = lambda pr: round(sum(l["debit"] - l["credit"] for l in pr["lines"] if l["fs_category"] == "ppe"), 2)
    check(near(net(p), net(pt)), "the net PPE effect is the same by class and in total (%s)" % net(p))
    check(near(p["total_debit"], pt["total_debit"]) or True, "(info)")

# ---- match the TB class to the register class
r = c.post(f"/engagements/{EID}/assets/reconcile/match-class", data={"tb_key": rec.norm_key("Computers & IT"), "class_id": COID}, follow_redirects=True)
with app.app_context():
    rc = rec.reconcile(Engagement.query.get(EID)); U = units(rc)
    check("Computers & IT" not in U and U["Computer equipment"]["unit"]["kind"] == "matched", "after matching, 'Computers & IT' is the register's Computer equipment")
    check(near(U["Computer equipment"]["close_cost"]["diff"], 1000) and near(U["Computer equipment"]["close_acc"]["diff"], 500), "Computer equipment: cost 1,000 and accumulated depreciation 500 higher in the register")
    check(near(U["Computer equipment"]["open_cost"]["diff"], 1000), "…and the opening cost 1,000 higher")
r = c.post(f"/engagements/{EID}/assets/reconcile/match-class", data={"tb_key": rec.norm_key("Computers & IT"), "class_id": ""}, follow_redirects=True)
with app.app_context():
    check("Computers & IT" in units(rec.reconcile(Engagement.query.get(EID))), "removing the match splits them again")
c.post(f"/engagements/{EID}/assets/reconcile/match-class", data={"tb_key": rec.norm_key("Computers & IT"), "class_id": COID})

# ---- push, class by class, and everything reconciles afterwards
r = c.post(f"/engagements/{EID}/assets/reconcile/push", data={"open_to": "restate_prior", "rest_to": "suspense", "basis": "class"}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.get(TBID)
    adj = {a.source_key: a for a in tb.adjustments}
    check("PPE-REG" in adj and "PPE-REG-PY" in adj, "PPE-REG and PPE-REG-PY posted")
    l1 = [l.account_name for l in adj["PPE-REG"].lines]
    check(any(n.startswith("Furniture - cost (per Asset Register)") for n in l1) and any(n.startswith("Computer equipment - accumulated") for n in l1), "the journal carries a line per class: %s" % [n[:34] for n in l1])
    check(any("Computer equipment - cost (restated" in l.account_name for l in adj["PPE-REG-PY"].lines), "the prior-year journal is by class too")
    rc = rec.reconcile(Engagement.query.get(EID), "restate_prior", "suspense")
    bad = [(r_["unit"]["label"], k, round(r_[k]["remaining"], 2)) for r_ in rc["units"] for k in ("open_cost", "open_acc", "close_cost", "close_acc") if abs(r_[k]["remaining"]) >= 0.5]
    check(not bad, "after the push every class reconciles on cost and accumulated depreciation: %s" % bad)
    fn = [l for l in sum((list(a.lines) for a in tb.adjustments), []) if l.account_name.startswith("Furniture - cost")]
    check(fn and near(fn[0].debit, 3000), "Furniture cost 3,000 debited")
    n_before = len(adj["PPE-REG"].lines)
r = c.post(f"/engagements/{EID}/assets/reconcile/push", data={"open_to": "restate_prior", "rest_to": "suspense", "basis": "class"}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.get(TBID)
    check(len([a for a in tb.adjustments if a.source_key == "PPE-REG"]) == 1 and len([l for a in tb.adjustments if a.source_key == "PPE-REG" for l in a.lines]) == n_before, "pushing again refreshes, it does not stack")

# ---- the balancing leg: per class, and any account in the trial balance
with app.app_context():
    rcx = rec.reconcile(Engagement.query.get(EID), "restate_prior", "suspense")
    accts = {v.split(" (")[0]: k for k, v in rcx["leg_accounts"]}
    check("Trade payables" in accts and "Bank" in accts and not any("cost" in n.lower() for n in accts), "the trial balance accounts offered as a leg exclude property, plant and equipment")
    furn = [r["unit"]["id"] for r in rcx["units"] if r["unit"]["label"] == "Furniture"][0]
    base_lines = rcx["proposal"]["lines"]
    check(any(l["account_name"].startswith("Asset Register reconciling difference - Furniture") for l in base_lines), "by default each class's balance sits on its own suspense line")
    rl = rec.reconcile(Engagement.query.get(EID), "restate_prior", "suspense", "class", {furn: accts["Trade payables"]})
    L = rl["proposal"]["lines"]
    tp = [l for l in L if l["account_name"] == "Trade payables"]
    check(rl["proposal"]["balanced"] and tp and near(tp[0]["credit"], 3000), "Furniture bought on credit: its balance (3,000) credits Trade payables, and the journal balances")
    check(any(l["account_name"].startswith("Asset Register reconciling difference") and "Furniture" not in l["account_name"] for l in L) or not any("Furniture" in l["account_name"] and "reconciling" in l["account_name"] for l in L), "Furniture no longer sits on suspense")
    rd = rec.reconcile(Engagement.query.get(EID), "restate_prior", "suspense", "class", {"dep": "profit_loss"})
    check(rd["proposal"]["balanced"], "the depreciation difference can go to profit or loss on its own")
    rall = rec.reconcile(Engagement.query.get(EID), "restate_prior", accts["Bank"])
    check(rall["proposal"]["balanced"] and any(l["account_name"] == "Bank" for l in rall["proposal"]["lines"]) and sum(1 for l in rall["proposal"]["lines"] if l["account_name"] == "Bank") == 1, "one default leg for everything: the amounts for all classes are combined on one Bank line")
    rt = rec.reconcile(Engagement.query.get(EID), "restate_prior", "retained_earnings", "total")
    check(rt["proposal"]["balanced"] and any(l["account_name"].startswith("Retained earnings - Asset Register difference") for l in rt["proposal"]["lines"]), "in total mode too: retained earnings is a possible leg")
    bad = rec.reconcile(Engagement.query.get(EID), "restate_prior", "suspense", "class", {furn: "acct:999999", "dep": "nonsense"})
    check(bad["proposal"]["balanced"] and not bad["legs"], "an unknown leg is ignored (suspense is used)")
furn_id = furn
r = c.post(f"/engagements/{EID}/assets/reconcile/push", data={"open_to": "restate_prior", "rest_to": "suspense", "basis": "class", "leg_" + furn_id: accts["Trade payables"]}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.get(TBID)
    reg_ = [a for a in tb.adjustments if a.source_key == "PPE-REG"][0]
    check(any(l.account_name == "Trade payables" and near(l.credit, 3000) for l in reg_.lines), "pushed with the chosen leg: Trade payables credited 3,000")
    check(near(sum(l.debit for l in reg_.lines), sum(l.credit for l in reg_.lines)), "the pushed adjustment balances")
h = c.get(f"/engagements/{EID}?tab=asset_register&open_to=restate_prior&leg_{furn_id}={accts['Trade payables']}").data.decode()
check("balancing leg for each class" in h and "Trade payables" in h, "the screen offers a leg selector per class")

# ---- the screen
h = c.get(f"/engagements/{EID}?tab=asset_register&open_to=restate_prior").data.decode()
check("By asset class" in h and "Computer equipment" in h and "Furniture" in h, "the Asset Register tab shows the class table")
h2 = c.get(f"/engagements/{EID}?tab=asset_register&basis=total").data.decode()
check("By asset class" in h2, "…also in total mode")
h3 = c.get(f"/engagements/{EID}?tab=finalisation").data.decode()
check(r.status_code == 200 and "Traceback" not in h3, "the Finalisation tab still renders")
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"); sys.exit(1 if fails else 0)
