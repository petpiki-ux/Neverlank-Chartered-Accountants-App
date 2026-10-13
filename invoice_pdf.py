"""A PDF of an invoice, built on the server so it can be attached to an email
or opened from a WhatsApp link without anyone printing it from the browser first.
Mirrors the printed invoice: letterhead band, details, line items, totals,
payments received, balance due and the firm's payment details."""
import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0f2942")
NAVY_DARK = colors.HexColor("#0a1c2e")
GOLD = colors.HexColor("#b8934a")
GOLD_LIGHT = colors.HexColor("#d4af6a")
INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7684")
LINE = colors.HexColor("#e3e8ee")
TINT = colors.HexColor("#f6f8fb")
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "neverlank-logo.png")

_base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
_small = ParagraphStyle("small", parent=_base, fontSize=8, leading=11, textColor=MUTED)
_right = ParagraphStyle("right", parent=_base, alignment=TA_RIGHT)
_bold = ParagraphStyle("bold", parent=_base, fontName="Helvetica-Bold")


def money(amount):
    return f"{amount:,.2f}"


def _p(text, style=_base):
    return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)


def invoice_filename(invoice):
    return f"{invoice.invoice_number}.pdf"


def build_invoice_pdf(invoice, firm):
    """Return the invoice as PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=12 * mm,
                            bottomMargin=16 * mm, title=f"Invoice {invoice.invoice_number}", author=firm.get("name", ""))
    width = A4[0] - 32 * mm
    story = []

    # ---- letterhead band
    contact = [firm.get("address")]
    contact.append("  ·  ".join(x for x in (firm.get("web"), firm.get("email"), firm.get("phone")) if x))
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
    doc_cell = [
        Paragraph("<font name='Helvetica-Bold' size='24' color='#d4af6a'>INVOICE</font>", ParagraphStyle("r", parent=white, alignment=TA_RIGHT, leading=28)),
        Paragraph(f"<font name='Helvetica-Bold' size='11'>{escape(invoice.invoice_number)}</font>", ParagraphStyle("r2", parent=white, alignment=TA_RIGHT)),
        Paragraph(f"<font color='#cdd8e4' size='8'>{escape(invoice.currency)}</font>", ParagraphStyle("r3", parent=white, alignment=TA_RIGHT)),
    ]
    logo = Image(LOGO, 24 * mm, 24 * mm) if os.path.exists(LOGO) else ""
    band = Table([[logo, firm_cell, doc_cell]], colWidths=[30 * mm, width - 30 * mm - 55 * mm, 55 * mm])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 3, GOLD),
    ]))
    story += [band, Spacer(1, 8 * mm)]

    # ---- meta
    def meta(label, value):
        return [Paragraph(f"<font size='7' color='#6b7684'><b>{escape(label.upper())}</b></font>", _base),
                Paragraph(f"<font name='Helvetica-Bold' size='10.5' color='#0f2942'>{escape(value)}</font>", _base)]
    issue = invoice.issue_date.strftime("%d %b %Y") if invoice.issue_date else "—"
    due = invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "On receipt"
    cells = [meta("Invoice no.", invoice.invoice_number), meta("Issue date", issue), meta("Due date", due),
             meta("Currency", invoice.currency)]
    meta_t = Table([cells], colWidths=[width / 4] * 4)
    meta_t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                                ("LINEAFTER", (0, 0), (-2, -1), 0.5, LINE), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 9)]))
    story += [meta_t, Spacer(1, 6 * mm)]

    # ---- bill to
    client = invoice.client
    bill = [Paragraph("<font size='7' color='#b8934a'><b>BILL TO</b></font>", _base),
            Paragraph(f"<font name='Helvetica-Bold' size='11.5' color='#0f2942'>{escape(invoice.bill_to or client.name).replace(chr(10), '<br/>')}</font>", ParagraphStyle("bt", parent=_base, leading=15))]
    id_bits = "  ·  ".join(x for x in (f"TIN: {client.tin_number}" if client.tin_number else "",
                                      f"VAT No: {client.vat_number}" if client.vat_number else "") if x)
    if id_bits:
        bill.append(Paragraph(f"<font size='8.5' color='#555555'>{escape(id_bits)}</font>", _base))
    if invoice.engagement:
        bill.append(Paragraph(f"<font size='9'><b>Re:</b> {escape(invoice.engagement.title)}</font>", _base))
    bt = Table([[bill]], colWidths=[width])
    bt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("LINEBEFORE", (0, 0), (0, -1), 3.5, GOLD),
                            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [bt, Spacer(1, 6 * mm)]

    # ---- line items
    head = ParagraphStyle("h", parent=_base, fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)
    head_r = ParagraphStyle("hr", parent=head, alignment=TA_RIGHT)
    rows = [[Paragraph("DESCRIPTION", head), Paragraph("QTY", head_r), Paragraph("UNIT PRICE", head_r),
             Paragraph(f"AMOUNT ({escape(invoice.currency)})", head_r)]]
    for line in invoice.lines:
        rows.append([_p(line.description), _p(f"{line.quantity:g}", _right), _p(money(line.unit_price or 0), _right),
                     Paragraph(f"<b>{money(line.line_total)}</b>", _right)])
    if len(rows) == 1:
        rows.append([_p("No line items"), "", "", ""])
    items = Table(rows, colWidths=[width * 0.52, width * 0.1, width * 0.17, width * 0.21], repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfd")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [items, Spacer(1, 5 * mm)]

    # ---- totals
    cur = invoice.currency
    total_rows = [
        ["Subtotal", f"{cur} {money(invoice.subtotal)}"],
        [f"VAT ({invoice.vat_pct:g}%)", f"{cur} {money(invoice.vat_amount)}"],
        ["Total", f"{cur} {money(invoice.total)}"],
    ]
    paid = invoice.total if invoice.legacy_paid else invoice.amount_paid
    if paid > 0.005:
        total_rows.append(["Received", f"({cur} {money(paid)})"])
    total_rows.append(["Balance due", f"{cur} {money(invoice.balance)}"])
    tt = Table([[_p(a), _p(b, _right)] for a, b in total_rows], colWidths=[38 * mm, 42 * mm], hAlign="RIGHT")
    last = len(total_rows) - 1
    tt.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE),
        ("BACKGROUND", (0, last), (-1, last), NAVY), ("TEXTCOLOR", (0, last), (-1, last), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    # white bold text on the navy row
    tt._cellvalues[last] = [Paragraph(f"<font color='white'><b>{escape(total_rows[last][0])}</b></font>", _base),
                            Paragraph(f"<font color='#d4af6a'><b>{escape(total_rows[last][1])}</b></font>", _right)]
    story += [tt]

    if invoice.status == "Paid" and invoice.balance <= 0.005:
        story += [Spacer(1, 3 * mm), Paragraph("<font color='#1e8a4c' size='13'><b>PAID IN FULL - thank you</b></font>", _right)]

    if invoice.notes:
        story += [Spacer(1, 6 * mm), Paragraph("<font size='7.5' color='#b8934a'><b>NOTES</b></font>", _base), _p(invoice.notes, _small)]
    if firm.get("bank_details"):
        story += [Spacer(1, 5 * mm), Paragraph("<font size='7.5' color='#b8934a'><b>PAYMENT DETAILS</b></font>", _base),
                  _p(firm["bank_details"], _small)]
    story += [Spacer(1, 8 * mm), Paragraph(f"<font color='#6b7684' size='8'>Thank you for choosing {escape(firm.get('name', ''))}. "
              f"Please quote {escape(invoice.invoice_number)} with your payment.</font>", _base)]

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
