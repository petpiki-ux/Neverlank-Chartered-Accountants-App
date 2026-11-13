"""Speed guard: pages must not run a few queries per row. With 120 invoices the invoice list, analytics and ordinary pages must stay under a fixed
number of database queries; a loop that asks the database once per invoice would be many times over the limit."""
import os, sys, tempfile, random
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
from datetime import date, timedelta
from app import create_app
from extensions import db
from models import *
from sqlalchemy import event
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
random.seed(5)
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cls = [Client(name=f"C{i}", created_by_id=admin.id) for i in range(12)]
    db.session.add_all(cls); db.session.commit()
    engs = [Engagement(client_id=c.id, title="Audit", type="Audit", partner_id=admin.id, acceptance_required=False) for c in cls]
    db.session.add_all(engs); db.session.commit()
    n = 0
    for c in cls:
        for k in range(10):
            n += 1
            i = Invoice(invoice_number=f"Q-{n}", client_id=c.id, vat_pct=15, status=random.choice(["Sent", "Sent", "Paid"]), issue_date=date.today() - timedelta(days=random.randint(0, 300)),
                        due_date=date.today() - timedelta(days=random.randint(10, 100)), created_by_id=admin.id)
            db.session.add(i); db.session.flush()
            for l in range(3): db.session.add(InvoiceLineItem(invoice_id=i.id, description="x", quantity=1, unit_price=random.randint(50, 500)))
            if i.status == "Paid": db.session.add(Receipt(receipt_number=f"RR-{n}", invoice_id=i.id, amount=100.0))
    db.session.commit()
    uid, cid, eid = admin.id, cls[0].id, engs[0].id
    eng = app.extensions["sqlalchemy"].engine
counter = {"n": 0}
@event.listens_for(eng, "before_cursor_execute")
def _count(conn, cur, stmt, params, ctx, many): counter["n"] += 1
c = app.test_client()
with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
c.get("/invoices/")      # first visit compiles templates; count the next ones
def queries(path):
    counter["n"] = 0
    r = c.get(path)
    return r.status_code, counter["n"]
for path, limit in [("/invoices/", 90), ("/analytics/", 90), (f"/clients/{cid}", 90), (f"/engagements/{eid}", 120)]:
    code, q = queries(path)
    check(code == 200 and q <= limit, f"{path}: {q} queries for 120 invoices (limit {limit})")
print("\n%d failing" % len(fails))
sys.exit(1 if fails else 0)
