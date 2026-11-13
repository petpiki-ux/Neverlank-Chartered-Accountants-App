"""Tax Advisory engagements keep the Asset Register (its own tab, with the depreciation classes), gain the Accounting tab (capture a ledger and roll it
up to a trial balance) and the Trial Balance tab (import), and drop Understanding the Entity and Planning. The Income Tax Computation takes its profit
before tax from the trial balance once there is one."""
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

with app.app_context():
    mp = User(username="mp", name="Managing Partner", role="admin"); mp.set_password("x")
    ptn = User(username="ptn", name="Tax Partner", role="partner"); ptn.set_password("x")
    db.session.add_all([mp, ptn]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=mp.id); db.session.add(cl); db.session.commit()
    tax = Engagement(client_id=cl.id, title="Tax 2026", type=TAX_ADVISORY_TYPE, partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=False)
    aud = Engagement(client_id=cl.id, title="Audit 2026", type="Audit", partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=False)
    db.session.add_all([tax, aud]); db.session.commit()
    T, A = tax.id, aud.id
    M = login(mp.id)
    def page(eid, tab, **kw):
        r = M.get(f"/engagements/{eid}", query_string=dict(tab=tab, **kw), follow_redirects=True)
        return r, r.get_data(as_text=True)
    def link(h, tab): return f"tab={tab}\"" in h

    check(tax.has_accounting_module and not aud.has_accounting_module, "Tax Advisory has the accounting module; Audit does not unless ticked")

    # ---- the tab bar
    r, h = page(T, "overview")
    for t in ("asset_register", "accounting", "trial_balance"):
        check(link(h, t), f"Tax Advisory: the {t} tab is there")
    for t in ("entity", "planning", "substantive", "analytical", "risks"):
        check(not link(h, t), f"Tax Advisory: no {t} tab")
    for t in ("entity", "planning"):
        r, h = page(T, t)
        check(r.status_code == 200 and "Understanding the Entity" not in h, f"an old {t} link on Tax Advisory lands on the overview")
    r, h = page(A, "overview")
    check(link(h, "entity") and link(h, "planning") and link(h, "asset_register"), "Audit keeps Entity and Planning and also has the Asset Register tab")
    r, h = page(A, "asset_register")
    check(r.status_code == 200 and "Opening balances as at" in h, "an Audit engagement's Asset Register tab opens with the opening balances card")

    # ---- the Asset Register tab
    r, h = page(T, "asset_register")
    check(r.status_code == 200 and "Depreciation policy" in h and "Add class" in h and "Asset Register" in h, "the Asset Register tab holds the depreciation classes and the register")
    r = M.post(f"/engagements/{T}/ppe/asset-classes/add", data={"name": "Computers"}, headers={"Referer": f"/engagements/{T}?tab=asset_register"})
    check(r.status_code == 302 and "tab=asset_register" in r.headers["Location"], "adding a class returns to the Asset Register tab")
    cid = PPEAssetClass.query.filter_by(engagement_id=T).first().id
    r = M.post(f"/engagements/{T}/ppe/assets/add", data={"asset_code": "A1", "description": "Laptop", "asset_class_id": cid, "date_acquired": "2026-03-01", "cost": 1000, "current_year_depreciation": 100},
               headers={"Referer": f"/engagements/{T}?tab=asset_register"})
    check(r.status_code == 302 and "tab=asset_register" in r.headers["Location"], "adding an asset returns to the Asset Register tab")
    r, h = page(T, "asset_register")
    check("Laptop" in h, "the asset shows on the register")
    r, h = page(T, "overview")
    check("Asset Register (1)" in h, "the tab shows the asset count")
    r, h = page(T, "capital_allowances")
    check(r.status_code == 200 and "Laptop" in h, "the Capital Allowances tab works from the same register")

    # ---- the Accounting tab
    r, h = page(T, "accounting")
    check(r.status_code == 200 and "Chart of Accounts" in h and "Roll up to Trial Balance" in h, "the Accounting tab opens on Tax Advisory")
    check("lives on the" not in h or "Substantive Procedures</a> tab, alongside" not in h, "it does not point to a Substantive Procedures tab that Tax Advisory lacks")
    for code, name, typ, ob in (("1000", "Bank", "Asset", 0), ("4000", "Sales", "Income", 0), ("6000", "Admin expenses", "Expense", 0)):
        M.post(f"/accounting/{T}/gl-accounts/add", data={"code": code, "name": name, "account_type": typ, "opening_balance": ob})
    accs = {a.code: a.id for a in GLAccount.query.filter_by(engagement_id=T).all()}
    check(len(accs) == 3, "accounts are added to the chart of accounts")
    M.post(f"/accounting/{T}/journal-entries/add", data={"entry_date": "2026-06-30", "reference": "J1", "narration": "Sales and costs"})
    je = JournalEntry.query.filter_by(engagement_id=T).first()
    for acc, dr, cr in (("1000", 200000, 0), ("4000", 0, 200000), ("6000", 80000, 0), ("1000", 0, 80000)):
        M.post(f"/accounting/journal-entries/{je.id}/lines/add", data={"gl_account_id": accs[acc], "description": "x", "debit": dr, "credit": cr})
    r = M.post(f"/accounting/{T}/gl/roll-up-to-tb")
    check(r.status_code == 302 and "tab=trial_balance" in r.headers["Location"], "rolling up returns to the Trial Balance tab on Tax Advisory")
    tb = TrialBalance.query.filter_by(engagement_id=T).first()
    check(tb is not None and len(tb.lines) == 3, "the ledger rolled up to a trial balance")
    cats = {"1000": "cash", "4000": "revenue", "6000": "admin_expenses"}
    for l in tb.lines: l.fs_category = cats[l.account_code]
    db.session.commit()
    r, h = page(T, "trial_balance")
    check(r.status_code == 200 and "Sales" in h, "the Trial Balance tab shows the rolled-up trial balance")

    # ---- the Income Tax Computation takes profit before tax from it
    r, h = page(T, "income_tax")
    check(r.status_code == 200 and "Start the computation" not in h and "120,000.00" in h, "the Income Tax Computation takes profit before tax (120,000) from the trial balance")
    import engagement_tax as et
    with app.test_request_context():
        check(abs(et.profit_before_tax_for(db.session.get(Engagement, T)) - 120000) < 0.01, "profit_before_tax_for reads the trial balance")

print(f"\n{len(fails)} failure(s)")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
