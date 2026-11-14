"""Valuation Model - the written valuation REPORT as a letterhead PDF and Word document (portrait A4).

Cover with the conclusion, contents, the numbered report sections written in the Report writer tab (cf_valreport.py), the Managing Partner's signature
when signed, then appendices drawn from the model: summary of values and bridge, component schedule, valuation ranges chart (PDF), going-concern
schedule, holding items, exchange rates and sources. Any [●] placeholder still in the text is highlighted so it cannot be missed before issue.
"""
import io
import re
from datetime import date
from xml.sax.saxutils import escape

import brand_docs as brand
import cf_valmodel as V
import cf_valmodel_export as X
import cf_valreport as R

_PH = re.compile(r"\[●\](?:\s*\[[^\]]*\])?")


def _sections(d):
    """[(number, title, text)] for the sections that are switched on and have text."""
    out, n = [], 0
    for sid, title, st in R.ordered_sections(d):
        if st["include"] and (st["text"] or "").strip():
            n += 1
            out.append((n, title, st["text"]))
    return out


def _appendices(F):
    """Which appendices exist: [(letter, title)]."""
    res, d = F["res"], F["d"]
    apx = [("A", "Summary of values and bridge"), ("B", "Component schedule"), ("C", "Valuation ranges")]
    if d["goingconcern"]["enabled"] and F["gc_rows"]:
        apx.append(("D", "Going-concern and development value schedule"))
    nxt = chr(ord(apx[-1][0]) + 1)
    if d["holding"]:
        apx.append((nxt, "Holding-company items"))
        nxt = chr(ord(nxt) + 1)
    apx.append((nxt, "Exchange rates and sources of information"))
    return apx


# =========================================================================== PDF

def build_report_pdf(vm, signed=False):
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.platypus.tableofcontents import TableOfContents

    F = R.facts(vm)
    d, res, cur = F["d"], F["res"], F["cur"]
    firm = brand.firm_info()
    W = A4[0] - 32 * mm
    NAVY, GOLD, INK, MUTED, TINT, LINE = (colors.HexColor("#" + x) for x in (brand.NAVY, brand.GOLD, brand.INK, brand.MUTED, brand.TINT, brand.LINE))
    base = ParagraphStyle("rb", fontName="Helvetica", fontSize=9.5, leading=14, textColor=INK, spaceAfter=6, alignment=TA_JUSTIFY)
    h1 = ParagraphStyle("rh1", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=NAVY, spaceBefore=4, spaceAfter=8, alignment=0)
    h2 = ParagraphStyle("rh2", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=NAVY, spaceBefore=8, spaceAfter=3, alignment=0)
    note = ParagraphStyle("rn", parent=base, fontSize=7.8, leading=10.5, textColor=MUTED, alignment=0)
    bullet = ParagraphStyle("rbl", parent=base, leftIndent=14, bulletIndent=3, spaceAfter=3)

    class Doc(SimpleDocTemplate):
        def afterFlowable(self, fl):
            t = getattr(fl, "_toc", None)
            if t:
                self.notify("TOCEntry", (0, t, self.page))

    def rich(text):
        s = escape(text)
        return _PH.sub(lambda m: '<font backColor="#fff0a8">' + m.group(0).replace("●", "...") + "</font>", s)

    def head(text):
        p = Paragraph(escape(text), h1)
        p._toc = text
        return p

    buf = io.BytesIO()
    doc = Doc(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=12 * mm, bottomMargin=20 * mm,
              title=f"{vm.vm_number} Valuation report", author=firm.get("name", ""))
    cover = []
    try:
        import cover_pages
        cover = cover_pages.pdf_cover("vm", vm, A4, variant="report", signed=signed)
    except Exception:
        cover = []
    story = cover + [brand.pdf_letterhead(W, "Valuation Report", number=vm.vm_number, sub=("Signed" if signed else f"{vm.status} - for review"), firm=firm), Spacer(1, 14 * mm)]
    if not cover:                         # the designed cover already carries the title
        story.append(Paragraph("STRICTLY PRIVATE AND CONFIDENTIAL", ParagraphStyle("c0", parent=note, fontName="Helvetica-Bold", textColor=GOLD, fontSize=8.5)))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(escape(vm.name), ParagraphStyle("c1", parent=h1, fontSize=24, leading=29, spaceAfter=6)))
    else:
        story.append(Paragraph("Summary of the valuation", ParagraphStyle("c1", parent=h1, fontSize=18, leading=22, spaceAfter=6)))
    story.append(Paragraph(escape(f"Valuation of {'the Group' if F['whole'] else 'a ' + R.pc(F['g']['stake_pct']) + ' shareholding'} - {F['client']}"), ParagraphStyle("c2", parent=base, fontSize=12.5, leading=17, textColor=MUTED, alignment=0)))
    story.append(Spacer(1, 8 * mm))
    story.append(brand.pdf_meta_strip(W, [("Valuation date", F["vdate_s"]), ("Basis of value", F["basis"]), ("Currency", cur), ("Reference", vm.vm_number)]))
    story.append(Spacer(1, 8 * mm))
    tgt = F["target"]
    panel = Table([[Paragraph("<font size='8' color='#d4af6a'><b>CONCLUSION OF VALUE</b></font>", ParagraphStyle("p0", parent=base, alignment=0, textColor=colors.white))],
                   [Paragraph(f"<font size='20' color='#ffffff'><b>{escape(cur)} {tgt[1]:,.0f}</b></font>", ParagraphStyle("p1", parent=base, alignment=0, leading=26))],
                   [Paragraph(f"<font size='9' color='#cdd8e4'>Range {escape(cur)} {tgt[0]:,.0f} to {escape(cur)} {tgt[2]:,.0f}</font>" + (f"<font size='9' color='#cdd8e4'> · {escape(cur)} {res['per_share'][1]:,.4f} per share</font>" if res["per_share"] else ""),
                              ParagraphStyle("p2", parent=base, alignment=0))]], colWidths=[W])
    panel.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 14), ("TOPPADDING", (0, 0), (-1, -1), 4),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (0, 0), 12), ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
                               ("LINEBELOW", (0, -1), (-1, -1), 3, GOLD)]))
    story.append(panel)
    if d["report"]["audience"].strip():
        story += [Spacer(1, 8 * mm), Paragraph("<b>Prepared for</b>", note), Paragraph(escape(d["report"]["audience"]), ParagraphStyle("c3", parent=base, alignment=0))]
    story += [Spacer(1, 4 * mm), Paragraph(f"Prepared by {escape(firm.get('name', ''))} · {date.today().strftime('%d %B %Y')}", note), PageBreak()]

    # contents
    story.append(Paragraph("Contents", h1))
    toc = TableOfContents()
    toc.dotsMinLevel = 0
    toc.levelStyles = [ParagraphStyle("toc0", parent=base, fontSize=10, leading=17, alignment=0, leftIndent=0, spaceAfter=0)]
    story += [toc, PageBreak()]

    secs = _sections(d)
    for i, (n, title, text) in enumerate(secs):
        if i:
            story.append(Spacer(1, 4 * mm))
        story.append(head(f"{n}. {title}"))
        for kind, t in R.parse_blocks(text):
            if kind == "h":
                story.append(Paragraph(escape(t), h2))
            elif kind == "b":
                story.append(Paragraph(rich(t), bullet, bulletText="•"))
            else:
                story.append(Paragraph(rich(t), base))
    if not secs:
        story.append(Paragraph("The report has not been written yet. Use the Report writer tab to draft its sections.", base))
    if signed:
        story.append(KeepTogether([Spacer(1, 6 * mm)] + brand.pdf_signature_flowables(firm_name=firm.get("name"), signed_on=vm.mp_signed_at)))
    elif secs:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("This report has not yet been signed by the Managing Partner.", note))

    # appendices
    apx = _appendices(F)
    key = {t: l for l, t in apx}
    tbl = lambda data, widths, size=8, num_from=1, bold_rows=(): _tbl(data, widths, size, num_from, bold_rows)

    def _tbl(data, widths, size, num_from, bold_rows):
        t = Table(data, colWidths=widths, repeatRows=1)
        st = brand.pdf_table_style(1)
        t.setStyle(st)
        cmds = [("FONTSIZE", (0, 0), (-1, -1), size)]
        if num_from is not None:
            cmds.append(("ALIGN", (num_from, 0), (-1, -1), "RIGHT"))
        for r in bold_rows:
            cmds += [("FONT", (0, r), (-1, r), "Helvetica-Bold", size), ("LINEABOVE", (0, r), (-1, r), 0.6, NAVY)]
        t.setStyle(TableStyle(cmds))
        return t

    def cellp(text, bold=False, size=8):
        return Paragraph(escape(str(text)), ParagraphStyle("cp", parent=base, fontSize=size, leading=size + 2, alignment=0, spaceAfter=0, fontName="Helvetica-Bold" if bold else "Helvetica"))

    def app_head(letter, title):
        story.append(PageBreak())
        story.append(head(f"Appendix {letter} - {title}"))

    m = X._m
    app_head("A", "Summary of values and bridge")
    rows = [["", "Low", "Adopted", "High"], ["Group equity value", m(res["group_equity"][0]), m(res["group_equity"][1]), m(res["group_equity"][2])]]
    if not F["whole"]:
        rows.append([f"Value of a {F['g']['stake_pct']:g}% stake", m(res["stake"][0]), m(res["stake"][1]), m(res["stake"][2])])
    if res["per_share"]:
        rows.append([f"Per share ({F['g']['shares']:,.0f} shares)", *[f"{x:,.4f}" for x in res["per_share"]]])
    story.append(tbl(rows, [W - 3 * 30 * mm, 30 * mm, 30 * mm, 30 * mm], size=9))
    story.append(Paragraph("From the components to the Group value", h2))
    bl = X._bridge_lines(res)
    out = bl
    data = [["", "Low", "Adopted", "High"]] + [[cellp(l, bold=b, size=8.5), m(v[0]), m(v[1]), m(v[2])] for l, v, b in out]
    story.append(tbl(data, [W - 3 * 30 * mm, 30 * mm, 30 * mm, 30 * mm], size=8.5, bold_rows=[i for i, (_, _, b) in enumerate(out, start=1) if b]))

    app_head("B", "Component schedule")
    story.append(Paragraph(f"Values converted to {cur}. Equity value is after deducting net debt where a component was valued on an enterprise basis. Adjustments apply in the order control premium, minority discount, marketability discount (DLOM).", note))
    head_row = ["Component", "Method", "Cur", "Rate", "Equity 100%", "Own %", "Adjust.", "Low", "Adopted", "High"]
    data = [head_row]
    for r in res["rows"]:
        c = r["c"]
        if r["adjusted"] is None:
            data.append([cellp(c["name"], size=7), cellp(c["method"], size=7), c["currency"], "", "", "", "not in total", "", "", ""])
        else:
            data.append([cellp(c["name"], size=7), cellp(c["method"], size=7), c["currency"], f"{r['rate']:.4f}", m(r["equity"][1]), f"{c['ownership_pct']:g}",
                         cellp(X._adj_text(c), size=6.5), m(r["adjusted"][0]), m(r["adjusted"][1]), m(r["adjusted"][2])])
    data.append(["Sum of the components", "", "", "", "", "", "", m(res["parts"][0]), m(res["parts"][1]), m(res["parts"][2])])
    ws = [36, 25, 9, 13, 21, 10, 22, 19, 19, 19]
    sc = W / (sum(ws) * mm)
    story.append(tbl(data, [x * mm * sc for x in ws], size=7, num_from=3, bold_rows=(len(data) - 1,)))

    app_head("C", "Valuation ranges")
    bars = V.football(res)
    if bars:
        lo = min([b["low"] for b in bars] + [0.0])
        hi = max([b["high"] for b in bars] + [1.0])
        span = (hi - lo) or 1.0
        label_w, plot_w, row_h = 52 * mm, W - 52 * mm - 4 * mm, 17
        dr = Drawing(W, row_h * len(bars) + 22)
        for k, b in enumerate(bars):
            y = row_h * (len(bars) - 1 - k) + 14
            dr.add(String(0, y + 4, b["label"][:34], fontName="Helvetica-Bold" if b["kind"] != "component" else "Helvetica", fontSize=7.5, fillColor=INK))
            x0 = label_w + (b["low"] - lo) / span * plot_w
            w = max(1.5, (b["high"] - b["low"]) / span * plot_w)
            dr.add(Rect(x0, y, w, 10, fillColor=GOLD if b["kind"] != "component" else NAVY, strokeColor=None))
            xm = label_w + (b["mid"] - lo) / span * plot_w
            dr.add(Line(xm, y - 1, xm, y + 11, strokeColor=colors.white, strokeWidth=1.5))
        dr.add(String(label_w, 2, "0" if lo == 0 else m(lo), fontName="Helvetica", fontSize=6.5, fillColor=MUTED))
        dr.add(String(label_w + plot_w, 2, m(hi), fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor="end"))
        story.append(dr)
        story.append(Paragraph("Each bar runs from the low to the high value; the white mark is the adopted value. Component bars are after ownership and adjustments.", note))
        data = [["", "Low", "Adopted", "High"]] + [[cellp(b["label"], bold=b["kind"] != "component", size=8), m(b["low"]), m(b["mid"]), m(b["high"])] for b in bars]
        story += [Spacer(1, 3 * mm), tbl(data, [W - 3 * 30 * mm, 30 * mm, 30 * mm, 30 * mm], size=8)]

    if d["goingconcern"]["enabled"] and F["gc_rows"]:
        app_head("D", "Going-concern and development value schedule")
        story.append(Paragraph(f"Stage: {escape(F['gc']['stage'] or 'not stated')}. Amounts are present values at the Valuation Date, in {cur}. Each item is weighted by its probability; deductions are shown as negatives. 'Unrisked' is the adopted value before weighting; Low / Adopted / High are after weighting.", note))
        data = [["Item", "Treatment", "Prob.", "Unrisked", "Low", "Adopted", "High"]]
        for x in F["gc_rows"]:
            if not x["counted"]:
                data.append([cellp(x["label"], size=7.5), cellp(R.GC_SHORT[x["kind"]], size=7.5), "", "", "", "not in total", ""])
            else:
                data.append([cellp(x["label"], size=7.5), cellp(R.GC_SHORT[x["kind"]], size=7.5), f"{x['probability']:g}%", m(x["unrisked"][1]), m(x["effect"][0]), m(x["effect"][1]), m(x["effect"][2])])
        data.append(["Net going-concern and development value", "", "", m(res["gc_unrisked"][1]), m(res["gc"][0]), m(res["gc"][1]), m(res["gc"][2])])
        ws = [48, 42, 12, 28, 24, 24, 24]
        sc = W / (sum(ws) * mm)
        story.append(tbl(data, [x * mm * sc for x in ws], size=7.5, num_from=2, bold_rows=(len(data) - 1,)))
        dcfs = [x for x in F["gc_rows"] if x["kind"] == "dcf" and x["counted"]]
        for x in dcfs:
            story.append(Paragraph(escape(f"{x['label']}: {x['years']} yearly cash flows, discount rate {x['rate_pct']:g}% (range {x['rate_pct'] - x['rate_range']:g}% to {x['rate_pct'] + x['rate_range']:g}%), "
                                          + (f"terminal growth {x['growth_pct']:g}%" if x["growth_pct"] is not None else "no terminal value") + (", mid-year discounting." if x["mid_year"] else ", year-end discounting.")), note))

    if d["holding"]:
        app_head(key["Holding-company items"], "Holding-company items")
        names = {"add": "Added", "deduct": "Deducted", "eliminate": "Eliminated", "capcost": "Capitalised cost (deducted)"}
        data = [["Item", "Treatment", "Cur", "Low", "Adopted", "High"]]
        for hr in res["holding_rows"]:
            h, e = hr["h"], hr["effect"] or (None, None, None)
            data.append([cellp(h["label"], size=8), names[h["kind"]], h["currency"] or cur, m(e[0]), m(e[1]), m(e[2])])
        story.append(tbl(data, [W - 40 * mm - 12 * mm - 3 * 24 * mm, 40 * mm, 12 * mm, 24 * mm, 24 * mm, 24 * mm], size=8, num_from=3))

    app_head(apx[-1][0], "Exchange rates and sources of information")
    fxs = V.currencies_needed(d, cur)
    if fxs:
        story.append(tbl([["Currency", f"Rate to {cur}"]] + [[c, f"{d['fx'].get(c):.4f}" if d["fx"].get(c) else "not set"] for c in fxs], [40 * mm, 40 * mm], size=8))
        story.append(Spacer(1, 4 * mm))
    data = [["Component", "Valuation date", "Source"]] + [[cellp(c["name"], size=8), c["date"] or "-", cellp(c["source"]["label"] or "typed in", size=8)] for c in d["components"]]
    story.append(tbl(data, [60 * mm, 28 * mm, W - 88 * mm], size=8, num_from=None))

    foot = brand.pdf_footer(firm)
    doc.multiBuild(story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()


# =========================================================================== Word

def build_report_docx(vm, signed=False):
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt, RGBColor

    F = R.facts(vm)
    d, res, cur = F["d"], F["res"], F["cur"]
    firm = brand.firm_info()
    doc = Document()
    brand.setup_document(doc, margins_in=0.9, base_size=10.5)
    brand.branded_footer(doc, firm, center=vm.vm_number)
    has_cover = False
    try:
        import cover_pages
        has_cover = cover_pages.word_cover(doc, "vm", vm, variant="report", signed=signed, alt=f"Cover page: {vm.name} - valuation report")
    except Exception:
        pass
    brand.letterhead_band(doc, "Valuation Report", sub=("Signed" if signed else f"{vm.status} - for review"), number=vm.vm_number, firm=firm)
    width = brand.usable_width_in(doc)

    def para(text, size=10.5, bold=False, after=6, colour=brand.INK, italic=False, align=None):
        p = doc.add_paragraph()
        brand._tight(p, before=0, after=after, line=1.15)
        _runs(p, text, size, bold, colour, italic)
        if align:
            p.alignment = align
        return p

    def _runs(p, text, size, bold=False, colour=brand.INK, italic=False):
        pos = 0
        for m_ in _PH.finditer(text):
            if m_.start() > pos:
                brand._run(p, text[pos:m_.start()], size=size, bold=bold, italic=italic, colour=colour)
            r = brand._run(p, m_.group(0), size=size, bold=True, colour=brand.INK)
            try:
                from docx.enum.text import WD_COLOR_INDEX
                (r or p.runs[-1]).font.highlight_color = WD_COLOR_INDEX.YELLOW
            except Exception:
                pass
            pos = m_.end()
        if pos < len(text):
            brand._run(p, text[pos:], size=size, bold=bold, italic=italic, colour=colour)

    if not has_cover:                     # the designed cover already carries the title
        para("STRICTLY PRIVATE AND CONFIDENTIAL", size=8.5, bold=True, colour=brand.GOLD, after=14)
        para(vm.name, size=24, bold=True, colour=brand.NAVY, after=4)
    else:
        para("Summary of the valuation", size=15, bold=True, colour=brand.NAVY, after=4)
    para(f"Valuation of {'the Group' if F['whole'] else 'a ' + R.pc(F['g']['stake_pct']) + ' shareholding'} - {F['client']}", size=13, colour=brand.MUTED, after=14)
    brand.meta_strip(doc, [("Valuation date", F["vdate_s"]), ("Basis of value", F["basis"]), ("Currency", cur), ("Reference", vm.vm_number)])
    tgt = F["target"]
    brand.total_panel(doc, "Conclusion of value (adopted)", f"{cur} {tgt[1]:,.0f}", align_right=False, width_in=min(width, 4.6))
    para(f"Range {cur} {tgt[0]:,.0f} to {cur} {tgt[2]:,.0f}" + (f" · {cur} {res['per_share'][1]:,.4f} per share" if res["per_share"] else ""), size=10, colour=brand.MUTED, after=10)
    if d["report"]["audience"].strip():
        para("Prepared for", size=8.5, bold=True, colour=brand.MUTED, after=0)
        para(d["report"]["audience"], after=8)
    para(f"Prepared by {firm.get('name', '')} · {date.today().strftime('%d %B %Y')}", size=8.5, colour=brand.MUTED)

    secs = _sections(d)
    apx = _appendices(F)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    brand.heading(doc, "Contents", 1)
    for n, title, _ in secs:
        para(f"{n}.  {title}", after=2)
    for letter, title in apx:
        para(f"Appendix {letter}  -  {title}", after=2)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    for i, (n, title, text) in enumerate(secs):
        brand.heading(doc, f"{n}. {title}", 1)
        for kind, t in R.parse_blocks(text):
            if kind == "h":
                brand.heading(doc, t, 3)
            elif kind == "b":
                p = doc.add_paragraph(style="List Bullet")
                brand._tight(p, before=0, after=3, line=1.15)
                _runs(p, t, 10.5)
            else:
                para(t, align=3)
    if not secs:
        para("The report has not been written yet. Use the Report writer tab to draft its sections.")
    if signed:
        brand.add_signature_block(doc, firm_name=firm.get("name"), signed_on=vm.mp_signed_at)
    elif secs:
        para("This report has not yet been signed by the Managing Partner.", size=8.5, colour=brand.MUTED, italic=True)

    def table(head, rows, widths_in, num_from=1, size=8.5, bold_rows=()):
        t = doc.add_table(rows=1 + len(rows), cols=len(head))
        brand._fixed_widths(t, widths_in)
        for j, h in enumerate(head):
            t.rows[0].cells[j].text = h
        for i, row in enumerate(rows, start=1):
            for j, v in enumerate(row):
                t.rows[i].cells[j].text = str(v)
        brand.style_table(t, header_rows=1, numeric_from=num_from)
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(size)
        for i in bold_rows:
            for cell in t.rows[i].cells:
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.bold = True
        doc.add_paragraph()
        return t

    def app(letter, title):
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        brand.heading(doc, f"Appendix {letter} - {title}", 1)

    m = X._m
    key = {t: l for l, t in apx}
    app("A", "Summary of values and bridge")
    rows = [["Group equity value", m(res["group_equity"][0]), m(res["group_equity"][1]), m(res["group_equity"][2])]]
    if not F["whole"]:
        rows.append([f"Value of a {F['g']['stake_pct']:g}% stake", m(res["stake"][0]), m(res["stake"][1]), m(res["stake"][2])])
    if res["per_share"]:
        rows.append([f"Per share ({F['g']['shares']:,.0f} shares)", *[f"{x:,.4f}" for x in res["per_share"]]])
    table(["", "Low", "Adopted", "High"], rows, [width - 3 * 1.25, 1.25, 1.25, 1.25], size=9.5)
    brand.heading(doc, "From the components to the Group value", 2)
    bl = X._bridge_lines(res)
    out = bl
    table(["", "Low", "Adopted", "High"], [[l, m(v[0]), m(v[1]), m(v[2])] for l, v, _ in out], [width - 3 * 1.25, 1.25, 1.25, 1.25], size=9,
          bold_rows=[i for i, (_, _, b) in enumerate(out, start=1) if b])

    app("B", "Component schedule")
    para(f"Values converted to {cur}. Equity value is after deducting net debt where a component was valued on an enterprise basis. Adjustments apply in the order control premium, minority discount, marketability discount (DLOM).", size=8.5, colour=brand.MUTED)
    rows = []
    for r in res["rows"]:
        c = r["c"]
        if r["adjusted"] is None:
            rows.append([c["name"], c["method"], c["currency"], "", "", "", "not in total", "", "", ""])
        else:
            rows.append([c["name"], c["method"], c["currency"], f"{r['rate']:.4f}", m(r["equity"][1]), f"{c['ownership_pct']:g}", X._adj_text(c), m(r["adjusted"][0]), m(r["adjusted"][1]), m(r["adjusted"][2])])
    rows.append(["Sum of the components", "", "", "", "", "", "", m(res["parts"][0]), m(res["parts"][1]), m(res["parts"][2])])
    ws = [1.0, 0.8, 0.3, 0.45, 0.7, 0.35, 0.7, 0.6, 0.6, 0.6]
    sc = width / sum(ws)
    table(["Component", "Method", "Cur", "Rate", "Equity 100%", "Own %", "Adjust.", "Low", "Adopted", "High"], rows, [x * sc for x in ws], num_from=3, size=7, bold_rows=(len(rows),))

    app("C", "Valuation ranges")
    bars = V.football(res)
    table(["", "Low", "Adopted", "High"], [[b["label"], m(b["low"]), m(b["mid"]), m(b["high"])] for b in bars], [width - 3 * 1.25, 1.25, 1.25, 1.25], size=9,
          bold_rows=[i for i, b in enumerate(bars, start=1) if b["kind"] != "component"])

    if d["goingconcern"]["enabled"] and F["gc_rows"]:
        app("D", "Going-concern and development value schedule")
        para(f"Stage: {F['gc']['stage'] or 'not stated'}. Amounts are present values at the Valuation Date, in {cur}. Each item is weighted by its probability; deductions are shown as negatives. 'Unrisked' is the adopted value before weighting; Low / Adopted / High are after weighting.", size=8.5, colour=brand.MUTED)
        rows = []
        for x in F["gc_rows"]:
            if not x["counted"]:
                rows.append([x["label"], R.GC_SHORT[x["kind"]], "", "", "", "not in total", ""])
            else:
                rows.append([x["label"], R.GC_SHORT[x["kind"]], f"{x['probability']:g}%", m(x["unrisked"][1]), m(x["effect"][0]), m(x["effect"][1]), m(x["effect"][2])])
        rows.append(["Net going-concern and development value", "", "", m(res["gc_unrisked"][1]), m(res["gc"][0]), m(res["gc"][1]), m(res["gc"][2])])
        ws = [1.5, 1.3, 0.45, 0.9, 0.8, 0.8, 0.8]
        sc = width / sum(ws)
        table(["Item", "Treatment", "Prob.", "Unrisked", "Low", "Adopted", "High"], rows, [x * sc for x in ws], num_from=2, size=8, bold_rows=(len(rows),))
        for x in [x for x in F["gc_rows"] if x["kind"] == "dcf" and x["counted"]]:
            para(f"{x['label']}: {x['years']} yearly cash flows, discount rate {x['rate_pct']:g}% (range {x['rate_pct'] - x['rate_range']:g}% to {x['rate_pct'] + x['rate_range']:g}%), "
                 + (f"terminal growth {x['growth_pct']:g}%" if x["growth_pct"] is not None else "no terminal value") + (", mid-year discounting." if x["mid_year"] else ", year-end discounting."), size=8.5, colour=brand.MUTED)

    if d["holding"]:
        app(key["Holding-company items"], "Holding-company items")
        names = {"add": "Added", "deduct": "Deducted", "eliminate": "Eliminated", "capcost": "Capitalised cost (deducted)"}
        rows = []
        for hr in res["holding_rows"]:
            h, e = hr["h"], hr["effect"] or (None, None, None)
            rows.append([h["label"], names[h["kind"]], h["currency"] or cur, m(e[0]), m(e[1]), m(e[2])])
        table(["Item", "Treatment", "Cur", "Low", "Adopted", "High"], rows, [width - 1.9 - 0.5 - 3 * 1.0, 1.9, 0.5, 1.0, 1.0, 1.0], num_from=3, size=8.5)

    app(apx[-1][0], "Exchange rates and sources of information")
    fxs = V.currencies_needed(d, cur)
    if fxs:
        table(["Currency", f"Rate to {cur}"], [[c, f"{d['fx'].get(c):.4f}" if d["fx"].get(c) else "not set"] for c in fxs], [1.4, 1.6], size=9)
    table(["Component", "Valuation date", "Source"], [[c["name"], c["date"] or "-", c["source"]["label"] or "typed in"] for c in d["components"]], [2.4, 1.2, width - 3.6], num_from=None, size=8.5)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
