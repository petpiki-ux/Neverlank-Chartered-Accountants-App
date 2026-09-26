"""Bulk import of Zimbabwean tax legislation from Veritas Zimbabwe's
taxonomy listing pages (e.g. https://www.veritaszim.net/taxonomy/term/98,
the "Income Tax" term - Petros's own link) into Legislative Update Control.

Run via `flask import-veritas-legislation` (see app.py) - not a web route,
for the same reason zimra_notices.py/zimlii_import.py aren't: each item
involves an HTTP download and (for anything not a full Act) a Claude API
call, and a bulk run shouldn't be bound by a web request's timeout. In
practice a single taxonomy term here is much smaller (a few dozen items
across 1-2 listing pages, not hundreds), so a run finishes quickly, but the
same CLI-not-a-route reasoning still applies.

Explicit instruction from Petros: "Upload legislation on this page to the
library where an Act or SI has already been uploaded skip." That's the
dedup rule below (see _existing_identifiers/_extract_identifier) - but it's
deliberately NOT the same "already imported by this specific
importer" check zimra_notices.py/zimlii_import.py use (a source_reference
tag unique to that importer). Veritas, ZimLII and ZIMRA all publish
overlapping Zimbabwean legislation, so the same SI or Act could easily
already be on file from a DIFFERENT source than Veritas. Instead this
parses a structured (kind, year, number) identifier - e.g. ("SI", 2025, 26)
for "SI 2025-026", or ("Act", 2015, 9) for "Act No. 9/2015" - out of BOTH
the Veritas candidate's own title and, for cross-source matching, out of
every already-filed LegislativeUpdate's title and source_reference (which
for a ZimLII import contains its AKN work URI, e.g.
"ZimLII /akn/zw/act/si/2025/26" - the same (year, number) pair). This is
necessarily best-effort (a ZIMRA notice or a manually-typed entry whose
title never states the SI/Act number in a recognisable form won't be
matched), but it catches the common, well-identified case honestly rather
than silently re-filing what's already there. This importer also tags its
own filings with "Veritas /node/<id>" in source_reference, both for the
usual "safe to interrupt and re-run" guarantee and as a second, independent
check in case identifier extraction ever misses on a title Veritas itself
later reformats.

An item that doesn't look like an Act, SI or Government Notice at all (e.g.
a "Bill Watch" commentary newsletter also tagged under this term) is
skipped outright - Petros asked to "upload legislation", and a newsletter
about legislation isn't the legislation itself.

This app's own build environment can't reach veritaszim.net directly - the
page structure below (a Drupal 7 site: /node/<id> content pages, a
"?page=N" taxonomy-term pager, a "Gazetted DD-MM-YYYY" line and a linked
.pdf attachment on each node's own page) was reverse-engineered from what a
fetch-and-summarise tool could observe of the live site, not tested against
its raw HTML - exactly the same situation zimra_notices.py and
zimlii_import.py were built under, and the same defensive approach is used
here: keying off the /node/<id> link pattern and a plain ".pdf" href suffix
rather than any particular theme's CSS classes.

ALWAYS run with --dry-run --pages 1 first - it lists every title this would
import, and which (Act/SI/Notice, year, number) identifier it extracted,
making no request beyond the listing page itself. Read through it: if a
title's identifier looks wrong, or something that should be classified as
an Act/SI isn't, stop and say so before running the real import.

Safe to interrupt and re-run at any point.
"""
import os
import re
import time
import uuid
import urllib.robotparser
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from extensions import db
from models import LegislativeUpdate, User
from config import Config
import sanctions_data
import legislation_summary
import legislation_chunking

VERITAS_BASE_URL = "https://www.veritaszim.net"
DEFAULT_TERM_ID = 98  # "Income Tax" - the term Petros linked
VERITAS_PER_PAGE = 20  # Drupal 7's default taxonomy-term pager page size, for stopping detection
USER_AGENT = "Mozilla/5.0 (compatible; NeverlankAuditApp/1.0; +https://neverlank-audit-app.onrender.com)"
REQUEST_TIMEOUT = 30
DEFAULT_SLEEP_SECONDS = 1.5  # politeness delay between requests to Veritas's own server
TITLE_MAX_LEN = 200  # matches models.LegislativeUpdate.title's column width

NODE_LINK_RE = re.compile(r"^/node/(\d+)$")
# A Veritas/ZIMRA-style "SI YYYY-NNN" or "SI YYYY-NN" title (e.g. "SI 2025-026").
SI_TITLE_RE = re.compile(r"\bSI\s*(\d{4})[\s-](\d{1,4})\b", re.IGNORECASE)
# "GN YYYY-NNN" - a Government Notice, numbered the same way as an SI.
GN_TITLE_RE = re.compile(r"\bGN\s*(\d{4})[\s-](\d{1,4})\b", re.IGNORECASE)
# "Act No. 9/2015", "Act No 9 of 2015", "(Act No. 9/2015)" - captures (number, year).
ACT_TITLE_RE = re.compile(r"Act\s*No\.?\s*(\d{1,4})\s*(?:of|/)\s*(\d{4})", re.IGNORECASE)
# ZimLII's own AKN "work" URI, as stored in an existing entry's source_reference
# (see zimlii_import.py) - lets an Act/SI already filed FROM ZimLII be
# recognised here too, not just one filed from Veritas or ZIMRA.
AKN_SI_RE = re.compile(r"/akn/zw/act/si/(\d{4})/(\d+)")
AKN_ACT_RE = re.compile(r"/akn/zw/act/(\d{4})/(\d+)")
GAZETTE_DATE_RE = re.compile(r"Gazetted[:\s]+(\d{2})-(\d{2})-(\d{4})", re.IGNORECASE)


def _legislative_updates_dir():
    directory = Config.LEGISLATIVE_UPDATES_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory


def _robots_allow(path):
    """Same fix as zimra_notices.py/zimlii_import.py's _robots_allow: fetch
    robots.txt with our own _fetch() (a proper User-Agent) and hand the
    text to RobotFileParser.parse(), rather than RobotFileParser.read()'s
    own bare, unauthenticated-looking request."""
    try:
        resp = _fetch(urljoin(VERITAS_BASE_URL, "/robots.txt"))
        if resp.status_code != 200:
            return True
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(USER_AGENT, urljoin(VERITAS_BASE_URL, path))
    except Exception:
        return True


def _fetch(url):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)


def extract_identifier(title=None, source_reference=None):
    """Pull a structured (kind, year, number) identifier - "SI", "Notice"
    (a GN) or "Act" - out of a title (an already-filed entry's, or a
    Veritas candidate's) or, failing that, out of a ZimLII-style AKN work
    URI found in source_reference. Returns None if neither yields a
    recognisable identifier (this is exactly what marks a listing item as
    "not legislation" - e.g. a Bill Watch commentary piece - and, for an
    existing library entry, simply means it doesn't participate in this
    dedup check)."""
    if title:
        m = SI_TITLE_RE.search(title)
        if m:
            return ("SI", int(m.group(1)), int(m.group(2)))
        m = GN_TITLE_RE.search(title)
        if m:
            return ("Notice", int(m.group(1)), int(m.group(2)))
        m = ACT_TITLE_RE.search(title)
        if m:
            return ("Act", int(m.group(2)), int(m.group(1)))
    if source_reference:
        m = AKN_SI_RE.search(source_reference)
        if m:
            return ("SI", int(m.group(1)), int(m.group(2)))
        m = AKN_ACT_RE.search(source_reference)
        if m:
            return ("Act", int(m.group(1)), int(m.group(2)))
    return None


def _existing_identifiers():
    """Every (kind, year, number) identifier already recognisable among
    ALL currently-filed LegislativeUpdate rows, regardless of source or of
    what instrument_type it was tagged with - a ZIMRA-filed rate notice
    that happens to state "SI 65 of 2024" in its own title still counts as
    that SI already being on file. See extract_identifier and this
    module's docstring for why this is the dedup key rather than a
    Veritas-only source_reference tag."""
    identifiers = set()
    rows = db.session.query(LegislativeUpdate.title, LegislativeUpdate.source_reference).all()
    for title, source_reference in rows:
        identifier = extract_identifier(title=title, source_reference=source_reference)
        if identifier:
            identifiers.add(identifier)
    return identifiers


def _existing_veritas_node_ids():
    refs = db.session.query(LegislativeUpdate.source_reference).filter(
        LegislativeUpdate.source_reference.like("Veritas /node/%")
    ).all()
    ids = set()
    for (ref,) in refs:
        m = re.search(r"/node/(\d+)$", ref or "")
        if m:
            ids.add(m.group(1))
    return ids


def _parse_listing_page(html):
    """Parse one taxonomy-term listing page's HTML into a list of
    {"title", "node_id", "url"} dicts, deduplicated by node id. A Drupal 7
    teaser typically links the same node twice (the title itself, plus a
    separate "Read more" link) - keeps whichever anchor text is longer,
    which in practice is always the real title, never the generic "Read
    more" text."""
    soup = BeautifulSoup(html, "html.parser")
    by_node = {}
    for a in soup.find_all("a", href=True):
        path = urlparse(urljoin(VERITAS_BASE_URL, a["href"])).path
        match = NODE_LINK_RE.match(path)
        if not match:
            continue
        node_id = match.group(1)
        text = a.get_text(strip=True)
        if not text or text.lower() in ("read more", "add new comment", ""):
            continue
        existing = by_node.get(node_id)
        if existing is None or len(text) > len(existing["title"]):
            by_node[node_id] = {"title": text[:TITLE_MAX_LEN], "node_id": node_id, "url": urljoin(VERITAS_BASE_URL, f"/node/{node_id}")}
    return list(by_node.values())


def list_all_items(term_id=DEFAULT_TERM_ID, max_pages=None, sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """Walk every page of one taxonomy term's listing (?page=0, 1, 2, ...)
    and return every item found, deduplicated by node id. Stops once a page
    turns up nothing new, or once max_pages pages have been fetched."""
    listing_path = f"/taxonomy/term/{term_id}"
    if not _robots_allow(listing_path):
        raise RuntimeError(f"robots.txt on {VERITAS_BASE_URL} disallows crawling {listing_path} - stopping without fetching anything further.")
    all_items = {}
    page = 0
    while max_pages is None or page < max_pages:
        url = f"{VERITAS_BASE_URL}{listing_path}" + (f"?page={page}" if page else "")
        log(f"Fetching listing page {page + 1} ({url}) ...")
        resp = _fetch(url)
        if resp.status_code != 200:
            log(f"  stopped - HTTP {resp.status_code}")
            break
        page_items = _parse_listing_page(resp.text)
        if not page_items:
            log("  no items found on this page - assuming this is past the end of the listing.")
            break
        new_count = 0
        for item in page_items:
            if item["node_id"] not in all_items:
                all_items[item["node_id"]] = item
                new_count += 1
        log(f"  found {len(page_items)} item(s) on this page, {new_count} not seen on an earlier page.")
        if new_count == 0:
            log("  every item on this page was already seen - assuming pagination has looped back to the start.")
            break
        page += 1
        time.sleep(sleep_seconds)
    return list(all_items.values())


def list_candidates(term_id=DEFAULT_TERM_ID, max_pages=None, sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """Every item on the listing that looks like an Act, SI or Government
    Notice (has an extractable identifier - see extract_identifier),
    tagged with which kind and identifier it matched. Anything else (a
    Bill Watch commentary piece, an unrelated page that happened to link
    to the same term) is left out - this is what "upload legislation, skip
    what isn't" means for a listing item, before dedup is even considered."""
    items = list_all_items(term_id=term_id, max_pages=max_pages, sleep_seconds=sleep_seconds, log=log)
    candidates = []
    for item in items:
        identifier = extract_identifier(title=item["title"])
        if not identifier:
            continue
        item = dict(item, instrument_type=identifier[0], identifier=identifier)
        candidates.append(item)
    return candidates


def _parse_node_detail(html, node_url):
    """Pull the gazette date (if the "Gazetted DD-MM-YYYY" line is present)
    and the linked PDF's URL off one Veritas node page - see module
    docstring for why this is read off the node's own page rather than the
    listing page's text."""
    soup = BeautifulSoup(html, "html.parser")
    pdf_url = None
    for a in soup.find_all("a", href=True):
        if a["href"].split("?", 1)[0].lower().endswith(".pdf"):
            pdf_url = urljoin(node_url, a["href"])
            break
    gazette_date = None
    match = GAZETTE_DATE_RE.search(soup.get_text(" ", strip=True))
    if match:
        try:
            gazette_date = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            gazette_date = None
    return {"pdf_url": pdf_url, "gazette_date": gazette_date}


def import_legislation(term_id=DEFAULT_TERM_ID, default_area=None, dry_run=False, max_pages=None, limit=None,
                        sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """The actual bulk import. Lists every Act/SI/Notice-looking item on
    the given taxonomy term, works out which ones aren't already on file
    anywhere (see _existing_identifiers/this module's docstring for the
    dedup rule Petros asked for), and - unless dry_run - downloads and
    files each remaining one exactly like a manual upload through the app:
    same PDF text extraction (sanctions_data.extract_pdf_text), same AI
    summarisation (legislation_summary.py). One item failing (a bad
    download, a node page with no PDF link) is logged and skipped rather
    than stopping the whole run."""
    candidates = list_candidates(term_id=term_id, max_pages=max_pages, sleep_seconds=sleep_seconds, log=log)
    already_identifiers = _existing_identifiers()
    already_node_ids = _existing_veritas_node_ids()
    to_import = []
    skipped_dupe = 0
    for c in candidates:
        if c["identifier"] in already_identifiers or c["node_id"] in already_node_ids:
            skipped_dupe += 1
            continue
        to_import.append(c)
    if limit is not None:
        to_import = to_import[:limit]

    log(f"\n{len(candidates)} legislation item(s) found on Veritas term {term_id}, "
        f"{skipped_dupe} already on file (Act/SI already uploaded), {len(to_import)} to import"
        f"{f' (showing up to {limit})' if limit is not None else ''}.")

    if dry_run:
        log("--dry-run: nothing will be downloaded or saved. Items that WOULD be imported:")
        for c in to_import:
            kind, year, number = c["identifier"]
            log(f"  - [{c['instrument_type']}] {c['title']}  ({kind} {year}-{number})  {c['url']}")
        return {"found": len(candidates), "already_on_file": skipped_dupe, "would_import": len(to_import)}

    admin_user = User.query.filter_by(role="admin").order_by(User.id).first()
    imported, failed = 0, 0
    for i, c in enumerate(to_import, start=1):
        log(f"[{i}/{len(to_import)}] [{c['instrument_type']}] {c['title']}")
        try:
            _import_one_item(c, default_area=default_area, created_by_id=admin_user.id if admin_user else None, log=log)
            imported += 1
        except Exception as exc:
            db.session.rollback()
            failed += 1
            log(f"  FAILED: {exc}")
        time.sleep(sleep_seconds)
    log(f"\nDone - {imported} imported, {failed} failed, {skipped_dupe} already on file (Act/SI already uploaded, skipped).")
    return {"found": len(candidates), "already_on_file": skipped_dupe, "imported": imported, "failed": failed}


def _import_one_item(candidate, default_area, created_by_id, log):
    detail_resp = _fetch(candidate["url"])
    detail_resp.raise_for_status()
    detail = _parse_node_detail(detail_resp.text, candidate["url"])
    if not detail["pdf_url"]:
        raise ValueError("no PDF attachment link found on the node page - page structure may not match what this was written against")

    pdf_resp = _fetch(detail["pdf_url"])
    pdf_resp.raise_for_status()
    content_type = pdf_resp.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and not pdf_resp.content[:5].startswith(b"%PDF"):
        raise ValueError(f"download didn't look like a PDF (Content-Type: {content_type!r}) - skipped")

    source_reference = f"Veritas /node/{candidate['node_id']}"
    update = LegislativeUpdate(
        instrument_type=candidate["instrument_type"],
        title=candidate["title"],
        source_reference=source_reference,
        gazette_date=detail["gazette_date"],
        summary=f"Imported from Veritas Zimbabwe ({candidate['url']}) - see the AI summary below once generated.",
        created_by_id=created_by_id,
    )
    if default_area:
        update.set_areas([default_area])
    db.session.add(update)
    db.session.flush()  # assign update.id without committing yet

    original_name = f"veritas_node_{candidate['node_id']}.pdf"
    stored_name = f"leg{update.id}_{uuid.uuid4().hex[:8]}_{original_name}"
    filepath = os.path.join(_legislative_updates_dir(), stored_name)
    with open(filepath, "wb") as f:
        f.write(pdf_resp.content)
    update.original_filename = original_name
    update.stored_filename = stored_name

    text, extraction_status, page_count = sanctions_data.extract_pdf_text(filepath)
    update.extracted_text = text
    update.extraction_status = extraction_status
    update.page_count = page_count
    db.session.commit()  # save the download itself before attempting the AI call, so a slow/failed AI step never loses it

    skip_reason = legislation_summary.skip_summary_reason(candidate["instrument_type"])
    if skip_reason:
        update.ai_status = "skipped"
        update.ai_error = skip_reason
        update.ai_processed_at = datetime.utcnow()
    else:
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
    legislation_chunking.ensure_chunks(update)
    db.session.commit()
    log(f"  saved (ai_status={update.ai_status})")
