"""News Watch: the 'From' filter (days) must open, not give an Internal Server Error (a template once used type=int, which a template cannot do)."""
import os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app
from models import User
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
c = app.test_client()
with app.app_context(): uid = User.query.filter_by(role="admin").first().id
with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
def picked(url):
    r = c.get(url); m = re.search(r'<option value="(\d+)" selected>', r.get_data(as_text=True))
    return r.status_code, (int(m.group(1)) if m else None)
check(picked("/news-watch/") == (200, 14), "no filter: opens, 'Last 2 weeks' chosen")
check(picked("/news-watch/?q=Garnishes&urgency=&topic=&days=14&show=relevant") == (200, 14), "a search with a range opens (this was an Internal Server Error)")
for d in (2, 7, 30, 90):
    check(picked(f"/news-watch/?days={d}") == (200, d), f"'From' = {d} days opens and is shown as chosen")
check(picked("/news-watch/?days=abc") == (200, 14), "a nonsense range falls back to two weeks")
print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
