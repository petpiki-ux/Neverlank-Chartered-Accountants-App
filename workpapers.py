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

FIRM_NAME = "Neverlank Chartered Accountants"
FIRM_ADDRESS = "2nd Floor, Michael House, 62 Nelson Mandela Avenue, Harare, Zimbabwe"

_GOLD = RGBColor(0xB8, 0x93, 0x4A)
_GOLD_HEX = "B8934A"
_NAVY_HEX = "1F2A44"

_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "neverlank-logo.png")


def _fmt_date(d):
    return d.strftime("%d %B %Y") if d else "—"


def _fmt_num(n):
    n = n or 0.0
    return f"({abs(n):,.2f})" if n < 0 else f"{n:,.2f}"


# ---------------------------------------------------------------- Word helpers

def _new_document(title, engagement, subtitle=None, doc=None):
    """A Word document with the firm's letterhead (logo if available, firm
    name/address) and a title block for this working paper. Pass an
    existing `doc` (e.g. one that already has a client-facing cover page
    and Client Details/Table of Contents page ahead of this letterhead -
    see build_financial_statements_docx) to append the letterhead to it
    instead of starting a fresh document."""
    if doc is None:
        doc = Document()

    section = doc.sections[0]
    section.left_margin = Inches(0.9)
    section.right_margin = Inches(0.9)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    header = doc.add_paragraph()
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if os.path.exists(_LOGO_PATH):
        run = header.add_run()
        try:
            run.add_picture(_LOGO_PATH, height=Inches(0.7))
        except Exception:
            pass

    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run(FIRM_NAME)
    name_run.bold = True
    name_run.font.size = Pt(15)
    name_run.font.color.rgb = _GOLD

    addr_p = doc.add_paragraph()
    addr_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    addr_run = addr_p.add_run(FIRM_ADDRESS)
    addr_run.font.size = Pt(9)
    addr_run.italic = True

    doc.add_paragraph()

    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run(title)
    title_run.bold = True
    title_run.font.size = Pt(14)

    client_name = engagement.client.name if engagement.client else ""
    meta_p = doc.add_paragraph()
    meta_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_run = meta_p.add_run(
        f"{client_name} — {engagement.title}"
        + (f"\nPeriod ended {_fmt_date(engagement.period_end)}" if engagement.period_end else "")
    )
    meta_run.font.size = Pt(11)

    if subtitle:
        sub_p = doc.add_paragraph()
        sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sub_run = sub_p.add_run(subtitle)
        sub_run.italic = True
        sub_run.font.size = Pt(10)

    doc.add_paragraph()
    return doc


def _add_heading(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = _GOLD if level == 1 else RGBColor(0x00, 0x00, 0x00)
    return h


def _style_table(table):
    table.style = "Light Grid Accent 1"
    for cell in table.rows[0].cells:
        for p in cell.paragraphs:
            for r in p.runs:
                r.bold = True
    return table


def _finish(doc):
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _statement_table(doc, title, headers, row_specs, level=1):
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
    _add_heading(doc, title, level=level)
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
    doc.add_paragraph()
    return table


def _fs_row_cells(r, with_prior=True):
    """A statement row (see financials._row) as this document's cell texts:
    label, Note (blank if the row has none), current year, and - unless
    with_prior is False (the Cash Flow Statement, current year only) -
    prior year."""
    cells = [r["label"], str(r.get("note") or ""), _fmt_num(r["current"])]
    if with_prior:
        cells.append(_fmt_num(r.get("prior", 0)))
    return cells


# ---------------------------------------------------------------- Excel helpers

_HEADER_FILL = PatternFill(start_color=_GOLD_HEX, end_color=_GOLD_HEX, fill_type="solid")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_THIN = Side(style="thin", color="CCCCCC")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _new_workbook_sheet(wb_title, engagement, sheet_title="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    client_name = engagement.client.name if engagement.client else ""
    ws["A1"] = FIRM_NAME
    ws["A1"].font = Font(bold=True, size=14, color=_GOLD_HEX)
    ws["A2"] = FIRM_ADDRESS
    ws["A2"].font = Font(italic=True, size=9)
    ws["A3"] = wb_title
    ws["A3"].font = Font(bold=True, size=12)
    ws["A4"] = f"{client_name} — {engagement.title}"
    if engagement.period_end:
        ws["A5"] = f"Period ended {_fmt_date(engagement.period_end)}"
    return wb, ws


def _header_row(ws, row, headers, start_col=1):
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=start_col + i, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
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

def _cover_page(doc, engagement):
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
    detail_row("Auditors", f"{FIRM_NAME}, {FIRM_ADDRESS}")

    doc.add_paragraph()
    _add_heading(doc, "Table of Contents", level=1)
    for i, section_title in enumerate(toc_sections, start=1):
        doc.add_paragraph(f"{i}.  {section_title}")

    doc.add_page_break()


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


def _audit_opinion_section(doc, audit_opinion):
    is_audit = (audit_opinion.report_basis if audit_opinion else "audit") != "review"
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
    doc.add_paragraph()
    doc.add_paragraph(FIRM_NAME)
    doc.add_paragraph(FIRM_ADDRESS)
    doc.add_paragraph(_fmt_date(audit_opinion.report_date))
    doc.add_page_break()


def build_financial_statements_docx(engagement, statements, financial_statements, directors_statement=None, audit_opinion=None, client_key_people=None):
    show_opinion = engagement.type in ("Audit", "Assurance")

    doc = Document()
    _cover_page(doc, engagement)

    toc_sections = ["Directors' Statement"]
    if show_opinion:
        toc_sections.append("Independent Auditor's Report" if (audit_opinion.report_basis if audit_opinion else "audit") != "review" else "Independent Reviewer's Report")
    toc_sections += [
        "Statement of Financial Position", "Statement of Profit or Loss and Other Comprehensive Income",
        "Statement of Changes in Equity", "Statement of Cash Flows", "Notes to the Financial Statements",
    ]
    _client_details_and_toc_page(doc, engagement, client_key_people, toc_sections)

    _directors_statement_section(doc, directors_statement, engagement)
    if show_opinion:
        _audit_opinion_section(doc, audit_opinion)

    doc = _new_document(
        "Financial Statements",
        engagement,
        subtitle=f"Ref. {filing_reference('financials')}" + (" (draft - not yet fully mapped/reviewed)" if not financial_statements or not financial_statements.is_partner_signed else ""),
        doc=doc,
    )

    is_ifrs_framework = engagement.reporting_framework in ("full_ifrs", "ifrs_for_smes")
    if not is_ifrs_framework and financial_statements and financial_statements.basis_of_preparation:
        _add_heading(doc, "Basis of Preparation", level=2)
        doc.add_paragraph(financial_statements.basis_of_preparation)

    def two_col_table(title, rows, level=1):
        _add_heading(doc, title, level=level)
        table = doc.add_table(rows=1, cols=3)
        hdr = table.rows[0].cells
        hdr[0].text = ""
        hdr[1].text = "Current year"
        hdr[2].text = "Prior year"
        _style_table(table)
        for r in rows:
            row = table.add_row().cells
            row[0].text = r["label"]
            row[1].text = _fmt_num(r["current"])
            row[2].text = _fmt_num(r.get("prior", 0))
            if r.get("bold"):
                for c in row:
                    for p in c.paragraphs:
                        for run in p.runs:
                            run.bold = True
        doc.add_paragraph()
        return table

    def movement_table(title, note, level=1):
        # Property, plant and equipment note built from the Asset Register
        # (financials.build_ppe_movement_schedule) instead of a plain list
        # of trial balance accounts - condensed IAS 16.73(e) presentation:
        # one row per asset class reconciling opening to closing NBV.
        _add_heading(doc, title, level=level)
        headers = ["Asset class", "Opening NBV", "Additions", "Disposals", "Depreciation charge", "Closing NBV"]
        table = doc.add_table(rows=1, cols=len(headers))
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = h
        _style_table(table)

        def add_row(label, row, bold=False):
            disposals_nbv = row["disposals_cost"] - row["disposals_acc_dep"]
            values = [
                label, _fmt_num(row["opening_nbv"]), _fmt_num(row["additions"]),
                _fmt_num(-disposals_nbv) if disposals_nbv else _fmt_num(0),
                _fmt_num(-row["charge"]) if row["charge"] else _fmt_num(0),
                _fmt_num(row["closing_nbv"]),
            ]
            cells = table.add_row().cells
            for i, v in enumerate(values):
                cells[i].text = v
                if bold:
                    for p in cells[i].paragraphs:
                        for run in p.runs:
                            run.bold = True

        for row in note["movement"]:
            add_row(row["class_name"], row)
        add_row("Total", note["movement_total"], bold=True)

        rec = note.get("reconciliation")
        if rec:
            # Always shown - links the register's own closing/opening NBV
            # back to the trial balance PPE total on both years, whether or
            # not they agree, per the standard "agree the note to the
            # underlying schedule" step. A non-nil difference is called out
            # as the basis for a proposed adjustment, not silently dropped.
            base = (
                f"Per trial balance: {_fmt_num(rec['tb_current'])} (current year), {_fmt_num(rec['tb_prior'])} (prior year). "
                f"Per Asset Register: {_fmt_num(rec['register_current'])} (current year), {_fmt_num(rec['register_prior'])} (prior year)."
            )
            if rec["ties_out"]:
                p = doc.add_paragraph(base + " Ties out to the trial balance.")
            else:
                p = doc.add_paragraph(
                    base + f" Difference of {_fmt_num(rec['diff_current'])} (current year), {_fmt_num(rec['diff_prior'])} (prior year) "
                    "- basis for a proposed adjustment to the trial balance or a correction to the Asset Register, as appropriate."
                )
            p.runs[0].italic = True
        doc.add_paragraph()

    sfp = statements["sfp"]
    sfp_specs = [{"section": "Non-current assets"}]
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["non_current_assets"]]
    sfp_specs.append({"cells": ["Total non-current assets", "", _fmt_num(sfp["nca_total"]["current"]), _fmt_num(sfp["nca_total"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Current assets"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["current_assets"]]
    sfp_specs.append({"cells": ["Total current assets", "", _fmt_num(sfp["ca_total"]["current"]), _fmt_num(sfp["ca_total"]["prior"])], "bold": True})
    sfp_specs.append({"cells": ["Total assets", "", _fmt_num(sfp["total_assets"]["current"]), _fmt_num(sfp["total_assets"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Equity"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["equity"]]
    sfp_specs.append({"cells": ["Total equity", "", _fmt_num(sfp["total_equity"]["current"]), _fmt_num(sfp["total_equity"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Non-current liabilities"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["non_current_liabilities"]]
    sfp_specs.append({"cells": ["Total non-current liabilities", "", _fmt_num(sfp["ncl_total"]["current"]), _fmt_num(sfp["ncl_total"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Current liabilities"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["current_liabilities"]]
    sfp_specs.append({"cells": ["Total current liabilities", "", _fmt_num(sfp["cl_total"]["current"]), _fmt_num(sfp["cl_total"]["prior"])], "bold": True})
    sfp_specs.append({"cells": ["Total equity and liabilities", "", _fmt_num(sfp["total_equity_and_liabilities"]["current"]), _fmt_num(sfp["total_equity_and_liabilities"]["prior"])], "bold": True})
    # One continuous table, top to bottom, exactly matching the Finalisation
    # tab on screen - not a separate table per section with paragraph
    # breaks in between.
    _statement_table(doc, "Statement of Financial Position", ["", "Note", "Current year", "Prior year"], sfp_specs)
    bc = sfp["balance_check"]
    tie_note = "Ties out." if abs(bc["current"]) <= 0.01 and abs(bc["prior"]) <= 0.01 else "Non-zero - check every account is mapped to the right category and that the trial balance balances."
    doc.add_paragraph(f"Balance check (assets less equity and liabilities): current year {_fmt_num(bc['current'])}, prior year {_fmt_num(bc['prior'])}. {tie_note}").runs[0].italic = True

    pl_specs = [{"cells": _fs_row_cells(r), "bold": r.get("bold", False)} for r in statements["pl"]["rows"]]
    _statement_table(doc, "Statement of Profit or Loss and Other Comprehensive Income", ["", "Note", "Current year", "Prior year"], pl_specs)

    _add_heading(doc, "Statement of Changes in Equity", level=1)
    eq_table = doc.add_table(rows=1, cols=4)
    hdr = eq_table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Component", "Opening", "Movement", "Closing"
    _style_table(eq_table)
    for r in statements["equity"]["rows"]:
        row = eq_table.add_row().cells
        row[0].text = r["label"]
        row[1].text = _fmt_num(r["opening"])
        row[2].text = f"{_fmt_num(r['movement'])} ({r['movement_desc']})"
        row[3].text = _fmt_num(r["closing"])
    doc.add_paragraph()

    cf = statements["cf"]
    method_label = CASH_FLOW_METHOD_LABELS.get(cf["method"], "Indirect method")
    cf_specs = [{"section": "Operating activities"}]
    for r in cf["operating_rows"]:
        cf_specs.append({"cells": _fs_row_cells(r, with_prior=False), "bold": r.get("bold", False), "muted": r.get("memo", False)})
    cf_specs.append({"cells": ["Net cash from operating activities", "", _fmt_num(cf["net_operating"])], "bold": True})
    cf_specs.append({"section": "Investing activities"})
    for r in cf["investing_rows"]:
        cf_specs.append({"cells": _fs_row_cells(r, with_prior=False)})
    cf_specs.append({"cells": ["Net cash used in investing activities", "", _fmt_num(cf["net_investing"])], "bold": True})
    cf_specs.append({"section": "Financing activities"})
    for r in cf["financing_rows"]:
        cf_specs.append({"cells": _fs_row_cells(r, with_prior=False)})
    cf_specs.append({"cells": ["Net cash from financing activities", "", _fmt_num(cf["net_financing"])], "bold": True})
    cf_specs.append({"cells": ["Net increase/(decrease) in cash", "", _fmt_num(cf["net_movement"])], "bold": True})
    cf_specs.append({"cells": ["Cash at beginning of year", "", _fmt_num(cf["cash_open"])]})
    cf_specs.append({"cells": ["Cash at end of year (computed)", "", _fmt_num(cf["cash_close_computed"])], "bold": True})
    cf_specs.append({"cells": ["Cash at end of year (per trial balance)", "", _fmt_num(cf["cash_close_actual"])]})
    _statement_table(doc, f"Statement of Cash Flows ({method_label}, current year)", ["", "Note", "Amount"], cf_specs)
    variance_note = "Ties out to the trial balance." if abs(cf["variance"]) <= 0.01 else f"Computed closing cash differs from the trial balance by {_fmt_num(cf['variance'])} - likely additions/disposals not visible from net asset movements, or linked to the retained earnings continuity check above. Review and adjust."
    doc.add_paragraph(variance_note).runs[0].italic = True

    if is_ifrs_framework:
        _add_heading(doc, "Notes to the Financial Statements", level=1)

        _add_heading(doc, "1. General information", level=2)
        doc.add_paragraph(fin.general_information_note(engagement.client, engagement))

        _add_heading(doc, "2. Basis of preparation", level=2)
        basis_text = (financial_statements.basis_of_preparation if financial_statements else "") or (
            fin.REPORTING_FRAMEWORK_COMPLIANCE_TEXT.get(engagement.reporting_framework, "") + fin.DEFAULT_BASIS_OF_PREPARATION_TAIL
        )
        doc.add_paragraph(basis_text)

        policy_notes = [n for n in statements["notes"] if n.get("policy")]
        if policy_notes:
            _add_heading(doc, "3. Significant accounting policies", level=2)
            for i, note in enumerate(policy_notes, start=1):
                p = doc.add_paragraph()
                run = p.add_run(f"({i}) {note['title']}. ")
                run.bold = True
                p.add_run(note["policy"])

        for note in statements["notes"]:
            if note.get("movement"):
                movement_table(f"{note['number']}. {note['title']}", note, level=2)
                continue
            account_rows = [
                {"label": (f"{a['account_code']} - " if a.get("account_code") else "") + a["account_name"],
                 "current": a["current"], "prior": a["prior"]}
                for a in note["accounts"]
            ] + [{"label": "Total", "current": note["total"]["current"], "prior": note["total"]["prior"], "bold": True}]
            two_col_table(f"{note['number']}. {note['title']}", account_rows, level=2)

        next_num = (statements["notes"][-1]["number"] + 1) if statements["notes"] else 4
        closing_notes = [
            (next_num, "Related party transactions", financial_statements.related_party_note if financial_statements else None, fin.DEFAULT_CLOSING_NOTE_TEXT["related_party"]),
            (next_num + 1, "Contingencies and commitments", financial_statements.commitments_note if financial_statements else None, fin.DEFAULT_CLOSING_NOTE_TEXT["commitments"]),
            (next_num + 2, "Events after the reporting period", financial_statements.subsequent_events_note if financial_statements else None, fin.DEFAULT_CLOSING_NOTE_TEXT["subsequent_events"]),
        ]
        for number, title, saved_text, default_text in closing_notes:
            _add_heading(doc, f"{number}. {title}", level=2)
            doc.add_paragraph(saved_text or default_text)

        if financial_statements and financial_statements.notes_to_financial_statements:
            _add_heading(doc, "Other matters", level=2)
            for para in financial_statements.notes_to_financial_statements.split("\n"):
                doc.add_paragraph(para)
    elif financial_statements and financial_statements.notes_to_financial_statements:
        _add_heading(doc, "Notes to the Financial Statements", level=1)
        for para in financial_statements.notes_to_financial_statements.split("\n"):
            doc.add_paragraph(para)

    _add_heading(doc, "Sign-off", level=2)
    if financial_statements:
        doc.add_paragraph(
            f"Prepared by {financial_statements.completed_by.name if financial_statements.completed_by else '—'} "
            f"on {_fmt_date(financial_statements.completed_at)}."
        )
        if financial_statements.is_reviewed:
            doc.add_paragraph(f"Reviewed by {financial_statements.reviewed_by.name} on {_fmt_date(financial_statements.reviewed_at)}.")
        if financial_statements.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {financial_statements.partner_signed_by.name} on {_fmt_date(financial_statements.partner_signed_at)}.")
    else:
        doc.add_paragraph("Not yet prepared/signed off within the app.")

    return _finish(doc)


# ================================================================= Trial balance & adjustments (Excel)

def build_trial_balance_adjustments_xlsx(engagement, trial_balance):
    wb, ws = _new_workbook_sheet("Trial Balance & Adjustments — Ref. N1000", engagement, "Trial Balance")

    row = 7
    row = _header_row(ws, row, ["Account code", "Account name", "IAS 1 category", "Current debit", "Current credit", "Prior debit", "Prior credit"])
    tb_start = row
    for line in (trial_balance.lines if trial_balance else []):
        ws.cell(row=row, column=1, value=line.account_code or "").border = _BORDER
        ws.cell(row=row, column=2, value=line.account_name).border = _BORDER
        ws.cell(row=row, column=3, value=line.fs_category or "(unmapped)").border = _BORDER
        ws.cell(row=row, column=4, value=line.current_debit or 0).border = _BORDER
        ws.cell(row=row, column=5, value=line.current_credit or 0).border = _BORDER
        ws.cell(row=row, column=6, value=line.prior_debit or 0).border = _BORDER
        ws.cell(row=row, column=7, value=line.prior_credit or 0).border = _BORDER
        row += 1
    tb_end = row - 1
    if tb_end >= tb_start:
        row_totals = row
        ws.cell(row=row_totals, column=2, value="TOTAL").font = Font(bold=True)
        for col in (4, 5, 6, 7):
            letter = get_column_letter(col)
            cell = ws.cell(row=row_totals, column=col, value=f"=SUM({letter}{tb_start}:{letter}{tb_end})")
            cell.font = Font(bold=True)
        row = row_totals + 1

    row += 2
    ws.cell(row=row, column=1, value="Audit Adjustments").font = Font(bold=True, size=12, color=_GOLD_HEX)
    row += 1
    if trial_balance and trial_balance.adjustments:
        for adj in trial_balance.adjustments:
            ws.cell(row=row, column=1, value=f"{adj.reference or '(no ref)'} — {adj.description or ''}").font = Font(bold=True)
            row += 1
            row = _header_row(ws, row, ["Account", "IAS 1 category", "Debit", "Credit"])
            adj_start = row
            for line in adj.lines:
                ws.cell(row=row, column=1, value=line.account_name).border = _BORDER
                ws.cell(row=row, column=2, value=line.fs_category).border = _BORDER
                ws.cell(row=row, column=3, value=line.debit or 0).border = _BORDER
                ws.cell(row=row, column=4, value=line.credit or 0).border = _BORDER
                row += 1
            adj_end = row - 1
            if adj_end >= adj_start:
                ws.cell(row=row, column=1, value="Balanced" if adj.is_balanced else "OUT OF BALANCE").font = Font(
                    bold=True, color="1F7A1F" if adj.is_balanced else "B02A2A"
                )
                row += 1
            prep = f"Prepared by {adj.completed_by.name if adj.completed_by else '—'} on {_fmt_date(adj.completed_at)}."
            if adj.is_reviewed:
                prep += f" Reviewed by {adj.reviewed_by.name} on {_fmt_date(adj.reviewed_at)}."
            if adj.is_partner_signed:
                prep += f" Partner sign-off by {adj.partner_signed_by.name} on {_fmt_date(adj.partner_signed_at)}."
            ws.cell(row=row, column=1, value=prep).font = Font(italic=True, size=9)
            row += 2
    else:
        ws.cell(row=row, column=1, value="No adjustments proposed.")
        row += 1

    if trial_balance:
        row += 1
        prep = f"Trial balance prepared by {trial_balance.completed_by.name if trial_balance.completed_by else '—'} on {_fmt_date(trial_balance.completed_at)}."
        if trial_balance.is_reviewed:
            prep += f" Reviewed by {trial_balance.reviewed_by.name} on {_fmt_date(trial_balance.reviewed_at)}."
        if trial_balance.is_partner_signed:
            prep += f" Partner sign-off by {trial_balance.partner_signed_by.name} on {_fmt_date(trial_balance.partner_signed_at)}."
        ws.cell(row=row, column=1, value=prep).font = Font(italic=True, size=9)

    _autofit(ws, [16, 32, 24, 16, 16, 16, 16])
    return _finish_wb(wb)


# ================================================================= PPE Depreciation policy & Asset Register (Excel)

def build_ppe_asset_register_xlsx(engagement, asset_classes, assets):
    """Exports the two Property, Plant and Equipment working papers kept
    live in the app (Substantive Procedures > Property, Plant and
    Equipment) - the Depreciation policy and the Asset Register - as one
    workbook for filing. Both are already editable directly in the app;
    this is a point-in-time copy for the file, not the working paper's own
    source of truth (that stays in the database, same as every other
    in-app working paper this app exports)."""
    wb, ws = _new_workbook_sheet("PPE Depreciation Policy & Asset Register — Ref. N3300", engagement, "Depreciation policy")

    row = 7
    row = _header_row(ws, row, ["Asset class", "Depreciation method", "Rate (% p.a.)", "Useful life (years)"])
    for c in asset_classes:
        ws.cell(row=row, column=1, value=c.name).border = _BORDER
        ws.cell(row=row, column=2, value=c.depreciation_method or "").border = _BORDER
        ws.cell(row=row, column=3, value=c.rate_percent if c.rate_percent is not None else "").border = _BORDER
        ws.cell(row=row, column=4, value=c.useful_life_years if c.useful_life_years is not None else "").border = _BORDER
        row += 1
    if not asset_classes:
        ws.cell(row=row, column=1, value="No asset classes defined yet.")
        row += 1
    _autofit(ws, [28, 22, 16, 18])

    ws2 = wb.create_sheet("Asset Register")
    ws2["A1"] = FIRM_NAME
    ws2["A1"].font = Font(bold=True, size=14, color=_GOLD_HEX)
    ws2["A2"] = FIRM_ADDRESS
    ws2["A2"].font = Font(italic=True, size=9)
    ws2["A3"] = "PPE Asset Register — Ref. N3300"
    ws2["A3"].font = Font(bold=True, size=12)
    client_name = engagement.client.name if engagement.client else ""
    ws2["A4"] = f"{client_name} — {engagement.title}"
    if engagement.period_end:
        ws2["A5"] = f"Period ended {_fmt_date(engagement.period_end)}"

    headers = [
        "Asset code", "Description", "Asset class", "Date acquired", "Cost",
        "Opening accumulated depreciation", "Depreciation method", "Rate / useful life",
        "Current year depreciation charge", "Disposals - cost", "Disposals - accumulated depreciation",
        "Closing NBV",
    ]
    row = 7
    row = _header_row(ws2, row, headers)
    class_by_id = {c.id: c for c in asset_classes}
    reg_start = row
    for a in assets:
        cls = class_by_id.get(a.asset_class_id)
        values = [
            a.asset_code or "", a.description, cls.name if cls else "Unclassified",
            _fmt_date(a.date_acquired) if a.date_acquired else "",
            a.cost or 0.0, a.opening_accumulated_depreciation or 0.0,
            cls.depreciation_method if cls else "", cls.rate_display if cls else "—",
            a.current_year_depreciation or 0.0, a.disposal_cost or 0.0, a.disposal_accumulated_depreciation or 0.0,
            a.closing_nbv,
        ]
        for i, v in enumerate(values, start=1):
            ws2.cell(row=row, column=i, value=v).border = _BORDER
        row += 1
    reg_end = row - 1
    if reg_end >= reg_start:
        ws2.cell(row=row, column=2, value="TOTAL").font = Font(bold=True)
        for col in (5, 6, 9, 10, 11, 12):
            letter = get_column_letter(col)
            ws2.cell(row=row, column=col, value=f"=SUM({letter}{reg_start}:{letter}{reg_end})").font = Font(bold=True)
    else:
        ws2.cell(row=row, column=1, value="No assets in the register yet.")
    _autofit(ws2, [14, 32, 18, 14, 14, 16, 16, 16, 16, 14, 16, 14])

    return _finish_wb(wb)


# ================================================================= Management representation letter (Word)

def build_rep_letter_docx(engagement, statements, narrative=None):
    doc = _new_document("Management Representation Letter", engagement, subtitle=f"Ref. {filing_reference('rep_letter')}")

    client_name = engagement.client.name if engagement.client else "[Client]"
    period_end = _fmt_date(engagement.period_end)

    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_accounting = engagement.type == "Accounting & Bookkeeping"

    doc.add_paragraph(f"To: {FIRM_NAME}")
    doc.add_paragraph(f"Date: {_fmt_date(date.today())}")
    doc.add_paragraph()
    if is_business_it:
        doc.add_paragraph(
            f"This representation letter is provided in connection with your engagement in respect of the IT "
            f"governance, cyber security, information security and AML/CFT control environment of {client_name} "
            f"for the period ended {period_end}, for the purpose of expressing a conclusion on the matters within "
            f"the scope of that engagement."
        )
    elif is_accounting:
        doc.add_paragraph(
            f"This representation letter is provided in connection with your engagement to prepare and/or compile "
            f"the accounting, financial, management and cost accounting records and reports of {client_name} for "
            f"the period ended {period_end}, encompassing Financial Accounting, Management Accounting, Cost "
            f"Accounting and Tax compliance matters as applicable. This is a compilation/bookkeeping engagement "
            f"and not an audit or review, and accordingly no opinion or assurance conclusion is expressed on the "
            f"information prepared."
        )
    else:
        doc.add_paragraph(
            f"This representation letter is provided in connection with your engagement in respect of the financial "
            f"statements of {client_name} for the period ended {period_end}, for the purpose of expressing an opinion "
            f"as to whether the financial statements give a true and fair view (or present fairly, in all material "
            f"respects) in accordance with the applicable financial reporting framework."
        )
    doc.add_paragraph("We confirm that, to the best of our knowledge and belief, having made such inquiries as we considered necessary for the purpose of appropriately informing ourselves:")

    # The body of representations is a persistent, editable workpaper (see
    # models.WorkpaperNarrative, kind="rep_letter") rather than fixed text -
    # fall back to the firm's (type-appropriate) default wording if nothing
    # has been saved yet - see models.default_workpaper_narrative_body.
    body = (narrative.body if narrative and narrative.body else None) or default_workpaper_narrative_body("rep_letter", engagement)
    for line in body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line, style="List Bullet")

    if statements:
        pbt = statements["pl"]["profit_before_tax"]["current"]
        doc.add_paragraph()
        doc.add_paragraph(f"For reference, profit before tax for the period per the draft financial statements is {_fmt_num(pbt)}.")

    doc.add_paragraph()
    doc.add_paragraph("Signed on behalf of management:")
    doc.add_paragraph()
    doc.add_paragraph("_______________________________")
    doc.add_paragraph("Name / Title / Date")

    if narrative and narrative.completed_by:
        doc.add_paragraph()
        _add_heading(doc, "Sign-off", level=2)
        doc.add_paragraph(f"Prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")

    return _finish(doc)


# ================================================================= Report to Management (Word)

def build_report_to_management_docx(engagement, narrative=None):
    """The Report to Management (RTM) - the standard audit deliverable to
    client management covering internal-control observations and
    recommendations arising during the engagement, distinct from the
    Management Representation Letter above (which is signed BY management,
    not addressed TO them). Its body is a persistent, editable workpaper
    (models.WorkpaperNarrative, kind="report_to_management")."""
    doc = _new_document("Report to Management", engagement, subtitle=f"Ref. {filing_reference('report_to_management')}")

    client_name = engagement.client.name if engagement.client else "[Client]"
    period_end = _fmt_date(engagement.period_end)

    doc.add_paragraph(f"To: The Directors, {client_name}")
    doc.add_paragraph(f"Date: {_fmt_date(date.today())}")
    doc.add_paragraph()
    doc.add_paragraph(
        f"In connection with our engagement in respect of {client_name} for the period ended {period_end}, "
        f"we set out below the matters we wish to bring to the attention of management."
    )
    doc.add_paragraph()

    body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES["report_to_management"]
    for line in body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line, style="List Bullet")

    doc.add_paragraph()
    doc.add_paragraph("Yours faithfully,")
    doc.add_paragraph()
    doc.add_paragraph("_______________________________")
    doc.add_paragraph(FIRM_NAME)

    if narrative and narrative.completed_by:
        doc.add_paragraph()
        _add_heading(doc, "Sign-off", level=2)
        doc.add_paragraph(f"Prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")

    return _finish(doc)


# ================================================================= Tax Opinion / Tax Health Check Report (Word)
# Both are just an ordered set of WorkpaperNarrative parts (see
# models.TAX_OPINION_PARTS / TAX_HEALTH_CHECK_PARTS) rendered as numbered
# sections - one shared builder covers both, parameterised by title/parts.

def _build_tax_sections_docx(engagement, title, filing_kind, parts, narratives_by_kind):
    doc = _new_document(title, engagement, subtitle=f"Ref. {filing_reference(filing_kind)}")

    client_name = engagement.client.name if engagement.client else "[Client]"
    doc.add_paragraph(f"Client: {client_name}")
    doc.add_paragraph(f"Engagement: {engagement.title}")
    doc.add_paragraph(f"Date: {_fmt_date(date.today())}")
    doc.add_paragraph()

    for i, (kind, label) in enumerate(parts, start=1):
        narrative = narratives_by_kind.get(kind)
        _add_heading(doc, f"{i}. {label}", level=2)
        body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES.get(kind, "")
        for line in body.split("\n"):
            line = line.strip()
            if line:
                doc.add_paragraph(line)
        if narrative and narrative.completed_by:
            signoff_bits = [f"Prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}."]
            if narrative.is_reviewed:
                signoff_bits.append(f"Reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
            if narrative.is_partner_signed:
                signoff_bits.append(f"Partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")
            p = doc.add_paragraph(" ".join(signoff_bits))
            for run in p.runs:
                run.italic = True
                run.font.size = Pt(9)
        doc.add_paragraph()

    return _finish(doc)


def build_tax_opinion_docx(engagement, narratives_by_kind):
    """The Tax Opinion (Tax module, Module 10) - its 14 parts are each a
    persistent WorkpaperNarrative (see models.TAX_OPINION_PARTS); this just
    renders them, in order, as one combined Word document."""
    from models import TAX_OPINION_PARTS
    return _build_tax_sections_docx(engagement, "Tax Opinion", "tax_opinion", TAX_OPINION_PARTS, narratives_by_kind)


def build_tax_health_check_docx(engagement, narratives_by_kind):
    """The Tax Health Check Report (Tax module, Module 10) - its 15 parts
    are each a persistent WorkpaperNarrative (see models.
    TAX_HEALTH_CHECK_PARTS); this just renders them, in order, as one
    combined Word document."""
    from models import TAX_HEALTH_CHECK_PARTS
    return _build_tax_sections_docx(engagement, "Tax Health Check Report", "tax_health_check", TAX_HEALTH_CHECK_PARTS, narratives_by_kind)


def build_management_accounts_report_docx(engagement, narratives_by_kind):
    """The Management Accounts Report (Accounting module) - its 10 parts
    are each a persistent WorkpaperNarrative (see models.
    MANAGEMENT_ACCOUNTS_REPORT_PARTS); reuses the same shared section-by-
    section builder as the Tax Opinion/Health Check above, since the shape
    (an ordered set of named, independently signed-off narrative sections)
    is identical."""
    from models import MANAGEMENT_ACCOUNTS_REPORT_PARTS
    return _build_tax_sections_docx(engagement, "Management Accounts Report", "management_accounts_report", MANAGEMENT_ACCOUNTS_REPORT_PARTS, narratives_by_kind)


# ================================================================= Forensic investigation report (Word)

def build_forensic_report_docx(engagement, narrative=None):
    ca = engagement.client_acceptance
    ra = engagement.risk_assessment
    strategy = engagement.audit_strategy
    entity = engagement.entity_understanding
    finalisation = engagement.finalisation_checklist

    doc = _new_document("Forensic Investigation Report", engagement, subtitle="Confidential — Ref. FR-1")

    doc.add_paragraph("Confidential", ).runs[0].bold = True
    doc.add_paragraph(f"Date of report: {_fmt_date(date.today())}")

    # Executive Summary is a persistent, editable workpaper (see models.
    # WorkpaperNarrative, kind="forensic_executive_summary") - everything
    # else in this report stays a live roll-up of the engagement's other
    # tabs, since there's no other free-standing narrative to persist there.
    _add_heading(doc, "Executive Summary")
    summary_body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES["forensic_executive_summary"]
    for line in summary_body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line)

    _add_heading(doc, "1. Background and Mandate")
    if strategy and strategy.objective_statement:
        doc.add_paragraph(strategy.objective_statement)
    else:
        doc.add_paragraph("[Objective statement not yet recorded on the Audit Strategy tab.]")
    if strategy and strategy.reporting_destination:
        doc.add_paragraph(f"Reporting destination: {strategy.reporting_destination}")

    _add_heading(doc, "2. Scope and Legal Framework")
    if strategy and strategy.chain_of_custody_notes:
        doc.add_paragraph(strategy.chain_of_custody_notes)
    if ca and ca.legal_evidence_notes:
        doc.add_paragraph(f"Legal framework & evidence control assessment: {ca.legal_evidence_notes}")

    _add_heading(doc, "3. Preliminary Fraud Theory")
    if strategy:
        if strategy.potential_perpetrators:
            doc.add_paragraph(f"Potential perpetrators: {strategy.potential_perpetrators}")
        if strategy.fraud_vulnerability:
            doc.add_paragraph(f"Control vulnerability exploited: {strategy.fraud_vulnerability}")
        if strategy.evidentiary_red_flags:
            doc.add_paragraph(f"Evidentiary red flags: {strategy.evidentiary_red_flags}")
    else:
        doc.add_paragraph("[Preliminary fraud theory not yet recorded on the Audit Strategy tab.]")

    _add_heading(doc, "4. Risk Assessment Summary")
    if ra and ra.rating:
        doc.add_paragraph(f"Overall fraud risk rating: {ra.rating} (score {ra.score}/25).")
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "High-risk scheme"
        table.rows[0].cells[1].text = "Significance (1-5)"
        _style_table(table)
        for field, label, _ in ra.impact_questions:
            val = getattr(ra, field)
            if val is not None:
                row = table.add_row().cells
                row[0].text = label
                row[1].text = str(val)
    else:
        doc.add_paragraph("[Risk Assessment not yet completed.]")

    _add_heading(doc, "5. Procedures Performed")
    areas = sorted(engagement.substantive_procedure_areas, key=lambda a: a.area) if engagement.substantive_procedure_areas else []
    if areas:
        for area in areas:
            doc.add_paragraph(area.area, style="List Bullet")
            for item in area.items:
                p = doc.add_paragraph(f"{item.procedure_text} — {item.status}", style="List Bullet 2")
                if item.notes:
                    p.add_run(f" ({item.notes})").italic = True
    else:
        doc.add_paragraph("[No substantive/investigative procedures recorded yet.]")

    _add_heading(doc, "6. Findings and Conclusion")
    if finalisation:
        for section_name in ["Findings & Conclusions"]:
            items = [i for i in finalisation.checklist_items if i.section == section_name]
            for item in items:
                p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
                if item.comment:
                    p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Finalisation checklist not yet seeded/completed - see the Finalisation tab.]")

    _add_heading(doc, "7. Sign-off")
    if strategy:
        doc.add_paragraph(
            f"Prepared by {strategy.completed_by.name if strategy.completed_by else '—'} on {_fmt_date(strategy.completed_at)}."
        )
        if strategy.is_reviewed:
            doc.add_paragraph(f"Reviewed by {strategy.reviewed_by.name} on {_fmt_date(strategy.reviewed_at)}.")
        if strategy.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {strategy.partner_signed_by.name} on {_fmt_date(strategy.partner_signed_at)}.")
    if narrative and narrative.completed_by:
        doc.add_paragraph(f"Executive Summary prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Executive Summary reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Executive Summary partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")

    return _finish(doc)


def build_it_audit_report_docx(engagement, narrative=None):
    """The Business Intelligence and IT Engagements equivalent of
    build_forensic_report_docx above - a standalone "IT & Cyber Assurance
    Report" bringing together the engagement's own IT-specific data (Client
    Acceptance's Management Integrity & Cooperativeness questions, the
    Regulatory Landscape entity-understanding answers, the ITGC/Substantive
    procedures actually performed grouped by the four scoped domains, and
    the Finalisation checklist's Findings & Recommendations section) under
    one persistent, editable Executive Summary - see models.
    WorkpaperNarrative, kind="it_audit_report_summary"."""
    ca = engagement.client_acceptance
    entity = engagement.entity_understanding
    finalisation = engagement.finalisation_checklist

    doc = _new_document("IT & Cyber Assurance Report", engagement, subtitle="Confidential")

    doc.add_paragraph("Confidential").runs[0].bold = True
    doc.add_paragraph(f"Date of report: {_fmt_date(date.today())}")

    _add_heading(doc, "Executive Summary")
    summary_body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES["it_audit_report_summary"]
    for line in summary_body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line)

    _directors_statement_section(doc, engagement.directors_statement, engagement)

    _add_heading(doc, "1. Scope and Engagement Acceptance")
    if ca:
        doc.add_paragraph(f"Decision: {ca.decision or 'Not yet decided'}")
        mgmt_items = [i for i in ca.checklist_items if i.section == "Competence - Management Integrity & Cooperativeness"]
        if mgmt_items:
            doc.add_paragraph("Management integrity & cooperativeness (access to logs, source code, configurations, database schemas):")
            for item in mgmt_items:
                p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
                if item.comment:
                    p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Client Acceptance not yet recorded - see the Client Acceptance tab.]")

    _add_heading(doc, "2. Regulatory Landscape & Standards Alignment")
    reg_items = [i for i in entity.checklist_items if i.section == "Regulatory Landscape & Standards Alignment"] if entity else []
    if reg_items:
        for item in reg_items:
            p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
            if item.comment:
                p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Not yet completed - see the Understanding the Entity tab's Regulatory Landscape & Standards Alignment section.]")

    _add_heading(doc, "3. Procedures Performed and Results, by Domain")
    areas = sorted(engagement.substantive_procedure_areas, key=lambda a: a.area) if engagement.substantive_procedure_areas else []
    if areas:
        for area in areas:
            _add_heading(doc, area.area, level=2)
            if area.items:
                table = doc.add_table(rows=1, cols=4)
                hdr = table.rows[0].cells
                hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Procedure", "Kind", "Status", "Notes / Findings"
                _style_table(table)
                for item in area.items:
                    row = table.add_row().cells
                    row[0].text = item.procedure_text
                    row[1].text = item.procedure_kind or ""
                    row[2].text = item.status
                    row[3].text = item.notes or ""
            else:
                doc.add_paragraph("[No procedures recorded yet for this domain.]")
            if area.notes:
                p = doc.add_paragraph()
                p.add_run("Domain findings: ").bold = True
                p.add_run(area.notes)
    else:
        doc.add_paragraph("[No substantive procedures recorded yet - see the Substantive Procedures tab.]")

    _add_heading(doc, "4. Findings, Recommendations and Conclusion")
    if finalisation:
        for section_name in ["Findings & Recommendations"]:
            items = [i for i in finalisation.checklist_items if i.section == section_name]
            for item in items:
                p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
                if item.comment:
                    p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Finalisation checklist not yet seeded/completed - see the Finalisation tab.]")

    _add_heading(doc, "5. Sign-off")
    if narrative and narrative.completed_by:
        doc.add_paragraph(f"Executive Summary prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Executive Summary reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Executive Summary partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")
    else:
        doc.add_paragraph("[Executive Summary not yet prepared - see the Finalisation tab.]")

    return _finish(doc)


# ================================================================= Substantive Procedures programme (Word / Excel)

def build_substantive_procedures_docx(engagement, areas_by_name, area_order, area_refs):
    """areas_by_name: {area_name: SubstantiveProcedureArea or None}
    area_order: the ordered list of area names for this engagement type
    area_refs: {area_name: reference code string}"""
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic:
        title, ref_prefix = "Investigative Procedures Programme", "IP"
    elif is_business_it:
        title, ref_prefix = "IT & Cyber Assurance Procedures Programme", "ITP"
    elif is_secretarial:
        title, ref_prefix = "Secretarial Execution Plan", "EXE"
    else:
        title, ref_prefix = "Substantive Procedures Programme", "SP"
    doc = _new_document(title, engagement, subtitle=f"Ref. {ref_prefix}-1")

    for area_name in area_order:
        area = areas_by_name.get(area_name)
        ref = area_refs.get(area_name, "")
        _add_heading(doc, f"{ref} — {area_name}" if ref else area_name, level=1)
        items = area.items if area else []
        if items:
            # A Secretarial Execution Plan gets three extra columns
            # (Trigger/Event, Lead Responsible, Target Output) matching the
            # firm's Practical Secretarial Execution Plan Schedule; a
            # Business Intelligence and IT Engagement gets one extra column
            # (Kind - ITGC or Substantive, see BUSINESS_IT_PROCEDURE_KIND) so
            # the filed programme reads as the ITGC/Substantive table the
            # methodology calls for. Every other engagement type keeps the
            # original 5-column layout.
            cols = 8 if is_secretarial else (6 if is_business_it else 5)
            table = doc.add_table(rows=1, cols=cols)
            hdr = table.rows[0].cells
            if is_secretarial:
                headers = ["Task", "Trigger / Event", "Lead Responsible", "Target Output", "Source", "Status", "Notes", "Tickmark"]
            elif is_business_it:
                headers = ["Procedure", "Kind", "Source", "Status", "Notes", "Tickmark"]
            else:
                headers = ["Procedure", "Source", "Status", "Notes", "Tickmark"]
            for i, h in enumerate(headers):
                hdr[i].text = h
            _style_table(table)
            for item in items:
                row = table.add_row().cells
                if is_secretarial:
                    row[0].text = item.procedure_text
                    row[1].text = item.trigger_event or ""
                    row[2].text = item.responsible_role or ""
                    row[3].text = item.target_output or ""
                    row[4].text = item.source
                    row[5].text = item.status
                    row[6].text = item.notes or ""
                    row[7].text = item.tickmark.symbol if item.tickmark else ""
                elif is_business_it:
                    row[0].text = item.procedure_text
                    row[1].text = item.procedure_kind or ""
                    row[2].text = item.source
                    row[3].text = item.status
                    row[4].text = item.notes or ""
                    row[5].text = item.tickmark.symbol if item.tickmark else ""
                else:
                    row[0].text = item.procedure_text
                    row[1].text = item.source
                    row[2].text = item.status
                    row[3].text = item.notes or ""
                    row[4].text = item.tickmark.symbol if item.tickmark else ""
        else:
            doc.add_paragraph("No procedures recorded for this area.")
        if area and area.notes:
            # The editable "Area notes" free-text box on the Substantive
            # Procedures tab (see engagements.save_substantive_area_notes) -
            # previously captured in the app but never actually carried
            # through into this generated/filed programme document.
            notes_p = doc.add_paragraph()
            notes_p.add_run("Notes: ").bold = True
            notes_p.add_run(area.notes)
        if area:
            prep = f"Prepared by {area.completed_by.name if area.completed_by else '—'} on {_fmt_date(area.completed_at)}."
            if area.is_reviewed:
                prep += f" Reviewed by {area.reviewed_by.name} on {_fmt_date(area.reviewed_at)}."
            if area.is_partner_signed:
                prep += f" Partner sign-off by {area.partner_signed_by.name} on {_fmt_date(area.partner_signed_at)}."
            note_p = doc.add_paragraph(prep)
            note_p.runs[0].italic = True
            note_p.runs[0].font.size = Pt(9)
        doc.add_paragraph()

    return _finish(doc)


def build_substantive_procedures_xlsx(engagement, areas_by_name, area_order, area_refs):
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic:
        sheet_title = "Investigative Procedures Programme — Ref. IP-1"
    elif is_business_it:
        sheet_title = "IT & Cyber Assurance Procedures Programme — Ref. ITP-1"
    elif is_secretarial:
        sheet_title = "Secretarial Execution Plan — Ref. EXE-1"
    else:
        sheet_title = "Substantive Procedures Programme — Ref. SP-1"
    wb, ws = _new_workbook_sheet(sheet_title, engagement, "Programme")

    row = 7
    if is_secretarial:
        headers = ["Ref.", "Workstream", "Task", "Trigger / Event", "Lead Responsible", "Target Output", "Source", "Status", "Notes", "Tickmark"]
    elif is_business_it:
        headers = ["Ref.", "Area", "Procedure", "Kind", "Source", "Status", "Notes", "Tickmark"]
    else:
        headers = ["Ref.", "Area", "Procedure", "Source", "Status", "Notes", "Tickmark"]
    row = _header_row(ws, row, headers)
    for area_name in area_order:
        area = areas_by_name.get(area_name)
        ref = area_refs.get(area_name, "")
        items = area.items if area else []
        last_col = len(headers)
        if not items:
            ws.cell(row=row, column=1, value=ref).border = _BORDER
            ws.cell(row=row, column=2, value=area_name).border = _BORDER
            ws.cell(row=row, column=3, value="(no procedures recorded)").border = _BORDER
            for col in range(4, last_col + 1):
                ws.cell(row=row, column=col, value="").border = _BORDER
            row += 1
            continue
        for i, item in enumerate(items):
            ws.cell(row=row, column=1, value=ref if i == 0 else "").border = _BORDER
            ws.cell(row=row, column=2, value=area_name if i == 0 else "").border = _BORDER
            ws.cell(row=row, column=3, value=item.procedure_text).border = _BORDER
            if is_secretarial:
                ws.cell(row=row, column=4, value=item.trigger_event or "").border = _BORDER
                ws.cell(row=row, column=5, value=item.responsible_role or "").border = _BORDER
                ws.cell(row=row, column=6, value=item.target_output or "").border = _BORDER
                ws.cell(row=row, column=7, value=item.source).border = _BORDER
                ws.cell(row=row, column=8, value=item.status).border = _BORDER
                ws.cell(row=row, column=9, value=item.notes or "").border = _BORDER
                ws.cell(row=row, column=10, value=item.tickmark.symbol if item.tickmark else "").border = _BORDER
            elif is_business_it:
                ws.cell(row=row, column=4, value=item.procedure_kind or "").border = _BORDER
                ws.cell(row=row, column=5, value=item.source).border = _BORDER
                ws.cell(row=row, column=6, value=item.status).border = _BORDER
                ws.cell(row=row, column=7, value=item.notes or "").border = _BORDER
                ws.cell(row=row, column=8, value=item.tickmark.symbol if item.tickmark else "").border = _BORDER
            else:
                ws.cell(row=row, column=4, value=item.source).border = _BORDER
                ws.cell(row=row, column=5, value=item.status).border = _BORDER
                ws.cell(row=row, column=6, value=item.notes or "").border = _BORDER
                ws.cell(row=row, column=7, value=item.tickmark.symbol if item.tickmark else "").border = _BORDER
            row += 1
        if area and area.notes:
            # Same editable "Area notes" gap fix as build_substantive_
            # procedures_docx above - now carried into the Excel programme too.
            ws.cell(row=row, column=1, value="").border = _BORDER
            ws.cell(row=row, column=2, value="Notes").border = _BORDER
            ws.cell(row=row, column=3, value=area.notes).border = _BORDER
            for col in range(4, last_col + 1):
                ws.cell(row=row, column=col, value="").border = _BORDER
            row += 1

    if is_secretarial:
        _autofit(ws, [8, 24, 45, 22, 16, 30, 12, 14, 26, 10])
    elif is_business_it:
        _autofit(ws, [8, 24, 55, 14, 12, 14, 30, 10])
    else:
        _autofit(ws, [8, 28, 55, 12, 14, 30, 10])
    return _finish_wb(wb)


# ================================================================= Checklist workpapers (Word)
#
# None of the four checklist types (Client Acceptance, Understanding the
# Entity, the main Engagement Checklist, Finalisation) previously had a
# downloadable Word workpaper of their own - only the on-screen tab and the
# Engagement File Summary PDF showed their responses. These four functions
# fill that gap, each as its own workpaper with a table of every response
# plus the section's sign-off.

def _checklist_table_docx(doc, items, per_item_status=False):
    """A docx table of checklist-item rows: Section | Item | Response |
    Comment, and (only for the main Engagement Checklist, whose items each
    carry their own Preparer/Reviewer/Partner sign-off rather than one for
    the whole checklist) a trailing Status column.

    Handles both field-naming conventions across the four checklist-item
    models: "response"/"comment" (Client Acceptance, Entity Understanding,
    Finalisation) and "status"/"notes" (the main Engagement Checklist)."""
    cols = 5 if per_item_status else 4
    table = doc.add_table(rows=1, cols=cols)
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Section", "Item", "Response", "Comment"
    if per_item_status:
        hdr[4].text = "Status"
    _style_table(table)
    for item in items:
        response = getattr(item, "response", None) or getattr(item, "status", None) or "Not assessed"
        comment = getattr(item, "comment", None) or getattr(item, "notes", None) or ""
        row = table.add_row().cells
        row[0].text = item.section or ""
        row[1].text = item.item_text or ""
        row[2].text = response
        row[3].text = comment
        if per_item_status:
            row[4].text = _sign_off_line(item)
    return table


def build_client_acceptance_checklist_docx(engagement, client_acceptance):
    doc = _new_document("Client Acceptance & Continuance - Checklist", engagement, subtitle=f"Ref. {filing_reference('acceptance')}")
    items = client_acceptance.checklist_items if client_acceptance else []
    if items:
        _checklist_table_docx(doc, items)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    _add_heading(doc, "Sign-off", level=2)
    doc.add_paragraph(f"Decision: {client_acceptance.decision if client_acceptance and client_acceptance.decision else 'Not yet decided'}")
    doc.add_paragraph(_sign_off_line(client_acceptance))
    return _finish(doc)


def build_entity_understanding_checklist_docx(engagement, entity_understanding):
    doc = _new_document("Understanding the Entity's Business - Checklist", engagement, subtitle=f"Ref. {filing_reference('entity')}")
    items = entity_understanding.checklist_items if entity_understanding else []
    if items:
        _checklist_table_docx(doc, items)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    _add_heading(doc, "Sign-off", level=2)
    doc.add_paragraph(_sign_off_line(entity_understanding))
    return _finish(doc)


def build_engagement_checklist_docx(engagement):
    doc = _new_document("Engagement Checklist", engagement, subtitle="Ref. F-1")
    items = engagement.checklist_items
    if items:
        _checklist_table_docx(doc, items, per_item_status=True)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    doc.add_paragraph(f"Overall progress: {engagement.checklist_progress}% complete.").runs[0].italic = True
    return _finish(doc)


def build_finalisation_checklist_docx(engagement, finalisation_checklist):
    doc = _new_document("Finalisation Checklist", engagement, subtitle=f"Ref. {filing_reference('finalisation')}")
    items = finalisation_checklist.checklist_items if finalisation_checklist else []
    if items:
        _checklist_table_docx(doc, items)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    _add_heading(doc, "Sign-off", level=2)
    doc.add_paragraph(_sign_off_line(finalisation_checklist))
    return _finish(doc)


# ================================================================= Engagement File Summary (PDF)

def _sign_off_line(record, label="Prepared", preparer_attr="completed_by"):
    """One plain-text status line for any workpaper record carrying the
    standard Preparer/Reviewer/Partner fields (or MaterialityCalculation's
    updated_by/updated_at variant) - "not yet started" if there's no record
    or no preparer yet, otherwise Prepared/Reviewed(-or-exempt)/Partner-
    signed, each only if it applies."""
    if record is None:
        return "Not yet started."
    preparer = getattr(record, preparer_attr, None)
    prepared_at = getattr(record, "completed_at", None) or getattr(record, "updated_at", None)
    if not preparer:
        return "Not yet started."
    parts = [f"{label} by {preparer.name} on {_fmt_date(prepared_at)}."]
    if getattr(record, "is_reviewed", False):
        parts.append(f"Reviewed by {record.reviewed_by.name} on {_fmt_date(record.reviewed_at)}.")
    elif effectively_reviewed(record):
        parts.append("Prepared by a Partner/Admin - no separate reviewer required.")
    else:
        parts.append("Not yet reviewed.")
    if getattr(record, "is_partner_signed", False):
        parts.append(f"Partner sign-off by {record.partner_signed_by.name} on {_fmt_date(record.partner_signed_at)}.")
    return " ".join(parts)


def _checklist_items_table(items):
    """A reportlab Table summarising a list of checklist-item rows (item
    text, response, comment) - used for every checklist-driven workpaper
    section in the file summary below."""
    # EngagementChecklistItem (the main Checklist tab) predates the other
    # three checklist-item models and names its fields differently -
    # "status" (Not Started/In Progress/Done/N/A) instead of "response", and
    # "notes" instead of "comment" - handle both rather than special-casing
    # the main checklist tab's items at every call site.
    data = [["Item", "Response", "Comment"]]
    for item in items:
        response = getattr(item, "response", None) or getattr(item, "status", None) or "Not assessed"
        comment = getattr(item, "comment", None) or getattr(item, "notes", None) or ""
        data.append([
            Paragraph(item.item_text or "", getSampleStyleSheet()["BodyText"]),
            response,
            Paragraph(comment, getSampleStyleSheet()["BodyText"]),
        ])
    table = Table(data, colWidths=[220, 70, 200], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{_GOLD_HEX}")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def build_engagement_file_summary_pdf(engagement):
    """The Engagement File Summary - one PDF pulling together every major
    workpaper section (see models.WORKPAPER_SECTIONS for the fixed A-L
    index), its sign-off status (honouring the Partner-preparer review
    exemption - models.effectively_reviewed), and, for the four checklist-
    driven sections, every response filed under that section's reference -
    the "responses summarised in a PDF with references filed under the
    relevant sections" requested for a completed, reviewed and signed
    engagement. Ends with the tickmark legend for whichever tickmarks were
    actually used on this engagement's Substantive Procedures."""
    is_forensic = engagement.type == "Investigative Engagement"
    narratives = {wn.kind: wn for wn in engagement.workpaper_narratives}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=0.8 * inch, rightMargin=0.8 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
    )
    styles = getSampleStyleSheet()
    center = ParagraphStyle("center", parent=styles["Normal"], alignment=TA_CENTER)
    body = styles["BodyText"]
    gold = colors.HexColor(f"#{_GOLD_HEX}")
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=gold, fontSize=13, spaceBefore=14, spaceAfter=6)
    status_style = ParagraphStyle("status", parent=styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceAfter=8)

    story = []
    if os.path.exists(_LOGO_PATH):
        try:
            img = Image(_LOGO_PATH, width=1.4 * inch, height=0.6 * inch)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 8))
        except Exception:
            pass
    story.append(Paragraph(f"<b><font color='#{_GOLD_HEX}' size=16>{FIRM_NAME}</font></b>", center))
    story.append(Paragraph(f"<font size=9>{FIRM_ADDRESS}</font>", center))
    story.append(Spacer(1, 16))
    story.append(Paragraph("<b>ENGAGEMENT FILE SUMMARY</b>", ParagraphStyle("title", parent=styles["Normal"], alignment=TA_CENTER, fontSize=15)))
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"{engagement.title}", ParagraphStyle("sub", parent=styles["Normal"], alignment=TA_CENTER, fontSize=12)))
    story.append(Paragraph(f"{engagement.client.name if engagement.client else ''} &middot; {engagement.type}", center))
    story.append(Paragraph(f"Period ended {_fmt_date(engagement.period_end)}", center))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"<font size=8 color='grey'>Generated {_fmt_date(date.today())}</font>", center))
    story.append(PageBreak())

    for code, key, label, filing_code in WORKPAPER_SECTIONS:
        if key == "forensic_report" and not is_forensic:
            continue  # the forensic report only exists on Investigative Engagements
        story.append(Paragraph(f"{filing_code or code}. {label}", h1))

        if key == "acceptance":
            record = engagement.client_acceptance
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record:
                decision = record.decision or "Not yet decided"
                story.append(Paragraph(f"Decision: <b>{decision}</b>", body))
                if record.checklist_items:
                    story.append(Spacer(1, 4))
                    story.append(_checklist_items_table(record.checklist_items))
            else:
                story.append(Paragraph("No Client Acceptance record on this engagement yet.", body))

        elif key == "entity":
            record = engagement.entity_understanding
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record:
                if record.checklist_items:
                    story.append(_checklist_items_table(record.checklist_items))
                else:
                    for field, question, _ in ENTITY_UNDERSTANDING_FIELDS:
                        value = getattr(record, field, None)
                        if value:
                            story.append(Paragraph(f"<b>{question}</b> {value}", body))
                            story.append(Spacer(1, 4))
            else:
                story.append(Paragraph("Understanding the Entity's Business not yet started.", body))

        elif key == "risks":
            record = engagement.risk_assessment
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record and record.rating:
                story.append(Paragraph(f"Overall risk rating: <b>{record.rating}</b> (score {record.score}/25).", body))
            else:
                story.append(Paragraph("Risk Assessment not yet completed.", body))

        elif key == "planning":
            record = engagement.materiality
            story.append(Paragraph(_sign_off_line(record, preparer_attr="updated_by"), status_style))
            if record and record.overall_materiality:
                story.append(Paragraph(
                    f"Overall materiality: {_fmt_num(record.overall_materiality)}. "
                    f"Performance materiality: {_fmt_num(record.performance_materiality)}. "
                    f"Clearly trivial threshold: {_fmt_num(record.trivial_threshold)}.", body,
                ))
            else:
                story.append(Paragraph("Materiality not yet calculated.", body))

        elif key == "analytical":
            record = engagement.analytical_review
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record:
                flagged = len(record.significant_lines) if record.significant_lines else 0
                story.append(Paragraph(f"{flagged} line(s) flagged as significant fluctuations.", body))
            else:
                story.append(Paragraph("Analytical Review not yet started.", body))

        elif key == "checklist":
            items = engagement.checklist_items
            story.append(Paragraph(f"Progress: {engagement.checklist_progress}% complete.", status_style))
            if items:
                story.append(_checklist_items_table(items))
            else:
                story.append(Paragraph("No checklist items added yet.", body))

        elif key == "substantive":
            areas = sorted(engagement.substantive_procedure_areas, key=lambda a: a.area) if engagement.substantive_procedure_areas else []
            if areas:
                for area in areas:
                    story.append(Paragraph(f"<b>{area.area}</b> - {_sign_off_line(area)}", status_style))
            else:
                story.append(Paragraph("No substantive procedure areas started yet.", body))

        elif key == "financials":
            record = engagement.financial_statements
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record and record.notes_to_financial_statements:
                for line in record.notes_to_financial_statements.split("\n"):
                    if line.strip():
                        story.append(Paragraph(line.strip(), body))
            else:
                story.append(Paragraph("No notes to the financial statements recorded yet.", body))

        elif key == "finalisation":
            record = engagement.finalisation_checklist
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record and record.checklist_items:
                story.append(_checklist_items_table(record.checklist_items))
            else:
                story.append(Paragraph("Finalisation checklist not yet started.", body))

        elif key in ("rep_letter", "report_to_management", "forensic_report"):
            narrative_kind = {"rep_letter": "rep_letter", "report_to_management": "report_to_management", "forensic_report": "forensic_executive_summary"}[key]
            narrative = narratives.get(narrative_kind)
            story.append(Paragraph(_sign_off_line(narrative), status_style))
            text = narrative.body if narrative and narrative.body else DEFAULT_WORKPAPER_NARRATIVE_BODIES.get(narrative_kind, "")
            for line in text.split("\n"):
                if line.strip():
                    story.append(Paragraph(f"&bull; {line.strip()}", body))

        story.append(Spacer(1, 6))

    # ------------------------------------------------------- Tickmark legend
    # Tickmarks are only used on Substantive Procedure items now - the four
    # checklist types (Acceptance, Entity, Checklist, Finalisation) don't
    # carry a tickmark field.
    used_ids = set()
    used = {}
    item_sources = []
    for area in engagement.substantive_procedure_areas:
        item_sources += list(area.items)
    for item in item_sources:
        tm = getattr(item, "tickmark", None)
        if tm and tm.id not in used_ids:
            used_ids.add(tm.id)
            used[tm.id] = tm

    story.append(PageBreak())
    story.append(Paragraph("Tickmark Legend", h1))
    if used:
        data = [["Symbol", "Meaning"]] + [[tm.symbol, tm.meaning] for tm in sorted(used.values(), key=lambda t: t.symbol)]
        legend_table = Table(data, colWidths=[80, 400])
        legend_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), gold),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(legend_table)
    else:
        story.append(Paragraph("No tickmarks were used anywhere in this engagement's file.", body))

    doc.build(story)
    buf.seek(0)
    return buf
