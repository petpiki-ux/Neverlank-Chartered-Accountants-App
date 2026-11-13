"""Asset Register reconciled to the trial balance (opening, closing, depreciation) and pushed to the trial balance as adjustment PPE-REG."""
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
import asset_recon as rec
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
def near(a, b, t=0.005): return abs(a - b) < t

# ---- pure helpers
check(rec._is_depreciation_expense("Depreciation - motor vehicles", "admin_expenses"), "a depreciation expense booked in administrative expenses is recognised")
check(not rec._is_depreciation_expense("Accumulated depreciation - vehicles", "admin_expenses"), "accumulated depreciation is not an expense")
check(not rec._is_depreciation_expense("Amortisation of software", "depreciation_amortisation"), "amortisation of intangibles is not depreciation of PPE")
check(rec._is_depreciation_expense("Depreciation & amortisation", "depreciation_amortisation"), "the depreciation category counts")
check(not rec._is_depreciation_expense("Salaries", "admin_expenses"), "an ordinary expense is not depreciation")

with app.app_context():
    db.create_all()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Penanel (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    def mk(title, pe, typ="Audit"):
        e = Engagement(client_id=cl.id, title=title, type=typ, partner_id=partner.id, manager_id=staff.id, acceptance_required=False, period_end=pe)
        db.session.add(e); db.session.commit(); return e
    e24, e25, sec = mk("Audit 2024", date(2024, 12, 31)), mk("Audit 2025", date(2025, 12, 31)), mk("Sec", date(2025, 12, 31), "Secretarial")
    def tbl(e, rows):
        tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.flush()
        for i, (name, cat, cd, cc, pd, pc) in enumerate(rows):
            db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=str(i), account_name=name, fs_category=cat,
                                            current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc))
        db.session.commit(); return tb
    # FY2024: closing = the 2025 opening
    tbl(e24, [("Motor vehicles - cost", "ppe", 100000, 0, 0, 0), ("Motor vehicles - accumulated depreciation", "ppe", 0, 40000, 0, 0),
              ("Office equipment - cost", "ppe", 20000, 0, 0, 0), ("Office equipment - accumulated depreciation", "ppe", 0, 5000, 0, 0),
              ("Depreciation", "depreciation_amortisation", 9000, 0, 0, 0)])
    # FY2025: no computer addition booked, office depreciation short
    tb25 = tbl(e25, [("Motor vehicles - cost", "ppe", 100000, 0, 100000, 0), ("Motor vehicles - accumulated depreciation", "ppe", 0, 50000, 0, 40000),
                     ("Office equipment - cost", "ppe", 20000, 0, 20000, 0), ("Office equipment - accumulated depreciation", "ppe", 0, 6000, 0, 5000),
                     ("Depreciation", "depreciation_amortisation", 11000, 0, 9000, 0)])
    # the register: office equipment opening depreciation is 4,000 (the TB says 5,000)
    def cls(e, n): 
        c = PPEAssetClass(engagement_id=e.id, name=n); db.session.add(c); db.session.flush(); return c
    mv, oe, co = cls(e25, "Motor vehicles"), cls(e25, "Office equipment"), cls(e25, "Computers")
    db.session.add_all([
        PPEAsset(engagement_id=e25.id, asset_class_id=mv.id, description="Hilux", cost=100000, opening_accumulated_depreciation=40000, current_year_depreciation=10000),
        PPEAsset(engagement_id=e25.id, asset_class_id=oe.id, description="Desks", cost=20000, opening_accumulated_depreciation=4000, current_year_depreciation=2000),
        PPEAsset(engagement_id=e25.id, asset_class_id=co.id, description="Laptops", cost=5000, date_acquired=date(2025, 3, 1), current_year_depreciation=1000)])
    db.session.commit()
    ids = dict(e24=e24.id, e25=e25.id, sec=sec.id, staff=staff.id, partner=partner.id)
S = login(ids["staff"]); P = login(ids["partner"]); E = ids["e25"]; U = f"/engagements/{E}"

with app.app_context():
    rc = rec.reconcile(Engagement.query.get(E))
    check(rc["ok"], "the reconciliation can be worked")
    o = {r["label"]: r for r in rc["opening"]}
    check(near(o["Cost brought forward"]["diff"], 0) and near(o["Accumulated depreciation brought forward"]["diff"], -1000), "opening cost agrees; opening accumulated depreciation is 1,000 lower in the register")
    check(near(o["Net book value brought forward"]["diff"], 1000), "opening net book value differs by 1,000")
    check(rc["open_differs"], "the opening difference is flagged")
    check(rc["continuity"] is not None and rc["continuity"].id == ids["e24"], "last year's engagement for the same client is found")
    check(near(o["Cost brought forward"]["cont_diff"], 0) and near(o["Accumulated depreciation brought forward"]["cont_diff"], 0), "the comparatives agree to last year's closing trial balance")
    c = {r["label"]: r for r in rc["closing"]}
    check(near(c["Cost carried forward"]["register"], 125000) and near(c["Cost carried forward"]["tb"], 120000) and near(c["Cost carried forward"]["diff"], 5000), "closing cost: register 125,000 vs trial balance 120,000")
    check(near(c["Accumulated depreciation carried forward"]["register"], 57000) and near(c["Accumulated depreciation carried forward"]["diff"], 1000), "closing accumulated depreciation: register 57,000, trial balance 56,000")
    check(near(rc["depreciation"]["register"], 13000) and near(rc["depreciation"]["tb"], 11000), "depreciation charge: register 13,000, trial balance 11,000")
    check(near(rc["info"][0]["value"], 9000), "the prior-year charge is shown for information")
    p = rc["proposal"]
    check(p["balanced"] and len(p["lines"]) == 5, "the proposed journal balances and has five lines: %s" % [(l["account_name"][:20], l["debit"], l["credit"]) for l in p["lines"]])
    by = {l["fs_category"] + ("_c" if l["credit"] else "_d"): l for l in p["lines"]}
    check(near(by["ppe_d"]["debit"], 5000) and near(by["ppe_c"]["credit"], 1000), "debit cost 5,000; credit accumulated depreciation 1,000")
    check(near(by["depreciation_amortisation_d"]["debit"], 2000), "debit the depreciation expense 2,000")
    check(near(by["retained_earnings_c"]["credit"], 1000), "the opening difference (1,000) goes to retained earnings")
    check(near(by["other_current_liabilities_c"]["credit"], 5000), "the balance (5,000, the other side of the addition) is left in suspense")
    rc2 = rec.reconcile(Engagement.query.get(E), "profit_loss", "profit_loss")
    cats = sorted(l["fs_category"] for l in rc2["proposal"]["lines"])
    check(rc2["proposal"]["balanced"] and "retained_earnings" not in cats and "other_current_liabilities" not in cats and cats.count("other_income") == 2, "the other treatment sends both balancing lines through profit or loss: %s" % cats)

h = S.get(f"{U}?tab=asset_register").data.decode()
check("5. Reconcile to the trial balance" in h and "Push to the trial balance (PPE-REG)" in h and "125,000.00" in h, "the card is on the Asset Register tab with the preview")
check("Audit 2024" in h and "closing" in h, "the card names last year's engagement")
h2 = S.get(f"{U}?tab=asset_register&open_to=profit_loss&rest_to=profit_loss").data.decode()
check("Opening net book value difference on property, plant and equipment" in h2, "the preview follows the chosen treatment")
r = S.post(f"/engagements/{ids['sec']}/assets/reconcile/push"); check(r.status_code == 404, "a Secretarial engagement has no push")

# ---- push
r = S.post(f"{U}/assets/reconcile/push", data={"open_to": "retained_earnings", "rest_to": "suspense"}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=E).first()
    adjs = [a for a in tb.adjustments if a.source_key == "PPE-REG"]
    check(len(adjs) == 1 and len(adjs[0].lines) == 5 and adjs[0].is_balanced, "adjustment PPE-REG posted: five lines, balanced")
    check(adjs[0].completed_by_id == ids["staff"] and not adjs[0].is_reviewed and not adjs[0].is_partner_signed, "prepared by the pusher; not reviewed or signed")
    tot = fin.apply_adjustments(fin.compute_totals(tb.lines), tb.adjustments)
    check(near(tot["ppe"]["current"], 68000), "the adjusted trial balance now carries property, plant and equipment at the register's net book value (68,000)")
    check(near(tot["depreciation_amortisation"]["current"], 13000), "and the depreciation charge of 13,000")
    rc = rec.reconcile(Engagement.query.get(E))
    check(all(abs(r_["remaining"]) < 0.005 for r_ in rc["closing"]) and abs(rc["depreciation"]["remaining"]) < 0.005 and not rc["unreconciled"], "nothing is left unreconciled after the push")
    check(rc["existing"] is not None and rc["proposal"]["balanced"], "the preview is still worked from the trial balance before PPE-REG")
    st = fin.build_all_statements(tb.lines, tb.adjustments)
    check(st["sfp"] is not None, "the statements build with the adjustment")
h = S.get(f"{U}?tab=asset_register").data.decode()
check("Refresh the adjustment(s) (PPE-REG)" in h, "the button now offers a refresh")

# ---- push again: one adjustment, sign-offs cleared
with app.app_context():
    a = AuditAdjustment.query.filter_by(source_key="PPE-REG").first()
    a.reviewed_by_id = ids["partner"]; a.partner_signed_by_id = ids["partner"]; db.session.commit()
S.post(f"{U}/assets/reconcile/push", data={"open_to": "retained_earnings", "rest_to": "suspense"})
with app.app_context():
    adjs = AuditAdjustment.query.filter_by(source_key="PPE-REG").all()
    check(len(adjs) == 1 and len(adjs[0].lines) == 5, "pushing again refreshes the one adjustment, it does not stack another")
    check(not adjs[0].is_reviewed and not adjs[0].is_partner_signed, "a refresh clears the review and partner sign-off")

# ---- the register changes: a disposal, then refresh
with app.app_context():
    a = PPEAsset.query.filter_by(engagement_id=E, description="Desks").first()
    a.disposal_cost = 20000; a.disposal_accumulated_depreciation = 6000; a.tax_disposed_on = date(2025, 9, 1); a.tax_disposal_proceeds = 15000; db.session.commit()
    rc = rec.reconcile(Engagement.query.get(E))
    check(near(rc["reg"]["close_cost"], 105000) and near(rc["reg"]["close_acc"], 51000), "after a disposal the register carries 105,000 cost and 51,000 depreciation")
    check(any("disposal" in i["label"].lower() for i in rc["info"]) and near(rc["info"][-1]["value"], 1000), "the profit on disposal (15,000 - 14,000 = 1,000) is shown")
    check(rc["proposal"]["balanced"], "the refreshed journal still balances")
    check(rc["existing"].is_balanced, "the existing one is still the old one until refreshed")
    check(rc["unreconciled"], "the disposal is flagged as unreconciled until the adjustment is refreshed")
S.post(f"{U}/assets/reconcile/push", data={})
with app.app_context():
    rc = rec.reconcile(Engagement.query.get(E))
    check(not rc["unreconciled"], "refreshing brings the adjusted trial balance back to the register")

# ---- the Excel export carries the reconciliation
import io as _io
import openpyxl
r = S.post(f"{U}/assets/schedules/generate", follow_redirects=True)
with app.app_context():
    import workpapers as wp, asset_schedules as asched
    from engagement_tax import capital_allowance_context
    e = Engagement.query.get(E)
    with app.test_request_context(f"/engagements/{E}"):
        ctx = asched.tab_context(e); ca = capital_allowance_context(e)
        buf = wp.build_asset_schedules_xlsx(e, ctx, ca)
    wbk = openpyxl.load_workbook(_io.BytesIO(buf.getvalue()))
    check("Trial balance reconciliation" in wbk.sheetnames, "the workbook has a Trial balance reconciliation sheet")
    txt = " ".join(str(c.value) for row in wbk["Trial balance reconciliation"].iter_rows() for c in row if c.value is not None)
    check("Cost carried forward" in txt and "Proposed adjustment PPE-REG" in txt, "the sheet carries the comparison and the proposed adjustment")

# ---- the client corrects the TB: no difference left -> the adjustment goes
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=E).first()
    for l in tb.lines:
        if l.account_name == "Office equipment - cost": l.current_debit = 0
        if l.account_name == "Office equipment - accumulated depreciation": l.current_credit = 0
        if l.account_name == "Motor vehicles - accumulated depreciation": l.current_credit = 50000
        if l.account_name == "Depreciation": l.current_debit = 13000
    db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code="x", account_name="Computers - cost", fs_category="ppe", current_debit=5000, current_credit=0, prior_debit=0, prior_credit=0))
    db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code="y", account_name="Computers - accumulated depreciation", fs_category="ppe", current_debit=0, current_credit=1000, prior_debit=0, prior_credit=0))
    db.session.commit()
    rc = rec.reconcile(Engagement.query.get(E))
    check(rc["proposal"]["lines"] == [] and not rc["unreconciled"] or rc["existing"] is not None, "(recomputed)")
    check(rc["proposal"]["lines"] == [], "once the client's trial balance agrees to the register nothing is proposed (an opening difference alone posts nothing)")
    check(rc["open_differs"], "the opening difference is still shown as a matter to look at")
S.post(f"{U}/assets/reconcile/push", data={})
with app.app_context():
    check(AuditAdjustment.query.filter_by(source_key="PPE-REG").count() == 0, "pushing when everything agrees removes the PPE-REG adjustment that is no longer needed")

# ---- missing pieces
with app.app_context():
    e3 = Engagement(client_id=Client.query.first().id, title="No TB", type="Audit", partner_id=ids["partner"], manager_id=ids["staff"], acceptance_required=False, period_end=date(2026, 12, 31))
    db.session.add(e3); db.session.commit(); e3id = e3.id
    check(not rec.reconcile(Engagement.query.get(e3id))["ok"], "no register: the card says what is missing")
    db.session.add(PPEAsset(engagement_id=e3id, description="X", cost=1000)); db.session.commit()
    rr = rec.reconcile(Engagement.query.get(e3id))
    check(not rr["ok"] and "trial balance" in rr["reason"], "a register but no trial balance: the card says so")
h = S.get(f"/engagements/{e3id}?tab=asset_register").data.decode()
check("import or enter it on the Trial Balance tab" in h, "the reason is shown on the tab")
r = S.post(f"/engagements/{e3id}/assets/reconcile/push", follow_redirects=True)
check("trial balance" in r.data.decode().lower(), "pushing without a trial balance is refused politely")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
