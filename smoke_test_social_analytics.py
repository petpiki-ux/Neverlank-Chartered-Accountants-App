"""Social analytics: dashboard figures and insights, Google Analytics visits (simulated), importing a platform report (CSV and Excel), connections page, permissions."""
import os, sys, tempfile, io, json, base64, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
for k in ("ANTHROPIC_API_KEY", "LEADS_YEAR", "GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"):
    os.environ.pop(k, None)
from app import create_app
from extensions import db
from models import *
import social as SM
import social_analytics as SA
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    pu = User(username="part1", name="Pat Partner", email="p@x.co", role="partner"); pu.set_password("pw")
    st = User(username="staff1", name="Sam Staff", email="s@x.co", role="staff"); st.set_password("pw")
    db.session.add_all([pu, st]); db.session.commit(); pid, sid = pu.id, st.id
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
admin, partner, staff = login(aid), login(pid), login(sid)
anon = app.test_client()

check(anon.get("/social/analytics").status_code in (302, 401), "the analytics pages need a sign-in")
check(staff.get("/social/analytics").status_code == 403 and staff.get("/social/analytics/import").status_code == 403 and staff.get("/social/analytics/connect").status_code == 403, "ordinary staff are not allowed in")
r = partner.get("/social/analytics")
check(r.status_code == 200 and "Nothing was posted in this period" in r.get_data(as_text=True), "with nothing posted the dashboard says so, without errors")
check(partner.get("/social/analytics?period=all").status_code == 200 and partner.get("/social/analytics?period=zzz").status_code == 200, "every period, and a wrong one, opens")

# ---- data: posts, versions posted on several days, with and without numbers, and leads from tracked links
now = dtm.datetime.utcnow()
def mk(title, pillar, platform, days_ago, link=True, nums=None, url="", body="", campaign=None):
    p = SocialPost(title=title, pillar=pillar, status="Posted", author_id=pid, campaign=campaign or SM.slug(title) + "-t", link_url="https://app.neverlank.co.zw/compliance-calendar" if link else "")
    db.session.add(p); db.session.flush()
    v = SocialVersion(post_id=p.id, platform=platform, body=body or title, status="posted", posted_at=now - dtm.timedelta(days=days_ago), post_url=url)
    if nums:
        v.impressions, v.likes, v.comments, v.shares, v.clicks = nums
        v.metrics_at, v.metrics_source = now, "typed"
    db.session.add(v); db.session.commit()
    return p.id, v.id, p.campaign
with app.app_context():
    a_p, a_v, a_c = mk("Quarterly dates nobody owns", SM.PILLARS[1], "linkedin", 3, nums=(2000, 40, 6, 4, 90), url="https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/", body="Most late penalties are about a date nobody owned. Put a name against every date and get the free calendar.")
    b_p, b_v, b_c = mk("Quarterly dates nobody owns X", SM.PILLARS[1], "x", 3, nums=(800, 10, 2, 3, 30), url="https://x.com/neverlank/status/1900000000000000002")
    c_p, c_v, c_c = mk("PAYE changes explained", SM.PILLARS[0], "linkedin", 10, nums=(1000, 5, 0, 0, 10), url="")
    d_p, d_v, d_c = mk("Meet the team", SM.PILLARS[4], "facebook", 12)                # posted, no numbers typed
    e_p, e_v, e_c = mk("Old post", SM.PILLARS[1], "linkedin", 200, nums=(500, 5, 1, 1, 5))
    for i in range(3):
        db.session.add(MarketingLead(first_name="L%d" % i, email="l%d@x.co" % i, utm_source="linkedin", utm_medium="social", utm_campaign=a_c, created_at=now - dtm.timedelta(days=2)))
    db.session.add(MarketingLead(first_name="X1", email="x1@x.co", utm_source="x", utm_medium="social", utm_campaign=b_c, created_at=now - dtm.timedelta(days=1)))
    db.session.add(MarketingLead(first_name="Web", email="w@x.co", utm_source="", utm_medium="", utm_campaign="", created_at=now))
    db.session.commit()

with app.app_context():
    rep = SA.build_report(30)
    t = rep["totals"]
    check(t["posts"] == 4 and t["versions"] == 4, "30 days: the 200-day-old post is left out (4 posts)")
    check(t["impressions"] == 3800 and t["engagement"] == 40 + 6 + 4 + 10 + 2 + 3 + 5, "views and likes+comments+shares are summed (3,800 views)")
    check(t["leads"] == 4, "sign-ups counted through tracked links per post and platform (3 LinkedIn + 1 X; the untracked sign-up is not counted)")
    check(t["missing"] == 1 and abs(t["rate"] - 100.0 * 70 / 3800) < 0.01, "one post has no numbers, and the rate is engagement over views")
    plats = {g["key"]: g for g in rep["platforms"]}
    check(set(plats) == {"linkedin", "x", "facebook"} and plats["linkedin"]["leads"] == 3 and plats["x"]["leads"] == 1 and rep["platforms"][0]["key"] == "linkedin", "platform table ranks LinkedIn first by sign-ups")
    check(plats["facebook"]["rate"] is None and plats["linkedin"]["rate"] is not None, "a platform with no views has no rate (not zero)")
    check(max(g["leads_pct"] for g in rep["platforms"]) == 100 and plats["x"]["leads_pct"] == 33, "bar widths are relative to the biggest")
    check({p["label"] for p in rep["pillars"]} == {"Education", "Authority", "Firm life and people"}, "pillars are shown by short name")
    texts = " ".join(x for _, x in rep["insights"])
    check("1 of 4 posted items have no results" in texts, "the insights say how many posts have no numbers")
    check("LinkedIn brought the most sign-ups: 3 of 4" in texts, "the insights name the platform with most sign-ups")
    check("Fewer than 10 posts" in texts, "a small sample is flagged")
    check("Instagram" in texts and "Nothing was posted on" in texts, "idle platforms are named")
    check(all(k in ("good", "look", "note") for k, _ in rep["insights"]), "every insight has a known kind")
    check(len(rep["weeks"]) >= 5 and sum(w["leads"] for w in rep["weeks"]) == 4 and sum(w["posts"] for w in rep["weeks"]) == 4, "weekly series count the posts and the sign-ups")
    check(rep["best"][0]["post"].id == a_p, "best post is the one with most sign-ups")
    allrep = SA.build_report(None)
    check(allrep["totals"]["posts"] == 5, "all time includes the old post")
r = partner.get("/social/analytics")
html = r.get_data(as_text=True)
check(r.status_code == 200 and "What the numbers say" in html and "By platform" in html and "Quarterly dates nobody owns" in html and "Website visits from Google Analytics are not connected" in html, "the dashboard shows the figures, the insights and the 'not connected' note")
check("Website visits</th>" not in html, "no website-visits column without Google Analytics")
check("Analytics</a>" in html and partner.get("/social/results").status_code == 200 and "Analytics dashboard" in partner.get("/social/results").get_data(as_text=True), "the Social menu and the Results page link to the dashboard")

# ---- the engagement-rate insight only speaks when one platform is clearly ahead
def _g(rate, n=4): return {"rate": rate, "with_numbers": n, "leads": 0, "engagement": 0, "versions": n}
close = SA._insights([{"has_numbers": True, "post": type("P", (), {"id": 1}), "when": dtm.datetime.utcnow(), "clicks": 0}], {"linkedin": _g(2.5), "x": _g(2.4), "facebook": _g(2.4)}, {}, {}, 30, None)
check(not any("best engagement rate" in t for _, t in close), "platforms with nearly the same engagement rate are not ranked")
clear = SA._insights([{"has_numbers": True, "post": type("P", (), {"id": 1}), "when": dtm.datetime.utcnow(), "clicks": 0}], {"linkedin": _g(4.0), "x": _g(2.4)}, {}, {}, 30, None)
check(any("LinkedIn has the best engagement rate: 4.0%" in t and "2.4% on the next best" in t for _, t in clear), "a clear lead is reported with the runner-up for comparison")

# ---- results typed by hand are marked as typed
r = partner.post("/social/%d/version/facebook/results" % d_p, data={"impressions": "300", "likes": "9"}, follow_redirects=True)
with app.app_context():
    v = db.session.get(SocialVersion, d_v)
    check(v.impressions == 300 and v.likes == 9 and v.metrics_source == "typed", "typing results marks their source as typed")

# ---- Google Analytics (simulated)
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
key = {"type": "service_account", "client_email": "neverlank-app@proj.iam.gserviceaccount.com", "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"}
check(not SA.ga_configured(), "not connected without the settings")
os.environ["GA4_PROPERTY_ID"] = "properties/123456789"
check(SA.ga_property() == "123456789" and not SA.ga_configured(), "the property number is read (the 'properties/' prefix is dropped) but a key is still needed")
os.environ["GA4_SERVICE_ACCOUNT_JSON"] = "not json"
check(SA.ga_key() is None, "a key that is not JSON is not used")
os.environ["GA4_SERVICE_ACCOUNT_JSON"] = base64.b64encode(json.dumps(key).encode()).decode()
check(SA.ga_key()["client_email"].startswith("neverlank-app") and SA.ga_configured(), "a base64 key is accepted")
os.environ["GA4_SERVICE_ACCOUNT_JSON"] = json.dumps(key)
jwt_text, uri = SA._signed_jwt(key, 1700000000)
head, claims, sig = jwt_text.split(".")
pad = lambda s: s + "=" * (-len(s) % 4)
claim = json.loads(base64.urlsafe_b64decode(pad(claims)))
check(json.loads(base64.urlsafe_b64decode(pad(head)))["alg"] == "RS256" and claim["iss"] == key["client_email"] and claim["scope"].endswith("analytics.readonly") and claim["exp"] - claim["iat"] == 3600, "the sign-in token is built and signed correctly")
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
pub = serialization.load_pem_private_key(pem.encode(), None).public_key()
pub.verify(base64.urlsafe_b64decode(pad(sig)), (head + "." + claims).encode(), padding.PKCS1v15(), hashes.SHA256())
check(True, "its signature verifies with the key's public half")
check(SA._signed_jwt(dict(key, token_uri="https://evil.example/token"), 1)[1] == SA.GA_TOKEN_URL, "a token address that is not Google's is ignored")

calls = []
class Resp:
    def __init__(self, status, payload): self.status_code, self._p = status, payload
    def json(self): return self._p
mode = {"ga": "ok"}
def fake_post(url, **kw):
    calls.append((url, kw))
    if "oauth2" in url:
        return Resp(200, {"access_token": "tok123", "expires_in": 3600})
    if mode["ga"] == "ok":
        return Resp(200, {"rows": [
            {"dimensionValues": [{"value": a_c}, {"value": "LinkedIn"}], "metricValues": [{"value": "120"}, {"value": "100"}]},
            {"dimensionValues": [{"value": a_c}, {"value": "x"}], "metricValues": [{"value": "15"}, {"value": "14"}]},
            {"dimensionValues": [{"value": b_c}, {"value": "x"}], "metricValues": [{"value": "40"}, {"value": "35"}]},
            {"dimensionValues": [{"value": "(not set)"}], "metricValues": [{"value": "1"}]}]})
    if mode["ga"] == "denied":
        return Resp(403, {"error": {"message": "User does not have sufficient permissions for this property."}})
    if mode["ga"] == "api_off":
        return Resp(403, {"error": {"message": "Google Analytics Data API has not been used in project 1 before or it is disabled."}})
    return Resp(404, {"error": {"message": "Property not found"}})
SA.requests.post = fake_post
SA._ga_cache.clear(); SA._ga_token.update(token="", exp=0)
ok, data = SA.ga_visits(30)
check(ok and data[(a_c, "linkedin")]["sessions"] == 120 and data[(b_c, "x")]["sessions"] == 40 and (a_c, "x") in data and len(data) == 3, "visits are read per campaign and platform, and a malformed row is skipped")
rq = [c for c in calls if "runReport" in c[0]][0]
check("properties/123456789:runReport" in rq[0] and rq[1]["headers"]["Authorization"] == "Bearer tok123" and rq[1]["json"]["dimensionFilter"]["filter"]["stringFilter"]["value"] == "social", "the report is asked for social traffic only, with the token")
n = len(calls); SA.ga_visits(30); check(len(calls) == n, "the answer is kept for a while (no second call)")
SA.ga_visits(30, force=True); check(len(calls) > n, "refresh asks again")
html = partner.get("/social/analytics?period=30").get_data(as_text=True)
check("Website visits</th>" in html and "Google Analytics</div>" in html, "with Google Analytics connected the dashboard shows website visits")
with app.app_context():
    rep = SA.build_report(30, data)
    check(rep["totals"]["visits"] == 120 + 40 and {g["key"]: g["visits"] for g in rep["platforms"]}["linkedin"] == 120, "visits are attributed to the right post and platform (LinkedIn 120; the X visit of another post does not leak)")
for m, expect in (("denied", "Viewer"), ("api_off", "Data API"), ("notfound", "property number")):
    mode["ga"] = m; SA._ga_cache.clear()
    ok, msg = SA.ga_visits(30)
    check(not ok and expect in msg, "a Google Analytics refusal is explained in words: " + m)
html = partner.get("/social/analytics?period=30").get_data(as_text=True)
check(partner.get("/social/analytics?period=30").status_code == 200 and "could not be read from Google Analytics" in html, "a failed Google Analytics call does not break the dashboard")
mode["ga"] = "ok"; SA._ga_cache.clear()
r = partner.post("/social/analytics/connect/test", follow_redirects=True)
check("Connected." in r.get_data(as_text=True) and "3 tracked social campaign" in r.get_data(as_text=True), "the Test button reports a working connection")
page = partner.get("/social/analytics/connect").get_data(as_text=True)
check("123456789" in page and key["client_email"] in page and "BEGIN PRIVATE KEY" not in page and pem[40:80] not in page, "the connection page shows the property and the service account email, never the key")
check(staff.post("/social/analytics/connect/test").status_code == 403, "only people who manage Social can test it")
os.environ.pop("GA4_SERVICE_ACCOUNT_JSON"); os.environ.pop("GA4_PROPERTY_ID")
page = partner.get("/social/analytics/connect").get_data(as_text=True)
check("not connected" in page and "GA4_PROPERTY_ID" in page and "Not connected, and not built yet" in page and "Community Management" in page, "without settings the page says what is missing and how to connect")
check("analytics/connect/test" not in page, "no test button until it is set up")

# ---- importing reports
check(SA.url_key("https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/") == SA.url_key("https://linkedin.com/posts/neverlank_activity-7100000000000000001-xyz?utm_source=share"), "LinkedIn addresses match by the post number")
check(SA.url_key("https://twitter.com/neverlank/status/1900000000000000002?s=20") == SA.url_key("https://x.com/i/web/status/1900000000000000002"), "X addresses match by the status number, twitter.com or x.com")
check(SA.url_key("https://www.instagram.com/p/AbC123xyz/") == SA.url_key("instagram.com/p/abc123xyz"), "Instagram addresses match by the shortcode")
check(SA.url_key("https://neverlank.co.zw/blog/") == SA.url_key("http://www.neverlank.co.zw/blog"), "other addresses match without www, query or end slash")
check(SA._to_int("1,234") == 1234 and SA._to_int("12.0") == 12 and SA._to_int("-") is None and SA._to_int("") is None and SA._to_int(None) is None and SA._to_int(-3) is None and SA._to_int(True) is None and SA._to_int(5.0) == 5, "numbers are read from text, decimals, blanks and dashes")

x_csv = ("Tweet id,Tweet permalink,Tweet text,time,impressions,engagements,retweets,replies,likes,user profile clicks,url link clicks,detail expands\n"
         "1900000000000000002,https://twitter.com/neverlank/status/1900000000000000002,A date nobody owns is a penalty waiting to happen,2026-11-20 08:00 +0000,\"1,234\",90,5,3,25,4,41,2\n"
         "1900000000000000999,https://twitter.com/neverlank/status/1900000000000000999,Something not in the app,2026-11-21 08:00 +0000,50,2,0,0,2,0,1,0\n").encode()
rows, err = SA.read_report("tweets.csv", x_csv)
check(not err and len(rows) == 2 and rows[0]["impressions"] == 1234 and rows[0]["likes"] == 25 and rows[0]["comments"] == 3 and rows[0]["shares"] == 5 and rows[0]["clicks"] == 41, "an X export is read: views, likes, replies, retweets and link clicks")
check(rows[0]["url"].endswith("1900000000000000002") and rows[0]["text"].startswith("A date nobody"), "its address and wording are read")

import openpyxl
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Metrics"
ws.append(["Content analytics", None, None]); ws.append(["Report for 1 Nov to 30 Nov", None, None])
ws.append(["Post title", "Post link", "Created date", "Impressions (total)", "Clicks (total)", "Reactions (total)", "Comments (total)", "Reposts (total)", "Click through rate (CTR)"])
ws.append(["Most late penalties are about a date nobody owned. Put a name against every date and get the free calendar.", "https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/", "11/18/2026", 2500, 120, 55, 8, 6, 0.048])
ws.append(["Different post not in the app", "https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000777/", "11/19/2026", 700, 20, 9, 0, 1, 0.02])
buf = io.BytesIO(); wb.save(buf)
rows, err = SA.read_report("content.xlsx", buf.getvalue())
check(not err and len(rows) == 2 and rows[0]["impressions"] == 2500 and rows[0]["likes"] == 55 and rows[0]["clicks"] == 120 and rows[0]["shares"] == 6, "a LinkedIn Excel export is read even with title rows above the header, ignoring the click-through rate")
rows, err = SA.read_report("x.csv", b"foo,bar\n1,2\n")
check(rows == [] and "could not find the columns" in err, "a file with the wrong columns gets a plain explanation")
rows, err = SA.read_report("x.pdf", b"%PDF")
check(rows == [] and ".csv" in err, "other file types are refused")
rows, err = SA.read_report("x.xlsx", b"not a workbook")
check(rows == [] and "could not be read" in err, "a broken file does not crash")

# preview and apply
r = partner.post("/social/analytics/import", data={"step": "preview", "platform": "x", "report": (io.BytesIO(x_csv), "tweets.csv")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check(r.status_code == 200 and "2 rows in tweets.csv, 1 matched" in html and "matched by its address" in html and "no match found" in html and "1,234" in html, "the preview matches the X row by its address and leaves the other unmatched")
with app.app_context():
    v = db.session.get(SocialVersion, b_v); check(v.impressions == 800, "nothing changed by previewing")
# first row matched to the X version; apply
form = {"step": "apply", "platform": "x", "n": "2", "vid_0": str(b_v), "vid_1": "", "url_0": "https://x.com/neverlank/status/1900000000000000002", "url_1": "",
        "impressions_0": "1234", "likes_0": "25", "comments_0": "3", "shares_0": "5", "clicks_0": "41", "impressions_1": "50", "likes_1": "", "comments_1": "", "shares_1": "", "clicks_1": ""}
r = partner.post("/social/analytics/import", data=form, follow_redirects=True)
with app.app_context():
    v = db.session.get(SocialVersion, b_v)
    check(v.impressions == 1234 and v.likes == 25 and v.comments == 3 and v.shares == 5 and v.clicks == 41 and v.metrics_source == "import", "applying updates the matched post and marks the numbers as imported")
check("Updated the results of 1 post" in r.get_data(as_text=True), "the person is told how many posts were updated")
form = {"step": "apply", "platform": "x", "n": "1", "vid_0": str(b_v), "url_0": "", "impressions_0": "", "likes_0": "", "comments_0": "", "shares_0": "", "clicks_0": "7"}
partner.post("/social/analytics/import", data=form, follow_redirects=True)
with app.app_context():
    v = db.session.get(SocialVersion, b_v)
    check(v.impressions == 1234 and v.clicks == 7, "a blank in the report never clears a number")
form = {"step": "apply", "platform": "x", "n": "2", "vid_0": str(a_v), "vid_1": "abc", "impressions_0": "1", "impressions_1": "2"}
r = partner.post("/social/analytics/import", data=form, follow_redirects=True)
with app.app_context():
    check(db.session.get(SocialVersion, a_v).impressions == 2000, "a post of another platform cannot be updated through the X import")
    check(db.session.get(SocialVersion, d_v).metrics_source == "typed", "other posts are untouched")
check("Nothing was updated" in r.get_data(as_text=True), "if nothing matched the person is told")
r = partner.post("/social/analytics/import", data={"step": "apply", "platform": "linkedin", "n": "9999999", "vid_0": str(c_v), "impressions_0": "1500", "url_0": "https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000555/"}, follow_redirects=True)
with app.app_context():
    v = db.session.get(SocialVersion, c_v)
    check(v.impressions == 1500 and v.post_url.endswith("555/"), "an address missing on the post is filled in from the report; an absurd row count is capped")
    db.session.get(SocialVersion, c_v).post_url = ""; db.session.commit()
r = partner.post("/social/analytics/import", data={"step": "preview", "platform": "linkedin", "report": (io.BytesIO(buf.getvalue()), "content.xlsx")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check("matched by its address" in html and "2,500" in html and "now 2,000" in html, "an Excel report matches the LinkedIn post by address and shows the number now on the post")
# wording fallback: remove the stored address, the wording still matches
with app.app_context():
    db.session.get(SocialVersion, a_v).post_url = ""; db.session.commit()
r = partner.post("/social/analytics/import", data={"step": "preview", "platform": "linkedin", "report": (io.BytesIO(buf.getvalue()), "content.xlsx")}, content_type="multipart/form-data")
check("matched by its wording, please check" in r.get_data(as_text=True), "without an address the post is matched by its wording, and flagged to check")
r = partner.post("/social/analytics/import", data={"step": "preview", "platform": "linkedin"}, follow_redirects=True)
check("Choose the report file first" in r.get_data(as_text=True), "a missing file is explained")
big = io.BytesIO(b"x" * (SA.MAX_IMPORT_BYTES + 10))
r = partner.post("/social/analytics/import", data={"step": "preview", "platform": "linkedin", "report": (big, "big.csv")}, content_type="multipart/form-data", follow_redirects=True)
check("larger than 5 MB" in r.get_data(as_text=True), "a file over 5 MB is refused")
check(staff.post("/social/analytics/import", data={"step": "apply", "platform": "x", "n": "1", "vid_0": str(b_v), "clicks_0": "1"}).status_code == 403, "ordinary staff cannot import")
check(partner.get("/social/analytics/import?platform=nonsense").status_code == 200, "a wrong platform in the address falls back safely")

print("\n%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
