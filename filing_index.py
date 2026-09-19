"""Filing Index & Manual - the firm's Audit Working Paper Indexing & Filing
Policy, made available in-app: the written policy itself (models.
FILING_INDEX_MANUAL) plus the full, editable list of numbered working-paper
references it defines (models.FilingIndexSection, seeded once from
seed.seed_filing_index). Firm-wide, like the Tickmarks page - not tied to
any one engagement or client.

Per the policy's own "numbers are never reused" rule, there's deliberately
no delete route here: a code that's no longer needed is retired
(is_active=False, "not used") rather than removed, so its number can never
accidentally be reassigned to something else later.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import FilingIndexSection, FILING_INDEX_MANUAL

filing_index_bp = Blueprint("filing_index", __name__, url_prefix="/filing-index")


@filing_index_bp.route("/")
@login_required
def list_filing_index():
    sections = FilingIndexSection.query.order_by(FilingIndexSection.is_permanent, FilingIndexSection.order, FilingIndexSection.code).all()
    current_file = [s for s in sections if not s.is_permanent]
    permanent_file = [s for s in sections if s.is_permanent]
    return render_template(
        "filing_index/list.html",
        manual=FILING_INDEX_MANUAL, current_file=current_file, permanent_file=permanent_file,
    )


@filing_index_bp.route("/add", methods=["POST"])
@login_required
def add_section():
    code = request.form.get("code", "").strip().upper()
    category = request.form.get("category", "").strip()
    section = request.form.get("section", "").strip()
    typical_contents = request.form.get("typical_contents", "").strip()
    is_permanent = request.form.get("is_permanent") == "1"

    if not code or not section:
        flash("Please enter both a code and a section name.", "danger")
        return redirect(url_for("filing_index.list_filing_index"))
    if FilingIndexSection.query.filter_by(code=code).first():
        flash(f'A filing index entry with code "{code}" already exists.', "danger")
        return redirect(url_for("filing_index.list_filing_index"))

    max_order = db.session.query(db.func.max(FilingIndexSection.order)).scalar() or 0
    db.session.add(FilingIndexSection(
        code=code, category=category, section=section, typical_contents=typical_contents,
        is_permanent=is_permanent, order=max_order + 1,
        created_by_id=current_user.id, updated_by_id=current_user.id,
    ))
    db.session.commit()
    flash(f"Filing index entry {code} added.", "success")
    return redirect(url_for("filing_index.list_filing_index"))


@filing_index_bp.route("/<int:section_id>/update", methods=["POST"])
@login_required
def update_section(section_id):
    entry = FilingIndexSection.query.get_or_404(section_id)
    category = request.form.get("category", "").strip()
    section = request.form.get("section", "").strip()
    typical_contents = request.form.get("typical_contents", "").strip()
    if not section:
        flash("Please enter a section name.", "danger")
        return redirect(url_for("filing_index.list_filing_index"))
    entry.category = category
    entry.section = section
    entry.typical_contents = typical_contents
    entry.updated_by_id = current_user.id
    db.session.commit()
    flash(f"Filing index entry {entry.code} updated.", "success")
    return redirect(url_for("filing_index.list_filing_index"))


@filing_index_bp.route("/<int:section_id>/toggle-active", methods=["POST"])
@login_required
def toggle_active(section_id):
    entry = FilingIndexSection.query.get_or_404(section_id)
    entry.is_active = not entry.is_active
    entry.updated_by_id = current_user.id
    db.session.commit()
    flash(f"{entry.code} marked as {'active' if entry.is_active else 'not used'}.", "success")
    return redirect(url_for("filing_index.list_filing_index"))
