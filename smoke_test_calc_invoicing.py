"""Invoice, receipt and credit-note money rules with figures worked out by hand: VAT, part payments, voids, credit notes, refunds."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date, timedelta
from app import create_app
from extensions import db
from models import *
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005): return abs(a - b) <= tol

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Calc Client", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    n = [0]
    def invoice(lines, vat=15.0, status="Sent", due=None):
        n[0] += 1
        inv = Invoice(invoice_number=f"T-{n[0]:04d}", client_id=cl.id, vat_pct=vat, status=status, due_date=due, created_by_id=admin.id)
        db.session.add(inv); db.session.flush()
        for q, p in lines: db.session.add(InvoiceLineItem(invoice_id=inv.id, description="x", quantity=q, unit_price=p))
        db.session.commit(); return inv
    def receipt(inv, amount, void=False):
        r = Receipt(receipt_number=f"R-{inv.id}-{len(inv.receipts)+1}", invoice_id=inv.id, amount=amount)
        if void: r.voided_at = __import__("datetime").datetime.utcnow()
        db.session.add(r); db.session.commit(); db.session.refresh(inv); return r
    def credit(inv, lines, vat=None):
        c = CreditNote(credit_note_number=f"C-{inv.id}-{len(inv.credit_notes)+1}", invoice_id=inv.id, vat_pct=inv.vat_pct if vat is None else vat, reason="r")
        db.session.add(c); db.session.flush()
        for q, p in lines: db.session.add(CreditNoteLine(credit_note_id=c.id, description="x", quantity=q, unit_price=p))
        db.session.commit(); db.session.refresh(inv); return c

    # 3 x 200.00 + 1 x 50.50 = 650.50; VAT 15% = 97.575; total 748.075
    inv = invoice([(3, 200), (1, 50.5)])
    check(near(inv.subtotal, 650.5) and near(inv.vat_amount, 97.575, 0.0001) and near(inv.total, 748.075, 0.0001), "3 x 200 + 50.50 = 650.50, VAT 15% = 97.575, total 748.075")
    check(near(inv.balance, 748.08, 0.006) or near(inv.balance, 748.07, 0.006), "nothing paid: the whole invoice is owed")
    check(invoice([(1, 100)], vat=0).total == 100, "a zero-rated invoice has no VAT")
    check(near(invoice([(2.5, 40)], vat=14.5).total, 100 * 1.145), "fractional quantities and a 14.5% rate: 2.5 x 40 = 100 + 14.5 = 114.50")
    check(invoice([]).total == 0 and invoice([(0, 100)]).total == 0, "an invoice with no lines, or nil quantity, totals nil")

    inv = invoice([(1, 1000)])      # total 1150
    check(near(inv.total, 1150), "1000 + 15% = 1150")
    receipt(inv, 400)
    check(near(inv.amount_paid, 400) and near(inv.balance, 750) and inv.payment_state == "Part-paid", "a 400 receipt leaves 750 owing and the invoice part-paid")
    r2 = receipt(inv, 100, void=True)
    check(near(inv.amount_paid, 400) and near(inv.balance, 750), "a voided receipt counts for nothing")
    receipt(inv, 750)
    check(near(inv.amount_paid, 1150) and inv.balance == 0 and inv.receiptable_balance == 0, "paying the rest clears the balance and nothing more can be receipted")
    check(inv.refund_due == 0, "no refund is due when paid exactly")
    check(inv.is_overdue is False, "a settled invoice is never overdue")

    # credit note after payment: invoice 1150 fully paid, credit 200 + VAT = 230 -> net 920, refund due 230
    c = credit(inv, [(1, 200)])
    check(near(c.subtotal, 200) and near(c.vat_amount, 30) and near(c.total, 230), "credit note 200 + 15% = 230")
    check(near(inv.credited_total, 230) and near(inv.net_total, 920), "credited 230 leaves the client liable for 920")
    check(near(inv.refund_due, 230), "the client has paid 230 more than they owe: refund due 230")
    check(near(inv.creditable_balance, 920), "a further 920 may still be credited")
    c2 = credit(inv, [(1, 800)])
    check(near(inv.credited_total, 230 + 920) and inv.net_total == 0 and inv.is_fully_credited, "crediting the rest makes the invoice fully credited")
    check(near(inv.refund_due, 1150) and inv.creditable_balance == 0, "everything paid is now refundable; nothing more can be credited")
    c2.voided_at = __import__("datetime").datetime.utcnow(); db.session.commit(); db.session.refresh(inv)
    check(near(inv.credited_total, 230) and not inv.is_fully_credited and near(inv.net_total, 920), "voiding a credit note puts the liability back")

    # credit note before payment lowers what can be receipted
    inv = invoice([(1, 1000)])
    credit(inv, [(1, 400)])      # 460 incl VAT -> net 690
    check(near(inv.net_total, 690) and near(inv.receiptable_balance, 690) and near(inv.balance, 690), "a credit note before payment cuts the balance to 690")
    receipt(inv, 690)
    check(inv.balance == 0 and inv.refund_due == 0, "paying the net amount settles it with no refund")

    # over-credit protection: net total never negative
    inv = invoice([(1, 100)])
    credit(inv, [(1, 100)]); credit(inv, [(1, 100)])
    check(inv.net_total == 0 and inv.creditable_balance == 0 and inv.balance == 0, "credit notes beyond the invoice never make the totals negative")

    # legacy Paid invoice (no receipts)
    inv = invoice([(1, 500)], status="Paid")
    check(inv.legacy_paid and inv.balance == 0 and near(inv.refund_due, 0), "an invoice marked Paid before receipts existed is settled in full")

    # overdue
    inv = invoice([(1, 100)], due=date.today() - timedelta(days=3))
    check(inv.is_overdue, "an unpaid invoice past its due date is overdue")
    receipt(inv, inv.total)
    check(not inv.is_overdue, "paying it removes the overdue flag")
    check(not invoice([(1, 100)], due=date.today() - timedelta(days=3), status="Cancelled").is_overdue, "a cancelled invoice is never overdue")
    check(not invoice([(1, 100)], due=date.today() + timedelta(days=3)).is_overdue, "an invoice not yet due is not overdue")

    # many small receipts add up exactly
    inv = invoice([(1, 100)])    # 115
    for _ in range(23): receipt(inv, 5)
    check(near(inv.amount_paid, 115) and inv.balance == 0, "23 receipts of 5.00 settle a 115.00 invoice exactly")

print("\n%d failing" % len(fails))
sys.exit(1 if fails else 0)
