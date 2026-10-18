"""Reading the Reserve Bank of Zimbabwe interbank rates from the RBZ website.

The RBZ publishes one PDF per working day at a fixed address:
    https://www.rbz.co.zw/documents/Exchange_Rates/<year>/<Month>/RATES_<day>_<MONTH>_<year>.pdf
The PDF carries a dated table; the USD row reads  "USD 1 1 1.0000 <bid> <ask> <mid>"  where the last three numbers are the ZWG
interbank bid, ask and mid (average) per 1 USD. Weekends and holidays have no file.

Nothing here touches the database: firm_fx.py stores what is read. `http_get` is the only function that uses the network, so
tests replace it."""
import io
import re
import time
from datetime import date, timedelta

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
BASE = "https://www.rbz.co.zw/documents/Exchange_Rates"
TIMEOUT = (6, 12)          # connect, read (seconds)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NeverlankFirmApp/1.0; +https://www.neverlank.co.zw)"}


def http_get(url):
    """(status code or None, body bytes or b'', error text or ''). Never raises."""
    try:
        import requests
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        return r.status_code, r.content, ""
    except Exception as e:                       # network down, TLS problem, timeout ...
        return None, b"", f"{type(e).__name__}: {str(e)[:160]}"


def pdf_urls(day):
    m = MONTHS[day.month - 1]
    names = []
    for folder in (m, m.upper()):
        names.append(f"{BASE}/{day.year}/{folder}/RATES_{day.day}_{m.upper()}_{day.year}.pdf")
    return names


def pdf_text(body):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", re.I)


def parse_text(text):
    """{"date": date or None, "bid", "ask", "mid"} from the PDF text, or None when the USD row cannot be read with confidence."""
    row = None
    for line in text.splitlines():
        if re.match(r"\s*USD\b", line, re.I):
            nums = [float(n.replace(",", "")) for n in _NUM.findall(line)]
            if len(nums) >= 3:
                row = nums
                break
    if not row:
        return None
    bid, ask, mid = row[-3:]
    # sanity: a rate per 1 USD, bid <= mid <= ask (allow a hair of rounding), and not the "1 1 1.0000" index columns
    if min(bid, ask, mid) <= 1.0 or max(bid, ask, mid) > 1e7 or not (bid - 1e-6 <= mid <= ask + 1e-6) or ask > bid * 1.5:
        return None
    when = None
    m = _DATE.search(text)
    if m:
        try:
            when = date(int(m.group(3)), [x.lower() for x in MONTHS].index(m.group(2).lower()) + 1, int(m.group(1)))
        except ValueError:
            when = None
    return {"date": when, "bid": bid, "ask": ask, "mid": mid}


def fetch_day(day):
    """Read one day's rate. Returns a dict with status:
       ok          rate, bid, ask, mid, date (the date printed in the PDF, else `day`), url
       none        no file published for that day (weekend, holiday, not yet out)
       unreadable  a file exists but the USD row could not be read
       error       the website could not be reached (message)"""
    last_err = ""
    for url in pdf_urls(day):
        status, body, err = http_get(url)
        if status is None:
            return {"status": "error", "day": day, "message": err or "could not reach the RBZ website"}
        if status == 404:
            continue
        if status != 200 or not body:
            last_err = f"HTTP {status}"
            continue
        try:
            rec = parse_text(pdf_text(body))
        except Exception as e:
            return {"status": "unreadable", "day": day, "url": url, "message": f"{type(e).__name__}: {str(e)[:120]}"}
        if rec is None:
            return {"status": "unreadable", "day": day, "url": url, "message": "the USD row was not found in the PDF"}
        if rec["date"] is not None and rec["date"] != day:
            return {"status": "unreadable", "day": day, "url": url, "message": f"the PDF is dated {rec['date'].strftime('%d %b %Y')}, not {day.strftime('%d %b %Y')}"}
        rec.update({"status": "ok", "day": day, "date": day, "url": url})
        return rec
    if last_err:
        return {"status": "error", "day": day, "message": last_err}
    return {"status": "none", "day": day}


def pick(rec, basis):
    return rec.get({"bid": "bid", "ask": "ask"}.get(basis, "mid"))


def fetch_latest(on, max_back=7):
    """The most recent published rate on or before `on` (looks back up to `max_back` days). Returns (record or None, notes)."""
    notes = []
    d = on
    for _ in range(max_back + 1):
        if d.weekday() < 5:
            rec = fetch_day(d)
            if rec["status"] == "ok":
                return rec, notes
            if rec["status"] in ("error", "unreadable"):
                notes.append(f"{d.strftime('%d %b %Y')}: {rec['message']}")
                if rec["status"] == "error":
                    return None, notes
        d -= timedelta(days=1)
    return None, notes or [f"No rate was published in the {max_back + 1} days up to {on.strftime('%d %b %Y')}."]


def fetch_month(year, month, upto, skip=(), budget=40.0):
    """Every working day of the month (to `upto`) that is not in `skip`. Stops when the time budget is spent.
    Returns {"found": [records], "none": [days], "bad": [(day, message)], "done": bool, "stopped_at": day or None}."""
    start = date(year, month, 1)
    end = min(upto, (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)))
    out = {"found": [], "none": [], "bad": [], "done": True, "stopped_at": None}
    t0 = time.monotonic()
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            if time.monotonic() - t0 > budget:
                out["done"], out["stopped_at"] = False, d
                break
            rec = fetch_day(d)
            if rec["status"] == "ok":
                out["found"].append(rec)
            elif rec["status"] == "none":
                out["none"].append(d)
            else:
                out["bad"].append((d, rec["message"]))
                if rec["status"] == "error":
                    out["done"], out["stopped_at"] = False, d
                    break
        d += timedelta(days=1)
    return out
