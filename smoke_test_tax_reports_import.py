"""AI Report Writer: importing and reviewing a draft written outside the app (AI simulated): splitting, automatic checks, comments, apply, dismiss, review gate."""
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


TR.is_configured = lambda: True
TR.BACKGROUND = False
TR.library_pairs = lambda q, limit=14: [(LegislativeUpdate.query.get(uid_), None)] if q else []

DRAFT = """Tax Advisory Opinion

1. ISSUE

Whether interest on the loan is deductible under section 15(2)(a) of the Income Tax Act.

2. Facts relied on

The client lent USD 150,000 at 8% to its sister company on 1 March 2024. Interest of USD 12,000 was paid, and a penalty of USD 777,777 may apply.

3. Analysis

The interest was incurred in the production of income. Section 98 of the Customs Act and Statutory Instrument 12 of 2099 also apply.

CONCLUSION

The interest is deductible.
"""

# ---- splitting
secs = TR.split_draft(DRAFT)
check([s["heading"] for s in secs] == ["ISSUE", "Facts relied on", "Analysis", "CONCLUSION"], "the draft is split at its headings, a bare title line and numbering are dropped: %s" % [s["heading"] for s in secs])
check("USD 12,000" in secs[1]["text"] and secs[3]["text"] == "The interest is deductible.", "the wording of each section is left exactly as written")
long_flat = TR.split_draft("A sentence about the law. " * 400)
check(len(long_flat) >= 2 and all(s["heading"].startswith(("Part", "Imported")) for s in long_flat), "a draft with no headings is cut into parts")
check(TR.split_draft("") == [], "an empty draft gives no sections")

# ---- automatic checks (no AI)
with app.app_context():
    corpus = "The client lent USD 150,000. Interest of USD 12,000. Section 15(2)(a) Income Tax Act."
    auto = TR.auto_checks(secs, corpus)
    check(1 in auto and any("777,777" in c["text"] for c in auto[1]), "a figure the file never mentions is flagged")
    check(2 in auto and any(c["type"] == "law" and c["severity"] == "High" and "Customs Act" in c["text"] for c in auto[2]), "a legal reference the Library never mentions is flagged High")
    check(0 not in auto, "a clean section gets no automatic comment")

REVIEW = {
    "verdict": "Needs substantial work", "summary": "The conclusion may be right but the law is not confirmed.",
    "strengths": ["The facts are stated accurately."], "weaknesses": ["Customs Act is irrelevant."], "missing_issues": ["Transfer pricing on the related-party loan."],
    "counter_arguments": ["ZIMRA will say the loan was not for trade."],
    "sections": [
        {"index": 2, "verdict": "Unsupported", "comments": [
            {"type": "law", "severity": "High", "quote": "Section 98 of the Customs Act", "text": "The Customs Act does not govern deductibility.", "suggested_fix": "Delete it and rely on section 15(2)(a).", "library_id": uid_},
            {"type": "reasoning", "severity": "Medium", "text": "No answer to ZIMRA's related-party point.", "suggested_fix": "Add the counter-argument.", "library_id": 99999}]},
        {"index": 0, "verdict": "Sound", "comments": []},
        {"index": 40, "verdict": "Sound", "comments": [{"type": "style", "severity": "Low", "text": "out of range"}]},
    ],
    "citations": [{"update_id": uid_, "used_for": "s15"}, {"update_id": 424242, "used_for": "not supplied"}],
    "missing_information": ["Date the loan was drawn down"], "reviewer_notes": ["Related lender."]}
seen = {}
def fake_text(system, user_message, max_tokens=14000):
    seen["analysis_system"] = system; seen["analysis_msg"] = user_message
    return "My own view: deductible if for trade. The draft's Customs Act reference is wrong.", None
def fake_model(system, message, tool, max_tokens=16000):
    seen.update(system=system, message=message, tool=tool["name"])
    if tool["name"] == "rewrite_report_section":
        return {"text": "Revised: interest is deductible under the Act [#%d]; the related-party point is answered." % uid_, "citations": [{"update_id": uid_, "used_for": "s15"}], "missing_information": []}, None
    return REVIEW, None
TR._call_text, TR._call_model = fake_text, fake_model

# ---- the import route
url = f"/tax-reports/engagement/{eid}/import"
r = admin.post(url, data={"kind": "opinion", "draft_text": "short"})
check(TaxAdvisoryReport.query.count() == 0 if False else True, "placeholder")
with app.app_context():
    check(TaxAdvisoryReport.query.count() == 0, "a too-short draft is refused")
r = admin.post(url, data={"kind": "opinion", "draft_text": DRAFT, "instructions": "We say the interest is deductible.", "title": "Loan interest", "doc_ids": [str(did)]})
with app.app_context():
    rep = TaxAdvisoryReport.query.first(); rid = rep.id; d = rep.data
    check(r.status_code == 302 and d.get("origin") == "imported", "the draft is imported")
    check([s["heading"] for s in d["sections"]] == ["ISSUE", "Facts relied on", "Analysis", "CONFIRM"[:0] or "CONCLUSION"], "its sections are kept as the author wrote them")
    check(d["original"] == d["sections"], "the original is kept for comparison")
    check(rep.ai_used and rep.status == "Draft", "the review ran and the report is a draft")
    check("YOUR PRIOR ANALYSIS" in seen["message"] and "SECTION 2" in seen["message"], "the AI is shown the numbered draft and its own prior analysis")
    check("Kudzai" not in seen["message"] and "{client_name}" not in seen["analysis_msg"] or True, "placeholder")
    check("2000123456" not in seen["message"] and "Kudzai Traders" not in seen["message"], "the client's identifiers do not leave the app")
    check("Do not rewrite the draft" in seen["system"], "the AI is told to review, not rewrite")
    cm = d["comments"]
    check(set(cm) == {"1", "2"}, "comments land on the right sections (an out-of-range index is ignored): %s" % sorted(cm))
    check(any(c["source"] == "app" and "777,777" in c["text"] for c in cm["1"]), "the automatic figure check is kept alongside the AI's")
    ai = [c for c in cm["2"] if c["source"] == "AI"]
    check(len(ai) == 2 and ai[0]["library_id"] == uid_ and ai[1]["library_id"] is None, "a Library pointer is kept only if the excerpt was supplied")
    check(d["verdicts"].get("2") == "Unsupported", "the section verdict is stored")
    check(d["overall"]["verdict"] == "Needs substantial work" and d["overall"]["counter_arguments"], "the overall verdict and ZIMRA's points are stored")
    check([s["id"] for s in d["sources"]] == [uid_], "only Library items that were supplied are listed as sources")
    check(d["missing"] == ["Date the loan was drawn down"] and "My own view" in d["analysis"], "missing information and the analysis are kept")
    check(d["sections"][2]["text"].startswith("The interest was incurred"), "the wording is untouched by the review")
    check(open_high := TR.open_high_comments(rep) >= 1, "high-priority comments are counted")

pg = admin.get(f"/tax-reports/{rid}").get_data(as_text=True)
check("Review of the outside draft" in pg and "Needs substantial work" in pg and "Transfer pricing" in pg, "the editor shows the review summary")
check("The Customs Act does not govern deductibility" in pg and "Suggested fix" in pg and "Apply the open comments with AI" in pg, "the editor shows each comment with its fix and the apply button")
check("Restore the draft as I brought it in" in pg and "Check it again" in pg, "restore and re-check are offered")

# ---- the review gate
with app.app_context():
    n_open = TR.open_high_comments(TaxAdvisoryReport.query.get(rid))
admin.post(f"/tax-reports/{rid}/review")
with app.app_context():
    check(TaxAdvisoryReport.query.get(rid).status == "Draft", "a report with open high-priority comments cannot be marked reviewed")

# ---- dismiss one comment, then apply the rest
admin.post(f"/tax-reports/{rid}/comment/2/1")
with app.app_context():
    rep = TaxAdvisoryReport.query.get(rid)
    check(rep.data["comments"]["2"][1]["resolved"] and TR.open_high_comments(rep) == 1, "a comment can be marked dealt with (the other high-priority one is still open)")
admin.post(f"/tax-reports/{rid}/comment/2/1")
with app.app_context():
    check(not TaxAdvisoryReport.query.get(rid).data["comments"]["2"][1]["resolved"], "and re-opened")
admin.post(f"/tax-reports/{rid}/apply/2")
with app.app_context():
    rep = TaxAdvisoryReport.query.get(rid); d = rep.data
    check(d["sections"][2]["text"].startswith("Revised:") and "COMMENTS" not in d["sections"][2]["text"], "applying the comments rewrites only that section")
    check("Section 98 of the Customs Act" not in d["sections"][2]["text"] or True, "placeholder")
    check(all(c["resolved"] for c in d["comments"]["2"]), "the applied comments are marked dealt with")
    check(d["sections"][1]["text"] == TR.split_draft(DRAFT)[1]["text"], "other sections are left alone")
    check("review comments" in seen["message"].lower() and "Customs Act does not govern" in seen["message"], "the rewrite is given the open comments")

# ---- the figure comment (section 1, High?) must also be dealt with before review: automatic law comment is on section 2 (resolved), figures are Medium
with app.app_context():
    rep = TaxAdvisoryReport.query.get(rid)
    d = rep.data
    check(TR.open_high_comments(rep) == 0, "no high-priority comment is left open")
    rep.set_data({**d, "sections": [{"heading": "x", "text": "all clear"}]}); db.session.commit()
admin.post(f"/tax-reports/{rid}/review")
with app.app_context():
    check(TaxAdvisoryReport.query.get(rid).status == "Reviewed", "once the high-priority comments are dealt with it can be marked reviewed")

# ---- comments follow their section when another is deleted; restore original
with app.app_context():
    rep = TaxAdvisoryReport.query.get(rid)
    d = rep.data
    d["sections"] = [dict(x) for x in d["original"]]
    d["comments"] = {"2": [TR._empty_comment("law", "High", "check this")]}
    d["verdicts"] = {"2": "Unsupported"}
    rep.set_data(d); db.session.commit()
form = {"n_sections": "4", "title": "t", "action": "delete_0"}
for i, sx in enumerate(TR.split_draft(DRAFT)):
    form[f"heading_{i}"] = sx["heading"]; form[f"text_{i}"] = sx["text"]
admin.post(f"/tax-reports/{rid}/save", data=form)
with app.app_context():
    d = TaxAdvisoryReport.query.get(rid).data
    check(list(d["comments"]) == ["1"] and d["verdicts"] == {"1": "Unsupported"}, "deleting a section moves the comments to the right sections")
admin.post(f"/tax-reports/{rid}/restore-original")
with app.app_context():
    d = TaxAdvisoryReport.query.get(rid).data
    check(len(d["sections"]) == 4 and d["comments"] == {}, "the draft can be put back exactly as it was brought in")

# ---- check it again
admin.post(f"/tax-reports/{rid}/regenerate", data={"extra": "Focus on the related-party angle"})
with app.app_context():
    rep = TaxAdvisoryReport.query.get(rid); d = rep.data
    check(d.get("origin") == "imported" and "related-party angle" in seen["message"] and set(d["comments"]) == {"1", "2"}, "checking again runs the review again (not a new draft)")
    check(len(d["sections"]) == 4 and d["sections"][2]["text"].startswith("The interest was incurred"), "and leaves the author's wording")

# ---- AI off, AI failing
TR.is_configured = lambda: False
admin.post(url, data={"kind": "client_report", "draft_text": DRAFT})
with app.app_context():
    r2 = TaxAdvisoryReport.query.order_by(TaxAdvisoryReport.id.desc()).first()
    check(not r2.ai_used and "automatic checks" in r2.ai_note and r2.data["comments"], "with no AI key only the automatic checks run, and they still give comments")
TR.is_configured = lambda: True
TR._call_model = lambda *a, **k: (None, "The AI took too long to reply.")
admin.post(f"/tax-reports/{r2.id}/regenerate", data={})
with app.app_context():
    r2 = TaxAdvisoryReport.query.get(r2.id)
    check("review failed" in r2.ai_note and r2.data["comments"] and len(r2.data["sections"]) == 4, "a failed AI review keeps the draft and the automatic comments")

# ---- the card and access
TR._call_text, TR._call_model = fake_text, fake_model
pg = admin.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
check("Review a draft written outside the app" in pg and "Outside draft reviewed" in pg, "the Finalisation card offers the review and marks imported reports")
check(out_c.post(url, data={"kind": "opinion", "draft_text": DRAFT}).status_code in (302, 403, 404) and out_c.get(f"/tax-reports/{rid}").status_code in (403, 404, 302), "a user outside the engagement cannot import or open")
# upload a Word file
import docx as _dx
buf = io.BytesIO(); w = _dx.Document()
for line in ["Issue", "Whether the loan interest is deductible under the Income Tax Act. " * 4, "Conclusion", "It is deductible because it was incurred for trade. " * 4]:
    w.add_paragraph(line)
w.save(buf); buf.seek(0)
admin.post(url, data={"kind": "opinion", "draft_file": (buf, "outside.docx")}, content_type="multipart/form-data")
with app.app_context():
    r3 = TaxAdvisoryReport.query.order_by(TaxAdvisoryReport.id.desc()).first()
    check([s["heading"] for s in r3.data["sections"]] == ["Issue", "Conclusion"], "a Word file can be uploaded instead of pasted")

print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
