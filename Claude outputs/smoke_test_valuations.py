"""Valuation certificates: the Desktop Valuation Certificate wording, the worked-out conclusion and per-hectare figures, Word / PDF output on the
letterhead, the [CONFIRM: ...] placeholders blocking the signature, 'Sign as Managing Partner' (Admin only), the signature dropping when the
figures or wording change, and who may write or open a certificate."""
import io, os, re, sys, tempfile, zipfile
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
from datetime import date
from app import create_app
from extensions import db
from models import *
import valuation_docs as VD
import brand_docs as brand

app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

def docx_text(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = z.read("word/document.xml").decode("utf8")
    return re.sub(r"<[^>]+>", " ", xml), z.namelist()

def form_from(cert_dict, **over):
    f = {k: ("" if v is None else (f"{v:.0f}" if isinstance(v, float) else str(v))) for k, v in cert_dict.items()}
    f["reference"] = over.pop("reference", "NCA/DVC/2026/001")
    f["valuation_date"] = "2026-11-02"; f["certificate_date"] = "2026-11-02"
    f.update({k: ("" if v is None else str(v)) for k, v in over.items()})
    return f

with app.app_context():
    mp = User(username="mp", name="Petros Piki", role="admin"); mp.set_password("x")
    ptn = User(username="ptn", name="Other Partner", role="partner"); ptn.set_password("x")
    stf = User(username="stf", name="Staff", role="staff"); stf.set_password("x")
    db.session.add_all([mp, ptn, stf]); db.session.commit()
    ids = dict(mp=mp.id, ptn=ptn.id, stf=stf.id)

admin, partner, staff = login(ids["mp"]), login(ids["ptn"]), login(ids["stf"])

# --- numbers and words (no database needed)
c = ValuationCertificate(currency="USD", area_ha=9000.0, low_value=500000.0, adopted_value=1000000.0, high_value=2000000.0,
                         valuation_date=date(2026, 11, 2), asset_noun="claim")
check(VD.money(c, 1000000.0) == "US$1,000,000", "money prints US$1,000,000")
check(VD.words(c, 1000000.0) == "one million United States dollars", f"words: {VD.words(c, 1000000.0)}")
pre, strong, post = VD.conclusion_parts(c)
check(pre == "The indicative market value of the claim at 2 November 2026 is " and strong == "US$1,000,000 (one million United States dollars)"
      and post == ", within a range of US$500,000 to US$2,000,000.", "conclusion sentence worked out from the figures")
head, rows = VD.value_table(c)
check(rows == [["Low", "500,000", "56"], ["Adopted", "1,000,000", "111"], ["High", "2,000,000", "222"]], f"value table with per-hectare 56/111/222: {rows}")
check(head[2].startswith("Per hectare (US$, 9,000 ha)"), "per-hectare heading shows the area")
c2 = ValuationCertificate(currency="ZWG", adopted_value=250000.0, valuation_date=date(2026, 11, 2), asset_noun="business")
h2, r2 = VD.value_table(c2)
check(len(h2) == 2 and r2 == [["Adopted", "250,000"]], "no area -> no per-hectare column; only the adopted row")
check(VD.conclusion_parts(c2)[2] == "." and "Zimbabwe Gold" in VD.conclusion_parts(c2)[1], "no range -> sentence ends; ZWG worded as Zimbabwe Gold")
check(VD.figures_problems(ValuationCertificate(low_value=5.0, adopted_value=3.0, high_value=9.0)) != [], "low above adopted is a problem")
check(VD.figures_problems(ValuationCertificate(low_value=1.0, adopted_value=3.0, high_value=2.0)) != [], "high below adopted is a problem")
check(VD.figures_problems(ValuationCertificate(low_value=1.0, adopted_value=3.0)) != [], "low without high is a problem")
check(VD.pieces("**Title.** The [CONFIRM: x] claim") == [("Title.", True, False), (" The ", False, False), ("[CONFIRM: x]", False, True), (" claim", False, False)],
      "bold lead-in and placeholder pieces")

# --- who may start one
r = staff.get("/valuations/new?template=desktop")
check(r.status_code == 403, "staff cannot write a certificate")
check(staff.get("/valuations/").status_code == 200, "staff can open the list")
r = partner.get("/valuations/new")
check(r.status_code == 200 and b"Desktop Valuation Certificate" in r.data and b"Blank certificate" in r.data, "partner sees the two starting points")
r = admin.get("/valuations/new?template=desktop")
body = r.get_data(as_text=True)
check(r.status_code == 200 and "Broxmen Investments (Private) Limited" in body and "NCA/DVC/" in body, "form is pre-filled with the Broxmen wording and a reference")
check("Coal claim or special grant of approximately 9,000 hectares" in body and "Contango Holdings agreed to acquire 70%" in body, "particulars and approach are in the form")
check("[CONFIRM: claim or special grant number" in body and "1000000" in body, "title reference placeholder and the figures are in the form")

# --- save the Broxmen certificate
tmpl = VD.DESKTOP_TEMPLATE
post = form_from(tmpl, reference="NCA/DVC/2026/001")
post["client_name"] = tmpl["client_name"]
r = admin.post("/valuations/new?template=desktop", data=post, follow_redirects=False)
check(r.status_code == 302, f"certificate saved ({r.status_code})")
with app.app_context():
    cert = ValuationCertificate.query.filter_by(reference="NCA/DVC/2026/001").first()
    cid = cert.id
    check(cert.adopted_value == 1000000.0 and cert.area_ha == 9000.0 and cert.currency == "USD", "figures saved")
    check(len(VD.unresolved(cert)) == 2, f"two placeholders left (title reference, no-interest statement): {VD.unresolved(cert)}")
with app.app_context():
    check(VD.next_reference(2026) == "NCA/DVC/2026/002", "next reference is 002")

# duplicate reference refused
r = admin.post("/valuations/new?template=desktop", data=post)
check(r.status_code == 200 and b"already used" in r.data, "duplicate reference refused")
# bad figures refused
bad = dict(post, reference="NCA/DVC/2026/009", low_value="3000000")
r = admin.post("/valuations/new?template=desktop", data=bad)
check(r.status_code == 200 and b"low value is above" in r.data, "low above adopted refused on save")

# --- view page
r = admin.get(f"/valuations/{cid}")
body = r.get_data(as_text=True)
check(r.status_code == 200 and "US$1,000,000 (one million United States dollars)" in body, "preview shows the conclusion sentence")
check(">56<" in body and ">111<" in body and ">222<" in body, "preview shows per-hectare 56 / 111 / 222")
check("placeholder(s) still in the certificate" in body and "Sign as Managing Partner" not in body, "placeholders warn and the sign button is not offered")
check("cannot be signed" in body or "placeholder(s) are still" in body, "reason shown")

# --- documents with placeholders
r = admin.get(f"/valuations/{cid}/download/docx")
check(r.status_code == 200 and r.headers["Content-Disposition"].endswith('NCA_DVC_2026_001.docx"'), "Word download named after the reference")
text, names = docx_text(r.data)
check("Certificate particulars" in text and "Broxmen Investments" in text and "1,000,000" in text and "Adopted value" in text, "Word has particulars, client and the adopted panel")
check("DRAFT - placeholders to complete" in text, "Word letterhead flags the draft")
check("word/media/image1.png" in names or any(n.startswith("word/media/") for n in names), "Word carries the logo")
check("a 100% interest" in text.replace("  ", " ") or "100% interest" in text, "Word carries the basis of value")
r = admin.get(f"/valuations/{cid}/download/pdf")
check(r.status_code == 200 and r.data[:5] == b"%PDF-", "PDF download")
pdf_unsigned = r.data
check(admin.get(f"/valuations/{cid}/download/xls").status_code == 404, "unknown format 404")

# --- signing is blocked while placeholders remain
r = admin.post(f"/sign/valuation/{cid}", follow_redirects=True)
check(b"placeholder(s) are still in the certificate" in r.data, "cannot sign with placeholders")
with app.app_context():
    check(ValuationCertificate.query.get(cid).mp_signed_at is None, "still unsigned")

# --- complete the placeholders
post2 = dict(post)
post2["title_reference"] = "Special Grant 5123, registered 4 March 2019 at the Mining Affairs Board, Bulawayo"
post2["certification"] = post["certification"].replace("[CONFIRM: I have no interest in the subject asset - signatory to confirm]",
                                                       "I have no interest in the subject asset.")
r = admin.post(f"/valuations/{cid}/edit", data=post2)
check(r.status_code == 302, "placeholders completed")
with app.app_context():
    check(VD.unresolved(ValuationCertificate.query.get(cid)) == [], "no placeholders left")

# partner (not Admin) cannot sign
check(partner.post(f"/sign/valuation/{cid}").status_code == 403, "a partner who is not an Admin cannot sign")
r = admin.get(f"/valuations/{cid}")
check(b"Sign as Managing Partner" in r.data and b"DRAFT - placeholders" not in r.data, "sign button now offered; draft flag gone")
r = admin.post(f"/sign/valuation/{cid}", follow_redirects=True)
check(b"now carries the Managing Partner" in r.data, "signed by the Managing Partner")
with app.app_context():
    cert = ValuationCertificate.query.get(cid)
    check(brand.signature_valid(cert) and VD.sign_state(cert), "signature valid")
body = admin.get(f"/valuations/{cid}").get_data(as_text=True)
check("data:image/png;base64" in body and "Signed electronically by the Managing Partner" in body, "preview shows the signature image")
pdf_signed = admin.get(f"/valuations/{cid}/download/pdf").data
check(pdf_signed[:5] == b"%PDF-" and len(pdf_signed) > len(pdf_unsigned) + 500, f"PDF with the signature image is larger ({len(pdf_signed)} vs {len(pdf_unsigned)})")
text, names = docx_text(admin.get(f"/valuations/{cid}/download/docx").data)
check(sum(1 for n in names if n.startswith("word/media/")) >= 2, "Word has logo + signature images")
check("Signed electronically by the Managing Partner" in text and "DRAFT" not in text, "Word shows the signed line, no draft flag")
check(partner.get(f"/valuations/{cid}").status_code == 200, "partner can open a signed certificate")
check(partner.post(f"/valuations/{cid}/delete").status_code == 302, "delete redirects")
with app.app_context():
    check(ValuationCertificate.query.get(cid) is not None, "a signed certificate cannot be deleted")

# --- change after signing drops the signature
post3 = dict(post2, adopted_value="1100000")
r = admin.post(f"/valuations/{cid}/edit", data=post3, follow_redirects=True)
check(b"signature was dropped" in r.data, "editing the figures says the signature was dropped")
with app.app_context():
    cert = ValuationCertificate.query.get(cid)
    check(cert.mp_signed_at is not None and not brand.signature_valid(cert), "stored signature no longer valid")
check(b"Signed electronically" not in admin.get(f"/valuations/{cid}").data, "printed page drops the signature")
# a change that does not touch the content (re-saving the same data) keeps the signature after re-signing
admin.post(f"/sign/valuation/{cid}")
admin.post(f"/valuations/{cid}/edit", data=post3)
with app.app_context():
    check(brand.signature_valid(ValuationCertificate.query.get(cid)), "re-saving unchanged content keeps the signature")
# signatory other than the Managing Partner cannot be signed by him, and shows no image
post4 = dict(post3, signatory_name="Another Partner CA(Z)")
admin.post(f"/valuations/{cid}/edit", data=post4)
r = admin.post(f"/sign/valuation/{cid}", follow_redirects=True)
check(b"signed by someone other than the Managing Partner" in r.data, "signing refused for another signatory")
text, names = docx_text(admin.get(f"/valuations/{cid}/download/docx").data)
check(sum(1 for n in names if n.startswith("word/media/")) == 1, "no signature image for another signatory")
# unsign
admin.post(f"/valuations/{cid}/edit", data=post3)
admin.post(f"/sign/valuation/{cid}")
r = admin.post(f"/sign/valuation/{cid}/remove", follow_redirects=True)
check(b"signature was removed" in r.data, "Admin can remove the signature")
check(partner.post(f"/sign/valuation/{cid}/remove").status_code == 403, "partner cannot remove it")
# delete unsigned
r = admin.post(f"/valuations/{cid}/delete", follow_redirects=True)
check(b"Certificate deleted" in r.data, "unsigned certificate can be deleted")

# --- blank certificate
r = partner.get("/valuations/new?template=blank")
check(r.status_code == 200 and b"NCA/DVC/" in r.data and b"[CONFIRM: the approach" in r.data, "blank template has its placeholders")
bl = form_from(VD.BLANK_TEMPLATE, reference="NCA/DVC/2026/050")
bl.update(client_name="Acme Holdings", adopted_value="250000", currency="ZWG", subject_asset="A building in Harare")
r = partner.post("/valuations/new?template=blank", data=bl)
check(r.status_code == 302, "blank certificate saved by a partner")
with app.app_context():
    b = ValuationCertificate.query.filter_by(reference="NCA/DVC/2026/050").first()
    pdf = VD.build_pdf(b); dx = VD.build_docx(b)
    check(pdf[:5] == b"%PDF-" and len(dx) > 5000, "blank certificate builds as PDF and Word")
    t, _ = docx_text(dx)
    check("ZWG250,000" in t.replace(" ", "") and "Zimbabwe Gold" in t, "ZWG figures and words in Word")
    check("Per hectare" not in t, "no per-hectare column without an area")

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
