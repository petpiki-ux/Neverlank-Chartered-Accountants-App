""""Ask the library" - a plain-English question answered from the firm's own
Legislative Update Control library (see models.LegislativeUpdate), rather
than the model's general knowledge.

Two-stage design, deliberately simple and dependency-free (no SQLite FTS
extension, no vector database - this needs to run identically whether the
app is on Render or packaged as a local .exe per config.py's _is_frozen()):

  1. search_candidates() - a plain Python keyword-overlap scan across every
     filed update's title/summary/AI summary/key changes/practice areas/
     extracted text, entirely in this process. Cheap enough at a few hundred
     to a few thousand filed instruments, and never depends on the AI being
     configured at all - it's what lets the UI say "nothing filed addresses
     that" without ever calling the model.
  2. ask() - only called once real keyword matches exist. Hands the top
     candidates' summaries/key changes and a text excerpt to Claude (same
     tool-calling pattern as legislation_summary.py) and asks it to answer
     using ONLY that material, citing exactly which filed update(s) it drew
     on. If the candidates don't actually address the question, the model
     says so explicitly (found_answer=False) rather than guessing - this is
     legislation a real client's compliance may depend on, so a wrong but
     confident-sounding answer is worse than no answer.

Every result is purely informational, like every other AI-assisted feature
in this app: it never files anything or changes any record - a person reads
the answer and its cited sources and judges them, the same as with the
per-update AI summaries."""
import os
import re

from models import LegislativeUpdate

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90
MAX_CANDIDATES = 8  # how many filed updates get handed to the model as context
EXCERPT_CHARS = 3000  # per-candidate slice of extracted_text included in the prompt

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "and", "or", "but",
    "of", "in", "on", "at", "to", "for", "with", "about", "as", "by", "from", "into", "than",
    "what", "when", "where", "who", "whom", "which", "why", "how", "does", "did", "do", "can",
    "could", "should", "would", "will", "shall", "may", "might", "must", "has", "have", "had",
    "this", "that", "these", "those", "it", "its", "any", "all", "there", "our", "we", "you",
    "current", "currently", "now", "please", "tell", "know", "there's", "whats", "affect",
    "affects", "affected", "relevant", "notice", "notices", "update", "updates",
}
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'\-]{2,}")


def _tokenize(text):
    return [w.lower() for w in _WORD_RE.findall(text or "") if w.lower() not in _STOPWORDS]


def _searchable_blob(update):
    parts = [
        update.title or "",
        update.tax_head or "",
        update.type_label or "",
        update.source_reference or "",
        update.summary or "",
        update.ai_summary or "",
        " ".join(update.ai_key_changes or []),
        " ".join(update.areas or []),
        (update.extracted_text or "")[:8000],
    ]
    return " ".join(parts).lower()


def search_candidates(question, limit=MAX_CANDIDATES):
    """Rank every filed update by how many of the question's keywords it
    contains (title matches count double), and return the top-scoring ones.
    Returns an empty list if nothing filed shares any keyword with the
    question at all - the caller should treat that as "nothing to ask the
    model about" rather than forcing a guess."""
    tokens = set(_tokenize(question))
    if not tokens:
        return []
    updates = LegislativeUpdate.query.all()
    scored = []
    for update in updates:
        # Whole-word matching, not substring - a naive "is this token a
        # substring of the blob" check matches nonsense like "vat" inside
        # "private" or "act" inside "extracted". Tokenizing the blob the
        # same way as the question and comparing sets avoids that.
        title_tokens = set(_tokenize(update.title or ""))
        blob_tokens = set(_WORD_RE.findall(_searchable_blob(update)))
        score = 0
        for token in tokens:
            if token in blob_tokens:
                score += 2 if token in title_tokens else 1
        if score > 0:
            scored.append((score, update))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [update for _, update in scored[:limit]]


ANSWER_TOOL = {
    "name": "answer_legislation_question",
    "description": "Answer a practitioner's question using only the filed legislation excerpts provided, citing which ones were used.",
    "input_schema": {
        "type": "object",
        "properties": {
            "found_answer": {
                "type": "boolean",
                "description": "True only if the provided excerpts actually contain enough to answer the question. False if they're only tangentially related, or don't cover it.",
            },
            "answer": {
                "type": "string",
                "description": "The answer, written plainly for an audit/tax practitioner, drawn ONLY from the excerpts provided - never from general knowledge. If found_answer is false, a brief, honest note that the filed library doesn't appear to cover this, rather than a guess.",
            },
            "citation_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "The bracketed [Update #N] id(s) actually drawn on for the answer. Empty if found_answer is false.",
            },
        },
        "required": ["found_answer", "answer", "citation_ids"],
    },
}

SYSTEM_PROMPT = (
    "You are answering a question for staff at a Zimbabwean audit, tax and accounting firm, using "
    "ONLY the excerpts below from their own filed Legislative Update Control library (Acts, "
    "Government Notices and Statutory Instruments they have filed). Do not use any outside "
    "knowledge of Zimbabwean law, tax rates, thresholds, or dates beyond what is explicitly stated "
    "in these excerpts, even if you believe you know the answer - the firm relies on this being "
    "grounded only in what they've actually filed and reviewed. If the excerpts don't clearly "
    "answer the question, set found_answer to false and say so plainly rather than guessing or "
    "filling in from general knowledge. Always cite the specific [Update #N] id(s) you actually "
    "drew on. Keep the answer concise and practical."
)


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


def _candidate_block(update):
    lines = [
        f"[Update #{update.id}] {update.title}",
        f"Type: {update.type_label}" + (f" ({update.tax_head})" if update.tax_head else ""),
    ]
    if update.gazette_date:
        lines.append(f"Gazetted: {update.gazette_date.strftime('%d %b %Y')}")
    if update.effective_date:
        lines.append(f"Effective: {update.effective_date.strftime('%d %b %Y')}")
    if update.source_reference:
        lines.append(f"Source: {update.source_reference}")
    summary = update.ai_summary or update.summary
    if summary:
        lines.append(f"Summary: {summary}")
    if update.ai_key_changes:
        lines.append("Key changes: " + "; ".join(update.ai_key_changes))
    if update.extracted_text:
        lines.append(f"Excerpt from the filed document: {update.extracted_text[:EXCERPT_CHARS]}")
    return "\n".join(lines)


def ask(question):
    """Answer `question` from the filed legislative library.

    Returns (status, payload):
      - "not_configured": no ANTHROPIC_API_KEY set. payload is None.
      - "no_candidates": nothing filed shares any keyword with the question.
        payload is None - nothing was sent to the model.
      - "no_answer": candidates were found but the model judged they don't
        actually answer the question. payload is {"answer": str, "candidates": [...]}
        (candidates are the ones considered, for a person to check by hand).
      - "done": payload is {"answer": str, "citations": [LegislativeUpdate, ...]}
      - "error": payload is a short error message string.
    Never raises."""
    question = (question or "").strip()
    if not question:
        return "error", "Please enter a question."
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return "not_configured", None

    candidates = search_candidates(question)
    if not candidates:
        return "no_candidates", None

    context = "\n\n---\n\n".join(_candidate_block(u) for u in candidates)
    user_message = (
        f"Excerpts from the firm's filed Legislative Update Control library:\n\n{context}\n\n---\n\n"
        f"Question: {question}"
    )

    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=[ANSWER_TOOL],
            tool_choice={"type": "tool", "name": "answer_legislation_question"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        return "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "answer_legislation_question":
            data = block.input or {}
            answer = (data.get("answer") or "").strip()
            if not answer:
                return "error", "The model didn't return an answer - try rephrasing the question."
            if not data.get("found_answer"):
                return "no_answer", {"answer": answer, "candidates": candidates}
            cited_ids = {int(i) for i in (data.get("citation_ids") or []) if str(i).isdigit()}
            citations = [u for u in candidates if u.id in cited_ids] or candidates[:1]
            return "done", {"answer": answer, "citations": citations}
    return "error", "The model didn't return a structured result - try rephrasing the question."
