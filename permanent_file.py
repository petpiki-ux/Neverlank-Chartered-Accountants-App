"""Permanent File (P-series) documents - see models.PermanentFileDocument
for why this is separate from an engagement's own Documents tab: the firm's
Audit Working Paper Indexing & Filing Policy (models.FILING_INDEX_MANUAL)
describes the Permanent File as "information of continuing relevance across
audit years" (incorporation documents, the standing engagement letter, the
accounting policy manual, prior-year financial statements, structure &
governance records), filed once per Client rather than re-filed on every
engagement's Current File.

Deliberately simple - unlike Company Documents (company_documents.py),
nothing here is text-extracted or AI-processed; it's just a file, a P-code
from the Filing Index, and a note, reusing the same upload folder and
allowed-extensions rules as an engagement's own Documents tab.

Two things layered on top of that: every document filed under a P-code gets
its own numbered sub-reference (P1001, P1002, ... - see
models.PermanentFileDocument's docstring and _next_reference_number below),
and file_upload_to_permanent_file() is called by company_documents.py to
auto-file a copy of certain Company Document uploads here, so a preparer
never has to upload the same incorporation document twice.
"""
import os
import shutil
import uuid

from flask import Blueprint, redirect, url_for, request, flash, current_app, send_from_directory, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import Client, PermanentFileDocument, PermanentFileReferenceCounter, FilingIndexSection, user_has_permission

permanent_file_bp = Blueprint("permanent_file", __name__, url_prefix="/clients")


def _allowed_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def _next_reference_number(client_id, filing_index_id):
    """The next free sub-reference number for this client under this P-code
    - see models.PermanentFileDocument's docstring. The first document filed
    under a code gets base+1 (e.g. 1001 under "P1000"), never the bare code
    itself, so every actual document consistently gets its own number.
    Backed by PermanentFileReferenceCounter so a number is never reused even
    if the document it was assigned to is later deleted - looking at
    MAX(reference_number) on currently-existing documents alone can't give
    that guarantee once rows can be deleted. Does NOT commit - the caller
    commits the counter bump together with whatever document row it's
    assigning the number to, so the two always move together."""
    filing_index = FilingIndexSection.query.get(filing_index_id)
    if not filing_index:
        return None
    try:
        base = int(filing_index.code[1:])
    except (ValueError, IndexError):
        base = 0
    counter = PermanentFileReferenceCounter.query.filter_by(client_id=client_id, filing_index_id=filing_index_id).first()
    if not counter:
        counter = PermanentFileReferenceCounter(client_id=client_id, filing_index_id=filing_index_id, last_number=base)
        db.session.add(counter)
    counter.last_number += 1
    # Keep the counter in step with any higher number already on a document
    # under this code (e.g. one a preparer typed in by hand above the
    # auto-assigned sequence, or data from before this counter existed) so
    # that number is never handed out again either.
    existing_max = db.session.query(db.func.max(PermanentFileDocument.reference_number)).filter_by(
        client_id=client_id, filing_index_id=filing_index_id,
    ).scalar()
    if existing_max and existing_max >= counter.last_number:
        counter.last_number = existing_max + 1
    db.session.flush()
    return counter.last_number


def file_upload_to_permanent_file(client_id, source_filepath, original_filename, filing_code, notes, uploaded_by_id, source_company_document_id=None):
    """Copies an already-uploaded file into the Permanent File under the
    given P-code (e.g. "P1000"), auto-assigning the next sub-reference
    number - used by company_documents.upload_document to auto-file certain
    Company Document uploads (see models.COMPANY_DOCUMENT_TYPE_TO_FILING_CODE)
    without a preparer having to upload the same file twice. A COPY is made
    (not a shared reference to the same stored file) so the Permanent File
    entry has its own independent lifecycle - deleting the Company Document
    later never breaks or removes this copy.

    Degrades silently (returns None, does nothing else) if filing_code isn't
    mapped to anything, or isn't seeded in this install's Filing Index yet -
    auto-filing is a convenience on top of Company Documents, never a reason
    to fail that upload."""
    if not filing_code:
        return None
    filing_index = FilingIndexSection.query.filter_by(code=filing_code).first()
    if not filing_index or not os.path.exists(source_filepath):
        return None

    stored_name = f"client{client_id}_perm_{uuid.uuid4().hex[:10]}_{original_filename}"
    dest_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name)
    shutil.copyfile(source_filepath, dest_path)

    existing = PermanentFileDocument.query.filter_by(client_id=client_id, original_filename=original_filename).count()
    doc = PermanentFileDocument(
        client_id=client_id, original_filename=original_filename, stored_filename=stored_name,
        filing_index_id=filing_index.id, reference_number=_next_reference_number(client_id, filing_index.id),
        notes=notes, version=existing + 1, uploaded_by_id=uploaded_by_id,
        source_company_document_id=source_company_document_id,
    )
    db.session.add(doc)
    db.session.commit()
    return doc


@permanent_file_bp.route("/<int:client_id>/permanent-file/upload", methods=["POST"])
@login_required
def upload_permanent_file_document(client_id):
    client = Client.query.get_or_404(client_id)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))

    filing_index_id = request.form.get("filing_index_id", "").strip()
    filing_index_id = int(filing_index_id) if filing_index_id.isdigit() else None
    notes = request.form.get("notes", "").strip()

    original_name = secure_filename(file.filename)
    existing = PermanentFileDocument.query.filter_by(client_id=client_id, original_filename=original_name).count()
    version = existing + 1
    stored_name = f"client{client_id}_perm_{uuid.uuid4().hex[:10]}_{original_name}"
    file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name))

    reference_number = _next_reference_number(client_id, filing_index_id) if filing_index_id else None
    doc = PermanentFileDocument(
        client_id=client_id, original_filename=original_name, stored_filename=stored_name,
        filing_index_id=filing_index_id, reference_number=reference_number,
        notes=notes, version=version, uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.commit()
    flash(f"Filed '{original_name}' (v{version}) to the Permanent File{f' as {doc.reference_code}' if doc.reference_code else ''}.", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@permanent_file_bp.route("/permanent-file/<int:doc_id>/update", methods=["POST"])
@login_required
def update_permanent_file_document(doc_id):
    """Lets a preparer correct the auto-assigned filing - re-file a document
    under a different P-code (re-numbering it under that code), or fix its
    sub-reference/notes directly. Always available, same "stays directly
    editable" philosophy as everywhere else in this app that auto-assigns a
    figure or a reference."""
    doc = PermanentFileDocument.query.get_or_404(doc_id)
    client_id = doc.client_id
    filing_index_id = request.form.get("filing_index_id", "").strip()
    filing_index_id = int(filing_index_id) if filing_index_id.isdigit() else None
    reference_number = request.form.get("reference_number", "").strip()

    if filing_index_id != doc.filing_index_id:
        # Moved to a different P-code (or cleared entirely) - re-number
        # under the new code rather than keep a number that belonged to the
        # old one.
        doc.filing_index_id = filing_index_id
        doc.reference_number = _next_reference_number(client_id, filing_index_id) if filing_index_id else None
    elif filing_index_id and reference_number.isdigit():
        doc.reference_number = int(reference_number)

    doc.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash(f"Updated{f' - now {doc.reference_code}' if doc.reference_code else ''}.", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@permanent_file_bp.route("/permanent-file/<int:doc_id>/download")
@login_required
def download_permanent_file_document(doc_id):
    doc = PermanentFileDocument.query.get_or_404(doc_id)
    download_name = doc.original_filename
    if doc.reference_code:
        stem, _, ext = doc.original_filename.rpartition(".")
        description = stem or doc.original_filename
        download_name = secure_filename(f"{doc.reference_code}_{description}.{ext}") if ext else secure_filename(f"{doc.reference_code}_{description}")
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], doc.stored_filename, as_attachment=True,
        download_name=download_name or doc.original_filename,
    )


@permanent_file_bp.route("/permanent-file/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_permanent_file_document(doc_id):
    if not user_has_permission(current_user, "delete_documents"):
        abort(403)
    doc = PermanentFileDocument.query.get_or_404(doc_id)
    client_id = doc.client_id
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_filename))
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for("clients.view_client", client_id=client_id))
