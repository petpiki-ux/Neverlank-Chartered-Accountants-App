"""A PDF of a credit note, in the same design as the invoice PDF (letterhead
band, details strip, 'Credit to' block, line items, totals, reason). Used for
the Download PDF button and as the attachment when a credit note is emailed."""
import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from invoice_pdf import (
    NAVY, NAVY_DARK, GOLD, LINE, TINT, LOGO, _base, _small, _right, _p, money,
)


def credit_note_filename(note):
    return f"{note.credit_note_number}.pdf"


def build_credit_note_pdf(note, firm):
    """Return the credit note as PDF bytes."""
    invoice, client, cur = note.invoice, note.invoice.client, note.currency
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=12 * mm,
                            bottomMargin=16 * mm, title=f"Credit note {note.credit_note_number}", author=firm.get("name", ""))
    width = A4[0] - 32 * mm
    story = []

    # ---- letterhead band
    contact = [firm.get("address"), "  ·  ".join(x for x in (firm.get("web"), firm.get("email"), firm.get("phone")) if x)]
    ids = "  ·  ".join(x for x in (f"VAT No: {firm['vat_number']}" if firm.get("vat_number") else "",
                                   f"TIN: {firm['tin']}" if firm.get("tin") else "") if x)
    if ids:
        contact.append(ids)
    white = ParagraphStyle("w", parent=_base, textColor=colors.white)
    firm_cell = [
        Paragraph(f"<font name='Helvetica-Bold' size='15'>{escape(firm.get('name', ''))}</font>", ParagraphStyle("fn", parent=white, leading=19)),
        Paragraph(f"<font color='#d4af6a' size='7.5'>{escape((firm.get('strapline') or firm.get('tagline') or '').upper())}</font>", ParagraphStyle("st", parent=white, leading=11)),
        Spacer(1, 3),
    ] + [Paragraph(f"<font color='#cdd8e4' size='8'>{escape(c)}</font>", white) for c in contact if c]
    r = lambda n: ParagraphStyle(n, parent=white, alignment=TA_RIGHT)
    doc_cell = [
        Paragraph("<font name='Helvetica-Bold' size='20' color='#d4af6a'>CREDIT NOTE</font>", ParagraphStyle("r", parent=white, alignment=TA_RIGHT, leading=24)),
        Paragraph(f"<font name='Helvetica-Bold' size='11'>{escape(note.credit_note_number)}</font>", r("r2")),
        Paragraph(f"<font color='#cdd8e4' size='8'>{escape(cur)}{'  ·  VOID' if note.is_void else ''}</font>", r("r3")),
    ]
    logo = Image(LOGO, 24 * mm, 24 * mm) if os.path.exists(LOGO) else ""
    band = Table([[logo, firm_cell, doc_cell]], colWidths=[30 * mm, width - 30 * mm - 58 * mm, 58 * mm])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 3, GOLD),
    ]))
    story += [band, Spacer(1, 8 * mm)]

    # ---- meta strip
    def meta(label, value):
        return [Paragraph(f"<font size='7' color='#6b7684'><b>{escape(label.upper())}</b></font>", _base),
                Paragraph(f"<font name='Helvetica-Bold' size='10.5' color='#0f2942'>{escape(value)}</font>", _base)]
    cells = [meta("Credit note no.", note.credit_note_number), meta("Date", note.issue_date.strftime("%d %b %Y")),
             meta("Against invoice", invoice.invoice_number), meta("Currency", cur)]
    meta_t = Table([cells], colWidths=[width / 4] * 4)
    meta_t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                                ("LINEAFTER", (0, 0), (-2, -1), 0.5, LINE), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 9)]))
    story += [meta_t, Spacer(1, 6 * mm)]

    # ---- credit to
    party = [Paragraph("<font size='7' color='#b8934a'><b>CREDIT TO</b></font>", _base),
             Paragraph(f"<font name='Helvetica-Bold' size='11.5' color='#0f2942'>{escape(invoice.bill_to or client.name).replace(chr(10), '<br/>')}</font>", ParagraphStyle("bt", parent=_base, leading=15))]
    id_bits = "  ·  ".join(x for x in (f"TIN: {client.tin_number}" if client.tin_number else "",
                                      f"VAT No: {client.vat_number}" if client.vat_number else "") if x)
    if id_bits:
        party.append(Paragraph(f"<font size='8.5' color='#555555'>{escape(id_bits)}</font>", _base))
    original = f"<b>Original invoice:</b> {escape(invoice.invoice_number)}"
    if invoice.issue_date:
        original += f" dated {invoice.issue_date.strftime('%d %b %Y')}"
    party.append(Paragraph(f"<font size='9'>{original}</font>", _base))
    pt = Table([[party]], colWidths=[width])
    pt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("LINEBEFORE", (0, 0), (0, -1), 3.5, GOLD),
                            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [pt, Spacer(1, 5 * mm)]

    story += [Paragraph("<font size='7.5' color='#b8934a'><b>REASON FOR CREDIT</b></font>", _base), _p(note.reason), Spacer(1, 4 * mm)]

    # ---- lines
    head = ParagraphStyle("h", parent=_base, fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)
    head_r = ParagraphStyle("hr", parent=head, alignment=TA_RIGHT)
    rows = [[Paragraph("DESCRIPTION", head), Paragraph("QTY", head_r), Paragraph("UNIT PRICE", head_r),
             Paragraph(f"AMOUNT ({escape(cur)})", head_r)]]
    for line in note.lines:
        rows.append([_p(line.description), _p(f"{line.quantity:g}", _right), _p(money(line.unit_price or 0), _right),
                     Paragraph(f"<b>{money(line.line_total)}</b>", _right)])
    items = Table(rows, colWidths=[width * 0.52, width * 0.1, width * 0.17, width * 0.21], repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfd")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [items, Spacer(1, 5 * mm)]

    # ---- totals
    total_rows = [["Subtotal", f"{cur} {money(note.subtotal)}"],
                  [f"VAT ({note.vat_pct:g}%)", f"{cur} {money(note.vat_amount)}"],
                  ["Total credited", f"{cur} {money(note.total)}"]]
    tt = Table([[_p(a), _p(b, _right)] for a, b in total_rows], colWidths=[50 * mm, 38 * mm], hAlign="RIGHT")
    last = len(total_rows) - 1
    tt.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE),
        ("BACKGROUND", (0, last), (-1, last), NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    tt._cellvalues[last] = [Paragraph(f"<font color='white'><b>{escape(total_rows[last][0])}</b></font>", _base),
                            Paragraph(f"<font color='#d4af6a'><b>{escape(total_rows[last][1])}</b></font>", _right)]
    story += [tt, Spacer(1, 4 * mm)]

    summary = [["Invoice total", f"{cur} {money(invoice.total)}"],
               ["Credited to date", f"({cur} {money(invoice.credited_total)})"],
               ["Invoice net of credits", f"{cur} {money(invoice.net_total)}"]]
    st = Table([[_p(a, _small), _p(b, ParagraphStyle("sr", parent=_small, alignment=TA_RIGHT))] for a, b in summary],
               colWidths=[50 * mm, 38 * mm], hAlign="RIGHT")
    st.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [st]

    if invoice.refund_due > 0.005 and not note.is_void:
        story += [Spacer(1, 4 * mm), Paragraph(
            f"<font size='9' color='#9a3412'><b>Refund due to you: {escape(cur)} {money(invoice.refund_due)}</b> "
            "(you have paid more than is now owing on this invoice).</font>", _base)]
    if note.notes:
        story += [Spacer(1, 5 * mm), Paragraph("<font size='7.5' color='#b8934a'><b>NOTES</b></font>", _base), _p(note.notes, _small)]
    if note.is_void:
        story += [Spacer(1, 5 * mm), Paragraph("<font color='#c0392b' size='11'><b>VOID - this credit note has been cancelled and no longer reduces the invoice.</b></font>", _base)]
    story += [Spacer(1, 8 * mm), Paragraph(
        f"<font color='#6b7684' size='8'>This credit note reduces the amount due on invoice {escape(invoice.invoice_number)}.</font>", _base)]

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(NAVY_DARK)
        canvas.rect(0, 0, A4[0], 9 * mm, stroke=0, fill=1)
        canvas.setFillColor(GOLD)
        canvas.rect(0, 9 * mm, A4[0], 0.8 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor("#cdd8e4"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(16 * mm, 3.4 * mm, firm.get("name", ""))
        canvas.drawRightString(A4[0] - 16 * mm, 3.4 * mm, firm.get("web", "") or "")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
