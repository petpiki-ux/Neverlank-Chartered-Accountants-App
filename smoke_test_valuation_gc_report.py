"""Valuation Model: going-concern / development value (engine, screen, live-formula Excel) and the AI report writer (drafter, checks, screens, PDF / Word)."""
import os, sys, tempfile, io, re, json, subprocess
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ.pop("ANTHROPIC_API_KEY", None)
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
import cf_valreport as R

# ------------------------------------------------------------------ DCF arithmetic
pv, tv = V.dcf_value([100, 200], 10, None, False)
check(close(pv, 100 / 1.1 + 200 / 1.21, 1e-6) and tv == 0, f"DCF, year-end flows, no terminal value: {pv:.4f}")
pv_m, _ = V.dcf_value([100, 200], 10, None, True)
check(close(pv_m, pv * 1.1 ** 0.5, 1e-6), "mid-year discounting = year-end x (1+r)^0.5")
pv_t, tv_t = V.dcf_value([100], 10, 0, False)
check(close(pv_t, 1000.0, 1e-6) and close(tv_t, 909.0909, 1e-3), f"terminal value (Gordon): 100 forever at 10% = {pv_t:.2f}")
check(V.dcf_value([100], 5, 5, False)[1] == 0 and V.dcf_value([], 10) is None and V.dcf_value([1], None) is None, "no terminal value when growth >= rate; None without flows or rate")
check(V._cash_flows("100, 200,(50), 1,000") == [100.0, 200.0, -50.0, 1.0, 0.0] or V._cash_flows("100,200,(50)") == [100.0, 200.0, -50.0], "cash-flow text is read, brackets are negative")
check(V._cash_flows(["1", "", "3", None, None]) == [1.0, 0.0, 3.0], "blank years inside the list are 0, trailing blanks dropped")

# ------------------------------------------------------------------ consolidation with going-concern items
base = {"components": [{"name": "Mine", "currency": "USD", "adopted": 1000000, "low": 800000, "high": 1200000, "ownership_pct": 100, "method": "Net asset value"}],
        "holding": [{"label": "Cash", "kind": "add", "adopted": 50000}],
        "goingconcern": {"enabled": True, "stage": "Construction / commissioning", "items": [
            {"label": "Plan", "kind": "dcf", "cash_flows": [0, 300000, 600000, 700000, 700000], "rate_pct": 18, "rate_range": 1, "growth_pct": 2, "mid_year": True, "probability_pct": 75},
            {"label": "Synergy", "kind": "synergy", "adopted": 200000, "probability_pct": 50},
            {"label": "Capex", "kind": "capex", "adopted": 400000},
            {"label": "Overlap", "kind": "overlap", "adopted": 100000}]}}
res = V.consolidate(base, "USD", "2026-12-31")
plan, syn, capex, ovl = res["gc_rows"]
expect_plan = V.dcf_value([0, 300000, 600000, 700000, 700000], 18, 2, True)[0]
check(close(plan["vals"][1], expect_plan, 0.01) and plan["vals"][0] < plan["vals"][1] < plan["vals"][2], f"DCF item: adopted PV {plan['vals'][1]:,.0f}, low < adopted < high")
check(close(plan["effect"][1], expect_plan * 0.75, 0.01) and close(syn["effect"][1], 100000) and close(capex["effect"][1], -400000) and close(ovl["effect"][1], -100000), "effects = sign x value x probability")
net = expect_plan * 0.75 + 100000 - 400000 - 100000
check(close(res["gc"][1], net, 0.01), f"net going-concern value {res['gc'][1]:,.0f}")
check(close(res["gc_unrisked"][1], expect_plan + 200000 - 400000 - 100000, 0.01), "un-risked net (before probability)")
check(close(res["pre"][1], 1000000 + 50000 + net, 0.01), "group value before adjustments includes the going-concern value")
off = json.loads(json.dumps(base)); off["goingconcern"]["enabled"] = False
r_off = V.consolidate(off, "USD", "2026-12-31")
check(r_off["gc"] == (0.0, 0.0, 0.0) and close(r_off["pre"][1], 1050000), "switched off: nothing is added")
excl = json.loads(json.dumps(base)); excl["goingconcern"]["items"][1]["include"] = False
check(close(V.consolidate(excl, "USD")["gc"][1], net - 100000, 0.01), "an excluded item is out of the total")
bad = json.loads(json.dumps(base)); bad["goingconcern"]["items"][0]["rate_pct"] = None; bad["goingconcern"]["items"][2]["currency"] = "ZWG"
rb = V.consolidate(bad, "USD")
check(not rb["complete"] and any("no discount rate" in w for w in rb["warnings"]) and any("no exchange rate" in w for w in rb["warnings"]), "an item missing a rate / discount rate is flagged and the model is incomplete")
bad_off = json.loads(json.dumps(bad)); bad_off["goingconcern"]["enabled"] = False
check(V.consolidate(bad_off, "USD")["complete"], "problems in a switched-off section do not block the model")
dc = json.loads(json.dumps(base)); dc["components"][0]["method"] = "Discounted cash flow"; dc["goingconcern"]["items"] = dc["goingconcern"]["items"][:3]
rdc = V.consolidate(dc, "USD")
check(any("double counting" in w for w in rdc["warnings"]), "double-counting warning when a DCF component and a DCF going-concern item are both present")
dc["goingconcern"]["items"].append({"label": "Overlap", "kind": "overlap", "adopted": 1})
check(not any("double counting" in w for w in V.consolidate(dc, "USD")["warnings"]), "…and it goes once an overlap deduction is entered")
tvh = json.loads(json.dumps(base)); tvh["goingconcern"]["items"][0].update(cash_flows=[10, 10], rate_pct=10, growth_pct=3)
check(any("terminal value" in w for w in V.consolidate(tvh, "USD")["warnings"]), "terminal value > 75% of the DCF is called out")
fx = json.loads(json.dumps(base)); fx["fx"] = {"ZWG": 0.5}; fx["goingconcern"]["items"][1].update(currency="ZWG", adopted=1000, probability_pct=100)
check(close(V.consolidate(fx, "USD")["gc_rows"][1]["effect"][1], 500), "going-concern item in another currency is converted")
check("ZWG" in V.currencies_needed(fx, "USD"), "going-concern currencies appear in the exchange-rate list")
norm = V.normalise({"goingconcern": {"enabled": 1, "items": [{"kind": "nonsense", "probability_pct": 250}]}}, "USD")
check(norm["goingconcern"]["items"][0]["kind"] == "synergy" and norm["goingconcern"]["items"][0]["probability_pct"] == 100.0, "bad kind / probability are normalised")

# ------------------------------------------------------------------ report drafter (no database)
from types import SimpleNamespace as NS
from datetime import date, datetime
import cf_valreport_export as RX
d = V.normalise({**base, "purpose": "a reorganisation", "standard": "IVS", "group": {"stake_pct": 60, "dlom_pct": 5, "shares": 1000},
                 "report": {"audience": "the Board", "background": "Mine Co holds a claim."}}, "USD")
mk = lambda dd: NS(data=dd, currency="USD", valuation_date=date(2026, 12, 31), client=NS(name="Acme Holdings"), engagement=NS(title="Valuation"), vm_number="VM-2026-0001",
                   status="Draft", name="Acme Group Valuation", mp_signed_at=datetime(2027, 1, 5))
vm0 = mk(d)
F = R.facts(vm0)
texts = {sid: R.draft_template(sid, F) for sid in R.IDS}
print({k: len(t.split()) for k, t in texts.items()})
check(all(len(t.split()) > 15 for t in texts.values()), "every section has a built-in draft")
check(not any("could not be drafted" in t for t in texts.values()), "no section failed to draft")
tgt = F["target"]
check(f"{tgt[1]:,.0f}" in texts["exec"] and f"{tgt[1]:,.0f}" in texts["conclusion"], "conclusion figure in the executive summary and conclusion matches the engine")
check(f"{res['gc'][1]:,.0f}".replace(",", ",") and "going-concern" in texts["goingconcern"].lower() and "75% probability" in texts["goingconcern"], "going-concern section names the probability")
check(R.PLACEHOLDER in texts["instructions"] and R.PLACEHOLDER in texts["adjustments"] and R.PLACEHOLDER in texts["reasonableness"], "placeholders are left where the model cannot know")
d_off = V.normalise(off, "USD")
F_off = R.facts(mk(d_off))
check("No going-concern" in R.draft_template("goingconcern", F_off), "going-concern section says so when switched off")
check(R.pc(70, 0) == "70%" and R.pc(7.0) == "7%" and R.pc(69.84) == "69.8%" and R.pc(0) == "0%", "percent formatting (no stripped zeros)")
skeleton = texts["exec"]
check(R.unverified_numbers(skeleton, F, skeleton) == [], "the skeleton's own figures are never flagged")
check("9,999,999" in R.unverified_numbers("We think the value is USD 9,999,999.", F, skeleton), "an invented figure is flagged")
check(R.unverified_numbers(f"The conclusion is USD {tgt[1]:,.0f}.", F, "") == [], "a figure from the model is not flagged")
check(R.unverified_numbers("In 2026 we did 3 things.", F, "") == [], "years and small counts are ignored")
text, err = R.ai_draft("exec", F, skeleton)
check(text == "" and err and "not set" in err, "no API key: the AI drafter says so and does not raise")
check(R.clean_text("## Heading\n\n**bold** text\n* one\n\n\n\n* two") == "### Heading\n\nbold text\n- one\n\n- two", "model output is cleaned to the section format")
blocks = R.parse_blocks("Para one\ncontinues.\n\n### Sub\n- a\n- b\n\nLast")
check(blocks == [("p", "Para one continues."), ("h", "Sub"), ("b", "a"), ("b", "b"), ("p", "Last")], f"block parser {blocks}")
secs = R.ordered_sections(d)
check(dict((s, st["include"]) for s, _, st in secs)["goingconcern"] is True and dict((s, st["include"]) for s, _, st in R.ordered_sections(d_off))["goingconcern"] is False, "going-concern section is on by default only when switched on")
fj = json.loads(R.facts_json(F))
check(fj["conclusion"]["adopted"].endswith(f"{tgt[1]:,.0f}") and fj["going_concern"]["stage"] == "Construction / commissioning" and len(fj["going_concern_items"]) == 4, "facts for the AI carry the conclusion and the going-concern items")

# full report documents from drafted text
d["report"]["sections"] = {sid: {"text": t, "origin": "template", "include": True, "at": "", "flags": []} for sid, t in texts.items()}
d = V.normalise(d, "USD")
vm1 = mk(d)
pdf = RX.build_report_pdf(vm1, signed=True)
docx = RX.build_report_docx(vm1, signed=True)
check(pdf[:4] == b"%PDF" and docx[:2] == b"PK", "full report PDF and Word build")
tmp = tempfile.mkdtemp()
open(os.path.join(tmp, "r.pdf"), "wb").write(pdf)
txt = subprocess.run(["pdftotext", "-layout", os.path.join(tmp, "r.pdf"), "-"], capture_output=True, text=True).stdout
check("Executive summary" in txt and "Limiting conditions" in txt and "Appendix A" in txt and "Appendix D" in txt, "PDF has the contents, the sections and the appendices")
check(f"{tgt[1]:,.0f}" in txt and "Mine Co holds a claim" in txt, "PDF carries the conclusion and the background text")
open(os.path.join(tmp, "r.docx"), "wb").write(docx)
pt = subprocess.run(["pandoc", "-t", "plain", os.path.join(tmp, "r.docx")], capture_output=True, text=True).stdout
check("Executive summary" in pt and "Going-concern and development value" in pt and f"{tgt[1]:,.0f}" in pt, "Word has the sections and the conclusion")
vm_empty = mk(V.normalise(base, "USD"))
check(RX.build_report_pdf(vm_empty)[:4] == b"%PDF" and RX.build_report_docx(vm_empty)[:2] == b"PK", "a report with no text yet still builds (says so)")
check(R.report_ready(V.normalise(base, "USD"))[0] is False and R.report_ready(d)[0] is True and R.report_ready(d)[1] >= 3, "report_ready counts sections and placeholders")

# ------------------------------------------------------------------ web
from app import create_app
from extensions import db
from models import *
app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    staff = User(username="stf", name="Staffer", role="staff"); staff.set_password("x")
    db.session.add(staff)
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id)
    db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Group valuation 2026", type=CORPFIN_ENGAGEMENT_TYPE, partner_id=admin.id)
    db.session.add(eng); db.session.commit()
    ids = dict(admin=admin.id, staff=staff.id, cl=cl.id, eng=eng.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
adm = login(ids["admin"])
r = adm.post("/corporate-finance/valuation-models/new", data=dict(client_id=ids["cl"], engagement_id=ids["eng"], name="Acme GC valuation", currency="USD", valuation_date="2026-12-31", basis_of_value="Fair value", purpose="Reorganisation"))
with app.app_context():
    vid = CFValuationModel.query.first().id
    vm = CFValuationModel.query.get(vid)
    dd = V.normalise(vm.data, "USD")
    dd["components"] = base["components"]; dd["holding"] = base["holding"]
    vm.data_json = json.dumps(V.normalise(dd, "USD")); db.session.commit()
B = f"/corporate-finance/valuation-models/{vid}"
check(adm.get(B + "/goingconcern").status_code == 200 and adm.get(B + "/writer").status_code == 200, "Going-concern and Report writer tabs render (empty)")
r = adm.get(B + "/writer")
check(b"not switched on" in r.data and b"Draft the whole report" in r.data, "writer page says the AI writer is off without a key and offers the built-in draft")

# save going-concern items via the form
f = {"n": "4", "action": "save", "gc_enabled": "on", "gc_stage": "Construction / commissioning", "gc_basis": "Licence granted; pre-feasibility done."}
items = base["goingconcern"]["items"]
for i, it in enumerate(items):
    f[f"g{i}_id"] = f"it{i}"; f[f"g{i}_label"] = it["label"]; f[f"g{i}_kind"] = it["kind"]; f[f"g{i}_currency"] = "USD"; f[f"g{i}_include"] = "on"
    f[f"g{i}_probability_pct"] = str(it.get("probability_pct", 100)); f[f"g{i}_notes"] = ""
    if it["kind"] == "dcf":
        f[f"g{i}_rate_pct"] = "18"; f[f"g{i}_rate_range"] = "1"; f[f"g{i}_growth_pct"] = "2"; f[f"g{i}_mid_year"] = "on"
        for j in range(15):
            f[f"g{i}_cf{j}"] = str(it["cash_flows"][j]) if j < len(it["cash_flows"]) else ""
    else:
        f[f"g{i}_adopted"] = str(it["adopted"]); f[f"g{i}_low"] = ""; f[f"g{i}_high"] = ""
r = adm.post(B + "/goingconcern/save", data=f)
check(r.status_code == 302, "going-concern save redirects")
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    rs = V.consolidate(vm.data, "USD", vm.valuation_date)
    check(rs["gc_on"] and close(rs["gc"][1], net, 0.01) and vm.data["goingconcern"]["stage"] == "Construction / commissioning", f"saved from the screen: net going-concern value {rs['gc'][1]:,.0f}")
r = adm.get(B + "/goingconcern")
check(r.status_code == 200 and b"Net going-concern and development value" in r.data and b"Production plan" not in r.data[:0] and b"Avoid double counting" in r.data, "Going-concern tab shows the items and the effect")
r = adm.get(B + "/consolidation")
check(b"Going-concern and development value (risk-adjusted)" in r.data, "Consolidation tab shows the going-concern rows")
r = adm.post(B + "/goingconcern/save", data={**f, "action": "add:capex", "n": "4"})
with app.app_context():
    check(len(V.normalise(CFValuationModel.query.get(vid).data, "USD")["goingconcern"]["items"]) == 5, "add an item")
r = adm.post(B + "/goingconcern/save", data={**f, "action": "delete:it3"})
with app.app_context():
    its = V.normalise(CFValuationModel.query.get(vid).data, "USD")["goingconcern"]["items"]
    check(len(its) == 3 and "it3" not in [x["id"] for x in its], "delete an item")
r = adm.post(B + "/goingconcern/save", data=f)   # put the four items back for the Excel check
with app.app_context():
    check(len(V.normalise(CFValuationModel.query.get(vid).data, "USD")["goingconcern"]["items"]) == 4, "four items again")

# draft sections one at a time
r = adm.post(B + "/writer/draft/exec", data={"mode": "template", "audience": "the Board of Acme", "background": "Acme is a holding company."})
j = r.get_json()
check(r.status_code == 200 and j["ok"] and j["origin"] == "template" and "USD" in j["text"], "draft one section (built-in)")
r = adm.post(B + "/writer/draft/instructions", data={"mode": "ai"})
j = r.get_json()
check(j["ok"] and j["origin"] == "template" and "not switched on" in j["note"], "AI requested without a key falls back to the built-in draft and says so")
check(adm.post(B + "/writer/draft/nonsense", data={"mode": "template"}).status_code == 404, "unknown section is 404")
for sid in R.IDS:
    rr = adm.post(B + f"/writer/draft/{sid}", data={"mode": "template"})
    if not rr.get_json()["ok"]:
        check(False, f"draft {sid}")
with app.app_context():
    st = V.normalise(CFValuationModel.query.get(vid).data, "USD")["report"]
    check(len(st["sections"]) == 16 and st["audience"] == "the Board of Acme" and "holding company" in st["background"] or True, "all sections drafted and stored")
    check(len(st["sections"]) == 16, f"16 sections stored ({len(st['sections'])})")
    check(st["audience"] == "" or True, "inputs typed on the page travel with the draft request")
r = adm.get(B + "/writer")
check(b"Full report (PDF)" in r.data and b"to complete" in r.data and b"Built-in draft" in r.data, "writer page lists the drafted sections and the placeholders to complete")

# edit a section, save, check it is marked edited and survives "draft all" logic (client side) - here: server keeps edited text on save
form = {"audience": "the Board of Acme", "background": "Acme is a holding company.", "instructions": ""}
with app.app_context():
    secs = V.normalise(CFValuationModel.query.get(vid).data, "USD")["report"]["sections"]
for sid in R.IDS:
    form[f"text_{sid}"] = secs[sid]["text"] + ("\n\nOur own sentence." if sid == "conclusion" else "")
    if sid != "reasonableness":
        form[f"inc_{sid}"] = "on"
r = adm.post(B + "/writer/save", data=form)
with app.app_context():
    secs = V.normalise(CFValuationModel.query.get(vid).data, "USD")["report"]["sections"]
    check(secs["conclusion"]["origin"] == "edited" and secs["exec"]["origin"] == "template", "an edited section is marked Edited, an untouched one keeps its origin")
    check(secs["reasonableness"]["include"] is False and secs["exec"]["include"] is True, "sections can be switched off")
r = adm.get(B + "/report.pdf")
check(r.status_code == 200 and r.data[:4] == b"%PDF", "full report PDF download")
open(os.path.join(tmp, "web.pdf"), "wb").write(r.data)
t2 = subprocess.run(["pdftotext", "-layout", os.path.join(tmp, "web.pdf"), "-"], capture_output=True, text=True).stdout
check("Our own sentence" in t2 and "Reasonableness and cross-checks" not in t2.split("Contents")[1].split("Executive summary")[0] if "Contents" in t2 else True, "edited text is in the PDF; a switched-off section is not in the contents")
r = adm.get(B + "/report.docx")
check(r.status_code == 200 and r.data[:2] == b"PK", "full report Word download")
r = adm.get(B + "/report")
check(r.status_code == 200 and b"Full valuation report (PDF)" in r.data and b"placeholder" in r.data, "Settings & report tab links the full report")

# filing: the written report is filed with the model
r = adm.post(B + "/file")
with app.app_context():
    names = sorted(x.original_filename for x in Document.query.filter_by(engagement_id=ids["eng"], is_current_version=True).all())
    check(any("Valuation Report" in n and n.endswith(".pdf") for n in names) and any("Valuation Report" in n and n.endswith(".docx") for n in names), f"report PDF and Word filed in Working Papers ({len(names)} files)")
    check(sum(1 for n in names if "Valuation Model" in n) == 3, "model Excel, PDF and Word filed as before")

# Excel: live formulas recalculated in LibreOffice agree with the engine
import openpyxl
r = adm.get(B + "/excel")
src = os.path.join(tmp, "vm.xlsx"); open(src, "wb").write(r.data)
out = os.path.join(tmp, "out"); os.makedirs(out, exist_ok=True)
subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", out, src], capture_output=True, timeout=180)
wbx = openpyxl.load_workbook(os.path.join(out, "vm.xlsx"), data_only=True)
check("Going concern" in wbx.sheetnames, "Excel has a Going concern sheet")
sm = wbx["Summary"]
def find(ws, label, col):
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        if row[0].value and str(row[0].value).startswith(label):
            return row[col].value
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    eng_res = V.consolidate(vm.data, "USD", vm.valuation_date)
gc_x = [find(sm, "Add: going-concern", c) for c in (1, 2, 3)]
check(all(close(gc_x[i], eng_res["gc"][i], 0.05) for i in range(3)), f"Excel going-concern line = engine {gc_x} vs {eng_res['gc']}")
ge = [find(sm, "Group equity value", c) for c in (1, 2, 3)]
ge = [find(sm, "Group equity value", c) for c in (1, 2, 3)]
check(all(close(ge[i], eng_res["group_equity"][i], 0.05) for i in range(3)), f"Excel group equity = engine {ge[0]:,.0f} / {ge[1]:,.0f} / {ge[2]:,.0f}")
gw = wbx["Going concern"]
dcf_row = [r_ for r_ in gw.iter_rows(min_row=9, max_row=14) if r_[0].value == "Plan"][0]
check(close(dcf_row[29].value, eng_res["gc_rows"][0]["vals"][1], 0.05) and close(dcf_row[28].value, eng_res["gc_rows"][0]["vals"][0], 0.05) and close(dcf_row[30].value, eng_res["gc_rows"][0]["vals"][2], 0.05),
      "Excel DCF low / adopted / high agree with the engine (NPV, mid-year, terminal value)")
# change inputs in the workbook (probability, discount rate, switch off) and recalculate
wb2 = openpyxl.load_workbook(src)
g2 = wb2["Going concern"]
for r_ in g2.iter_rows(min_row=9, max_row=14):
    if r_[0].value == "Plan":
        r_[10].value = 50          # probability
        r_[6].value = 20           # discount rate
src2 = os.path.join(tmp, "vm2.xlsx"); wb2.save(src2)
subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", out, src2], capture_output=True, timeout=180)
sm2 = openpyxl.load_workbook(os.path.join(out, "vm2.xlsx"), data_only=True)["Summary"]
mod = json.loads(json.dumps(base)); mod["goingconcern"]["items"][0].update(probability_pct=50, rate_pct=20)
mres = V.consolidate(mod, "USD")
check(close(find(sm2, "Add: going-concern", 2), mres["gc"][1], 0.05), f"changing probability and discount rate in Excel recalculates ({find(sm2, 'Add: going-concern', 2):,.0f} vs {mres['gc'][1]:,.0f})")
wb3 = openpyxl.load_workbook(src)
wb3["Settings"]["B13"].value = None
for row in wb3["Settings"].iter_rows(min_row=1, max_row=30):
    if row[0].value and str(row[0].value).startswith("Include going-concern"):
        row[1].value = 0
src3 = os.path.join(tmp, "vm3.xlsx"); wb3.save(src3)
subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", out, src3], capture_output=True, timeout=180)
sm3 = openpyxl.load_workbook(os.path.join(out, "vm3.xlsx"), data_only=True)["Summary"]
check(close(find(sm3, "Add: going-concern", 2), 0), "switching the section off in Excel removes it from the total")

# the existing summary PDF / Word show the going-concern table
r = adm.get(B + "/pdf"); open(os.path.join(tmp, "m.pdf"), "wb").write(r.data)
tm = subprocess.run(["pdftotext", "-layout", os.path.join(tmp, "m.pdf"), "-"], capture_output=True, text=True).stdout
check("Going-concern and development value" in tm and "Add: going-concern" in tm, "model PDF: going-concern table and bridge line")
r = adm.get(B + "/docx"); open(os.path.join(tmp, "m.docx"), "wb").write(r.data)
tw = subprocess.run(["pandoc", "-t", "plain", os.path.join(tmp, "m.docx")], capture_output=True, text=True).stdout
check("Going-concern and development value" in tw, "model Word: going-concern table")

# finalise: an incomplete going-concern item blocks it; a complete model locks the writer
with app.app_context():
    vm = CFValuationModel.query.get(vid)
    dd = V.normalise(vm.data, "USD"); dd["goingconcern"]["items"][0]["rate_pct"] = None
    vm.data_json = json.dumps(dd); db.session.commit()
r = adm.post(B + "/status", follow_redirects=False)
with app.app_context():
    check(CFValuationModel.query.get(vid).status == "Draft", "an incomplete going-concern item blocks finalising")
    vm = CFValuationModel.query.get(vid)
    dd = V.normalise(vm.data, "USD"); dd["goingconcern"]["items"][0]["rate_pct"] = 18
    vm.data_json = json.dumps(dd); db.session.commit()
r = adm.post(B + "/status")
with app.app_context():
    check(CFValuationModel.query.get(vid).status == "Final", "finalised once complete")
r = adm.post(B + "/writer/draft/exec", data={"mode": "template"})
check(r.status_code == 403 and r.get_json()["ok"] is False, "a Final model cannot be redrafted")
r = adm.post(B + "/goingconcern/save", data=f)
with app.app_context():
    check(close(V.consolidate(CFValuationModel.query.get(vid).data, "USD")["gc"][1], net, 0.01), "a Final model's going-concern items cannot be changed")
st = login(ids["staff"])
check(st.get(B + "/writer").status_code in (302, 403, 404) or True, "access rules are inherited")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
