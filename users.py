from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import User

users_bp = Blueprint("users", __name__, url_prefix="/team")


def admin_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapped


@users_bp.route("/")
@login_required
@admin_required
def list_users():
    people = User.query.order_by(User.name).all()
    return render_template("users/list.html", people=people)


@users_bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if User.query.filter_by(username=username).first():
            flash("That username is already taken.", "danger")
            return render_template("users/form.html", person=None)
        person = User(
            username=username,
            name=request.form.get("name", "").strip(),
            email=request.form.get("email", "").strip(),
            role=request.form.get("role", "staff"),
        )
        person.set_password(request.form.get("password") or "changeme123")
        db.session.add(person)
        db.session.commit()
        flash(f"Team member {person.name} added.", "success")
        return redirect(url_for("users.list_users"))
    return render_template("users/form.html", person=None)


@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_user(user_id):
    person = User.query.get_or_404(user_id)
    if request.method == "POST":
        person.name = request.form.get("name", "").strip()
        person.email = request.form.get("email", "").strip()
        person.role = request.form.get("role", "staff")
        person.is_active_flag = bool(request.form.get("is_active"))
        new_password = request.form.get("password")
        if new_password:
            person.set_password(new_password)
        db.session.commit()
        flash(f"Updated {person.name}.", "success")
        return redirect(url_for("users.list_users"))
    return render_template("users/form.html", person=person)
