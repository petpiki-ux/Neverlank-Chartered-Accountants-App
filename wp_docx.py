"""Working papers in the app, Word side: a .docx is shown as a page you can type into, and every edit is written back into the same .docx
(layout, letterhead, tables and styles stay exactly as the template made them - only the text changes).

Everything is addressed by a *path*: [b] is the b-th paragraph/table of the page body; after a table the path continues [row, cell, b] into
that cell, so "3.1.2.0" is the first block of cell 2 of row 1 of the table that is block 3. Paths are only valid for the file as it stands, so
the page reloads after any change that adds or removes paragraphs or rows.
"""
import base64
import copy
import html
import os
import re
import tempfile
from datetime import date

from docx import Document as DocxDocument
from docx.oxml.ns import qn

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PX_PER_TWIP = 1 / 15.0                     # 1440 twips = 1 inch = 96 px
_HIGHLIGHT = {"yellow": "#ffff00", "green": "#00ff00", "cyan": "#00ffff", "magenta": "#ff00ff", "blue": "#0000ff", "red": "#ff0000",
              "darkBlue": "#00008b", "darkCyan": "#008b8b", "darkGreen": "#006400", "darkMagenta": "#8b008b", "darkRed": "#8b0000",
              "darkYellow": "#808000", "darkGray": "#a9a9a9", "lightGray": "#d3d3d3", "black": "#000000", "white": "#ffffff"}


# ------------------------------------------------------------------ structure helpers
def _blocks(el):
    return [c for c in el if c.tag in (qn("w:p"), qn("w:tbl"))]


def _rows(tbl):
    return [c for c in tbl if c.tag == qn("w:tr")]


def _cells(tr):
    return [c for c in tr if c.tag == qn("w:tc")]


def resolve(doc, path):
    """The paragraph / table element at `path` (a list of ints), or None."""
    el = doc.element.body
    segs = list(path)
    i = 0
    try:
        while True:
            blk = _blocks(el)[segs[i]]
            i += 1
            if i == len(segs):
                return blk
            if blk.tag != qn("w:tbl"):
                return None
            el = _cells(_rows(blk)[segs[i]])[segs[i + 1]]
            i += 2
    except (IndexError, TypeError):
        return None


def parse_path(text):
    try:
        return [int(x) for x in str(text).split(".")]
    except ValueError:
        return None


def _val(el, default=True):
    """A w:b / w:i style on-off element: present with no value = on."""
    if el is None:
        return None
    v = el.get(qn("w:val"))
    if v is None:
        return default
    return v.lower() not in ("0", "false", "off", "none")


def _run_text(r):
    out = []
    for c in r:
        if c.tag == qn("w:t"):
            out.append(c.text or "")
        elif c.tag == qn("w:tab"):
            out.append("\t")
        elif c.tag in (qn("w:br"), qn("w:cr")):
            if c.get(qn("w:type")) in (None, "textWrapping"):
                out.append("\n")
    return "".join(out)


def set_run_text(r, text):
    """Replaces a run's text, keeping its formatting (a run that holds a picture is left alone)."""
    if r.find(".//" + qn("w:drawing")) is not None or r.find(".//" + qn("w:pict")) is not None:
        return
    for c in list(r):
        if c.tag in (qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")):
            r.remove(c)
    from docx.oxml import OxmlElement
    parts = re.split(r"(\t|\n)", text or "")
    for part in parts:
        if part == "":
            continue
        if part == "\t":
            r.append(OxmlElement("w:tab"))
        elif part == "\n":
            r.append(OxmlElement("w:br"))
        else:
            t = OxmlElement("w:t")
            t.text = part
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            r.append(t)


def para_runs(p):
    """Every text run of a paragraph in reading order (including those inside hyperlinks)."""
    return [r for r in p.iter(qn("w:r")) if r.find(qn("w:instrText")) is None and r.find(qn("w:fldChar")) is None]


def para_text(p):
    return "".join(_run_text(r) for r in para_runs(p))


# ------------------------------------------------------------------ styles
class _Styles:
    def __init__(self, doc):
        self.doc = doc
        root = doc.styles.element
        self.by_id = {s.get(qn("w:styleId")): s for s in root.findall(qn("w:style"))}
        dd = root.find(qn("w:docDefaults"))
        self.default_rpr = {}
        self.default_ppr = {}
        if dd is not None:
            rpr = dd.find(".//" + qn("w:rPrDefault") + "/" + qn("w:rPr"))
            self.default_rpr = rpr_props(rpr)
            ppr = dd.find(".//" + qn("w:pPrDefault") + "/" + qn("w:pPr"))
            self.default_ppr = ppr_props(ppr)
        self.default_para_style = None
        for s in self.by_id.values():
            if s.get(qn("w:type")) == "paragraph" and s.get(qn("w:default")) in ("1", "true"):
                self.default_para_style = s.get(qn("w:styleId"))

    def chain(self, style_id):
        out, seen = [], set()
        while style_id and style_id in self.by_id and style_id not in seen:
            seen.add(style_id)
            s = self.by_id[style_id]
            out.append(s)
            b = s.find(qn("w:basedOn"))
            style_id = b.get(qn("w:val")) if b is not None else None
        return list(reversed(out))

    def rpr(self, style_id):
        d = {}
        for s in self.chain(style_id):
            d.update(rpr_props(s.find(qn("w:rPr"))))
        return d

    def ppr(self, style_id):
        d = {}
        for s in self.chain(style_id):
            d.update(ppr_props(s.find(qn("w:pPr"))))
        return d

    def borders(self, style_id):
        d = {}
        for s in self.chain(style_id):
            tp = s.find(qn("w:tblPr"))
            if tp is not None:
                b = tp.find(qn("w:tblBorders"))
                if b is not None:
                    d.update(border_props(b))
        return d


def rpr_props(rpr):
    d = {}
    if rpr is None:
        return d
    for tag, key in (("w:b", "bold"), ("w:i", "italic"), ("w:strike", "strike"), ("w:caps", "caps"), ("w:smallCaps", "smallcaps")):
        v = _val(rpr.find(qn(tag)))
        if v is not None:
            d[key] = v
    u = rpr.find(qn("w:u"))
    if u is not None:
        d["underline"] = (u.get(qn("w:val")) or "single") != "none"
    c = rpr.find(qn("w:color"))
    if c is not None and c.get(qn("w:val")) not in (None, "auto"):
        d["color"] = "#" + c.get(qn("w:val"))
    sz = rpr.find(qn("w:sz"))
    if sz is not None and sz.get(qn("w:val")):
        try:
            d["size"] = int(sz.get(qn("w:val"))) / 2.0
        except ValueError:
            pass
    f = rpr.find(qn("w:rFonts"))
    if f is not None:
        name = f.get(qn("w:ascii")) or f.get(qn("w:hAnsi"))
        if name:
            d["font"] = name
    h = rpr.find(qn("w:highlight"))
    if h is not None and h.get(qn("w:val")) in _HIGHLIGHT:
        d["bg"] = _HIGHLIGHT[h.get(qn("w:val"))]
    sh = rpr.find(qn("w:shd"))
    if sh is not None and sh.get(qn("w:fill")) not in (None, "auto"):
        d["bg"] = "#" + sh.get(qn("w:fill"))
    va = rpr.find(qn("w:vertAlign"))
    if va is not None:
        d["vert"] = va.get(qn("w:val"))
    return d


def ppr_props(ppr):
    d = {}
    if ppr is None:
        return d
    jc = ppr.find(qn("w:jc"))
    if jc is not None:
        d["align"] = {"both": "justify", "center": "center", "right": "right", "end": "right", "left": "left", "start": "left"}.get(jc.get(qn("w:val")), "left")
    ind = ppr.find(qn("w:ind"))
    if ind is not None:
        for k, a in (("left", "w:left"), ("left", "w:start"), ("right", "w:right"), ("first", "w:firstLine"), ("hanging", "w:hanging")):
            v = ind.get(qn(a))
            if v:
                try:
                    d["ind_" + k] = int(v) * _PX_PER_TWIP
                except ValueError:
                    pass
    sp = ppr.find(qn("w:spacing"))
    if sp is not None:
        for k, a in (("before", "w:before"), ("after", "w:after")):
            v = sp.get(qn(a))
            if v:
                try:
                    d["sp_" + k] = int(v) * _PX_PER_TWIP
                except ValueError:
                    pass
        line, rule = sp.get(qn("w:line")), sp.get(qn("w:lineRule"))
        if line:
            try:
                if rule in (None, "auto"):
                    d["line"] = int(line) / 240.0
                else:
                    d["line_px"] = int(line) * _PX_PER_TWIP
            except ValueError:
                pass
    sh = ppr.find(qn("w:shd"))
    if sh is not None and sh.get(qn("w:fill")) not in (None, "auto"):
        d["bg"] = "#" + sh.get(qn("w:fill"))
    pb = ppr.find(qn("w:pBdr"))
    if pb is not None:
        d["borders"] = border_props(pb)
    if ppr.find(qn("w:numPr")) is not None:
        d["list"] = True
    return d


def border_props(el):
    out = {}
    for side in ("top", "left", "bottom", "right", "start", "end", "insideH", "insideV"):
        b = el.find(qn("w:" + side))
        if b is None:
            continue
        val = b.get(qn("w:val"))
        if val in (None, "nil", "none"):
            out[side] = None
            continue
        try:
            w = max(1, round(int(b.get(qn("w:sz")) or 4) / 8 * 1.333))
        except ValueError:
            w = 1
        color = b.get(qn("w:color"))
        out[side] = f"{w}px solid {'#' + color if color and color != 'auto' else '#000'}"
    return out


def _css_run(props):
    css = []
    if props.get("bold"):
        css.append("font-weight:700")
    if props.get("italic"):
        css.append("font-style:italic")
    deco = []
    if props.get("underline"):
        deco.append("underline")
    if props.get("strike"):
        deco.append("line-through")
    if deco:
        css.append("text-decoration:" + " ".join(deco))
    if props.get("color"):
        css.append("color:" + props["color"])
    if props.get("size"):
        css.append("font-size:%.1fpt" % props["size"])
    if props.get("font"):
        css.append("font-family:'%s',Calibri,Arial,sans-serif" % props["font"].replace("'", ""))
    if props.get("bg"):
        css.append("background:" + props["bg"])
    if props.get("caps"):
        css.append("text-transform:uppercase")
    if props.get("smallcaps"):
        css.append("font-variant:small-caps")
    if props.get("vert") == "superscript":
        css.append("vertical-align:super;font-size:smaller")
    elif props.get("vert") == "subscript":
        css.append("vertical-align:sub;font-size:smaller")
    return ";".join(css)


# ------------------------------------------------------------------ rendering
class _Ctx:
    def __init__(self, doc):
        self.doc = doc
        self.styles = _Styles(doc)
        self.images = {}
        self.read_only_count = 0

    def image_uri(self, rid, part=None):
        part = part or self.doc.part
        key = (id(part), rid)
        if key not in self.images:
            try:
                ip = part.related_parts[rid]
                mime = getattr(ip, "content_type", "image/png")
                self.images[key] = f"data:{mime};base64,{base64.b64encode(ip.blob).decode()}"
            except Exception:
                self.images[key] = ""
        return self.images[key]


def _run_html(ctx, p, r, idx, base, part=None):
    """One run as a span. Pictures are shown but not editable."""
    props = dict(base)
    rs = r.find(qn("w:rPr"))
    if rs is not None:
        rst = rs.find(qn("w:rStyle"))
        if rst is not None:
            props.update(ctx.styles.rpr(rst.get(qn("w:val"))))
        props.update(rpr_props(rs))
    css = _css_run(props)
    pics = []
    for blip in r.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"):
        rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        if not rid:
            continue
        w = h = None
        ext = None
        for e in r.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"):
            ext = e
            break
        size = ""
        if ext is not None:
            try:
                size = f' style="width:{int(ext.get("cx")) / 9525:.0f}px;height:auto;max-width:100%"'
            except (TypeError, ValueError):
                pass
        uri = ctx.image_uri(rid, part)
        if uri:
            pics.append(f'<img src="{uri}"{size} alt="">')
    if pics:
        return f'<span class="wr pic" contenteditable="false" data-r="{idx}">{"".join(pics)}</span>'
    text = _run_text(r)
    if text == "":
        return f'<span class="wr" data-r="{idx}"{(" style=" + chr(34) + html.escape(css, quote=True) + chr(34)) if css else ""}></span>'
    return f'<span class="wr" data-r="{idx}"{(" style=" + chr(34) + html.escape(css, quote=True) + chr(34)) if css else ""}>{html.escape(text)}</span>'


def _para_html(ctx, p, path, in_table=False, part=None):
    ppr_el = p.find(qn("w:pPr"))
    style_id = None
    if ppr_el is not None and ppr_el.find(qn("w:pStyle")) is not None:
        style_id = ppr_el.find(qn("w:pStyle")).get(qn("w:val"))
    style_id = style_id or ctx.styles.default_para_style
    pp = dict(ctx.styles.default_ppr)
    pp.update(ctx.styles.ppr(style_id))
    pp.update(ppr_props(ppr_el))
    base = dict(ctx.styles.default_rpr)
    base.update(ctx.styles.rpr(style_id))
    css = []
    if pp.get("align"):
        css.append("text-align:" + pp["align"])
    if pp.get("ind_left"):
        css.append("padding-left:%.0fpx" % pp["ind_left"])
    if pp.get("ind_hanging") and not pp.get("list"):
        css.append("text-indent:-%.0fpx" % pp["ind_hanging"])
    elif pp.get("ind_first"):
        css.append("text-indent:%.0fpx" % pp["ind_first"])
    css.append("margin:%.0fpx 0 %.0fpx" % (0 if in_table else pp.get("sp_before", 0), 2 if in_table else max(pp.get("sp_after", 0), 2)))
    if pp.get("line"):
        css.append("line-height:%.2f" % max(pp["line"], 1.0))
    if pp.get("bg"):
        css.append("background:" + pp["bg"])
    for side, v in (pp.get("borders") or {}).items():
        if v and side in ("top", "bottom", "left", "right"):
            css.append(f"border-{side}:{v}")
    if base.get("size"):
        css.append("font-size:%.1fpt" % base["size"])
    if base.get("font"):
        css.append("font-family:'%s',Calibri,Arial,sans-serif" % base["font"].replace("'", ""))
    if base.get("color"):
        css.append("color:" + base["color"])
    runs = para_runs(p)
    inner = "".join(_run_html(ctx, p, r, i, base, part) for i, r in enumerate(runs))
    marker = ' data-list="1"' if pp.get("list") else ""
    has_pic = any(r.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip") is not None for r in runs)
    editable = "false" if (has_pic and not para_text(p).strip()) else "true"
    placeholder = "" if inner.replace("<span", "").strip() and para_text(p) else " empty"
    return (f'<div class="wp-p{placeholder}" contenteditable="{editable}" data-path="{".".join(map(str, path))}"{marker} '
            f'style="{html.escape(";".join(css), quote=True)}" spellcheck="true">{inner}</div>')


def _cell_borders(tbl_borders, tc):
    out = dict(tbl_borders)
    tcp = tc.find(qn("w:tcPr"))
    if tcp is not None:
        b = tcp.find(qn("w:tcBorders"))
        if b is not None:
            out.update(border_props(b))
    return out


def _table_html(ctx, tbl, path, part=None):
    tp = tbl.find(qn("w:tblPr"))
    style_id = None
    if tp is not None and tp.find(qn("w:tblStyle")) is not None:
        style_id = tp.find(qn("w:tblStyle")).get(qn("w:val"))
    tb = dict(ctx.styles.borders(style_id)) if style_id else {}
    if tp is not None and tp.find(qn("w:tblBorders")) is not None:
        tb.update(border_props(tp.find(qn("w:tblBorders"))))
    grid = tbl.find(qn("w:tblGrid"))
    widths = []
    if grid is not None:
        for gc in grid.findall(qn("w:gridCol")):
            try:
                widths.append(int(gc.get(qn("w:w"))) * _PX_PER_TWIP)
            except (TypeError, ValueError):
                widths.append(0)
    total = sum(widths) or 0
    rows = _rows(tbl)
    # rowspan from vMerge
    spans = {}
    colpos = []
    for ri, tr in enumerate(rows):
        pos = 0
        cp = []
        for ci, tc in enumerate(_cells(tr)):
            tcp = tc.find(qn("w:tcPr"))
            gs = 1
            vm = None
            if tcp is not None:
                g = tcp.find(qn("w:gridSpan"))
                if g is not None:
                    try:
                        gs = int(g.get(qn("w:val")))
                    except (TypeError, ValueError):
                        gs = 1
                v = tcp.find(qn("w:vMerge"))
                if v is not None:
                    vm = v.get(qn("w:val")) or "continue"
            cp.append((pos, gs, vm))
            pos += gs
        colpos.append(cp)
    for ri, cp in enumerate(colpos):
        for ci, (pos, gs, vm) in enumerate(cp):
            if vm == "restart":
                n = 1
                for rj in range(ri + 1, len(colpos)):
                    nxt = [x for x in colpos[rj] if x[0] == pos]
                    if nxt and nxt[0][2] == "continue":
                        n += 1
                    else:
                        break
                spans[(ri, ci)] = n
    out = [f'<table class="wp-t" data-path="{".".join(map(str, path))}" style="width:{total:.0f}px;max-width:100%">']
    if widths:
        out.append("<colgroup>" + "".join(f'<col style="width:{w:.0f}px">' for w in widths) + "</colgroup>")
    for ri, tr in enumerate(rows):
        out.append(f'<tr data-row="{ri}">')
        for ci, tc in enumerate(_cells(tr)):
            pos, gs, vm = colpos[ri][ci]
            if vm == "continue":
                continue
            tcp = tc.find(qn("w:tcPr"))
            css = []
            attrs = ""
            if gs > 1:
                attrs += f' colspan="{gs}"'
            if (ri, ci) in spans:
                attrs += f' rowspan="{spans[(ri, ci)]}"'
            if tcp is not None:
                shd = tcp.find(qn("w:shd"))
                if shd is not None and shd.get(qn("w:fill")) not in (None, "auto"):
                    css.append("background:#" + shd.get(qn("w:fill")))
                va = tcp.find(qn("w:vAlign"))
                if va is not None:
                    css.append("vertical-align:" + {"center": "middle", "bottom": "bottom"}.get(va.get(qn("w:val")), "top"))
            bd = _cell_borders(tb, tc)
            for side, key in (("top", "top"), ("bottom", "bottom"), ("left", "left"), ("right", "right")):
                v = bd[key] if key in bd else bd.get({"top": "insideH", "bottom": "insideH", "left": "insideV", "right": "insideV"}[key])
                if v:
                    css.append(f"border-{side}:{v}")
            inner = []
            for bi, blk in enumerate(_blocks(tc)):
                bp = path + [ri, ci, bi]
                if blk.tag == qn("w:tbl"):
                    inner.append(_table_html(ctx, blk, bp, part))
                else:
                    inner.append(_para_html(ctx, blk, bp, True, part))
            out.append(f'<td{attrs} style="{html.escape(";".join(css), quote=True)}">{"".join(inner)}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _header_footer_html(ctx, container):
    """Text/pictures of a header or footer, shown read-only (they repeat on every page of the downloaded file)."""
    parts = []
    try:
        for blk in _blocks(container._element):
            if blk.tag == qn("w:p"):
                txt = para_text(blk).strip()
                pics = [r for r in blk.iter(qn("w:r")) if r.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip") is not None]
                if txt or pics:
                    html_p = _para_html(ctx, blk, [0], True, container.part)
                    html_p = html_p.replace('contenteditable="true"', 'contenteditable="false"').replace('class="wp-p', 'class="wp-p ro')
                    parts.append(html_p)
    except Exception:
        return ""
    return "".join(parts)


def render_page(doc):
    """(html, page_width_px) for the editor: header, body, footer."""
    ctx = _Ctx(doc)
    sec = doc.sections[0]
    try:
        width = (sec.page_width - sec.left_margin - sec.right_margin) / 914400.0 * 96
    except Exception:
        width = 700
    width = max(480, min(width, 900))
    body = doc.element.body
    parts = []
    head = _header_footer_html(ctx, sec.header) if not sec.header.is_linked_to_previous else ""
    if head:
        parts.append(f'<div class="wp-hf">{head}</div>')
    for bi, blk in enumerate(_blocks(body)):
        if blk.tag == qn("w:tbl"):
            parts.append(_table_html(ctx, blk, [bi]))
        else:
            parts.append(_para_html(ctx, blk, [bi]))
    foot = _header_footer_html(ctx, sec.footer) if not sec.footer.is_linked_to_previous else ""
    if foot:
        parts.append(f'<div class="wp-hf wp-foot">{foot}</div>')
    return "".join(parts), width


# ------------------------------------------------------------------ editing
def _segments_to_runs(p, segments):
    """Rewrites an edited paragraph from formatted pieces [{t, b, i, u, c, s}] - used when the typed text no longer lines up with the
    original runs (bold applied, text pasted over several runs ...)."""
    from docx.oxml import OxmlElement
    runs = para_runs(p)
    template = None
    for r in runs:
        if r.find(".//" + qn("w:drawing")) is None:
            template = r
            break
    keep_pics = [r for r in runs if r.find(".//" + qn("w:drawing")) is not None]
    for r in runs:
        if r not in keep_pics:
            parent = r.getparent()
            parent.remove(r)
    for seg in segments:
        text = seg.get("t", "")
        if text == "":
            continue
        r = OxmlElement("w:r")
        if template is not None and template.find(qn("w:rPr")) is not None:
            rpr = copy.deepcopy(template.find(qn("w:rPr")))
        else:
            rpr = OxmlElement("w:rPr")
        for tag in ("w:b", "w:i", "w:u", "w:color", "w:sz", "w:szCs", "w:bCs", "w:iCs"):
            for x in rpr.findall(qn(tag)):
                rpr.remove(x)
        if seg.get("b"):
            rpr.append(OxmlElement("w:b"))
        if seg.get("i"):
            rpr.append(OxmlElement("w:i"))
        if seg.get("u"):
            u = OxmlElement("w:u")
            u.set(qn("w:val"), "single")
            rpr.append(u)
        if seg.get("c") and re.fullmatch(r"[0-9a-fA-F]{6}", seg["c"]):
            c = OxmlElement("w:color")
            c.set(qn("w:val"), seg["c"].upper())
            rpr.append(c)
        if seg.get("s"):
            try:
                sz = OxmlElement("w:sz")
                sz.set(qn("w:val"), str(int(round(float(seg["s"]) * 2))))
                rpr.append(sz)
            except (TypeError, ValueError):
                pass
        # schema order: keep rFonts first, then the rest as written (Word is tolerant of rPr order in practice)
        r.append(rpr)
        set_run_text(r, text)
        # place before the first picture run (or at the end)
        p.append(r)


def apply_changes(doc, changes):
    """changes: [{"path": "3.1.0.0", "runs": {"0": "text", ...}}  or  {"path": ..., "segments": [...]}]. Returns how many paragraphs changed."""
    n = 0
    for ch in changes or []:
        path = parse_path(ch.get("path"))
        p = resolve(doc, path) if path else None
        if p is None or p.tag != qn("w:p"):
            continue
        if "runs" in ch:
            runs = para_runs(p)
            for k, text in (ch["runs"] or {}).items():
                try:
                    r = runs[int(k)]
                except (ValueError, IndexError):
                    continue
                if _run_text(r) != text:
                    set_run_text(r, text)
                    n += 1
        elif "segments" in ch:
            _segments_to_runs(p, ch["segments"] or [])
            n += 1
    return n


def _clear_texts(el):
    """A copy of a paragraph / row with its text emptied (formatting kept) - used for 'add row' and 'add paragraph'."""
    for p in el.iter(qn("w:p")):
        runs = para_runs(p)
        first = True
        for r in runs:
            if r.find(".//" + qn("w:drawing")) is not None:
                r.getparent().remove(r)
                continue
            if first:
                set_run_text(r, "")
                first = False
            else:
                r.getparent().remove(r)


def structural(doc, op, path_text):
    """add/delete a row or a paragraph. Returns (ok, message)."""
    path = parse_path(path_text)
    el = resolve(doc, path) if path else None
    if el is None:
        return False, "That place is no longer in the paper - reload the page."
    if op in ("row_add", "row_delete"):
        if len(path) < 4:
            return False, "Click inside a table cell first."
        tbl = resolve(doc, path[:-3])
        tr = _rows(tbl)[path[-3]]
        if op == "row_add":
            new = copy.deepcopy(tr)
            _clear_texts(new)
            tr.addnext(new)
            return True, "Row added."
        if len(_rows(tbl)) <= 1:
            return False, "A table needs at least one row."
        tr.getparent().remove(tr)
        return True, "Row deleted."
    if op == "para_add":
        if el.tag != qn("w:p"):
            return False, "Click in a paragraph first."
        new = copy.deepcopy(el)
        _clear_texts(new)
        el.addnext(new)
        return True, "Paragraph added."
    if op == "para_delete":
        if el.tag != qn("w:p"):
            return False, "Click in a paragraph first."
        parent = el.getparent()
        if len([c for c in parent if c.tag == qn("w:p")]) <= 1 and parent.tag == qn("w:tc"):
            _clear_texts(el)
            return True, "Paragraph cleared."
        parent.remove(el)
        return True, "Paragraph deleted."
    return False, "Unknown action."


def save_atomic(doc, filename):
    d = os.path.dirname(filename)
    fd, tmp = tempfile.mkstemp(suffix=".docx", dir=d)
    os.close(fd)
    try:
        doc.save(tmp)
        os.replace(tmp, filename)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ------------------------------------------------------------------ generate: fill in what the engagement already knows
def _blank(tc):
    return not "".join(para_text(p) for p in tc.iter(qn("w:p"))).strip()


def _set_cell_text(tc, text, like_tc=None):
    """Types `text` into an empty cell (its first paragraph), in the formatting of the label cell next to it, but not bold."""
    from docx.oxml import OxmlElement
    ps = [c for c in tc if c.tag == qn("w:p")]
    if not ps:
        return
    p = ps[0]
    runs = para_runs(p)
    if runs:
        set_run_text(runs[0], text)
        return
    r = OxmlElement("w:r")
    src = None
    if like_tc is not None:
        for rr in like_tc.iter(qn("w:r")):
            if rr.find(qn("w:rPr")) is not None:
                src = rr.find(qn("w:rPr"))
                break
    if src is not None:
        rpr = copy.deepcopy(src)
        for x in rpr.findall(qn("w:b")) + rpr.findall(qn("w:bCs")):
            rpr.remove(x)
        r.append(rpr)
    set_run_text(r, text)
    p.append(r)


def _replace_in_para(p, mapping):
    """Replaces [Placeholders] in a paragraph even when Word has split them over runs; the replacement takes the formatting of the run
    the placeholder starts in."""
    runs = para_runs(p)
    texts = [_run_text(r) for r in runs]
    full = "".join(texts)
    changed = False
    for ph, value in mapping.items():
        start = 0
        while True:
            i = full.lower().find(ph.lower(), start)
            if i < 0:
                break
            j = i + len(ph)
            pos = 0
            first = True
            for k, t in enumerate(texts):
                a, b = pos, pos + len(t)
                pos = b
                if b <= i or a >= j:
                    continue
                lo, hi = max(i, a) - a, min(j, b) - a
                texts[k] = t[:lo] + (value if first else "") + t[hi:]
                first = False
            full = "".join(texts)
            start = i + len(value)
            changed = True
    if changed:
        for r, t in zip(runs, texts):
            set_run_text(r, t)
    return changed


def _num(v):
    if v is None:
        return ""
    try:
        return f"{round(float(v)):,}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v):
    return "" if v is None else f"{v:.1f}%"


def prefill(doc, facts):
    """facts: {"client", "period_end", "today", "user", "address", "tin", "industry", "file_ref", "fin": {...}}. Fills the placeholder
    text and the empty value cell next to the 'Client name:' style labels; leaves signature blocks and everything else for people."""
    fin = facts.get("fin") or {}
    mapping = {"[Client Name]": facts.get("client", ""), "[Period End]": facts.get("period_end", "")}
    mapping = {k: v for k, v in mapping.items() if v}
    singles = {
        "file reference": facts.get("file_ref"), "investigation reference": facts.get("file_ref"),
        "client name": facts.get("client"), "client / engaging party": facts.get("client"), "client / engaging party:": facts.get("client"),
        "financial year end": facts.get("period_end"), "date": facts.get("today"), "date of letter": facts.get("today"),
        "prepared by": facts.get("user"),
        "registered address": facts.get("address"), "tax registration / tin number": facts.get("tin"), "addressed to": facts.get("client"),
        "industry / sector": facts.get("industry"), "period / date": facts.get("period_end"),
    }
    bench = {"revenue": fin.get("revenue"), "total assets": fin.get("total_assets"), "profit before tax": fin.get("pbt")}
    ratios_cur = {"revenue": _num(fin.get("revenue")), "gross profit margin": _pct(fin.get("gross_margin")), "net profit margin": _pct(fin.get("net_margin")),
                  "current ratio": ("" if fin.get("current_ratio") is None else f"{fin['current_ratio']:.2f}")}
    no_prior = not fin.get("revenue_prior")
    ratios_pri = {"revenue": "" if no_prior else _num(fin.get("revenue_prior")), "gross profit margin": "" if no_prior else _pct(fin.get("gross_margin_prior")), "net profit margin": "" if no_prior else _pct(fin.get("net_margin_prior")),
                  "current ratio": ("" if fin.get("current_ratio_prior") is None or no_prior else f"{fin['current_ratio_prior']:.2f}")}
    filled = 0
    body = doc.element.body
    for p in body.iter(qn("w:p")):
        if _replace_in_para(p, mapping):
            filled += 1
    for tbl in body.iter(qn("w:tbl")):
        rows = _rows(tbl)
        if not rows:
            continue
        header = ["".join(para_text(p) for p in c.iter(qn("w:p"))).strip().lower() for c in _cells(rows[0])]
        is_sign = "signature" in header
        has_prior_cur = any("prior year" in h for h in header) and any("current year" in h for h in header)
        has_amount = any(h.startswith("amount") for h in header)
        for ri, tr in enumerate(rows):
            tcs = _cells(tr)
            if len(tcs) < 2:
                continue
            label = "".join(para_text(p) for p in tcs[0].iter(qn("w:p"))).strip()
            low = label.lower().rstrip(":").strip()
            nxt = tcs[1]
            if has_prior_cur and ri > 0 and low in ratios_cur:
                cur_i = next(i for i, h in enumerate(header) if "current year" in h)
                pri_i = next(i for i, h in enumerate(header) if "prior year" in h)
                if cur_i < len(tcs) and _blank(tcs[cur_i]) and ratios_cur[low]:
                    _set_cell_text(tcs[cur_i], ratios_cur[low], tcs[0]); filled += 1
                if pri_i < len(tcs) and _blank(tcs[pri_i]) and ratios_pri[low]:
                    _set_cell_text(tcs[pri_i], ratios_pri[low], tcs[0]); filled += 1
                continue
            if has_amount and ri > 0 and low in bench and bench[low] is not None and _blank(nxt):
                _set_cell_text(nxt, _num(bench[low]), tcs[0]); filled += 1
                continue
            if is_sign:
                if low == "prepared by" and facts.get("user") and len(tcs) >= 3 and _blank(tcs[2]) and facts.get("today"):
                    _set_cell_text(tcs[2], facts["today"], tcs[0]); filled += 1
                continue
            value = singles.get(low) if low in singles else singles.get(label.lower())
            if value and _blank(nxt) and len(tcs) == 2:
                _set_cell_text(nxt, value, tcs[0]); filled += 1
    return filled


def format_date(d):
    return d.strftime("%d %B %Y").lstrip("0") if d else ""
