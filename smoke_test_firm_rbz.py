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
from datetime import date, datetime, timedelta
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

import re, io, threading
import rbz_rates
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from datetime import date, datetime, timedelta

TODAY = date.today()
def prev_weekday(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
LAST_PUB = prev_weekday(TODAY)             # the RBZ has published up to the last working day before today
FIRST_PUB = date(2026, 7, 1)
ST = date(2026, 9, 10), date(2026, 9, 14), date(2026, 9, 18), date(2026, 9, 21)     # garbage, server error, copy of the 17th, dead link
HOLIDAY = date(2026, 8, 31)                 # a Monday with no file

def make_pdf(printed, bid, ask, mid, garbage=False, us_style=False):
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(60, 800, "RESERVE BANK OF ZIMBABWE - INTERBANK RATES")
    c.drawString(60, 780, (printed.strftime("%A, %B ") + f"{printed.day}, {printed.year}") if us_style else (printed.strftime("%A, ") + f"{printed.day:02d} " + printed.strftime("%B %Y")))
    c.drawString(60, 750, "CURRENCY INDICES BID ASK MID RATE BID RATE ASK RATE MID RATE")
    if not garbage:
        c.drawString(60, 730, "ZAR 0.0555 0.0555 0.0555 1.4400 1.5100 1.4750")
        c.drawString(60, 710, f"USD 1 1 1.0000 {bid:.4f} {ask:.4f} {mid:.4f}")
    else:
        c.drawString(60, 730, "No rates today")
    c.save(); return buf.getvalue()

def rate_for(d):                       # bid, ask, mid
    mid = round(26.0 + d.day * 0.01 + d.month * 0.1, 4)
    return round(mid - 0.65, 4), round(mid + 0.66, 4), mid

def file_url(d):
    """The RBZ's own (irregular) address for a day's file."""
    m = rbz_rates.MONTHS[d.month - 1]
    folder, year, name = "Exchange_Rates", d.year, f"RATES_{d.day}_{m.upper()}_{d.year}.pdf"
    if d == date(2026, 9, 2): name = f"RATES_02_{m.upper()}_{d.year}.pdf"          # zero-padded
    if d == date(2026, 9, 9): folder = "Mosi-Rates"                                 # other folder
    if d == date(2026, 9, 15): year = 2024                                          # typo in the folder
    return f"https://www.rbz.co.zw/documents/{folder}/{year}/{m}/{name}"

def link_for(d):
    if d in ST and d == date(2026, 9, 18): return file_url(date(2026, 9, 17))      # the 18th links to the 17th's file
    if d == HOLIDAY or d > LAST_PUB or d.weekday() >= 5 or d < FIRST_PUB: return None
    return file_url(d)

FILES = {}
for n in range((LAST_PUB - FIRST_PUB).days + 1):
    d = FIRST_PUB + timedelta(days=n)
    if d.weekday() < 5 and d != HOLIDAY and d not in ST:
        b, a, mi = rate_for(d)
        FILES[file_url(d)] = (200, make_pdf(d, b, a, mi, us_style=(d.day % 2 == 0)))
FILES[file_url(date(2026, 9, 10))] = (200, make_pdf(date(2026, 9, 10), 1, 1, 1, garbage=True))
FILES[file_url(date(2026, 9, 14))] = (500, b"")
# 18 Sep links to the 17th's file (already in FILES); 21 Sep links to a file that does not exist

MONTH_IDS = {}
def month_page_url(y, m):
    return f"https://www.rbz.co.zw/index.php/research/markets/exchange-rates/13-daily-exchange-rates/{1500 + (y - 2026) * 12 + m}-{rbz_rates.MONTHS[m - 1].lower()}-{y}"
months = []
d = date(FIRST_PUB.year, FIRST_PUB.month, 1)
while d <= TODAY:
    months.append((d.year, d.month)); d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
months.reverse()                        # newest first, as the RBZ lists them

def month_html(y, m):
    rows = ""
    last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    for n in range(1, last.day + 1):
        d = date(y, m, n)
        if d.weekday() >= 5: continue
        if d == date(2026, 9, 21):
            link = file_url(d)                      # dead link
        else:
            link = link_for(d)
        if link:
            href = link.replace("https://www.rbz.co.zw", "") if d.day % 2 else link             # some links relative, some absolute
            rows += f'<tr><td>{n:02d}</td><td><a href="{href}">.pdf [058 KB]</a></td></tr>'
        else:
            rows += f'<tr><td>{n:02d}</td><td>.pdf [058 KB]</td></tr>'
    return f"<html><body><h1>Exchange Rates</h1><table><tr><td>DATE</td><td>INTERBANK RATE</td></tr>{rows}</table></body></html>"

def listing_html(page):
    chunk = months[page * 3: page * 3 + 3]                 # three months to a page, to exercise the paging
    links = "".join(f'<li><a href="{month_page_url(y, m)}">{rbz_rates.MONTHS[m - 1]} {y}</a></li>' for y, m in chunk)
    return f"<html><body><ul>{links}</ul><a href='?start=10'>2</a></body></html>"

CALLS = []
OFFLINE = {"on": False}
LISTING = rbz_rates.LISTING
def fake_get(url):
    CALLS.append(url)
    if OFFLINE["on"]:
        return None, b"", "ConnectionError: offline"
    if url == LISTING or url.startswith(LISTING + "?start="):
        page = 0 if url == LISTING else int(url.split("=")[1]) // 10
        return 200, listing_html(page).encode(), ""
    for (y, m) in months:
        if url == month_page_url(y, m):
            return 200, month_html(y, m).encode(), ""
    if url in FILES:
        return FILES[url][0], FILES[url][1], ""
    return 404, b"", ""
rbz_rates.http_get = fake_get
def pages():
    return [u for u in CALLS if u.endswith(".pdf")]

# ---- the parser and the fetchers on their own (no app needed)
txt = "Thursday, 03 September 2026\nCURRENCY BID ASK\nUSD 1 1 1.0000 25.9876 27.3202 26.6539\n"
rec = rbz_rates.parse_text(txt)
check(rec and rec["date"] == date(2026, 9, 3) and rec["bid"] == 25.9876 and rec["ask"] == 27.3202 and rec["mid"] == 26.6539, "the USD row reads bid 25.9876, ask 27.3202, mid 26.6539 dated 3 Sep 2026")
check(rbz_rates.parse_text("Tuesday, June 30, 2026\nUSD 1 1 1.0000 26.1006 27.4390 26.7698\n")["date"] == date(2026, 6, 30), "the American-style date 'June 30, 2026' is understood")
check(rbz_rates.parse_text("02 January 2026\nUSD 1 1 1.0000 25.2747 26.5709 25.9228\n")["date"] == date(2026, 1, 2), "'02 January 2026' is understood")
check(rbz_rates.parse_text("USD 1 1 1.0000\n") is None and rbz_rates.parse_text("EUR 1 1 1 25 27 26\n") is None, "a USD row without ZWG figures, or no USD row, is refused")
check(rbz_rates.parse_text("USD 1 1 1.0000 27.3202 25.9876 26.6539\n") is None, "bid above ask is refused rather than guessed")
check(rbz_rates.parse_text("USD 1 1 1.0000 1,025.50 1,030.00 1,027.75\n")["mid"] == 1027.75, "thousands separators are understood")
# monthly page and listing
lst = rbz_rates.parse_listing(listing_html(0))
check(len(lst) == 3 and (months[0] in lst), "the listing page yields its monthly pages")
idx = rbz_rates.parse_month_page(month_html(2026, 9), 2026, 9, month_page_url(2026, 9))
check(idx[2] == [file_url(date(2026, 9, 2))] and "Mosi-Rates" in idx[9][0] and "/2024/" in idx[15][0] and idx[18] == [file_url(date(2026, 9, 17))], "the monthly page gives the RBZ's own addresses: zero-padded, Mosi-Rates, 2024 typo, 18th -> 17th's file")
check(21 in idx and 5 not in idx and 6 not in idx, "weekends have no entry")
check(1 in idx and idx[1][0].startswith("https://www.rbz.co.zw/"), "relative links are made absolute")

CALLS.clear()
r = rbz_rates.fetch_day(date(2026, 9, 3))
check(r["status"] == "ok" and r["mid"] == rate_for(date(2026, 9, 3))[2] and r["date"] == date(2026, 9, 3), "a day's PDF is fetched and read")
r = rbz_rates.fetch_day(date(2026, 9, 2))
check(r["status"] == "ok" and r["mid"] == rate_for(date(2026, 9, 2))[2], "2 Sep (file name RATES_02_...) is found - this failed before")
r = rbz_rates.fetch_day(date(2026, 9, 9))
check(r["status"] == "ok" and r["mid"] == rate_for(date(2026, 9, 9))[2], "9 Sep (in the Mosi-Rates folder) is found - this failed before")
r = rbz_rates.fetch_day(date(2026, 9, 15))
check(r["status"] == "ok" and r["mid"] == rate_for(date(2026, 9, 15))[2], "15 Sep (the 2024 folder typo) is found - this failed before")
r = rbz_rates.fetch_day(date(2026, 9, 18))
check(r["status"] == "unreadable" and "copy of another day" in r["message"] and "17 Sep 2026" in r["message"], "18 Sep (linked to the 17th's file) is refused and says why")
r = rbz_rates.fetch_day(date(2026, 9, 10))
check(r["status"] == "unreadable" and "USD row" in r["message"], "a PDF with no USD row is reported unreadable")
r = rbz_rates.fetch_day(date(2026, 9, 14))
check(r["status"] == "unreadable" and "500" in r["message"], "a server error on the file is reported")
r = rbz_rates.fetch_day(date(2026, 9, 21))
check(r["status"] == "unreadable" and "leads nowhere" in r["message"], "a dead link is reported")
r = rbz_rates.fetch_day(HOLIDAY)
check(r["status"] == "none", "a day with no link (public holiday) has no file")
r = rbz_rates.fetch_day(date(2026, 9, 5))
check(r["status"] == "none", "a Saturday has no file")
r = rbz_rates.fetch_day(date(2024, 3, 1))
check(r["status"] == "none" and "Zimbabwe dollars" in r["message"], "dates before the ZWG began are refused")
# the monthly page cannot be read: fall back to the usual address
saved = dict(rbz_rates._MONTH_URLS); rbz_rates._INDEX_CACHE.clear(); rbz_rates._MONTH_URLS.clear()
_real = rbz_rates.http_get
rbz_rates.http_get = lambda u: (404, b"", "") if ("/index.php" in u) else fake_get(u)
r = rbz_rates.fetch_day(date(2026, 9, 3))
check(r["status"] == "ok", "if the RBZ listing cannot be read the usual file address is tried instead")
rbz_rates.http_get = fake_get; rbz_rates._INDEX_CACHE.clear()
OFFLINE["on"] = True
check(rbz_rates.fetch_day(date(2026, 9, 3))["status"] == "error", "no connection is reported as an error")
OFFLINE["on"] = False; rbz_rates._INDEX_CACHE.clear()
rec, notes = rbz_rates.fetch_latest(TODAY)
check(rec and rec["date"] == LAST_PUB, "the latest rate is the last working day published")
rec, notes = rbz_rates.fetch_latest(date(2026, 9, 18))
check(rec and rec["date"] == date(2026, 9, 17), "18 Sep falls back to the 17th's own file")
rec, notes = rbz_rates.fetch_latest(date(2026, 5, 20), max_back=3)
check(rec is None and notes, "nothing published in the window gives no rate and a note")
res = rbz_rates.fetch_month(2026, 9, date(2026, 10, 18), skip={date(2026, 9, 1)})
check(len(res["found"]) == 22 - 1 - 4 and res["done"], "September 2026: 22 working days less one skipped and four unusable = 17 fetched")
res = rbz_rates.fetch_month(2026, 9, date(2026, 10, 18), budget=-1)
check(res["done"] is False and res["stopped_at"] == date(2026, 9, 1), "a spent time budget stops the month and says where")
rbz_rates._INDEX_CACHE.clear()
# a month on the second listing page is found by paging through the listing
rbz_rates._MONTH_URLS.clear(); rbz_rates._INDEX_CACHE.clear(); CALLS.clear()
idx, url, note = rbz_rates.month_index(2026, 7)
check(idx is not None and 1 in idx and any("start=" in c for c in CALLS), "July 2026 is found by paging through the RBZ listing")

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
import fx_engine
app.config["RBZ_SYNC"] = True
def reset():
    with app.app_context():
        FirmExchangeRate.query.delete(); FirmRbzReading.query.delete(); FxRate.query.delete(); FirmFxMiss.query.delete(); FirmFxConfig.query.delete(); db.session.commit()
    rbz_rates._INDEX_CACHE.clear()

# staff
for url in ("/exchange-rates/rbz/latest", "/exchange-rates/rbz/month", "/exchange-rates/rbz/settings", "/exchange-rates/rbz/year-end/1", "/exchange-rates/rbz/catch-up"):
    check(S.post(F + url, data={}).status_code == 403, "staff cannot use " + url)

# ---- catch-up
reset()
T = date(2026, 9, 25)
with app.app_context():
    msg = firm_fx.catch_up(today=T)
    want = [d for d in rbz_rates.working_days(max(T - timedelta(days=45), FIRST_PUB), T)]
    bad = set(ST) | {HOLIDAY}
    rows = {r.rate_date: r for r in FirmRbzReading.query.all()}
    check(set(rows) == {d for d in want if d not in bad}, f"catch-up read every working day the RBZ published ({len(rows)}) and none of the unusable ones")
    e = lambda d: fx_engine.rate_on(d)
    check(e(date(2026, 9, 2)).source == "RBZ interbank mid" and near(e(date(2026, 9, 9)).rate, rate_for(date(2026, 9, 9))[2], 1e-6) and near(e(date(2026, 9, 15)).rate, rate_for(date(2026, 9, 15))[2], 1e-6), "including the three irregular links")
    misses = {m.rate_date: m.reason for m in FirmFxMiss.query.all()}
    check(set(misses) == bad, "the five unusable days are remembered with reasons")
    check("copy of another day" in misses[date(2026, 9, 18)], "the 18 Sep reason says the file is another day's")
    check("added" in msg and "no usable file" in msg, "the summary line says what happened: " + msg)
    n = len(pages())
    msg2 = firm_fx.catch_up(today=T)
    check("up to date" in msg2 and len(pages()) == n, "a second catch-up asks the RBZ for nothing: settled days are not retried")
    n = len(pages())
    firm_fx.catch_up(today=T, force=True)
    check(len(pages()) > n, "'fill every missing day now' retries the days that failed")
    # a recent miss is retried after six hours, not before
    FirmFxMiss.query.filter_by(rate_date=date(2026, 9, 24)).delete()
    FirmRbzReading.query.filter_by(rate_date=date(2026, 9, 24)).delete(); db.session.commit()
    d24 = firm_fx.missing_days(T)
    check(d24 == [date(2026, 9, 24)], "a day with no rate and no earlier miss is wanted")
    firm_fx._note_miss(date(2026, 9, 24), "x"); db.session.commit()
    check(firm_fx.missing_days(T) == [], "a day missed a moment ago is left alone")
    FirmFxMiss.query.filter_by(rate_date=date(2026, 9, 24)).first().checked_at = datetime.utcnow() - timedelta(hours=7); db.session.commit()
    check(firm_fx.missing_days(T) == [date(2026, 9, 24)], "and asked about again after six hours")

# year-end with no rate gets its closing rate
reset()
with app.app_context():
    y = FirmYearEnd(period_end=date(2026, 8, 31), zwg_rate=None); db.session.add(y)
    y2 = FirmYearEnd(period_end=date(2026, 7, 31), zwg_rate=31.0); db.session.add(y2); db.session.commit(); ya, yb = y.id, y2.id
    firm_fx.catch_up(today=date(2026, 9, 2))
    check(near(FirmYearEnd.query.get(ya).zwg_rate, rate_for(date(2026, 8, 28))[2], 1e-6), "a year-end with no rate takes the closing rate (31 Aug is a holiday: 28 Aug's rate)")
    check(near(FirmYearEnd.query.get(yb).zwg_rate, 31.0), "a year-end with a rate already is not touched")
    check(FirmRbzReading.query.filter_by(rate_date=date(2026, 7, 31)).count() == 1, "but its closing day is read from the RBZ (year-end window)")

# ---- sign-in
reset()
with app.app_context():
    FirmYearEnd.query.delete(); db.session.commit()
    app.config["RBZ_SYNC"] = True
c = app.test_client()
CALLS.clear()
r = c.post("/auth/login", data={"username": "stf", "password": "x"})
check(r.status_code in (302, 303) and not pages() and not [u for u in CALLS if "index.php" in u], "a staff sign-in does not touch the RBZ website")
c = app.test_client()
r = c.post("/auth/login", data={"username": "ptn", "password": "x"})
with app.app_context():
    got = FirmRbzReading.query.count()
    row = fx_engine.rate_on(LAST_PUB)
    check(r.status_code in (302, 303) and got > 20 and row is not None and row.rate_date == LAST_PUB and near(row.rate, rate_for(LAST_PUB)[2], 1e-6), f"the Managing Partner signing in fetched today's rate and {got - 1} missing days")
    check(FirmFxConfig.query.first().last_message.startswith("Catch-up"), "and the result is recorded")
n = len(CALLS)
c = app.test_client(); c.post("/auth/login", data={"username": "ptn", "password": "x"})
check(len(CALLS) == n, "signing in again within six hours does not ask again")
with app.app_context():
    cfg = FirmFxConfig.query.first(); cfg.last_attempt = datetime.utcnow() - timedelta(hours=7); cfg.auto_fetch = False; db.session.commit()
c = app.test_client(); c.post("/auth/login", data={"username": "ptn", "password": "x"})
check(len(CALLS) == n, "with automatic fetching switched off sign-in never calls the website")
with app.app_context():
    cfg = FirmFxConfig.query.first(); cfg.auto_fetch = True; db.session.commit()
OFFLINE["on"] = True
c = app.test_client(); r = c.post("/auth/login", data={"username": "ptn", "password": "x"})
check(r.status_code in (302, 303), "sign-in works when the RBZ website is down")
OFFLINE["on"] = False
# in the background (as in production)
reset()
app.config["RBZ_SYNC"] = False
with app.app_context():
    started = firm_fx.start_catch_up(app)
for t in threading.enumerate():
    if t is not threading.current_thread() and t.daemon:
        t.join(timeout=60)
with app.app_context():
    check(started and FirmRbzReading.query.count() > 20, "run in a background thread it fills the register without holding up the page")
app.config["RBZ_SYNC"] = True

# ---- page open
reset()
CALLS.clear()
r = P.get(F + "/exchange-rates")
check(r.status_code == 200 and b"Reserve Bank of Zimbabwe rates" in r.data and b"Fill every missing day now" in r.data, "the page has the RBZ section")
with app.app_context():
    row = fx_engine.rate_on(LAST_PUB)
    exp = rate_for(LAST_PUB)
    check(row is not None and row.rate_date == LAST_PUB and near(row.rate, exp[2], 1e-6) and row.source == "RBZ interbank mid" and f"bid {exp[0]:g}" in row.note, "opening the page fetched the latest RBZ mid rate, with bid and ask in the note")
n = len(CALLS)
r = P.get(F + "/exchange-rates")
check(len(CALLS) == n and b"Catch-up" in r.data, "opening it again within six hours does not fetch again, and shows the last result")
r = P.get(F + "/exchange-rates")
check(b"no usable file for" in r.data, "the days the RBZ has no usable file for are listed")

# manual latest
with app.app_context():
    FirmExchangeRate.query.delete(); FirmRbzReading.query.delete(); db.session.add(FirmExchangeRate(currency="ZWG", rate_date=LAST_PUB, rate=99.0, source="typed")); db.session.commit()
r = P.post(F + "/exchange-rates/rbz/latest", data={}, follow_redirects=True)
with app.app_context():
    check(near(fx_engine.rate_on(LAST_PUB).rate, 99.0) and b"stays in use" in r.data, "a rate typed for that date stays in use")
    check(FirmRbzReading.query.filter_by(rate_date=LAST_PUB).count() == 1, "while the RBZ's figures are still kept beside it")
    typed = FirmExchangeRate.query.filter_by(rate_date=LAST_PUB).first(); tid = typed.id
r = P.post(F + f"/exchange-rates/{tid}/delete", follow_redirects=True)
with app.app_context():
    check(near(fx_engine.rate_on(LAST_PUB).rate, rate_for(LAST_PUB)[2]), "delete the typed rate and the RBZ's takes over")
P.post(F + "/exchange-rates/rbz/settings", data={"basis": "bid", "auto_fetch": "1"})
with app.app_context():
    row = fx_engine.rate_on(LAST_PUB)
    check(near(row.rate, rate_for(LAST_PUB)[0]) and row.source == "RBZ interbank bid", "choosing 'bid' switches every date to the bid at once, and says so in the source")
P.post(F + "/exchange-rates/rbz/settings", data={"basis": "mid", "auto_fetch": "1"})
OFFLINE["on"] = True; rbz_rates._INDEX_CACHE.clear(); rbz_rates._MONTH_URLS.clear()
r = P.post(F + "/exchange-rates/rbz/latest", data={}, follow_redirects=True)
check(b"Could not get a rate from the RBZ website" in r.data and r.status_code == 200, "when the website cannot be reached the user is told and nothing breaks")
OFFLINE["on"] = False

# month
reset()
with app.app_context():
    db.session.add(FirmExchangeRate(currency="ZWG", rate_date=date(2026, 9, 3), rate=50.0, source="typed")); db.session.commit()
r = P.post(F + "/exchange-rates/rbz/month", data={"month": "9", "year": "2026"}, follow_redirects=True)
with app.app_context():
    check(FirmRbzReading.query.count() == 22 - 4, "September: 18 days read (the four unusable days left out)")
    check(near(fx_engine.rate_on(date(2026, 9, 3)).rate, 50.0) and FirmRbzReading.query.filter_by(rate_date=date(2026, 9, 3)).count() == 1, "the typed 3 Sep rate still wins, with the RBZ's figures kept beside it")
    check(all(FirmRbzReading.query.filter_by(rate_date=d).count() == 1 for d in (date(2026, 9, 2), date(2026, 9, 9), date(2026, 9, 15))), "2, 9 and 15 Sep are all there")
check(b"No usable RBZ file for" in r.data and b"copy of another day" in r.data, "the page names the days it could not load and why")
r = P.post(F + "/exchange-rates/rbz/month", data={"month": "13", "year": "2026"}, follow_redirects=True)
check(b"not in the future" in r.data, "a bad month is refused")
r = P.post(F + "/exchange-rates/rbz/month", data={"month": "9", "year": "2099"}, follow_redirects=True)
check(b"not in the future" in r.data, "a future month is refused")
r = P.post(F + "/exchange-rates/rbz/month", data={"month": "2", "year": "2024"}, follow_redirects=True)
check(b"Zimbabwe dollars" in r.data, "a month before the ZWG began is refused")

# year-end closing rate
reset()
with app.app_context():
    FirmYearEnd.query.delete(); db.session.commit()
r = P.post(F + "/year-ends/new", data={"period_end": "2026-09-30", "zwg_rate": "", "opening_nil": "1"}, follow_redirects=True)
with app.app_context():
    y = FirmYearEnd.query.first(); y1 = y.id
    FirmExchangeRate.query.delete(); FirmRbzReading.query.delete(); y.zwg_rate = None; db.session.commit()
P.post(F + f"/exchange-rates/rbz/year-end/{y1}", data={"back": "year_end"}, follow_redirects=True)
with app.app_context():
    check(near(FirmYearEnd.query.get(y1).zwg_rate, rate_for(date(2026, 9, 30))[2]), "'Get the closing rate from the RBZ' fetched 30 Sep and put it on the year-end")
    y2 = FirmYearEnd(period_end=date(2026, 8, 31), zwg_rate=None); db.session.add(y2); db.session.commit(); y2id = y2.id
P.post(F + f"/exchange-rates/rbz/year-end/{y2id}", data={}, follow_redirects=True)
with app.app_context():
    check(near(FirmYearEnd.query.get(y2id).zwg_rate, rate_for(date(2026, 8, 28))[2]), "a year-end on a non-publishing day takes the last working day before it (28 Aug)")
    y3 = FirmYearEnd(period_end=date(2099, 12, 31)); db.session.add(y3); db.session.commit(); y3id = y3.id
r = P.post(F + f"/exchange-rates/rbz/year-end/{y3id}", data={}, follow_redirects=True)
check(b"in the future" in r.data, "a future year-end is refused")
r = P.get(F + f"/year-ends/{y1}")
check(r.status_code == 200 and b"Get the closing rate from the RBZ" in r.data, "the year-end page offers the RBZ button")
r = P.post(F + "/exchange-rates/rbz/catch-up", data={}, follow_redirects=True)
check(r.status_code == 200 and b"Catch-up" in r.data, "the 'fill every missing day now' button works")
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
