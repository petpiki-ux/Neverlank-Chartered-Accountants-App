"""Clearing the clearing accounts: cash book entries are matched to the figures typed on the year-end (changing nothing in the
statements) or allocated to a real account (which adds them to the statements), checked against hand-worked amounts."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ.pop("ANTHROPIC_API_KEY", None)
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
import firm_statements as fs
import firm_ledger as fl
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005):
    return a is not None and abs(a - b) <= tol

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme", created_by_id=partner.id); db.session.add(cl); db.session.commit()
    inv = Invoice(invoice_number="INV-1", client_id=cl.id, currency="USD", vat_pct=15.0, status="Sent", issue_date=date(2025, 5, 5), due_date=date(2025, 5, 5))
    inv.lines.append(InvoiceLineItem(description="Fee", quantity=1, unit_price=1000)); db.session.add(inv)
    bank = CashAccount(name="Bank", currency="USD", opening_balance=0.0, opening_date=date(2025, 1, 1), default_in=True, default_out=True)
    db.session.add(bank); db.session.commit()
    for d, direction, amt, cat in ((date(2025, 1, 10), "in", 5000, "Owner / shareholder contribution"), (date(2025, 2, 1), "in", 1000, "Loan received"),
                                   (date(2025, 3, 1), "out", 200, "Dividend paid"), (date(2025, 6, 30), "out", 30, "Bank charges"),
                                   (date(2025, 7, 1), "out", 150, "Other payment")):
        db.session.add(CashEntry(account_id=bank.id, entry_date=d, direction=direction, amount=amt, category=cat, description=cat))
    db.session.commit()
    pid, sid = partner.id, staff.id

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(pid), login(sid)
F = "/firm-finance"
P.post(F + "/year-ends/new", data={"period_end": "2025-12-31", "zwg_rate": "", "opening_nil": "1"})
with app.app_context():
    y = FirmYearEnd.query.first(); y.tax_manual = True; db.session.commit(); y1 = y.id
for cat, desc, amt in (("share_capital", "Share capital", 5000), ("long_term_borrowings", "Director loan", 1000),
                       ("dividends_paid", "Dividends", 200), ("other_current_liabilities", "VAT payable", 150)):
    P.post(F + f"/year-ends/{y1}/lines/add", data={"fs_category": cat, "description": desc, "amount": str(amt), "currency": "USD"})


def plan():
    with app.app_context():
        yes = FirmYearEnd.query.all()
        L = fl.build_ledger(year_ends=yes)
        return L, fl.allocation_proposals(L)

def stm():
    with app.app_context():
        cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
        return cols[0]["totals"], cols

check(S.get(F + "/ledger/clearing").status_code == 403 and S.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "apply": "x"}).status_code == 403, "staff cannot clear the clearing accounts")
L, pl = plan()
by = {p["key"]: p for p in pl["proposals"]}
check(sorted(by) == sorted(["match|2025-12-31|vat", "match|2025-12-31|dividends_paid", "post|2025-12-31|finance_costs", "match|2025-12-31|long_term_borrowings", "match|2025-12-31|share_capital"]),
      "proposals: match VAT, dividends, loans and share capital; post the bank charges: " + ", ".join(sorted(by)))
check(near(by["match|2025-12-31|share_capital"]["amount"], 5000) and near(by["match|2025-12-31|long_term_borrowings"]["amount"], 1000)
      and near(by["match|2025-12-31|dividends_paid"]["amount"], 200) and near(by["match|2025-12-31|vat"]["amount"], 150), "matched amounts 5,000, 1,000, 200 and 150")
check(near(by["post|2025-12-31|finance_costs"]["amount"], 30), "bank charges 30 proposed as a post")
check(len(pl["choose"]) == 1 and near(pl["choose"][0]["amount"], -150) and pl["choose"][0]["category"] == "Other payment", "the 150 'Other payment' needs a chosen account")
check(near(pl["balances"]["SUSP_CASH"], -5620) and near(pl["balances"]["VAT_CTRL"], -150) and near(pl["balances"]["SUSP_OPEN"], 5950), "starting balances SUSP_CASH -5,620, VAT_CTRL -150, SUSP_OPEN 5,950")
r = P.get(F + f"/ledger/clearing?ye={y1}")
check(r.status_code == 200 and b"Post the ticked journals" in r.data and b"no obvious home" in r.data, "the clearing page opens with proposals and the choose list")

before, _ = stm()
matches = ["match|2025-12-31|vat", "match|2025-12-31|dividends_paid", "match|2025-12-31|long_term_borrowings", "match|2025-12-31|share_capital"]
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": matches}, follow_redirects=True)
check(b"4 journal(s) posted" in r.data, "the four matches were posted")
L, pl = plan()
after, cols = stm()
with app.app_context():
    check(after == before, "matching changes nothing in the statements")
    check(fl.reconcile(L, cols[0])["different"] == 0, "the ledger still agrees to the statements")
    check(near(pl["balances"]["SUSP_CASH"], 180) and near(pl["balances"]["VAT_CTRL"], 0) and near(pl["balances"]["SUSP_OPEN"], 0),
          "after matching: SUSP_CASH holds the bank charges and other payment (debit 180), VAT_CTRL and SUSP_OPEN are nil")
    check([p["key"] for p in pl["proposals"]] == ["post|2025-12-31|finance_costs"], "re-running proposes only the bank charges")
    j = FirmJournal.query.filter_by(alloc_key="match|2025-12-31|share_capital").first()
    check(j is not None and j.status == "posted" and j.journal_date == date(2025, 12, 31) and j.reference == "Auto-allocation", "the match journal is posted at the year-end with its reference")
# a second submit of the same item does nothing
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": matches}, follow_redirects=True)
with app.app_context():
    check(FirmJournal.query.filter_by(alloc_key="match|2025-12-31|share_capital").count() == 1, "submitting a match twice never doubles it")
# draft mode
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "draft", "apply": ["post|2025-12-31|finance_costs"]}, follow_redirects=True)
check(b"saved as drafts" in r.data, "a post can be saved as a draft")
with app.app_context():
    d = FirmJournal.query.filter_by(alloc_key="post|2025-12-31|finance_costs").first()
    check(d.status == "draft", "the draft is not in the ledger yet")
    L2 = fl.build_ledger()
    check(near(fl.allocation_proposals(L2)["balances"]["SUSP_CASH"], 180), "so the balances are unchanged")
    d_id = d.id
# posting the bank charges after the draft: the draft counts as covering it, so nothing more is proposed
L, pl = plan()
check(pl["proposals"] == [], "a draft already covers the bank charges, so no second proposal appears")
with app.app_context():
    d = FirmJournal.query.get(d_id); db.session.delete(d); db.session.commit()
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": ["post|2025-12-31|finance_costs"]}, follow_redirects=True)
check(b"1 journal(s) posted" in r.data, "the bank charges allocation was posted")
t, cols = stm()
L, pl = plan()
with app.app_context():
    check(near(t["finance_costs"], 30) and near(t["finance_costs"] - before["finance_costs"], 30), "bank charges now count in the statements (finance costs 30)")
    check(near(fs.column_profit(t)[1] - fs.column_profit(before)[1], -30), "profit falls by 30")
    check(fl.reconcile(L, cols[0])["different"] == 0, "the ledger agrees to the statements after the posting")
    check(near(pl["balances"]["SUSP_CASH"], 150), "SUSP_CASH now holds only the 150 'other payment'")
    exp_id = FirmAccount.query.filter(FirmAccount.fs_category == "admin_expenses", FirmAccount.is_active == True).first().id
    ck = pl["choose"][0]["key"]
# choose without an account is left alone
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": [ck], "choose_" + ck: ""}, follow_redirects=True)
check(b"no account was chosen" in r.data, "a chosen item with no account is left alone")
# closed period blocks
P.post(F + "/ledger/lock", data={"locked_through": "2025-12-31", "action": "lock"})
L, pl = plan()
check(pl["choose"][0]["blocked"] is not None, "a closed period blocks the choose item")
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": [ck], "choose_" + ck: str(exp_id)}, follow_redirects=True)
with app.app_context():
    check(FirmJournal.query.filter_by(alloc_key=ck).count() == 0, "nothing posted into a closed period")
P.post(F + "/ledger/lock", data={"action": "unlock", "locked_through": ""})
with app.app_context():
    check(fl.current_lock() is None, "the books can be reopened")
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": [ck], "choose_" + ck: str(exp_id)}, follow_redirects=True)
check(b"1 journal(s) posted" in r.data, "the loose 150 was posted to the chosen expense account")
t2, cols = stm()
L, pl = plan()
with app.app_context():
    check(near(t2["admin_expenses"] - t["admin_expenses"], 150), "admin expenses rise by 150")
    check(fl.reconcile(L, cols[0])["different"] == 0, "the ledger agrees to the statements")
    check(near(pl["balances"]["SUSP_CASH"], 0) and near(pl["balances"]["SUSP_OPEN"], 0) and near(pl["balances"]["VAT_CTRL"], 0), "all clearing accounts are now nil")
    check(pl["proposals"] == [] and pl["choose"] == [], "nothing left to place")
# reversing an allocation brings its proposal back
with app.app_context():
    jj = FirmJournal.query.filter_by(alloc_key="post|2025-12-31|finance_costs").first(); jid = jj.id
r = P.post(F + f"/ledger/journal/{jid}/reverse", data={"date": "2025-12-31"}, follow_redirects=True)
L, pl = plan()
check([p["key"] for p in pl["proposals"]] == ["post|2025-12-31|finance_costs"], "reversing the allocation makes the bank charges proposable again")

# ---- an asset bought with cash: the Asset register side and the cash book payment cancel
with app.app_context():
    comp = FirmAssetClass(name="Computer equipment", depreciation_method="Straight-line", rate_percent=25.0, order=0)
    db.session.add(comp); db.session.commit()
    db.session.add(FirmAsset(asset_code="FA-0001", description="Laptop", asset_class_id=comp.id, date_acquired=date(2025, 3, 15), cost=1200.0))
    db.session.add(CashEntry(account_id=CashAccount.query.first().id, entry_date=date(2025, 3, 16), direction="out", amount=1200, category="Other payment", description="Laptop purchase"))
    db.session.commit()
L, pl = plan()
am = [p for p in pl["proposals"] if p["key"].startswith("match|FA-0001|")]
check(len(am) == 1 and near(am[0]["amount"], 1200), "the laptop purchase 1,200 is matched to the cash book payment of 1,200")
check(near(pl["balances"]["SUSP_ASSET"], -1200 * -1) or abs(pl["balances"]["SUSP_ASSET"]) == 1200, "asset funding account holds 1,200 before")
before_a, _ = stm()
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": [am[0]["key"]]}, follow_redirects=True)
check(b"1 journal(s) posted" in r.data, "the asset match was posted")
L, pl = plan()
after_a, cols = stm()
with app.app_context():
    check(near(pl["balances"]["SUSP_ASSET"], 0), "asset funding account is nil")
    check(after_a == before_a, "no change to the statements")
    check(fl.reconcile(L, cols[0])["different"] == 0, "ledger still agrees to the statements")
    check(not any(p["key"].startswith("match|FA-0001|") for p in pl["proposals"]), "the asset is not proposed again")
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
