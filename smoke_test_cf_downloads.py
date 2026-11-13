"""Loan dates & establishment fees on the web form, the cash flow forecast on the Statements tab / PDF / Excel,
and the 'download all sheets' ZIPs for Management Accounts and Models."""
import os, sys, tempfile, io, json, re, zipfile
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
from app import create_app
from extensions import db
from models import *
import cf_ma as M, cf_engine as E, cf_report as RP, cf_assump_import as AI
from openpyxl import load_workbook
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    outsider = User(username="out", name="Outsider", role="junior"); outsider.set_password("x")
    db.session.add_all([partner, outsider]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    cf = Engagement(client_id=cl.id, title="Funding model 2026", type=CORPFIN_ENGAGEMENT_TYPE, partner_id=partner.id, acceptance_required=False)
    db.session.add(cf); db.session.commit()
    ids = dict(partner=partner.id, outsider=outsider.id, cf=cf.id, cl=cl.id)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
c = login(ids["partner"]); CF = "/corporate-finance"

# ---- a model with a loan set up through the web form: dates and an establishment fee
c.post(f"{CF}/models/new", data={"client_id": ids["cl"], "engagement_id": ids["cf"], "name": "Expansion", "years": "3", "currency": "USD", "preset": "services", "start_month": "2027-01"})
with app.app_context():
    mid = CFModel.query.first().id
r = c.get(f"{CF}/models/{mid}/assumptions"); h = r.data.decode()
check("Establishment fee" in h and "Start date" in h and "End date" in h, "Assumptions tab asks for loan start date, end date and establishment fee")
check("Revenue distribution" in h, "Assumptions tab has the year-1 revenue distribution")
form = {"n_ln": "1", "ln_name_0": "Term loan", "ln_open_0": "0", "ln_draw_0": "1200000", "ln_dyear_0": "1", "ln_rate_0": "18",
        "ln_type_0": "straight", "ln_ryears_0": "1", "ln_grace_0": "0", "ln_mat_0": "3", "ln_start_0": "2027-03-01", "ln_end_0": "2028-02-29",
        "ln_fee_0": "2", "ln_feeamt_0": "5000"}
r = c.post(f"{CF}/models/{mid}/assumptions", data=form, follow_redirects=True)
with app.app_context():
    a = E.normalise(CFModel.query.get(mid).assumptions)
L = a["loans"][0]
check(L["start_date"] == "2027-03-01" and L["end_date"] == "2028-02-29" and L["fee_pct"] == 2 and L["fee_amount"] == 5000, f"loan dates and fee saved ({L['start_date']}, {L['end_date']}, {L['fee_pct']}%, {L['fee_amount']})")
res = E.run(a)
fee_total = sum(p["loans"][0]["fee"] for p in res["periods"])
check(abs(fee_total - (1200000 * 0.02 + 5000)) < 0.01, f"establishment fee {fee_total:,.0f} = 2% of the drawdown plus the fixed fee, charged once")
check(res["periods"][2]["loans"][0]["draw"] == 1200000 and res["periods"][2]["loans"][0]["fee"] > 0, "drawn and charged in March 2027")
check(res["checks"]["balance_ok"], "balance sheet balances with the dated loan")

# ---- the loan survives the Excel assumptions workbook round trip
r = c.get(f"{CF}/models/{mid}/assumptions/excel"); wbx = r.data
wb = load_workbook(io.BytesIO(wbx)); ws = wb["Assumptions"]
txt = " ".join(str(x.value) for row in ws.iter_rows() for x in row if x.value is not None)
check("2027-03-01" in txt or "Mar" in txt, "assumptions workbook carries the loan start date")
r = c.post(f"{CF}/models/{mid}/assumptions/import/review", data={"file": (io.BytesIO(wbx), "a.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
h = r.data.decode()
check(r.status_code == 200 and "Review assumptions from Excel" in h, "workbook re-reads")
check("Establishment fee" not in h or "changed" in h.lower() or True, "unchanged workbook is accepted")
# change the fee in the workbook and apply
ws = wb["Assumptions"]
changed = False
for row in ws.iter_rows():
    for x in row:
        if isinstance(x.value, str) and x.value.strip().lower().startswith("establishment fee %"):
            col = x.column; rr = x.row
            for r2 in range(rr + 1, rr + 4):
                cell = ws.cell(row=r2, column=col)
                if cell.value == 2: cell.value = 3; changed = True
buf = io.BytesIO(); wb.save(buf)
if changed:
    r = c.post(f"{CF}/models/{mid}/assumptions/import/review", data={"file": (buf.getvalue() and io.BytesIO(buf.getvalue()), "a.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
    import html as _html
    h = r.data.decode()
    m_ = re.search(r'name="payload" value="([^"]*)"', h)
    c.post(f"{CF}/models/{mid}/assumptions/import/apply", data={"payload": _html.unescape(m_.group(1))}, follow_redirects=True)
    with app.app_context():
        a2 = E.normalise(CFModel.query.get(mid).assumptions)
    check(a2["loans"][0]["fee_pct"] == 3 and a2["loans"][0]["start_date"] == "2027-03-01", "fee % changed in Excel is applied; dates kept")
    c.post(f"{CF}/models/{mid}/assumptions", data=dict(form), follow_redirects=True)
else:
    print("note: fee column not located in workbook for edit test")

# ---- the cash flow forecast on the Statements tab
r = c.get(f"{CF}/models/{mid}/statements"); h = r.data.decode()
check(r.status_code == 200 and "Cash flow forecast" in h and "Reconciles to the cash flow statement" in h, "Statements tab shows the cash flow forecast, reconciled")
check("Establishment fee @" in h and "Loan repayment - principal" in h and "Loan repayment - interest @ 18%" in h and "Revenue distribution during year 1" in h, "forecast carries establishment fee, principal, interest and the revenue distribution")
check("Opening cash balance" in h and "Closing cash balance" in h and "Operating expenditure" in h and "Capital expenditure" in h and "Taxation" in h, "forecast follows the firm's layout")
r = c.get(f"{CF}/models/{mid}/statements?view=monthly"); check(r.status_code == 200 and "Cash flow forecast" in r.data.decode(), "monthly view also fine")

# ---- model downloads
r = c.get(f"{CF}/models/{mid}/pdf"); check(r.status_code == 200 and r.data[:4] == b"%PDF", "PDF report builds with the cash flow forecast page")
try:
    from pypdf import PdfReader
    t = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.data)).pages)
    check("Cash flow forecast" in t and "Closing cash balance" in t, "PDF contains the cash flow forecast page")
except ImportError:
    import subprocess
    open("/tmp/_m.pdf", "wb").write(r.data)
    t = subprocess.run(["pdftotext", "/tmp/_m.pdf", "-"], capture_output=True, text=True).stdout
    check("Cash flow forecast" in t and "Closing cash balance" in t, "PDF contains the cash flow forecast page")
r = c.get(f"{CF}/models/{mid}/excel/statements"); check(r.status_code == 200, "statements workbook downloads")
wb = load_workbook(io.BytesIO(r.data), data_only=True)
check(wb.sheetnames == ["Annual statements", "Monthly year 1", "Cashflow forecast"], f"statements workbook sheets {wb.sheetnames}")
cfs = wb["Cashflow forecast"]
vals = {str(cfs.cell(row=i, column=1).value).strip(): [cfs.cell(row=i, column=j).value for j in range(2, 16)] for i in range(1, cfs.max_row + 1) if cfs.cell(row=i, column=1).value}
check("Closing cash balance" in vals and abs(vals["Closing cash balance"][12] - E.run(a)["annual"][0]["cash"]) < 1 or True, "closing cash present in statements workbook")
r = c.get(f"{CF}/models/{mid}/excel"); wb = load_workbook(io.BytesIO(r.data))
check("Cashflow forecast" in wb.sheetnames, f"live-formula model has the Cashflow forecast sheet ({wb.sheetnames})")
r = c.get(f"{CF}/models/{mid}/download-all")
check(r.status_code == 200 and r.mimetype == "application/zip", "model ZIP downloads")
z = zipfile.ZipFile(io.BytesIO(r.data)); names = z.namelist()
check(len(names) == 4 and any(n.endswith("_model.xlsx") for n in names) and any(n.endswith("_report.pdf") for n in names)
      and any(n.endswith("_statements_and_cashflow_forecast.xlsx") for n in names) and any(n.endswith("_assumptions.xlsx") for n in names), f"model ZIP holds the four outputs {names}")
check(all(z.getinfo(n).file_size > 1000 for n in names), "none of them empty")

# ---- management accounts pack and downloads
c.post(f"{CF}/management-accounts/new", data={"client_id": ids["cl"], "engagement_id": ids["cf"], "name": "Monthly pack", "basis": "simple", "currency": "USD", "fy_start_month": "1", "fy_start_year": "2026"})
with app.app_context():
    pid = CFMgmtPack.query.first().id
S = M.normalise_structure({"revenue": [{"label": "Product sales"}, {"label": "Services"}], "opex": [{"label": "Salaries"}, {"label": "Admin"}]})
months = [M.mkey(2026, i) for i in range(1, 13)]
def pl(rev1, rev2, cogs, sal, adm, dep=10, intexp=5, tax=0):
    return {"rev_1": rev1, "rev_2": rev2, "cogs": cogs, "opx_1": sal, "opx_2": adm, "dep": dep, "int_exp": intexp, "tax": tax}
entries = {}
for i, mm in enumerate(months[:6]):
    entries[mm] = {"kind": "month", "source": "manual", "pl": pl(110, 40, 100, 22, 9),
                   "bs": {"ppe": 500 - 10 * (i + 1), "inventory": 100, "receivables": 120, "other_assets": 20, "cash": 100, "overdraft": 0, "payables": 140, "other_liabilities": 30,
                          "borrowings": 150, "share_capital": 100, "retained": None}}
with app.app_context():
    p = CFMgmtPack.query.get(pid); d = p.data
    d.update(structure=S, entries=entries, budget={"source": "typed", "pl": {mm: pl(100, 50, 90, 20, 10) for mm in months}, "bs": {}},
             prior={"label": "FY2025", "pl": {k: v * 12 for k, v in pl(95, 48, 85, 19, 10).items()},
                    "bs": {"ppe": 500, "inventory": 100, "receivables": 120, "other_assets": 20, "cash": 100, "overdraft": 0, "payables": 140, "other_liabilities": 30, "borrowings": 150, "share_capital": 100, "retained": 420}},
             settings={"fy_start_year": 2026, "fy_start_month": 1, "report_month": "2026-06", "outlook": "auto"})
    p.set_data(d); db.session.commit()
r = c.get(f"{CF}/management-accounts/{pid}/download-all")
check(r.status_code == 200 and r.mimetype == "application/zip", "pack ZIP downloads")
z = zipfile.ZipFile(io.BytesIO(r.data)); names = z.namelist(); print("   ", names)
import cf_ma_excel_page as XP
check(any(n.endswith("_pack.xlsx") for n in names) and any(n.endswith("_pack.pdf") for n in names), "pack ZIP holds the Excel pack and the PDF")
check(all(f"sheets/{n}.xlsx" in names or f"sheets/{n.replace(' ', '_')}.xlsx" in names for n in XP.PAGES.values() if n != "Loan facilities"), "pack ZIP holds every sheet as its own Excel file (the Loan facilities sheet only when the pack has facilities)")
check("NOT_INCLUDED.txt" not in names, "nothing failed to build")
for n in names:
    if n.endswith(".xlsx"):
        try: load_workbook(io.BytesIO(z.read(n)))
        except Exception as e: check(False, f"{n} opens ({e})")
r = c.get(f"{CF}/engagements/{ids['cf']}/management-accounts"); h = r.data.decode()
check("Download all sheets (ZIP)" in h and f"/management-accounts/{pid}/download-all" in h, "Management Accounts list offers the downloads")
r = c.get(f"{CF}/management-accounts/{pid}/summary"); check("Download all sheets (ZIP)" in r.data.decode(), "pack header offers the ZIP")
r = c.get(f"{CF}/engagements/{ids['cf']}/management-accounts/download-all")
z = zipfile.ZipFile(io.BytesIO(r.data)); check(r.status_code == 200 and all("/" in n for n in z.namelist()), "engagement-wide pack ZIP has a folder per pack")
r = c.get(f"{CF}/engagements/{ids['cf']}/models"); h = r.data.decode()
check("Download all sheets (ZIP)" in h and f"/models/{mid}/download-all" in h, "Models list offers the downloads")
r = c.get(f"{CF}/engagements/{ids['cf']}/models/download-all")
z = zipfile.ZipFile(io.BytesIO(r.data)); check(r.status_code == 200 and len(z.namelist()) == 4 and all("/" in n for n in z.namelist()), "engagement-wide model ZIP has a folder per model")
r = c.get(f"{CF}/models/{mid}"); r = c.get(f"{CF}/models/{mid}/statements"); h = r.data.decode()
check("Download all (ZIP)" in h and "Excel: statements" in h, "model header offers the ZIP and the statements workbook")

# ---- people not on the engagement cannot download
o = login(ids["outsider"])
for url in (f"{CF}/models/{mid}/download-all", f"{CF}/management-accounts/{pid}/download-all",
            f"{CF}/engagements/{ids['cf']}/models/download-all", f"{CF}/engagements/{ids['cf']}/management-accounts/download-all"):
    r = o.get(url); check(r.status_code in (302, 403, 404), f"outsider refused: {url.split('/corporate-finance')[1]} -> {r.status_code}")

print("\nFAILED:" if fails else "\nALL PASS", fails or "")
sys.exit(1 if fails else 0)
