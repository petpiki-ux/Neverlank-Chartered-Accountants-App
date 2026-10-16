import os
import re
import uuid

from datetime import date, datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, current_app, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    Client, INDUSTRY_OPTIONS, user_has_permission, user_can_access_engagement,
    client_scope_ids, user_can_access_client,
    COMPANY_DOCUMENT_TYPES, PERSON_ROLES, PUBLIC_RESEARCH_SCOPES, FilingIndexSection,
    TAX_HEADS, FILING_ARCHIVE_SECTIONS, LegislativeUpdate,
    ClientSanctionsScreening, ClientKeyPerson, screening_assessment_for,
    SANCTIONS_SCREENING_SOURCES, SANCTIONS_SCREENING_RESULTS, SANCTIONS_AUTO_SOURCES,
    REGULATORY_NOTICE_SOURCES, RegulatoryNotice, SanctionsListStatus,
)
from engagements import sync_substantive_procedures_if_started

_LOGO_EXTENSIONS = {"png", "jpg", "jpeg"}


def _logos_dir():
    directory = current_app.config["CLIENT_LOGOS_DATA_DIR"]
    os.makedirs(directory, exist_ok=True)
    return directory

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


def _duplicate_message(label, value, duplicate):
    """Warning for a duplicate number. A client the person is not working on is
    not named (that would reveal it) - they are told to ask a Partner instead."""
    if user_can_access_client(current_user, duplicate):
        return (f"A client with {label} \"{value}\" already exists: "
                f"{duplicate.name}. Open that client instead, or double-check the number.")
    return (f"A client with {label} \"{value}\" is already on the system. You are not working on that client - "
            "ask a Partner to add you to its engagement team instead of creating it again.")


TAX_ID_FIELDS = {"tin_number": "TIN", "vat_number": "VAT number"}


def _clean_tax_id(value):
    """Tidy what was typed: trim and collapse stray spaces. Case and any
    dashes are left as typed (what the person sees is what they entered)."""
    return " ".join((value or "").split())[:40]


def _tax_id_key(value):
    """The form of an identifier used only to compare two of them: spaces,
    dashes and slashes ignored, case ignored - so "2000-123 456" and
    "2000123456" count as the same TIN."""
    return re.sub(r"[\s\-/]", "", value or "").upper()


def _find_duplicate_by_tax_id(field, value, exclude_client_id=None):
    """Another client already holding this TIN / VAT number, or None. Blank
    values never collide (not every client is VAT registered)."""
    key = _tax_id_key(value)
    if not key:
        return None
    for other in Client.query.filter(getattr(Client, field).isnot(None)).all():
        if other.id != exclude_client_id and _tax_id_key(getattr(other, field)) == key:
            return other
    return None


def _check_duplicate_ids(company_number, tin, vat, exclude_client_id=None):
    """First clash among company number, TIN and VAT number as
    (human label, value, other client), or None."""
    dup = _find_duplicate_by_company_number(company_number, exclude_client_id)
    if dup:
        return "company number", company_number, dup
    for field, value in (("tin_number", tin), ("vat_number", vat)):
        dup = _find_duplicate_by_tax_id(field, value, exclude_client_id)
        if dup:
            return TAX_ID_FIELDS[field], value, dup
    return None


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


def _search_visible_clients(q=""):
    """Clients matching `q` (name, company number, TIN or VAT number) that the
    current user is allowed to see, by name."""
    query = Client.query
    scope = client_scope_ids(current_user)
    if scope is not None:
        query = query.filter(Client.id.in_(scope)) if scope else query.filter(db.false())
    if q:
        query = query.filter(
            db.or_(
                Client.name.ilike(f"%{q}%"), Client.company_number.ilike(f"%{q}%"),
                Client.tin_number.ilike(f"%{q}%"), Client.vat_number.ilike(f"%{q}%"),
            )
        )
    return query.order_by(Client.name).all()


@clients_bp.route("/")
@login_required
def list_clients():
    q = request.args.get("q", "").strip()
    all_clients = _search_visible_clients(q)
    return render_template("clients/list.html", clients=all_clients, q=q)


@clients_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_client():
    if request.method == "POST":
        form_values = dict(request.form)
        company_number = request.form.get("company_number", "").strip()
        tin_number = _clean_tax_id(request.form.get("tin_number"))
        vat_number = _clean_tax_id(request.form.get("vat_number"))
        clash = _check_duplicate_ids(company_number, tin_number, vat_number)
        if clash:
            label, value, duplicate = clash
            flash(_duplicate_message(label, value, duplicate), "danger")
            visible_duplicate = duplicate if user_can_access_client(current_user, duplicate) else None
            return render_template(
                "clients/form.html", client=None, industry_options=INDUSTRY_OPTIONS,
                form_values=form_values, duplicate_client=visible_duplicate, duplicate_label=label, duplicate_hidden=visible_duplicate is None,
            )
        client = Client(
            name=request.form.get("name", "").strip(),
            contact_person=request.form.get("contact_person", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            address=request.form.get("address", "").strip(),
            industry=_resolve_industry(request.form),
            company_number=company_number,
            tin_number=tin_number or None,
            vat_number=vat_number or None,
            notes=request.form.get("notes", "").strip(),
            created_by_id=current_user.id,
        )
        db.session.add(client)
        db.session.commit()
        flash(f"Client '{client.name}' created.", "success")
        return redirect(url_for("clients.view_client", client_id=client.id))
    return render_template("clients/form.html", client=None, industry_options=INDUSTRY_OPTIONS)


def _invoice_visible(invoice):
    """An invoice linked to an engagement is hidden from anyone not on that engagement."""
    from models import user_can_access_engagement
    return not invoice.engagement_id or user_can_access_engagement(current_user, invoice.engagement)


def _visible_zimra_cases(cases):
    """The client's ZIMRA cases the current user may see (open first, newest first)."""
    from models import user_can_access_engagement
    shown = [c for c in cases if not c.engagement_id or user_can_access_engagement(current_user, c.engagement)]
    return sorted(shown, key=lambda c: (c.is_closed, -(c.id or 0)))


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
    # Soonest deadline first; an engagement with no deadline set goes last
    # (comparing a blank deadline with a date used to crash this page).
    visible_engagements = sorted(visible_engagements, key=lambda e: (e.deadline is None, e.deadline or date.max))
    suggested_people = [p for p in client.key_people if p.status == "Suggested"]
    confirmed_people = [p for p in client.key_people if p.status == "Confirmed"]
    has_legacy_details = any(
        (p.details or "").strip() and not p.needs_detail_review
        and not (p.number_of_shares or p.shareholding_percentage or p.id_number or p.nationality or p.address)
        for p in client.key_people
    )
    permanent_file_sections = FilingIndexSection.query.filter_by(is_permanent=True, is_active=True).order_by(FilingIndexSection.order, FilingIndexSection.code).all()
    # Group this client's Tax & Accounting Filing Archive documents by
    # section (tax_clearance/return/schedule - see models.
    # FILING_ARCHIVE_SECTIONS) so the template can render each of the three
    # sections as its own list without re-filtering client.
    # filing_archive_documents three times itself.
    filing_archive_by_section = {key: [] for key, _ in FILING_ARCHIVE_SECTIONS}
    for doc in client.filing_archive_documents:
        filing_archive_by_section.setdefault(doc.section, []).append(doc)
    # Sanctions / AML vetting: one list per client, applying to every engagement (see ClientSanctionsScreening).
    sanctions_list_status = {
        row.source: row for row in SanctionsListStatus.query.filter(SanctionsListStatus.source.in_(SANCTIONS_AUTO_SOURCES)).all()
    }
    regulatory_notice_counts = {}
    for source in REGULATORY_NOTICE_SOURCES:
        notices = RegulatoryNotice.query.filter_by(source=source).all()
        regulatory_notice_counts[source] = {"total": len(notices), "searchable": sum(1 for n in notices if n.extraction_status == "extracted")}
    return render_template(
        "clients/detail.html", client=client, visible_engagements=visible_engagements,
        vetting_list=list(client.sanctions_screenings),
        vetting_assessment=screening_assessment_for(client.sanctions_screenings),
        vetting_key_people=[p for p in confirmed_people if p.full_name not in {s.individual_name for s in client.sanctions_screenings}],
        can_vet=user_can_access_client(current_user, client),
        has_investigative=any(e.type == "Investigative Engagement" for e in client.engagements),
        sanctions_screening_sources=SANCTIONS_SCREENING_SOURCES,
        sanctions_screening_results=SANCTIONS_SCREENING_RESULTS,
        sanctions_auto_sources=SANCTIONS_AUTO_SOURCES,
        sanctions_list_status=sanctions_list_status,
        can_manage_sanctions_lists=user_has_permission(current_user, "manage_sanctions_lists"),
        regulatory_notice_sources=REGULATORY_NOTICE_SOURCES,
        regulatory_notice_counts=regulatory_notice_counts,
        suggested_people=suggested_people, confirmed_people=confirmed_people,
        has_legacy_details=has_legacy_details,
        company_document_types=COMPANY_DOCUMENT_TYPES, person_roles=PERSON_ROLES,
        public_research_scopes=PUBLIC_RESEARCH_SCOPES,
        public_research_runs=client.public_research_runs,
        can_manage_company_documents=user_has_permission(current_user, "manage_company_documents"),
        permanent_file_sections=permanent_file_sections,
        filing_archive_sections=FILING_ARCHIVE_SECTIONS,
        filing_archive_by_section=filing_archive_by_section,
        tax_heads=TAX_HEADS,
        can_delete_filing_archive=user_has_permission(current_user, "delete_documents"),
        legislative_updates_for_tagging=LegislativeUpdate.query.order_by(LegislativeUpdate.created_at.desc()).all(),
        zimra_cases=_visible_zimra_cases(client.zimra_cases),
        visible_invoices=[i for i in client.invoices if _invoice_visible(i)],
        visible_advances=[a for a in client.advances if not a.is_void and (not a.engagement_id or user_can_access_engagement(current_user, a.engagement))],
    )


@clients_bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = Client.query.get_or_404(client_id)
    if request.method == "POST":
        company_number = request.form.get("company_number", "").strip()
        tin_number = _clean_tax_id(request.form.get("tin_number"))
        vat_number = _clean_tax_id(request.form.get("vat_number"))
        clash = _check_duplicate_ids(company_number, tin_number, vat_number, exclude_client_id=client.id)
        if clash:
            label, value, duplicate = clash
            flash(_duplicate_message(label, value, duplicate), "danger")
            visible_duplicate = duplicate if user_can_access_client(current_user, duplicate) else None
            return render_template(
                "clients/form.html", client=client, industry_options=INDUSTRY_OPTIONS,
                form_values=dict(request.form), duplicate_client=visible_duplicate, duplicate_label=label, duplicate_hidden=visible_duplicate is None,
            )
        client.name = request.form.get("name", "").strip()
        client.contact_person = request.form.get("contact_person", "").strip()
        client.email = request.form.get("email", "").strip()
        client.phone = request.form.get("phone", "").strip()
        client.address = request.form.get("address", "").strip()
        old_industry = client.industry
        client.industry = _resolve_industry(request.form)
        client.company_number = company_number
        client.tin_number = tin_number or None
        client.vat_number = vat_number or None
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


@clients_bp.route("/<int:client_id>/logo/upload", methods=["POST"])
@login_required
def upload_client_logo(client_id):
    """The client's own logo (not the firm's) - used only on the cover page
    of that client's generated Financial Statements. One file per client:
    uploading a new one replaces (and deletes) whatever was there before,
    rather than accumulating old copies with nothing pointing at them."""
    client = Client.query.get_or_404(client_id)
    file = request.files.get("logo")
    if not file or file.filename == "":
        flash("Please choose an image file to upload.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in _LOGO_EXTENSIONS:
        flash("Only PNG and JPG images are accepted for a client logo.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))

    old_filename = client.logo_filename
    stored_name = f"client{client.id}_{uuid.uuid4().hex[:8]}.{ext}"
    file.save(os.path.join(_logos_dir(), stored_name))
    client.logo_filename = stored_name
    db.session.commit()
    if old_filename:
        old_path = os.path.join(_logos_dir(), old_filename)
        if os.path.exists(old_path):
            os.remove(old_path)
    flash("Client logo uploaded - it will appear on this client's Financial Statements cover page.", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@clients_bp.route("/<int:client_id>/logo/remove", methods=["POST"])
@login_required
def remove_client_logo(client_id):
    client = Client.query.get_or_404(client_id)
    old_filename = client.logo_filename
    client.logo_filename = None
    db.session.commit()
    if old_filename:
        old_path = os.path.join(_logos_dir(), old_filename)
        if os.path.exists(old_path):
            os.remove(old_path)
    flash("Client logo removed.", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@clients_bp.route("/<int:client_id>/logo")
@login_required
def client_logo_image(client_id):
    """Serves the client's own logo inline (not as a download) so it can be
    used as an <img> preview on the Client and Finalisation tabs."""
    client = Client.query.get_or_404(client_id)
    if not client.logo_filename:
        abort(404)
    return send_from_directory(_logos_dir(), client.logo_filename)


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


# ---------- Sanctions & AML vetting (applies to every engagement for the client) ----------
# One list of key individuals per client, screened against the UN, OFAC and EU sanctions lists and
# the RBZ / FIU adverse notices (matching in acceptance.auto_screen_individual). Each engagement's
# Acceptance tab shows a summary of it and links here. Advisory only: it never blocks anything.

def _vetting_client(client_id):
    client = Client.query.get_or_404(client_id)
    if not user_can_access_client(current_user, client):
        abort(403)
    return client


def _vetting_row(screening_id):
    row = ClientSanctionsScreening.query.get_or_404(screening_id)
    if not user_can_access_client(current_user, row.client_id):
        abort(403)
    return row


def _to_vetting(client_id):
    return redirect(url_for("clients.view_client", client_id=client_id, _anchor="vetting"))


@clients_bp.route("/<int:client_id>/vetting/add", methods=["POST"])
@login_required
def add_vetting_individual(client_id):
    client = _vetting_client(client_id)
    name = request.form.get("individual_name", "").strip()
    if not name:
        flash("Enter the individual's name before adding them for vetting.", "danger")
        return _to_vetting(client.id)
    if any(" ".join(s.individual_name.lower().split()) == " ".join(name.lower().split()) for s in client.sanctions_screenings):
        flash(f"{name} is already on this client's vetting list.", "info")
        return _to_vetting(client.id)
    db.session.add(ClientSanctionsScreening(
        client_id=client.id, individual_name=name, role_description=request.form.get("role_description", "").strip(),
        order=max([s.order or 0 for s in client.sanctions_screenings], default=0) + 1, created_by_id=current_user.id))
    db.session.commit()
    flash(f"{name} added for sanctions and AML vetting.", "success")
    return _to_vetting(client.id)


@clients_bp.route("/<int:client_id>/vetting/add-from-key-person", methods=["POST"])
@login_required
def add_vetting_from_key_person(client_id):
    """Pull a Confirmed Director/Shareholder already on file for the client onto the vetting list (an AI-suggested one has not been checked by a person yet, so it is not offered)."""
    client = _vetting_client(client_id)
    person = ClientKeyPerson.query.get_or_404(request.form.get("key_person_id", type=int))
    if person.client_id != client.id:
        abort(403)
    if person.status != "Confirmed":
        flash(f"{person.full_name} has not been confirmed yet on the Directors & Shareholders list - review and confirm them there first.", "danger")
        return _to_vetting(client.id)
    if any(s.individual_name == person.full_name for s in client.sanctions_screenings):
        flash(f"{person.full_name} is already on the vetting list.", "info")
        return _to_vetting(client.id)
    db.session.add(ClientSanctionsScreening(
        client_id=client.id, individual_name=person.full_name, role_description=person.role,
        order=max([s.order or 0 for s in client.sanctions_screenings], default=0) + 1, created_by_id=current_user.id))
    db.session.commit()
    flash(f"{person.full_name} added for sanctions and AML vetting.", "success")
    return _to_vetting(client.id)


@clients_bp.route("/vetting/<int:screening_id>/update", methods=["POST"])
@login_required
def update_vetting(screening_id):
    row = _vetting_row(screening_id)
    for field, _label in SANCTIONS_SCREENING_SOURCES:
        value = request.form.get(field, "Not Checked").strip()
        setattr(row, field, value if value in SANCTIONS_SCREENING_RESULTS else "Not Checked")
    row.notes = request.form.get("notes", "").strip()
    row.role_description = request.form.get("role_description", row.role_description or "").strip()
    row.screened_by_id = current_user.id
    row.screened_at = datetime.utcnow()
    db.session.commit()
    flash("Vetting result saved.", "success")
    return _to_vetting(row.client_id)


@clients_bp.route("/vetting/<int:screening_id>/delete", methods=["POST"])
@login_required
def delete_vetting(screening_id):
    row = _vetting_row(screening_id)
    client_id = row.client_id
    db.session.delete(row)
    db.session.commit()
    flash("Vetting record deleted.", "success")
    return _to_vetting(client_id)


@clients_bp.route("/vetting/<int:screening_id>/auto-screen", methods=["POST"])
@login_required
def auto_screen_vetting(screening_id):
    from acceptance import auto_screen_individual
    row = _vetting_row(screening_id)
    auto_screen_individual(row)
    db.session.commit()
    flash(f"Auto-screened {row.individual_name} against UN/OFAC/EU and the uploaded RBZ/FIU notices - review any Potential Match below.", "success")
    return _to_vetting(row.client_id)


@clients_bp.route("/<int:client_id>/vetting/auto-screen-all", methods=["POST"])
@login_required
def auto_screen_all_vetting(client_id):
    from acceptance import auto_screen_individual
    client = _vetting_client(client_id)
    if not client.sanctions_screenings:
        flash("No key individuals added for vetting yet.", "info")
        return _to_vetting(client.id)
    for row in client.sanctions_screenings:
        auto_screen_individual(row)
    db.session.commit()
    flash(f"Auto-screened all {len(client.sanctions_screenings)} individual(s) - review any Potential Match below.", "success")
    return _to_vetting(client.id)
