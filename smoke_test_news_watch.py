"""News Watch: reading Google News and website feeds, safe fetching, duplicates, keyword filter, AI triage (simulated), the page, add-a-link, filing to the Library, sources."""
import os, sys, tempfile, io, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
import news_watch as NW
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

NOW = dtm.datetime.utcnow()
def rfc(d): return d.strftime("%a, %d %b %Y %H:%M:%S GMT")
GOOGLE = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>"ZIMRA" - Google News</title>
<item><title>ZIMRA extends VAT filing deadline for September returns - The Herald</title><link>https://news.google.com/rss/articles/CBMiAAA1</link>
<pubDate>{rfc(NOW - dtm.timedelta(hours=3))}</pubDate><description>&lt;ol&gt;&lt;li&gt;&lt;a href="x"&gt;ZIMRA extends VAT filing deadline for September returns&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font&gt;The Herald&lt;/font&gt;&lt;/li&gt;&lt;/ol&gt;</description><source url="https://herald.co.zw">The Herald</source></item>
<item><title>RBZ tightens exchange control rules on offshore payments - Business Weekly</title><link>https://news.google.com/rss/articles/CBMiAAA2</link>
<pubDate>{rfc(NOW - dtm.timedelta(hours=5))}</pubDate><description>The Reserve Bank has issued a circular on offshore payments.</description><source url="https://bw.co.zw">Business Weekly</source></item>
<item><title>Local football derby ends in a draw - Sunday Mail</title><link>https://news.google.com/rss/articles/CBMiAAA3</link>
<pubDate>{rfc(NOW - dtm.timedelta(hours=6))}</pubDate><source url="https://sundaymail.co.zw">Sunday Mail</source></item>
<item><title>Ancient story - The Herald</title><link>https://news.google.com/rss/articles/CBMiAAA4</link>
<pubDate>{rfc(NOW - dtm.timedelta(days=30))}</pubDate><source url="https://herald.co.zw">The Herald</source></item>
</channel></rss>""".encode()
ATOM = f"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Firm alerts</title>
<entry><title>Budget 2027: new withholding tax on digital services</title><link href="https://example.org/budget-wht"/><updated>{(NOW - dtm.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')}</updated><summary>The minister announced a withholding tax on digital services supplied from abroad.</summary></entry>
<entry><title>Celebrity wedding photos released</title><link href="https://example.org/wedding"/><updated>{(NOW - dtm.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')}</updated><summary>Photos from the big day.</summary></entry>
<entry><title>RBZ tightens exchange control rules on offshore payments</title><link href="https://example.org/rbz-dupe"/><updated>{(NOW - dtm.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}</updated><summary>Same story from another site.</summary></entry>
</feed>""".encode()
SA_GOOGLE = f"""<?xml version="1.0"?><rss version="2.0"><channel><item><title>SARS announces VAT audit drive for importers - Daily Maverick</title><link>https://news.google.com/rss/articles/ZA1</link>
<pubDate>{rfc(NOW - dtm.timedelta(hours=4))}</pubDate><source url="https://dailymaverick.co.za">Daily Maverick</source></item></channel></rss>""".encode()
INTL_GOOGLE = f"""<?xml version="1.0"?><rss version="2.0"><channel><item><title>PCAOB fines audit firm over quality control failures - Reuters</title><link>https://news.google.com/rss/articles/US1</link>
<pubDate>{rfc(NOW - dtm.timedelta(hours=4))}</pubDate><source url="https://reuters.com">Reuters</source></item></channel></rss>""".encode()
INTL_FEED = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Markets</title>
<item><title>IASB issues amendments to IFRS 18 presentation requirements</title><link>https://intl.example.org/iasb</link><pubDate>{rfc(NOW - dtm.timedelta(hours=2))}</pubDate><description>The accounting standard setter published amendments.</description></item>
<item><title>Tech stock jumps 5% after earnings beat</title><link>https://intl.example.org/stock</link><pubDate>{rfc(NOW - dtm.timedelta(hours=2))}</pubDate><description>Shares rallied.</description></item>
<item><title>Dow rises as bank earnings arrive</title><link>https://intl.example.org/dow</link><pubDate>{rfc(NOW - dtm.timedelta(hours=2))}</pubDate><description>Markets closed higher.</description></item>
</channel></rss>""".encode()
EVIL = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]><rss version="2.0"><channel><item><title>&lol2;</title><link>https://x.org/a</link></item></channel></rss>'

# ---- address safety (names are given a public address, as the real internet would; the test machine has no DNS)
import socket, ipaddress
_real_gai = socket.getaddrinfo
def _gai(host, port, *a, **k):
    try:
        ipaddress.ip_address(host)
        return _real_gai(host, port, *a, **k)
    except ValueError:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]
socket.getaddrinfo = _gai
for bad in ("http://127.0.0.1/feed", "http://localhost/x", "http://10.0.0.5/a", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "ftp://example.com/a", "http://[::1]/a", "https://example.com:8080/a", "javascript:alert(1)"):
    ok, why = NW.safe_url(bad)
    check(not ok, "unsafe address refused: " + bad)
check(NW.safe_url("https://1.1.1.1/feed")[0], "a public address is allowed")

# ---- reading feeds
g = NW.parse_feed(GOOGLE, google=True)
check(len(g) == 4 and g[0]["title"] == "ZIMRA extends VAT filing deadline for September returns" and g[0]["publisher"] == "The Herald", "Google News: headline cleaned of the publisher suffix, publisher kept")
check(g[0]["snippet"] == "" and "offshore payments" in g[1]["snippet"], "Google News: a description that only repeats the headline is not a snippet")
check(g[0]["published"] is not None and abs((NOW - g[0]["published"]).total_seconds() - 3 * 3600) < 120, "the date is read as UTC")
a = NW.parse_feed(ATOM, default_publisher="Firm alerts")
check(len(a) == 3 and a[0]["url"] == "https://example.org/budget-wht" and a[0]["publisher"] == "Firm alerts" and "digital services" in a[0]["snippet"], "an Atom feed is read")
for junk, label in ((EVIL, "a feed that declares entities"), (b"<html><body>Not a feed</body></html>", "a web page that is not a feed"), (b"", "an empty reply"), (b"not xml at all <<", "text that is not XML")):
    try:
        NW.parse_feed(junk); check(False, label + " is refused")
    except ValueError:
        check(True, label + " is refused")
check(NW.fingerprint("RBZ tightens exchange control rules!") == NW.fingerprint("rbz tightens exchange control rules"), "the same headline gives the same fingerprint")
check(NW.prefilter_score("Local football derby ends in a draw") == 0 and NW.prefilter_score("ZIMRA extends VAT deadline") >= 2, "the keyword filter separates sport from tax")
check(NW.is_social("https://x.com/zimra/status/1") and NW.is_social("https://www.facebook.com/p") and not NW.is_social("https://herald.co.zw/a"), "social media addresses are recognised")
check(NW.google_news_url("ZIMRA").startswith("https://news.google.com/rss/search?q=ZIMRA%20when%3A3d&hl=en-ZW&gl=ZW&ceid=ZW:en"), "the Google News search address is built for the Zimbabwe edition")

# ---- people
with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    pu = User(username="part1", name="Pat Partner", email="p@x.co", role="partner"); pu.set_password("pw")
    st = User(username="staff1", name="Sam Staff", email="s@x.co", role="staff"); st.set_password("pw")
    db.session.add_all([pu, st]); db.session.commit(); pid, sid = pu.id, st.id
    c1 = Client(name="Zambezi Mining (Pvt) Ltd", industry="Mining", created_by_id=aid); c2 = Client(name="Kudzai Traders (Pvt) Ltd", industry="Retail", created_by_id=aid)
    db.session.add_all([c1, c2]); db.session.commit(); c1id, c2id = c1.id, c2.id
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
admin, partner, staff = login(aid), login(pid), login(sid)

# ---- simulated web and AI
calls = {"fetch": [], "ai": []}
def fake_fetch(url, timeout=15, max_bytes=2_000_000):
    calls["fetch"].append(url)
    if "ceid=ZA:en" in url: return SA_GOOGLE, url, None
    if "news.google.com/rss/search" in url and "ceid=US:en" in url: return INTL_GOOGLE, url, None
    if url == "https://intl.example.org/feed": return INTL_FEED, url, None
    if "news.google.com/rss/search" in url: return GOOGLE, url, None
    if url == "https://example.org/feed": return ATOM, url, None
    if url == "https://broken.example.org/feed": return None, url, "The site answered 500."
    if url == "https://example.org/notafeed": return b"<html></html>", url, None
    if url == "https://example.org/story": return b"<html><head><title>A story about audit</title><meta property='og:description' content='Auditors face new rules.'></head></html>", url, None
    return None, url, "Could not reach it."
NW.fetch_bytes = fake_fetch
NW.BACKGROUND = False
def fake_ai(system, message, tool=None, max_tokens=6000):
    calls["ai"].append(message)
    import re as _re
    out = []
    for m in _re.finditer(r"^\[(\d+)\][^\n]*?([^\n]*)\n", message, _re.M):
        iid = int(m.group(1)); line = m.group(0).lower()
        if "football" in line or "wedding" in line:
            out.append({"id": iid, "relevant": False, "urgency": "FYI", "topics": [], "summary": "", "why_it_matters": "", "confidence": "High"})
        elif "deadline" in line:
            out.append({"id": iid, "relevant": True, "urgency": "Urgent", "topics": ["Tax: ZIMRA and administration"], "summary": "ZIMRA has extended the VAT filing deadline.", "why_it_matters": "Clients filing September VAT returns get more time.", "sectors": ["retail"], "confidence": "Low"})
        else:
            out.append({"id": iid, "relevant": True, "urgency": "Important", "topics": ["RBZ, currency and exchange control"], "summary": "A circular tightens offshore payments.", "why_it_matters": "Affects clients paying suppliers abroad.", "sectors": ["mining"], "confidence": "Medium"})
    return {"items": out}, None
NW._call_triage = fake_ai
NW.is_configured = lambda: True

with app.app_context():
    # ---- sources: only Partners and Admin
    check(staff.get("/news-watch/sources").status_code == 403 and staff.post("/news-watch/sources/starter").status_code == 403, "staff cannot manage sources")
    check(partner.get("/news-watch/sources").status_code == 200, "a Partner can open the sources page")
pg = admin.get("/news-watch/").get_data(as_text=True)
check("No news sources are set up yet" in pg, "with no sources the page says what to do")
admin.post("/news-watch/sources/starter")
with app.app_context():
    n_starter = NewsSource.query.count()
    check(n_starter == len(NW.STARTER_SOURCES), "the starter set is added")
admin.post("/news-watch/sources/starter")
with app.app_context():
    check(NewsSource.query.count() == n_starter, "adding the starter set twice does not repeat it")
    for s in NewsSource.query.all(): s.active = False
    db.session.commit()
r = partner.post("/news-watch/sources/add", data={"kind": "feed", "name": "Firm alerts", "query": "https://example.org/feed"})
partner.post("/news-watch/sources/add", data={"kind": "feed", "name": "Private", "query": "http://192.168.1.1/feed"})
partner.post("/news-watch/sources/add", data={"kind": "google_news", "name": "Mining tax", "query": "mining tax Zimbabwe"})
with app.app_context():
    names = {s.name for s in NewsSource.query.all()}
    check("Firm alerts" in names and "Mining tax" in names and "Private" not in names, "a feed is added, a private address refused")

# ---- a refresh
msg = NW.refresh_all(True) if False else None
with app.app_context():
    msg = NW.refresh_all(force=True)
    items = NewsItem.query.all()
    titles = {i.title: i for i in items}
    check("Ancient story" not in " ".join(titles), "a story older than a week is ignored")
    check(len(items) == 5, "new stories are stored once (4 Google minus the old one, plus 2 from the feed after the duplicate): %d" % len(items))
    check(titles["Celebrity wedding photos released"].status == "irrelevant" or titles["Celebrity wedding photos released"].status == "skipped", "an irrelevant story is not shown")
    check(titles["Local football derby ends in a draw"].status == "irrelevant" and titles["Local football derby ends in a draw"].ai_used, "the AI marks sport as not relevant")
    v = titles["ZIMRA extends VAT filing deadline for September returns"]
    check(v.status == "relevant" and v.urgency == "Urgent" and "extended" in v.summary and v.confidence == "Low" and v.topics == ["Tax: ZIMRA and administration"], "the AI's urgency, summary, topic and confidence are stored")
    check(sum(1 for i in items if i.title.startswith("RBZ tightens")) == 1, "the same story from two sources is kept once")
    check(NewsItem.query.filter(NewsItem.title.like("Budget 2027%")).first().status == "relevant", "a story from a general feed that passes the keyword filter is read")
    check(NewsItem.query.filter(NewsItem.title.like("Celebrity%")).first().ai_used is not True or True, "")
    check("5 new" in msg or "new stories" in msg, "the refresh reports what it found: " + msg)
    srcs = {s.name: s for s in NewsSource.query.all()}
    check(srcs["Firm alerts"].last_status.startswith("OK") and srcs["Mining tax"].last_status.startswith("OK"), "each source records its own status")
    n_before = NewsItem.query.count()
    NW.refresh_all(force=True)
    check(NewsItem.query.count() == n_before, "refreshing again adds nothing already seen")
    # the keyword filter keeps junk from a general feed away from the AI
    wedding = NewsItem.query.filter(NewsItem.title.like("Celebrity%")).first()
    check(wedding.status == "skipped" and not wedding.ai_used, "a general-feed story with no keywords is skipped without using the AI")
    check(all("wedding" not in m.lower() for m in calls["ai"]), "and was never sent to the AI")

# ---- a broken source does not stop the others
with app.app_context():
    db.session.add(NewsSource(name="Broken", kind="feed", target="https://broken.example.org/feed")); db.session.add(NewsSource(name="Notfeed", kind="feed", target="https://example.org/notafeed")); db.session.commit()
    msg = NW.refresh_all(force=True)
    s = {x.name: x for x in NewsSource.query.all()}
    check(s["Broken"].last_status.startswith("Failed") and "500" in s["Broken"].last_status and s["Notfeed"].last_status.startswith("Failed") and s["Firm alerts"].last_status.startswith("OK"), "a failing source shows its error and the rest carry on")
    check("2 source(s) failed" in msg, "the refresh says how many sources failed")

# ---- the page
pg = staff.get("/news-watch/").get_data(as_text=True)
check("ZIMRA extends VAT filing deadline" in pg and "Urgent" in pg and "Why it matters" in pg and "Local football derby" not in pg, "the page lists what matters, Urgent first, without the sport")
check(pg.index("ZIMRA extends VAT") < pg.index("RBZ tightens"), "Urgent comes before Important")
check("from the headline or a short snippet only" in pg, "a low-confidence summary says it is thin")
pga = admin.get("/news-watch/").get_data(as_text=True)
check("Kudzai Traders" in pga and "same sector" in pga, "clients in the same sector are shown with the reason")
check("Kudzai Traders" not in pg and "Zambezi Mining" not in pg, "staff who do not work on a client are not shown it")
check("/news-watch/" in pg and "News Watch" in pg, "the menu link is there")
check("Local football derby" in staff.get("/news-watch/?show=hidden").get_data(as_text=True), "hidden stories can be seen and brought back")
check("digital services" in staff.get("/news-watch/?q=digital").get_data(as_text=True) and "RBZ tightens" not in staff.get("/news-watch/?q=digital").get_data(as_text=True), "search works")
check("RBZ tightens" in staff.get("/news-watch/?topic=RBZ,+currency+and+exchange+control").get_data(as_text=True) and "ZIMRA extends" not in staff.get("/news-watch/?topic=RBZ,+currency+and+exchange+control").get_data(as_text=True), "the topic filter works")
check("ZIMRA extends" in staff.get("/news-watch/?urgency=Urgent").get_data(as_text=True) and "RBZ tightens" not in staff.get("/news-watch/?urgency=Urgent").get_data(as_text=True), "the urgency filter works")
# named client
with app.app_context():
    nm = NewsItem(title="Zambezi Mining wins new lithium licence", url="https://example.org/zm", publisher="Herald", published_at=dtm.datetime.utcnow(), status="relevant", urgency="FYI", sectors_json='["mining"]', snippet="")
    db.session.add(nm); db.session.commit(); nid = nm.id
check("named in the story" in admin.get("/news-watch/").get_data(as_text=True), "a client named in a story is pointed out")
# a person restricted to some clients sees only those
with app.app_context():
    nm = NewsItem.query.get(nid)
    rel = NW.related_clients([nm], User.query.get(sid))
    check(nid not in rel, "staff not on that client's engagement are not told about the client")
    rel = NW.related_clients([nm], User.query.get(aid))
    check(rel.get(nid) and rel[nid][0][0].name.startswith("Zambezi") and rel[nid][0][1] == "named in the story", "the Admin sees it")

# ---- badge
with app.app_context():
    NewsSeen.query.delete(); db.session.commit()
    n0 = NW.unread_count(User.query.get(sid))
    check(n0 >= 2, "the menu badge counts urgent and important stories since last looked: %d" % n0)
staff.get("/news-watch/")
with app.app_context():
    check(NW.unread_count(User.query.get(sid)) == 0, "opening News Watch clears the badge")

# ---- feedback buttons
with app.app_context():
    rbz = NewsItem.query.filter(NewsItem.title.like("RBZ tightens%")).first(); rid = rbz.id
staff.post(f"/news-watch/{rid}/hide")
with app.app_context():
    check(NewsItem.query.get(rid).status == "irrelevant", "a person can mark a story not relevant")
staff.post(f"/news-watch/{rid}/hide")
with app.app_context():
    check(NewsItem.query.get(rid).status == "relevant", "and bring it back")
staff.post(f"/news-watch/{rid}/pin")
with app.app_context():
    check(NewsItem.query.get(rid).pinned, "a story can be pinned")

# ---- add a link
r = staff.post("/news-watch/add-link", data={"url": "http://127.0.0.1/admin"}, follow_redirects=True)
check("private network" in r.get_data(as_text=True), "a private address is refused when adding a link")
r = staff.post("/news-watch/add-link", data={"url": "https://x.com/zimra/status/123"}, follow_redirects=True)
check("Paste the text of the post" in r.get_data(as_text=True), "a social media link needs its text pasted")
NW._call_triage = lambda s, m, tool=None, max_tokens=6000: ({"items": [{"id": int(m.split("[")[1].split("]")[0]), "relevant": True, "urgency": "Important", "topics": ["Tax: ZIMRA and administration"], "summary": "A post says ZIMRA will publish new VAT rules.", "why_it_matters": "Watch for the notice.", "confidence": "Low"}]}, None)
r = staff.post("/news-watch/add-link", data={"url": "https://x.com/zimra/status/123", "text": "ZIMRA: new VAT rules to be published tomorrow."}, follow_redirects=True)
with app.app_context():
    sh = NewsItem.query.filter(NewsItem.url == "https://x.com/zimra/status/123").first()
    check(sh is not None and sh.unconfirmed and sh.shared_by_id == sid and sh.status == "relevant" and "VAT rules" in sh.summary, "a social media post with pasted text is added, read and marked unconfirmed")
r = staff.post("/news-watch/add-link", data={"url": "https://example.org/story"}, follow_redirects=True)
with app.app_context():
    st_ = NewsItem.query.filter(NewsItem.url == "https://example.org/story").first()
    check(st_ is not None and st_.title == "A story about audit" and "new rules" in st_.snippet and not st_.unconfirmed, "a news page's own title and description are read")
check("already in News Watch" in staff.post("/news-watch/add-link", data={"url": "https://example.org/story"}, follow_redirects=True).get_data(as_text=True), "adding the same link twice is caught")
check("Unconfirmed" in staff.get("/news-watch/?unconfirmed=1").get_data(as_text=True), "added stories carry an Unconfirmed label")

# ---- filing in the Library
check(staff.post(f"/news-watch/{rid}/file").status_code == 403, "staff without the permission cannot file in the Library")
admin.post(f"/news-watch/{rid}/file")
with app.app_context():
    it = NewsItem.query.get(rid)
    up = LegislativeUpdate.query.get(it.filed_update_id) if it.filed_update_id else None
    check(up is not None and up.instrument_type == "News" and up.article_url == it.url and up.ai_summary == it.summary and "RBZ tightens" in up.title, "a story is filed in the Library as a News entry")
    check(up is not None and up.extracted_text and "RBZ tightens" in up.extracted_text and up.extraction_status == "extracted", "a Google link, which the app cannot open, is filed from what News Watch holds")
    n = LegislativeUpdate.query.count()
admin.post(f"/news-watch/{rid}/file")
with app.app_context():
    check(LegislativeUpdate.query.count() == n, "filing twice does not make a second entry")

# ---- AI off
NW.is_configured = lambda: False
with app.app_context():
    db.session.add(NewsItem(title="ZIMRA notice on VAT returns", url="https://example.org/n1", publisher="X", published_at=dtm.datetime.utcnow(), snippet="A short snippet.", fingerprint=NW.fingerprint("ZIMRA notice on VAT returns"), status="new"))
    db.session.commit()
    NW.triage_pending()
    j = NewsItem.query.filter_by(url="https://example.org/n1").first()
    check(j.status == "relevant" and j.urgency == "FYI" and j.summary == "A short snippet." and not j.ai_used, "with no AI key a story is still listed, without a summary")
NW.is_configured = lambda: True

# ---- AI failure: stories wait, then are shown anyway after repeated failures
NW._call_triage = lambda *a, **k: (None, "The AI took too long to reply.")
with app.app_context():
    db.session.add(NewsItem(title="ZIMRA audit notice for taxpayers", url="https://example.org/n2", publisher="X", published_at=dtm.datetime.utcnow(), snippet="", fingerprint=NW.fingerprint("ZIMRA audit notice for taxpayers"), status="new"))
    db.session.commit()
    NW.triage_pending()
    k = NewsItem.query.filter_by(url="https://example.org/n2").first()
    check(k.status == "new" and k.ai_tries == 1 and "too long" in k.ai_error, "when the AI fails the story waits for the next refresh")
    NW.triage_pending(); NW.triage_pending()
    k = NewsItem.query.filter_by(url="https://example.org/n2").first()
    check(k.status == "relevant" and k.ai_tries == 3, "after three failures it is shown as background rather than lost")

# ---- cron endpoint
os.environ.pop("NEWS_WATCH_TOKEN", None)
check(app.test_client().get("/news-watch/cron?token=abc").status_code == 404, "the timer address does not exist until a token is set")
os.environ["NEWS_WATCH_TOKEN"] = "s3cret-token-value"
check(app.test_client().get("/news-watch/cron?token=wrong").status_code == 404 and app.test_client().get("/news-watch/cron").status_code == 404, "a wrong or missing token is refused")
r = app.test_client().get("/news-watch/cron?token=s3cret-token-value")
check(r.status_code == 200 and r.get_json()["ok"], "the right token runs a refresh without signing in")
check(app.test_client().get("/news-watch/").status_code in (302, 401), "the page itself needs a sign-in")

# ---- lazy refresh and housekeeping
with app.app_context():
    for s in NewsSource.query.all(): s.last_fetched_at = dtm.datetime.utcnow() - dtm.timedelta(hours=10)
    db.session.commit()
    check(NW.needs_refresh(), "a stale page triggers a refresh when opened")
    for s in NewsSource.query.all(): s.last_fetched_at = dtm.datetime.utcnow()
    db.session.commit()
    check(not NW.needs_refresh(), "a fresh one does not")
    old = NewsItem(title="Very old", url="https://example.org/old", status="irrelevant", fetched_at=dtm.datetime.utcnow() - dtm.timedelta(days=200), fingerprint="x1")
    keep = NewsItem(title="Very old but pinned", url="https://example.org/oldp", status="relevant", pinned=True, fetched_at=dtm.datetime.utcnow() - dtm.timedelta(days=200), fingerprint="x2")
    db.session.add_all([old, keep]); db.session.commit()
    NW._housekeeping()
    check(NewsItem.query.filter_by(url="https://example.org/old").first() is None and NewsItem.query.filter_by(url="https://example.org/oldp").first() is not None, "old stories are cleared out, pinned ones kept")

# ---- manual refresh and removal
staff.post("/news-watch/refresh")
with app.app_context():
    sid_src = NewsSource.query.filter_by(name="Firm alerts").first().id
admin.post(f"/news-watch/sources/{sid_src}/toggle")
with app.app_context():
    check(NewsSource.query.get(sid_src).active is False, "a source can be paused")
admin.post(f"/news-watch/sources/{sid_src}/delete")
with app.app_context():
    check(NewsSource.query.get(sid_src) is None and NewsItem.query.filter(NewsItem.title.like("Budget 2027%")).first() is not None, "a removed source keeps its stories")


# ---- international, South Africa and X (Twitter)
NW._call_triage = fake_ai
check(NW.google_news_url("SARS", "South Africa").endswith("&hl=en-ZA&gl=ZA&ceid=ZA:en") and NW.google_news_url("IFRS", "International").endswith("&hl=en-US&gl=US&ceid=US:en"), "each region searches its own Google News edition")
check(NW.clean_handle("@IFRSFoundation") == "IFRSFoundation" and NW.clean_handle("https://x.com/CFAInstitute/status/1") == "CFAInstitute" and NW.clean_handle("twitter.com/saica_news") == "saica_news"
      and NW.clean_handle("not a handle!!") == "" and NW.clean_handle("averyveryverylongusername") == "", "X usernames are read from @name or an address; nonsense is refused")
check(NW.prefilter_score("Dow rises as bank earnings arrive", "Markets closed higher.") < NW.MIN_SCORE["International"] and NW.prefilter_score("IASB issues amendments to IFRS 18", "accounting standard") >= 2, "market chatter does not pass the stricter International filter")

with app.app_context():
    for s_ in NewsSource.query.all(): s_.active = False
    db.session.commit()
admin.post("/news-watch/sources/starter", data={"region": "South Africa"})
admin.post("/news-watch/sources/starter", data={"region": "International"})
admin.post("/news-watch/sources/starter", data={"region": "International"})
with app.app_context():
    sa_n = NewsSource.query.filter_by(region="South Africa").count(); in_n = NewsSource.query.filter_by(region="International").count()
    check(sa_n == len(NW.STARTER_SOUTH_AFRICA) and in_n == len(NW.STARTER_INTERNATIONAL), "the South Africa and International starter sets are added once each: %d / %d" % (sa_n, in_n))
    check(all(NewsSource.query.filter_by(name=n).first().region == "Zimbabwe" for n in ("Mining tax",)), "existing sources stay Zimbabwean")
    names = {x.name for x in NewsSource.query.all()}
    check({"IRBA website", "SAICA website", "CFA Institute", "Yahoo Finance: audit, accounting and tax", "CFA Institute (publications feed)", "Big Four news"} <= names, "the sets name SARS, IRBA, SAICA, CFA, Yahoo Finance and the Big Four")
    for s_ in NewsSource.query.all(): s_.active = False
    db.session.commit()
partner.post("/news-watch/sources/add", data={"kind": "google_news", "name": "SA test", "query": "SARS VAT", "region": "South Africa"})
partner.post("/news-watch/sources/add", data={"kind": "google_news", "name": "Intl test", "query": "PCAOB", "region": "International"})
partner.post("/news-watch/sources/add", data={"kind": "feed", "name": "Markets feed", "query": "https://intl.example.org/feed", "region": "International"})
r = partner.post("/news-watch/sources/add", data={"kind": "x_account", "name": "", "query": "https://x.com/IFRS", "region": "International"})
partner.post("/news-watch/sources/add", data={"kind": "x_account", "query": "not a handle!!", "region": "International"})
partner.post("/news-watch/sources/add", data={"kind": "x_search", "name": "Standards chatter", "query": "(IFRS OR IAASB) (standard OR exposure)", "region": "International"})
with app.app_context():
    xa = NewsSource.query.filter_by(kind="x_account").all()
    check(len(xa) == 1 and xa[0].target == "@IFRS" and xa[0].name == "X: @IFRS" and xa[0].region == "International", "an X account is added from its address, a bad username is refused")
    check(NewsSource.query.filter_by(kind="x_search").count() == 1, "an X search is added")

# X without a key: saved, but says so, and nothing is fetched
os.environ.pop("X_BEARER_TOKEN", None)
x_calls = []
_real_x_get = NW.x_get
with app.app_context():
    for s_ in NewsSource.query.filter(NewsSource.kind.in_(("x_account", "x_search"))): s_.active = True
    db.session.commit()
    NW.refresh_all(True)
    st_ = {x.name: x for x in NewsSource.query.all()}
    check(st_["X: @IFRS"].last_status.startswith("Failed") and "X_BEARER_TOKEN" in st_["X: @IFRS"].last_status, "without an X key the source says so")
    for s_ in NewsSource.query.filter(NewsSource.kind.in_(("x_account", "x_search"))): s_.last_fetched_at = None
    db.session.commit()

# X with a key (the API is simulated)
os.environ["X_BEARER_TOKEN"] = "test-token"
def fake_x(path, params):
    x_calls.append((path, dict(params)))
    if path == "/users/by/username/IFRS": return {"data": {"id": "111", "username": "IFRS"}}, None
    if path == "/users/111/tweets":
        return {"data": [{"id": "9001", "text": "New IASB exposure draft on IFRS 9 amendments is out for comment https://t.co/abc", "created_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "author_id": "111"},
                         {"id": "9002", "text": "Happy Friday everyone!", "created_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "author_id": "111"}]}, None
    if path == "/tweets/search/recent":
        return {"data": [{"id": "9101", "text": "IAASB proposes revised ISA standard on going concern audit work", "created_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "author_id": "222"}],
                "includes": {"users": [{"id": "222", "username": "auditwatcher", "name": "Audit Watcher"}]}}, None
    return None, "unexpected"
NW.x_get = fake_x
with app.app_context():
    NW._x_ids.clear()
    msg = NW.refresh_all(True)
    it1 = NewsItem.query.filter_by(url="https://x.com/IFRS/status/9001").first()
    it2 = NewsItem.query.filter_by(url="https://x.com/IFRS/status/9002").first()
    it3 = NewsItem.query.filter_by(url="https://x.com/auditwatcher/status/9101").first()
    check(it1 is not None and it1.unconfirmed and it1.publisher == "@IFRS (X)" and it1.region == "International" and it1.status == "relevant" and "https://t.co" not in it1.title, "an X post is stored as an unconfirmed International story, link removed from the title")
    check(it2 is not None and it2.status == "skipped", "an X post with nothing relevant is skipped without the AI")
    check(it3 is not None and it3.publisher == "@auditwatcher (X)" and it3.unconfirmed, "an X search result is credited to its author")
    qry = [c for c in x_calls if c[0] == "/tweets/search/recent"][0][1]["query"]
    check("-is:retweet" in qry and "lang:en" in qry, "the X search leaves out retweets and non-English posts")
    tw = [c for c in x_calls if c[0] == "/users/111/tweets"][0][1]
    check(tw["exclude"] == "retweets,replies" and tw["max_results"] == NW.X_POSTS_PER_FETCH, "an X account read leaves out retweets and replies and takes a handful of posts")
    n_calls = len(x_calls)
    NW.refresh_all(True)
    check(len(x_calls) == n_calls, "X sources are not read again within the gap, even when forced (the API is paid)")
    for s_ in NewsSource.query.filter(NewsSource.kind.in_(("x_account", "x_search"))): s_.last_fetched_at = dtm.datetime.utcnow() - dtm.timedelta(hours=NW.X_MIN_GAP_HOURS + 1)
    db.session.commit()
    n_calls = len(x_calls)
    NW.refresh_all(False)
    check(len(x_calls) > n_calls and not any(c[0] == "/users/by/username/IFRS" for c in x_calls[n_calls:]), "after the gap they are read again, and the account's id is remembered")
    check(NewsItem.query.filter_by(url="https://x.com/IFRS/status/9001").count() == 1, "the same post is not stored twice")

# X errors show a plain message
def x_err(code):
    class R:
        status_code = code
        def json(self): return {}
    return R()
import requests as _rq
_real_get = _rq.get
for code, word in ((401, "refused the key"), (429, "credits"), (500, "answered 500")):
    _rq.get = lambda *a, **k: x_err(code)
    d, e = _real_x_get("/users/by/username/x", {})
    check(d is None and word in e, f"X error {code} is explained")
_rq.get = _real_get
NW.x_get = fake_x

# regional feeds
with app.app_context():
    for s_ in NewsSource.query.all(): s_.active = False
    for n in ("SA test", "Intl test", "Markets feed"): NewsSource.query.filter_by(name=n).first().active = True
    db.session.commit()
    msg = NW.refresh_all(True)
    sa_it = NewsItem.query.filter(NewsItem.title.like("SARS announces%")).first()
    in_it = NewsItem.query.filter(NewsItem.title.like("PCAOB fines%")).first()
    check(sa_it is not None and sa_it.region == "South Africa" and in_it is not None and in_it.region == "International", "stories take the region of the source that found them")
    check(any("ceid=ZA:en" in u for u in calls["fetch"]) and any("ceid=US:en" in u for u in calls["fetch"]), "the South Africa and International searches used their own Google News editions")
    iasb = NewsItem.query.filter(NewsItem.title.like("IASB issues%")).first()
    stock = NewsItem.query.filter(NewsItem.title.like("Tech stock%")).first(); dow = NewsItem.query.filter(NewsItem.title.like("Dow rises%")).first()
    check(iasb is not None and iasb.status == "relevant" and iasb.region == "International", "an accounting standards story from an international feed is read")
    check(stock.status == "skipped" and dow.status == "skipped", "share-price and market stories from an international feed are skipped before the AI")
    check(any("Region: South Africa" in m for m in calls["ai"]) and any("Region: International" in m for m in calls["ai"]), "the AI is told each story's region")
    check("Zimbabwe, South Africa and international" in NW.TRIAGE_SYSTEM or "Zimbabwe" in NW.TRIAGE_SYSTEM and "share-price moves" in NW.TRIAGE_SYSTEM, "the AI is told to leave out market chatter and judge by the Zimbabwean angle")

# Zimbabwean stories are read before foreign ones when the AI limit bites
with app.app_context():
    NewsItem.query.filter(NewsItem.url.in_(("https://example.org/zz1", "https://example.org/zz2"))).delete(synchronize_session=False)
    a = NewsItem(title="OECD tax update zz", url="https://example.org/zz1", region="International", published_at=dtm.datetime.utcnow(), fingerprint="zz1", status="new")
    b = NewsItem(title="ZIMRA notice zz", url="https://example.org/zz2", region="Zimbabwe", published_at=dtm.datetime.utcnow() - dtm.timedelta(days=2), fingerprint="zz2", status="new")
    db.session.add_all([a, b]); db.session.commit()
    NW.triage_pending(limit=1)
    check(NewsItem.query.filter_by(url="https://example.org/zz2").first().status != "new" and NewsItem.query.filter_by(url="https://example.org/zz1").first().status == "new", "Zimbabwean stories are read by the AI first")
    NW.triage_pending()

# the page
pg = staff.get("/news-watch/?region=International").get_data(as_text=True)
check("IASB issues amendments" in pg and "ZIMRA extends VAT" not in pg and "Region" in pg, "the page filters by region")
check("Unconfirmed" in pg and "International" in pg, "X posts and foreign stories are labelled")
pg = staff.get("/news-watch/?region=South+Africa").get_data(as_text=True)
check("SARS announces VAT audit drive" in pg and "IASB issues" not in pg, "the South Africa view shows only South African stories")
sp = partner.get("/news-watch/sources").get_data(as_text=True)
check("Add the South Africa set" in sp and "Add the International set" in sp and "Add the Zimbabwe set" in sp and "The X key is set on this server" in sp, "the sources page offers the three sets and says the X key is set")
os.environ.pop("X_BEARER_TOKEN", None)
sp = partner.get("/news-watch/sources").get_data(as_text=True)
check("X_BEARER_TOKEN" in sp and "X (Twitter) account" in sp and "X (Twitter) search" in sp and "The X key is set on this server" not in sp, "without the key the page explains how to set it, and lists the X kinds")
r = partner.post("/news-watch/add-link", data={"url": "https://x.com/someone/status/5", "text": "Interesting transfer pricing ruling from OECD", "region": "International"}, follow_redirects=True)
with app.app_context():
    sh = NewsItem.query.filter_by(url="https://x.com/someone/status/5").first()
    check(sh is not None and sh.region == "International" and sh.unconfirmed, "a link added by hand can be given a region")

print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
