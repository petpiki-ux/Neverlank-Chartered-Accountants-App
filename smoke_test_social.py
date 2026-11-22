"""Social media management: permissions, posts and platform versions, tracked links, automatic checks, AI drafting (simulated), approval, posting, results, WhatsApp list, templates and log."""
import os, sys, tempfile, re, json, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
for k in ("ANTHROPIC_API_KEY", "LEADS_YEAR"):
    os.environ.pop(k, None)
from app import create_app
from extensions import db
from models import *
import social as SM
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    pu = User(username="part1", name="Pat Partner", email="p@x.co", role="partner"); pu.set_password("pw")
    st = User(username="staff1", name="Sam Staff", email="s@x.co", role="staff"); st.set_password("pw")
    sv = User(username="sup1", name="Sue Supervisor", email="sv@x.co", role="supervisor"); sv.set_password("pw")
    db.session.add_all([pu, st, sv]); db.session.commit(); pid, sid, svid = pu.id, st.id, sv.id
    db.session.add(Client(name="Zambezi Mining (Pvt) Ltd", industry="Mining", created_by_id=aid)); db.session.commit()
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
admin, partner, staff, sup = login(aid), login(pid), login(sid), login(svid)
anon = app.test_client()

check(any(p[0] == "manage_social" and p[3] == ("supervisor", "partner", "admin") for p in PERMISSIONS) and any(p[0] == "approve_social" and p[3] == ("partner", "admin") for p in PERMISSIONS), "the two permissions exist: manage (supervisor and up), approve (partner and admin)")
check(anon.get("/social/").status_code in (302, 401), "the pages need a sign-in")
check(staff.get("/social/").status_code == 403 and staff.get("/social/new").status_code == 403 and staff.get("/social/whatsapp").status_code == 403, "ordinary staff are not allowed in")
check(sup.get("/social/").status_code == 200 and partner.get("/social/").status_code == 200, "supervisors and partners are")

# ---- pure helpers
class P: pass
post = P(); post.link_url = "https://app.neverlank.co.zw/compliance-calendar?x=1"; post.campaign = "cal-1"; post.title = "t"
tl = SM.tracked_link(post, "linkedin")
check("utm_source=linkedin" in tl and "utm_medium=social" in tl and "utm_campaign=cal-1" in tl and "x=1" in tl, "each platform gets its own tracked link, keeping the existing query")
post.link_url = tl
check(SM.tracked_link(post, "x").count("utm_source") == 1 and "utm_source=x" in SM.tracked_link(post, "x"), "tags are replaced, not stacked")
post.link_url = ""
check(SM.tracked_link(post, "x") == "", "no link, no tracked link")
check(SM.measure("x", "hello " + "https://app.neverlank.co.zw/compliance-calendar?utm_source=x&utm_medium=social&utm_campaign=abc-123") == 6 + 23 and SM.measure("linkedin", "abc") == 3, "a link counts as 23 characters on X only")
check(SM.safe_url("javascript:alert(1)") is None and SM.safe_url("") == "" and SM.safe_url("https://a.co/x") == "https://a.co/x" and SM.safe_url("ftp://a.co") is None, "only http and https links are accepted")
check(SM.hashtags("a #Tax b #ZIMRA2027 c#no") == ["#Tax", "#ZIMRA2027"], "hashtags are counted")

# ---- making a post
r = partner.post("/social/new", data={"title": "", "mode": "draft"}, follow_redirects=True)
check("short title" in r.get_data(as_text=True), "a title is required")
r = partner.post("/social/new", data={"title": "Bad link", "link_url": "javascript:alert(1)", "mode": "draft"}, follow_redirects=True)
with app.app_context():
    check(SocialPost.query.filter_by(title="Bad link").count() == 0, "a link that is not http(s) is refused")
r = partner.post("/social/new", data={"title": "Quarterly dates", "brief": "Quarterly payment dates are coming. Nobody owns the date.", "pillar": SM.PILLARS[1], "planned_date": dtm.date.today().isoformat(),
                                      "link_url": "https://app.neverlank.co.zw/compliance-calendar", "platforms": ["linkedin", "x", "instagram"], "mode": "draft"})
with app.app_context():
    p1 = SocialPost.query.filter_by(title="Quarterly dates").first(); p1id = p1.id
    check(r.status_code == 302 and p1.status == "Draft" and p1.author_id == pid and p1.campaign == f"quarterly-dates-{p1id}" and sorted(v.platform for v in p1.versions) == ["instagram", "linkedin", "x"], "a blank draft is created with a version per platform and its own campaign tag")
r = partner.post("/social/new", data={"title": "An idea", "mode": "idea", "platforms": ["facebook"]})
with app.app_context():
    check(SocialPost.query.filter_by(title="An idea").first().status == "Idea", "an idea can be saved")
r = partner.post("/social/new", data={"title": "No AI", "brief": "Some facts.", "mode": "ai", "platforms": ["linkedin"]}, follow_redirects=True)
check("not switched on" in r.get_data(as_text=True), "asking for the AI when it is off says so and leaves the wording blank")

# ---- AI drafting (simulated)
SM.ai_available = lambda: True
calls = []
def fake_ai(system, message):
    calls.append((system, message))
    return {"posts": [
        {"platform": "linkedin", "headline": "", "body": "Most late penalties are about a date nobody owned.\n\nPut a name against every date.\n\nGet the calendar: {link}\n\nGeneral information, not advice.\n\n#Tax #Zimbabwe #Compliance", "media_note": ""},
        {"platform": "x", "headline": "", "body": "A date nobody owns is a penalty waiting to happen. Get the free calendar: {link} #Tax", "media_note": ""},
        {"platform": "youtube", "headline": "How to stop missing tax dates", "body": "A short video on owning every date. {link}", "media_note": "Talking-head, 60 seconds, navy background"},
        {"platform": "tiktok", "headline": "", "body": "should not be used, not requested", "media_note": ""}],
        "check_these": ["Check the quarterly payment dates against the Finance Act", "Confirm the penalty wording"]}, None
SM._call_ai = fake_ai
r = partner.post("/social/new", data={"title": "Dates nobody owns", "brief": "Quarterly dates. Offer: free 2027 Compliance Calendar.", "mode": "ai", "link_url": "https://app.neverlank.co.zw/compliance-calendar", "platforms": ["linkedin", "x", "youtube"]}, follow_redirects=True)
with app.app_context():
    p2 = SocialPost.query.filter_by(title="Dates nobody owns").first(); p2id = p2.id
    vs = {v.platform: v for v in p2.versions}
    check(p2.ai_used and "Put a name" in vs["linkedin"].body and vs["youtube"].headline == "How to stop missing tax dates" and "navy" in vs["youtube"].media_note and "tiktok" not in vs, "the AI fills the platforms asked for, and only those")
    check("Finance Act" in p2.ai_flags and len(p2.ai_flags.split("\n")) == 2, "the things a person must check are kept")
check("Drafted by the AI" in r.get_data(as_text=True) and "must verify" in r.get_data(as_text=True), "the page says it was drafted by the AI and lists what to verify")
check("BRIEF:" in calls[-1][1] and "{link}" in calls[-1][1] and "Quarterly dates" in calls[-1][1], "the AI is given the brief, the platforms and the link rule")
check("ONLY facts" in calls[-1][0] and "Never name a client" in calls[-1][0] and "General information, not advice" in calls[-1][0], "the AI is told never to invent facts, never to name clients, and to add the disclaimer")
SM._call_ai = lambda s, m: (None, "The AI is busy.")
r = partner.post(f"/social/{p2id}/redraft", follow_redirects=True)
check("could not draft it" in r.get_data(as_text=True), "if the AI fails, a message appears and the wording is kept")
with app.app_context():
    check("Put a name" in SocialVersion.query.filter_by(post_id=p2id, platform="linkedin").first().body, "nothing is lost when the AI fails")
SM._call_ai = fake_ai

# ---- the post page
page = partner.get(f"/social/{p2id}").get_data(as_text=True)
check("LinkedIn" in page and "X (Twitter)" in page and "YouTube" in page and "Copy and open LinkedIn" in page and "utm_source=linkedin" in page and "data-limit=\"280\"" in page, "the post page shows each platform with its limit, copy buttons and tracked link")
check(staff.get(f"/social/{p2id}").status_code == 403, "staff cannot open it")

# ---- automatic checks
with app.app_context():
    p = SocialPost.query.get(p2id)
    issues = SM.scan(p)
    check(not SM.blocking(issues), "the AI's draft has no blocking errors")
    check(not [i for i in issues if "client" in i["text"].lower()], "no client is mentioned")
    v = [x for x in p.versions if x.platform == "x"][0]
    v.body = "x" * 300; db.session.flush()
    check(any(i["level"] == "error" and "over the 280" in i["text"] for i in SM.scan(p)), "text over a platform's limit is an error")
    v.body = "Hello [CLIENT NAME] and {thing}"; db.session.flush()
    check(any(i["level"] == "error" and "placeholder" in i["text"] for i in SM.scan(p)), "a leftover [bracket] or {brace} is an error")
    v.body = "Zambezi Mining paid US$1,200 by 30 November, guaranteed no tax. 15% off."; db.session.flush()
    texts = " | ".join(i["text"] for i in SM.scan(p))
    check("Zambezi Mining" in texts and "figures" in texts and "date" in texts and "promise" in texts, "client name, figures, dates and promises are flagged")
    v.body = "A date nobody owns. {link}"; db.session.flush()
    yt = [x for x in p.versions if x.platform == "youtube"][0]; yt.headline = ""
    check(any("YouTube: add a title" in i["text"] for i in SM.scan(p)), "YouTube needs a title")
    yt.headline = "t" * 120; db.session.flush()
    check(any("title is over 100" in i["text"] for i in SM.scan(p)), "a YouTube title over 100 characters is an error")
    yt.headline = "How to stop missing tax dates"
    li = [x for x in p.versions if x.platform == "linkedin"][0]
    li.body = "Tax tip. " + "The Act says you must pay. " * 12 + "\n\n#a #b #c #d #e #f #g #h #i #j #k"; db.session.flush()
    t = " | ".join(i["text"] for i in SM.scan(p))
    check("does not say it is general information" in t and "hashtags" in t, "tax wording without the disclaimer, and too many hashtags, are flagged")
    db.session.rollback()

# ---- submit and approve
LI = "Most late penalties are about a date nobody owned.\n\nPut a name against every date.\n\nGet the calendar: {link}\n\nGeneral information, not advice.\n\n#Tax #Zimbabwe"
data = {"title": "Dates nobody owns", "link_url": "https://app.neverlank.co.zw/compliance-calendar", "planned_date": dtm.date.today().isoformat(), "brief": "b",
        "body_linkedin": LI, "body_x": "A date nobody owns is a penalty waiting to happen. {link}", "body_youtube": "A short video. {link}", "headline_youtube": "How to stop missing tax dates",
        "media_youtube": "Talking head", "media_linkedin": "", "media_x": ""}
r = partner.post(f"/social/{p2id}/save", data=data, follow_redirects=True)
check("Saved." in r.get_data(as_text=True), "the post can be saved")
r = partner.post(f"/social/{p2id}/save", data=dict(data, link_url="ftp://bad"), follow_redirects=True)
check("must start with http" in r.get_data(as_text=True), "a bad link is not saved")
r = sup.post(f"/social/{p2id}/approve", data={f"check_{k}": "1" for k, _ in SM.CHECKLIST})
check(r.status_code == 403, "a supervisor cannot approve")
r = partner.post(f"/social/{p2id}/approve", data={"check_client": "1", "check_facts": "1"}, follow_redirects=True)
with app.app_context():
    check("Tick every item" in r.get_data(as_text=True) and SocialPost.query.get(p2id).status == "Draft", "approval needs every checklist item ticked")
r = sup.post(f"/social/{p2id}/submit", follow_redirects=True)
with app.app_context():
    check(SocialPost.query.get(p2id).status == "Pending approval", "a supervisor can send it for approval")
badge = partner.get("/", follow_redirects=True).get_data(as_text=True)
check('title="Posts waiting for your approval"' in badge, "the menu shows how many posts wait for an approver")
check('title="Posts waiting for your approval"' not in sup.get("/", follow_redirects=True).get_data(as_text=True), "and only to approvers")
r = partner.post(f"/social/{p2id}/reject", data={"review_note": ""}, follow_redirects=True)
check("what to change" in r.get_data(as_text=True), "sending back needs a reason")
r = partner.post(f"/social/{p2id}/reject", data={"review_note": "Add the date source"}, follow_redirects=True)
with app.app_context():
    x = SocialPost.query.get(p2id)
    check(x.status == "Rejected" and x.review_note == "Add the date source", "it can be sent back with a note")
check("Add the date source" in partner.get(f"/social/{p2id}").get_data(as_text=True), "the note is shown")
partner.post(f"/social/{p2id}/submit")
r = partner.post(f"/social/{p2id}/approve", data={f"check_{k}": "1" for k, _ in SM.CHECKLIST}, follow_redirects=True)
with app.app_context():
    x = SocialPost.query.get(p2id)
    check(x.status == "Approved" and x.approver_id == pid and x.approved_at and len(x.checks) == len(SM.CHECKLIST), "approved with the checklist, the approver and the time recorded")

# blocked approval
r = partner.post("/social/new", data={"title": "Too long", "platforms": ["x"], "mode": "draft"})
with app.app_context():
    tl_id = SocialPost.query.filter_by(title="Too long").first().id
partner.post(f"/social/{tl_id}/save", data={"title": "Too long", "body_x": "y" * 400})
r = partner.post(f"/social/{tl_id}/submit", follow_redirects=True)
with app.app_context():
    check("over the 280" in r.get_data(as_text=True) and SocialPost.query.get(tl_id).status == "Draft", "a post with an error cannot be sent for approval")
r = partner.post(f"/social/{tl_id}/approve", data={f"check_{k}": "1" for k, _ in SM.CHECKLIST}, follow_redirects=True)
with app.app_context():
    check(SocialPost.query.get(tl_id).status == "Draft", "nor approved, however the checklist is ticked")

# editing after approval
r = partner.post(f"/social/{p2id}/save", data=dict(data, body_x="A different wording. {link}"), follow_redirects=True)
with app.app_context():
    x = SocialPost.query.get(p2id)
    check("needs approval again" in r.get_data(as_text=True) and x.status == "Draft" and x.approver_id is None and x.checks == [], "changing the wording after approval sends it back to draft")
partner.post(f"/social/{p2id}/save", data=dict(data, body_x="Same again. {link}", planned_date=""))
partner.post(f"/social/{p2id}/approve", data={f"check_{k}": "1" for k, _ in SM.CHECKLIST})
r = partner.post(f"/social/{p2id}/save", data=dict(data, body_x="Same again. {link}", pillar=SM.PILLARS[0], planned_date=dtm.date.today().isoformat()))
with app.app_context():
    check(SocialPost.query.get(p2id).status == "Approved", "changing only the date or pillar does not need approval again")

# ---- posting
r = partner.post(f"/social/{p1id}/version/linkedin/posted", data={"post_url": "https://linkedin.com/x"}, follow_redirects=True)
check("must be approved" in r.get_data(as_text=True), "nothing can be marked as posted before approval")
r = partner.post(f"/social/{p2id}/version/linkedin/posted", data={"post_url": "javascript:alert(1)"}, follow_redirects=True)
check("must start with http" in r.get_data(as_text=True), "the post's address must be http(s)")
partner.post(f"/social/{p2id}/version/linkedin/posted", data={"post_url": "https://linkedin.com/posts/neverlank-1"})
with app.app_context():
    x = SocialPost.query.get(p2id)
    check(x.status == "Approved" and SocialVersion.query.filter_by(post_id=p2id, platform="linkedin").first().status == "posted", "one platform posted: the post is still open")
partner.post(f"/social/{p2id}/version/x/posted", data={"post_url": ""})
partner.post(f"/social/{p2id}/version/youtube/skip")
with app.app_context():
    check(SocialPost.query.get(p2id).status == "Posted", "all platforms posted or skipped: the post is Posted")
partner.post(f"/social/{p2id}/version/youtube/skip")
with app.app_context():
    check(SocialPost.query.get(p2id).status == "Approved", "undoing a skip reopens it")
partner.post(f"/social/{p2id}/version/youtube/skip"); 
r = partner.post(f"/social/{p2id}/version/x/results", data={"impressions": "1,250", "likes": "30", "comments": "4", "shares": "2", "clicks": "55"}, follow_redirects=True)
with app.app_context():
    v = SocialVersion.query.filter_by(post_id=p2id, platform="x").first()
    check(v.impressions == 1250 and v.likes == 30 and v.clicks == 55 and v.metrics_at, "results are saved")
r = partner.post(f"/social/{p2id}/version/x/results", data={"likes": "lots"}, follow_redirects=True)
check("whole numbers" in r.get_data(as_text=True), "results must be numbers")

# leads attributed to the post
with app.app_context():
    camp = SocialPost.query.get(p2id).campaign
    db.session.add_all([MarketingLead(first_name="A", email="a@x.co", consent_email=True, utm_source="linkedin", utm_medium="social", utm_campaign=camp, unsub_token="a" * 30),
                        MarketingLead(first_name="B", email="b@x.co", consent_email=True, utm_source="linkedin", utm_medium="social", utm_campaign=camp, unsub_token="b" * 30),
                        MarketingLead(first_name="C", email="c@x.co", consent_email=True, utm_source="x", utm_medium="social", utm_campaign=camp, unsub_token="c" * 30)]); db.session.commit()
res = partner.get("/social/results").get_data(as_text=True)
check("Dates nobody owns" in res and "1,250" in res, "the results page lists the post with its numbers")
with app.app_context():
    rows = SM._result_rows()
    byp = {r["version"].platform: r["leads"] for r in rows}
    check(byp.get("linkedin") == 2 and byp.get("x") == 1, "leads from the tracked links are counted per platform")
c = partner.get("/social/results.csv")
check(c.status_code == 200 and c.mimetype == "text/csv" and "Dates nobody owns" in c.get_data(as_text=True), "results download as CSV")
check("2 leads" in partner.get(f"/social/{p2id}").get_data(as_text=True), "the post page shows its leads")
check(staff.get("/social/results").status_code == 403 and staff.get("/social/results.csv").status_code == 403, "results are closed to staff")

# ---- the calendar and the list
page = partner.get("/social/").get_data(as_text=True)
check("Dates nobody owns" in page and "The next four weeks" in page and "Quarterly dates" in page, "the plan page shows the calendar and the list")
check("Dates nobody owns" in partner.get("/social/?status=Posted&platform=x").get_data(as_text=True) and "Quarterly dates" not in partner.get("/social/?status=Posted&platform=x").get_data(as_text=True).split("<h3 class=\"mt-0\">The next four weeks")[1].split("Search")[1], "filters work")
check("No posts match" in partner.get("/social/?q=zzzznothing").get_data(as_text=True), "a search with no match says so")

# ---- accounts
r = partner.post("/social/accounts", data={"handle_linkedin": "Neverlank", "url_linkedin": "https://linkedin.com/company/neverlank", "owner_linkedin": str(svid), "url_x": "javascript:x"}, follow_redirects=True)
with app.app_context():
    a = db.session.get(SocialAccount, "linkedin")
    check(a.handle == "Neverlank" and a.owner_id == svid and "must start with http" in r.get_data(as_text=True), "accounts are saved; a bad address is refused")

# ---- News Watch hand-off
with app.app_context():
    src = NewsSource(name="s", kind="feed", target="https://x.org/f", region="Zimbabwe")
    db.session.add(src); db.session.flush()
    it = NewsItem(source_id=src.id, title="ZIMRA extends VAT filing deadline", url="https://x.org/1", publisher="The Herald", summary="Filing moved to the 25th.", why="Clients must refile.", status="relevant", urgency="Urgent", unconfirmed=False, fetched_at=dtm.datetime.utcnow())
    db.session.add(it); db.session.commit(); nid = it.id
page = partner.get(f"/social/new?news={nid}").get_data(as_text=True)
check("ZIMRA extends VAT filing deadline" in page and "reported by The Herald" in page and "not law" in page and f'value="{nid}"' in page, "a News Watch story pre-fills the brief as reported news, not law")
check(f"/social/new?news={nid}" in partner.get("/news-watch/").get_data(as_text=True), "News Watch offers 'Draft a post'")
check("/social/new?news=" not in staff.get("/news-watch/").get_data(as_text=True), "but not to people without the permission")
partner.post("/social/new", data={"title": "VAT", "brief": "b", "news_item_id": str(nid), "platforms": ["linkedin"], "mode": "draft"})
with app.app_context():
    check(SocialPost.query.filter_by(title="VAT").first().news_item_id == nid, "the post remembers its story")

# ---- WhatsApp
check(partner.get("/social/whatsapp").status_code == 200, "the WhatsApp page opens")
r = partner.post("/social/whatsapp", data={"name": "Tendai Moyo", "phone": "077 123 4567", "company": "Moyo Logistics", "list_name": "Clients"}, follow_redirects=True)
with app.app_context():
    c1 = WhatsAppContact.query.filter_by(phone="263771234567").first(); c1id = c1.id
    check(c1 and not c1.consent and "no messages can be sent" in r.get_data(as_text=True), "a person can be added, but without consent nothing can be sent")
r = partner.post("/social/whatsapp", data={"name": "Dup", "phone": "+263 77 123 4567"}, follow_redirects=True)
check("already in the list" in r.get_data(as_text=True), "the same number is not added twice")
r = partner.post("/social/whatsapp", data={"name": "X", "phone": "123"}, follow_redirects=True)
check("valid mobile" in r.get_data(as_text=True), "a number that is too short is refused")
r = partner.post("/social/whatsapp", data={"name": "Y", "phone": "0772223333", "consent": "1"}, follow_redirects=True)
with app.app_context():
    check(WhatsAppContact.query.filter_by(phone="263772223333").count() == 0 and "how they agreed" in r.get_data(as_text=True).lower(), "consent needs a recorded source")
page = partner.get(f"/social/whatsapp/{c1id}?template=calendar").get_data(as_text=True)
check("has not agreed to be messaged" in page and "wa.me" not in page, "no send link for someone without consent")
r = partner.post(f"/social/whatsapp/{c1id}/log", data={"direction": "out", "body": "hi"}, follow_redirects=True)
with app.app_context():
    check(WhatsAppMessage.query.count() == 0, "nothing can be logged as sent to them")
partner.post(f"/social/whatsapp/{c1id}/update", data={"name": "Tendai Moyo", "company": "Moyo Logistics", "list_name": "Clients", "consent": "1", "consent_source": "Is a client"})
with app.app_context():
    c1 = WhatsAppContact.query.get(c1id)
    check(c1.consent and c1.consent_source == "Is a client" and c1.consent_at, "consent is recorded with its source")
page = partner.get(f"/social/whatsapp/{c1id}?template=calendar").get_data(as_text=True)
check("https://wa.me/263771234567?text=Hello%20Tendai" in page and "Pat" in page and "Moyo%20Logistics%27s" in page and not re.search(r"\{(first|sender|year|topic)", page), "the calendar message is filled in with a send link and no placeholders")
page = partner.get(f"/social/whatsapp/{c1id}?template=reminder").get_data(as_text=True)
check("Type what the message is about" in page and "wa.me" not in page, "a message that needs a topic asks for it")
page = partner.get(f"/social/whatsapp/{c1id}?template=reminder&topic=PAYE%20is%20due%20on%20the%2010th").get_data(as_text=True)
check("PAYE is due on the 10th" in page and "wa.me/263771234567" in page, "with a topic the link appears")
partner.post(f"/social/whatsapp/{c1id}/log", data={"direction": "out", "body": "Hello Tendai", "template": "calendar"})
partner.post(f"/social/whatsapp/{c1id}/log", data={"direction": "in", "body": "Thanks, got it"})
with app.app_context():
    check([m.direction for m in WhatsAppContact.query.get(c1id).messages] == ["out", "in"] and WhatsAppContact.query.get(c1id).last_message_at, "sent and received messages are logged")
partner.post(f"/social/whatsapp/{c1id}/log", data={"direction": "in", "body": "STOP"})
with app.app_context():
    check(WhatsAppContact.query.get(c1id).opted_out, "a reply that says STOP opts them out")
page = partner.get(f"/social/whatsapp/{c1id}?template=calendar").get_data(as_text=True)
check("opted out" in page.lower() and "wa.me/263771234567?text" not in page, "an opted-out person has no send link")
r = partner.post(f"/social/whatsapp/{c1id}/log", data={"direction": "out", "body": "hello again"})
with app.app_context():
    check(WhatsAppMessage.query.filter_by(contact_id=c1id, direction="out").count() == 1, "nor can a send be logged")
check(staff.get("/social/whatsapp").status_code == 403 and staff.post(f"/social/whatsapp/{c1id}/log", data={"body": "x"}).status_code == 403, "WhatsApp is closed to staff")

# import consenting leads
with app.app_context():
    db.session.add_all([MarketingLead(first_name="Rudo", last_name="Dube", company="Dube Co", email="rudo@d.co", mobile="0773334444", consent_email=True, consent_whatsapp=True, unsub_token="r" * 30, consent_at=dtm.datetime.utcnow()),
                        MarketingLead(first_name="No", last_name="Wa", email="n@d.co", mobile="0774445555", consent_email=True, consent_whatsapp=False, unsub_token="n" * 30)]); db.session.commit()
check("Add 1 lead who agreed" in partner.get("/social/whatsapp").get_data(as_text=True), "the page offers to add leads who agreed to WhatsApp")
partner.post("/social/whatsapp/import-leads")
with app.app_context():
    rudo = WhatsAppContact.query.filter_by(phone="263773334444").first()
    check(rudo and rudo.consent and rudo.list_name == "Leads" and rudo.consent_source == "Signed up on our page" and WhatsAppContact.query.filter_by(phone="263774445555").count() == 0, "only leads who agreed are added, with their consent source")
partner.post("/social/whatsapp/import-leads")
with app.app_context():
    check(WhatsAppContact.query.filter_by(phone="263773334444").count() == 1, "importing twice adds no one twice")

# broadcast
r = partner.post("/social/new", data={"title": "Status update", "link_url": "https://app.neverlank.co.zw/compliance-calendar", "platforms": ["whatsapp"], "mode": "draft"})
with app.app_context():
    wid = SocialPost.query.filter_by(title="Status update").first().id
partner.post(f"/social/{wid}/save", data={"title": "Status update", "link_url": "https://app.neverlank.co.zw/compliance-calendar", "body_whatsapp": "Our 2027 compliance calendar is free: {link}"})
check(partner.get(f"/social/whatsapp/broadcast/{wid}", follow_redirects=True).status_code == 200 and "Only an approved post" in partner.get(f"/social/whatsapp/broadcast/{wid}", follow_redirects=True).get_data(as_text=True), "an unapproved post cannot be broadcast")
partner.post(f"/social/{wid}/approve", data={f"check_{k}": "1" for k, _ in SM.CHECKLIST})
page = partner.get(f"/social/whatsapp/broadcast/{wid}").get_data(as_text=True)
check("Dube" in page.replace("Rudo", "Dube") and "Rudo Dube" in page and "Tendai" not in page and "utm_source%3Dwhatsapp" in page, "only people who agreed and have not opted out are listed, with tracked links")
with app.app_context():
    rid_ = WhatsAppContact.query.filter_by(phone="263773334444").first().id
body_sent = "Hello Rudo, Our 2027 compliance calendar is free: x"
partner.post(f"/social/whatsapp/{rid_}/log", data={"direction": "out", "body": body_sent, "post_id": str(wid), "next": f"/social/whatsapp/broadcast/{wid}"})
check("logged as sent" in partner.get(f"/social/whatsapp/broadcast/{wid}").get_data(as_text=True), "a logged send is shown as sent")

# ---- deleting
check(sup.post(f"/social/{p2id}/delete").status_code == 403, "a supervisor cannot delete someone else's approved post")
partner.post(f"/social/{p2id}/delete")
with app.app_context():
    check(SocialPost.query.get(p2id) is None and SocialVersion.query.filter_by(post_id=p2id).count() == 0, "a partner can delete a post with its versions")
partner.post(f"/social/whatsapp/{c1id}/delete")
with app.app_context():
    check(WhatsAppContact.query.get(c1id) is None and WhatsAppMessage.query.filter_by(contact_id=c1id).count() == 0, "a partner can delete a contact with their log")
check(sup.post(f"/social/whatsapp/{rid_}/delete").status_code == 403, "a supervisor cannot delete a contact")

print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
