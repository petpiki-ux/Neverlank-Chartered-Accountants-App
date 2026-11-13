"""Whole-number financial statements, right-aligned figures in the Word pack, and the Asset Register / Capital Allowance Schedule worked
from the year before's closing balances (opening balances, additions, disposals)."""
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
import firm_tax
import asset_schedules as asched
import workpapers as wp
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
def ln(cat, d=0, c=0, pd=0, pc=0, name="acc"):
    return NS(fs_category=cat, current_debit=d, current_credit=c, prior_debit=pd, prior_credit=pc, account_code=None, account_name=name)

# ================================================================== 1. whole numbers (pure)
check(fin.whole(2.5) == 3 and fin.whole(-2.5) == -3 and fin.whole(0.49) == 0 and fin.whole(1234.5) == 1235, "whole(): halves go away from zero")
check(str(fin.whole(-0.4)) == "0.0", "whole(): -0.4 is 0, not -0")
lines = [ln("ppe", d=10000.40), ln("trade_receivables", d=5000.40), ln("cash", d=3000.40), ln("share_capital", c=1000.50),
         ln("retained_earnings", c=2000.50), ln("trade_payables", c=4000.20), ln("revenue", c=20000.30), ln("admin_expenses", d=9000.30)]
raw = fin.build_all_statements(lines)
rnd = fin.build_all_statements(lines, whole_numbers=True)
check(abs(raw["sfp"]["balance_check"]["current"]) < 0.01, "the sample trial balance balances to the cent")
check(rnd["whole_numbers"] and rnd["rounding"]["whole_numbers"], "statements say they are in whole numbers")
def all_whole(st):
    vals = []
    for r in st["pl"]["rows"] + st["sfp"]["non_current_assets"] + st["sfp"]["current_assets"] + st["sfp"]["equity"]:
        vals += [r["current"], r.get("prior", 0)]
    for k in ("total_assets", "total_equity_and_liabilities", "total_equity"):
        vals += [st["sfp"][k]["current"], st["sfp"][k]["prior"]]
    return all(abs(v - round(v)) < 1e-9 for v in vals)
check(all_whole(rnd) and not all_whole(raw), "every printed figure is whole (and the unrounded build is not)")
check(abs(rnd["sfp"]["balance_check"]["current"]) < 1e-9, "the rounded statement of financial position balances")
sfp = rnd["sfp"]
check(abs(sum(r["current"] for r in sfp["non_current_assets"]) - sfp["nca_total"]["current"]) < 1e-9
      and abs(sfp["nca_total"]["current"] + sfp["ca_total"]["current"] - sfp["total_assets"]["current"]) < 1e-9, "subtotals add up from the rounded lines")
pl = {r["label"]: r["current"] for r in rnd["pl"]["rows"]}
check(abs(pl["Profit for the year"] - (pl["Revenue"] + pl["Administrative expenses"])) < 1e-9, "profit adds up from the rounded lines")
absorbed = rnd["rounding"]["absorbed"]
check(len(absorbed) == 1 and absorbed[0]["period"] == "current" and abs(absorbed[0]["amount"]) <= 2 and absorbed[0]["category"] in ("ppe", "trade_receivables", "trade_payables"),
      "a rounding difference is absorbed on a large balance and reported: %s" % absorbed)
check(rnd["cf"]["cash_close_actual"] == 3000.0, "cash is never the line that absorbs the difference")
# a genuinely unbalanced trial balance is left showing
bad = [ln("ppe", d=1000), ln("share_capital", c=990)]
b = fin.build_all_statements(bad, whole_numbers=True)
check(b["rounding"]["absorbed"] == [] and abs(b["sfp"]["balance_check"]["current"]) > 5, "a real imbalance is never absorbed")
# notes foot
tot = {n["title"]: n for n in rnd["notes"]}
for n in rnd["notes"]:
    if n["accounts"]:
        check(abs(sum(a["current"] for a in n["accounts"]) - n["total"]["current"]) < 1e-9, f"note '{n['title']}' accounts add to its whole-number total")
# two accounts in a note
l2 = [ln("admin_expenses", d=100.4, name="a"), ln("admin_expenses", d=100.4, name="b"), ln("admin_expenses", d=100.4, name="c"), ln("cash", d=301.2),
      ln("share_capital", c=602.4)]
r2 = fin.build_all_statements(l2, whole_numbers=True)
nn = [n for n in r2["notes"] if n["title"] == "Administrative expenses"][0]
check(abs(sum(a["current"] for a in nn["accounts"]) - nn["total"]["current"]) < 1e-9 and nn["total"]["current"] == 301, "three accounts of 100.4 add to the note total of 301")

# PPE movement in whole numbers
cls = [{"id": 1, "name": "Vehicles"}, {"id": 2, "name": "Equipment"}]
assets = [{"asset_class_id": 1, "cost": 1000.4, "opening_accumulated_depreciation": 200.4, "current_year_depreciation": 100.4, "disposal_cost": 0, "disposal_accumulated_depreciation": 0, "date_acquired": date(2023, 1, 1)},
          {"asset_class_id": 2, "cost": 500.4, "opening_accumulated_depreciation": 0, "current_year_depreciation": 50.4, "disposal_cost": 0, "disposal_accumulated_depreciation": 0, "date_acquired": date(2025, 5, 1)}]
mv = fin.build_ppe_movement_schedule(cls, assets, date(2025, 12, 31))
mw = fin.round_ppe_movement(mv)
okrows = all(abs(r["opening_nbv"] + r["additions"] - (r["disposals_cost"] - r["disposals_acc_dep"]) - r["charge"] - r["closing_nbv"]) < 1e-9 for r in mw["by_class"] + [mw["total"]])
check(okrows and all(isinstance(r["class_id"], int) for r in mw["by_class"]), "PPE movement rounds with every row adding across, class ids untouched")
check(abs(sum(r["closing_nbv"] for r in mw["by_class"]) - mw["total"]["closing_nbv"]) < 1e-9, "PPE movement total adds down")

# ================================================================== 2. Word pack: whole numbers and right alignment
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
check(wp._fmt_whole(1234567.5) == "1,234,568" and wp._fmt_whole(-1234.4) == "(1,234)" and wp._fmt_whole(-0.4) == "0" and wp._fmt_whole(None) == "0", "_fmt_whole formats whole, separated, brackets for negatives")

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=partner.id, manager_id=staff.id, acceptance_required=False,
                     period_end=date(2025, 12, 31))
    tax = Engagement(client_id=cl.id, title="Tax 2025", type="Tax Advisory", partner_id=partner.id, manager_id=staff.id, acceptance_required=False,
                     period_end=date(2025, 12, 31))
    sec = Engagement(client_id=cl.id, title="Sec", type="Secretarial", partner_id=partner.id, manager_id=staff.id, acceptance_required=False,
                     period_end=date(2025, 12, 31))
    db.session.add_all([eng, tax, sec]); db.session.commit()
    ids = dict(admin=admin.id, staff=staff.id, eng=eng.id, tax=tax.id, sec=sec.id)
    check(eng.has_asset_schedules and tax.has_asset_schedules and not sec.has_asset_schedules, "the asset tabs belong to engagements that keep accounts (audit, tax) and not to Secretarial")
    tb = TrialBalance(engagement_id=eng.id); db.session.add(tb); db.session.flush()
    for i, l in enumerate(lines):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=str(i), account_name=f"Account {i}", fs_category=l.fs_category,
                                        current_debit=l.current_debit, current_credit=l.current_credit, prior_debit=0, prior_credit=0))
    db.session.commit()
A = login(ids["admin"]); S = login(ids["staff"]); eid = ids["eng"]

with app.app_context():
    eng = Engagement.query.get(eid)
    tbx = TrialBalance.query.filter_by(engagement_id=eid).first()
    st = fin.build_all_statements(tbx.lines, tbx.adjustments, reporting_framework=eng.reporting_framework, cash_flow_method=eng.cash_flow_method, whole_numbers=True)
    buf = wp.build_financial_statements_docx(eng, st, None)
    d = DocxDocument(buf)
    text = "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    check("10,002" in text or "10,000" in text, "the Word pack prints whole numbers with separators")
    check(".00" not in text and ".40" not in text, "no cents appear in the statements' tables")
    right = wrong = 0
    for t in d.tables:
        hdr = [c.text for c in t.rows[0].cells]
        import re as _re
        isyr = lambda h: bool(_re.match(r"^\d{4}\n", h))
        if not any(isyr(h) for h in hdr) and "Current year" not in hdr and "Opening" not in hdr and "Amount" not in hdr:
            continue
        for row in t.rows:
            cells = row.cells
            if len(cells) > 1 and cells[0]._tc is cells[-1]._tc:
                continue
            for i, c in enumerate(cells):
                if hdr[i] in ("Current year", "Prior year", "Amount", "Opening", "Movement", "Closing") or isyr(hdr[i]):
                    ok = all(p.alignment == WD_ALIGN_PARAGRAPH.RIGHT for p in c.paragraphs)
                    right += ok; wrong += (not ok)
    check(right > 20 and wrong == 0, f"every figure cell in the statement tables is right-aligned ({right} cells, {wrong} not)")
    check("rounded to the nearest whole number" in text.replace("\n", " ") or any("rounded to the nearest whole number" in p.text for p in d.paragraphs), "the pack says the figures are rounded")

# the Finalisation tab
r = S.get(f"/engagements/{eid}?tab=finalisation"); h = r.data.decode()
check(r.status_code == 200 and "rounded to the nearest whole number" in h, "Finalisation tab says the figures are rounded")
import re as _re
body = h[h.index("Statement of Financial Position"):h.index("Company classification")]
check(not _re.search(r">\s*-?[\d,]+\.\d\d\s*<", body.split("Balance check")[0]), "no cents in the Statement of Financial Position on the Finalisation tab")

# ================================================================== 3. engine: tax value brought forward
rule = dict(method="WT_SL", wt=20.0, sia=25.0, sia_years=4, sia_first=None, cap=None, prorata=False, verified=True)
base = {"cost": 1000.0, "date_acquired": date(2023, 3, 1), "opening_tax_value": 700.0, "opening_as_at": date(2024, 12, 31)}
sim = firm_tax.simulate_asset(base, rule, date(2025, 12, 31), date(2025, 12, 31))
check(len(sim) == 1 and sim[0]["opening"] == 700 and sim[0]["additions"] == 0 and sim[0]["allowance"] == 200 and sim[0]["closing"] == 500,
      "wear and tear is worked from the entered tax value: 700 b/f, 200 allowance, 500 c/f")
none = dict(base, opening_tax_value=None, opening_as_at=None)
sim2 = firm_tax.simulate_asset(none, rule, date(2025, 12, 31), date(2025, 12, 31))
check(sim2[-1]["opening"] == 600 and sim2[-1]["closing"] == 400, "without a tax value b/f it is rebuilt from the cost (600 b/f after the 2023 and 2024 allowances)")
sia = dict(rule, method="SIA")
s3 = firm_tax.simulate_asset(base, sia, date(2025, 12, 31), date(2025, 12, 31))
check(s3[0]["allowance"] == 250 and s3[0]["closing"] == 450, "SIA years used are counted from the date acquired (2023, 2024 used; 2025 is the third)")
s4 = firm_tax.simulate_asset(dict(base, date_acquired=date(2021, 1, 1)), sia, date(2025, 12, 31), date(2025, 12, 31))
check(s4[0]["allowance"] == 0 and s4[0]["closing"] == 700, "SIA already used up for an older asset: nothing more")
s5 = firm_tax.simulate_asset(dict(base, date_acquired=None), rule, date(2025, 12, 31), date(2025, 12, 31))
check(len(s5) == 1 and s5[0]["opening"] == 700 and s5[0]["allowance"] == 200, "an undated opening asset with a tax value is still worked")
s6 = firm_tax.simulate_asset(dict(base, date_acquired=None), sia, date(2025, 12, 31), date(2025, 12, 31))
check(s6[0]["allowance"] == 0, "undated + SIA: taken as fully claimed")
s7 = firm_tax.simulate_asset(base, rule, date(2025, 12, 31), date(2024, 12, 31))
check(s7[-1]["closing"] == 700 and s7[-1]["allowance"] == 0, "asking for the opening date itself gives the entered balance")
s8 = firm_tax.simulate_asset(base, rule, date(2025, 12, 31), date(2026, 12, 31))
check([r["closing"] for r in s8] == [500, 300], "later years carry on from the entered balance: 500 then 300")
disposed = dict(base, disposed_on=date(2025, 6, 30), disposal_proceeds=800.0)
s9 = firm_tax.simulate_asset(disposed, rule, date(2025, 12, 31), date(2025, 12, 31))
check(s9[0]["recoupment"] == 100 and s9[0]["scrapping"] == 0 and s9[0]["closing"] == 0 and s9[0]["disposed"], "sale above the tax value b/f recoups the difference (800 - 700)")
s10 = firm_tax.simulate_asset(dict(disposed, disposal_proceeds=500.0), rule, date(2025, 12, 31), date(2025, 12, 31))
check(s10[0]["scrapping"] == 200 and s10[0]["recoupment"] == 0, "sale below the tax value b/f gives a scrapping allowance (700 - 500)")
# an addition in the year ignores any 'tax value b/f'
add = {"cost": 600.0, "date_acquired": date(2025, 4, 1), "opening_tax_value": 999.0, "opening_as_at": date(2024, 12, 31)}
s11 = firm_tax.simulate_asset(add, rule, date(2025, 12, 31), date(2025, 12, 31))
check(s11[0]["additions"] == 600 and s11[0]["opening"] == 0, "an asset bought in the year starts from its cost")

# ================================================================== 4. the pages
pid = {}
r = S.get(f"/engagements/{eid}?tab=asset_register"); h = r.data.decode()
check(r.status_code == 200 and "1. Opening balances as at 31 December 2024" in h, "an AUDIT engagement now has the Asset Register tab, with the 2024 opening balances card")
check("2. Additions during the year" in h and "3. Disposals during the year" in h and "4. Asset Register schedule" in h, "additions, disposals and the schedule cards show")
check(">Asset Register" in h and "tab=capital_allowances" in h, "the tab bar has Asset Register and Capital Allowances")
r = S.get(f"/engagements/{ids['sec']}?tab=asset_register")
check("Opening balances as at" not in r.data.decode(), "a Secretarial engagement does not get them")
r = S.get(f"/engagements/{ids['tax']}?tab=asset_register")
check(r.status_code == 200 and "Opening balances as at 31 December 2024" in r.data.decode(), "the Tax Advisory engagement too")

# add opening balances
B = f"/engagements/{eid}/assets"
S.post(f"{B}/opening/add", data={"asset_code": "V1", "description": "Toyota Hilux", "date_acquired": "2023-03-15", "cost": "1000", "opening_accumulated_depreciation": "400", "opening_tax_value": "700"})
S.post(f"{B}/opening/add", data={"asset_code": "C1", "description": "Old computer", "cost": "500", "opening_accumulated_depreciation": "500"})
r = S.post(f"{B}/opening/add", data={"description": "Too new", "date_acquired": "2025-02-01", "cost": "5"}, follow_redirects=True)
check("Use &#39;Additions&#39;" in r.data.decode() or "bought in the year" in r.data.decode(), "an opening balance dated inside the year is refused")
with app.app_context():
    n = PPEAsset.query.filter_by(engagement_id=eid).count()
    hilux = PPEAsset.query.filter_by(description="Toyota Hilux").first()
    check(n == 2 and hilux.opening_tax_value == 700 and hilux.cost == 1000, "two opening balances saved, tax value b/f kept")
    hid = hilux.id
    comp = PPEAsset.query.filter_by(description="Old computer").first(); cid = comp.id

# paste from Excel
paste = "Code\tDescription\tClass\tDate acquired\tCost\tAccumulated depreciation\tTax value\nF1\tOffice desks\tFurniture\t2022-06-30\t2,000.00\t(800.00)\t1,100\nF2\tBad line\tFurniture\tnot a date\t10\t0\t0\nF3\tGenerator\tPlant\t2024-01-10\t3000\t1000\t"
r = S.post(f"{B}/opening/paste", data={"paste": paste}, follow_redirects=True)
with app.app_context():
    desks = PPEAsset.query.filter_by(description="Office desks").first()
    gen = PPEAsset.query.filter_by(description="Generator").first()
    check(desks is not None and desks.cost == 2000 and desks.opening_accumulated_depreciation == -800 and desks.opening_tax_value == 1100, "pasted row read: separators, brackets, header skipped")
    check(gen is not None and gen.opening_tax_value is None and gen.date_acquired == date(2024, 1, 10), "a blank tax value stays blank (rebuild)")
    check(PPEAsset.query.filter_by(description="Bad line").count() == 0 and "could not be read" in r.data.decode(), "an unreadable date skips just that line and says so")
    check(PPEAssetClass.query.filter_by(engagement_id=eid).count() == 2 and desks.asset_class is not None, "classes in the paste are created")
    fid = desks.id; gid = gen.id

# save an opening balance keeps what it is not given
S.post(f"{B}/{hid}/opening/save", data={"cost": "1100", "opening_tax_value": "750"})
with app.app_context():
    a = PPEAsset.query.get(hid)
    check(a.cost == 1100 and a.opening_tax_value == 750 and a.description == "Toyota Hilux" and a.date_acquired == date(2023, 3, 15), "saving some fields leaves the others alone")
S.post(f"{B}/{hid}/opening/save", data={"cost": "1000", "opening_tax_value": "700"})

# additions
r = S.post(f"{B}/addition/add", data={"asset_code": "N1", "description": "New laptop", "date_acquired": "2025-04-10", "cost": "600", "current_year_depreciation": "100"})
r = S.post(f"{B}/addition/add", data={"description": "Last year's", "date_acquired": "2024-04-10", "cost": "60"}, follow_redirects=True)
check("Opening balances" in r.data.decode() and "acquired between" in r.data.decode(), "an addition dated outside the year is refused")
with app.app_context():
    lap = PPEAsset.query.filter_by(description="New laptop").first()
    check(lap is not None and lap.date_acquired == date(2025, 4, 10) and PPEAsset.query.filter_by(description="Last year's").count() == 0, "addition saved, the refused one not")
    lid = lap.id

# classes: give the four assets classes through the existing route
with app.app_context():
    veh = PPEAssetClass(engagement_id=eid, name="Motor vehicles", order=5); db.session.add(veh); db.session.commit()
    PPEAsset.query.get(hid).asset_class_id = veh.id
    PPEAsset.query.get(cid).asset_class_id = PPEAssetClass.query.filter_by(engagement_id=eid, name="Furniture").first().id
    PPEAsset.query.get(lid).asset_class_id = PPEAssetClass.query.filter_by(engagement_id=eid, name="Furniture").first().id
    db.session.commit()

# disposals
r = S.post(f"{B}/{hid}/dispose", data={"tax_disposed_on": "2024-06-01", "tax_disposal_proceeds": "500"}, follow_redirects=True)
check("disposal in this period" in r.data.decode(), "a disposal dated before the year is refused")
r = S.post(f"{B}/{hid}/dispose", data={"tax_disposed_on": "2025-09-30", "tax_disposal_proceeds": "800"}, follow_redirects=True)
with app.app_context():
    a = PPEAsset.query.get(hid)
    check(a.tax_disposed_on == date(2025, 9, 30) and a.tax_disposal_proceeds == 800 and a.disposal_cost == 1000, "disposal recorded with the whole cost removed")
    check(a.disposal_accumulated_depreciation == 400, "depreciation removed = the opening accumulated depreciation (400) + this year's charge (0)")
h = r.data.decode()
_k = h.find("Profit / (loss)"); _seg = h[_k:h.find("<h3", _k)] if _k >= 0 else ""
check("Profit / (loss)" in h and "(200.00)" not in _seg and "600.00" in _seg, "the disposals card shows the carrying amount (600) and a profit of 200 on proceeds of 800")

# the schedules
with app.app_context():
    eng = Engagement.query.get(eid)
    ctx = asched.tab_context(eng)
    mvx = ctx["as_movement"]; t = mvx["total"]
    check(len(ctx["as_openings"]) == 4 and len(ctx["as_additions"]) == 1 and len(ctx["as_disposals"]) == 1, "split: 4 opening balances, 1 addition, 1 disposal")
    check(t["opening_cost"] == 1000 + 500 + 2000 + 3000 and t["additions"] == 600 and t["disposals_cost"] == 1000, "register schedule: cost b/f, additions, disposals")
    check(abs(t["opening_nbv"] - ((1000 - 400) + 0 + (2000 + 800) + (3000 - 1000))) < 1e-9, "register schedule: NBV b/f from the entered balances")
    check(abs(t["opening_nbv"] + t["additions"] - (t["disposals_cost"] - t["disposals_acc_dep"]) - t["charge"] - t["closing_nbv"]) < 1e-9, "register schedule adds across")
    import engagement_tax
    ca = engagement_tax.capital_allowance_context(eng)
    res = ca["ca_result"]
    check(ca["ca_opening_as_at"] == date(2024, 12, 31), "opening date = 31 Dec 2024")
    rows = {r["description"]: r for r in res["rows"]}
    check(rows["Toyota Hilux"]["opening"] == 700 and rows["Toyota Hilux"]["recoupment"] == 100 and rows["Toyota Hilux"]["disposed_tv"] == 700, "Hilux: 700 b/f, sold for 800 -> recoupment 100")
    check(rows["New laptop"]["additions"] == 600 and rows["New laptop"]["opening"] == 0, "addition starts from cost")
    check(rows["Office desks"]["opening"] == 1100 and rows["Office desks"]["carried"], "pasted tax value used")
    tt = res["total"]
    check(abs(tt["opening"] + tt["additions"] - tt["disposed_tv"] - tt["allowance"] - tt["closing"]) < 1e-9, "tax schedule: b/f + additions - disposals - allowances = c/f")
    check(abs(sum(c["closing"] for c in res["by_class"]) - tt["closing"]) < 1e-9 and tt["proceeds"] == 800, "by-class totals agree with the total; proceeds 800")
    check([a.description for a in ca["ca_undated"]] == ["Old computer"], "only the asset with neither a date nor a tax value b/f is 'undated'")

r = S.get(f"/engagements/{eid}?tab=capital_allowances"); h = r.data.decode()
check(r.status_code == 200 and "By asset class" in h and "Tax value b/f" in h and "Opening position" in h, "Capital Allowances tab shows the by-class schedule and the opening note on an audit engagement")
check("31 December 2024" in h, "the opening note names 31 December 2024")

# the capital-allowances-tab disposal form also takes the asset out of the register
S.post(f"/engagements/{eid}/capital-allowances/assets/{cid}/disposal", data={"tax_disposed_on": "2025-11-01", "tax_disposal_proceeds": "0"})
with app.app_context():
    a = PPEAsset.query.get(cid)
    check(a.disposal_cost == 500 and a.disposal_accumulated_depreciation == 500, "a disposal dated on the Capital Allowances tab also fills the register's disposal cost/depreciation")
S.post(f"/engagements/{eid}/capital-allowances/assets/{cid}/disposal", data={"tax_disposed_on": ""})
with app.app_context():
    a = PPEAsset.query.get(cid)
    check(a.tax_disposed_on is None and a.disposal_cost == 0, "clearing it there clears the register too")

# saving the full-register form (no tax value field) keeps the tax value b/f
S.post(f"/ppe/assets/{hid}/update", data={"description": "Toyota Hilux", "cost": "1000", "asset_class_id": "", "date_acquired": "2023-03-15"})
with app.app_context():
    check(PPEAsset.query.get(hid).opening_tax_value == 700, "a form without the tax-value field leaves it alone")

# remove a disposal
S.post(f"{B}/{hid}/dispose", data={"clear": "1"})
with app.app_context():
    a = PPEAsset.query.get(hid)
    check(a.tax_disposed_on is None and a.disposal_cost == 0 and a.disposal_accumulated_depreciation == 0, "Remove clears the disposal")
S.post(f"{B}/{hid}/dispose", data={"tax_disposed_on": "2025-09-30", "tax_disposal_proceeds": "800"})

# access
O = login(User.query.filter_by(username="stf").first().id if False else ids["staff"])
r = A.post(f"/engagements/{ids['sec']}/assets/opening/add", data={"description": "x"})
check(r.status_code == 404, "no asset schedules on a Secretarial engagement")

# Excel
r = S.post(f"{B}/schedules/generate", follow_redirects=False)
check(r.status_code == 302, "Excel schedule generates and files")
with app.app_context():
    doc = Document.query.filter_by(engagement_id=eid, workpaper_kind="asset_schedules").first()
    check(doc is not None and doc.original_filename.endswith(".xlsx"), "filed as a workpaper")
    import openpyxl
    path = os.path.join(app.config["UPLOAD_FOLDER"], doc.stored_filename)
    wb = openpyxl.load_workbook(path)
    check(wb.sheetnames[:5] == ["Opening balances", "Additions", "Disposals", "Asset Register schedule", "Capital allowances"] and wb.sheetnames[5:] in ([], ["Trial balance reconciliation"]), "workbook has the five sheets (and the trial balance reconciliation when there is a trial balance): %s" % wb.sheetnames)
    ws = wb["Capital allowances"]
    vals = [[c.value for c in row] for row in ws.iter_rows(min_row=7, max_row=14)]
    flat = str(vals)
    check("Total" in flat and "Motor vehicles" in flat, "capital allowance sheet has classes and a total")
r = S.get(f"/engagements/{eid}?tab=asset_register"); h = r.data.decode()
check("Asset_Register_and_Capital_Allowances" in h or "asset_schedules" in h or "v1" in h, "the filed schedule is listed on the Asset Register tab")

# the FS pack uses the register in whole numbers
with app.app_context():
    eng = Engagement.query.get(eid)
    import engagements as E
    classes, assets_, cd, ad = E._ppe_plain_data(eid)
    pm, pt = E._ppe_movement_and_policy(cd, ad, eng.period_end)
    tbx = TrialBalance.query.filter_by(engagement_id=eid).first()
    st = fin.build_all_statements(tbx.lines, tbx.adjustments, reporting_framework=eng.reporting_framework, cash_flow_method=eng.cash_flow_method, ppe_movement=pm, ppe_policy_text=pt, whole_numbers=True)
    ppe_note = [n for n in st["notes"] if n.get("movement")]
    check(len(ppe_note) == 1 and all(abs(r["closing_nbv"] - round(r["closing_nbv"])) < 1e-9 for r in ppe_note[0]["movement"]), "the PPE note's movement schedule is in whole numbers")
    check("reconciliation" in ppe_note[0], "PPE note still reconciles the register to the trial balance")
    buf = wp.build_financial_statements_docx(eng, st, None)
    check(buf.getbuffer().nbytes > 5000, "the pack builds with a register-based PPE note")

# ================================================================== 5. import the opening balances from the trial balance
with app.app_context():
    e2 = Engagement(client_id=Client.query.first().id, title="Audit 2025 B", type="Audit", partner_id=Engagement.query.first().partner_id, manager_id=Engagement.query.first().manager_id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e2); db.session.commit(); e2id = e2.id
    tb2 = TrialBalance(engagement_id=e2id); db.session.add(tb2); db.session.flush()
    def tl(name, pd=0, pc=0, cat="ppe"):
        db.session.add(TrialBalanceLine(trial_balance_id=tb2.id, account_code=name[:3], account_name=name, fs_category=cat, current_debit=0, current_credit=0, prior_debit=pd, prior_credit=pc))
    tl("Motor vehicles - cost", pd=120000); tl("Motor vehicles - accumulated depreciation", pc=45000)
    tl("Office Equipment at cost", pd=30000.5); tl("Accumulated depreciation: office equipment", pc=12000.25)
    tl("Land", pd=500000); tl("Computers (credit by mistake)", pc=10)
    tl("Cash at bank", pd=999, cat="cash")
    db.session.commit()
    rows = asched.tb_opening_rows(Engagement.query.get(e2id))
    by = {r["class_name"]: r for r in rows}
    check(set(by) == {"Motor vehicles", "Office Equipment", "Land", "Computers (credit by mistake)"}, "classes read from the account names: %s" % sorted(by))
    check(by["Motor vehicles"]["cost"] == 120000 and by["Motor vehicles"]["acc_dep"] == 45000, "cost and accumulated depreciation separated (credit = depreciation)")
    check(by["Office Equipment"]["cost"] == 30000.5 and by["Office Equipment"]["acc_dep"] == 12000.25, "'at cost' and 'Accumulated depreciation: ...' both join Office Equipment")
    check(by["Land"]["cost"] == 500000 and by["Land"]["acc_dep"] == 0, "an account with no depreciation account is cost only")
    check("Cash at bank" not in str(rows), "only property, plant and equipment lines are read")
T2 = f"/engagements/{e2id}"
h = S.get(f"{T2}?tab=asset_register").data.decode()
check("Import from the trial balance" in h and "Motor vehicles" in h and "new row" in h, "the preview is shown on the Asset Register tab")
r = S.post(f"{T2}/assets/opening/import-tb", follow_redirects=True)
with app.app_context():
    a = PPEAsset.query.filter_by(engagement_id=e2id, description="Motor vehicles - opening balance per trial balance").first()
    check(a is not None and a.cost == 120000 and a.opening_accumulated_depreciation == 45000 and a.date_acquired is None and a.opening_tax_value is None and a.asset_class.name == "Motor vehicles", "imported as a class-level opening row, linked to its class, no date or tax value")
    n1 = PPEAsset.query.filter_by(engagement_id=e2id).count()
    a.opening_tax_value = 60000; a.description = a.description; db.session.commit()
S.post(f"{T2}/assets/opening/import-tb")
with app.app_context():
    check(PPEAsset.query.filter_by(engagement_id=e2id).count() == n1, "importing again does not duplicate")
    tbl = [l for l in TrialBalance.query.filter_by(engagement_id=e2id).first().lines if l.account_name == "Motor vehicles - cost"][0]
    tbl.prior_debit = 130000; db.session.commit()
S.post(f"{T2}/assets/opening/import-tb")
with app.app_context():
    a = PPEAsset.query.filter_by(engagement_id=e2id, description="Motor vehicles - opening balance per trial balance").first()
    check(a.cost == 130000 and a.opening_tax_value == 60000, "a re-import refreshes cost and depreciation and keeps the tax value typed in")
    ctx2 = asched.tab_context(Engagement.query.get(e2id))
    check(ctx2["as_tb_diff"] is not None and abs(ctx2["as_tb_diff"]) < 0.01, "after the import the register's opening NBV agrees with the trial balance")
# no trial balance -> nothing to import
r = S.post(f"/engagements/{eid}/assets/opening/import-tb", follow_redirects=True)
check("no property, plant and equipment balances" in r.data.decode() or "has no property" in r.data.decode() or r.status_code == 200, "an engagement without PPE in the prior column just says so")

# ================================================================== 6. importing a client's asset register
import asset_import as ai
import openpyxl
ps, pe = date(2025, 1, 1), date(2025, 12, 31)
check(ai.to_num("ZWG 5 000.50") == 5000.5 and ai.to_num("(1,234)") == -1234 and ai.to_num("-") is None and ai.to_num("US$1,200") == 1200 and ai.to_num("abc") is None, "figures read: currency, spaces, brackets, dash")
check(ai.to_date(45000) == date(2023, 3, 15) and ai.to_date("15 Mar 2023") == date(2023, 3, 15) and ai.to_date("15/03/2023") == date(2023, 3, 15) and ai.to_date(datetime_ := None) is None, "dates read: Excel serial, text, d/m/Y")
rowsA = [["ACME HOLDINGS - FIXED ASSET REGISTER", None, None], [None],
         ["Asset No", "Asset Description", "Date of Purchase", "Cost", "Accumulated Depreciation", "NBV", "Date Sold", "Sale Proceeds"],
         ["MOTOR VEHICLES"],
         ["V001", "Toyota Hilux ABC 1234", "15/03/2023", "$45,000.00", "18,000.00", "27,000.00", None, None],
         ["V002", "Ford Ranger", "2022-06-30", "(38,000)", "30,000", "8,000", "2025-08-15", "9,500"],
         ["Total Motor Vehicles", None, None, 83000, 48000, 35000],
         ["OFFICE EQUIPMENT"],
         ["E001", "Laptop Dell", date(2025, 4, 10), "1 200.00", "0", None, None, None],
         ["E002", "Old printer", None, "500", None, "100", None, None],
         ["Grand Total", None, None, 84700, 48500, None]]
rA = ai.parse_register(rowsA, ps, pe, default_date=pe)
itA = {i["code"]: i for i in rA["items"] if i["kind"] != "skip"}
check(rA["header_row"] == 2, "the header row is found below the title lines")
check(set(itA) == {"V001", "V002", "E001", "E002"} and sum(1 for i in rA["items"] if i["kind"] == "skip") == 2, "four assets read; the two total rows are left out")
check(itA["V001"]["class_name"] == "MOTOR VEHICLES" and itA["E001"]["class_name"] == "OFFICE EQUIPMENT", "class taken from the heading lines")
check(itA["V001"]["kind"] == "opening" and itA["V001"]["cost"] == 45000 and itA["V001"]["acc_dep"] == 18000 and itA["V001"]["date"] == date(2023, 3, 15), "opening asset: cost, accumulated depreciation, date")
check(itA["V002"]["cost"] == 38000 and itA["V002"]["disposal"]["date"] == date(2025, 8, 15) and itA["V002"]["disposal"]["proceeds"] == 9500, "brackets read as a positive cost; the sale is picked up with its date and proceeds")
check(itA["E001"]["kind"] == "addition" and itA["E001"]["cost"] == 1200 and itA["E001"]["acc_dep"] == 0, "an asset bought in the year is an addition")
check(itA["E002"]["kind"] == "opening" and itA["E002"]["acc_dep"] == 400 and any("cost less net book value" in n for n in itA["E002"]["notes"]), "accumulated depreciation worked out from cost less net book value when only NBV is given")
rowsB = [["Class", "Cost b/f", "Additions", "Disposals", "Cost c/f", "Acc Dep b/f", "Charge for the year", "Tax WDV"],
         ["Motor vehicles", 120000, 35000, 0, 155000, 45000, 24000, 60000], ["Computers", 20000, 0, 5000, 15000, 12000, 4000, 3000], ["Total", 140000, 35000, 5000, 170000, 57000, 28000, 63000]]
rB = ai.parse_register(rowsB, ps, pe, default_date=pe)
kinds = [(i["description"], i["kind"], i["cost"]) for i in rB["items"] if i["kind"] != "skip"]
check(("Motor vehicles", "opening", 120000) in kinds and ("Motor vehicles - additions", "addition", 35000) in kinds, "a movement register gives the opening line and the additions line")
check(("Computers", "opening", 15000) in kinds and ("Computers - sold", "opening", 5000) in kinds, "a part-disposal splits the line into what is held and what was sold")
held = [i for i in rB["items"] if i["description"] == "Computers"][0]; gone = [i for i in rB["items"] if i["description"] == "Computers - sold"][0]
check(abs(held["acc_dep"] + gone["acc_dep"] - 12000) < 1e-9 and abs(held["tax_value"] + gone["tax_value"] - 3000) < 1e-9 and gone["disposal"] is not None and held["disposal"] is None, "depreciation and tax value shared in proportion to cost; only the sold part is disposed of")
check(not any(i["description"] == "Total" and i["kind"] != "skip" for i in rB["items"]), "the Total row is not imported")

# the pages: upload -> preview -> import
with app.app_context():
    e3 = Engagement(client_id=Client.query.first().id, title="Audit 2025 C", type="Audit", partner_id=Engagement.query.first().partner_id, manager_id=Engagement.query.first().manager_id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e3); db.session.commit(); e3id = e3.id
    db.session.add(PPEAsset(engagement_id=e3id, description="Laptop Dell", asset_code="E001", cost=1200))
    db.session.commit()
wbk = openpyxl.Workbook(); sh = wbk.active; sh.title = "Register"
for r in rowsA:
    sh.append(r)
bio = io.BytesIO(); wbk.save(bio)
h = S.get(f"/engagements/{e3id}?tab=asset_register").data.decode()
check("Import the client&#39;s asset register" in h or "Import the client's asset register" in h, "the Asset Register tab offers the import")
r = S.post(f"/engagements/{e3id}/assets/import/upload", data={"file": (io.BytesIO(bio.getvalue()), "register.xlsx")}, content_type="multipart/form-data")
h = r.data.decode()
check(r.status_code == 200 and "Import asset register" in h and "Toyota Hilux ABC 1234" in h and "Already in the Asset Register." in h, "the preview lists what was found and flags the asset that is already in the register")
import re as _re2
token = _re2.search(r"/import/([0-9a-f]{32})/", h).group(1)
check("3 asset(s)" in h or "Import 3 asset" in h, "the button counts the 3 new assets")
r = S.post(f"/engagements/{e3id}/assets/import/upload", data={"file": (io.BytesIO(b"x"), "notes.pdf")}, content_type="multipart/form-data", follow_redirects=True)
check("Excel (.xlsx) or CSV" in r.data.decode(), "another file type is refused with a plain message")
# change the columns on the preview and read again
r = S.post(f"/engagements/{e3id}/assets/import/{token}/preview", data={"ext": ".xlsx", "filename": "register", "remap": "1", "header_row_no": "3", "col_code": "0", "col_description": "1", "col_date": "2", "col_cost": "3", "col_acc_dep": "4", "default_date": "2025-12-31"})
check(r.status_code == 200 and "Toyota Hilux ABC 1234" in r.data.decode(), "re-reading with the chosen columns works")
# import, leaving one row out
r = S.post(f"/engagements/{e3id}/assets/import/{token}/confirm", data={"ext": ".xlsx", "filename": "register", "header_row": "2", "default_date": "2025-12-31",
           "col_code": "0", "col_description": "1", "col_date": "2", "col_cost": "3", "col_acc_dep": "4", "col_nbv": "5", "col_disp_date": "6", "col_proceeds": "7", "skip_row": "10"}, follow_redirects=True)
with app.app_context():
    got = {a.asset_code: a for a in PPEAsset.query.filter_by(engagement_id=e3id).all()}
    check(set(got) == {"E001", "V001", "V002"}, "imported V001 and V002; E001 was already there; the unticked row E002 was left out: %s" % sorted(got))
    v2 = got["V002"]
    check(v2.tax_disposed_on == date(2025, 8, 15) and v2.tax_disposal_proceeds == 9500 and v2.disposal_cost == 38000 and v2.disposal_accumulated_depreciation == 30000, "V002 imported as sold: date, proceeds, cost and depreciation removed")
    check(got["V001"].asset_class is not None and got["V001"].asset_class.name == "MOTOR VEHICLES" and got["V001"].opening_accumulated_depreciation == 18000, "V001 imported with its class and accumulated depreciation")
    check(not [f for f in os.listdir(app.config["UPLOAD_FOLDER"]) if f.startswith(f"assetimp_{e3id}_")], "the temporary upload is removed")
r = S.post(f"/engagements/{e3id}/assets/import/{token}/confirm", data={"ext": ".xlsx", "filename": "register"}, follow_redirects=True)
check("expired" in r.data.decode(), "an old upload link says it has expired")
# CSV with semicolons and a different layout
csvtext = "Category;Item;Purchased;Price;Acc Dep\nFurniture;Desk;2021-05-01;1,500.00;600.00\nFurniture;Chair;2025-02-02;200;0\n"
r = S.post(f"/engagements/{e3id}/assets/import/upload", data={"file": (io.BytesIO(csvtext.encode()), "r.csv")}, content_type="multipart/form-data")
h = r.data.decode()
check("Desk" in h and "Chair" in h and "Opening" in h and "Addition" in h, "a semicolon CSV with other headings is read (Category, Item, Purchased, Price)")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
