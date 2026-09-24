"""Bulk import of Zimbabwean primary legislation (Acts) and current
subsidiary legislation (regulations/Statutory Instruments) from ZimLII
(https://zimlii.org/legislation/) into Legislative Update Control - filtered
to tax, customs, companies, commercial, estate duty and related Finance law,
per Petros's request (as opposed to the ~410 Acts and 157 subsidiary
instruments ZimLII carries across every field of Zimbabwean law).

Run via `flask import-zimlii-legislation` (see app.py) - not a web route,
for the same reason zimra_notices.py isn't: this touches a few hundred
documents, some of them very large (a full Act can run to hundreds of
pages), well beyond a web request's timeout. Run it from Render's Shell.

ALWAYS run with --dry-run first - twice, in fact, for this one:

  1. `flask import-zimlii-legislation --dry-run` lists every title this
     would file, and which of the target categories it matched on (e.g.
     "Estate Duty & Succession: matched 'estate duty'"). This makes NO
     network request beyond the listing pages themselves - nothing is
     downloaded or filed. Read through the list: if something is
     miscategorised or an obviously irrelevant title slipped in (or a title
     you expected is missing), stop and tell me before running the real
     import - fixing the CATEGORY_KEYWORDS matching below and redeploying is
     quick, much quicker than re-downloading everything after filing the
     wrong set.
  2. Once the categorisation looks right, `flask import-zimlii-legislation
     --dry-run --pages 1 --fetch-sample 2` additionally fetches and prints
     an excerpt of 2 real documents' extracted text (without saving them),
     so you can see the actual extraction quality against the live site
     before trusting it with everything - this environment's own network
     policy blocks it from reaching zimlii.org directly to test this
     itself, exactly as with the ZIMRA importer before it.

This app's own build environment can't reach zimlii.org to test any of this
against the live site - the page structure below (AKN URIs, listing
pagination, content extraction) was reverse-engineered from what could be
observed of the live site through a fetch-and-summarise tool, not tested
against raw HTML. It's written defensively for the same reason
zimra_notices.py was: keying off the AKN URI pattern in each link
(`/akn/zw/act/<year>/<number>` or `/akn/zw/act/si/<year>/<number>`), which
is a stable, documented part of the Laws.Africa platform this site runs on,
rather than any particular theme's CSS classes.

Safe to interrupt and re-run: every filed document is tagged with its own
stable AKN "work" URI in source_reference (e.g. "ZimLII /akn/zw/act/1999/9"),
so a partial or repeated run only ever imports what's still missing.
"""
import os
import re
import time
import uuid
import urllib.robotparser
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from extensions import db
from models import LegislativeUpdate, User, LEGISLATIVE_UPDATE_AREAS
from config import Config
import legislation_summary
import legislation_chunking

ZIMLII_BASE_URL = "https://zimlii.org"
LISTING_PATHS = {
    "Act": "/legislation/",           # current Acts & Ordinances (~410)
    "SI": "/legislation/subsidiary",  # current subsidiary legislation (~157)
}
USER_AGENT = "Mozilla/5.0 (compatible; NeverlankAuditApp/1.0; +https://neverlank-audit-app.onrender.com)"
REQUEST_TIMEOUT = 30
DEFAULT_SLEEP_SECONDS = 2.0  # a full Act page is heavier than a ZIMRA notice - a bit more courtesy delay
TITLE_MAX_LEN = 200  # matches models.LegislativeUpdate.title's column width
PER_LISTING_PAGE_SIZE = 20  # ZimLII's own pagination page size, for stopping detection

# The AKN "work" URI for an Act or SI - the stable, version-independent id
# for a piece of legislation on this platform (e.g. /akn/zw/act/1999/9, or
# /akn/zw/act/si/2024/45). A listing link may point at a specific dated
# "expression" of it (.../eng@2018-03-14); we always key on the work URI so
# re-runs and later amendments of the same instrument aren't re-filed as a
# new document.
AKN_WORK_RE = re.compile(r"(/akn/zw/act/(?:si/)?\d{4}/[^/]+)")

# Only titles matching at least one of these keyword phrases (checked as a
# case-insensitive substring of the title) get filed - out of ZimLII's ~570
# current Acts/SIs across every field of Zimbabwean law, this is what narrows
# it down to tax, customs, companies, commercial, estate duty and related
# Finance legislation, per Petros's request. Maps onto the SAME practice-area
# tags used everywhere else in Legislative Update Control (see
# models.LEGISLATIVE_UPDATE_AREAS), rather than inventing a separate
# taxonomy just for this import. If the --dry-run list below turns up a
# false positive or a clear miss, this dict is the thing to adjust.
CATEGORY_KEYWORDS = {
    "Tax": [
        "income tax", "value added tax", "capital gains tax", "tax reserve certificates",
        "revenue authority", "presumptive tax", "stamp duties", "finance act", "finance (",
    ],
    "Customs & Excise": [
        "customs and excise", "customs tariff", "export incentives",
    ],
    "Companies & Corporate Law": [
        "companies and other business entities", "companies act", "insolvency",
        "close corporations", "business names", "co-operative", "cooperative societies",
        "competition act",
    ],
    "Commercial Law": [
        "hire purchase", "bills of exchange", "mercantile law", "sale of goods",
        "consumer contracts", "contractual penalties", "deeds registries",
        "movable property security interests",
    ],
    "Estate Duty & Succession": [
        "estate duty", "administration of estates", "deceased estates succession",
        "deceased persons", "wills act",
    ],
    "Banking & Finance": [
        "banking act", "building societies", "microfinance", "collective investment schemes",
        "securities and exchange", "capital markets", "public finance management",
        "public debt management", "reserve bank of zimbabwe", "asset management",
        "zimbabwe investment and development agency", "special economic zones",
    ],
    "Insurance & Pensions": [
        "insurance act", "pension and provident funds", "pension fund",
        "national social security",
    ],
    "Exchange Control": [
        "exchange control",
    ],
    "Anti-Money Laundering": [
        "money laundering", "proceeds of crime",
    ],
}


def _legislative_updates_dir():
    directory = Config.LEGISLATIVE_UPDATES_DATA_DIR
    os.makedirs(directory, exist_ok=True)
    return directory


def classify_title(title):
    """Return the list of practice areas (from LEGISLATIVE_UPDATE_AREAS)
    this title matches, and which keyword triggered each - purely for the
    --dry-run listing to be reviewable, not just a "trust me" filter. A
    title can legitimately match more than one area (e.g. the Deeds
    Registries Act matters to both Commercial Law and Estate Duty &
    Succession) - all matches are recorded and all get applied as tags."""
    lower = title.lower()
    matches = []  # list of (area, keyword)
    for area, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                matches.append((area, kw))
                break
    return matches


def _robots_allow(path):
    """Same fix as zimra_notices.py's _robots_allow: fetch robots.txt with
    our own _fetch() (a proper User-Agent) and hand the text to
    RobotFileParser.parse(), rather than RobotFileParser.read()'s own bare,
    unauthenticated-looking request - see that module for the full story of
    why .read() alone produces false positives on some servers."""
    try:
        resp = _fetch(urljoin(ZIMLII_BASE_URL, "/robots.txt"))
        if resp.status_code != 200:
            return True
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(USER_AGENT, urljoin(ZIMLII_BASE_URL, path))
    except Exception:
        return True


def _fetch(url):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)


def _parse_listing_page(html, instrument_type):
    """Parse one listing page's HTML into {"title", "url", "work_uri",
    "instrument_type"} dicts, keyed off the AKN work URI in each link's
    href rather than any particular CSS class (see module docstring)."""
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    documents = []
    for a in soup.find_all("a", href=True):
        match = AKN_WORK_RE.search(a["href"])
        if not match:
            continue
        work_uri = match.group(1)
        if work_uri in seen:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue  # likely an icon-only link riding along the same href, not the title link itself
        seen.add(work_uri)
        documents.append({
            "title": title[:TITLE_MAX_LEN],
            "url": urljoin(ZIMLII_BASE_URL, a["href"]),
            "work_uri": work_uri,
            "instrument_type": "SI" if "/act/si/" in work_uri else instrument_type,
        })
    return documents


def list_all_documents(instrument_type, max_pages=None, sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """Walk every page of one listing (Acts, or subsidiary legislation) and
    return every document found, deduplicated by AKN work URI. Stops once a
    page turns up nothing new, or once max_pages pages have been fetched."""
    listing_path = LISTING_PATHS[instrument_type]
    if not _robots_allow(listing_path):
        raise RuntimeError(f"robots.txt on {ZIMLII_BASE_URL} disallows crawling {listing_path} - stopping without fetching anything further.")
    all_documents = {}
    page = 1
    while max_pages is None or page <= max_pages:
        url = f"{ZIMLII_BASE_URL}{listing_path}" + (f"?page={page}" if page > 1 else "")
        log(f"Fetching {instrument_type} listing page {page} ({url}) ...")
        resp = _fetch(url)
        if resp.status_code != 200:
            log(f"  stopped - HTTP {resp.status_code}")
            break
        page_documents = _parse_listing_page(resp.text, instrument_type)
        if not page_documents:
            log("  no documents found on this page - assuming this is past the end of the listing.")
            break
        new_count = 0
        for d in page_documents:
            if d["work_uri"] not in all_documents:
                all_documents[d["work_uri"]] = d
                new_count += 1
        log(f"  found {len(page_documents)} document(s) on this page, {new_count} not seen on an earlier page.")
        if new_count == 0:
            log("  every document on this page was already seen - assuming pagination has looped back to the start.")
            break
        page += 1
        time.sleep(sleep_seconds)
    return list(all_documents.values())


def list_candidates(include_acts=True, include_subsidiary=True, max_pages=None, sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """List every document across the requested listing(s), classified
    against CATEGORY_KEYWORDS, keeping only those that matched at least one
    target area. Returns a list of {"title", "url", "work_uri",
    "instrument_type", "areas": [area, ...], "matches": [(area, keyword), ...]}."""
    documents = []
    if include_acts:
        documents += list_all_documents("Act", max_pages=max_pages, sleep_seconds=sleep_seconds, log=log)
    if include_subsidiary:
        documents += list_all_documents("SI", max_pages=max_pages, sleep_seconds=sleep_seconds, log=log)

    candidates = []
    for d in documents:
        matches = classify_title(d["title"])
        if not matches:
            continue
        d = dict(d)
        d["matches"] = matches
        d["areas"] = sorted({area for area, _ in matches})
        candidates.append(d)
    return candidates


def _extract_document_text(html):
    """Pull the actual legislative text out of a ZimLII document page,
    stripping navigation/scripts/styles - defensive about exactly which
    element holds the content (see module docstring: this was never tested
    against the live site's raw HTML from this build environment), trying
    the most specific container first and falling back to the whole body
    rather than failing outright."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    content = (
        soup.find(class_=re.compile(r"^akn"))
        or soup.find("article")
        or soup.find("main")
        or soup.body
        or soup
    )
    text = content.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _existing_source_references():
    return {
        row.source_reference for row in
        db.session.query(LegislativeUpdate.source_reference).filter(LegislativeUpdate.source_reference.isnot(None)).all()
    }


def import_documents(include_acts=True, include_subsidiary=True, dry_run=False, max_pages=None, limit=None,
                      sleep_seconds=DEFAULT_SLEEP_SECONDS, log=print):
    """The actual bulk import. Lists every current Act and/or subsidiary
    instrument on ZimLII matching a target category, works out which aren't
    already on file (matched by the "ZimLII <work URI>" tag this stamps into
    source_reference), and - unless dry_run - fetches and files each
    remaining one: extracts its text straight from the rendered page (see
    _extract_document_text), saves that text as the "filed instrument", and
    runs it through the same AI summarisation as any other filed instrument
    (legislation_summary.py). One document failing (a bad fetch, an
    unreadable page) is logged and skipped rather than stopping the whole
    run."""
    candidates = list_candidates(include_acts=include_acts, include_subsidiary=include_subsidiary,
                                  max_pages=max_pages, sleep_seconds=sleep_seconds, log=log)
    already = _existing_source_references()
    to_import = []
    for d in candidates:
        source_reference = f"ZimLII {d['work_uri']}"
        if source_reference in already:
            continue
        d["source_reference"] = source_reference
        to_import.append(d)
    if limit is not None:
        to_import = to_import[:limit]

    log(f"\n{len(candidates)} document(s) matched a target category, {len(to_import)} not yet imported"
        f"{f' (showing up to {limit})' if limit is not None else ''}.")

    if dry_run:
        log("--dry-run: nothing will be fetched or saved. Titles that WOULD be imported:")
        for d in to_import:
            reasons = ", ".join(f"{area} (matched '{kw}')" for area, kw in d["matches"])
            log(f"  - [{d['instrument_type']}] {d['title']}")
            log(f"      {reasons}")
            log(f"      {d['url']}")
        return {"found": len(candidates), "would_import": len(to_import)}

    admin_user = User.query.filter_by(role="admin").order_by(User.id).first()
    imported, failed = 0, 0
    for i, d in enumerate(to_import, start=1):
        log(f"[{i}/{len(to_import)}] [{d['instrument_type']}] {d['title']}")
        try:
            _import_one_document(d, created_by_id=admin_user.id if admin_user else None, log=log)
            imported += 1
        except Exception as exc:
            db.session.rollback()
            failed += 1
            log(f"  FAILED: {exc}")
        time.sleep(sleep_seconds)
    log(f"\nDone - {imported} imported, {failed} failed, {len(candidates) - len(to_import)} already on file from an earlier run.")
    return {"found": len(candidates), "imported": imported, "failed": failed}


def _import_one_document(document, created_by_id, log):
    resp = _fetch(document["url"])
    resp.raise_for_status()
    extracted_text = _extract_document_text(resp.text)
    if not extracted_text or len(extracted_text) < 100:
        raise ValueError("extracted text was empty or suspiciously short - the page structure may not match what this was written against")

    update = LegislativeUpdate(
        instrument_type=document["instrument_type"],
        title=document["title"],
        source_reference=document["source_reference"],
        summary=f"Imported from ZimLII ({document['url']}) - see the AI summary below once generated.",
        created_by_id=created_by_id,
    )
    update.set_areas(document["areas"])
    db.session.add(update)
    db.session.flush()  # assign update.id without committing yet

    slug = document["work_uri"].strip("/").replace("/", "_")
    original_name = f"{slug}.txt"
    stored_name = f"leg{update.id}_{uuid.uuid4().hex[:8]}_{original_name}"
    filepath = os.path.join(_legislative_updates_dir(), stored_name)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(extracted_text)
    update.original_filename = original_name
    update.stored_filename = stored_name
    update.extracted_text = extracted_text
    update.extraction_status = "extracted"
    db.session.commit()  # save the filed text itself before attempting the AI call, so a slow/failed AI step never loses it

    from datetime import datetime
    skip_reason = legislation_summary.skip_summary_reason(document["instrument_type"])
    if skip_reason:
        # Full Acts aren't auto-summarised - see legislation_summary.skip_summary_reason.
        # Subsidiary legislation (SIs) still gets the normal AI summary below.
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
