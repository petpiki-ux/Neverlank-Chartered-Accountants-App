"""Reading the Reserve Bank of Zimbabwe interbank rates from the RBZ website.

The RBZ publishes one PDF per working day, linked from a monthly page (for example .../13-daily-exchange-rates/1644-september-2026).
The addresses are read from that page because they are not regular. The PDF carries a dated table; the USD row reads  "USD 1 1 1.0000 <bid> <ask> <mid>"  where the last three numbers are the ZWG
interbank bid, ask and mid (average) per 1 USD. Weekends and holidays have no file.

Nothing here touches the database: firm_fx.py stores what is read. `http_get` is the only function that uses the network, so
tests replace it."""
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import urljoin, urlsplit

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
SITE = "https://www.rbz.co.zw"
BASE = SITE + "/documents/Exchange_Rates"
LISTING = SITE + "/index.php/research/markets/exchange-rates"      # lists the monthly pages, ten to a page (?start=10, 20 ...)
ZWG_START = date(2024, 4, 5)       # the ZWG began on 5 April 2024; the RBZ's earlier figures are in Zimbabwe dollars, not ZWG
TIMEOUT = (6, 12)          # connect, read (seconds)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NeverlankFirmApp/1.0; +https://www.neverlank.co.zw)"}
WORKERS = 4


def http_get(url):
    """(status code or None, body bytes or b'', error text or ''). Never raises."""
    try:
        import requests
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        return r.status_code, r.content, ""
    except Exception as e:                       # network down, TLS problem, timeout ...
        return None, b"", f"{type(e).__name__}: {str(e)[:160]}"


def pdf_text(body):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MON = "|".join(MONTHS)
_DATE_DMY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MON + r")\s*,?\s+(\d{4})", re.I)       # 3 September 2026
_DATE_MDY = re.compile(r"(" + _MON + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(\d{4})", re.I)       # June 30, 2026


def printed_date(text):
    """The date printed at the top of the PDF (either '3 September 2026' or 'June 30, 2026'), or None."""
    head = "\n".join(text.splitlines()[:12])            # the title block; never a date further down
    for rx, order in ((_DATE_DMY, "dmy"), (_DATE_MDY, "mdy")):
        m = rx.search(head)
        if m:
            d, mon, y = (m.group(1), m.group(2), m.group(3)) if order == "dmy" else (m.group(2), m.group(1), m.group(3))
            try:
                return date(int(y), [x.lower() for x in MONTHS].index(mon.lower()) + 1, int(d))
            except ValueError:
                return None
    return None


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
    return {"date": printed_date(text), "bid": bid, "ask": ask, "mid": mid}


# ---------------------------------------------------------------- finding the files
# The RBZ's links are not regular (a zero-padded day on 2 Jan, a "Mosi-Rates" folder on 22 Jul, a "2024" folder typo on 24 Mar,
# the 18 Feb link pointing at the 17 Feb file), so the addresses are read from the RBZ's own monthly page, never guessed. Guessing
# is only the fallback when the monthly page cannot be found or read.

_MONTH_URLS = {}                 # (year, month) -> monthly page address (they never change once published)
_INDEX_CACHE = {}                # (year, month) -> (fetched at, {day: [pdf urls]}, monthly page url)
INDEX_TTL = 1800


def _text(body):
    return body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else (body or "")


def parse_listing(html):
    """{(year, month): url} for every monthly page linked from a listing page."""
    from bs4 import BeautifulSoup
    out = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        m = re.search(r"/\d+-(" + _MON + r")-(\d{4})(?:[/?#]|$)", a["href"], re.I)
        if m:
            out[(int(m.group(2)), [x.lower() for x in MONTHS].index(m.group(1).lower()) + 1)] = urljoin(SITE + "/", a["href"])
    return out


def find_month_page(year, month, max_pages=20):
    """(url or None, error text). Walks the RBZ's listing pages (ten months to a page) until the month is found."""
    key = (year, month)
    if key in _MONTH_URLS:
        return _MONTH_URLS[key], ""
    err = ""
    for page in range(max_pages):
        url = LISTING if page == 0 else f"{LISTING}?start={page * 10}"
        status, body, e = http_get(url)
        if status != 200:
            err = e or f"HTTP {status} from the RBZ listing"
            break
        found = parse_listing(_text(body))
        _MONTH_URLS.update(found)
        if key in _MONTH_URLS:
            return _MONTH_URLS[key], ""
        if not found:
            break
    return None, err


def parse_month_page(html, year, month, page_url=SITE + "/"):
    """{day: [pdf urls]} from a monthly page. A row whose cell holds no link (days not yet published) gives no entry."""
    from bs4 import BeautifulSoup
    out = {}

    def add(day, href):
        if 1 <= day <= 31 and href:
            url = urljoin(page_url, href.strip())
            if url not in out.setdefault(day, []):
                out[day].append(url)

    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        hrefs = [a["href"] for a in tr.find_all("a", href=True) if urlsplit(a["href"]).path.lower().endswith(".pdf")]
        if not hrefs:
            continue
        day = int(cells[0]) if cells and re.fullmatch(r"\d{1,2}", cells[0]) else None
        for h in hrefs:
            if day is None:
                m = re.search(r"RATES?_0*(\d{1,2})_[A-Za-z]+_\d{4}", h, re.I)
                if m:
                    add(int(m.group(1)), h)
            else:
                add(day, h)
    return out


def month_index(year, month, fresh=False):
    """({day: [urls]}, page url or None, note). Cached for half an hour (the current month's page changes daily)."""
    key = (year, month)
    hit = _INDEX_CACHE.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < INDEX_TTL:
        return hit[1], hit[2], ""
    url, err = find_month_page(year, month)
    if url is None:
        return None, None, err or "the RBZ monthly page was not found"
    status, body, e = http_get(url)
    if status != 200:
        return None, url, e or f"HTTP {status} from the RBZ monthly page"
    idx = parse_month_page(_text(body), year, month, url)
    if not idx:
        return None, url, "the RBZ monthly page lists no files"
    _INDEX_CACHE[key] = (time.monotonic(), idx, url)
    return idx, url, ""


def guessed_urls(day):
    """Addresses built from the usual pattern: only used when the monthly page cannot be read."""
    m = MONTHS[day.month - 1]
    out = []
    for folder in (m, m.upper()):
        for dd in (str(day.day), f"{day.day:02d}"):
            out.append(f"{BASE}/{day.year}/{folder}/RATES_{dd}_{m.upper()}_{day.year}.pdf")
    return out


def fetch_day(day, index=None):
    """Read one day's rate. `index` is month_index(...)[0] when the caller already has it. Returns a dict with status:
       ok          rate, bid, ask, mid, date, url
       none        the RBZ has no file for that day (weekend, public holiday, not yet published)
       unreadable  a file is linked but it cannot be used (message says why: another day's figures, no USD row ...)
       error       the website could not be reached (message)"""
    if day < ZWG_START:
        return {"status": "none", "day": day, "message": "before the ZWG began (5 April 2024): the RBZ's earlier rates are in Zimbabwe dollars"}
    note = ""
    own = index is None
    if own:
        index, _page, note = month_index(day.year, day.month)
    if index is not None:
        urls = list(index.get(day.day, []))
        if not urls and own and time.monotonic() - _INDEX_CACHE.get((day.year, day.month), (0,))[0] > 60:
            index, _page, note = month_index(day.year, day.month, fresh=True)      # the RBZ may have added the day since we last looked
            urls = list((index or {}).get(day.day, []))
        if not urls:
            return {"status": "none", "day": day, "message": "the RBZ lists no file for this day"}
    else:
        urls = guessed_urls(day)
    problem = ""
    for url in urls:
        status, body, err = http_get(url)
        if status is None:
            return {"status": "error", "day": day, "message": err or "could not reach the RBZ website"}
        if status == 404:
            problem = problem or "the RBZ's link for this day leads nowhere (file not found)"
            continue
        if status != 200 or not body:
            problem = f"HTTP {status} for the RBZ file"
            continue
        try:
            rec = parse_text(pdf_text(body))
        except Exception as e:
            problem = f"the RBZ file could not be read ({type(e).__name__})"
            continue
        if rec is None:
            problem = "the USD row was not found in the RBZ file"
            continue
        if rec["date"] is not None and rec["date"] != day:
            problem = f"the RBZ file linked for this day is dated {rec['date'].strftime('%d %b %Y')} (a copy of another day), not {day.strftime('%d %b %Y')}"
            continue
        rec.update({"status": "ok", "day": day, "date": day, "url": url})
        return rec
    if problem:
        return {"status": "unreadable", "day": day, "message": problem}
    return {"status": "none", "day": day, "message": note or "no file"}


def pick(rec, basis):
    return rec.get({"bid": "bid", "ask": "ask"}.get(basis, "mid"))


def working_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def fetch_days(days, budget=40.0):
    """Fetch many days (newest first is the caller's choice), a few at a time, one monthly page per month.
    Returns {"found": [records], "none": [(day, why)], "bad": [(day, why)], "error": text or "", "done": bool, "stopped_at": day or None}."""
    out = {"found": [], "none": [], "bad": [], "error": "", "done": True, "stopped_at": None}
    t0 = time.monotonic()
    days = list(days)
    indexes = {}
    for d in days:
        if d >= ZWG_START and (d.year, d.month) not in indexes:
            indexes[(d.year, d.month)] = month_index(d.year, d.month)
    i = 0
    while i < len(days):
        if time.monotonic() - t0 > budget:
            out["done"], out["stopped_at"] = False, days[i]
            break
        chunk = days[i:i + WORKERS]
        def one(d):
            idx = indexes.get((d.year, d.month))
            return fetch_day(d, idx[0] if idx else None) if idx else fetch_day(d)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            results = list(ex.map(one, chunk))
        for rec in results:
            if rec["status"] == "ok":
                out["found"].append(rec)
            elif rec["status"] == "none":
                out["none"].append((rec["day"], rec.get("message", "")))
            elif rec["status"] == "unreadable":
                out["bad"].append((rec["day"], rec["message"]))
            else:
                out["error"] = rec["message"]
        if out["error"]:
            out["done"], out["stopped_at"] = False, chunk[0]
            break
        i += WORKERS
    return out


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
    """Every working day of the month (to `upto`) that is not in `skip`."""
    end = min(upto, date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    days = [d for d in working_days(date(year, month, 1), end) if d not in skip]
    r = fetch_days(days, budget)
    r["none"] = [d for d, _ in r["none"]]
    return r
