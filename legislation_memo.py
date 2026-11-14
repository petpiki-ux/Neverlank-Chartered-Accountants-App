"""A Word memo of an "Ask the library" answer or a drafted advice note, in the firm's letterhead style.

`memo_from_ask(question, payload)` and `memo_from_advice(brief, advice)` turn what legislation_search returns into a plain dictionary (text
and lists only, so it can be posted back from the page as JSON); `build_docx(memo)` prints that dictionary. Nothing here searches, calls the AI
or changes any record - it only lays out what a person has already seen on screen, with the sources numbered and the checks kept.
"""
import io
import re
from datetime import date

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

import brand_docs as brand
import legislation_understand

CAVEAT = ("DRAFT FOR PARTNER REVIEW. Prepared only from the instruments, cases and articles filed in the firm's Legislative Update Control "
          "library, and the facts as described. It is not a legal opinion. Check every figure and section against the instrument itself before "
          "it goes to a client.")


def _numbered(text, order):
    """[#id] markers in `text` become [n] by the order the sources are listed in the memo; markers for unlisted ids are dropped."""
    pos = {i: n + 1 for n, i in enumerate(order)}

    def one(m):
        ns = [pos[int(x)] for x in re.findall(r"\d+", m.group(0)) if int(x) in pos]
        return "[" + ", ".join(str(n) for n in ns) + "]" if ns else ""
    return re.sub(r"\s*\[#?\s*\d+(?:\s*[,;]\s*#?\s*\d+)*\s*\]", lambda m: (" " if m.group(0)[:1].isspace() else "") + one(m), text or "").strip()


def _source_rows(updates, flags=None):
    rows = []
    for n, u in enumerate(updates, 1):
        ref = " · ".join(x for x in [u.source_reference, ("effective " + u.effective_date.strftime("%d %b %Y")) if u.effective_date else ""] if x)
        rows.append({"n": n, "title": u.title, "ref": ref, "label": legislation_understand.weight_label(u),
                     "flags": list((flags or {}).get(u.id, []))})
    return rows


def memo_from_ask(question, payload):
    """The memo for one answer. `payload` is what legislation_search.ask returned with status "done"."""
    st = payload.get("structured") or {}
    cites = payload.get("citations") or []
    order = [u.id for u in cites]
    flags = legislation_understand.supersession_flags(cites)
    blocks = []
    for part in st.get("explanation") or []:
        blocks.append({"h": part.get("heading") or "", "paras": [_numbered(part["text"], order)]})
    if not blocks and payload.get("answer") and not st.get("bottom_line"):
        blocks.append({"h": "", "paras": [_numbered(payload["answer"], order)]})
    figs = st.get("key_figures") or []
    by_id = {u.id: u for u in cites}
    if figs:
        blocks.append({"h": "Key figures", "table": {"head": ["Item", "Figure", "Applies from", "Source"],
                       "rows": [[f["label"], f["value"], f.get("applies_from") or "", ("[%d]" % (order.index(f["source_id"]) + 1) if f["source_id"] in order else "") + ("" if f.get("checked", True) else " check")] for f in figs]}})
    if st.get("quotes"):
        blocks.append({"h": "From the sources", "quotes": [{"text": q["text"], "src": ("[%d] %s" % (order.index(q["source_id"]) + 1, by_id[q["source_id"]].title)) if q["source_id"] in order and q["source_id"] in by_id else ""} for q in st["quotes"]]})
    if st.get("watch_points"):
        blocks.append({"h": "Check before relying on this", "bullets": [_numbered(w, order) for w in st["watch_points"]]})
    conf = st.get("confidence") or {}
    return {"title": "Library answer", "sub": date.today().strftime("%d %B %Y"),
            "meta": [("Confidence", conf.get("level") or "—"), ("Sources", str(len(cites))), ("Status", "Draft")],
            "question": question, "headline": _numbered(st.get("bottom_line") or "", order), "confidence_reason": conf.get("reason") or "",
            "blocks": blocks, "sources": _source_rows(cites, flags), "caveat": CAVEAT}


def memo_from_advice(brief, advice):
    """The memo for drafted advice. `advice` is what legislation_search.advise returned with status "done"."""
    updates = [s["doc"] for s in advice.get("sources") or []]
    order = [u.id for u in updates]
    flags = {s["doc"].id: s["flags"] for s in advice.get("sources") or []}
    blocks = []
    if advice.get("summary"):
        blocks.append({"h": "Summary", "paras": [advice["summary"]]})
    if advice.get("recommended_actions"):
        blocks.append({"h": "What to do next", "numbered": [_with_suffix(a["action"] + (" - " + a["why"] if a.get("why") else ""), a.get("citation_ids"), order) for a in advice["recommended_actions"]]})
    for sec in advice.get("sections") or []:
        block = {"h": sec["topic"] + (f"  ({sec['coverage']})" if sec.get("coverage") else ""), "paras": [_with_suffix(sec["finding"], sec.get("citation_ids"), order)]}
        if sec.get("key_figures"):
            block["table"] = {"head": ["Item", "Figure", "Source"], "rows": [[f["label"], f["value"], ("[%d]" % (order.index(f["source_id"]) + 1) if f["source_id"] in order else "") + ("" if f.get("checked", True) else " check")] for f in sec["key_figures"]]}
        if sec.get("planning_points"):
            block["bullets"] = [_numbered(p, order) for p in sec["planning_points"]]
        blocks.append(block)
    if advice.get("not_covered"):
        blocks.append({"h": "Not covered by the filed library - check before advising", "paras": [advice["not_covered"]]})
    if advice.get("facts_to_confirm"):
        blocks.append({"h": "Facts to confirm with the client", "bullets": list(advice["facts_to_confirm"])})
    if advice.get("watch_points"):
        blocks.append({"h": "Check before relying on this", "bullets": [_numbered(w, order) for w in advice["watch_points"]]})
    return {"title": "Draft advice", "sub": date.today().strftime("%d %B %Y"),
            "meta": [("Topics", str(len(advice.get("sections") or []))), ("Sources", str(len(updates))), ("Status", "Draft")],
            "question": brief, "headline": advice.get("headline") or "", "confidence_reason": "", "blocks": blocks,
            "sources": _source_rows(updates, flags), "caveat": CAVEAT}


def _with_suffix(text, ids, order):
    """The text with its [#id] markers numbered; the section's sources are added at the end only when the text carries no markers of its own."""
    return _numbered(text, order) + ("" if re.search(r"\[#?\s*\d+", text or "") else _suffix(ids, order))


def _suffix(ids, order):
    ns = [order.index(i) + 1 for i in (ids or []) if i in order]
    return " [" + ", ".join(str(n) for n in ns) + "]" if ns else ""


def _clip(x, n=4000):
    return str(x or "")[:n]


def clean_memo(raw):
    """A memo posted back from the page, reduced to plain strings and lists so nothing unexpected reaches the document."""
    if not isinstance(raw, dict):
        raise ValueError("not a memo")
    lst = lambda v: [x for x in (v if isinstance(v, list) else [])][:60]
    out = {"title": _clip(raw.get("title"), 80) or "Library answer", "sub": _clip(raw.get("sub"), 60),
           "meta": [(_clip(m[0], 40), _clip(m[1], 160)) for m in lst(raw.get("meta")) if isinstance(m, (list, tuple)) and len(m) == 2][:5],
           "question": _clip(raw.get("question"), 6000), "headline": _clip(raw.get("headline")), "confidence_reason": _clip(raw.get("confidence_reason"), 600),
           "caveat": CAVEAT, "blocks": [], "sources": []}
    for b in lst(raw.get("blocks")):
        if not isinstance(b, dict):
            continue
        nb = {"h": _clip(b.get("h"), 200)}
        for k in ("paras", "bullets", "numbered"):
            if b.get(k):
                nb[k] = [_clip(x) for x in lst(b[k])]
        if isinstance(b.get("table"), dict):
            nb["table"] = {"head": [_clip(x, 60) for x in lst(b["table"].get("head"))][:6],
                           "rows": [[_clip(c, 300) for c in r][:6] for r in lst(b["table"].get("rows")) if isinstance(r, (list, tuple))]}
        if b.get("quotes"):
            nb["quotes"] = [{"text": _clip(q.get("text"), 600), "src": _clip(q.get("src"), 300)} for q in lst(b["quotes"]) if isinstance(q, dict)]
        out["blocks"].append(nb)
    for s in lst(raw.get("sources")):
        if isinstance(s, dict):
            out["sources"].append({"n": s.get("n") if isinstance(s.get("n"), int) else len(out["sources"]) + 1, "title": _clip(s.get("title"), 300),
                                   "ref": _clip(s.get("ref"), 300), "label": _clip(s.get("label"), 60), "flags": [_clip(x, 300) for x in lst(s.get("flags"))]})
    return out


def build_docx(memo):
    """The memo as a Word document (an io.BytesIO)."""
    memo = clean_memo(memo)
    doc = Document()
    brand.setup_document(doc)
    brand.branded_footer(doc)
    brand.letterhead_band(doc, memo["title"], sub=memo["sub"])
    brand.meta_strip(doc, memo["meta"])
    doc.add_paragraph()
    if memo["question"]:
        p = doc.add_paragraph()
        brand._run(p, "QUESTION" if memo["title"] == "Library answer" else "SITUATION AND TOPICS", size=7.5, bold=True, colour=brand.GOLD)
        p = doc.add_paragraph()
        brand._run(p, memo["question"], size=10, italic=True, colour=brand.MUTED)
    if memo["headline"]:
        brand.note_box(doc, memo["headline"] + ("\n" + memo["confidence_reason"] if False else ""), title="The short answer")
        if memo["confidence_reason"]:
            p = doc.add_paragraph()
            brand._run(p, "Confidence: " + memo["confidence_reason"], size=9, italic=True, colour=brand.MUTED)
    for b in memo["blocks"]:
        if b.get("h"):
            brand.heading(doc, b["h"], level=2)
        for t in b.get("paras") or []:
            for chunk in [c for c in re.split(r"\n{2,}", t) if c.strip()]:
                p = doc.add_paragraph()
                brand._run(p, chunk.strip(), size=10.5)
                p.paragraph_format.space_after = Pt(5)
        if b.get("table") and b["table"]["rows"]:
            tb = doc.add_table(rows=len(b["table"]["rows"]) + 1, cols=len(b["table"]["head"]))
            for ci, h in enumerate(b["table"]["head"]):
                tb.rows[0].cells[ci].paragraphs[0].add_run(h)
            for ri, row in enumerate(b["table"]["rows"], 1):
                for ci in range(len(b["table"]["head"])):
                    tb.rows[ri].cells[ci].paragraphs[0].add_run(row[ci] if ci < len(row) else "")
            brand.style_table(tb)
            doc.add_paragraph()
        for q in b.get("quotes") or []:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            brand.para_border(p, "left", brand.GOLD, size=18, space=8)
            brand._run(p, "“" + q["text"] + "”", size=10, italic=True)
            if q.get("src"):
                brand._run(p, "\n" + q["src"], size=8.5, colour=brand.MUTED)
        for t in b.get("bullets") or []:
            p = doc.add_paragraph(style="List Bullet")
            brand._run(p, t, size=10.5)
        for t in b.get("numbered") or []:
            p = doc.add_paragraph(style="List Number")
            brand._run(p, t, size=10.5)
    if memo["sources"]:
        brand.heading(doc, "Sources", level=2)
        for s in memo["sources"]:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            brand._run(p, f"[{s['n']}]  ", size=9.5, bold=True, colour=brand.NAVY)
            brand._run(p, s["title"], size=9.5, bold=True)
            tail = " · ".join(x for x in [s["label"], s["ref"]] if x)
            if tail:
                brand._run(p, "  " + tail, size=9, colour=brand.MUTED)
            for f in s["flags"]:
                q = doc.add_paragraph()
                q.paragraph_format.left_indent = Pt(24)
                q.paragraph_format.space_after = Pt(1)
                brand._run(q, "Check: " + f, size=8.5, italic=True, colour="9A6B00")
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    brand._run(p, memo["caveat"], size=8, italic=True, colour=brand.MUTED)
    brand.fix_document(doc)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
