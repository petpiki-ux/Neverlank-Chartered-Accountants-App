"""Text extraction for the Firm Library upgrade (Policies and Procedures -
see hr.py/models.PolicyDocument), extending the app's existing PDF-only
extraction (sanctions_data.extract_pdf_text) to Word, Excel and PowerPoint,
so an uploaded policy/procedure of any of those types can be chunked
(policy_chunking - reuses legislation_chunking.chunk_text) and searched/
summarised (policy_search.py, policy_summary.py) the same way a filed Act or
Notice already is.

Every extractor returns (text, status, page_count) - the exact shape
sanctions_data.extract_pdf_text already uses - so callers don't need to
special-case the file type once extraction is done:
  - "extracted": readable text was found
  - "no_text_found": the file opened fine but had no text in it (an empty
    document, or a slide deck of images only) - kept and listed, just not
    searched/summarised automatically
  - "error": the file couldn't be opened/parsed as that format at all
  - "unsupported_format": a type this app doesn't read text from directly
    yet (the legacy binary .doc/.xls/.ppt formats) - the file itself is
    still saved and downloadable, same graceful-degradation principle as
    every other AI-assisted feature in this app; re-saving as .docx/.xlsx/
    .pptx (or PDF) picks up extraction on the next upload.
`page_count` is a rough per-type analogue (sheet count for Excel, slide
count for PowerPoint) rather than a literal page count - it's informational
only, never relied on for anything.

Deliberately dependency-light: python-docx/openpyxl/python-pptx are all
pure-Python (no native binaries, no system package), so this works
identically on Render and inside the packaged offline .exe, same constraint
every other extraction/AI feature in this app is already built to respect
(see config.py's _is_frozen())."""
import docx
import openpyxl
import pptx

import sanctions_data

EXTRACTABLE_EXTENSIONS = {"pdf", "docx", "xlsx", "xlsm", "pptx"}


def extract_docx_text(filepath):
    """Paragraph and table text, in document order. Headers/footers aren't
    read (python-docx doesn't expose them without extra XML digging) - a
    reasonable gap for a first pass, since the body text is what a research
    question is almost always actually looking for."""
    try:
        document = docx.Document(filepath)
        parts = []
        for para in document.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        text = "\n".join(parts)
        if text.strip():
            return text, "extracted", None
        return "", "no_text_found", None
    except Exception:
        return "", "error", None


def extract_xlsx_text(filepath):
    """Every sheet's cell values, row by row, tagged with the sheet name -
    read_only mode so a large workbook doesn't get fully loaded into memory
    just to pull its text out. data_only=True reads a formula's last
    calculated value rather than the formula text itself, since that's what
    a research question actually wants."""
    try:
        workbook = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
        try:
            parts = []
            for sheet in workbook.worksheets:
                sheet_lines = [f"[Sheet: {sheet.title}]"]
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
                    if cells:
                        sheet_lines.append(" | ".join(cells))
                if len(sheet_lines) > 1:
                    parts.append("\n".join(sheet_lines))
            text = "\n\n".join(parts)
            sheet_count = len(workbook.worksheets)
        finally:
            workbook.close()
        if text.strip():
            return text, "extracted", sheet_count
        return "", "no_text_found", sheet_count
    except Exception:
        return "", "error", None


def extract_pptx_text(filepath):
    """Every slide's shape text and table cells, plus its speaker notes if
    any, tagged with the slide number."""
    try:
        presentation = pptx.Presentation(filepath)
        parts = []
        for index, slide in enumerate(presentation.slides, start=1):
            slide_lines = [f"[Slide {index}]"]
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    slide_lines.append(shape.text_frame.text)
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if cells:
                            slide_lines.append(" | ".join(cells))
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                slide_lines.append("Notes: " + slide.notes_slide.notes_text_frame.text)
            if len(slide_lines) > 1:
                parts.append("\n".join(slide_lines))
        text = "\n\n".join(parts)
        slide_count = len(presentation.slides)
        if text.strip():
            return text, "extracted", slide_count
        return "", "no_text_found", slide_count
    except Exception:
        return "", "error", None


def extract_text_for_file(filepath, ext):
    """Dispatches to the right extractor by (lowercased) file extension.
    Returns (text, status, page_count) - see the module docstring for the
    status values, including "unsupported_format" for anything this app
    doesn't read text from directly (legacy .doc/.xls/.ppt, or any other
    extension)."""
    ext = (ext or "").lower()
    if ext == "pdf":
        return sanctions_data.extract_pdf_text(filepath)
    if ext == "docx":
        return extract_docx_text(filepath)
    if ext in ("xlsx", "xlsm"):
        return extract_xlsx_text(filepath)
    if ext == "pptx":
        return extract_pptx_text(filepath)
    return "", "unsupported_format", None
