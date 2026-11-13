"""Financial Statements cover page: colours taken from the client's logo (or typed, or the firm's own), contrast rules, the artwork,
its placement behind the first page of the Word file, the fallback to the plain cover, and the client-page controls."""
import os, sys, tempfile, io, random, shutil
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
import datetime as dtm
from PIL import Image, ImageDraw
from app import create_app
from extensions import db
from models import *
from config import Config
import cover_art as ca
import workpapers as wp
import financials as fin
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
TMP = tempfile.mkdtemp(prefix="logo_")
def logo(name, shapes, size=(500, 220), bg=(0, 0, 0, 0)):
    im = Image.new("RGBA", size, bg); d = ImageDraw.Draw(im)
    for box, col in shapes: d.rectangle(box, fill=col)
    p = os.path.join(TMP, name); im.save(p); return p
def hue_of(rgb):
    import colorsys
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0] * 360
def near_hue(rgb, target, tol=25):
    d = abs(hue_of(rgb) - target) % 360
    return min(d, 360 - d) <= tol

# ---- colours from logos
two = logo("two.png", [((10, 10, 300, 200), (30, 90, 200, 255)), ((310, 60, 480, 160), (235, 90, 30, 255))])
m, s = ca.logo_colours(two)
check(m is not None and near_hue(m, 220) and s is not None and near_hue(s, 18), "two-colour logo gives both colours (blue, orange)")
one = logo("one.png", [((10, 10, 480, 200), (0, 122, 70, 255))])
m, s = ca.logo_colours(one)
check(m is not None and near_hue(m, 152) and s is None, "one-colour logo gives one colour and no second")
check(ca.logo_colours(logo("black.png", [((10, 10, 480, 200), (10, 10, 10, 255))])) == (None, None), "a black logo has no colour to use")
check(ca.logo_colours(logo("grey.png", [((10, 10, 480, 200), (130, 130, 130, 255))])) == (None, None), "a grey logo has no colour to use")
check(ca.logo_colours(logo("white.png", [((10, 10, 480, 200), (255, 255, 255, 255))], bg=(255, 255, 255, 255))) == (None, None), "a white logo has no colour to use")
check(ca.logo_colours(logo("clear.png", [])) == (None, None), "a fully transparent logo gives nothing")
bad = os.path.join(TMP, "bad.png"); open(bad, "wb").write(b"not an image")
check(ca.logo_colours(bad) == (None, None), "an unreadable file gives nothing and does not raise")
white_bg = logo("onwhite.png", [((100, 60, 400, 160), (200, 30, 40, 255))], bg=(255, 255, 255, 255))
m, s = ca.logo_colours(white_bg)
check(m is not None and near_hue(m, 357, 20), "a logo on a white box ignores the white")
tiny = logo("speck.png", [((0, 0, 3, 3), (200, 30, 40, 255))], size=(500, 500), bg=(255, 255, 255, 255))
check(ca.logo_colours(tiny) == (None, None), "a few stray coloured pixels are not a brand colour")

# ---- palette rules
white = (255, 255, 255)
rnd = random.Random(7)
worst_w, worst_a = 99, 99
for _ in range(400):
    a = tuple(rnd.randrange(256) for _ in range(3)); b = tuple(rnd.randrange(256) for _ in range(3)) if rnd.random() < 0.6 else None
    pal = ca.build_palette(a, b)
    P, A = ca.hex_to_rgb(pal["primary"]), ca.hex_to_rgb(pal["accent"])
    worst_w = min(worst_w, ca.contrast(P, white)); worst_a = min(worst_a, ca.contrast(A, P))
check(worst_w >= 4.4, f"white text reads on the main colour for any input (worst contrast {worst_w:.2f})")
check(worst_a >= 2.4, f"the accent stands out from the main colour for any input (worst contrast {worst_a:.2f})")
d = ca.build_palette()
check((d["primary"], d["accent"], d["source"]) == (ca.FIRM_NAVY, ca.FIRM_GOLD, "default"), "no colour at all gives the firm's navy and gold")
yellow = ca.build_palette((255, 235, 0), None)
check(ca.contrast(ca.hex_to_rgb(yellow["primary"]), white) >= 4.4, "a bright yellow logo is deepened so white text is readable")
check(ca.valid_hex("1F4E9C") and ca.valid_hex("#abc") and not ca.valid_hex("12345") and not ca.valid_hex("GGGGGG") and not ca.valid_hex(None), "hex validation")

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    os.makedirs(Config.CLIENT_LOGOS_DATA_DIR, exist_ok=True)
    shutil.copy(two, os.path.join(Config.CLIENT_LOGOS_DATA_DIR, "c1.png"))
    cl = Client(name="Greenfield Agricultural Holdings (Private) Limited", company_number="1234/2015", created_by_id=admin.id, logo_filename="c1.png")
    nolo = Client(name="Plain Co", created_by_id=admin.id)
    db.session.add_all([cl, nolo]); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY26 audit", type="Audit", partner_id=admin.id, period_end=dtm.date(2026, 12, 31), reporting_framework="ifrs_for_smes")
    e2 = Engagement(client_id=nolo.id, title="FY26", type="Audit", partner_id=admin.id, period_end=dtm.date(2026, 6, 30))
    db.session.add_all([e, e2]); db.session.commit()
    cid, nid, eid, e2id, aid = cl.id, nolo.id, e.id, e2.id, admin.id

    pal = ca.palette_for_client(cl)
    check(pal["source"] == "logo" and near_hue(ca.hex_to_rgb(pal["primary"]), 220), "client with a logo: colours follow the logo")
    check(ca.palette_for_client(nolo)["source"] == "default", "client without a logo: the firm's colours")
    cl.cover_primary = "7A1F3D"; db.session.commit()
    pal = ca.palette_for_client(cl)
    check(pal["source"] == "typed" and near_hue(ca.hex_to_rgb(pal["primary"]), 337, 15), "typed colour wins over the logo")
    cl.cover_primary = "ZZZZZZ"; db.session.commit()
    check(ca.palette_for_client(cl)["source"] == "logo", "an invalid stored colour is ignored")
    cl.cover_primary = None; db.session.commit()

    # ---- the artwork
    for aspect, label in ((1.4142, "A4"), (1.2941, "Letter")):
        png, _ = ca.render_for_engagement(e, aspect=aspect, width_px=900)
        im = Image.open(png)
        check(im.size[0] == 900 and abs(im.size[1] / 900 - aspect) < 0.002, f"{label} artwork has the page's proportions: {im.size}")
    im = Image.open(ca.render_for_engagement(e, width_px=900)[0]).convert("RGB")
    check(im.getpixel((880, 880)) != (255, 255, 255) and im.getpixel((5, im.size[1] - 5)) == (255, 255, 255), "colour field on the right, white corner bottom-left")
    long_name = "The Very Long Named Agricultural, Mining and Engineering Services Holdings Group (Private) Limited T/A Something Else Entirely " * 2
    for nm, lg, pe in ((long_name, two, dtm.date(2026, 12, 31)), ("", None, None), ("Zed", bad, dtm.date(2027, 2, 28))):
        try:
            out = ca.render_cover(nm, pe, ca.build_palette(), logo_path=lg, reg_no="1/2", framework="Full IFRS", width_px=700)
            ok = Image.open(out).size[0] == 700
        except Exception as exc:
            ok = False; print(exc)
        check(ok, f"renders without error: name {len(nm)} chars, logo {'yes' if lg else 'no'}, year end {pe}")

    # ---- the Word file
    st = fin.build_all_statements([], [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect")
    buf = wp.build_financial_statements_docx(e, st, None)
    from docx import Document
    d = Document(buf)
    body_xml = d.element.body.xml
    first_pic = body_xml.find("<wp:anchor")
    check(first_pic >= 0 and 'behindDoc="1"' in body_xml and "<wp:inline" not in body_xml[:first_pic + 50], "cover artwork is placed behind the page text (anchored, not inline)")
    check('descr="Cover page: Greenfield' in body_xml, "the cover has alternative text with the client name")
    check(d.sections[0].different_first_page_header_footer is True and not any(p.text.strip() for p in d.sections[0].first_page_footer.paragraphs), "the cover page carries no footer")
    check(any("Page" in p.text or "NUMPAGES" in p._p.xml for p in d.sections[0].footer.paragraphs), "the pages after it keep the firm footer")
    check("Client Details" in [p.text for p in d.paragraphs], "the Client Details page follows the cover")
    check(len(d.part.package.image_parts) >= 1, "the artwork is embedded in the file")
    # fallback to the plain cover
    orig = ca.render_for_engagement
    def boom(*a, **k): raise RuntimeError("no fonts")
    ca.render_for_engagement = boom
    try:
        d2 = Document(wp.build_financial_statements_docx(e, st, None))
    finally:
        ca.render_for_engagement = orig
    texts = [p.text for p in d2.paragraphs]
    check("Financial Statements" in texts and "Client Details" in texts and "<wp:anchor" not in d2.element.body.xml, "if the artwork fails, the plain cover is used and the pack is still produced")

# ---- the client page and routes
from flask import url_for
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
c = login(aid)
r = c.get(f"/clients/{cid}/cover-preview.png")
check(r.status_code == 200 and r.mimetype == "image/png" and Image.open(io.BytesIO(r.data)).size[0] == 900, "preview route returns the cover as a PNG")
r = c.get(f"/clients/{nid}/cover-preview.png")
check(r.status_code == 200, "preview works for a client with no logo")
check(c.get("/clients/99999/cover-preview.png").status_code == 404, "unknown client is a 404")
page = c.get(f"/clients/{cid}").data
check(b"cover-preview.png" in page and b"Main colour (hex)" in page and b"colours taken from the logo" in page, "client page shows the preview and the colour boxes")
r = c.post(f"/clients/{cid}/cover-colours", data={"cover_primary": "#1f4e9c", "cover_accent": "f28c28"}, follow_redirects=True)
with app.app_context():
    cc = db.session.get(Client, cid)
    check(cc.cover_primary == "1F4E9C" and cc.cover_accent == "F28C28", "typed colours are saved (upper-case, without #)")
check(b"colours typed below" in c.get(f"/clients/{cid}").data, "the page says the typed colours are in use")
r = c.post(f"/clients/{cid}/cover-colours", data={"cover_primary": "blue", "cover_accent": ""}, follow_redirects=True)
with app.app_context():
    check(db.session.get(Client, cid).cover_primary == "1F4E9C" and b"6-digit hex" in r.data, "an invalid colour is refused and the old ones kept")
c.post(f"/clients/{cid}/cover-colours", data={"reset": "1"}, follow_redirects=True)
with app.app_context():
    cc = db.session.get(Client, cid)
    check(cc.cover_primary is None and cc.cover_accent is None, "reset goes back to the logo's colours")
check(b"Preview the cover page" in c.get(f"/engagements/{eid}?tab=finalisation").data, "the engagement's Finalisation tab links to the preview")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
