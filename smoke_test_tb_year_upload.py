"""Upload one year's trial balance on its own (2025, or 2024) - merged into the engagement's trial balance."""
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
import tb_year_import as ty
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
def xlsx(rows, sheet="TB", extra_sheets=()):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = sheet
    for r in rows: ws.append(r)
    for n, rr in extra_sheets:
        w = wb.create_sheet(n)
        for r in rr: w.append(r)
    b = io.BytesIO(); wb.save(b); return b.getvalue()

# ---- the reader
check(ty.to_num("1,234.50") == (1234.5, None) and ty.to_num("(1,234.50)") == (-1234.5, None) and ty.to_num("1 234.50")[0] == 1234.5, "figures: 1,234.50, (1,234.50), 1 234.50")
check(ty.to_num("500.00 Cr") == (500.0, "cr") and ty.to_num("-") == (None, None) and ty.to_num(None) == (None, None), "figures: Cr marks, dashes, blanks")
rows = [["Neverlank Client (Pvt) Ltd"], ["Trial balance as at 31 December 2025"], [],
        ["Acc No", "Account Description", "Debit", "Credit"],
        ["1000", "Property, plant and equipment", 1313007, None], ["1100", "Cash at bank", 500000, ""], ["2000", "Share capital", None, 1000],
        ["3000", "Retained earnings", "", 1812007], ["", "ASSETS (heading)", None, None], ["", "Total", 1813007, 1813007]]
acc, _ = ty.parse_year_file(rows, "2025")
check([a["name"] for a in acc] == ["Property, plant and equipment", "Cash at bank", "Share capital", "Retained earnings"], "header found below a title; headings and the total row are left out")
check(acc[0]["code"] == "1000" and acc[0]["debit"] == 1313007 and acc[3]["credit"] == 1812007, "code, debit and credit columns read")
rows2 = [["Code", "Name", "Closing balance"], ["1", "Plant", "1,000.00"], ["2", "Loan", "(400.00)"], ["3", "Equity", "600.00 Cr"], ["4", "Bank", "0"]]
acc2, _ = ty.parse_year_file(rows2, "2025")
d = {a["name"]: a for a in acc2}
check(d["Plant"]["debit"] == 1000 and d["Loan"]["credit"] == 400 and d["Equity"]["credit"] == 600, "a single signed balance column: brackets and Cr are credits")
rows3 = [["Account Name", "2024 Debit", "2024 Credit", "2025 Debit", "2025 Credit"], ["Plant", 100, 0, 150, 0], ["Equity", 0, 100, 0, 150]]
a25, _ = ty.parse_year_file(rows3, "2025"); a24, _ = ty.parse_year_file(rows3, "2024")
check(a25[0]["debit"] == 150 and a24[0]["debit"] == 100, "a file with both years: the columns for the chosen year are used")
try:
    ty.parse_year_file([["Hello", "World"], ["a", "b"]], "2025"); check(False, "an unreadable file is refused")
except ValueError:
    check(True, "an unreadable file is refused")

# ---- merging into a trial balance
with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Penanel", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    EID, UID, CID = e.id, u.id, cl.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(UID); s["_fresh"] = True
URL = f"/engagements/{EID}/trial-balance/upload-year"
import re
def preview(year, fileobj, name):
    if isinstance(fileobj, (bytes, io.BytesIO)):
        fileobj = io.BytesIO(fileobj if isinstance(fileobj, bytes) else fileobj.getvalue())
    r = c.post(URL, data={"year": year, "file": (fileobj, name)}, content_type="multipart/form-data", follow_redirects=True)
    return r.data.decode()
def fields_of(h):
    """what the browser would post from the confirm form, with the default ticks"""
    m = re.search(r'<form method="post" action="([^"]*/confirm)"', h)
    if not m: return None, None
    body = h[m.end():h.index("</form>", m.end())]
    data = {}
    for t in re.findall(r'<input type="hidden"[^>]*>', body):
        n = re.search(r'name="([^"]*)"', t).group(1); v = re.search(r'value="([^"]*)"', t).group(1); data[n] = v
    data["inc"] = [re.search(r'value="(\d+)"', t).group(1) for t in re.findall(r'<input type="checkbox" class="inc"[^>]*>', body) if " checked " in t or t.rstrip(">").endswith("checked")]
    return m.group(1), data
def upload(year, fileobj, name, drop=()):
    h = preview(year, fileobj, name)
    action, data = fields_of(h)
    assert action, "no preview form: " + h[:300]
    data["inc"] = [i for i in data["inc"] if i not in drop]
    r = c.post(action, data=data, follow_redirects=True)
    return r.data.decode()

# 1. 2024 first, on its own, to an engagement with no trial balance
f24 = xlsx([["Code", "Account Name", "Debit", "Credit"], ["1000", "Property, plant and equipment", 869513, None], ["1100", "Cash at bank", 100000, None],
            ["2000", "Share capital", None, 1000], ["3000", "Retained earnings", None, 968513], ["4000", "Old supplier", None, 0]])
h0 = preview("prior", f24, "tb2024.xlsx")
check("before it is imported" in h0 and "Nothing has been changed yet" in h0, "the upload shows a preview first")
with app.app_context():
    check(TrialBalance.query.filter_by(engagement_id=EID).first() is None, "nothing is imported until the preview is confirmed")
h = upload("prior", f24, "tb2024.xlsx")
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=EID).first()
    L = {l.account_name: l for l in tb.lines}
    check(tb is not None and len(tb.lines) == 5 and L["Property, plant and equipment"].prior_debit == 869513 and L["Property, plant and equipment"].current_debit == 0, "the 2024 file creates the trial balance with the prior-year columns only")
    check("2024 trial balance read" in h, "the page says which year was read")
    L["Cash at bank"].fs_category = "cash"; L["Property, plant and equipment"].fs_category = "ppe"; db.session.commit()

# 2. the 2025 file on its own
f25 = xlsx([["Neverlank - trial balance"], ["Acc No", "Account Description", "Debit", "Credit"],
            ["1000", "Property, plant and equipment", 1313007, None], ["1100", "Cash at bank", 655000, None], ["2000", "Share capital", None, 1000],
            ["3000", "Retained earnings", None, 968513], ["5000", "New expense", 0, 0], ["6000", "Revenue", None, 999494], ["7000", "Expenses", 1000, None],
            ["", "Total", 1969007, 1969007]], sheet="TB 2025")
h = upload("current", f25, "tb2025.xlsx")
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=EID).first()
    L = {l.account_name: l for l in tb.lines}
    check(L["Property, plant and equipment"].current_debit == 1313007 and L["Property, plant and equipment"].prior_debit == 869513, "2025 figures set; the 2024 figures kept on the same line")
    check(L["Property, plant and equipment"].fs_category == "ppe" and L["Cash at bank"].fs_category == "cash", "the IAS 1 categories already chosen are kept")
    check("Revenue" in L and L["Revenue"].current_credit == 999494 and L["Revenue"].prior_credit == 0 and L["Revenue"].fs_category is None, "a new account is added with no prior-year figures")
    check("Total" not in L, "the total row is not imported")
    check("Old supplier" not in L, "an old account with nothing in either year is dropped")
    check("matched" in h and "figures were kept" in h, "the result is summarised")
    check("do not balance" not in h, "a balanced file raises no warning")
    check(not [l for l in tb.lines if l.account_name == "Share capital" and l.prior_credit != 1000], "other accounts' prior figures untouched")

# 3. a file that does not balance, and an account missing from the file
f25b = xlsx([["Account Name", "Debit", "Credit"], ["Property, plant and equipment", 1313007, None], ["Cash at bank", 600000, None], ["Share capital", None, 1000]])
h = upload("current", f25b, "tb2025b.xlsx")
check("do not balance" in h, "a file whose debits and credits differ is flagged")
with app.app_context():
    tb = TrialBalance.query.filter_by(engagement_id=EID).first()
    L = {l.account_name: l for l in tb.lines}
    check(L["Retained earnings"].current_credit == 0 and L["Retained earnings"].prior_credit == 968513, "an existing account missing from the file is set to nil for that year only")
    check("Revenue" not in L, "an account with nothing in the other year is dropped when it is missing from the file")

# 4. bad files
r = c.post(URL, data={"year": "current"}, follow_redirects=True); check("choose a file" in r.data.decode(), "no file: asked to choose one")
r = c.post(URL, data={"year": "current", "file": (io.BytesIO(b"hello"), "x.pdf")}, content_type="multipart/form-data", follow_redirects=True); check("xlsx or .csv" in r.data.decode(), "wrong type: refused")
csvb = b"Account Name;Debit;Credit\nPlant;100;\nEquity;;100\n"
upload("current", csvb, "t.csv")
with app.app_context():
    check({l.account_name for l in TrialBalance.query.filter_by(engagement_id=EID).first().lines} >= {"Plant", "Equity"}, "a semicolon CSV is read too")

# 5. the Trial Balance tab offers it
h = c.get(f"/engagements/{EID}?tab=trial_balance").data.decode()
check("upload-year" in h and "on its own" in h, "the Trial Balance tab offers 'upload on its own' for each year")

# 6. rows that are not accounts: subtotals, a second block, a total without the word total
rows = [["Code", "Account", "Debit", "Credit"],
        ["1", "Cash", 100, None], ["2", "Debtors", 200, None], ["", "Current assets", 300, None],            # subtotal of the 2 rows above
        ["3", "Share capital", None, 400], ["4", "Loan", None, 100], ["", "Equity and liabilities", None, 500],  # subtotal
        [], [], ["", "Summary", 300, 500]]
t = ty.read_table(rows, "2025")
fl = {i["name"]: i["flag"] for i in t["items"]}
check(fl["Cash"] == "" and fl["Debtors"] == "" and fl["Share capital"] == "" and fl["Loan"] == "", "ordinary accounts are not flagged")
check("subtotal" in fl["Current assets"] and "subtotal" in fl["Equity and liabilities"], "subtotals are flagged")
check(fl["Summary"] != "", "a block after a gap is flagged")
acc, _ = ty.parse_year_file(rows, "2025")
check(sum(a["debit"] for a in acc) == 300 and sum(a["credit"] for a in acc) == 500, "default import leaves out the flagged rows (200 difference remains: file genuinely unbalanced)")
rows_b = [["Code", "Account", "Debit", "Credit"], ["1", "Cash", 100, None], ["2", "Debtors", 200, None], ["3", "Equity", None, 300],
          ["", "Totals", 300, 300]]
t = ty.read_table(rows_b, "2025")
check([i["flag"] for i in t["items"]][-1] != "", "a totals line without 'total' in the name is flagged as the sum of all debits and credits")
check(ty.diagnose([i for i in t["items"] if not i["flag"]]) == [], "no hints when the ticked rows balance")
# an imbalance: a subtotal row ticked
items = [i for i in ty.read_table(rows, "2025")["items"]]
hints = ty.diagnose(items)
check(hints, "hints are given when ticked rows do not balance")
# a figure placed in the wrong column
wrong = [["Account", "Debit", "Credit"], ["Plant", 1000, None], ["Bank", 500, None], ["Equity", None, 1000], ["Loan", 500, None]]
hs = ty.diagnose(ty.read_table(wrong, "2025")["items"])
check(any("Loan" in x for x in hs), "the hint names the row that is half the difference (wrong column)")

# 7. the preview in the browser: flagged rows are unticked, and the user can tick them again; the sheet choice
f = xlsx([["Code", "Account", "Debit", "Credit"], ["1", "Cash", 100, None], ["2", "Debtors", 200, None], ["", "Current assets", 300, None],
          ["3", "Share capital", None, 300]], sheet="Sheet1", extra_sheets=[("TB 2025", [["Code", "Account", "Debit", "Credit"], ["1", "Bank", 50, None], ["2", "Capital", None, 50]])])
h = preview("current", f, "two.xlsx")
check("TB 2025" in h and "Bank" in h, "the sheet named for the year is chosen when the workbook has several")
action, data = fields_of(h)
r = c.post(action.replace("/confirm", "/preview"), data={"ext": data["ext"], "filename": data["filename"], "year": "current", "sheet": "Sheet1", "remap": "1"})
h2 = r.data.decode()
check("Current assets" in h2 and "looks like a subtotal" in h2, "choosing another sheet re-reads it and flags the subtotal")
a2, d2 = fields_of(h2)
check("2" in d2["inc"] and len(d2["inc"]) == 3, "the flagged subtotal is unticked by default (3 of 4 rows ticked)")
r = c.post(a2, data={**d2, "inc": []}, follow_redirects=True); check("no rows were ticked" in r.data.decode(), "nothing ticked: nothing imported")
# ticking the subtotal as well is allowed and imports it
d2["inc"] = d2["inc"] + [i for i in re.findall(r'name="inc" value="(\d+)"', h2) if i not in d2["inc"]]
r = c.post(a2, data=d2, follow_redirects=True)
with app.app_context():
    names = {l.account_name for l in TrialBalance.query.filter_by(engagement_id=EID).first().lines}
    check("Current assets" in names, "a row the user ticks is imported")
r = c.post(action.replace(re.search(r"/upload-year/([0-9a-f]{32})/", action).group(1), "0" * 32), data=d2, follow_redirects=True)
check("expired" in r.data.decode(), "an unknown upload token is refused")
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"); sys.exit(1 if fails else 0)
