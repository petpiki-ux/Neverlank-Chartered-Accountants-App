"""Automated sanctions/adverse-notice screening support.

Backs the "Auto-screen" actions on Client Acceptance's Sanctions & Adverse
Notice Screening card (see acceptance.py and models.SanctionsScreening):

  - UN, OFAC and EU each publish a free, official, machine-readable list
    (no account/API key needed - OFAC just requires a User-Agent header,
    see REQUEST_HEADERS below). fetch_un_entries/fetch_ofac_entries/
    fetch_eu_entries pull those down, refresh_source/refresh_all_sources
    cache the result in SanctionsWatchlistEntry (replacing the previous
    cache for that source wholesale), and find_matches does the actual
    name comparison against that cache.
  - RBZ and FIU Zimbabwe publish nothing of the kind (RBZ only posts
    occasional PDF "Public Notices"; FIU Zimbabwe has no searchable list at
    all) - so instead the firm uploads those PDFs to the Regulatory Notices
    library (see regulatory_notices.py and models.RegulatoryNotice).
    extract_pdf_text pulls the selectable text out of each PDF on upload,
    and search_notices_for_name searches that text for a name match in
    place of a live feed.

Matching is deliberately conservative throughout this module: it only ever
returns "no match" or "potential match" - it never claims a confirmed hit.
A potential match always needs a human to actually look at it and decide,
via the existing manual Confirmed Hit / Clear dropdown on each row; nothing
in this module writes that far. See acceptance.auto_screen_individual for
where these functions' results get applied to a SanctionsScreening row.

A note on the three feeds' exact XML shapes: this sandbox's outbound network
access is restricted to an allowlist that doesn't include un.org, treas.gov
or europa.eu, so the parsers below are written from each feed's documented/
widely-referenced schema rather than a live sample, and written defensively
(matching by local element name regardless of namespace, skipping anything
that doesn't parse rather than crashing the whole fetch) so a small schema
drift degrades to "0 matches from that field" rather than an exception.
Validate against real data once this is deployed somewhere with normal
outbound internet access (e.g. Render) - refresh_source's last_error is
surfaced on the Sanctions & Adverse Notice Screening card if a feed's shape
has changed enough to break the parse entirely.
"""
import re
import difflib
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import pdfplumber

from extensions import db
from models import SanctionsWatchlistEntry, SanctionsListStatus, SANCTIONS_AUTO_SOURCES

UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
OFAC_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
# The token in this URL is not a private credential - it's the long-standing
# public token the EU's own Financial Sanctions Files service expects on
# every request to this endpoint (documented as such by multiple open-source
# sanctions-data projects), not something issued per-account.
EU_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"

# OFAC's list service returns 403 to any request without a User-Agent header
# identifying the client; sending one satisfies UN's and EU's endpoints too.
REQUEST_HEADERS = {
    "User-Agent": "NeverlankAuditApp-SanctionsScreening/1.0 (+https://www.neverlank.co.zw)",
}
REQUEST_TIMEOUT = 45  # seconds - these are large government/EU publications

# A match at or above this SequenceMatcher ratio is surfaced as a "Potential
# Match" for a human to review. Deliberately conservative/moderate rather
# than requiring near-exact equality, on the theory that a screening tool
# that stays silent on a real match is far worse than one that occasionally
# asks a reviewer to rule out a false positive - but see the module
# docstring: this NEVER escalates itself to "Confirmed Hit".
POTENTIAL_MATCH_THRESHOLD = 0.82


def _normalize_name(name):
    """Upper-case, strip punctuation, collapse whitespace - so "O'Brien,
    John" and "JOHN O BRIEN" compare sensibly against each other."""
    if not name:
        return ""
    name = name.upper()
    name = re.sub(r"[^A-Z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _local(tag):
    """Strip a `{namespace}` prefix off an ElementTree tag, if present, so
    the parsers below can match elements by name alone regardless of which
    (or whether any) XML namespace a feed declares."""
    return tag.split("}")[-1] if "}" in tag else tag


def _child_text(elem, *names):
    """Text of the first direct child of `elem` matching `names[0]`; if
    none of those has non-empty text, falls back to `names[1]`, and so on -
    e.g. _child_text(elem, "REFERENCE_NUMBER", "DATAID") prefers a
    REFERENCE_NUMBER child over a DATAID child even if DATAID happens to
    appear first in the document. Returns None if nothing matches."""
    for name in names:
        for child in elem:
            if _local(child.tag) == name and (child.text or "").strip():
                return child.text.strip()
    return None


# ---------- Fetch + parse each source's feed ----------

def fetch_un_entries():
    """UN Security Council Consolidated List. Documented schema:
    <CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><FIRST_NAME/><SECOND_NAME/>
    <THIRD_NAME/><FOURTH_NAME/><REFERENCE_NUMBER/><UN_LIST_TYPE/>
    <INDIVIDUAL_ALIAS><ALIAS_NAME/></INDIVIDUAL_ALIAS>...</INDIVIDUAL>
    </INDIVIDUALS><ENTITIES><ENTITY><NAME1/>..<NAME6/>...</ENTITY>
    </ENTITIES></CONSOLIDATED_LIST>."""
    resp = requests.get(UN_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    entries = []
    for elem in root.iter():
        tag = _local(elem.tag)
        if tag not in ("INDIVIDUAL", "ENTITY"):
            continue
        try:
            if tag == "INDIVIDUAL":
                parts = [_child_text(elem, n) for n in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
                name = " ".join(p for p in parts if p)
            else:
                parts = [_child_text(elem, f"NAME{i}") for i in range(1, 7)]
                name = " ".join(p for p in parts if p) or (_child_text(elem, "FIRST_NAME") or "")
            if not name:
                continue
            aliases = []
            for alias_group in elem:
                if _local(alias_group.tag) in ("INDIVIDUAL_ALIAS", "ENTITY_ALIAS"):
                    alias_name = _child_text(alias_group, "ALIAS_NAME")
                    if alias_name:
                        aliases.append(alias_name)
            entries.append({
                "name": name,
                "aliases": "\n".join(aliases),
                "reference": _child_text(elem, "REFERENCE_NUMBER", "DATAID"),
                "programme": _child_text(elem, "UN_LIST_TYPE"),
            })
        except Exception:
            continue  # one malformed entry shouldn't sink the whole refresh
    return entries


def fetch_ofac_entries():
    """OFAC Sanctions List Service, SDN export. Documented schema:
    <sdnList><sdnEntry><uid/><lastName/><firstName/><sdnType/>
    <programList><program/></programList><akaList><aka><lastName/>
    <firstName/></aka></akaList></sdnEntry>...</sdnList>."""
    resp = requests.get(OFAC_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    entries = []
    for elem in root.iter():
        if _local(elem.tag) != "sdnEntry":
            continue
        try:
            last = _child_text(elem, "lastName")
            first = _child_text(elem, "firstName")
            name = " ".join(p for p in (first, last) if p)
            if not name:
                continue
            aliases = []
            programmes = []
            for child in elem:
                child_tag = _local(child.tag)
                if child_tag == "akaList":
                    for aka in child:
                        if _local(aka.tag) != "aka":
                            continue
                        aka_name = " ".join(p for p in (_child_text(aka, "firstName"), _child_text(aka, "lastName")) if p)
                        if aka_name:
                            aliases.append(aka_name)
                elif child_tag == "programList":
                    for prog in child:
                        if _local(prog.tag) == "program" and (prog.text or "").strip():
                            programmes.append(prog.text.strip())
            entries.append({
                "name": name,
                "aliases": "\n".join(aliases),
                "reference": _child_text(elem, "uid"),
                "programme": ", ".join(programmes) or None,
            })
        except Exception:
            continue
    return entries


def fetch_eu_entries():
    """EU Financial Sanctions Files (FSF), full list export. Documented
    schema (per the widely-mirrored eu_fsf dataset format): <export>
    <sanctionEntity logicalId="..."><nameAlias wholeName="..." firstName="..."
    lastName="..."/>...<regulation programmeType="..." numberTitle="..."/>
    </sanctionEntity>...</export>. The EU doesn't publish its schema as
    plainly as UN/OFAC do, so this is the least certain of the three - see
    the module docstring."""
    resp = requests.get(EU_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    entries = []
    for elem in root.iter():
        if _local(elem.tag) != "sanctionEntity":
            continue
        try:
            names = []
            programmes = []
            for child in elem:
                child_tag = _local(child.tag)
                if child_tag == "nameAlias":
                    whole = (child.attrib.get("wholeName") or "").strip()
                    if whole:
                        names.append(whole)
                    else:
                        combined = " ".join(p for p in (child.attrib.get("firstName", ""), child.attrib.get("lastName", "")) if p).strip()
                        if combined:
                            names.append(combined)
                elif child_tag == "regulation":
                    prog = child.attrib.get("programmeType") or child.attrib.get("numberTitle")
                    if prog:
                        programmes.append(prog)
            if not names:
                continue
            entries.append({
                "name": names[0],
                "aliases": "\n".join(dict.fromkeys(names[1:])),
                "reference": elem.attrib.get("logicalId") or elem.attrib.get("euReferenceNumber"),
                "programme": ", ".join(dict.fromkeys(programmes)) or None,
            })
        except Exception:
            continue
    return entries


FETCHERS = {"UN": fetch_un_entries, "OFAC": fetch_ofac_entries, "EU": fetch_eu_entries}


# ---------- Cache refresh ----------

def _get_or_create_status(source):
    status = SanctionsListStatus.query.filter_by(source=source).first()
    if not status:
        status = SanctionsListStatus(source=source)
        db.session.add(status)
        db.session.commit()
    return status


def refresh_source(source, user_id=None):
    """Fetch and re-cache one SANCTIONS_AUTO_SOURCES source. On success,
    replaces every existing SanctionsWatchlistEntry row for that source with
    the freshly fetched set and updates SanctionsListStatus (entry_count,
    last_refreshed_at, clears last_error). On failure, leaves the existing
    cache exactly as it was - screening keeps working off the last good
    refresh rather than being wiped by a transient network error - and
    records the error on SanctionsListStatus.last_error instead. Never
    raises: refresh_all_sources and the /acceptance/lists/refresh route both
    rely on that so one source's failure can't block the other two.
    Returns (ok, entry_count, error)."""
    status = _get_or_create_status(source)
    try:
        entries = FETCHERS[source]()
        if not entries:
            raise ValueError("The feed returned no usable entries - leaving the existing cache in place rather than wiping it.")
        SanctionsWatchlistEntry.query.filter_by(source=source).delete()
        now = datetime.utcnow()
        for e in entries:
            db.session.add(SanctionsWatchlistEntry(
                source=source,
                name=e["name"][:300],
                aliases=(e.get("aliases") or None),
                reference=((e.get("reference") or "")[:150] or None),
                programme=((e.get("programme") or "")[:300] or None),
                fetched_at=now,
            ))
        status.last_refreshed_at = now
        status.entry_count = len(entries)
        status.last_error = None
        status.last_refreshed_by_id = user_id
        db.session.commit()
        return True, len(entries), None
    except Exception as exc:
        db.session.rollback()
        status = _get_or_create_status(source)
        status.last_error = str(exc)[:2000]
        status.last_refreshed_by_id = user_id
        db.session.commit()
        return False, 0, str(exc)


def refresh_all_sources(user_id=None):
    """Refresh every SANCTIONS_AUTO_SOURCES source in turn. Returns
    {source: (ok, entry_count, error)}."""
    return {source: refresh_source(source, user_id=user_id) for source in SANCTIONS_AUTO_SOURCES}


# ---------- Matching ----------

def _name_candidates(entry):
    candidates = [entry.name]
    if entry.aliases:
        candidates.extend(a for a in entry.aliases.split("\n") if a.strip())
    return candidates


def find_matches(query_name, source, threshold=POTENTIAL_MATCH_THRESHOLD, limit=3):
    """Compare query_name against every cached SanctionsWatchlistEntry for
    `source` (and its known aliases), using difflib.SequenceMatcher on
    normalized names. Returns up to `limit` matches at or above `threshold`,
    highest similarity first - an empty list means no plausible match was
    found in the cache, NOT that the individual is confirmed clear against
    the live list (the cache is only as current as its last successful
    refresh - see SanctionsListStatus)."""
    query_norm = _normalize_name(query_name)
    if not query_norm:
        return []
    results = []
    for entry in SanctionsWatchlistEntry.query.filter_by(source=source).all():
        best_ratio, best_candidate = 0.0, entry.name
        for candidate in _name_candidates(entry):
            cand_norm = _normalize_name(candidate)
            if not cand_norm:
                continue
            ratio = difflib.SequenceMatcher(None, query_norm, cand_norm).ratio()
            if ratio > best_ratio:
                best_ratio, best_candidate = ratio, candidate
        if best_ratio >= threshold:
            results.append({
                "name": entry.name, "matched_as": best_candidate, "score": best_ratio,
                "reference": entry.reference, "programme": entry.programme,
            })
    results.sort(key=lambda m: m["score"], reverse=True)
    return results[:limit]


# ---------- RBZ/FIU uploaded-notice PDFs ----------

def extract_pdf_text(filepath):
    """Extract selectable text from a PDF, page by page, for a
    RegulatoryNotice on upload. Returns (text, status, page_count):
      - "extracted": at least one page had selectable text
      - "no_text_found": the PDF opened fine but no page had any (typically
        a scanned/image-only notice) - it's kept and listed, just not
        searched automatically; add a manual note to it instead
      - "error": the file couldn't be opened/parsed as a PDF at all
    Deliberately doesn't fall back to OCR - this app has no dependency on a
    system `tesseract` binary being present on the hosting platform, so a
    scanned notice degrades gracefully rather than the whole upload failing
    or silently depending on something that may not be installed there."""
    try:
        pages_text = []
        with pdfplumber.open(filepath) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
        full_text = "\n".join(pages_text)
        if full_text.strip():
            return full_text, "extracted", page_count
        return "", "no_text_found", page_count
    except Exception:
        return "", "error", None


def search_notices_for_name(query_name, notices, threshold=POTENTIAL_MATCH_THRESHOLD, context_chars=160):
    """Conservative line-by-line search of `notices` (a list of
    RegulatoryNotice rows) for a plausible mention of query_name - same
    Clear/Potential-Match-only philosophy as find_matches above (see the
    module docstring): never claims a confirmed hit. A notice whose text
    wasn't successfully extracted is skipped entirely rather than counted
    either way, since there's genuinely nothing to check it against.
    Returns a list of {"notice", "score", "excerpt"}, highest first."""
    query_norm = _normalize_name(query_name)
    if not query_norm:
        return []
    query_tokens = set(query_norm.split())
    results = []
    for notice in notices:
        if notice.extraction_status != "extracted" or not notice.extracted_text:
            continue
        best_ratio, best_line = 0.0, None
        for line in notice.extracted_text.splitlines():
            line_norm = _normalize_name(line)
            if not line_norm:
                continue
            line_tokens = set(line_norm.split())
            if query_tokens and not (query_tokens & line_tokens):
                continue  # cheap pre-filter before the more expensive ratio() below
            ratio = difflib.SequenceMatcher(None, query_norm, line_norm).ratio()
            if line_tokens:
                # A long line diluted by surrounding text can understate a
                # real name match - also try just the words shared with the
                # query, in the line's own order, and keep whichever's higher.
                overlap = " ".join(w for w in line_norm.split() if w in query_tokens)
                ratio = max(ratio, difflib.SequenceMatcher(None, query_norm, overlap).ratio())
            if ratio > best_ratio:
                best_ratio, best_line = ratio, line.strip()
        if best_ratio >= threshold:
            results.append({"notice": notice, "score": best_ratio, "excerpt": (best_line or "")[:context_chars]})
    results.sort(key=lambda m: m["score"], reverse=True)
    return results
