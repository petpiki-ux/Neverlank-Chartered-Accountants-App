"""Valuation certificates: the text, the figures and the Word / PDF output on the firm letterhead.

The certificate is laid out like the firm's other documents (navy letterhead band, details strip, tinted panels, navy table headers, gold rules,
navy footer). The figures are typed once (low, adopted, high, and the area when a per-hectare column is wanted); the conclusion sentence, the
value table and the per-hectare figures are worked out from them, so the wording and the numbers cannot disagree.

Whatever the app does not know is left as a visible ``[CONFIRM: ...]`` placeholder (highlighted in the Word file, red in the PDF). A
certificate that still contains one cannot be signed as Managing Partner.
"""
import io
import re
from datetime import date
from xml.sax.saxutils import escape

PLACEHOLDER_RE = re.compile(r"\[CONFIRM:[^\]]*\]")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

CURRENCY_PREFIX = {"USD": "US$", "ZWG": "ZWG "}
CURRENCY_WORDS = {"USD": "United States dollars", "ZWG": "Zimbabwe Gold"}
CURRENCIES = ["USD", "ZWG"]

DEFAULT_TITLE = "Desktop Valuation Certificate"
DEFAULT_SIGNATORY = "Petros Piki CA(Z), MSc, RPA, RTA, PSPA Program"
DEFAULT_SIGNATORY_TITLE = "Managing Partner, Neverlank Chartered Accountants"

# The text fields in reading order (used for the placeholder check and the signature fingerprint)
TEXT_FIELDS = ["client_name", "certificate_title", "intro", "subject_asset", "title_reference", "asset_stage", "purpose", "basis_of_value",
               "valuation_type", "range_note", "approach", "assumptions", "certification", "signatory_name", "signatory_title",
               "sources_heading", "sources"]


# --------------------------------------------------------------------------------------------- the starting texts

DESKTOP_ASSUMPTIONS = (
    "This valuation holds only on the following assumptions and conditions.\n\n"
    "- **Title.** The claim or special grant is assumed valid, correctly registered, unencumbered and in good standing, with fees paid and any "
    "work programme met. None of this was verified.\n"
    "- **Area and boundaries.** The 9,000 hectares and the boundaries are as supplied by the client. Overlaps with other titles, protected areas "
    "or communities were not checked.\n"
    "- **No technical data.** No geological, drilling, coal quality or logistics data was reviewed. No resource or reserve is stated or implied.\n"
    "- **Comparable.** The benchmark is a single 2020 transaction settled in shares, converted at an assumed exchange rate. Public reports were "
    "relied on and not independently verified.\n"
    "- **Special capital gains tax.** A transfer of mining title, including by a shareholder in a company that holds it, attracts special capital "
    "gains tax payable by the transferee. Sources give 5% of the transaction value where ministerial approval of the transfer is required, and "
    "some report 20%; the rate must be confirmed with ZIMRA. Section 15 relief for companies under the same control may be elected, but it "
    "should not be assumed to cover this tax. The transferee must be a registered taxpayer.\n"
    "- **Not a technical report.** This is not a feasibility study, a SAMREC or JORC competent person's report, or a fairness opinion, and it must "
    "not be used to raise finance or report resources.\n"
    "- **Reliance and currency.** It is for the client and the stated purpose only, and should be revisited if material information emerges or "
    "after six months."
)

DESKTOP_APPROACH = (
    "The value rests on the market approach, using the one closely comparable public transaction in Binga and discounting it heavily for the "
    "absence of any resource data.\n\n"
    "**Comparable.** In 2020 Contango Holdings agreed to acquire 70% of the company holding the Lubu coalfield (now Muchesu), together with its "
    "loans and mining data, for £6.45 million in shares. That implies about £9.2 million, or roughly US$11.8 million at an assumed 1.28 US "
    "dollars to the pound, for 100% of 19,236 hectares, and about US$613 per hectare. The asset carried a reported NI 43-101 resource above 1.3 "
    "billion tonnes and over US$20 million of earlier exploration spend.\n\n"
    "**Adjustment.** The subject claim has no resource statement and no drilling, so it is valued at US$56 to US$222 per hectare, a discount of "
    "about 64% to 91% on the comparable. The adopted US$111 per hectare is about 82% below it. This discount is the valuer's judgement; no sale "
    "of a comparable bare claim in the district was identified.\n\n"
    "**Market context.** Richards Bay thermal coal futures reached US$131.40 per tonne in late October 2026, and a coking coal operation is "
    "already working in Binga. Against that, the claim must still carry exploration cost and risk, the 3% coal levy on gross sales and a 25% "
    "corporate tax rate plus the 3% AIDS levy, and it is exposed to the state's \"use it or lose it\" policy on idle coal grants."
)

DESKTOP_SOURCES = (
    "- [Contango Lubu acquisition terms](https://customnews.pk/?p=35530)\n"
    "- [Mining Weekly: Contango LSE entry](https://www.miningweekly.com/article/contango-makes-entry-on-lse-with-billion-tonne-zimbabwean-coal-asset-2020-06-18)\n"
    "- [Herald: Binga coking coal project](https://www.heraldonline.co.zw/binga-coking-coal-project-takes-shape-5/)\n"
    "- [MiningMX: coal price, October 2026](https://miningmx.com/2026-10-23-thungela-takes-gains-to-98-as-coal-nears-one-year-high/)\n"
    "- [NewsDay: coal levy and mining tax changes](https://www.newsday.co.zw/southerneye/local/article/200052575/how-tax-regime-changes-are-reshaping-zims-mining-law)\n"
    "- [ZIMRA: special capital gains tax](https://zimra.co.zw/news/2337-special-capital-gains-tax)\n"
    "- [Mining Weekly: use-it-or-lose-it policy](https://www.miningweekly.com/article/zim-acts-against-companies-holding-on-to-coal-claims-for-speculative-purposes-2013-01-25)"
)

CERTIFICATION_TEXT = (
    "I certify that this is a desktop valuation prepared on the information and assumptions stated above, and that the conclusion is my opinion "
    "of the indicative market value of the subject {noun} at the valuation date. [CONFIRM: I have no interest in the subject asset - signatory to confirm]"
)

# The "Desktop Valuation Certificate" wording: the Broxmen coal claim, Binga.
DESKTOP_TEMPLATE = {
    "certificate_title": DEFAULT_TITLE,
    "client_name": "Broxmen Investments (Private) Limited",
    "intro": ("This is a desktop valuation of an undeveloped coal claim in Binga, prepared for a company reorganisation. No site visit, title "
              "search or technical data review was carried out."),
    "subject_asset": "Coal claim or special grant of approximately 9,000 hectares, Binga District, Matabeleland North, Zimbabwe",
    "title_reference": "[CONFIRM: claim or special grant number, registration date and registering office]",
    "asset_stage": "Bare claim or special grant: no resource statement and no drilling",
    "purpose": "Reorganisation of company",
    "basis_of_value": "Indicative market value of a 100% interest, in United States dollars",
    "valuation_type": "Desktop valuation: no inspection, no title verification, no review of geological data",
    "currency": "USD", "area_ha": 9000.0, "low_value": 500000.0, "adopted_value": 1000000.0, "high_value": 2000000.0,
    "asset_noun": "claim",
    "range_note": ("The range is wide because no resource, drilling or quality data exists to narrow it. A competent person's resource "
                   "statement could move the value materially in either direction."),
    "approach": DESKTOP_APPROACH,
    "assumptions": DESKTOP_ASSUMPTIONS,
    "certification": CERTIFICATION_TEXT.format(noun="claim"),
    "signatory_name": DEFAULT_SIGNATORY,
    "signatory_title": DEFAULT_SIGNATORY_TITLE,
    "sources_heading": "Public reports, not independently verified (desktop valuation):",
    "sources": DESKTOP_SOURCES,
}

# A blank certificate: the structure and the standard closing wording, with the subject-specific parts left to complete.
BLANK_TEMPLATE = {
    "certificate_title": DEFAULT_TITLE,
    "client_name": "",
    "intro": "[CONFIRM: one or two sentences on what was valued, for whom, and what was NOT done (site visit, title search, review of data)]",
    "subject_asset": "", "title_reference": "", "asset_stage": "", "purpose": "",
    "basis_of_value": "Indicative market value of a 100% interest, in United States dollars",
    "valuation_type": "Desktop valuation: no inspection, no title verification, no review of geological data",
    "currency": "USD", "area_ha": None, "low_value": None, "adopted_value": None, "high_value": None, "asset_noun": "asset",
    "range_note": "",
    "approach": "[CONFIRM: the approach (market, income or cost), the comparable or data relied on, the adjustment made and why]",
    "assumptions": ("This valuation holds only on the following assumptions and conditions.\n\n"
                    "- **Title.** [CONFIRM: what is assumed about title, and that it was not verified]\n"
                    "- **Information supplied.** [CONFIRM: what the client supplied and was relied on without verification]\n"
                    "- **Reliance and currency.** It is for the client and the stated purpose only, and should be revisited if material information "
                    "emerges or after six months."),
    "certification": CERTIFICATION_TEXT.format(noun="asset"),
    "signatory_name": DEFAULT_SIGNATORY,
    "signatory_title": DEFAULT_SIGNATORY_TITLE,
    "sources_heading": "", "sources": "",
}

TEMPLATES = {"desktop": ("Desktop Valuation Certificate - the Broxmen coal claim, Binga (US$1,000,000)", DESKTOP_TEMPLATE),
             "blank": ("Blank certificate - same layout, wording to complete", BLANK_TEMPLATE)}


# --------------------------------------------------------------------------------------------- helpers

def unresolved(cert):
    """Every [CONFIRM: ...] placeholder still in the certificate, in order, without repeats."""
    seen, out = set(), []
    for f in TEXT_FIELDS:
        for m in PLACEHOLDER_RE.findall(getattr(cert, f, None) or ""):
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out


def next_reference(year=None):
    """NCA/DVC/<year>/<NNN>: the next free number for the year."""
    from models import ValuationCertificate
    year = year or date.today().year
    prefix = f"NCA/DVC/{year}/"
    top = 0
    for (ref,) in ValuationCertificate.query.with_entities(ValuationCertificate.reference).filter(
            ValuationCertificate.reference.like(prefix + "%")).all():
        tail = ref[len(prefix):]
        if tail.isdigit():
            top = max(top, int(tail))
    return f"{prefix}{top + 1:03d}"


def prefix(cert):
    return CURRENCY_PREFIX.get(cert.currency, (cert.currency or "") + " ")


def money(cert, value):
    """US$1,000,000 (whole units - a valuation is an opinion, not a ledger)."""
    if value is None:
        return "—"
    return f"{prefix(cert)}{value:,.0f}"


def plain(value):
    return "—" if value is None else f"{value:,.0f}"


def per_ha(cert, value):
    if value is None or not cert.area_ha:
        return None
    return value / cert.area_ha


def words(cert, value):
    """'one million United States dollars' (the receipts' number-to-words, worded for a certificate)."""
    from receipts import amount_in_words
    text = amount_in_words(round(value), "USD").rstrip()
    if text.endswith(" only"):
        text = text[:-5]
    text = text.replace("US dollars", CURRENCY_WORDS.get("USD"))
    if cert.currency == "ZWG":
        text = text.replace(CURRENCY_WORDS["USD"], CURRENCY_WORDS["ZWG"])
    return text[0].lower() + text[1:]


def long_date(d):
    return f"{d.day} {d.strftime('%B %Y')}" if d else "—"


def has_figures(cert):
    return cert.adopted_value is not None


def figures_problems(cert):
    """Why the figures cannot be used (low <= adopted <= high), or [] when they are fine."""
    out = []
    lo, mid, hi = cert.low_value, cert.adopted_value, cert.high_value
    if mid is None:
        out.append("Enter the adopted value.")
    if lo is not None and mid is not None and lo > mid:
        out.append("The low value is above the adopted value.")
    if hi is not None and mid is not None and hi < mid:
        out.append("The high value is below the adopted value.")
    if (lo is None) != (hi is None):
        out.append("Enter both the low and the high value, or neither.")
    return out


def conclusion_parts(cert):
    """(before, bold, after) of the conclusion sentence."""
    noun = (cert.asset_noun or "asset").strip()
    when = long_date(cert.valuation_date)
    if not has_figures(cert):
        return (f"The indicative market value of the {noun} at {when} is ", "[CONFIRM: adopted value]", ".")
    bold = f"{money(cert, cert.adopted_value)} ({words(cert, cert.adopted_value)})"
    after = "."
    if cert.low_value is not None and cert.high_value is not None:
        after = f", within a range of {money(cert, cert.low_value)} to {money(cert, cert.high_value)}."
    return (f"The indicative market value of the {noun} at {when} is ", bold, after)


def value_table(cert):
    """Header and rows of the value table: Low / Adopted / High with the per-hectare column when an area is given."""
    head = ["", f"Value ({prefix(cert).strip()})"]
    if cert.area_ha:
        head.append(f"Per hectare ({prefix(cert).strip()}, {cert.area_ha:,.0f} ha)")
    rows = []
    for label, v in (("Low", cert.low_value), ("Adopted", cert.adopted_value), ("High", cert.high_value)):
        if v is None:
            continue
        row = [label, plain(v)]
        if cert.area_ha:
            row.append(f"{per_ha(cert, v):,.0f}")
        rows.append(row)
    return head, rows


def particulars(cert):
    rows = [("Certificate reference", cert.reference), ("Client / title holder", cert.client_name), ("Subject asset", cert.subject_asset),
            ("Title reference", cert.title_reference), ("Asset stage", cert.asset_stage), ("Purpose", cert.purpose),
            ("Valuation date", long_date(cert.valuation_date)), ("Basis of value", cert.basis_of_value),
            ("Type of valuation", cert.valuation_type)]
    return [(k, v) for k, v in rows if (v or "").strip()]


def blocks(text):
    """Split free text into ('p', text) paragraphs and ('b', text) bullets (blank lines separate blocks; "- " starts a bullet)."""
    out = []
    for chunk in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue
        if all(ln.lstrip().startswith("- ") for ln in lines):
            out += [("b", ln.lstrip()[2:].strip()) for ln in lines]
        else:
            para = []
            for ln in lines:
                if ln.lstrip().startswith("- "):
                    if para:
                        out.append(("p", " ".join(para)))
                        para = []
                    out.append(("b", ln.lstrip()[2:].strip()))
                else:
                    para.append(ln.strip())
            if para:
                out.append(("p", " ".join(para)))
    return out


def pieces(text):
    """Cut a line into (text, bold, placeholder) pieces: **bold** lead-ins and [CONFIRM: ...] placeholders."""
    out = []
    pos = 0
    marks = sorted([(m.start(), m.end(), "b", m.group(1)) for m in BOLD_RE.finditer(text)] +
                   [(m.start(), m.end(), "p", m.group(0)) for m in PLACEHOLDER_RE.finditer(text)])
    for s, e, kind, inner in marks:
        if s < pos:
            continue
        if s > pos:
            out.append((text[pos:s], False, False))
        if kind == "b":
            # a placeholder can sit inside bold text
            for t, _b, ph in pieces(inner):
                out.append((t, True, ph))
        else:
            out.append((inner, False, True))
        pos = e
    if pos < len(text):
        out.append((text[pos:], False, False))
    return out


def sources_list(cert):
    """[(title, url)] from "- [Title](url)" lines (a plain line is kept as a title with no link)."""
    out = []
    for kind, text in blocks(cert.sources):
        m = LINK_RE.search(text)
        out.append((m.group(1), m.group(2)) if m else (text, ""))
    return out


def firm_address():
    import brand_docs as brand
    f = brand.firm_info()
    return ", ".join(x for x in (f.get("address"), f.get("web")) if x)


def sign_state(cert):
    """True when the certificate carries the Managing Partner's signature (signed, unchanged, and he is the signatory)."""
    import brand_docs as brand
    return bool(brand.is_managing_partner(cert.signatory_name) and brand.signature_valid(cert))


def banner(cert):
    return "DRAFT - placeholders to complete" if unresolved(cert) else None


def filename(cert, ext):
    ref = re.sub(r"[^A-Za-z0-9_-]+", "_", cert.reference or f"valuation{cert.id}").strip("_")
    return f"{ref}.{ext}"


# --------------------------------------------------------------------------------------------- Word

def _grid(table, widths_in):
    """Set the table grid columns too (Word follows the cell widths, LibreOffice follows the grid)."""
    from docx.shared import Inches
    for col, w in zip(table.columns, widths_in):
        col.width = Inches(w)


def build_docx(cert, firm=None):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
    from docx.shared import Inches, Mm, Pt
    import brand_docs as brand

    firm = firm or brand.firm_info()
    doc = Document()
    for s in doc.sections:
        s.page_width, s.page_height = Mm(210), Mm(297)
    brand.setup_document(doc, margins_in=0.85, base_size=10.5)
    brand.branded_footer(doc, firm, center=cert.reference or "")
    brand.letterhead_band(doc, cert.certificate_title or DEFAULT_TITLE, sub=banner(cert), number=cert.reference, firm=firm)
    brand.meta_strip(doc, [("Certificate no.", cert.reference), ("Valuation date", long_date(cert.valuation_date)),
                           ("Issued", long_date(cert.certificate_date)), ("Currency", prefix(cert).strip())])
    brand.party_block(doc, "Prepared for", cert.client_name, [cert.purpose] if (cert.purpose or "").strip() else [])

    def runs(par, text, size=10.5, bold=False, colour=brand.INK):
        for t, b, ph in pieces(text):
            r = brand._run(par, t, size=size, bold=bold or b or ph, colour=colour)
            if ph:
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW
        return par

    def para(text="", after=7, size=10.5, keep=False, bold=False, colour=brand.INK):
        p = doc.add_paragraph()
        brand._tight(p, before=0, after=after, line=1.12)
        if keep:
            p.paragraph_format.keep_with_next = True
        if text:
            runs(p, text, size=size, bold=bold, colour=colour)
        return p

    def bullets(items):
        for i, text in enumerate(items):
            p = doc.add_paragraph(style="List Bullet")
            brand._tight(p, before=0, after=6 if i == len(items) - 1 else 3, line=1.1)
            runs(p, text, size=10)

    def body(text):
        run, last = [], None
        flat = blocks(text)
        for i, (kind, t) in enumerate(flat):
            if kind == "b":
                run.append(t)
                if i + 1 >= len(flat) or flat[i + 1][0] != "b":
                    bullets(run)
                    run = []
            else:
                para(t)

    if (cert.intro or "").strip():
        para(cert.intro, after=6)

    # particulars
    h = brand.heading(doc, "Certificate particulars", level=1)
    rows = particulars(cert)
    width = brand.usable_width_in(doc)
    table = doc.add_table(rows=len(rows), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    brand._fixed_widths(table, [1.75, width - 1.75])
    _grid(table, [1.75, width - 1.75])
    brand.table_borders(table, colour=brand.LINE, size=4, inside=True)
    for row, (k, v) in zip(table.rows, rows):
        left, right = row.cells
        brand.shade(left, brand.TINT)
        brand.cell_margins(left, top=40, bottom=40, left=130, right=100)
        brand.cell_margins(right, top=40, bottom=40, left=130, right=100)
        p = left.paragraphs[0]
        brand._tight(p)
        brand._run(p, k, size=9.5, bold=True, colour=brand.NAVY)
        p = right.paragraphs[0]
        brand._tight(p)
        runs(p, v, size=10)
    gap0 = doc.add_paragraph()
    brand._tight(gap0, before=0, after=0)
    for r_ in gap0.runs:
        r_.font.size = Pt(4)

    # conclusion
    brand.heading(doc, "Valuation conclusion", level=1)
    pre, strong, post = conclusion_parts(cert)
    p = doc.add_paragraph()
    brand._tight(p, before=0, after=8, line=1.12)
    p.paragraph_format.keep_with_next = True
    brand._run(p, pre, size=10.5)
    r = brand._run(p, strong, size=10.5, bold=True, colour=brand.NAVY)
    if PLACEHOLDER_RE.search(strong):
        r.font.highlight_color = WD_COLOR_INDEX.YELLOW
    brand._run(p, post, size=10.5)
    if has_figures(cert):
        head, vrows = value_table(cert)
        panel = brand.total_panel(doc, "Adopted value", money(cert, cert.adopted_value), align_right=True, width_in=3.4)
        for cell in panel.rows[0].cells:
            for pp in cell.paragraphs:
                pp.paragraph_format.keep_with_next = True
        gap = doc.add_paragraph()
        brand._tight(gap, before=0, after=0, line=0.5)
        gap.paragraph_format.keep_with_next = True
        t = doc.add_table(rows=1 + len(vrows), cols=len(head))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        vw = [1.5, 2.0, width - 3.5] if len(head) == 3 else [1.8, width - 1.8]
        brand._fixed_widths(t, vw)
        _grid(t, vw)
        for ci, text in enumerate(head):
            brand._run(t.rows[0].cells[ci].paragraphs[0], text, size=9.5, bold=True, colour="FFFFFF")
        for ri, row in enumerate(vrows, start=1):
            adopted = row[0] == "Adopted"
            for ci, text in enumerate(row):
                cell = t.rows[ri].cells[ci]
                brand._run(cell.paragraphs[0], text, size=10.5 if adopted else 10, bold=adopted or ci == 0,
                           colour=brand.NAVY if (adopted or ci == 0) else brand.INK)
                if adopted:
                    brand.shade(cell, "FBF4E4")
        brand.style_table(t, header_rows=1, numeric_from=1)
        # style_table resets run colours of the header only; keep our weights on the body rows
        for ri, row in enumerate(vrows, start=1):
            for ci, text in enumerate(row):
                for rr in t.rows[ri].cells[ci].paragraphs[0].runs:
                    rr.font.size = Pt(10.5 if row[0] == "Adopted" else 10)
                    rr.bold = (row[0] == "Adopted") or ci == 0
        for ri, trow in enumerate(t.rows[:-1]):  # keep the panel, the gap and the table rows together
            for cell in trow.cells:
                for pp in cell.paragraphs:
                    pp.paragraph_format.keep_with_next = True
        sp = doc.add_paragraph()
        brand._tight(sp, after=4)
    if (cert.range_note or "").strip():
        para(cert.range_note, after=6)

    # approach
    if (cert.approach or "").strip():
        brand.heading(doc, "Basis and approach", level=1)
        body(cert.approach)

    # assumptions
    if (cert.assumptions or "").strip():
        brand.heading(doc, "Assumptions and limiting conditions", level=1)
        body(cert.assumptions)

    # certification
    brand.heading(doc, "Certification", level=1)
    if (cert.certification or "").strip():
        for kind, t in blocks(cert.certification):
            para(t, after=8, keep=True)
    signed = sign_state(cert)
    sig_p = doc.add_paragraph()
    brand._tight(sig_p, before=6, after=0)
    sig_p.paragraph_format.keep_with_next = True
    if not (signed and brand.add_signature_picture(sig_p, width_in=1.7)):
        brand._tight(sig_p, before=26, after=0)
    line = doc.add_paragraph()
    brand._tight(line, before=0, after=0)
    line.paragraph_format.keep_with_next = True
    brand.para_border(line, edge="top", colour=brand.NAVY, size=6, space=1)
    pf = line.paragraph_format
    pf.right_indent = Inches(width - 3.4)
    brand._run(line, cert.signatory_name or "", size=10.5, bold=True, colour=brand.NAVY)
    for text, size, colour in ((cert.signatory_title, 9.5, brand.MUTED),
                               (", ".join(x for x in (firm.get("address"), firm.get("web")) if x), 9, brand.MUTED),
                               (f"Date: {long_date(cert.certificate_date)}" if cert.certificate_date else "Date: ______________________", 9.5, brand.INK),
                               (f"Signed electronically by the Managing Partner on {cert.mp_signed_at.strftime('%d %b %Y')}" if signed and cert.mp_signed_at else "", 8.5, brand.MUTED)):
        if text:
            q = doc.add_paragraph()
            brand._tight(q, before=1, after=0)
            q.paragraph_format.keep_with_next = True
            brand._run(q, text, size=size, colour=colour)

    # sources
    src = sources_list(cert)
    if src:
        h = brand.heading(doc, "Sources", level=2)
        if (cert.sources_heading or "").strip():
            p = para(cert.sources_heading, after=3, size=9, colour=brand.MUTED)
        for title, url in src:
            q = doc.add_paragraph(style="List Bullet")
            brand._tight(q, before=0, after=1)
            brand._run(q, title, size=8.5, colour=brand.INK)
            if url:
                brand._run(q, f"  {url}", size=7.5, colour=brand.MUTED)

    brand.fix_document(doc)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------------------------- PDF

def build_pdf(cert, firm=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (HRFlowable, Image, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    import brand_docs as brand

    firm = firm or brand.firm_info()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=12 * mm, bottomMargin=20 * mm,
                            title=f"{cert.certificate_title} {cert.reference}", author=firm.get("name", ""))
    width = A4[0] - 36 * mm
    col = lambda h: colors.HexColor("#" + h)
    base = ParagraphStyle("vb", fontName="Helvetica", fontSize=9.5, leading=13.6, textColor=col(brand.INK), spaceAfter=6)
    small = ParagraphStyle("vs", parent=base, fontSize=8.5, leading=11.5, spaceAfter=2, textColor=col(brand.MUTED))
    h1 = ParagraphStyle("vh1", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=col(brand.NAVY), spaceBefore=12, spaceAfter=2)
    h2 = ParagraphStyle("vh2", parent=h1, fontSize=11, spaceBefore=10)

    def fmt(text, bold_all=False):
        out = ""
        for t, b, ph in pieces(text):
            t = escape(t)
            if ph:
                t = f"<font color='#c0392b'><b>{t}</b></font>"
            elif b or bold_all:
                t = f"<b>{t}</b>"
            out += t
        return out

    def head(text, style=h1):
        return [Paragraph(escape(text), style), HRFlowable(width="100%", thickness=1.1, color=col(brand.GOLD), spaceBefore=1, spaceAfter=7)]

    def body(text):
        flat = blocks(text)
        out, items = [], []

        def flush():
            nonlocal items
            if items:
                out.append(ListFlowable([ListItem(Paragraph(fmt(t), ParagraphStyle("lb", parent=base, spaceAfter=3, fontSize=9.3)), leftIndent=12)
                                         for t in items], bulletType="bullet", start="•", leftIndent=12, bulletColor=col(brand.GOLD)))
                out.append(Spacer(1, 3))
                items = []
        for kind, t in flat:
            if kind == "b":
                items.append(t)
            else:
                flush()
                out.append(Paragraph(fmt(t), base))
        flush()
        return out

    story = [brand.pdf_letterhead(width, cert.certificate_title or DEFAULT_TITLE, number=cert.reference, sub=banner(cert), firm=firm),
             Spacer(1, 4 * mm),
             brand.pdf_meta_strip(width, [("Certificate no.", cert.reference), ("Valuation date", long_date(cert.valuation_date)),
                                          ("Issued", long_date(cert.certificate_date)), ("Currency", prefix(cert).strip())]),
             Spacer(1, 3 * mm)]

    party = Table([[[Paragraph(f"<font size='7' color='#{brand.GOLD}'><b>PREPARED FOR</b></font>", base),
                     Paragraph(f"<font name='Helvetica-Bold' size='11.5' color='#{brand.NAVY}'>{escape(cert.client_name or '')}</font>",
                               ParagraphStyle("pp", parent=base, spaceAfter=1)),
                     Paragraph(escape(cert.purpose or ""), ParagraphStyle("pq", parent=small, textColor=col("555555")))
                     if (cert.purpose or "").strip() else Spacer(1, 0)]]], colWidths=[width])
    party.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col(brand.TINT)), ("LINEBEFORE", (0, 0), (0, -1), 3, col(brand.GOLD)),
                               ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [party, Spacer(1, 3 * mm)]
    if (cert.intro or "").strip():
        story.append(Paragraph(fmt(cert.intro), base))

    # particulars
    story += head("Certificate particulars")
    prow = [[Paragraph(f"<b>{escape(k)}</b>", ParagraphStyle("pk", parent=base, fontSize=9, textColor=col(brand.NAVY), spaceAfter=0)),
             Paragraph(fmt(v), ParagraphStyle("pv", parent=base, fontSize=9.3, spaceAfter=0))] for k, v in particulars(cert)]
    pt = Table(prow, colWidths=[42 * mm, width - 42 * mm])
    pt.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), col(brand.TINT)), ("BOX", (0, 0), (-1, -1), 0.5, col(brand.LINE)),
                            ("INNERGRID", (0, 0), (-1, -1), 0.5, col(brand.LINE)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                            ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story.append(pt)

    # conclusion
    story += head("Valuation conclusion")
    pre, strong, post = conclusion_parts(cert)
    strong_html = f"<font color='#c0392b'><b>{escape(strong)}</b></font>" if PLACEHOLDER_RE.search(strong) else f"<b><font color='#{brand.NAVY}'>{escape(strong)}</font></b>"
    story.append(Paragraph(escape(pre) + strong_html + escape(post), base))
    if has_figures(cert):
        vh, vr = value_table(cert)
        data = [[Paragraph(f"<b>{escape(h)}</b>", ParagraphStyle("vth", parent=base, textColor=colors.white, fontSize=8.8, spaceAfter=0,
                                                                 alignment=0 if i == 0 else 2)) for i, h in enumerate(vh)]]
        adopted_row = None
        for i, row in enumerate(vr, start=1):
            if row[0] == "Adopted":
                adopted_row = i
            data.append([Paragraph(("<b>%s</b>" if (row[0] == "Adopted" or ci == 0) else "%s") % escape(t),
                                   ParagraphStyle("vtd", parent=base, spaceAfter=0, fontSize=10 if row[0] == "Adopted" else 9.5,
                                                  alignment=0 if ci == 0 else 2, textColor=col(brand.NAVY if (row[0] == "Adopted" or ci == 0) else brand.INK)))
                         for ci, t in enumerate(row)])
        cw = [38 * mm, 42 * mm, width - 80 * mm] if len(vh) == 3 else [38 * mm, 42 * mm]
        vt = Table(data, colWidths=cw, hAlign="LEFT")
        style = [("BACKGROUND", (0, 0), (-1, 0), col(brand.NAVY)), ("LINEBELOW", (0, 1), (-1, -1), 0.5, col(brand.LINE)),
                 ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        if adopted_row:
            style.append(("BACKGROUND", (0, adopted_row), (-1, adopted_row), col("FBF4E4")))
            style.append(("LINEABOVE", (0, adopted_row), (-1, adopted_row), 0.8, col(brand.GOLD)))
            style.append(("LINEBELOW", (0, adopted_row), (-1, adopted_row), 0.8, col(brand.GOLD)))
        vt.setStyle(TableStyle(style))
        total = Table([[Paragraph("<font color='#FFFFFF'><b>Adopted value</b></font>", ParagraphStyle("tp", parent=base, fontSize=10.5, spaceAfter=0)),
                        Paragraph(f"<font name='Helvetica-Bold' size='12' color='#{brand.GOLD_LIGHT}'>{escape(money(cert, cert.adopted_value))}</font>",
                                  ParagraphStyle("tp2", parent=base, alignment=2, spaceAfter=0))]], colWidths=[40 * mm, 48 * mm], hAlign="RIGHT")
        total.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col(brand.NAVY)), ("TOPPADDING", (0, 0), (-1, -1), 7),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9)]))
        story += [total, Spacer(1, 3 * mm), vt, Spacer(1, 3 * mm)]
    if (cert.range_note or "").strip():
        story.append(Paragraph(fmt(cert.range_note), base))

    if (cert.approach or "").strip():
        story += head("Basis and approach") + body(cert.approach)
    if (cert.assumptions or "").strip():
        story += head("Assumptions and limiting conditions") + body(cert.assumptions)

    # certification, kept together with the signature
    cert_block = head("Certification")
    for kind, t in blocks(cert.certification):
        cert_block.append(Paragraph(fmt(t), base))
    signed = sign_state(cert)
    sig = []
    if signed and brand.signature_bytes():
        img = Image(io.BytesIO(brand.signature_bytes()), width=42 * mm, height=42 * mm * 178 / 294)
        img.hAlign = "LEFT"
        sig.append(img)
    else:
        sig.append(Spacer(1, 16 * mm))
    rule = Table([[""]], colWidths=[84 * mm], hAlign="LEFT", rowHeights=[1])
    rule.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.8, col(brand.NAVY))]))
    sig.append(rule)
    sig.append(Paragraph(f"<b><font color='#{brand.NAVY}'>{escape(cert.signatory_name or '')}</font></b>", ParagraphStyle("sg", parent=base, fontSize=10.3, spaceAfter=1)))
    for text in (cert.signatory_title, ", ".join(x for x in (firm.get("address"), firm.get("web")) if x)):
        if text:
            sig.append(Paragraph(escape(text), ParagraphStyle("sg2", parent=small, spaceAfter=1)))
    sig.append(Paragraph(escape(f"Date: {long_date(cert.certificate_date)}" if cert.certificate_date else "Date: ______________________"),
                         ParagraphStyle("sg3", parent=base, fontSize=9.3, spaceBefore=2, spaceAfter=1)))
    if signed and cert.mp_signed_at:
        sig.append(Paragraph(escape(f"Signed electronically by the Managing Partner on {cert.mp_signed_at.strftime('%d %b %Y')}"),
                             ParagraphStyle("sg4", parent=small, fontSize=8)))
    story.append(KeepTogether(cert_block + [Spacer(1, 2 * mm)] + sig))

    src = sources_list(cert)
    if src:
        story += head("Sources", h2)
        if (cert.sources_heading or "").strip():
            story.append(Paragraph(escape(cert.sources_heading), small))
        items = []
        for title, url in src:
            link = f" <font size='7' color='#{brand.MUTED}'><link href='{escape(url, {chr(34): '&quot;'})}'>{escape(url)}</link></font>" if url else ""
            items.append(ListItem(Paragraph(escape(title) + link, ParagraphStyle("sl", parent=small, fontSize=8.3, textColor=col(brand.INK),
                                                                                  spaceAfter=1)), leftIndent=10))
        story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=10, bulletFontSize=7))

    footer = brand.pdf_footer(firm)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
