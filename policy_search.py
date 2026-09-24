""""Ask the Firm Library" - a plain-English question answered from the
firm's own Policies & Procedures library (see models.PolicyDocument), rather
than the model's general knowledge. The Firm Library research hub's
counterpart to legislation_search.py ("Ask the library"), following exactly
the same design and for the same reasons (see that module's own docstring
for the full rationale) - deliberately simple and dependency-free (no SQLite
FTS extension, no vector database, so this runs identically whether the app
is on Render or packaged as a local .exe per config.py's _is_frozen()):

  1. A fast-recall cache (models.PolicyAskCache) is checked first, by exact
     then fuzzy (difflib) match against the question actually asked. A hit
     returns instantly, with no search and no AI call at all, and is always
     visibly flagged as a reused answer.
  2. search_candidate_chunks() - a plain Python keyword-overlap scan across
     every filed document's CHUNKS (models.PolicyDocumentChunk - see
     policy_chunking.py), falling back to the whole document's own
     searchable text for anything with no chunks at all.
  3. ask() - only calls the AI once real keyword matches exist. Hands the
     top-matching chunks (with their parent document's summary/key points)
     to Claude and asks it to answer using ONLY that material, citing
     exactly which filed document(s) it drew on. If the material doesn't
     actually address the question, the model says so explicitly
     (found_answer=False) rather than guessing.

Every result is purely informational: it never files anything or changes
any record - a person reads the answer and its cited sources and judges
them."""
import os
import re
import json
import difflib
from datetime import datetime

from extensions import db
from models import PolicyDocument, PolicyAskCache

import policy_chunking

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90
MAX_CANDIDATES = 8  # how many filed documents get handed to the model as context
EXCERPT_CHARS = 3000  # per-chunk slice included in the prompt
FUZZY_MATCH_THRESHOLD = 0.88  # difflib ratio above which a cached question is treated as "the same question" for recall purposes

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "and", "or", "but",
    "of", "in", "on", "at", "to", "for", "with", "about", "as", "by", "from", "into", "than",
    "what", "when", "where", "who", "whom", "which", "why", "how", "does", "did", "do", "can",
    "could", "should", "would", "will", "shall", "may", "might", "must", "has", "have", "had",
    "this", "that", "these", "those", "it", "its", "any", "all", "there", "our", "we", "you",
    "current", "currently", "now", "please", "tell", "know", "there's", "whats", "policy",
    "policies", "procedure", "procedures",
}
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'\-]{2,}")


def _tokenize(text):
    return [w.lower() for w in _WORD_RE.findall(text or "") if w.lower() not in _STOPWORDS]


def _searchable_blob(policy):
    """Whole-document searchable text - used for a document with no chunks
    at all (e.g. no extracted text)."""
    parts = [
        policy.title or "",
        policy.category or "",
        policy.description or "",
        policy.ai_summary or "",
        " ".join(policy.ai_key_points or []),
        (policy.extracted_text or "")[:8000],
    ]
    return " ".join(parts).lower()


def _ensure_all_chunks():
    """One-time-per-document lazy backfill: chunk every filed policy that
    has extracted text but no chunks yet. Pure Python string work, no
    network or AI calls, so this is safe to call at the start of every
    search; it does real work only the first time it sees each document."""
    changed = False
    for policy in PolicyDocument.query.filter(PolicyDocument.extracted_text.isnot(None)).all():
        if policy.extracted_text and not policy.chunks:
            policy_chunking.ensure_chunks(policy)
            changed = True
    if changed:
        db.session.commit()


def search_candidate_chunks(question, limit=MAX_CANDIDATES):
    """Score every filed policy's individual chunks (falling back to the
    whole-document blob for anything with no chunks) against the question's
    keywords, and return the top-scoring (policy, chunk) pairs, best first.
    `chunk` is None when the match came from the whole-document fallback.
    Returns [] if nothing filed shares any keyword with the question."""
    tokens = set(_tokenize(question))
    if not tokens:
        return []
    _ensure_all_chunks()
    scored = []
    for policy in PolicyDocument.query.all():
        title_tokens = set(_tokenize(policy.title or ""))
        if policy.chunks:
            for chunk in policy.chunks:
                blob = " ".join([policy.title or "", policy.category or "", chunk.heading or "", chunk.text]).lower()
                blob_tokens = set(_WORD_RE.findall(blob))
                score = sum(2 if t in title_tokens else 1 for t in tokens if t in blob_tokens)
                if score > 0:
                    scored.append((score, policy, chunk))
        else:
            blob_tokens = set(_WORD_RE.findall(_searchable_blob(policy)))
            score = sum(2 if t in title_tokens else 1 for t in tokens if t in blob_tokens)
            if score > 0:
                scored.append((score, policy, None))
    scored.sort(key=lambda triple: triple[0], reverse=True)
    return [(policy, chunk) for _, policy, chunk in scored[:limit]]


def search_candidates(question, limit=MAX_CANDIDATES):
    """Document-level view over search_candidate_chunks(): the top-matching
    filed policies, deduplicated, in best-match order. Returns [] if nothing
    filed shares any keyword with the question at all."""
    seen_ids = set()
    result = []
    for policy, _chunk in search_candidate_chunks(question, limit=limit * 3):
        if policy.id not in seen_ids:
            seen_ids.add(policy.id)
            result.append(policy)
        if len(result) >= limit:
            break
    return result


ANSWER_TOOL = {
    "name": "answer_policy_question",
    "description": "Answer a staff member's question using only the filed Firm Library excerpts provided, citing which ones were used.",
    "input_schema": {
        "type": "object",
        "properties": {
            "found_answer": {
                "type": "boolean",
                "description": "True only if the provided excerpts actually contain enough to answer the question. False if they're only tangentially related, or don't cover it.",
            },
            "answer": {
                "type": "string",
                "description": "The answer, written plainly for a member of staff, drawn ONLY from the excerpts provided - never from general knowledge. If found_answer is false, a brief, honest note that the Firm Library doesn't appear to cover this, rather than a guess.",
            },
            "citation_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "The bracketed [Document #N] id(s) actually drawn on for the answer. Empty if found_answer is false.",
            },
        },
        "required": ["found_answer", "answer", "citation_ids"],
    },
}

SYSTEM_PROMPT = (
    "You are answering a question for staff at a Zimbabwean audit, tax and accounting firm, using ONLY the "
    "excerpts below from their own filed Policies & Procedures library (the Firm Library). Do not use any "
    "outside knowledge of what a typical firm's policies say, even if you believe you know the answer - the "
    "firm relies on this being grounded only in what they've actually filed. If the excerpts don't clearly "
    "answer the question, set found_answer to false and say so plainly rather than guessing or filling in from "
    "general knowledge. Always cite the specific [Document #N] id(s) you actually drew on. Keep the answer "
    "concise and practical."
)


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


def _candidate_block(policy, chunk=None):
    lines = [
        f"[Document #{policy.id}] {policy.title}",
        f"Category: {policy.category}",
    ]
    if policy.description:
        lines.append(f"Description: {policy.description}")
    summary = policy.ai_summary
    if summary:
        lines.append(f"Summary: {summary}")
    if policy.ai_key_points:
        lines.append("Key points: " + "; ".join(policy.ai_key_points))
    if chunk is not None:
        heading_note = f" ({chunk.heading})" if chunk.heading else ""
        lines.append(f"Relevant excerpt from the filed document{heading_note}: {chunk.text[:EXCERPT_CHARS]}")
    elif policy.extracted_text:
        lines.append(f"Excerpt from the filed document: {policy.extracted_text[:EXCERPT_CHARS]}")
    return "\n".join(lines)


# ---------------------------------------------------------------- fast-recall cache

def _normalize_question(question):
    return " ".join((question or "").strip().lower().split())


def _cache_lookup(question):
    """Exact match first, then a fuzzy (difflib) match against every cached
    question. Returns the matched PolicyAskCache row (with its hit_count
    bumped and committed) or None."""
    normalized = _normalize_question(question)
    if not normalized:
        return None
    row = (
        PolicyAskCache.query.filter_by(question_normalized=normalized)
        .order_by(PolicyAskCache.created_at.desc())
        .first()
    )
    if row is None:
        best_ratio = 0.0
        for candidate in PolicyAskCache.query.all():
            ratio = difflib.SequenceMatcher(None, normalized, candidate.question_normalized).ratio()
            if ratio > best_ratio:
                best_ratio, row = ratio, candidate
        if best_ratio < FUZZY_MATCH_THRESHOLD:
            row = None
    if row is not None:
        row.hit_count = (row.hit_count or 1) + 1
        row.last_hit_at = datetime.utcnow()
        db.session.commit()
    return row


def _row_to_payload(row):
    citation_ids = json.loads(row.citation_ids_json) if row.citation_ids_json else []
    candidate_ids = json.loads(row.candidate_ids_json) if row.candidate_ids_json else []
    all_ids = citation_ids + candidate_ids
    by_id = {p.id: p for p in PolicyDocument.query.filter(PolicyDocument.id.in_(all_ids)).all()} if all_ids else {}

    payload = {"answer": row.answer, "from_cache": True, "cache_hit_count": row.hit_count}
    if row.status == "done":
        payload["citations"] = [by_id[i] for i in citation_ids if i in by_id]
    else:
        payload["candidates"] = [by_id[i] for i in candidate_ids if i in by_id]
    return payload


def _store_cache(question, status, answer, citation_ids=None, candidate_ids=None, asked_by_id=None):
    normalized = _normalize_question(question)
    if not normalized:
        return
    row = PolicyAskCache(
        question_normalized=normalized,
        question_original=question[:500],
        status=status,
        answer=answer[:5000],
        citation_ids_json=json.dumps(list(citation_ids)) if citation_ids else None,
        candidate_ids_json=json.dumps(list(candidate_ids)) if candidate_ids else None,
        asked_by_id=asked_by_id,
        hit_count=1,
    )
    db.session.add(row)
    db.session.commit()


def ask(question, asked_by_id=None):
    """Answer `question` from the filed Firm Library.

    Returns (status, payload):
      - "not_configured": no ANTHROPIC_API_KEY set (and no cached answer
        already exists for this question). payload is None.
      - "no_candidates": nothing filed shares any keyword with the
        question. payload is None - nothing was sent to the model, and
        nothing is cached.
      - "no_answer": candidates were found but the model judged they don't
        actually answer the question. payload is
        {"answer": str, "candidates": [...], "from_cache": bool[, "cache_hit_count": int]}.
      - "done": payload is
        {"answer": str, "citations": [PolicyDocument, ...], "from_cache": bool[, "cache_hit_count": int]}.
      - "error": payload is a short error message string.
    A "done"/"no_answer" result is saved to the fast-recall cache
    (models.PolicyAskCache) and reused verbatim - always visibly flagged via
    payload["from_cache"] - for a close repeat of the same question; every
    other status is never cached. Never raises."""
    question = (question or "").strip()
    if not question:
        return "error", "Please enter a question."

    cached = _cache_lookup(question)
    if cached is not None:
        return cached.status, _row_to_payload(cached)

    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return "not_configured", None

    candidates_with_chunks = search_candidate_chunks(question)
    if not candidates_with_chunks:
        return "no_candidates", None

    seen_ids = set()
    distinct_candidates = []
    for policy, _chunk in candidates_with_chunks:
        if policy.id not in seen_ids:
            seen_ids.add(policy.id)
            distinct_candidates.append(policy)

    context = "\n\n---\n\n".join(_candidate_block(p, c) for p, c in candidates_with_chunks)
    user_message = (
        f"Excerpts from the firm's filed Policies & Procedures library:\n\n{context}\n\n---\n\n"
        f"Question: {question}"
    )

    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=[ANSWER_TOOL],
            tool_choice={"type": "tool", "name": "answer_policy_question"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        return "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "answer_policy_question":
            data = block.input or {}
            answer = (data.get("answer") or "").strip()
            if not answer:
                return "error", "The model didn't return an answer - try rephrasing the question."
            if not data.get("found_answer"):
                _store_cache(question, "no_answer", answer, candidate_ids=[p.id for p in distinct_candidates], asked_by_id=asked_by_id)
                return "no_answer", {"answer": answer, "candidates": distinct_candidates, "from_cache": False}
            cited_ids = {int(i) for i in (data.get("citation_ids") or []) if str(i).isdigit()}
            citations = [p for p in distinct_candidates if p.id in cited_ids] or distinct_candidates[:1]
            _store_cache(question, "done", answer, citation_ids=[p.id for p in citations], asked_by_id=asked_by_id)
            return "done", {"answer": answer, "citations": citations, "from_cache": False}
    return "error", "The model didn't return a structured result - try rephrasing the question."
