"""Social media: date and time, and automatic posting through a scheduling service (simulated), the graphics' signed addresses, the reply and the timer."""
import os, sys, tempfile, io, json
from datetime import datetime, timedelta
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k in ("ANTHROPIC_API_KEY", "SOCIAL_WEBHOOK_URL", "SOCIAL_WEBHOOK_SECRET", "SOCIAL_CRON_TOKEN", "PUBLIC_BASE_URL"):
    os.environ.pop(k, None)
from PIL import Image
from app import create_app
from extensions import db
from models import *
import social as SM
import social_images as SI
import social_schedule as SS
import graphics as G
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

sent = []
mode = {"ok": True}
def fake_send(body):
    sent.append(body)
    return (True, "") if mode["ok"] else (False, "Make refused it (HTTP 500).")
SS._send = fake_send
SM.ai_available = lambda: False

with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    pu = User(username="part1", name="Pat Partner", email="p@x.co", role="partner"); pu.set_password("pw")
    st = User(username="staff1", name="Sam Staff", email="s@x.co", role="staff"); st.set_password("pw")
    db.session.add_all([pu, st]); db.session.commit(); pid, sid = pu.id, st.id
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
partner, staff = login(pid), login(sid)
anon = app.test_client()
ALL = {f"check_{k}": "1" for k, _ in SM.CHECKLIST}
BODY = {"linkedin": "Name one person for every tax date.\n\n{link}\n\nGeneral information, not advice.", "x": "Name one person per tax date. {link}", "facebook": "Who owns your tax dates? {link}",
        "instagram": "Who owns your tax dates?", "youtube": "Tax dates explained", "whatsapp": "Tax dates: {link}"}

def make_post(title, platforms=("linkedin", "x", "facebook", "instagram"), graphic=True, approve=True):
    partner.post("/social/new", data={"title": title, "brief": "Quarterly dates.", "platforms": list(platforms), "mode": "draft", "link_url": "https://app.neverlank.co.zw/compliance-calendar"})
    with app.app_context():
        pid_ = SocialPost.query.filter_by(title=title).first().id
    form = {"title": title, "link_url": "https://app.neverlank.co.zw/compliance-calendar"}
    for k in platforms:
        form[f"body_{k}"] = BODY[k]
        form[f"headline_{k}"] = "Tax dates explained"
        form[f"media_{k}"] = "A graphic"
    partner.post(f"/social/{pid_}/save", data=form)
    if graphic:
        partner.post(f"/social/{pid_}/graphics/new", data={"style": "tip", "theme": "navy", "size": "linkedin", "mode": "blank"})
        with app.app_context():
            g = SocialImage.query.filter_by(post_id=pid_).first()
        partner.post(f"/social/graphics/{g.id}", data={"label": "Tip", "style": "tip", "theme": "navy", "size": "linkedin", "title": "Who owns your tax dates?", "body": "Name one person per date.", "paab": "1"})
    if approve:
        partner.post(f"/social/{pid_}/submit"); partner.post(f"/social/{pid_}/approve", data=ALL)
    return pid_

def due(base):
    with app.app_context():
        return SS.process_due(base)

def failed():
    with app.app_context():
        return SS.failed_count()

def status(pid_):
    with app.app_context():
        return SocialPost.query.get(pid_).status

def when(hours):      # a Harare date and time `hours` from now, as the form wants them
    t = datetime.utcnow() + timedelta(hours=hours) + timedelta(hours=2)
    return {"date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M")}

# ---- not connected yet
p1 = make_post("Dates one")
check(status(p1) == "Approved", "an approved post to work with")
r = partner.post(f"/social/{p1}/schedule", data=dict(when(1), auto="1"), follow_redirects=True)
check("not connected" in r.get_data(as_text=True) or "not connected yet" in r.get_data(as_text=True), "scheduling without a connected service says nothing will be sent yet")
with app.app_context():
    s = SocialPost.query.get(p1).schedule
    check(s is not None and s.enabled and SocialPost.query.get(p1).planned_date is not None, "the date and time are still saved, and the calendar date follows")
    # 09:00 in Harare is 07:00 in UTC
r = partner.post(f"/social/{p1}/schedule", data={"date": "2031-03-05", "time": "09:00", "auto": "1"})
with app.app_context():
    check(SocialPost.query.get(p1).schedule.publish_at == datetime(2031, 3, 5, 7, 0) and SocialPost.query.get(p1).planned_date.isoformat() == "2031-03-05", "a time typed in Harare time is stored as UTC (09:00 is 07:00)")
page = partner.get(f"/social/{p1}").get_data(as_text=True)
check("05 Mar 2031, 09:00" in page and 'value="09:00"' in page and "Send automatically at this time" in page, "the post page shows it back in Harare time")
check(due("https://x") == {"sent": 0, "failed": 0, "held": 0} and not sent, "nothing is sent while the service is not connected")
check("Not connected" in partner.get("/social/publishing").get_data(as_text=True), "the Automatic posting page says it is not connected")
check(partner.post("/social/publishing/test", follow_redirects=True).status_code == 200 and not sent, "and the test button sends nothing")
check("not connected" in partner.post(f"/social/{p1}/send-now", follow_redirects=True).get_data(as_text=True).lower(), "Send now says so too")

# ---- connect the service
os.environ["SOCIAL_WEBHOOK_URL"] = "https://hook.eu1.make.com/abc"
os.environ["SOCIAL_WEBHOOK_SECRET"] = "s3cret-value"
os.environ["PUBLIC_BASE_URL"] = "https://app.neverlank.co.zw"
check(SS.configured() and "Ready" in partner.get("/social/publishing").get_data(as_text=True), "once the address and secret are set the page says Ready")
partner.post("/social/publishing/test")
check(sent and sent[-1]["event"] == "test" and sent[-1]["platform"] == "test", "the test sends a message marked as a test")
sent.clear()

# the time rules
check("already passed" in partner.post(f"/social/{p1}/schedule", data={"date": "2020-01-01", "time": "09:00", "auto": "1"}, follow_redirects=True).get_data(as_text=True), "an automatic time in the past is refused")
check("Choose a date" in partner.post(f"/social/{p1}/schedule", data={"date": "", "time": ""}, follow_redirects=True).get_data(as_text=True), "a missing date or time is refused")
partner.post(f"/social/{p1}/schedule", data=dict(when(-1)) | {"auto": ""})
with app.app_context():
    check(not SocialPost.query.get(p1).schedule.enabled, "a past time is allowed as a reminder when automatic sending is off")
check(due("https://x")["sent"] == 0, "a reminder is never sent")

# ---- sending at the time
partner.post(f"/social/{p1}/schedule", data=dict(when(1), auto="1"))
check(due("https://app.neverlank.co.zw") == {"sent": 0, "failed": 0, "held": 0}, "nothing is sent before the time")
with app.app_context():
    s = SocialPost.query.get(p1).schedule; s.publish_at = datetime.utcnow() - timedelta(minutes=5); db.session.commit()
res = due("https://app.neverlank.co.zw")
check(res["sent"] == 4 and res["failed"] == 0 and len(sent) == 4, "at the time, the four automatic platforms are sent")
by = {b["platform"]: b for b in sent}
check(set(by) == {"linkedin", "x", "facebook", "instagram"}, "one message per platform")
li = by["linkedin"]
check(li["event"] == "post" and "utm_source=linkedin" in li["text"] and "{link}" not in li["text"] and li["link"].startswith("https://app.neverlank.co.zw/compliance-calendar?"), "the wording carries that platform's tracked link")
check(li["publish_at"].endswith("+02:00") and len(li["publish_at"]) == 25, "the time is sent in Harare time with its offset")
check(len(li["images"]) == 1 and li["images"][0]["url"].startswith("https://app.neverlank.co.zw/social/media/") and li["images"][0]["width"] == 1200 and li["images"][0]["alt"].startswith("Neverlank graphic"), "the graphic is sent as a web address with its size and alt text")
check(li["callback_url"] == "https://app.neverlank.co.zw/social/callback?token=s3cret-value", "the reply address is included")
with app.app_context():
    post = SocialPost.query.get(p1)
    check(all(v.status == "posted" and v.dispatch.status == "sent" for v in post.versions) and post.status == "Posted", "the versions are marked posted, and so is the post")
    check(post.schedule.note == "", "no problem is recorded")
n = len(sent)
due("https://x")
check(len(sent) == n, "the same post is never sent twice")

# ---- only approved posts; the other platforms stay by hand
p2 = make_post("Dates two", platforms=("linkedin", "youtube", "whatsapp"), approve=False)
partner.post(f"/social/{p2}/schedule", data=dict(when(1), auto="1"))
with app.app_context():
    SocialPost.query.get(p2).schedule.publish_at = datetime.utcnow() - timedelta(minutes=1); db.session.commit()
sent.clear()
check(due("https://x")["sent"] == 0 and not sent, "a post that is not approved is not sent, even when its time has come")
partner.post(f"/social/{p2}/submit"); partner.post(f"/social/{p2}/approve", data=ALL)
check(status(p2) == "Approved", "(approved now)")
due("https://x")
check([b["platform"] for b in sent] == ["linkedin"], "only LinkedIn goes automatically: YouTube and WhatsApp stay by hand")
check(status(p2) == "Approved", "so the post stays Approved until the rest is posted by hand")
check("Posted by hand" not in partner.get(f"/social/{p2}").get_data(as_text=True) and "sent to the scheduling service" in partner.get(f"/social/{p2}").get_data(as_text=True), "the post page shows what was sent")

# ---- late posts are held
p3 = make_post("Dates three")
partner.post(f"/social/{p3}/schedule", data=dict(when(1), auto="1"))
with app.app_context():
    SocialPost.query.get(p3).schedule.publish_at = datetime.utcnow() - timedelta(hours=7); db.session.commit()
sent.clear()
res = due("https://x")
with app.app_context():
    check(res["held"] == 1 and not sent and "Held" in SocialPost.query.get(p3).schedule.note, "a post more than six hours late is held, with a note, not sent")
page = partner.get(f"/social/{p3}").get_data(as_text=True)
check("Held" in page and "Send now" in page, "the post page shows why, and offers Send now")
r = partner.post(f"/social/{p3}/send-now", follow_redirects=True)
check(len(sent) == 4 and "Sent 4" in r.get_data(as_text=True) and status(p3) == "Posted", "Send now sends it after a person decides to")

# ---- a problem at the time of sending
p4 = make_post("Dates four")
partner.post(f"/social/{p4}/schedule", data=dict(when(1), auto="1"))
with app.app_context():
    v = SocialVersion.query.filter_by(post_id=p4, platform="linkedin").first(); v.body = "Call [NAME] about this. {link}\n\nGeneral information, not advice."
    SocialPost.query.get(p4).schedule.publish_at = datetime.utcnow() - timedelta(minutes=1); db.session.commit()
sent.clear()
res = due("https://x")
with app.app_context():
    check(not sent and "problem" in SocialPost.query.get(p4).schedule.note.lower(), "if the checks find an error at the time, nothing is sent and the reason is shown")

# ---- editing cancels approval, so nothing unchecked goes out
p5 = make_post("Dates five")
partner.post(f"/social/{p5}/schedule", data=dict(when(1), auto="1"))
form = {"title": "Dates five", "link_url": "https://app.neverlank.co.zw/compliance-calendar"}
for k in ("linkedin", "x", "facebook", "instagram"):
    form[f"body_{k}"] = BODY[k] + " More."; form[f"headline_{k}"] = "t"; form[f"media_{k}"] = "A graphic"
partner.post(f"/social/{p5}/save", data=form)
with app.app_context():
    SocialPost.query.get(p5).schedule.publish_at = datetime.utcnow() - timedelta(minutes=1); db.session.commit()
sent.clear()
check(status(p5) == "Draft" and due("https://x")["sent"] == 0 and not sent, "changing the wording after approval stops the automatic send until it is approved again")
partner.post(f"/social/{p5}/submit"); partner.post(f"/social/{p5}/approve", data=ALL)
check(due("https://x")["sent"] == 4, "approved again, it goes out (it is only minutes late)")

# ---- failures and tries
p6 = make_post("Dates six", platforms=("linkedin",))
partner.post(f"/social/{p6}/schedule", data=dict(when(1), auto="1"))
with app.app_context():
    SocialPost.query.get(p6).schedule.publish_at = datetime.utcnow() - timedelta(minutes=1); db.session.commit()
mode["ok"] = False; sent.clear()
for i in range(5): due("https://x")
with app.app_context():
    d = SocialVersion.query.filter_by(post_id=p6).first().dispatch
    check(len(sent) == 3 and d.status == "failed" and d.attempts == 3 and "HTTP 500" in d.error and status(p6) == "Approved", "a failed send is tried three times and then left, still approved")
check(failed() == 1 and "1 automatic send failed" in partner.get("/social/").get_data(as_text=True), "the Plan page warns about it")
page = partner.get(f"/social/{p6}").get_data(as_text=True)
check("not sent (try 3 of 3)" in page and "HTTP 500" in page, "the post shows the reason")
check("HTTP 500" in partner.get("/social/publishing").get_data(as_text=True), "and so does the Automatic posting page")
mode["ok"] = True; sent.clear()
partner.post(f"/social/{p6}/send-now")
check(len(sent) == 1 and status(p6) == "Posted" and failed() == 0, "Send now tries again from the start and succeeds")

# Instagram without a picture
p7 = make_post("Dates seven", platforms=("instagram",), graphic=False)
partner.post(f"/social/{p7}/schedule", data=dict(when(1), auto="1"))
with app.app_context():
    SocialPost.query.get(p7).schedule.publish_at = datetime.utcnow() - timedelta(minutes=1); db.session.commit()
sent.clear()
if status(p7) == "Approved":
    due("https://x")
    with app.app_context():
        d = SocialVersion.query.filter_by(post_id=p7).first().dispatch
        check(not sent and d.status == "failed" and "needs a picture" in d.error, "Instagram with no graphic is not sent, and says why")
else:
    check(True, "(Instagram with no picture cannot even be approved)")

# an accidental re-open does not post it twice
partner.post(f"/social/{p5}/version/linkedin/reopen")
sent.clear(); due("https://x")
check(not [b for b in sent if b["post_id"] == p5 and b["platform"] == "linkedin"], "marking a platform as not posted does not send it a second time")

# ---- the graphics' addresses
with app.app_context():
    g = SocialImage.query.filter_by(post_id=p1).first(); gid = g.id; url = SS.media_url(g, 1, "")
r = anon.get(url)
check(r.status_code == 200 and r.mimetype == "image/png" and Image.open(io.BytesIO(r.data)).size == (1200, 627), "the signed address gives the picture without signing in")
check(anon.get(f"/social/media/{gid}/1.png?t=wrong").status_code == 404 and anon.get(f"/social/media/{gid}/1.png").status_code == 404, "a wrong or missing signature is refused")
check(anon.get(url.replace("/1.png", "/2.png")).status_code == 404, "so is another slide's address with this signature")
with app.app_context():
    g2 = SocialImage.query.filter_by(post_id=p4).first(); url4 = SS.media_url(g2, 1, "")
partner.post(f"/social/{p4}/reject", data={"review_note": "x"})
check(anon.get(url4).status_code == 404, "a graphic of a post that is not approved is not served")
partner.post(f"/social/graphics/{gid}", data={"label": "Tip", "style": "tip", "theme": "navy", "size": "linkedin", "title": "Changed headline", "body": "x", "paab": "1"})
check(anon.get(url).status_code == 404, "an address stops working when the graphic is changed")

# ---- the reply from the service
check(anon.post("/social/callback", json={"post_id": p1, "platform": "linkedin"}).status_code == 404 and anon.post("/social/callback?token=bad", json={}).status_code == 404, "the reply address needs the secret")
r = anon.post("/social/callback?token=s3cret-value", json={"post_id": p6, "platform": "linkedin", "url": "https://www.linkedin.com/feed/update/123"})
with app.app_context():
    check(r.status_code == 200 and SocialVersion.query.filter_by(post_id=p6).first().post_url == "https://www.linkedin.com/feed/update/123", "the service can report the live address")
r = anon.post("/social/callback?token=s3cret-value", json={"post_id": p6, "platform": "linkedin", "ok": False, "error": "Token expired"})
with app.app_context():
    v = SocialVersion.query.filter_by(post_id=p6).first()
    check(v.status == "pending" and v.dispatch.status == "failed" and "Token expired" in v.dispatch.error and status(p6) == "Approved", "or that it failed, so the post is not shown as posted")
check(anon.post("/social/callback?token=s3cret-value", json={"post_id": 99999, "platform": "x"}).status_code == 404, "an unknown post is refused")

# ---- the timer
check(anon.get("/social/cron").status_code == 404 and anon.get("/social/cron?token=").status_code == 404, "the timer address is closed until a token is set")
os.environ["SOCIAL_CRON_TOKEN"] = "tok123"
check(anon.get("/social/cron?token=nope").status_code == 404, "a wrong token is refused")
r = anon.get("/social/cron?token=tok123")
check(r.status_code == 200 and r.json["ok"] and r.json["configured"] and set(r.json) >= {"sent", "failed", "held"}, "the right token runs the sending")

# ---- opening the Social pages sends what is due
p8 = make_post("Dates eight", platforms=("x",))
partner.post(f"/social/{p8}/schedule", data=dict(when(1), auto="1"))
with app.app_context():
    SocialPost.query.get(p8).schedule.publish_at = datetime.utcnow() - timedelta(minutes=1); db.session.commit()
sent.clear()
partner.get("/social/")
check(len(sent) == 1 and sent[0]["platform"] == "x" and status(p8) == "Posted", "opening the Plan page sends what is due, when there is no timer")

# ---- permissions and removing
check(staff.post(f"/social/{p1}/schedule", data=when(1)).status_code == 403 and staff.post(f"/social/{p1}/send-now").status_code == 403 and staff.get("/social/publishing").status_code == 403 and staff.post("/social/publishing/test").status_code == 403, "staff cannot schedule or send")
p9 = make_post("Dates nine", approve=False)
partner.post(f"/social/{p9}/schedule", data=dict(when(5), auto="1"))
partner.post(f"/social/{p9}/schedule", data={"clear": "1"})
with app.app_context():
    check(SocialPost.query.get(p9).schedule is None, "a schedule can be removed")
partner.post(f"/social/{p9}/schedule", data=dict(when(5), auto="1"))
partner.post(f"/social/{p9}/delete")
with app.app_context():
    check(SocialSchedule.query.filter_by(post_id=p9).count() == 0, "deleting a post deletes its schedule")

# ---- graphics for existing posts
p10 = make_post("Old post", graphic=False, approve=False)
SM.ai_available = lambda: True
SI._call_design = lambda s, m: ({"kicker": "Tip", "title": "Who owns your tax dates?", "body": "Name one person per date.", "check_these": []}, None)
page = partner.get("/social/").get_data(as_text=True)
check("Posts without a graphic" in page and "Old post" in page and "Make graphic" in page, "the Plan page lists posts without a graphic, with a one-click button")
r = partner.post(f"/social/{p10}/graphics/new", data={"style": "tip", "mode": "ai"}, follow_redirects=True)
with app.app_context():
    gi = SocialImage.query.filter_by(post_id=p10).first()
    check(gi and gi.ai_used and gi.spec["title"] == "Who owns your tax dates?", "one click makes it from the post's own wording")
check("Old post" not in partner.get("/social/").get_data(as_text=True).split("Posts without a graphic")[-1].split("The next four weeks")[0] if "Posts without a graphic" in partner.get("/social/").get_data(as_text=True) else True, "and it leaves the list")

# ---- WhatsApp Status kit and "due now, post by hand"
pw = make_post("Status post", platforms=("linkedin", "whatsapp"), graphic=False)
page = partner.get(f"/social/{pw}").get_data(as_text=True)
check("WhatsApp Status" in page and "Make the Status picture" in page and 'name="size" value="story"' in page, "a post with a WhatsApp wording offers to make the full-screen Status picture")
check('id="wa-status"' not in partner.get(f"/social/{p1}").get_data(as_text=True), "a post without WhatsApp does not")
SI._call_design = lambda s, m: ({"kicker": "Tax tip", "title": "Who owns your tax dates?", "body": "Name one person per date.", "check_these": []}, None)
partner.post(f"/social/{pw}/graphics/new", data={"style": "tip", "theme": "navy", "size": "story", "mode": "ai"})
with app.app_context():
    gs = SocialImage.query.filter_by(post_id=pw).first(); gsid = gs.id
    check(gs.spec["size"] == "story" and gs.spec["title"] == "Who owns your tax dates?", "the Status picture is made at 1080 x 1920 from the post's wording")
page = partner.get(f"/social/{pw}").get_data(as_text=True)
check("Share to WhatsApp Status" in page and "Copy caption" in page and "Tax dates: https://app.neverlank.co.zw" in page and f"/social/graphics/{gsid}/1.png" in page, "then the card shows the picture, the caption (with the tracked link) and the share button")
r = partner.get(f"/social/graphics/{gsid}/1.png")
check(Image.open(io.BytesIO(r.data)).size == (1080, 1920), "the picture is full screen")
# due now: post by hand
partner.post(f"/social/{pw}/submit"); partner.post(f"/social/{pw}/approve", data=ALL)
check(status(pw) == "Approved", "(approved)")
partner.post(f"/social/{pw}/schedule", data=dict(when(1), auto="1"))
def due_card():
    pg = partner.get("/social/").get_data(as_text=True)
    return pg.split("Due now: post by hand")[1].split("</div>\n")[0] if "Due now: post by hand" in pg else ""
check("Status post" not in due_card(), "before the time it is not listed as due")
with app.app_context():
    SocialPost.query.get(pw).schedule.publish_at = datetime.utcnow() - timedelta(minutes=3); db.session.commit()
sent.clear(); due("https://x")
page = partner.get("/social/").get_data(as_text=True)
check([b["platform"] for b in sent] == ["linkedin"] and "Status post" in due_card() and "WhatsApp Status kit" in due_card(), "LinkedIn goes automatically; WhatsApp is listed on the Plan page as due, to post by hand")
r = partner.post(f"/social/{pw}/version/whatsapp/posted", data={"post_url": ""}, follow_redirects=True)
check(status(pw) == "Posted" and "Marked as posted on WhatsApp" in r.get_data(as_text=True), "marked posted with no address (a Status has none), the post is complete")
check("Status post" not in due_card(), "and it leaves the due list")

print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
