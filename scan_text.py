"""Putting the scan reader (ocr_reader.py) to work on uploads.

Two kinds of file:

* LIBRARY files that already keep their text on their own row and are searched from there: RBZ/FIU notices (the sanctions screen),
  filed Acts/Notices/Judgments (the legislation library) and policies. For these a scan used to leave `extracted_text` empty; now the
  scan is read and the text stored on the row exactly as a text PDF's would be, with `text_source` recording that it came from a picture.
  Notices and legislation are re-chunked so "Ask the library" finds them.

* CLIENT files that never kept any text: engagement Documents, the Permanent File, the Tax & Accounting Filing Archive and ZIMRA
  correspondence. Their text (their own, or read from a scan) is stored in `ScanText`, which the "Search inside documents" page uses.

Reading a long scan can take a minute, so after an upload the reading runs in the background (`start`) and the row shows "Reading..."
until it finishes. Set OCR_SYNC=1 to run it inline (the test suite does). Nothing here can lose an upload: every step is wrapped, and a
failure is recorded on the row as a status, never raised.
"""
import os
import re
import threading
from datetime import datetime

from flask import current_app

from extensions import db
import ocr_reader


def _sync():
    return os.environ.get("OCR_SYNC") == "1"


def _ext(name):
    return name.rsplit(".", 1)[-1].lower() if name and "." in name else ""


# ------------------------------------------------------------------ what each kind of file is
def _kinds():
    from config import Config
    from models import (RegulatoryNotice, LegislativeUpdate, PolicyDocument, Document, PermanentFileDocument,
                        TaxAccountingFilingDocument, ZimraCorrespondence)
    upload = lambda: current_app.config["UPLOAD_FOLDER"]
    return {
        "regulatory_notice": dict(library=True, model=RegulatoryNotice, dir=lambda: Config.REGULATORY_NOTICES_DATA_DIR, file="stored_filename", label="RBZ/FIU notice"),
        "legislative_update": dict(library=True, model=LegislativeUpdate, dir=lambda: Config.LEGISLATIVE_UPDATES_DATA_DIR, file="stored_filename", label="Legislation library"),
        "policy_document": dict(library=True, model=PolicyDocument, dir=lambda: Config.POLICIES_DATA_DIR, file="filename", label="Policy"),
        "engagement_doc": dict(library=False, model=Document, dir=upload, file="stored_filename", label="Engagement document"),
        "permanent_file": dict(library=False, model=PermanentFileDocument, dir=upload, file="stored_filename", label="Permanent File"),
        "filing_archive": dict(library=False, model=TaxAccountingFilingDocument, dir=upload, file="stored_filename", label="Filing Archive"),
        "zimra_corr": dict(library=False, model=ZimraCorrespondence, dir=lambda: Config.ZIMRA_DATA_DIR, file="stored_filename", label="ZIMRA correspondence"),
    }


def kind_info(kind):
    return _kinds().get(kind)


def file_path(kind, row):
    info = kind_info(kind)
    name = getattr(row, info["file"], None)
    return os.path.join(info["dir"](), name) if name else None


def _original_name(kind, row):
    return getattr(row, "original_filename", None) or getattr(row, "filename", None) or ""


def _describe(kind, row):
    """(title, client_id, engagement_id) for a client file."""
    if kind == "engagement_doc":
        eng = row.engagement
        return row.original_filename, (eng.client_id if eng else None), row.engagement_id
    if kind == "permanent_file":
        ref = getattr(row, "reference_code", None)
        return (f"{ref} - " if ref else "") + (row.original_filename or ""), row.client_id, None
    if kind == "filing_archive":
        return (row.title or row.original_filename), row.client_id, None
    if kind == "zimra_corr":
        case = row.case
        return f"{row.subject}" + (f" ({row.original_filename})" if row.original_filename else ""), (case.client_id if case else None), (case.engagement_id if case else None)
    return _original_name(kind, row), None, None


# ------------------------------------------------------------------ reading one file
def _rechunk(kind, row):
    try:
        if kind == "legislative_update":
            import legislation_chunking
            row.chunks = []
            db.session.flush()
            legislation_chunking.ensure_chunks(row)
        elif kind == "policy_document":
            import policy_chunking
            row.chunks = []
            db.session.flush()
            policy_chunking.ensure_chunks(row)
    except Exception:
        pass


def read_library_row(kind, row):
    """Read the file behind a library row and store its text on the row. Commits. Returns the status."""
    path = file_path(kind, row)
    if not path or not os.path.exists(path):
        row.extraction_status = "error"
        db.session.commit()
        return "error"
    text, status, pages, method = ocr_reader.extract_text(path, _ext(_original_name(kind, row) or path))
    if status == "extracted":
        row.extracted_text = text
        row.page_count = pages if pages is not None else getattr(row, "page_count", None)
        row.text_source = f"scan:{method}" if method in ("tesseract", "claude") else None
        row.extraction_status = "extracted"
        _rechunk(kind, row)
    else:
        row.extraction_status = "no_text_found" if status == "not_configured" else status
        row.text_source = None
        if status == "not_configured":
            row.extraction_status = "no_text_found"
    db.session.commit()
    return status


def read_client_row(kind, row):
    """Read a client file and store its text in ScanText. Commits. Returns the ScanText row (None if the file type is not readable)."""
    from models import ScanText
    path = file_path(kind, row)
    title, client_id, engagement_id = _describe(kind, row)
    st = ScanText.query.filter_by(kind=kind, item_id=row.id).first()
    if st is None:
        st = ScanText(kind=kind, item_id=row.id)
        db.session.add(st)
    st.client_id, st.engagement_id, st.title = client_id, engagement_id, (title or "")[:300]
    ext = _ext(_original_name(kind, row) or (path or ""))
    if not path or not os.path.exists(path):
        st.status, st.text, st.error = "error", None, "The file is no longer on disk."
    elif ext not in ocr_reader.SCANNABLE_EXTENSIONS and ext not in ("docx", "xlsx", "xlsm", "pptx"):
        st.status, st.text, st.method, st.error = "unsupported_format", None, None, None
    else:
        text, status, pages, method = ocr_reader.extract_text(path, ext)
        st.status, st.pages, st.method = status, pages, method
        st.text = text if status == "extracted" else None
        st.error = None
        if status == "not_configured":
            st.error = "No scan reader is set up (add ANTHROPIC_API_KEY on Render)."
    st.read_at = datetime.utcnow()
    db.session.commit()
    return st


def run_job(kind, item_id):
    info = kind_info(kind)
    if info is None:
        return None
    row = db.session.get(info["model"], item_id)
    if row is None:
        return None
    try:
        return read_library_row(kind, row) if info["library"] else read_client_row(kind, row)
    except Exception as exc:
        db.session.rollback()
        try:
            if info["library"]:
                row = db.session.get(info["model"], item_id)
                row.extraction_status = "error"
            else:
                from models import ScanText
                st = ScanText.query.filter_by(kind=kind, item_id=item_id).first()
                if st is not None:
                    st.status, st.error = "error", str(exc)[:300]
            db.session.commit()
        except Exception:
            db.session.rollback()
        return "error"


def mark_reading(kind, row):
    """Show 'Reading...' on the row straight away. Does not commit by itself (the caller commits, or `start` does)."""
    info = kind_info(kind)
    if info["library"]:
        row.extraction_status = "reading"
    else:
        from models import ScanText
        title, client_id, engagement_id = _describe(kind, row)
        st = ScanText.query.filter_by(kind=kind, item_id=row.id).first()
        if st is None:
            st = ScanText(kind=kind, item_id=row.id)
            db.session.add(st)
        st.client_id, st.engagement_id, st.title = client_id, engagement_id, (title or "")[:300]
        st.status, st.text, st.error = "reading", None, None


def start(kind, row):
    """After an upload: read the file. In the background on a live server, inline when OCR_SYNC=1. Never raises."""
    try:
        mark_reading(kind, row)
        db.session.commit()
        item_id = row.id
        if _sync():
            run_job(kind, item_id)
            return
        app = current_app._get_current_object()

        def work():
            with app.app_context():
                run_job(kind, item_id)
                db.session.remove()
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        db.session.rollback()


def start_many(pairs):
    """Read several files one after another in one background job (used by the catch-up on the search page)."""
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            for kind, item_id in pairs:
                run_job(kind, item_id)
            db.session.remove()
    if _sync():
        with app.app_context():
            for kind, item_id in pairs:
                run_job(kind, item_id)
        return
    threading.Thread(target=work, daemon=True).start()


def forget(kind, item_id):
    from models import ScanText
    try:
        ScanText.query.filter_by(kind=kind, item_id=item_id).delete()
    except Exception:
        db.session.rollback()


# ------------------------------------------------------------------ files not yet read (existing uploads)
CLIENT_KINDS = ("engagement_doc", "permanent_file", "filing_archive", "zimra_corr")
READABLE = ocr_reader.SCANNABLE_EXTENSIONS | {"docx", "xlsx", "xlsm", "pptx"}


def unread(limit=None):
    """[(kind, id)] of client files that have no ScanText row yet and a readable file type."""
    from models import ScanText
    out = []
    have = {(k, i) for k, i in db.session.query(ScanText.kind, ScanText.item_id).all()}
    for kind in CLIENT_KINDS:
        info = kind_info(kind)
        for row in info["model"].query.order_by(info["model"].id.desc()).all():
            if (kind, row.id) in have:
                continue
            if _ext(_original_name(kind, row) or getattr(row, info["file"], "")) in READABLE:
                out.append((kind, row.id))
                if limit and len(out) >= limit:
                    return out
    return out


def unread_library():
    """[(kind, id)] of library files whose text is missing because they are scans (status no_text_found / reading stuck)."""
    out = []
    for kind in ("regulatory_notice", "legislative_update", "policy_document"):
        info = kind_info(kind)
        for row in info["model"].query.all():
            name = getattr(row, info["file"], None)
            if name and name != "pending" and row.extraction_status in ("no_text_found", None) and not (row.extracted_text or "").strip() \
                    and _ext(name) in ocr_reader.SCANNABLE_EXTENSIONS:
                out.append((kind, row.id))
    return out


# ------------------------------------------------------------------ searching
def _can_see(user, st):
    from models import user_can_access_client, user_can_access_engagement, Engagement
    if st.client_id and not user_can_access_client(user, st.client_id):
        return False
    if st.engagement_id:
        eng = db.session.get(Engagement, st.engagement_id)
        if eng is not None and not user_can_access_engagement(user, eng):
            return False
    return True


def _snippet(text, terms, width=110):
    low = text.lower()
    pos = min([low.find(t) for t in terms if low.find(t) >= 0] or [0])
    a = max(0, pos - width)
    b = min(len(text), pos + width * 2)
    s = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("... " if a > 0 else "") + s + (" ..." if b < len(text) else "")


def search(user, query, kind=None, limit=60):
    """[{st, snippet, label}] - files whose text contains every word of `query`, that `user` is allowed to see."""
    from models import ScanText
    terms = [t.lower() for t in re.findall(r"[\w'./-]+", query or "") if len(t) >= 2][:8]
    if not terms:
        return []
    q = ScanText.query.filter(ScanText.status == "extracted")
    if kind:
        q = q.filter(ScanText.kind == kind)
    for t in terms:
        q = q.filter(ScanText.text.ilike(f"%{t}%"))
    out = []
    kinds = _kinds()
    for st in q.order_by(ScanText.read_at.desc()).limit(limit * 3).all():
        if not _can_see(user, st):
            continue
        info = kinds.get(st.kind)
        if info is None or db.session.get(info["model"], st.item_id) is None:
            continue
        out.append({"st": st, "snippet": _snippet(st.text or "", terms), "label": info["label"]})
        if len(out) >= limit:
            break
    return out
