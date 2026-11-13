"""'Premises' and 'Land & Buildings' are one asset class, Land and Buildings: the name rule, the start-up merge, and the add-class page."""
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
import asset_schedules as AS
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

for n in ("Premises", " premises", "Land & Buildings", "land and buildings", "LAND/BUILDINGS", "Land  and  Building", "Land, Buildings"):
    check(AS.canonical_class_name(n) == "Land and Buildings", f"'{n}' is Land and Buildings")
for n in ("Motor vehicles", "Buildings", "Land", "Plant & Equipment", "Furniture  and Fittings"):
    check(AS.canonical_class_name(n) == " ".join(n.split()), f"'{n}' is left as typed")
check(AS.canonical_class_name(None) == "" and AS.canonical_class_name("  ") == "", "a blank name stays blank")

with app.app_context():
    cl = Client(name="C"); db.session.add(cl); db.session.commit()
    e1 = Engagement(client_id=cl.id, title="A", type="Audit", period_end=date(2025, 12, 31)); e2 = Engagement(client_id=cl.id, title="B", type="Audit", period_end=date(2025, 12, 31))
    db.session.add_all([e1, e2]); db.session.commit()
    # engagement 1: only 'Premises'   -> renamed.   engagement 2: both 'Premises' and 'Land and Buildings' -> merged.
    p1 = PPEAssetClass(engagement_id=e1.id, name="Premises", rate_percent=2.5)
    p2 = PPEAssetClass(engagement_id=e2.id, name="Premises", rate_percent=5.0, tb_aliases="premises")
    lb = PPEAssetClass(engagement_id=e2.id, name="Land and Buildings", tb_aliases="land and buildings")
    mv = PPEAssetClass(engagement_id=e2.id, name="Motor vehicles")
    db.session.add_all([p1, p2, lb, mv]); db.session.commit()
    a1 = PPEAsset(engagement_id=e1.id, asset_class_id=p1.id, description="Office", cost=100)
    a2 = PPEAsset(engagement_id=e2.id, asset_class_id=p2.id, description="Factory", cost=200)
    a3 = PPEAsset(engagement_id=e2.id, asset_class_id=lb.id, description="Land", cost=300)
    a4 = PPEAsset(engagement_id=e2.id, asset_class_id=mv.id, description="Truck", cost=50)
    db.session.add_all([a1, a2, a3, a4]); db.session.commit()
    ids = dict(e1=e1.id, e2=e2.id, p1=p1.id, p2=p2.id, lb=lb.id, a2=a2.id)
    rule = EngagementTaxAssetRule(engagement_id=e2.id, asset_class_id=p2.id, method="WT_SL", wt_rate_percent=5.0)
    db.session.add(rule); db.session.commit()

    n = AS.normalise_class_names()
    check(n == 2, "two classes were renamed or merged")
    c1 = db.session.get(PPEAssetClass, ids["p1"])
    check(c1.name == "Land and Buildings" and c1.rate_percent == 2.5, "a lone 'Premises' is renamed, keeping its rate")
    check(db.session.get(PPEAssetClass, ids["p2"]) is None, "the duplicate 'Premises' class is gone")
    kept = db.session.get(PPEAssetClass, ids["lb"])
    check(db.session.get(PPEAsset, ids["a2"]).asset_class_id == kept.id, "its assets moved into the existing Land and Buildings")
    check(kept.rate_percent == 5.0, "the missing rate was taken over from the duplicate")
    check(set(kept.tb_aliases.split("|")) == {"premises", "land and buildings"}, "trial-balance aliases from both are kept")
    check(EngagementTaxAssetRule.query.filter_by(asset_class_id=kept.id).count() == 1 and EngagementTaxAssetRule.query.filter_by(asset_class_id=ids["p2"]).count() == 0, "the tax rule moved across (one per class)")
    check(PPEAssetClass.query.filter_by(engagement_id=ids["e2"]).count() == 2, "engagement 2 now has Land and Buildings and Motor vehicles only")
    check(AS.normalise_class_names() == 0, "running it again changes nothing")
    check(AS._class_by_name(ids["e1"], "Premises", create=False).id == ids["p1"], "looking a class up as 'Premises' finds Land and Buildings")
    check(AS._class_by_name(ids["e1"], "Premises").name == "Land and Buildings" and PPEAssetClass.query.filter_by(engagement_id=ids["e1"]).count() == 1, "no second class is made for 'Premises'")
    admin = User.query.filter_by(role="admin").first(); aid = admin.id

c = app.test_client()
with c.session_transaction() as s: s["_user_id"] = str(aid); s["_fresh"] = True
with app.app_context():
    rule_url = [str(x.rule) for x in app.url_map.iter_rules() if x.endpoint.endswith("add_ppe_asset_class") or "asset_class" in x.endpoint and "add" in x.endpoint]
print("add-class routes:", rule_url)
if rule_url:
    url = rule_url[0].replace("<int:engagement_id>", str(ids["e1"]))
    r = c.post(url, data={"name": "Premises", "depreciation_method": "Straight-line", "rate_percent": "2"})
    with app.app_context():
        names = sorted(x.name for x in PPEAssetClass.query.filter_by(engagement_id=ids["e1"]).all())
    check(r.status_code in (302, 303) and names.count("Land and Buildings") >= 1 and "Premises" not in names, f"adding a class called Premises through the page stores Land and Buildings ({names})")
print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
