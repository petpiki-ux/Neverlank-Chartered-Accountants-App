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
import rbz_rates
rbz_rates.http_get = lambda url: (None, b'', 'offline in tests')   # never reach the real website from a test
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
    pid, sid = partner.id, staff.id

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(pid), login(sid)
F = "/firm-finance"
import firm_fx

check(S.get(F + "/exchange-rates").status_code == 403 and S.post(F + "/exchange-rates/add", data={"currency": "ZWG", "rate_date": "2025-01-01", "rate": "25"}).status_code == 403, "staff cannot see or change the rates")
r = P.get(F + "/exchange-rates")
check(r.status_code == 200 and b"Exchange rates" in r.data, "the register page opens (empty)")
# add, replace
P.post(F + "/exchange-rates/add", data={"currency": "ZWG", "rate_date": "2025-11-30", "rate": "26.5", "source": "RBZ"})
P.post(F + "/exchange-rates/add", data={"currency": "ZWG", "rate_date": "2025-11-30", "rate": "26.8"})
with app.app_context():
    rows = FirmExchangeRate.query.all()
    check(len(rows) == 1 and near(rows[0].rate, 26.8) and rows[0].source == "RBZ", "a second entry for the same date replaces the rate and keeps the source")
# refused
for bad in ({"currency": "ZWG", "rate_date": "2025-12-01", "rate": "0"}, {"currency": "ZWG", "rate_date": "2025-12-01", "rate": "-3"},
            {"currency": "EUR", "rate_date": "2025-12-01", "rate": "1"}, {"currency": "ZWG", "rate_date": "", "rate": "5"}, {"currency": "USD", "rate_date": "2025-12-01", "rate": "1"}):
    P.post(F + "/exchange-rates/add", data=bad)
with app.app_context():
    check(FirmExchangeRate.query.count() == 1, "zero, negative, unknown-currency, USD and dateless rates are refused")
# bulk paste
r = P.post(F + "/exchange-rates/bulk", data={"currency": "ZWG", "source": "RBZ", "lines": "2025-12-15, 27.1\n2025-12-31\t27.6\n31/01/2026 28.2\nnonsense\n2026-02-01, 0\n"}, follow_redirects=True)
check(b"3 rate(s) added" in r.data and b"not understood" in r.data, "bulk paste adds 3 and reports the 2 bad lines")
with app.app_context():
    check(firm_fx.register_rate("ZWG", date(2025, 12, 31)).rate == 27.6, "the rate on the date itself is used")
    check(firm_fx.register_rate("ZWG", date(2025, 12, 20)).rate == 27.1, "otherwise the latest earlier rate")
    check(firm_fx.register_rate("ZWG", date(2025, 11, 1)) is None, "no rate before the first one")
    check(firm_fx.register_rate("ZWG", date(2026, 1, 31)).rate == 28.2, "dd/mm/yyyy dates are understood")
# year-end created blank takes the closing rate
r = P.post(F + "/year-ends/new", data={"period_end": "2025-12-31", "zwg_rate": "", "opening_nil": "1"}, follow_redirects=True)
with app.app_context():
    y = FirmYearEnd.query.first(); y1 = y.id
    check(near(y.zwg_rate, 27.6), "a year-end created without a rate takes the closing rate from the register (27.6)")
    info = firm_fx.year_end_info(y)
    check(info["status"] == "match" and not info["stale"], "status: matches the register")
# a typed rate is respected, then the register button replaces it
P.post(F + f"/year-ends/{y1}/settings", data={"period_end": "2025-12-31", "zwg_rate": "30"})
with app.app_context():
    y = FirmYearEnd.query.get(y1)
    check(near(y.zwg_rate, 30) and firm_fx.year_end_info(y)["status"] == "differs", "a typed rate overrides, and the page flags that it differs")
r = P.get(F + f"/year-ends/{y1}")
check(r.status_code == 200 and b"register" in r.data.lower(), "the year-end page shows the register rate")
r = P.post(F + f"/exchange-rates/apply/{y1}", data={"back": "year_end"}, follow_redirects=True)
with app.app_context():
    check(near(FirmYearEnd.query.get(y1).zwg_rate, 27.6), "'Use the register rate' puts 27.6 back")
# blank save also uses the register
P.post(F + f"/year-ends/{y1}/settings", data={"period_end": "2025-12-31", "zwg_rate": ""})
with app.app_context():
    check(near(FirmYearEnd.query.get(y1).zwg_rate, 27.6), "saving the year-end with a blank rate uses the register")
# statements translate ZWG at the stored rate
with app.app_context():
    cl = Client(name="Z Co", created_by_id=pid); db.session.add(cl); db.session.commit()
    inv = Invoice(invoice_number="Z-1", client_id=cl.id, currency="ZWG", vat_pct=0.0, status="Sent", issue_date=date(2025, 6, 1), due_date=date(2025, 6, 1))
    inv.lines.append(InvoiceLineItem(description="Fee", quantity=1, unit_price=2760)); db.session.add(inv); db.session.commit()
    L = fl.build_ledger(year_ends=FirmYearEnd.query.all())
    ar = L.by_key["AR"]
    bal = sum(l["dr"] - l["cr"] for l in L.lines if l["account_id"] == ar.id)
    check(near(bal, 100.0), "a ZWG 2,760 invoice is 100.00 USD at 27.6")
P.post(F + "/exchange-rates/add", data={"currency": "ZWG", "rate_date": "2025-12-31", "rate": "30"})
P.post(F + f"/exchange-rates/apply/{y1}", data={})
with app.app_context():
    L = fl.build_ledger(year_ends=FirmYearEnd.query.all())
    bal = sum(l["dr"] - l["cr"] for l in L.lines if l["account_id"] == L.by_key["AR"].id)
    check(near(bal, 92.0), "after applying the new closing rate 30 the same invoice is 92.00 USD")
# stale flag
with app.app_context():
    FirmExchangeRate.query.filter(FirmExchangeRate.rate_date >= date(2025, 12, 1)).delete(); db.session.commit()
    y = FirmYearEnd.query.get(y1)
    info = firm_fx.year_end_info(y)
    check(info["row"].rate_date == date(2025, 11, 30) and info["age_days"] == 31 and not info["stale"], "a rate 31 days old is not yet stale")
    db.session.add(FirmExchangeRate(currency="ZWG", rate_date=date(2025, 10, 1), rate=25.0)); FirmExchangeRate.query.filter_by(rate_date=date(2025, 11, 30)).delete(); db.session.commit()
    info = firm_fx.year_end_info(FirmYearEnd.query.get(y1))
    check(info["stale"] and info["age_days"] == 91, "a rate 91 days old is flagged stale")
r = P.get(F + "/exchange-rates")
check(r.status_code == 200 and b"older" in r.data.lower() or b"stale" in r.data.lower() or r.status_code == 200, "the page still renders")
with app.app_context():
    rid = FirmExchangeRate.query.first().id
P.post(F + f"/exchange-rates/{rid}/delete")
with app.app_context():
    check(FirmExchangeRate.query.count() == 0 and near(FirmYearEnd.query.get(y1).zwg_rate, 30), "deleting a rate leaves the year-end's stored rate alone")
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
