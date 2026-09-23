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

from models import LEGISLATIVE_UPDATE_AREAS

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90  # seconds - a multi-page scanned gazette can take a while to read
MAX_PAGES = 5  # cap how many page images are sent in one call - keeps cost/size bounded for a large scanned instrument
MAX_TEXT_CHARS = 60000  # generous for a piece of legislation; truncated defensively beyond this

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
