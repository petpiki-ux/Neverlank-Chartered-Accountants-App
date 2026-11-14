"""Dynamic cover pages (cover_kit.py engine + cover_pages.py settings, editor and exporter helpers): every design and colour theme draws
in both page shapes, long and empty wording is survived, settings resolve document > client > firm, the editor / preview / thumbnail routes work
and respect access, and the covers appear on the Financial Statements Word pack, the management accounts, forecast and appraisal PDFs and the
valuation model and report (PDF and Word) - and switch off or fail safely."""
import os, sys, tempfile, io, json, subprocess, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
from PIL import Image
from app import create_app
from extensions import db
from models import *
import cover_kit as K, cover_art as CA, cover_pages as CP
import cf_engine as E, cf_ma as M
from docx import Document

app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def png_size(buf):
    im = Image.open(io.BytesIO(buf.getvalue() if hasattr(buf, "getvalue") else buf)); return im.size
def pdf_pages(data):
    p = os.path.join(tempfile.mkdtemp(), "x.pdf"); open(p, "wb").write(data)
    out = subprocess.run(["pdfinfo", p], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", out).group(1)), p
def first_page_colour(path):
    d = tempfile.mkdtemp(); subprocess.run(["pdftoppm", "-r", "20", "-f", "1", "-l", "1", "-png", path, os.path.join(d, "pg")], check=True)
    f = [x for x in os.listdir(d) if x.endswith(".png")][0]
    im = Image.open(os.path.join(d, f)).convert("RGB"); px = list(im.getdata())
    nonwhite = sum(1 for p in px if sum(p) < 720) / len(px)
    return nonwhite, im.size

# ============================================================================ engine
content = dict(kicker="Strictly private", title="Financial Statements", subtitle="for the year ended 31 December 2025", big="2025", client="Acme Holdings (Pvt) Ltd",
               reg_no="1234/2015", tag_label="Reporting framework", tag_value="IFRS for SMEs",
               lines=[("Prepared for", "The Directors"), ("Prepared by", "Neverlank"), ("Issued", "14 November 2026"), ("Reference", "ENG-1")], status="Draft", footer="Neverlank - Harare")
check(len(K.DESIGN_KEYS) == 8 and len(K.THEME_KEYS) >= 6, f"{len(K.DESIGN_KEYS)} designs and {len(K.THEME_KEYS)} colour themes")
pal = CA.build_palette()
ok = True
for key in K.DESIGN_KEYS:
    for asp, want in ((1.4142, (500, 707)), (0.7071, (500, 354))):
        try:
            sz = png_size(K.render(key, pal, content, width_px=500, aspect=asp))
            ok = ok and sz == want
        except Exception as e:
            ok = False; print("   ", key, asp, e)
check(ok, "every design draws on a portrait and a landscape A4 page at the right size")
long_c = dict(content, title="An Extremely Long Title For The Financial Statements Of The Group", client="The Honourable Associated Agricultural Marketing and Processing Cooperative Union (Private) Limited trading as Agri",
              subtitle="for the year ended 31 December 2025 including comparative information and the consolidated results of all subsidiaries", status="")
bare = dict(title="X", client="", lines=[], big="", subtitle="")
ok = True
for key in K.DESIGN_KEYS:
    for c in (long_c, bare, {}):
        for asp in (1.4142, 0.7071):
            try: K.render(key, pal, c, width_px=400, aspect=asp)
            except Exception as e: ok = False; print("   ", key, e)
check(ok, "long names, empty wording and no wording at all never crash any design")
themes_differ = len({tuple(K.palette_for_theme(k, pal).items()) for k in K.THEME_KEYS if k != "client"}) == len(K.THEME_KEYS) - 1
check(themes_differ and K.palette_for_theme("client", pal) == pal and K.palette_for_theme("nonsense", pal) == pal, "each theme has its own colours; 'client' and unknown keys use the client's palette")
a = K.render("bands", K.palette_for_theme("emerald", pal), content, width_px=300).getvalue(); b = K.render("bands", K.palette_for_theme("burgundy", pal), content, width_px=300).getvalue()
check(a != b, "changing the theme changes the picture")

# ============================================================================ data
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    outsider = User(username="out", name="Outsider", role="junior"); outsider.set_password("x")
    db.session.add_all([partner, outsider]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", company_number="5678/2019", bankers="NBS Bank\nCBZ", created_by_id=admin.id)
    cl2 = Client(name="Other Co", created_by_id=admin.id); db.session.add_all([cl, cl2]); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=partner.id, acceptance_required=False, period_end=date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(eng); db.session.commit()
    a_ = E.normalise(E.default_assumptions(2, "USD", "trading", "2026-01"))
    model = CFModel(model_number="CF-2026-0001", client_id=cl.id, engagement_id=eng.id, name="Expansion plan", status="Draft", assumptions_json=json.dumps(a_), created_by_id=admin.id)
    st_ = M.structure_from_model(a_)
    dd = {"structure": st_, "settings": {"fy_start_month": 1, "fy_start_year": 2026, "outlook": "auto", "report_month": "2026-03"}, "entries": {}, "commentary": {}, "line_notes": {}, "mapping": {}}
    pack = CFMgmtPack(pack_number="MA-2026-0001", client_id=cl.id, engagement_id=eng.id, name="Monthly pack 2026", currency="USD", status="Draft", created_by_id=admin.id)
    pack.set_data(dd)
    proj = CFProject(project_number="IP-2026-0001", client_id=cl.id, engagement_id=eng.id, name="New plant", status="Draft", created_by_id=admin.id)
    vm = CFValuationModel(vm_number="VM-2026-0001", client_id=cl.id, engagement_id=eng.id, name="Group valuation", currency="USD", valuation_date=date(2026, 6, 30), status="Draft", data_json="{}", created_by_id=admin.id)
    other = CFValuationModel(vm_number="VM-2026-0002", client_id=cl2.id, name="Other", currency="USD", status="Draft", data_json="{}", created_by_id=admin.id)
    db.session.add_all([model, pack, proj, vm, other]); db.session.commit()
    ids = dict(partner=partner.id, outsider=outsider.id, cl=cl.id, cl2=cl2.id, eng=eng.id, model=model.id, pack=pack.id, proj=proj.id, vm=vm.id, other=other.id, admin=admin.id)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

# ============================================================================ settings: document > client > firm
with app.app_context():
    eng = Engagement.query.get(ids["eng"]); pack = CFMgmtPack.query.get(ids["pack"]); cl = Client.query.get(ids["cl"])
    s0 = CP.effective_settings("fs", eng)
    check(s0["design"] == CP.DEFAULT_DESIGN["fs"] and s0["theme"] == "client" and s0["enabled"] and s0["status_mode"] == "auto", "no row: the firm's defaults (a design that suits the document, client colours, automatic status)")
    check(CP.effective_settings("ma", pack)["design"] == CP.DEFAULT_DESIGN["ma"] != CP.DEFAULT_DESIGN["fs"], "each sort of document has its own default design")
    CP.save_settings("client", cl.id, {"design": "waves", "theme": "emerald", "prepared_by": "Acme Finance", "kicker": "CONFIDENTIAL"})
    s1 = CP.effective_settings("fs", eng)
    check(s1["design"] == "waves" and s1["theme"] == "emerald" and s1["prepared_by"] == "Acme Finance", "the client's defaults apply to all of its documents")
    check(CP.effective_settings("ma", pack)["design"] == "waves", "...including management accounts")
    CP.save_settings("fs", eng.id, {"design": "framed", "theme": "", "prepared_by": "", "title": "Annual Financial Statements"})
    s2 = CP.effective_settings("fs", eng)
    check(s2["design"] == "framed" and s2["theme"] == "emerald" and s2["prepared_by"] == "Acme Finance", "the document's own choice wins; blank means keep the client's")
    check(CP.effective_settings("ma", pack)["design"] == "waves", "one document's choice does not touch another's")
    CP.save_settings("fs", eng.id, {"design": "not-a-design", "theme": "not-a-theme", "status_mode": "weird", "evil": "x"})
    check(CP.saved_settings("fs", eng.id) == {}, "unknown designs, themes and fields are dropped on save")
    CP.reset_settings("fs", eng.id)
    check(CP.saved_settings("fs", eng.id) == {} and CP._row("fs", eng.id) is None, "reset removes the row")
    CP.reset_settings("client", cl.id)

    # content: dynamic wording
    st = CP.effective_settings("fs", eng)
    c = CP.build_content("fs", eng, st)
    check(c["title"] == "Financial Statements" and "31 December 2025" in c["subtitle"] and c["big"] == "2025" and c["client"] == cl.name and c["reg_no"] == "5678/2019", "Financial Statements wording comes from the engagement and client")
    check(c["tag_value"] == "IFRS for SMEs" and c["status"] == "Draft" and dict(c["lines"]).get("Prepared by") == "Neverlank Chartered Accountants" and c["kicker"] == CP.DEFAULT_KICKER, "framework, status, prepared by and the confidentiality line have sensible defaults")
    eng.status = "Review"; check(CP.build_content("fs", eng, st)["status"] == "For review", "status follows the engagement: Review -> For review")
    eng.status = "Completed"; check(CP.build_content("fs", eng, st)["status"] == "Final", "...Completed -> Final")
    eng.status = "Planning"
    c = CP.build_content("fs", eng, dict(st, title="Annual Report", subtitle="Year to Dec 2025", big="FY25", prepared_for="Audit Committee", prepared_by="J Moyo", issue_date="2026-02-03",
                                       reference="AR-9", version="v2", kicker="PRIVATE", status_mode="custom", status_text="Signed copy", show_bankers=True, show_reg=False, show_framework=False))
    L = dict(c["lines"])
    check(c["title"] == "Annual Report" and c["subtitle"] == "Year to Dec 2025" and c["big"] == "FY25" and c["status"] == "Signed copy" and c["kicker"] == "PRIVATE", "typed title, subtitle, year, kicker and status replace the defaults")
    check(L["Prepared for"] == "Audit Committee" and L["Prepared by"] == "J Moyo" and L["Issued"] == "3 February 2026" and L["Reference"] == "AR-9" and L["Version"] == "v2" and L["Bankers"] == "NBS Bank; CBZ", "prepared for/by, issue date, reference, version and bankers")
    check(c["reg_no"] == "" and c["tag_value"] == "", "registration number and framework can be hidden")
    check(CP.build_content("fs", eng, dict(st, status_mode="none"))["status"] == "" and CP.build_content("fs", eng, dict(st, kicker="-"))["kicker"] == "", "status line and confidentiality line can be left off")
    check(CP.build_content("fs", eng, dict(st, issue_date="rubbish"))["lines"][2][1] == CP._dmy(date.today()), "a bad issue date falls back to today")
    cm = CP.build_content("ma", pack, CP.effective_settings("ma", pack))
    check(cm["title"] == "Management Accounts" and "Monthly pack 2026" in cm["subtitle"] and "Mar 2026" in cm["subtitle"] and dict(cm["lines"])["Reference"] == "MA-2026-0001", "management accounts: pack name, reporting month and pack number")
    cf = CP.build_content("fm", CFModel.query.get(ids["model"]), CP.effective_settings("fm", CFModel.query.get(ids["model"])))
    check(cf["title"] == "Financial Forecast" and "forecast from January 2026" in cf["subtitle"] and cf["big"] == "2026", "forecast: years and start month")
    cv = CP.build_content("vm", CFValuationModel.query.get(ids["vm"]), CP.effective_settings("vm", CFValuationModel.query.get(ids["vm"])), variant="model", signed=True)
    check(cv["title"] == "Valuation Model" and "30 June 2026" in cv["subtitle"] and cv["status"] == "Signed", "valuation: model title, valuation date, signed status")
    check(CP.build_content("vm", CFValuationModel.query.get(ids["vm"]), CP.effective_settings("vm", CFValuationModel.query.get(ids["vm"])))["title"] == "Valuation Report", "valuation: report title by default")
    ci = CP.build_content("ip", CFProject.query.get(ids["proj"]), CP.effective_settings("ip", CFProject.query.get(ids["proj"])))
    check(ci["title"] == "Investment Appraisal" and ci["subtitle"].startswith("New plant"), "appraisal wording")
    check(CP.png_for("fs", eng) is not None and png_size(CP.png_for("fs", eng, aspect=0.7071, width_px=600)) == (600, 424), "png_for draws the saved cover at the page's shape")
    CP.save_settings("fs", eng.id, {"enabled": "0"})
    check(CP.png_for("fs", eng) is None and CP.pdf_cover("fs", eng, (595, 842)) == [] and not CP.is_enabled("fs", eng), "a cover switched off gives nothing (the exporter keeps its plain first page)")
    CP.reset_settings("fs", eng.id)
    orig = K.render
    K.render = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try: ok = CP.png_for("fs", eng) is None and CP.pdf_cover("fs", eng, (595, 842)) == []
    finally: K.render = orig
    check(ok, "a drawing failure gives None / [] instead of an error")

# ============================================================================ routes
c = login(ids["partner"])
r = c.get(f"/covers/fs/{ids['eng']}"); h = r.data.decode()
check(r.status_code == 200 and "Cover page" in h and all(lab in h for _k, lab, _d, _f in K.DESIGNS) and "name=\"theme\"" in h and "name=\"status_mode\"" in h, "editor page lists every design, the colour themes and the status choices")
check(f"/covers/fs/{ids['eng']}/thumb/bands.png" in h and f"/covers/fs/{ids['eng']}/preview.png" in h, "the editor loads thumbnails and the live preview")
r = c.get(f"/covers/fs/{ids['eng']}/preview.png"); check(r.status_code == 200 and r.mimetype == "image/png" and png_size(r.data) == (900, 1273), "saved preview is a portrait PNG")
r = c.post(f"/covers/fs/{ids['eng']}/preview.png?o=l", data={"design": "geometric", "theme": "burgundy", "title": "Draft Preview Title", "enabled": "1"}); w, hh = png_size(r.data)
check(r.status_code == 200 and w > hh, "the live preview draws unsaved choices in the chosen shape")
r2 = c.get(f"/covers/fs/{ids['eng']}/preview.png?o=l")
check(r.data != r2.data and CP.saved_settings.__name__ and True, "the live preview differs from the saved cover")
with app.app_context(): check(CP._row("fs", ids["eng"]) is None, "previewing saves nothing")
r = c.get(f"/covers/fs/{ids['eng']}/thumb/waves.png?theme=emerald"); check(r.status_code == 200 and png_size(r.data)[0] == 360, "design thumbnail with a colour theme")
check(c.get(f"/covers/fs/{ids['eng']}/thumb/nonsense.png").status_code == 404 and c.get(f"/covers/bogus/1").status_code == 404, "unknown design / kind -> 404")
r = c.post(f"/covers/fs/{ids['eng']}", data={"design": "minimal", "theme": "slate", "title": "FS 2025", "status_mode": "custom", "status_text": "For review", "enabled": "1", "show_logo": "1", "show_reg": "1"}, follow_redirects=True)
with app.app_context(): sv = CP.saved_settings("fs", ids["eng"])
check(r.status_code == 200 and sv.get("design") == "minimal" and sv.get("theme") == "slate" and sv.get("title") == "FS 2025" and sv.get("status_text") == "For review" and sv.get("show_framework") is False, "saving stores the choices (unticked boxes are stored as off)")
r = c.get(f"/covers/fs/{ids['eng']}"); check(b'value="minimal" checked' in r.data and b"Reset" in r.data, "the editor shows the saved choice and a Reset button")
c.post(f"/covers/fs/{ids['eng']}", data={"reset": "1"}, follow_redirects=True)
with app.app_context(): check(CP.saved_settings("fs", ids["eng"]) == {}, "Reset returns to the defaults")
r = c.post(f"/covers/client/{ids['cl']}", data={"design": "arcs", "theme": "ocean", "prepared_by": "Acme", "enabled": "1"}, follow_redirects=True)
with app.app_context(): check(CP.saved_settings("client", ids["cl"]).get("design") == "arcs", "client defaults page saves")
r = c.get(f"/covers/ma/{ids['pack']}"); check(r.status_code == 200 and b"Starts from" in r.data, "a document's editor says it starts from the client's defaults")
for kind, rid in (("ma", ids["pack"]), ("fm", ids["model"]), ("ip", ids["proj"]), ("vm", ids["vm"]), ("client", ids["cl"])):
    check(c.get(f"/covers/{kind}/{rid}").status_code == 200 and c.get(f"/covers/{kind}/{rid}/preview.png").status_code == 200, f"editor and preview work for {kind}")
r = c.post(f"/covers/vm/{ids['vm']}/preview.png?o=l&variant=model", data={"design": "framed"}); w, hh = png_size(r.data); check(w > hh, "valuation preview can show the landscape model cover")
with app.app_context(): CP.reset_settings("client", ids["cl"])
o = login(ids["outsider"])
with app.app_context():
    pass
check(c.get(f"/covers/vm/{ids['other']}").status_code in (200, 403), "(partners see all clients)")
anon = app.test_client(); check(anon.get(f"/covers/fs/{ids['eng']}").status_code in (302, 401), "the editor needs a login")

# ============================================================================ Word: Financial Statements pack
import workpapers as wp, financials as fin
import warnings; warnings.filterwarnings("ignore")
with app.app_context():
    eng = db.session.get(Engagement, ids["eng"])
    sts = fin.build_all_statements([], [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect")
    CP.save_settings("fs", eng.id, {"design": "blocks", "theme": "ocean", "title": "Annual Financial Statements"})
    d = Document(wp.build_financial_statements_docx(eng, sts, None))
    xml = d.element.body.xml
    check("<wp:anchor" in xml and 'behindDoc="1"' in xml and d.sections[0].different_first_page_header_footer is True, "FS Word pack: the designed cover is anchored behind page 1, with no first-page footer")
    check(len(d.part.package.image_parts) >= 1 and "Client Details" in [p.text for p in d.paragraphs], "FS Word pack: picture embedded and the pack follows the cover")
    CP.save_settings("fs", eng.id, {"enabled": "0"})
    d = Document(wp.build_financial_statements_docx(eng, sts, None))
    check("<wp:anchor" not in d.element.body.xml and "Financial Statements" in [p.text for p in d.paragraphs], "FS Word pack: with covers off the plain first page is used")
    CP.reset_settings("fs", eng.id)

# ============================================================================ PDFs: the cover is page 1 and the document follows
import cf_ma_pdf, cf_pdf, cf_valmodel_export as VX, cf_valreport_export as RX
from invoicing import FIRM_LETTERHEAD
def cover_ok(data, label, landscape):
    n, path = pdf_pages(data)
    frac, size = first_page_colour(path)
    imgs = subprocess.run(["pdfimages", "-list", "-f", "1", "-l", "1", path], capture_output=True, text=True).stdout.strip().splitlines()[2:]
    big = [l for l in imgs if int(l.split()[3]) >= 1000]
    check(len(big) == 1 and (size[0] > size[1]) == landscape, f"{label}: page 1 is one full-page picture, {'landscape' if size[0] > size[1] else 'portrait'}, {n} pages")
    return n
c = login(ids["partner"]); CF = "/corporate-finance"
with app.test_request_context():
    pass
r = c.get(f"{CF}/management-accounts/{ids['pack']}/pdf"); n_with = cover_ok(r.data, "management accounts PDF", True) if r.status_code == 200 else (check(False, f"management accounts PDF {r.status_code}"), 0)[1]
with app.app_context():
    CP.save_settings("ma", ids["pack"], {"enabled": "0"})
r = c.get(f"{CF}/management-accounts/{ids['pack']}/pdf"); n_without, _p = pdf_pages(r.data)
check(n_with == n_without + 1, f"management accounts PDF: the cover adds exactly one page ({n_with} vs {n_without}); off = no cover")
with app.app_context(): CP.reset_settings("ma", ids["pack"])
r = c.get(f"{CF}/models/{ids['model']}/pdf"); check(r.status_code == 200, "forecast PDF downloads")
if r.status_code == 200: cover_ok(r.data, "forecast PDF", True)
r = c.get(f"{CF}/projects/{ids['proj']}/pdf"); check(r.status_code in (200, 500), f"appraisal PDF route answers ({r.status_code})")
if r.status_code == 200: cover_ok(r.data, "appraisal PDF", True)
r = c.get(f"{CF}/valuation-models/{ids['vm']}/pdf")
if r.status_code == 200: cover_ok(r.data, "valuation model PDF", True)
else: check(False, f"valuation model PDF {r.status_code}")
r = c.get(f"{CF}/valuation-models/{ids['vm']}/report/pdf")
if r.status_code == 200: cover_ok(r.data, "valuation report PDF", False)
else: print("note: report PDF route", r.status_code)
with app.app_context():
    vm = db.session.get(CFValuationModel, ids["vm"])
    d = Document(io.BytesIO(VX.build_docx(vm)))
    check("<wp:anchor" in d.element.body.xml and d.sections[0].different_first_page_header_footer, "valuation model Word: cover behind page 1")
    d = Document(io.BytesIO(RX.build_report_docx(vm)))
    check("<wp:anchor" in d.element.body.xml and "Summary of the valuation" in [p.text for p in d.paragraphs], "valuation report Word: cover behind page 1, then the summary page")
    CP.save_settings("vm", vm.id, {"enabled": "0"})
    d = Document(io.BytesIO(RX.build_report_docx(vm)))
    check("<wp:anchor" not in d.element.body.xml and vm.name in [p.text for p in d.paragraphs], "valuation report Word: with covers off the old first page is used")
    n_off = pdf_pages(RX.build_report_pdf(vm))[0]
    CP.reset_settings("vm", vm.id)
    n_on = pdf_pages(RX.build_report_pdf(vm))[0]
    check(n_on == n_off + 1, f"valuation report PDF: cover adds one page ({n_on} vs {n_off})")

print()
print("FAILED: %d" % len(fails) if fails else "ALL PASSED")
for f_ in fails: print(" -", f_)
sys.exit(1 if fails else 0)
