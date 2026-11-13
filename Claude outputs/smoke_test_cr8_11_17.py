"""CR8 Special Resolution, CR11 Allotment of Shares and CR17 Annual Report (AGM) as Company Documents: offered on the client page, filed to the Permanent File, not read for people, each with its own reference."""
import os, sys, tempfile, io
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
from app import create_app
from extensions import db
from models import *
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
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
TYPES = {"CR8 - Special Resolution": "P1000", "CR11 - Allotment of Shares": "P5000", "CR17 - Annual Report (AGM)": "P1000"}
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id)
    db.session.add(cl); db.session.commit()
    cid, aid = cl.id, admin.id
    for t, code in TYPES.items():
        check(t in COMPANY_DOCUMENT_TYPES, f"{t} is in the list of Company Document types")
        check(COMPANY_DOCUMENT_TYPE_TO_FILING_CODE.get(t) == code, f"{t} files to {code}")
        check(t in COMPANY_DOCUMENT_TYPES_NOT_READ_FOR_PEOPLE and t in COMPANY_DOCUMENT_NOT_READ_NOTES, f"{t} is filed but not read for people")
    check("CR9 - Share Transfer Form" in COMPANY_DOCUMENT_TYPES_NOT_READ_FOR_PEOPLE, "the CR9 form is unchanged")
    for code in ("P1000", "P5000"):
        check(FilingIndexSection.query.filter_by(code=code).first() is not None, f"{code} exists on a fresh database")
c = login(aid)
r = c.get(f"/clients/{cid}")
check(r.status_code == 200 and all(t.encode() in r.data for t in TYPES), "the client page offers all three in the upload list")
check(b"doc_type_hint" in r.data and b"CR8 - Special Resolution" in r.data and b"doc_type_hint_why" in r.data, "the page carries the note shown when one is chosen")
def upload(dtype, title=""):
    return c.post(f"/clients/{cid}/company-documents/upload", data={"document_type": dtype, "title": title, "file": (io.BytesIO(PDF), "f.pdf")},
                  content_type="multipart/form-data", follow_redirects=True)
for t, code in TYPES.items():
    r = upload(t, "Test " + t.split(" - ")[0])
    check(r.status_code == 200 and b"not read for people" in r.data, f"{t}: upload succeeds and says it is not read for people")
    with app.app_context():
        d = CompanyDocument.query.filter_by(document_type=t).first()
        check(d is not None and d.title == "Test " + t.split(" - ")[0] and d.ai_status == "skipped", f"{t}: stored with the title and status 'skipped'")
        check(d.permanent_file_entry is not None and d.permanent_file_entry.reference_code.startswith(code[:2]), f"{t}: filed to the Permanent File as {d.permanent_file_entry.reference_code if d.permanent_file_entry else None}")
        did = d.id
    page = c.get(f"/clients/{cid}").data
    check(page.count(f"company-documents/{did}/reprocess".encode()) == 0, f"{t}: no Reprocess button")
    r = c.post(f"/clients/company-documents/{did}/reprocess", follow_redirects=True)
    check(b"not read for people" in r.data, f"{t}: reprocess is refused with an explanation")
with app.app_context():
    check(ClientKeyPerson.query.filter_by(client_id=cid).count() == 0, "no people were suggested from any of them")
page = c.get(f"/clients/{cid}").data
check(b"Filed - not read for people" in page and b"Filed - update shareholders by hand" in page, "the list shows the right badge for each")
upload("CR8 - Special Resolution", "Second resolution")
with app.app_context():
    refs = [d.permanent_file_entry.reference_code for d in CompanyDocument.query.filter_by(document_type="CR8 - Special Resolution").all() if d.permanent_file_entry]
    check(len(refs) == 2 and len(set(refs)) == 2, "two special resolutions each get their own reference: " + ", ".join(refs))
upload("Share Register / Share Certificates", "Register")
with app.app_context():
    d2 = CompanyDocument.query.filter_by(document_type="Share Register / Share Certificates").first()
    check(d2 is not None and d2.ai_status == "not_configured", "a share register is still read")
print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
