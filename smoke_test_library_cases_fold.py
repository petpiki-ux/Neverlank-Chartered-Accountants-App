"""Legislative Updates library: every class (Act of Parliament, Government Notice, Statutory Instrument, Case Law, Publication, News Article) is its own closed group with a count, so the search and the filing form are reachable without scrolling; each case is closed on its own inside Case Law."""
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
for lab in ("Act of Parliament (2)", "Government Notice (0)", "Statutory Instrument (0)", "Case Law (2)", "Publication (0)", "News Article (0)"):
    check(lab in h, "closed group: " + lab)
check(h.count('<details class="lu-group"') == 7 and ' open' not in "".join(re.findall(r'<details class="lu-group"[^>]*>', h)), "7 groups (6 classes + the text-log entries), all closed by default")
check("none filed yet" in h, "an empty class says so")
check(h.index("Search the library") < h.index("File an instrument") < h.index("Filed library"), "search, then the filing form, then the library: nothing to scroll past")
grp = re.search(r'<details class="lu-group" id="lu-cases".*?</details>\s*(?=<details class="lu-group"|<script)', h, re.S)
check(grp is not None, "Case Law is its own <details> block")
g = grp.group(0) if grp else ""
check(g.count('class="lu-fold"') == 2 and "Commissioner v Delta Corp" in g and "Pioneer v SARS" in g, "each case is its own closed fold inside Case Law")
check("Income Tax Act" not in g and "Practice note 3" not in g, "Acts and publications stay outside the Case Law group")
check(f'id="legislative-update-{ids["Commissioner v Delta Corp"]}"' in g and "Supreme Court" in g and "SC 12/22" in g, "a case keeps its anchor and its court and citation in the closed line")
check("Save authority &amp; areas" in g and "Save &amp; mark reviewed" in g, "the case's authority form and review box are still inside the fold")
act = re.search(r'<details class="lu-group" id="lu-group-1".*?</details>\s*(?=<details class="lu-group")', h, re.S)
check(act is not None and "Income Tax Act" in act.group(0) and "Finance Act 2026" in act.group(0) and "Save areas" in act.group(0), "the Acts group holds the two Acts with their full entries")
check("Practice note 3" in h and "Text log entries" in h, "an entry whose type is not a listed class goes in the text-log group, not lost")
check("function luFold" in h and "function luReveal" in h and "function luGroups" in h, "open-all / jump-to-anchor script is present")
check("details = el.parentElement" not in h and "p.tagName === 'DETAILS'" in h, "a link to one entry opens its class group first")
# no cases at all: the class still shows, empty
with app.app_context():
    LegislativeUpdate.query.filter_by(instrument_type="Case").delete(); db.session.commit()
h2 = c.get("/tax/legislative-updates").data.decode()
check("Case Law (0)" in h2 and "Income Tax Act" in h2, "no cases: Case Law shows (0)")
print("ALL PASSED" if not fails else f"FAILED {len(fails)}")
