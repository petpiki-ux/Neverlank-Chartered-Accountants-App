"""One-time (or repeatable) bulk import of ZIMRA's own Public Notices page
(https://www.zimra.co.zw/public-notices) into Legislative Update Control
(see models.LegislativeUpdate, tax.py) - filing each one as a "Notice",
extracting its text and running it through the same AI summarisation as a
manually-filed instrument (legislation_summary.py).

Run via `flask import-zimra-notices` (see app.py's register_cli) - this is
NOT a web route on purpose: at roughly 480 notices, each involving an HTTP
download and a Claude API call, a single HTTP request would badly exceed
any reasonable web request timeout. Run it from Render's Shell (or locally
against a copy of the database) instead - it can safely run for as long as
it needs to and prints its progress as it goes.

ALWAYS run with --dry-run --pages 1 first. ZIMRA's site (confirmed to be
Joomla-based, from its "?start=N" pagination and "?download=ID:slug"
document links) was reverse-engineered from what this app's own build
environment could reach - a network policy in the environment this was
built in blocks direct access to zimra.co.zw, so the page-parsing logic
below could only be checked against a description of the live page, not the
live page's raw HTML. It's written defensively (it keys off the
"?download=" links themselves rather than any particular theme's CSS
classes, which is the part most likely to survive a template change) but a
dry run against a page or two of the real site is the only way to confirm
it's actually extracting sensible titles before trusting it with all ~480.

Safe to interrupt and re-run at any point: every notice this has already
imported is recorded via a "ZIMRA Public Notice #<id>" tag in
source_reference (ZIMRA's own numeric id for that document, parsed out of
its download link), so a partial or repeated run only ever imports what's
still missing - it never re-downloads or duplicates one already on file.
"""
import os
import re
import time
import uuid
import urllib.robotparser
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from extensions import db
from models import LegislativeUpdate, User
from config import Config
import sanctions_data
import legislation_summary

ZIMRA_BASE_URL = "https://www.zimra.co.zw"
ZIMRA_NOTICES_PATH = "/public-notices"
ZIMRA_NOTICES_PER_PAGE = 20
USER_AGENT = "Mozilla/5.0 (compatible; NeverlankAuditApp/1.0; +https://neverlank-audit-app.onrender.com)"
REQUEST_TIMEOUT = 30
DEFAULT_SLEEP_SECONDS = 1.5  # politeness delay between requests to ZIMRA's own server - this is their infrastructure, not ours
TITLE_MAX_LEN = 200  # matches models.LegislativeUpdate.title's column width

DOWNLOAD_LINK_RE = re.compile(r"[?&]download=(\d+)(?::([\w-]+))?")


def _legislative_updates_dir():
    directory = Config.LEGISLATIVE_UPDATES_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory


def _robots_allow(path):
    """Check robots.txt before crawling anything, rather than assuming
    permission. Fails safe in the "allow" direction only when robots.txt
    itself can't be fetched/parsed at all (most sites with no reachable
    robots.txt do allow ordinary crawling) - any Disallow that actually
    matches the path is always honoured."""
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urljoin(ZIMRA_BASE_URL, "/robots.txt"))
        rp.read()
        return rp.can_fetch(USER_AGENT, urljoin(ZIMRA_BASE_URL, path))
    except Exception:
        return True


def _fetch(url):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)


def _parse_notice_list_page(html):
    """Parse one listing page's HTML into a list of {"title", "download_url",
    "zimra_id"} dicts. Deliberately keys off every link matching ZIMRA's own
    "?download=<id>[:<slug>]" pattern rather than any particular CSS class
    or table structure, since that's the part of a Joomla document-listing
    template most likely to change; the numeric id is also what makes a
    re-run safely skip a notice it's already imported (see
    import_notices)."""
    soup = BeautifulSoup(html, "html.parser")
    seen_ids = set()
    notices = []
    for a in soup.find_all("a", href=True):
        match = DOWNLOAD_LINK_RE.search(a["href"])
        if not match:
            continue
        zimra_id = match.group(1)
        if zimra_id in seen_ids:
            continue  # the same download link can appear twice on one row (the title itself, plus a separate "Download" icon/link)
        title = a.get_text(strip=True) or (a.get("title") or "").strip()
        if not title or title.lower() in ("download", "pdf", "view", ""):
            # the link itself carries no useful text - fall back to the slug in the URL
            slug = match.group(2) or ""
            title = slug.replace("-", " ").strip().capitalize()
        if not title:
            continue
        seen_ids.add(zimra_id)
        notices.append({
            "title": title[:TITLE_MAX_LEN],
            "download_url": urljoin(ZIMRA_BASE_URL, a["href"]),
            "zimra_id": zimra_id,
        })
    return notices


def list_all_notices(max_pages=None, sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """Walk every listing page (?start=0, 20, 40, ...) and return every
    notice found, deduplicated by ZIMRA's own download id. Stops once a
    page turns up nothing new (either past the real last page, or looped
    back to the start) or once max_pages pages have been fetched."""
    if not _robots_allow(ZIMRA_NOTICES_PATH):
        raise RuntimeError(f"robots.txt on {ZIMRA_BASE_URL} disallows crawling {ZIMRA_NOTICES_PATH} - stopping without fetching anything further.")
    all_notices = {}
    page = 0
    while max_pages is None or page < max_pages:
        start = page * ZIMRA_NOTICES_PER_PAGE
        url = f"{ZIMRA_BASE_URL}{ZIMRA_NOTICES_PATH}" + (f"?start={start}" if start else "")
        log(f"Fetching listing page {page + 1} ({url}) ...")
        resp = _fetch(url)
        if resp.status_code != 200:
            log(f"  stopped - HTTP {resp.status_code}")
            break
        page_notices = _parse_notice_list_page(resp.text)
        if not page_notices:
            log("  no notices found on this page - assuming this is past the end of the listing.")
            break
        new_count = 0
        for n in page_notices:
            if n["zimra_id"] not in all_notices:
                all_notices[n["zimra_id"]] = n
                new_count += 1
        log(f"  found {len(page_notices)} notice(s) on this page, {new_count} not seen on an earlier page.")
        if new_count == 0:
            log("  every notice on this page was already seen - assuming pagination has looped back to the start.")
            break
        page += 1
        time.sleep(sleep_seconds)
    return list(all_notices.values())


def _existing_source_references():
    return {
        row.source_reference for row in
        db.session.query(LegislativeUpdate.source_reference).filter(LegislativeUpdate.source_reference.isnot(None)).all()
    }


def import_notices(dry_run=False, max_pages=None, limit=None, sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """The actual bulk import. Lists every notice currently on ZIMRA's
    Public Notices page, works out which ones aren't already on file
    (matched by the "ZIMRA Public Notice #<id>" tag this stamps into
    source_reference), and - unless dry_run - downloads and files each
    remaining one exactly like a manual upload through the app: same text
    extraction (sanctions_data.extract_pdf_text), same AI summarisation
    (legislation_summary.summarize_legislative_update). One notice failing
    (a bad download, an unreadable file) is logged and skipped rather than
    stopping the whole run."""
    notices = list_all_notices(max_pages=max_pages, sleep_seconds=sleep_seconds, log=log)
    already = _existing_source_references()
    to_import = []
    for n in notices:
        source_reference = f"ZIMRA Public Notice #{n['zimra_id']}"
        if source_reference in already:
            continue
        n["source_reference"] = source_reference
        to_import.append(n)
    if limit is not None:
        to_import = to_import[:limit]

    log(f"\n{len(notices)} notice(s) found on ZIMRA, {len(to_import)} not yet imported"
        f"{f' (showing up to {limit})' if limit is not None else ''}.")

    if dry_run:
        log("--dry-run: nothing will be downloaded or saved. Titles that WOULD be imported:")
        for n in to_import:
            log(f"  - {n['title']}  ({n['download_url']})")
        return {"found": len(notices), "would_import": len(to_import)}

    admin_user = User.query.filter_by(role="admin").order_by(User.id).first()
    imported, failed = 0, 0
    for i, n in enumerate(to_import, start=1):
        log(f"[{i}/{len(to_import)}] {n['title']}")
        try:
            _import_one_notice(n, created_by_id=admin_user.id if admin_user else None, log=log)
            imported += 1
        except Exception as exc:
            db.session.rollback()
            failed += 1
            log(f"  FAILED: {exc}")
        time.sleep(sleep_seconds)
    log(f"\nDone - {imported} imported, {failed} failed, {len(notices) - len(to_import)} already on file from an earlier run.")
    return {"found": len(notices), "imported": imported, "failed": failed}


def _import_one_notice(notice, created_by_id, log):
    resp = _fetch(notice["download_url"])
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and not resp.content[:5].startswith(b"%PDF"):
        raise ValueError(f"download didn't look like a PDF (Content-Type: {content_type!r}) - skipped")

    update = LegislativeUpdate(
        instrument_type="Notice",
        title=notice["title"],
        source_reference=notice["source_reference"],
        summary=f"Imported from ZIMRA Public Notices ({ZIMRA_BASE_URL}{ZIMRA_NOTICES_PATH}) - see the AI summary below once generated.",
        created_by_id=created_by_id,
    )
    db.session.add(update)
    db.session.flush()  # assign update.id without committing yet

    original_name = notice["source_reference"].replace("ZIMRA Public Notice #", "zimra_notice_") + ".pdf"
    stored_name = f"leg{update.id}_{uuid.uuid4().hex[:8]}_{original_name}"
    filepath = os.path.join(_legislative_updates_dir(), stored_name)
    with open(filepath, "wb") as f:
        f.write(resp.content)
    update.original_filename = original_name
    update.stored_filename = stored_name

    text, extraction_status, page_count = sanctions_data.extract_pdf_text(filepath)
    update.extracted_text = text
    update.extraction_status = extraction_status
    update.page_count = page_count
    db.session.commit()  # save the download itself before attempting the AI call, so a slow/failed AI step never loses it

    result, ai_status, ai_error = legislation_summary.summarize_legislative_update(
        filepath, False, update.extracted_text, update.extraction_status,
    )
    update.ai_status = ai_status
    update.ai_error = ai_error
    update.ai_processed_at = datetime.utcnow()
    if ai_status == "done":
        update.ai_summary = result["summary"]
        update.set_ai_key_changes(result["key_changes"])
        update.set_ai_suggested_areas(result["suggested_areas"])
    db.session.commit()
    log(f"  saved (ai_status={ai_status})")
