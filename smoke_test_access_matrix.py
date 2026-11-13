"""Who can open what: every page that takes an engagement or client number is requested as a partner on the engagement, a manager who is not on it,
a supervisor and a staff member. Restricted people must get 403/404 only (never a page, a redirect-after-change, or an error), on every route and method."""
import os, sys, tempfile, re, json

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import date
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
with app.app_context():
    def mk(u, n, role):
        x = User(username=u, name=n, role=role); x.set_password("Correct-horse-9"); db.session.add(x); db.session.commit(); return x.id
    P = mk("pp", "Partner P", "partner"); S = mk("ss", "Staff S", "staff"); M = mk("mm", "Manager M", "manager"); SUP = mk("sv","Sup","supervisor")
    cl = Client(name="Secret Client", created_by_id=P); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=P, manager_id=P, acceptance_required=False, period_end=date(2025,12,31))
    db.session.add(e); db.session.commit()
    CID, EID = cl.id, e.id
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
rules = []
for r in app.url_map.iter_rules():
    if r.endpoint == "static": continue
    args = set(r.arguments)
    if not ({"engagement_id", "client_id"} & args): continue
    rules.append(r)
def fill(r):
    url = r.rule
    for a in r.arguments:
        conv = r._converters[a]
        val = str(EID) if a == "engagement_id" else str(CID) if a == "client_id" else ("1" if conv.__class__.__name__ == "IntegerConverter" else "x")
        url = url.replace("<%s>" % a, val).replace("<int:%s>" % a, val).replace("<path:%s>" % a, val).replace("<float:%s>" % a, val)
    return url
import shutil, sqlite3
dbpath = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
master = dbpath + ".master"
with app.app_context():
    db.session.remove(); db.engine.dispose()
_c = sqlite3.connect(dbpath); _c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); _m = sqlite3.connect(master); _c.backup(_m); _m.close(); _c.close()
def restore():
    with app.app_context():
        db.session.remove(); db.engine.dispose()
    src = sqlite3.connect(master); dst = sqlite3.connect(dbpath)
    src.backup(dst); dst.commit(); dst.close(); src.close()

fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
res = {}
people = (("partner_on", P), ("staff", S), ("manager_not_on", M), ("supervisor", SUP))
for who, uid in people:
    c = login(uid)
    for r in rules:
        for m in sorted(r.methods & {"GET", "POST"}):
            url = fill(r)
            if m == "POST": restore()
            try:
                code = c.open(url, method=m, data={} if m == "POST" else None, headers={"Referer": "/"}).status_code
            except Exception as ex:
                code = "EXC:" + type(ex).__name__
            if m == "POST": restore()
            res.setdefault((r.endpoint, m, r.rule), {})[who] = code
check(len(res) >= 150, f"{len(res)} route/method pairs were probed")
def bad(who, only=lambda rule: True):
    return sorted(f"{k[1]} {k[2]} -> {v[who]}" for k, v in res.items() if v[who] not in (403, 404) and only(k[2]))
for who in ("staff", "supervisor"):
    b = bad(who)
    check(not b, f"{who}: every engagement and client page is refused (403/404)" + ("" if not b else " - OPEN: " + "; ".join(b[:8])))
b = bad("manager_not_on", lambda rule: "<int:engagement_id>" in rule and "client_id" not in rule)
check(not b, "a manager who is not on the engagement is refused on every engagement page" + ("" if not b else " - OPEN: " + "; ".join(b[:8])))
check(sum(1 for v in res.values() if v["partner_on"] in (200, 302)) >= 100, "the partner on the engagement can use the pages (the test is not trivially refusing everything)")
check(sum(1 for v in res.values() if v["manager_not_on"] in (200, 302)) >= 1, "managers can still see clients (firm-wide client access)")
print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
