"""Importing the valuer's list (Excel) into the draft revaluation: two tables in one sheet, matching by serial number and description,
many lines to one register asset, the check screen, and the totals."""
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
from datetime import date
import openpyxl
from app import create_app
from extensions import db
from models import *
import ppe_revaluation as pr
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
def near(a, b, t=0.01): return abs(a - b) <= t

# the valuer's file, laid out like the one received: a machinery table by department, then a motor vehicles table
wb = openpyxl.Workbook(); ws = wb.active
ws.append([None, None, None, "Machinery & Equipment Register", None, None, "Page 1"])
ws.append(["Department", "Item No.", "Category", "Make/Model", "Serial Number", "Year Of Man", "Value US$"])
ws.append(["FACTORY", 1, "Machinery", "SPEEDMASTER SM 102-8 PS - 8 COLOUR", 538317, None, 400000])
ws.append(["FACTORY", 2, "Machinery", "Polar 115E Guillotine", 6811203, None, 15550])
ws.append(["FACTORY", 3, "Machinery", "Verco Machine", None, None, 1000.5])
ws.append([None, None, None, None, None, None, 416550.5])
ws.append([None, None, None, None, None, None, 416550.5])
ws.append(["Motor Vehicles Register"])
ws.append(["Make/Model", "Year", "Serial Number", "Value US$"])
ws.append(["\nYAMAHA XTZ125E", 2023, "AGR 6036", 2500])
ws.append(["TOYOTA COROLLA ", 1999, "ADK 3638", 3000])
ws.append(["TOYOTA HILUX", 2022, "AFX 7094", 65000])
ws.append(["TOYOTA HILUX", 2022, "AFX 0988", 40000])
ws.append([None, None, None, 110500])
buf = io.BytesIO(); wb.save(buf); XLSX = buf.getvalue()

with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Oakbrook", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False,
                   period_end=date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e); db.session.commit()
    EID, UID = e.id, u.id
    mc = PPEAssetClass(engagement_id=EID, name="Machinery and Equipment", order=1)
    mv = PPEAssetClass(engagement_id=EID, name="Motor Vehicles", order=2)
    db.session.add_all([mc, mv]); db.session.commit()
    def A(cls, code, desc, cost, acc, dep):
        a = PPEAsset(engagement_id=EID, asset_class_id=cls.id, asset_code=code, description=desc, date_acquired=date(2018, 1, 1), cost=cost,
                     opening_accumulated_depreciation=acc, current_year_depreciation=dep)
        db.session.add(a); db.session.commit(); return a.id
    M1 = A(mc, "M1", "Printing machinery", 1000000, 400000, 50000)
    V1 = A(mv, "AFX 7094", "Hilux double cab", 80000, 30000, 8000)
    V2 = A(mv, "", "Toyota Corolla 1999", 6000, 2500, 500)
    V3 = A(mv, "AGR 6036", "Yamaha motorcycle", 3000, 800, 300)

c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(UID); s["_fresh"] = True

# the card offers the import
h = c.get(f"/engagements/{EID}?tab=asset_register").data.decode()
check("Import the valuer" in h, "the Revaluation card offers the import")

# upload: the check screen
r = c.post(f"/engagements/{EID}/revaluation/import/upload", data={"file": (io.BytesIO(XLSX), "valuation.xlsx")}, content_type="multipart/form-data")
h = r.data.decode()
check(r.status_code == 200 and "Import the valuer" in h and "Machinery &amp; Equipment Register" in h and "Motor Vehicles Register" in h, "the check screen lists both tables of the file")
check("agrees (416,550.50)" in h and "agrees (110,500.00)" in h, "each table's total agrees to the total stated in the file")
check("serial number" in h and "similar description" in h and "only asset in the class" in h, "lines are matched by serial number, by a similar description and to the only asset of the class")
import re
token = re.search(r"/import/([0-9a-f]{32})/confirm", h).group(1)
sel = {m.group(1): m.group(2) for m in re.finditer(r'<select name="asset_(\d+)".*?</select>', h, re.S) for m in [re.match(r"(\d+)", m.group(1)) and re.search(r'name="asset_(\d+)".*?<option value="(\d+)" selected', m.group(0), re.S)] if m}
print("auto-matched:", sel)
check(len(sel) == 6, "six of the seven lines were matched without help (the second Hilux is not in the register)")

# confirm with the suggestions as they stand (the form posts the selected options)
form = {"ext": ".xlsx", "filename": "valuation", "bulk_asset": ""}
for k, v in sel.items(): form[f"asset_{k}"] = v
r = c.post(f"/engagements/{EID}/revaluation/import/{token}/confirm", data=form, follow_redirects=True)
with app.app_context():
    rec = pr.get(EID)
    lines = {l.asset_id: l.fair_value for l in rec.lines}
    check(near(lines.get(M1, 0), 416550.5), "the three machinery lines are added up on the one register asset: 416,550.50 (%s)" % lines.get(M1))
    check(near(lines.get(V1, 0), 65000), "the Hilux AFX 7094 is matched by its registration number: 65,000 (%s)" % lines.get(V1))
    check(near(lines.get(V2, 0), 3000), "the Corolla is matched by its description: 3,000 (%s)" % lines.get(V2))
    check(near(lines.get(V3, 0), 2500), "the Yamaha is matched by its registration number: 2,500 (%s)" % lines.get(V3))
    check(rec.valuation_date == date(2025, 12, 31), "the valuation date defaults to the year end, 31 December 2025")
    check(rec.status == "Draft", "the import only fills the draft; nothing is applied")
    calc = pr.calculate(Engagement.query.get(EID), rec)
    check(near(calc["totals"]["fair_value"], sum(lines.values())), "the draft's fair value total follows the import")
h = r.data.decode()
check("Imported the valuer" in h, "the page confirms the import")
# the second Hilux (AFX 0988) is not in the register: it is reported, not silently dropped
check("not assigned" in h or "not in the revaluation" in h, "a line with no register asset is reported (the second Hilux, 40,000)")

# ---- an engagement with no register yet: the list creates the assets
with app.app_context():
    e2 = Engagement(client_id=Client.query.first().id, title="Audit 2025 (empty register)", type="Audit", partner_id=UID, manager_id=UID,
                    acceptance_required=False, period_end=date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e2); db.session.commit(); EID2 = e2.id
    tb2 = TrialBalance(engagement_id=EID2); db.session.add(tb2); db.session.flush()
    db.session.add(TrialBalanceLine(trial_balance_id=tb2.id, account_name="Bank", fs_category="cash", current_debit=1, current_credit=0, prior_debit=0, prior_credit=0))
    db.session.commit()
r = c.post(f"/engagements/{EID2}/revaluation/import/upload", data={"file": (io.BytesIO(XLSX), "valuation.xlsx")}, content_type="multipart/form-data")
h = r.data.decode()
check("Add a register asset for every line" in h and 'name="create_missing" value="1" checked' in h, "with nothing matched, the check screen offers to add the assets (ticked)")
token2 = re.search(r"/import/([0-9a-f]{32})/confirm", h).group(1)
r = c.post(f"/engagements/{EID2}/revaluation/import/{token2}/confirm", data={"ext": ".xlsx", "filename": "valuation", "create_missing": "1"}, follow_redirects=True)
with app.app_context():
    assets = PPEAsset.query.filter_by(engagement_id=EID2).all()
    check(len(assets) == 7, "seven register assets were created from the seven lines")
    names = sorted(a.asset_class.name for a in assets)
    check(set(names) == {"Machinery", "Motor Vehicles"}, "in two classes taken from the file: %s" % sorted(set(names)))
    check(all((a.cost or 0) == 0 and (a.opening_accumulated_depreciation or 0) == 0 for a in assets), "their cost and accumulated depreciation are left at nil")
    check(any(a.asset_code == "AFX 7094" for a in assets), "the registration number is the asset code")
    check(any("2022" in a.description for a in assets), "the year is added to the description of a vehicle")
    rec2 = pr.get(EID2)
    check(len(rec2.lines) == 7 and near(sum(l.fair_value for l in rec2.lines), 416550.5 + 110500), "every asset carries the valuer's value, total 527,050.50")
    calc2 = pr.calculate(Engagement.query.get(EID2), rec2)
    check(any("no cost in the register" in i for i in calc2["issues"]), "applying is blocked until the cost and accumulated depreciation are entered")
    for a in assets:
        a.cost = a.cost or 1000.0
    db.session.commit()
    calc2 = pr.calculate(Engagement.query.get(EID2), rec2)
    check(not any("no cost in the register" in i for i in calc2["issues"]), "...and the block lifts once each asset has a cost")

# ---- the values as the register's closing balances
with app.app_context():
    e3 = Engagement(client_id=Client.query.first().id, title="Audit 2025 (closing balances)", type="Audit", partner_id=UID, manager_id=UID,
                    acceptance_required=False, period_end=date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e3); db.session.commit(); EID3 = e3.id
r = c.post(f"/engagements/{EID3}/revaluation/import/upload", data={"file": (io.BytesIO(XLSX), "valuation.xlsx")}, content_type="multipart/form-data")
h = r.data.decode()
check('name="use" value="closing"' in h, "the check screen offers the values as the register's closing balances")
token3 = re.search(r"/import/([0-9a-f]{32})/confirm", h).group(1)
r = c.post(f"/engagements/{EID3}/revaluation/import/{token3}/confirm", data={"ext": ".xlsx", "filename": "valuation", "create_missing": "1", "use": "closing"}, follow_redirects=True)
with app.app_context():
    import asset_schedules as sched, financials as fin_
    eng3 = Engagement.query.get(EID3)
    check(PPEAsset.query.filter_by(engagement_id=EID3).count() == 7, "seven assets were set up")
    check(near(sum(a.cost for a in PPEAsset.query.filter_by(engagement_id=EID3).all()), 527050.5), "their cost is the valuer's value: 527,050.50")
    import engagements as eng_mod
    _, _, cd_, ad_ = eng_mod._ppe_plain_data(EID3)
    mv_ = fin_.build_ppe_movement_schedule(cd_, ad_, eng3.period_end)
    check(near(mv_["total"]["closing_nbv"], 527050.5) and near(mv_["total"]["closing_acc_dep"], 0) and near(mv_["total"]["charge"], 0), "the register closes at 527,050.50 with nil accumulated depreciation and no charge")
    check(pr.get(EID3) is None or not pr.get(EID3).lines, "no revaluation is recorded")
    check("The Asset Register schedule now closes at 527,050.50" in r.data.decode(), "the confirmation says what the register schedule now closes at")
    check(not pr.calculate(eng3, pr.get(EID3))["totals"]["count"], "the revaluation card has nothing to apply")

# an applied revaluation is locked against importing
with app.app_context():
    rec = pr.get(EID); rec.status = "Applied"; db.session.commit()
r = c.post(f"/engagements/{EID}/revaluation/import/upload", data={"file": (io.BytesIO(XLSX), "valuation.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
check("Undo it first" in r.data.decode(), "importing is refused while the revaluation is applied")

print("\n%d failure(s)" % len(fails))
if fails:
    for f in fails: print(" -", f)
    sys.exit(1)
