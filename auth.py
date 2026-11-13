import time

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User
import security

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def complete_login(user, nxt, remember=False):
    """Everything that happens once a person is fully signed in (password, and the code when two-factor is on)."""
    login_user(user, remember=remember)
    try:                                    # Managing Partner / Admin: bring the exchange-rate register up to date in the background
        from flask import current_app
        import firm_fx
        firm_fx.on_login(current_app, user)
    except Exception:
        pass
    flash(f"Welcome back, {user.name}.", "success")
    return redirect(nxt or url_for("engagements.dashboard"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("engagements.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip = security.client_ip()
        wait = security.throttle.retry_after(ip, username)
        if wait:
            flash(f"Too many failed sign-in attempts. Please wait {max(1, wait // 60 + (1 if wait % 60 else 0))} minute(s) and try again.", "danger")
            return render_template("auth/login.html"), 429
        user = User.query.filter_by(username=username).first()
        if user and user.is_active_flag and user.check_password(password):
            nxt = security.safe_next(request.args.get("next"), url_for("engagements.dashboard"))
            if user.totp_enabled:                   # password right - now the second step (the throttle is cleared only after the code)
                session.clear()
                session["pending_2fa"] = {"uid": user.id, "t": time.time(), "next": nxt, "remember": False}
                return redirect(url_for("twofactor.challenge"))
            security.throttle.success(ip, username)
            return complete_login(user, nxt)
        security.throttle.failure(ip, username)
        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    if (request.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site" or not security.same_origin_ok():
        return redirect(url_for("engagements.dashboard"))      # another site cannot sign people out
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        new_password = request.form.get("new_password", "")
        if new_password:
            if not current_user.check_password(request.form.get("current_password", "")):
                flash("To change your password, enter your current password correctly.", "danger")
                return redirect(url_for("auth.account"))
            problem = security.password_problem(new_password)
            if problem:
                flash(problem, "danger")
                return redirect(url_for("auth.account"))
        current_user.name = name or current_user.name
        current_user.email = email
        if new_password:
            current_user.set_password(new_password)
        db.session.commit()
        flash("Account updated.", "success")
        return redirect(url_for("auth.account"))
    return render_template("auth/account.html")
