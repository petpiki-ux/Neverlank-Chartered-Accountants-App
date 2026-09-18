"""Generates a Payslip as both a Word (.docx) and a PDF document - the firm
asked for both formats to always be available for the same payslip. Reuses
the firm's letterhead constants/helpers from workpapers.py rather than
duplicating them, and (like workpapers.py) never recalculates a figure -
every number here is the SAME figure already stored on the Payslip record
and shown on screen, so a downloaded document can never disagree with the
app.
"""
import io
import os

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

from workpapers import FIRM_NAME, FIRM_ADDRESS, _LOGO_PATH, _GOLD, _GOLD_HEX, _fmt_date, _fmt_num


def _employer_name(period):
    """Neverlank's own name for an internal payslip; the client's name for
    a client-payroll-service payslip (Neverlank is processing payroll ON
    BEHALF OF that client, so the client is the employer of record)."""
    if period.scope == "client" and period.client:
        return period.client.name
    return FIRM_NAME


def _payslip_rows(payslip):
    """The (label, amount) rows shared by both the Word and PDF layout, so
    the two formats can never show different figures for the same
    payslip."""
    earnings = [("Basic Salary", payslip.basic_salary or 0.0)]
    for item in payslip.items:
        if item.category == "Allowance":
            earnings.append((item.label, item.amount or 0.0))

    deductions = [("PAYE (Income Tax)", payslip.paye_tax or 0.0), ("AIDS Levy", payslip.aids_levy or 0.0)]
    if payslip.nssa_employee:
        deductions.append(("NSSA (Employee)", payslip.nssa_employee))
    for item in payslip.items:
        if item.category == "Deduction":
            deductions.append((item.label, item.amount or 0.0))

    return earnings, deductions


# ---------------------------------------------------------------- Word (.docx)

def generate_payslip_docx(payslip):
    employee = payslip.employee
    period = payslip.period

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
            run.add_picture(_LOGO_PATH, height=Inches(0.6))
        except Exception:
            pass

    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run(_employer_name(period))
    name_run.bold = True
    name_run.font.size = Pt(15)
    name_run.font.color.rgb = _GOLD

    sub_p = doc.add_paragraph()
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if period.scope == "client":
        sub_run = sub_p.add_run(f"Payroll processed by {FIRM_NAME}")
    else:
        sub_run = sub_p.add_run(FIRM_ADDRESS)
    sub_run.font.size = Pt(9)
    sub_run.italic = True

    doc.add_paragraph()

    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run("PAYSLIP")
    title_run.bold = True
    title_run.font.size = Pt(14)

    meta_p = doc.add_paragraph()
    meta_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_run = meta_p.add_run(f"{employee.full_name} — {period.name}")
    meta_run.font.size = Pt(11)

    doc.add_paragraph()

    info_table = doc.add_table(rows=3, cols=4)
    info_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    info_rows = [
        ("Employee No.", employee.employee_number or "—", "Pay Date", _fmt_date(period.pay_date)),
        ("Job Title", employee.job_title or "—", "Period",
         f"{_fmt_date(period.period_start)} - {_fmt_date(period.period_end)}"),
        ("NSSA No.", employee.nssa_number or "—", "National ID", employee.national_id or "—"),
    ]
    for r, (l1, v1, l2, v2) in enumerate(info_rows):
        cells = info_table.rows[r].cells
        for idx, text in enumerate((l1, v1, l2, v2)):
            p = cells[idx].paragraphs[0]
            run = p.add_run(text)
            run.font.size = Pt(9.5)
            if idx in (0, 2):
                run.bold = True

    doc.add_paragraph()

    earnings, deductions = _payslip_rows(payslip)
    max_rows = max(len(earnings), len(deductions))
    body_table = doc.add_table(rows=max_rows + 2, cols=4)
    body_table.style = "Light Grid Accent 1"

    hdr = body_table.rows[0].cells
    for idx, text in enumerate(["Earnings", "Amount", "Deductions", "Amount"]):
        p = hdr[idx].paragraphs[0]
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(10)

    for i in range(max_rows):
        row = body_table.rows[i + 1].cells
        e_label, e_amt = earnings[i] if i < len(earnings) else ("", None)
        d_label, d_amt = deductions[i] if i < len(deductions) else ("", None)
        row[0].paragraphs[0].add_run(e_label).font.size = Pt(9.5)
        row[1].paragraphs[0].add_run(_fmt_num(e_amt) if e_amt is not None else "").font.size = Pt(9.5)
        row[2].paragraphs[0].add_run(d_label).font.size = Pt(9.5)
        row[3].paragraphs[0].add_run(_fmt_num(d_amt) if d_amt is not None else "").font.size = Pt(9.5)

    total_row = body_table.rows[max_rows + 1].cells
    total_labels = ["Gross Pay", _fmt_num(payslip.gross_pay), "Total Deductions", _fmt_num(payslip.total_deductions)]
    for idx, text in enumerate(total_labels):
        run = total_row[idx].paragraphs[0].add_run(text)
        run.bold = True
        run.font.size = Pt(9.5)

    doc.add_paragraph()

    net_p = doc.add_paragraph()
    net_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    net_run = net_p.add_run(f"NET PAY: {period.currency or ''} {_fmt_num(payslip.net_pay)}")
    net_run.bold = True
    net_run.font.size = Pt(13)
    net_run.font.color.rgb = _GOLD

    doc.add_paragraph()
    caveat_p = doc.add_paragraph()
    caveat_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caveat_run = caveat_p.add_run(
        "Tax figures calculated per this firm's own editable Payroll Tax Settings. "
        "Verify current PAYE bands, AIDS levy and NSSA rates against ZIMRA/NSSA before relying on this payslip."
    )
    caveat_run.font.size = Pt(7)
    caveat_run.italic = True
    caveat_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------- PDF

def generate_payslip_pdf(payslip):
    employee = payslip.employee
    period = payslip.period

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
    )
    styles = getSampleStyleSheet()
    center = ParagraphStyle("center", parent=styles["Normal"], alignment=TA_CENTER)
    gold = colors.HexColor(f"#{_GOLD_HEX}")

    story = []
    if os.path.exists(_LOGO_PATH):
        try:
            img = Image(_LOGO_PATH, width=1.3 * inch, height=0.55 * inch)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 6))
        except Exception:
            pass

    story.append(Paragraph(f"<b><font color='#{_GOLD_HEX}' size=15>{_employer_name(period)}</font></b>", center))
    if period.scope == "client":
        story.append(Paragraph(f"<i>Payroll processed by {FIRM_NAME}</i>", center))
    else:
        story.append(Paragraph(f"<i>{FIRM_ADDRESS}</i>", center))
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>PAYSLIP</b>", ParagraphStyle("title", parent=styles["Normal"], alignment=TA_CENTER, fontSize=14)))
    story.append(Paragraph(f"{employee.full_name} - {period.name}", center))
    story.append(Spacer(1, 14))

    info_data = [
        ["Employee No.", employee.employee_number or "—", "Pay Date", _fmt_date(period.pay_date)],
        ["Job Title", employee.job_title or "—", "Period", f"{_fmt_date(period.period_start)} - {_fmt_date(period.period_end)}"],
        ["NSSA No.", employee.nssa_number or "—", "National ID", employee.national_id or "—"],
    ]
    info_table = Table(info_data, colWidths=[80, 155, 80, 130])
    info_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.grey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    earnings, deductions = _payslip_rows(payslip)
    max_rows = max(len(earnings), len(deductions))
    body = [["Earnings", "Amount", "Deductions", "Amount"]]
    for i in range(max_rows):
        e_label, e_amt = earnings[i] if i < len(earnings) else ("", None)
        d_label, d_amt = deductions[i] if i < len(deductions) else ("", None)
        body.append([
            e_label, _fmt_num(e_amt) if e_amt is not None else "",
            d_label, _fmt_num(d_amt) if d_amt is not None else "",
        ])
    body.append(["Gross Pay", _fmt_num(payslip.gross_pay), "Total Deductions", _fmt_num(payslip.total_deductions)])

    body_table = Table(body, colWidths=[120, 90, 120, 90])
    body_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), gold),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey),
        ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(body_table)
    story.append(Spacer(1, 16))

    story.append(Paragraph(
        f"<b>NET PAY: {period.currency or ''} {_fmt_num(payslip.net_pay)}</b>",
        ParagraphStyle("net", parent=styles["Normal"], alignment=TA_CENTER, fontSize=13, textColor=gold),
    ))
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "<font size=7 color='grey'>Tax figures calculated per this firm's own editable Payroll Tax Settings. "
        "Verify current PAYE bands, AIDS levy and NSSA rates against ZIMRA/NSSA before relying on this payslip.</font>",
        center,
    ))

    doc.build(story)
    buf.seek(0)
    return buf
