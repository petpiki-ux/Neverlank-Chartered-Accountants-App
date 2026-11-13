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

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    staff = User(username="stf", name="Staffer", role="staff"); staff.set_password("x")
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    db.session.add_all([staff, partner]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", address="1 Samora Machel Ave\nHarare", tin_number="2000111", vat_number="1234", created_by_id=staff.id)
    db.session.add(cl); db.session.commit()
    inv = Invoice(invoice_number="INV-2026-0001", client_id=cl.id, currency="USD", vat_pct=15.0, status="Sent",
                  issue_date=date.today()-timedelta(days=20), due_date=date.today()+timedelta(days=10), bill_to=cl.name+"\n"+cl.address)
    inv.lines.append(InvoiceLineItem(description="Audit fee 2025", quantity=1, unit_price=800))
    inv.lines.append(InvoiceLineItem(description="Tax return", quantity=2, unit_price=100))
    db.session.add(inv); db.session.commit()
    inv_id, cl_id, admin_id, staff_id, partner_id = inv.id, cl.id, admin.id, staff.id, partner.id
    check(abs(inv.total - 1150) < 1e-6, "invoice total 1150")

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

# staff cannot issue
c = login(staff_id)
r = c.get(f"/credit-notes/invoice/{inv_id}/new")
check(r.status_code == 403, f"staff GET new -> 403 (got {r.status_code})")
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "full", "reason": "x", "issue_date": date.today().isoformat()})
check(r.status_code == 403, "staff POST -> 403")
r = c.get(f"/invoices/{inv_id}")
check(r.status_code == 200 and b"Issue credit note" not in r.data, "staff invoice page has no Issue button")

# partner: form renders
c = login(partner_id)
r = c.get(f"/credit-notes/invoice/{inv_id}/new")
check(r.status_code == 200 and b"Audit fee 2025" in r.data, "partner sees form prefilled")
r = c.get(f"/invoices/{inv_id}")
check(b"Issue credit note" in r.data, "partner sees Issue button")

# validation
today = date.today().isoformat()
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "partial", "description": ["Audit fee 2025"], "quantity": ["1"], "unit_price": ["1100"], "reason": "Too much", "issue_date": today})
check(b"more than the" in r.data, "over-credit refused")
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "partial", "description": ["Audit fee 2025"], "quantity": ["1"], "unit_price": ["100"], "reason": "", "issue_date": today})
check(b"Give the reason" in r.data, "reason required")
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "partial", "description": ["Audit fee 2025"], "quantity": ["1"], "unit_price": ["100"], "reason": "r", "issue_date": (date.today()+timedelta(days=2)).isoformat()})
check(b"cannot be in the future" in r.data, "future date refused")
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "partial", "description": ["Audit fee 2025"], "quantity": ["1"], "unit_price": ["100"], "reason": "r", "issue_date": (date.today()-timedelta(days=60)).isoformat()})
check(b"before the invoice" in r.data, "pre-invoice date refused")
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "partial", "description": ["Audit fee 2025"], "quantity": ["-1"], "unit_price": ["100"], "reason": "r", "issue_date": today})
check(b"above zero" in r.data, "negative qty refused")

# partial credit 200 ex VAT = 230
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "partial", "description": ["Audit fee 2025 - discount", ""], "quantity": ["1", ""], "unit_price": ["200", ""], "reason": "Fee discount agreed with client", "issue_date": today, "notes": "Per partner approval"}, follow_redirects=False)
check(r.status_code == 302, f"partial credit issued (got {r.status_code})")
with app.app_context():
    inv = Invoice.query.get(inv_id)
    check(abs(inv.credited_total - 230) < 1e-6, "credited 230")
    check(abs(inv.balance - 920) < 1e-6, f"balance 920 (got {inv.balance})")
    cn = inv.credit_notes[0]
    check(cn.credit_note_number == f"CN-{date.today().year}-0001", "numbered CN-0001")
    cn_id = cn.id
r = c.get(f"/credit-notes/{cn_id}")
check(r.status_code == 200 and b"Credit note" in r.data and b"Fee discount agreed" in r.data and b"230.00" in r.data, "print page renders")
open("/tmp/cnt/cn_print.html", "wb").write(r.data)
r = c.get("/credit-notes/")
check(r.status_code == 200 and b"CN-" in r.data, "list renders")
r = c.get(f"/invoices/{inv_id}")
check(b"CN-" in r.data and b"Part-credited" in r.data, "invoice page lists credit note")
r = c.get(f"/invoices/{inv_id}/print")
check(b"Less credit note" in r.data, "invoice print shows credit")
check(b"data:image/png;base64," not in r.data, "invoice print is unsigned until the Managing Partner signs")
open("/tmp/cnt/inv_print.html", "wb").write(r.data)

# lock
r = c.post(f"/invoices/{inv_id}/lines/add", data={"description": "x", "quantity": 1, "unit_price": 5}, follow_redirects=True)
check(b"credit note(s) issued against it" in r.data, "line add locked")
r = c.post(f"/invoices/{inv_id}/delete", follow_redirects=True)
check(b"cannot be deleted" in r.data, "delete blocked")

# payments: pay 920 -> Paid
r = c.post(f"/receipts/invoice/{inv_id}/add", data={"amount": "921", "paid_on": today, "method": "Cash"}, follow_redirects=True)
check(b"more than the" in r.data, "cannot overpay net balance")
r = c.post(f"/receipts/invoice/{inv_id}/add", data={"amount": "920", "paid_on": today, "method": "Cash"}, follow_redirects=True)
with app.app_context():
    inv = Invoice.query.get(inv_id)
    check(inv.status == "Paid" and inv.balance == 0, f"paid on net amount (status {inv.status})")

# credit after payment -> refund due
r = c.post(f"/credit-notes/invoice/{inv_id}/new", data={"mode": "partial", "description": ["Tax return rebate"], "quantity": ["1"], "unit_price": ["100"], "reason": "Return not filed", "issue_date": today})
with app.app_context():
    inv = Invoice.query.get(inv_id)
    check(abs(inv.refund_due - 115) < 1e-6, f"refund due 115 (got {inv.refund_due})")
    check(inv.status == "Paid", "still Paid")
    cn2 = inv.credit_notes[1].id
r = c.get(f"/credit-notes/{cn2}"); check(b"Refund due" in r.data, "refund shown on credit note")
r = c.get(f"/statements/client/{cl_id}")
check(r.status_code == 200 and b"Credit note" in r.data, "statement shows credit note rows")
open("/tmp/cnt/stmt.html","wb").write(r.data)
with app.app_context():
    from statements import build_statement, client_invoices
    inv = Invoice.query.get(inv_id)
    st = build_statement([inv], date.today()-timedelta(days=90), date.today())["USD"]
    check(abs(st["closing"] - (-115)) < 1e-6, f"statement closing -115 (got {st['closing']})")

# void second credit note -> refund gone
r = c.post(f"/credit-notes/{cn2}/void", data={"reason": "Raised in error"}, follow_redirects=True)
with app.app_context():
    inv = Invoice.query.get(inv_id)
    check(inv.refund_due == 0 and abs(inv.credited_total-230) < 1e-6 and inv.status == "Paid", "void restores")
# staff cannot void
cs = login(staff_id)
r = cs.post(f"/credit-notes/{cn_id}/void", data={"reason": "x"}); check(r.status_code == 403, "staff void 403")

# full credit on fresh invoice
with app.app_context():
    inv2 = Invoice(invoice_number="INV-2026-0002", client_id=cl_id, currency="USD", vat_pct=15.0, status="Sent", issue_date=date.today()-timedelta(days=5), due_date=date.today()+timedelta(days=25), bill_to="Acme")
    inv2.lines.append(InvoiceLineItem(description="Advisory", quantity=3, unit_price=333.33))
    db.session.add(inv2); db.session.commit(); i2 = inv2.id
r = c.post(f"/credit-notes/invoice/{i2}/new", data={"mode": "full", "reason": "Raised in error", "issue_date": today})
with app.app_context():
    inv2 = Invoice.query.get(i2)
    check(inv2.is_fully_credited and inv2.balance == 0 and inv2.payment_state == "Credited", "fully credited")
    check(inv2.status == "Sent", "status stays Sent")
r = c.get(f"/credit-notes/invoice/{i2}/new", follow_redirects=True); check(b"already been credited in full" in r.data, "no second credit")
r = c.post(f"/invoices/{i2}/status", data={"status": "Paid"}, follow_redirects=True); check(b"credited in full" in r.data, "cannot mark paid")
r = c.post(f"/invoices/{i2}/status", data={"status": "Cancelled"}, follow_redirects=True); check(b"cannot go back to Draft" in r.data, "cannot cancel credited")

# draft refused
with app.app_context():
    d = Invoice(invoice_number="INV-2026-0003", client_id=cl_id, currency="USD", vat_pct=15.0, status="Draft", issue_date=date.today(), bill_to="Acme")
    d.lines.append(InvoiceLineItem(description="x", quantity=1, unit_price=10)); db.session.add(d); db.session.commit(); did = d.id
r = c.get(f"/credit-notes/invoice/{did}/new", follow_redirects=True); check(b"still a Draft" in r.data, "draft refused")

# pdf + analytics + reminders + dashboard
with app.app_context():
    from invoice_pdf import build_invoice_pdf
    from invoicing import FIRM_LETTERHEAD
    inv = Invoice.query.get(inv_id)
    pdf = build_invoice_pdf(inv, FIRM_LETTERHEAD); open("/tmp/cnt/inv.pdf","wb").write(pdf)
    check(pdf[:4] == b"%PDF", "invoice pdf builds with credits")
    from analytics import build_report
    rep = build_report("USD", "12m")
    check(rep is not None, "analytics builds")
for url in ["/analytics/", "/invoices/", "/invoices/reminders", "/receipts/", "/statements/", f"/clients/{cl_id}"]:
    r = login(admin_id).get(url, follow_redirects=True)
    check(r.status_code == 200, f"GET {url} -> {r.status_code}")

r = login(partner_id).get(f"/credit-notes/{cn_id}/pdf")
check(r.status_code == 200 and r.data[:4] == b"%PDF", "credit note pdf")
open("/tmp/cnt/cn.pdf","wb").write(r.data)
import emailer
sent = {}
emailer.is_configured = lambda: True
emailer.send_email = lambda to, subject, text, html=None, attachments=None, cc=None: (sent.update(to=to, subject=subject, att=attachments), (True, ""))[1]
r = login(partner_id).post(f"/credit-notes/{cn_id}/email", data={"to": "acme@example.com"}, follow_redirects=True)
check(sent.get("to") == ["acme@example.com"] and sent["att"][0][0].startswith("CN-"), "credit note emailed with PDF")
r = login(partner_id).get(f"/credit-notes/{cn_id}"); check(b"Download PDF" in r.data, "download button")
print("FAILS:", fails)
