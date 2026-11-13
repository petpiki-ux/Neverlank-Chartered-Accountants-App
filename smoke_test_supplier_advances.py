"""Advances (prepayments) paid to suppliers: recorded through the supplier's account, held as an asset (Advances to suppliers), applied to the
supplier's later bills with no cash moving, or refunded. Checked against the cash book, the ledger, the supplier statement and ageing, and the
financial statements (the ledger must still agree to them line by line)."""
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
from datetime import date, timedelta
from app import create_app
from extensions import db
from models import *
import suppliers as sp
import analytics
import cashbook
import firm_ledger as fl
import firm_statements as fs
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005):
    return a is not None and abs(a - b) <= tol
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

today = date.today()
D = lambda n: today - timedelta(days=n)
F = "/firm-finance/suppliers"
with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    bank = CashAccount(name="Bank (USD)", kind="Bank", currency="USD", opening_balance=5000.0, opening_date=D(400), default_in=True, default_out=True)
    db.session.add(bank); db.session.commit()
    sup = Supplier(code="SUP-0001", name="Furniture Co", payment_terms_days=30, currency="USD")
    sup2 = Supplier(code="SUP-0002", name="Other Co", payment_terms_days=30, currency="USD")
    db.session.add_all([sup, sup2]); db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, bank=bank.id, sup=sup.id, sup2=sup2.id)
P, S = login(ids["partner"]), login(ids["staff"])
sid = ids["sup"]

# year-end covering today, so the statements exist
P.post("/firm-finance/year-ends/new", data={"period_end": date(today.year, 12, 31).isoformat(), "opening_nil": "1"})
with app.app_context():
    ye = FirmYearEnd.query.first(); ye.tax_manual = True; db.session.commit(); yeid = ye.id

def snapshot():
    with app.app_context():
        L = fl.build_ledger(year_ends=FirmYearEnd.query.all())
        bal = fl.account_balances(L, date(today.year, 12, 31), date(today.year, 1, 1))
        g = lambda k: round(bal.get(L.by_key[k].id, 0.0), 2)
        return g("SUPP_ADV"), g("AP"), g(f"CASH:{ids['bank']}"), fl.trial_balance(L, date(today.year, 12, 31))["balanced"]

# ---------------------------------------------------------------- access and validation
check(S.get(F + f"/{sid}/advance/new").status_code == 403, "staff cannot open the advance form")
check(S.get(F + "/advances").status_code == 403, "staff cannot open the advances list")
check(P.get(F + f"/{sid}/advance/new").status_code == 200, "a partner opens the advance form")
r = P.post(F + f"/{sid}/advance/new", data={"advance_date": D(20).isoformat(), "amount": "0", "currency": "USD"}, follow_redirects=True)
check(b"Enter the amount paid in advance" in r.data, "a nil advance is refused")
r = P.post(F + f"/{sid}/advance/new", data={"advance_date": "", "amount": "10", "currency": "USD"}, follow_redirects=True)
check(b"Enter the date paid" in r.data, "an advance needs a date")
with app.app_context():
    s_ = Supplier.query.get(sid); s_.status = "On hold"; db.session.commit()
r = P.post(F + f"/{sid}/advance/new", data={"advance_date": D(20).isoformat(), "amount": "50", "currency": "USD"}, follow_redirects=True)
check(b"on hold, so payments are refused" in r.data, "a supplier on hold cannot be paid in advance")
with app.app_context():
    s_ = Supplier.query.get(sid); s_.status = "Active"; db.session.commit()
    check(SupplierAdvance.query.count() == 0, "nothing was recorded by the refused attempts")

# ---------------------------------------------------------------- pay in advance
r = P.post(F + f"/{sid}/advance/new", data={"advance_date": D(20).isoformat(), "amount": "400", "currency": "USD", "method": "Bank transfer",
                                            "reference": "PRO-77", "purpose": "40% deposit on desks", "cash_account_id": str(ids["bank"])}, follow_redirects=True)
check(b"Advance to Furniture Co" in r.data and b"40% deposit on desks" in r.data, "the advance is recorded and its page opens")
with app.app_context():
    adv = SupplierAdvance.query.first(); aid = adv.id
    check(near(sp.advance_unapplied(adv), 400), "all 400 is unused")
    txns = cashbook.resolve_accounts(cashbook.collect_transactions())
    t = [x for x in txns if x["key"] == f"supplieradv:{aid}"]
    check(len(t) == 1 and t[0]["source"] == "Supplier advance" and near(t[0]["amount"], -400) and t[0]["account_id"] == ids["bank"], "the cash book shows 400 out of the chosen account")
    check(near(sp.net_by_currency(Supplier.query.get(sid)).get("USD"), -400), "net position -400: the supplier holds 400 of ours")
adv_l, ap_l, bank_l, balanced = snapshot()
check(near(adv_l, 400) and near(ap_l, 0) and balanced, f"ledger: Advances to suppliers 400 (debit), no payable, balanced ({adv_l}, {ap_l})")
check(near(bank_l, 5000 - 400), "ledger bank is 4,600")
with app.app_context():
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    L = fl.build_ledger(year_ends=FirmYearEnd.query.all())
    rec = fl.reconcile(L, cols[-1])
    check(rec["different"] == 0, "ledger agrees to the statements line by line: " + ", ".join(f"{r['code']} {r['ledger']} vs {r['stmt']}" for r in rec["rows"] if abs(r["diff"]) >= 0.01))
    items = [i["name"] for i in cols[-1]["acc"]["other_current_assets"]] if "acc" in cols[-1] else []
r = P.get(f"/firm-finance/balance-sheet?ye={yeid}")
check(r.status_code == 200 and b"Other current assets" in r.data and b"400.00" in r.data, "the balance sheet carries the 400 under other current assets")
r = P.get(f"/firm-finance/notes?ye={yeid}")
check(r.status_code == 200 and b"Advances paid to suppliers" in r.data, "the notes name 'Advances paid to suppliers'")
r = P.get(F + f"/{sid}")
check(b"Paid in advance, not yet used" in r.data and b"40% deposit on desks" in r.data and b"they hold of ours" in r.data, "the supplier page shows the advance and the net position")
r = P.get(F + "/")
check(b"Paid in advance, not yet used" in r.data and b"Advance held" in r.data, "the supplier list shows advances held")
r = P.get(F + "/advances")
check(r.status_code == 200 and b"Furniture Co" in r.data and b"PRO-77" in r.data, "the advances list shows it")

# ---------------------------------------------------------------- the bill arrives
with app.app_context():
    db.session.add(Expense(expense_date=D(5), supplier="Furniture Co", supplier_id=sid, category="Office supplies", amount=1000, currency="USD", status="Unpaid", reference="FC-1"))
    db.session.add(Expense(expense_date=D(5), supplier="Furniture Co", supplier_id=sid, category="Office supplies", amount=5000, currency="ZWG", status="Unpaid", reference="FC-ZWG"))
    db.session.commit()
    b1 = Expense.query.filter_by(reference="FC-1").first().id; bz = Expense.query.filter_by(reference="FC-ZWG").first().id
r = P.get(F + f"/{sid}/pay")
check(b"was paid to this supplier in advance" in r.data and b"400.00" in r.data, "the pay screen warns that an advance is unused")
r = P.post(F + f"/advances/{aid}/apply", data={f"amt_{b1}": "450", "applied_on": D(1).isoformat()}, follow_redirects=True)
check(b"more than the USD 400.00 left on this advance" in r.data, "more than the advance holds is refused")
r = P.post(F + f"/advances/{aid}/apply", data={f"amt_{b1}": "300", "applied_on": D(30).isoformat()}, follow_redirects=True)
check(b"cannot be applied before the date it was paid" in r.data, "an advance cannot be applied before it was paid")
r = P.post(F + f"/advances/{aid}/apply", data={f"amt_{b1}": "300", "applied_on": D(10).isoformat()}, follow_redirects=True)
check(b"applied before the bill" in r.data, "...nor before the bill it settles")
r = P.post(F + f"/advances/{aid}/apply", data={f"amt_{bz}": "300", "applied_on": D(1).isoformat()}, follow_redirects=True)
check(b"advance is in USD but a bill is in ZWG" in r.data, "a USD advance cannot settle a ZWG bill")
r = P.post(F + f"/advances/{aid}/apply", data={"applied_on": D(1).isoformat()}, follow_redirects=True)
check(b"Enter an amount against at least one bill" in r.data, "applying nothing is refused")
with app.app_context():
    check(SupplierPayment.query.count() == 0, "nothing was applied by the refused attempts")
    n_cash_before = len(cashbook.collect_transactions())
r = P.post(F + f"/advances/{aid}/apply", data={f"amt_{b1}": "300", "applied_on": D(1).isoformat()}, follow_redirects=True)
check(b"of the advance applied to 1 bill" in r.data, "300 of the advance is applied to the bill")
with app.app_context():
    e = Expense.query.get(b1); adv = SupplierAdvance.query.get(aid)
    check(e.status == "Unpaid" and near(sp.outstanding(e), 700) and near(sp.paid_on(e), 300), "the bill is part-settled: 700 still owed")
    check(near(sp.advance_unapplied(adv), 100), "100 of the advance is left")
    check(len(cashbook.collect_transactions()) == n_cash_before, "no cash moved when the advance was applied")
    check(near(sp.balances_by_currency(Supplier.query.get(sid))["USD"], 700) and near(sp.net_by_currency(Supplier.query.get(sid))["USD"], 600), "owed on bills 700, net after the 100 left = 600")
adv_l, ap_l, bank_l, balanced = snapshot()
check(near(adv_l, 100) and near(ap_l, -700) and near(bank_l, 4600) and balanced, f"ledger: advances 100, payable 700, bank still 4,600 ({adv_l}, {ap_l}, {bank_l})")
with app.app_context():
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    rec = fl.reconcile(fl.build_ledger(year_ends=FirmYearEnd.query.all()), cols[-1])
    check(rec["different"] == 0, "the ledger still agrees to the statements after the application: " + ", ".join(f"{r['code']} {r['ledger']} vs {r['stmt']}" for r in rec["rows"] if abs(r["diff"]) >= 0.01))
    rows = analytics._expense_rows("USD")
    check(near(sum(analytics.exp_owed_on(r_, today) for r_ in rows), 700), "analytics creditors see the application: 700")

# ---------------------------------------------------------------- the statement
with app.app_context():
    st = sp.statement(Supplier.query.get(sid), D(60), today, "USD")
    kinds = [(l["kind"], l["bill"], l["payment"], l["balance"]) for l in st["lines"]]
    check(kinds == [("Advance", 0.0, 400.0, -400.0), ("Bill", 1000.0, 0.0, 600.0), ("Memo", 0.0, 0.0, 600.0)], f"statement: advance, bill, then a memo line that moves nothing: {kinds}")
    check(near(st["closing"], 600), "closing 600 = 700 owed on bills less 100 advance left")
r = P.get(F + f"/{sid}/statement?currency=USD&from={D(60).isoformat()}")
check(b"Advance paid" in r.data and b"already counted when paid" in r.data, "the statement page lists the advance and the memo line")

# ---------------------------------------------------------------- ageing
with app.app_context():
    ag = sp.ageing(today, "USD")
    g = {x["name"]: x for x in ag["rows"]}["Furniture Co"]
    check(near(g["total"], 700) and near(g["advance"], 100) and near(g["net"], 600), "ageing: owed 700, advance 100, net 600")
    check(near(ag["advance_total"], 100) and near(ag["net_total"], 600), "ageing totals carry advances and net")
    old = sp.ageing(D(15), "USD")
    g2 = {x["name"]: x for x in old["rows"]}["Furniture Co"]
    check(near(g2["total"], 0) and near(g2["advance"], 400) and near(g2["net"], -400), "ageing at an earlier date: no bills yet, the whole 400 advance held")
r = P.get(F + "/ageing?currency=USD")
check(b"Advances held" in r.data and b"Furniture Co" in r.data, "the ageing page has the advances columns")
r = P.get(F + "/ageing?currency=USD&csv=1")
check(b"Advances paid, not yet used" in r.data, "the CSV has them too")

# ---------------------------------------------------------------- refund
r = P.post(F + f"/advances/{aid}/refund", data={"amount": "150", "refunded_on": D(1).isoformat()}, follow_redirects=True)
check(b"more than the USD 100.00 left" in r.data, "refunding more than is unused is refused")
r = P.post(F + f"/advances/{aid}/refund", data={"amount": "40", "refunded_on": D(30).isoformat()}, follow_redirects=True)
check(b"on or after the advance" in r.data, "a refund cannot pre-date the advance")
r = P.post(F + f"/advances/{aid}/refund", data={"amount": "40", "refunded_on": D(1).isoformat(), "method": "Bank transfer", "reference": "RF-1", "cash_account_id": str(ids["bank"])}, follow_redirects=True)
check(b"Refund of USD 40.00" in r.data, "a refund is recorded")
with app.app_context():
    rf = SupplierAdvanceRefund.query.first(); rfid = rf.id
    txns = cashbook.resolve_accounts(cashbook.collect_transactions())
    t = [x for x in txns if x["key"] == f"supplierref:{rfid}"]
    check(len(t) == 1 and t[0]["source"] == "Supplier refund" and near(t[0]["amount"], 40) and t[0]["account_id"] == ids["bank"], "the cash book shows 40 coming in")
    check(near(sp.advance_unapplied(SupplierAdvance.query.get(aid)), 60), "60 of the advance is left")
adv_l, ap_l, bank_l, balanced = snapshot()
check(near(adv_l, 60) and near(bank_l, 4640) and balanced, f"ledger: advances 60, bank 4,640 ({adv_l}, {bank_l})")
with app.app_context():
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    rec = fl.reconcile(fl.build_ledger(year_ends=FirmYearEnd.query.all()), cols[-1])
    check(rec["different"] == 0, "ledger still agrees to the statements after the refund")

# ---------------------------------------------------------------- auto-apply the rest
with app.app_context():
    db.session.add(Expense(expense_date=D(3), supplier="Furniture Co", supplier_id=sid, category="Office supplies", amount=200, currency="USD", status="Unpaid", reference="FC-2")); db.session.commit()
r = P.post(F + f"/{sid}/advances/auto-apply", data={"currency": "USD", "applied_on": today.isoformat()}, follow_redirects=True)
check(b"USD 60.00 of advances applied" in r.data, "'apply advances to the oldest open bills' uses up the 60")
with app.app_context():
    check(near(sp.advance_unapplied(SupplierAdvance.query.get(aid)), 0), "the advance is fully used")
    check(near(sp.outstanding(Expense.query.get(b1)), 640) and near(sp.outstanding(Expense.query.filter_by(reference="FC-2").first()), 200), "the oldest bill took it: 640 and 200 still owed")
r = P.post(F + f"/{sid}/advances/auto-apply", data={"currency": "USD", "applied_on": today.isoformat()}, follow_redirects=True)
check(b"Nothing to apply" in r.data, "a second run finds nothing to apply")
adv_l, ap_l, bank_l, balanced = snapshot()
check(near(adv_l, 0) and near(ap_l, -840) and balanced, f"ledger: advances nil, payable 840 ({adv_l}, {ap_l})")

# ---------------------------------------------------------------- undo, delete
r = P.post(F + f"/advances/{aid}/delete", follow_redirects=True)
check(b"has been applied or refunded" in r.data, "an advance with applications cannot be deleted")
with app.app_context():
    apps = SupplierPayment.query.filter(SupplierPayment.advance_id == aid).order_by(SupplierPayment.id).all()
    app_ids = [a.id for a in apps]
r = P.post(F + f"/payments/{app_ids[-1]}/delete", follow_redirects=True)
with app.app_context():
    check(near(sp.advance_unapplied(SupplierAdvance.query.get(aid)), 60), "undoing the last application gives the 60 back to the advance")
r = P.post(F + f"/advances/refunds/{rfid}/delete", follow_redirects=True)
with app.app_context():
    check(near(sp.advance_unapplied(SupplierAdvance.query.get(aid)), 100), "deleting the refund gives its 40 back")
r = P.post(F + f"/payments/{app_ids[0]}/delete", follow_redirects=True)
with app.app_context():
    check(near(sp.advance_unapplied(SupplierAdvance.query.get(aid)), 400) and not SupplierAdvance.query.get(aid).applications, "with everything undone the whole 400 is unused again")
r = P.post(F + f"/advances/{aid}/delete", follow_redirects=True)
with app.app_context():
    check(SupplierAdvance.query.count() == 0, "an untouched advance can be deleted")
    check(not [t for t in cashbook.collect_transactions() if t["key"].startswith("supplieradv:")], "...and it leaves the cash book")
adv_l, ap_l, bank_l, balanced = snapshot()
check(near(adv_l, 0) and near(bank_l, 5000) and balanced, "the ledger is back to the start: bank 5,000, no advance")

# ---------------------------------------------------------------- deleting a bill gives its applied advance back
r = P.post(F + f"/{sid}/advance/new", data={"advance_date": D(20).isoformat(), "amount": "100", "currency": "USD"}, follow_redirects=True)
with app.app_context():
    a2 = SupplierAdvance.query.first().id
P.post(F + f"/advances/{a2}/apply", data={f"amt_{b1}": "100", "applied_on": D(1).isoformat()})
with app.app_context():
    check(near(sp.advance_unapplied(SupplierAdvance.query.get(a2)), 0), "a second advance of 100 is applied")
P.post(f"/expenses/{b1}/delete")
with app.app_context():
    check(near(sp.advance_unapplied(SupplierAdvance.query.get(a2)), 100), "deleting the bill returns the advance applied to it")
# blocked supplier
with app.app_context():
    s_ = Supplier.query.get(sid); s_.status = "Blocked"; db.session.commit()
r = P.post(F + f"/advances/{a2}/apply", data={f"amt_{Expense.query.filter_by(reference='FC-2').first().id if False else 0}": "1", "applied_on": today.isoformat()}, follow_redirects=True)
check(b"advances cannot be applied" in r.data or b"Enter an amount" in r.data, "a blocked supplier's advance cannot be applied")

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
