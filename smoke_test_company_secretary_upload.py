"""Company Secretary typed on the company-document upload form is recorded as a confirmed key person."""
import os, sys, tempfile, io
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
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    cl = Client(name="Sec Co", created_by_id=aid); db.session.add(cl); db.session.commit(); cid = cl.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(aid); s["_fresh"] = True
check('name="company_secretary"' in c.get(f"/clients/{cid}").get_data(as_text=True), "upload form has a Company Secretary box")
def up(doc_type, **extra):
    d = {"document_type": doc_type, "file": (io.BytesIO(PDF), "x.pdf")}; d.update(extra)
    return c.post(f"/clients/{cid}/company-documents/upload", data=d, content_type="multipart/form-data", follow_redirects=True)
r = up("CR6 - Return of Directors", company_secretary="Tendai Moyo", secretary_details="Appointed 1 March 2024")
with app.app_context():
    ps = ClientKeyPerson.query.filter_by(client_id=cid, role="Company Secretary").all()
    check(len(ps) == 1 and ps[0].full_name == "Tendai Moyo" and ps[0].status == "Confirmed" and ps[0].source_document_id and ps[0].details == "Appointed 1 March 2024", "secretary recorded: confirmed, linked to the document, with details")
check("recorded as Company Secretary" in r.get_data(as_text=True), "the message says so")
up("CR9 - Share Transfer Form", company_secretary="  tendai   MOYO ")
with app.app_context():
    check(ClientKeyPerson.query.filter_by(client_id=cid, role="Company Secretary").count() == 1, "the same name is not added twice (works on a document type that is not read for people)")
up("CR6 - Return of Directors")
with app.app_context():
    check(ClientKeyPerson.query.filter_by(client_id=cid, role="Company Secretary").count() == 1, "left blank: nothing added")
    p = ClientKeyPerson(client_id=cid, full_name="Rudo Dube", role="Company Secretary", status="Suggested"); db.session.add(p); db.session.commit(); pid = p.id
up("CR6 - Return of Directors", company_secretary="Rudo Dube")
with app.app_context():
    check(ClientKeyPerson.query.get(pid).status == "Confirmed", "typing a name already suggested by the AI confirms it")
print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
