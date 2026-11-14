"""Generates downloadable Word (.docx) and Excel (.xlsx) working papers.

Used by the Finalisation tab (financial statements, trial balance &
adjustments, management representation letter, forensic investigation
report) and the Substantive Procedures tab (the audit/investigative
programme itself, one workpaper per area/category).

Deliberately built the same way financials.py is: each function takes plain
data already computed/stored elsewhere (an engagement, plus whatever
record(s) it needs) and returns an io.BytesIO ready to hand to Flask's
send_file. Every figure embedded here is the SAME figure already computed by
financials.py and shown on screen - nothing is recalculated independently -
so an exported file can never disagree with what the app displays.
"""
import io
import os
from datetime import date

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak,
)

from models import DEFAULT_WORKPAPER_NARRATIVE_BODIES, default_workpaper_narrative_body, WORKPAPER_SECTIONS, effectively_reviewed, ENTITY_UNDERSTANDING_FIELDS, filing_reference, CASH_FLOW_METHOD_LABELS
from config import Config
import financials as fin
import brand_docs as brand

FIRM_NAME = "Neverlank Chartered Accountants"
FIRM_ADDRESS = "2nd Floor, Michael House, 62 Nelson Mandela Avenue, Harare, Zimbabwe"

_GOLD = RGBColor(0xB8, 0x93, 0x4A)
_GOLD_HEX = "B8934A"
_NAVY_HEX = "1F2A44"

_LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "img", "neverlank-logo.png")   # this part sits one folder down in workpapers/


def _fmt_date(d):
    return d.strftime("%d %B %Y") if d else "—"


def _fmt_num(n):
    n = n or 0.0
    return f"({abs(n):,.2f})" if n < 0 else f"{n:,.2f}"


def _fmt_whole(n):
    """A figure of the financial statements: a whole number, thousands separated, a negative in brackets (-0.4 prints as 0, not (0))."""
    from financials import whole
    n = whole(n)
    return f"({abs(n):,.0f})" if n < 0 else f"{n:,.0f}"


def _col_widths(doc, table, fractions):
    """Fix a statement table's column widths as fractions of the usable page width (a wide label column, narrower figure columns)."""
    from docx.shared import Inches
    from docx.oxml import OxmlElement
    total = brand.usable_width_in(doc)
    widths = [total * f for f in fractions]
    table.autofit = False
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)
    for i, w in enumerate(widths):
        table.columns[i].width = Inches(w)
    for row in table.rows:
        cells = row.cells
        if len(cells) > 1 and cells[0]._tc is cells[-1]._tc:
            continue
        for c, w in zip(cells, widths):
            c.width = Inches(w)


def _right_align(table, first_col=1, skip_cols=()):
    """Right-align the figure columns of a statement table (header included) - the label column stays on the left. A merged section row
    is left alone."""
    for row in table.rows:
        cells = row.cells
        if len(cells) > 1 and cells[0]._tc is cells[-1]._tc:
            continue
        for i, cell in enumerate(cells):
            if i >= first_col and i not in skip_cols:
                for p in cell.paragraphs:
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT


# ---------------------------------------------------------------- Word helpers

def _new_document(title, engagement, subtitle=None, doc=None):
    """A Word document in the firm's invoice-style design: navy letterhead band
    (logo, firm name, gold strapline, address; this working paper's title on the
    right), a details strip (client, engagement, period) and a branded footer.
    Pass an existing `doc` (e.g. one that already has a client-facing cover page
    and Client Details/Table of Contents page ahead of this letterhead - see
    build_financial_statements_docx) to append the letterhead to it instead of
    starting a fresh document."""
    if doc is None:
        doc = Document()
    brand.setup_document(doc)
    brand.branded_footer(doc)
    brand.letterhead_band(doc, title, sub=subtitle)
    client_name = engagement.client.name if engagement.client else ""
    brand.meta_strip(doc, [
        ("Client", client_name),
        ("Engagement", engagement.title),
        ("Period ended", _fmt_date(engagement.period_end)),
        ("Prepared", _fmt_date(date.today())),
    ])
    return doc


def _bookmark(paragraph, name, doc):
    """Wrap a paragraph's content in a Word bookmark (the Table of Contents page numbers refer to it)."""
    from docx.oxml import OxmlElement
    doc._bm_seq = getattr(doc, "_bm_seq", 100) + 1
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(doc._bm_seq))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(doc._bm_seq))
    p = paragraph._p
    ppr = p.find(qn("w:pPr"))
    if ppr is not None:
        ppr.addnext(start)
    else:
        p.insert(0, start)
    p.append(end)


def _add_heading(doc, text, level=1):
    h = brand.heading(doc, text, level=level)
    targets = getattr(doc, "_fs_toc", None)
    if targets and level == 1:
        for title in list(targets):
            if text == title or (title == "Statement of Cash Flows" and text.startswith(title)):
                _bookmark(h, targets.pop(title), doc)
                break
    return h


def _style_table(table):
    return brand.style_table(table)


def _finish(doc):
    brand.fix_document(doc)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _statement_banner(doc, label):
    """Under a statement's heading: the entity's name and the period the statement covers - "as at <date>" for the Statement of
    Financial Position, "for the year ended <date>" for the others."""
    ctx = getattr(doc, "_fs_ctx", None)
    if not ctx:
        return
    name, period_end = ctx
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(name or "")
    r.bold = True
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(("as at " if label == "as_at" else "for the year ended ") + _fmt_date(period_end))
    r.italic = True


def _statement_table(doc, title, headers, row_specs, level=1, period="year_ended", new_page=True):
    """Builds ONE continuous Word table for a whole financial statement -
    used for the Statement of Financial Position, the Statement of Profit
    or Loss, and the Statement of Cash Flows, matching how the Finalisation
    tab shows each of them on screen (a single table with bold section-title
    and subtotal rows running straight down, not a separate table per
    section with paragraph breaks in between).

    `row_specs` is an ordered list of dicts, each either:
      {"section": "Non-current assets"} - a bold row spanning every column
      {"cells": [...], "bold": bool, "muted": bool} - a normal/total/memo row,
        one string per column in `headers`' order
    """
    if new_page:
        doc.add_page_break()
    _add_heading(doc, title, level=level)
    _statement_banner(doc, period)
    table = doc.add_table(rows=1, cols=len(headers))
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
    _style_table(table)
    for spec in row_specs:
        cells = table.add_row().cells
        if "section" in spec:
            merged = cells[0]
            for c in cells[1:]:
                merged = merged.merge(c)
            merged.text = spec["section"]
            for p in merged.paragraphs:
                for run in p.runs:
                    run.bold = True
            continue
        for i, text in enumerate(spec["cells"]):
            cells[i].text = text
        if spec.get("bold") or spec.get("muted"):
            for c in cells:
                for p in c.paragraphs:
                    for run in p.runs:
                        if spec.get("bold"):
                            run.bold = True
                        if spec.get("muted"):
                            run.italic = True
    _col_widths(doc, table, [0.46, 0.10, 0.22, 0.22] if len(headers) == 4 else [0.62, 0.10, 0.28])
    _right_align(table, first_col=2)
    for row in table.rows:                                  # the Note reference sits centred between the label and the figures
        if len(row.cells) > 2 and row.cells[0]._tc is not row.cells[-1]._tc:
            for p in row.cells[1].paragraphs:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()
    return table


def _fs_row_cells(r, with_prior=True):
    """A statement row (see financials._row) as this document's cell texts:
    label, Note (blank if the row has none), current year, and - unless
    with_prior is False (the Cash Flow Statement, current year only) -
    prior year."""
    cells = [r["label"], str(r.get("note") or ""), _fmt_whole(r["current"])]
    if with_prior:
        cells.append(_fmt_whole(r.get("prior", 0)))
    return cells


# ---------------------------------------------------------------- Excel helpers

_HEADER_FONT, _HEADER_FILL, _HEADER_ALIGN, _BORDER = brand.xlsx_header_style()


def _new_workbook_sheet(wb_title, engagement, sheet_title="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    client_name = engagement.client.name if engagement.client else ""
    details = f"{client_name} — {engagement.title}"
    if engagement.period_end:
        details += f"  ·  Period ended {_fmt_date(engagement.period_end)}"
    brand.xlsx_title_band(ws, wb_title, [details], ncols=8)
    brand.xlsx_page_setup(ws)
    return wb, ws


def _header_row(ws, row, headers, start_col=1):
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=start_col + i, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGN
        cell.border = _BORDER
    return row + 1


def _autofit(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _finish_wb(wb):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ================================================================= Financial statements (Word)

def _plain_cover_page(doc, engagement):
    """The very first page of the Financial Statements themselves - the
    CLIENT's own identity (logo if one has been uploaded on the Client
    detail page, name, reporting period), not the firm's letterhead (that
    still follows, on its own page, ahead of the primary statements - see
    build_financial_statements_docx). Renders cleanly with no logo at all
    for a client who hasn't uploaded one yet."""
    client = engagement.client
    logo_path = None
    if client and client.logo_filename:
        candidate = os.path.join(Config.CLIENT_LOGOS_DATA_DIR, client.logo_filename)
        if os.path.exists(candidate):
            logo_path = candidate

    for _ in range(3):
        doc.add_paragraph()

    if logo_path:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        try:
            run.add_picture(logo_path, height=Inches(1.4))
        except Exception:
            pass
        doc.add_paragraph()

    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run(client.name if client else "")
    name_run.bold = True
    name_run.font.size = Pt(22)

    doc.add_paragraph()

    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run("Financial Statements")
    title_run.bold = True
    title_run.font.size = Pt(18)
    title_run.font.color.rgb = _GOLD

    period_p = doc.add_paragraph()
    period_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    period_run = period_p.add_run(f"for the year ended {_fmt_date(engagement.period_end)}")
    period_run.font.size = Pt(13)

    doc.add_page_break()


def _cover_page(doc, engagement):
    """The designed cover (cover_pages.py / cover_kit.py): full-page artwork in the design and colours chosen for this engagement, placed behind
    the page so the margins and the rest of the pack are untouched. The first page carries no footer. If covers are switched off for this
    engagement the plain cover is used; if the artwork cannot be made for any reason (an unreadable logo, a missing font library) the earlier
    cover_art design is tried and then the plain one - a pack is never lost to a cover."""
    try:
        import cover_pages
        if not cover_pages.is_enabled("fs", engagement):
            return _plain_cover_page(doc, engagement)
        if cover_pages.word_cover(doc, "fs", engagement, alt=f"Cover page: {engagement.client.name if engagement.client else ''} - Financial Statements for the year ended {_fmt_date(engagement.period_end)}"):
            return
    except Exception:
        pass
    try:
        import cover_art
        sec = doc.sections[0]
        page_w, page_h = int(sec.page_width), int(sec.page_height)
        png, pal = cover_art.render_for_engagement(engagement, aspect=page_h / float(page_w))
        import cover_pages
        cover_pages.place_word_picture(doc, png, "Financial statements cover")
        doc.add_page_break()
    except Exception:
        # take back anything half-added to the body, then fall back
        body = doc.element.body
        for child in list(body):
            if child.tag != qn("w:sectPr"):
                body.remove(child)
        _plain_cover_page(doc, engagement)



def _start_notes_section(doc, engagement):
    """The Notes to the Financial Statements begin on a new page in their own section, whose running header repeats on every notes page:
    the entity's name, "NOTES TO THE FINANCIAL STATEMENTS" and the year-end line, over a thin blue rule. The footer stays the pack's footer."""
    from docx.enum.section import WD_SECTION
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    sec.different_first_page_header_footer = False          # the cover's "no header/footer on page 1" is not inherited
    client = engagement.client
    brand.statement_page_header(sec, [
        (client.name if client else "").upper(),
        "NOTES TO THE FINANCIAL STATEMENTS",
        f"For the year ended {_fmt_date(engagement.period_end)}",
    ])


def _end_notes_section(doc):
    """What follows the notes (the app's own sign-off record) is not a note: it goes in a section with no running header."""
    from docx.enum.section import WD_SECTION
    sec = doc.add_section(WD_SECTION.CONTINUOUS)
    sec.different_first_page_header_footer = False
    brand.blank_page_header(sec)


def _client_details_and_toc_page(doc, engagement, client_key_people, toc_sections):
    """Client Details (registered name, company number, registered address,
    directors, company secretary) and a Table of Contents, together on the
    page immediately after the cover page - the reader's first look at who
    the entity is and what the pack contains, before the letterhead/primary
    statements that follow."""
    client = engagement.client
    _add_heading(doc, "Client Details", level=1)

    def detail_row(label, value):
        p = doc.add_paragraph()
        run = p.add_run(f"{label}: ")
        run.bold = True
        p.add_run(value or "—")

    detail_row("Registered name", client.name if client else None)
    detail_row("Company registration number", client.company_number if client else None)
    detail_row("Registered address", client.address if client else None)
    detail_row("Industry", client.industry if client else None)
    detail_row("Reporting period ended", _fmt_date(engagement.period_end))

    directors = [p for p in (client_key_people or []) if p.get("role") == "Director"]
    secretaries = [p for p in (client_key_people or []) if p.get("role") == "Company Secretary"]
    detail_row("Director(s)", ", ".join(p["full_name"] for p in directors) if directors else None)
    detail_row("Company Secretary", ", ".join(p["full_name"] for p in secretaries) if secretaries else None)
    detail_row("Main bankers", "; ".join(fin.bankers_list(client)) if client else None)
    detail_row("Auditors", f"{FIRM_NAME}, {FIRM_ADDRESS}")

    doc.add_paragraph()
    _add_heading(doc, "Table of Contents", level=1)
    _toc_entries(doc, toc_sections)

    doc.add_page_break()


def _toc_entries(doc, toc_sections):
    """One line per section: the title, a dot leader and the page the section starts on. The page is a Word PAGEREF field to a bookmark on
    the section's heading, so it is the real page of the finished document; Word fills the numbers in when the file is opened (the pack asks
    for the fields to be updated), and F9 refreshes them after any edit."""
    from docx.oxml import OxmlElement
    from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
    sec = doc.sections[0]
    width = sec.page_width - sec.left_margin - sec.right_margin
    targets = {}
    for i, title in enumerate(toc_sections, start=1):
        name = f"FS_TOC_{i}"
        targets[title] = name
        p = doc.add_paragraph()
        p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        p.add_run(f"{i}.  {title}\t")
        for kind, text in (("begin", None), ("instr", f" PAGEREF {name} \\h "), ("separate", None), ("text", "\u2013"), ("end", None)):
            r = p.add_run()
            if kind == "instr":
                el = OxmlElement("w:instrText")
                el.set(qn("xml:space"), "preserve")
                el.text = text
                r._r.append(el)
            elif kind == "text":
                r.text = text
            else:
                fc = OxmlElement("w:fldChar")
                fc.set(qn("w:fldCharType"), kind)
                r._r.append(fc)
    doc._fs_toc = targets
    # ask Word to refresh the fields (the page numbers) when the file is opened
    settings = doc.settings.element
    if settings.find(qn("w:updateFields")) is None:
        uf = OxmlElement("w:updateFields")
        uf.set(qn("w:val"), "true")
        settings.append(uf)


def _directors_statement_section(doc, directors_statement, engagement=None):
    _add_heading(doc, "Directors' Statement", level=1)
    doc.add_paragraph((directors_statement.statement_text if directors_statement else None) or fin.default_directors_statement_text(engagement))
    doc.add_paragraph()
    sig_table = doc.add_table(rows=3, cols=2)
    d1_name = (directors_statement.director1_name if directors_statement else None) or "_______________________"
    d1_title = (directors_statement.director1_title if directors_statement and directors_statement.director1_title else None) or "Director"
    d2_name = (directors_statement.director2_name if directors_statement else None) or "_______________________"
    d2_title = (directors_statement.director2_title if directors_statement and directors_statement.director2_title else None) or "Director"
    sig_table.rows[0].cells[0].text = "_______________________"
    sig_table.rows[0].cells[1].text = "_______________________"
    sig_table.rows[1].cells[0].text = d1_name
    sig_table.rows[1].cells[1].text = d2_name
    sig_table.rows[2].cells[0].text = d1_title
    sig_table.rows[2].cells[1].text = d2_title
    doc.add_paragraph()
    date_p = doc.add_paragraph()
    date_p.add_run("Date: " + _fmt_date(directors_statement.statement_date if directors_statement else None))
    doc.add_page_break()



def _opinion_signoff(doc, report_date):
    """The signing block under the auditor's/reviewer's report: a line to sign on, then the signing auditor, the PAAB / ICAZ numbers, the firm
    with its PAAB firm number, the place and the date of the report. Kept together on one page."""
    for _ in range(2):                                   # room for the signature
        sp = doc.add_paragraph()
        sp.paragraph_format.space_after = Pt(0)
        sp.paragraph_format.keep_with_next = True
    lines = [
        "\u2026" * 42,
        brand.AUDITOR_SIGNOFF_NAME,
        f"(PAAB Practice Certificate No {brand.AUDITOR_PAAB_CERT_NO}, ICAZ No {brand.AUDITOR_ICAZ_NO})",
        f"{FIRM_NAME} PAAB Firm No. {brand.FIRM_PAAB_NO}",
        "Harare, Zimbabwe",
        _fmt_date(report_date),
    ]
    for i, text in enumerate(lines):
        p = doc.add_paragraph(text)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.keep_with_next = i < len(lines) - 1


def _audit_opinion_section(doc, audit_opinion, engagement=None):
    is_audit = (audit_opinion.report_basis if audit_opinion else "audit") != "review"
    # The auditor's / reviewer's report is the firm's own: it starts under the Neverlank letterhead. The financial statements that follow are the client's and are not.
    client = engagement.client if engagement is not None else None
    brand.letterhead_band(doc, "Independent Auditor's Report" if is_audit else "Independent Reviewer's Report",
                          sub=(f"{client.name} - year ended {_fmt_date(engagement.period_end)}" if client else None))
    _add_heading(doc, "Independent Auditor's Report" if is_audit else "Independent Reviewer's Report", level=1)
    if not audit_opinion:
        doc.add_paragraph("Not yet prepared within the app.")
        doc.add_page_break()
        return
    modification_heading = "Opinion" if audit_opinion.modification == "unmodified" else f"{audit_opinion.modification.capitalize()} {'Opinion' if is_audit else 'Conclusion'}"
    _add_heading(doc, modification_heading, level=2)
    doc.add_paragraph(audit_opinion.opinion_paragraph or "")
    if audit_opinion.modification != "unmodified":
        _add_heading(doc, f"Basis for {modification_heading}", level=2)
    else:
        _add_heading(doc, "Basis for Opinion" if is_audit else "Basis for Conclusion", level=2)
    doc.add_paragraph(audit_opinion.basis_paragraph or "")
    _add_heading(doc, "Directors' Responsibility for the Financial Statements", level=2)
    doc.add_paragraph(audit_opinion.management_responsibility_paragraph or "")
    _add_heading(doc, "Auditor's Responsibility" if is_audit else "Reviewer's Responsibility", level=2)
    doc.add_paragraph(audit_opinion.auditor_responsibility_paragraph or "")
    _opinion_signoff(doc, audit_opinion.report_date)
    doc.add_page_break()


def _years(doc):
    """The two year headings of the statements and notes ("2025\nUSD", "2024\nUSD"); "Current year" / "Prior year" if not known."""
    return tuple(getattr(doc, "_fs_years", None) or ("Current year", "Prior year"))


def _dash(v):
    """A figure in a free-form note table: whole numbers with thousands separators, brackets for negatives, a dash for nil / not known."""
    return "-" if v is None or abs(v) < 0.5 else _fmt_whole(v)


def _plain_note_table(doc, headers, fractions):
    """A borderless table for the notes drawn like the firm's own working layouts (heading row of years and currency, labelled rows,
    a single rule above a subtotal and a double rule under the total). Returns (table, add_row)."""
    table = doc.add_table(rows=1, cols=len(headers))
    for i, h in enumerate(headers):
        c = table.rows[0].cells[i]
        c.text = ""
        parts = h.split(" ", 1)
        p = c.paragraphs[0]
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i else WD_ALIGN_PARAGRAPH.LEFT
        if h:
            p.add_run(parts[0]).bold = True
            if len(parts) > 1:
                p.add_run("\n" + parts[1]).bold = True

    def add_row(cells, bold=False, rule=None):
        row = table.add_row().cells
        for i, v in enumerate(cells):
            row[i].text = v
            for p in row[i].paragraphs:
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(2)
                if i:
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                for r in p.runs:
                    r.bold = bold
                    r.font.size = Pt(9.5)
            if i and rule == "single":
                brand.cell_borders(row[i], top=("single", 6, "000000"))
            if i and rule == "double":
                brand.cell_borders(row[i], top=("single", 6, "000000"), bottom=("double", 12, "000000"))
            if i and rule == "double_only":
                brand.cell_borders(row[i], bottom=("double", 12, "000000"))
        return row
    _col_widths(doc, table, fractions)
    brand.table_cell_margins(table)          # the same cell padding, centring and type size as the other note tables, so the figures line up
    from docx.enum.table import WD_TABLE_ALIGNMENT
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for row in table.rows:
        for c in row.cells:
            for p in c.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9.5)
    return table, add_row


def _lines_layout(doc, lay):
    """Draws a note laid out as `lay["lines"]` (see client_notes): label column and one figure column per year, the same widths as every
    other note table so the two years line up from note to note."""
    table, add = _plain_note_table(doc, [""] + lay["headers"], [0.56, 0.22, 0.22])
    for ln in lay["lines"]:
        st = ln["style"]
        if st == "gap":
            add(["", "", ""])
        elif st == "head":
            add([ln["label"], "", ""], bold=True)
        else:
            rule = {"sub": "single", "total": "double", "double": "double_only"}.get(st)
            add([ln["label"], _dash(ln["cur"]), _dash(ln["pri"])], rule=rule)
    if lay.get("after"):
        para = doc.add_paragraph(lay["after"])
        para.paragraph_format.space_before = Pt(8)
    else:
        doc.add_paragraph()


_loan_note_layout = _share_capital_layout = _lines_layout


def _extra_note_tables(doc, note):
    """The roll-forward tables under a note (movement in deferred tax): a small bold caption and a table. A one-year table keeps the
    same column positions as the two-year note tables (label, current year, an empty prior-year column) so the figures line up."""
    for t in note.get("extra_tables") or []:
        cap = doc.add_paragraph()
        cap.paragraph_format.keep_with_next = True
        cap.add_run(t["title"]).bold = True
        headers = list(t["headers"])
        pad = len(headers) == 2
        if pad:
            headers.append("")
        table = doc.add_table(rows=1, cols=len(headers))
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = _years(doc)[0] if h == "Current year" else h
        _style_table(table)
        last = len(t["rows"]) - 1
        for k, r in enumerate(t["rows"]):
            r = list(r) + ([""] if pad else [])
            cells = table.add_row().cells
            for i, v in enumerate(r):
                cells[i].text = v if isinstance(v, str) else _fmt_whole(v)
                if t.get("total_last") and (k == last or (k == 0 and len(t["headers"]) == 2)):
                    for p in cells[i].paragraphs:
                        for run in p.runs:
                            run.bold = True
        _col_widths(doc, table, [0.56, 0.22, 0.22] if len(headers) == 3 else [0.34, 0.22, 0.22, 0.22])
        _right_align(table, first_col=1)
        doc.add_paragraph()
