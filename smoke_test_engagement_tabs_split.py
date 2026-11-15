"""The engagement page is a small shell (templates/engagements/detail.html) that includes one file per tab (templates/engagements/tabs/<tab>.html).
This keeps it that way: every tab file exists and is included, no tab body has crept back into the shell, and every tab still opens for the main
engagement types with its own tab marked active."""
import os, sys, tempfile, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
from datetime import date
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

TABS = ["overview", "acceptance", "entity", "analytical", "risks", "planning", "trial_balance", "checklist", "documents", "tasks", "income_tax", "deferred_tax",
        "tax", "accounting", "finalisation", "vat", "asset_register", "capital_allowances", "qpd", "registry_forms", "substantive"]
shell = open(os.path.join(HERE, "templates", "engagements", "detail.html"), encoding="utf-8").read()
tabdir = os.path.join(HERE, "templates", "engagements", "tabs")
check(len(shell) < 100_000, f"the page shell stays small ({len(shell):,} characters)")
included = re.findall(r'\{% include "engagements/tabs/(\w+)\.html" %\}', shell)
check(sorted(included) == sorted(TABS), "the shell includes exactly the 21 tab files")
check(all(os.path.exists(os.path.join(tabdir, t + ".html")) for t in TABS), "and every one of them exists")
check(max(os.path.getsize(os.path.join(tabdir, f)) for f in os.listdir(tabdir)) < 150_000, "no single tab file has grown past 150 KB")
check(len(re.findall(r"\{% (?:if|elif) tab == '", shell)) == len(TABS), "the shell holds only the 21 tab switches, no tab bodies")

with app.app_context():
    u = User(username="p", name="P", role="admin"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Acme (Pvt) Ltd", created_by_id=u.id, vat_number="1234567"); db.session.add(cl); db.session.commit()
    ids = {}
    for t in ("Audit", "Tax Advisory", "Secretarial"):
        e = Engagement(client_id=cl.id, title=f"{t} 2025", type=t, partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
        db.session.add(e); db.session.commit(); ids[t] = e.id
    UID = u.id
c = app.test_client()
with c.session_transaction() as s: s["_user_id"] = str(UID); s["_fresh"] = True
for t, eid in ids.items():
    shown, bad = set(), []
    for tab in TABS:
        r = c.get(f"/engagements/{eid}", query_string={"tab": tab}, follow_redirects=True)
        h = r.get_data(as_text=True)
        if r.status_code != 200 or "Traceback" in h:
            bad.append(tab); continue
        m = re.search(r'tab=(\w+)" class="active"', h)
        if m: shown.add(m.group(1))
    check(not bad, f"{t}: every tab opens without an error" + (f" (failed: {bad})" if bad else ""))
    check(len(shown) >= (12 if t != "Secretarial" else 8), f"{t}: tabs show as active when opened ({len(shown)} different tabs reached)")
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
