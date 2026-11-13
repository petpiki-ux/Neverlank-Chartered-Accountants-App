"""The Income Tax Act's provisions on the tax pages: links beside the computation and the prohibited-deductions schedule, the capital allowance
reference panel, the first-year special initial allowance rate, and the starter building rule."""
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
import act_import as AI, firm_tax
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def close(a, b): return abs(a - b) < 0.005

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Tax FY2025", type="Audit", partner_id=partner.id, period_end=date(2025, 12, 31), acceptance_required=False)
    db.session.add(eng); db.session.commit()
    tb = TrialBalance(engagement_id=eng.id, source="manual"); db.session.add(tb); db.session.commit()
    db.session.add_all([TrialBalanceLine(trial_balance_id=tb.id, account_code="4000", account_name="Sales", fs_category="revenue", current_credit=100000.0),
                        TrialBalanceLine(trial_balance_id=tb.id, account_code="6000", account_name="Admin", fs_category="admin_expenses", current_debit=60000.0)])
    db.session.commit()
    ids = dict(partner=partner.id, eng=eng.id)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P = login(ids["partner"]); E = ids["eng"]

# --- starter rules from the Act
d = firm_tax.default_rule("Buildings"); check(d["method"] == "WT_SL" and d["wt"] == 2.5 and "para 6" in d["note"], "starter rule for buildings: 2.5% (Fourth Schedule para 6)")
check(firm_tax.default_rule("Freehold land")["method"] == "NONE", "land still gets no allowance")

# --- first-year SIA rate (the Act's 50% then 25% twice)
asset = {"asset_code": "A1", "description": "Press", "date_acquired": date(2025, 3, 10), "cost": 100000.0, "asset_class_id": 1}
rule = dict(method="SIA", wt=0.0, sia=25.0, sia_years=3, sia_first=50.0, cap=None, prorata=False, verified=True)
rows = firm_tax.simulate_asset(asset, rule, date(2025, 12, 31), date(2028, 12, 31))
check([close(r["allowance"], v) for r, v in zip(rows, (50000, 25000, 25000, 0))] == [True] * 4 and close(rows[-1]["closing"], 0), "SIA 50% then 25% and 25%: tax value runs to nil in three years")
rule2 = dict(rule, sia_first=None, sia_years=4)
rows2 = firm_tax.simulate_asset(asset, rule2, date(2025, 12, 31), date(2028, 12, 31))
check(all(close(r["allowance"], 25000) for r in rows2), "no first-year rate: a flat 25% for four years, as before")
check(firm_tax.rule_dict(None)["sia_first"] is None, "an asset with no rule has no first-year rate")

# --- pages with the Act not filed
r = P.get(f"/engagements/{E}?tab=income_tax")
check(r.status_code == 200 and b"Where the Income Tax Act says so" in r.data and b"File the Income Tax Act" in r.data and b'href="/tax/act/ita/16(1)(m)"' not in r.data, "engagement tax tab, Act not filed: panel shown as plain text with an invitation to file")
# --- file the Act
with app.app_context():
    AI.load_act("ita", user_id=ids["partner"])
r = P.get(f"/engagements/{E}?tab=income_tax")
check(r.status_code == 200 and b'href="/tax/act/ita/16(1)(m)"' in r.data and b'href="/tax/act/ita/sch4.9"' in r.data and b'href="/tax/act/ita/15(3)"' in r.data and b"File the Income Tax Act" not in r.data, "engagement tax tab: provisions link into the Act")
r = P.get("/tax/act/ita/15(3)")
check(r.status_code == 200 and b"assessed loss" in r.data and b"more than six" in r.data, "15(3) opens the assessed loss provision, with the six-year limit")
r = P.get("/tax/act/ita/sch35")
check(r.status_code == 200 and b"Thirty-Fifth Schedule" in r.data, "the Thirty-Fifth Schedule (transfer pricing) opens")
r = P.get("/tax/act/ita/72"); check(r.status_code == 200 and b"provisional tax" in r.data.lower(), "section 72 (provisional tax) opens")
r = P.get("/tax/act/ita/37A"); check(r.status_code == 200 and b"elf assessment" in r.data, "section 37A (self assessment) opens")
r = P.get("/tax/act/ita/98B"); check(r.status_code == 200, "section 98B opens")
# every link in the panel resolves
import re
html = P.get(f"/engagements/{E}?tab=income_tax").data.decode()
refs = sorted(set(re.findall(r'href="(/tax/act/ita/[^"]+)"', html)))
bad = [h for h in refs if P.get(h).status_code != 200 or b"Couldn" in P.get(h, follow_redirects=True).data]
check(len(refs) >= 20 and not bad, "all %d Act links on the tax tab open (%s)" % (len(refs), bad))

# --- prohibited schedule links
B = f"/engagements/{E}/prohibited-deductions"
P.post(f"{B}/items/add", data={"expense_type": "client_entertainment", "description": "Client dinner", "amount": "1200", "voucher_ref": "PV-1", "charged_to_pl": "1"}, follow_redirects=True)
r = P.get(B)
check(r.status_code == 200 and b'>Section 16(1)(m)</a>' in r.data, "the schedule's clause headings link to the Act")
r = P.get(f"/engagements/{E}?tab=income_tax")
check(b"read section 16" in r.data, "the engagement's section 16 card links to the whole section")

# --- rules page: reference panel and first-year rate
with app.app_context():
    cls = FirmAssetClass.query.first()
    if cls is None:
        cls = FirmAssetClass(name="Plant and machinery"); db.session.add(cls); db.session.commit()
    cid = cls.id
r = P.get("/firm-finance/tax/rules")
check(r.status_code == 200 and b"What the Income Tax Act says about these allowances" in r.data and b'href="/tax/act/ita/sch4.9"' in r.data and b"SIA year-1 %" in r.data, "Rates & rules: Act panel with links and the first-year column")
r = P.post("/firm-finance/tax/rules/assets", data={f"method_{cid}": "SIA", f"wt_{cid}": "0", f"sia_{cid}": "25", f"first_{cid}": "50", f"years_{cid}": "3", f"cap_{cid}": ""}, follow_redirects=True)
with app.app_context():
    x = FirmTaxAssetRule.query.filter_by(asset_class_id=cid).first()
    check(x.sia_first_percent == 50.0 and x.sia_years == 3 and x.sia_percent == 25.0, "the first-year rate is saved")
    check(firm_tax.rule_dict(x)["sia_first"] == 50.0, "...and reaches the calculation")
r = P.post("/firm-finance/tax/rules/assets", data={f"method_{cid}": "SIA", f"wt_{cid}": "0", f"sia_{cid}": "25", f"first_{cid}": "150", f"years_{cid}": "3", f"cap_{cid}": ""}, follow_redirects=True)
check(b"Not saved" in r.data, "a first-year rate above 100 is refused")
r = P.post("/firm-finance/tax/rules/assets", data={f"method_{cid}": "SIA", f"wt_{cid}": "0", f"sia_{cid}": "25", f"first_{cid}": "", f"years_{cid}": "3", f"cap_{cid}": ""}, follow_redirects=True)
with app.app_context():
    check(FirmTaxAssetRule.query.filter_by(asset_class_id=cid).first().sia_first_percent is None, "clearing the box goes back to the same rate every year")

print("\nALL PASSED" if not fails else "\n%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
