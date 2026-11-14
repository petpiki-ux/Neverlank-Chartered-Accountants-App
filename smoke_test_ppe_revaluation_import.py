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

# an applied revaluation is locked against importing
with app.app_context():
    rec = pr.get(EID); rec.status = "Applied"; db.session.commit()
r = c.post(f"/engagements/{EID}/revaluation/import/upload", data={"file": (io.BytesIO(XLSX), "valuation.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
check("Undo it first" in r.data.decode(), "importing is refused while the revaluation is applied")

print("\n%d failure(s)" % len(fails))
if fails:
    for f in fails: print(" -", f)
    sys.exit(1)
