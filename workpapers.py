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

from models import DEFAULT_WORKPAPER_NARRATIVE_BODIES, WORKPAPER_SECTIONS, effectively_reviewed, ENTITY_UNDERSTANDING_FIELDS

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

def _new_document(title, engagement, subtitle=None):
    """A fresh Word document with the firm's letterhead (logo if available,
    firm name/address) and a title block for this working paper."""
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

def build_financial_statements_docx(engagement, statements, financial_statements):
    doc = _new_document(
        "Financial Statements",
        engagement,
        subtitle="Ref. FS-1" + (" (draft - not yet fully mapped/reviewed)" if not financial_statements or not financial_statements.is_partner_signed else ""),
    )

    if financial_statements and financial_statements.basis_of_preparation:
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

    sfp = statements["sfp"]
    _add_heading(doc, "Statement of Financial Position", level=1)
    two_col_table("Non-current assets", sfp["non_current_assets"], level=2)
    doc.add_paragraph(f"Total non-current assets: {_fmt_num(sfp['nca_total']['current'])} ({_fmt_num(sfp['nca_total']['prior'])} prior year)")
    two_col_table("Current assets", sfp["current_assets"], level=2)
    doc.add_paragraph(f"Total assets: {_fmt_num(sfp['total_assets']['current'])} ({_fmt_num(sfp['total_assets']['prior'])} prior year)")
    two_col_table("Equity", sfp["equity"], level=2)
    doc.add_paragraph(f"Total equity: {_fmt_num(sfp['total_equity']['current'])} ({_fmt_num(sfp['total_equity']['prior'])} prior year)")
    two_col_table("Non-current liabilities", sfp["non_current_liabilities"], level=2)
    two_col_table("Current liabilities", sfp["current_liabilities"], level=2)
    doc.add_paragraph(f"Total equity and liabilities: {_fmt_num(sfp['total_equity_and_liabilities']['current'])} ({_fmt_num(sfp['total_equity_and_liabilities']['prior'])} prior year)")

    two_col_table("Statement of Profit or Loss and Other Comprehensive Income", statements["pl"]["rows"])

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
    _add_heading(doc, "Statement of Cash Flows (current year, indirect method)", level=1)
    doc.add_paragraph(f"Net cash from operating activities: {_fmt_num(cf['net_operating'])}")
    doc.add_paragraph(f"Net cash used in investing activities: {_fmt_num(cf['net_investing'])}")
    doc.add_paragraph(f"Net cash from financing activities: {_fmt_num(cf['net_financing'])}")
    doc.add_paragraph(f"Net increase/(decrease) in cash: {_fmt_num(cf['net_movement'])}")
    doc.add_paragraph(f"Cash at end of year (per trial balance): {_fmt_num(cf['cash_close_actual'])}")

    if financial_statements and financial_statements.notes_to_financial_statements:
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
    wb, ws = _new_workbook_sheet("Trial Balance & Adjustments — Ref. TB-1", engagement, "Trial Balance")

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


# ================================================================= Management representation letter (Word)

def build_rep_letter_docx(engagement, statements, narrative=None):
    doc = _new_document("Management Representation Letter", engagement, subtitle="Ref. MRL-1")

    client_name = engagement.client.name if engagement.client else "[Client]"
    period_end = _fmt_date(engagement.period_end)

    doc.add_paragraph(f"To: {FIRM_NAME}")
    doc.add_paragraph(f"Date: {_fmt_date(date.today())}")
    doc.add_paragraph()
    doc.add_paragraph(
        f"This representation letter is provided in connection with your engagement in respect of the financial "
        f"statements of {client_name} for the period ended {period_end}, for the purpose of expressing an opinion "
        f"as to whether the financial statements give a true and fair view (or present fairly, in all material "
        f"respects) in accordance with the applicable financial reporting framework."
    )
    doc.add_paragraph("We confirm that, to the best of our knowledge and belief, having made such inquiries as we considered necessary for the purpose of appropriately informing ourselves:")

    # The body of representations is a persistent, editable workpaper (see
    # models.WorkpaperNarrative, kind="rep_letter") rather than fixed text -
    # fall back to the firm's default wording if nothing has been saved yet.
    body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES["rep_letter"]
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
    doc = _new_document("Report to Management", engagement, subtitle="Ref. RTM-1")

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


# ================================================================= Substantive Procedures programme (Word / Excel)

def build_substantive_procedures_docx(engagement, areas_by_name, area_order, area_refs):
    """areas_by_name: {area_name: SubstantiveProcedureArea or None}
    area_order: the ordered list of area names for this engagement type
    area_refs: {area_name: reference code string}"""
    is_forensic = engagement.type == "Investigative Engagement"
    doc = _new_document(
        "Investigative Procedures Programme" if is_forensic else "Substantive Procedures Programme",
        engagement,
        subtitle="Ref. " + ("SP" if not is_forensic else "IP") + "-1",
    )

    for area_name in area_order:
        area = areas_by_name.get(area_name)
        ref = area_refs.get(area_name, "")
        _add_heading(doc, f"{ref} — {area_name}" if ref else area_name, level=1)
        items = area.items if area else []
        if items:
            table = doc.add_table(rows=1, cols=5)
            hdr = table.rows[0].cells
            hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text, hdr[4].text = "Procedure", "Source", "Status", "Notes", "Tickmark"
            _style_table(table)
            for item in items:
                row = table.add_row().cells
                row[0].text = item.procedure_text
                row[1].text = item.source
                row[2].text = item.status
                row[3].text = item.notes or ""
                row[4].text = item.tickmark.symbol if item.tickmark else ""
        else:
            doc.add_paragraph("No procedures recorded for this area.")
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
    wb, ws = _new_workbook_sheet(
        ("Investigative Procedures Programme — Ref. IP-1" if is_forensic else "Substantive Procedures Programme — Ref. SP-1"),
        engagement, "Programme",
    )

    row = 7
    row = _header_row(ws, row, ["Ref.", "Area", "Procedure", "Source", "Status", "Notes", "Tickmark"])
    for area_name in area_order:
        area = areas_by_name.get(area_name)
        ref = area_refs.get(area_name, "")
        items = area.items if area else []
        if not items:
            ws.cell(row=row, column=1, value=ref).border = _BORDER
            ws.cell(row=row, column=2, value=area_name).border = _BORDER
            ws.cell(row=row, column=3, value="(no procedures recorded)").border = _BORDER
            for col in (4, 5, 6, 7):
                ws.cell(row=row, column=col, value="").border = _BORDER
            row += 1
            continue
        for i, item in enumerate(items):
            ws.cell(row=row, column=1, value=ref if i == 0 else "").border = _BORDER
            ws.cell(row=row, column=2, value=area_name if i == 0 else "").border = _BORDER
            ws.cell(row=row, column=3, value=item.procedure_text).border = _BORDER
            ws.cell(row=row, column=4, value=item.source).border = _BORDER
            ws.cell(row=row, column=5, value=item.status).border = _BORDER
            ws.cell(row=row, column=6, value=item.notes or "").border = _BORDER
            ws.cell(row=row, column=7, value=item.tickmark.symbol if item.tickmark else "").border = _BORDER
            row += 1

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
    doc = _new_document("Client Acceptance & Continuance - Checklist", engagement, subtitle="Ref. A-1")
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
    doc = _new_document("Understanding the Entity's Business - Checklist", engagement, subtitle="Ref. B-1")
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
    doc = _new_document("Finalisation Checklist", engagement, subtitle="Ref. I-1")
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

    for code, key, label in WORKPAPER_SECTIONS:
        if key == "forensic_report" and not is_forensic:
            continue  # the forensic report only exists on Investigative Engagements
        story.append(Paragraph(f"{code}. {label}", h1))

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
