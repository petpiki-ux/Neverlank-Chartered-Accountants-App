"""The firm's PAAB Firm No. Z10110 is on every letterhead: the Word band (auditor's report, valuation documents, payslips ...), the invoice and credit-note PDFs,
the corporate-finance PDF band and the printed-page template."""
import os, sys, tempfile, io, subprocess
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docx import Document
import brand_docs as brand
import invoicing
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
check(invoicing.FIRM_LETTERHEAD.get("paab_no") == "Z10110" == brand.FIRM_PAAB_NO, "the firm's letterhead details carry PAAB Firm No. Z10110, the same number as the auditor's sign-off")
doc = Document(); brand.letterhead_band(doc, "Independent Auditor's Report")
txt = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
check("PAAB Firm No. Z10110" in txt, "the Word letterhead band prints PAAB Firm No. Z10110")
doc2 = Document(); brand.letterhead_band(doc2, "Payslip", firm={"name": "Other", "address": "x", "web": "w"})
check("PAAB" not in "\n".join(c.text for t in doc2.tables for r in t.rows for c in r.cells), "a letterhead given no PAAB number prints none")
def pdf_text(data):
    p = os.path.join(tempfile.mkdtemp(), "x.pdf"); open(p, "wb").write(data)
    return subprocess.run(["pdftotext", p, "-"], capture_output=True, text=True).stdout
import cf_pdf
from reportlab.platypus import SimpleDocTemplate
from reportlab.lib.pagesizes import A4
band = cf_pdf._band(invoicing.FIRM_LETTERHEAD, "MODEL", "CF-1", "sub")
buf = io.BytesIO(); SimpleDocTemplate(buf, pagesize=A4).build([band])
check("PAAB Firm No. Z10110" in pdf_text(buf.getvalue()), "the corporate-finance PDF band prints it")
# the printed-page template (invoice, quotation, receipt, statement)
import jinja2
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "invoicing", "print_base.html"), encoding="utf-8").read()
check("PAAB Firm No. ' ~ firm.paab_no" in src.replace("\"", "'") or "firm.paab_no" in src, "the printed-page template reads the number")
snip = "{% set ids" + src.split("{% set ids")[1].split("{% endif %}")[0] + "{% endif %}"
out = jinja2.Environment(autoescape=True).from_string(snip).render(firm=invoicing.FIRM_LETTERHEAD)
check("PAAB Firm No. Z10110" in out, "and prints it: " + out.strip())
print("\nALL PASSED" if not fails else "\nFAILED: %d" % len(fails)); sys.exit(1 if fails else 0)
