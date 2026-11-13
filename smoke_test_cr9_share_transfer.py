"""CR9 Share Transfer Form as a Company Document: offered on the client page, filed to the Permanent File (P5000), not read for people
(a transfer names the person leaving as well as the one arriving), reprocess refuses it, other types are unchanged."""
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
T = "CR9 - Share Transfer Form"
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id)
    db.session.add(cl); db.session.commit()
    cid, aid = cl.id, admin.id
    check(T in COMPANY_DOCUMENT_TYPES, "the type is in the list of Company Document types")
    check(COMPANY_DOCUMENT_TYPE_TO_FILING_CODE.get(T) == "P5000", "it files to P5000 (Structure & Governance)")
    check(FilingIndexSection.query.filter_by(code="P5000").first() is not None, "P5000 exists on a fresh database")
c = login(aid)
r = c.get(f"/clients/{cid}")
check(r.status_code == 200 and T.encode() in r.data, "the client page offers the type in the upload list")
check(b"doc_type_hint" in r.data, "the page carries the note shown when CR9 is chosen")

def upload(dtype, title=""):
    return c.post(f"/clients/{cid}/company-documents/upload", data={"document_type": dtype, "title": title, "file": (io.BytesIO(PDF), "transfer.pdf")},
                  content_type="multipart/form-data", follow_redirects=True)
r = upload(T, "Moyo to Ncube, 12 Oct 2026")
check(r.status_code == 200 and b"not read for people" in r.data, "upload succeeds and says it is not read for people")
with app.app_context():
    d = CompanyDocument.query.filter_by(document_type=T).first()
    check(d is not None and d.title == "Moyo to Ncube, 12 Oct 2026", "stored with the title given")
    check(d.ai_status == "skipped", "reading status is 'skipped'")
    check(ClientKeyPerson.query.filter_by(client_id=cid).count() == 0, "no people were suggested")
    check(d.permanent_file_entry is not None and d.permanent_file_entry.reference_code.startswith("P5"), "a copy was filed to the Permanent File as P5xxx: " + str(d.permanent_file_entry.reference_code if d.permanent_file_entry else None))
    did = d.id
page = c.get(f"/clients/{cid}").data
check(b"update shareholders by hand" in page, "the list shows 'Filed - update shareholders by hand'")
check(page.count(f"company-documents/{did}/reprocess".encode()) == 0, "no Reprocess button for the transfer form")
r = c.post(f"/clients/company-documents/{did}/reprocess", follow_redirects=True)
check(b"not read for people" in r.data, "reprocess is refused with an explanation")
with app.app_context():
    check(ClientKeyPerson.query.filter_by(client_id=cid).count() == 0, "still no people after reprocess")
# a second transfer is allowed and gets its own reference
upload(T, "Ncube to Dube")
with app.app_context():
    refs = [d.permanent_file_entry.reference_code for d in CompanyDocument.query.filter_by(document_type=T).all() if d.permanent_file_entry]
    check(len(refs) == 2 and len(set(refs)) == 2, "two transfers each get their own Permanent File reference: " + ", ".join(refs))
# other types behave as before (reading attempted; no key here so 'not_configured')
upload("Share Register / Share Certificates", "Register")
with app.app_context():
    d2 = CompanyDocument.query.filter_by(document_type="Share Register / Share Certificates").first()
    check(d2 is not None and d2.ai_status == "not_configured", "a share register is still read (status not_configured without a key)")
# deleting removes it
r = c.post(f"/clients/company-documents/{did}/delete", follow_redirects=True)
with app.app_context():
    check(db.session.get(CompanyDocument, did) is None, "delete works")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
