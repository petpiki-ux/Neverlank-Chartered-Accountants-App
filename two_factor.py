"""Optional two-factor sign-in (authenticator-app codes) with one-time recovery codes.

* Each person can turn it on under My Account > Two-factor sign-in: they add the key to an authenticator app (Microsoft/Google Authenticator,
  Authy ...), confirm with one code, and are shown 8 recovery codes once. From then on, signing in needs the password AND a 6-digit code.
* The codes are standard TOTP (RFC 6238: HMAC-SHA1, 30 seconds, 6 digits), so any authenticator app works. A code can be used only once.
* A recovery code (each works once) stands in for the app if the phone is lost. An admin can switch two-factor off for someone
  (Team > Edit) when they have lost both.
* NL_REQUIRE_2FA_ROLES (for example "admin,partner") makes it compulsory for those roles: until they have set it up, every page sends
  them to the set-up page. Left empty (the default) it is optional for everybody.
No third-party library is needed for the codes; an on-screen QR code is drawn if the small 'segno' package is installed.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_required, current_user

import security
from extensions import db

twofactor_bp = Blueprint("twofactor", __name__, url_prefix="/auth/2fa")

STEP = 30
DIGITS = 6
ISSUER = "Neverlank"
PENDING_MINUTES = 10
RECOVERY_COUNT = 8


# --------------------------------------------------------------------------- the code itself

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _hotp(secret, counter):
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(value).zfill(DIGITS)


def totp_now(secret, at=None):
    return _hotp(secret, int((at if at is not None else time.time()) // STEP))


def check_code(secret, code, last_step=None, at=None, window=1):
    """The time step the code belongs to when it is right (this step or one either side) and newer than `last_step`; otherwise None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    now_step = int((at if at is not None else time.time()) // STEP)
    for step in range(now_step - window, now_step + window + 1):
        if hmac.compare_digest(_hotp(secret, step), code) and (last_step is None or step > last_step):
            return step
    return None


def otpauth_uri(secret, username):
    from urllib.parse import quote
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_svg(text):
    """An inline SVG QR code, or None when the 'segno' package is not installed."""
    try:
        import segno
        import io
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="svg", scale=4, border=2, xmldecl=False, svgns=True, nl=False)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return None


# --------------------------------------------------------------------------- recovery codes

def _hash_recovery(user, code):
    return hashlib.sha256(f"{user.id}:{code.replace('-', '').lower()}".encode()).hexdigest()


def make_recovery_codes(user):
    """Replace the person's recovery codes; returns the new ones in plain text (shown once, stored only as hashes)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(RECOVERY_COUNT)]
    user.recovery_hashes = json.dumps([_hash_recovery(user, c) for c in codes])
    return codes


def use_recovery_code(user, code):
    code = (code or "").strip().lower()
    if len(code.replace("-", "")) != 8:
        return False
    try:
        hashes = json.loads(user.recovery_hashes or "[]")
    except ValueError:
        return False
    h = _hash_recovery(user, code)
    for stored in hashes:
        if hmac.compare_digest(stored, h):
            hashes.remove(stored)
            user.recovery_hashes = json.dumps(hashes)
            return True
    return False


def recovery_left(user):
    try:
        return len(json.loads(user.recovery_hashes or "[]"))
    except ValueError:
        return 0


def verify_second_factor(user, entered):
    """True when `entered` is a current authenticator code (used once) or an unused recovery code. Updates the user (not committed)."""
    entered = (entered or "").strip()
    step = check_code(user.totp_secret, entered, user.totp_last_step)
    if step is not None:
        user.totp_last_step = step
        return True
    return use_recovery_code(user, entered)


def required_roles():
    return {r.strip().lower() for r in os.environ.get("NL_REQUIRE_2FA_ROLES", "").split(",") if r.strip()}


def must_set_up(user):
    return bool(user.is_authenticated and not getattr(user, "totp_enabled", False) and (user.role or "").lower() in required_roles())


# --------------------------------------------------------------------------- routes

@twofactor_bp.route("/", methods=["GET", "POST"])
def challenge():
    """Second step of signing in: the password was right; now the code."""
    pending = session.get("pending_2fa")
    if not pending or time.time() - pending.get("t", 0) > PENDING_MINUTES * 60:
        session.pop("pending_2fa", None)
        flash("Please sign in again.", "info")
        return redirect(url_for("auth.login"))
    from models import User
    user = User.query.get(pending["uid"])
    if user is None or not user.is_active_flag or not user.totp_enabled:
        session.pop("pending_2fa", None)
        return redirect(url_for("auth.login"))
    ip = security.client_ip()
    if request.method == "POST":
        wait = security.throttle.retry_after(ip, user.username)
        if wait:
            flash(f"Too many failed attempts. Please wait {max(1, wait // 60 + (1 if wait % 60 else 0))} minute(s) and try again.", "danger")
            return render_template("auth/two_factor.html", username=user.username), 429
        if verify_second_factor(user, request.form.get("code", "")):
            db.session.commit()
            security.throttle.success(ip, user.username)
            session.pop("pending_2fa", None)
            from auth import complete_login
            return complete_login(user, pending.get("next"), pending.get("remember", False))
        db.session.rollback()
        security.throttle.failure(ip, user.username)
        flash("That code is not right. Check the code in your authenticator app, or use one of your recovery codes.", "danger")
    return render_template("auth/two_factor.html", username=user.username)


@twofactor_bp.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    if current_user.totp_enabled:
        return render_template("auth/two_factor_setup.html", enabled=True, left=recovery_left(current_user), required=current_user.role.lower() in required_roles())
    secret = session.get("totp_setup_secret")
    if request.method == "POST":
        step = check_code(secret, request.form.get("code", ""))
        if step is None:
            flash("That code is not right. Check that the clock on your phone is correct and try the next code.", "danger")
        else:
            current_user.totp_secret = secret
            current_user.totp_enabled = True
            current_user.totp_last_step = step
            codes = make_recovery_codes(current_user)
            db.session.commit()
            session.pop("totp_setup_secret", None)
            flash("Two-factor sign-in is on.", "success")
            return render_template("auth/two_factor_setup.html", enabled=True, new_codes=codes, left=len(codes), required=current_user.role.lower() in required_roles())
    if not secret:
        secret = session["totp_setup_secret"] = new_secret()
    uri = otpauth_uri(secret, current_user.username)
    return render_template("auth/two_factor_setup.html", enabled=False, secret=" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), uri=uri, qr=qr_svg(uri))


@twofactor_bp.route("/disable", methods=["POST"])
@login_required
def disable():
    if current_user.role.lower() in required_roles():
        flash("Two-factor sign-in is required for your role, so it cannot be switched off.", "danger")
        return redirect(url_for("twofactor.setup"))
    if not current_user.check_password(request.form.get("password", "")) or not verify_second_factor(current_user, request.form.get("code", "")):
        db.session.rollback()
        flash("Enter your password and a current code (or a recovery code) to switch two-factor sign-in off.", "danger")
        return redirect(url_for("twofactor.setup"))
    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_last_step = None
    current_user.recovery_hashes = None
    db.session.commit()
    flash("Two-factor sign-in is off.", "info")
    return redirect(url_for("auth.account"))


@twofactor_bp.route("/recovery-codes", methods=["POST"])
@login_required
def new_recovery_codes():
    if not current_user.totp_enabled:
        return redirect(url_for("twofactor.setup"))
    if not current_user.check_password(request.form.get("password", "")):
        flash("Enter your password to get new recovery codes.", "danger")
        return redirect(url_for("twofactor.setup"))
    codes = make_recovery_codes(current_user)
    db.session.commit()
    return render_template("auth/two_factor_setup.html", enabled=True, new_codes=codes, left=len(codes), required=current_user.role.lower() in required_roles())


def admin_reset(person):
    """Switch two-factor off for someone who has lost their phone and recovery codes (the caller commits)."""
    person.totp_enabled = False
    person.totp_secret = None
    person.totp_last_step = None
    person.recovery_hashes = None


def enforce_setup():
    """When NL_REQUIRE_2FA_ROLES covers the signed-in person's role, every page except the set-up page and sign-out sends them to set-up."""
    if not required_roles() or request.endpoint in (None, "static") or request.path.startswith("/socket.io"):
        return None
    if request.endpoint in ("twofactor.setup", "twofactor.challenge", "auth.logout", "auth.login"):
        return None
    if must_set_up(current_user):
        flash("Your role must use two-factor sign-in. Please set it up to continue.", "warning")
        return redirect(url_for("twofactor.setup"))
    return None
