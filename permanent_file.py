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
"""
import os
import uuid

from flask import Blueprint, redirect, url_for, request, flash, current_app, send_from_directory, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import Client, PermanentFileDocument, user_has_permission

permanent_file_bp = Blueprint("permanent_file", __name__, url_prefix="/clients")


def _allowed_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


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

    db.session.add(PermanentFileDocument(
        client_id=client_id, original_filename=original_name, stored_filename=stored_name,
        filing_index_id=filing_index_id, notes=notes, version=version, uploaded_by_id=current_user.id,
    ))
    db.session.commit()
    flash(f"Filed '{original_name}' (v{version}) to the Permanent File.", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@permanent_file_bp.route("/permanent-file/<int:doc_id>/download")
@login_required
def download_permanent_file_document(doc_id):
    doc = PermanentFileDocument.query.get_or_404(doc_id)
    download_name = doc.original_filename
    if doc.filing_index and doc.filing_index.code:
        stem, _, ext = doc.original_filename.rpartition(".")
        description = stem or doc.original_filename
        download_name = secure_filename(f"{doc.filing_index.code}_{description}.{ext}") if ext else secure_filename(f"{doc.filing_index.code}_{description}")
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
