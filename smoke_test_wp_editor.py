"""Working Papers tab: generate a working paper from a template and work in it inside the app (Word page editor, Excel grid)."""
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
import datetime as dtm
from docx import Document as DocxDocument
import openpyxl
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
    cl = Client(name="Penanel Trading (Private) Limited", address="12 Samora Machel Ave, Harare", tin_number="2000123456", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25 Statutory Audit", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes", acceptance_required=False)
    db.session.add(e); db.session.commit(); eid = e.id
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for nm, cat, dr, cr, pdr, pcr in [("Cash", "cash", 90000, 0, 3000, 0), ("Receivables", "trade_receivables", 30000, 0, 1000, 0), ("Share capital", "share_capital", 0, 100, 0, 100),
                                      ("Sales", "revenue", 0, 120000, 0, 80000), ("Cost of sales", "cost_of_sales", 50000, 0, 30000, 0), ("Payables", "trade_payables", 0, 20000, 0, 5000)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=nm, fs_category=cat, current_debit=dr, current_credit=cr, prior_debit=pdr, prior_credit=pcr))
    db.session.commit()
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(aid); s["_fresh"] = True
    page = c.get(f"/engagements/{eid}?tab=documents").get_data(as_text=True)
    check(">Working Papers (" in page and "Generate a working paper" in page and ">Generate</button>" in page and ">Documents (" not in page, "the tab is called Working Papers and each template has a Generate button")
    tpl = DocumentTemplate.query.filter_by(filename="FA-03 Materiality and Risk Assessment Workpaper.docx").first()
    r = c.post(f"/working-papers/engagements/{eid}/generate/{tpl.id}", follow_redirects=False)
    doc = Document.query.filter_by(engagement_id=eid).first()
    check(r.status_code == 302 and doc and f"/working-papers/{doc.id}/edit" in r.headers["Location"] and doc.in_app and doc.is_generated and doc.category == "Working Papers" and doc.reference == tpl.ref_code, "Generate files a copy and opens it in the editor")
    path = os.path.join(app.config["UPLOAD_FOLDER"], doc.stored_filename)
    d = DocxDocument(path)
    txt = [[c_.text.strip() for c_ in row.cells] for t in d.tables for row in t.rows]
    flat = [x for row in txt for x in row]
    check("Penanel Trading (Private) Limited" in flat and "31 December 2025" in flat and "FY25 Statutory Audit" in flat, "client name, year end and file reference are filled in")
    rev = [row for row in txt if row and row[0] == "Revenue"][0]; ta = [row for row in txt if row and row[0] == "Total assets"][0]; pbt = [row for row in txt if row and row[0] == "Profit before tax"][0]
    check(rev[1] == "120,000" and ta[1] == "120,000" and pbt[1] == "70,000", "materiality benchmarks filled from the trial balance: %s %s %s" % (rev[1], ta[1], pbt[1]))
    check(any(row[0] == "Prepared by" and row[2] == wp_docx.format_date(dtm.date.today()) for row in txt), "the sign-off table gets today's date against Prepared by, the signature stays blank")
    page = c.get(f"/working-papers/{doc.id}/edit").get_data(as_text=True)
    check(r.status_code == 302 and 'class="wp-page"' in page and "Penanel Trading" in page and 'contenteditable="true"' in page and "+ Row" in page, "the editor page shows the paper with editable text")
    # edit: change a paragraph run, and a table cell by segments
    d = DocxDocument(path)
    from docx.oxml.ns import qn
    body_paths = []
    for bi, blk in enumerate(wp_docx._blocks(d.element.body)):
        if blk.tag == qn("w:p") and wp_docx.para_text(blk).strip():
            body_paths.append((bi, wp_docx.para_text(blk)))
    bi, orig = body_paths[0]
    r = c.post(f"/working-papers/{doc.id}/save", json={"changes": [{"path": str(bi), "runs": {"0": "Materiality calculation (edited)"}}]})
    j = r.get_json()
    d = DocxDocument(path)
    check(j["ok"] and j["changed"] == 1 and wp_docx.para_text(wp_docx._blocks(d.element.body)[bi]) == "Materiality calculation (edited)", "typed text is saved into the same Word file")
    # a table cell, via segments (bold applied)
    rationale = None
    for ti, blk in enumerate(wp_docx._blocks(d.element.body)):
        if blk.tag == qn("w:tbl") and "Rationale" in "".join(wp_docx.para_text(p) for p in blk.iter(qn("w:p"))):
            rationale = ti
    cellp = f"{rationale}.0.1.0"
    r = c.post(f"/working-papers/{doc.id}/save", json={"changes": [{"path": cellp, "segments": [{"t": "Revenue is stable, so ", "b": False}, {"t": "revenue", "b": True}, {"t": " is the benchmark.", "b": False}]}]})
    d = DocxDocument(path)
    cp = wp_docx.resolve(d, wp_docx.parse_path(cellp))
    runs = wp_docx.para_runs(cp)
    check(r.get_json()["ok"] and wp_docx.para_text(cp) == "Revenue is stable, so revenue is the benchmark." and sum(1 for x in runs if x.find(qn("w:rPr")) is not None and x.find(qn("w:rPr")).find(qn("w:b")) is not None) == 1,
          "a bold segment typed in a table cell is saved as a bold run")
    # structure: add a row to the risk register, then delete it
    risk = None
    for ti, blk in enumerate(wp_docx._blocks(d.element.body)):
        if blk.tag == qn("w:tbl") and "Risk identified" in "".join(wp_docx.para_text(p) for p in blk.iter(qn("w:p"))):
            risk = ti
    nrows = len(wp_docx._rows(wp_docx._blocks(d.element.body)[risk]))
    r = c.post(f"/working-papers/{doc.id}/op", json={"op": "row_add", "path": f"{risk}.1.0.0"})
    d = DocxDocument(path)
    check(r.get_json()["ok"] and len(wp_docx._rows(wp_docx._blocks(d.element.body)[risk])) == nrows + 1, "a table row can be added")
    r = c.post(f"/working-papers/{doc.id}/op", json={"op": "row_delete", "path": f"{risk}.2.0.0"})
    d = DocxDocument(path)
    check(r.get_json()["ok"] and len(wp_docx._rows(wp_docx._blocks(d.element.body)[risk])) == nrows, "and deleted again")
    n_before = len(wp_docx._blocks(d.element.body))
    c.post(f"/working-papers/{doc.id}/op", json={"op": "para_add", "path": str(bi)})
    d = DocxDocument(path)
    check(len(wp_docx._blocks(d.element.body)) == n_before + 1, "a paragraph can be added")
    # sign-off: reviewer reviews; the partner signs; further edits are refused
    second = User(username="pat", name="Pat Partner", email="pat@example.com", role="partner"); second.set_password("x"); db.session.add(second); db.session.commit()
    doc = Document.query.get(doc.id)
    doc.partner_signed_by_id = second.id; doc.partner_signed_at = dtm.datetime.utcnow(); db.session.commit()
    r = c.post(f"/working-papers/{doc.id}/save", json={"changes": [{"path": str(bi), "runs": {"0": "should not save"}}]})
    check(r.status_code == 409 and "signed off" in r.get_json()["error"], "a partner-signed working paper cannot be changed")
    page = c.get(f"/working-papers/{doc.id}/edit").get_data(as_text=True)
    check("signed off by the partner" in page, "the page says it is read-only")
    doc.partner_signed_by_id = None; doc.partner_signed_at = None; doc.reviewed_by_id = second.id; db.session.commit()
    c.post(f"/working-papers/{doc.id}/save", json={"changes": [{"path": str(bi), "runs": {"0": "Changed after review"}}]})
    check(Document.query.get(doc.id).reviewed_by_id is None, "changing a reviewed paper clears the review")
    # an uploaded file opens as a new version, the upload stays as it was
    buf = io.BytesIO(); up = DocxDocument(); up.add_paragraph("Uploaded paragraph one."); t = up.add_table(rows=2, cols=2); t.cell(0, 0).text = "A"; up.save(buf); buf.seek(0)
    c.post(f"/engagements/{eid}/documents/upload", data={"file": (buf, "notes.docx"), "category": "General"}, content_type="multipart/form-data")
    up_doc = Document.query.filter_by(engagement_id=eid, original_filename="notes.docx").first()
    r = c.get(f"/working-papers/{up_doc.id}/open", follow_redirects=False)
    docs = Document.query.filter_by(engagement_id=eid, original_filename="notes.docx").order_by(Document.version).all()
    check(r.status_code == 302 and len(docs) == 2 and docs[1].version == 2 and docs[1].in_app and not docs[0].in_app and f"/working-papers/{docs[1].id}/edit" in r.headers["Location"], "an uploaded file opens as a new version")
    c.post(f"/working-papers/{docs[1].id}/save", json={"changes": [{"path": "0", "runs": {"0": "Edited in the app."}}]})
    o = DocxDocument(os.path.join(app.config["UPLOAD_FOLDER"], docs[0].stored_filename)); n = DocxDocument(os.path.join(app.config["UPLOAD_FOLDER"], docs[1].stored_filename))
    check(o.paragraphs[0].text == "Uploaded paragraph one." and n.paragraphs[0].text == "Edited in the app.", "the original upload is unchanged")
    r = c.get(f"/working-papers/{docs[1].id}/open", follow_redirects=False)
    check(len(Document.query.filter_by(engagement_id=eid, original_filename="notes.docx").all()) == 2, "opening the in-app version again does not copy it")
    # Excel
    tx = DocumentTemplate.query.filter_by(ref_code="TX-03", type="Tax Advisory").first()
    e2 = Engagement(client_id=cl.id, title="CGT on Stand 5", type="Tax Advisory", partner_id=aid, period_end=dtm.date(2025, 12, 31), acceptance_required=False); db.session.add(e2); db.session.commit()
    r = c.post(f"/working-papers/engagements/{e2.id}/generate/{tx.id}", follow_redirects=False)
    xd = Document.query.filter_by(engagement_id=e2.id).first()
    xp = os.path.join(app.config["UPLOAD_FOLDER"], xd.stored_filename)
    ws = openpyxl.load_workbook(xp).active
    check(r.status_code == 302 and ws["E5"].value == "Penanel Trading (Private) Limited" and ws["B5"].value == "CGT on Stand 5" and ws["E6"].value == admin.name and ws["B7"].value == "2025", "the Excel paper gets client, file reference, preparer and tax year: %s" % [ws["E5"].value, ws["B5"].value, ws["E6"].value, ws["B7"].value])
    page = c.get(f"/working-papers/{xd.id}/edit").get_data(as_text=True)
    check('id="xl"' in page and "IFERROR" in page and "CGT Computation" in page, "the Excel paper opens as a grid")
    r = c.post(f"/working-papers/{xd.id}/save", json={"changes": [{"sheet": "CGT Computation", "cell": "B13", "value": "1,000,000"}, {"sheet": "CGT Computation", "cell": "B14", "value": "01/03/2015"},
                                                                  {"sheet": "CGT Computation", "cell": "B19", "value": "30/06/2025"}, {"sheet": "CGT Computation", "cell": "B21", "value": "0.05"}, {"sheet": "CGT Computation", "cell": "B16", "value": "=B13+B15+1"}]})
    ws = openpyxl.load_workbook(xp).active
    check(r.get_json()["ok"] and ws["B13"].value == 1000000 and ws["B14"].value == dtm.datetime(2015, 3, 1) and ws["B14"].number_format == "dd/mm/yyyy" and ws["B21"].value == 0.05 and ws["B16"].value == "=B13+B15+1" and ws["B20"].value == "=IFERROR((B19-B14)/365,0)",
          "typed numbers, dates and formulas are written into the workbook: %s %s" % (ws["B13"].value, ws["B14"].value))
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
