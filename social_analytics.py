"""Social media analytics, added to the Social blueprint (see the last lines of social.py).

In plain words
* Dashboard (/social/analytics): what was posted, how it did, and what it brought, for the last 30 or 90 days, 12 months or all time. It reads only what the
  app already holds (the numbers typed in or imported on each post, and the sign-ups counted from the tracked links) and says in plain sentences what seems to
  be working. It never invents a figure: where a number is missing it says so.
* Website visits (Google Analytics 4): when GA4_PROPERTY_ID and GA4_SERVICE_ACCOUNT_JSON are set on Render, the number of website visits each post and
  platform sent (matched by the utm tags on the tracked links) is read from Google Analytics and shown next to the other numbers. Only visitors who accepted
  cookies on the website are counted by Google Analytics, so the figure is a floor, not the full traffic.
* Import (/social/analytics/import): upload the free analytics export from LinkedIn, X, Facebook / Instagram (Meta Business Suite), TikTok or YouTube; the
  app finds the post each row belongs to (by its address, or by its wording) and fills in views, likes, comments, shares and clicks after the person has
  looked at the match and confirmed. Nothing is changed before that.
* Connections (/social/analytics/connect): the state of the Google Analytics link, and honest notes on what each platform needs before its own live
  numbers could be pulled in (not built: each needs its own approved developer account).

Settings on Render: GA4_PROPERTY_ID (the number of the Google Analytics property, not the G- tag) and GA4_SERVICE_ACCOUNT_JSON (the whole JSON key of a service
account that has Viewer access to the property; plain JSON or base64 of it).
"""
import base64
import csv
import io
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlsplit

import requests
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from extensions import db
from models import MarketingLead, SocialPost, SocialVersion
from social import PLATFORMS, PLATFORM_ORDER, _need_manage, safe_url, social_bp
from timeutil import to_cat, today_cat

METRICS = ("impressions", "likes", "comments", "shares", "clicks")
PERIODS = (("30", "Last 30 days", 30), ("90", "Last 90 days", 90), ("365", "Last 12 months", 365), ("all", "All time", None))
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_IMPORT_ROWS = 500
MAX_IMPORT_BYTES = 5 * 1024 * 1024


def _env(name):
    return (os.environ.get(name) or "").strip()


def _short_pillar(value):
    return (value or "").split(" (")[0].strip() or "No pillar"


# ------------------------------------------------------------------ Google Analytics 4 (website visits by post and platform)

GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GA_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ga_cache = {}            # days -> (stored at, ok, data or message)
_ga_token = {"token": "", "exp": 0}


def ga_property():
    value = _env("GA4_PROPERTY_ID").replace("properties/", "").strip()
    return value if value.isdigit() else ""


def ga_key():
    """The service account key from the environment, or None when it is missing or not usable."""
    raw = _env("GA4_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    if not raw.lstrip().startswith("{"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        return None
    return data


def ga_configured():
    return bool(ga_property() and ga_key())


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signed_jwt(key, now):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    token_uri = key.get("token_uri") or GA_TOKEN_URL
    if not str(token_uri).startswith("https://oauth2.googleapis.com/"):
        token_uri = GA_TOKEN_URL
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": key["client_email"], "scope": GA_SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600}).encode())
    signing_input = f"{header}.{claims}".encode()
    private_key = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}", token_uri


def _access_token(key):
    now = int(time.time())
    if _ga_token["token"] and _ga_token["exp"] - 120 > now:
        return _ga_token["token"]
    assertion, token_uri = _signed_jwt(key, now)
    r = requests.post(token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError("Google did not accept the service account key (%s)." % r.status_code)
    data = r.json()
    _ga_token["token"], _ga_token["exp"] = data.get("access_token", ""), now + int(data.get("expires_in", 3600))
    if not _ga_token["token"]:
        raise RuntimeError("Google sent no access token.")
    return _ga_token["token"]


def _ga_error(status, payload):
    message = ""
    try:
        message = (payload.get("error") or {}).get("message", "")
    except AttributeError:
        pass
    low = message.lower()
    if status == 403 and ("has not been used" in low or "disabled" in low or "not enabled" in low):
        return "Turn on the \"Google Analytics Data API\" in the Google Cloud project that owns the key (APIs and Services, then Library)."
    if status in (401, 403):
        return "Google Analytics refused access. Add the service account's email as a Viewer under Admin, then Property access management."
    if status == 404 or "not found" in low:
        return "Google Analytics did not find that property. Check GA4_PROPERTY_ID is the property number (Admin, then Property details), not the G- tag."
    return "Google Analytics returned an error (%s). %s" % (status, message[:160])


def _ga_fetch(days):
    key, pid = ga_key(), ga_property()
    try:
        token = _access_token(key)
    except Exception as exc:  # the message is ours, never the key
        return False, str(exc) if isinstance(exc, RuntimeError) else "The service account key could not be used. Check GA4_SERVICE_ACCOUNT_JSON."
    start = "2020-01-01" if days is None else (today_cat() - timedelta(days=days)).isoformat()
    body = {"dateRanges": [{"startDate": start, "endDate": "today"}],
            "dimensions": [{"name": "sessionCampaignName"}, {"name": "sessionSource"}],
            "metrics": [{"name": "sessions"}, {"name": "totalUsers"}],
            "dimensionFilter": {"filter": {"fieldName": "sessionMedium", "stringFilter": {"matchType": "EXACT", "value": "social"}}},
            "limit": 10000}
    try:
        r = requests.post("https://analyticsdata.googleapis.com/v1beta/properties/%s:runReport" % pid, json=body,
                          headers={"Authorization": "Bearer " + token}, timeout=25)
    except requests.RequestException:
        return False, "Could not reach Google Analytics just now. Try again in a minute."
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    if r.status_code != 200:
        return False, _ga_error(r.status_code, payload)
    out = {}
    for row in payload.get("rows", []) or []:
        dims = [d.get("value", "") for d in row.get("dimensionValues", [])]
        vals = [m.get("value", "0") for m in row.get("metricValues", [])]
        if len(dims) < 2 or len(vals) < 2:
            continue
        try:
            sessions, users = int(float(vals[0] or 0)), int(float(vals[1] or 0))
        except ValueError:
            continue
        slot = out.setdefault((dims[0], dims[1].lower()), {"sessions": 0, "users": 0})
        slot["sessions"] += sessions
        slot["users"] += users
    return True, out


def ga_visits(days=90, force=False):
    """(True, {(campaign, platform): {sessions, users}}) or (False, why not). Kept for 15 minutes (a failure for one), never raises."""
    if not ga_configured():
        return False, "Not connected."
    now = time.time()
    hit = _ga_cache.get(days)
    if hit and not force and now - hit[0] < (900 if hit[1] else 60):
        return hit[1], hit[2]
    ok, data = _ga_fetch(days)
    _ga_cache[days] = (now, ok, data)
    return ok, data


# ------------------------------------------------------------------ the report

def _leads_by_post():
    return {(c or "", s or ""): n for c, s, n in db.session.query(MarketingLead.utm_campaign, MarketingLead.utm_source, func.count(MarketingLead.id))
            .filter(MarketingLead.utm_medium == "social").group_by(MarketingLead.utm_campaign, MarketingLead.utm_source).all()}


def _rows(days, visits):
    q = SocialVersion.query.join(SocialPost).filter(SocialVersion.status == "posted")
    if days is not None:
        q = q.filter(SocialVersion.posted_at >= datetime.utcnow() - timedelta(days=days))
    leads = _leads_by_post()
    rows = []
    for v in q.order_by(SocialVersion.posted_at.desc()).all():
        post = v.post
        eng = (v.likes or 0) + (v.comments or 0) + (v.shares or 0)
        slot = (visits or {}).get((post.campaign or "", v.platform)) if visits is not None else None
        rows.append({"v": v, "post": post, "platform": v.platform, "label": PLATFORMS.get(v.platform, {}).get("label", v.platform),
                     "when": to_cat(v.posted_at), "impressions": v.impressions, "clicks": v.clicks, "engagement": eng,
                     "has_numbers": any(getattr(v, f) is not None for f in METRICS), "has_views": bool(v.impressions),
                     "leads": leads.get((post.campaign or "", v.platform), 0), "visits": (slot or {}).get("sessions", 0) if visits is not None else None,
                     "pillar": _short_pillar(post.pillar)})
    return rows


def _group(rows, keyfn):
    out = {}
    for r in rows:
        g = out.setdefault(keyfn(r), {"posts": set(), "versions": 0, "impressions": 0, "engagement": 0, "clicks": 0, "leads": 0, "visits": 0,
                                      "eng_num": 0, "eng_den": 0, "with_numbers": 0})
        g["posts"].add(r["post"].id)
        g["versions"] += 1
        g["impressions"] += r["impressions"] or 0
        g["engagement"] += r["engagement"]
        g["clicks"] += r["clicks"] or 0
        g["leads"] += r["leads"]
        g["visits"] += r["visits"] or 0
        g["with_numbers"] += 1 if r["has_numbers"] else 0
        if r["has_views"]:                       # an engagement rate only from posts whose views are known
            g["eng_num"] += r["engagement"]
            g["eng_den"] += r["impressions"]
    for g in out.values():
        g["post_count"] = len(g["posts"])
        g["rate"] = (100.0 * g["eng_num"] / g["eng_den"]) if g["eng_den"] else None
        g["leads_per_post"] = g["leads"] / g["versions"] if g["versions"] else 0
    return out


def _pct(value, top):
    return int(round(100.0 * value / top)) if top else 0


def _weeks(days, rows):
    """Posts and sign-ups per week (Harare), oldest first, ending with this week."""
    today = today_cat()
    this_monday = today - timedelta(days=today.weekday())
    if days is None:
        first = min([r["when"].date() for r in rows] or [today])
        count = max(4, min(52, (this_monday - (first - timedelta(days=first.weekday()))).days // 7 + 1))
    else:
        count = max(4, min(53, days // 7 + 1))
    starts = [this_monday - timedelta(weeks=i) for i in range(count - 1, -1, -1)]
    index = {d: i for i, d in enumerate(starts)}
    posts = [set() for _ in starts]
    for r in rows:
        d = r["when"].date()
        i = index.get(d - timedelta(days=d.weekday()))
        if i is not None:
            posts[i].add(r["post"].id)
    leads = [0] * len(starts)
    q = MarketingLead.query.filter(MarketingLead.utm_medium == "social", MarketingLead.created_at >= datetime.combine(starts[0], datetime.min.time()) - timedelta(hours=2))
    for lead in q.all():
        d = to_cat(lead.created_at).date()
        i = index.get(d - timedelta(days=d.weekday()))
        if i is not None:
            leads[i] += 1
    top_p, top_l = max([len(p) for p in posts] or [0]), max(leads or [0])
    return [{"label": s.strftime("%d %b"), "posts": len(p), "leads": n, "posts_pct": _pct(len(p), top_p), "leads_pct": _pct(n, top_l)} for s, p, n in zip(starts, posts, leads)]


def _insights(rows, platforms, pillars, weekdays, days, visits_ok):
    """Plain sentences about what the numbers show. Each is a (kind, text): good / look / note."""
    out = []
    n = len(rows)
    if not n:
        return [("note", "Nothing was posted in this period. Mark a post as posted on its page and it will appear here.")]
    missing = [r for r in rows if not r["has_numbers"]]
    if missing:
        out.append(("look", "%d of %d posted items have no results yet (typed in or imported), so views and engagement below are understated. Use Import reports, or type them on each post." % (len(missing), n)))
    if len({r["post"].id for r in rows}) < 10:
        out.append(("note", "Fewer than 10 posts so far: treat the comparisons below as early signs, not as proof."))
    lead_total = sum(g["leads"] for g in platforms.values())
    if lead_total:
        key, g = max(platforms.items(), key=lambda kv: kv[1]["leads"])
        out.append(("good", "%s brought the most sign-ups: %d of %d counted through the tracked links." % (PLATFORMS.get(key, {}).get("label", key), g["leads"], lead_total)))
        pl = {k: g for k, g in pillars.items() if g["leads"]}
        if len(pillars) > 1 and pl:
            k, g = max(pl.items(), key=lambda kv: kv[1]["leads"])
            out.append(("good", "The \"%s\" posts brought the most sign-ups (%d)." % (k, g["leads"])))
    else:
        clicks = sum(r["clicks"] or 0 for r in rows)
        if clicks:
            out.append(("look", "Posts got %s link clicks but no sign-up came through a tracked link. Check that the page the link opens makes the offer clear, and that the post's link is the tracked one." % format(clicks, ",")))
        else:
            out.append(("note", "No sign-up has come through the tracked links in this period. Posts with a link to the Compliance Calendar page let the app count them."))
    rated = {k: g for k, g in platforms.items() if g["rate"] is not None and g["with_numbers"] >= 3}
    if len(rated) > 1:
        ranked = sorted(rated.items(), key=lambda kv: -kv[1]["rate"])
        (k, g), second = ranked[0], ranked[1][1]
        if g["rate"] - second["rate"] >= 0.3 and g["rate"] >= 1.25 * second["rate"]:     # only when the lead is real, not a rounding difference
            out.append(("good", "%s has the best engagement rate: %.1f%% of views led to a like, comment or share (%d posts), against %.1f%% on the next best." % (PLATFORMS.get(k, {}).get("label", k), g["rate"], g["with_numbers"], second["rate"])))
    with_numbers = [r for r in rows if r["has_numbers"]]
    days_used = {k: g for k, g in weekdays.items() if g["with_numbers"] >= 2}
    if len(with_numbers) >= 8 and len(days_used) >= 3:
        k, g = max(days_used.items(), key=lambda kv: kv[1]["engagement"] / kv[1]["with_numbers"])
        out.append(("note", "Posts on %ss did best for likes, comments and shares (an average of %.0f per post). Tentative: it needs more posts to be sure." % (WEEKDAYS[k], g["engagement"] / g["with_numbers"])))
    used = set(platforms)
    idle = [PLATFORMS[k]["label"] for k in ("linkedin", "x", "facebook", "instagram") if k not in used]
    if idle:
        out.append(("note", "Nothing was posted on %s in this period." % ", ".join(idle)))
    stamps = sorted({r["when"].date() for r in rows})
    if days is not None and stamps and (today_cat() - stamps[-1]).days > 14:
        out.append(("look", "The last post was %d days ago. A steady rhythm (one or two a week) tends to matter more than any single post." % (today_cat() - stamps[-1]).days))
    if visits_ok is False:
        out.append(("note", "Website visits are not shown because Google Analytics is not connected (see Connections)."))
    return out


def build_report(days, visits=None):
    rows = _rows(days, visits)
    platforms = _group(rows, lambda r: r["platform"])
    pillars = _group(rows, lambda r: r["pillar"])
    weekdays = _group(rows, lambda r: r["when"].weekday())
    top = {f: max([g[f] for g in platforms.values()] or [0]) for f in ("leads", "engagement", "impressions", "visits")}
    plat_rows = []
    for key in PLATFORM_ORDER:
        g = platforms.get(key)
        if g:
            plat_rows.append(dict(g, key=key, label=PLATFORMS[key]["label"], leads_pct=_pct(g["leads"], top["leads"]), eng_pct=_pct(g["engagement"], top["engagement"]),
                                  views_pct=_pct(g["impressions"], top["impressions"]), visits_pct=_pct(g["visits"], top["visits"])))
    plat_rows.sort(key=lambda g: (-g["leads"], -g["engagement"], g["label"]))
    pillar_rows = sorted(({"label": k, **g} for k, g in pillars.items()), key=lambda g: (-g["leads"], -g["engagement"], g["label"]))
    day_rows = []
    for i, name in enumerate(WEEKDAYS):
        g = weekdays.get(i)
        avg = (g["engagement"] / g["with_numbers"]) if g and g["with_numbers"] else None
        day_rows.append({"label": name, "versions": g["versions"] if g else 0, "avg": avg})
    top_avg = max([d["avg"] for d in day_rows if d["avg"] is not None] or [0])
    for d in day_rows:
        d["pct"] = _pct(d["avg"] or 0, top_avg)
    best = sorted([r for r in rows if r["leads"] or r["engagement"] or r["impressions"]], key=lambda r: (-r["leads"], -r["engagement"], -(r["impressions"] or 0)))[:5]
    totals = {"posts": len({r["post"].id for r in rows}), "versions": len(rows), "impressions": sum(r["impressions"] or 0 for r in rows),
              "engagement": sum(r["engagement"] for r in rows), "clicks": sum(r["clicks"] or 0 for r in rows), "leads": sum(r["leads"] for r in rows),
              "visits": sum(r["visits"] or 0 for r in rows) if visits is not None else None, "missing": sum(1 for r in rows if not r["has_numbers"])}
    eng_den = sum(r["impressions"] for r in rows if r["has_views"])
    totals["rate"] = (100.0 * sum(r["engagement"] for r in rows if r["has_views"]) / eng_den) if eng_den else None
    return {"rows": rows, "totals": totals, "platforms": plat_rows, "pillars": pillar_rows, "days": day_rows, "weeks": _weeks(days, rows), "best": best,
            "insights": _insights(rows, platforms, pillars, weekdays, days, None if visits is not None else False)}


@social_bp.route("/analytics")
@login_required
def analytics():
    _need_manage()
    key = request.args.get("period", "90")
    period = next((p for p in PERIODS if p[0] == key), PERIODS[1])
    visits, ga_note = None, ""
    if ga_configured():
        ok, data = ga_visits(period[2], force=request.args.get("refresh") == "1")
        if ok:
            visits = data
        else:
            ga_note = data
    return render_template("social/analytics.html", rep=build_report(period[2], visits), period=period[0], periods=PERIODS, ga_on=visits is not None,
                           ga_configured=ga_configured(), ga_note=ga_note)


# ------------------------------------------------------------------ importing a platform's own report

_FIELD_RULES = {
    "url": (("tweetpermalink", "permalink", "postlink", "posturl", "postpermalink", "contentlink", "link", "url"), ()),
    "text": (("tweettext", "posttext", "posttitle", "postmessage", "message", "caption", "title", "description", "postcontent", "text", "content"), ()),
    "date": (("time", "date", "createddate", "postdate", "publishtime", "publishedtime", "posttime", "createdat", "datepublished", "publishdate"), ()),
    "impressions": (("impressions", "views", "reach", "videoviews", "totalviews"), ("impressions",)),
    "likes": (("likes", "reactions", "like", "totalreactions"), ("reactions",)),
    "comments": (("comments", "replies", "reply", "totalcomments"), ("comments",)),
    "shares": (("shares", "reposts", "retweets", "shared", "repostsincludingquotes"), ("shares", "reposts", "retweets")),
    "clicks": (("clicks", "linkclicks", "urllinkclicks", "urlclicks", "totalclicks", "clickstotal"), ()),
}


def _norm(header):
    return re.sub(r"[^a-z0-9]", "", str(header or "").lower())


def _map_headers(headers):
    """{field: column index} choosing, for each field, the best-ranked matching column."""
    keys = [_norm(h) for h in headers]
    mapping = {}
    for field, (exact, prefixes) in _FIELD_RULES.items():
        best = None
        for i, k in enumerate(keys):
            if not k or "rate" in k or "percent" in k or k.endswith("ctr"):
                continue
            rank = exact.index(k) if k in exact else (len(exact) + 1 if any(k.startswith(p) for p in prefixes) else None)
            if rank is not None and (best is None or rank < best[0]):
                best = (rank, i)
        if best:
            mapping[field] = best[1]
    return mapping


def _score(mapping):
    has_id = "url" in mapping or "text" in mapping
    metrics = sum(1 for f in METRICS if f in mapping)
    return (metrics * 2 + (2 if has_id else 0)) if (has_id and metrics) else 0


def _to_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text in ("-", "--", "n/a", "N/A"):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number >= 0 else None


def _table_from_rows(raw_rows):
    """Finds the header row among the first 25 rows, then returns (mapping, data rows as lists)."""
    best = (0, None, None)
    for idx, row in enumerate(raw_rows[:25]):
        mapping = _map_headers(row)
        score = _score(mapping)
        if score > best[0]:
            best = (score, idx, mapping)
    if not best[0]:
        return None, []
    return best[2], raw_rows[best[1] + 1:]


def _decode(data):
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("latin-1")


def read_report(filename, data):
    """-> (rows, error). Each row: {url, text, date, impressions, likes, comments, shares, clicks}."""
    name = (filename or "").lower()
    raw_sets = []
    try:
        if name.endswith(".csv") or name.endswith(".txt"):
            text = _decode(data)
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            raw_sets.append([list(r) for r in csv.reader(io.StringIO(text), dialect)])
        elif name.endswith(".xlsx") or name.endswith(".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            for ws in wb.worksheets:
                raw_sets.append([list(r) for r in ws.iter_rows(values_only=True)])
        elif name.endswith(".xls"):
            import xlrd
            wb = xlrd.open_workbook(file_contents=data)
            for ws in wb.sheets():
                raw_sets.append([ws.row_values(i) for i in range(ws.nrows)])
        else:
            return [], "Upload a .csv, .xlsx or .xls file."
    except Exception:
        return [], "That file could not be read. Download the report again from the platform and try once more."
    best = None
    for raw in raw_sets:
        mapping, body = _table_from_rows(raw)
        if mapping and (best is None or _score(mapping) > _score(best[0])):
            best = (mapping, body)
    if not best:
        return [], "I could not find the columns I need (the post's address or wording, and views, likes, comments, shares or clicks). Use the platform's report with one row per post, not the page summary."
    mapping, body = best
    rows = []
    for raw in body:
        cell = lambda f: raw[mapping[f]] if f in mapping and mapping[f] < len(raw) else None
        row = {"url": str(cell("url") or "").strip()[:500], "text": re.sub(r"\s+", " ", str(cell("text") or "")).strip()[:400], "date": str(cell("date") or "").strip()[:30]}
        for f in METRICS:
            row[f] = _to_int(cell(f))
        if not (row["url"] or row["text"]) or all(row[f] is None for f in METRICS):
            continue
        rows.append(row)
        if len(rows) > MAX_IMPORT_ROWS:
            return [], "That file has more than %d posts. Export a shorter date range and import it in parts." % MAX_IMPORT_ROWS
    if not rows:
        return [], "The file has a header but no post rows with numbers in it."
    return rows, ""


_ID_PATTERNS = (r"status(?:es)?/(\d{6,})", r"activity[-:](\d{8,})", r"ugcpost[-:](\d{8,})", r"share[-:](\d{8,})", r"/posts/(\d{6,})", r"story_fbid=(\d{6,})",
                r"/p/([a-z0-9_-]{5,})", r"/reel/([a-z0-9_-]{5,})", r"/video/(\d{6,})", r"[?&]v=([a-z0-9_-]{6,})", r"youtu\.be/([a-z0-9_-]{6,})", r"/shorts/([a-z0-9_-]{6,})")


def url_key(url):
    """A comparison key for a post address: the post's own id where the platform has one, otherwise the address without the www, query and end slash."""
    u = (url or "").strip().lower()
    if not u:
        return ""
    for pattern in _ID_PATTERNS:
        m = re.search(pattern, u)
        if m:
            return "id:" + m.group(1)
    parts = urlsplit(u if "://" in u else "https://" + u)
    return parts.netloc.replace("www.", "").replace("twitter.com", "x.com") + parts.path.rstrip("/")


def _words(text):
    text = re.sub(r"https?://\S+", " ", (text or "").lower()).replace("{link}", " ")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())[:100]


def match_rows(rows, platform):
    """For each row: (version id or None, how: 'address' / 'wording' / ''). Looks only at posts already marked as posted on that platform."""
    versions = SocialVersion.query.filter_by(platform=platform, status="posted").all()
    by_key = defaultdict(list)
    for v in versions:
        if v.post_url:
            by_key[url_key(v.post_url)].append(v)
    prepared = [(v, _words(v.body)) for v in versions]
    out = []
    for row in rows:
        found = by_key.get(url_key(row["url"])) if row["url"] else None
        if found and len(found) == 1:
            out.append((found[0].id, "address"))
            continue
        words = _words(row["text"])
        pick = None
        if len(words) >= 20:
            scored = sorted(((SequenceMatcher(None, words, w).ratio(), v) for v, w in prepared if len(w) >= 20), key=lambda t: -t[0])
            if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
                pick = scored[0][1]
        out.append((pick.id, "wording") if pick else (None, ""))
    return out


def _version_choices(platform):
    out = []
    for v in SocialVersion.query.filter_by(platform=platform, status="posted").order_by(SocialVersion.posted_at.desc()).all():
        out.append({"id": v.id, "title": v.post.title, "when": to_cat(v.posted_at).strftime("%d %b %Y") if v.posted_at else ""})
    return out


@social_bp.route("/analytics/import", methods=["GET", "POST"])
@login_required
def analytics_import():
    _need_manage()
    platform = request.values.get("platform", "linkedin")
    if platform not in PLATFORMS:
        platform = "linkedin"
    step = request.form.get("step", "")
    if request.method == "POST" and step == "preview":
        upload = request.files.get("report")
        if not upload or not upload.filename:
            flash("Choose the report file first.", "danger")
            return redirect(url_for("social.analytics_import", platform=platform))
        data = upload.read(MAX_IMPORT_BYTES + 1)
        if len(data) > MAX_IMPORT_BYTES:
            flash("That file is larger than 5 MB. Export a shorter date range.", "danger")
            return redirect(url_for("social.analytics_import", platform=platform))
        rows, error = read_report(upload.filename, data)
        if error:
            flash(error, "danger")
            return redirect(url_for("social.analytics_import", platform=platform))
        matches = match_rows(rows, platform)
        versions = {v.id: v for v in SocialVersion.query.filter_by(platform=platform, status="posted").all()}
        items = []
        for row, (vid, how) in zip(rows, matches):
            v = versions.get(vid)
            items.append({"row": row, "vid": vid, "how": how, "title": v.post.title if v else "", "current": {f: getattr(v, f) for f in METRICS} if v else {}})
        return render_template("social/analytics_import.html", platform=platform, platforms=PLATFORMS, items=items, choices=_version_choices(platform), metrics=METRICS,
                               matched=sum(1 for i in items if i["vid"]), filename=upload.filename)
    if request.method == "POST" and step == "apply":
        try:
            n = min(int(request.form.get("n", "0")), MAX_IMPORT_ROWS)
        except ValueError:
            n = 0
        versions = {v.id: v for v in SocialVersion.query.filter_by(platform=platform, status="posted").all()}
        done, seen = 0, set()
        for i in range(n):
            try:
                vid = int(request.form.get("vid_%d" % i, "") or 0)
            except ValueError:
                continue
            v = versions.get(vid)
            if not v or vid in seen:
                continue
            values = {f: _to_int(request.form.get("%s_%d" % (f, i))) for f in METRICS}
            if all(x is None for x in values.values()):
                continue
            seen.add(vid)
            for f, x in values.items():
                if x is not None:
                    setattr(v, f, x)
            v.metrics_at, v.metrics_source = datetime.utcnow(), "import"
            addr = safe_url(request.form.get("url_%d" % i))
            if addr and not v.post_url:
                v.post_url = addr
            done += 1
        db.session.commit()
        flash("Updated the results of %d post%s from the %s report." % (done, "" if done == 1 else "s", PLATFORMS[platform]["label"]) if done else "Nothing was updated: no row was matched to a post.", "success" if done else "info")
        return redirect(url_for("social.results"))
    return render_template("social/analytics_import.html", platform=platform, platforms=PLATFORMS, items=None, choices=[], metrics=METRICS, matched=0, filename="")


# ------------------------------------------------------------------ connections

PLATFORM_NOTES = (
    ("linkedin", "LinkedIn", "A LinkedIn developer app approved for the Community Management API, with the Page's administrator signing in once. LinkedIn reviews each request.",
     "Page analytics, Content tab, Export (choose the dates)."),
    ("x", "X (Twitter)", "A paid X developer plan with read access. The free plan does not give post numbers.",
     "analytics.x.com, Tweets tab, Export data (By Tweet)."),
    ("facebook", "Facebook", "A Meta developer app with the Page insights permission, approved by Meta (business verification can be required).",
     "Meta Business Suite, Insights, Content, Export data (CSV)."),
    ("instagram", "Instagram", "The same Meta app, with an Instagram professional account linked to the Facebook Page, and Meta's approval for Instagram insights.",
     "Meta Business Suite, Insights, Content, Export data (CSV)."),
    ("tiktok", "TikTok", "A TikTok for Developers app, approved by TikTok, with the account owner signing in once.",
     "TikTok Studio, Analytics, Content, Download data."),
    ("youtube", "YouTube", "A Google Cloud project with the YouTube Analytics API on, and the channel owner signing in once (Google may ask to verify the app).",
     "YouTube Studio, Analytics, Advanced mode, Export current view."),
)


@social_bp.route("/analytics/connect")
@login_required
def analytics_connect():
    _need_manage()
    key = ga_key()
    return render_template("social/analytics_connect.html", ga_property=ga_property(), ga_email=(key or {}).get("client_email", ""), ga_has_key=bool(key),
                           ga_ready=ga_configured(), notes=PLATFORM_NOTES)


@social_bp.route("/analytics/connect/test", methods=["POST"])
@login_required
def analytics_connect_test():
    _need_manage()
    if not ga_configured():
        flash("Google Analytics is not connected yet. Set both settings on Render first.", "danger")
        return redirect(url_for("social.analytics_connect"))
    ok, data = ga_visits(30, force=True)
    if ok:
        flash("Connected. Google Analytics answered: %d tracked social campaign%s in the last 30 days." % (len(data), "" if len(data) == 1 else "s"), "success")
    else:
        flash(data, "danger")
    return redirect(url_for("social.analytics_connect"))
