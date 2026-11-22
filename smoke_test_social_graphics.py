"""Social media graphics: the drawing (sizes, themes, fit, carousels), the AI wording (simulated), the pages, checks and approval."""
import os, sys, tempfile, io, zipfile, json, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
os.environ.pop("ANTHROPIC_API_KEY", None)
from PIL import Image
from app import create_app
from extensions import db
from models import *
import graphics as G
import social as SM
import social_images as SI
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def png_size(data):
    im = Image.open(io.BytesIO(data)); im.load(); return im.size

# ---- the drawing
for key, (label, w, h, safe) in G.SIZES.items():
    spec = G.clean_spec(dict(style="deadline", size=key, kicker="Reminder", date_label="30 Nov", title="Quarterly payment dates", body="Name one person per date.", cta="Get the calendar"))
    data, pr = G.render(spec)
    check(png_size(data) == (w, h) and not pr, f"{label}: drawn at exactly {w} x {h} with nothing overflowing")
for style in G.STYLES:
    spec = G.clean_spec(dict(style=style, kicker="K", title="A headline", body="One line.", points=["a", "b"], date_label="30 NOV", cta="Go", attribution="Someone", big_value="3 in 5", caption="what it means", source="a survey",
                             slides=[{"title": "One", "body": "x"}, {"title": "Two", "body": "y"}]))
    n = G.slide_count(spec)
    check(all(png_size(G.render(spec, i)[0]) == G.SIZES[spec["size"]][1:3] and not G.render(spec, i)[1] for i in range(n)), f"style {style}: every picture is drawn ({n})")
spec = G.clean_spec(dict(style="carousel", title="T", slides=[{"title": "a", "body": "b"}] * 3, cta="End"))
check(G.slide_count(spec) == 5 and G.slide_count(G.clean_spec(dict(style="carousel", title="T", slides=[{"title": "a"}]))) == 2, "a carousel is a cover, its slides and a closing slide (when there is closing text)")
z = zipfile.ZipFile(io.BytesIO(G.zip_of(spec)))
check(len(z.namelist()) == 5 and z.namelist()[0].endswith("-01.png"), "the zip holds one numbered picture per slide")
check(G.render(spec, 99)[0][:8] == b"\x89PNG\r\n\x1a\n", "a slide number out of range draws the last slide instead of failing")

# fit: long text shrinks; hopeless text is reported
spec = G.clean_spec(dict(style="tip", size="x", title="Word " * 60, body="Sentence. " * 60, points=["Point " * 20] * 4))
check(len(spec["title"]) <= G.LIMITS["title"] and len(spec["body"]) <= G.LIMITS["body"] and all(len(p) <= 100 for p in spec["points"]), "typed text is cut to its limit")
data, pr = G.render(spec)
check(png_size(data) == (1600, 900), "very long text still produces a picture")
_old = dict(G.LIMITS)
G.LIMITS.update(body=3000, title=1500)
tight = G.clean_spec(dict(style="tip", size="facebook", title="Word " * 200, body="Sentence words. " * 150, points=["p"] * 4))
check(any("too long" in p for p in G.problems(tight)), "text that cannot fit even at the smallest size is reported")
G.LIMITS.clear(); G.LIMITS.update(_old)
worst = [G.problems(G.clean_spec(dict(style=st, size=sz, kicker="K" * 40, title="Wide word " * 20, body="Sentence words here " * 20, date_label="30 NOVEMBER", cta="C" * 60, attribution="A" * 80, big_value="W" * 14, caption="c " * 80, source="s " * 50, points=["Long point words " * 8] * 4, slides=[{"title": "T" * 70, "body": "b " * 100}] * 8))) for st in G.STYLES for sz in G.SIZES]
check(not any(worst), "text typed up to every limit still fits on every layout and size")
check(not G.problems(G.clean_spec(dict(style="tip", title="Fine", body="Short."))), "short text has no problems")
check(G.problems(G.clean_spec(dict(style="tip"))) == ["The graphic has no text yet."], "a graphic with no words is reported")
check("A carousel needs at least one slide." in G.problems(G.clean_spec(dict(style="carousel", title="T"))), "a carousel with no slides is reported")
check(G.clean_spec({"style": "evil", "theme": "x", "size": "y"})["style"] == "tip" and G.clean_spec({"theme": "bad"})["theme"] == "navy" and G.clean_spec({"size": "bad"})["size"] == "linkedin" and G.clean_spec("junk")["style"] == "tip", "unknown style, theme or size fall back to the defaults")
check(G.clean_spec(dict(title="a\r\nb\x00c"))["title"].count("\x00") == 0, "control characters are removed")

# colours: readable
for name, t in G.THEMES.items():
    check(G.contrast(t["text"], t["bg"]) >= 7 and G.contrast(t["muted"], t["bg"]) >= 4.5 and G.contrast(t["accent"], t["bg"]) >= 4.5 and G.contrast(t["accent_text"], t["fill"]) >= 4.5, f"theme {name}: text, muted text, accent and button text are all easy to read")
a = G.render(G.clean_spec(dict(style="tip", title="Same", body="same")))[0]
b = G.render(G.clean_spec(dict(style="tip", title="Same", body="same")))[0]
check(a == b, "the same description always gives the same picture")
check(G.alt_text(G.clean_spec(dict(title="Who owns your deadlines?", body="Name one person."))).startswith("Neverlank graphic: Who owns your deadlines?"), "alt text is made from the words")

# ---- the pages
with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    pu = User(username="part1", name="Pat Partner", email="p@x.co", role="partner"); pu.set_password("pw")
    st = User(username="staff1", name="Sam Staff", email="s@x.co", role="staff"); st.set_password("pw")
    db.session.add_all([pu, st]); db.session.commit(); pid, sid = pu.id, st.id
    db.session.add(Client(name="Zambezi Mining (Pvt) Ltd", industry="Mining", created_by_id=aid)); db.session.commit()
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
partner, staff = login(pid), login(sid)
partner.post("/social/new", data={"title": "Dates nobody owns", "brief": "Quarterly dates. Offer: free 2027 Compliance Calendar.", "platforms": ["linkedin", "instagram"], "mode": "draft", "link_url": "https://app.neverlank.co.zw/compliance-calendar"})
with app.app_context():
    post = SocialPost.query.filter_by(title="Dates nobody owns").first(); pid_post = post.id
partner.post(f"/social/{pid_post}/save", data={"title": "Dates nobody owns", "link_url": "https://app.neverlank.co.zw/compliance-calendar", "body_linkedin": "Name a person for every date.\n\n{link}\n\nGeneral information, not advice.", "body_instagram": "Who owns your deadlines?", "media_instagram": "", "media_linkedin": ""})
page = partner.get(f"/social/{pid_post}").get_data(as_text=True)
check("Graphics" in page and "Add, words by AI" in page and "Add, I write the words" in page, "the post page offers to add a graphic")
check("say which picture or video" in page, "without a graphic, Instagram still asks which picture it needs")

# AI off: blank words
r = partner.post(f"/social/{pid_post}/graphics/new", data={"style": "tip", "theme": "navy", "size": "instagram_portrait", "mode": "ai"}, follow_redirects=True)
check("not switched on" in r.get_data(as_text=True), "asking the AI when it is off says so")
with app.app_context():
    imgs = SocialImage.query.filter_by(post_id=pid_post).all()
    check(len(imgs) == 1 and imgs[0].spec["style"] == "tip" and imgs[0].spec["size"] == "instagram_portrait" and imgs[0].created_by_id == pid, "a graphic is created with its style and size")
    gid = imgs[0].id
page = partner.get(f"/social/{pid_post}").get_data(as_text=True)
check("say which picture or video" not in page, "with a graphic attached, the 'which picture' warning goes away")
check("has no text yet" in page, "a graphic with no words is flagged on the post")
r = partner.get(f"/social/graphics/{gid}/1.png")
check(r.status_code == 200 and r.mimetype == "image/png" and png_size(r.data) == (1080, 1350), "the picture is served as a PNG at the right size")
check(partner.get(f"/social/graphics/{gid}/2.png").status_code == 404 and partner.get(f"/social/graphics/{gid}/0.png").status_code == 404, "slides that do not exist are a 404")
check("attachment" in partner.get(f"/social/graphics/{gid}/1.png?download=1").headers["Content-Disposition"], "the download link saves it as a file")
check(staff.get(f"/social/graphics/{gid}/1.png").status_code == 403 and staff.get(f"/social/graphics/{gid}").status_code == 403 and staff.post(f"/social/{pid_post}/graphics/new", data={}).status_code == 403, "staff cannot use graphics")
check(app.test_client().get(f"/social/graphics/{gid}/1.png").status_code in (302, 401), "nor can anyone who is not signed in")

# AI wording (simulated)
SM.ai_available = lambda: True
asked = []
def fake_design(system, message):
    asked.append((system, message))
    return {"kicker": "Tax tip", "title": "Who owns your deadlines?", "body": "Most late penalties are about a date nobody owned.", "points": ["Name one person per date", "Review the list monthly"],
            "date_label": "", "cta": "Get the free 2027 calendar", "slides": [{"title": "Tax returns", "body": "Know who files and when."}, {"title": "Payroll", "body": "PAYE has its own dates."}],
            "check_these": ["Confirm the penalty wording"]}, None
SI._call_design = fake_design
r = partner.post(f"/social/{pid_post}/graphics/new", data={"style": "carousel", "theme": "ivory", "size": "instagram", "mode": "ai"}, follow_redirects=True)
with app.app_context():
    g2 = SocialImage.query.filter_by(post_id=pid_post).order_by(SocialImage.id.desc()).first(); g2id = g2.id
    sp = g2.spec
    check(g2.ai_used and sp["style"] == "carousel" and sp["theme"] == "ivory" and len(sp["slides"]) == 2 and sp["title"] == "Who owns your deadlines?", "the AI's words fill the graphic in the chosen style and colours")
    check("Confirm the penalty wording" in SocialPost.query.get(pid_post).ai_flags, "what the AI says to verify is added to the post")
check("Dates nobody owns" in asked[-1][1] and "Name a person for every date" in asked[-1][1] and "carousel" in asked[-1][1].lower(), "the AI is given the post's own wording and the layout wanted")
check("You do NOT draw" in asked[-1][0] and "never invent or recall" in asked[-1][0] and "Never name a client" in asked[-1][0], "the AI is told only to write words, never to invent facts, never to name clients")
check("Download all slides (zip)" in partner.get(f"/social/graphics/{g2id}").get_data(as_text=True) and f"/social/graphics/{g2id}/4.png" in partner.get(f"/social/graphics/{g2id}").get_data(as_text=True), "a carousel page shows every slide and a zip link")
z = partner.get(f"/social/graphics/{g2id}.zip")
check(z.status_code == 200 and z.mimetype == "application/zip" and len(zipfile.ZipFile(io.BytesIO(z.data)).namelist()) == 4, "the zip downloads")
SI._call_design = lambda s, m: (None, "The AI is busy.")
r = partner.post(f"/social/graphics/{g2id}/redraft", follow_redirects=True)
check("could not write the words" in r.get_data(as_text=True), "if the AI fails, a message is shown")
with app.app_context():
    check(len(SocialImage.query.get(g2id).spec["slides"]) == 2, "and the existing words are kept")
SI._call_design = fake_design

# editing
form = {"label": "Deadline card", "style": "deadline", "theme": "navy", "size": "linkedin", "kicker": "Compliance reminder", "date_label": "30 Nov", "title": "Quarterly payment dates are close", "body": "Name one person per date.",
        "cta": "Get the free calendar", "footer": "www.neverlank.co.zw", "paab": "1"}
r = partner.post(f"/social/graphics/{g2id}", data=form, follow_redirects=True)
with app.app_context():
    sp = SocialImage.query.get(g2id).spec
    check(sp["style"] == "deadline" and sp["date_label"] == "30 Nov" and sp["size"] == "linkedin" and sp["paab"] is True and SocialImage.query.get(g2id).label == "Deadline card", "the words, layout and size can be edited")
page = partner.get(f"/social/graphics/{g2id}").get_data(as_text=True)
check('value="30 Nov"' in page and "Quarterly payment dates are close" in page and "Alt text for the platform" in page, "the edit page shows the fields for the layout")
partner.post(f"/social/graphics/{g2id}", data=dict(form, paab=""))
with app.app_context():
    check(SocialImage.query.get(g2id).spec["paab"] is False, "the PAAB line can be switched off")
partner.post(f"/social/graphics/{g2id}", data=dict(form, style="carousel", title="Cover", slide_title_1="One", slide_body_1="first", slide_title_2="", slide_body_2="", slide_title_3="Three", slide_body_3="third", cta="Get it", paab="1"))
with app.app_context():
    sp = SocialImage.query.get(g2id).spec
    check([s["title"] for s in sp["slides"]] == ["One", "Three"], "carousel slides come from the numbered rows, skipping empty ones")

# checks and approval include the graphic
partner.post(f"/social/graphics/{g2id}", data=dict(form, title="Zambezi Mining pays ZWG 1,200 - guaranteed", paab="1"))
page = partner.get(f"/social/{pid_post}").get_data(as_text=True)
check("Zambezi Mining" in page and "shows a figure" in page and "shows a date" in page and "promise" in page, "client names, figures, dates and promises on a graphic are flagged")
partner.post(f"/social/graphics/{g2id}", data=dict(form, title="Dates [CLIENT] owns", paab="1"))
check("placeholder is still in the words" in partner.get(f"/social/{pid_post}").get_data(as_text=True), "a placeholder left on a graphic is an error")
with app.app_context():
    _g = SocialImage.query.get(g2id); _s = dict(_g.spec, style="tip", size="facebook", title="Word " * 200, body="Sentence words. " * 150); _g.spec_json = json.dumps(_s); db.session.commit()
G.LIMITS.update(body=3000, title=1500)
r = partner.post(f"/social/{pid_post}/submit", follow_redirects=True)
G.LIMITS.clear(); G.LIMITS.update(_old)
with app.app_context():
    check("too long to fit" in r.get_data(as_text=True) and SocialPost.query.get(pid_post).status == "Draft", "a graphic whose text does not fit blocks sending for approval")
partner.post(f"/social/graphics/{g2id}", data=form)
partner.post(f"/social/graphics/{gid}", data=dict(form, label="Tip"))
partner.post(f"/social/graphics/{gid}", data=dict(form, style="tip", size="instagram_portrait", kicker="Tax tip", title="Who owns your deadlines?", body="Most late penalties are about a date nobody owned.", date_label="", cta="", paab="1"))
partner.post(f"/social/graphics/{g2id}", data=dict(form, date_label="", kicker="Reminder", title="Quarterly payment dates", body="Name one person per date.", paab="1"))
partner.post(f"/social/{pid_post}/submit")
r = partner.post(f"/social/{pid_post}/approve", data={f"check_{k}": "1" for k, _ in SM.CHECKLIST}, follow_redirects=True)
with app.app_context():
    check(SocialPost.query.get(pid_post).status == "Approved", "once the words fit and everything is ticked, a post with graphics can be approved")
# changing a graphic after approval
r = partner.post(f"/social/graphics/{gid}", data=dict(form, style="tip", size="instagram_portrait", title="A new headline", date_label="", cta="", paab="1"), follow_redirects=True)
with app.app_context():
    check(SocialPost.query.get(pid_post).status == "Draft" and "needs approval again" in r.get_data(as_text=True), "changing a graphic after approval sends the post back for approval")
partner.post(f"/social/{pid_post}/submit"); partner.post(f"/social/{pid_post}/approve", data={f"check_{k}": "1" for k, _ in SM.CHECKLIST})
partner.post(f"/social/graphics/{gid}", data=dict(form, label="Renamed", style="tip", size="instagram_portrait", title="A new headline", date_label="", cta="", paab="1"))
with app.app_context():
    check(SocialPost.query.get(pid_post).status == "Approved", "renaming a graphic, with no change to what it shows, does not")
# deleting
r = partner.post(f"/social/graphics/{g2id}/delete", follow_redirects=True)
with app.app_context():
    check(SocialImage.query.get(g2id) is None and SocialPost.query.get(pid_post).status == "Draft", "a graphic can be deleted (the post goes back for approval)")
partner.post(f"/social/{pid_post}/delete")
with app.app_context():
    check(SocialImage.query.filter_by(post_id=pid_post).count() == 0, "deleting a post deletes its graphics")

# ---- one step: post wording and graphic together (New post)
page = partner.get("/social/new").get_data(as_text=True)
check("Also make a graphic for this post" in page and 'name="g_style"' in page and 'name="g_size"' in page and "Best size for the first platform" in page, "New post offers a graphic together with the wording")
SM.ai_available = lambda: True
SM._call_ai = lambda system, message: ({"posts": [{"platform": "linkedin", "body": "Name one person for every tax date.\n\n{link}\n\nGeneral information, not advice."}, {"platform": "x", "body": "Name one person per tax date. {link}"}],
                                        "check_these": ["Confirm the dates"]}, None)
seen = []
def _fake2(system, message):
    seen.append(message)
    return {"kicker": "Tax tip", "title": "Who owns your tax dates?", "body": "Name one person per date.", "check_these": []}, None
SI._call_design = _fake2
before = None
r = partner.post("/social/new", data={"title": "Both at once", "brief": "Quarterly dates. Offer: free calendar.", "platforms": ["linkedin", "x"], "mode": "ai", "with_graphic": "1", "g_style": "tip", "g_theme": "gold", "g_size": "", "link_url": "https://app.neverlank.co.zw/compliance-calendar"}, follow_redirects=True)
with app.app_context():
    both = SocialPost.query.filter_by(title="Both at once").first()
    imgs = SocialImage.query.filter_by(post_id=both.id).all()
    check(len(imgs) == 1 and imgs[0].ai_used and imgs[0].spec["theme"] == "gold" and imgs[0].spec["size"] == "linkedin" and imgs[0].spec["title"] == "Who owns your tax dates?", "one click makes the wording and a graphic, in the chosen colours and the first platform's size")
    check(any("Name one person for every tax date" in m for m in seen), "the graphic's words are written from the wording the AI has just drafted")
    check(both.status == "Draft", "the post is a draft, awaiting review and approval")
    both_id = both.id
check("Graphic: The AI wrote the words" in r.get_data(as_text=True) and "Drafted by the AI" in r.get_data(as_text=True), "both results are announced")
SI._call_design = lambda s, m: (None, "The AI is busy.")
r = partner.post("/social/new", data={"title": "Graphic fails", "brief": "Something", "platforms": ["linkedin"], "mode": "ai", "with_graphic": "1", "g_style": "stat"}, follow_redirects=True)
with app.app_context():
    p2 = SocialPost.query.filter_by(title="Graphic fails").first()
    check(p2 is not None and SocialImage.query.filter_by(post_id=p2.id).count() == 1 and SocialImage.query.filter_by(post_id=p2.id).first().spec["style"] == "stat", "if the AI cannot write the graphic's words, the post and an empty graphic are still made")
check("Graphic: The AI could not write the words" in r.get_data(as_text=True), "and the person is told")
partner.post("/social/new", data={"title": "Blank both", "brief": "", "platforms": ["instagram"], "mode": "draft", "with_graphic": "1", "g_style": "banner", "g_size": "story"})
with app.app_context():
    p3 = SocialPost.query.filter_by(title="Blank both").first()
    g3 = SocialImage.query.filter_by(post_id=p3.id).first()
    check(g3 and not g3.ai_used and g3.spec["size"] == "story" and g3.spec["title"] == "Blank both", "a blank draft with a graphic starts the graphic from the post title")
partner.post("/social/new", data={"title": "No graphic", "brief": "x", "platforms": ["linkedin"], "mode": "draft"})
partner.post("/social/new", data={"title": "Idea only", "brief": "x", "platforms": ["linkedin"], "mode": "idea", "with_graphic": "1"})
with app.app_context():
    check(SocialImage.query.filter_by(post_id=SocialPost.query.filter_by(title="No graphic").first().id).count() == 0 and SocialImage.query.filter_by(post_id=SocialPost.query.filter_by(title="Idea only").first().id).count() == 0, "no graphic unless asked, and none for a saved idea")
check(staff.post("/social/new", data={"title": "x", "with_graphic": "1", "mode": "ai"}).status_code == 403, "staff still cannot")

print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
