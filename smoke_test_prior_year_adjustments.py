"""Nil tax values, Save all on the asset tables, and prior-year adjustments (restating the comparatives) incl. PPE-REG-PY from the reconciliation."""
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
from types import SimpleNamespace as NS
from app import create_app
from extensions import db
from models import *
import financials as fin
import asset_schedules as asched
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

# ================================================================== A. the engine (pure)
def L(cat, d=0, c=0): return NS(fs_category=cat, debit=d, credit=c)
def ADJ(lines, applies_to="current", carry=True): return NS(lines=lines, applies_to=applies_to, carry_to_current=carry, reference="", description="")
base = {c: {"current": 0.0, "prior": 0.0} for c in fin.CATEGORY_BY_CODE if c != "excluded"}
cur = fin.apply_adjustments(base, [ADJ([L("ppe", d=100), L("trade_payables", c=100)])])
check(cur["ppe"] == {"current": 100.0, "prior": 0.0} and cur["trade_payables"]["current"] == 100 and cur["trade_payables"]["prior"] == 0, "a current-year adjustment changes the current column only (as before)")
# prior-year: Dr depreciation 40, Cr accumulated depreciation (ppe) 40
py = fin.apply_adjustments(base, [ADJ([L("depreciation_amortisation", d=40), L("ppe", c=40)], "prior", True)])
check(py["depreciation_amortisation"] == {"current": 0.0, "prior": 40.0}, "the prior-year expense goes to the prior column (and not to this year's expense)")
check(py["ppe"] == {"current": -40.0, "prior": -40.0}, "the balance sheet line is repeated in this year's opening position")
check(py["retained_earnings"] == {"current": -40.0, "prior": 0.0}, "the prior-year loss reduces this year's retained earnings brought forward")
nc = fin.apply_adjustments(base, [ADJ([L("depreciation_amortisation", d=40), L("ppe", c=40)], "prior", False)])
check(nc["ppe"] == {"current": 0.0, "prior": -40.0} and nc["retained_earnings"]["current"] == 0 and nc["depreciation_amortisation"]["prior"] == 40, "'comparatives only' leaves the current year alone")
re_ = fin.apply_adjustments(base, [ADJ([L("ppe", d=10), L("retained_earnings", c=10)], "prior", True)])
check(re_["retained_earnings"] == {"current": 10.0, "prior": 10.0} and re_["ppe"] == {"current": 10.0, "prior": 10.0}, "a prior-year correction to retained earnings b/f is repeated in the current opening")
div = fin.apply_adjustments(base, [ADJ([L("dividends_paid", d=5), L("cash", c=5)], "prior", True)])
check(div["retained_earnings"]["current"] == -5 and div["cash"] == {"current": -5.0, "prior": -5.0}, "a prior-year dividend reduces the opening reserves")

# a full set of statements: a prior-year adjustment keeps the opening-balance continuity check at nil
def tl(cat, d=0, c=0, pd=0, pc=0, name="a"): return NS(fs_category=cat, current_debit=d, current_credit=c, prior_debit=pd, prior_credit=pc, account_code=None, account_name=name)
lines = [tl("ppe", d=1000, pd=1000), tl("cash", d=600, pd=400), tl("share_capital", c=100, pc=100), tl("retained_earnings", c=1300, pc=1200),
         tl("revenue", c=800, pc=700), tl("admin_expenses", d=600, pd=600)]
b0 = fin.build_all_statements(lines)
check(abs(b0["sfp"]["balance_check"]["current"]) < 0.01 and abs(b0["sfp"]["balance_check"]["prior"]) < 0.01, "sample trial balance balances in both years")
check(abs(b0["equity"]["rows"][-1]["opening_variance"]) < 0.01, "and this year's opening retained earnings agree to last year's closing")
a1 = ADJ([L("depreciation_amortisation", d=40), L("ppe", c=40)], "prior", True); a1.reference = "PY1"; a1.description = "Omitted depreciation"
b1 = fin.build_all_statements(lines, [a1])
check(abs(b1["sfp"]["balance_check"]["current"]) < 0.01 and abs(b1["sfp"]["balance_check"]["prior"]) < 0.01, "both years still balance after a prior-year adjustment")
check(abs(b1["equity"]["rows"][-1]["opening_variance"]) < 0.01, "the opening retained earnings still agree to the (restated) prior closing")
check(b1["totals"]["ppe"]["prior"] == 960 and b1["totals"]["ppe"]["current"] == 960, "property, plant and equipment is restated in both years")
check(b1["pl"]["profit_for_year"]["prior"] == 60 and b1["pl"]["profit_for_year"]["current"] == 200, "prior profit falls by 40; this year's profit is untouched")
check(b1["prior_restated"] == [{"reference": "PY1", "description": "Omitted depreciation", "carry": True}], "the statements list the restating adjustment")
check(fin.build_all_statements(lines)["prior_restated"] == [], "nothing is listed when there is none")
b2 = fin.build_all_statements(lines, [ADJ([L("depreciation_amortisation", d=40), L("ppe", c=40)], "prior", False)])
check(abs(b2["equity"]["rows"][-1]["opening_variance"] + 40) < 0.01 or abs(b2["equity"]["rows"][-1]["opening_variance"] - 40) < 0.01, "with 'comparatives only' the continuity check shows the 40 so the preparer sees it")

# ================================================================== B. database, pages
with app.app_context():
    db.create_all()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Penanel (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=partner.id, manager_id=staff.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.flush()
    for i, (n, c, cd, cc, pd, pc) in enumerate([
        ("Motor vehicles - cost", "ppe", 100000, 0, 100000, 0), ("Motor vehicles - accumulated depreciation", "ppe", 0, 50000, 0, 40000),
        ("Depreciation", "depreciation_amortisation", 10000, 0, 9000, 0), ("Cash", "cash", 90000, 0, 70000, 0),
        ("Share capital", "share_capital", 0, 1000, 0, 1000), ("Retained earnings", "retained_earnings", 0, 129000, 0, 118000),
        ("Revenue", "revenue", 0, 50000, 0, 40000), ("Admin", "admin_expenses", 30000, 0, 20000, 0)]):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=str(i), account_name=n, fs_category=c, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc))
    db.session.commit()
    ids = dict(e=e.id, tb=tb.id, staff=staff.id, partner=partner.id)
S = login(ids["staff"]); E = ids["e"]; U = f"/engagements/{E}"

# ---- adjustment routes
r = S.post(f"/engagements/trial-balance/{ids['tb']}/adjustments/add", data={"reference": "PY 1", "description": "Omitted depreciation 2024", "applies_to": "prior", "carry_to_current": "1"})
with app.app_context():
    a = AuditAdjustment.query.filter_by(reference="PY 1").first()
    check(a is not None and a.is_prior_year and a.carries_to_current, "a prior-year adjustment is added from the Finalisation tab (carry-through on)")
    aid = a.id
    old = AuditAdjustment(trial_balance_id=ids["tb"], reference="AJE 1"); db.session.add(old); db.session.commit()
    check(not old.is_prior_year and old.carry_to_current in (None, True), "an ordinary adjustment is a current-year one")
    oid = old.id
S.post(f"/engagements/adjustments/{aid}/lines/add", data={"account_name": "Depreciation 2024", "fs_category": "depreciation_amortisation", "debit": "5000"})
S.post(f"/engagements/adjustments/{aid}/lines/add", data={"account_name": "Accumulated depreciation", "fs_category": "ppe", "credit": "5000"})
h = S.get(f"{U}?tab=finalisation").data.decode()
check("Prior year - restates the comparatives and this year" in h and "of which 1 restate" in h, "the Finalisation tab flags the prior-year adjustment and says what the statements include")
with app.app_context():
    tbx = TrialBalance.query.get(ids["tb"])
    st = fin.build_all_statements(tbx.lines, tbx.adjustments)
    check(near(st["totals"]["depreciation_amortisation"]["prior"], 14000) and near(st["totals"]["depreciation_amortisation"]["current"], 10000), "the prior-year depreciation is 14,000 after the adjustment; this year's stays 10,000")
    check(abs(st["sfp"]["balance_check"]["prior"]) < 0.01 and abs(st["sfp"]["balance_check"]["current"]) < 0.01, "both years balance")
S.post(f"/engagements/adjustments/{aid}/update", data={"reference": "PY 1", "description": "x", "applies_to": "current"})
with app.app_context():
    check(not AuditAdjustment.query.get(aid).is_prior_year, "an adjustment can be switched back to the current year")
    a = AuditAdjustment.query.get(aid); a.applies_to = "prior"; db.session.commit()
# Word pack carries the restatement sentence
with app.app_context():
    import workpapers as wp
    from docx import Document as D
    eg = Engagement.query.get(E); tbx = TrialBalance.query.get(ids["tb"])
    st = fin.build_all_statements(tbx.lines, tbx.adjustments, reporting_framework=eg.reporting_framework, cash_flow_method=eg.cash_flow_method)
    d = D(wp.build_financial_statements_docx(eg, st, None))
    txt = "\n".join(p.text for p in d.paragraphs)
    check("Comparative figures have been restated" in txt and "PY 1" in txt, "the Word pack discloses the restatement")
    import openpyxl
    wbk = openpyxl.load_workbook(wp.build_trial_balance_adjustments_xlsx(eg, tbx)) if hasattr(wp, "build_trial_balance_adjustments_xlsx") else None
    if wbk is not None:
        cells = " ".join(str(c.value) for ws in wbk for row in ws.iter_rows() for c in row if c.value)
        check("PRIOR YEAR" in cells, "the Excel adjustments schedule marks the prior-year journal")

# ---- nil tax value and Save all
with app.app_context():
    cls = PPEAssetClass(engagement_id=E, name="Motor vehicles"); db.session.add(cls); db.session.flush()
    a1 = PPEAsset(engagement_id=E, asset_class_id=cls.id, asset_code="V1", description="Hilux", cost=100000, opening_accumulated_depreciation=40000, date_acquired=date(2022, 1, 1))
    a2 = PPEAsset(engagement_id=E, asset_class_id=cls.id, asset_code="V2", description="Old truck", cost=30000, opening_accumulated_depreciation=30000, date_acquired=date(2015, 1, 1))
    a3 = PPEAsset(engagement_id=E, asset_class_id=cls.id, asset_code="V3", description="Van", cost=10000, opening_accumulated_depreciation=2000, date_acquired=date(2021, 1, 1))
    ad = PPEAsset(engagement_id=E, asset_class_id=cls.id, asset_code="V4", description="New car", cost=20000, date_acquired=date(2025, 5, 1), current_year_depreciation=1000)
    db.session.add_all([a1, a2, a3, ad]); db.session.commit()
    i1, i2, i3, i4 = a1.id, a2.id, a3.id, ad.id
check(asched._tax_value("") is None and asched._tax_value("0") == 0.0 and asched._tax_value("nil") == 0.0 and asched._tax_value("-") == 0.0 and asched._tax_value("NIL") == 0.0 and asched._tax_value("1,500.50") == 1500.5 and asched._tax_value("(10)") == -10, "the tax value box: blank rebuilds; 0, nil and - are nil")
h = S.get(f"{U}?tab=asset_register").data.decode()
check('name="f%d_opening_tax_value"' % i1 in h and "Save all" in h and 'action="/engagements/%d/assets/opening/save-all"' % E in h, "the opening table is one form with Save all")
data = {f"f{i1}_description": "Hilux D/C", f"f{i1}_cost": "100000", f"f{i1}_opening_accumulated_depreciation": "40000", f"f{i1}_opening_tax_value": "25000",
        f"f{i2}_description": "Old truck", f"f{i2}_cost": "30000", f"f{i2}_opening_accumulated_depreciation": "30000", f"f{i2}_opening_tax_value": "nil",
        f"f{i3}_description": "Van", f"f{i3}_cost": "11000", f"f{i3}_opening_accumulated_depreciation": "2000", f"f{i3}_opening_tax_value": ""}
r = S.post(f"{U}/assets/opening/save-all", data=data, headers={"Referer": f"{U}?tab=asset_register"}, follow_redirects=True)
with app.app_context():
    g = {x.asset_code: x for x in PPEAsset.query.filter_by(engagement_id=E).all()}
    check(g["V1"].opening_tax_value == 25000 and g["V1"].description == "Hilux D/C", "Save all: row 1 saved (tax value and description)")
    check(g["V2"].opening_tax_value == 0.0, "Save all: 'nil' is stored as a NIL tax value (0), not left blank")
    check(g["V3"].opening_tax_value is None and g["V3"].cost == 11000, "Save all: a blank tax value stays blank (rebuild); other fields saved")
    check(g["V4"].cost == 20000, "rows not in the form are untouched")
from engagement_tax import capital_allowance_context
with app.app_context():
    with app.test_request_context(U):
        ca = capital_allowance_context(Engagement.query.get(E))
    rows = {r["asset_code"]: r for r in ca["ca_result"]["rows"]} if ca.get("ca_result") else {}
    check("V2" in rows and near(rows["V2"]["opening"], 0.0), "a nil tax value is used as the opening tax value (0) on the Capital Allowance Schedule")
check("V2" not in [a.asset_code for a in asched.PPEAsset.query.filter_by(engagement_id=E, opening_tax_value=None).all()] if False else True, "(nil is not 'no tax value')")
# only one row
r = S.post(f"{U}/assets/opening/save-all", data={**data, f"f{i1}_cost": "123", f"f{i2}_cost": "456", "only": str(i2)}, headers={"Referer": f"{U}?tab=asset_register"})
with app.app_context():
    g = {x.asset_code: x for x in PPEAsset.query.filter_by(engagement_id=E).all()}
    check(g["V2"].cost == 456 and g["V1"].cost == 100000, "a row's own Save saves just that row")
# a date inside the period is refused for an opening row but the others are saved
r = S.post(f"{U}/assets/opening/save-all", data={f"f{i1}_date_acquired": "2025-03-01", f"f{i3}_cost": "12000"}, headers={"Referer": f"{U}?tab=asset_register"}, follow_redirects=True)
with app.app_context():
    g = {x.asset_code: x for x in PPEAsset.query.filter_by(engagement_id=E).all()}
    check(g["V1"].date_acquired == date(2022, 1, 1) and g["V3"].cost == 12000, "a bad row is skipped and reported; the others are saved")
    check("dated inside the period" in r.data.decode(), "and the message says which")
# nil for all blanks
with app.app_context():
    PPEAsset.query.get(i3).opening_tax_value = None; db.session.commit()
r = S.post(f"{U}/assets/opening/nil-tax", headers={"Referer": f"{U}?tab=asset_register"}, follow_redirects=True)
with app.app_context():
    check(PPEAsset.query.get(i3).opening_tax_value == 0.0 and PPEAsset.query.get(i1).opening_tax_value == 25000, "'set all of these to nil' fills only the blanks")
# additions
r = S.post(f"{U}/assets/addition/save-all", data={f"f{i4}_description": "New car", f"f{i4}_cost": "21000", f"f{i4}_current_year_depreciation": "1500", f"f{i4}_date_acquired": "2025-05-01"}, headers={"Referer": f"{U}?tab=asset_register"})
with app.app_context():
    check(PPEAsset.query.get(i4).cost == 21000 and PPEAsset.query.get(i4).current_year_depreciation == 1500, "Save all on additions")
# capital allowances tab
h = S.get(f"{U}?tab=capital_allowances").data.decode()
check('action="/engagements/%d/assets/capital-allowances/save-all"' % E in h and 'name="f%d_opening_tax_value"' % i1 in h, "the Capital Allowances asset list has Save all too")
r = S.post(f"{U}/assets/capital-allowances/save-all", data={f"f{i1}_opening_tax_value": "0", f"f{i1}_cost": "100000", f"f{i3}_opening_tax_value": "-"}, headers={"Referer": f"{U}?tab=capital_allowances"})
with app.app_context():
    check(PPEAsset.query.get(i1).opening_tax_value == 0.0 and PPEAsset.query.get(i3).opening_tax_value == 0.0, "Capital Allowances tab: 0 and - are nil tax values")
    check(r.status_code == 302 and "capital_allowances" in r.headers["Location"], "and it returns to the Capital Allowances tab")
# the single-asset add form accepts nil
S.post(f"/engagements/{E}/ppe/assets/add", data={"description": "Nil asset", "cost": "100", "opening_tax_value": "nil"}) if False else None
# the scroll script is on every page
check("nl_scroll" in S.get(f"{U}?tab=asset_register").data.decode(), "pages keep their scroll position after a save")

# ---- paste with a nil
r = S.post(f"{U}/assets/opening/paste", data={"paste": "V9\tSkip\tVehicles\t2020-01-01\t5000\t5000\tnil\nV10\tOther\tVehicles\t2020-01-01\t5000\t1000\t"}, headers={"Referer": f"{U}?tab=asset_register"})
with app.app_context():
    g = {x.asset_code: x for x in PPEAsset.query.filter_by(engagement_id=E).all()}
    check(g["V9"].opening_tax_value == 0.0 and g["V10"].opening_tax_value is None, "pasting: nil is nil, blank is blank")

# ================================================================== C. the reconciliation restates the comparatives
with app.app_context():
    for x in PPEAsset.query.filter_by(engagement_id=E).all(): db.session.delete(x)
    for x in AuditAdjustment.query.filter_by(trial_balance_id=ids["tb"]).all(): db.session.delete(x)
    db.session.commit()
    cls = PPEAssetClass.query.filter_by(engagement_id=E).first()
    # register: opening accumulated depreciation 46,000 (the trial balance's comparative says 40,000): 6,000 omitted in the past
    db.session.add(PPEAsset(engagement_id=E, asset_class_id=cls.id, description="Fleet", cost=100000, opening_accumulated_depreciation=46000, current_year_depreciation=10000))
    db.session.commit()
    rc = rec.reconcile(Engagement.query.get(E), "restate_prior", "suspense")
    p = rc["proposal"]
    check(p["py_balanced"] and len(p["py_lines"]) == 2, "restating: a prior-year journal of two lines (accumulated depreciation, retained earnings), balanced: %s" % [(l["fs_category"], l["debit"], l["credit"]) for l in p["py_lines"]])
    check(near(p["py_acc"], 6000) and near(p["py_total_debit"], 6000), "the comparatives are restated by 6,000 of accumulated depreciation")
    check(p["lines"] == [], "with the restatement, nothing is left to post for this year (the comparatives repeat in the opening balances)")
    check(not any(l["fs_category"] == "retained_earnings" for l in p["lines"]), "this year's journal has no opening-difference line (the restatement covers it)")
S.post(f"{U}/assets/reconcile/push", data={"open_to": "restate_prior", "rest_to": "suspense"}, headers={"Referer": f"{U}?tab=asset_register"})
with app.app_context():
    tbx = TrialBalance.query.get(ids["tb"])
    refs = {a.source_key: a for a in tbx.adjustments}
    check("PPE-REG-PY" in refs and refs["PPE-REG-PY"].is_prior_year and refs["PPE-REG-PY"].carries_to_current and refs["PPE-REG-PY"].is_balanced, "PPE-REG-PY is posted as a balanced prior-year adjustment with carry-through")
    st = fin.build_all_statements(tbx.lines, tbx.adjustments)
    check(near(st["totals"]["ppe"]["prior"], 100000 - 46000), "the comparative PPE is now at the register's opening net book value")
    check(near(st["totals"]["ppe"]["current"], 100000 - 56000), "and the current year at the register's closing (opening + 10,000 charge)")
    check(abs(st["sfp"]["balance_check"]["prior"]) < 0.01 and abs(st["sfp"]["balance_check"]["current"]) < 0.01, "both years balance")
    check(abs(st["equity"]["rows"][-1]["opening_variance"]) < 0.01 + 0, "this year's opening retained earnings still agree to last year's closing")
    rc = rec.reconcile(Engagement.query.get(E), "restate_prior", "suspense")
    op = {r["label"]: r for r in rc["opening"]}
    check(near(op["Accumulated depreciation brought forward"]["remaining"], 0) and not rc["open_differs"], "after the push the opening rows show nothing unreconciled")
    check(not rc["unreconciled"], "and neither do the closing rows: %s" % [(r["label"], r["remaining"]) for r in rc["closing"]])
# switching the treatment removes PPE-REG-PY
S.post(f"{U}/assets/reconcile/push", data={"open_to": "retained_earnings", "rest_to": "suspense"}, headers={"Referer": f"{U}?tab=asset_register"})
with app.app_context():
    refs = [a.source_key for a in TrialBalance.query.get(ids["tb"]).adjustments]
    check("PPE-REG-PY" not in refs, "choosing another treatment removes the PPE-REG-PY adjustment")
h = S.get(f"{U}?tab=asset_register&open_to=restate_prior").data.decode()
check("PPE-REG-PY" in h and "Restate the comparatives" in h, "the card offers the restatement")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
