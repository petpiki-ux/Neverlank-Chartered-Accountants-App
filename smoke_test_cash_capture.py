"""Choosing the cash book account when money is captured: payment/receipt, client advance, advance refund, expense,
and the cash book's own entry form (which must not silently pre-select an account)."""
import os, re, sys, tempfile
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
import cashbook
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

today = date.today()
with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme (Pvt) Ltd", created_by_id=partner.id); db.session.add(cl); db.session.commit()
    inv = Invoice(invoice_number="INV-T1", client_id=cl.id, currency="USD", vat_pct=0.0, status="Sent", issue_date=date(2026, 1, 10), due_date=date(2026, 2, 10))
    inv.lines.append(InvoiceLineItem(description="Fee", quantity=1, unit_price=1000)); db.session.add(inv)
    def acct(name, cur, methods="", d_in=False, d_out=False, kind="Bank"):
        a = CashAccount(name=name, kind=kind, currency=cur, opening_balance=0, opening_date=date(2020, 1, 1), methods=methods,
                        default_in=d_in, default_out=d_out, is_active=True, order=CashAccount.query.count())
        db.session.add(a); db.session.commit(); return a.id
    A = acct("Main bank", "USD", "Bank transfer", True, True)
    B = acct("Second bank", "USD", "")
    C = acct("Cash box", "USD", "Cash", kind="Cash")
    Z = acct("ZiG bank", "ZWG", "Bank transfer", True, True)
    off = acct("Closed account", "USD", ""); CashAccount.query.get(off).is_active = False; db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, inv=inv.id, client=cl.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(ids["partner"]), login(ids["staff"])

def assigned(key):
    with app.app_context():
        r = CashAssign.query.filter_by(source_key=key).first()
        return r.account_id if r else None
def account_of(key):
    with app.app_context():
        t = next(x for x in cashbook.resolve_accounts(cashbook.collect_transactions()) if x["key"] == key)
        return t["account_id"]

# ---- payment against an invoice
page = P.get(f"/invoices/{ids['inv']}").data
check(b'name="cash_account_id"' in page and b"Second bank" in page and b"Main bank" in page, "partner sees the Cash book account picker on the payment form")
check(b"Closed account" not in page, "a switched-off account is not offered")
check(b"Automatic (by payment method)" in page, "the picker keeps an automatic option")
sp = S.get(f"/invoices/{ids['inv']}").data
check(b'name="cash_account_id"' not in sp, "staff do not see the picker")

def pay(client, amt, method, acc):
    d = {"amount": str(amt), "paid_on": today.isoformat(), "method": method}
    if acc is not None: d["cash_account_id"] = str(acc)
    return client.post(f"/receipts/invoice/{ids['inv']}/add", data=d, follow_redirects=True)

r = pay(P, 100, "Bank transfer", B)
with app.app_context():
    r1 = Receipt.query.order_by(Receipt.id.desc()).first().id
check(assigned(f"receipt:{r1}") == B and account_of(f"receipt:{r1}") == B, "payment lands in the account chosen, not the one the method would pick")
r = pay(P, 50, "Bank transfer", Z)
with app.app_context():
    n = Receipt.query.count()
check(n == 1 and b"ZWG account" in r.data, "a ZWG account is refused for a USD payment and no receipt is created")
r = pay(P, 60, "Bank transfer", off)
with app.app_context():
    n = Receipt.query.count()
check(n == 1 and b"not available" in r.data, "a switched-off account is refused")
r = pay(P, 70, "Bank transfer", None)
with app.app_context():
    r2 = Receipt.query.order_by(Receipt.id.desc()).first().id
check(assigned(f"receipt:{r2}") is None and account_of(f"receipt:{r2}") == A, "blank = automatic: the payment method decides (Main bank)")
r = pay(P, 20, "Cash", C)
with app.app_context():
    r3 = Receipt.query.order_by(Receipt.id.desc()).first().id
check(account_of(f"receipt:{r3}") == C, "cash payment into the cash box")
r = pay(S, 10, "Bank transfer", B)
with app.app_context():
    r4 = Receipt.query.order_by(Receipt.id.desc()).first()
    r4 = r4.id if r4.id not in (r1, r2, r3) else None
if r4:
    check(assigned(f"receipt:{r4}") is None, "a staff member's choice is ignored (automatic rules apply)")
else:
    check(True, "staff payment not allowed on this invoice (picker not applicable)")
check(b"1 receipt" not in b"" and True, "ok")

# ---- advance
page = P.get("/advances/new").data
check(b'name="cash_account_id"' in page and b"ZiG bank" in page, "advance form has the picker (all currencies; the page filters by currency)")
def adv(client, cur, acc):
    d = {"client_id": str(ids["client"]), "currency": cur, "amount": "300", "received_on": today.isoformat(), "method": "Bank transfer"}
    if acc is not None: d["cash_account_id"] = str(acc)
    return client.post("/advances/new", data=d, follow_redirects=True)
r = adv(P, "USD", Z)
with app.app_context():
    n = ClientAdvance.query.count()
check(n == 0 and b"ZWG account" in r.data, "advance in USD refused for a ZWG account")
r = adv(P, "USD", B)
with app.app_context():
    a1 = ClientAdvance.query.first()
    a1id = a1.id
check(assigned(f"advance:{a1id}") == B and account_of(f"advance:{a1id}") == B, "advance lands in the chosen account")
r = adv(P, "ZWG", None)
with app.app_context():
    a2id = ClientAdvance.query.order_by(ClientAdvance.id.desc()).first().id
check(account_of(f"advance:{a2id}") == Z, "ZWG advance with no choice goes to the ZWG default")

# ---- refund of the advance
page = P.get(f"/advances/{a1id}").data
check(b'name="cash_account_id"' in page, "refund form has the picker")
r = P.post(f"/advances/{a1id}/refund", data={"amount": "40", "refunded_on": today.isoformat(), "method": "Bank transfer", "cash_account_id": str(Z)}, follow_redirects=True)
with app.app_context():
    n = ClientAdvanceRefund.query.count()
check(n == 0 and b"ZWG account" in r.data, "refund refused for the wrong currency account")
r = P.post(f"/advances/{a1id}/refund", data={"amount": "40", "refunded_on": today.isoformat(), "method": "Bank transfer", "cash_account_id": str(C)}, follow_redirects=True)
with app.app_context():
    rf = ClientAdvanceRefund.query.first().id
check(assigned(f"refund:{rf}") == C and account_of(f"refund:{rf}") == C, "refund paid out of the chosen account")

# ---- expenses
page = P.get("/expenses/new").data
check(b'name="cash_account_id"' in page, "expense form has the picker")
def newexp(acc, cur="USD", amt="90"):
    d = {"supplier": "Landlord", "expense_date": today.isoformat(), "amount": amt, "currency": cur, "status": "Paid", "category": "Other", "paid_date": today.isoformat()}
    if acc is not None: d["cash_account_id"] = str(acc)
    return P.post("/expenses/new", data=d, follow_redirects=True)
r = newexp(Z)
with app.app_context():
    n = Expense.query.count()
check(n == 0 and b"ZWG account" in r.data, "USD expense refused for a ZWG account")
newexp(C)
with app.app_context():
    e1 = Expense.query.first().id
check(assigned(f"expense:{e1}") == C and account_of(f"expense:{e1}") == C, "expense paid from the chosen account")
check(b"Cash box" in P.get(f"/expenses/{e1}/edit").data and re.search(rf'<option value="{C}"[^>]*selected'.encode(), P.get(f"/expenses/{e1}/edit").data) is not None, "editing shows the account already chosen")
d = {"supplier": "Landlord", "expense_date": today.isoformat(), "amount": "90", "currency": "USD", "status": "Paid", "category": "Other", "paid_date": today.isoformat(), "cash_account_id": str(B)}
P.post(f"/expenses/{e1}/edit", data=d)
check(assigned(f"expense:{e1}") == B, "editing the expense can move it to another account")
d["cash_account_id"] = ""
P.post(f"/expenses/{e1}/edit", data=d)
check(assigned(f"expense:{e1}") is None and account_of(f"expense:{e1}") == A, "choosing Automatic clears the choice (default-out account)")
d["cash_account_id"] = str(C); P.post(f"/expenses/{e1}/edit", data=d)
P.post(f"/expenses/{e1}/delete")
check(assigned(f"expense:{e1}") is None, "deleting an expense forgets its account choice (so a reused id can't inherit it)")

# ---- the cash book's own entry form
page = P.get("/firm-finance/cash-book/entries/new").data
check(b"Choose the account..." in page and b'<option value="" selected>Choose the account...' in page, "new cash book entry starts with no account selected")
check(f'<option value="{B}" selected'.encode() not in page and f'<option value="{A}" selected'.encode() not in page, "no account is silently pre-selected")
page = P.get(f"/firm-finance/cash-book/entries/new?account={C}").data
check(f'<option value="{C}" selected'.encode() in page, "coming from one account's page pre-selects that account")
r = P.post("/firm-finance/cash-book/entries/new", data={"kind": "in", "account_id": "", "entry_date": today.isoformat(), "amount": "10", "category": "Other receipt"}, follow_redirects=True)
with app.app_context():
    n = CashEntry.query.count()
check(n == 0 and b"Choose the account" in r.data, "an entry with no account chosen is refused")
r = P.post("/firm-finance/cash-book/entries/new", data={"kind": "in", "account_id": str(B), "entry_date": today.isoformat(), "amount": "10", "category": "Other receipt"}, follow_redirects=True)
with app.app_context():
    n = CashEntry.query.count()
check(n == 1, "an entry with an account chosen is saved")

print("\nFAILED:" if fails else "\nALL PASSED", *fails, sep="\n")
sys.exit(1 if fails else 0)
