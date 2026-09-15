from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Client

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")


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
            industry=request.form.get("industry", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(client)
        db.session.commit()
        flash(f"Client '{client.name}' created.", "success")
        return redirect(url_for("clients.view_client", client_id=client.id))
    return render_template("clients/form.html", client=None)


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
        client.industry = request.form.get("industry", "").strip()
        client.notes = request.form.get("notes", "").strip()
        db.session.commit()
        flash("Client updated.", "success")
        return redirect(url_for("clients.view_client", client_id=client.id))
    return render_template("clients/form.html", client=client)


@clients_bp.route("/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    flash("Client and all its engagements were deleted.", "info")
    return redirect(url_for("clients.list_clients"))
