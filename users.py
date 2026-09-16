from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import User, Permission, PERMISSIONS, USER_ROLES, user_has_permission

users_bp = Blueprint("users", __name__, url_prefix="/team")


def admin_required(f):
    """A hardcoded (never configurable) admin-only check - used only for the
    Permissions settings screen itself. That screen controls who else gets
    delegated administrative capabilities, so it must never be one of the
    capabilities it hands out - otherwise a role granted "Manage Team
    Members" could grant itself (or anyone) more access than an actual
    admin intended, or lock every admin out."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def manage_users_required(f):
    """Managing team members is gated by the configurable "Manage Team
    Members" permission (Team > Permissions) - defaults to admin only, same
    as before this was made configurable."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not user_has_permission(current_user, "manage_users"):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


@users_bp.route("/")
@login_required
@manage_users_required
def list_users():
    people = User.query.order_by(User.name).all()
    return render_template("users/list.html", people=people)


@users_bp.route("/new", methods=["GET", "POST"])
@login_required
@manage_users_required
def new_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if User.query.filter_by(username=username).first():
            flash("That username is already taken.", "danger")
            return render_template("users/form.html", person=None)
        requested_role = request.form.get("role", "staff")
        if requested_role == "admin" and current_user.role != "admin":
            # Someone with a delegated "Manage Team Members" permission (not
            # an actual admin) can't grant the admin role to anyone,
            # including themselves - only a real admin can create another
            # admin. Silently fall back to staff rather than blocking the
            # whole action.
            flash("Only an admin can grant the admin role - the new team member was created as Staff instead.", "info")
            requested_role = "staff"
        person = User(
            username=username,
            name=request.form.get("name", "").strip(),
            email=request.form.get("email", "").strip(),
            role=requested_role,
        )
        person.set_password(request.form.get("password") or "changeme123")
        db.session.add(person)
        db.session.commit()
        flash(f"Team member {person.name} added.", "success")
        return redirect(url_for("users.list_users"))
    return render_template("users/form.html", person=None)


@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@manage_users_required
def edit_user(user_id):
    person = User.query.get_or_404(user_id)
    if request.method == "POST":
        person.name = request.form.get("name", "").strip()
        person.email = request.form.get("email", "").strip()
        requested_role = request.form.get("role", "staff")
        if requested_role == "admin" and current_user.role != "admin" and person.role != "admin":
            flash("Only an admin can grant the admin role - this team member's role was left unchanged.", "info")
        else:
            person.role = requested_role
        person.is_active_flag = bool(request.form.get("is_active"))
        new_password = request.form.get("password")
        if new_password:
            person.set_password(new_password)
        db.session.commit()
        flash(f"Updated {person.name}.", "success")
        return redirect(url_for("users.list_users"))
    return render_template("users/form.html", person=person)


@users_bp.route("/permissions", methods=["GET", "POST"])
@login_required
@admin_required
def manage_permissions():
    """Team > Permissions - lets an admin allow or deny each of the
    configurable administrative capabilities (PERMISSIONS in models.py) per
    role. Admin-only, hardcoded (see admin_required above) since this screen
    is what hands out every other delegated capability."""
    if request.method == "POST":
        for key, _, _, _ in PERMISSIONS:
            for role in USER_ROLES:
                if role == "admin":
                    continue  # admin is always fully allowed, never stored as a toggle
                allowed = request.form.get(f"perm__{key}__{role}") == "on"
                perm = Permission.query.filter_by(role=role, permission_key=key).first()
                if not perm:
                    perm = Permission(role=role, permission_key=key)
                    db.session.add(perm)
                perm.allowed = allowed
        db.session.commit()
        flash("Permissions updated.", "success")
        return redirect(url_for("users.manage_permissions"))

    rows = Permission.query.all()
    current = {(p.role, p.permission_key): p.allowed for p in rows}
    return render_template(
        "users/permissions.html",
        permissions=PERMISSIONS,
        roles=[r for r in USER_ROLES if r != "admin"],
        current=current,
    )
