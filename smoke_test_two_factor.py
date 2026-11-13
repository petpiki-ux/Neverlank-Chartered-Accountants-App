"""Two-factor sign-in: TOTP maths (RFC 6238 vectors), set-up, the two-step login, replay, recovery codes, lockout, switch-off, admin reset, required roles."""
import os, sys, tempfile, time, base64, json
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
import security, two_factor as tf
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

# ------------------------------------------------------------ the maths (RFC 6238 appendix B, SHA-1, last six digits of the 8-digit values)
sec = base64.b32encode(b"12345678901234567890").decode()
for t, code in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037")):
    check(tf.totp_now(sec, at=t) == code, f"RFC 6238 vector at t={t}")
check(tf.check_code(sec, "287082", at=59) == 1, "a right code returns its time step")
check(tf.check_code(sec, "287082", at=59 + 25) == 1, "a code one step old is still accepted (clock drift)")
check(tf.check_code(sec, "287082", at=59 + 95) is None, "a code several steps old is refused")
check(tf.check_code(sec, "287082", last_step=1, at=59) is None, "a code already used cannot be used again")
check(tf.check_code(sec, "28708", at=59) is None and tf.check_code(sec, "abcdef", at=59) is None and tf.check_code(sec, "", at=59) is None, "wrong-length or non-numeric codes are refused")
check(tf.check_code(sec, "287 082", at=59) == 1, "spaces typed in the code are ignored")
check(tf.otpauth_uri("ABC", "a b").startswith("otpauth://totp/Neverlank%3Aa%20b?secret=ABC"), "the app link is well formed")
check((tf.qr_svg("otpauth://x") or "<svg").lstrip().startswith("<svg"), "a QR code is drawn (or skipped cleanly)")

with app.app_context():
    for name, role in (("alice", "partner"), ("bob", "staff")):
        u = User(username=name, name=name.title(), role=role, email=""); u.set_password("Correct-horse-9"); db.session.add(u)
    db.session.commit()
    ids = {u.username: u.id for u in User.query.all()}

def login_as(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
def signed_in(c):
    return c.get("/auth/account").status_code == 200
def user(name):
    return db.session.get(User, ids[name])
def fresh_throttle():
    security.throttle.__init__()

# ------------------------------------------------------------ without two-factor, login is unchanged
c = app.test_client()
r = c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
check(r.status_code == 302 and signed_in(c), "without two-factor, a password signs in as before")

# ------------------------------------------------------------ set-up
c = login_as(ids["alice"])
r = c.get("/auth/2fa/setup")
check(r.status_code == 200 and b"Key:" in r.data, "the set-up page shows the key")
with c.session_transaction() as s:
    secret = s["totp_setup_secret"]
r = c.post("/auth/2fa/setup", data={"code": "000000"})
with app.app_context():
    check(not user("alice").totp_enabled, "a wrong code does not turn two-factor on")
r = c.post("/auth/2fa/setup", data={"code": tf.totp_now(secret)})
with app.app_context():
    a = user("alice")
    check(a.totp_enabled and a.totp_secret == secret, "the right code turns two-factor on")
    check(tf.recovery_left(a) == 8, "eight recovery codes are made")
    check(secret not in (a.recovery_hashes or ""), "the secret is not in the recovery hashes")
html = r.get_data(as_text=True)
import re
codes = re.findall(r"\b[a-z2-9]{4}-[a-z2-9]{4}\b", re.search(r"<pre[^>]*>(.*?)</pre>", html, re.S).group(1))
check(len(set(codes)) == 8, "the recovery codes are shown once")
with app.app_context():
    check(all(code not in (user("alice").recovery_hashes or "") for code in codes), "recovery codes are stored only as hashes")

# ------------------------------------------------------------ two-step sign-in
fresh_throttle()
c = app.test_client()
r = c.post("/auth/login?next=/clients/", data={"username": "alice", "password": "Correct-horse-9"})
check(r.status_code == 302 and "/auth/2fa" in r.headers["Location"], "with two-factor on, the password leads to the code page")
check(not signed_in(c), "the password alone does not sign the person in")
check(c.get("/auth/2fa/").status_code == 200, "the code page opens")
r = c.post("/auth/2fa/", data={"code": "111111"})
check(r.status_code == 200 and not signed_in(c), "a wrong code does not sign in")
code = tf.totp_now(secret)
# the setup already used this very step, so the same code must be refused (replay)
r = c.post("/auth/2fa/", data={"code": code})
check(not signed_in(c), "the code used to set up cannot be replayed to sign in")
with app.app_context():
    u = user("alice"); u.totp_last_step = int(time.time() // 30) - 2; db.session.commit()
r = c.post("/auth/2fa/", data={"code": code})
check(r.status_code == 302 and r.headers["Location"].endswith("/clients/") and signed_in(c), "the right code signs in and goes to the page asked for")
c.get("/auth/logout")
r = c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
r = c.post("/auth/2fa/", data={"code": code})
check(not signed_in(c), "the same code cannot be used a second time")

# ------------------------------------------------------------ recovery codes
c = app.test_client()
c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
r = c.post("/auth/2fa/", data={"code": codes[0]})
check(r.status_code == 302 and signed_in(c), "a recovery code signs in")
with app.app_context():
    check(tf.recovery_left(user("alice")) == 7, "the used recovery code is gone")
c.get("/auth/logout")
c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
c.post("/auth/2fa/", data={"code": codes[0]})
check(not signed_in(c), "a recovery code cannot be used twice")
c = app.test_client()
c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
c.post("/auth/2fa/", data={"code": codes[1].upper()})
check(signed_in(c), "recovery codes are not case-sensitive")

# ------------------------------------------------------------ the second step is not a way around the first
c = app.test_client()
check(c.get("/auth/2fa/").status_code == 302 and c.post("/auth/2fa/", data={"code": codes[2]}).status_code == 302 and not signed_in(c), "the code page without a password step does nothing")
c.post("/auth/login", data={"username": "alice", "password": "wrong-wrong"})
check(c.get("/auth/2fa/").status_code == 302, "a wrong password does not reach the code page")
c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
with c.session_transaction() as s:
    pend = dict(s["pending_2fa"]); pend["t"] = time.time() - 3600; s["pending_2fa"] = pend
r = c.post("/auth/2fa/", data={"code": codes[2]})
check(not signed_in(c), "a half-finished sign-in expires")

# ------------------------------------------------------------ lockout on guessing
fresh_throttle()
c = app.test_client()
c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
last = None
for i in range(12):
    last = c.post("/auth/2fa/", data={"code": "%06d" % (i + 1)})
check(last.status_code == 429, "repeated wrong codes lock the sign-in for a while")
check(not signed_in(c) and c.post("/auth/2fa/", data={"code": codes[3]}).status_code in (302, 429) and not signed_in(c), "even a right recovery code is refused during the lockout")
fresh_throttle()

# ------------------------------------------------------------ manage
c = login_as(ids["alice"])
r = c.post("/auth/2fa/recovery-codes", data={"password": "nope"})
with app.app_context(): check(tf.recovery_left(user("alice")) == 6, "new recovery codes need the password")
r = c.post("/auth/2fa/recovery-codes", data={"password": "Correct-horse-9"})
with app.app_context(): check(tf.recovery_left(user("alice")) == 8, "new recovery codes replace the old set")
r = c.post("/auth/2fa/disable", data={"password": "Correct-horse-9", "code": "000000"})
with app.app_context(): check(user("alice").totp_enabled, "switching off needs a right code")
r = c.post("/auth/2fa/disable", data={"password": "bad", "code": tf.totp_now(secret)})
with app.app_context(): check(user("alice").totp_enabled, "switching off needs the password")
with app.app_context():
    u = user("alice"); u.totp_last_step = int(time.time() // 30) - 2; db.session.commit()
r = c.post("/auth/2fa/disable", data={"password": "Correct-horse-9", "code": tf.totp_now(secret)})
with app.app_context():
    a = user("alice")
    check(not a.totp_enabled and a.totp_secret is None and a.recovery_hashes is None, "the right password and code switch it off and wipe the secret")

# ------------------------------------------------------------ admin reset
c = login_as(ids["bob"]); c.get("/auth/2fa/setup")
with c.session_transaction() as s: bsec = s["totp_setup_secret"]
c.post("/auth/2fa/setup", data={"code": tf.totp_now(bsec)})
with app.app_context(): check(user("bob").totp_enabled, "bob turns it on")
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
ca = login_as(aid)
ca.post(f"/team/{ids['bob']}/edit", data={"name": "Bob", "email": "", "role": "staff", "is_active": "1"})
with app.app_context(): check(user("bob").totp_enabled, "saving someone's page without the tick leaves two-factor on")
ca.post(f"/team/{ids['bob']}/edit", data={"name": "Bob", "email": "", "role": "staff", "is_active": "1", "reset_2fa": "1"})
with app.app_context():
    b = user("bob"); check(not b.totp_enabled and not b.totp_secret and not b.recovery_hashes, "an admin can switch off a colleague's two-factor")
r = ca.get(f"/team/{ids['bob']}/edit")
check(r.status_code == 200, "the team edit page still opens")

# ------------------------------------------------------------ required roles
os.environ["NL_REQUIRE_2FA_ROLES"] = "partner,admin"
c = login_as(ids["alice"])
r = c.get("/clients/")
check(r.status_code == 302 and "/auth/2fa/setup" in r.headers["Location"], "a required role is sent to set-up from every page")
check(c.get("/auth/2fa/setup").status_code == 200, "the set-up page itself is reachable")
check(c.get("/static/css/style.css").status_code != 302, "static files are not redirected")
r = c.get("/auth/logout")
cb = login_as(ids["bob"])
check(cb.get("/auth/account").status_code == 200, "roles not listed are not forced")
c = login_as(ids["alice"]); c.get("/auth/2fa/setup")
with c.session_transaction() as s: asec = s["totp_setup_secret"]
c.post("/auth/2fa/setup", data={"code": tf.totp_now(asec)})
check(c.get("/clients/").status_code == 200, "once set up, the required role works normally")
r = c.post("/auth/2fa/disable", data={"password": "Correct-horse-9", "code": tf.totp_now(asec)})
with app.app_context(): check(user("alice").totp_enabled, "a required role cannot switch it off")
os.environ.pop("NL_REQUIRE_2FA_ROLES")

# ------------------------------------------------------------ inactive users and a deleted pending user
with app.app_context():
    u = user("alice"); u.is_active_flag = False; db.session.commit()
c = app.test_client()
r = c.post("/auth/login", data={"username": "alice", "password": "Correct-horse-9"})
check(not signed_in(c) and "/auth/2fa" not in (r.headers.get("Location") or ""), "an inactive account does not get as far as the code page")

print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
