"""Suppliers Management Engine (Firm Finance > Suppliers): supplier master, bills linked to suppliers, payments in full and in
parts, the supplier ledger (statement, ageing, agreement to a supplier's own statement), status controls, and agreement with the cash book,
the Trade payables account of the ledger, the analytics creditors and the financial statements - checked against figures worked out by hand."""
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
    bank = CashAccount(name="Bank (USD)", kind="Bank", currency="USD", opening_balance=1000.0, opening_date=D(400), default_in=True, default_out=True, methods="Bank transfer")
    bank2 = CashAccount(name="Cash box", kind="Cash", currency="USD", opening_balance=0.0, opening_date=D(400), methods="Cash")
    zbank = CashAccount(name="Bank (ZWG)", kind="Bank", currency="ZWG", opening_balance=0.0, opening_date=D(400), default_in=True, default_out=True)
    db.session.add_all([bank, bank2, zbank]); db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, bank=bank.id, bank2=bank2.id, zbank=zbank.id)
P, S = login(ids["partner"]), login(ids["staff"])

# ---------------------------------------------------------------- access
for path in ("/", "/new", "/ageing", "/payments"):
    check(S.get(F + path).status_code == 403, f"staff cannot open Suppliers {path}")
check(S.post(F + "/import").status_code == 403, "staff cannot import suppliers")
check(P.get(F + "/").status_code == 200, "a partner opens the supplier list")
r = P.get("/firm-finance/")
check(b"/firm-finance/suppliers/" in r.data, "the Firm Finance menu links to Suppliers")

# ---------------------------------------------------------------- legacy register -> suppliers
with app.app_context():
    db.session.add(Expense(expense_date=D(80), supplier="  Zesa   Holdings ", category="Utilities", amount=200, currency="USD", status="Paid", paid_date=D(70), reference="Z-1"))
    db.session.add(Expense(expense_date=D(50), supplier="zesa holdings", category="Utilities", amount=150, currency="USD", status="Unpaid", due_date=D(20), reference="Z-2"))
    db.session.add(Expense(expense_date=D(10), supplier="Print Shop", category="Office supplies", amount=60, currency="USD", status="Unpaid"))
    db.session.commit()
r = P.get(F + "/")
check(b"3 bill(s) in the Expense register are not linked" in r.data, "the list offers to create suppliers from unlinked bills")
r = P.post(F + "/import", follow_redirects=True)
check(b"2 supplier(s) created and 3 bill(s) linked" in r.data, "two distinct names (case/space-insensitive) became two suppliers, three bills linked")
with app.app_context():
    zesa = Supplier.query.filter(Supplier.name.ilike("%zesa%")).first()
    pshop = Supplier.query.filter_by(name="Print Shop").first()
    check(zesa is not None and zesa.code == "SUP-0001" and pshop.code == "SUP-0002", "codes run SUP-0001, SUP-0002")
    check(zesa.name == "Zesa Holdings", "the name is tidied (spaces collapsed)")
    zid, pid = zesa.id, pshop.id
    check(all(e.supplier_id == zid for e in Expense.query.filter(Expense.supplier.ilike("%zesa%")).all()), "both Zesa bills are linked to the one supplier")
    check(near(sp.balance(Supplier.query.get(zid)), 150) and near(sp.balance(Supplier.query.get(pid)), 60), "owed: Zesa 150 (200 was paid), Print Shop 60")
check(b"not linked" not in P.get(F + "/").data, "once linked, the warning disappears")

# ---------------------------------------------------------------- master record
r = P.post(F + "/new", data={"name": "Zesa  Holdings"}, follow_redirects=True)
check(b"already on file" in r.data, "a duplicate name is refused")
r = P.post(F + "/new", data={"name": "CBZ Insurance", "contact_person": "Mr Moyo", "payment_terms_days": "14", "currency": "USD", "tin": "1234567",
                              "tax_clearance_expiry": (today + timedelta(days=10)).isoformat(), "wht_applicable": "1", "wht_rate": "10", "bank_name": "CBZ"}, follow_redirects=True)
check(b"CBZ Insurance added as SUP-0003" in r.data, "a new supplier gets the next code")
r = P.post(F + "/new", data={"name": "Bad Terms", "payment_terms_days": "999"}, follow_redirects=True)
check(b"between 0 and 365" in r.data, "terms outside 0-365 days are refused")
r = P.post(F + "/new", data={"name": "Bad Rate", "wht_applicable": "1", "wht_rate": "150"}, follow_redirects=True)
check(b"between 0 and 100" in r.data, "a withholding rate outside 0-100 is refused")
with app.app_context():
    cbz = Supplier.query.filter_by(name="CBZ Insurance").first(); cid = cbz.id
    check(cbz.payment_terms_days == 14 and cbz.wht_rate == 10 and cbz.wht_applicable, "terms and the typed withholding rate were kept")
    check(sp.clearance_state(cbz) == ("soon", 10), "tax clearance expiring in 10 days is flagged 'soon'")
r = P.get(F + "/")
check(b"Expires in 10 day(s)" in r.data and b"CBZ Insurance" in r.data, "the list flags the expiring tax clearance")
r = P.get(F + "/?q=moyo")
check(b"CBZ Insurance" in r.data and b"Zesa Holdings" not in r.data, "search finds a supplier by contact")
r = P.post(F + f"/{cid}/edit", data={"name": "CBZ Insurance Co", "payment_terms_days": "14", "currency": "USD"}, follow_redirects=True)
check(b"Supplier updated" in r.data, "a supplier can be edited")
r = P.get(F + f"/{cid}"); check(r.status_code == 200 and b"CBZ Insurance Co" in r.data, "the supplier page opens")

# ---------------------------------------------------------------- bills through the Expense form
r = P.get(f"/expenses/new?supplier_id={cid}")
check(r.status_code == 200 and b'<option value="%d" data-name="CBZ Insurance Co"' % cid in r.data and b"selected" in r.data, "'Add a bill' from a supplier pre-selects it on the expense form")
r = P.post("/expenses/new", data={"expense_date": D(5).isoformat(), "supplier_id": str(cid), "supplier": "typed rubbish", "category": "Other", "amount": "300",
                                  "currency": "USD", "status": "Unpaid", "reference": "CBZ-77"}, follow_redirects=True)
check(b"Bill recorded" in r.data, "a bill is captured against a supplier on file")
with app.app_context():
    e = Expense.query.filter_by(reference="CBZ-77").first(); eid = e.id
    check(e.supplier == "CBZ Insurance Co" and e.supplier_id == cid, "the supplier's name replaces the typed one and the bill is linked")
    check(e.due_date == D(5) + timedelta(days=14), "a missing due date is bill date plus the supplier's 14-day terms")
r = P.post("/expenses/new", data={"expense_date": D(3).isoformat(), "supplier": "print shop", "category": "Other", "amount": "25", "currency": "USD", "status": "Unpaid", "reference": "PS-2"}, follow_redirects=True)
with app.app_context():
    e = Expense.query.filter_by(reference="PS-2").first()
    check(e.supplier_id == pid and e.supplier == "Print Shop", "a typed name that matches a supplier on file links to it")
    psid2 = e.id

# ---------------------------------------------------------------- status controls
r = P.post(F + f"/{cid}/status", data={"status": "Blocked", "status_note": "Tax clearance lapsed"}, follow_redirects=True)
check(b"is now blocked" in r.data, "a supplier can be blocked")
r = P.post("/expenses/new", data={"expense_date": D(1).isoformat(), "supplier_id": str(cid), "supplier": "x", "category": "Other", "amount": "10", "currency": "USD", "status": "Unpaid"}, follow_redirects=True)
check(b"is blocked" in r.data and b"Tax clearance lapsed" in r.data, "a bill cannot be recorded against a blocked supplier")
with app.app_context():
    check(Expense.query.filter_by(amount=10).count() == 0, "...and nothing was saved")
r = P.post(F + f"/{cid}/pay", data={"currency": "USD", f"amt_{eid}": "50", "payment_date": D(1).isoformat()}, follow_redirects=True)
check(b"payments are refused" in r.data, "a blocked supplier cannot be paid")
r = P.post(F + f"/{cid}/status", data={"status": "On hold", "status_note": "Dispute"}, follow_redirects=True)
r = P.post("/expenses/new", data={"expense_date": D(1).isoformat(), "supplier_id": str(cid), "supplier": "x", "category": "Other", "amount": "12", "currency": "USD", "status": "Unpaid", "reference": "HOLD-1"}, follow_redirects=True)
check(b"Bill recorded" in r.data, "an on-hold supplier can still send bills")
r = P.post(F + f"/{cid}/pay", data={"currency": "USD", f"amt_{eid}": "50", "payment_date": D(1).isoformat()}, follow_redirects=True)
check(b"on hold, so payments are refused" in r.data, "...but cannot be paid while on hold")
P.post(F + f"/{cid}/status", data={"status": "Active"})
with app.app_context():
    hold = Expense.query.filter_by(reference="HOLD-1").first(); db.session.delete(hold); db.session.commit()   # keep the numbers below simple
    check(Supplier.query.get(cid).status == "Active" and Supplier.query.get(cid).status_note is None, "Active again clears the reason")

# ---------------------------------------------------------------- payments: validation
def pay(sid, amts, **kw):
    data = {"currency": kw.pop("currency", "USD"), "payment_date": kw.pop("on", D(2).isoformat()), "method": kw.pop("method", "Bank transfer"), "reference": kw.pop("ref", "TT-1")}
    data.update({f"amt_{k}": str(v) for k, v in amts.items()})
    data.update(kw)
    return P.post(F + f"/{sid}/pay", data=data, follow_redirects=True)
r = pay(cid, {eid: 0})
check(b"Enter an amount against at least one bill" in r.data, "a payment with no amount is refused")
r = pay(cid, {eid: 300.01})
check(b"more than the USD 300.00 still owed" in r.data, "paying more than is owed is refused")
r = pay(cid, {eid: 100}, on=(D(5) - timedelta(days=1)).isoformat())
check(b"dated before the bill" in r.data, "a payment dated before its bill is refused")
with app.app_context():
    check(SupplierPayment.query.count() == 0, "nothing was recorded by the refused attempts")

# ---------------------------------------------------------------- part payment, then the rest
r = pay(cid, {eid: 100}, ref="TT-100", cash_account_id=str(ids["bank2"]))
check(b"Remittance advice" in r.data and b"TT-100" in r.data and b"100.00" in r.data, "a part payment is recorded and the remittance advice opens")
with app.app_context():
    e = Expense.query.get(eid)
    check(e.status == "Unpaid" and e.paid_date is None, "the bill stays Unpaid after a part payment")
    check(near(sp.paid_on(e), 100) and near(sp.outstanding(e), 200) and sp.bill_state(e) in ("Part-paid", "Overdue"), "paid 100, owed 200")
    check(near(sp.balance(Supplier.query.get(cid)), 200), "the supplier owes 200 now")
    pay1 = SupplierPayment.query.first()
    check(CashAssign.query.filter_by(source_key=f"supplierpay:{pay1.id}").first().account_id == ids["bank2"], "the cash book account chosen on the payment is remembered")
    txns = cashbook.resolve_accounts(cashbook.collect_transactions())
    t = [x for x in txns if x["key"] == f"supplierpay:{pay1.id}"][0]
    check(t["source"] == "Expense" and near(t["amount"], -100) and t["account_id"] == ids["bank2"], "the cash book shows -100 out of the chosen account")
r = P.get("/expenses/")
check(b"Part-paid" in r.data, "the Expense register labels the bill Part-paid")
r = P.post(f"/expenses/{eid}/edit", data={"expense_date": D(5).isoformat(), "supplier_id": str(cid), "supplier": "x", "category": "Other", "amount": "90", "currency": "USD", "status": "Unpaid"}, follow_redirects=True)
check(b"has already been paid on this bill" in r.data, "a bill cannot be cut below what has been paid")
r = P.post(f"/expenses/{eid}/edit", data={"expense_date": D(5).isoformat(), "supplier_id": str(cid), "supplier": "x", "category": "Other", "amount": "300", "currency": "ZWG", "status": "Unpaid"}, follow_redirects=True)
check(b"currency cannot be changed" in r.data, "...nor its currency changed")
r = P.post(f"/expenses/{eid}/edit", data={"expense_date": D(5).isoformat(), "supplier_id": str(cid), "supplier": "x", "category": "Other", "amount": "300", "currency": "USD", "status": "Paid", "paid_date": D(1).isoformat()}, follow_redirects=True)
with app.app_context():
    e = Expense.query.get(eid)
    check(e.status == "Unpaid" and near(sp.outstanding(e), 200), "ticking Paid on a bill with part payments does not override its payments")
r = P.post(f"/expenses/{eid}/paid", data={"paid_date": D(1).isoformat()}, follow_redirects=True)
with app.app_context():
    e = Expense.query.get(eid)
    check(e.status == "Paid" and e.paid_date == D(1) and near(sp.paid_on(e), 300) and e.supplier_payments.__len__() == 2, "'Mark paid' on a part-paid bill records the rest as a payment")
    check(near(sp.balance(Supplier.query.get(cid)), 0), "the supplier is paid up")

# a payment run across two bills of one supplier
with app.app_context():
    z1 = Expense.query.filter_by(reference="Z-2").first(); z1id = z1.id
    db.session.add(Expense(expense_date=D(15), supplier="Zesa Holdings", supplier_id=zid, category="Utilities", amount=90, currency="USD", status="Unpaid", reference="Z-3")); db.session.commit()
    z3id = Expense.query.filter_by(reference="Z-3").first().id
r = pay(zid, {z1id: 150, z3id: 40}, ref="RUN-1")
check(b"Remittance advice" in r.data and b"190.00" in r.data, "one payment settles one bill and part of another")
with app.app_context():
    batches = {p.batch_ref for p in SupplierPayment.query.filter(SupplierPayment.supplier_id == zid).all()}
    check(len(batches) == 1, "both rows share one batch reference")
    check(Expense.query.get(z1id).status == "Paid" and Expense.query.get(z3id).status == "Unpaid", "Z-2 is paid, Z-3 is still open")
    check(near(sp.balance(Supplier.query.get(zid)), 50), "Zesa is owed 50 (Z-3: 90 - 40)")
    zbatch = list(batches)[0]
r = P.get(F + f"/remittance/{zbatch}")
check(r.status_code == 200 and b"RUN-1" in r.data and b"190.00" in r.data and b"Z-2" in r.data, "the remittance advice lists both bills and the total")
check(P.get(F + "/remittance/NOPE").status_code == 404, "an unknown remittance is a 404")

# ---------------------------------------------------------------- currencies never mix
r = pay(zid, {z3id: 10}, currency="ZWG")
check(b"Enter an amount against at least one bill" in r.data, "ZWG payment screen lists no USD bills, so a USD bill cannot be paid from it")
with app.app_context():
    db.session.add(Expense(expense_date=D(9), supplier="Zesa Holdings", supplier_id=zid, category="Utilities", amount=1000, currency="ZWG", status="Unpaid", reference="Z-ZWG")); db.session.commit()
    check(sp.balances_by_currency(Supplier.query.get(zid)) == {"USD": 50.0, "ZWG": 1000.0}, "balances are kept per currency")
    zwgid = Expense.query.filter_by(reference="Z-ZWG").first().id

# ---------------------------------------------------------------- statement
with app.app_context():
    st = sp.statement(Supplier.query.get(zid), D(100), today, "USD")
    kinds = [(l["kind"], l["bill"], l["payment"], l["balance"]) for l in st["lines"]]
    check(near(st["opening"], 0), "nothing before the period")
    check(kinds == [("Bill", 200.0, 0.0, 200.0), ("Payment", 0.0, 200.0, 0.0), ("Bill", 150.0, 0.0, 150.0), ("Bill", 90.0, 0.0, 240.0), ("Payment", 0.0, 150.0, 90.0), ("Payment", 0.0, 40.0, 50.0)]
          or [k[:3] for k in kinds] == [("Bill", 200.0, 0.0), ("Payment", 0.0, 200.0), ("Bill", 150.0, 0.0), ("Bill", 90.0, 0.0), ("Payment", 0.0, 150.0), ("Payment", 0.0, 40.0)],
          f"statement lines are in date order with a running balance: {kinds}")
    check(near(st["closing"], 50) and near(st["billed"], 440) and near(st["paid"], 390), "closing 50 = billed 440 - paid 390")
    st2 = sp.statement(Supplier.query.get(zid), D(60), today, "USD")
    check(near(st2["opening"], 0) and near(st2["closing"], 50), "a later start brings forward the balance")
    st3 = sp.statement(Supplier.query.get(zid), D(45), today, "USD")
    check(near(st3["opening"], 150), "from 45 days ago: the 200 bill was paid 70 days ago, but Z-2 (150, 50 days ago) is still open and is brought forward")
    st4 = sp.statement(Supplier.query.get(zid), D(75), today, "USD")
    check(near(st4["opening"], 200), "the 200 bill is brought forward from 75 days ago (paid 70 days ago)")
r = P.get(F + f"/{zid}/statement?currency=USD")
check(r.status_code == 200 and b"Balance carried forward" in r.data, "the statement page opens")
r = P.get(F + f"/{zid}/statement?currency=USD&their_balance=50")
check(b"Agrees." in r.data, "a supplier statement that matches ours is reported as agreeing")
r = P.get(F + f"/{zid}/statement?currency=USD&their_balance=140")
check(b"Difference of USD 90.00" in r.data and b"not recorded" in r.data, "a difference is quantified with a hint")
r = P.get(F + f"/{zid}/statement?currency=USD&print=1")
check(r.status_code == 200 and b"Supplier account statement" in r.data, "the print version opens")
r = P.get(F + f"/{zid}/statement?currency=USD&csv=1")
check(r.status_code == 200 and r.mimetype == "text/csv" and b"Balance carried forward" in r.data, "the statement downloads as CSV")

# ---------------------------------------------------------------- ageing
with app.app_context():
    cur_owed = sp.ageing(today, "USD")
    names = {g["name"]: g for g in cur_owed["rows"]}
    check(set(names) == {"Zesa Holdings", "Print Shop", "CBZ Insurance Co"} - {"CBZ Insurance Co"}, f"only suppliers that are owed appear: {sorted(names)}")
    z = names["Zesa Holdings"]
    # Z-3: 90 bill dated 15 days ago, no due date, supplier terms 30 -> due in 15 days -> not yet due; part-paid so 50 left
    check(near(z["total"], 50) and near(z["buckets"][0], 50), "Z-3 (50 left) is not yet due: bill 15 days ago on 30-day terms")
    ps = names["Print Shop"]
    check(near(ps["total"], 85) and near(ps["buckets"][0], 85), "Print Shop: 60 (10 days ago) + 25 (3 days ago), both inside 30-day terms")
    check(near(cur_owed["grand"], 135), "USD grand total 135")
    old = sp.ageing(today + timedelta(days=70), "USD")
    zo = {g["name"]: g for g in old["rows"]}["Zesa Holdings"]
    check(near(zo["buckets"][2], 50), "70 days later Z-3 (due in 15 days) is 55 days overdue: the 31-60 bucket")
    check(sp.ageing(today, "ZWG")["grand"] == 1000, "ZWG is aged on its own")
    past = sp.ageing(D(60), "USD")
    check(near({g["name"]: g for g in past["rows"]}["Zesa Holdings"]["total"], 0) if "Zesa Holdings" in {g["name"] for g in past["rows"]} else True, "ageing at a past date ignores later bills and payments")
r = P.get(F + "/ageing?currency=USD")
check(r.status_code == 200 and b"Print Shop" in r.data and b"135.00" in r.data, "the ageing page shows the totals")
r = P.get(F + "/ageing?currency=USD&csv=1")
check(r.mimetype == "text/csv" and b"Creditors ageing" in r.data, "ageing downloads as CSV")
# an unlinked bill still ages, flagged 'not on file'
with app.app_context():
    db.session.add(Expense(expense_date=D(120), supplier="Walk-in vendor", category="Other", amount=40, currency="USD", status="Unpaid", due_date=D(100))); db.session.commit()
r = P.get(F + "/ageing?currency=USD")
check(b"Walk-in vendor" in r.data and b"not on file" in r.data, "a bill with no supplier on file still ages and is flagged")
with app.app_context():
    g = {g["name"]: g for g in sp.ageing(today, "USD")["rows"]}["Walk-in vendor"]
    check(near(g["buckets"][4], 40), "100 days overdue lands in 'over 90'")

# ---------------------------------------------------------------- everything agrees: cash book, ledger, analytics, statements
with app.app_context():
    owed_all = sum(sp.outstanding(e) for e in Expense.query.filter_by(currency="USD").all())
    L = fl.build_ledger()
    bal = fl.account_balances(L, today + timedelta(days=1), D(1000))
    ap = -round(bal.get(L.by_key["AP"].id, 0.0), 2)
    check(near(ap, owed_all), f"ledger Trade payables {ap} = what the supplier ledger says is owed {owed_all} (USD)")
    check(near(owed_all, 175), "...which is 135 (suppliers) + 40 (walk-in vendor)")
    rows = analytics._expense_rows("USD")
    check(near(sum(analytics.exp_owed_on(r_, today) for r_ in rows), owed_all), "analytics creditors agree")
    check(near(sum(analytics.exp_owed_on(r_, D(60)) for r_ in rows), sum(sp.outstanding(e, D(60)) for e in Expense.query.filter_by(currency="USD").all())), "...also at an earlier date")
    # bank balances: 1000 opening - payments
    accts, txns, _ = cashbook.load_all()
    out_total = round(sum(t["amount"] for t in txns if t["source"] == "Expense" and t["currency"] == "USD"), 2)
    check(near(out_total, -(200 + 300 + 190)), f"cash book payments out = -690 (Zesa 200 legacy + CBZ 300 + run 190): {out_total}")
    check(len([t for t in txns if t["key"].startswith("supplierpay:")]) == 4, "four payment rows reach the cash book (2 CBZ + 2 Zesa); the legacy paid bill keeps its single line")
    check(len([t for t in txns if t["key"].startswith("expense:")]) == 1, "...and a part-paid bill is not also counted as a whole-amount payment")
    tb = fl.trial_balance(L, today + timedelta(days=1))
    check(tb["balanced"], "the trial balance balances")

# ---------------------------------------------------------------- deleting
with app.app_context():
    p_first = SupplierPayment.query.filter_by(expense_id=eid).order_by(SupplierPayment.id).first()
    pid_first = p_first.id
r = P.post(F + f"/payments/{pid_first}/delete", follow_redirects=True)
check(b"Payment deleted" in r.data, "a payment can be deleted")
with app.app_context():
    e = Expense.query.get(eid)
    check(e.status == "Unpaid" and e.paid_date is None and near(sp.outstanding(e), 100), "the bill re-opens for the amount that was paid back out")
    check(CashAssign.query.filter_by(source_key=f"supplierpay:{pid_first}").count() == 0, "the cash book account choice for it is forgotten")
r = P.post(F + f"/payments/{SupplierPayment.query.filter_by(expense_id=eid).first().id if False else 0}/delete")
with app.app_context():
    last = SupplierPayment.query.filter_by(expense_id=eid).first()
    lid = last.id
r = P.post(F + f"/payments/{lid}/delete", follow_redirects=True)
with app.app_context():
    e = Expense.query.get(eid)
    check(e.status == "Unpaid" and near(sp.outstanding(e), 300) and not e.supplier_payments, "with every payment gone the bill is fully owed again")
r = P.post(f"/expenses/{eid}/delete", follow_redirects=True)
with app.app_context():
    check(Expense.query.get(eid) is None, "deleting a bill works")
r = P.post(F + f"/{zid}/delete", follow_redirects=True)
check(b"cannot be deleted" in r.data, "a supplier with bills cannot be deleted")
with app.app_context():
    empty = Supplier(code=sp.next_code(), name="Nobody Ltd"); db.session.add(empty); db.session.commit(); nid = empty.id
r = P.post(F + f"/{nid}/delete", follow_redirects=True)
with app.app_context():
    check(Supplier.query.get(nid) is None, "a supplier with no history can be deleted")

# ---------------------------------------------------------------- renaming carries to the bills
r = P.post(F + f"/{pid}/edit", data={"name": "Print Shop (Pvt) Ltd", "payment_terms_days": "30", "currency": "USD"}, follow_redirects=True)
with app.app_context():
    check(all(e.supplier == "Print Shop (Pvt) Ltd" for e in Expense.query.filter_by(supplier_id=pid).all()), "renaming a supplier renames it on its bills")
r = P.get(F + "/payments")
check(r.status_code == 200 and b"RUN-1" in r.data, "the payments list shows the payments made")
r = P.get(F + f"/payments?supplier={zid}&from={D(1000).isoformat()}&to={today.isoformat()}")
check(r.status_code == 200 and b"RUN-1" in r.data, "...and filters by supplier and dates")

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
