"""Asset Register: how an asset was paid for (the correcting leg) and one ledger sub-account per asset class under Property, plant and
equipment. Hand-worked amounts: laptop 1,200 (bank), printer 600 (credit), vehicle 10,000 (shareholder), desk 300 (not stated)."""
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
    bank = CashAccount(name="Bank", currency="USD", opening_balance=0.0, opening_date=date(2025, 1, 1), default_in=True, default_out=True)
    zbank = CashAccount(name="ZWG Bank", currency="ZWG", opening_balance=0.0, opening_date=date(2025, 1, 1))
    comp = FirmAssetClass(name="Computer equipment", depreciation_method="Straight-line", rate_percent=25.0, order=1)
    motor = FirmAssetClass(name="Motor vehicles", depreciation_method="Straight-line", rate_percent=20.0, order=2)
    db.session.add_all([bank, zbank, comp, motor]); db.session.commit()
    pid, sid, bank_id, zbank_id, comp_id, motor_id = partner.id, staff.id, bank.id, zbank.id, comp.id, motor.id

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
    ids = {a.system_key: a.id for a in (fl.ensure_chart() and None) or FirmAccount.query.filter(FirmAccount.system_key.isnot(None)).all()}
    other_payables = FirmAccount.query.filter_by(code="2050").first().id
    share_capital = FirmAccount.query.filter_by(code="3000").first().id
    other_recv = FirmAccount.query.filter_by(code="1110").first().id
    trade_ar = FirmAccount.query.filter_by(system_key="AR").first().id

def base(desc, cls, on, cost):
    return {"description": desc, "asset_class_id": str(cls), "date_acquired": on, "cost": str(cost), "residual_value": "0"}

# ---------------------------------------------------------------- the form's refusals
r = P.post(F + "/assets/new", data=dict(base("Bad", comp_id, "2025-03-10", 100), acq_method="bank", acq_cash_account_id=zbank_id), follow_redirects=True)
check(b"not a USD account" in r.data, "a ZWG account is refused for the payment")
r = P.post(F + "/assets/new", data=dict(base("Bad", comp_id, "2025-03-10", 100), acq_method="credit", acq_account_credit=share_capital), follow_redirects=True)
check(b"Choose the account the supplier is owed through" in r.data, "an equity account is refused for 'bought on credit'")
r = P.post(F + "/assets/new", data=dict(base("Bad", comp_id, "2025-03-10", 0), acq_method="bank", acq_cash_account_id=bank_id), follow_redirects=True)
check(b"above zero" in r.data, "a bank payment needs a cost above zero")
r = P.post(F + "/assets/new", data=dict(base("Bad", comp_id, "2025-03-10", 100), acq_method="bank"), follow_redirects=True)
check(b"Choose the bank or cash account" in r.data, "the bank account must be chosen")
with app.app_context():
    check(FirmAsset.query.count() == 0 and CashEntry.query.count() == 0, "nothing was saved by the refused forms")
check(S.post(F + "/assets/new", data=dict(base("x", comp_id, "2025-03-10", 100))).status_code == 403, "staff cannot add assets")
r = P.get(F + "/assets/new")
check(r.status_code == 200 and b"How was it paid for?" in r.data and b"Provided by a shareholder" in r.data and b"2050 Other payables" in r.data and b"3000 Share capital" in r.data, "the form offers bank, credit and shareholder, with the liability and equity accounts")
check(b"1010" not in r.data.split(b'name="acq_account_credit"')[1].split(b"</select>")[0], "no bank account is offered as a ledger account")

# ---------------------------------------------------------------- four assets
P.post(F + "/assets/new", data=dict(base("Laptop", comp_id, "2025-03-10", 1200), acq_method="bank", acq_cash_account_id=bank_id))
P.post(F + "/assets/new", data=dict(base("Printer", comp_id, "2025-04-01", 600), acq_method="credit", acq_account_credit=other_payables))
P.post(F + "/assets/new", data=dict(base("Pickup truck", motor_id, "2025-05-01", 10000), acq_method="shareholder", acq_account_shareholder=share_capital))
P.post(F + "/assets/new", data=dict(base("Desk", "", "2025-06-01", 300)))
with app.app_context():
    a1, a2, a3, a4 = [FirmAsset.query.filter_by(asset_code=f"FA-000{i}").first() for i in (1, 2, 3, 4)]
    check(a1 and a2 and a3 and a4, "four assets FA-0001 to FA-0004 were added")
    e = CashEntry.query.get(a1.acq_cash_entry_id) if a1.acq_cash_entry_id else None
    check(e is not None and e.account_id == bank_id and e.direction == "out" and near(e.amount, 1200) and e.category == "Other payment" and e.entry_date == date(2025, 3, 10) and e.reference == "FA-0001",
          "the laptop's USD 1,200 payment was entered in the Cash Book for it (out, Other payment, 10 Mar 2025)")
    check(CashEntry.query.count() == 1 and a2.acq_cash_entry_id is None and a3.acq_cash_entry_id is None, "credit and shareholder purchases make no Cash Book entry")
    check(a2.acq_method == "credit" and a2.acq_account_id == other_payables and a3.acq_method == "shareholder" and a3.acq_account_id == share_capital and not a4.acq_method, "the choices are kept on each asset")

def build():
    with app.app_context():
        yes = FirmYearEnd.query.all()
        L = fl.build_ledger(year_ends=yes)
        return L, fl.allocation_proposals(L)

# ---------------------------------------------------------------- sub-accounts
L, pl = build()
with app.app_context():
    bal = fl.account_balances(L, date(2025, 12, 31), date(2025, 1, 1))
    def b(key): return round(bal.get(L.by_key[key].id, 0.0), 2)
    ck, mk = f"PPE_COST:{comp_id}", f"PPE_COST:{motor_id}"
    check(near(b(ck), 1800) and near(b(mk), 10000) and near(b("PPE_COST"), 300), "cost: computers 1,800, vehicles 10,000 and the unclassified desk 300 stay in 1500")
    check(near(b(f"PPE_ACCDEP:{comp_id}"), -362.5) and near(b(f"PPE_ACCDEP:{motor_id}"), -1333.33) and near(b("PPE_ACCDEP"), 0), "accumulated depreciation by class: 362.50 (250 + 112.50) and 1,333.33")
    check(near(b("DEPR"), 1695.83), "depreciation expense stays one account: 1,695.83")
    ca, ma = L.by_key[ck], L.by_key[mk]
    parent = L.by_key["PPE_COST"]
    check(ca.parent_id == parent.id and ca.code == parent.code + "-01" and ma.code == parent.code + "-02" and ca.fs_category == "ppe" and ca.name == "Computer equipment - cost",
          "sub-accounts 1500-01 and 1500-02 sit under 1500, mapped to Property, plant and equipment")
    check(L.by_key[f"PPE_ACCDEP:{comp_id}"].code == L.by_key["PPE_ACCDEP"].code + "-01", "and 1510-01 under accumulated depreciation")

# ---------------------------------------------------------------- the funding proposals
keys = {p["key"]: p for p in pl["proposals"]}
mk_bank = [k for k in keys if k.startswith("match|FA-0001|")]
check(len(mk_bank) == 1 and near(keys[mk_bank[0]]["amount"], 1200) and keys[mk_bank[0]]["kind"] == "match", "the laptop purchase matches its own Cash Book payment (1,200)")
p2, p3 = keys.get("fund|FA-0002|acq"), keys.get("fund|FA-0003|acq")
check(p2 and near(p2["amount"], 600) and p2["kind"] == "post" and p2["chosen"], "the printer: 600 funding posted to the account chosen")
check(p3 and near(p3["amount"], 10000) and p3["chosen"], "the truck: 10,000 funding posted to share capital")
check("fund|FA-0004|acq" not in keys, "the desk (nothing stated) has no funding proposal")
check(near(pl["balances"]["SUSP_ASSET"], -12100), "the clearing account holds all four purchases (12,100 credit)")
with app.app_context():
    t2 = [ln for ln in p2["lines"]]
    check(any(r_ == "SUSP_ASSET" and dr == 600 for r_, dr, cr, n in t2) and any(r_ == other_payables and cr == 600 for r_, dr, cr, n in t2), "printer journal: debit clearing 600, credit 2050 Other payables 600")
r = P.get(F + f"/ledger/clearing?ye={y1}")
check(b"Funding</b>" in r.data and b"Other payables" in r.data, "the clearing page lists the funding items")
import re
check(re.search(rb'value="fund\|FA-0002\|acq"[^>]*checked', r.data) is not None, "and ticks them (the choice was made on the asset)")

before = None
with app.app_context():
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    before = dict(cols[0]["totals"])
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": [mk_bank[0], "fund|FA-0002|acq", "fund|FA-0003|acq"]}, follow_redirects=True)
check(b"3 journal(s) posted" in r.data, "the three items were posted")
L, pl = build()
with app.app_context():
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    after = cols[0]["totals"]
    check(near(after.get("share_capital", 0) - before.get("share_capital", 0), 10000), "share capital rose by 10,000 in the statements")
    check(near(after.get("other_current_liabilities", 0) - before.get("other_current_liabilities", 0), 600), "other payables rose by 600 in the statements")
    check(near(after.get("ppe", 0), before.get("ppe", 0)), "property, plant and equipment is unchanged (it always came from the register)")
    check(fl.reconcile(L, cols[0])["different"] == 0, "the ledger agrees to the statements")
    check(near(pl["balances"]["SUSP_ASSET"], -300), "only the desk (300) is left in the clearing account")
    check(not [p for p in pl["proposals"] if p["key"].startswith(("fund|", "match|FA"))], "re-running proposes nothing more for assets")
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": ["fund|FA-0002|acq"]}, follow_redirects=True)
with app.app_context():
    check(FirmJournal.query.filter_by(alloc_key="fund|FA-0002|acq").count() == 1, "submitting a funding item twice never doubles it")

# ---------------------------------------------------------------- edits keep the Cash Book in step
P.post(F + f"/assets/{a1.id}/edit", data=dict(base("Laptop (Dell)", comp_id, "2025-03-12", 1200), acq_method="bank", acq_cash_account_id=bank_id))
with app.app_context():
    e = CashEntry.query.get(FirmAsset.query.get(a1.id).acq_cash_entry_id)
    check(CashEntry.query.count() == 1 and e.entry_date == date(2025, 3, 12) and "Dell" in e.description, "editing the asset updates its one Cash Book entry (no second payment)")
P.post(F + "/assets/new", data=dict(base("Scanner", comp_id, "2025-07-01", 50), acq_method="bank", acq_cash_account_id=bank_id))
with app.app_context():
    a5 = FirmAsset.query.filter_by(asset_code="FA-0005").first(); a5id = a5.id
    check(CashEntry.query.count() == 2, "a fifth asset adds a second Cash Book entry")
P.post(F + f"/assets/{a5id}/edit", data=dict(base("Scanner", comp_id, "2025-07-01", 50), acq_method="credit", acq_account_credit=other_payables))
with app.app_context():
    check(CashEntry.query.count() == 1 and FirmAsset.query.get(a5id).acq_cash_entry_id is None, "changing it to 'on credit' removes the Cash Book entry the app made")
P.post(F + f"/assets/{a5id}/edit", data=dict(base("Scanner", comp_id, "2025-07-01", 50), acq_method="bank", acq_cash_account_id=bank_id))
P.post(F + f"/assets/{a5id}/delete")
with app.app_context():
    check(FirmAsset.query.filter_by(id=a5id).first() is None and CashEntry.query.count() == 1, "removing the asset removes its Cash Book entry too")

# ---------------------------------------------------------------- disposals
r = P.post(F + f"/assets/{a1.id}/dispose", data={"disposed_on": "2025-12-01", "disposal_proceeds": "700", "disp_method": "bank"}, follow_redirects=True)
check(b"Choose the bank or cash account" in r.data, "a bank sale needs the account")
P.post(F + f"/assets/{a1.id}/dispose", data={"disposed_on": "2025-12-01", "disposal_proceeds": "700", "disp_method": "bank", "disp_cash_account_id": bank_id})
P.post(F + f"/assets/{a4.id}/dispose", data={"disposed_on": "2025-11-30", "disposal_proceeds": "100", "disp_method": "credit", "disp_account_credit": other_recv})
with app.app_context():
    x = FirmAsset.query.get(a1.id)
    e = CashEntry.query.get(x.disp_cash_entry_id)
    check(e is not None and e.direction == "in" and near(e.amount, 700) and e.category == "Other receipt" and e.entry_date == date(2025, 12, 1), "the laptop's USD 700 sale proceeds were entered as a Cash Book receipt")
    check(FirmAsset.query.get(a4.id).disp_method == "credit" and FirmAsset.query.get(a4.id).disp_account_id == other_recv, "the desk's sale on credit is kept")
L, pl = build()
keys = {p["key"]: p for p in pl["proposals"]}
dk = [k for k in keys if k.startswith("match|FA-0001|") and k not in mk_bank]
check(len(dk) == 1 and near(keys[dk[0]]["amount"], 700), "the laptop's proceeds match the Cash Book receipt (700)")
d4 = keys.get("fund|FA-0004|disp")
check(d4 and near(d4["amount"], 100) and d4["chosen"], "the desk's 100 proceeds are proposed to Other receivables")
with app.app_context():
    sa = d4["lines"]
    check(any(r_ == "SUSP_ASSET" and cr == 100 for r_, dr, cr, n in sa) and any(r_ == other_recv and dr == 100 for r_, dr, cr, n in sa), "journal: debit 1110 Other receivables 100, credit clearing 100")
    bal = fl.account_balances(L, date(2025, 12, 31), date(2025, 1, 1))
    def b2(key): return round(bal.get(L.by_key[key].id, 0.0), 2)
    check(near(b2(f"PPE_COST:{comp_id}"), 600) and near(b2(f"PPE_ACCDEP:{comp_id}"), -112.5) and near(b2("PPE_COST"), 0), "after the sales: computers cost 600 less 112.50 depreciation; the desk is gone from 1500")
    check(near(b2("DISP_GL"), 475), "losses on disposal: laptop 275 (700 + 225 - 1,200) and desk 200 (100 - 300) = 475 debit")
r = P.post(F + "/ledger/clearing/apply", data={"ye_id": y1, "mode": "post", "apply": [dk[0], "fund|FA-0004|disp"]}, follow_redirects=True)
check(b"2 journal(s) posted" in r.data, "the two sale items were posted")
L, pl = build()
with app.app_context():
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    check(fl.reconcile(L, cols[0])["different"] == 0, "the ledger still agrees to the statements")
    check(near(pl["balances"]["SUSP_ASSET"], -300), "only the desk's unstated purchase (300) is left in the asset clearing account")
    tb = fl.trial_balance(L, date(2025, 12, 31))
    check(tb["balanced"], "the trial balance balances")
    grp = [r_ for r_ in tb["rows"] if r_.get("group_total")]
    check(len(grp) == 2 and {g["account"].system_key for g in grp} == {"PPE_COST", "PPE_ACCDEP"}, "it shows a group total under 1500 and 1510")
    cg = next(g for g in grp if g["account"].system_key == "PPE_COST")
    check(near(cg["debit"], 10600), "the 1500 group total is the whole cost: computers 600 + vehicles 10,000")
    check(near(sum(r_["debit"] for r_ in tb["rows"] if not r_.get("group_total")), tb["debit"]), "group totals are not added into the trial balance totals")
    check(tb["rows"].index(cg) > 0 and tb["rows"][tb["rows"].index(cg) - 1]["account"].parent_id == cg["account"].id, "the group total comes straight after its sub-accounts")

# ---------------------------------------------------------------- pages
r = P.get(F + f"/ledger/trial-balance?ye={y1}")
check(r.status_code == 200 and b"and its sub-accounts" in r.data and b"Computer equipment - cost" in r.data, "the trial balance page lists the sub-accounts and the group total")
r = P.get(F + "/ledger/trial-balance.xlsx?ye=%d" % y1)
check(r.status_code == 200 and r.data[:2] == b"PK", "the Excel export still works")
r = P.get(F + "/ledger/accounts")
check(r.status_code == 200 and b"1500-01" in r.data and b"Motor vehicles - cost" in r.data, "the chart of accounts lists the sub-accounts")
with app.app_context():
    pcost = FirmAccount.query.filter_by(system_key="PPE_COST").first().id
r = P.get(F + f"/ledger/account?account={pcost}&ye={y1}")
check(r.status_code == 200 and b"Includes its sub-accounts" in r.data and b"Computer equipment - cost" in r.data, "the ledger of 1500 includes its sub-accounts")
r = P.get(F + "/assets")
check(r.status_code == 200 and b"How was the sale paid?" in r.data, "the disposal pop-up asks how the sale was paid")
# a new class gets its own sub-accounts on the next ledger page
P.post(F + "/assets/classes/new", data={"name": "Furniture", "depreciation_method": "Straight-line", "rate_percent": "10"})
P.get(F + "/ledger/accounts")
with app.app_context():
    fcls = FirmAssetClass.query.filter_by(name="Furniture").first()
    check(fcls is None or FirmAccount.query.filter_by(system_key=f"PPE_COST:{fcls.id}").first() is not None, "a new asset class gets its sub-accounts")
    c = FirmAssetClass.query.get(comp_id); c.name = "Computers"; db.session.commit()
P.get(F + "/ledger/accounts")
with app.app_context():
    check(FirmAccount.query.filter_by(system_key=f"PPE_COST:{comp_id}").first().name == "Computers - cost", "renaming the class renames its sub-account")
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
