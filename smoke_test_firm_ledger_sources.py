"""Ledger postings for the less common sources - a legacy-paid invoice, an advance refunded, a transfer between cash accounts,
a ZWG cash account and ZWG bill translated at the year-end rate, an asset sold at a loss, figures entered on two year-ends
(a balance and a period figure) - checked against numbers worked out by hand, and shown to agree to the statements."""
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
from datetime import date, datetime
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
    db.session.add(partner); db.session.commit()
    cl = Client(name="Beta Traders", created_by_id=partner.id)
    db.session.add(cl); db.session.commit()
    inv = Invoice(invoice_number="INV-L", client_id=cl.id, currency="USD", vat_pct=0.0, status="Paid", issue_date=date(2025, 2, 1),
                  due_date=date(2025, 2, 1), paid_at=datetime(2025, 2, 15, 9, 0))
    inv.lines.append(InvoiceLineItem(description="Fee", quantity=1, unit_price=1000)); db.session.add(inv)
    adv = ClientAdvance(advance_number="ADV-9", client_id=cl.id, currency="USD", amount=400, received_on=date(2025, 3, 1), method="Bank transfer")
    db.session.add(adv); db.session.commit()
    db.session.add(ClientAdvanceRefund(advance_id=adv.id, amount=100, refunded_on=date(2025, 3, 20), method="Bank transfer"))
    db.session.add(Expense(expense_date=date(2025, 5, 1), supplier="Harare Landlord", category="Rent & premises", amount=500, currency="ZWG", status="Unpaid"))
    cls = FirmAssetClass(name="Vehicles", depreciation_method="Straight-line", rate_percent=20.0, order=0)
    db.session.add(cls); db.session.commit()
    db.session.add(FirmAsset(asset_code="FA-0001", description="Car", asset_class_id=cls.id, date_acquired=date(2025, 2, 1), cost=3000.0,
                             disposed_on=date(2025, 9, 30), disposal_proceeds=2000.0))
    b1 = CashAccount(name="Bank 1", currency="USD", opening_balance=0.0, opening_date=date(2025, 1, 1), default_in=True, default_out=True)
    b2 = CashAccount(name="Bank 2", currency="USD", opening_balance=0.0, opening_date=date(2025, 1, 1))
    bz = CashAccount(name="ZWG account", currency="ZWG", opening_balance=2500.0, opening_date=date(2025, 1, 1), default_in=True, default_out=True)
    db.session.add_all([b1, b2, bz]); db.session.commit()
    tid = "11111111-1111-1111-1111-111111111111"
    db.session.add(CashEntry(account_id=b1.id, entry_date=date(2025, 4, 1), direction="out", amount=200, category=CASH_TRANSFER_CATEGORY, transfer_id=tid))
    db.session.add(CashEntry(account_id=b2.id, entry_date=date(2025, 4, 1), direction="in", amount=200, category=CASH_TRANSFER_CATEGORY, transfer_id=tid))
    db.session.commit()
    pid = partner.id
    b1id, b2id, bzid = b1.id, b2.id, bz.id

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P = login(pid)
F = "/firm-finance"
P.post(F + "/year-ends/new", data={"period_end": "2025-12-31", "zwg_rate": "25", "opening_nil": "1"})
P.post(F + "/year-ends/new", data={"period_end": "2026-12-31", "zwg_rate": "25"})
with app.app_context():
    y1 = FirmYearEnd.query.filter_by(period_end=date(2025, 12, 31)).first()
    y2 = FirmYearEnd.query.filter_by(period_end=date(2026, 12, 31)).first()
    y1.tax_manual = True; y2.tax_manual = True; db.session.commit()
    y1, y2 = y1.id, y2.id
# a balance entered on both year-ends (5,000 then 6,000) and a period figure on the second
P.post(F + f"/year-ends/{y1}/lines/add", data={"fs_category": "share_capital", "description": "Share capital", "amount": "5000", "currency": "USD"})
P.post(F + f"/year-ends/{y2}/lines/add", data={"fs_category": "share_capital", "description": "Share capital", "amount": "6000", "currency": "USD"})
P.post(F + f"/year-ends/{y2}/lines/add", data={"fs_category": "admin_expenses", "description": "Staff salaries", "amount": "800", "currency": "USD"})
P.post(F + f"/year-ends/{y2}/lines/add", data={"fs_category": "admin_expenses", "description": "ZWG levy", "amount": "250", "currency": "ZWG"})

with app.app_context():
    yes = sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end)
    L = fl.build_ledger(year_ends=yes)
    byk = {k: a.id for k, a in L.by_key.items()}
    e1, e2 = date(2025, 12, 31), date(2026, 12, 31)
    bal1 = fl.account_balances(L, e1, date(2025, 1, 1))
    def b(key, bal=bal1): return round(bal.get(byk[key], 0.0), 2)
    check(near(b(f"CASH:{b1id}"), 1100), "bank 1 = legacy-paid invoice 1,000 + advance 400 - refund 100 - transfer out 200 = 1,100")
    check(near(b(f"CASH:{b2id}"), 200), "bank 2 = the 200 transferred in")
    check(near(b(f"CASH:{bzid}"), 100), "ZWG account: 2,500 ZWG at 25 = 100 USD")
    check(near(b("CLR_TRANSFER"), 0), "the transfer between two cash accounts nets to nil in its clearing account")
    check(near(b("ADV"), -300), "advances held 300 = 400 received - 100 refunded")
    check(near(b("AR"), 0), "the legacy-paid invoice leaves nothing owing")
    check(near(b("REV:Not linked to an engagement"), -1000), "fees 1,000")
    check(near(b("AP"), -20) and near(b("EXP:Rent & premises"), 20), "the 500 ZWG bill is 20 USD at 25")
    check(near(b("PPE_COST"), 0) and near(b("PPE_ACCDEP"), 0), "the sold car is out of cost and out of accumulated depreciation")
    check(near(b("DEPR"), 350), "depreciation 350 = 3,000 x 20% x 7 months (Feb to Aug) / 12")
    check(near(b("DISP_GL"), 650), "loss on disposal 650 = proceeds 2,000 against net book value 2,650 (debit)")
    check(near(b("SUSP_ASSET"), -1000), "asset clearing: purchase 3,000 (credit) less sale proceeds 2,000 (debit)")
    check(near(b("SUSP_OPEN"), 5000 - 100), "opening clearing: share capital entered 5,000 less the ZWG account's opening 100")
    cols = fs.build_chain(yes, *fs.load_register())
    t = cols[0]["totals"]
    check(near(t["other_expenses"], 650) and near(t["cash"], 1400) and near(t["depreciation_amortisation"], 350), "statements: loss on disposal 650, cash 1,400, depreciation 350")
    rec = fl.reconcile(L, cols[0])
    check(rec["different"] == 0, "year 1 agrees to the statements: " + ", ".join(f"{r['code']} {r['ledger']} vs {r['stmt']}" for r in rec["rows"] if abs(r["diff"]) >= 0.01))
    check(fl.trial_balance(L, e1)["balanced"], "year 1 trial balance balances")
    # year 2: the balance 5,000 -> 6,000 is posted as the 1,000 increase; the period figures in full
    sc = L.by_key["SUSP_OPEN"].id
    ent = [l for l in L.lines if l["source"] == "Entered figure" and l["date"] == e2]
    by_acc = {}
    for l in ent:
        by_acc.setdefault(L.accounts[l["account_id"]].fs_category, 0.0)
        by_acc[L.accounts[l["account_id"]].fs_category] += l["cr"] - l["dr"]
    check(near(by_acc.get("share_capital", 0), 1000), "share capital entered as 5,000 then 6,000 posts only the 1,000 increase in year 2")
    check(near(by_acc.get("admin_expenses", 0), -(800 + 10)), "period figures post in full: salaries 800 and the ZWG levy 250 / 25 = 10")
    rec2 = fl.reconcile(L, cols[1])
    check(rec2["different"] == 0, "year 2 agrees to the statements: " + ", ".join(f"{r['code']} {r['ledger']} vs {r['stmt']}" for r in rec2["rows"] if abs(r["diff"]) >= 0.01))
    check(fl.trial_balance(L, e2)["balanced"], "year 2 trial balance balances")
    check(near(cols[1]["totals"]["share_capital"], 6000), "the statements show share capital 6,000 (a balance, not 5,000 + 6,000)")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
