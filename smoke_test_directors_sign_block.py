"""Directors sign the Statement of Financial Position: two lines side by side, names, "Director", date centred."""
import os, sys, tempfile, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    cl = Client(name="Sino Co", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes", acceptance_required=False)
    db.session.add(e); db.session.commit()
    st = fin.build_all_statements([], [], reporting_framework="ifrs_for_smes")
    def build(ds, people):
        d = DocxDocument(wp.build_financial_statements_docx(e, st, None, directors_statement=ds, client_key_people=people))
        body = d.element.body
        order = []
        for el in body.iterchildren():
            if el.tag.endswith("}tbl"):
                from docx.table import Table
                t = Table(el, d)
                order.append(("T", [[c.text for c in r.cells] for r in t.rows]))
            elif el.tag.endswith("}p"):
                order.append(("P", "".join(t.text or "" for t in el.iter() if t.tag.endswith("}t"))))
        return d, order
    ds = DirectorsStatement(engagement_id=e.id, director1_name="Cheng Guofeng", director2_name="Liu Fan", statement_date=dtm.date(2026, 11, 17))
    d, order = build(ds, None)
    sig = [x for x in order if x[0] == "T" and any("Cheng Guofeng" in c for r in x[1] for c in r) and len(x[1]) == 3 and len(x[1][0]) == 2 and x[1][2][0] == "Director"]
    # the block sits under the Statement of Financial Position, before the profit or loss statement
    texts = [t for k, t in order if k == "P"]
    check(len(sig) >= 1, "two signing columns with the directors' names and 'Director' in the Statement of Financial Position")
    blocks = [x for x in sig if x[1][1] == ["Cheng Guofeng", "Liu Fan"]]
    check(len(blocks) >= 1 and "_" * 10 in blocks[-1][1][0][0], "a signing line above each name")
    check("17 November 2026" in texts, "the approval date is printed (centred)")
    idx_sfp = next(i for i, (k, t) in enumerate(order) if k == "P" and t.strip() == "Statement of Financial Position" and i > 20)
    idx_blk = max(i for i, x in enumerate(order) if x in blocks)
    idx_pl = next(i for i, (k, t) in enumerate(order) if k == "P" and t.startswith("Statement of Profit or Loss") and i > idx_sfp)
    check(idx_sfp < idx_blk < idx_pl, "it sits after the balance sheet and before the profit or loss statement")
    # fallbacks
    d, order = build(None, [{"role": "Director", "full_name": "Rudo Dube"}, {"role": "Director", "full_name": "Tino Moyo"}, {"role": "Shareholder", "full_name": "Z"}])
    check(any(x[0] == "T" and len(x[1]) == 3 and x[1][1] == ["Rudo Dube", "Tino Moyo"] for x in order), "no Directors' Statement: the client's first two Directors are used")
    d, order = build(None, None)
    check(any(x[0] == "T" and len(x[1]) == 3 and x[1][1] == ["", ""] and x[1][2] == ["Director", "Director"] for x in order), "nothing recorded: lines left blank to sign and name by hand")
print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
