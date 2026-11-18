"""AI Report Writer on the Tax Advisory Finalisation tab: drafting (AI simulated), the safeguards, editing, review, Managing Partner signing, Word download."""
import os, sys, tempfile, io, zipfile, datetime as dtm
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
import brand_docs
import tax_reports as TR
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
if not os.path.exists(brand_docs.SIGNATURE_PATH):
    from PIL import Image
    _png = os.path.join(tempfile.mkdtemp(prefix="sig_"), "sig.png")
    Image.new("RGBA", (294, 178), (20, 20, 120, 255)).save(_png)
    brand_docs.SIGNATURE_PATH = _png
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    cl = Client(name="Kudzai Traders (Pvt) Ltd", tin_number="2000123456", vat_number="10011223", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Tax advisory 2026", type=TAX_ADVISORY_TYPE, partner_id=aid, period_end=dtm.date(2026, 12, 31), acceptance_required=False)
    db.session.add(e); db.session.commit(); eid = e.id
    db.session.add(TaxResearchLogEntry(engagement_id=eid, topic="Interest deductibility", question="Is interest on the shareholder loan deductible?",
                   research_notes="Loan applied to stock purchases.", conclusion="Deductible if incurred in the production of income.", references="s15(2)(a)"))
    db.session.add(TaxPosition(engagement_id=eid, tax_head="Corporate Income Tax (CIT)", position_description="Interest fully deductible", classification="Probable", estimated_exposure=48250.0))
    db.session.add(PenaltyInterestCalculation(engagement_id=eid, tax_head="Value Added Tax (VAT)", principal_amount=10000.0, days_late=30, rate_percent_used=10.0, computed_amount=82.19))
    db.session.commit()
    upd = LegislativeUpdate(title="Income Tax Act [Chapter 23:06]", instrument_type="Act", tax_head="Corporate Income Tax (CIT)", source_reference="Chapter 23:06",
                            summary="Section 15(2)(a): deductions for expenditure incurred in the production of income.",
                            extracted_text="15. General deductions. (2) The following shall be deductible: (a) expenditure and losses to the extent to which they are incurred for the purposes of trade.",
                            extraction_status="extracted")
    db.session.add(upd); db.session.commit(); uid_ = upd.id
    # an uploaded engagement document the AI can read
    updir = app.config["UPLOAD_FOLDER"]; os.makedirs(updir, exist_ok=True)
    import docx as _docx
    _d = _docx.Document(); _d.add_paragraph("Loan agreement dated 1 March 2024. Principal USD 150,000 at 8% per annum."); _d.save(os.path.join(updir, "loan.docx"))
    dc = Document(engagement_id=eid, original_filename="Loan agreement.docx", stored_filename="loan.docx", category="General", uploaded_by_id=aid)
    db.session.add(dc); db.session.commit(); did = dc.id
    staff = User(username="staff1", name="Staff One", email="s1@x.co", role="staff"); staff.set_password("pw")
    db.session.add(staff); db.session.commit(); sid = staff.id
    e.team_members.append(staff); db.session.commit()
    outsider = User(username="out1", name="Out Sider", email="o1@x.co", role="staff"); outsider.set_password("pw"); db.session.add(outsider); db.session.commit(); oid = outsider.id

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
admin, staff_c, out_c = login(aid), login(sid), login(oid)

seen = {}
def fake_model(system, message, tool, max_tokens=6000):
    seen.update(system=system, message=message, tool=tool["name"])
    if tool["name"] == "rewrite_report_section":
        return {"text": "Firmer wording. The deduction rests on the Act [#%d]." % uid_, "citations": [{"update_id": uid_, "used_for": "s15"}], "missing_information": []}, None
    return {
        "sections": [
            {"heading": "Issue", "text": "Whether interest paid by {client_name} (TIN {client_tin}) is deductible."},
            {"heading": "Analysis", "text": "Under the Income Tax Act expenditure incurred for trade is deductible [#%d]. The interest was USD 12,000 on the loan, "
                                           "and section 98 of the Customs Act may also apply. A made-up figure of 7,654,321 was also paid [#9999]." % uid_},
            {"heading": "Conclusion", "text": "Dear Sirs\n\nThe interest is deductible.\n\nYours faithfully"},
        ],
        "risk_rows": [{"tax_head": "CIT", "issue": "Interest disallowed", "exposure": "USD 48,250.00", "likelihood": "Medium", "mitigation": "Keep the loan agreement"}],
        "citations": [{"update_id": uid_, "used_for": "The deduction rule"}, {"update_id": 424242, "used_for": "not supplied"}],
        "missing_information": ["Date the loan was drawn down"], "reviewer_notes": ["The lender is a related party."]}, None
TR._call_model = fake_model
TR.is_configured = lambda: True
TR.library_pairs = lambda q, limit=14: [(LegislativeUpdate.query.get(uid_), None)] if q else []

with app.app_context():
    page = admin.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
    check("AI Report Writer" in page and "Write the first draft" in page, "Finalisation tab shows the AI Report Writer for a Tax Advisory engagement")
    check("Loan agreement.docx" in page, "uploaded documents offered to the AI")
    opts = ["Tax advisory opinion / memo", "Advisory report to the client (letter)", "Tax position and risk review", "ZIMRA submission / objection draft"]
    check(all(o in page for o in opts), "all four report kinds offered")

    r = admin.post(f"/tax-reports/engagement/{eid}/new", data={"kind": "opinion", "instructions": "Interest on the loan from the director is deductible; take a firm line.",
                                                              "doc_ids": [str(did)], "title": "Opinion on interest"})
    check(r.status_code == 302 and "/tax-reports/" in r.headers["Location"], "creating a report redirects to its editor")
    rep = TaxAdvisoryReport.query.filter_by(engagement_id=eid).first(); rid = rep.id
    d = rep.data
    msg = seen["message"]
    check("Kudzai Traders" not in msg and "2000123456" not in msg and "10011223" not in msg, "the client's name, TIN and VAT number never reach the AI")
    check("Interest deductibility" in msg and "Interest fully deductible" in msg and "30 days late" in msg, "research log, positions and penalty runs are sent")
    check("Principal USD 150,000" in msg, "text of the ticked uploaded document is sent")
    check("firm line" in msg and f"[Update #{uid_}]" in msg, "partner's instructions and the Firm Library excerpt are sent")
    check(rep.ai_used and rep.status == "Draft" and len(d["sections"]) == 3, "AI draft stored as a Draft with its sections")
    s0 = d["sections"][0]["text"]
    check("Kudzai Traders (Pvt) Ltd" in s0 and "2000123456" in s0 and "{client_" not in s0, "tokens replaced by the real name and TIN in the draft")
    s1 = d["sections"][1]["text"]
    check(f"[#{uid_}]" in s1 and "[#9999]" not in s1, "citation to a supplied excerpt kept; to one not supplied removed")
    check("[CONFIRM: verify 7,654,321" in s1, "a figure not in the material is flagged")
    check("[CONFIRM: verify section 98" in s1, "a legal reference not in the Library is flagged")
    check("USD 12,000" in s1 and "[CONFIRM: verify 12,000" in s1, "an unsupported figure (12,000) is flagged too")
    check(not d["sections"][2]["text"].lower().startswith("dear"), "salutation and closing stripped from the wording")
    check([x["id"] for x in d["sources"]] == [uid_], "only the supplied excerpt remains as a source")
    check(any("not supplied" in f for f in d["flags"]) and d["missing"] == ["Date the loan was drawn down"], "flags and missing information kept for the reviewer")
    check(rep.open_placeholders >= 3, "placeholders counted")

    # permissions
    check(out_c.get(f"/tax-reports/{rid}").status_code == 403, "someone not on the engagement team cannot open the report")
    check(staff_c.get(f"/tax-reports/{rid}").status_code == 200, "a team member can open it")
    ed = admin.get(f"/tax-reports/{rid}").get_data(as_text=True)
    check("Income Tax Act [Chapter 23:06]" in ed and "Check before you rely on this draft" in ed and "Date the loan was drawn down" in ed, "editor shows the sources, flags and missing information")
    lib = admin.get(f"/tax-reports/{rid}?q=deduction").get_data(as_text=True)
    check("[#%d] Income Tax Act" % uid_ in lib, "Firm Library search inside the editor")

    # cannot be reviewed or signed with placeholders
    check("placeholder" in admin.post(f"/tax-reports/{rid}/review", follow_redirects=True).get_data(as_text=True), "review refused while [CONFIRM] placeholders remain")
    check(TaxAdvisoryReport.query.get(rid).status == "Draft", "still a draft")

    # edit: clear the placeholders
    import re
    def clean(t): return re.sub(r"\s*\[CONFIRM:[^\]]*\]", "", t)
    form = {"title": "Opinion on interest", "addressee": "", "reference": "OP-1", "instructions": rep.instructions, "n_sections": "3", "action": "save"}
    for i, s in enumerate(TaxAdvisoryReport.query.get(rid).data["sections"]):
        form[f"heading_{i}"] = s["heading"]; form[f"text_{i}"] = clean(s["text"])
    r = admin.post(f"/tax-reports/{rid}/save", data=form, follow_redirects=True)
    rep = TaxAdvisoryReport.query.get(rid)
    check(rep.open_placeholders == 0 and rep.reference == "OP-1", "edits saved, placeholders cleared")
    check(staff_c.post(f"/tax-reports/{rid}/review").status_code == 403, "staff cannot mark a report reviewed")
    # Managing Partner signing needs review first
    check("reviewed" in admin.post(f"/sign/tax_report/{rid}", follow_redirects=True).get_data(as_text=True).lower() and not brand_docs.signature_valid(TaxAdvisoryReport.query.get(rid)), "cannot be signed before it is reviewed")
    admin.post(f"/tax-reports/{rid}/review")
    check(TaxAdvisoryReport.query.get(rid).status == "Reviewed", "Partner marks it reviewed")
    admin.post(f"/sign/tax_report/{rid}")
    check(brand_docs.signature_valid(TaxAdvisoryReport.query.get(rid)), "Managing Partner signs the reviewed report")
    # word file
    r = admin.get(f"/tax-reports/{rid}/docx")
    z = zipfile.ZipFile(io.BytesIO(r.data)); xml = z.read("word/document.xml").decode("utf8")
    check(r.status_code == 200 and "Analysis" in xml and "Sources (Firm Library)" in xml and "Income Tax Act" in xml, "Word file built with the sections and numbered sources")
    check("[#" not in xml and "[1]" in xml, "[#id] markers become [1] in the Word file")
    check("DRAFT - not yet reviewed" not in xml, "no draft banner on a reviewed report")
    # editing after signing withdraws the signature
    form["text_2"] = "The interest is deductible, subject to the loan agreement."
    admin.post(f"/tax-reports/{rid}/save", data=form)
    rep = TaxAdvisoryReport.query.get(rid)
    check(not brand_docs.signature_valid(rep) and rep.status == "Draft", "editing a signed report puts it back to Draft and withdraws the signature")

    # section rewrite
    r = admin.post(f"/tax-reports/{rid}/section/1/rewrite", data={"instruction": "make this firmer"}, follow_redirects=True)
    check("Firmer wording" in TaxAdvisoryReport.query.get(rid).data["sections"][1]["text"] and seen["tool"] == "rewrite_report_section", "one section rewritten by the AI on instruction")
    # regenerate keeps the previous wording; restore
    admin.post(f"/tax-reports/{rid}/regenerate", data={"extra": "take a softer line"})
    rep = TaxAdvisoryReport.query.get(rid)
    check("softer line" in seen["message"] and rep.data.get("previous"), "regenerate sends the extra instruction and keeps the previous draft")
    admin.post(f"/tax-reports/{rid}/restore")
    check("Firmer wording" in TaxAdvisoryReport.query.get(rid).data["sections"][1]["text"], "previous draft restored")

    # the other three kinds
    for kind in ("client_report", "risk_review", "zimra_submission"):
        r = admin.post(f"/tax-reports/engagement/{eid}/new", data={"kind": kind, "instructions": "x", "addressee": "The Commissioner General\nZIMRA\nHarare"})
        rr = TaxAdvisoryReport.query.filter_by(engagement_id=eid, kind=kind).first()
        check(rr is not None and rr.ai_used and rr.data["sections"], f"{kind}: drafted")
        w = admin.get(f"/tax-reports/{rr.id}/docx")
        x = zipfile.ZipFile(io.BytesIO(w.data)).read("word/document.xml").decode("utf8")
        check(w.status_code == 200 and "DRAFT - not yet reviewed" in x, f"{kind}: Word file marked as an unreviewed draft")
        if kind == "risk_review":
            check(rr.data["risk_rows"] and "Risk register" in x and "Interest disallowed" in x, "risk review carries its risk register in the Word file")
        if kind in ("client_report", "zimra_submission"):
            check("Yours faithfully" in x and "Commissioner General" in x and ("Dear Sir/Madam" in x or "Dear Sirs" in x), f"{kind}: letter format with addressee, salutation and closing")

    # AI off: skeleton, and never overwrites an existing draft
    TR.is_configured = lambda: False
    admin.post(f"/tax-reports/engagement/{eid}/new", data={"kind": "opinion", "instructions": "Do the thing"})
    sk = TaxAdvisoryReport.query.filter_by(engagement_id=eid, kind="opinion").order_by(TaxAdvisoryReport.id.desc()).first()
    check(not sk.ai_used and sk.open_placeholders >= 5 and "not switched on" in (sk.ai_note or ""), "with the AI off, a structure with placeholders is made")
    before = TaxAdvisoryReport.query.get(rid).data_json
    admin.post(f"/tax-reports/{rid}/regenerate", data={})
    check(TaxAdvisoryReport.query.get(rid).data_json == before, "with the AI off, regenerate does not overwrite a draft")
    # AI failure keeps the draft
    TR.is_configured = lambda: True
    TR._call_model = lambda *a, **k: (None, "The AI service is busy just now. Wait a moment and try again.")
    admin.post(f"/tax-reports/{rid}/regenerate", data={})
    check(TaxAdvisoryReport.query.get(rid).data_json == before and "busy" in TaxAdvisoryReport.query.get(rid).ai_note, "an AI failure leaves the existing draft untouched")

    # delete
    check(staff_c.post(f"/tax-reports/{rid}/delete").status_code == 403, "staff who did not create it cannot delete")
    admin.post(f"/tax-reports/{rid}/delete")
    check(TaxAdvisoryReport.query.get(rid) is None, "a Partner/Admin can delete a report")
    # not offered on other engagement types
    a = Engagement(client_id=TaxAdvisoryReport.query.first().engagement.client_id, title="Audit", type="Audit", partner_id=aid, acceptance_required=False)
    db.session.add(a); db.session.commit()
    pg = admin.get(f"/engagements/{a.id}?tab=finalisation").get_data(as_text=True)
    check("AI Report Writer" not in pg, "the writer is not shown on an engagement with no tax advisory service")
print(f"{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
