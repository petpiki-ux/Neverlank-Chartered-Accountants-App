"""AI-assisted summarisation of a filed Act/Government Notice/Statutory
Instrument (see models.LegislativeUpdate and legislative_updates.py for the
upload route that calls this).

Mirrors entity_extraction.py's approach: the Claude API (the `anthropic`
package), tool-calling for structured output, and the same text-vs-image
branching for a scanned instrument with no PDF text layer. Requires an
ANTHROPIC_API_KEY environment variable (see the README for how to set this
on Render) - without it, summarisation is skipped with a clear
"not_configured" status rather than failing silently or crashing the
upload. Every result is purely informational, like every other AI-assisted
feature in this app: it never decides anything on its own - a person reads
the summary (and the key changes and suggested areas) and judges them. The
suggested practice areas in particular are never applied automatically -
see models.LegislativeUpdate.set_areas, which only a person's own tagging
ever calls."""
import os
import io
import base64

import anthropic
import pdfplumber

from models import LEGISLATIVE_UPDATE_AREAS, CASE_AUTHORITY_STATUSES

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90  # seconds - a multi-page scanned gazette can take a while to read
MAX_PAGES = 5  # cap how many page images are sent in one call - keeps cost/size bounded for a large scanned instrument
MAX_TEXT_CHARS = 60000  # generous for a piece of legislation; truncated defensively beyond this

# A full Act of Parliament can run to hundreds of pages - far more than
# MAX_TEXT_CHARS ever reads, so a one-paragraph AI summary of one would only
# ever reflect its earlier Parts at best. Rather than generate a
# necessarily-partial summary of something this long, full Acts skip
# summarisation entirely and rely on full-text chunked search instead (see
# legislation_chunking.py/legislation_search.py's "Ask the library") -
# Notices and subsidiary regulations/SIs are almost always short enough that
# a summary stays genuinely useful, so they're unaffected.
SKIP_SUMMARY_INSTRUMENT_TYPES = {"Act"}


def skip_summary_reason(instrument_type):
    """Returns a short, person-readable reason if `instrument_type` should
    skip AI summarisation entirely, or None if it should be summarised as
    normal. Callers set ai_status="skipped" (never "done"/"error"/
    "not_configured") and store this string as ai_error when this returns
    non-None, without ever calling summarize()/summarize_legislative_update()."""
    if instrument_type in SKIP_SUMMARY_INSTRUMENT_TYPES:
        return (
            "Full Acts aren't auto-summarised - a one-paragraph summary of a document this long "
            "would only ever reflect part of it. The full text is filed and searchable through "
            "\"Ask the library\" instead."
        )
    return None

SUMMARY_TOOL = {
    "name": "record_legislative_summary",
    "description": "Record a plain-language summary of this piece of Zimbabwean legislation for an audit/tax firm's internal register.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A plain-language summary of what this instrument does and why it matters, written for an audit/tax/accounting practitioner - a few sentences, not a paragraph-by-paragraph restatement.",
            },
            "key_changes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The specific, actionable changes this instrument makes (e.g. a rate change, a new threshold, a new filing obligation, a repealed provision), each as one short standalone bullet. Leave empty if the instrument makes no substantive change (e.g. a purely administrative notice).",
            },
            "suggested_areas": {
                "type": "array",
                "items": {"type": "string", "enum": LEGISLATIVE_UPDATE_AREAS},
                "description": "Which of the listed practice areas this instrument is actually relevant to. Only include an area the instrument genuinely touches - this is only a suggestion for a person to confirm, so it's fine to leave it short rather than guessing broadly.",
            },
        },
        "required": ["summary", "key_changes", "suggested_areas"],
    },
}

SYSTEM_PROMPT = (
    "You are summarising a piece of Zimbabwean legislation (an Act of Parliament, a Government "
    "Notice, or a Statutory Instrument) for an audit, tax and accounting firm's internal "
    "Legislative Update Control register. Read the instrument carefully and call "
    "record_legislative_summary with a plain-language summary a busy practitioner can read in "
    "under a minute, a list of the specific, actionable changes it makes, and the practice areas "
    "it actually touches. Do not invent effects, dates, or figures that are not actually stated in "
    "the document - if something is unclear or not stated, leave it out rather than guessing. If "
    "the instrument makes no substantive change (e.g. it is a purely administrative appointment or "
    "commencement notice), say so plainly in the summary and leave key_changes empty."
)

# ---------------------------------------------------------------- Case Law
#
# A filed "Case" (instrument_type == "Case", see models.py) is a court
# judgment rather than a piece of legislation, so it gets its own tool/
# prompt rather than being forced through SUMMARY_TOOL/SYSTEM_PROMPT above,
# which are written as if everything filed were Zimbabwean legislation. That
# framing is exactly what caused a real bug: a South African tax case,
# filed through the legislation-only prompt, came back described as "not
# relevant to Zimbabwean tax law" - true only in the narrow sense that it
# isn't Zimbabwean legislation, but wrong in the sense a practitioner
# actually cares about, since Zimbabwean courts have long treated South
# African case law as persuasive authority (shared Roman-Dutch/common-law
# tradition, closely analogous legislation including much of Zimbabwe's own
# tax law). CASE_SYSTEM_PROMPT below asks the model to judge authority the
# way a Zimbabwean practitioner would, rather than by jurisdiction alone -
# see models.CASE_AUTHORITY_STATUSES for the resulting classification.
CASE_TOOL = {
    "name": "record_case_law_summary",
    "description": "Record an analysis of this court judgment for an audit/tax firm's internal case-law register, including a plain assessment of its authority for Zimbabwean practice.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A plain-language summary of the case (the issue, what was decided, and why) written for an audit/tax/accounting practitioner - a few sentences, not a paragraph-by-paragraph restatement.",
            },
            "key_holdings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The specific legal principles or holdings this case establishes or applies, each as one short standalone bullet - the kind of point a practitioner would actually cite it for.",
            },
            "suggested_areas": {
                "type": "array",
                "items": {"type": "string", "enum": LEGISLATIVE_UPDATE_AREAS},
                "description": "Which of the listed practice areas this case is actually relevant to. Only include an area the case genuinely touches - this is only a suggestion for a person to confirm.",
            },
            "citation": {
                "type": "string",
                "description": "The case's own citation as it appears in the judgment (e.g. 'ITC 1234 (2019) 82 SATC 123' or 'HH 45/20'). Empty string if none is evident.",
            },
            "court": {
                "type": "string",
                "description": "The court that decided the case (e.g. 'High Court of Zimbabwe', 'Tax Court of South Africa, Johannesburg', 'Supreme Court of Zimbabwe'). Empty string if unclear.",
            },
            "jurisdiction": {
                "type": "string",
                "description": "The country/jurisdiction of the deciding court, e.g. 'Zimbabwe', 'South Africa', 'United Kingdom'. Empty string if unclear.",
            },
            "authority_status": {
                "type": "string",
                "enum": CASE_AUTHORITY_STATUSES,
                "description": (
                    "How a Zimbabwean practitioner should weigh this case. 'binding' only for a "
                    "Zimbabwean court decision that binds within the ordinary court hierarchy. "
                    "'persuasive' for a foreign decision - especially South African - that Zimbabwean "
                    "courts would plausibly treat as persuasive authority, e.g. because the legal "
                    "issue or the underlying statutory provision is the same or closely analogous to "
                    "Zimbabwean law (do not require an exact statutory match - shared Roman-Dutch/"
                    "common-law lineage and similarly-worded tax legislation are themselves reasons "
                    "South African case law is routinely persuasive in Zimbabwe). 'limited' if the "
                    "persuasive value is real but weak or uncertain (e.g. a jurisdiction with less "
                    "shared legal history, or a point turning on a provision that differs materially "
                    "from Zimbabwean law). 'not_relevant' ONLY if the case's actual subject matter has "
                    "no bearing on Zimbabwean tax/legal practice at all - never merely because the "
                    "court is foreign."
                ),
            },
            "authority_reasoning": {
                "type": "string",
                "description": "One or two sentences explaining the authority_status classification above - e.g. which Zimbabwean provision or line of authority it bears on, and why it would (or wouldn't) be persuasive.",
            },
        },
        "required": ["summary", "key_holdings", "suggested_areas", "citation", "court", "jurisdiction", "authority_status", "authority_reasoning"],
    },
}

CASE_SYSTEM_PROMPT = (
    "You are analysing a court judgment being filed into a Zimbabwean audit, tax and accounting "
    "firm's Legislative Update Control library, which tracks case law alongside legislation. The "
    "case may be Zimbabwean or from another jurisdiction. Read it carefully and call "
    "record_case_law_summary with a plain-language summary, the specific holdings a practitioner "
    "would actually cite it for, the practice areas it touches, its citation/court/jurisdiction as "
    "stated in the judgment, and an honest assessment of its authority for Zimbabwean practice. "
    "Judge authority the way a Zimbabwean practitioner would, not by jurisdiction alone: a "
    "Zimbabwean court's decision is binding within the ordinary hierarchy; a foreign court's "
    "decision is never binding, but Zimbabwean courts have long treated South African case law in "
    "particular as persuasive authority, given the shared Roman-Dutch/common-law tradition and "
    "closely analogous legislation (including much of Zimbabwe's own tax law) - being foreign is "
    "not, on its own, a reason to call a case not relevant. Reserve 'not_relevant' for a case whose "
    "actual subject matter has no bearing on Zimbabwean tax or legal practice. Do not invent facts, "
    "figures, or holdings not actually stated in the judgment."
)


# -------------------------------------------------------------- Publications
#
# A filed "Pub" (instrument_type == "Pub", see models.py) is a professional
# publication or journal article - e.g. a SAIT Tax Chronicles Monthly piece,
# a firm's own tax alert, an ICAZ technical note - not a piece of Zimbabwean
# legislation and not a court judgment. Added for the same reason CASE_TOOL/
# CASE_SYSTEM_PROMPT were: forcing something through SUMMARY_TOOL/
# SYSTEM_PROMPT above, which is written as if everything filed were
# Zimbabwean legislation, produces a wrong-framed result - in this case a
# real South African tax journal issue (containing analysis of GAAR, CFCs,
# influencer income, customs proof of export and similar South African tax
# topics, and no Zimbabwean statutory instruments at all) came back
# described in terms of Zimbabwean statutory instruments, when what it
# actually is is a well-regarded professional publication with persuasive,
# secondary-source commentary - filed not as law to comply with, but as
# commentary that helps a practitioner's own decision-making (weighing an
# unsettled position, seeing how a technical point is argued elsewhere,
# sense-checking an approach). PUBLICATION_SYSTEM_PROMPT asks the model to
# treat it as exactly that: useful analysis to weigh on its own merits, not
# primary Zimbabwean law and not binding on anyone.
PUBLICATION_TOOL = {
    "name": "record_publication_summary",
    "description": "Record a summary of this professional publication/journal article for an audit/tax firm's internal library, for a practitioner weighing its analysis as commentary that informs their own decision-making - not as a statement of Zimbabwean law.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A plain-language summary of what this publication covers and why it might matter to an audit/tax/accounting practitioner - a few sentences, not a paragraph-by-paragraph restatement. If it contains several distinct articles/topics, say so and name the main ones.",
            },
            "key_changes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The specific points, arguments, or pieces of technical analysis a practitioner would actually want to weigh when making their own decision, each as one short standalone bullet - e.g. a position taken on an unsettled question, a practical pitfall flagged, a worked example's conclusion. Leave empty if there's nothing beyond the general summary worth pulling out separately.",
            },
            "suggested_areas": {
                "type": "array",
                "items": {"type": "string", "enum": LEGISLATIVE_UPDATE_AREAS},
                "description": "Which of the listed practice areas this publication's content is actually relevant to. Only include an area it genuinely touches - this is only a suggestion for a person to confirm.",
            },
            "publication_jurisdiction": {
                "type": "string",
                "description": "The country/jurisdiction this publication is written for or about, e.g. 'South Africa', 'Zimbabwe', 'international'. Empty string if unclear or mixed.",
            },
        },
        "required": ["summary", "key_changes", "suggested_areas", "publication_jurisdiction"],
    },
}

PUBLICATION_SYSTEM_PROMPT = (
    "You are summarising a professional publication or journal article (e.g. a tax institute's "
    "journal, a firm's technical alert, a professional body's technical note) being filed into a "
    "Zimbabwean audit, tax and accounting firm's internal library. This is NOT a piece of "
    "Zimbabwean legislation and NOT a court judgment - it is secondary commentary/analysis, filed as "
    "a source of informed opinion that helps a practitioner make their OWN decision, not as law they "
    "must comply with. It may not even be about Zimbabwe at all (e.g. a South African tax journal is "
    "routinely useful commentary for a Zimbabwean tax practitioner given the shared Roman-Dutch/"
    "common-law tradition and closely analogous legislation, without being Zimbabwean law itself). "
    "Read it carefully and call record_publication_summary with a plain-language summary of what it "
    "covers, the specific points a practitioner would actually want to weigh when deciding how to "
    "treat a similar issue, the practice areas it touches, and the jurisdiction it's written for. "
    "Describe what the publication actually says and argues - do not describe it in terms of "
    "Zimbabwean statutory instruments, gazette numbers, or an 'effective date' it does not have, and "
    "do not claim it contains Zimbabwean legislation if it does not. Do not invent facts, figures, or "
    "positions not actually stated in the text."
)


def summarize_publication(text=None, page_images=None):
    """The Publication counterpart to summarize()/summarize_case_law() above
    - same shape (text or page_images in, (result, status, error) out, never
    raises), but using PUBLICATION_TOOL/PUBLICATION_SYSTEM_PROMPT so a
    journal article or professional publication is described as what it
    actually is, rather than forced through the legislation-only prompt.

    On "done", result is {"summary", "key_changes", "suggested_areas",
    "publication_jurisdiction"}."""
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return None, "not_configured", "ANTHROPIC_API_KEY is not set - see the README for how to add it on Render."
    if not text and not page_images:
        return None, "error", "No text or page images were available to summarise."

    content = []
    if page_images:
        for img_bytes in page_images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img_bytes).decode("ascii")},
            })
        content.append({"type": "text", "text": "Summarise the professional publication shown in the page image(s) above."})
    else:
        content.append({"type": "text", "text": f"Summarise this professional publication:\n\n{text[:MAX_TEXT_CHARS]}"})

    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2048,
            system=PUBLICATION_SYSTEM_PROMPT,
            tools=[PUBLICATION_TOOL],
            tool_choice={"type": "tool", "name": "record_publication_summary"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        return None, "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_publication_summary":
            data = block.input or {}
            summary = (data.get("summary") or "").strip()
            if not summary:
                return None, "error", "The model didn't return a summary - try again, or add one manually."
            key_changes = [str(k).strip()[:500] for k in (data.get("key_changes") or []) if str(k).strip()]
            suggested_areas = [a for a in (data.get("suggested_areas") or []) if a in LEGISLATIVE_UPDATE_AREAS]
            return (
                {
                    "summary": summary[:5000],
                    "key_changes": key_changes,
                    "suggested_areas": suggested_areas,
                    "publication_jurisdiction": (data.get("publication_jurisdiction") or "").strip()[:100],
                },
                "done",
                None,
            )
    return None, "error", "The model didn't return a structured result - try again, or add a summary manually."


def summarize_publication_update(filepath, is_image, extracted_text, extraction_status):
    """The Publication counterpart to summarize_legislative_update()/
    summarize_case_law_update() below - same text-vs-image branching,
    calling summarize_publication() instead."""
    if is_image:
        try:
            with open(filepath, "rb") as f:
                image_bytes = f.read()
        except Exception as exc:
            return None, "error", f"Could not read the uploaded image file: {exc}"[:2000]
        return summarize_publication(page_images=[image_bytes])

    if extraction_status == "extracted" and extracted_text:
        return summarize_publication(text=extracted_text)
    if extraction_status == "no_text_found":
        images = render_pdf_pages_to_images(filepath)
        if not images:
            return None, "error", "Couldn't render the scanned PDF's pages to images."
        return summarize_publication(page_images=images)
    return None, "error", "Couldn't read the PDF file itself, so there's nothing to summarise from."


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


def render_pdf_pages_to_images(filepath, max_pages=MAX_PAGES, resolution=150):
    """Rasterize the first `max_pages` pages of a PDF to PNG bytes, for a
    scanned/image-only gazette PDF (no extractable text layer) that needs to
    go to Claude as images rather than text. Returns a list of PNG byte
    strings; an empty list (rather than raising) if the PDF can't be opened/
    rendered."""
    images = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages[:max_pages]:
                pil_image = page.to_image(resolution=resolution).original
                buf = io.BytesIO()
                pil_image.convert("RGB").save(buf, format="PNG")
                images.append(buf.getvalue())
    except Exception:
        return []
    return images


def summarize(text=None, page_images=None):
    """Ask Claude to summarise a filed instrument, given either its
    extracted text (preferred when available) or a list of page images (PNG
    bytes, for a scanned instrument with no text layer, or a photographed
    page uploaded directly as an image file).

    Returns (result, status, error):
      - status "done": result is a {"summary", "key_changes", "suggested_areas"} dict
      - status "not_configured": no ANTHROPIC_API_KEY is set
      - status "error": the request failed or returned something unusable
        (error holds a short message either way)
    Never raises - a failure here should never take down an upload."""
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return None, "not_configured", "ANTHROPIC_API_KEY is not set - see the README for how to add it on Render."
    if not text and not page_images:
        return None, "error", "No text or page images were available to summarise."

    content = []
    if page_images:
        for img_bytes in page_images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img_bytes).decode("ascii")},
            })
        content.append({"type": "text", "text": "Summarise the piece of legislation shown in the page image(s) above."})
    else:
        content.append({"type": "text", "text": f"Summarise this piece of legislation:\n\n{text[:MAX_TEXT_CHARS]}"})

    # Client construction and the API call are both wrapped here - neither
    # should ever be allowed to raise out of this function and take an
    # upload down with it (a bad/expired key, a network blip, or any other
    # client-side failure all degrade to status "error" instead).
    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[SUMMARY_TOOL],
            tool_choice={"type": "tool", "name": "record_legislative_summary"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        return None, "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_legislative_summary":
            data = block.input or {}
            summary = (data.get("summary") or "").strip()
            if not summary:
                return None, "error", "The model didn't return a summary - try again, or add one manually."
            key_changes = [str(k).strip()[:500] for k in (data.get("key_changes") or []) if str(k).strip()]
            suggested_areas = [a for a in (data.get("suggested_areas") or []) if a in LEGISLATIVE_UPDATE_AREAS]
            return (
                {"summary": summary[:5000], "key_changes": key_changes, "suggested_areas": suggested_areas},
                "done",
                None,
            )
    return None, "error", "The model didn't return a structured result - try again, or add a summary manually."


def summarize_case_law(text=None, page_images=None):
    """The Case-law counterpart to summarize() above - same shape (text or
    page_images in, (result, status, error) out, never raises), but using
    CASE_TOOL/CASE_SYSTEM_PROMPT so a court judgment is analysed as case
    law (with a citation/court/jurisdiction and an authority_status
    assessment) rather than forced through the legislation-only prompt.

    On "done", result is {"summary", "key_holdings", "suggested_areas",
    "citation", "court", "jurisdiction", "authority_status",
    "authority_reasoning"}."""
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return None, "not_configured", "ANTHROPIC_API_KEY is not set - see the README for how to add it on Render."
    if not text and not page_images:
        return None, "error", "No text or page images were available to analyse."

    content = []
    if page_images:
        for img_bytes in page_images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img_bytes).decode("ascii")},
            })
        content.append({"type": "text", "text": "Analyse the court judgment shown in the page image(s) above."})
    else:
        content.append({"type": "text", "text": f"Analyse this court judgment:\n\n{text[:MAX_TEXT_CHARS]}"})

    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2048,
            system=CASE_SYSTEM_PROMPT,
            tools=[CASE_TOOL],
            tool_choice={"type": "tool", "name": "record_case_law_summary"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        return None, "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_case_law_summary":
            data = block.input or {}
            summary = (data.get("summary") or "").strip()
            if not summary:
                return None, "error", "The model didn't return a summary - try again, or add one manually."
            authority_status = data.get("authority_status")
            if authority_status not in CASE_AUTHORITY_STATUSES:
                authority_status = None
            return (
                {
                    "summary": summary[:5000],
                    "key_holdings": [str(k).strip()[:500] for k in (data.get("key_holdings") or []) if str(k).strip()],
                    "suggested_areas": [a for a in (data.get("suggested_areas") or []) if a in LEGISLATIVE_UPDATE_AREAS],
                    "citation": (data.get("citation") or "").strip()[:300],
                    "court": (data.get("court") or "").strip()[:200],
                    "jurisdiction": (data.get("jurisdiction") or "").strip()[:100],
                    "authority_status": authority_status,
                    "authority_reasoning": (data.get("authority_reasoning") or "").strip()[:2000],
                },
                "done",
                None,
            )
    return None, "error", "The model didn't return a structured result - try again, or add a summary manually."


def summarize_case_law_update(filepath, is_image, extracted_text, extraction_status):
    """The Case-law counterpart to summarize_legislative_update() below -
    same text-vs-image branching, calling summarize_case_law() instead of
    summarize()."""
    if is_image:
        try:
            with open(filepath, "rb") as f:
                image_bytes = f.read()
        except Exception as exc:
            return None, "error", f"Could not read the uploaded image file: {exc}"[:2000]
        return summarize_case_law(page_images=[image_bytes])

    if extraction_status == "extracted" and extracted_text:
        return summarize_case_law(text=extracted_text)
    if extraction_status == "no_text_found":
        images = render_pdf_pages_to_images(filepath)
        if not images:
            return None, "error", "Couldn't render the scanned PDF's pages to images."
        return summarize_case_law(page_images=images)
    return None, "error", "Couldn't read the PDF file itself, so there's nothing to analyse from."


def summarize_legislative_update(filepath, is_image, extracted_text, extraction_status):
    """Top-level helper for legislative_updates.upload_update: decides
    whether to send text or page images based on what extract_pdf_text (for
    a PDF) already found, or sends the uploaded file itself as an image
    directly (for a JPG/PNG upload - a photographed gazette page has no text
    layer to try first). Returns the same (result, status, error) as
    summarize above."""
    if is_image:
        try:
            with open(filepath, "rb") as f:
                image_bytes = f.read()
        except Exception as exc:
            return None, "error", f"Could not read the uploaded image file: {exc}"[:2000]
        return summarize(page_images=[image_bytes])

    if extraction_status == "extracted" and extracted_text:
        return summarize(text=extracted_text)
    if extraction_status == "no_text_found":
        images = render_pdf_pages_to_images(filepath)
        if not images:
            return None, "error", "Couldn't render the scanned PDF's pages to images."
        return summarize(page_images=images)
    return None, "error", "Couldn't read the PDF file itself, so there's nothing to summarise from."
