from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("engagements.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.is_active_flag and user.check_password(password):
            login_user(user)
            flash(f"Welcome back, {user.name}.", "success")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("engagements.dashboard"))
        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
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
        current_user.name = name or current_user.name
        current_user.email = email
        if new_password:
            current_user.set_password(new_password)
        db.session.commit()
        flash("Account updated.", "success")
        return redirect(url_for("auth.account"))
    return render_template("auth/account.html")
