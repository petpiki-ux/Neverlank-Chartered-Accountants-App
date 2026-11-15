"""A line of description under an account's name in its note (Broxmen: the Binga coal special grant under Mining Rights, the Chegutu land
under Right-of-Use Assets): saved on the Finalisation tab, shown on screen and in the Word notes, joined with the same account's other line."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
import datetime as dtm
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
H = "‑"
MINING = "Coal special grant, Binga (about 9,710 ha)"
LAND = "Land, Subdivision 2 of Lot 3 of Knockmalloch Estate of Austria, Chegutu (129 ha)"
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Broxmen Investment (Private) Limited", created_by_id=aid); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY2026", type="Audit", partner_id=aid, manager_id=aid, period_end=dtm.date(2026, 10, 31), reporting_framework="full_ifrs", acceptance_required=False)
    db.session.add(e); db.session.commit(); eid = e.id
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    ids = {}
    for code, nm, cat, cd, cc, pd, pc in [("1641", f"Right{H}of{H}Use Assets", "ppe", 150000, 0, 150000, 0), ("1701.03", "Mining Rights", "intangible_assets", 1500, 0, 1500, 0),
                                       ("4001", "Paid-in Capital", "share_capital", 0, 151500, 0, 151500)]:
        ln = TrialBalanceLine(trial_balance_id=tb.id, account_code=code, account_name=nm, fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=pd, prior_credit=pc)
        db.session.add(ln); db.session.flush(); ids[code] = ln.id
    db.session.commit()
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(aid); s["_fresh"] = True
page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
check("Describe accounts in the notes" in page and f'name="desc_{ids["1701.03"]}"' in page, "the Finalisation tab offers a description box per account")
r = c.post(f"/engagements/{eid}/notes/descriptions", data={"line_id": [str(ids["1641"]), str(ids["1701.03"]), str(ids["4001"])],
           f"desc_{ids['1641']}": LAND, f"desc_{ids['1701.03']}": MINING, f"desc_{ids['4001']}": ""}, follow_redirects=True)
with app.app_context():
    lines = {l.account_code: l for l in TrialBalance.query.filter_by(engagement_id=eid).first().lines}
    check(lines["1641"].note_description == LAND and lines["1701.03"].note_description == MINING and not lines["4001"].note_description, "saved on the right accounts; a blank one stays empty")
    st = fin.build_all_statements(list(lines.values()), [], reporting_framework="full_ifrs")
    notes = {n["title"]: n for n in st["notes"]}
    check(notes["Intangible assets"]["accounts"][0]["description"] == MINING, "the Mining Rights note row carries its description")
    check(notes["Property, plant and equipment"]["accounts"][0]["description"] == LAND, "the Right-of-Use Assets note row carries its description")
    check(notes["Share capital"]["accounts"][0]["description"] == "", "an account without one has none")
    d = DocxDocument(wp.build_financial_statements_docx(Engagement.query.get(eid), st, None))
    paras = [p.text for p in d.paragraphs]
    cells = [cell.text for t in d.tables for row in t.rows for cell in row.cells]
    check(any(t.startswith("Mining Rights: ") and MINING in t for t in paras), "the Word note prints the description as body text, with the account name, under Mining Rights")
    check(any(t.startswith("Right") and "Use Assets: " in t and LAND in t for t in paras), "the Word note prints the description as body text under Right-of-Use Assets")
    check(not any(MINING in t or LAND in t for t in cells), "the descriptions are not inside the table of numbers")
    nm = [n for n in st["notes"] if n["title"] == "Intangible assets"][0]
    check(nm["descriptions"] == [{"name": "Mining Rights", "text": MINING}], "the note carries its descriptions as a list")
page = c.get(f"/engagements/{eid}?tab=finalisation").get_data(as_text=True)
check(MINING in page and LAND in page, "the on-screen notes show both descriptions")
import re as _re
check(_re.search(r"</table>\s*(?:<[^>]+>\s*)*<p[^>]*><strong>Mining Rights:</strong>", page) is not None, "on screen the description follows the note's table")
# the same account split across two lines keeps its description
with app.app_context():
    from types import SimpleNamespace as NS
    L = lambda **k: NS(**{**dict(fs_category="intangible_assets", account_code="", note_description=None, current_debit=0, current_credit=0, prior_debit=0, prior_credit=0), **k})
    two = [L(account_name="Mining Rights", prior_debit=1500, note_description=MINING), L(account_name="Mining Rights", current_debit=1500),
           NS(fs_category="share_capital", account_code="", account_name="Capital", note_description=None, current_debit=0, current_credit=1500, prior_debit=0, prior_credit=1500)]
    n = next(x for x in fin.build_all_statements(two, [], reporting_framework="full_ifrs")["notes"] if x["title"] == "Intangible assets")
    check(len(n["accounts"]) == 1 and n["accounts"][0]["description"] == MINING, "the two halves of one account become one line and keep the description")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
