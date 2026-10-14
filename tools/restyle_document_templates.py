"""Restyle the Word templates in document_templates/ into the firm's invoice-style
design (navy letterhead band with the document title, gold-ruled section headings,
tidy field and data tables, navy footer carrying the template reference) WITHOUT
changing any wording, fields, tables or numbering - only the look.

Run from the project root:   python tools/restyle_document_templates.py
It is safe to run twice: files already in the new design are skipped.
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

import brand_docs as brand

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "document_templates")
MARKER = "NeverlankBrandV2"  # stored in the document's 'keywords' so a restyled file is recognised


def _text(el):
    return "".join(t.text or "" for t in el.iter(qn("w:t")))


def _ensure_base_styles(doc):
    """These templates were generated without a Normal style or default fonts; add them."""
    styles = doc.styles.element
    has_normal = any(s.get(qn("w:styleId")) == "Normal" for s in styles.findall(qn("w:style")))
    if not has_normal:
        normal = OxmlElement("w:style")
        normal.set(qn("w:type"), "paragraph")
        normal.set(qn("w:default"), "1")
        normal.set(qn("w:styleId"), "Normal")
        name = OxmlElement("w:name")
        name.set(qn("w:val"), "Normal")
        normal.append(name)
        normal.append(OxmlElement("w:qFormat"))
        styles.insert(1 if styles.find(qn("w:docDefaults")) is not None else 0, normal)
    # Fonts and sizes
    normal_style = doc.styles["Normal"]
    normal_style.font.name = brand.BODY_FONT
    normal_style.font.size = Pt(10.5)
    normal_style.font.color.rgb = brand.rgb(brand.INK)
    normal_style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), brand.BODY_FONT)
    normal_style.paragraph_format.space_after = Pt(6)
    normal_style.paragraph_format.line_spacing = 1.12
    for style_name, size in (("Heading 2", 12.5), ("Heading 1", 15), ("Heading 3", 11)):
        try:
            st = doc.styles[style_name]
        except KeyError:
            continue
        st.font.name = brand.HEAD_FONT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = brand.rgb(brand.NAVY)
        rfonts = st.element.get_or_add_rPr().get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            rfonts.set(qn(attr), brand.HEAD_FONT)
        st.paragraph_format.space_before = Pt(14)
        st.paragraph_format.space_after = Pt(5)
        st.paragraph_format.keep_with_next = True


def _find_title_index(body):
    """Index of the document-title paragraph: the first bold centred navy paragraph (size 13)."""
    for i, el in enumerate(body):
        if el.tag != qn("w:p"):
            continue
        rpr = next(el.iter(qn("w:rPr")), None)
        if rpr is None:
            continue
        color = rpr.find(qn("w:color"))
        sz = rpr.find(qn("w:sz"))
        if color is not None and color.get(qn("w:val")) == "0F2942" and sz is not None and int(sz.get(qn("w:val"))) >= 24:
            return i
    return None


def _template_ref(doc, filename):
    for section in doc.sections:
        for p in section.footer.paragraphs:
            m = re.search(r"Template Ref:\s*([A-Za-z0-9\-]+)", p.text)
            if m:
                return m.group(1)
    m = re.match(r"([A-Z]+-\d+)", os.path.basename(filename))
    return m.group(1) if m else None


def _restyle_tables(doc):
    for tbl in doc.element.body.findall(qn("w:tbl")):
        rows = tbl.findall(qn("w:tr"))
        cols = len(rows[0].findall(qn("w:tc")))
        fills = {s.get(qn("w:fill")) for s in rows[0].iter(qn("w:shd"))}
        table = next(t for t in doc.tables if t._tbl is tbl)
        if len(rows) == 1 and cols == 2:
            # a "Label: ______" field row
            label, value = table.rows[0].cells
            brand.shade(label, brand.TINT)
            brand.cell_margins(label, top=70, bottom=70, left=140, right=100)
            brand.cell_borders(label, left=("single", 24, brand.GOLD))
            for p in label.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9.5)
                    r.font.color.rgb = brand.rgb(brand.NAVY)
                    r.bold = True
            brand.cell_margins(value, top=70, bottom=70, left=140, right=60)
            for cell in (label, value):
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.space_before = Pt(0)
            nxt = tbl.getnext()
            if nxt is not None and nxt.tag == qn("w:p") and not _text(nxt).strip():
                spacing = nxt.find(qn("w:pPr") + "/" + qn("w:spacing"))
                if spacing is not None:
                    spacing.set(qn("w:after"), "50")
            for p in value.paragraphs:
                p_pr = p._p.get_or_add_pPr()
                old = p_pr.find(qn("w:pBdr"))
                if old is not None:
                    p_pr.remove(old)
                brand.para_border(p, "bottom", brand.LINE, size=8, space=1)
            continue
        header_navy = "0F2942" in fills
        brand.table_borders(table, colour=brand.LINE, size=4, inside=True)
        brand.table_cell_margins(table, top=60, bottom=60, left=100, right=100)
        for ri, row in enumerate(table.rows):
            for cell in row.cells:
                if ri == 0:
                    if header_navy:
                        brand.shade(cell, brand.NAVY)
                    else:  # the Role / Name | Signature | Date block
                        brand.shade(cell, brand.TINT)
                        brand.cell_borders(cell, bottom=("single", 12, brand.GOLD))
                    for p in cell.paragraphs:
                        for r in p.runs:
                            r.bold = True
                            r.font.size = Pt(9)
                            r.font.color.rgb = brand.rgb("FFFFFF" if header_navy else brand.NAVY)
                else:
                    for p in cell.paragraphs:
                        p.paragraph_format.space_after = Pt(0)
                        for r in p.runs:
                            r.font.size = Pt(9.5)
                    if header_navy and ri % 2 == 0:
                        brand.shade(cell, "FAFBFD")


def _restyle_headings(doc):
    for p in doc.paragraphs:
        if p.style is not None and p.style.name == "Heading 2":
            brand.para_border(p, "bottom", brand.GOLD_LIGHT, size=6, space=2)


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _find_loose_title(body, title):
    """For a template that was edited in Word and no longer has the original title paragraph: look in the
    first few elements for a paragraph that carries the template's name. Returns (index, text) or (None, None)."""
    want = _norm(title)
    if not want:
        return None, None
    for i, el in enumerate(list(body)[:15]):
        if el.tag != qn("w:p"):
            continue
        got = _norm(_text(el))
        if got and (got == want or (len(got) >= 8 and (got in want or want in got)) ):
            return i, _text(el).strip()
    return None, None


def restyle(path, title=None, ref=None, flexible=False):
    """Restyle one Word template in place. Returns True if changed, False if it was already in the design.
    flexible=True is for copies that were edited or re-uploaded by the firm: if the original title paragraph
    is gone, the template's own title/ref (passed in) are used and the old letterhead is only removed when it
    can be recognised."""
    doc = Document(path)
    if doc.core_properties.keywords and MARKER in doc.core_properties.keywords:
        return False
    body = doc.element.body
    idx = _find_title_index(body)
    if idx is not None:
        title = _text(body[idx]).strip()
    elif flexible:
        idx, found = _find_loose_title(body, title)
        title = found or title or os.path.splitext(os.path.basename(path))[0]
    else:
        raise RuntimeError(f"{path}: could not find the title paragraph")
    if not ref:
        ref = _template_ref(doc, path)

    # drop the old centred logo / tagline / address / gold rule and the title (they move into the band)
    if idx is not None:
        for el in list(body)[:idx + 1]:
            if el.tag != qn("w:sectPr"):
                body.remove(el)

    try:
        _restyle_tables(doc)  # before the band is added, so the band itself is not treated as a data table
    except Exception:
        if not flexible:
            raise
    _ensure_base_styles(doc)
    for section in doc.sections:
        section.top_margin = brand.Inches(0.6)
        section.bottom_margin = brand.Inches(0.9)
        section.footer_distance = brand.Inches(0.0)
    first = next((el for el in body if el.tag != qn("w:sectPr")), None)
    band = brand.letterhead_band(doc, title.title() if title.isupper() else title, sub="Template", number=f"Ref. {ref}" if ref else None)
    # letterhead_band appends at the end of the body (before sectPr); move table + spacer to the top
    band_el, spacer_el = band._tbl, band._tbl.getnext()
    for el in (spacer_el, band_el):
        body.remove(el)
        if first is not None:
            first.addprevious(el)
        else:
            body.insert(0, el)
    # first.addprevious inserts before 'first', so insert band first then spacer after it
    band_el.addnext(spacer_el)

    brand.branded_footer(doc, center=f"Template Ref: {ref}" if ref else "")
    _restyle_headings(doc)
    kw = (doc.core_properties.keywords or "")
    doc.core_properties.keywords = (kw + " " + MARKER).strip()
    brand.fix_document(doc)
    doc.save(path)
    return True


def main(paths=None):
    paths = paths or sorted(glob.glob(os.path.join(ROOT, "*", "*.docx")))
    done = 0
    for path in paths:
        if restyle(path):
            done += 1
            print("restyled", os.path.relpath(path, ROOT))
        else:
            print("already done", os.path.relpath(path, ROOT))
    print(f"{done} file(s) restyled")




# ---------------------------------------------------------------- Excel templates

def restyle_xlsx(path):
    """Light-touch: bring section bars / header rows / input cells to the firm palette and add
    print set-up and a footer. Formulas, layout and cell positions are never touched."""
    import openpyxl
    from openpyxl.styles import PatternFill, Border, Side, Font
    wb = openpyxl.load_workbook(path)
    if wb.properties.keywords and MARKER in wb.properties.keywords:
        return False
    navy = PatternFill(start_color=brand.NAVY, end_color=brand.NAVY, fill_type="solid")
    soft_yellow = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
    gold_edge = Border(bottom=Side(style="medium", color=brand.GOLD))
    old_slate = {"FF556677", "00556677"}
    old_navy = {"FF1F3864", "001F3864"}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.fill and c.fill.fill_type == "solid":
                    rgb_value = c.fill.fgColor.rgb if isinstance(c.fill.fgColor.rgb, str) else None
                    if rgb_value in old_slate:
                        c.fill = navy
                        c.border = gold_edge
                    elif rgb_value in old_navy:
                        c.fill = navy
                    elif rgb_value in ("FFFFFF00", "00FFFF00"):
                        c.fill = soft_yellow
        brand.xlsx_page_setup(ws)
        ws.sheet_view.showGridLines = False
    wb.properties.keywords = ((wb.properties.keywords or "") + " " + MARKER).strip()
    wb.save(path)
    return True


def main_xlsx(paths=None):
    paths = paths or (sorted(glob.glob(os.path.join(ROOT, "*", "*.xlsx")))
                      + sorted(glob.glob(os.path.join(os.path.dirname(ROOT), "static", "downloads", "*.xlsx"))))
    for path in paths:
        print("restyled" if restyle_xlsx(path) else "already done", os.path.basename(path))


if __name__ == "__main__":
    main(sys.argv[1:] or None) if not any(a.endswith(".xlsx") for a in sys.argv[1:]) else None
    main_xlsx([a for a in sys.argv[1:] if a.endswith(".xlsx")] or None)
