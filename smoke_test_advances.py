import os, sys, tempfile, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date, timedelta
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open

from app import create_app
from extensions import db
from models import *
app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

today = date.today()
iso = today.isoformat()
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    staff = User(username="stf", name="Staffer", role="staff"); staff.set_password("x")
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    db.session.add_all([staff, partner]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", address="1 Samora Machel Ave\nHarare", created_by_id=staff.id)
    db.session.add(cl); db.session.commit()
    # a confidential engagement staff is NOT on
    conf = Engagement(client_id=cl.id, title="Confidential review", type="Audit", partner_id=partner.id,
                      period_end=date(2025, 12, 31))
    db.session.add(conf); db.session.commit()
    def mk(num, status="Sent", cur="USD", price=1000, days=30):
        i = Invoice(invoice_number=num, client_id=cl.id, currency=cur, vat_pct=15.0, status=status,
                    issue_date=today - timedelta(days=days), due_date=today + timedelta(days=10), bill_to=cl.name)
        i.lines.append(InvoiceLineItem(description="Audit fee", quantity=1, unit_price=price))
        db.session.add(i); db.session.commit()
        return i.id
    inv1 = mk("INV-2026-0001")            # total 1150
    inv2 = mk("INV-2026-0002", price=400)  # total 460
    inv_draft = mk("INV-2026-0003", status="Draft")
    inv_zwg = mk("INV-2026-0004", cur="ZWG")
    ids = dict(cl=cl.id, conf=conf.id, staff=staff.id, partner=partner.id, admin=admin.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

staff_c, partner_c = login(ids["staff"]), login(ids["partner"])

# ---- record: form + validation
r = staff_c.get("/advances/new")
check(r.status_code == 200 and b"Acme Holdings" in r.data, "new-advance form renders for staff")
base = {"client_id": ids["cl"], "currency": "USD", "amount": "1000", "received_on": iso, "method": "Bank transfer",
        "reference": "CBZ-123", "purpose": "Deposit for FY2026 audit"}
def post(c, **over):
    d = dict(base); d.update(over)
    return c.post("/advances/new", data=d)
check(b"more than zero" in post(staff_c, amount="0").data, "zero amount refused")
check(b"more than zero" in post(staff_c, amount="abc").data, "non-numeric amount refused")
check(b"future" in post(staff_c, received_on=(today + timedelta(days=2)).isoformat()).data, "future date refused")
check(b"Choose how" in post(staff_c, method="Barter").data, "bad method refused")
check(b"currency" in post(staff_c, currency="EUR").data, "bad currency refused")
with app.app_context():
    check(ClientAdvance.query.count() == 0, "nothing saved by the refused attempts")

r = post(staff_c)
check(r.status_code == 302, f"advance recorded (got {r.status_code})")
with app.app_context():
    a = ClientAdvance.query.first()
    check(a.advance_number == f"ADV-{today.year}-0001", "numbered ADV-YYYY-0001")
    check(a.balance == 1000 and a.state == "Unapplied", "balance 1000, Unapplied")
    adv1 = a.id
r = staff_c.get(f"/advances/{adv1}")
check(r.status_code == 200 and b"ADV-" in r.data and b"1,000.00" in r.data and b"INV-2026-0001" in r.data
      and b"INV-2026-0004" not in r.data, "view page lists only same-currency open invoices")
check(b"INV-2026-0003" not in r.data, "draft invoice not offered")
check(b"Record refund" not in r.data and b"Void this advance" not in r.data, "staff sees no refund/void controls")
r = staff_c.get(f"/advances/{adv1}/print")
check(r.status_code == 200 and b"One thousand US dollars only" in r.data and b"Held on your account" in r.data, "receipt prints with words")
check(b"data:image/png;base64," not in r.data and b"Received by:" in r.data, "advance receipt prints unsigned until the Managing Partner signs, and shows who received it")
r = staff_c.get("/advances/")
check(r.status_code == 200 and b"ADV-" in r.data and b"1,000.00" in r.data, "list renders with held total")
r = staff_c.get(f"/clients/{ids['cl']}")
check(r.status_code == 200 and b"Advances received" in r.data and b"ADV-" in r.data, "client page lists the advance")

# ---- statement before any application: advance is a credit -> In credit overall? invoices owe 1150+460
with app.app_context():
    from statements import build_statement, client_invoices
    cl_ = Client.query.get(ids["cl"])
    sec = build_statement([i for i in cl_.invoices if i.status in ("Sent", "Paid")], today - timedelta(days=90), today, cl_.advances)
    u = sec["USD"]
    check(abs(u["closing"] - (1150 + 460 - 1000)) < 0.01, f"closing = invoices 1610 less advance 1000 = 610 (got {u['closing']})")
    check(abs(u["held"] - 1000) < 0.01 and abs(u["ageing"]["total"] - 1610) < 0.01, "held 1000, ageing total 1610")
    check(abs(u["closing"] - (u["ageing"]["total"] - u["held"])) < 0.01, "closing = ageing total - held")
    check(any(r_["kind"] == "Advance received" for r_ in u["rows"]), "advance row on statement")
r = partner_c.get(f"/statements/client/{ids['cl']}")
check(r.status_code == 200 and b"Advance received" in r.data and b"received from you in advance" in r.data, "statement page shows advance + note")
r = partner_c.get("/statements/")
check(r.status_code == 200 and b"Advances held" in r.data, "statements index lists advances held")

# ---- apply: validation
def apply(c, adv, inv, amount, when=iso, nxt=None):
    d = {"invoice_id": inv, "amount": amount, "applied_on": when}
    if nxt: d["next"] = nxt
    return c.post(f"/advances/{adv}/apply", data=d, follow_redirects=True)
r = apply(staff_c, adv1, inv_draft, "100")
check(b"Mark the invoice as Sent" in r.data, "draft invoice refused")
r = apply(staff_c, adv1, inv_zwg, "100")
check(b"never mixed" in r.data, "currency mismatch refused")
r = apply(staff_c, adv1, inv2, "0")
check(b"more than zero" in r.data, "zero apply refused")
r = apply(staff_c, adv1, inv2, "461")
check(b"still owing" in r.data, "more than invoice owes refused")
r = apply(staff_c, adv1, inv1, "1000.01")
check(b"still unapplied" in r.data, "more than the advance holds refused")
r = apply(staff_c, adv1, inv1, "100", when=(today + timedelta(days=1)).isoformat())
check(b"future" in r.data, "future apply date refused")
r = apply(staff_c, adv1, inv1, "100", when=(today - timedelta(days=5)).isoformat())
check(b"before the day it was received" in r.data, "apply before receipt date refused")
with app.app_context():
    check(Receipt.query.count() == 0, "no receipt created by refused attempts")

# ---- apply 600 to inv1 (part), then 400 to inv2 (rest, part of 460)
r = apply(staff_c, adv1, inv1, "600")
check(b"applied to invoice" in r.data and b"400.00 of the advance remains" in r.data, "600 applied; 400 remains")
with app.app_context():
    i1 = Invoice.query.get(inv1)
    a = ClientAdvance.query.get(adv1)
    rc = i1.active_receipts[0]
    check(rc.method == "Advance applied" and rc.from_advance and rc.receipt_number.startswith(f"RCT-{today.year}-"), "ordinary RCT receipt, method Advance applied")
    check(abs(i1.balance - 550) < 0.01 and i1.status == "Sent", f"invoice 1 balance 550, still Sent (got {i1.balance})")
    check(abs(a.balance - 400) < 0.01 and a.state == "Part applied", "advance balance 400, Part applied")
r = apply(staff_c, adv1, inv2, "400", nxt="invoice")
check(b"60.00 is still owing" in r.data or b"60.00" in r.data, "400 applied to invoice 2 (60 left)")
with app.app_context():
    a = ClientAdvance.query.get(adv1)
    check(a.balance == 0 and a.state == "Fully applied", "advance fully applied")
    check(abs(Invoice.query.get(inv2).balance - 60) < 0.01, "invoice 2 balance 60")
r = apply(staff_c, adv1, inv1, "10")
check(b"still unapplied" in r.data, "a spent advance can't be applied again")
r = staff_c.get(f"/invoices/{inv1}")
check(b"Advance applied" in r.data and b"ADV-" in r.data, "invoice page shows the advance receipt")

# ---- statement with applications: no double credit
with app.app_context():
    cl_ = Client.query.get(ids["cl"])
    sec = build_statement([i for i in cl_.invoices if i.status in ("Sent", "Paid")], today - timedelta(days=90), today, cl_.advances)
    u = sec["USD"]
    check(abs(u["closing"] - (1610 - 1000)) < 0.01, f"closing still 610 after applying (got {u['closing']})")
    check(abs(u["credits"] - 1000) < 0.01, f"credits 1000 not 2000 (got {u['credits']})")
    check(abs(u["ageing"]["total"] - 610) < 0.01 and u["held"] == 0, "ageing total 610, nothing held")
    check(sum(1 for r_ in u["rows"] if r_.get("memo")) == 2, "two memo 'applied' lines")
    # period starting after the advance: opening brought forward carries it
    sec2 = build_statement([i for i in cl_.invoices if i.status in ("Sent", "Paid")], today - timedelta(days=1), today, cl_.advances)
    check(abs(sec2["USD"]["closing"] - 610) < 0.01, "later period closes at 610 too")
r = partner_c.get(f"/statements/client/{ids['cl']}")
check(r.status_code == 200 and b"Advance INV" not in r.data and b"applied to INV-2026-0001" in r.data, "statement shows applied memo line")

# ---- analytics
with app.app_context():
    import analytics
    rep = analytics.build_report(currency="USD", preset="12m")
    check(rep["kpi"]["advances_held"] == 0, "analytics: nothing held now")
    inv_rows = analytics._invoice_rows("USD")
    coll = analytics.collected_net(inv_rows, today - timedelta(days=30), today)
    check(abs(coll - 1000 / 1.15) < 0.01, f"collected counts 1000 gross once, ex-VAT (got {coll:.2f})")

# ---- reverse application: staff 403, partner ok
with app.app_context():
    ap = ClientAdvanceApplication.query.filter_by(invoice_id=inv2).first(); ap_id = ap.id
r = staff_c.post(f"/advances/application/{ap_id}/reverse", data={"reason": "x"})
check(r.status_code == 403, "staff cannot reverse")
r = partner_c.post(f"/advances/application/{ap_id}/reverse", data={"reason": ""}, follow_redirects=True)
check(b"give a reason" in r.data, "reason required to reverse")
r = partner_c.post(f"/advances/application/{ap_id}/reverse", data={"reason": "Applied to wrong invoice"}, follow_redirects=True)
check(b"Application reversed" in r.data, "partner reverses")
with app.app_context():
    a = ClientAdvance.query.get(adv1)
    check(abs(a.balance - 400) < 0.01, "400 back in the advance")
    check(abs(Invoice.query.get(inv2).balance - 460) < 0.01, "invoice 2 owes 460 again")
    check(ClientAdvanceApplication.query.get(ap_id).is_active is False, "application inactive")
r = partner_c.post(f"/advances/application/{ap_id}/reverse", data={"reason": "again"}, follow_redirects=True)
check(b"already been reversed" in r.data, "double reverse is a no-op")

# ---- voiding the receipt directly also returns the money
with app.app_context():
    rc_id = Invoice.query.get(inv1).active_receipts[0].id
r = partner_c.post(f"/receipts/{rc_id}/void", data={"reason": "mistake"}, follow_redirects=True)
check(b"back in advance" in r.data, "voiding an advance receipt says money returns to the advance")
with app.app_context():
    check(abs(ClientAdvance.query.get(adv1).balance - 1000) < 0.01, "advance back to 1000")

# ---- receipts list excludes applied money from cash totals
r = partner_c.get("/receipts/")
check(r.status_code == 200 and b"excludes advances applied" in r.data, "receipts list notes the exclusion")

# ---- refund: staff 403; validation; partner ok
r = staff_c.post(f"/advances/{adv1}/refund", data={"amount": "100", "refunded_on": iso, "method": "Cash"})
check(r.status_code == 403, "staff cannot refund")
rf = lambda **k: partner_c.post(f"/advances/{adv1}/refund", data=dict({"amount": "100", "refunded_on": iso, "method": "Cash"}, **k), follow_redirects=True)
check(b"most that can be refunded" in rf(amount="1000.01").data, "over-refund refused")
check(b"Enter the amount" in rf(amount="0").data, "zero refund refused")
check(b"Enter the date" in rf(refunded_on=(today + timedelta(days=1)).isoformat()).data, "future refund date refused")
check(b"Refund of" in rf(amount="300").data, "refund 300 recorded")
with app.app_context():
    a = ClientAdvance.query.get(adv1)
    check(abs(a.balance - 700) < 0.01 and abs(a.refunded_total - 300) < 0.01, "balance 700 after refund")
    cl_ = Client.query.get(ids["cl"])
    u = build_statement([i for i in cl_.invoices if i.status in ("Sent", "Paid")], today - timedelta(days=90), today, cl_.advances)["USD"]
    check(abs(u["closing"] - (1610 - 700)) < 0.01 and abs(u["held"] - 700) < 0.01, f"statement: refund is a debit (closing 910, got {u['closing']})")
    check(any(r_["kind"] == "Advance refund" for r_ in u["rows"]), "refund row on statement")
    rfd_id = a.active_refunds[0].id
r = partner_c.post(f"/advances/{adv1}/void", data={"reason": "oops"}, follow_redirects=True)
check(b"Reverse those first" in r.data, "cannot void an advance with a refund")
r = staff_c.post(f"/advances/refund/{rfd_id}/void", data={"reason": "x"})
check(r.status_code == 403, "staff cannot void refund")
r = partner_c.post(f"/advances/refund/{rfd_id}/void", data={"reason": "Bounced"}, follow_redirects=True)
check(b"held on the advance again" in r.data, "refund voided")
with app.app_context():
    check(abs(ClientAdvance.query.get(adv1).balance - 1000) < 0.01, "balance 1000 again")

# ---- void advance
r = staff_c.post(f"/advances/{adv1}/void", data={"reason": "x"})
check(r.status_code == 403, "staff cannot void")
r = partner_c.post(f"/advances/{adv1}/void", data={"reason": ""}, follow_redirects=True)
check(b"give a reason" in r.data, "void reason required")
r = partner_c.post(f"/advances/{adv1}/void", data={"reason": "Recorded twice"}, follow_redirects=True)
check(b"voided" in r.data, "advance voided")
with app.app_context():
    a = ClientAdvance.query.get(adv1)
    check(a.is_void and a.balance == 0 and a.state == "Void", "void advance holds nothing")
    cl_ = Client.query.get(ids["cl"])
    u = build_statement([i for i in cl_.invoices if i.status in ("Sent", "Paid")], today - timedelta(days=90), today, cl_.advances)["USD"]
    check(abs(u["closing"] - 1610) < 0.01, "void advance drops off the statement")
r = apply(staff_c, adv1, inv1, "10")
check(b"voided" in r.data, "void advance cannot be applied")
r = partner_c.get(f"/advances/{adv1}/print")
check(b"voided" in r.data.lower() and b"not valid as proof" in r.data, "void receipt print says so")

# ---- second advance in ZWG, paid in full on an invoice -> invoice becomes Paid
r = post(partner_c, currency="ZWG", amount="5000", purpose="")
with app.app_context():
    adv2 = ClientAdvance.query.filter_by(currency="ZWG").first().id
    check(ClientAdvance.query.filter_by(currency="ZWG").first().advance_number == f"ADV-{today.year}-0002", "second number ADV-0002 (void number not reused)")
r = apply(staff_c, adv2, inv_zwg, "1150")
with app.app_context():
    iz = Invoice.query.get(inv_zwg)
    check(iz.status == "Paid" and iz.balance == 0, "invoice paid in full from advance -> Paid")
    check(abs(ClientAdvance.query.get(adv2).balance - 3850) < 0.01, "ZWG advance 3850 left")
r = staff_c.get(f"/receipts/{iz.receipts[0].id}")
check(b"Settled from your advance payment" in r.data and b"ADV-" in r.data and b"Date applied" in r.data, "invoice receipt print refers to the advance")
# in-credit statement: only ZWG invoice paid, 3850 held -> closing negative
with app.app_context():
    cl_ = Client.query.get(ids["cl"])
    z = build_statement([i for i in cl_.invoices if i.status in ("Sent", "Paid")], today - timedelta(days=90), today, cl_.advances)["ZWG"]
    check(abs(z["closing"] + 3850) < 0.01, f"ZWG closing is -3850 (in credit) (got {z['closing']})")
r = partner_c.get(f"/statements/client/{ids['cl']}")
check(b"In credit" in r.data, "statement says In credit")
with app.app_context():
    rep = analytics.build_report(currency="ZWG", preset="12m")
    check(abs(rep["kpi"]["advances_held"] - 3850) < 0.01, "analytics: ZWG advances held 3850")
    check(analytics.build_report(currency="USD", preset="12m")["kpi"]["advances_held"] == 0, "analytics: USD held 0 (currencies not mixed)")
r = partner_c.get("/analytics/")
check(r.status_code == 200, f"analytics page renders (got {r.status_code})")

# ---- confidentiality: advance linked to an engagement staff is not on
r = partner_c.post("/advances/new", data=dict(base, engagement_id=ids["conf"], purpose="Confidential"))
with app.app_context():
    adv3 = ClientAdvance.query.filter_by(purpose="Confidential").first().id
r = staff_c.get(f"/advances/{adv3}")
check(r.status_code == 403, "staff blocked from advance on a confidential engagement")
r = staff_c.get("/advances/")
check(b"Confidential" not in r.data, "hidden from staff list")
r = staff_c.get(f"/statements/client/{ids['cl']}")
check(r.status_code == 403, "staff cannot get a statement that would omit a hidden advance")
r = staff_c.post("/advances/new", data=dict(base, engagement_id=ids["conf"]))
check(r.status_code == 403, "staff cannot record an advance against that engagement")
r = partner_c.get(f"/advances/{adv3}")
check(r.status_code == 200, "partner on the engagement can open it")
# engagement of another client refused
with app.app_context():
    other = Client(name="Other Ltd", created_by_id=ids["partner"]); db.session.add(other); db.session.commit()
    oe = Engagement(client_id=other.id, title="Other eng", type="Audit", partner_id=ids["partner"]); db.session.add(oe); db.session.commit()
    oe_id = oe.id
r = partner_c.post("/advances/new", data=dict(base, engagement_id=oe_id))
check(b"doesn&#39;t belong" in r.data or b"doesn't belong" in r.data, "engagement of another client refused")

# ---- deleting an invoice with an application keeps the advance consistent
with app.app_context():
    i = Invoice.query.get(inv2)
    # apply 200 of adv2? currency differs; use a fresh USD advance
r = post(partner_c, amount="300", purpose="For delete test")
with app.app_context():
    adv4 = ClientAdvance.query.filter_by(purpose="For delete test").first().id
apply(partner_c, adv4, inv2, "300")
with app.app_context():
    check(abs(ClientAdvance.query.get(adv4).balance) < 0.01, "adv4 spent")
    i = Invoice.query.get(inv2)
    db.session.delete(i); db.session.commit()
    check(abs(ClientAdvance.query.get(adv4).balance - 300) < 0.01, "deleting the invoice returns the money to the advance")

# ---- migration: the receipt column is added to an existing database
with app.app_context():
    from sqlalchemy import text
    cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(receipt)")).fetchall()]
    check("advance_application_id" in cols, "receipt.advance_application_id exists")
    # rebuild `receipt` the way an old database has it (without the new column), then run the migration
    info = db.session.execute(text("PRAGMA table_info(receipt)")).fetchall()
    keep = [row for row in info if row[1] != "advance_application_id"]
    names = ", ".join(row[1] for row in keep)
    defs = ", ".join(f"{row[1]} {row[2]}" for row in keep)
    db.session.execute(text("PRAGMA foreign_keys=OFF"))
    db.session.execute(text("ALTER TABLE receipt RENAME TO receipt_backup"))
    db.session.execute(text(f"CREATE TABLE receipt ({defs})"))
    db.session.execute(text(f"INSERT INTO receipt ({names}) SELECT {names} FROM receipt_backup"))
    db.session.execute(text("DROP TABLE receipt_backup"))
    db.session.commit()
    cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(receipt)")).fetchall()]
    check("advance_application_id" not in cols, "old-style receipt table has no advance column")
    from app import _add_missing_columns
    _add_missing_columns()
    cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(receipt)")).fetchall()]
    check("advance_application_id" in cols, "_add_missing_columns adds the column to an existing database")
    check(Receipt.query.count() >= 0, "receipts still readable after migration")

# every finance page still renders with the new tab
for url in ("/invoices/", "/receipts/", "/statements/", "/advances/", "/advances/new"):
    r = partner_c.get(url)
    check(r.status_code == 200 and b">Advances</a>" in r.data, f"{url} renders with Advances tab")

print()
print("FAILED:" if fails else "ALL PASSED", len(fails) if fails else "")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
