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

import rbz_rates, fx_engine, qpd, firm_fx
rbz_rates.http_get = lambda url: (None, b"", "offline in tests")
D = lambda m, d: date(2026, m, d)

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    pid, sid = partner.id, staff.id
    fx_engine.config().auto_fetch = False
    # Tax rates (ZIMRA), as uploaded under Tax > FX rates
    for d, r in ((D(9, 26), 26.00), (D(9, 28), 26.50), (D(9, 29), 26.60), (D(9, 30), 26.70)):
        db.session.add(FxRate(rate_date=d, rate=r, source="ZIMRA"))
    # what the RBZ published (bid, ask, mid)
    for d, b, a, m in ((D(9, 28), 25.90, 27.10, 26.50), (D(9, 29), 26.00, 27.30, 26.65), (D(9, 30), 26.20, 27.60, 26.90)):
        db.session.add(FirmRbzReading(rate_date=d, bid=b, ask=a, mid=m))
    db.session.commit()

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(pid), login(sid)
F = "/firm-finance"
E = fx_engine

with app.app_context():
    r = E.rate_on(D(9, 28)); t = E.rate_on(D(9, 28), "tax")
    check(near(r.rate, 26.50) and r.kind == "rbz" and r.exact and near(t.rate, 26.50) and t.kind == "zimra", "28 Sep: both agree at 26.50; statements read the RBZ, Tax reads ZIMRA")
    r = E.rate_on(D(9, 29)); t = E.rate_on(D(9, 29), "tax")
    check(near(r.rate, 26.65) and near(t.rate, 26.60), "29 Sep: statements 26.65 (RBZ mid), Tax 26.60 (ZIMRA)")
    r = E.rate_on(D(9, 30)); t = E.rate_on(D(9, 30), "tax")
    check(near(r.rate, 26.90) and near(t.rate, 26.70), "30 Sep: statements 26.90, Tax 26.70")
    r = E.rate_on(D(9, 26))
    check(near(r.rate, 26.00) and r.kind == "zimra" and r.exact, "26 Sep (a Saturday): only ZIMRA has it, so the engine falls back to it")
    r = E.rate_on(D(9, 27))
    check(near(r.rate, 26.00) and not r.exact and r.age_days == 1 and r.rate_date == D(9, 26), "27 Sep carries Saturday's rate forward (one day old)")
    check(E.rate_on(D(1, 1)) is None, "before any reading there is no rate")
    check(E.rate_on(D(9, 30), "reporting", "USD").rate == 1.0 and E.rate_on(D(9, 30), "reporting", "EUR") is None, "USD is 1; an unknown currency has none")
    # verdicts
    rows = {r["date"]: r for r in E.days_between(D(9, 26), D(9, 30))}
    check(rows[D(9, 28)]["code"] == "same" and rows[D(9, 29)]["code"] == "near" and rows[D(9, 30)]["code"] == "differs" and rows[D(9, 26)]["code"] == "norbz", "check: 28 same, 29 within 0.5%, 30 differs, 26 has no RBZ reading")
    check(E.counts_of(list(rows.values())) == {"same": 1, "near": 1, "differs": 1, "norbz": 1}, "counts 1/1/1/1")
    check(E.verdict(25.90, (25.90, 27.10, 26.50))[0] == "bid" and E.verdict(27.10, (25.90, 27.10, 26.50))[0] == "ask" and E.verdict(26.5, (25.9, 27.1, 26.5004))[0] == "same", "equal to the bid, equal to the ask, and mid within four decimals")
    # basis
    cfg = E.config(); cfg.rbz_basis = "bid"; db.session.commit()
    check(near(E.rate_on(D(9, 30)).rate, 26.20) and E.rate_on(D(9, 30)).source == "RBZ interbank bid", "choosing the bid changes every date at once")
    cfg.rbz_basis = "ask"; db.session.commit()
    check(near(E.rate_on(D(9, 30)).rate, 27.60), "and the ask")
    cfg.rbz_basis = "mid"; cfg.prefer = "zimra"; db.session.commit()
    check(near(E.rate_on(D(9, 30)).rate, 26.70) and E.rate_on(D(9, 30)).kind == "zimra", "if the Partner prefers ZIMRA, statements read ZIMRA first")
    cfg.prefer = None; db.session.commit()
    # typed rates
    db.session.add(FirmExchangeRate(currency="ZWG", rate_date=D(9, 30), rate=27.00, source="Bank rate")); db.session.commit()
    check(near(E.rate_on(D(9, 30)).rate, 27.00) and E.rate_on(D(9, 30)).kind == "manual", "a typed rate beats the RBZ for the statements")
    check(near(E.rate_on(D(9, 30), "tax").rate, 26.70) and E.rate_on(D(9, 30), "tax").kind == "zimra", "but Tax still reads ZIMRA")
    db.session.add(FirmExchangeRate(currency="ZWG", rate_date=D(10, 1), rate=27.10, source="typed")); db.session.add(FirmRbzReading(rate_date=D(10, 1), bid=26.9, ask=27.3, mid=27.05))
    db.session.add(FirmRbzReading(rate_date=D(10, 2), bid=27.0, ask=27.4, mid=27.20)); db.session.commit()
    check(near(E.rate_on(D(10, 1), "tax").rate, 27.10) and E.rate_on(D(10, 1), "tax").kind == "manual", "1 Oct: Tax has no ZIMRA rate that day, so a typed rate comes next")
    check(near(E.rate_on(D(10, 2), "tax").rate, 27.20) and E.rate_on(D(10, 2), "tax").kind == "rbz", "2 Oct: only the RBZ has it, and being newer it beats the older ZIMRA rate")
    check(near(E.rate_on(D(10, 3)).rate, 27.20) and not E.rate_on(D(10, 3)).exact, "3 Oct (Saturday) carries the 2nd forward")
    # older register rows that carry the RBZ figures in their note
    db.session.add(FirmExchangeRate(currency="ZWG", rate_date=D(10, 5), rate=27.30, source="RBZ interbank mid", note="bid 27.1 / ask 27.5 / mid 27.3, fetched from rbz.co.zw")); db.session.commit()
    r = E.rate_on(D(10, 5))
    check(near(r.rate, 27.30) and r.kind == "rbz", "an older RBZ row in the register still counts as an RBZ reading")
    cfg.rbz_basis = "bid"; db.session.commit()
    check(near(E.rate_on(D(10, 5)).rate, 27.10), "and honours the chosen figure")
    cfg.rbz_basis = "mid"; db.session.commit()
    check(not any(x["manual"] and x["manual"].source.startswith("RBZ") for x in E.days_between(D(10, 1), D(10, 5))), "such a row is not mistaken for a typed rate")
    # converting and averages
    v, used = E.convert(100, "USD", "ZWG", D(9, 30), "reporting")
    check(near(v, 2700.0) and used.kind == "manual", "USD 100 at 27.00 = ZWG 2,700.00")
    v, _ = E.convert(2700, "ZWG", "USD", D(9, 30), "reporting")
    check(near(v, 100.0), "ZWG 2,700 = USD 100.00")
    v, used = E.convert(100, "USD", "ZWG", D(9, 30), "tax")
    check(near(v, 2670.0), "for Tax the same USD 100 is ZWG 2,670.00 (ZIMRA 26.70)")
    check(E.convert(50, "USD", "USD", D(9, 30)) == (50, None) and E.convert(50, "USD", "ZWG", D(1, 1)) == (None, None), "same currency is unchanged; no rate gives nothing")
    avg, n = E.average_rate(D(9, 28), D(9, 29))
    check(n == 2 and near(avg, (26.50 + 26.65) / 2, 1e-6), "average over 28-29 Sep = 26.575 from 2 days")
    # Tax (QPD) reads the same engine
    q = qpd.get_fx_rate_as_of(D(9, 30))
    check(near(q.rate, 26.70) and q.rate_date == D(9, 30), "Tax (QPD) gets 26.70 for 30 Sep from the engine")
    q = qpd.get_fx_rate_as_of(D(10, 2))
    check(near(q.rate, 27.20), "and now falls back to the RBZ where ZIMRA has nothing newer")
    check(qpd.get_fx_rate_as_of(None) is None, "no date gives nothing")

# the pages
check(S.get(F + "/exchange-rates").status_code == 403 and S.get(F + "/exchange-rates/compare").status_code == 403, "staff cannot open the Firm Finance rate pages")
r = P.get(F + "/exchange-rates?from=2026-09-25&to=2026-10-06")
check(r.status_code == 200 and b"Rates by day" in r.data and b"26.9" in r.data and b"27.1" in r.data and b"Delete typed" in r.data, "the page lists each day with every reading")
r = P.get(F + "/exchange-rates?amount=100&frm=USD&to_cur=ZWG&on=2026-09-30&purpose=tax")
check(b"2,670.00 ZWG" in r.data, "the converter works for Tax")
r = P.get(F + "/exchange-rates?amount=100&frm=USD&to_cur=ZWG&on=2026-09-30&purpose=reporting")
check(b"2,700.00 ZWG" in r.data, "and for reporting")
r = P.get(F + "/exchange-rates/compare?from=2026-09-20&to=2026-09-30")
check(r.status_code == 200 and b"1 same as the RBZ mid" in r.data and b"1 differ" in r.data and b"within 0.5%" in r.data, "the Tax-against-RBZ check shows 1 same, 1 near, 1 differs")
r = P.post(F + "/exchange-rates/rbz/settings", data={"basis": "mid", "prefer": "zimra", "auto_fetch": ""}, follow_redirects=True)
with app.app_context():
    check(fx_engine.config().prefer == "zimra" and fx_engine.config().auto_fetch is False, "the preference is saved")
    fx_engine.config().prefer = None; db.session.commit()
# JSON lookup for any other part of the app
c = app.test_client()
r = c.get(F + "/exchange-rates/lookup?on=2026-09-30")
check(r.status_code in (302, 401), "the lookup needs a sign-in")
r = S.get(F + "/exchange-rates/lookup?on=2026-09-29&purpose=tax")
j = r.get_json()
check(r.status_code == 200 and near(j["rate"], 26.60) and j["exact"] and j["kind"] == "zimra", "any signed-in user can look a rate up (staff): 26.60 for Tax on 29 Sep")
r = S.get(F + "/exchange-rates/lookup?on=2026-09-30&amount=10&frm=USD&to=ZWG")
j = r.get_json()
check(near(j["converted"], 270.0) and j["purpose"] == "reporting", "with an amount it converts: USD 10 = ZWG 270.00")
r = S.get(F + "/exchange-rates/lookup?on=2025-01-01")
check(r.status_code == 404 and r.get_json()["found"] is False, "a date with no rate answers 404")

# year-end closing rate comes from the engine
with app.app_context():
    db.session.add(FxRate(rate_date=D(8, 31), rate=25.80, source="ZIMRA")); db.session.commit()
r = P.post(F + "/year-ends/new", data={"period_end": "2026-08-31", "zwg_rate": "", "opening_nil": "1"}, follow_redirects=True)
with app.app_context():
    y = FirmYearEnd.query.filter_by(period_end=D(8, 31)).first()
    check(near(y.zwg_rate, 25.80), "a year-end created without a rate takes the closing rate even when only a Tax (ZIMRA) rate exists: 25.80")
    info = firm_fx.year_end_info(y)
    check(info["status"] == "match" and info["row"].kind == "zimra", "and the page knows where it came from")
r = P.post(F + "/year-ends/new", data={"period_end": "2026-09-30", "zwg_rate": "", "opening_nil": "1"}, follow_redirects=True)
with app.app_context():
    check(near(FirmYearEnd.query.filter_by(period_end=D(9, 30)).first().zwg_rate, 27.00), "30 Sep year-end takes the typed 27.00")
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
