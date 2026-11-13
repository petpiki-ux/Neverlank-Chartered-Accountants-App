"""The Notes to the Financial Statements carry a running page header: entity name, NOTES TO THE FINANCIAL STATEMENTS, year-end line, blue rule."""
import os, sys, tempfile, io
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datetime as dtm
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def htext(sec): return [p.text for p in sec.header.paragraphs]
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Penanel Trading (Private) Limited", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY24 audit", type="Audit", partner_id=admin.id, period_end=dtm.date(2024, 12, 31), reporting_framework="ifrs_for_smes")
    e2 = Engagement(client_id=cl.id, title="FY24 local", type="Audit", partner_id=admin.id, period_end=dtm.date(2024, 12, 31), reporting_framework="other")
    db.session.add_all([e, e2]); db.session.commit()
    lines = [TrialBalanceLine(account_name="Cash", fs_category="cash", current_debit=5000, prior_debit=100),
             TrialBalanceLine(account_name="Share capital", fs_category="share_capital", current_credit=5000, prior_credit=100)]
    st = fin.build_all_statements(lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect")
    d = DocxDocument(wp.build_financial_statements_docx(e, st, None))
    secs = d.sections
    check(len(secs) == 3, f"cover/statements, notes and sign-off are three sections: {len(secs)}")
    check(not any(t.strip() for t in htext(secs[0])), "the pages before the notes have no running header")
    check(htext(secs[1]) == ["PENANEL TRADING (PRIVATE) LIMITED", "NOTES TO THE FINANCIAL STATEMENTS", "For the year ended 31 December 2024"], "notes header: %s" % htext(secs[1]))
    check(not secs[1].header.is_linked_to_previous, "the notes header belongs to the notes section only")
    check('w:bottom' in secs[1].header.paragraphs[2]._p.xml and "2E75B6" in secs[1].header.paragraphs[2]._p.xml, "a thin blue rule sits under the last line")
    check(secs[1].different_first_page_header_footer is False, "the notes' first page has the header too (the cover's own 'no header' is not inherited)")
    check(secs[1].footer.is_linked_to_previous and any("NUMPAGES" in p._p.xml for p in secs[0].footer.paragraphs), "the pack footer carries through")
    check(not any(t.strip() for t in htext(secs[2])) and not secs[2].header.is_linked_to_previous, "the sign-off section has no notes header")
    check(secs[1].start_type == 2 or str(secs[1].start_type).endswith("NEW_PAGE (2)") or "NEW_PAGE" in str(secs[1].start_type), "the notes start on a new page: %s" % secs[1].start_type)
    check(secs[0].different_first_page_header_footer is True, "the cover page still has no footer")
    # a local-GAAP engagement: header only when it has notes text
    st2 = fin.build_all_statements(lines, [], reporting_framework="other", cash_flow_method="indirect")
    d2 = DocxDocument(wp.build_financial_statements_docx(e2, st2, None))
    check(len(d2.sections) == 1, "no notes -> no notes section or header")
    class FS:  # a stand-in saved record with free-text notes
        notes_to_financial_statements = "1. Basis\nPrepared on the historical cost basis."
        basis_of_preparation = ""; is_reviewed = False; is_partner_signed = False; completed_by = None; completed_at = None
    d3 = DocxDocument(wp.build_financial_statements_docx(e2, st2, FS()))
    check(len(d3.sections) == 3 and htext(d3.sections[1])[1] == "NOTES TO THE FINANCIAL STATEMENTS", "free-text notes get the same header")
    # a long name, an unnamed client and a missing year end do not break it
    cl.name = "The Very Long Named Agricultural, Mining and Engineering Services Holdings Group (Private) Limited"; e.period_end = None; db.session.commit()
    d4 = DocxDocument(wp.build_financial_statements_docx(e, st, None))
    check(htext(d4.sections[1])[0].startswith("THE VERY LONG") and htext(d4.sections[1])[2].endswith("—"), "long names and a missing year end still build")
    open(os.path.join(tempfile.gettempdir(), "notes_header_sample.docx"), "wb").write(wp.build_financial_statements_docx(e2, st2, FS()).getvalue() if hasattr(wp.build_financial_statements_docx(e2, st2, FS()), "getvalue") else b"")

# ---- the sign-off under the auditor's report
with app.app_context():
    cl.name = "Penanel Trading (Private) Limited"; e.period_end = dtm.date(2024, 12, 31); db.session.commit()
    class AO:
        report_basis = "audit"; modification = "unmodified"; opinion_paragraph = "We have audited..."; basis_paragraph = "We conducted..."
        management_responsibility_paragraph = "Directors are responsible..."; auditor_responsibility_paragraph = "Our objectives..."
        report_date = dtm.date(2026, 6, 24)
    d5 = DocxDocument(wp.build_financial_statements_docx(e, st, None, audit_opinion=AO()))
    texts = [p.text for p in d5.paragraphs]
    i = [k for k, t in enumerate(texts) if t.startswith("\u2026")]
    check(len(i) == 1, "one signing line of dots")
    block = texts[i[0]:i[0] + 6]
    check(block == ["\u2026" * 42, "Petros Piki, CA(Z), MSc, RPA", "(PAAB Practice Certificate No 0625, ICAZ No 254069)",
                    "Neverlank Chartered Accountants PAAB Firm No. Z10110", "Harare, Zimbabwe", "24 June 2026"], "sign-off block reads as specified: %s" % block)
    check("Neverlank Chartered Accountants" not in texts[i[0] - 3:i[0]] and not any("Michael House" in t for t in texts[i[0]:i[0] + 8]), "the old firm-name / street-address lines are gone")
    check(all(p.paragraph_format.keep_with_next for p in d5.paragraphs[[k for k, p in enumerate(d5.paragraphs) if p.text.startswith("\u2026")][0]:][:5]), "the block is kept together on one page")
    AO.report_basis = "review"
    d6 = DocxDocument(wp.build_financial_statements_docx(e, st, None, audit_opinion=AO()))
    check(any(p.text.startswith("\u2026") for p in d6.paragraphs) and "Independent Reviewer's Report" in [p.text for p in d6.paragraphs], "a reviewer's report has the same sign-off")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
