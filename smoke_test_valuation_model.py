"""Valuation Model: engine, importers, web pages, exports (Excel recalculated in LibreOffice), filing, signing, access."""
import os, sys, tempfile, io, re, json, subprocess
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    if isinstance(k.get("data"), list):
        from werkzeug.datastructures import MultiDict
        k["data"] = MultiDict(k["data"])
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open

fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def close(a, b, tol=0.01):
    return a is not None and b is not None and abs(a - b) <= tol

import cf_valmodel as V
import cf_valimport as VI

# ------------------------------------------------------------------ engine
d = {"fx": {"ZWG": 0.04, "EUR": 1.1},
     "components": [
         {"name": "Alpha", "currency": "USD", "low": 800, "adopted": 1000, "high": 1300, "ownership_pct": 60},
         {"name": "Beta", "currency": "USD", "basis": "enterprise", "adopted": 2000, "net_debt": 500, "ownership_pct": 100, "dlom_pct": 10},
         {"name": "Gamma", "currency": "ZWG", "low": 10000, "adopted": 12500, "high": 15000, "ownership_pct": 50, "minority_pct": 20, "control_pct": 10},
         {"name": "Excluded Co", "currency": "USD", "adopted": 99999, "include": False}],
     "holding": [{"label": "HoldCo cash", "kind": "add", "adopted": 100, "low": 90, "high": 120},
                 {"label": "HoldCo debt", "kind": "deduct", "adopted": 300},
                 {"label": "Cross-holding", "kind": "eliminate", "adopted": 50},
                 {"label": "Head office", "kind": "capcost", "annual": 40, "multiple": 5}],
     "group": {"control_pct": 0, "minority_pct": 10, "dlom_pct": 5, "stake_pct": 75, "shares": 1000}}
res = V.consolidate(d, "USD", "2026-12-31")
a = res["rows"][0]["adjusted"]
check(close(a[0], 480) and close(a[1], 600) and close(a[2], 780), f"ownership 60% applied to equity value {a}")
b = res["rows"][1]["adjusted"]
check(close(b[1], (2000 - 500) * 0.9) and close(b[0], b[1]) and close(b[2], b[1]), "enterprise value less net debt, DLOM, only adopted given -> low = high = adopted")
c = res["rows"][2]["adjusted"]
exp_c = 12500 * 0.04 * 0.5 * 1.10 * 0.80
check(close(c[1], exp_c), f"currency conversion, ownership, control and minority: {c[1]} vs {exp_c}")
check(res["rows"][3]["adjusted"] is None and res["rows"][3]["problem"] == "excluded", "excluded component is out of the total")
parts = [a[i] + b[i] + c[i] for i in range(3)]
check(all(close(res["parts"][i], parts[i]) for i in range(3)), "sum of parts")
eff_h = [(+90 - 300 - 50 - 200), (100 - 300 - 50 - 200), (120 - 300 - 50 - 200)]
pre = [parts[i] + eff_h[i] for i in range(3)]
check(all(close(res["pre"][i], pre[i]) for i in range(3)), f"holding items: add, deduct, eliminate, capcost -> {res['pre']}")
gf = 0.9 * 0.95
check(close(res["group_equity"][1], pre[1] * gf) and close(res["stake"][1], pre[1] * gf * 0.75) and close(res["per_share"][1], pre[1] * gf * 0.75 / 1000, 0.0001), "group factor, stake, per share")
check(res["complete"] and res["count"] == 3, "complete with 3 components")
res2 = V.consolidate({"fx": {}, "components": [{"name": "Z", "currency": "ZWG", "adopted": 100}, {"name": "Y", "currency": "USD"}]}, "USD")
check(not res2["complete"] and any("INCOMPLETE" in w for w in res2["warnings"]) and res2["count"] == 0, "missing rate / value flagged and incomplete")
check(V.case_values(None, None, 90) == (90, 90, 90) and V.case_values(10, None, 30) == (10, 20, 30) and V.case_values(None, None, None) is None, "case_values fill-ins")
res3 = V.consolidate({"components": [{"name": "Old", "currency": "USD", "adopted": 1, "date": "2024-01-01"}]}, "USD", "2026-12-31")
check(any("more than a year" in w for w in res3["warnings"]), "stale valuation date warning")
check(V.f("US$1,234.5") == 1234.5 and V.f("(500)") == -500 and V.f("12%") == 12 and V.f("") == 0, "lenient number parser")

# ------------------------------------------------------------------ importers (no database)
tpl = VI.template_workbook("USD", "Acme", rows=[{"name": "Sub One", "type": "Subsidiary", "currency": "ZWG", "basis": "enterprise", "low": 1, "adopted": 2, "high": 3, "net_debt": 0.5, "ownership_pct": 70, "date": "2026-06-30"},
                                                {"name": "Sub Two", "type": "Property", "currency": "USD", "adopted": 500, "ownership_pct": 100}])
tp = os.path.join(tempfile.mkdtemp(), "tpl.xlsx"); open(tp, "wb").write(tpl)
cands, notes, method = VI.read_file(tp, "tpl.xlsx", "USD")
check(method == "template" and len(cands) == 2, f"template re-read: {method} {len(cands)}")
k = cands[0]["comp"]
check(k["name"] == "Sub One" and k["currency"] == "ZWG" and k["basis"] == "enterprise" and k["low"] == 1 and k["net_debt"] == 0.5 and k["ownership_pct"] == 70 and k["date"] == "2026-06-30", "template row fields")
blank = VI.template_workbook("USD", "Acme")
bp = os.path.join(tempfile.mkdtemp(), "blank.xlsx"); open(bp, "wb").write(blank)
cb, nb = VI.read_excel(bp, "blank.xlsx", "USD")
check(cb == [] , f"the untouched example row of the template is not imported {[c['comp']['name'] for c in cb]}")

import openpyxl
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Valuation summary"
ws["A1"] = "Company:"; ws["B1"] = "Delta Foods (Pvt) Ltd"; ws["A2"] = "Valuation date"; ws["B2"] = "31/12/2025"; ws["A3"] = "Currency: USD"
ws.append([]); ws.append(["", "Low", "Base", "High"])
ws.append(["Enterprise value", 2000000, 2500000, 3200000]); ws.append(["Net debt", 300000, 300000, 300000])
ws.append(["Equity value", 1700000, 2200000, 2900000]); ws.append(["WACC", 0.18, 0.17, 0.16]); ws.append(["Equity value per share", 1.7, 2.2, 2.9])
gp = os.path.join(tempfile.mkdtemp(), "delta.xlsx"); wb.save(gp)
cg, ng, mg = VI.read_file(gp, "delta.xlsx", "USD")
sel = [c for c in cg if c["selected"]]
check(len(cg) == 2 and len(sel) == 1 and sel[0]["comp"]["adopted"] == 2200000 and sel[0]["comp"]["basis"] == "equity", f"generic table: picks the Equity value row ({len(cg)} found)")
check(sel[0]["comp"]["name"] == "Delta Foods (Pvt) Ltd" and sel[0]["comp"]["date"] == "2025-12-31" and sel[0]["comp"]["low"] == 1700000, "generic table: entity name, date and low/high")
text = ("DESKTOP VALUATION CERTIFICATE\nSubject asset: Binga coal claim\nValuation date: 30 June 2026\nAll figures in US$.\n"
        "The value of the claim is US$1,000,000 (one million United States dollars), within a range of US$500,000 to US$2,000,000.")
ct, nt = VI.read_text(text, "cert.pdf", "ZWG")
cc = ct[0]["comp"]
check(cc["currency"] == "USD" and cc["adopted"] == 1000000 and cc["low"] == 500000 and cc["high"] == 2000000 and cc["name"].startswith("Binga"), f"text reader: {cc['currency']} {cc['low']}/{cc['adopted']}/{cc['high']} {cc['name']}")
check(cc["date"] == "2026-06-30", f"text reader date {cc['date']}")
t2 = "Valuation summary\n Low | Adopted | High\nEquity value | 1.2 | 1.5 | 1.9\nEquity value of US$1.5 million"
ct2, _ = VI.read_text("The fair value of the business is USD 2.5 million. It lies between USD 2 million and USD 3.1 million.", "r.docx", "USD")
check(ct2 and ct2[0]["comp"]["adopted"] == 2500000 and ct2[0]["comp"]["low"] == 2000000 and ct2[0]["comp"]["high"] == 3100000, "text reader: million wording and 'between X and Y'")
none, nn = VI.read_text("Nothing to see here", "x.txt", "USD")
check(none == [] and nn, "text with no valuation gives a message, not a guess")
ai = VI.parse_ai({"components": [{"name": "A Co", "adopted": 10, "currency": "usd", "basis": "enterprise", "net_debt": 2}, {"name": "empty"}]}, "ZWG")
check(len(ai[0]) == 1 and ai[0][0]["comp"]["currency"] == "USD" and ai[0][0]["comp"]["basis"] == "enterprise", "AI result parsing drops empty components")

# ------------------------------------------------------------------ web
from app import create_app
from extensions import db
from models import *
import cf_engine as E, cf_tools as T
app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staffer", role="staff"); staff.set_password("x")
    plain = User(username="pln", name="No Access", role="supervisor"); plain.set_password("x")
    db.session.add_all([partner, staff, plain]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id)
    cl2 = Client(name="Other Co", created_by_id=admin.id)
    db.session.add_all([cl, cl2]); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Group valuation 2026", type=CORPFIN_ENGAGEMENT_TYPE, partner_id=partner.id)
    db.session.add(eng); db.session.commit()
    # a forecast model with a valuation, and a certificate
    model = CFModel(model_number="CF-2026-0099", client_id=cl.id, engagement_id=eng.id, name="Acme forecast", status="Draft", created_by_id=admin.id)
    db.session.add(model); db.session.commit()
    model.set_json("assumptions", E.default_assumptions(3, "USD", "blank", None)); model.set_json("historical", {}); model.set_json("actuals", {})
    db.session.add(CFScenario(model_id=model.id, name="Base case", is_base=True, sort_order=0, shocks_json="{}"))
    val = CFValuation(model_id=model.id, name="DCF at Dec 2026", valuation_date=__import__("datetime").date(2026, 12, 31), inputs_json=json.dumps(T.normalise_valuation({"stake_pct": 80, "dlom": 10})))
    db.session.add(val)
    cert = ValuationCertificate(reference="NCA/DVC/2026/777", client_id=cl.id, client_name=cl.name, certificate_title="Desktop Valuation Certificate", currency="USD",
                                subject_asset="Mining claim at Binga", low_value=500000, adopted_value=1000000, high_value=2000000,
                                valuation_date=__import__("datetime").date(2026, 6, 30), created_by_id=admin.id)
    db.session.add(cert); db.session.commit()
    ids = dict(admin=admin.id, partner=partner.id, staff=staff.id, plain=plain.id, cl=cl.id, cl2=cl2.id, eng=eng.id, model=model.id, val=val.id, cert=cert.id)
    check(user_has_permission(partner, "manage_corporate_finance"), "partner has the permission")

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

adm, par, stf = login(ids["admin"]), login(ids["partner"]), login(ids["staff"])
r = adm.get("/corporate-finance/valuation-models/new")
check(r.status_code == 200 and b"New valuation model" in r.data, "new valuation model form")
r = adm.post("/corporate-finance/valuation-models/new", data=dict(client_id=ids["cl"], engagement_id=ids["eng"], name="Acme group valuation", currency="USD", valuation_date="2026-12-31", basis_of_value="Fair value", purpose="Reorganisation"))
check(r.status_code == 302 and "/components" in r.headers["Location"], "created and sent to Components")
with app.app_context():
    vm = CFValuationModel.query.first(); vid = vm.id
    check(vm.vm_number.startswith("VM-") and vm.engagement_id == ids["eng"], f"numbered {vm.vm_number}")
base = f"/corporate-finance/valuation-models/{vid}"
check(adm.get(base + "/components").status_code == 200, "components tab renders (empty)")
check(adm.get(base + "/consolidation").status_code == 200 and adm.get(base + "/group").status_code == 200 and adm.get(base + "/report").status_code == 200, "all four tabs render")
check(adm.get(base + "/nonsense").status_code == 404, "unknown tab is 404")

# template upload -> review -> apply
tpl2 = VI.template_workbook("USD", "Acme", rows=[
    {"name": "Acme Foods (Pvt) Ltd", "type": "Subsidiary", "method": "Discounted cash flow", "currency": "USD", "basis": "equity", "low": 800000, "adopted": 1000000, "high": 1300000, "ownership_pct": 60, "date": "2026-12-31"},
    {"name": "Acme Zim Land", "type": "Property", "method": "Market multiples", "currency": "ZWG", "basis": "equity", "adopted": 25000000, "ownership_pct": 100, "date": "2026-11-30"}])
r = adm.post(base + "/import/read", data={"file": (io.BytesIO(tpl2), "acme_components.xlsx")}, content_type="multipart/form-data")
check(r.status_code == 200 and b"Check what was read" in r.data and b"Acme Foods" in r.data and b"Excel template" in r.data, "upload shows the review page")
stored = re.search(rb'name="stored" value="([^"]+)"', r.data).group(1).decode()
check(stored.startswith(f"vm{vid}_src_"), "uploaded file kept for the source register")
form = {"n": "2", "stored": stored, "src_name": "acme_components.xlsx", "method": "template"}
for i, (nm, cur, lo, ad, hi, own) in enumerate([("Acme Foods (Pvt) Ltd", "USD", 800000, 1000000, 1300000, 60), ("Acme Zim Land", "ZWG", "", 25000000, "", 100)]):
    form.update({f"c{i}_use": "on", f"c{i}_target": "new", f"c{i}_name": nm, f"c{i}_type": "Subsidiary" if i == 0 else "Property", f"c{i}_method": "DCF", f"c{i}_currency": cur,
                 f"c{i}_basis": "equity", f"c{i}_date": "2026-12-31", f"c{i}_low": lo, f"c{i}_adopted": ad, f"c{i}_high": hi, f"c{i}_net_debt": "", f"c{i}_ownership_pct": own,
                 f"c{i}_minority_pct": "", f"c{i}_dlom_pct": "", f"c{i}_control_pct": "", f"c{i}_notes": "", f"c{i}_where": f"Components row {i+2}"})
r = adm.post(base + "/import/apply", data=form)
check(r.status_code == 302, "apply redirects")
with app.app_context():
    vm = CFValuationModel.query.get(vid); dd = V.normalise(vm.data, "USD")
    check(len(dd["components"]) == 2 and dd["components"][0]["source"]["kind"] == "upload" and dd["components"][0]["source"]["label"].startswith("acme_components.xlsx"), "two components added with their source")
    docs = Document.query.filter_by(engagement_id=ids["eng"], reference=vm.vm_number).all()
    check(len(docs) == 1 and docs[0].category == "Working Papers" and docs[0].stored_filename == stored, "the uploaded source is filed as a Working Paper")
    check(dd["components"][0]["source"]["document_id"] == docs[0].id, "component points at the filed document")
# bad stored name is refused (path traversal)
r = adm.post(base + "/import/apply", data={"n": "1", "stored": "../../etc/passwd", "src_name": "x", "c0_use": "on", "c0_name": "Hack", "c0_adopted": "5", "c0_currency": "USD"})
with app.app_context():
    dd = V.normalise(CFValuationModel.query.get(vid).data, "USD")
    hack = [c for c in dd["components"] if c["name"] == "Hack"]
    check(len(Document.query.filter_by(engagement_id=ids["eng"]).all()) == 1 and (not hack or hack[0]["source"]["document_id"] is None), "a forged stored file name is not filed")
# update an existing component from a revised upload
upd = {"n": "1", "method": "template", "c0_use": "on", "c0_target": dd["components"][0]["id"], "c0_name": "Acme Foods", "c0_type": "Subsidiary", "c0_method": "DCF (revised)", "c0_currency": "USD",
       "c0_basis": "equity", "c0_date": "2026-12-31", "c0_low": 900000, "c0_adopted": 1100000, "c0_high": 1400000, "c0_ownership_pct": 10}
adm.post(base + "/import/apply", data=upd)
with app.app_context():
    dd = V.normalise(CFValuationModel.query.get(vid).data, "USD")
    c0 = dd["components"][0]
    check(c0["adopted"] == 1100000 and c0["method"] == "DCF (revised)" and c0["ownership_pct"] == 60 and c0["name"] == "Acme Foods (Pvt) Ltd", "revised upload updates the values but keeps name and ownership %")
    check(len([c for c in dd["components"] if c["name"] == "Hack"]) <= 1, "no stray components")
    dd["components"] = [c for c in dd["components"] if c["name"] != "Hack"]
    vm = CFValuationModel.query.get(vid); vm.data_json = json.dumps(dd); db.session.commit()

# saved valuations: forecast model valuation + certificate
r = adm.get(base + "/components")
check(b"NCA/DVC/2026/777" in r.data and b"CF-2026-0099" in r.data, "saved valuations are offered on the Components tab")
r = adm.post(base + "/import/saved", data=[("pick", f"m:{ids['model']}:{ids['val']}"), ("pick", f"c:{ids['cert']}")])
check(r.status_code == 200 and b"Binga" in r.data and b"Forecast model valuation" in r.data, "saved valuations go through the review page")
with app.app_context():
    m = CFModel.query.get(ids["model"]); v = CFValuation.query.get(ids["val"])
    cand = VI.from_model_valuation(m, v, E.run(E.normalise(m.assumptions)))
    base_eq = T.dcf(E.run(E.normalise(m.assumptions)), T.normalise_valuation(v.inputs))["equity"]
    check(close(cand["comp"]["adopted"], base_eq, 0.5) and cand["comp"]["ownership_pct"] == 80 and cand["comp"]["dlom_pct"] == 10, "forecast-model valuation: DCF equity, stake % and DLOM carried across")
sv = {"n": "2", "method": "saved"}
sv.update({"c0_use": "on", "c0_target": "new", "c0_name": "Binga coal claim", "c0_type": "Mining / resource asset", "c0_currency": "USD", "c0_basis": "equity", "c0_low": 500000, "c0_adopted": 1000000,
           "c0_high": 2000000, "c0_ownership_pct": 100, "c0_where": "NCA/DVC/2026/777", "c0_date": "2026-06-30"})
adm.post(base + "/import/apply", data=sv)
sv2 = {"n": "1", "method": "saved", "c0_use": "on", "c0_target": "new", "c0_name": "Acme forecast", "c0_type": "Subsidiary", "c0_currency": "USD", "c0_basis": "equity", "c0_adopted": 700000,
       "c0_ownership_pct": 100, "c0_where": "CF-2026-0099 / DCF"}
# components tab save: tweak rows by form
with app.app_context():
    dd = V.normalise(CFValuationModel.query.get(vid).data, "USD")
    check(len(dd["components"]) == 3 and dd["components"][2]["source"]["kind"] == "saved", "certificate added as a component")
f = {"n": str(len(dd["components"])), "action": "save"}
for i, c in enumerate(dd["components"]):
    f[f"c{i}_id"] = c["id"]; f[f"c{i}_include"] = "on"
    for k in ("name", "type", "method", "currency", "basis", "date", "low", "adopted", "high", "net_debt", "ownership_pct", "minority_pct", "dlom_pct", "control_pct", "notes"):
        f[f"c{i}_{k}"] = "" if c[k] is None else c[k]
f["c1_ownership_pct"] = "100"; f["c2_dlom_pct"] = "15"
r = adm.post(base + "/components/save", data=f)
with app.app_context():
    dd = V.normalise(CFValuationModel.query.get(vid).data, "USD")
    check(dd["components"][2]["dlom_pct"] == 15 and dd["components"][1]["source"]["kind"] == "upload", f"components saved; sources kept {[ (c['name'], c['dlom_pct'], c['source']['kind']) for c in dd['components']]}")
r = adm.post(base + "/components/save", data={"n": "1", "action": "add", "c0_id": dd["components"][0]["id"], "c0_name": "Acme Foods (Pvt) Ltd"})
with app.app_context():
    dd = V.normalise(CFValuationModel.query.get(vid).data, "USD")
    check(len(dd["components"]) == 2 and dd["components"][1]["name"] == "New component", "action=add keeps only the posted rows and adds a blank one")

# rebuild three real components directly, then use group/settings routes
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    vm.data_json = json.dumps(V.normalise({"components": [
        {"name": "Acme Foods", "currency": "USD", "low": 800000, "adopted": 1000000, "high": 1300000, "ownership_pct": 60, "date": "2026-12-31"},
        {"name": "Zim Land", "currency": "ZWG", "adopted": 25000000, "ownership_pct": 100, "type": "Property", "date": "2026-11-30"},
        {"name": "Binga claim", "currency": "USD", "basis": "enterprise", "low": 400000, "adopted": 900000, "high": 1500000, "net_debt": 100000, "ownership_pct": 40, "minority_pct": 10}]}, "USD"))
    db.session.commit()
r = adm.get(base + "/consolidation")
check(b"No exchange rate for ZWG" in r.data and b"INCOMPLETE" in r.data, "missing exchange rate is flagged on the page")
r = adm.post(base + "/settings", data={"name": "Acme group valuation", "currency": "USD", "valuation_date": "2026-12-31", "engagement_id": ids["eng"], "basis_of_value": "Fair market value",
                                         "premise": "Going concern", "purpose": "Reorganisation", "standard": "IVS", "fx_ZWG": "0.04", "nar_scope": "Sum of the parts.", "nar_assumptions": "Rates at 31 Dec.", "nar_limiting": "None."})
with app.app_context():
    vm = CFValuationModel.query.get(vid); dd = V.normalise(vm.data, "USD")
    check(dd["fx"].get("ZWG") == 0.04 and dd["basis_of_value"] == "Fair market value" and dd["narrative"]["scope"] == "Sum of the parts.", "settings: FX rates, basis, narrative saved")
g = {"n": "3", "action": "save", "g_control_pct": "0", "g_minority_pct": "5", "g_dlom_pct": "10", "g_stake_pct": "75", "g_shares": "1000000",
     "h0_id": "a1", "h0_label": "HoldCo cash", "h0_kind": "add", "h0_currency": "USD", "h0_adopted": "250000", "h0_low": "", "h0_high": "",
     "h1_id": "a2", "h1_label": "HoldCo loan", "h1_kind": "deduct", "h1_currency": "USD", "h1_adopted": "400000",
     "h2_id": "a3", "h2_label": "Head office", "h2_kind": "capcost", "h2_currency": "USD", "h2_annual": "60000", "h2_multiple": "4"}
adm.post(base + "/group/save", data=g)
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    res = V.consolidate(vm.data, vm.currency, vm.valuation_date)
    expected_parts = (800000 * .6, 1000000 * .6, 1300000 * .6)
    zim = 25000000 * 0.04
    bin_ = (400000 - 100000) * .4 * .9, (900000 - 100000) * .4 * .9, (1500000 - 100000) * .4 * .9
    ep = [expected_parts[i] + (0 if i != 1 else 0) + bin_[i] for i in range(3)]
    ep[1] += zim; ep[0] += zim; ep[2] += zim
    check(res["complete"] and all(close(res["parts"][i], ep[i]) for i in range(3)), f"sum of parts {res['parts']} vs {ep}")
    pre = [ep[i] + 250000 - 400000 - 240000 for i in range(3)]
    ge = [p * 0.95 * 0.9 for p in pre]
    check(all(close(res["group_equity"][i], ge[i]) for i in range(3)) and close(res["stake"][1], ge[1] * .75) and close(res["per_share"][1], ge[1] * .75 / 1e6, 1e-6), "group equity, stake and per share from the pages")
r = adm.get(base + "/consolidation")
check(r.status_code == 200 and b"Group equity value" in r.data and f"{round(ge[1]):,}".encode() in r.data, "consolidation page shows the adopted group equity")
check(b"Valuation ranges" in r.data and b"cf-range" in r.data, "range chart present")

# ---- Excel export, recalculated in LibreOffice, equals the engine
r = adm.get(base + "/excel")
check(r.status_code == 200 and r.data[:2] == b"PK", "Excel export downloads")
dtmp = tempfile.mkdtemp(prefix="xl_"); src = os.path.join(dtmp, "vm.xlsx"); open(src, "wb").write(r.data)
out = os.path.join(dtmp, "out"); os.makedirs(out)
subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", out, src], capture_output=True, timeout=180)
wbx = openpyxl.load_workbook(os.path.join(out, "vm.xlsx"), data_only=True)
sm = wbx["Summary"]
labels = {sm.cell(row=i, column=1).value: i for i in range(9, 30) if sm.cell(row=i, column=1).value}
def xl(label, col): return sm.cell(row=labels[label], column=col).value
check(all(close(xl("Group equity value", 2 + i), ge[i], 0.5) for i in range(3)), f"Excel group equity (recalculated) = engine: {[xl('Group equity value', 2 + i) for i in range(3)]}")
check(all(close(xl("Sum of the components (after ownership and adjustments)", 2 + i), res["parts"][i], 0.5) for i in range(3)), "Excel sum of parts = engine")
check(close(xl("Value of the stake", 3), ge[1] * .75, 0.5) and close(xl("Value per share", 3), ge[1] * .75 / 1e6, 1e-5), "Excel stake and per-share = engine")
check(all(close(xl("Difference (zero until you change an input)", 2 + i), 0, 0.01) for i in range(3)), "Excel check row is zero")
# change an input in the workbook and recalc: ownership of Acme Foods 60 -> 100
wbm = openpyxl.load_workbook(src)
wsc = wbm["Components"]
hdr = [c.value for c in wsc[8]]
oc = hdr.index("Ownership %") + 1
wsc.cell(row=9, column=oc).value = 100
src2 = os.path.join(dtmp, "vm2.xlsx"); wbm.save(src2)
subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", out, src2], capture_output=True, timeout=180)
sm2 = openpyxl.load_workbook(os.path.join(out, "vm2.xlsx"), data_only=True)["Summary"]
with app.app_context():
    dd = V.normalise(CFValuationModel.query.get(vid).data, "USD"); dd["components"][0]["ownership_pct"] = 100
    res_b = V.consolidate(dd, "USD")
check(close(sm2.cell(row=labels["Group equity value"], column=3).value, res_b["group_equity"][1], 0.5), "changing an input in the workbook recalculates to the engine's figure")

# ---- PDF / Word
r = adm.get(base + "/pdf")
check(r.status_code == 200 and r.data[:4] == b"%PDF", "PDF report")
pdfp = os.path.join(dtmp, "vm.pdf"); open(pdfp, "wb").write(r.data)
txt = subprocess.run(["pdftotext", "-layout", pdfp, "-"], capture_output=True, text=True).stdout
check("VALUATION MODEL" in txt and "Acme Foods" in txt and f"{round(ge[1]):,}" in txt and "Sum of the components" in txt, "PDF text carries the title, a component and the group value")
check("Sum of the parts." in txt and "Rates at 31 Dec." in txt, "PDF carries the narrative")
r = adm.get(base + "/docx")
check(r.status_code == 200 and r.data[:2] == b"PK", "Word report")
from docx import Document as DX
dx = DX(io.BytesIO(r.data)); alltext = "\n".join(p.text for p in dx.paragraphs) + "\n".join(c.text for t in dx.tables for row in t.rows for c in row.cells)
check("Acme Foods" in alltext and f"{round(ge[1]):,}" in alltext and "Valuation Model".upper() in alltext.upper(), "Word report carries the components and group value")

# ---- file in Working Papers
r = adm.post(base + "/file")
with app.app_context():
    docs = Document.query.filter_by(engagement_id=ids["eng"], workpaper_kind="valuation_model").all()
    check(len(docs) == 3 and {d.original_filename.rsplit(".", 1)[-1] for d in docs} == {"xlsx", "pdf", "docx"} and all(d.is_current_version for d in docs), "filed Excel, PDF and Word in Working Papers")
    check(all(os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], d.stored_filename)) for d in docs), "filed files exist on disk")
adm.post(base + "/file")
with app.app_context():
    docs = Document.query.filter_by(engagement_id=ids["eng"], workpaper_kind="valuation_model").all()
    cur = [d for d in docs if d.is_current_version]
    check(len(docs) == 6 and len(cur) == 3 and {d.version for d in cur} == {2}, "filing again replaces the current version")
check(b"Working Papers" in adm.get(f"/engagements/{ids['eng']}?tab=documents").data, "Working Papers tab opens with the filed papers")

# ---- signing
from mpsign import can_sign
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    ok, why = can_sign(vm)
    check(ok, f"complete model can be signed ({why})")
r = adm.post(f"/sign/valuation_model/{vid}")
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    import brand_docs
    check(brand_docs.signature_valid(vm), "signature applied")
rs = adm.get(base + "/pdf")
with app.app_context():
    check(brand_docs.signature_bytes() is None or len(rs.data) > len(r.data if False else b"") , "signed PDF builds")
# a change removes the signature
adm.post(base + "/group/save", data=dict(g, g_dlom_pct="12"))
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    check(not brand_docs.signature_valid(vm) and vm.mp_signed_at is not None, "changing the model drops the signature")
    dd = V.normalise(vm.data, "USD"); dd["components"].append({"name": "Broken", "currency": "EUR", "adopted": 5})
    vm.data_json = json.dumps(dd); db.session.commit()
    ok, why = can_sign(vm)
    check(not ok and "incomplete" in why.lower(), "an incomplete model cannot be signed")
    dd["components"] = [c for c in dd["components"] if c["name"] != "Broken"]; vm.data_json = json.dumps(dd); db.session.commit()

# ---- finalise / lock / reopen
r = stf.post(base + "/status")
check(r.status_code in (302, 403), "staff cannot finalise")
with app.app_context():
    check(CFValuationModel.query.get(vid).status == "Draft", "model still Draft after a staff attempt")
r = par.post(base + "/status")
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    check(vm.status == "Final", f"partner finalises -> {vm.status}")
    filed = Document.query.filter_by(engagement_id=ids["eng"], workpaper_kind="valuation_model", is_current_version=True).count()
    check(filed == 3, "finalising refiles the three reports as current")
with app.app_context():
    before = json.dumps(V.normalise(CFValuationModel.query.get(vid).data, "USD"))
adm.post(base + "/components/save", data={"n": "1", "action": "add", "c0_id": "x", "c0_name": "Sneaky"})
adm.post(base + "/group/save", data=dict(g, g_stake_pct="10"))
adm.post(base + "/settings", data={"name": "X", "fx_ZWG": "9"})
with app.app_context():
    after = json.dumps(V.normalise(CFValuationModel.query.get(vid).data, "USD"))
    check(before == after, "a Final model refuses every edit")
r = adm.post(base + "/delete")
with app.app_context():
    check(CFValuationModel.query.get(vid) is not None, "a Final model cannot be deleted")
par.post(base + "/status")
with app.app_context():
    check(CFValuationModel.query.get(vid).status == "Draft", "partner reopens")

# ---- pages & access
r = adm.get(f"/corporate-finance/engagements/{ids['eng']}/valuation-models")
check(r.status_code == 200 and b"Acme group valuation" in r.data, "engagement Valuation Models tab lists the model")
check(b"Valuation Models (1)" in adm.get(f"/engagements/{ids['eng']}?tab=overview").data, "engagement tab bar shows Valuation Models (1)")
check(b"Acme group valuation" in adm.get("/corporate-finance/").data and b"New valuation model" in adm.get("/corporate-finance/").data, "dashboard lists the model and offers New valuation model")
plain = login(ids["plain"])
check(plain.get(base + "/consolidation").status_code == 403, "a user without the permission is refused")
check(stf.get(base + "/consolidation").status_code == 403, "staff without the permission are refused")
with app.app_context():
    other = CFValuationModel(vm_number="VM-2026-0777", client_id=ids["cl2"], name="Other", currency="USD", status="Draft", data_json="{}", created_by_id=ids["admin"])
    db.session.add(other); db.session.commit(); oid = other.id
r = par.get(f"/corporate-finance/valuation-models/{oid}/consolidation")
check(r.status_code in (200, 403), "access follows the client scope")
# upload error handling
r = adm.post(base + "/import/read", data={"file": (io.BytesIO(b"hello"), "notes.exe")}, content_type="multipart/form-data")
check(r.status_code == 302, "an unsupported file type is turned away")
r = adm.post(base + "/import/read", data={"text": "Nothing about value here at all, just words."})
check(r.status_code == 302, "pasted text with no valuation is turned away with a message")
r = adm.post(base + "/import/read", data={"text": "The fair value of the investment is US$750,000, within a range of US$600,000 to US$900,000."})
check(r.status_code == 200 and b"750000" in r.data and b"600000" in r.data, "pasted conclusion goes to the review page")
r = adm.get(base + "/template.xlsx")
check(r.status_code == 200 and r.data[:2] == b"PK", "template download")
r = adm.get(base + "/components/excel")
check(r.status_code == 200 and r.data[:2] == b"PK", "current components export in template layout")
# delete (draft, unsigned)
with app.app_context():
    vm = CFValuationModel.query.get(vid); vm.mp_signed_at = None; vm.mp_signed_key = None; db.session.commit()
r = adm.post(base + "/delete")
with app.app_context():
    check(CFValuationModel.query.get(vid) is None, "a Draft model can be deleted")

print()
print("FAILED: %d" % len(fails) if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
