"""Security hardening for the whole app (one place, wired in by app.create_app).

* Cross-site request forgery: every state-changing request (POST / PUT / PATCH / DELETE) must come from this site itself. Modern browsers
  say so with the Sec-Fetch-Site header; older ones send Origin or Referer. A request that is clearly cross-site is refused. Requests with
  none of these headers (scripts, tests) are allowed: a browser always sends at least one of them on a cross-site form post.
* Session cookies are HttpOnly, SameSite=Lax and (on https) Secure.
* Standard response headers: no MIME sniffing, no framing by other sites, a strict referrer policy, HSTS on https, and a permissions
  policy that still lets the site's own pages use the camera and microphone (voice and video calls).
* Login throttling: repeated failures from one address for one user are locked out for a while (in memory; the app runs as one process).
* Password rules and safe redirect targets.
"""
import os
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import abort, request

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_SECRET = "neverlank-dev-secret-change-me"


def _extra_hosts():
    hosts = set()
    for raw in (os.environ.get("TRUSTED_ORIGINS", "") + "," + os.environ.get("PUBLIC_BASE_URL", "")).split(","):
        raw = raw.strip()
        if not raw:
            continue
        host = urlparse(raw if "//" in raw else "//" + raw).netloc.lower()
        if host:
            hosts.add(host)
    return hosts


def same_origin_ok(req=None):
    """True when the request may change state: it was made from this site (or carries no browser headers at all)."""
    req = req or request
    site = (req.headers.get("Sec-Fetch-Site") or "").lower()
    if site:
        return site in ("same-origin", "none")
    allowed = {req.host.lower()} | _extra_hosts()
    for header in ("Origin", "Referer"):
        value = req.headers.get(header)
        if value:
            if value.strip().lower() == "null":
                return False
            netloc = urlparse(value).netloc.lower()
            return netloc == "" or netloc in allowed        # a relative Referer ('/page') points at this site
    return True


def safe_next(target, fallback):
    """A post-login redirect target is used only if it is a path on this site ('/x', not '//host' or '/\\host')."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target and not re.match(r"^/+[a-z][a-z0-9+.-]*:", target, re.I):
        return target
    return fallback


# --------------------------------------------------------------------------- passwords

COMMON_PASSWORDS = {"changeme123", "password", "password1", "password123", "123456789", "1234567890", "qwerty123", "welcome123", "neverlank", "neverlank123",
                    "letmein123", "admin12345", "iloveyou12"}
MIN_PASSWORD = 10


def password_problem(pw):
    """None when the password is acceptable, else a sentence saying what is wrong."""
    pw = pw or ""
    if len(pw) < MIN_PASSWORD:
        return f"The password must be at least {MIN_PASSWORD} characters long."
    if pw.isdigit():
        return "The password cannot be only numbers."
    if pw.lower() in COMMON_PASSWORDS or len(set(pw)) < 4:
        return "That password is too easy to guess. Choose a longer, less common one."
    return None


def temporary_password():
    """A readable random password (no look-alike characters) for a new account."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


# --------------------------------------------------------------------------- login throttling

class LoginThrottle:
    """Locks a (client address, username) pair after MAX_FAILS failures within WINDOW seconds, and a client address after IP_MAX_FAILS."""
    MAX_FAILS = 5
    IP_MAX_FAILS = 25
    WINDOW = 15 * 60
    LOCK = 15 * 60

    def __init__(self):
        self._lock = threading.Lock()
        self._fails = {}        # key -> [timestamps]
        self._locked = {}       # key -> until

    def _keys(self, ip, username):
        return (("u", ip, (username or "").lower()), ("ip", ip))

    def _prune(self, key, now):
        self._fails[key] = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]

    def retry_after(self, ip, username, now=None):
        """Seconds until the next attempt is allowed (0 = allowed)."""
        now = now or time.time()
        with self._lock:
            wait = 0
            for key in self._keys(ip, username):
                until = self._locked.get(key, 0)
                if until > now:
                    wait = max(wait, int(until - now) + 1)
                elif until:
                    self._locked.pop(key, None)
            return wait

    def failure(self, ip, username, now=None):
        now = now or time.time()
        with self._lock:
            for key, limit in zip(self._keys(ip, username), (self.MAX_FAILS, self.IP_MAX_FAILS)):
                self._prune(key, now)
                self._fails.setdefault(key, []).append(now)
                if len(self._fails[key]) >= limit:
                    self._locked[key] = now + self.LOCK
                    self._fails[key] = []

    def success(self, ip, username):
        with self._lock:
            key = self._keys(ip, username)[0]
            self._fails.pop(key, None)
            self._locked.pop(key, None)

    def reset(self):
        with self._lock:
            self._fails.clear()
            self._locked.clear()


throttle = LoginThrottle()


def client_ip(req=None):
    """The caller's address. Behind the host's proxy the last X-Forwarded-For entry is the one the proxy itself added."""
    req = req or request
    fwd = (req.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
    return fwd or req.remote_addr or "?"


# --------------------------------------------------------------------------- wiring

def init_security(app):
    https = (os.environ.get("PUBLIC_BASE_URL", "").startswith("https://") or bool(os.environ.get("RENDER"))) and not app.config.get("TESTING")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    if https:
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["REMEMBER_COOKIE_SECURE"] = True
    if app.config.get("SECRET_KEY") == DEFAULT_SECRET and not app.config.get("TESTING") and not os.environ.get("NEVERLANK_KEEP_DEFAULT_KEY"):
        # The built-in key is public (it is in the source code), so anyone could forge a login cookie with it. When the host has not set a
        # SECRET_KEY, make a random one once and keep it on the data disk so sessions survive restarts.
        try:
            from config import INSTANCE_DIR
            path = os.path.join(INSTANCE_DIR, "secret_key")
            os.makedirs(INSTANCE_DIR, exist_ok=True)
            if os.path.exists(path):
                key = open(path).read().strip()
            else:
                key = secrets.token_hex(32)
                with open(path, "w") as fh:
                    fh.write(key)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            if len(key) >= 32:
                app.config["SECRET_KEY"] = key
        except Exception:
            app.logger.warning("Could not create a private SECRET_KEY: set SECRET_KEY in the environment.")

    @app.before_request
    def _csrf_same_origin():
        if request.method in UNSAFE_METHODS and not same_origin_ok():
            abort(403, description="This request did not come from this site, so it was refused (cross-site request protection). "
                                   "Go back, reload the page and try again.")

    @app.after_request
    def _security_headers(resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(), payment=()")
        if https:
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp
