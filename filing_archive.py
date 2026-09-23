"""Tax & Accounting Filing Archive - a client's Tax Clearance certificates,
filed Returns, and supporting Schedules (see models.
TaxAccountingFilingDocument / FILING_ARCHIVE_SECTIONS), filed once per
Client rather than tied to whichever Tax Compliance/Accounting &
Bookkeeping engagement happened to be open at the time - same reasoning as
the Permanent File (permanent_file.py), which this deliberately mirrors: a
simple file + tag + notes record, reusing the same upload folder and
allowed-extensions rules as everywhere else in the app.
"""
import os
import uuid

from flask import Blueprint, redirect, url_for, request, flash, current_app, send_from_directory, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import Client, TaxAccountingFilingDocument, FILING_ARCHIVE_SECTION_LABELS, user_has_permission

filing_archive_bp = Blueprint("filing_archive", __name__, url_prefix="/clients")


def _allowed_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


@filing_archive_bp.route("/<int:client_id>/filing-archive/upload", methods=["POST"])
@login_required
def upload_filing_archive_document(client_id):
    client = Client.query.get_or_404(client_id)
    section = request.form.get("section", "").strip()
    if section not in FILING_ARCHIVE_SECTION_LABELS:
        flash("Unrecognised filing archive section.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))
    if not _allowed_file(file.filename):
        flash("File type not allowed.", "danger")
        return redirect(url_for("clients.view_client", client_id=client_id))

    tax_head = request.form.get("tax_head", "").strip() or None
    title = request.form.get("title", "").strip() or None
    period_label = request.form.get("period_label", "").strip() or None
    reference = request.form.get("reference", "").strip() or None
    notes = request.form.get("notes", "").strip()

    original_name = secure_filename(file.filename)
    stored_name = f"client{client_id}_filingarchive_{uuid.uuid4().hex[:10]}_{original_name}"
    file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name))

    db.session.add(TaxAccountingFilingDocument(
        client_id=client_id, section=section, tax_head=tax_head, title=title,
        period_label=period_label, reference=reference,
        original_filename=original_name, stored_filename=stored_name,
        notes=notes, uploaded_by_id=current_user.id,
    ))
    db.session.commit()
    flash(f"Filed '{original_name}' to {FILING_ARCHIVE_SECTION_LABELS[section]}.", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@filing_archive_bp.route("/filing-archive/<int:doc_id>/download")
@login_required
def download_filing_archive_document(doc_id):
    doc = TaxAccountingFilingDocument.query.get_or_404(doc_id)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], doc.stored_filename, as_attachment=True,
        download_name=doc.original_filename,
    )


@filing_archive_bp.route("/filing-archive/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_filing_archive_document(doc_id):
    if not user_has_permission(current_user, "delete_documents"):
        abort(403)
    doc = TaxAccountingFilingDocument.query.get_or_404(doc_id)
    client_id = doc.client_id
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_filename))
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for("clients.view_client", client_id=client_id))
