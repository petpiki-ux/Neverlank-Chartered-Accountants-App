"""Library of downloadable, fillable Word/Excel workpaper templates
(engagement letters, planning memos, a CGT computation sheet, etc), grouped
by Audit / Assurance / Consulting. These are separate from the per-engagement
"Documents" tab: that tab holds working papers actually produced on an
engagement, while this library holds the blank starting templates staff
download, fill in, and then upload back onto an engagement.

Template metadata (and, for admins/partners, the underlying file itself) is
editable from the app via the New/Edit/Delete routes below - the master
copies live in Config.DOCUMENT_TEMPLATES_DATA_DIR, a writable folder next to
the database, not the read-only bundle the app ships with.
"""
import os
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash,
    send_from_directory, abort, current_app,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import DocumentTemplate, ENGAGEMENT_TYPES
from config import Config

doc_templates_bp = Blueprint("doc_templates", __name__, url_prefix="/document-templates")

# Kept in sync with the engagement types in models.py so a new engagement
# type (e.g. Secretarial) automatically becomes choosable here too.
TEMPLATE_TYPES = ENGAGEMENT_TYPES
ALLOWED_TEMPLATE_EXTENSIONS = {"docx", "xlsx"}


def editor_required(f):
    """Editing the master template library is restricted to admin/partner
    roles so staff can't accidentally overwrite or delete a firm-wide
    template - everyone can still view and download."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if current_user.role not in ("admin", "partner"):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def _allowed_template_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_TEMPLATE_EXTENSIONS


def _template_dir(eng_type):
    directory = os.path.join(Config.DOCUMENT_TEMPLATES_DATA_DIR, eng_type)
    os.makedirs(directory, exist_ok=True)
    return directory


@doc_templates_bp.route("/")
@login_required
def list_templates():
    library = {}
    for eng_type in TEMPLATE_TYPES:
        items = DocumentTemplate.query.filter_by(type=eng_type).order_by(
            DocumentTemplate.order, DocumentTemplate.ref_code
        ).all()
        if items:
            library[eng_type] = items
    can_edit = current_user.role in ("admin", "partner")
    return render_template("doc_templates/list.html", library=library, can_edit=can_edit)


@doc_templates_bp.route("/download/<int:template_id>")
@login_required
def download_template(template_id):
    tpl = DocumentTemplate.query.get_or_404(template_id)
    directory = _template_dir(tpl.type)
    if not os.path.exists(os.path.join(directory, tpl.filename)):
        abort(404)
    return send_from_directory(directory, tpl.filename, as_attachment=True)


@doc_templates_bp.route("/new", methods=["GET", "POST"])
@login_required
@editor_required
def new_template():
    if request.method == "POST":
        eng_type = request.form.get("type", "Audit")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        ref_code = request.form.get("ref_code", "").strip()
        file = request.files.get("file")

        if eng_type not in TEMPLATE_TYPES:
            flash("Please choose a valid template type.", "danger")
            return render_template("doc_templates/form.html", template=None, types=TEMPLATE_TYPES)
        if not title:
            flash("Please give the template a name.", "danger")
            return render_template("doc_templates/form.html", template=None, types=TEMPLATE_TYPES)
        if not file or file.filename == "":
            flash("Please choose a Word (.docx) or Excel (.xlsx) file to upload.", "danger")
            return render_template("doc_templates/form.html", template=None, types=TEMPLATE_TYPES)
        if not _allowed_template_file(file.filename):
            flash("Only .docx and .xlsx files are allowed for templates.", "danger")
            return render_template("doc_templates/form.html", template=None, types=TEMPLATE_TYPES)

        tpl = DocumentTemplate(
            type=eng_type,
            ref_code=ref_code,
            title=title,
            description=description,
            filename="pending",  # placeholder until we know the row's id
            updated_by_id=current_user.id,
        )
        db.session.add(tpl)
        db.session.flush()  # assign tpl.id without committing yet

        original_name = secure_filename(file.filename)
        stored_name = f"tpl{tpl.id}_{original_name}"
        file.save(os.path.join(_template_dir(eng_type), stored_name))
        tpl.filename = stored_name

        db.session.commit()
        flash(f"Template '{tpl.title}' added.", "success")
        return redirect(url_for("doc_templates.list_templates"))

    return render_template("doc_templates/form.html", template=None, types=TEMPLATE_TYPES)


@doc_templates_bp.route("/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
@editor_required
def edit_template(template_id):
    tpl = DocumentTemplate.query.get_or_404(template_id)
    if request.method == "POST":
        eng_type = request.form.get("type", tpl.type)
        title = request.form.get("title", "").strip()
        if eng_type not in TEMPLATE_TYPES:
            flash("Please choose a valid template type.", "danger")
            return render_template("doc_templates/form.html", template=tpl, types=TEMPLATE_TYPES)
        if not title:
            flash("Please give the template a name.", "danger")
            return render_template("doc_templates/form.html", template=tpl, types=TEMPLATE_TYPES)

        file = request.files.get("file")
        if file and file.filename != "":
            if not _allowed_template_file(file.filename):
                flash("Only .docx and .xlsx files are allowed for templates.", "danger")
                return render_template("doc_templates/form.html", template=tpl, types=TEMPLATE_TYPES)

            # Remove the old file (it may be in a different type-folder if
            # the type is also being changed) before saving the new one.
            old_path = os.path.join(_template_dir(tpl.type), tpl.filename)
            if os.path.exists(old_path):
                os.remove(old_path)

            original_name = secure_filename(file.filename)
            stored_name = f"tpl{tpl.id}_{original_name}"
            file.save(os.path.join(_template_dir(eng_type), stored_name))
            tpl.filename = stored_name
        elif eng_type != tpl.type:
            # Type changed but no new file uploaded: move the existing file
            # across to the new type-folder so it stays on disk correctly.
            old_path = os.path.join(_template_dir(tpl.type), tpl.filename)
            new_path = os.path.join(_template_dir(eng_type), tpl.filename)
            if os.path.exists(old_path):
                os.replace(old_path, new_path)

        tpl.type = eng_type
        tpl.title = title
        tpl.description = request.form.get("description", "").strip()
        tpl.ref_code = request.form.get("ref_code", "").strip()
        tpl.updated_by_id = current_user.id
        db.session.commit()
        flash(f"Template '{tpl.title}' updated.", "success")
        return redirect(url_for("doc_templates.list_templates"))

    return render_template("doc_templates/form.html", template=tpl, types=TEMPLATE_TYPES)


@doc_templates_bp.route("/<int:template_id>/delete", methods=["POST"])
@login_required
@editor_required
def delete_template(template_id):
    tpl = DocumentTemplate.query.get_or_404(template_id)
    file_path = os.path.join(_template_dir(tpl.type), tpl.filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    title = tpl.title
    db.session.delete(tpl)
    db.session.commit()
    flash(f"Template '{title}' deleted.", "info")
    return redirect(url_for("doc_templates.list_templates"))
