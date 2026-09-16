from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import Client, INDUSTRY_OPTIONS, user_has_permission

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")


def _resolve_industry(form):
    """The industry field is a <select> of INDUSTRY_OPTIONS with "Other"
    revealing a free-text companion field - resolve whichever was actually
    meant. Anything unexpected (e.g. a stale value) falls back to "Other"
    with the raw value preserved as free text rather than being dropped."""
    choice = form.get("industry", "").strip()
    other_text = form.get("industry_other", "").strip()
    if choice == "Other":
        return other_text or "Other"
    if choice in INDUSTRY_OPTIONS:
        return choice
    return choice  # blank, or a legacy free-text value from before this field existed


@clients_bp.route("/")
@login_required
def list_clients():
    q = request.args.get("q", "").strip()
    query = Client.query
    if q:
        query = query.filter(Client.name.ilike(f"%{q}%"))
    all_clients = query.order_by(Client.name).all()
    return render_template("clients/list.html", clients=all_clients, q=q)


@clients_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_client():
    if request.method == "POST":
        client = Client(
            name=request.form.get("name", "").strip(),
            contact_person=request.form.get("contact_person", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            address=request.form.get("address", "").strip(),
            industry=_resolve_industry(request.form),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(client)
        db.session.commit()
        flash(f"Client '{client.name}' created.", "success")
        return redirect(url_for("clients.view_client", client_id=client.id))
    return render_template("clients/form.html", client=None, industry_options=INDUSTRY_OPTIONS)


@clients_bp.route("/<int:client_id>")
@login_required
def view_client(client_id):
    client = Client.query.get_or_404(client_id)
    return render_template("clients/detail.html", client=client)


@clients_bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = Client.query.get_or_404(client_id)
    if request.method == "POST":
        client.name = request.form.get("name", "").strip()
        client.contact_person = request.form.get("contact_person", "").strip()
        client.email = request.form.get("email", "").strip()
        client.phone = request.form.get("phone", "").strip()
        client.address = request.form.get("address", "").strip()
        client.industry = _resolve_industry(request.form)
        client.notes = request.form.get("notes", "").strip()
        db.session.commit()
        flash("Client updated.", "success")
        return redirect(url_for("clients.view_client", client_id=client.id))
    return render_template("clients/form.html", client=client, industry_options=INDUSTRY_OPTIONS)


@clients_bp.route("/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    if not user_has_permission(current_user, "delete_clients"):
        abort(403)
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    flash("Client and all its engagements were deleted.", "info")
    return redirect(url_for("clients.list_clients"))
