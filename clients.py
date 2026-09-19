from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import (
    Client, INDUSTRY_OPTIONS, user_has_permission, user_can_access_engagement,
    COMPANY_DOCUMENT_TYPES, PERSON_ROLES, PUBLIC_RESEARCH_SCOPES, FilingIndexSection,
)
from engagements import sync_substantive_procedures_if_started

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")


def _find_duplicate_by_company_number(company_number, exclude_client_id=None):
    """Look up another client already using this company number, so
    onboarding can warn/block before a client gets accidentally created
    twice under two different names. Blank company numbers never collide -
    plenty of clients (sole traders, individuals) won't have one."""
    if not company_number:
        return None
    query = Client.query.filter(Client.company_number == company_number)
    if exclude_client_id is not None:
        query = query.filter(Client.id != exclude_client_id)
    return query.first()


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
        query = query.filter(
            db.or_(Client.name.ilike(f"%{q}%"), Client.company_number.ilike(f"%{q}%"))
        )
    all_clients = query.order_by(Client.name).all()
    return render_template("clients/list.html", clients=all_clients, q=q)


@clients_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_client():
    if request.method == "POST":
        form_values = dict(request.form)
        company_number = request.form.get("company_number", "").strip()
        duplicate = _find_duplicate_by_company_number(company_number)
        if duplicate:
            flash(
                f"A client with company number \"{company_number}\" already exists: "
                f"{duplicate.name}. Open that client instead, or double-check the number.",
                "danger",
            )
            return render_template(
                "clients/form.html", client=None, industry_options=INDUSTRY_OPTIONS,
                form_values=form_values, duplicate_client=duplicate,
            )
        client = Client(
            name=request.form.get("name", "").strip(),
            contact_person=request.form.get("contact_person", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            address=request.form.get("address", "").strip(),
            industry=_resolve_industry(request.form),
            company_number=company_number,
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
    # The client directory itself stays visible to everyone, but each
    # engagement underneath it follows the same confidentiality rule as
    # opening it directly - a non-admin only sees the ones they're on.
    visible_engagements = (
        client.engagements if current_user.role == "admin"
        else [e for e in client.engagements if user_can_access_engagement(current_user, e)]
    )
    suggested_people = [p for p in client.key_people if p.status == "Suggested"]
    confirmed_people = [p for p in client.key_people if p.status == "Confirmed"]
    has_legacy_details = any(
        (p.details or "").strip() and not p.needs_detail_review
        and not (p.number_of_shares or p.shareholding_percentage or p.id_number or p.nationality or p.address)
        for p in client.key_people
    )
    permanent_file_sections = FilingIndexSection.query.filter_by(is_permanent=True, is_active=True).order_by(FilingIndexSection.order, FilingIndexSection.code).all()
    return render_template(
        "clients/detail.html", client=client, visible_engagements=visible_engagements,
        suggested_people=suggested_people, confirmed_people=confirmed_people,
        has_legacy_details=has_legacy_details,
        company_document_types=COMPANY_DOCUMENT_TYPES, person_roles=PERSON_ROLES,
        public_research_scopes=PUBLIC_RESEARCH_SCOPES,
        public_research_runs=client.public_research_runs,
        can_manage_company_documents=user_has_permission(current_user, "manage_company_documents"),
        permanent_file_sections=permanent_file_sections,
    )


@clients_bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = Client.query.get_or_404(client_id)
    if request.method == "POST":
        company_number = request.form.get("company_number", "").strip()
        duplicate = _find_duplicate_by_company_number(company_number, exclude_client_id=client.id)
        if duplicate:
            flash(
                f"A client with company number \"{company_number}\" already exists: "
                f"{duplicate.name}. Open that client instead, or double-check the number.",
                "danger",
            )
            return render_template(
                "clients/form.html", client=client, industry_options=INDUSTRY_OPTIONS,
                duplicate_client=duplicate,
            )
        client.name = request.form.get("name", "").strip()
        client.contact_person = request.form.get("contact_person", "").strip()
        client.email = request.form.get("email", "").strip()
        client.phone = request.form.get("phone", "").strip()
        client.address = request.form.get("address", "").strip()
        old_industry = client.industry
        client.industry = _resolve_industry(request.form)
        client.company_number = company_number
        client.notes = request.form.get("notes", "").strip()
        db.session.flush()

        # If the industry actually changed, keep every one of this client's
        # engagements' Substantive Procedures checklists in sync rather than
        # leaving them static at whatever industry was on file when someone
        # last clicked "Generate suggested procedures" - only touches
        # engagements where that section has already been started.
        added_count = 0
        if client.industry != old_industry:
            for engagement in client.engagements:
                added_count += sync_substantive_procedures_if_started(engagement.id)

        db.session.commit()
        if added_count:
            flash(f"Client updated. {added_count} suggested procedure(s) were also added across this client's engagements to match the updated industry.", "success")
        else:
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
