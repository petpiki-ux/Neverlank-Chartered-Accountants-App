"""News Watch: scans news sources the firm chooses, keeps what matters to a Zimbabwean tax, audit and business practice, and tells the team.

Sources (Partners and Admin manage them on the sources page):
  - a Google News search (Google publishes an RSS feed for any search; it is unofficial, so it may change),
  - a website's own RSS or Atom feed,
  - a Google Alerts feed,
  - an X (Twitter) account or search, through the paid X API (needs the X_BEARER_TOKEN setting; every post counts as unconfirmed).
Each source belongs to a region (Zimbabwe, South Africa or International); the AI judges International and South African stories by what they mean for a
Zimbabwean practice and its clients, and leaves out market chatter (share-price moves, stock picks, crypto speculation).
Anything else (social media posts, a story somebody spotted) is added by a team member pasting a link and, for social media, the text.

Each refresh: fetch the feeds (safely: public web addresses only, size and time limits), drop duplicates, send what passes a keyword filter to the AI
in small batches to decide relevance, urgency and topic and to write a short summary FROM THE HEADLINE AND SNIPPET ONLY, then show it. A story's full text is
never copied or stored. News is an unconfirmed signal: it is never law and never filed as law (the Library keeps it as a News entry at the lowest legal weight).

How it runs: the page refreshes itself in the background when it is more than a few hours stale, a button refreshes on demand, and a secret-token address
(/news-watch/cron?token=...) lets a Render Cron Job or any timer call it. With no AI key it still lists keyword-matched stories without summaries.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote, urljoin, urlparse

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Client, LegislativeUpdate, NewsItem, NewsSeen, NewsSource, client_scope_ids, user_has_permission

news_bp = Blueprint("news", __name__, url_prefix="/news-watch")

USER_AGENT = "NeverlankNewsWatch/1.0 (+https://app.neverlank.co.zw; practice news monitor)"
KINDS = {"google_news": "Google News search", "feed": "Website feed (RSS / Atom)", "google_alert": "Google Alerts feed",
         "x_account": "X (Twitter) account", "x_search": "X (Twitter) search"}
X_KINDS = ("x_account", "x_search")
REGIONS = ("Zimbabwe", "South Africa", "International")
# The Google News edition used for a region's searches (language, country, edition)
EDITIONS = {"Zimbabwe": ("en-ZW", "ZW", "ZW:en"), "South Africa": ("en-ZA", "ZA", "ZA:en"), "International": ("en-US", "US", "US:en")}
URGENCIES = ("Urgent", "Important", "FYI")
TOPICS = ["International standards (IFRS, ISA, ethics)", "International tax and transfer pricing", "Africa and regional economy", "Global markets and business",
          "Tax: ZIMRA and administration", "Tax: policy and Finance Act", "RBZ, currency and exchange control", "Companies and registry",
          "Audit and accounting standards", "Banking and finance", "Labour and payroll", "Mining", "Agriculture", "Property and construction",
          "Regulation and compliance", "Economy and markets", "Courts and cases", "Other"]
SOCIAL_HOSTS = ("x.com", "twitter.com", "facebook.com", "fb.com", "fb.watch", "linkedin.com", "instagram.com", "t.me", "telegram.me", "tiktok.com",
                "youtube.com", "youtu.be", "whatsapp.com", "threads.net")

BACKGROUND = True              # refresh in a background thread (tests switch this off)
FETCH_TIMEOUT = 15
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
MAX_PER_SOURCE = 40            # newest stories taken from one source per refresh
MAX_AGE_DAYS = 7               # ignore stories older than this the first time they are seen
STALE_HOURS = 6                # opening the page refreshes in the background when the last refresh is older than this
MIN_GAP_MINUTES = 30           # a source is not fetched again sooner than this unless a refresh is forced
AI_BATCH = 15
AI_PER_RUN = 60                # most stories sent to the AI in one refresh; the rest wait for the next
MAX_AI_TRIES = 3
X_MIN_GAP_HOURS = 3            # an X source is never fetched more often than this, even on demand: the X API is paid per post read
X_POSTS_PER_FETCH = 10
X_TIMEOUT = 20
X_API = "https://api.x.com/2"
MIN_SCORE = {"Zimbabwe": 1, "South Africa": 1, "International": 2}   # keyword hits a general feed story needs before the AI reads it
KEEP_DAYS = 120                # stories older than this go (unless pinned or filed)
RECENT_DAYS = 14               # the page shows stories from this many days unless a longer range is chosen

# (name, kind, query): the starter set, added only when a Partner presses the button. Edit freely on the sources page.
STARTER_SOURCES = [
    ("ZIMRA and tax in Zimbabwe", "google_news", '(ZIMRA OR "Zimbabwe Revenue Authority" OR "Zimbabwe tax" OR "Finance Act" OR "Income Tax Act") Zimbabwe'),
    ("Zimbabwe budget and tax policy", "google_news", '(Zimbabwe budget OR "tax measures" OR "VAT" OR "IMTT" OR "withholding tax" OR "capital gains tax") Zimbabwe'),
    ("RBZ, currency and exchange control", "google_news", '("Reserve Bank of Zimbabwe" OR RBZ OR ZiG OR "ZWG" OR "exchange control" OR "monetary policy") Zimbabwe'),
    ("Companies, registry and compliance", "google_news", '("Registrar of Companies" OR "Companies and Other Business Entities Act" OR "beneficial ownership" OR "Zimbabwe Stock Exchange") Zimbabwe'),
    ("Audit and accounting profession", "google_news", '(ICAZ OR PAAB OR "audit" OR "IFRS" OR "chartered accountants") Zimbabwe'),
    ("ZIMRA website", "google_news", "site:zimra.co.zw"),
    ("RBZ website", "google_news", "site:rbz.co.zw"),
]

STARTER_SOUTH_AFRICA = [
    ("SARS and tax in South Africa", "google_news", '("South African Revenue Service" OR SARS OR "Tax Administration Act" OR "Income Tax Act" OR "tax tribunal" OR VAT) "South Africa"'),
    ("IRBA and auditors in South Africa", "google_news", '(IRBA OR "Independent Regulatory Board for Auditors" OR "audit regulator" OR auditor OR "audit firm") "South Africa"'),
    ("SAICA and accounting in South Africa", "google_news", '(SAICA OR "chartered accountants" OR IFRS OR "financial reporting") "South Africa"'),
    ("Reserve Bank, rand and JSE", "google_news", '("South African Reserve Bank" OR SARB OR rand OR "exchange control" OR JSE) "South Africa"'),
    ("Companies Act and CIPC", "google_news", '(CIPC OR "Companies Act" OR "beneficial ownership" OR "Companies Tribunal") "South Africa"'),
    ("South Africa and Zimbabwe trade", "google_news", '("South Africa" AND Zimbabwe) (trade OR border OR tax OR SADC OR migration OR "customs")'),
    ("SARS website", "google_news", "site:sars.gov.za"),
    ("IRBA website", "google_news", "site:irba.co.za"),
    ("SAICA website", "google_news", "site:saica.org.za"),
]

STARTER_INTERNATIONAL = [
    ("Global accounting and audit standards", "google_news", '(IFRS OR IASB OR ISSB OR IAASB OR IESBA OR IFAC OR "auditing standard" OR "accounting standard")'),
    ("Audit regulators and audit quality", "google_news", '(PCAOB OR "Financial Reporting Council" OR "audit quality" OR "audit reform" OR "audit firm fined")'),
    ("Big Four news", "google_news", '(Deloitte OR PwC OR KPMG OR "Ernst & Young") (audit OR tax OR regulator OR fine OR standards)'),
    ("Global tax developments", "google_news", '(OECD OR "Pillar Two" OR BEPS OR "transfer pricing" OR "global minimum tax" OR "digital services tax" OR "tax treaty")'),
    ("Financial crime and sanctions", "google_news", '(FATF OR "grey list" OR "anti-money laundering" OR sanctions) (compliance OR rules OR guidance)'),
    ("Africa business and economy", "google_news", '(Africa OR SADC OR AfCFTA) (economy OR tax OR investment OR "central bank" OR "debt restructuring")'),
    ("Yahoo Finance: audit, accounting and tax", "google_news", 'site:finance.yahoo.com (audit OR accounting OR "tax rules" OR IFRS OR "financial reporting")'),
    ("CFA Institute", "google_news", "site:cfainstitute.org OR site:blogs.cfainstitute.org"),
    ("Accountancy press", "google_news", "site:accountancyage.com OR site:ifac.org OR site:ifrs.org OR site:icaew.com OR site:accountingtoday.com OR site:cfo.com"),
    ("CFA Institute (publications feed)", "feed", "https://rpc.cfainstitute.org/RPC-Publications-Feed"),
    ("Yahoo Finance headlines (feed)", "feed", "https://finance.yahoo.com/news/rssindex"),
]

STARTER_SETS = {"Zimbabwe": STARTER_SOURCES, "South Africa": STARTER_SOUTH_AFRICA, "International": STARTER_INTERNATIONAL}

# Words that make a story worth the AI's time when it comes from a general feed (a targeted Google search skips this filter).
_RELEVANT_TERMS = [
    "zimra", "revenue authority", "tax", "vat", "paye", "imtt", "withholding", "customs", "duty", "duties", "finance act", "income tax", "capital gains",
    "rbz", "reserve bank", "exchange control", "exchange rate", "zwg", "zig", "forex", "foreign currency", "interest rate", "monetary policy", "inflation",
    "treasury", "budget", "statutory instrument", "gazette", "registrar", "companies act", "company law", "beneficial ownership", "cr6", "cr14",
    "audit", "auditor", "icaz", "paab", "ifrs", "accounting standard", "financial reporting", "chartered account", "iaasb", "isa ",
    "nssa", "zimdef", "nec ", "minimum wage", "payroll", "pension", "ipec", "bank", "banking", "microfinance", "insurance", "stock exchange", "zse", "vfex",
    "merger", "acquisition", "takeover", "delist", "liquidation", "insolvency", "judicial management", "sanction", "money laundering", "aml", "fiu",
    "mining", "royalt", "lithium", "gold", "agricultur", "land tenure", "property", "stand", "investment", "zida", "special economic zone", "sez",
    "levy", "licen", "regulation", "compliance", "penalty", "deadline", "amnesty", "voluntary disclosure",
    # South Africa and international
    "sars", "south african revenue", "irba", "saica", "cipc", "sarb", "jse", "iasb", "issb", "ifac", "iesba", "pcaob", "financial reporting council", "frc ",
    "audit quality", "going concern", "big four", "deloitte", "pwc", "kpmg", "ernst & young", "oecd", "pillar two", "beps", "transfer pricing", "minimum tax",
    "tax treaty", "double taxation", "digital services tax", "fatf", "grey list", "anti-money", "cfa institute", "sustainability reporting", "sustainability disclosure",
    "accounting", "financial statements", "tax rules", "tax law", "tax reform", "central bank", "imf", "world bank", "afcfta", "sadc", "debt restructuring",
]


# ------------------------------------------------------------------ safe fetching

def safe_url(url):
    """(True, '') for a public http(s) address the server may fetch; (False, reason) otherwise (private networks, odd ports, other schemes)."""
    try:
        u = urlparse((url or "").strip())
    except ValueError:
        return False, "That is not a valid web address."
    if u.scheme not in ("http", "https") or not u.hostname:
        return False, "The address must start with http:// or https://."
    if u.port not in (None, 80, 443):
        return False, "Only the normal web ports are allowed."
    host = u.hostname
    if host.lower() in ("localhost",) or host.lower().endswith((".local", ".internal", ".localhost")):
        return False, "That address is on a private network."
    try:
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return False, "That address could not be found."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "That address is on a private network."
    return True, ""


def fetch_bytes(url, timeout=FETCH_TIMEOUT, max_bytes=MAX_BYTES):
    """(content bytes, final url, None) or (None, url, error). Follows at most a few redirects, re-checking every hop, and stops reading at max_bytes.
    The one place that talks to a news website (tests replace it)."""
    import requests
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        ok, why = safe_url(current)
        if not ok:
            return None, current, why
        try:
            r = requests.get(current, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5"},
                             timeout=timeout, stream=True, allow_redirects=False)
        except Exception as exc:
            return None, current, ("Could not reach it: " + str(exc))[:200]
        try:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                current = urljoin(current, r.headers["Location"])
                continue
            if r.status_code >= 400:
                return None, current, f"The site answered {r.status_code}."
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    break
            return b"".join(chunks)[:max_bytes], current, None
        finally:
            r.close()
    return None, current, "Too many redirects."


def google_news_url(query, region="Zimbabwe"):
    """The Google News RSS search for `query` on the edition that goes with the region, newest first, last few days only."""
    q = (query or "").strip()
    if "when:" not in q:
        q += " when:3d"
    hl, gl, ceid = EDITIONS.get(region) or EDITIONS["Zimbabwe"]
    return f"https://news.google.com/rss/search?q={quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"


def source_url(source):
    return google_news_url(source.target, getattr(source, "region", None) or "Zimbabwe") if source.kind == "google_news" else source.target


def region_of(source_or_item):
    r = getattr(source_or_item, "region", None)
    return r if r in REGIONS else "Zimbabwe"


# ------------------------------------------------------------------ X (Twitter), through the paid X API

_x_ids = {}                    # handle -> user id, remembered while the server runs (saves a paid lookup each time)


def x_configured():
    return bool(os.environ.get("X_BEARER_TOKEN"))


def clean_handle(value):
    """The bare X username from '@name', 'name' or an x.com / twitter.com address, or '' if it is not one."""
    v = (value or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/(@?[A-Za-z0-9_]{1,15})(?:[/?#].*)?$", v, re.I)
    if m:
        v = m.group(1)
    v = v.lstrip("@")
    return v if re.fullmatch(r"[A-Za-z0-9_]{1,15}", v) else ""


def x_get(path, params):
    """(json dict, None) or (None, error message). The only place that talks to the X API (tests replace it)."""
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return None, "No X API key is set on the server (X_BEARER_TOKEN), so X sources are not read."
    import requests
    try:
        r = requests.get(X_API + path, params=params, headers={"Authorization": "Bearer " + token, "User-Agent": USER_AGENT}, timeout=X_TIMEOUT)
    except Exception as exc:
        return None, ("Could not reach X: " + str(exc))[:200]
    if r.status_code in (401, 403):
        return None, f"X refused the key ({r.status_code}). Check X_BEARER_TOKEN and that the X developer account has API access."
    if r.status_code in (402, 429):
        return None, f"X says the account is out of credits or over its limit ({r.status_code}). Top up the X developer account or wait."
    if r.status_code >= 400:
        return None, f"X answered {r.status_code}."
    try:
        return r.json(), None
    except ValueError:
        return None, "X returned something that could not be read."


def _x_title(text):
    t = re.sub(r"https?://\S+", "", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:137].rstrip() + "...") if len(t) > 140 else (t or "Post on X")


def fetch_x(source):
    """(entries, None) or (None, error): recent posts from an X account or search, in the same shape as parse_feed. Original posts only (no retweets or replies)."""
    start = (datetime.utcnow() - timedelta(days=MAX_AGE_DAYS) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = {"tweet.fields": "created_at,author_id,lang", "max_results": X_POSTS_PER_FETCH, "start_time": start}
    if source.kind == "x_account":
        handle = clean_handle(source.target)
        if not handle:
            return None, "That is not a valid X username."
        uid = _x_ids.get(handle.lower())
        if not uid:
            data, err = x_get("/users/by/username/" + handle, {})
            if err:
                return None, err
            uid = ((data or {}).get("data") or {}).get("id")
            if not uid:
                return None, f"X has no account called @{handle}."
            _x_ids[handle.lower()] = uid
        data, err = x_get(f"/users/{uid}/tweets", dict(fields, exclude="retweets,replies"))
        names = {uid: handle}
    else:
        q = (source.target or "").strip()
        if not q:
            return None, "The search is empty."
        if "is:retweet" not in q:
            q += " -is:retweet"
        if "lang:" not in q:
            q += " lang:en"
        data, err = x_get("/tweets/search/recent", dict(fields, query=q[:500], expansions="author_id", **{"user.fields": "username,name"}))
        names = {u.get("id"): u.get("username") for u in ((data or {}).get("includes") or {}).get("users") or []}
    if err:
        return None, err
    entries = []
    for t in (data or {}).get("data") or []:
        tid, text = t.get("id"), t.get("text") or ""
        who = names.get(t.get("author_id")) or (clean_handle(source.target) if source.kind == "x_account" else "")
        if not tid or not who:
            continue
        entries.append({"title": _x_title(text), "url": f"https://x.com/{who}/status/{tid}", "publisher": f"@{who} (X)", "published": _when(t.get("created_at")),
                        "snippet": re.sub(r"\s+", " ", text).strip()[:1200]})
    return entries, None


# ------------------------------------------------------------------ reading a feed

def _text(el):
    return (el.text or "").strip() if el is not None else ""


def _strip_ns(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _html_text(html):
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        return re.sub(r"\s+", " ", BeautifulSoup(unescape(html), "html.parser").get_text(" ")).strip()
    except Exception:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(html))).strip()


def _when(value):
    """A naive UTC datetime from an RSS (RFC 822) or Atom (ISO 8601) date, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def parse_feed(content, default_publisher="", google=False):
    """[{title, url, publisher, published, snippet}] from RSS or Atom bytes. Raises ValueError for anything that is not a feed. A feed that declares a
    DOCTYPE or entities is refused (the standard XML reader can be tricked by those)."""
    if not content:
        raise ValueError("The feed was empty.")
    if b"<!entity" in content[:50000].lower():                  # entity declarations can be used to blow up the XML reader
        raise ValueError("The feed uses features that are not allowed.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ValueError("That address did not return a news feed (RSS or Atom).")
    items = []
    for el in root.iter():
        name = _strip_ns(el.tag)
        if name not in ("item", "entry"):
            continue
        fields = {}
        link = ""
        for ch in el:
            n = _strip_ns(ch.tag).lower()
            if n == "link":
                link = ch.get("href") or _text(ch) or link
            elif n not in fields:
                fields[n] = ch
        title = _html_text(_text(fields.get("title")))
        if not title or not link:
            continue
        publisher = _html_text(_text(fields.get("source"))) or default_publisher
        if google and not publisher and " - " in title:
            publisher = title.rsplit(" - ", 1)[1].strip()
        if google and " - " in title:
            head_part, tail = title.rsplit(" - ", 1)
            if publisher and tail.strip().lower() == publisher.lower():
                title = head_part.strip()
        raw = _text(fields.get("description")) or _text(fields.get("summary")) or _text(fields.get("encoded")) or _text(fields.get("content"))
        snippet = _html_text(raw)
        if google:                                              # Google's description is a list of the headline and its publisher: no real snippet
            snippet = "" if snippet.lower().startswith(title.lower()[:40]) and len(snippet) < len(title) + 120 else snippet
        published = _when(_text(fields.get("pubdate")) or _text(fields.get("published")) or _text(fields.get("updated")) or _text(fields.get("date")))
        items.append({"title": title[:400], "url": link.strip()[:1000], "publisher": (publisher or "")[:200], "published": published, "snippet": snippet[:1200]})
    if not items and _strip_ns(root.tag) not in ("rss", "feed", "rdf", "RDF"):
        raise ValueError("That address did not return a news feed (RSS or Atom).")
    return items


def fingerprint(title):
    words = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower()).split()
    return hashlib.sha1(" ".join(words[:14]).encode()).hexdigest()[:40]


def prefilter_score(title, snippet=""):
    text = " " + (title + " " + (snippet or "")).lower() + " "
    return sum(1 for t in _RELEVANT_TERMS if t in text)


def is_social(url):
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)


# ------------------------------------------------------------------ the AI

TRIAGE_TOOL = {
    "name": "record_news_triage",
    "description": "Return, for every story supplied, whether it matters to the firm and a short summary.",
    "input_schema": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"},
            "relevant": {"type": "boolean"},
            "urgency": {"type": "string", "enum": list(URGENCIES)},
            "topics": {"type": "array", "items": {"type": "string", "enum": TOPICS}},
            "summary": {"type": "string", "description": "One to three sentences, from the headline and snippet only. Empty if not relevant."},
            "why_it_matters": {"type": "string", "description": "One sentence on what it means for a Zimbabwean tax, audit and advisory practice and its clients. Empty if not relevant."},
            "sectors": {"type": "array", "items": {"type": "string"}, "description": "Client sectors affected, e.g. mining, agriculture, banking, retail, property, manufacturing, telecoms."},
            "confidence": {"type": "string", "enum": ["High", "Medium", "Low"], "description": "How well the headline and snippet support the summary. Low when only a headline was given."},
        }, "required": ["id", "relevant", "urgency", "topics", "summary", "why_it_matters", "confidence"]}}},
        "required": ["items"],
    },
}

TRIAGE_SYSTEM = (
    "You are the news analyst for Neverlank Chartered Accountants, a Zimbabwean firm of chartered accountants, auditors and tax practitioners. "
    "You are given a numbered list of news items (headline, publisher, date and sometimes a short snippet). For each, decide whether the team should "
    "know about it, and summarise it for them.\n\n"
    "Each item carries a REGION: Zimbabwe, South Africa or International. Zimbabwean items are judged as below. South African and international items are "
    "relevant only if they could change what a Zimbabwean firm of chartered accountants, auditors and tax practitioners does or advises: a new or amended "
    "accounting, auditing, ethics or sustainability standard (IFRS, ISA, IESBA, ISSB); an audit regulator's action or an audit-quality finding; a tax, transfer "
    "pricing or anti-money-laundering rule that Zimbabwe may follow or that touches cross-border clients; South African rules (SARS, IRBA, SAICA, CIPC, Reserve "
    "Bank) that affect clients trading with or owning businesses in South Africa; or a regional or global economic event that moves Zimbabwean business (currency, "
    "commodity, trade, sanctions, debt). NOT relevant from abroad: share-price moves, stock tips, individual company earnings, crypto speculation, personal "
    "finance advice, market commentary, celebrity or political stories, and press-release marketing. International items are rarely Urgent: use Urgent only when "
    "something takes effect now and clients must act. In why_it_matters say what it means for Zimbabwe or the firm's clients.\n\n"
    "RELEVANT means it bears on: tax (ZIMRA, the Finance Act, VAT, PAYE, IMTT, withholding tax, capital gains, customs); the Reserve Bank, currency and "
    "exchange control; companies law and the Registrar; audit, accounting and reporting standards and the profession (ICAZ, PAAB, IFRS); banking, "
    "insurance and capital markets; labour, payroll and pension rules; sector rules for mining, agriculture, property and other client sectors; "
    "sanctions and money laundering; or a major business event (merger, listing, liquidation) that could affect clients. NOT relevant: sport, "
    "entertainment, party politics with no business effect, ordinary crime, foreign stories with no Zimbabwean effect, and adverts.\n\n"
    "URGENCY: Urgent = a new tax or regulatory measure, notice or deadline that takes effect now or within weeks and changes what clients must do. "
    "Important = a significant development the team should know this week. FYI = background.\n\n"
    "RULES: use ONLY the headline and snippet given. Never add a fact, figure, date, section or name that is not in them. If you were given only a headline, "
    "say so in the summary ('Headline only: ...') and set confidence Low. Never present news as law or as confirmed: say 'reported' where it is a report. "
    "If an item says it was shared from social media or by a person, or is a post on X, treat it as unconfirmed and say 'posted on X' or 'reported'. Keep the summary to one to three plain sentences. "
    "Return one entry for every id you were given."
)


def is_configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _model():
    return os.environ.get("NEWS_WATCH_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"


def _call_triage(system, user_message, tool=TRIAGE_TOOL, max_tokens=6000):
    """(tool input dict, None) or (None, error). The only place the AI is called (tests replace it)."""
    import anthropic
    default = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
    last = None
    for model in dict.fromkeys([_model(), default]):
        try:
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=120)
            resp = client.messages.create(model=model, max_tokens=max_tokens, system=system, tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
                                          messages=[{"role": "user", "content": user_message}])
        except Exception as exc:
            last = exc
            t = str(exc).lower()
            if "model" in t and ("not_found" in t or "not found" in t or "does not exist" in t):
                continue
            break
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool["name"]:
                data = block.input
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        data = {}
                return (data if isinstance(data, dict) else {}), None
        return None, "The AI did not return a structured answer."
    try:
        import legislation_search
        return None, legislation_search.friendly_error(last)
    except Exception:
        return None, str(last)[:300]


def _item_block(item):
    when = item.published_at.strftime("%d %b %Y") if item.published_at else "date unknown"
    extra = " [SHARED BY A TEAM MEMBER / SOCIAL MEDIA / X POST - UNCONFIRMED]" if item.unconfirmed else ""
    return f"[{item.id}]{extra} {item.title}\nRegion: {region_of(item)} | Publisher: {item.publisher or 'unknown'} | {when}\nSnippet: {item.snippet or '(none - headline only)'}"


def _apply_ai(item, entry):
    """Store the AI's verdict on one item."""
    relevant = bool(entry.get("relevant"))
    item.status = "relevant" if relevant else "irrelevant"
    item.ai_used = True
    item.ai_error = None
    if relevant:
        item.urgency = entry.get("urgency") if entry.get("urgency") in URGENCIES else "FYI"
        item.topics_json = json.dumps([t for t in (entry.get("topics") or []) if t in TOPICS][:4])
        item.summary = str(entry.get("summary") or "").strip()[:900] or None
        item.why = str(entry.get("why_it_matters") or "").strip()[:500] or None
        item.sectors_json = json.dumps([re.sub(r"\s+", " ", str(s)).strip().lower()[:40] for s in (entry.get("sectors") or []) if str(s).strip()][:6])
        item.confidence = entry.get("confidence") if entry.get("confidence") in ("High", "Medium", "Low") else "Low"


def triage_pending(limit=AI_PER_RUN):
    """Ask the AI about stories still waiting (newest first), in small batches. Returns (done, failed). Without an AI key, stories that passed the keyword
    filter are shown as FYI with no summary. Never raises."""
    region_rank = db.case((NewsItem.region == "International", 2), (NewsItem.region == "South Africa", 1), else_=0)         # Zimbabwean stories are read first
    pending = NewsItem.query.filter(NewsItem.status == "new", NewsItem.ai_tries < MAX_AI_TRIES).order_by(region_rank, NewsItem.published_at.desc().nullslast(), NewsItem.id.desc()).limit(limit).all()
    if not pending:
        return 0, 0
    if not is_configured():
        for it in pending:
            it.status = "relevant"
            it.urgency = "FYI"
            it.ai_used = False
            it.summary = (it.snippet or "")[:300] or None
            it.confidence = "Low"
        db.session.commit()
        return len(pending), 0
    done = failed = 0
    for i in range(0, len(pending), AI_BATCH):
        batch = pending[i:i + AI_BATCH]
        message = "NEWS ITEMS\n\n" + "\n\n".join(_item_block(it) for it in batch) + f"\n\nToday's date: {datetime.utcnow():%d %B %Y}. Triage every item now."
        data, err = _call_triage(TRIAGE_SYSTEM, message)
        by_id = {}
        for e in (data or {}).get("items") or []:
            try:
                by_id[int(e.get("id"))] = e
            except (TypeError, ValueError, AttributeError):
                continue
        for it in batch:
            it.ai_tries = (it.ai_tries or 0) + 1
            if it.id in by_id:
                _apply_ai(it, by_id[it.id])
                done += 1
            else:
                it.ai_error = (err or "The AI left this one out.")[:300]
                failed += 1
                if it.ai_tries >= MAX_AI_TRIES:                      # give up quietly: show it as unread background rather than lose it
                    it.status = "relevant"
                    it.urgency = "FYI"
                    it.summary = (it.snippet or "")[:300] or None
                    it.confidence = "Low"
        db.session.commit()
    return done, failed


# ------------------------------------------------------------------ refreshing

_lock = threading.Lock()
_state = {"running": False, "started": None, "finished": None, "message": ""}


def refresh_state():
    return dict(_state)


def _fresh_enough(source, force):
    if source.last_fetched_at is None:
        return False
    age = datetime.utcnow() - source.last_fetched_at
    if source.kind in X_KINDS:                          # paid per post: never more often than the gap, forced or not
        return age < timedelta(hours=X_MIN_GAP_HOURS)
    return (not force) and age < timedelta(minutes=MIN_GAP_MINUTES)


def fetch_source(source):
    """Fetch one source and store its new stories. Returns (new_count, error or None). Never raises."""
    source.last_fetched_at = datetime.utcnow()
    is_x = source.kind in X_KINDS
    if is_x:
        entries, err = fetch_x(source)
        if err:
            source.last_status = ("Failed: " + err)[:300]
            source.last_new_count = 0
            return 0, err
    else:
        content, _final, err = fetch_bytes(source_url(source))
        if err:
            source.last_status = ("Failed: " + err)[:300]
            source.last_new_count = 0
            return 0, err
        try:
            entries = parse_feed(content, default_publisher=source.name if source.kind != "google_news" else "", google=(source.kind == "google_news"))
        except ValueError as exc:
            source.last_status = ("Failed: " + str(exc))[:300]
            source.last_new_count = 0
            return 0, str(exc)
    region = region_of(source)
    threshold = MIN_SCORE.get(region, 1) if source.kind in ("feed", "x_search") else 1
    cutoff = datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)
    entries.sort(key=lambda e: e["published"] or datetime.utcnow(), reverse=True)
    new = 0
    for e in entries[:MAX_PER_SOURCE]:
        if e["published"] and e["published"] < cutoff:
            continue
        fp = fingerprint(e["title"])
        if NewsItem.query.filter(db.or_(NewsItem.fingerprint == fp, NewsItem.url == e["url"])).first() is not None:
            continue
        # a targeted Google search needs no keyword filter; a general feed or an X search does (International needs more hits); a chosen X account needs one
        skip = (source.kind in ("feed", "x_search", "x_account")) and prefilter_score(e["title"], e["snippet"]) < threshold
        db.session.add(NewsItem(source_id=source.id, title=e["title"], url=e["url"], publisher=e["publisher"], published_at=e["published"], snippet=e["snippet"],
                                region=region, unconfirmed=is_x, fingerprint=fp, status="skipped" if skip else "new", fetched_at=datetime.utcnow()))
        new += 1
    source.last_new_count = new
    source.last_status = f"OK: {len(entries)} found, {new} new"
    return new, None


def refresh_all(force=False):
    """Fetch every active source, then let the AI read the new stories. Returns a one-line message. Safe to call from a thread with an app context."""
    if not _lock.acquire(blocking=False):
        return "A refresh is already running."
    _state.update(running=True, started=datetime.utcnow(), message="")
    try:
        total_new = errors = 0
        for source in NewsSource.query.filter_by(active=True).order_by(NewsSource.id).all():
            if _fresh_enough(source, force):
                continue
            try:
                n, err = fetch_source(source)
                db.session.commit()
            except Exception as exc:                          # one bad source never stops the others
                db.session.rollback()
                source = NewsSource.query.get(source.id)
                if source is not None:
                    source.last_status = ("Failed: " + str(exc))[:300]
                    db.session.commit()
                n, err = 0, str(exc)
            total_new += n
            errors += 1 if err else 0
        done, failed = triage_pending()
        _housekeeping()
        msg = f"{total_new} new stor{'y' if total_new == 1 else 'ies'} found, {done} read by the AI" + (f", {errors} source(s) failed" if errors else "") + "."
        _state["message"] = msg
        return msg
    except Exception as exc:
        db.session.rollback()
        _state["message"] = "The refresh failed: " + str(exc)[:200]
        return _state["message"]
    finally:
        _state.update(running=False, finished=datetime.utcnow())
        _lock.release()


def _housekeeping():
    cutoff = datetime.utcnow() - timedelta(days=KEEP_DAYS)
    NewsItem.query.filter(NewsItem.fetched_at < cutoff, NewsItem.pinned.isnot(True), NewsItem.filed_update_id.is_(None)).delete(synchronize_session=False)
    db.session.commit()


def start_refresh(force=False):
    """Refresh in the background (or now, in tests). Returns True if one was started."""
    if _state["running"]:
        return False
    if not BACKGROUND:
        refresh_all(force)
        return True
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            try:
                refresh_all(force)
            finally:
                db.session.remove()
    threading.Thread(target=run, daemon=True).start()
    return True


def last_refresh_at():
    row = db.session.query(db.func.max(NewsSource.last_fetched_at)).scalar()
    return row


def needs_refresh():
    if _state["running"] or NewsSource.query.filter_by(active=True).count() == 0:
        return False
    last = last_refresh_at()
    return last is None or (datetime.utcnow() - last) > timedelta(hours=STALE_HOURS)


# ------------------------------------------------------------------ what the page shows

def unread_count(user):
    """Urgent and Important stories found since the person last opened News Watch (or the last three days), for the menu badge. Never raises."""
    try:
        seen = NewsSeen.query.get(user.id)
        since = seen.seen_at if seen is not None else datetime.utcnow() - timedelta(days=3)
        return min(99, NewsItem.query.filter(NewsItem.status == "relevant", NewsItem.urgency.in_(("Urgent", "Important")), NewsItem.fetched_at > since).count())
    except Exception:
        db.session.rollback()
        return 0


def _norm_name(name):
    n = re.sub(r"\((?:pvt|private|pty)[^)]*\)|\b(?:private|pvt|limited|ltd|holdings|group|proprietary|zimbabwe|zim|trust|investments|enterprises)\b", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", n).split()


def related_clients(items, user, cap=6):
    """{item id: [(client, reason)]}: clients the person may see that a story names, or whose sector it touches. Names need two words or a distinctive one."""
    scope = client_scope_ids(user)
    q = Client.query
    if scope is not None:
        q = q.filter(Client.id.in_(scope)) if scope else q.filter(db.false())
    clients = q.all()
    prepared = []
    for c in clients:
        words = _norm_name(c.name)
        phrase = " ".join(words)
        prepared.append((c, phrase if len(phrase) >= 6 else "", (c.industry or "").strip().lower()))
    out = {}
    for it in items:
        hay = " " + re.sub(r"[^a-z0-9 ]+", " ", (it.title + " " + (it.snippet or "")).lower()) + " "
        hits = []
        sectors = set(it.sectors)
        for c, phrase, industry in prepared:
            if phrase and (" " + phrase + " ") in hay:
                hits.append((c, "named in the story"))
            elif industry and sectors and any(s and (s in industry or industry in s) for s in sectors):
                hits.append((c, "same sector"))
        hits.sort(key=lambda h: 0 if h[1] == "named in the story" else 1)
        if hits:
            out[it.id] = hits[:cap]
    return out


def can_manage(user):
    return getattr(user, "role", None) in ("admin", "partner")


def _query_items(args):
    """The stories for the page, filtered by the query string."""
    days = args.get("days", type=int) or RECENT_DAYS
    q = NewsItem.query
    show = args.get("show", "relevant")
    if show == "hidden":
        q = q.filter(NewsItem.status.in_(("irrelevant", "skipped")))
    else:
        q = q.filter(NewsItem.status == "relevant")
    if args.get("urgency") in URGENCIES:
        q = q.filter(NewsItem.urgency == args["urgency"])
    if args.get("topic") in TOPICS:
        q = q.filter(NewsItem.topics_json.like('%"' + args["topic"].replace('"', "") + '"%'))
    if args.get("region") in REGIONS:
        q = q.filter(db.func.coalesce(NewsItem.region, "Zimbabwe") == args["region"])
    if args.get("pinned"):
        q = q.filter(NewsItem.pinned.is_(True))
    if args.get("unconfirmed"):
        q = q.filter(NewsItem.unconfirmed.is_(True))
    term = (args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(db.or_(NewsItem.title.ilike(like), NewsItem.summary.ilike(like), NewsItem.snippet.ilike(like), NewsItem.publisher.ilike(like)))
    q = q.filter(db.or_(NewsItem.pinned.is_(True), db.func.coalesce(NewsItem.published_at, NewsItem.fetched_at) >= datetime.utcnow() - timedelta(days=days)))
    when = db.func.coalesce(NewsItem.published_at, NewsItem.fetched_at)         # a story with no publish date counts from when it was found
    items = q.order_by(when.desc(), NewsItem.id.desc()).limit(400).all()          # newest first, whatever its urgency or pin
    if args.get("sort") == "urgency":
        rank = {"Urgent": 0, "Important": 1, "FYI": 2}
        items.sort(key=lambda i: (0 if i.pinned else 1, rank.get(i.urgency, 3)))    # stable sort keeps newest first inside each group
    return items


# ------------------------------------------------------------------ pages

@news_bp.route("/")
@login_required
def index():
    started = False
    if needs_refresh():
        started = start_refresh(False)
    items = _query_items(request.args)
    today = {u: NewsItem.query.filter(NewsItem.status == "relevant", NewsItem.urgency == u, NewsItem.fetched_at > datetime.utcnow() - timedelta(days=3)).count() for u in URGENCIES}
    new_since = None
    seen = NewsSeen.query.get(current_user.id)
    if seen is not None:
        new_since = seen.seen_at
    related = related_clients(items[:150], current_user) if request.args.get("show", "relevant") != "hidden" else {}
    page = render_template("newswatch/index.html", items=items, related=related, today=today, days=(request.args.get("days", type=int) or RECENT_DAYS), topics=TOPICS, urgencies=URGENCIES, regions=REGIONS, args=request.args,
                           state=refresh_state(), configured=is_configured(), can_manage=can_manage(current_user), new_since=new_since,
                           n_sources=NewsSource.query.filter_by(active=True).count(), last=last_refresh_at(), refreshing=_state["running"] or started,
                           can_file=user_has_permission(current_user, "manage_legislative_updates"), social_hosts=SOCIAL_HOSTS, can_publish=can_manage(current_user))
    if request.args.get("show", "relevant") != "hidden":
        if seen is None:
            db.session.add(NewsSeen(user_id=current_user.id, seen_at=datetime.utcnow()))
        else:
            seen.seen_at = datetime.utcnow()
        db.session.commit()
    return page


@news_bp.route("/refresh", methods=["POST"])
@login_required
def refresh():
    last = last_refresh_at()
    if not can_manage(current_user) and last is not None and (datetime.utcnow() - last) < timedelta(minutes=10):
        flash("News was refreshed a few minutes ago. Try again shortly.", "info")
    elif start_refresh(True):
        flash("Looking for news now. This takes a minute or two; reload the page to see it.", "info")
    else:
        flash("A refresh is already running.", "info")
    return redirect(url_for("news.index"))


@news_bp.route("/cron", methods=["GET", "POST"])
def cron():
    """For a Render Cron Job or any timer: /news-watch/cron?token=THE_SECRET. Needs the NEWS_WATCH_TOKEN environment variable to be set on the server."""
    secret = os.environ.get("NEWS_WATCH_TOKEN") or ""
    given = request.values.get("token") or ""
    if not secret or not hmac.compare_digest(secret.encode(), given.encode()):
        abort(404)
    if _state["running"]:
        return jsonify(ok=True, message="A refresh is already running.")
    return jsonify(ok=True, message=refresh_all(False))


@news_bp.route("/<int:item_id>/hide", methods=["POST"])
@login_required
def hide(item_id):
    item = NewsItem.query.get_or_404(item_id)
    item.status = "relevant" if item.status in ("irrelevant", "skipped") else "irrelevant"
    if item.status == "relevant" and not item.urgency:
        item.urgency = "FYI"
    db.session.commit()
    flash("Marked as not relevant. It is under 'Hidden' if you want it back." if item.status == "irrelevant" else "Brought back.", "info")
    return redirect(request.referrer or url_for("news.index"))


@news_bp.route("/<int:item_id>/pin", methods=["POST"])
@login_required
def pin(item_id):
    item = NewsItem.query.get_or_404(item_id)
    item.pinned = not item.pinned
    db.session.commit()
    return redirect(request.referrer or url_for("news.index"))


@news_bp.route("/add-link", methods=["POST"])
@login_required
def add_link():
    """A team member adds a story or a social media post by link (and, for social media, the text, since those pages need a login)."""
    url = (request.form.get("url") or "").strip()
    pasted = re.sub(r"\s+", " ", request.form.get("text") or "").strip()[:2500]
    ok, why = safe_url(url)
    if not ok:
        flash(why, "danger")
        return redirect(url_for("news.index"))
    social = is_social(url)
    title, snippet, publisher = "", pasted, (urlparse(url).hostname or "").replace("www.", "")
    if not pasted:
        if social:
            flash("Social media pages cannot be read automatically. Paste the text of the post in the box as well.", "danger")
            return redirect(url_for("news.index"))
        content, _final, err = fetch_bytes(url, max_bytes=600_000)
        if err:
            flash("Could not read that page (" + err + "). Paste the text of the story in the box as well.", "danger")
            return redirect(url_for("news.index"))
        title, snippet = _page_summary(content)
    if not title:
        title = (pasted[:120] + ("..." if len(pasted) > 120 else "")) if pasted else (publisher or url)[:120]
    if NewsItem.query.filter(NewsItem.url == url[:1000]).first() is not None:
        flash("That link is already in News Watch.", "info")
        return redirect(url_for("news.index"))
    region = request.form.get("region") if request.form.get("region") in REGIONS else "Zimbabwe"
    item = NewsItem(title=title[:400], url=url[:1000], publisher=publisher[:200], published_at=datetime.utcnow(), snippet=snippet[:1200], fingerprint=fingerprint(title), region=region,
                    status="new", unconfirmed=bool(social or pasted), shared_by_id=current_user.id, fetched_at=datetime.utcnow())
    db.session.add(item)
    db.session.commit()
    if is_configured():
        data, err = _call_triage(TRIAGE_SYSTEM, "NEWS ITEMS\n\n" + _item_block(item) + "\n\nTriage this item now.")
        entry = next((e for e in (data or {}).get("items") or [] if isinstance(e, dict) and str(e.get("id")) == str(item.id)), None)
        if entry:
            _apply_ai(item, entry)
            if not entry.get("relevant"):
                item.status = "relevant"                      # a person chose to add it, so it stays visible, flagged FYI
                item.urgency = "FYI"
                item.summary = item.summary or "The AI did not think this bears on the firm's work, but you added it."
        else:
            item.status, item.urgency, item.summary, item.ai_error = "relevant", "FYI", snippet[:300], (err or "")[:300]
    else:
        item.status, item.urgency, item.summary = "relevant", "FYI", snippet[:300]
    item.ai_tries = 1
    db.session.commit()
    flash("Added to News Watch" + (" as unconfirmed (it came from social media or text you pasted)." if item.unconfirmed else "."), "success")
    return redirect(url_for("news.index"))


def _page_summary(content):
    """(title, a short text) from an HTML page's own title and description, no more."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        meta = lambda **kw: (soup.find("meta", attrs=kw) or {}).get("content", "")
        title = (meta(property="og:title") or (soup.title.string if soup.title and soup.title.string else "")).strip()
        desc = (meta(property="og:description") or meta(name="description")).strip()
        return re.sub(r"\s+", " ", title)[:300], re.sub(r"\s+", " ", desc)[:1200]
    except Exception:
        return "", ""


@news_bp.route("/<int:item_id>/file", methods=["POST"])
@login_required
def file_to_library(item_id):
    """Keep a story in the Firm Library as a News entry (the lowest legal weight, flagged as a report to confirm against the instrument)."""
    if not user_has_permission(current_user, "manage_legislative_updates"):
        abort(403)
    item = NewsItem.query.get_or_404(item_id)
    if item.filed_update_id:
        flash("Already in the Firm Library.", "info")
        return redirect(request.referrer or url_for("news.index"))
    import legislation_chunking
    update = LegislativeUpdate(title=item.title[:200], instrument_type="News", summary=item.summary or item.snippet or "", source_reference=(item.publisher or "")[:200],
                               created_by_id=current_user.id, article_url=item.url[:500])
    db.session.add(update)
    db.session.flush()
    text = None
    if "news.google." not in (urlparse(item.url).hostname or "") and not is_social(item.url):
        try:
            import tax
            ok, _why = safe_url(item.url)
            if ok:
                text, _err = tax._fetch_and_extract_news_article(item.url)
        except Exception:
            text = None
    if text:
        import legislation_summary
        update.extracted_text, update.extraction_status = text, "extracted"
        result, status, error = legislation_summary.summarize_news_update(text, item.url)
        update.ai_status, update.ai_error, update.ai_processed_at = status, error, datetime.utcnow()
        if status == "done":
            update.ai_summary = result["summary"]
            update.set_ai_key_changes(result["key_changes"])
            update.set_ai_suggested_areas(result["suggested_areas"])
    else:                                                      # a Google link or a social post cannot be read by the app: keep what News Watch holds
        update.extracted_text = f"{item.title}\n\n{item.snippet or ''}\n\n(Summary from News Watch: {item.summary or 'none'})".strip()
        update.extraction_status = "extracted"
        update.ai_status, update.ai_summary, update.ai_processed_at = "done", item.summary, datetime.utcnow()
        if item.why:
            update.set_ai_key_changes([item.why])
    legislation_chunking.ensure_chunks(update)
    item.filed_update_id = update.id
    db.session.commit()
    flash("Filed in the Firm Library as a News entry. It is a report, not law: check the instrument itself before relying on it.", "success")
    return redirect(request.referrer or url_for("news.index"))


# ---- publish an approved story to the firm's website (Partners and Admin only; see news_site.py)

@news_bp.route("/<int:item_id>/publish", methods=["GET", "POST"])
@login_required
def publish_to_site(item_id):
    if not can_manage(current_user):
        abort(403)
    import news_site
    item = NewsItem.query.get_or_404(item_id)
    reason = news_site.blocked_reason(item)
    form = {"title": request.form.get("title", item.title or "").strip(), "note": request.form.get("note", news_site.default_note(item)).strip(),
            "ticked": bool(request.form.get("checked")), "draft": request.form.get("mode") == "draft"}
    problems = []
    if request.method == "POST" and not reason:
        problems = news_site.check_text(form["title"], form["note"], form["ticked"])
        if not news_site.configured():
            problems.append("The website connection is not set up yet: add WP_USERNAME and WP_APP_PASSWORD on Render.")
        if not problems:
            ok, data = news_site.publish(item, form["title"], form["note"], draft=form["draft"])
            if ok:
                item.wp_post_id = data.get("id") or item.wp_post_id
                item.wp_url = (data.get("link") or "")[:500]
                item.wp_status = "draft" if form["draft"] else "publish"
                item.wp_posted_at, item.wp_posted_by_id = datetime.utcnow(), current_user.id
                db.session.commit()
                flash("Saved as a draft on the website." if form["draft"] else "Published on the website.", "success")
                return redirect(url_for("news.index") + f"#n{item.id}")
            problems.append(data)
    return render_template("newswatch/publish.html", it=item, form=form, problems=problems, blocked=reason, configured=news_site.configured(),
                           category=news_site.settings()["category"], limits=(news_site.NOTE_MIN, news_site.NOTE_MAX), disclaimer=news_site.DISCLAIMER)


@news_bp.route("/<int:item_id>/unpublish", methods=["POST"])
@login_required
def unpublish_from_site(item_id):
    if not can_manage(current_user):
        abort(403)
    import news_site
    item = NewsItem.query.get_or_404(item_id)
    ok, data = news_site.take_down(item)
    if ok:
        item.wp_status = "draft"
        db.session.commit()
        flash("Taken off the website (kept there as a draft).", "success")
    else:
        flash(data, "danger")
    return redirect(request.referrer or url_for("news.index"))


# ---- sources (Partners and Admin)

def _manager_only():
    if not can_manage(current_user):
        abort(403)


@news_bp.route("/sources")
@login_required
def sources():
    _manager_only()
    return render_template("newswatch/sources.html", sources=NewsSource.query.order_by(NewsSource.id).all(), kinds=KINDS, starter_sets=STARTER_SETS, regions=REGIONS,
                           configured=is_configured(), x_set=x_configured(), x_gap=X_MIN_GAP_HOURS, x_posts=X_POSTS_PER_FETCH, token_set=bool(os.environ.get("NEWS_WATCH_TOKEN")), state=refresh_state(),
                           counts={s.id: NewsItem.query.filter_by(source_id=s.id).count() for s in NewsSource.query.all()})


@news_bp.route("/sources/add", methods=["POST"])
@login_required
def add_source():
    _manager_only()
    kind = request.form.get("kind", "")
    query = (request.form.get("query") or "").strip()
    name = (request.form.get("name") or "").strip()[:120] or query[:60]
    region = request.form.get("region") if request.form.get("region") in REGIONS else "Zimbabwe"
    if kind not in KINDS or not query:
        flash("Choose a kind and enter the search words, the X username or the address.", "danger")
        return redirect(url_for("news.sources"))
    if kind == "x_account":
        handle = clean_handle(query)
        if not handle:
            flash("That is not an X username. Type it as @name or paste the account's x.com address.", "danger")
            return redirect(url_for("news.sources"))
        query = "@" + handle
        name = (request.form.get("name") or "").strip()[:120] or "X: " + query
    elif kind == "x_search":
        name = (request.form.get("name") or "").strip()[:120] or "X search: " + query[:50]
    elif kind != "google_news":
        ok, why = safe_url(query)
        if not ok:
            flash(why, "danger")
            return redirect(url_for("news.sources"))
    db.session.add(NewsSource(name=name, kind=kind, target=query[:500], region=region, created_by_id=current_user.id))
    db.session.commit()
    flash("Source added. Use 'Look for news now' to try it.", "success")
    return redirect(url_for("news.sources"))


@news_bp.route("/sources/starter", methods=["POST"])
@login_required
def add_starter():
    _manager_only()
    region = request.form.get("region") if request.form.get("region") in STARTER_SETS else "Zimbabwe"
    have = {(s.kind, s.target) for s in NewsSource.query.all()}
    added = 0
    for name, kind, query in STARTER_SETS[region]:
        if (kind, query) not in have:
            db.session.add(NewsSource(name=name, kind=kind, target=query, region=region, created_by_id=current_user.id))
            added += 1
    db.session.commit()
    flash(f"{added} {region} starter source(s) added." if added else f"The {region} starter sources are already there.", "success")
    return redirect(url_for("news.sources"))


@news_bp.route("/sources/<int:source_id>/toggle", methods=["POST"])
@login_required
def toggle_source(source_id):
    _manager_only()
    s = NewsSource.query.get_or_404(source_id)
    s.active = not s.active
    db.session.commit()
    return redirect(url_for("news.sources"))


@news_bp.route("/sources/<int:source_id>/delete", methods=["POST"])
@login_required
def delete_source(source_id):
    _manager_only()
    s = NewsSource.query.get_or_404(source_id)
    NewsItem.query.filter_by(source_id=s.id).update({"source_id": None})
    db.session.delete(s)
    db.session.commit()
    flash("Source removed. The stories it found are kept.", "info")
    return redirect(url_for("news.sources"))
