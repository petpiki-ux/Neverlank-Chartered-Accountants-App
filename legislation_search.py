""""Ask the library" - a plain-English question answered from the firm's own
Legislative Update Control library (see models.LegislativeUpdate), rather
than the model's general knowledge.

Design, deliberately simple and dependency-free (no SQLite FTS extension, no
vector database - this needs to run identically whether the app is on
Render or packaged as a local .exe per config.py's _is_frozen()):

  1. A fast-recall cache (models.LegislativeAskCache) is checked first, by
     exact then fuzzy (difflib) match against the question actually asked.
     A hit returns instantly, with no search and no AI call at all, and is
     always visibly flagged as a reused answer - see _row_to_payload(). This
     is a verbatim recall cache, not a shared knowledge base: a cached
     answer only ever gets reused for a close repeat of the SAME question,
     never blended into the answer to a different one.
  2. search_candidate_chunks() - a plain Python keyword-overlap scan across
     every filed update's CHUNKS (models.LegislativeUpdateChunk - see
     legislation_chunking.py), falling back to the whole document's own
     searchable text for anything with no chunks at all (e.g. no extracted
     text). Chunking is what lets this find a keyword buried deep inside a
     long Act, not just whatever appears in a fixed-size excerpt from its
     start. Cheap enough at a few hundred to a few thousand filed
     instruments, and never depends on the AI being configured at all - it's
     what lets the UI say "nothing filed addresses that" without ever
     calling the model. search_candidates() is a thin document-level view
     over the same search, for callers that just want the matching updates.
  3. ask() - only calls the AI once real keyword matches exist. Hands the
     top-matching chunks (with their parent update's summary/key changes)
     to Claude (same tool-calling pattern as legislation_summary.py) and
     asks it to answer using ONLY that material, citing exactly which filed
     update(s) it drew on. If the material doesn't actually address the
     question, the model says so explicitly (found_answer=False) rather
     than guessing - this is legislation a real client's compliance may
     depend on, so a wrong but confident-sounding answer is worse than no
     answer. A "done" or "no_answer" result is then saved to the cache for
     next time; a transient failure, an unconfigured key, or "nothing filed
     matches at all" never is, so those always retry fresh.

Every result is purely informational, like every other AI-assisted feature
in this app: it never files anything or changes any record - a person reads
the answer and its cited sources and judges them, the same as with the
per-update AI summaries."""
import os
import re
import json
import difflib
from datetime import datetime

from extensions import db
from models import LegislativeUpdate, LegislativeAskCache

import legislation_chunking
import legislation_index
import legislation_understand

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90
# The web server gives a request 120 seconds in all, so the AI is given less than that, and is never retried on a timeout (a retry would
# push the whole request over the limit and the person would see a raw network error instead of a clear message).
ASK_TIMEOUT = 75
ADVISE_TIMEOUT = 105
MAX_CANDIDATES = 10  # how many passages from the filed updates get handed to the model as context
EXCERPT_CHARS = 2200  # per-chunk slice included in the prompt
ADVISE_EXCERPT_CHARS = 1500  # advice reads many topics at once, so each passage is shorter
ADVISE_MAX_PASSAGES = 18
FUZZY_MATCH_THRESHOLD = 0.88  # difflib ratio above which a cached question is treated as "the same question" for recall purposes

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
    """Whole-document searchable text - used for a document with no chunks
    at all (e.g. a manually-logged entry with no filed instrument), and by
    search_candidates()' title-weighting."""
    parts = [
        update.title or "",
        update.tax_head or "",
        update.type_label or "",
        update.source_reference or "",
        update.case_citation or "",
        update.case_court or "",
        update.case_jurisdiction or "",
        update.summary or "",
        update.ai_summary or "",
        " ".join(update.ai_key_changes or []),
        " ".join(update.areas or []),
        (update.extracted_text or "")[:8000],
    ]
    return " ".join(parts).lower()


def _ensure_all_chunks():
    """One-time-per-document lazy backfill: chunk every filed update that
    has extracted text but no chunks yet - i.e. everything filed before
    chunk-based search existed. Pure Python string work, no network or AI
    calls, so this is safe to call at the start of every search; it does
    real work only the first time it sees each document."""
    changed = False
    for update in LegislativeUpdate.query.filter(LegislativeUpdate.extracted_text.isnot(None)).all():
        if update.extracted_text and not update.chunks:
            legislation_chunking.ensure_chunks(update)
            changed = True
    if changed:
        db.session.commit()


def search_candidate_chunks(question, limit=MAX_CANDIDATES):
    """The best (update, chunk) pairs in the filed library for a question or a longer description, best first. Uses the deep-search
    engine in legislation_index.py (BM25 ranking over every passage of every filed instrument, word-ending and vocabulary tolerance,
    typo tolerance, key-concept extraction for long descriptions). `chunk` is None when the match came from an update's front matter
    only (e.g. a text-only log entry). Returns [] if nothing filed shares any word with the question."""
    if not (question or "").strip():
        return []
    try:
        return legislation_index.rank_chunks(question, limit=limit)
    except Exception:
        db.session.rollback()
        return _search_candidate_chunks_simple(question, limit)


def _search_candidate_chunks_simple(question, limit=MAX_CANDIDATES):
    """The original exact-word scan, kept only as a safety net should the index ever fail."""
    tokens = set(_tokenize(question))
    if not tokens:
        return []
    _ensure_all_chunks()
    scored = []
    for update in LegislativeUpdate.query.all():
        title_tokens = set(_tokenize(update.title or ""))
        if update.chunks:
            for chunk in update.chunks:
                blob = " ".join([update.title or "", update.tax_head or "", update.type_label or "", update.case_citation or "", chunk.heading or "", chunk.text]).lower()
                blob_tokens = set(_WORD_RE.findall(blob))
                score = sum(2 if t in title_tokens else 1 for t in tokens if t in blob_tokens)
                if score > 0:
                    scored.append((score, update, chunk))
        else:
            blob_tokens = set(_WORD_RE.findall(_searchable_blob(update)))
            score = sum(2 if t in title_tokens else 1 for t in tokens if t in blob_tokens)
            if score > 0:
                scored.append((score, update, None))
    scored.sort(key=lambda triple: triple[0], reverse=True)
    return [(update, chunk) for _, update, chunk in scored[:limit]]


def search_candidates(question, limit=MAX_CANDIDATES):
    """Document-level view over search_candidate_chunks(): the top-matching
    filed updates, deduplicated (a long Act can match on several of its own
    chunks), in best-match order. Returns [] if nothing filed shares any
    keyword with the question at all - the caller should treat that as
    "nothing to ask the model about" rather than forcing a guess."""
    seen_ids = set()
    result = []
    for update, _chunk in search_candidate_chunks(question, limit=limit * 3):
        if update.id not in seen_ids:
            seen_ids.add(update.id)
            result.append(update)
        if len(result) >= limit:
            break
    return result


ANSWER_TOOL = {
    "name": "answer_legislation_question",
    "description": "Answer a practitioner's question using only the filed legislation excerpts provided, in a structured, citable form.",
    "input_schema": {
        "type": "object",
        "properties": {
            "found_answer": {
                "type": "boolean",
                "description": "True only if the provided excerpts actually contain enough to answer the question. False if they're only tangentially related, or don't cover it.",
            },
            "answer": {
                "type": "string",
                "description": "The whole answer as plain text (the bottom line, then the explanation), drawn ONLY from the excerpts - never from general knowledge. Cite as [#N] using the Update ids. If found_answer is false, a brief, honest note that the filed library doesn't appear to cover this, rather than a guess.",
            },
            "citation_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "The bracketed [Update #N] id(s) actually drawn on for the answer. Empty if found_answer is false.",
            },
            "bottom_line": {
                "type": "string",
                "description": "One or two sentences that answer the question directly, as a partner would say it across a desk. Lead with the answer (the rate, the limit, yes or no, the date), not with the law's name. Cite as [#N]. Leave empty if found_answer is false.",
            },
            "explanation": {
                "type": "array",
                "description": "Two to four short parts that support the bottom line, for example: The rule; Who and what it covers; Conditions and exceptions; Effect on the facts asked about. Each part is 1 to 4 sentences. Cite as [#N]. Do not repeat the bottom line.",
                "items": {"type": "object", "properties": {"heading": {"type": "string"}, "text": {"type": "string"}}, "required": ["heading", "text"]},
            },
            "key_figures": {
                "type": "array",
                "description": "Rates, amounts, limits, periods and dates the answer rests on, each exactly as the excerpt gives it. Only figures that appear in the excerpts.",
                "items": {"type": "object", "properties": {
                    "label": {"type": "string", "description": "What the figure is, e.g. 'Standard rate of VAT'"},
                    "value": {"type": "string", "description": "The figure as written in the excerpt, e.g. '15%' or 'within 30 days'"},
                    "applies_from": {"type": "string", "description": "The date or period it applies from or to, if the excerpts say; else empty"},
                    "source_id": {"type": "integer", "description": "The Update id it comes from"}}, "required": ["label", "value", "source_id"]},
            },
            "quotes": {
                "type": "array",
                "description": "Up to three short passages (each under 45 words) copied EXACTLY, word for word, from the excerpts, that carry the answer. Do not paraphrase inside a quote.",
                "items": {"type": "object", "properties": {"source_id": {"type": "integer"}, "text": {"type": "string"}}, "required": ["source_id", "text"]},
            },
            "watch_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short points the reader must check before relying on this: a later instrument, a date not yet reached, a condition, a source that is only commentary or news, something the library does not cover. Include every 'Check:' line given with an excerpt that affects the answer.",
            },
            "confidence": {
                "type": "object",
                "description": "How firmly the filed material supports the answer.",
                "properties": {"level": {"type": "string", "enum": ["High", "Medium", "Low"]}, "reason": {"type": "string", "description": "One sentence: why this level."}},
                "required": ["level", "reason"],
            },
            "follow_ups": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Two or three natural next questions a practitioner would ask, phrased as questions the filed library could answer.",
            },
        },
        "required": ["found_answer", "answer", "citation_ids"],
    },
}

SYSTEM_PROMPT = (
    "You are answering a question for staff at a Zimbabwean audit, tax and accounting firm, using "
    "ONLY the excerpts below from their own filed Legislative Update Control library (Acts, "
    "Government Notices, Statutory Instruments, Case Law, professional Publications, and News "
    "Articles they have filed). Do not use any outside knowledge of Zimbabwean law, tax rates, "
    "thresholds, or dates beyond what is explicitly stated in these excerpts, even if you believe you "
    "know the answer - the firm relies on this being grounded only in what they've actually filed and "
    "reviewed. If the excerpts don't clearly answer the question, set found_answer to false and say so "
    "plainly rather than guessing or filling in from general knowledge. Weigh each source by what it "
    "actually is, shown as its Type and Legal weight in the excerpt: an Act, Notice or SI is primary Zimbabwean law; a "
    "Case's authority status (shown in the excerpt) tells you whether it's binding (a Zimbabwean "
    "court) or merely persuasive (e.g. a foreign, often South African, decision); a Publication is "
    "secondary commentary/analysis - useful and often persuasive, but never primary law and never "
    "binding, and not necessarily about Zimbabwe at all - so present anything drawn from one as "
    "commentary or analysis, attributed to its source, not as a statement of Zimbabwean law in its own "
    "right; a News Article is the least authoritative source of all - ordinary news reporting that may "
    "describe a development before it is officially gazetted or confirmed, so never present anything "
    "drawn from a News Article as settled law or as something the firm can rely on for compliance - "
    "frame it explicitly as reported/proposed and flag that it should be verified against the actual "
    "instrument once filed. Always cite the specific [Update #N] id(s) you actually drew on, written as [#N]. "
    "Write like a senior colleague: lead with the answer in the bottom line, then explain briefly, with no preamble, no "
    "'based on the excerpts' filler and no repetition. Put every rate, amount, limit, period and date you rely on into "
    "key_figures exactly as the excerpt gives it, with the date it applies from where the excerpt says so. Quote only words "
    "that appear verbatim in an excerpt. Where excerpts disagree, or a 'Check:' line shows a later instrument or a date not "
    "yet reached, say which you relied on and why, and put it in watch_points. If the question is a follow-up, use the earlier "
    "exchange only to understand what is being asked; the law still comes only from the excerpts. Keep it concise and practical."
)


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


def _with_limits(client, timeout):
    """The same client with this call's own time limit and no automatic retry (the test doubles have no with_options, and are used as they are)."""
    opt = getattr(client, "with_options", None)
    return opt(timeout=timeout, max_retries=0) if callable(opt) else client


def friendly_error(exc):
    """A sentence a person can act on, instead of the raw network message."""
    text = str(exc)
    name = type(exc).__name__.lower()
    if "timeout" in name or "timed out" in text.lower() or "interrupted" in text.lower() or "connection" in name:
        return "The AI took too long to reply, so nothing was drafted. Try again; for a long description, send fewer topics at a time."
    if "rate" in name and "limit" in name or "overloaded" in text.lower() or "529" in text:
        return "The AI service is busy just now. Wait a moment and try again."
    return text[:400]


def _candidate_block(update, chunk=None, flags=None, chars=None):
    lines = [
        f"[Update #{update.id}] {update.title}",
        f"Type: {update.type_label}" + (f" ({update.tax_head})" if update.tax_head else ""),
        f"Legal weight: {legislation_understand.weight_label(update)}",
    ]
    for note in (flags or {}).get(update.id, []):
        lines.append(f"Check: {note}")
    if update.instrument_type == "Case":
        if update.case_court or update.case_jurisdiction:
            lines.append(f"Court: {update.case_court or '—'} ({update.case_jurisdiction or 'jurisdiction unclear'})")
        if update.case_citation:
            lines.append(f"Citation: {update.case_citation}")
        authority_label = update.authority_label or update.ai_authority_label
        if authority_label:
            lines.append(f"Authority for Zimbabwean practice: {authority_label}" + (" (AI suggestion, not yet confirmed)" if not update.case_authority_status else ""))
    elif update.instrument_type == "Pub":
        lines.append("Note: this is a professional publication (secondary commentary/analysis), not Zimbabwean legislation and not binding.")
    elif update.instrument_type == "News":
        lines.append("Note: this is a general news report, not Zimbabwean legislation and not a professional/technical analysis - treat it as an early signal of a possible change, not confirmed until the actual instrument is filed and reviewed.")
    if update.article_url:
        lines.append(f"Read online: {update.article_url}")
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
        lines.append(("Key holdings: " if update.instrument_type == "Case" else "Key changes: ") + "; ".join(update.ai_key_changes))
    if chunk is not None:
        heading_note = f" ({chunk.heading})" if chunk.heading else ""
        lines.append(f"Relevant excerpt from the filed document{heading_note}: {chunk.text[:chars or EXCERPT_CHARS]}")
    elif update.extracted_text:
        lines.append(f"Excerpt from the filed document: {update.extracted_text[:chars or EXCERPT_CHARS]}")
    return "\n".join(lines)


# ---------------------------------------------------------------- fast-recall cache

def _normalize_question(question):
    return " ".join((question or "").strip().lower().split())[:500]


def _cache_lookup(question):
    """Exact match first, then a fuzzy (difflib) match against every cached
    question - cheap at the scale this library operates at, same philosophy
    as the keyword search above. Returns the matched LegislativeAskCache row
    (with its hit_count bumped and committed) or None."""
    normalized = _normalize_question(question)
    if not normalized:
        return None
    row = (
        LegislativeAskCache.query.filter_by(question_normalized=normalized)
        .order_by(LegislativeAskCache.created_at.desc())
        .first()
    )
    if row is None:
        best_ratio = 0.0
        for candidate in LegislativeAskCache.query.all():
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


# ---------------------------------------------------------------- structured answers

def _norm_text(t):
    """Lower-case, curly quotes straightened, all runs of whitespace and punctuation collapsed - for checking that a quotation or figure
    really is in the excerpt it is credited to."""
    t = (t or "").replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"').replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"[^a-z0-9%]+", " ", t.lower()).strip()


def _figure_in(raw_text, value):
    """Is this figure (a rate, an amount, a period, a date) in the passage it is credited to? Compares the numbers as numbers, so "15%" matches
    "15 per cent" and "1,000" matches "1000", but "15" does not match "2015"."""
    raw = re.sub(r"(?<=\d),(?=\d{3})", "", (raw_text or "").lower())
    val = re.sub(r"(?<=\d),(?=\d{3})", "", (value or "").lower())
    nums = re.findall(r"\d+(?:\.\d+)?", val)
    if nums:
        return all(re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d]|\.\d)", raw) for n in nums)
    v = _norm_text(value)                      # a figure with no digits, e.g. "monthly" or "within thirty days"
    return bool(v) and v in _norm_text(raw_text)


def _clean_structured(data, pairs, flags, ctx):
    """The model's structured answer, checked against the excerpts it was given: quotations must appear word for word in the
    source they are credited to, figures must appear in it, ids must be ones it was given. Whatever fails is dropped or marked, never
    silently kept. Adds the checks the model cannot be trusted with (legal weight, instruments not in force, later instruments)."""
    by_id, text_by_id, raw_by_id = {}, {}, {}
    for u, c in pairs:
        by_id[u.id] = u
        piece = " ".join(filter(None, [u.title, u.ai_summary or u.summary or "", "; ".join(u.ai_key_changes or []), c.text if c is not None else (u.extracted_text or "")[:EXCERPT_CHARS]]))
        text_by_id[u.id] = text_by_id.get(u.id, "") + " " + _norm_text(piece)
        raw_by_id[u.id] = raw_by_id.get(u.id, "") + " " + piece

    def sid(x):
        try:
            x = int(x)
        except (TypeError, ValueError):
            return None
        return x if x in by_id else None

    out = {"bottom_line": (data.get("bottom_line") or "").strip(), "explanation": [], "key_figures": [], "quotes": [], "watch_points": [],
           "confidence": None, "follow_ups": [], "dropped": 0}
    for part in data.get("explanation") or []:
        if isinstance(part, dict) and (part.get("text") or "").strip():
            out["explanation"].append({"heading": (part.get("heading") or "").strip(), "text": part["text"].strip()})
    for fig in data.get("key_figures") or []:
        if not isinstance(fig, dict) or not (fig.get("label") and fig.get("value")):
            continue
        i = sid(fig.get("source_id"))
        if i is None:
            out["dropped"] += 1
            continue
        out["key_figures"].append({"label": str(fig["label"]).strip(), "value": str(fig["value"]).strip(), "applies_from": str(fig.get("applies_from") or "").strip(),
                                   "source_id": i, "checked": _figure_in(raw_by_id[i], fig["value"])})
    for q in (data.get("quotes") or [])[:3]:
        if not isinstance(q, dict) or not (q.get("text") or "").strip():
            continue
        i = sid(q.get("source_id"))
        qn = _norm_text(q["text"])
        if i is None or len(qn) < 12 or qn not in text_by_id[i]:
            out["dropped"] += 1          # a quotation that is not in the source is never shown
            continue
        out["quotes"].append({"source_id": i, "text": q["text"].strip()})
    out["watch_points"] = [str(w).strip() for w in (data.get("watch_points") or []) if str(w).strip()]
    out["follow_ups"] = [str(f).strip() for f in (data.get("follow_ups") or []) if str(f).strip()][:3]
    cited = []
    for i in [sid(x) for x in (data.get("citation_ids") or [])] + [f["source_id"] for f in out["key_figures"]] + [q["source_id"] for q in out["quotes"]]:
        if i is not None and i not in cited:
            cited.append(i)
    # the model's [#N] markers in the text count as citations too
    blob = " ".join([out["bottom_line"], data.get("answer") or ""] + [p["text"] for p in out["explanation"]])
    for m in re.findall(r"\[#?\s*(\d+)\s*\]", blob):
        i = sid(m)
        if i is not None and i not in cited:
            cited.append(i)
    if not cited:
        cited = [pairs[0][0].id] if pairs else []
    out["citation_ids"] = cited

    # checks of our own, from the filed dates and types
    own = []
    for i in cited:
        for note in flags.get(i, []):
            line = f"{by_id[i].title}: {note}"
            if line not in own:
                own.append(line)
    for w in own:
        if not any(w.split(": ", 1)[-1][:40] in x for x in out["watch_points"]):
            out["watch_points"].append(w)
    if out["dropped"]:
        out["watch_points"].append(f"{out['dropped']} item{'s' if out['dropped'] != 1 else ''} the AI offered could not be found in the filed text and {'were' if out['dropped'] != 1 else 'was'} left out.")
    unchecked = [f for f in out["key_figures"] if not f["checked"]]
    if unchecked:
        out["watch_points"].append("Check against the source before relying on: " + "; ".join(f"{f['label']} ({f['value']})" for f in unchecked) + " - not found word for word in the passage supplied.")

    # confidence: the AI's view, held back by what the sources actually are
    conf = data.get("confidence") if isinstance(data.get("confidence"), dict) else {}
    level = conf.get("level") if conf.get("level") in ("High", "Medium", "Low") else "Medium"
    reason = (conf.get("reason") or "").strip()
    top = max([legislation_understand.weight_of(by_id[i]) for i in cited] or [0])
    order = {"Low": 0, "Medium": 1, "High": 2}
    cap = None
    if top <= 2:
        cap, why = "Low", "Only commentary or news is filed on this - no Act, Notice or Statutory Instrument."
    elif ctx.get("primary_only") and top < 4:
        cap, why = "Medium", "The sources are case law, not the legislation itself."
    elif any("Not yet in force" in n for i in cited for n in flags.get(i, [])):
        cap, why = "Medium", "Part of this rests on an instrument that is not yet in force."
    elif unchecked:
        cap, why = "Medium", "A figure could not be matched word for word to the filed text."
    if cap and order[level] > order[cap]:
        level, reason = cap, why + (" " + reason if reason else "")
    out["confidence"] = {"level": level, "reason": reason}
    return out


def _plain(structured, fallback=""):
    """The structured answer as plain text, for the recall cache, the Partner review of a verified answer and anywhere only text fits."""
    if not structured or not (structured.get("bottom_line") or structured.get("explanation")):
        return fallback
    parts = [structured.get("bottom_line", "")]
    for e in structured.get("explanation") or []:
        parts.append((e["heading"] + ": " if e.get("heading") else "") + e["text"])
    return "\n\n".join(p for p in parts if p).strip() or fallback


def render_citations(text, order):
    """`text` with each [#id] turned into a numbered link to that source's entry, numbered by `order` (the list of ids as listed under the
    answer). Markers for ids that are not in `order` are removed. Returns Markup, safe to print."""
    from markupsafe import Markup, escape
    pos = {i: n + 1 for n, i in enumerate(order)}

    def one(m):
        ids = [int(x) for x in re.findall(r"\d+", m.group(0))]
        links = [f'<a class="cite" href="#src-{i}" title="Go to source {pos[i]}">{pos[i]}</a>' for i in ids if i in pos]
        return "<sup>[" + ", ".join(links) + "]</sup>" if links else ""
    safe = str(escape(text or ""))
    safe = re.sub(r"\[#?\s*\d+(?:\s*[,;]\s*#?\s*\d+)*\s*\]", one, safe)
    return Markup(safe.replace("\n\n", "<br><br>").replace("\n", "<br>"))


def _sources(updates, pairs, flags):
    """What is shown under an answer for each source: the update, its legal weight, the passage used and what to check."""
    passage = {}
    for u, c in pairs:
        if u.id not in passage and c is not None:
            passage[u.id] = (c.heading or "").strip()
    return [{"doc": u, "label": legislation_understand.weight_label(u), "colour": legislation_understand.weight_colour(u),
             "weight": legislation_understand.weight_of(u), "passage": passage.get(u.id, ""), "flags": flags.get(u.id, [])} for u in updates]


def _row_to_payload(row):
    citation_ids = json.loads(row.citation_ids_json) if row.citation_ids_json else []
    candidate_ids = json.loads(row.candidate_ids_json) if row.candidate_ids_json else []
    all_ids = citation_ids + candidate_ids
    by_id = {u.id: u for u in LegislativeUpdate.query.filter(LegislativeUpdate.id.in_(all_ids)).all()} if all_ids else {}

    payload = {"answer": row.answer, "from_cache": True, "cache_hit_count": row.hit_count}
    if row.status == "done":
        payload["citations"] = [by_id[i] for i in citation_ids if i in by_id]
        try:
            structured = json.loads(row.structured_json) if row.structured_json else None
        except ValueError:
            structured = None
        if structured:
            payload["structured"] = structured
            payload["understood"] = structured.get("understood") or []
            payload["sources"] = _sources(payload["citations"], [], legislation_understand.supersession_flags(payload["citations"]))
    else:
        payload["candidates"] = [by_id[i] for i in candidate_ids if i in by_id]
    return payload


def _store_cache(question, status, answer, citation_ids=None, candidate_ids=None, asked_by_id=None, structured=None):
    normalized = _normalize_question(question)
    if not normalized:
        return None
    row = LegislativeAskCache(
        question_normalized=normalized,
        question_original=question[:500],
        status=status,
        answer=answer[:5000],
        citation_ids_json=json.dumps(list(citation_ids)) if citation_ids else None,
        candidate_ids_json=json.dumps(list(candidate_ids)) if candidate_ids else None,
        structured_json=json.dumps(structured) if structured else None,
        asked_by_id=asked_by_id,
        hit_count=1,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _history_note(history):
    """The earlier exchange, only so a follow-up such as "and for 2023?" can be understood."""
    if not history:
        return ""
    q, a = history
    return f"Earlier in this conversation the practitioner asked: {q[:400]}\nThe answer given was: {a[:700]}\n\nThe new question below is a follow-up to that.\n\n"


def ask(question, asked_by_id=None, history=None):
    """Answer `question` from the filed legislative library.

    `history` is an optional (earlier question, earlier answer) pair: the new question is then a follow-up, is understood in that light,
    and is neither recalled from nor stored in the cache (its meaning depends on the earlier exchange).

    Returns (status, payload):
      - "not_configured": no ANTHROPIC_API_KEY set (and no cached answer already exists for this question). payload is None.
      - "no_candidates": nothing filed shares any keyword with the question. payload is None.
      - "no_answer": candidates were found but the model judged they don't actually answer the question. payload is
        {"answer": str, "candidates": [...], "from_cache": bool, "understood": [...][, "cache_hit_count": int]}.
      - "done": payload is {"answer": str, "citations": [LegislativeUpdate, ...], "from_cache": bool, "structured": {...}, "sources": [...],
        "understood": [lines][, "cache_hit_count": int, "cache_id": int]}. `structured` holds bottom_line, explanation, key_figures,
        quotes, watch_points, confidence, follow_ups, citation_ids (each quotation and figure checked against the filed text).
      - "verified": the firm has a Partner-approved answer to this very question (a FirmLesson); payload is
        {"answer": str, "lesson": FirmLesson, "from_cache": False}. Served without searching or calling the AI.
      - "error": payload is a short error message string.
    A "done"/"no_answer" result is saved to the fast-recall cache and reused verbatim - always visibly flagged via payload["from_cache"] -
    for a close repeat of the same question; every other status is never cached. Never raises."""
    question = (question or "").strip()
    if not question:
        return "error", "Please enter a question."

    if not history:
        # A Partner-approved "verified answer" (see learning.py) beats everything else: it is served as the firm's own
        # answer, with no search and no AI call, until its review date.
        import learning
        verified = learning.verified_answer(question)
        if verified is not None:
            learning.mark_used([verified.id])
            db.session.commit()
            return "verified", {"answer": verified.content, "lesson": verified, "from_cache": False}

        cached = _cache_lookup(question)
        if cached is not None:
            payload = _row_to_payload(cached)
            payload["cache_id"] = cached.id
            return cached.status, payload

    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return "not_configured", None

    try:
        search_text = question if not history else f"{history[0]} {question}"
        pairs, ctx, flags = legislation_understand.retrieve(search_text, limit=MAX_CANDIDATES)
    except Exception:
        db.session.rollback()
        pairs, ctx, flags = search_candidate_chunks(question), legislation_understand.understand(question), {}
    if not pairs:
        return "no_candidates", None

    seen_ids = set()
    distinct_candidates = []
    for update, _chunk in pairs:
        if update.id not in seen_ids:
            seen_ids.add(update.id)
            distinct_candidates.append(update)

    context = "\n\n---\n\n".join(_candidate_block(u, c, flags) for u, c in pairs)
    user_message = (
        f"Excerpts from the firm's filed Legislative Update Control library, strongest sources first:\n\n{context}\n\n---\n\n"
        f"{_history_note(history)}Question: {question}"
    )

    try:
        client = _with_limits(_client(), ASK_TIMEOUT)
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1800,
            system=SYSTEM_PROMPT,
            tools=[ANSWER_TOOL],
            tool_choice={"type": "tool", "name": "answer_legislation_question"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        return "error", friendly_error(exc)

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "answer_legislation_question":
            data = block.input or {}
            answer = (data.get("answer") or "").strip()
            if not answer and not (data.get("bottom_line") or "").strip():
                return "error", "The model didn't return an answer - try rephrasing the question."
            if not data.get("found_answer"):
                answer = answer or (data.get("bottom_line") or "").strip()
                if not history:
                    _store_cache(question, "no_answer", answer, candidate_ids=[u.id for u in distinct_candidates], asked_by_id=asked_by_id)
                return "no_answer", {"answer": answer, "candidates": distinct_candidates, "from_cache": False, "understood": ctx.get("lines", [])}
            structured = _clean_structured(data, pairs, flags, ctx)
            if not structured["bottom_line"] and not structured["explanation"]:
                # an older-shaped reply: only the single `answer` text - show it as the explanation, first sentence as the bottom line
                first = re.split(r"(?<=[.!?])\s+", answer, maxsplit=1)
                structured["bottom_line"] = first[0]
                if len(first) > 1 and first[1].strip():
                    structured["explanation"] = [{"heading": "", "text": first[1].strip()}]
            structured["understood"] = ctx.get("lines", [])
            by_id = {u.id: u for u in distinct_candidates}
            citations = [by_id[i] for i in structured["citation_ids"] if i in by_id] or distinct_candidates[:1]
            text = _plain(structured, answer)
            row = None if history else _store_cache(question, "done", text, citation_ids=[u.id for u in citations], asked_by_id=asked_by_id, structured=structured)
            return "done", {"answer": text, "citations": citations, "from_cache": False, "cache_id": row.id if row else None,
                            "structured": structured, "understood": structured["understood"], "sources": _sources(citations, pairs, flags)}
    return "error", "The model didn't return a structured result - try rephrasing the question."


# ---------------------------------------------------------------- advisory brief: a situation and the topics to cover

ADVISE_TOOL = {
    "name": "advise_from_library",
    "description": "Set out what the firm's filed legislation and material say about each topic of a client situation, citing the excerpts used and being clear about what they do not cover.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "The position in one or two sentences, as a partner would say it first. Lead with what the client should expect or do. Plain words, no citations."},
            "summary": {"type": "string", "description": "The overall position in 3 to 6 plain sentences, drawn only from the excerpts and the facts given. Say plainly if the filed material is thin."},
            "sections": {
                "type": "array",
                "description": "One section per topic listed, in the order listed (plus one for any important issue the facts raise that is not a listed topic).",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "coverage": {"type": "string", "enum": ["Covered", "Partly covered", "Not covered"], "description": "Whether the excerpts actually deal with this topic for these facts."},
                        "finding": {"type": "string", "description": "What the excerpts say that bears on this topic and the facts given, written for a tax practitioner. If they say nothing relevant, say so in one sentence."},
                        "key_figures": {"type": "array", "description": "Rates, limits, periods and dates this topic turns on, exactly as the excerpts give them.", "items": {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}, "source_id": {"type": "integer"}}, "required": ["label", "value", "source_id"]}},
                        "citation_ids": {"type": "array", "items": {"type": "integer"}, "description": "The [Update #N] ids drawn on. Empty if none."},
                        "planning_points": {"type": "array", "items": {"type": "string"}, "description": "Practical points or options that follow from the excerpts for these facts. Each must rest on the excerpts, not on general knowledge."},
                    },
                    "required": ["topic", "finding", "citation_ids"],
                },
            },
            "recommended_actions": {"type": "array", "description": "What the firm should do next for this client, most important first, each resting on the excerpts. Three to six.", "items": {"type": "object", "properties": {"action": {"type": "string"}, "why": {"type": "string"}, "citation_ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["action"]}},
            "not_covered": {"type": "string", "description": "What the filed library does not address that the practitioner must check against the actual law before advising."},
            "facts_to_confirm": {"type": "array", "items": {"type": "string"}, "description": "Facts about the client's situation that would change the advice and should be confirmed."},
            "watch_points": {"type": "array", "items": {"type": "string"}, "description": "Anything in the sources that limits how far this can be relied on: commentary or news rather than law, a later instrument, a date not reached, conflicting sources. Include the 'Check:' lines that matter."},
        },
        "required": ["summary", "sections", "not_covered"],
    },
}

ADVISE_PROMPT = (
    "You are helping a partner at a Zimbabwean audit, tax and accounting firm prepare advice for a client. You are given (1) the client's "
    "situation and the topics to cover, written by the partner, and (2) excerpts from the firm's own filed Legislative Update Control "
    "library, strongest sources first. The situation is the partner's own statement of the facts: use it as the facts, but it is not law and not something the "
    "excerpts confirm. For the law, use ONLY the excerpts: do not use outside knowledge of Zimbabwean law, tax rates, thresholds or dates, "
    "even if you believe you know them. Go topic by topic. For each, say what the excerpts provide that bears on these facts, cite the "
    "[Update #N] ids, and add practical planning points only where they rest on the excerpts. Mark each topic Covered, Partly covered or Not covered. "
    "Where the excerpts are silent or only tangential, say so plainly and put the gap in not_covered rather than filling it in. Weigh each source by its Type "
    "and Legal weight as in the excerpt: an Act, Notice or SI is primary Zimbabwean law; a Case is binding or persuasive as its authority line says; a Publication is "
    "commentary and never binding; a News Article is only a signal to be verified. Where the facts raise an issue the topics do not list "
    "(for example, costs borne by one company while the income is taxed in another), add a section for it, but only to the extent the "
    "excerpts touch it. Start with a one-or-two-sentence headline, then the summary. Finish with the next actions for the firm, "
    "most important first, and the facts to confirm. Write for a partner who will send this on: direct, no filler, no repetition between sections. "
    "Put the 'Check:' notes that matter into watch_points. Be brief so the memo is quick to produce: each finding at most about 80 words, at most 6 sections, "
    "at most 5 next actions, at most 3 planning points per section, and key figures only where a rate, limit or date really decides the point. "
    "This is a draft for the partner's review, never a final opinion."
)


def advise(brief_text, asked_by_id=None):
    """Draft advice for a described situation from the filed library alone.
    Returns (status, payload): "not_configured" (no key), "no_candidates" (nothing filed matches), "error" (payload = message) or
    "done" with payload {"headline", "summary", "sections": [{"topic", "coverage", "finding", "citations": [updates], "key_figures", "planning_points": [...]}],
    "recommended_actions": [{"action", "why", "citations"}], "not_covered", "facts_to_confirm", "watch_points", "sources": [...], "excerpts": [(update, chunk)]}.
    Searches topic by topic and as a whole so each topic gets its own passages, strongest law first. Never cached, never files anything. Never raises."""
    brief_text = (brief_text or "").strip()
    if not brief_text:
        return "error", "Describe the situation first."
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return "not_configured", None
    try:
        topics, facts = legislation_index.split_brief(brief_text)
        pairs, seen = [], set()

        def take(pair):
            u, c = pair
            key = (u.id, c.id if c is not None else None)
            if key not in seen:
                seen.add(key)
                pairs.append(pair)

        for t in topics:
            got, _ctx, _fl = legislation_understand.retrieve(t, limit=4, per_doc=2)
            for pair in got:
                take(pair)
        got, _ctx, _fl = legislation_understand.retrieve(brief_text, limit=10, per_doc=2)
        for pair in got:
            take(pair)
        pairs = pairs[:ADVISE_MAX_PASSAGES]
        flags = legislation_understand.supersession_flags(list({u.id: u for u, _c in pairs}.values()))
    except Exception as exc:
        db.session.rollback()
        return "error", f"The search failed: {str(exc)[:300]}"
    if not pairs:
        return "no_candidates", None
    context = "\n\n---\n\n".join(_candidate_block(u, c, flags, ADVISE_EXCERPT_CHARS) for u, c in pairs)
    user_message = (f"The partner's description of the situation and the topics to cover:\n\n{brief_text}\n\n=====\n\n"
                    f"Excerpts from the firm's filed library:\n\n{context}")
    try:
        client = _with_limits(_client(), ADVISE_TIMEOUT)
        response = client.messages.create(
            model=DEFAULT_MODEL, max_tokens=3600, system=ADVISE_PROMPT, tools=[ADVISE_TOOL],
            tool_choice={"type": "tool", "name": "advise_from_library"}, messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        return "error", friendly_error(exc)
    by_id = {u.id: u for u, _c in pairs}
    text_by_id = {}
    for u, c in pairs:
        text_by_id[u.id] = text_by_id.get(u.id, "") + " " + " ".join(filter(None, [u.title, u.ai_summary or u.summary or "", c.text if c is not None else ""]))
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "advise_from_library":
            data = block.input or {}
            used = []

            def ids_of(raw):
                out = []
                for i in raw or []:
                    if str(i).isdigit() and int(i) in by_id and int(i) not in out:
                        out.append(int(i))
                        if int(i) not in used:
                            used.append(int(i))
                return out
            sections = []
            for sec in data.get("sections") or []:
                ids = ids_of(sec.get("citation_ids"))
                figs = []
                for fg in sec.get("key_figures") or []:
                    if isinstance(fg, dict) and fg.get("label") and fg.get("value") and str(fg.get("source_id") or "").isdigit() and int(fg["source_id"]) in by_id:
                        figs.append({"label": str(fg["label"]).strip(), "value": str(fg["value"]).strip(), "source_id": int(fg["source_id"]),
                                     "checked": _figure_in(text_by_id.get(int(fg["source_id"]), ""), fg["value"])})
                        ids_of([fg["source_id"]])
                cov = sec.get("coverage") if sec.get("coverage") in ("Covered", "Partly covered", "Not covered") else ("Covered" if ids else "Not covered")
                sections.append({"topic": (sec.get("topic") or "").strip(), "finding": (sec.get("finding") or "").strip(), "coverage": cov,
                                 "citation_ids": ids, "citations": [by_id[i] for i in ids], "key_figures": figs,
                                 "planning_points": [str(x).strip() for x in (sec.get("planning_points") or []) if str(x).strip()]})
            actions = []
            for a in data.get("recommended_actions") or []:
                if isinstance(a, dict) and (a.get("action") or "").strip():
                    ids = ids_of(a.get("citation_ids"))
                    actions.append({"action": a["action"].strip(), "why": (a.get("why") or "").strip(), "citation_ids": ids, "citations": [by_id[i] for i in ids]})
            if not (data.get("summary") or sections):
                return "error", "The model didn't return any advice - try again."
            watch = [str(w).strip() for w in (data.get("watch_points") or []) if str(w).strip()]
            for i in used:
                for note in flags.get(i, []):
                    line = f"{by_id[i].title}: {note}"
                    if not any(note[:40] in w for w in watch):
                        watch.append(line)
            if any(not f["checked"] for sec in sections for f in sec["key_figures"]):
                watch.append("Some figures could not be matched word for word to the filed text (marked in the memo) - check them against the source.")
            source_updates = [by_id[i] for i in used] or [pairs[0][0]]
            return "done", {"headline": (data.get("headline") or "").strip(), "summary": (data.get("summary") or "").strip(), "sections": sections,
                            "recommended_actions": actions, "not_covered": (data.get("not_covered") or "").strip(),
                            "facts_to_confirm": [str(x).strip() for x in (data.get("facts_to_confirm") or []) if str(x).strip()],
                            "watch_points": watch, "source_ids": [u.id for u in source_updates], "sources": _sources(source_updates, pairs, flags), "excerpts": pairs}
    return "error", "The model didn't return a structured result - try again."
