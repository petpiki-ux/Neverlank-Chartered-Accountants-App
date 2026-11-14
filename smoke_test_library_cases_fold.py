"""Legislative Updates library: court Cases are gathered into one closed 'Court cases (n)' group, each case closed on its own; other items unchanged."""
import os, sys, tempfile, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); UID = admin.id
    ids = {}
    for t, typ, kw in [("Income Tax Act [Chapter 23:06]", "Act", {}), ("Finance Act 2026", "Act", {}),
                       ("Commissioner v Delta Corp", "Case", dict(case_court="Supreme Court", case_citation="SC 12/22", case_jurisdiction="Zimbabwe")),
                       ("Pioneer v SARS", "Case", dict(case_court="SCA", case_jurisdiction="South Africa")), ("Practice note 3", "Publication", {})]:
        u = LegislativeUpdate(title=t, instrument_type=typ, summary="A summary of " + t, article_url="https://example.org/" + str(len(ids)), ai_status="done", ai_summary="AI " + t, **kw); db.session.add(u); db.session.commit(); ids[t] = u.id
c = app.test_client()
with c.session_transaction() as s: s["_user_id"] = str(UID); s["_fresh"] = True
r = c.get("/tax/legislative-updates"); h = r.data.decode()
check(r.status_code == 200, f"library page loads ({r.status_code})")
check("Court cases (2)" in h, "one 'Court cases (2)' group")
grp = re.search(r'<details id="lu-cases".*?</details>\s*(?=<script)', h, re.S)
check(grp is not None, "the group is a closed <details> block")
g = grp.group(0) if grp else ""
check("<details id=\"lu-cases\" style" in h and ' open' not in h[h.index('<details id="lu-cases"'):h.index('<details id="lu-cases"') + 60], "group is closed by default")
check(g.count('class="lu-fold"') == 2 and "Commissioner v Delta Corp" in g and "Pioneer v SARS" in g, "each case is its own closed fold inside the group")
check("Income Tax Act" not in g and "Practice note 3" not in g, "Acts and publications stay outside the group")
check(h.index("Income Tax Act") < h.index('id="lu-cases"') and h.index("Practice note 3") < h.index('id="lu-cases"'), "other items listed first, cases after")
check(f'id="legislative-update-{ids["Commissioner v Delta Corp"]}"' in g and "Supreme Court" in g and "SC 12/22" in g, "a case keeps its anchor and its court and citation in the closed line")
check("Save authority &amp; areas" in g and "Save &amp; mark reviewed" in g, "the case's authority form and review box are still inside the fold")
check(f'id="legislative-update-{ids["Income Tax Act [Chapter 23:06]"]}"' in h and "Save areas" in h, "an Act keeps its full entry")
check("function luFold" in h and "function luReveal" in h, "open-all / jump-to-anchor script is present")
# no cases at all: no group
with app.app_context():
    LegislativeUpdate.query.filter_by(instrument_type="Case").delete(); db.session.commit()
h2 = c.get("/tax/legislative-updates").data.decode()
check("Court cases (" not in h2 and "Income Tax Act" in h2, "no cases: no group shown")
print("ALL PASSED" if not fails else f"FAILED {len(fails)}")
