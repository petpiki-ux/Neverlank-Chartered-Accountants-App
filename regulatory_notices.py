"""Regulatory Notices: a firm-wide library of RBZ (Reserve Bank of Zimbabwe)
and FIU Zimbabwe adverse-notice PDFs, uploaded by the firm itself.

Neither regulator publishes a searchable/machine-readable feed the app could
poll automatically the way it does for UN/OFAC/EU (see sanctions_data.py) -
RBZ only posts occasional PDF "Public Notices" and FIU Zimbabwe has no
searchable list at all - so this is the practical substitute: whenever the
firm receives or finds a notice, it gets uploaded here once, text is
extracted from the PDF automatically (see sanctions_data.extract_pdf_text),
and every "Auto-screen" run on Client Acceptance's Sanctions & Adverse
Notice Screening card searches that text for a name match (see
sanctions_data.search_notices_for_name and acceptance.auto_screen_individual).

This is a firm-wide library, not scoped to one engagement - the same
uploaded notice is checked against every engagement's key individuals -
which is why it's its own top-level blueprint/nav item rather than living
under Client Acceptance, mirroring doc_templates.py's Document Templates
library and hr.py's Policies and Procedures library.
"""
import os
import uuid
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash,
    send_from_directory, abort,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import RegulatoryNotice, REGULATORY_NOTICE_SOURCES, REGULATORY_NOTICE_SOURCE_LABELS, user_has_permission
from config import Config
import sanctions_data

regulatory_notices_bp = Blueprint("regulatory_notices", __name__, url_prefix="/regulatory-notices")


def editor_required(f):
    """Uploading/deleting notices is gated by the configurable "Manage
    Regulatory Notices" permission (Team > Permissions) - defaults to
    partner/admin only, since these feed into a compliance control. Everyone
    can still view and download regardless."""
    from functools import wraps

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not user_has_permission(current_user, "manage_regulatory_notices"):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def _notices_dir():
    directory = Config.REGULATORY_NOTICES_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory


@regulatory_notices_bp.route("/")
@login_required
def list_notices():
    library = {}
    for source in REGULATORY_NOTICE_SOURCES:
        items = RegulatoryNotice.query.filter_by(source=source).order_by(
            RegulatoryNotice.notice_date.desc(), RegulatoryNotice.uploaded_at.desc()
        ).all()
        if items:
            library[source] = items
    can_edit = user_has_permission(current_user, "manage_regulatory_notices")
    return render_template(
        "regulatory_notices/list.html", library=library, can_edit=can_edit,
        sources=REGULATORY_NOTICE_SOURCES, source_labels=REGULATORY_NOTICE_SOURCE_LABELS,
    )


@regulatory_notices_bp.route("/upload", methods=["POST"])
@login_required
@editor_required
def upload_notice():
    source = request.form.get("source", "").strip()
    title = request.form.get("title", "").strip()
    if source not in REGULATORY_NOTICE_SOURCES:
        flash("Please choose RBZ or FIU.", "danger")
        return redirect(url_for("regulatory_notices.list_notices"))
    if not title:
        flash("Please give the notice a title.", "danger")
        return redirect(url_for("regulatory_notices.list_notices"))
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a PDF file to upload.", "danger")
        return redirect(url_for("regulatory_notices.list_notices"))
    if not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are accepted for regulatory notices.", "danger")
        return redirect(url_for("regulatory_notices.list_notices"))

    notice_date = None
    raw_date = request.form.get("notice_date", "").strip()
    if raw_date:
        try:
            notice_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            notice_date = None

    notice = RegulatoryNotice(
        source=source,
        title=title,
        notice_date=notice_date,
        original_filename="pending",
        stored_filename="pending",
        notes=request.form.get("notes", "").strip(),
        uploaded_by_id=current_user.id,
    )
    db.session.add(notice)
    db.session.flush()  # assign notice.id without committing yet

    original_name = secure_filename(file.filename)
    stored_name = f"notice{notice.id}_{uuid.uuid4().hex[:8]}_{original_name}"
    filepath = os.path.join(_notices_dir(), stored_name)
    file.save(filepath)
    notice.original_filename = original_name
    notice.stored_filename = stored_name

    text, status, page_count = sanctions_data.extract_pdf_text(filepath)
    notice.extracted_text = text
    notice.extraction_status = status
    notice.page_count = page_count
    db.session.commit()

    if status == "extracted":
        flash(f"'{notice.title}' uploaded - text extracted from {page_count} page(s); it will be checked automatically during screening.", "success")
    elif status == "no_text_found":
        flash(f"'{notice.title}' uploaded, but no selectable text was found (likely a scanned image) - it's on file but won't be matched automatically. Consider adding a note with the key names it covers.", "warning")
    else:
        flash(f"'{notice.title}' uploaded, but the PDF's text couldn't be read - it's on file but won't be matched automatically.", "warning")
    return redirect(url_for("regulatory_notices.list_notices"))


@regulatory_notices_bp.route("/download/<int:notice_id>")
@login_required
def download_notice(notice_id):
    notice = RegulatoryNotice.query.get_or_404(notice_id)
    directory = _notices_dir()
    if not os.path.exists(os.path.join(directory, notice.stored_filename or "")):
        abort(404)
    return send_from_directory(directory, notice.stored_filename, as_attachment=True)


@regulatory_notices_bp.route("/<int:notice_id>/delete", methods=["POST"])
@login_required
@editor_required
def delete_notice(notice_id):
    notice = RegulatoryNotice.query.get_or_404(notice_id)
    path = os.path.join(_notices_dir(), notice.stored_filename or "")
    if os.path.exists(path):
        os.remove(path)
    title = notice.title
    db.session.delete(notice)
    db.session.commit()
    flash(f"'{title}' deleted.", "info")
    return redirect(url_for("regulatory_notices.list_notices"))
