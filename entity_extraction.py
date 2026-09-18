"""AI-assisted extraction of Directors/Shareholders/Beneficial Owners/
Company Secretaries from an uploaded company document (see
models.CompanyDocument and models.ClientKeyPerson, and company_documents.py
for the upload route that calls this).

Uses the Claude API (the `anthropic` package) so scanned/photographed
documents can be read the same way a person would, rather than relying on
OCR text alone - a scanned Zimbabwean CR14 or share register varies a lot
in layout and scan quality, and matching it with regex/keyword rules (the
approach sanctions_data.py uses for RBZ/FIU notices, where all that's
needed is "does this name appear anywhere") would be far less reliable for
actually picking out who is a director versus a shareholder versus neither.

Requires an ANTHROPIC_API_KEY environment variable (see the README for how
to set this on Render) - without it, extraction is skipped with a clear
"not_configured" status rather than failing silently or crashing the
upload. Every result is a *suggestion*: see models.ClientKeyPerson.status -
an extracted person always starts as "Suggested" and needs a person to
review and confirm it, the same conservative principle used throughout this
app's other automated/AI-assisted features (sanctions_data.py's matching
never claims a confirmed hit either).
"""
import os
import io
import base64

import anthropic
import pdfplumber

from models import PERSON_ROLES

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90  # seconds - a multi-page scanned document can take a while to read
MAX_PAGES = 5  # cap how many page images are sent in one call - keeps cost/size bounded for a large scanned filing
MAX_TEXT_CHARS = 60000  # generous for a company registration document; truncated defensively beyond this

PEOPLE_TOOL = {
    "name": "record_company_people",
    "description": "Record every director, shareholder, beneficial owner, or company secretary named in this document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "full_name": {"type": "string", "description": "The person's full name, exactly as written in the document."},
                        "role": {"type": "string", "enum": PERSON_ROLES, "description": "Their role. Use 'Other' if none of the listed roles fit."},
                        "number_of_shares": {
                            "type": "string",
                            "description": "The number of shares they hold, exactly as stated. Leave blank if not stated or not applicable to their role.",
                        },
                        "shareholding_percentage": {
                            "type": "string",
                            "description": "Their shareholding as a percentage, exactly as stated. Leave blank if not stated or not applicable to their role - do not calculate one yourself from number_of_shares.",
                        },
                        "id_number": {
                            "type": "string",
                            "description": "National ID or passport number, exactly as stated. Leave blank if not stated.",
                        },
                        "nationality": {
                            "type": "string",
                            "description": "Nationality, exactly as stated. Leave blank if not stated.",
                        },
                        "address": {
                            "type": "string",
                            "description": "Residential or registered address, exactly as stated. Leave blank if not stated.",
                        },
                        "other_notes": {
                            "type": "string",
                            "description": "Any other relevant detail actually stated for this person that doesn't fit the fields above - e.g. date of appointment, or another role they also hold. Leave blank rather than guessing.",
                        },
                    },
                    "required": ["full_name", "role"],
                },
            },
        },
        "required": ["people"],
    },
}

SYSTEM_PROMPT = (
    "You are extracting company officer/ownership information from a company registration "
    "document (for example a Certificate of Incorporation, a CR14 Return of Directors, a CR6, "
    "Memorandum & Articles of Association, or a share register) for an audit firm's Client "
    "Acceptance / AML-KYC records. Read the document carefully and call record_company_people "
    "with every individual actually named as a Director, Shareholder, Beneficial Owner, or "
    "Company Secretary. Use each person's full name exactly as written. If a person holds more "
    "than one of these roles, list them once under the most senior role that applies "
    "(Director > Beneficial Owner > Shareholder > Company Secretary) and note their other "
    "role(s) in other_notes. Fill in number_of_shares, shareholding_percentage, id_number, "
    "nationality and address only when the document actually states them for that person - "
    "leave a field blank rather than guessing or calculating one from another (e.g. never "
    "derive a percentage from a share count yourself). Do NOT invent people, roles, or details "
    "that are not actually stated in the document - if you are not sure a name refers to a real "
    "person holding one of these roles, leave them out. If the document names no such people at "
    "all, call the tool with an empty people list rather than not calling it."
)

SPLIT_TOOL = {
    "name": "record_split_details",
    "description": "Split a free-text note about a company director/shareholder into its component fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "number_of_shares": {"type": "string", "description": "The number of shares held, exactly as stated. Leave blank if the note doesn't state one."},
            "shareholding_percentage": {"type": "string", "description": "The shareholding as a percentage, exactly as stated. Leave blank if the note doesn't state one - do not calculate one from number_of_shares."},
            "id_number": {"type": "string", "description": "National ID or passport number, exactly as stated. Leave blank if the note doesn't state one."},
            "nationality": {"type": "string", "description": "Nationality, exactly as stated. Leave blank if the note doesn't state one."},
            "address": {"type": "string", "description": "Residential or registered address, exactly as stated. Leave blank if the note doesn't state one."},
            "other_notes": {"type": "string", "description": "Anything left over in the note that doesn't fit one of the fields above, preserved in its original wording. Leave blank if the note is fully covered by the fields above."},
        },
        "required": [],
    },
}

SPLIT_SYSTEM_PROMPT = (
    "You are tidying up an existing free-text note about a company director/shareholder into "
    "separate fields, so it matches records created since this app started asking for number of "
    "shares, shareholding percentage, ID number, nationality and address as their own fields "
    "instead of one note. Call record_split_details with whatever the note actually states for "
    "each field. Never invent or calculate a value (e.g. never derive a percentage from a share "
    "count): leave a field blank if the note doesn't state it. Put anything left over that doesn't "
    "fit one of those fields into other_notes, preserving its original wording - if the whole note "
    "is already fully covered by the fields above, leave other_notes blank."
)


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


def render_pdf_pages_to_images(filepath, max_pages=MAX_PAGES, resolution=150):
    """Rasterize the first `max_pages` pages of a PDF to PNG bytes, for a
    scanned/image-only PDF (no extractable text layer) that needs to go to
    Claude as images rather than text. Returns a list of PNG byte strings;
    an empty list (rather than raising) if the PDF can't be opened/rendered."""
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


def extract_people(text=None, page_images=None):
    """Ask Claude to extract Directors/Shareholders/Beneficial Owners/
    Company Secretaries from a company document, given either its
    extracted text (preferred when available - cheaper, and just as
    reliable as images when the PDF has a real text layer) or a list of
    page images (PNG bytes, for a scanned document with no text layer, or a
    photographed page uploaded directly as an image file).

    Returns (people, status, error):
      - status "done": people is a list of {"full_name", "role",
        "shareholding_percentage", "id_number", "nationality", "address",
        "details"} dicts (possibly empty, if the document genuinely names no one)
      - status "not_configured": no ANTHROPIC_API_KEY is set
      - status "error": the request failed or returned something unusable
        (error holds a short message either way)
    Never raises - a failure here should never take down a document upload."""
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return [], "not_configured", "ANTHROPIC_API_KEY is not set - see the README for how to add it on Render."
    if not text and not page_images:
        return [], "error", "No text or page images were available to extract from."

    content = []
    if page_images:
        for img_bytes in page_images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img_bytes).decode("ascii")},
            })
        content.append({"type": "text", "text": "Extract every director/shareholder/beneficial owner/company secretary shown in the page image(s) above."})
    else:
        content.append({"type": "text", "text": f"Extract every director/shareholder/beneficial owner/company secretary from this document text:\n\n{text[:MAX_TEXT_CHARS]}"})

    # Client construction and the API call are both wrapped here - neither
    # should ever be allowed to raise out of this function and take a
    # document upload down with it (a bad/expired key, a network blip, or
    # any other client-side failure all degrade to status "error" instead).
    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[PEOPLE_TOOL],
            tool_choice={"type": "tool", "name": "record_company_people"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        return [], "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_company_people":
            raw_people = (block.input or {}).get("people", [])
            cleaned = []
            for p in raw_people:
                name = (p.get("full_name") or "").strip()
                if not name:
                    continue
                role = p.get("role") if p.get("role") in PERSON_ROLES else "Other"
                cleaned.append({
                    "full_name": name[:200],
                    "role": role,
                    "number_of_shares": (p.get("number_of_shares") or "").strip()[:50],
                    "shareholding_percentage": (p.get("shareholding_percentage") or "").strip()[:50],
                    "id_number": (p.get("id_number") or "").strip()[:100],
                    "nationality": (p.get("nationality") or "").strip()[:100],
                    "address": (p.get("address") or "").strip()[:300],
                    "details": (p.get("other_notes") or "").strip()[:500],
                })
            return cleaned, "done", None
    return [], "error", "The model didn't return a structured result - try again, or add the people manually."


def split_legacy_details(details_text):
    """One-off backfill for a person who already existed before this app had
    separate Shareholding %/ID/Nationality/Address fields: take their old
    free-text 'details' note and split it into those fields via Claude, so
    older records end up with the same structure as newly-extracted ones.

    Returns (fields, status, error):
      - status "done": fields is a {"number_of_shares", "shareholding_percentage",
        "id_number", "nationality", "address", "details"} dict (any of which may be blank)
      - status "not_configured": no ANTHROPIC_API_KEY is set
      - status "error": the request failed, or there was nothing to split
    Never raises - see models.ClientKeyPerson.needs_detail_review, which is
    what marks a split result as still needing a person's check."""
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return None, "not_configured", "ANTHROPIC_API_KEY is not set - see the README for how to add it on Render."
    text = (details_text or "").strip()
    if not text:
        return None, "error", "Nothing to split - the existing note is blank."

    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=512,
            system=SPLIT_SYSTEM_PROMPT,
            tools=[SPLIT_TOOL],
            tool_choice={"type": "tool", "name": "record_split_details"},
            messages=[{"role": "user", "content": f"Note to split:\n\n{text[:2000]}"}],
        )
    except Exception as exc:
        return None, "error", str(exc)[:2000]

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_split_details":
            data = block.input or {}
            return (
                {
                    "number_of_shares": (data.get("number_of_shares") or "").strip()[:50],
                    "shareholding_percentage": (data.get("shareholding_percentage") or "").strip()[:50],
                    "id_number": (data.get("id_number") or "").strip()[:100],
                    "nationality": (data.get("nationality") or "").strip()[:100],
                    "address": (data.get("address") or "").strip()[:300],
                    "details": (data.get("other_notes") or "").strip()[:500],
                },
                "done",
                None,
            )
    return None, "error", "The model didn't return a structured result - try again, or edit the fields manually."


def extract_people_from_document(filepath, is_image, extracted_text, extraction_status):
    """Top-level helper for company_documents.upload_document: decides
    whether to send text or page images based on what extract_pdf_text (for
    a PDF) already found, or sends the uploaded file itself as an image
    directly (for a JPG/PNG upload - a photographed page has no text layer
    to try first). Returns the same (people, status, error) as
    extract_people above."""
    if is_image:
        try:
            with open(filepath, "rb") as f:
                image_bytes = f.read()
        except Exception as exc:
            return [], "error", f"Could not read the uploaded image file: {exc}"[:2000]
        return extract_people(page_images=[image_bytes])

    if extraction_status == "extracted" and extracted_text:
        return extract_people(text=extracted_text)
    if extraction_status == "no_text_found":
        images = render_pdf_pages_to_images(filepath)
        if not images:
            return [], "error", "Couldn't render the scanned PDF's pages to images."
        return extract_people(page_images=images)
    return [], "error", "Couldn't read the PDF file itself, so there's nothing to extract from."
