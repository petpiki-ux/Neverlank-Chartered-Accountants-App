"""The Deferred Tax Computation picks up property, plant and equipment automatically: carrying amounts from the Asset Register,
tax bases from the Capital Allowance schedule, class by class (audit engagement: no Tax service needed)."""
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
import engagement_tax as et
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Penanel", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    EID, UID = e.id, u.id
    mv = PPEAssetClass(engagement_id=EID, name="Motor vehicles", order=1); fu = PPEAssetClass(engagement_id=EID, name="Furniture", order=2)
    db.session.add_all([mv, fu]); db.session.commit()
    # opening balances with a tax value b/f
    db.session.add(PPEAsset(engagement_id=EID, asset_class_id=mv.id, asset_code="V1", description="Truck", date_acquired=date(2022, 3, 1), cost=100000,
                            opening_accumulated_depreciation=40000, current_year_depreciation=10000, opening_tax_value=30000))
    db.session.add(PPEAsset(engagement_id=EID, asset_class_id=fu.id, asset_code="F1", description="Desks", date_acquired=date(2023, 3, 1), cost=20000,
                            opening_accumulated_depreciation=4000, current_year_depreciation=2000, opening_tax_value=8000))
    db.session.commit()
    check(e.has_asset_schedules and not e.has_tax_module, "an audit engagement with no Tax service has the asset tabs")
    ctx = et.capital_allowance_context(e)
    from engagements import _ppe_movement_and_policy
    import engagements as eng_mod
    classes = [{"id": c.id, "name": c.name} for c in PPEAssetClass.query.filter_by(engagement_id=EID).all()]
    assets = [{"asset_class_id": a.asset_class_id, "cost": a.cost, "opening_accumulated_depreciation": a.opening_accumulated_depreciation,
               "current_year_depreciation": a.current_year_depreciation, "disposal_cost": 0, "disposal_accumulated_depreciation": 0, "date_acquired": a.date_acquired}
              for a in PPEAsset.query.filter_by(engagement_id=EID).all()]
    mvt, _ = _ppe_movement_and_policy(classes, assets, e.period_end)
    got = et.deferred_tax_ppe(ctx, mvt, None)
    check(got["mode"] == "auto" and got["auto_available"] and len(got["rows"]) == 2, "with nothing saved the mode is automatic and there is a row for each class")
    r = {x["description"]: x for x in got["rows"]}
    check(abs(r["Property, plant and equipment - Motor vehicles"]["accounting_amount"] - 50000) < 0.01, "motor vehicles carrying amount 100,000 - 40,000 - 10,000 = 50,000 from the Asset Register")
    check(abs(r["Property, plant and equipment - Furniture"]["accounting_amount"] - 14000) < 0.01, "furniture carrying amount 14,000")
    tv_total = ctx["ca_result"]["tax_value_end"]
    check(abs(got["tax_base_total"] - tv_total) < 0.01 and tv_total > 0, "the tax bases add up to the Capital Allowance schedule's tax value (%.2f)" % tv_total)
    check(all(x["tax_base_amount"] > 0 for x in got["rows"]), "each class has its own tax base")
    check(abs(sum(x["tax_base_amount"] for x in got["rows"]) - tv_total) < 0.01, "...and they add up")
    # manual mode
    rec = DeferredTaxComputation(engagement_id=EID, tax_rate_percent=24.72, ppe_tax_base=12345.0)
    db.session.add(rec); db.session.commit()
    check(et.ppe_tax_base_mode(rec) == "manual", "an older record with a typed tax base stays manual")
    m = et.deferred_tax_ppe(ctx, mvt, rec)
    check(m["rows"] is None and m["tax_base_total"] == 12345.0, "manual: the typed figure is used")
    rec.ppe_tax_base_mode = "auto"; db.session.commit()
    a2 = et.deferred_tax_ppe(ctx, mvt, rec)
    check(a2["rows"] and abs(a2["tax_base_total"] - tv_total) < 0.01, "switched to automatic: the schedule is used again")

c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(UID); s["_fresh"] = True
h = c.get(f"/engagements/{EID}?tab=deferred_tax").data.decode()
check("Property, plant and equipment - Motor vehicles" in h and "Property, plant and equipment - Furniture" in h, "the tab shows a computation row for each class")
check("Pick the tax base up automatically" in h and "50,000.00" in h, "the tab offers automatic and typed, and carries the register's carrying amount")
with app.app_context():
    e = Engagement.query.get(EID)
    rate_row = DeferredTaxComputation.query.filter_by(engagement_id=EID).first()
    from financials import build_deferred_tax_computation
# saving with automatic chosen
r = c.post(f"/engagements/{EID}/deferred-tax/save", data={"tax_rate_percent": "24.72", "ppe_tax_base_mode": "auto", "ppe_tax_base": "", "notes": "n"}, follow_redirects=True)
with app.app_context():
    rec = DeferredTaxComputation.query.filter_by(engagement_id=EID).first()
    check(rec.ppe_tax_base_mode == "auto" and rec.tax_rate_percent == 24.72, "saving with automatic chosen is remembered")
h = c.get(f"/engagements/{EID}?tab=deferred_tax").data.decode()
with app.app_context():
    ctx = et.capital_allowance_context(Engagement.query.get(EID))
    tvb = ctx["ca_result"]["tax_value_end"]
import re
check("Net deferred tax" in h, "the computation total is shown")
c.post(f"/engagements/{EID}/deferred-tax/save", data={"tax_rate_percent": "24.72", "ppe_tax_base_mode": "manual", "ppe_tax_base": "999", "notes": "n"}, follow_redirects=True)
h = c.get(f"/engagements/{EID}?tab=deferred_tax").data.decode()
check("The typed tax base is used" in h and "999.00" in h, "choosing to type it uses the typed figure")

# the tab on an engagement with no assets
with app.app_context():
    e2 = Engagement(client_id=Client.query.first().id, title="Audit 2024", type="Audit", partner_id=UID, manager_id=UID, acceptance_required=False, period_end=date(2024, 12, 31))
    db.session.add(e2); db.session.commit(); E2 = e2.id
h2 = c.get(f"/engagements/{E2}?tab=deferred_tax").data.decode()
check("Nothing to pick up yet" in h2, "with no assets the tab says what is missing instead of failing")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
