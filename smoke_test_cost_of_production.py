"""Cost of production note: Opening Stock, other cost accounts, Closing Stock in brackets, rule, total (Word and Finalisation tab)."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datetime as dtm
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin, client_notes, workpapers as wp
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def TL(n, c, cd=0, cc=0, pd=0, pc=0): return TrialBalanceLine(account_name=n, fs_category=c, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc)
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Agro Mills (Private) Limited", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25", type="Audit", partner_id=admin.id, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e); db.session.commit()
    # deliberately out of order, with a contra label
    lines = [TL("Purchases", "cost_of_sales", 1888558, 0, 267587, 0), TL("Closing Stock (contra)", "cost_of_sales", 0, 140163, 0, 18500),
             TL("Plant Repairs", "cost_of_sales", 17160, 0, 18582, 0), TL("Opening stock", "cost_of_sales", 18500, 0, 9467, 0),
             TL("Plant Depreciation", "cost_of_sales", 115301, 0, 11297, 0), TL("Sales", "revenue", 0, 3000000, 0, 600000),
             TL("Cash", "cash", 2000000, 0, 700000, 0), TL("Share capital", "share_capital", 0, 100, 0, 100), TL("Retained earnings", "retained_earnings", 0, 100, 0, 100)]
    st = fin.build_all_statements(lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    client_notes.attach_client_notes(st, e)
    note = next(n for n in st["notes"] if n["title"] == "Cost of production")
    check(note["title"] == "Cost of production", "titled Cost of production (plant accounts)")
    lay = note["share_layout"]
    labels = [l["label"] for l in lay["lines"] if l["label"]]
    check(labels == ["Opening Stock", "Purchases", "Plant Repairs", "Plant Depreciation", "Closing Stock"], f"opening first, closing last, brackets/contra dropped: {labels}")
    vals = [(l["cur"], l["pri"]) for l in lay["lines"] if l["label"]]
    check(vals[0] == (18500, 9467) and vals[-1] == (-140163, -18500), f"closing stock negative: {vals}")
    tot = lay["lines"][-1]
    check(tot["style"] == "end" and tot["cur"] == 1899356 and tot["pri"] == 288433, f"total {tot['cur']} / {tot['pri']} (1,899,356 / 288,433)")
    check(lay["lines"][-3]["style"] == "under", "rule under the closing stock line")
    # Word
    doc = wp.build_financial_statements_docx(e, st, None) if hasattr(wp, "build_financial_statements_docx") else None
    import io
    buf = doc if isinstance(doc, io.BytesIO) else None
    if buf:
        d = DocxDocument(io.BytesIO(buf.getvalue()))
        txt = "\n".join(p.text for p in d.paragraphs) + "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
        check("Cost of production" in txt and "(140,163)" in txt and "1,899,356" in txt, "Word pack shows the note with (140,163) and 1,899,356")
    # a trader with no stock accounts keeps the plain list
    st2 = fin.build_all_statements([TL("Cost of sales", "cost_of_sales", 500, 0, 400, 0), TL("Sales", "revenue", 0, 900, 0, 800), TL("Cash", "cash", 400, 0, 400, 0), TL("Share capital", "share_capital", 0, 100, 0, 100), TL("Retained earnings", "retained_earnings", 0, 300, 0, 300)],
                                   [], reporting_framework="ifrs_for_smes", whole_numbers=True)
    client_notes.attach_client_notes(st2, e)
    n2 = next(n for n in st2["notes"] if n["title"] == "Cost of sales")
    check(not n2.get("share_layout"), "no stock accounts: the plain Cost of sales list is kept")
print("ALL PASSED" if not fails else f"FAILED {len(fails)}")
