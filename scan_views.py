"""Search inside documents (including scans) and the "Read this scan" buttons. The reading itself is in ocr_reader.py / scan_text.py."""
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import user_has_permission, user_can_access_client, user_can_access_engagement, Engagement, ScanText
import ocr_reader
import scan_text as st_

scan_bp = Blueprint("scan", __name__, url_prefix="/documents")

LIBRARY_PERMISSION = {
    "regulatory_notice": "manage_regulatory_notices",
    "legislative_update": "manage_legislative_updates",
    "policy_document": "manage_policies",
}


def _senior():
    return current_user.role in ("admin", "partner")


def _safe_next(default):
    nxt = request.form.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


def _can_read(kind, row):
    """Whether the signed-in user may trigger a (re)read of this file."""
    if kind in LIBRARY_PERMISSION:
        return user_has_permission(current_user, LIBRARY_PERMISSION[kind])
    title, client_id, engagement_id = st_._describe(kind, row)
    if client_id and not user_can_access_client(current_user, client_id):
        return False
    if engagement_id:
        eng = db.session.get(Engagement, engagement_id)
        if eng is not None and not user_can_access_engagement(current_user, eng):
            return False
    return True


@scan_bp.route("/search")
@login_required
def search_documents():
    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind") or None
    if kind and kind not in st_.CLIENT_KINDS:
        kind = None
    results = st_.search(current_user, q, kind) if q else []
    indexed = ScanText.query.filter_by(status="extracted").count()
    reading = ScanText.query.filter_by(status="reading").count()
    unreadable = ScanText.query.filter(ScanText.status.in_(("not_configured", "error", "no_text_found"))).count()
    pending = len(st_.unread()) + len(st_.unread_library())
    labels = {k: st_.kind_info(k)["label"] for k in st_.CLIENT_KINDS}
    return render_template("scan/search.html", q=q, kind=kind, results=results, indexed=indexed, reading=reading, unreadable=unreadable,
                           pending=pending, engine=ocr_reader.status_summary(), kind_labels=labels, can_catch_up=_senior())


@scan_bp.route("/read/<kind>/<int:item_id>", methods=["POST"])
@login_required
def read_one(kind, item_id):
    info = st_.kind_info(kind)
    if info is None:
        abort(404)
    row = db.session.get(info["model"], item_id)
    if row is None:
        abort(404)
    if not _can_read(kind, row):
        abort(403)
    back = _safe_next(url_for("scan.search_documents"))
    if ocr_reader.engine_to_use() is None and not (st_._ext(st_._original_name(kind, row)) in ("docx", "xlsx", "xlsm", "pptx")):
        flash("No scan reader is set up on this server (it needs ANTHROPIC_API_KEY on Render), so this file could not be read.", "warning")
        return redirect(back)
    st_.start(kind, row)
    flash("Reading the file now. A long scan can take a minute: refresh this page to see the result.", "info")
    return redirect(back)


@scan_bp.route("/read-all", methods=["POST"])
@login_required
def read_all():
    if not _senior():
        abort(403)
    if ocr_reader.engine_to_use() is None:
        flash("No scan reader is set up on this server (it needs ANTHROPIC_API_KEY on Render).", "warning")
        return redirect(url_for("scan.search_documents"))
    pairs = st_.unread(limit=100)
    lib = st_.unread_library()
    for kind, item_id in pairs + lib:
        row = db.session.get(st_.kind_info(kind)["model"], item_id)
        if row is not None:
            st_.mark_reading(kind, row)
    db.session.commit()
    st_.start_many(pairs + lib)
    flash(f"Reading {len(pairs) + len(lib)} earlier file(s) in the background. Refresh in a few minutes.", "success")
    return redirect(url_for("scan.search_documents"))
