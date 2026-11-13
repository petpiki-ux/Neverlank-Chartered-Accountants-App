"""The Managing Partner's e-signature on the printed documents, the PDFs, the ZIMRA letters, the Report to Management letter and the Word
template library."""
import os, sys, tempfile, io, shutil
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
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
import brand_docs as brand
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

check(brand.signature_bytes() and brand.signature_bytes()[:4] == b"\x89PNG", "the signature image is on the server and is a PNG")
check(not os.path.exists(os.path.join(os.path.dirname(brand.SIGNATURE_PATH), "..", "static", "img", "managing_partner_signature.png")), "it is not in the public static folder")
check(brand.is_managing_partner("Petros Piki") and brand.is_managing_partner("Petros Piki, CA(Z), MSc") and brand.is_managing_partner("PETROS  PIKI")
      and not brand.is_managing_partner("Tendai Moyo") and not brand.is_managing_partner("") and not brand.is_managing_partner(None), "who counts as the Managing Partner")

with app.app_context():
    mp = User(username="mp", name="Petros Piki", role="admin"); mp.set_password("x")
    stf = User(username="stf", name="Other Partner", role="partner"); stf.set_password("x")
    db.session.add_all([mp, stf]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=mp.id); db.session.add(cl); db.session.commit()
    def login(uid):
        c = app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(uid); s["_fresh"] = True
        return c
    M = login(mp.id)
    sig_marker = "data:image/png;base64,"

    # ---- printed documents: unsigned until the Managing Partner presses Sign
    S = login(stf.id)
    r = M.post("/quotations/new", data={"client_id": cl.id, "issue_date": "2026-10-01", "valid_until": "2026-10-31", "currency": "USD", "subject": "Audit 2026"}, follow_redirects=False)
    q = Quotation.query.first()
    db.session.add(QuotationLineItem(quotation_id=q.id, description="Audit fee", quantity=1, unit_price=1000)); db.session.commit()
    r = M.get(f"/quotations/{q.id}/print"); h = r.get_data(as_text=True)
    check(r.status_code == 200 and sig_marker not in h and "Accepted for and on behalf of Acme Holdings" in h, "a new Quotation prints unsigned")
    check("Sign as Managing Partner" in h, "...and offers the Admin a Sign button")
    check("Sign as Managing Partner" not in S.get(f"/quotations/{q.id}/print").get_data(as_text=True), "...but not to a staff member")
    check(S.post(f"/sign/quotation/{q.id}").status_code == 403, "a staff member cannot sign")
    r = M.post(f"/sign/quotation/{q.id}", follow_redirects=False)
    check(r.status_code == 302, "the Admin signs the quotation")
    db.session.refresh(q)
    check(q.mp_signed_at is not None and q.mp_signed_by_id == mp.id, "who and when are recorded")
    h = M.get(f"/quotations/{q.id}/print").get_data(as_text=True)
    check(sig_marker in h and "Petros Piki CA(Z), MSc, RPA, RTA" in h and "Managing Partner" in h and "Signed " in h and h.count(sig_marker) == 1, "the signed Quotation carries the signature, name, title and date")
    check("Remove signature" in h, "...and the Admin can remove it")
    check(sig_marker in S.get(f"/quotations/{q.id}/print").get_data(as_text=True), "a staff member printing it also gets the signed copy")
    d = M.get(f"/quotations/{q.id}").get_data(as_text=True)
    check("Signed by the Managing Partner" in d, "the quotation page shows it is signed")
    # changing the quotation drops the signature
    db.session.add(QuotationLineItem(quotation_id=q.id, description="Extra", quantity=1, unit_price=50)); db.session.commit()
    h = M.get(f"/quotations/{q.id}/print").get_data(as_text=True)
    check(sig_marker not in h and "Sign as Managing Partner" in h, "after the amount changes the signature no longer shows until signed again")
    M.post(f"/sign/quotation/{q.id}"); M.post(f"/sign/quotation/{q.id}/remove")
    check(sig_marker not in M.get(f"/quotations/{q.id}/print").get_data(as_text=True), "removing the signature works")
    M.post(f"/sign/quotation/{q.id}")

    inv = Invoice(client_id=cl.id, invoice_number="INV-1", issue_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency="USD", status="Sent", created_by_id=mp.id)
    db.session.add(inv); db.session.commit()
    db.session.add(InvoiceLineItem(invoice_id=inv.id, description="Fee", quantity=1, unit_price=500)); db.session.commit()
    h = M.get(f"/invoices/{inv.id}/print", follow_redirects=True).get_data(as_text=True)
    check(sig_marker not in h and "Managing Partner" in h, "an unsigned Invoice has no signature block (only the Sign button)")
    from invoice_pdf import build_invoice_pdf
    from invoicing import FIRM_LETTERHEAD
    def has_image(pdf): return b"/Subtype /Image" in pdf.replace(b"/Subtype/Image", b"/Subtype /Image")
    unsigned_pdf = build_invoice_pdf(inv, FIRM_LETTERHEAD)
    M.post(f"/sign/invoice/{inv.id}")
    db.session.refresh(inv)
    h = M.get(f"/invoices/{inv.id}/print", follow_redirects=True).get_data(as_text=True)
    check(sig_marker in h, "the signed Invoice carries the signature")
    signed_pdf = build_invoice_pdf(inv, FIRM_LETTERHEAD)
    check(signed_pdf[:4] == b"%PDF" and len(signed_pdf) > len(unsigned_pdf) + 3000, "the signed invoice PDF is the unsigned one plus the signature image")
    voided = Invoice(client_id=cl.id, invoice_number="INV-2", issue_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency="USD", status="Sent", created_by_id=mp.id)
    # without the image on the server documents still work
    saved = brand.SIGNATURE_PATH
    brand.SIGNATURE_PATH = saved + ".missing"
    r = M.get(f"/quotations/{q.id}/print"); h = r.get_data(as_text=True)
    check(r.status_code == 200 and sig_marker not in h, "no image on the server: the quotation still prints")
    check(build_invoice_pdf(inv, FIRM_LETTERHEAD)[:4] == b"%PDF", "...and so does the invoice PDF")
    brand.SIGNATURE_PATH = saved

    # ---- ZIMRA reply letter (a real case and reply, so the Sign button and the signed check both run)
    import zimra_letters as zl
    import docx
    DRAW = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"
    def n_pics(data):
        d = docx.Document(io.BytesIO(data))
        return len(d.element.body.findall(".//" + DRAW)) + sum(len(sec.header._element.findall(".//" + DRAW)) for sec in d.sections)
    def has_image(pdf): return b"/Subtype /Image" in pdf.replace(b"/Subtype/Image", b"/Subtype /Image")
    case = ZimraCase(client_id=cl.id, case_number="ZC-1", title="Objection", created_by_id=mp.id) if "ZimraCase" in globals() else None
    check(case is not None, "the ZIMRA case model exists")
    db.session.add(case); db.session.commit()
    def new_reply(signatory):
        rp = ZimraReply(case_id=case.id, our_reference="NL/1", letter_date=date(2026, 10, 5), addressee="The Commissioner\nZIMRA", salutation="Dear Sir",
                        subject="Objection", body="We write in reply.\n\n- point one", status="Approved", signatory=signatory,
                        signatory_title="Neverlank Chartered Accountants")
        db.session.add(rp); db.session.commit()
        return rp
    mp_reply, other_reply = new_reply("Petros Piki CA(Z), MSc"), new_reply("Tendai Moyo")
    base = n_pics(zl.build_docx(other_reply))
    check(n_pics(zl.build_docx(mp_reply)) == base, "a letter signed by the Managing Partner is unsigned until he presses Sign (Word)")
    unsigned_pdf = zl.build_pdf(mp_reply)
    check(M.post(f"/sign/zimra_reply/{other_reply.id}").status_code == 302 and other_reply.mp_signed_at is None, "a letter signed by someone else cannot be signed with the Managing Partner's signature")
    M.post(f"/sign/zimra_reply/{mp_reply.id}"); db.session.refresh(mp_reply)
    check(mp_reply.mp_signed_at is not None, "the Admin signs the letter")
    check(n_pics(zl.build_docx(mp_reply)) == base + 1, "the signed letter carries the signature (Word)")
    signed_pdf = zl.build_pdf(mp_reply)
    check(signed_pdf[:4] == b"%PDF" and len(signed_pdf) > len(unsigned_pdf) + 3000, "...and in the PDF")
    mp_reply.body += "\n\nAnother paragraph."; db.session.commit()
    check(n_pics(zl.build_docx(mp_reply)) == base, "editing the letter after signing drops the signature")

    # ---- Report to Management letter
    import workpapers as wp
    eng = Engagement(client_id=cl.id, title="Audit 2026", type="Audit", partner_id=mp.id, period_end=date(2026, 12, 31), acceptance_required=False)
    db.session.add(eng); db.session.commit()
    def rtm_data():
        out = wp.build_report_to_management_docx(eng, WorkpaperNarrative.query.filter_by(engagement_id=eng.id, kind="report_to_management").first())
        return out.getvalue() if hasattr(out, "getvalue") else out
    base_pics = n_pics(rtm_data())
    check(S.post(f"/sign/report_to_management/engagement/{eng.id}").status_code == 403, "staff cannot sign the Report to Management")
    M.post(f"/sign/report_to_management/engagement/{eng.id}")
    data = rtm_data()
    check(n_pics(data) == base_pics + 1, "the Report to Management letter carries the signature once signed")
    check("Petros Piki CA(Z), MSc, RPA, RTA" in "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs), "...above the Managing Partner's name")
    page = M.get(f"/engagements/{eng.id}", query_string={"tab": "finalisation"}).get_data(as_text=True)
    check("Signed by the Managing Partner" in page and "Remove signature" in page, "the Finalisation tab shows it is signed")

    # ---- template library
    from doc_templates import _sign_template_file, _template_dir
    tpl_rows = DocumentTemplate.query.all()
    check(len(tpl_rows) > 0, "the template library is seeded")
    results = {t.title: _sign_template_file(t) for t in tpl_rows}
    check(any(v == "done" for v in results.values()), "engagement letters were signed")
    check(S.post("/document-templates/add-signature-all").status_code == 403, "only an Admin can add the signature to the template library")
    check(all(v in ("done", "already", "none", "skip") for v in results.values()), "no template failed")
    fa01 = next(t for t in tpl_rows if t.ref_code == "FA-01" and t.type == "Audit") if any(t.ref_code == "FA-01" for t in tpl_rows) else None
    if fa01:
        d = docx.Document(os.path.join(_template_dir(fa01.type), fa01.filename))
        sig_t = [t for t in d.tables if t.rows[0].cells[0].text.strip() == "Role / Name"][0]
        check(sig_t.rows[1].cells[1]._tc.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"), "FA-01: Neverlank's signature cell holds the picture")
        check(not sig_t.rows[2].cells[1]._tc.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"), "FA-01: the client representative's cell stays empty")
    again = {t.title: _sign_template_file(t) for t in tpl_rows}
    check(not any(v == "done" for v in again.values()), "running it again signs nothing twice")
    check(_sign_template_file(next(t for t in tpl_rows if t.file_ext == "xlsx")) == "skip" if any(t.file_ext == "xlsx" for t in tpl_rows) else True, "Excel templates are left alone")

print(f"\n{len(fails)} failure(s)")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
