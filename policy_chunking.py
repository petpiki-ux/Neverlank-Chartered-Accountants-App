"""Chunking of a filed policy/procedure's full extracted text into
retrievable sections (see models.PolicyDocumentChunk), so "Ask the Firm
Library" (policy_search.py) can pull the specific relevant part of a long
policy rather than being limited to a fixed-size excerpt from the start of
the document.

The actual chunking algorithm is shared with the Legislative Update Control
library - see legislation_chunking.chunk_text() for the heading-detection
logic and the reasoning behind it (deliberately dependency-free: no NLP/
tokenizer library, so this runs identically whether the app is on Render or
packaged as a local .exe - see config.py's _is_frozen()). This module only
adapts that same chunk_text() to PolicyDocument/PolicyDocumentChunk instead
of LegislativeUpdate/LegislativeUpdateChunk.

Chunking does NOT happen automatically for every already-filed document up
front - see ensure_chunks() below, called lazily wherever chunks are
actually needed (right after filing/editing a policy with a new file, and as
a one-time-per-document backfill from policy_search._ensure_all_chunks() for
anything filed before this existed)."""
from models import db, PolicyDocumentChunk
from legislation_chunking import chunk_text  # noqa: F401 - re-exported for callers that just want the shared algorithm


def ensure_chunks(policy):
    """Make sure `policy` (a models.PolicyDocument) has chunk rows for its
    current extracted_text, chunking it now if it has none yet. No-ops if
    there's no extracted text, or if chunks already exist - chunking never
    re-runs automatically just because it's asked for again.

    Does not commit - the caller commits, same convention as
    legislation_chunking.ensure_chunks."""
    if not policy.extracted_text:
        return
    if policy.chunks:
        return
    for index, (heading, body) in enumerate(chunk_text(policy.extracted_text)):
        db.session.add(PolicyDocumentChunk(
            policy_document_id=policy.id,
            chunk_index=index,
            heading=(heading[:300] if heading else None),
            text=body,
        ))
