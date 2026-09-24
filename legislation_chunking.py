"""Chunking of a filed legislative instrument's full extracted text into
retrievable sections (see models.LegislativeUpdateChunk), so "Ask the
library" (legislation_search.py) can pull the specific relevant part of a
long Act rather than being limited to a fixed-size excerpt from the start of
the document.

Deliberately dependency-free, like everything else in the legislative
library: no NLP/tokenizer library, just line-based heading detection and a
target character count per chunk. This needs to run identically whether the
app is on Render or packaged as a local .exe (see config.py's
_is_frozen()), which is also why there's no vector database or embeddings
involved anywhere in this feature - see legislation_search.py's own module
docstring for the same point.

Chunking does NOT happen automatically for every already-filed document up
front - see ensure_chunks() below, called lazily wherever chunks are
actually needed (right after filing/importing a new instrument, and as a
one-time-per-document backfill from legislation_search._ensure_all_chunks()
for anything filed before this existed)."""
import re

from models import db, LegislativeUpdateChunk

TARGET_CHUNK_CHARS = 2500  # roughly a page and a half - big enough for real context, small enough to keep several chunks' worth of prompt affordable
MIN_CHUNK_CHARS = 400  # a fragment shorter than this is folded into a neighbouring chunk rather than left as its own tiny, low-context chunk

# Matches a line that looks like a Part/Chapter heading ("PART IV",
# "CHAPTER II - REGISTRATION") or a numbered section heading ("12. Rate of
# tax"). Deliberately conservative (anchored to the whole line) so that
# ordinary body text mentioning "part" or a numbered list item in a
# schedule doesn't get misread as a structural heading.
_HEADING_RE = re.compile(
    r"^(PART\s+[IVXLCDM0-9]+\b.{0,100}|CHAPTER\s+[IVXLCDM0-9]+\b.{0,100}|\d{1,3}\.\s+[A-Z][^\n]{0,120})$"
)


def _detect_heading(line):
    """Returns the line itself (trimmed) if it looks like a structural
    heading, else None."""
    stripped = line.strip()
    if not stripped or len(stripped) > 160:
        return None
    return stripped if _HEADING_RE.match(stripped) else None


def chunk_text(text, target_chars=TARGET_CHUNK_CHARS):
    """Split `text` into a list of (heading, chunk_text) tuples, each
    roughly `target_chars` long. `heading` is the most recent heading-like
    line seen at or before that chunk started (None if none has been seen
    yet). Always returns at least one chunk for non-empty text - a short
    document with no detected headings simply comes back as a single chunk
    with heading=None. Returns [] for empty/blank text."""
    text = (text or "").strip()
    if not text:
        return []

    lines = text.splitlines()
    chunks = []
    current_heading = None
    buf_heading = None
    buf_lines = []
    buf_len = 0

    def flush():
        nonlocal buf_lines, buf_len
        if not buf_lines:
            return
        body = "\n".join(buf_lines).strip()
        if body:
            chunks.append((buf_heading, body))
        buf_lines = []
        buf_len = 0

    for line in lines:
        heading = _detect_heading(line)
        if heading:
            current_heading = heading
            # Start a new chunk at a heading, unless what's buffered so far
            # is tiny - avoids a flurry of near-empty chunks for
            # closely-spaced headings (e.g. a table of contents listing
            # every Part on its own line). flush() (if it runs) uses
            # whatever buf_heading already was, i.e. the heading in effect
            # BEFORE this one - correct for the buffer being closed out.
            # Only afterwards does buf_heading move on to this new heading,
            # so a chunk spanning several closely-spaced headings (because
            # none of them individually justified a flush) ends up labelled
            # by the last, most specific one rather than whichever heading
            # happened to be in effect when the buffer started.
            if buf_len >= MIN_CHUNK_CHARS:
                flush()
            buf_heading = current_heading
        buf_lines.append(line)
        buf_len += len(line) + 1
        if buf_len >= target_chars:
            flush()
    flush()

    # Fold a too-short trailing fragment (e.g. a schedule's last few lines,
    # or a short final Part) into the previous chunk rather than leaving it
    # as its own chunk. If the folded-in fragment introduced its own
    # heading, that heading wins for the merged chunk - it describes the
    # more specific/recent section, which is what actually dominates the
    # tail end of the merged text.
    if len(chunks) > 1 and len(chunks[-1][1]) < MIN_CHUNK_CHARS:
        last_heading, last_body = chunks.pop()
        prev_heading, prev_body = chunks.pop()
        chunks.append((last_heading or prev_heading, prev_body + "\n" + last_body))

    return chunks


def ensure_chunks(update):
    """Make sure `update` (a models.LegislativeUpdate) has chunk rows for
    its current extracted_text, chunking it now if it has none yet. No-ops
    if there's no extracted text, or if chunks already exist - chunking
    never re-runs automatically just because it's asked for again (nothing
    currently changes extracted_text after it's first set, so there's
    nothing to catch up on).

    Does not commit - the caller commits, same convention as everywhere
    else that files/imports a legislative update."""
    if not update.extracted_text:
        return
    if update.chunks:
        return
    for index, (heading, body) in enumerate(chunk_text(update.extracted_text)):
        db.session.add(LegislativeUpdateChunk(
            legislative_update_id=update.id,
            chunk_index=index,
            heading=(heading[:300] if heading else None),
            text=body,
        ))
