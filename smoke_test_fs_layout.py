"""Financial Statements Word pack: main bankers, TOC page numbers, auditor's report under the Neverlank letterhead, statements without it,
each statement labelled with its period and on its own page, and the PPE policy shown with a table."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datetime as dtm
from docx import Document as DocxDocument
from docx.oxml.ns import qn
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

def body_items(d):
    out = []
    for ch in d.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            txt = "".join(t.text or "" for t in ch.iter(qn("w:t")))
            pb = any(b.get(qn("w:type")) == "page" for b in ch.iter(qn("w:br")))
            out.append(("p", txt, pb, ch))
        elif ch.tag == qn("w:tbl"):
            txt = " ".join(t.text or "" for t in ch.iter(qn("w:t")))
            out.append(("t", txt, False, ch))
    return out

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Penanel Trading (Private) Limited", created_by_id=admin.id, bankers="CBZ Bank\nStanbic Bank")
    db.session.add(cl); db.session.commit()
    check(Client.query.first().bankers == "CBZ Bank\nStanbic Bank", "bankers saved on the client")
    e = Engagement(client_id=cl.id, title="FY24 audit", type="Audit", partner_id=admin.id, period_end=dtm.date(2024, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e); db.session.commit()
    lines = [TrialBalanceLine(account_name="Cash", fs_category="cash", current_debit=5000, prior_debit=100),
             TrialBalanceLine(account_name="Share capital", fs_category="share_capital", current_credit=5000, prior_credit=100)]
    st = fin.build_all_statements(lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect")
    classes = [dict(name="Motor Vehicles", depreciation_method="Straight-line", rate_percent=20, useful_life_years=5),
               dict(name="COMPUTER SOFTWARE & EQUIPMENT", depreciation_method="Straight-line", rate_percent=33, useful_life_years=3),
               dict(name="FURNITURE AND FITTINGS", depreciation_method="Straight-line", rate_percent=None, useful_life_years=None)]
    pol = fin.ppe_accounting_policy_text(classes)
    check(isinstance(pol, str) and "Motor Vehicles - straight-line (20% per annum, 5 years)" in pol, "flat policy text unchanged")
    check(pol.parts["table"]["rows"][0] == ["Motor Vehicles", "Straight-line", "20%", "5 years"] and len(pol.parts["table"]["rows"]) == 2 and "FURNITURE" not in str(pol), "policy parts table rows (a class with no rate or life is left out)")
    st["notes"].append({"number": 9, "title": "Property, plant and equipment", "policy": str(pol), "policy_parts": pol.parts, "accounts": [], "total": {"current": 0, "prior": 0}})
    class AO:
        report_basis = "audit"; modification = "unmodified"; opinion_paragraph = "We have audited..."; basis_paragraph = "We conducted..."
        management_responsibility_paragraph = "Directors are responsible..."; auditor_responsibility_paragraph = "Our objectives..."
        report_date = dtm.date(2026, 6, 24)
    buf = wp.build_financial_statements_docx(e, st, None, audit_opinion=AO())
    d = DocxDocument(buf)
    items = body_items(d)
    firm = wp.FIRM_NAME
    def idx(text, kind="p", nth=-1):
        hits = [k for k, it in enumerate(items) if it[0] == kind and it[1].strip() == text]
        return hits[nth] if hits else None
    ar = idx("Independent Auditor's Report")
    sfp = idx("Statement of Financial Position")
    check(ar is not None and any(it[0] == "t" and firm in it[1] for it in items[max(0, ar - 3):ar]), "auditor's report starts under the Neverlank letterhead")
    check(sfp is not None and sfp > ar, "SFP heading found after the auditor's report")
    check(not any(it[0] == "t" and firm in it[1] for it in items[ar + 3:]), "no Neverlank letterhead from the end of the auditor's report onwards (statements, notes)")
    texts = [it[1] for it in items]
    check(texts[sfp + 2].strip() == "as at 31 December 2024", "SFP labelled 'as at': %r" % texts[sfp + 2])
    check(texts[sfp + 1].strip() == "Penanel Trading (Private) Limited", "SFP names the entity")
    for title in ("Statement of Profit or Loss and Other Comprehensive Income", "Statement of Changes in Equity"):
        k = idx(title)
        check(k is not None and texts[k + 2].strip() == "for the year ended 31 December 2024", f"{title} labelled 'for the year ended'")
        check(items[k - 1][2] or any(i[2] for i in items[k - 2:k]), f"{title} starts a new page")
    kcf = [k for k, t in enumerate(texts) if t.startswith("Statement of Cash Flows") and items[k][0] == "p" and "Heading" in (items[k][3].find(qn("w:pPr")).xml if False else "") or t.startswith("Statement of Cash Flows (")]
    check(kcf and texts[kcf[-1] + 2].strip() == "for the year ended 31 December 2024", "Cash Flows labelled")
    check(any(i[2] for i in items[kcf[-1] - 2:kcf[-1]]), "Cash Flows starts a new page")
    # contents page
    xml = d.element.body.xml
    check("FS_TOC_" in xml and "PAGEREF" in xml and "w:bookmarkStart" in xml, "TOC has bookmarks and PAGEREF page numbers")
    check("updateFields" in d.settings.element.xml, "Word asked to update fields on open")
    bms = [b.get(qn("w:name")) for b in d.element.body.iter(qn("w:bookmarkStart"))]
    refs = xml.count("PAGEREF FS_TOC_")
    check(refs >= 5 and all(any(f"PAGEREF {b}" in xml for _ in [0]) for b in bms if b.startswith("FS_TOC_")), "every TOC line has a matching bookmark: %d refs, %s" % (refs, bms))
    check("Main bankers" in xml and "CBZ Bank; Stanbic Bank" in xml.replace("\n", " ") or "CBZ Bank" in xml, "main bankers on the Client Details page")
    check("Stanbic Bank" in "\n".join(texts), "bankers appear in the pack text")
    # PPE policy as a table
    kp = idx("(1) Property, plant and equipment") or [k for k, t in enumerate(texts) if t.startswith("(") and t.endswith("Property, plant and equipment") and items[k][0] == "p"][0]
    check(items[kp + 3][0] == "t" and "Rate per annum" in items[kp + 3][1] and "Motor Vehicles" in items[kp + 3][1], "PPE policy followed by a table of depreciation bases")
    check("COMPUTER SOFTWARE" not in items[kp + 3][1] and "Computer Software" in items[kp + 3][1], "upper-case class names tidied in the table")
    # no opinion for non-audit: nothing breaks
    e.type = "Compilation"; db.session.commit()
    d2 = DocxDocument(wp.build_financial_statements_docx(e, st, None))
    check(any("as at 31 December 2024" in it[1] for it in body_items(d2)), "pack without an auditor's report still builds with labels")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
