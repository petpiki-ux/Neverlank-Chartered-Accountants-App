"""Legislative Updates and the Legislative Calendar have their own top-bar menu, not the HR & Admin page."""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite://")
from app import create_app
from extensions import db
from models import User

failures = 0
def check(ok, label):
    global failures
    print(("PASS " if ok else "FAIL ") + label)
    if not ok: failures += 1

app = create_app()
app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
with app.app_context():
    db.create_all()
    admin = User.query.filter_by(role="admin").first()
    if admin is None:
        admin = User(name="A", email="a@x.test", role="admin"); admin.set_password("pw12345678!"); db.session.add(admin); db.session.commit()
    uid = admin.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(uid); s["_fresh"] = True

hr = c.get("/hr/").get_data(as_text=True)
body = hr.split('<div class="stat-grid">')[1].split("Related")[0]
check("Legislative Update" not in body and "/tax/legislative-updates" not in body, "HR & Admin page no longer lists the legislative pages")
check("Tax Penalty &amp; Interest Rates" in hr, "HR page still has the other cards")

page = c.get("/tax/legislative-updates").get_data(as_text=True)
check('Legislative Updates <span class="caret">' in page and "/tax/legislative-updates/calendar" in page, "own top-bar menu with the calendar link")
tc = re.search(r'Tax &amp; Compliance.*?</div>\s*</div>', page, re.S)
check(tc is not None and "/tax/legislative-updates" not in tc.group(0), "removed from the Tax & Compliance menu")
check(re.search(r'nav-group active">\s*<button[^>]*>Legislative Updates', page) is not None, "own menu is highlighted on the page")
cal = c.get("/tax/legislative-updates/calendar")
check(cal.status_code == 200, "calendar page opens")
print(f"{failures} failure(s)")
sys.exit(1 if failures else 0)
