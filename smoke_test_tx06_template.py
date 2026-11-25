"""TX-01 Tax and Advisory Services Engagement Letter (replaces the old TX-01): in the template library, generates for a tax engagement, the app fills in what it knows, and the one-time swap works."""
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
import wp_docx
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    tpl = DocumentTemplate.query.filter_by(ref_code="TX-01", type="Tax Advisory").first()
    check(tpl is not None and tpl.filename == "TX-01 Engagement Letter.docx" and "Tax and Advisory" in tpl.title and "CGT" not in tpl.title, "TX-01 is the Tax and Advisory Services letter in the library")
    check(DocumentTemplate.query.filter_by(ref_code="TX-06", type="Tax Advisory").first() is None, "there is no separate TX-06 any more")
    check(not os.path.exists(os.path.join(app.config["DOCUMENT_TEMPLATES_SEED_DIR"], "Tax Advisory", "TX-06 Tax and Advisory Services Engagement Letter.docx")), "the TX-06 file is gone from the bundled templates")
    src = os.path.join(app.config["DOCUMENT_TEMPLATES_DATA_DIR"], "Tax Advisory", tpl.filename)
    check(os.path.exists(src), "the file was copied into the writable template folder")
    cl = Client(name="Yu Cheng Group (Private) Limited", address="5 Borrowdale Road, Harare", tin_number="2000555111", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="CGT and tax advisory", type="Tax Advisory", partner_id=aid, period_end=dtm.date(2026, 12, 31), acceptance_required=False)
    db.session.add(e); db.session.commit()
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(aid); s["_fresh"] = True
    r = c.post(f"/working-papers/engagements/{e.id}/generate/{tpl.id}")
    doc = Document.query.filter_by(engagement_id=e.id).first()
    check(doc is not None and doc.in_app and doc.original_filename.startswith("TX-01"), "it generates as a working paper on a tax engagement")
    d = DocxDocument(os.path.join(app.config["UPLOAD_FOLDER"], doc.stored_filename))
    text = "\n".join(p.text for p in d.element.body.iter() if False) or ""
    full = "\n".join(wp_docx.para_text(p) for p in d.element.body.iter(wp_docx.qn("w:p")))
    check("Yu Cheng Group (Private) Limited" in full and "2000555111" in full and "5 Borrowdale Road, Harare" in full, "client name, TIN and registered address are filled in")
    check("[Client Name]" not in full and "(“you”, “your” or the “Client”)" in full and "Yu Cheng Group (Private) Limited (" in full, "the [Client Name] placeholder in the opening paragraph is filled")
    check("Date of letter" in full and "Ref. TX-01" in full and "TX-06" not in full and "CGT ADVISORY" not in full, "it carries the TX-01 reference and no leftovers of the old letter or the TX-06 name")
    check(all(f"{n}. " in full for n in range(1, 19)) and "19. " not in full and "Limits on our liability" not in full and "Schedule" not in full, "all eighteen sections are present, with no schedules and no limit-of-liability section")
    check("section 18." in full and "under section 16.2" in full and "under section 15, and" in full and "section 19" not in full and "section 17.2" not in full, "cross-references point at the renumbered sections")
    check("[Chapter 12:07]" in full and "[Chapter 9:24]" in full and "Z10110" in full, "it names the data protection and anti-money-laundering laws and the firm's PAAB number")
    check("section 2.1" in full and "hourly rates below" in full and "[Client Name]" not in full and "Schedule" not in full, "the services and fees are stated in the letter itself (sections 2.1 and 6.1)")
    import wp_assist
    check(len(wp_assist.docx_outline(d, empty=True)) > 100, "the Claude panel can read it (paragraphs and blank fields are listed)")
    # One-time swap on a live app: an old, customised TX-01 is replaced once (and backed up), TX-06 row removed, later edits are not overwritten.
    import shutil, seed
    dd = app.config["DOCUMENT_TEMPLATES_DATA_DIR"]; live = os.path.join(dd, "Tax Advisory", "TX-01 Engagement Letter.docx")
    marker = os.path.join(dd, seed.TX01_LETTER_MARKER)
    check(os.path.exists(marker), "the swap ran once at start-up and left its marker")
    os.remove(marker)
    with open(live, "wb") as fh: fh.write(b"OLD CUSTOMISED LETTER")
    db.session.add(DocumentTemplate(type="Tax Advisory", filename="TX-06 Tax and Advisory Services Engagement Letter.docx", ref_code="TX-06", title="x", description="x"))
    shutil.copy(live, os.path.join(dd, "Tax Advisory", "TX-06 Tax and Advisory Services Engagement Letter.docx")); db.session.commit()
    check(seed.replace_tx01_engagement_letter() is True, "the swap runs on a live app")
    check(os.path.getsize(live) > 1000 and DocxDocument(live) is not None, "the live TX-01 is now the new letter")
    check(any(f.startswith("TX-01") for f in os.listdir(os.path.join(dd, "_replaced"))), "the old customised copy was kept in _replaced")
    check(DocumentTemplate.query.filter_by(ref_code="TX-06").count() == 0 and not os.path.exists(os.path.join(dd, "Tax Advisory", "TX-06 Tax and Advisory Services Engagement Letter.docx")), "the TX-06 row and file are removed")
    with open(live, "wb") as fh: fh.write(b"EDITED LATER")
    check(seed.replace_tx01_engagement_letter() is False and open(live, "rb").read() == b"EDITED LATER", "it never runs twice, so later edits are kept")
print("\nALL PASSED" if not fails else "\nFAILED: %d" % len(fails))
sys.exit(1 if fails else 0)
