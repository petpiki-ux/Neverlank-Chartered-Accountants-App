"""Security hardening: cross-site request protection, safe login redirect, sign-in lockout, password rules, headers, cookies, docx injection."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from app import create_app
from extensions import db
from models import *
import security
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    p1 = User(username="alice", name="Alice", role="partner", email="a@x.com"); p1.set_password("Correct-horse-9")
    db.session.add(p1); db.session.commit()
    ids = dict(admin=admin.id, alice=p1.id)

def login_as(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

# ---------------------------------------------------------------- the seed no longer ships a known password
with app.app_context():
    check(not User.query.filter_by(role="admin").first().check_password("changeme123"), "the seeded admin does not use the old public password")
check(app.config["SECRET_KEY"] != security.DEFAULT_SECRET, "a private SECRET_KEY replaces the public default")

# ---------------------------------------------------------------- pure helpers
check(security.safe_next("/clients/3", "/") == "/clients/3", "a local path is accepted as a redirect target")
for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "javascript:alert(1)", "///evil.com", "", None):
    check(security.safe_next(bad, "/home") == "/home", f"redirect target {bad!r} is refused")
check(security.password_problem("short1") is not None, "a short password is refused")
check(security.password_problem("1234567890123") is not None, "an all-numbers password is refused")
check(security.password_problem("changeme123") is not None, "the old default password is refused")
check(security.password_problem("aaaaaaaaaaaa") is not None, "a repeated-character password is refused")
check(security.password_problem("Tr1cky-Phrase-77") is None, "a reasonable password is accepted")
tp = [security.temporary_password() for _ in range(20)]
check(len(set(tp)) == 20 and all(len(t) == 12 and security.password_problem(t) is None for t in tp), "temporary passwords are random, 12 characters and pass the rules")

# ---------------------------------------------------------------- cross-site request protection
c = login_as(ids["alice"])
r = c.post("/auth/account", data={"name": "Mallory", "email": "a@x.com"}, headers={"Sec-Fetch-Site": "cross-site"})
check(r.status_code == 403, "a cross-site browser post (Sec-Fetch-Site) is refused")
r = c.post("/auth/account", data={"name": "Mallory", "email": "a@x.com"}, headers={"Origin": "https://evil.example"})
check(r.status_code == 403, "a post whose Origin is another site is refused")
r = c.post("/auth/account", data={"name": "Mallory", "email": "a@x.com"}, headers={"Origin": "null"})
check(r.status_code == 403, "a post with Origin: null is refused")
r = c.post("/auth/account", data={"name": "Mallory", "email": "a@x.com"}, headers={"Referer": "https://evil.example/page"})
check(r.status_code == 403, "a post whose Referer is another site is refused")
with app.app_context():
    check(db.session.get(User, ids["alice"]).name == "Alice", "none of the refused posts changed the account")
r = c.post("/auth/account", data={"name": "Alice B", "email": "a@x.com"}, headers={"Sec-Fetch-Site": "same-origin"})
check(r.status_code == 302, "a same-origin post (Sec-Fetch-Site) is accepted")
r = c.post("/auth/account", data={"name": "Alice C", "email": "a@x.com"}, headers={"Origin": "http://localhost"})
check(r.status_code == 302, "a post whose Origin matches the host is accepted")
r = c.post("/auth/account", data={"name": "Alice D", "email": "a@x.com"}, headers={"Referer": "http://localhost/auth/account"})
check(r.status_code == 302, "a post whose Referer matches the host is accepted")
r = c.post("/auth/account", data={"name": "Alice D2", "email": "a@x.com"}, headers={"Referer": "/auth/account"})
check(r.status_code == 302, "a post whose Referer is a relative path on this site is accepted")
r = c.post("/auth/account", data={"name": "Alice E", "email": "a@x.com"})
check(r.status_code == 302, "a post with no browser headers (scripts, tests) is accepted")
r = c.get("/auth/account", headers={"Sec-Fetch-Site": "cross-site"})
check(r.status_code == 200, "reading a page from another site's link still works (only changes are blocked)")
os.environ["TRUSTED_ORIGINS"] = "https://app.neverlank.co.zw"
r = c.post("/auth/account", data={"name": "Alice F", "email": "a@x.com"}, headers={"Origin": "https://app.neverlank.co.zw"})
check(r.status_code == 302, "an origin listed in TRUSTED_ORIGINS is accepted")
os.environ.pop("TRUSTED_ORIGINS")

# ---------------------------------------------------------------- logout cannot be forced by another site
r = c.get("/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
r2 = c.get("/auth/account")
check(r2.status_code == 200, "a cross-site link cannot sign the user out")
r = c.get("/auth/logout")
check(c.get("/auth/account").status_code == 302, "a normal logout still signs the user out")

# ---------------------------------------------------------------- password change
c = login_as(ids["alice"])
def change(cur, new):
    return c.post("/auth/account", data={"name": "Alice", "email": "a@x.com", "current_password": cur, "new_password": new})
def pw_ok(pw):
    with app.app_context():
        return db.session.get(User, ids["alice"]).check_password(pw)
change("", "Brand-new-pass-1")
check(pw_ok("Correct-horse-9") and not pw_ok("Brand-new-pass-1"), "changing the password without the current one is refused")
change("wrong-wrong-1", "Brand-new-pass-1")
check(pw_ok("Correct-horse-9"), "changing the password with a wrong current one is refused")
change("Correct-horse-9", "short")
check(pw_ok("Correct-horse-9"), "a weak new password is refused")
change("Correct-horse-9", "Brand-new-pass-1")
check(pw_ok("Brand-new-pass-1"), "the right current password and a good new one changes it")
c.post("/auth/account", data={"name": "Alice Z", "email": "a@x.com"})
with app.app_context():
    u = db.session.get(User, ids["alice"])
    check(u.name == "Alice Z" and u.check_password("Brand-new-pass-1"), "saving the name alone leaves the password unchanged")

# ---------------------------------------------------------------- team members
ca = login_as(ids["admin"])
r = ca.post("/team/new", data={"username": "bob", "name": "Bob", "email": "", "role": "staff", "password": ""})
with ca.session_transaction() as s_:
    flashed = " ".join(m for _, m in s_.get("_flashes", []))
with app.app_context():
    bob = User.query.filter_by(username="bob").first()
    check(bob is not None and not bob.check_password("changeme123"), "a new team member without a typed password does not get the old default")
check("Temporary password for bob" in flashed, "the admin is shown the temporary password once")
r = ca.post("/team/new", data={"username": "carl", "name": "Carl", "email": "", "role": "staff", "password": "abc"})
with app.app_context():
    check(User.query.filter_by(username="carl").first() is None, "a new team member with a weak typed password is not created")
ca.post("/team/new", data={"username": "dina", "name": "Dina", "email": "", "role": "staff", "password": "Good-start-pass1"})
with app.app_context():
    check(User.query.filter_by(username="dina").first().check_password("Good-start-pass1"), "a good typed password is used")
    dina_id = User.query.filter_by(username="dina").first().id
ca.post(f"/team/{dina_id}/edit", data={"name": "Dina", "email": "", "role": "staff", "is_active": "1", "password": "weak"})
with app.app_context():
    check(db.session.get(User, dina_id).check_password("Good-start-pass1"), "an admin cannot reset a password to a weak one")

# ---------------------------------------------------------------- sign-in lockout
security.throttle.reset()
cl = app.test_client()
for i in range(5):
    r = cl.post("/auth/login", data={"username": "alice", "password": "wrong"})
r = cl.post("/auth/login", data={"username": "alice", "password": "Brand-new-pass-1"})
check(r.status_code == 429, "after 5 failed sign-ins the account is locked even for the right password")
r = cl.post("/auth/login", data={"username": "dina", "password": "Good-start-pass1"})
check(r.status_code == 302, "another user from the same address is not locked by one user's failures")
security.throttle.reset()
r = cl.post("/auth/login", data={"username": "alice", "password": "Brand-new-pass-1"})
check(r.status_code == 302, "once the lock is cleared the right password signs in")
# time-based release
t = security.LoginThrottle()
for i in range(5): t.failure("1.1.1.1", "x", now=1000)
check(t.retry_after("1.1.1.1", "x", now=1100) > 0 and t.retry_after("1.1.1.1", "x", now=1000 + t.LOCK + 5) == 0, "a lockout lifts by itself after the lock time")
t.success("1.1.1.1", "x")
check(t.retry_after("1.1.1.1", "x", now=1100) == 0, "a successful sign-in clears the failure count")
t2 = security.LoginThrottle()
for i in range(25): t2.failure("2.2.2.2", f"user{i}", now=1000)
check(t2.retry_after("2.2.2.2", "brand-new", now=1001) > 0, "one address trying many usernames is locked as a whole")
# the real client address is the last X-Forwarded-For entry (added by the host's proxy), not one the caller typed
security.throttle.reset()
cl = app.test_client()
for i in range(5):
    cl.post("/auth/login", data={"username": "alice", "password": "wrong"}, headers={"X-Forwarded-For": f"9.9.9.{i}, 5.5.5.5"})
r = cl.post("/auth/login", data={"username": "alice", "password": "Brand-new-pass-1"}, headers={"X-Forwarded-For": "8.8.8.8, 5.5.5.5"})
check(r.status_code == 429, "changing a made-up first X-Forwarded-For entry does not dodge the lockout")
security.throttle.reset()

# ---------------------------------------------------------------- redirect after sign-in
cl = app.test_client()
r = cl.post("/auth/login?next=https://evil.example/", data={"username": "alice", "password": "Brand-new-pass-1"})
check(r.status_code == 302 and "evil.example" not in r.headers["Location"], "login ignores an off-site next address")
cl = app.test_client()
r = cl.post("/auth/login?next=//evil.example/", data={"username": "alice", "password": "Brand-new-pass-1"})
check(r.status_code == 302 and "evil.example" not in r.headers["Location"], "login ignores a protocol-relative next address")
cl = app.test_client()
r = cl.post("/auth/login?next=/clients/", data={"username": "alice", "password": "Brand-new-pass-1"})
check(r.headers["Location"].endswith("/clients/"), "login still follows a local next address")

# ---------------------------------------------------------------- headers and cookies
r = login_as(ids["alice"]).get("/auth/account")
h = r.headers
check(h.get("X-Content-Type-Options") == "nosniff" and h.get("X-Frame-Options") == "SAMEORIGIN" and "Referrer-Policy" in h, "standard security headers are sent")
check("camera=(self)" in h.get("Permissions-Policy", "") and "microphone=(self)" in h.get("Permissions-Policy", ""), "calls can still use the camera and microphone on this site")
check(app.config["SESSION_COOKIE_HTTPONLY"] and app.config["SESSION_COOKIE_SAMESITE"] == "Lax", "session cookie is HttpOnly and SameSite=Lax")
cl = app.test_client()
r = cl.post("/auth/login", data={"username": "alice", "password": "Brand-new-pass-1"})
sc = r.headers.get("Set-Cookie", "")
check("HttpOnly" in sc and "SameSite=Lax" in sc, "the sign-in cookie carries HttpOnly and SameSite=Lax")

# ---------------------------------------------------------------- unauthenticated access is still gated
cl = app.test_client()
for path in ("/", "/clients/", "/team/", "/corporate-finance/", "/auth/account"):
    r = cl.get(path)
    check(r.status_code in (302, 401, 404) and (r.status_code == 404 or "/auth/login" in r.headers.get("Location", "")), f"{path} needs a sign-in")

# ---------------------------------------------------------------- the call-signalling channel only accepts this site
cs = app.test_client()
check(cs.get("/socket.io/?EIO=4&transport=polling").status_code == 200, "the signalling channel works for scripts with no Origin")
check(cs.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": "http://localhost"}).status_code == 200, "the signalling channel accepts this site's own origin")
check(cs.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": "https://evil.example"}).status_code == 400, "the signalling channel refuses another site's origin")

# ---------------------------------------------------------------- Word-file style injection
import wp_docx, inspect
src = inspect.getsource(wp_docx)
check(src.count("html.escape(") >= 3 and 'style=" + chr(34) + html.escape' in src, "style attributes built from Word files are escaped")

print("\n%d failing" % len(fails))
sys.exit(1 if fails else 0)
