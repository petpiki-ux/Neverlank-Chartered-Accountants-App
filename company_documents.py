"""Company Documents & Key People - lives on the Client (see models.
CompanyDocument, models.ClientKeyPerson), not on any one engagement, so
it's filled in once per client and reused by every engagement that client
ever has. Upload a company registration document (certificate of
incorporation, CR14, share register, etc); its text is extracted the same
way as a Regulatory Notice (sanctions_data.extract_pdf_text), and then the
Claude API is asked to pick out every Director/Shareholder/Beneficial
Owner/Company Secretary named in it (entity_extraction.py) - reading
scanned/photographed pages as images when there's no text layer to work
from. Every person found this way starts as "Suggested": a real person
still needs to review, correct if needed, and confirm each one (or add one
manually) before it's treated as settled - see models.ClientKeyPerson and
the conservative philosophy explained there and in entity_extraction.py.

Confirmed people can be pulled straight into an engagement's Sanctions &
Adverse Notice Screening list with one click (see acceptance.py's
add_key_person_to_screening) instead of being retyped in for every
engagement.
"""
import os
import uuid
from datetime import datetime

from flask import (
    Blueprint, redirect, url_for, request, flash, current_app, abort,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    Client, CompanyDocument, ClientKeyPerson,
    COMPANY_DOCUMENT_TYPES, PERSON_ROLES, PERSON_STATUSES,
    user_has_permission,
)
from config import Config
import sanctions_data
import entity_extraction

company_documents_bp = Blueprint("company_documents", __name__, url_prefix="/clients")

ALLOWED_COMPANY_DOC_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}


def editor_required(f):
    """Uploading/deleting company documents and confirming/editing/deleting
    the people found in them is gated by the configurable "Manage Company
    Documents" permission (Team > Permissions) - unrestricted by default,
    same granularity as the rest of Client Acceptance's own actions (see
    acceptance.py), since this is ordinary client-file-setup work rather
    than a firm-wide governed library like Document Templates/Policies."""
    from functools import wraps

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not user_has_permission(current_user, "manage_company_documents"):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def _documents_dir():
    directory = Config.COMPANY_DOCUMENTS_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory


def _allowed_company_doc_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_COMPANY_DOC_EXTENSIONS


@company_documents_bp.route("/<int:client_id>/company-documents/upload", methods=["POST"])
@login_required
@editor_required
def upload_document(client_id):
    client = Client.query.get_or_404(client_id)
    document_type = request.form.get("document_type", "").strip()
    title = request.form.get("title", "").strip()
    if document_type not in COMPANY_DOCUMENT_TYPES:
        flash("Please choose a document type.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if not _allowed_company_doc_file(file.filename):
        flash("Only PDF, JPG and PNG files are accepted for company documents.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))

    original_name = secure_filename(file.filename)
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    is_image = ext in IMAGE_EXTENSIONS

    doc = CompanyDocument(
        client_id=client_id,
        document_type=document_type,
        title=title or document_type,
        original_filename="pending",
        stored_filename="pending",
        notes=request.form.get("notes", "").strip(),
        uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.flush()  # assign doc.id without committing yet

    stored_name = f"doc{doc.id}_{uuid.uuid4().hex[:8]}_{original_name}"
    filepath = os.path.join(_documents_dir(), stored_name)
    file.save(filepath)
    doc.original_filename = original_name
    doc.stored_filename = stored_name

    if is_image:
        doc.extraction_status = None  # not applicable - no PDF text layer to try
    else:
        text, extraction_status, page_count = sanctions_data.extract_pdf_text(filepath)
        doc.extracted_text = text
        doc.extraction_status = extraction_status
        doc.page_count = page_count

    db.session.commit()  # save the upload itself before attempting the AI call, so a slow/failed AI step never loses the file

    people, ai_status, ai_error = entity_extraction.extract_people_from_document(
        filepath, is_image, doc.extracted_text, doc.extraction_status,
    )
    doc.ai_status = ai_status
    doc.ai_error = ai_error
    doc.ai_processed_at = datetime.utcnow()
    for p in people:
        db.session.add(ClientKeyPerson(
            client_id=client_id,
            full_name=p["full_name"],
            role=p["role"],
            details=p["details"] or None,
            status="Suggested",
            source_document_id=doc.id,
        ))
    db.session.commit()

    if ai_status == "done":
        if people:
            flash(f"'{doc.title}' uploaded - {len(people)} person(s) suggested below for review under Directors & Shareholders.", "success")
        else:
            flash(f"'{doc.title}' uploaded - no directors/shareholders/etc were found in it.", "info")
    elif ai_status == "not_configured":
        flash(f"'{doc.title}' uploaded, but automatic extraction isn't set up yet ({ai_error}) - add people manually below, or reprocess this document once it's configured.", "warning")
    else:
        flash(f"'{doc.title}' uploaded, but automatic extraction failed ({ai_error}) - add people manually below, or use \"Reprocess\" to try again.", "warning")
    return redirect(url_for("clients.view_client", client_id=client_id))


@company_documents_bp.route("/company-documents/<int:doc_id>/reprocess", methods=["POST"])
@login_required
@editor_required
def reprocess_document(doc_id):
    """Re-run AI extraction against an already-uploaded document, without
    re-uploading the file - for when the API key wasn't configured yet at
    upload time, or the previous attempt failed transiently."""
    doc = CompanyDocument.query.get_or_404(doc_id)
    filepath = os.path.join(_documents_dir(), doc.stored_filename or "")
    if not os.path.exists(filepath):
        flash("The original file can no longer be found on disk - re-upload the document.", "danger")
        return redirect(url_for("clients.view_client", client_id=doc.client_id))

    is_image = doc.file_ext in IMAGE_EXTENSIONS
    people, ai_status, ai_error = entity_extraction.extract_people_from_document(
        filepath, is_image, doc.extracted_text, doc.extraction_status,
    )
    doc.ai_status = ai_status
    doc.ai_error = ai_error
    doc.ai_processed_at = datetime.utcnow()
    for p in people:
        db.session.add(ClientKeyPerson(
            client_id=doc.client_id,
            full_name=p["full_name"],
            role=p["role"],
            details=p["details"] or None,
            status="Suggested",
            source_document_id=doc.id,
        ))
    db.session.commit()
    if ai_status == "done":
        flash(f"Reprocessed - {len(people)} person(s) newly suggested (existing entries below are untouched, so check for duplicates).", "success")
    else:
        flash(f"Reprocessing failed: {ai_error}", "danger")
    return redirect(url_for("clients.view_client", client_id=doc.client_id))


@company_documents_bp.route("/company-documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    from flask import send_from_directory
    doc = CompanyDocument.query.get_or_404(doc_id)
    directory = _documents_dir()
    if not os.path.exists(os.path.join(directory, doc.stored_filename or "")):
        abort(404)
    return send_from_directory(directory, doc.stored_filename, as_attachment=True)


@company_documents_bp.route("/company-documents/<int:doc_id>/delete", methods=["POST"])
@login_required
@editor_required
def delete_document(doc_id):
    doc = CompanyDocument.query.get_or_404(doc_id)
    client_id = doc.client_id
    path = os.path.join(_documents_dir(), doc.stored_filename or "")
    if os.path.exists(path):
        os.remove(path)
    # Detach (not delete) any people suggested from this document - they
    # still describe the client even once the source file is gone,
    # especially any that have since been reviewed and confirmed.
    ClientKeyPerson.query.filter_by(source_document_id=doc.id).update({"source_document_id": None})
    title = doc.title
    db.session.delete(doc)
    db.session.commit()
    flash(f"'{title}' deleted.", "info")
    return redirect(url_for("clients.view_client", client_id=client_id))


# ---------- Directors & Shareholders (ClientKeyPerson) ----------

@company_documents_bp.route("/<int:client_id>/key-people/add", methods=["POST"])
@login_required
@editor_required
def add_key_person(client_id):
    Client.query.get_or_404(client_id)
    full_name = request.form.get("full_name", "").strip()
    if not full_name:
        flash("Enter a name before adding a director/shareholder.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))
    role = request.form.get("role", "Other").strip()
    db.session.add(ClientKeyPerson(
        client_id=client_id,
        full_name=full_name,
        role=role if role in PERSON_ROLES else "Other",
        details=request.form.get("details", "").strip() or None,
        status="Confirmed",  # a person typing this in directly is already vouching for it
        created_by_id=current_user.id,
        confirmed_by_id=current_user.id,
        confirmed_at=datetime.utcnow(),
    ))
    db.session.commit()
    flash(f"{full_name} added.", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@company_documents_bp.route("/key-people/<int:person_id>/update", methods=["POST"])
@login_required
@editor_required
def update_key_person(person_id):
    person = ClientKeyPerson.query.get_or_404(person_id)
    full_name = request.form.get("full_name", "").strip()
    if not full_name:
        flash("Name can't be blank.", "danger")
        return redirect(url_for("clients.view_client", client_id=person.client_id))
    person.full_name = full_name
    role = request.form.get("role", person.role).strip()
    person.role = role if role in PERSON_ROLES else person.role
    person.details = request.form.get("details", "").strip() or None
    db.session.commit()
    return redirect(url_for("clients.view_client", client_id=person.client_id))


@company_documents_bp.route("/key-people/<int:person_id>/confirm", methods=["POST"])
@login_required
@editor_required
def confirm_key_person(person_id):
    """Review action for an AI-suggested row: mark it Confirmed once a
    person has checked it against the source document (edit first via
    update_key_person above if anything needs correcting)."""
    person = ClientKeyPerson.query.get_or_404(person_id)
    person.status = "Confirmed"
    person.confirmed_by_id = current_user.id
    person.confirmed_at = datetime.utcnow()
    db.session.commit()
    flash(f"{person.full_name} confirmed.", "success")
    return redirect(url_for("clients.view_client", client_id=person.client_id))


@company_documents_bp.route("/key-people/<int:person_id>/delete", methods=["POST"])
@login_required
@editor_required
def delete_key_person(person_id):
    person = ClientKeyPerson.query.get_or_404(person_id)
    client_id = person.client_id
    db.session.delete(person)
    db.session.commit()
    return redirect(url_for("clients.view_client", client_id=client_id))
