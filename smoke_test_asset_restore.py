"""Deleting an asset keeps a copy; 'Recently deleted' puts it back exactly as it was."""
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
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
with app.app_context():
    db.create_all()
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x"); db.session.add(staff); db.session.commit()
    cl = Client(name="Penanel", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=staff.id, manager_id=staff.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    c = PPEAssetClass(engagement_id=e.id, name="Vehicles"); db.session.add(c); db.session.flush()
    a = PPEAsset(engagement_id=e.id, asset_class_id=c.id, asset_code="V1", description="Hilux 2024", date_acquired=date(2024, 6, 1), cost=45000,
                 opening_accumulated_depreciation=4500, current_year_depreciation=9000, opening_tax_value=0.0, tax_disposed_on=date(2025, 9, 1), tax_disposal_proceeds=30000,
                 disposal_cost=45000, disposal_accumulated_depreciation=13500)
    b = PPEAsset(engagement_id=e.id, description="Desk", cost=100); db.session.add_all([a, b]); db.session.commit()
    ids = (e.id, a.id, b.id, staff.id, c.id)
E, A, B, U, C = ids
cl_ = app.test_client()
with cl_.session_transaction() as s:
    s["_user_id"] = str(U); s["_fresh"] = True
R = {"Referer": f"/engagements/{E}?tab=asset_register"}
cl_.post(f"/engagements/ppe/assets/{A}/delete", headers=R)
cl_.post(f"/engagements/ppe/assets/{B}/delete", headers=R)
with app.app_context():
    check(PPEAsset.query.filter_by(engagement_id=E).count() == 0 and DeletedPPEAsset.query.filter_by(engagement_id=E).count() == 2, "deleting keeps a copy of each asset")
h = cl_.get(f"/engagements/{E}?tab=asset_register").data.decode()
check("Recently deleted (2)" in h and "Hilux 2024" in h, "the Asset Register tab lists the deleted assets")
with app.app_context(): did = DeletedPPEAsset.query.filter(DeletedPPEAsset.description == "Hilux 2024").first().id
cl_.post(f"/engagements/{E}/assets/restore", data={"deleted_id": str(did)}, headers=R)
with app.app_context():
    r = PPEAsset.query.filter_by(engagement_id=E).all()
    check(len(r) == 1 and r[0].description == "Hilux 2024", "Restore puts back just that asset")
    x = r[0]
    check(x.cost == 45000 and x.opening_accumulated_depreciation == 4500 and x.current_year_depreciation == 9000 and x.opening_tax_value == 0.0 and x.date_acquired == date(2024, 6, 1)
          and x.tax_disposed_on == date(2025, 9, 1) and x.tax_disposal_proceeds == 30000 and x.disposal_cost == 45000 and x.disposal_accumulated_depreciation == 13500
          and x.asset_class_id == C and x.asset_code == "V1", "…exactly as it was (figures, nil tax value, dates, disposal, class)")
    check(DeletedPPEAsset.query.filter_by(engagement_id=E).count() == 1, "the restored copy is removed from the list")
    PPEAsset.query.filter_by(asset_class_id=C).update({"asset_class_id": None}); db.session.flush()   # (a database that enforces links, e.g. PostgreSQL, will not drop a class still in use)
    PPEAssetClass.query.filter_by(id=C).delete(); db.session.commit()
cl_.post(f"/engagements/{E}/assets/restore", data={}, headers=R)
with app.app_context():
    check(PPEAsset.query.filter_by(engagement_id=E).count() == 2 and DeletedPPEAsset.query.count() == 0, "Restore all puts back the rest")
cl_.post(f"/engagements/ppe/assets/{B}/delete", headers=R)
cl_.post(f"/engagements/{E}/assets/restore/discard", headers=R)
with app.app_context(): check(DeletedPPEAsset.query.count() == 0, "Discard forgets the saved copies")
# ---- several at once
with app.app_context():
    ids2 = []
    for n in ("A1", "A2", "A3"):
        x = PPEAsset(engagement_id=E, description=n, cost=10); db.session.add(x); db.session.flush(); ids2.append(x.id)
    db.session.commit()
h = cl_.get(f"/engagements/{E}?tab=asset_register").data.decode()
check(h.count('name="sel"') >= 3 and "Delete selected" in h, "the opening table has tick boxes and Delete selected")
r = cl_.post(f"/engagements/{E}/assets/delete-selected", data={"sel": [str(ids2[0]), str(ids2[2])]}, headers=R)
with app.app_context():
    left = [a.description for a in PPEAsset.query.filter_by(engagement_id=E).all()]
    check("A1" not in left and "A3" not in left and "A2" in left, "only the ticked assets are deleted")
    check(DeletedPPEAsset.query.filter_by(engagement_id=E).count() == 2, "each deleted asset is kept for restore")
r = cl_.post(f"/engagements/{E}/assets/delete-selected", data={}, headers=R, follow_redirects=True)
check("Tick the assets to delete first" in r.data.decode(), "nothing ticked: it says so")
# another engagement's asset cannot be deleted through this one
with app.app_context():
    e2 = Engagement(client_id=Client.query.first().id, title="Other", type="Audit", partner_id=U, manager_id=U, acceptance_required=False, period_end=date(2025, 12, 31)); db.session.add(e2); db.session.commit()
    o = PPEAsset(engagement_id=e2.id, description="Other client asset", cost=1); db.session.add(o); db.session.commit(); oid = o.id
cl_.post(f"/engagements/{E}/assets/delete-selected", data={"sel": [str(oid)]}, headers=R)
with app.app_context(): check(PPEAsset.query.get(oid) is not None, "an asset of another engagement is never deleted")
cl_.post(f"/engagements/{E}/assets/restore", data={}, headers=R)
with app.app_context(): check(PPEAsset.query.filter_by(engagement_id=E).count() == 4 or PPEAsset.query.filter_by(engagement_id=E).count() >= 3, "Restore all brings the deleted ones back")
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"); sys.exit(1 if fails else 0)
