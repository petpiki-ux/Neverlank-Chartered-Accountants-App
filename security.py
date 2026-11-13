"""Security hardening for the whole app (one place, wired in by app.create_app).

* Cross-site request forgery: every state-changing request (POST / PUT / PATCH / DELETE) must come from this site itself. Modern browsers
  say so with the Sec-Fetch-Site header; older ones send Origin or Referer. A request that is clearly cross-site is refused. Requests with
  none of these headers (scripts, tests) are allowed: a browser always sends at least one of them on a cross-site form post.
* Session cookies are HttpOnly, SameSite=Lax and (on https) Secure.
* Standard response headers: no MIME sniffing, no framing by other sites, a strict referrer policy, HSTS on https, and a permissions
  policy that still lets the site's own pages use the camera and microphone (voice and video calls).
* Login throttling: repeated failures from one address for one user are locked out for a while (in memory; the app runs as one process).
* Password rules and safe redirect targets.
* Uploads: every uploaded file, on every page, is checked once here - an accepted file type, and contents that match the type (a program or
  web page renamed to .pdf is refused).
* Idle sign-out: a session that has been idle for NL_IDLE_MINUTES (default 120; 0 turns it off) is signed out.
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


# --------------------------------------------------------------------------- uploads

# Every file type any page of the app accepts. A page may accept fewer (its own list still applies); none may accept more.
UPLOAD_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "xlsm", "ppt", "pptx", "csv", "tsv", "txt", "zip",
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff",
}
_OFFICE_ZIP = {"docx", "xlsx", "xlsm", "pptx", "zip"}
_OFFICE_OLE = {"doc", "xls", "ppt"}
_IMAGES = {
    "png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",), "jpeg": (b"\xff\xd8\xff",), "gif": (b"GIF87a", b"GIF89a"),
    "bmp": (b"BM",), "tif": (b"II*\x00", b"MM\x00*"), "tiff": (b"II*\x00", b"MM\x00*"),
}
# Whatever the extension says, a file that opens like one of these is a program or a web page, not a document.
_DANGEROUS_START = (b"MZ", b"\x7fELF", b"#!", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa")
_DANGEROUS_TEXT = (b"<?php", b"<script", b"<html", b"<!doctype html", b"<svg", b"<iframe", b"javascript:")


def upload_problem(storage):
    """Why an uploaded file (a werkzeug FileStorage) must be refused, or None when it is fine. Reads only the first few kilobytes."""
    name = (storage.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        return None                                             # nothing was chosen in this box
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in UPLOAD_EXTENSIONS:
        return f"Files of type '.{ext}' are not accepted. Allowed: PDF, Word, Excel, PowerPoint, CSV/text, images and zip."
    stream = storage.stream
    try:
        pos = stream.tell()
        head = stream.read(4096)
        stream.seek(pos)
    except (OSError, ValueError, AttributeError):
        return None
    if not head:
        return None                                             # an empty file: the page's own checks say so
    low = head.lstrip().lower()
    if head.startswith(_DANGEROUS_START) or (ext not in ("txt", "csv", "tsv") and any(low.startswith(t) for t in _DANGEROUS_TEXT)):
        return f"'{name}' is not really a {ext.upper()} file (it looks like a program or web page), so it was refused."
    if ext == "pdf" and b"%PDF" not in head[:1024]:
        return f"'{name}' is not a real PDF file."
    if ext in _OFFICE_ZIP and not head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"\xd0\xcf\x11\xe0")):
        return f"'{name}' is not a real {ext.upper()} file."
    if ext in _OFFICE_OLE and not head.startswith((b"\xd0\xcf\x11\xe0", b"PK\x03\x04")):
        # old Excel exports are sometimes plain text or XML under an .xls name; those are fine as long as they are not a page or program
        if ext == "xls" and (low.startswith(b"<?xml") or low.startswith(b"<table") or b"\x00" not in head[:512]):
            pass
        else:
            return f"'{name}' is not a real {ext.upper()} file."
    if ext in _IMAGES and not head.startswith(_IMAGES[ext]):
        return f"'{name}' is not a real {ext.upper()} image."
    if ext == "webp" and not (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
        return f"'{name}' is not a real WEBP image."
    return None


class UploadRefused(Exception):
    """Raised when a page tries to store an uploaded file that upload_problem() refuses."""


_save_guard_installed = False


def _install_save_guard():
    global _save_guard_installed
    if _save_guard_installed:
        return
    from werkzeug.datastructures import FileStorage
    original = FileStorage.save

    def checked_save(self, dst, buffer_size=16384):
        problem = upload_problem(self)
        if problem:
            raise UploadRefused(problem)
        return original(self, dst, buffer_size)

    FileStorage.save = checked_save
    _save_guard_installed = True


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

    # Uploaded files are checked at the one place every page stores them: FileStorage.save. A page's own checks (type, size, "choose a file")
    # run first and keep their own wording; whatever a page lets through is then refused here if it is a program, a web page or not what its
    # name says. Files a page only reads in memory (an Excel import) are never stored, so they are not touched.
    _install_save_guard()

    @app.errorhandler(UploadRefused)
    def _upload_refused(exc):
        from flask import flash, redirect
        from extensions import db
        db.session.rollback()
        problem = str(exc)
        ref = request.referrer or ""
        if request.accept_mimetypes.accept_html and ref and urlparse(ref).netloc.lower() in ({request.host.lower()} | _extra_hosts()):
            flash(problem, "danger")
            return redirect(ref)
        return problem, 400

    @app.before_request
    def _idle_signout():
        """Sign out a session that has been idle too long (NL_IDLE_MINUTES, default 120, 0 = never)."""
        try:
            limit = float(os.environ.get("NL_IDLE_MINUTES", "120")) * 60
        except ValueError:
            limit = 7200.0
        if limit <= 0 or request.endpoint in (None, "static") or request.path.startswith("/socket.io"):
            return None
        from flask import session, flash, redirect, url_for
        from flask_login import current_user, logout_user
        if not current_user.is_authenticated:
            session.pop("_last_active", None)
            return None
        now = time.time()
        last = session.get("_last_active")
        if last is not None and now - float(last) > limit:
            logout_user()
            session.pop("_last_active", None)
            flash("You were signed out because you were inactive for a while. Please sign in again.", "info")
            return redirect(url_for("auth.login", next=request.path if request.method == "GET" else None))
        if last is None or now - float(last) > 30:          # at most one session write every 30 seconds
            session["_last_active"] = now
        return None

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
