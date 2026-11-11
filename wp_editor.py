"""Working Papers tab: generate a working paper from a template and work in it inside the app - no download, edit, upload loop.
Word papers open as a page you type into (wp_docx), Excel papers as an editable grid (wp_xlsx); both save into the same stored file as you
type. A paper that was uploaded (not generated here) is first copied to a new version, so what was uploaded is never changed."""
import os
import re
import shutil
import uuid
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from extensions import db
from models import Document, DocumentTemplate, Engagement, TrialBalance, template_folder
from config import Config
import wp_docx
import wp_xlsx

wpedit_bp = Blueprint("wpedit", __name__, url_prefix="/working-papers")

CATEGORY = "Working Papers"


def _engagements():
    import engagements as eng
    return eng


def _doc_or_404(doc_id):
    doc = Document.query.get_or_404(doc_id)
    _engagements()._ensure_engagement_access(doc.engagement)
    return doc


def _path(doc):
    return os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_filename)


def _ext(doc):
    return doc.original_filename.rsplit(".", 1)[-1].lower() if "." in doc.original_filename else ""


def editable(doc):
    return _ext(doc) in ("docx", "xlsx") and os.path.exists(_path(doc))


def _fin_facts(engagement):
    """Revenue, profit, total assets and ratios for the paper's figure cells - from the trial balance as it stands, or {} if there is none."""
    try:
        import financials as fin
        tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
        if not tb or not tb.lines:
            return {}
        st = fin.build_all_statements(tb.lines, tb.adjustments, reporting_framework=engagement.reporting_framework,
                                      cash_flow_method=engagement.cash_flow_method, whole_numbers=True)
        rows = {r["label"]: r for r in st["pl"]["rows"]}
        def cur(label, k="current"):
            r = rows.get(label)
            return r[k] if r else None
        out = {"revenue": cur("Revenue"), "revenue_prior": cur("Revenue", "prior"), "pbt": st["pl"]["profit_before_tax"]["current"],
               "total_assets": st["sfp"]["total_assets"]["current"]}
        for sfx, k in (("", "current"), ("_prior", "prior")):
            rev = cur("Revenue", k)
            gp = cur("Gross profit", k)
            profit = st["pl"]["profit_for_year"][k]
            ca, cl = st["sfp"]["ca_total"][k], st["sfp"]["cl_total"][k]
            out["gross_margin" + sfx] = (gp / rev * 100) if rev and gp is not None else None
            out["net_margin" + sfx] = (profit / rev * 100) if rev else None
            out["current_ratio" + sfx] = (ca / cl) if cl else None
        return out
    except Exception:
        db.session.rollback()
        return {}


def engagement_facts(engagement):
    client = engagement.client
    return {
        "client": client.name if client else "", "address": (client.address or "") if client else "", "tin": (client.tin_number or "") if client else "",
        "industry": (client.industry or "") if client else "", "period_end": wp_docx.format_date(engagement.period_end),
        "today": wp_docx.format_date(date.today()), "user": current_user.name, "file_ref": engagement.title,
        "tax_year": str(engagement.period_end.year) if engagement.period_end else "", "fin": _fin_facts(engagement),
    }


# ------------------------------------------------------------------ generate
@wpedit_bp.route("/engagements/<int:engagement_id>/generate/<int:template_id>", methods=["POST"])
@login_required
def generate(engagement_id, template_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    eng = _engagements()
    eng._ensure_engagement_access(engagement)
    tpl = DocumentTemplate.query.get_or_404(template_id)
    src = os.path.join(Config.DOCUMENT_TEMPLATES_DATA_DIR, template_folder(tpl.type), tpl.filename)
    if not os.path.exists(src):
        flash("That template's file is missing from the library.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="documents"))
    ext = tpl.filename.rsplit(".", 1)[-1].lower()
    client_name = engagement.client.name if engagement.client else "Client"
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "", f"{tpl.ref_code} {tpl.title} - {client_name}").strip()[:150] + f".{ext}"
    stored = f"eng{engagement.id}_wp_{uuid.uuid4().hex[:10]}_{secure_filename(name) or 'paper.' + ext}"
    dest = os.path.join(current_app.config["UPLOAD_FOLDER"], stored)
    shutil.copyfile(src, dest)
    facts = engagement_facts(engagement)
    filled = 0
    try:
        if ext == "docx":
            from docx import Document as DocxDocument
            d = DocxDocument(dest)
            filled = wp_docx.prefill(d, facts)
            wp_docx.save_atomic(d, dest)
        else:
            filled = wp_xlsx.prefill(dest, facts)
    except Exception:
        current_app.logger.exception("working paper prefill failed")
    version = Document.query.filter_by(engagement_id=engagement.id, original_filename=name, category=CATEGORY).count() + 1
    doc = Document(engagement_id=engagement.id, original_filename=name, stored_filename=stored, category=CATEGORY, reference=tpl.ref_code,
                   version=version, uploaded_by_id=current_user.id, uploaded_at=datetime.utcnow(), is_generated=True,
                   workpaper_kind=f"template_{tpl.id}", is_current_version=True, in_app=True, notes=f"Generated from template {tpl.ref_code}")
    db.session.add(doc)
    db.session.commit()
    flash(f"{tpl.ref_code} generated for {client_name}" + (f" - {filled} field(s) filled in from the engagement." if filled else "."), "success")
    return redirect(url_for("wpedit.edit", doc_id=doc.id))


# ------------------------------------------------------------------ open / edit
@wpedit_bp.route("/<int:doc_id>/open")
@login_required
def open_doc(doc_id):
    """'Open in app': a paper made here opens as it is; an uploaded file is first copied to a new version (the upload stays untouched)."""
    doc = _doc_or_404(doc_id)
    if not editable(doc):
        flash("Only Word (.docx) and Excel (.xlsx) files can be opened in the app.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab="documents"))
    if not doc.in_app:
        stored = f"eng{doc.engagement_id}_wp_{uuid.uuid4().hex[:10]}_{secure_filename(doc.original_filename)}"
        shutil.copyfile(_path(doc), os.path.join(current_app.config["UPLOAD_FOLDER"], stored))
        version = Document.query.filter_by(engagement_id=doc.engagement_id, original_filename=doc.original_filename, category=doc.category).count() + 1
        new = Document(engagement_id=doc.engagement_id, original_filename=doc.original_filename, stored_filename=stored, category=doc.category,
                       reference=doc.reference, version=version, uploaded_by_id=current_user.id, uploaded_at=datetime.utcnow(),
                       filing_index_id=doc.filing_index_id, substantive_area_id=doc.substantive_area_id, in_app=True,
                       notes=f"Edited in the app from v{doc.version}")
        db.session.add(new)
        db.session.commit()
        flash(f"Opened as a new version (v{new.version}); v{doc.version} is kept unchanged.", "info")
        doc = new
    return redirect(url_for("wpedit.edit", doc_id=doc.id))


@wpedit_bp.route("/<int:doc_id>/edit")
@login_required
def edit(doc_id):
    doc = _doc_or_404(doc_id)
    if not editable(doc):
        abort(404)
    if not doc.in_app:
        return redirect(url_for("wpedit.open_doc", doc_id=doc.id))
    locked = doc.is_partner_signed
    common = dict(doc=doc, engagement=doc.engagement, locked=locked,
                  back=url_for("engagements.view_engagement", engagement_id=doc.engagement_id, tab="documents"))
    if _ext(doc) == "docx":
        from docx import Document as DocxDocument
        body, width = wp_docx.render_page(DocxDocument(_path(doc)))
        return render_template("wp_editor/docx.html", body=body, page_width=width, **common)
    model = wp_xlsx.sheet_model(_path(doc))
    return render_template("wp_editor/xlsx.html", model=model, **common)


def _check_writable(doc):
    if not doc.in_app or not editable(doc):
        return jsonify(ok=False, error="This file is not open for editing."), 400
    if doc.is_partner_signed:
        return jsonify(ok=False, error="This working paper is signed off by the partner - remove the partner sign-off before changing it."), 409
    return None


@wpedit_bp.route("/<int:doc_id>/save", methods=["POST"])
@login_required
def save(doc_id):
    doc = _doc_or_404(doc_id)
    bad = _check_writable(doc)
    if bad:
        return bad
    data = request.get_json(silent=True) or {}
    try:
        if _ext(doc) == "docx":
            from docx import Document as DocxDocument
            d = DocxDocument(_path(doc))
            n = wp_docx.apply_changes(d, data.get("changes"))
            if n:
                wp_docx.save_atomic(d, _path(doc))
        else:
            n = wp_xlsx.apply_changes(_path(doc), data.get("changes"))
    except Exception:
        current_app.logger.exception("working paper save failed")
        return jsonify(ok=False, error="Could not save that change."), 500
    if n:
        doc.uploaded_at = datetime.utcnow()
        # a paper that was reviewed / signed needs reviewing again once it has changed
        doc.reviewed_by_id = None
        doc.reviewed_at = None
        db.session.commit()
    return jsonify(ok=True, changed=n, saved_at=datetime.now().strftime("%H:%M:%S"))


@wpedit_bp.route("/<int:doc_id>/op", methods=["POST"])
@login_required
def op(doc_id):
    doc = _doc_or_404(doc_id)
    bad = _check_writable(doc)
    if bad:
        return bad
    if _ext(doc) != "docx":
        return jsonify(ok=False, error="Not available for Excel files."), 400
    data = request.get_json(silent=True) or {}
    from docx import Document as DocxDocument
    d = DocxDocument(_path(doc))
    ok, msg = wp_docx.structural(d, data.get("op"), data.get("path"))
    if ok:
        wp_docx.save_atomic(d, _path(doc))
        doc.uploaded_at = datetime.utcnow()
        doc.reviewed_by_id = None
        doc.reviewed_at = None
        db.session.commit()
    return jsonify(ok=ok, message=msg)
