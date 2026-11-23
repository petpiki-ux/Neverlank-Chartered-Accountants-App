"""News Watch -> website: the Publish button (Partners and Admin only), what is refused, what goes to WordPress (simulated), take down."""
import os, sys, tempfile, datetime as dtm
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
import news_site as NS
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

calls = []
POSTS = {}
def fake(method, path, json=None, params=None):
    calls.append((method, path, json))
    if path == "categories" and method == "GET": return True, []
    if path == "categories": return True, {"id": 7, "name": json["name"]}
    if path == "posts": POSTS[101] = json; return True, {"id": 101, "link": "https://neverlank.co.zw/zimra-vat-deadline/"}
    if path.startswith("posts/"):
        pid = int(path.split("/")[1]); POSTS[pid] = {**POSTS.get(pid, {}), **json}; return True, {"id": pid, "link": "https://neverlank.co.zw/zimra-vat-deadline/"}
    return False, "unexpected"
NS.wp_request = fake

with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    pu = User(username="part1", name="Pat Partner", email="p@x.co", role="partner"); pu.set_password("pw")
    st = User(username="staff1", name="Sam Staff", email="s@x.co", role="staff"); st.set_password("pw")
    db.session.add_all([pu, st]); db.session.add(Client(name="Zambezi Mining (Pvt) Ltd", industry="Mining", created_by_id=aid)); db.session.commit(); pid, sid = pu.id, st.id
    a = NewsItem(title="ZIMRA extends VAT filing deadline for September returns", url="https://herald.co.zw/vat-deadline", publisher="The Herald", status="relevant",
                 published_at=dtm.datetime(2026, 11, 20), summary="ZIMRA has moved the VAT filing date for September returns.", why="Late filing attracts penalties.", fingerprint="a1")
    b = NewsItem(title="Someone said on X that tax is changing", url="https://x.com/a/status/1", status="relevant", unconfirmed=True, fingerprint="b1")
    db.session.add_all([a, b]); db.session.commit(); aid_item, bid_item = a.id, b.id
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
admin, partner, staff = login(aid), login(pid), login(sid)
GOOD = "ZIMRA has extended the filing date for September VAT returns. If you file late you can be charged penalties and interest, so check your calendar and file early where you can."
def form(**k):
    d = {"title": "ZIMRA extends the VAT filing deadline", "note": GOOD, "checked": "1", "mode": "publish"}; d.update(k); return d

os.environ.pop("WP_USERNAME", None); os.environ.pop("WP_APP_PASSWORD", None)
check(not NS.configured(), "without the website login the connection is reported as not set up")
check(staff.get(f"/news-watch/{aid_item}/publish").status_code == 403, "staff cannot open the publish page")
check(staff.post(f"/news-watch/{aid_item}/publish", data=form()).status_code == 403, "staff cannot publish")
check(staff.post(f"/news-watch/{aid_item}/unpublish").status_code == 403, "staff cannot take a post down")
pg = partner.get("/news-watch/").get_data(as_text=True)
check("Publish to website" in pg, "a partner sees the Publish button on a story")
check("Publish to website" not in staff.get("/news-watch/").get_data(as_text=True), "staff do not see the button")
g = partner.get(f"/news-watch/{aid_item}/publish").get_data(as_text=True)
check("Not connected yet" in g and "ZIMRA has moved the VAT filing date" in g, "the page opens, says it is not connected, and starts from the summary")
r = partner.post(f"/news-watch/{aid_item}/publish", data=form()).get_data(as_text=True)
check("not set up yet" in r and not calls, "nothing is sent while the connection is missing")

os.environ["WP_USERNAME"], os.environ["WP_APP_PASSWORD"] = "api-user", "abcd efgh ijkl mnop"
check(NS.configured(), "with the username and application password the connection counts as set up")
check("Only stories from a news source" in partner.get(f"/news-watch/{bid_item}/publish").get_data(as_text=True), "an unconfirmed story is refused")
partner.post(f"/news-watch/{bid_item}/publish", data=form())
check(not calls, "an unconfirmed story is never sent")
check("Publish to website" not in partner.get("/news-watch/").get_data(as_text=True).split("Someone said on X")[1].split('class="card"')[0], "no Publish button on an unconfirmed story")

for bad, expect in ((form(checked=""), "Tick the box"), (form(note="Too short."), "your own words"), (form(title="Short"), "headline must be"),
                    (form(note=GOOD + " Zambezi Mining (Pvt) Ltd is affected."), "names a client")):
    r = partner.post(f"/news-watch/{aid_item}/publish", data=bad).get_data(as_text=True)
    check(expect in r, "refused: " + expect)
check(not calls, "none of the refused forms reached the website")

r = partner.post(f"/news-watch/{aid_item}/publish", data=form(), follow_redirects=True).get_data(as_text=True)
check("Published on the website" in r, "a good form publishes")
check([c[0] + " " + c[1] for c in calls] == ["GET categories", "POST categories", "POST posts"], "it finds, makes the category, then posts")
body = POSTS[101]
check(body["status"] == "publish" and body["categories"] == [7] and body["title"] == "ZIMRA extends the VAT filing deadline", "the post is live, in the category, with the edited headline")
check("Reported by The Herald" in body["content"] and 'href="https://herald.co.zw/vat-deadline"' in body["content"] and "nofollow" in body["content"] and "not tax, audit or legal advice" in body["content"], "the post has the publisher, the link to the original and the disclaimer")
check("Late filing attracts" not in body["content"], "the app's own 'why it matters' is not added unless the person keeps it in the note")
with app.app_context():
    it = NewsItem.query.get(aid_item)
    check(it.wp_post_id == 101 and it.wp_status == "publish" and it.wp_url.startswith("https://neverlank.co.zw/") and it.wp_posted_by_id == pid, "the story remembers the post, address, status and who published it")
pg = partner.get("/news-watch/").get_data(as_text=True)
check("On the website" in pg and "Take off website" in pg, "the page now shows it is on the website")
r = partner.post(f"/news-watch/{aid_item}/publish", data=form(title="ZIMRA moves the VAT filing deadline again"), follow_redirects=True).get_data(as_text=True)
check(calls[-1][0:2] == ("POST", "posts/101") and POSTS[101]["title"] == "ZIMRA moves the VAT filing deadline again", "editing updates the same post, it does not make a second one")
n = len(calls)
r = partner.post(f"/news-watch/{aid_item}/unpublish", follow_redirects=True).get_data(as_text=True)
check("Taken off the website" in r and POSTS[101]["status"] == "draft", "take down returns the post to a draft")
with app.app_context():
    check(NewsItem.query.get(aid_item).wp_status == "draft", "the story shows it is no longer live")
check("Publish again" in partner.get("/news-watch/").get_data(as_text=True), "it can be put up again")
r = partner.post(f"/news-watch/{aid_item}/publish", data=form(mode="draft"), follow_redirects=True).get_data(as_text=True)
check("Saved as a draft" in r and POSTS[101]["status"] == "draft", "'Save as draft' keeps it off the public site")

NS.wp_request = lambda *a, **k: (False, "The website refused the login. Check WP_USERNAME and the Application Password (not the normal password).")
r = partner.post(f"/news-watch/{aid_item}/publish", data=form()).get_data(as_text=True)
check("refused the login" in r and "Published on the website" not in r, "a website error is shown in plain words and nothing is recorded as published")
# the real request function: errors turned into words, no exception
import requests
class R:
    def __init__(s, c, j=None): s.status_code, s._j = c, j
    def json(s):
        if s._j is None: raise ValueError
        return s._j
importlib = __import__("importlib"); NS2 = importlib.reload(NS)
for code, text in ((401, "refused the login"), (500, "error (500)")):
    requests.request = lambda *a, **k: R(code)
    ok, why = NS2.wp_request("GET", "posts"); check(not ok and text in why, f"HTTP {code} becomes plain words")
def boom(*a, **k): raise requests.ConnectionError("x")
requests.request = boom
ok, why = NS2.wp_request("GET", "posts"); check(not ok and "could not be reached" in why, "a network failure does not crash the page")
os.environ["WP_SITE_URL"] = "http://neverlank.co.zw"
check(not NS2.configured(), "a plain http site address is not accepted")
os.environ.pop("WP_SITE_URL")
print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
