"""AI-assisted summarisation of a filed Policies & Procedures library
document (see models.PolicyDocument and hr.py's new_policy/edit_policy
routes that call this) - the Firm Library research hub's counterpart to
legislation_summary.py, following exactly the same pattern.

Uses the Claude API (the `anthropic` package) with tool-calling for
structured output. Requires an ANTHROPIC_API_KEY environment variable (see
the README for how to set this on Render) - without it, summarisation is
skipped with a clear "not_configured" status rather than failing silently or
crashing the upload. Every result is purely informational, like every other
AI-assisted feature in this app: it never decides anything on its own - a
person reads the summary (and key points, and suggested category) and
judges them. The suggested category in particular is never applied
automatically - it's only ever shown as a hint on the add/edit form; the
category a document actually lives under is always the person's own choice
(see models.PolicyDocument.ai_suggested_category)."""
import os
import base64

import anthropic

from models import POLICY_CATEGORIES
from legislation_summary import render_pdf_pages_to_images  # shared page-image fallback for a scanned PDF

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90
MAX_TEXT_CHARS = 60000  # generous for a policy document; truncated defensively beyond this

POLICY_TOOL = {
    "name": "record_policy_summary",
    "description": "Record a plain-language summary of this firm policy/procedure document for an audit/tax firm's internal Firm Library.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A plain-language summary of what this policy/procedure covers and who it applies to, written for a staff member of the firm - a few sentences, not a paragraph-by-paragraph restatement.",
            },
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The specific, actionable points a staff member would actually need to know or follow (e.g. a required step, a deadline, an approval threshold, who to contact), each as one short standalone bullet.",
            },
            "suggested_category": {
                "type": "string",
                "enum": POLICY_CATEGORIES,
                "description": "Which of the listed categories this document best fits. Only a suggestion for a person to confirm on the form - pick the single best fit.",
            },
        },
        "required": ["summary", "key_points", "suggested_category"],
    },
}

SYSTEM_PROMPT = (
    "You are summarising an internal policy/procedure document for a Zimbabwean audit, tax and accounting "
    "firm's Firm Library. Read it carefully and call record_policy_summary with a plain-language summary a "
    "busy staff member can read in under a minute, a list of the specific, actionable points they'd actually "
    "need to know or follow, and which category the document best fits. Do not invent requirements, dates, or "
    "figures that are not actually stated in the document - if something is unclear or not stated, leave it out "
    "rather than guessing."
)


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


def summarize_policy(text=None, page_images=None):
    """Ask Claude to summarise a filed policy/procedure, given either its
    extracted text (preferred when available) or a list of page images (PNG
    bytes, for a scanned PDF with no text layer).

    Returns (result, status, error):
      - status "done": result is a {"summary", "key_points", "suggested_category"} dict
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
        content.append({"type": "text", "text": "Summarise the policy/procedure document shown in the page image(s) above."})
    else:
        content.append({"type": "text", "text": f"Summarise this firm policy/procedure document:\n\n{text[:MAX_TEXT_CHARS]}"})

    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[POLICY_TOOL],
            tool_choice={"type": "tool", "name": "record_policy_summary"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        return None, "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_policy_summary":
            data = block.input or {}
            summary = (data.get("summary") or "").strip()
            if not summary:
                return None, "error", "The model didn't return a summary - try again, or add one manually."
            key_points = [str(k).strip()[:500] for k in (data.get("key_points") or []) if str(k).strip()]
            suggested_category = data.get("suggested_category")
            if suggested_category not in POLICY_CATEGORIES:
                suggested_category = None
            return (
                {"summary": summary[:5000], "key_points": key_points, "suggested_category": suggested_category},
                "done",
                None,
            )
    return None, "error", "The model didn't return a structured result - try again, or add a summary manually."


def summarize_policy_document(filepath, extracted_text, extraction_status):
    """Top-level helper for hr.py's new_policy/edit_policy: decides whether
    to send text or (for a scanned PDF with no text layer) page images,
    based on what file_text_extraction.extract_text_for_file already found.
    Returns the same (result, status, error) as summarize_policy above.

    A non-PDF document (Word/Excel/PowerPoint) with no extractable text has
    no image fallback - unlike a scanned PDF, there's no page image to
    rasterize - so that case degrades to status "error" with a plain
    explanation, same graceful-degradation principle as everywhere else."""
    if extraction_status == "extracted" and extracted_text:
        return summarize_policy(text=extracted_text)
    if extraction_status == "no_text_found" and filepath.lower().endswith(".pdf"):
        images = render_pdf_pages_to_images(filepath)
        if not images:
            return None, "error", "Couldn't render the scanned PDF's pages to images."
        return summarize_policy(page_images=images)
    if extraction_status == "no_text_found":
        return None, "error", "No readable text was found in this document, so there's nothing to summarise from."
    if extraction_status == "unsupported_format":
        return None, "error", "This file format isn't read automatically yet - re-save as PDF, Word, Excel or PowerPoint to get an AI summary."
    return None, "error", "Couldn't read the file itself, so there's nothing to summarise from."
