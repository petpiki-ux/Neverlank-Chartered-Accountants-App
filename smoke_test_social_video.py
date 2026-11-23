"""Social media: short videos (words, scenes, background making of the MP4, checks, approval, public signed address, automatic sending to YouTube/TikTok),
the per-platform picture sizes, and the "what should change?" revisions."""
import os, sys, tempfile, io, json, time, zipfile, subprocess
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
import social_video as SV
import video as V
import graphics as G
V.FPS = 4                                   # fewer frames: the tests only need a real, valid file
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

sent = []
SS._send = lambda body: (sent.append(body) or (True, ""))
SM.ai_available = lambda: False

with app.app_context():
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

def make_post(title, platforms=("tiktok", "youtube", "linkedin")):
    partner.post("/social/new", data={"title": title, "brief": "Quarterly provisional tax dates.", "platforms": list(platforms), "mode": "draft"})
    with app.app_context():
        pid_ = SocialPost.query.filter_by(title=title).first().id
    form = {"title": title, "brief": "Quarterly provisional tax dates."}
    for k in platforms:
        form[f"body_{k}"] = f"Who owns your tax dates? ({k}) General information, not advice."
        form[f"headline_{k}"] = "Tax dates explained"
        form[f"media_{k}"] = "A video"
    partner.post(f"/social/{pid_}/save", data=form)
    return pid_

def get_video(post_id):
    with app.app_context():
        v = SocialVideo.query.filter_by(post_id=post_id).order_by(SocialVideo.id.desc()).first()
        return v.id if v else None

def vstate(vid):
    with app.app_context():
        v = SocialVideo.query.get(vid)
        return SV.display_status(v), v.error, v.file_name, v.seconds

def wait_ready(vid, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        st_, err, *_ = vstate(vid)
        if st_ != "rendering":
            return st_, err
        time.sleep(0.3)
    return "rendering", ""

WORDS = {"theme": "navy", "kicker": "TAX TIP", "hook": "Who owns your tax dates?", "scene_title_1": "Name one person", "scene_body_1": "Every date has an owner, not a team.",
         "scene_title_2": "Put it in a calendar", "scene_body_2": "Reminders a week before each date.", "scene_title_3": "Check it each quarter", "scene_body_3": "Ten minutes, same day each quarter.",
         "cta": "Talk to us", "caption": "Link in our profile", "footer": "neverlank.co.zw", "paab": "1", "label": "Tax dates"}

p1 = make_post("Tax dates video")

# ---- permissions
check(staff.post(f"/social/{p1}/videos/new", data={"mode": "blank", "theme": "navy"}).status_code == 403, "staff without the permission cannot add a video")
check(anon.post(f"/social/{p1}/videos/new", data={"mode": "blank"}).status_code in (302, 401), "a visitor cannot add a video")

# ---- add blank, edit
r = partner.post(f"/social/{p1}/videos/new", data={"mode": "blank", "theme": "navy"})
vid = get_video(p1)
check(r.status_code == 302 and vid, "a blank video is added")
page = partner.get(f"/social/videos/{vid}").get_data(as_text=True)
check("Short video for" in page and "scene_title_1" in page and "Make the video" in page, "the video page opens with the form")
check("disabled" in page, "the make button is disabled while the words are missing")
r = partner.post(f"/social/{p1}/videos/new", data={"mode": "ai", "theme": "navy"}, follow_redirects=True)
check("not switched on" in r.get_data(as_text=True), "AI mode without a key says so and still adds a blank video")
with app.app_context():
    for v in SocialVideo.query.filter_by(post_id=p1).all():
        if v.id != vid: db.session.delete(v)
    db.session.commit()

check(partner.get(f"/social/{p1}").get_data(as_text=True).count("Add a video, words by AI") == 1, "the post page has the Short videos card")
with app.app_context():
    texts = [i["text"] for i in SM.scan(SocialPost.query.get(p1))]
check(any("Video" in t and "at least 2 scenes" in t for t in texts), "a video with fewer than two scenes is an error in the checks")
r = partner.post(f"/social/videos/{vid}", data=WORDS, follow_redirects=True)
check("Saved." in r.get_data(as_text=True), "the words are saved")
with app.app_context():
    v = SocialVideo.query.get(vid)
    spec = v.spec
    check(spec["hook"] == "Who owns your tax dates?" and len(spec["scenes"]) == 3 and v.label == "Tax dates", "the spec holds the hook and three scenes")
    check(V.problems(spec) == [], "the words have no problems")
    check(20 <= V.total_seconds(spec) <= 45, f"the length is a sensible {V.total_seconds(spec)} seconds")
    texts = [i for i in SM.scan(SocialPost.query.get(p1)) if "Video" in i["text"]]
check(any("has not been made" in i["text"] and i["level"] == "warn" for i in texts), "an unmade video is a warning, not an error")
check(not any(i["level"] == "error" for i in texts), "and no video error is left")

# ---- scene stills
r = partner.get(f"/social/videos/{vid}/scene/1.png")
im = Image.open(io.BytesIO(r.data))
check(r.status_code == 200 and im.size == (1080, 1920), "scene 1 is a 1080 x 1920 picture")
check(partner.get(f"/social/videos/{vid}/scene/99.png").status_code == 404, "a scene that does not exist is 404")
check(staff.get(f"/social/videos/{vid}/scene/1.png").status_code == 403, "staff cannot see scenes")

# ---- placeholders and sensitive wording
bad = dict(WORDS, scene_title_2="Ask [client name] about it", scene_body_3="We guarantee 100% savings by 31 March.")
partner.post(f"/social/videos/{vid}", data=bad)
with app.app_context():
    items = [i for i in SM.scan(SocialPost.query.get(p1)) if "Video" in i["text"]]
check(any(i["level"] == "error" and "placeholder" in i["text"] for i in items), "a [bracket] placeholder in a video is an error")
check(any(i["level"] == "warn" and "promise" in i["text"] for i in items), "promise wording in a video is flagged")
check(any(i["level"] == "warn" and "date" in i["text"] for i in items), "a date in a video is flagged")
check(any(i["level"] == "warn" and "figure" in i["text"] for i in items), "a figure in a video is flagged")
partner.post(f"/social/videos/{vid}", data=WORDS)

# ---- limits
long = dict(WORDS, hook="x" * 500, scene_title_1="y" * 500)
partner.post(f"/social/videos/{vid}", data=long)
with app.app_context():
    sp = SocialVideo.query.get(vid).spec
    check(len(sp["hook"]) <= V.LIMITS["hook"] and len(sp["scenes"][0]["title"]) <= V.SCENE_LIMITS["title"], "text is cut to the limits")
partner.post(f"/social/videos/{vid}", data=WORDS)

# ---- make the video
r = partner.post(f"/social/videos/{vid}/make", follow_redirects=True)
st_, err = wait_ready(vid)
check(st_ == "ready", f"the video is made in the background ({st_} {err})")
with app.app_context():
    v = SocialVideo.query.get(vid)
    path = SV.file_path(v)
    check(os.path.exists(path) and os.path.getsize(path) > 5000, "the MP4 is on disk")
    check(v.seconds and v.seconds > 15 and v.rendered_at, "length and time made are recorded")
    exe = V.ffmpeg_path()
    info = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    check("h264" in info and "1080x1920" in info and "aac" in info, "it is H.264 1080 x 1920 with a sound track")
    check(not [f for f in os.listdir(SV.video_dir()) if f.endswith(".mp4") and f != v.file_name], "no half-made file is left behind")
check("controls" in partner.get(f"/social/videos/{vid}").get_data(as_text=True) and "Download the video" in partner.get(f"/social/videos/{vid}").get_data(as_text=True), "the page shows the player and the download")
r = partner.get(f"/social/videos/{vid}.mp4")
check(r.status_code == 200 and r.mimetype == "video/mp4" and r.data[4:8] == b"ftyp", "the file is served as an MP4")
r2 = partner.get(f"/social/videos/{vid}.mp4", headers={"Range": "bytes=0-99"})
check(r2.status_code == 206 and len(r2.data) == 100, "range requests work (needed for playback)")
check("attachment" in partner.get(f"/social/videos/{vid}.mp4?download=1").headers.get("Content-Disposition", ""), "download=1 saves as a file")
check(staff.get(f"/social/videos/{vid}.mp4").status_code == 403 and anon.get(f"/social/videos/{vid}.mp4").status_code in (302, 401), "only people with the permission can open it")
js = partner.get(f"/social/videos/{vid}/status.json").get_json()
check(js["status"] == "ready" and js["progress"] == 1.0, "the status check says ready")
with app.app_context():
    items = [i for i in SM.scan(SocialPost.query.get(p1)) if "Video" in i["text"]]
check(not any("has not been made" in i["text"] for i in items), "once made, the 'not made' warning goes")

# ---- changing words makes it out of date, and sends an approved post back
partner.post(f"/social/{p1}/submit"); partner.post(f"/social/{p1}/approve", data=ALL)
with app.app_context():
    check(SocialPost.query.get(p1).status == "Approved", "the post can be approved with a made video")
partner.post(f"/social/videos/{vid}", data=dict(WORDS, cta="Book a call"), follow_redirects=True)
with app.app_context():
    check(SocialPost.query.get(p1).status == "Draft", "changing the words of a video sends an approved post back to Draft")
    check(SV.display_status(SocialVideo.query.get(vid)) == "stale", "and the file is marked out of date")
    check(SV.video_for(SocialPost.query.get(p1)) is None, "an out-of-date video is not used for sending")
partner.post(f"/social/videos/{vid}", data=dict(WORDS, cta="Book a call", label="Renamed"))
with app.app_context():
    check(SocialVideo.query.get(vid).label == "Renamed", "renaming works")
partner.post(f"/social/{p1}/submit"); partner.post(f"/social/{p1}/approve", data=ALL)
partner.post(f"/social/videos/{vid}", data=dict(WORDS, cta="Book a call", label="Another name"))
with app.app_context():
    check(SocialPost.query.get(p1).status == "Approved", "renaming alone does not send it back")

# ---- remake
partner.post(f"/social/videos/{vid}/make")
check(wait_ready(vid)[0] == "ready", "it is made again after the change")
with app.app_context():
    v = SocialVideo.query.get(vid)
    check(SV.is_current(v) and len([f for f in os.listdir(SV.video_dir()) if f.endswith(".mp4")]) == 1, "the old file was removed and the new one is current")

# ---- one at a time, and stale rendering
with app.app_context():
    SV._job_lock.acquire()
    try:
        v = SocialVideo.query.get(vid)
        started, msg = SV.start_render(v)
    finally:
        SV._job_lock.release()
check(not started and "Another video" in msg, "a second video cannot be made at the same time")
with app.app_context():
    v = SocialVideo.query.get(vid)
    v.status, v.updated_at = "rendering", datetime.utcnow() - timedelta(minutes=30)
    db.session.commit()
    check(SV.display_status(v) == "failed", "a 'being made' that is 30 minutes old is shown as failed (the server restarted)")
    v.status = "ready"; db.session.commit()

# ---- the public signed address
with app.app_context():
    v = SocialVideo.query.get(vid)
    url = SV.vmedia_url(v, "https://app.neverlank.co.zw")
check(url.startswith("https://app.neverlank.co.zw/social/vmedia/") and "?t=" in url, "the public address has a signature")
path = url.replace("https://app.neverlank.co.zw", "")
r = anon.get(path)
check(r.status_code == 200 and r.mimetype == "video/mp4", "the scheduling service can fetch it without signing in (approved post)")
check(anon.get(f"/social/vmedia/{vid}.mp4?t=wrong").status_code == 404 and anon.get(f"/social/vmedia/{vid}.mp4").status_code == 404, "a wrong or missing signature is 404")
partner.post(f"/social/{p1}/reject", data={"note": "check"})
with app.app_context():
    st_now = SocialPost.query.get(p1).status
check(anon.get(path).status_code == 404 or st_now in ("Approved", "Posted"), "a post that is not approved does not serve its video")
partner.post(f"/social/{p1}/submit"); partner.post(f"/social/{p1}/approve", data=ALL)

# ---- automatic sending
os.environ["SOCIAL_WEBHOOK_URL"] = "https://hook.eu1.make.com/abc"
os.environ["SOCIAL_WEBHOOK_SECRET"] = "s3cret-value"
os.environ["PUBLIC_BASE_URL"] = "https://app.neverlank.co.zw"
with app.app_context():
    post = SocialPost.query.get(p1)
    check(post.status == "Approved", "the post is approved to send")
    names = {v.platform: SS.is_auto(post, v) for v in post.versions}
check(names["tiktok"] and names["youtube"] and names["linkedin"], "with a finished video, TikTok and YouTube go out automatically")
with app.app_context():
    r_ = SS.send_post(SocialPost.query.get(p1), "https://app.neverlank.co.zw")
    db.session.commit()
bodies = {b["platform"]: b for b in sent}
check(set(bodies) == {"tiktok", "youtube", "linkedin"}, f"all three platforms were sent ({sorted(bodies)})")
vb = bodies["tiktok"]["video"]
check(vb and vb["url"].startswith("https://app.neverlank.co.zw/social/vmedia/") and vb["width"] == 1080 and vb["height"] == 1920 and vb["seconds"] > 15, "the TikTok message carries the video address, size and length")
check(bodies["youtube"]["video"] and bodies["youtube"]["images"] == [] and bodies["linkedin"]["video"] is None, "YouTube gets the video, LinkedIn none")
check(anon.get(vb["url"].replace("https://app.neverlank.co.zw", "")).status_code == 200, "the address in the message really works")
with app.app_context():
    check({v.platform: v.status for v in SocialPost.query.get(p1).versions} == {"tiktok": "posted", "youtube": "posted", "linkedin": "posted"}, "each is marked posted once sent")

# a post with no video: TikTok and YouTube stay by hand
sent.clear()
p2 = make_post("No video post")
partner.post(f"/social/{p2}/submit"); partner.post(f"/social/{p2}/approve", data=ALL)
with app.app_context():
    post = SocialPost.query.get(p2)
    check({v.platform: SS.is_auto(post, v) for v in post.versions} == {"tiktok": False, "youtube": False, "linkedin": True}, "without a video they stay by hand")
    SS.send_post(post, "https://app.neverlank.co.zw"); db.session.commit()
check([b["platform"] for b in sent] == ["linkedin"], "only LinkedIn was sent")

# ---- delete
r = partner.post(f"/social/videos/{vid}/delete", follow_redirects=True)
with app.app_context():
    check(SocialVideo.query.get(vid) is None, "the video is deleted")
    check(not [f for f in os.listdir(SV.video_dir()) if f.endswith(".mp4")], "and its file is removed from the disk")
check(anon.get(path).status_code == 404, "its public address stops working")

# ---- AI words
def fake_video(system, message):
    fake_video.last = message
    return {"kicker": "TAX TIP", "hook": "Who owns your tax dates?", "scenes": [{"title": "Name one person", "body": "Every date has an owner."}, {"title": "Use one calendar", "body": "Share it with the team."}],
            "cta": "Talk to us", "caption": "Link in our profile", "check_these": ["Quarterly dates", "Owner named"]}, None
SV._call_video = fake_video
SM.ai_available = lambda: True
p3 = make_post("AI video post")
partner.post(f"/social/{p3}/videos/new", data={"mode": "ai", "theme": "ivory", "instruction": "keep it short"}, follow_redirects=True)
v3 = get_video(p3)
with app.app_context():
    v = SocialVideo.query.get(v3)
    check(v.ai_used and v.spec["hook"] == "Who owns your tax dates?" and len(v.spec["scenes"]) == 2 and v.spec["theme"] == "ivory", "the AI's words fill the video and the chosen colours are kept")
    check("WORDING FOR" in fake_video.last and "keep it short" in fake_video.last and "REQUESTED CHANGE" in fake_video.last, "the AI is given the post wording and the requested change")
    check("Quarterly dates" in (SocialPost.query.get(p3).ai_flags or ""), "the 'check these' items are kept on the post")
check("never invent" in SV.VIDEO_SYSTEM.lower(), "the instructions forbid inventing facts")
check("Never name a client" in SV.VIDEO_SYSTEM and "Never promise" in SV.VIDEO_SYSTEM, "the instructions forbid naming clients and promises")
SV._call_video = lambda s, m: (None, "The AI service is busy.")
r = partner.post(f"/social/videos/{v3}/redraft", data={"instruction": "shorter"}, follow_redirects=True)
check("could not write the words" in r.get_data(as_text=True) and "Who owns your tax dates?" in r.get_data(as_text=True), "if the AI fails the words stay as they were")
SV._call_video = lambda s, m: ({"hook": "", "scenes": [], "check_these": []}, None)
r = partner.post(f"/social/videos/{v3}/redraft", data={}, follow_redirects=True)
check("could not write the words" in r.get_data(as_text=True), "an empty AI answer is refused")
SV._call_video = fake_video
partner.post(f"/social/{p3}/videos/new", data={"mode": "ai", "theme": "navy"})
with app.app_context():
    n_videos = SocialVideo.query.filter_by(post_id=p3).count()
SM.ai_available = lambda: False

# ---- graphic revision instruction and post revision instruction reach the AI
seen = {}
def fake_design(system, message):
    seen["design"] = message
    return {"title": "Who owns your tax dates?", "body": "Name one person.", "check_these": []}, None
SI._call_design = fake_design
SM.ai_available = lambda: True
partner.post(f"/social/{p3}/graphics/new", data={"style": "tip", "theme": "navy", "size": "linkedin", "mode": "blank"})
with app.app_context():
    g3 = SocialImage.query.filter_by(post_id=p3).first().id
partner.post(f"/social/graphics/{g3}/redraft", data={"instruction": "make it bolder"})
check("make it bolder" in seen.get("design", ""), "the graphic's 'what should change' reaches the AI")
def fake_ai(system, message):
    seen["post"] = message
    return {"posts": [{"platform": "linkedin", "body": "Shorter wording. {link}"}], "check_these": []}, None
SM._call_ai = fake_ai
r = partner.post(f"/social/{p3}/redraft", data={"instruction": "open with a question", "platforms": ["linkedin"]}, follow_redirects=True)
check("open with a question" in seen.get("post", "") and "CURRENT WORDING FOR" in seen.get("post", ""), "the post's 'what should change' reaches the AI with the current wording")
SM.ai_available = lambda: False

# ---- per-platform sizes
partner.post(f"/social/{p2}/graphics/new", data={"style": "tip", "theme": "navy", "size": "linkedin", "mode": "blank"})
with app.app_context():
    g2 = SocialImage.query.filter_by(post_id=p2).first().id
partner.post(f"/social/graphics/{g2}", data={"label": "Tip", "style": "tip", "theme": "navy", "size": "linkedin", "title": "Who owns your tax dates?", "body": "Name one person per date.", "paab": "1"})
for size, wh in (("story", (1080, 1920)), ("instagram_portrait", (1080, 1350)), ("x", (1600, 900))):
    r = partner.get(f"/social/graphics/{g2}/1.png?size={size}")
    check(r.status_code == 200 and Image.open(io.BytesIO(r.data)).size == wh, f"the same graphic is drawn at {size} ({wh[0]} x {wh[1]})")
check(partner.get(f"/social/graphics/{g2}/1.png?size=bogus").status_code in (200, 400, 404), "an unknown size does not crash")
r = partner.get(f"/social/graphics/{g2}/sizes.zip")
z = zipfile.ZipFile(io.BytesIO(r.data))
names = z.namelist()
check(r.status_code == 200 and len(names) >= len(G.SIZES) and all(n.endswith(".png") for n in names), f"the 'every size' zip holds a picture per size ({len(names)})")
sizes_ok = all(Image.open(io.BytesIO(z.read(n))).size[0] > 0 for n in names)
check(sizes_ok, "and every picture in it opens")
check(staff.get(f"/social/graphics/{g2}/sizes.zip").status_code == 403, "staff cannot download it")
SM.ai_available = lambda: True
page = partner.get(f"/social/{p2}").get_data(as_text=True)
SM.ai_available = lambda: False
check("Download at" in page and "size=story" in page and "size=linkedin" in page, "each platform card has its own picture at its own size to download")
check("every platform size (zip)" in page, "the Graphics card links to the every-size zip")
check("What should change?" in page, "the post page has the 'what should change' box")
check("What should change?" in partner.get(f"/social/graphics/{g2}").get_data(as_text=True) and "every platform size" in partner.get(f"/social/graphics/{g2}").get_data(as_text=True), "the graphic page has the 'what should change' box and the every-size link")

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
