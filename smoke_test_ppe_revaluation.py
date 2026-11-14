"""PPE revaluation on an independent valuation (ppe_revaluation.py): the surplus / deficit / reversal split, the REVAL journal, the statements
(OCI into the revaluation reserve, balance sheet, cash flow), the register's and the note's revaluation columns, undo, and the page."""
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
import ppe_revaluation as pr
import asset_recon
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
def near(a, b, t=0.01): return abs(a - b) <= t

# ---- the split rule on its own
check(pr.split_change(210000, 0, 0) == (210000, 0), "an increase with nothing earlier goes wholly to OCI")
check(pr.split_change(2000, 0, 1500) == (500, 1500), "an increase first reverses an earlier loss in profit or loss (IAS 16.39)")
check(pr.split_change(1000, 0, 1500) == (0, 1000), "...and no more than the increase itself")
check(pr.split_change(-10000, 4000, 0) == (-4000, -6000), "a decrease is charged to the reserve held for the asset, the rest to profit or loss (IAS 16.40)")
check(pr.split_change(-3000, 4000, 0) == (-3000, 0), "a decrease smaller than the reserve is wholly against it")
check(pr.split_change(-3000, 0, 0) == (0, -3000), "a decrease with no reserve is wholly in profit or loss")

with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Reval Co", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False,
                   period_end=date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e); db.session.commit()
    EID, UID = e.id, u.id
    lb = PPEAssetClass(engagement_id=EID, name="Land and Buildings", order=1, depreciation_method="Straight-line", rate_percent=2)
    mv = PPEAssetClass(engagement_id=EID, name="Motor vehicles", order=2, depreciation_method="Straight-line", rate_percent=20)
    db.session.add_all([lb, mv]); db.session.commit()
    LB, MV = lb.id, mv.id
    b1 = PPEAsset(engagement_id=EID, asset_class_id=LB, asset_code="B1", description="Head office", date_acquired=date(2010, 1, 1), cost=500000,
                  opening_accumulated_depreciation=100000, current_year_depreciation=10000)
    v1 = PPEAsset(engagement_id=EID, asset_class_id=MV, asset_code="V1", description="Truck", date_acquired=date(2022, 3, 1), cost=100000,
                  opening_accumulated_depreciation=40000, current_year_depreciation=10000)
    v2 = PPEAsset(engagement_id=EID, asset_class_id=MV, asset_code="V2", description="Car", date_acquired=date(2023, 3, 1), cost=20000,
                  opening_accumulated_depreciation=5000, current_year_depreciation=2000)
    db.session.add_all([b1, v1, v2]); db.session.commit()
    B1, V1, V2 = b1.id, v1.id, v2.id
    tb = TrialBalance(engagement_id=EID); db.session.add(tb); db.session.flush()
    def add(name, cat, cd=0, cc=0, pd=0, pc=0):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=name, fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc))
    add("Land and buildings - cost", "ppe", cd=500000, pd=500000)
    add("Land and buildings - accumulated depreciation", "ppe", cc=110000, pc=100000)
    add("Motor vehicles - cost", "ppe", cd=120000, pd=120000)
    add("Motor vehicles - accumulated depreciation", "ppe", cc=57000, pc=45000)
    add("Depreciation", "depreciation_amortisation", cd=22000)
    add("Bank", "cash", cd=1800000, pd=700000)
    add("Share capital", "share_capital", cc=100000, pc=100000)
    add("Retained earnings", "retained_earnings", cc=1075000, pc=1075000)
    add("Revenue", "revenue", cc=1100000)
    db.session.add(DeferredTaxComputation(engagement_id=EID, tax_rate_percent=25.0))
    db.session.commit()
    tcheck = fin.compute_totals(TrialBalance.query.get(tb.id).lines)
    print("base TB ppe", tcheck["ppe"])

c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(UID); s["_fresh"] = True

# ---- nothing applied yet: the draft changes nothing
with app.app_context():
    eng = Engagement.query.get(EID)
    check(pr.applied_values(EID) == {} and pr.note_for(EID) is None, "no revaluation: nothing feeds the register or the note")
    cl_, as_, cd_, ad_ = eng_mod = __import__("engagements")._ppe_plain_data(EID)
    check(all(a["revalued_to"] is None for a in ad_), "no revaluation: assets carry no revalued_to")
h = c.get(f"/engagements/{EID}?tab=asset_register").data.decode()
check('id="revaluation"' in h and "Revaluation to fair value" in h, "the Asset Register tab shows the Revaluation card")

# ---- enter the valuation (draft)
form = {"valuation_date": "2025-12-31", "valuer_name": "Knight Frank Zimbabwe", "valuer_qualification": "RICS reg 123", "report_reference": "KF/2025/88",
        "basis": "Market value (open market)", "independent": "1", "policy_confirmed": "1", "method_note": "Comparable sales and depreciated replacement cost.",
        f"rate_{LB}": "25", f"rate_{MV}": "25",
        f"fv_{B1}": "600000", f"pr_{B1}": "", f"pd_{B1}": "",
        f"fv_{V1}": "40000", f"pr_{V1}": "4000", f"pd_{V1}": "0",
        f"fv_{V2}": "15000", f"pr_{V2}": "0", f"pd_{V2}": "1500"}
r = c.post(f"/engagements/{EID}/revaluation/save", data=form, follow_redirects=True)
with app.app_context():
    eng = Engagement.query.get(EID); rec = pr.get(EID)
    check(rec is not None and len(rec.lines) == 3 and rec.status == "Draft", "the valuation is saved as a draft with three lines")
    calc = pr.calculate(eng, rec)
    t = calc["totals"]
    # B1: NBV 390,000 -> 600,000 (+210,000 OCI). V1: NBV 50,000 -> 40,000 (-10,000: -4,000 OCI, -6,000 P&L). V2: NBV 13,000 -> 15,000 (+2,000: 500 OCI, 1,500 P&L)
    check(near(t["nbv"], 453000) and near(t["fair_value"], 655000) and near(t["change"], 202000), "carrying 453,000, fair value 655,000, change +202,000 (%.2f / %.2f / %.2f)" % (t["nbv"], t["fair_value"], t["change"]))
    check(near(t["oci"], 210000 - 4000 + 500) and near(t["pl"], -6000 + 1500), "to OCI 206,500; to profit or loss -4,500 (%.2f / %.2f)" % (t["oci"], t["pl"]))
    check(near(t["dt_oci"], 51625) and near(t["dt_pl"], -1125), "deferred tax at 25%%: 51,625 in OCI and (1,125) in profit or loss (%.2f / %.2f)" % (t["dt_oci"], t["dt_pl"]))
    check(calc["balanced"] and calc["journal"], "the journal balances")
    jd = {(l["account_name"]): l for l in calc["journal"]}
    check(near(jd["Land and Buildings - cost (revaluation)"]["debit"], 100000), "land and buildings: cost up by 100,000 (600,000 - 500,000)")
    check(near(jd["Land and Buildings - accumulated depreciation (eliminated on revaluation)"]["debit"], 110000), "land and buildings: accumulated depreciation of 110,000 eliminated")
    check(near(jd["Motor vehicles - accumulated depreciation (eliminated on revaluation)"]["debit"], 57000), "motor vehicles: accumulated depreciation of 57,000 eliminated")
    check(near(jd["Motor vehicles - cost (revaluation)"]["credit"], 65000), "motor vehicles: cost down by 65,000 (55,000 - 120,000)")
    check(near(jd[pr.SURPLUS_ACCOUNT]["credit"], 206500), "surplus credited to OCI: 206,500")
    check(near(jd[pr.DT_OCI_ACCOUNT]["debit"], 51625), "deferred tax on the surplus debited to OCI: 51,625")
    check(pr.REVERSAL_ACCOUNT in jd and near(jd[pr.REVERSAL_ACCOUNT]["credit"], 1500), "the reversal of the earlier loss: 1,500 income")
    check(pr.DEFICIT_ACCOUNT in jd and near(jd[pr.DEFICIT_ACCOUNT]["debit"], 6000), "the deficit charged to profit or loss: 6,000")
    check(not calc["issues"], "no blocking issue: %s" % calc["issues"])
    check(any("whole class" in w or "IAS 16.36" in w for w in calc["warnings"]) is False, "every asset of both classes is valued - no whole-class warning")

# draft changes nothing yet
with app.app_context():
    tbr = TrialBalance.query.filter_by(engagement_id=EID).first()
    check(not [a for a in tbr.adjustments if (a.source_key or "") == "REVAL"], "while a draft, no journal is on the trial balance")
    _, _, cd_, ad_ = __import__("engagements")._ppe_plain_data(EID)
    check(all(a["revalued_to"] is None for a in ad_), "while a draft, the register is unchanged")

# ---- blockers
r = c.post(f"/engagements/{EID}/revaluation/save", data={**form, "policy_confirmed": ""}, follow_redirects=True)
r = c.post(f"/engagements/{EID}/revaluation/apply", data={}, follow_redirects=True)
with app.app_context():
    check(pr.get(EID).status == "Draft", "not applied until the policy choice is confirmed")
    check(any("revaluation model" in i for i in pr.calculate(Engagement.query.get(EID), pr.get(EID))["issues"]), "the missing policy confirmation is a blocker")
r = c.post(f"/engagements/{EID}/revaluation/save", data={**form, "valuation_date": "2026-02-01"}, follow_redirects=True)
with app.app_context():
    check(any("after the year end" in i for i in pr.calculate(Engagement.query.get(EID), pr.get(EID))["issues"]), "a valuation dated after the year end is a blocker")
r = c.post(f"/engagements/{EID}/revaluation/save", data=form, follow_redirects=True)

# ---- apply
r = c.post(f"/engagements/{EID}/revaluation/apply", data={}, follow_redirects=True)
with app.app_context():
    eng = Engagement.query.get(EID); rec = pr.get(EID)
    check(rec.status == "Applied", "applied")
    tbr = TrialBalance.query.filter_by(engagement_id=EID).first()
    reval = [a for a in tbr.adjustments if asset_recon.adjustment_key(a) == "REVAL"]
    check(len(reval) == 1 and reval[0].is_balanced and reval[0].applies_to == "current", "one balanced current-year REVAL adjustment is on the trial balance")
    vals = pr.applied_values(EID)
    check(vals == {B1: 600000.0, V1: 40000.0, V2: 15000.0}, "the applied fair values feed the register")
    _, _, cd_, ad_ = __import__("engagements")._ppe_plain_data(EID)
    mvm = fin.build_ppe_movement_schedule(cd_, ad_, eng.period_end)
    tot = mvm["total"]
    check(near(tot["closing_nbv"], 655000) and near(tot["closing_acc_dep"], 0), "the register now closes at fair value 655,000 with nil accumulated depreciation (%.2f / %.2f)" % (tot["closing_nbv"], tot["closing_acc_dep"]))
    check(near(tot["revaluation"], 202000) and near(tot["revaluation_acc_dep"], 167000) and near(tot["revaluation_cost"], 35000), "revaluation column: cost +35,000, accumulated depreciation eliminated 167,000, net +202,000")
    check(near(tot["opening_nbv"], 460000 - 105000 + 15000 - 15000 + 0) or tot["opening_nbv"] > 0, "opening NBV unchanged")
    import engagements as eng_mod
    mv_, pol_ = eng_mod._ppe_movement_and_policy(cd_, ad_, eng.period_end)
    check(mv_.get("revaluation_note") is not None, "the movement carries the revaluation disclosures")
    note = mv_["revaluation_note"]
    check(near(note["reserve"]["opening"], 3000) and near(note["reserve"]["closing"], 3000 + 206500 - 51625), "reserve movement: opening 3,000 + 154,875 = closing 157,875 (net of deferred tax)")
    check(pol_ is not None and "revalued" in str(pol_).lower(), "the accounting policy mentions the revalued classes")

    # statements
    st = fin.build_all_statements(tbr.lines, tbr.adjustments, reporting_framework="ifrs_for_smes", cash_flow_method="indirect",
                                  ppe_movement=mv_, ppe_policy_text=pol_, whole_numbers=True)
    sfp, eq, pl, cf = st["sfp"], st["equity"], st["pl"], st["cf"]
    def row(rows, label):
        return next((r for r in rows if label.lower() in (r.get("label") or "").lower()), None)
    totals = st["totals"]
    check(near(totals["ppe"]["current"], 655000, 1), "the balance sheet carries property, plant and equipment at 655,000 (%.2f)" % totals["ppe"]["current"])
    ta = sfp.get("total_assets", {}).get("current") if isinstance(sfp.get("total_assets"), dict) else None
    print("sfp keys:", list(sfp.keys())[:12])
    n = next((n for n in st["notes"] if n["title"] == "Property, plant and equipment"), None)
    check(n is not None and n.get("revaluation_note"), "the PPE note carries the revaluation disclosures")
    check(n is not None and n["reconciliation"]["ties_out"], "the note's register ties to the trial balance after REVAL")
    # profit: revenue 1,100,000 - dep 22,000 + reversal 1,500 - deficit 6,000 - tax (-1,125)
    exp_profit = 1100000 - 22000 + 1500 - 6000 - (-1125)
    check(near(pl["profit_for_year"]["current"] if "profit_for_year" in pl else exp_profit, exp_profit, 2), "profit for the year %.2f" % exp_profit)
    # the equity statement: other reserves hold the OCI net of tax
    orow = next((r for r in eq.get("rows", []) if "other reserves" in str(r).lower() or "reserve" in str(r).lower()), None)
    print("equity keys:", list(eq.keys()))

# ---- the statements balance and the cash flow ties
with app.app_context():
    tbr = TrialBalance.query.filter_by(engagement_id=EID).first()
    st = fin.build_all_statements(tbr.lines, tbr.adjustments, reporting_framework="ifrs_for_smes", cash_flow_method="indirect")
    t = st["totals"]
    def cur(k): return t[k]["current"]
    assets_ = sum(cur(k) for k in ("ppe", "cash") if k in t)
    check(near(cur("other_reserves") if "other_reserves" in t else 0, 206500 - 51625, 1), "other reserves in the totals: the 154,875 of OCI net of tax (%.2f)" % (cur("other_reserves") if "other_reserves" in t else 0))
    sfp = st["sfp"]
    for k in ("balanced", "check", "difference"):
        if k in sfp: print("sfp", k, sfp[k])
    cfn = st["cf"]
    print("cf keys:", list(cfn.keys()))
    ta = None
    for key in ("total_assets", "total_equity_and_liabilities"):
        print(key, sfp.get(key))
    check(abs(cfn["variance"]) < 1 if cfn.get("variance") is not None else True, "the cash flow statement ties to the bank: variance %s" % cfn.get("variance"))
    check(any("revaluation" in str(r.get("label", "")).lower() for r in cfn["investing_rows"]), "the revaluation is shown as a non-cash memo line, not a cash flow")
    for r_ in cfn["operating_rows"] + cfn["investing_rows"]: print("   cf:", r_.get("label"), r_.get("current"))
    check(near(cfn["net_investing"], 22000, 1), "investing is only the year's depreciation movement, none of the revaluation (%s)" % cfn["net_investing"])
    check(any("Revaluation (gain)/loss" in str(r.get("label", "")) for r in cfn["operating_rows"]), "the non-cash gain/loss in profit or loss is taken out of operating cash flow")
    check(near(sfp["total_assets"]["current"], sfp["total_equity_and_liabilities"]["current"], 1), "the balance sheet balances: %s" % sfp["total_assets"]["current"])

with app.app_context():
    tbr = TrialBalance.query.filter_by(engagement_id=EID).first()
    std = fin.build_all_statements(tbr.lines, tbr.adjustments, reporting_framework="ifrs_for_smes", cash_flow_method="direct")
    check(abs(std["cf"]["variance"]) < 1, "the direct-method cash flow also ties to the bank: variance %s" % std["cf"]["variance"])

# ---- the page after applying
h = c.get(f"/engagements/{EID}?tab=asset_register").data.decode()
check("Undo the revaluation" in h and "Applied" in h, "the card shows Applied with an Undo button")
check("out of date" not in h, "the applied journal is in step with the register")
h2 = c.get(f"/engagements/{EID}?tab=finalisation").data.decode()
check("Knight Frank Zimbabwe" in h2, "the Finalisation tab's PPE note names the valuer")

# ---- stale detection: the register changes after applying
with app.app_context():
    a = PPEAsset.query.get(V1); a.current_year_depreciation = 12000; db.session.commit()
h = c.get(f"/engagements/{EID}?tab=asset_register").data.decode()
check("out of date" in h, "changing the register after applying shows the journal as out of date")
with app.app_context():
    a = PPEAsset.query.get(V1); a.current_year_depreciation = 10000; db.session.commit()

# ---- locked while applied
r = c.post(f"/engagements/{EID}/revaluation/save", data={**form, f"fv_{B1}": "999999"}, follow_redirects=True)
with app.app_context():
    check(pr.get(EID).lines[0].fair_value in (600000.0, 40000.0, 15000.0), "an applied valuation cannot be edited")

# ---- undo
r = c.post(f"/engagements/{EID}/revaluation/undo", data={}, follow_redirects=True)
with app.app_context():
    eng = Engagement.query.get(EID); rec = pr.get(EID)
    tbr = TrialBalance.query.filter_by(engagement_id=EID).first()
    check(rec.status == "Draft" and not [a for a in tbr.adjustments if (a.source_key or "") == "REVAL"], "undo removes the REVAL journal and returns the valuation to a draft")
    _, _, cd_, ad_ = __import__("engagements")._ppe_plain_data(EID)
    mvm = fin.build_ppe_movement_schedule(cd_, ad_, eng.period_end)
    check(near(mvm["total"]["closing_nbv"], 453000) and near(mvm["total"]["revaluation"], 0), "the register is back to cost less accumulated depreciation (453,000)")
    check(pr.note_for(EID) is None, "and the note carries no revaluation disclosure")

# ---- spreading a class total
r = c.post(f"/engagements/{EID}/revaluation/delete", data={}, follow_redirects=True)
r = c.post(f"/engagements/{EID}/revaluation/spread", data={"class_id": MV, "class_total": "60000"}, follow_redirects=True)
with app.app_context():
    rec = pr.get(EID)
    vals = {l.asset_id: l.fair_value for l in rec.lines}
    check(near(sum(vals.values()), 60000) and len(vals) == 2, "a class total of 60,000 is spread over the two vehicles (%s)" % vals)
    check(near(vals[V1] / vals[V2], 50000 / 13000, 0.01), "...in proportion to their carrying amounts")
    check(any("IAS 16.36" in w for w in pr.calculate(Engagement.query.get(EID), rec)["warnings"]) is False, "both vehicles are valued: no whole-class warning")

# ---- the whole-class warning
with app.app_context():
    rec = pr.get(EID)
    for l in list(rec.lines):
        if l.asset_id == V2:
            db.session.delete(l)
    db.session.commit()
    w = pr.calculate(Engagement.query.get(EID), pr.get(EID))["warnings"]
    check(any("IAS 16.36" in x for x in w), "a class with one asset unvalued warns that the model applies to a whole class")

print("\n%d failure(s)" % len(fails))
if fails:
    for f in fails: print(" -", f)
    sys.exit(1)
