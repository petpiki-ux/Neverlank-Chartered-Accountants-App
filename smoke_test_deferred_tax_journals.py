"""Deferred Tax tab: compare the computed deferred tax with the trial balance (prior and current year) and propose journals that are
confirmed on the page (DT-PY, DT-CUR)."""
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
import deferred_tax_adj as dta
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
def near(a, b, t=0.01): return abs(a - b) <= t

with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Penanel", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    EID, UID = e.id, u.id
    mv = PPEAssetClass(engagement_id=EID, name="Motor vehicles", order=1); db.session.add(mv); db.session.commit()
    db.session.add(PPEAsset(engagement_id=EID, asset_class_id=mv.id, asset_code="V1", description="Truck", date_acquired=date(2022, 3, 1), cost=100000,
                            opening_accumulated_depreciation=40000, current_year_depreciation=10000, opening_tax_value=30000))
    tb = TrialBalance(engagement_id=EID); db.session.add(tb); db.session.flush()
    def add(name, cat, cd=0, cc=0, pd=0, pc=0):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=name, fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc))
    add("Motor vehicles (net)", "ppe", cd=50000, pd=60000)
    add("Deferred tax liability", "deferred_tax_liability", cc=8000, pc=5000)
    add("Deferred tax expense", "income_tax_expense", cd=3000)
    add("Retained earnings brought forward", "retained_earnings", cc=0, pc=0)
    add("Bank", "cash", cd=30000, pd=0)
    db.session.add(DeferredTaxComputation(engagement_id=EID, tax_rate_percent=25.0))
    db.session.commit()

    eng = Engagement.query.get(EID)
    pos = dta.position(eng)
    check(pos["ok"], "the comparison is worked")
    C, P = pos["current"], pos["prior"]
    import engagement_tax as et
    ctx = et.capital_allowance_context(eng)
    tv_end, tv_open = ctx["ca_result"]["total"]["closing"], ctx["ca_result"]["total"]["opening"]
    want_cur = (50000 - tv_end) * 0.25
    want_pri = (60000 - tv_open) * 0.25
    check(near(C["computed"], want_cur), "current year: computed (50,000 - tax value %.2f) x 25%% = %.2f" % (tv_end, want_cur))
    check(P is not None and near(P["computed"], want_pri), "prior year: computed (60,000 - tax value b/f %.2f) x 25%% = %.2f" % (tv_open, want_pri))
    check(near(P["tb_net"], 5000) and near(P["diff"], want_pri - 5000), "prior year: the trial balance carries 5,000, difference %.2f" % P["diff"])
    check(P["lines"] and abs(sum(l["debit"] for l in P["lines"]) - sum(l["credit"] for l in P["lines"])) < 0.01, "the prior-year journal balances")
    check({l["fs_category"] for l in P["lines"]} == {"deferred_tax_liability", "retained_earnings"}, "prior year: deferred tax liability against retained earnings")
    check(any(l["account_name"] == "Retained earnings brought forward" for l in P["lines"]), "the trial balance's own account names are used")
    check(near(C["tb_net"], 8000 + P["diff"]), "current year: the trial balance position allows for the prior-year journal (carried into this year's opening): %.2f" % C["tb_net"])
    check(C["lines"] and {l["fs_category"] for l in C["lines"]} == {"deferred_tax_liability", "income_tax_expense"}, "current year: deferred tax liability against the deferred tax expense")
    check(P["existing"] is None and C["existing"] is None, "nothing is posted yet")

c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(UID); s["_fresh"] = True
h = c.get(f"/engagements/{EID}?tab=deferred_tax").data.decode()
check("Compare with the trial balance and propose journals" in h and "DT-PY" in h and "DT-CUR" in h and "Confirm and post" in h, "the Deferred Tax tab shows the comparison and the proposed journals")
r = c.post(f"/engagements/{EID}/deferred-tax/post-journals", data={"post": ["prior", "current"], "oci_amount": "0"}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=EID).first()
    refs = {a.source_key: a for a in tb.adjustments}
    check(set(refs) == {"DT-PY", "DT-CUR"}, "confirming posts the two draft adjustments")
    check(refs["DT-PY"].applies_to == "prior" and refs["DT-CUR"].applies_to == "current" and refs["DT-PY"].is_balanced and refs["DT-CUR"].is_balanced, "prior-year and current-year, both balanced")
    t = fin.apply_adjustments(fin.compute_totals(tb.lines), tb.adjustments)
    eng = Engagement.query.get(EID)
    pos = dta.position(eng)
    check(near(t["deferred_tax_liability"]["prior"], pos["prior"]["computed"]), "after posting, the prior-year trial balance carries the computed deferred tax (%.2f)" % t["deferred_tax_liability"]["prior"])
    check(near(t["deferred_tax_liability"]["current"] - t["deferred_tax_asset"]["current"], pos["current"]["computed"]), "...and so does the current year (%.2f)" % t["deferred_tax_liability"]["current"])
    check(pos["prior"]["in_sync"] and pos["current"]["in_sync"], "the page sees both journals as posted and up to date")
r = c.post(f"/engagements/{EID}/deferred-tax/post-journals", data={"post": ["prior", "current"], "oci_amount": "0"}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=EID).first()
    check(len(tb.adjustments) == 2, "confirming again refreshes the same two adjustments, never a second layer")

# the revaluation part to other comprehensive income
r = c.post(f"/engagements/{EID}/deferred-tax/post-journals", data={"post": ["current"], "oci_amount": "500"}, follow_redirects=True)
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=EID).first()
    cur = next(a for a in tb.adjustments if a.source_key == "DT-CUR")
    oci = [l for l in cur.lines if l.fs_category == "oci_items"]
    check(len(oci) == 1 and near(oci[0].credit, 500) and cur.is_balanced, "500 of the (downward) movement can be taken to other comprehensive income")

# a computed asset
with app.app_context():
    rec = DeferredTaxComputation.query.filter_by(engagement_id=EID).first()
    db.session.add(DeferredTaxItem(computation_id=rec.id, description="Provision for leave pay", accounting_amount=0, tax_base_amount=0, prior_accounting_amount=0, prior_tax_base_amount=0, order=0))
    db.session.add(DeferredTaxItem(computation_id=rec.id, description="Tax losses", accounting_amount=0, tax_base_amount=400000, order=1))
    db.session.commit()
    pos = dta.position(Engagement.query.get(EID))
    check(pos["current"]["computed"] < 0 and any(l["fs_category"] == "deferred_tax_asset" for l in pos["current"]["lines"]), "a net asset is journalled to the deferred tax asset")
    check(any("Tax losses" in n for n in pos["prior_notes"]), "an item with no prior-year figures is named as left out of the prior year")

# nothing to journal when they agree: build a TB that already carries the computed amounts
with app.app_context():
    for a in list(TrialBalance.query.first().adjustments):
        db.session.delete(a)
    for it in list(DeferredTaxItem.query.all()): db.session.delete(it)
    db.session.commit()
    pos = dta.position(Engagement.query.get(EID))
    tbl = TrialBalance.query.first()
    for l in tbl.lines:
        if l.fs_category == "deferred_tax_liability":
            l.current_credit, l.prior_credit = pos["current"]["computed"] - 0.0, pos["prior"]["computed"]
    db.session.commit()
    pos = dta.position(Engagement.query.get(EID))
    # the current-year journal still has to allow for the prior-year restatement unless it agrees too
    check(pos["prior"]["lines"] == [] and pos["current"]["lines"] == [], "when the trial balance already agrees, no journal is proposed")
h = c.get(f"/engagements/{EID}?tab=deferred_tax").data.decode()
check("agrees to the computation for both years" in h, "...and the page says so")
no_tb = Engagement(client_id=1, title="Audit 2024", type="Audit", partner_id=UID, manager_id=UID, acceptance_required=False, period_end=date(2024, 12, 31))
with app.app_context():
    db.session.add(no_tb); db.session.commit(); N = no_tb.id
h = c.get(f"/engagements/{N}?tab=deferred_tax").data.decode()
check(r.status_code == 200 and "Deferred Tax Computation" in h, "an engagement with no trial balance still opens the tab")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
