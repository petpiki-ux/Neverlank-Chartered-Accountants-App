"""Jurisdiction of incorporation on the client page feeds note 1 and the Client Details page of the Financial Statements."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
import datetime as dtm
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(aid); s["_fresh"] = True

html = c.get("/clients/new").get_data(as_text=True)
check('name="jurisdiction"' in html and 'value="Zimbabwe"' in html, "new client form offers Jurisdiction of incorporation, prefilled Zimbabwe")
r = c.post("/clients/new", data={"name": "Mauritian Holdings Ltd", "jurisdiction": "Mauritius", "company_number": "MU-1"})
with app.app_context():
    cl = Client.query.filter_by(name="Mauritian Holdings Ltd").first()
    check(cl is not None and cl.jurisdiction == "Mauritius", "saved on the client")
    cid = cl.id
    e = Engagement(client_id=cid, title="FY25", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes", acceptance_required=False)
    db.session.add(e); db.session.commit(); eid = e.id
    n = fin.general_information_note(Client.query.get(cid), Engagement.query.get(eid))
    check('is a company incorporated in Mauritius.' in n and '[jurisdiction' not in n, "note 1: incorporated in Mauritius (%s)" % n[:90])
    d = DocxDocument(wp.build_financial_statements_docx(Engagement.query.get(eid), fin.build_all_statements([], [], reporting_framework="ifrs_for_smes"), None))
    txt = "\n".join(p.text for p in d.paragraphs)
    check("Jurisdiction of incorporation: Mauritius" in txt, "Client Details page of the pack shows it")
r = c.get(f"/clients/{cid}/edit"); check("Mauritius" in r.get_data(as_text=True), "edit form shows the saved value")
c.post(f"/clients/{cid}/edit", data={"name": "Mauritian Holdings Ltd", "jurisdiction": " Zimbabwe. ", "company_number": "MU-1"})
with app.app_context():
    check(Client.query.get(cid).jurisdiction == "Zimbabwe", "edit saves the new value")
    n = fin.general_information_note(Client.query.get(cid), Engagement.query.get(eid))
    check("incorporated in Zimbabwe." in n and "Zimbabwe.." not in n, "trailing full stop not doubled in note 1")
    old = Client(name="Old Co", created_by_id=aid); db.session.add(old); db.session.commit()
    n = fin.general_information_note(old, Engagement.query.get(eid))
    check("[jurisdiction of incorporation]" in n, "a client with none recorded keeps the bracketed placeholder")
    check("Jurisdiction of incorporation:</strong> Zimbabwe" in c.get(f"/clients/{cid}").get_data(as_text=True), "client page shows it")
print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
