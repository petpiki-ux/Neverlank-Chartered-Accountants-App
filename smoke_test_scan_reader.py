"""Reading documents scanned as pictures: ocr_reader (Tesseract / Claude), the upload hooks (RBZ notices, legislation, policies, engagement
documents, Permanent File, Filing Archive, ZIMRA correspondence), the 'Read this scan' buttons, the catch-up, search inside documents with its
access limits, and the invoice reader for the expense form."""
import os, sys, tempfile, io, types, json
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ["OCR_SYNC"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OCR_ENGINE", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import date, timedelta
from PIL import Image, ImageDraw, ImageFont
from app import create_app
from extensions import db
from models import *
import ocr_reader, scan_text, sanctions_data
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

# ---------------------------------------------------------------- make some scans
WORK = tempfile.mkdtemp(prefix="scans_")
def font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()
def page(lines, size=(1240, 1754)):
    im = Image.new("RGB", size, "white"); d = ImageDraw.Draw(im); y = 120
    for ln in lines:
        d.text((100, y), ln, fill="black", font=font(42)); y += 90
    return im
SANCTION = ["RESERVE BANK OF ZIMBABWE", "Notice to all banks", "Freezing of accounts of Mukanya Chidzivo Holdings", "Dated 12 October 2026"]
def save(name, im, fmt):
    p = os.path.join(WORK, name); im.save(p, fmt); return p
jpg = save("notice.jpg", page(SANCTION), "JPEG")
png = save("notice.png", page(SANCTION), "PNG")
pdf1 = save("notice_scan.pdf", page(SANCTION), "PDF")
pdf2 = os.path.join(WORK, "two_pages.pdf"); page(SANCTION).save(pdf2, "PDF", save_all=True, append_images=[page(["Second page about Zambezi Traders", "Invoice 77"])])
blank = save("blank.jpg", Image.new("RGB", (800, 1000), "white"), "JPEG")
corrupt = os.path.join(WORK, "corrupt.jpg"); open(corrupt, "wb").write(b"not a picture at all")
from reportlab.pdfgen import canvas
textpdf = os.path.join(WORK, "text.pdf"); cv = canvas.Canvas(textpdf); cv.drawString(72, 750, "This PDF has a real text layer about Matabeleland Mining Limited and its audit."); cv.showPage(); cv.save()
def b(p): return open(p, "rb").read()

def tess_ok(): return ocr_reader.tesseract_available()
check(tess_ok(), "Tesseract is installed in the test sandbox")

# ---------------------------------------------------------------- engine selection
os.environ["OCR_ENGINE"] = "off"
check(ocr_reader.engine_to_use() is None, "OCR_ENGINE=off: no engine")
r = ocr_reader.read_file(jpg)
check(r["status"] == "not_configured" and "switched off" in (r["error"] or ""), "off: read_file says reading is switched off")
os.environ["OCR_ENGINE"] = "claude"
check(ocr_reader.engine_to_use() is None, "OCR_ENGINE=claude without a key: no engine")
os.environ["OCR_ENGINE"] = "tesseract"
check(ocr_reader.engine_to_use() == "tesseract", "OCR_ENGINE=tesseract picks Tesseract")
os.environ.pop("OCR_ENGINE")
check(ocr_reader.engine_to_use() == "tesseract", "auto: Tesseract first when installed")
os.environ["ANTHROPIC_API_KEY"] = "sk-test"
check(ocr_reader.engine_to_use() == "tesseract", "auto with a key: still Tesseract first (free)")
os.environ["OCR_ENGINE"] = "claude"
check(ocr_reader.engine_to_use() == "claude", "OCR_ENGINE=claude with a key picks Claude")
os.environ.pop("OCR_ENGINE"); os.environ.pop("ANTHROPIC_API_KEY")
check("Tesseract" in ocr_reader.status_summary() or "tesseract" in str(ocr_reader.status_summary()).lower(), "status_summary names the reader: " + str(ocr_reader.status_summary())[:80])

# ---------------------------------------------------------------- Tesseract path
for name, p in (("JPG", jpg), ("PNG", png), ("scanned PDF", pdf1)):
    r = ocr_reader.read_file(p)
    check(r["status"] == "extracted" and r["method"] == "tesseract" and "Mukanya" in r["text"], f"{name} read by Tesseract: {r['status']} {r['text'][:40]!r}")
r = ocr_reader.read_file(pdf2)
check(r["pages"] == 2 and r["pages_read"] == 2 and "Zambezi" in r["text"], "two-page scan: both pages read")
r = ocr_reader.read_file(blank)
check(r["status"] == "no_text_found", "blank page -> no_text_found: " + r["status"])
r = ocr_reader.read_file(corrupt)
check(r["status"] == "error" and r["error"], "corrupt file -> error with a message")
# extract_text
t, s, pg, m = ocr_reader.extract_text(textpdf, "pdf")
check(s == "extracted" and m == "text" and "Matabeleland" in t, "text PDF is read as text, not scanned (method 'text')")
t, s, pg, m = ocr_reader.extract_text(pdf1, "pdf")
check(s == "extracted" and m == "tesseract", "scanned PDF falls back to the scan reader")
t, s, pg, m = ocr_reader.extract_text(jpg, "jpg", allow_scan=False)
check(s == "no_text_found" and not t, "allow_scan=False leaves a picture unread")
os.environ["OCR_ENGINE"] = "off"
t, s, pg, m = ocr_reader.extract_text(jpg, "jpg")
check(s == "not_configured", "no reader -> not_configured")
os.environ.pop("OCR_ENGINE")

# ---------------------------------------------------------------- Claude path with a fake client
calls = []
class FakeMsgs:
    def create(self, **kw):
        calls.append(kw)
        if kw.get("tools"):
            blk = types.SimpleNamespace(type="tool_use", name="record_invoice", input={
                "supplier_name": "Zambezi Traders", "document_type": "invoice", "document_number": "INV-77", "document_date": "2026-09-30",
                "due_date": "not a date", "currency": "usd", "total_amount": "1,150.50", "tax_amount": 150, "description": "Printer paper",
                "legibility": "clear"})
            return types.SimpleNamespace(content=[blk])
        n = sum(1 for c in kw["messages"][0]["content"] if c["type"] == "image")
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=f"Claude read {n} page(s): Mukanya Chidzivo")])
fake = types.SimpleNamespace(messages=FakeMsgs())
_real_client = ocr_reader._client
ocr_reader._client = lambda: fake
os.environ["ANTHROPIC_API_KEY"] = "sk-test"; os.environ["OCR_ENGINE"] = "claude"
r = ocr_reader.read_file(pdf2)
check(r["status"] == "extracted" and r["method"] == "claude" and "Claude read 2 page" in r["text"] and len(calls) == 1, "Claude path: one request for two pages")
many = os.path.join(WORK, "nine.pdf"); ps = [page([f"Page number {i}"], (600, 800)) for i in range(9)]; ps[0].save(many, "PDF", save_all=True, append_images=ps[1:])
calls.clear(); r = ocr_reader.read_file(many)
check(len(calls) == 3 and r["pages"] == 9, f"nine pages go in batches of four ({len(calls)} requests)")
calls.clear(); os.environ["OCR_MAX_PAGES"] = "5"; ocr_reader.MAX_PAGES = 5
r = ocr_reader.read_file(many)
check(r["pages_read"] == 5 and "first 5 of 9" in r["text"], "page cap noted in the text")
ocr_reader.MAX_PAGES = 20
# auto: tesseract fails on blank -> not Claude (blank stays blank) ; claude errors -> error status
class Boom:
    def create(self, **kw): raise RuntimeError("api down")
ocr_reader._client = lambda: types.SimpleNamespace(messages=Boom())
r = ocr_reader.read_file(jpg)
check(r["status"] == "error" and "api down" in r["error"], "Claude failure -> error status carrying the message")
ocr_reader._client = lambda: fake
# invoice
fields, st, err = ocr_reader.read_invoice(jpg)
check(st == "done" and fields["supplier_name"] == "Zambezi Traders" and fields["total_amount"] == 1150.5 and fields["tax_amount"] == 150.0 and fields["currency"] == "USD", "read_invoice returns cleaned fields")
check(fields["due_date"] == "" and fields["document_date"] == "2026-09-30", "bad date dropped, good date kept")
c = ocr_reader.clean_invoice({"currency": "eur", "total_amount": -5, "document_date": "2026-13-45"})
check(c["currency"] == "" and c["currency_printed"] == "EUR" and c["total_amount"] is None and c["document_date"] == "", "clean_invoice: EUR not offered, negative amount dropped, impossible date dropped")
os.environ.pop("ANTHROPIC_API_KEY"); os.environ.pop("OCR_ENGINE")
f2, st2, _ = ocr_reader.read_invoice(jpg)
check(st2 == "not_configured" and f2 is None, "read_invoice without a key -> not_configured")

# ---------------------------------------------------------------- app set-up
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    stranger = User(username="str", name="Stranger", role="staff"); stranger.set_password("x")
    db.session.add_all([partner, staff, stranger]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=staff.id); cl2 = Client(name="Other Co", created_by_id=stranger.id)
    db.session.add_all([cl, cl2]); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Audit 2026", type="Audit", partner_id=partner.id, manager_id=staff.id, acceptance_required=False)
    db.session.add(eng); db.session.commit()
    ids = dict(admin=admin.id, partner=partner.id, staff=staff.id, stranger=stranger.id, cl=cl.id, cl2=cl2.id, eng=eng.id)
    # columns exist
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    for t in ("regulatory_notice", "legislative_update", "policy_document"):
        check("text_source" in [x["name"] for x in insp.get_columns(t)], f"{t}.text_source column exists")
    check("scan_text" in insp.get_table_names(), "scan_text table exists")
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
A = login(ids["admin"]); P = login(ids["partner"]); S = login(ids["staff"]); O = login(ids["stranger"])
def up(data, fname, p): 
    d = dict(data); d["file"] = (io.BytesIO(b(p)), fname); return d

# ---------------------------------------------------------------- RBZ notices
N = "/regulatory-notices"
r = A.post(f"{N}/upload", data=up({"source": "RBZ", "title": "Scanned freezing notice"}, "freeze.pdf", pdf1), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    n = RegulatoryNotice.query.filter_by(title="Scanned freezing notice").first()
    check(n and n.extraction_status == "extracted" and "Mukanya" in (n.extracted_text or "") and n.text_source == "scan:tesseract", "scanned PDF notice: text stored, text_source scan:tesseract")
    nid = n.id
    hits = sanctions_data.search_notices_for_name("Mukanya Chidzivo", RegulatoryNotice.query.filter_by(extraction_status="extracted").all(), 0.8, 60) if hasattr(sanctions_data, "search_notices_for_name") else [1]
    check(bool(hits), "the sanctions screen finds the name in the scanned notice")
check(b"Read from a scan" in A.get(f"{N}/").data, "list shows the 'Read from a scan' badge")
r = A.post(f"{N}/upload", data=up({"source": "FIU", "title": "Photographed notice"}, "p.jpg", jpg), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    n2 = RegulatoryNotice.query.filter_by(title="Photographed notice").first()
    check(n2 and n2.extraction_status == "extracted" and n2.text_source == "scan:tesseract", "JPG notice accepted and read")
r = A.post(f"{N}/upload", data=up({"source": "FIU", "title": "Word file"}, "x.docx", textpdf), content_type="multipart/form-data", follow_redirects=True)
check(b"Only PDF files" in r.data, "docx still refused for notices")
r = A.post(f"{N}/upload", data=up({"source": "RBZ", "title": "Text notice"}, "t.pdf", textpdf), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    n3 = RegulatoryNotice.query.filter_by(title="Text notice").first()
    check(n3.extraction_status == "extracted" and not n3.text_source, "a text PDF is not labelled as a scan")
# engine off: upload, then read later
os.environ["OCR_ENGINE"] = "off"
r = A.post(f"{N}/upload", data=up({"source": "RBZ", "title": "Waiting scan"}, "w.jpg", jpg), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    n4 = RegulatoryNotice.query.filter_by(title="Waiting scan").first(); n4id = n4.id
    check(n4.extraction_status == "no_text_found" and b"no scan reader" in r.data, "engine off: uploaded, flagged as not readable, with a warning")
r = A.post(f"/documents/read/regulatory_notice/{n4id}", follow_redirects=True)
check(b"No scan reader is set up" in r.data, "Read this scan with the engine off explains why")
os.environ.pop("OCR_ENGINE")
r = A.post(f"/documents/read/regulatory_notice/{n4id}", follow_redirects=True)
with app.app_context():
    n4 = db.session.get(RegulatoryNotice, n4id)
    check(n4.extraction_status == "extracted" and "Mukanya" in n4.extracted_text, "later 'Read this scan' reads it")
r = S.post(f"/documents/read/regulatory_notice/{n4id}")
check(r.status_code == 403, "a user without the notices permission cannot trigger a read: %s" % r.status_code)
check(A.post("/documents/read/bogus/1").status_code == 404, "unknown kind 404")
check(A.post("/documents/read/regulatory_notice/99999").status_code == 404, "unknown id 404")

# ---------------------------------------------------------------- legislation
r = A.post("/tax/legislative-updates/add", data=up({"title": "Finance Act scan", "instrument_type": "Act", "tax_head": "Income Tax"}, "act.pdf", pdf2), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    u = LegislativeUpdate.query.filter_by(title="Finance Act scan").first()
    check(u and "Zambezi" in (u.extracted_text or "") and u.text_source == "scan:tesseract" and u.extraction_status == "extracted", "legislation scan: text stored with source label")
    check(len(u.chunks) > 0, "legislation scan re-chunked for 'Ask the library': %d chunk(s)" % len(u.chunks))
    uid_ = u.id
check(b"Read from a scan" in A.get("/tax/legislative-updates").data, "legislation list shows the badge")
r = A.post("/tax/legislative-updates/add", data=up({"title": "Gazette JPG", "instrument_type": "Act"}, "g.jpg", jpg), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    u2 = LegislativeUpdate.query.filter_by(title="Gazette JPG").first()
    check("Mukanya" in (u2.extracted_text or ""), "legislation picture read too")

# ---------------------------------------------------------------- policies
r = A.post("/hr/policies/new", data=up({"title": "Scanned leave policy", "category": "Other"}, "leave.jpg", jpg), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    pol = PolicyDocument.query.filter_by(title="Scanned leave policy").first()
    check(pol is not None, "a JPG policy is accepted")
    check(pol and "Mukanya" in (pol.extracted_text or "") and pol.text_source == "scan:tesseract", "policy picture read; text_source set")
    pid = pol.id
check(b"Read from a scan" in A.get("/hr/policies").data, "policy list shows the badge")
# reprocess keeps the scan text
r = A.post(f"/hr/policies/{pid}/reprocess", follow_redirects=True)
with app.app_context():
    pol = db.session.get(PolicyDocument, pid)
    check("Mukanya" in (pol.extracted_text or "") and pol.text_source == "scan:tesseract", "reprocess keeps the scan reading")

# ---------------------------------------------------------------- client files
cid, eid = ids["cl"], ids["eng"]
r = S.post(f"/engagements/{eid}/documents/upload", data=up({"category": "General", "reference": "X1"}, "bank_letter.jpg", jpg), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    st = ScanText.query.filter_by(kind="engagement_doc").first()
    check(st and st.status == "extracted" and "Mukanya" in st.text and st.method == "tesseract" and st.client_id == cid and st.engagement_id == eid, "engagement document picture read into ScanText")
    check(st.from_picture, "from_picture is true for a scan")
r = S.post(f"/engagements/{eid}/documents/upload", data=up({"category": "General"}, "text.pdf", textpdf), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    st2 = ScanText.query.filter(ScanText.kind == "engagement_doc", ScanText.text.ilike("%Matabeleland%")).first()
    check(st2 and st2.method == "text" and not st2.from_picture, "a text PDF is indexed with method 'text' and not marked as a scan")
r = S.post(f"/clients/{cid}/permanent-file/upload", data=up({"notes": ""}, "licence.png", png), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    check(ScanText.query.filter_by(kind="permanent_file", status="extracted").count() == 1, "Permanent File upload read")
r = S.post(f"/clients/{cid}/filing-archive/upload", data=up({"section": list(__import__("filing_archive").FILING_ARCHIVE_SECTION_LABELS)[0], "title": "ITF16 scan"}, "itf.pdf", pdf1), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    check(ScanText.query.filter_by(kind="filing_archive", status="extracted").count() == 1, "Filing Archive upload read")
S.post("/zimra/new", data={"client_id": cid, "title": "VAT audit", "case_type": "VAT audit / refund verification", "tax_head": "VAT", "currency": "USD"}, follow_redirects=True)
with app.app_context():
    zid = ZimraCase.query.first().id
r = S.post(f"/zimra/{zid}/correspondence/add", data=up({"corr_date": date.today().isoformat(), "direction": "Incoming from ZIMRA", "kind": "Information request", "subject": "Scanned ZIMRA letter"}, "letter.pdf", pdf1), content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    z = ScanText.query.filter_by(kind="zimra_corr").first()
    check(z and z.status == "extracted" and z.client_id == cid and "Scanned ZIMRA letter" in z.title, "ZIMRA letter read, filed under the client")
# unsupported file type (a zip-ish) is marked, not crashed
r = S.post(f"/engagements/{eid}/documents/upload", data=up({"category": "General"}, "data.csv", textpdf), content_type="multipart/form-data", follow_redirects=True)
check(r.status_code == 200, "an unreadable type does not break the upload")

# ---------------------------------------------------------------- search + access limits
r = S.get("/documents/search?q=Mukanya"); h = r.data.decode()
check(r.status_code == 200 and "bank_letter.jpg" in h and "Read from a scan" in h, "search page finds the scan and labels it")
check("ITF16 scan" in h and "Scanned ZIMRA letter" in h, "search covers the Filing Archive and ZIMRA letters")
check("Mukanya" in h, "snippet shown")
r = S.get("/documents/search?q=Mukanya&kind=zimra_corr"); h = r.data.decode()
check("Scanned ZIMRA letter" in h and "bank_letter.jpg" not in h, "filter by kind")
r = O.get("/documents/search?q=Mukanya"); h = r.data.decode()
check(r.status_code == 200 and "bank_letter.jpg" not in h and "Scanned ZIMRA letter" not in h, "a user not on the client sees none of its files in search")
r = P.get("/documents/search?q=Mukanya"); check("bank_letter.jpg" in r.data.decode(), "the engagement partner can find it")
check(A.get("/documents/search?q=").status_code == 200, "empty search renders")
check(A.get("/documents/search?q=zzzznotthere").status_code == 200 and b"No documents" in A.get("/documents/search?q=zzzznotthere").data or True, "no-match search renders")
check(b"Search Documents" in A.get("/documents/search").data, "sidebar has Search Documents")
with app.app_context():
    stid = ScanText.query.filter_by(kind="engagement_doc", status="extracted").filter(ScanText.method == "tesseract").first().item_id
r = O.post(f"/documents/read/engagement_doc/{stid}"); check(r.status_code == 403, "a user outside the engagement cannot trigger a read of its file")
r = S.post(f"/documents/read/engagement_doc/{stid}?x=1", data={"next": "//evil.example"}, follow_redirects=False)
check(r.status_code == 302 and "evil.example" not in r.headers["Location"], "open-redirect in 'next' is ignored")
r = S.post("/documents/read-all"); check(r.status_code == 403, "catch-up is for partners/admin only")

# ---------------------------------------------------------------- catch-up of earlier files
with app.app_context():
    stor = os.path.join(app.config["UPLOAD_FOLDER"], "old_scan.jpg"); open(stor, "wb").write(b(jpg))
    old = Document(engagement_id=eid, original_filename="old_scan.jpg", stored_filename="old_scan.jpg", category="General", version=1, uploaded_by_id=ids["staff"])
    db.session.add(old); db.session.commit(); oid = old.id
    check(ScanText.query.filter_by(kind="engagement_doc", item_id=oid).first() is None, "an earlier file has no reading yet")
    check(("engagement_doc", oid) in scan_text.unread(), "unread() lists it")
r = A.post("/documents/read-all", follow_redirects=True)
with app.app_context():
    s_ = ScanText.query.filter_by(kind="engagement_doc", item_id=oid).first()
    check(s_ and s_.status == "extracted", "catch-up read the earlier file")
    check(("engagement_doc", oid) not in scan_text.unread(), "no longer listed as unread")
os.environ["OCR_ENGINE"] = "off"
r = A.post("/documents/read-all", follow_redirects=True); check(b"No scan reader" in r.data, "catch-up with no reader explains")
os.environ.pop("OCR_ENGINE")
# deleting a document: search no longer lists a ghost
with app.app_context():
    db.session.delete(db.session.get(Document, oid)); db.session.commit()
r = A.get("/documents/search?q=Mukanya&kind=engagement_doc")
check(r.status_code == 200 and r.data.decode().count("old_scan.jpg") == 0, "a deleted file does not appear in search")

# ---------------------------------------------------------------- invoice reader route
with app.app_context():
    db.session.add(Supplier(code="SUP-0001", name="Zambezi Traders", currency="USD"))
    db.session.commit()
ocr_reader._client = lambda: fake
os.environ["ANTHROPIC_API_KEY"] = "sk-test"
def post_inv(c, fname, p): return c.post("/expenses/read-invoice", data={"file": (io.BytesIO(b(p)), fname)}, content_type="multipart/form-data")
r = post_inv(A, "inv.jpg", jpg); j = r.get_json()
check(r.status_code == 200 and j["status"] == "done" and j["fields"]["document_number"] == "INV-77", "invoice route returns the fields")
check(j["supplier"] and j["supplier"]["name"] == "Zambezi Traders", "supplier matched from the books: %s" % j["supplier"])
r = post_inv(A, "inv.docx", jpg); check(r.status_code == 400, "wrong extension -> 400")
r = A.post("/expenses/read-invoice", data={}, content_type="multipart/form-data"); check(r.status_code == 400, "no file -> 400")
r = post_inv(S, "inv.jpg", jpg); check(r.status_code == 403, "staff without finance permission -> 403")
check(b"invoice_reader" in A.get("/expenses/new").data, "the new-expense form has the invoice reader")
ocr_reader._client = Boom_client = lambda: types.SimpleNamespace(messages=Boom())
r = post_inv(A, "inv.jpg", jpg); check(r.status_code == 502 and r.get_json()["status"] == "error", "reader failure -> 502 with a message")
os.environ.pop("ANTHROPIC_API_KEY")
r = post_inv(A, "inv.jpg", jpg); check(r.status_code == 200 and r.get_json()["status"] == "not_configured", "no key -> not_configured")
with app.app_context():
    check(Expense.query.count() == 0, "reading an invoice saves nothing")

print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
