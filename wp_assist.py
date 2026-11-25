"""Claude beside the working paper: a side panel in the Word and Excel editors, like the Claude add-in in Word.

What it does, in plain words
* The person opens the panel and asks about the paper they are working in ("summarise this", "what is missing?", "check the figures",
  "tighten this paragraph"). Claude is given the whole paper (every paragraph with its position), the engagement's basic record and the best
  matching passages of the Firm Library, and answers in the panel.
* Claude never changes the paper by itself. When it suggests wording it comes back as a "suggested change" card showing before and after, and a
  person presses Apply (replace a paragraph, or add one below). A change made that way is saved like any other typing, so the usual rules apply:
  a paper signed by the partner stays read-only, and any change clears the reviewer's tick.
* Privacy: before anything leaves the app the client's name, TIN and VAT number are swapped for tokens, and every e-mail address and phone number
  for numbered tokens; they are put back in the reply. Nothing is stored by the assistant itself.
* Safeguards: every figure of 3+ digits in a suggested change that is not in the paper, the engagement record or what the person typed is flagged
  on the card. Sign-off cells (prepared by, reviewed by, signature ...) cannot be changed by a suggestion. The AI never calculates tax.
* Word papers get suggested changes; Excel papers get questions and answers only (changes to a sheet are typed in the grid as before).

Settings (Render > Environment): ANTHROPIC_API_KEY (already used by the other AI features); optional WP_ASSIST_MODEL for a stronger model
(falls back to ANTHROPIC_MODEL, then claude-sonnet-4-5).
"""
import os
import re
import time
from datetime import date, datetime

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from flask import current_app, jsonify, request
from flask_login import current_user, login_required

import wp_docx
import wp_xlsx
import zimra_ai
from extensions import db
from wp_editor import _check_writable, _doc_or_404, _ext, _fin_facts, _path, wpedit_bp

MODEL = os.environ.get("WP_ASSIST_MODEL") or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
FALLBACK_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
TIMEOUT = 100                 # seconds the AI may take over one answer
MAX_TOKENS = 6000
MAX_PAPER_CHARS = 60000       # how much of the paper is sent (the rest is cut, and Claude is told)
MAX_CELL_CHARS = 1500
MAX_MESSAGE = 4000
MAX_SELECTION = 6000
MAX_HISTORY = 10
MAX_EDITS = 10
MAX_PICTURES = 4
MAX_PICTURE_CHARS = 5_000_000   # base64 characters per picture (about 3.7 MB of image)
_PIC_TYPES = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff", "image/gif": b"GIF8", "image/webp": b"RIFF"}
MAX_EDIT_CHARS = 8000
RATE = (30, 600)              # at most 30 questions per person per 10 minutes
_calls = {}
_fin_cache = {}

# a paragraph that carries one of these words is a sign-off: a suggestion may not replace it
_SIGNOFF = re.compile(r"\b(signature|signed|sign-off|sign off|prepared by|reviewed by|approved by|partner review|date reviewed|initials)\b", re.I)

TOOL = {
    "name": "reply_in_paper_panel",
    "description": "Your answer to the person working in the paper, with any suggested changes to the paper itself.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Your answer, in short plain paragraphs. Use '- ' for bullets if a list helps. No headings."},
            "edits": {
                "type": "array",
                "description": "Suggested changes to the paper (leave empty if none were asked for or needed). A person decides whether to apply each one.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Word: the position in square brackets before the paragraph, for example 3.1.2.0. Excel: the sheet and cell, for example CGT Computation!B16. Use only positions shown."},
                        "kind": {"type": "string", "enum": ["replace", "insert_after"], "description": "replace = new wording for that whole paragraph (or the new value of that Excel cell, which may be a simple formula starting with =); insert_after = a new paragraph below it (Word only)."},
                        "text": {"type": "string", "description": "The complete new paragraph text, plain text, in the voice of the paper."},
                        "why": {"type": "string", "description": "One short sentence: why this change."},
                    },
                    "required": ["path", "kind", "text"],
                },
            },
        },
        "required": ["message"],
    },
}

SYSTEM = (
    "You are Claude, working beside a staff member of Neverlank Chartered Accountants (Harare, Zimbabwe) inside one of the firm's working papers "
    "(audit, tax, accounting or advisory). You can see the whole paper, the engagement's basic record and, where they match the question, passages "
    "from the firm's own Library of legislation and guidance. Be the careful colleague who reads the paper properly before answering.\n\n"
    "RULES\n"
    "1. The paper's text is given with a position in square brackets before each paragraph, for example [3.1.2.0]. When you suggest a change, "
    "use only those positions. You never change the paper yourself: you suggest, and a person presses Apply.\n"
    "2. Never invent figures, dates, names, reference numbers, case names, sections of an Act or amounts. Use what is in the paper, the engagement "
    "record, the Library excerpts and what the person wrote. You may use your general knowledge of ISAs, IFRS, Zimbabwean tax and company law to "
    "explain and to review, but anything the paper and Library do not show must be marked \"(to be verified)\".\n"
    "3. Do not compute tax, penalties or interest. You may check simple arithmetic in the paper (totals, ratios, agreement of one figure to "
    "another): show the sum you did and say plainly if two figures do not agree.\n"
    "4. A suggested change is one whole paragraph (Word) or one cell (Excel): kind replace gives the complete new text of that paragraph or cell, "
    "including filling in one that is shown as (empty - can be filled in); kind insert_after adds a new paragraph below one (Word only). At most ten "
    "suggestions in a reply. Plain text, no markdown, in the voice and tense of the paper. Never suggest changes to sign-off, preparer, reviewer, "
    "signature or date-signed cells.\n"
    "   WHEN TO SUGGEST: whenever the person asks you to write, draft, fill in, complete, fix, correct, rewrite, improve, tighten, shorten, "
    "proofread or update something, you MUST return the change as a suggestion in the edits list - never only describe it or paste it into your "
    "message. When you review or check a paper and find a clear mistake you can correct from what the paper, the record and the Library show, "
    "also give the correction as a suggestion. For a pure question, just answer. If you cannot suggest the change (for example the figure is not "
    "in the paper), say so in the message and say what you need.\n"
    "5. When asked to review or check, say what is wrong or missing and where (quote the position), in order of importance, and do not pad with praise. "
    "If the paper is fine, say so briefly.\n"
    "6. If the person selected text, 'this', 'it' and 'the selection' mean that selection.\n"
    "7. The client's identifiers are shown as tokens such as {client_name}, {client_tin}, {email_1}. Keep the tokens exactly as they are.\n"
    "8. Write in clear British English. Be as brief as the question allows. Ask one short question only if you truly cannot proceed.\n"
    "9. The person may attach pictures (screenshots, scans, photos of a document). Read them carefully and use what they show, but treat a figure you "
    "cannot read clearly as unreadable and say so rather than guessing. A picture is not masked, so do not repeat the client's name or identifiers from "
    "it: write {client_name} instead. Pictures are only attached to the message they come with.\n"
    "   The paper's own pictures (logos, scanned documents) are also shown to you, labelled PICTURE n in the paper and marked [PICTURE n: ...] in "
    "the text. A paragraph or cell marked [PICTURE ...] holds a picture: it is not empty. Look at them before you say what the paper contains, and "
    "count them correctly. The page header and footer are listed first; they cannot be changed by a suggestion.\n"
    "10. Return everything through the reply_in_paper_panel tool."
)


# ------------------------------------------------------------------ setup

def is_configured():
    return zimra_ai.is_configured()


def _rate_ok(user_id):
    now = time.time()
    hits = [t for t in _calls.get(user_id, []) if now - t < RATE[1]]
    if len(hits) >= RATE[0]:
        _calls[user_id] = hits
        return False
    hits.append(now)
    _calls[user_id] = hits
    return True


# ------------------------------------------------------------------ privacy

class Mask:
    """Swap the client's identifiers (and any e-mail address or phone number) for tokens on the way out, and back on the way in."""

    def __init__(self, client):
        self.client = client
        self.map = {}

    def _token(self, kind, value):
        for k, v in self.map.items():
            if v == value:
                return k
        n = sum(1 for k in self.map if k.startswith("{" + kind + "_")) + 1
        key = "{%s_%d}" % (kind, n)
        self.map[key] = value
        return key

    def out(self, text):
        t = text or ""
        t = zimra_ai._EMAIL_RE.sub(lambda m: self._token("email", m.group(0)), t)
        t = zimra_ai._PHONE_RE.sub(lambda m: self._token("phone", m.group(0)), t)
        if self.client is not None:
            t = zimra_ai.redact(t, self.client)
        return t

    def back(self, text):
        t = text or ""
        if self.client is not None:
            t = zimra_ai.restore_tokens(t, self.client)
        for key, value in self.map.items():
            t = t.replace(key, value)
        return t


# ------------------------------------------------------------------ the paper as text

_A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_V_IMG = "{urn:schemas-microsoft-com:vml}imagedata"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_DOCPR = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"
_EXTENT = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"
MAX_PAPER_PICTURES = 4          # pictures inside the paper that are shown to Claude (a logo that repeats counts once)
MAX_PICTURE_EDGE = 1000


def _para_pictures(p, part, where):
    """The pictures in one paragraph: [{rid, blob, mime, name, descr, w, h, where}] (w, h in pixels as laid out on the page)."""
    out = []
    for el in p.iter(_A_BLIP, _V_IMG):
        rid = el.get(_R_EMBED) or el.get(_R_ID)
        if not rid or any(a.tag == _MC_FALLBACK for a in el.iterancestors()):      # Word stores some pictures twice (a drawing and a fallback copy)
            continue
        try:
            ip = part.related_parts[rid]
            blob, mime = ip.blob, getattr(ip, "content_type", "image/png")
        except Exception:
            continue
        name = descr = ""
        w = h = 0
        anc = el.getparent()
        while anc is not None and anc is not p and anc.tag not in (qn("w:drawing"), qn("w:pict")):
            anc = anc.getparent()
        if anc is not None and anc is not p:
            for pr in anc.iter(_DOCPR):
                name, descr = pr.get("name") or "", pr.get("descr") or ""
                break
            for ex in anc.iter(_EXTENT):
                try:
                    w, h = round(int(ex.get("cx")) / 9525), round(int(ex.get("cy")) / 9525)
                except (TypeError, ValueError):
                    pass
                break
        out.append({"blob": blob, "mime": mime, "name": name, "descr": descr, "w": w, "h": h, "where": where})
    return out


def _picture_label(pic):
    bits = [x for x in (pic["name"], pic["descr"]) if x]
    size = f"{pic['w']}x{pic['h']} px" if pic["w"] else ""
    return "picture" + (f" ({', '.join(bits)}" + (f"; {size}" if size else "") + ")" if bits else (f" ({size})" if size else ""))


def docx_outline(d, empty=False, pics=None):
    """[(path, text, where)] for every paragraph that has text, in reading order; table cells say which table, row and cell.
    With empty=True the empty paragraphs inside table cells are listed too (text ""), so a blank field can be filled in.
    With pics=[] a paragraph that holds a picture is listed as well (its text ends "[PICTURE n: ...]") and the pictures are collected into `pics`."""
    out = []

    def walk(el, prefix, where):
        for idx, blk in enumerate(wp_docx._blocks(el)):
            if blk.tag == qn("w:p"):
                text = wp_docx.para_text(blk).strip()
                found = _para_pictures(blk, d.part, ".".join(str(x) for x in prefix + [idx])) if pics is not None else []
                for f in found:
                    pics.append(f)
                    text = (text + " " if text else "") + f"[PICTURE {len(pics)}: {_picture_label(f)}]"
                if text or (empty and where):
                    out.append((".".join(str(x) for x in prefix + [idx]), text, where))
            else:
                for ri, tr in enumerate(wp_docx._rows(blk)):
                    cells = wp_docx._cells(tr)
                    label = ""
                    if cells:
                        label = " ".join(wp_docx.para_text(p).strip() for p in cells[0].iter(qn("w:p"))).strip()[:40]
                    for ci, tc in enumerate(cells):
                        w = f"table {idx}, row {ri}, cell {ci}" + (f", row starts \"{label}\"" if label and ci else "")
                        walk(tc, prefix + [idx, ri, ci], w)

    walk(d.element.body, [], "")
    return out


def docx_header_footer(d, pics):
    """The page header and footer (they repeat on every page): lines of text, with any picture marked and collected into `pics`."""
    lines, seen = [], set()
    for si, sec in enumerate(d.sections):
        for label, c in (("HEADER", sec.header), ("FIRST-PAGE HEADER", sec.first_page_header), ("FOOTER", sec.footer), ("FIRST-PAGE FOOTER", sec.first_page_footer)):
            try:
                if c.is_linked_to_previous and si:
                    continue
                el = c._element
                if id(el) in seen:
                    continue
                seen.add(id(el))
                for p in el.iter(qn("w:p")):
                    text = wp_docx.para_text(p).strip()
                    for f in _para_pictures(p, c.part, label.lower()):
                        pics.append(f)
                        text = (text + " " if text else "") + f"[PICTURE {len(pics)}: {_picture_label(f)}]"
                    if text:
                        lines.append(f"({label.lower()}) {text[:MAX_CELL_CHARS]}")
            except Exception:
                continue
    return lines


def prepare_pictures(pics):
    """The paper's own pictures as [(label, media_type, base64)] for the AI: the same logo is sent once, tiny ones (bullets, rules) are skipped,
    big ones are shrunk, at most MAX_PAPER_PICTURES. Also returns a note of what was left out (or '')."""
    import base64, hashlib, io
    from PIL import Image
    out, seen, left = [], {}, 0
    for n, f in enumerate(pics, 1):
        h = hashlib.sha1(f["blob"]).hexdigest()
        if h in seen:
            seen[h]["also"].append(n)
            continue
        entry = {"n": n, "also": [], "pic": f}
        seen[h] = entry
        try:
            im = Image.open(io.BytesIO(f["blob"]))
            im.load()
        except Exception:
            left += 1
            entry["skip"] = True
            continue
        if min(im.size) < 24 or (f["w"] and f["h"] and min(f["w"], f["h"]) < 16):
            entry["skip"] = True
            continue
        if len(out) >= MAX_PAPER_PICTURES:
            left += 1
            entry["skip"] = True
            continue
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((MAX_PICTURE_EDGE, MAX_PICTURE_EDGE))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        mt = "image/png"
        if len(data) > 1_500_000:
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            data, mt = buf.getvalue(), "image/jpeg"
        entry["sent"] = (mt, base64.b64encode(data).decode())
        out.append(entry)
    shown = []
    for e in out:
        also = (" (the same picture also appears as PICTURE " + ", ".join(str(x) for x in e["also"]) + ")") if e["also"] else ""
        shown.append((f"PICTURE {e['n']} in the paper, at {e['pic']['where']}{also}:", e["sent"][0], e["sent"][1]))
    note = f"{left} picture(s) in the paper could not be shown to you (too many, or a format that cannot be read)." if left else ""
    return shown, note


def docx_paper_text(d, pics=None):
    lines, size, cut = [], 0, False
    if pics is not None:
        lines += docx_header_footer(d, pics)
        size = sum(len(x) + 1 for x in lines)
        if lines:
            lines.insert(0, "[page header and footer: they repeat on every page and cannot be changed by a suggestion]")
    for path, text, where in docx_outline(d, empty=True, pics=pics):
        line = f"[{path}] " + (f"({where}) " if where else "") + (text[:MAX_CELL_CHARS] if text else "(empty - can be filled in)")
        if size + len(line) > MAX_PAPER_CHARS:
            cut = True
            break
        lines.append(line)
        size += len(line) + 1
    body = "\n".join(lines) or "(The paper has no text yet.)"
    if cut:
        body += "\n(The rest of the paper is not shown: it is longer than the assistant can read in one go.)"
    return body


def xlsx_paper_text(path):
    import openpyxl
    try:
        wb_f = openpyxl.load_workbook(path)
        wb_v = openpyxl.load_workbook(path, data_only=True)
    except Exception:
        return "(The workbook could not be read.)"
    lines, size, cut = [], 0, False
    for ws in wb_f.worksheets:
        if ws.sheet_state != "visible":
            continue
        wv = wb_v[ws.title]
        head = f"=== Sheet: {ws.title} ==="
        lines.append(head)
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 300), max_col=min(ws.max_column or 1, 30)):
            for c in row:
                if c.value is None or str(c.value).strip() == "":
                    continue
                val = str(c.value)
                if val.startswith("="):
                    cached = wv[c.coordinate].value
                    line = f"{c.coordinate}: {val}" + (f"  (shows {cached})" if cached is not None else "")
                else:
                    line = f"{c.coordinate}: {val[:MAX_CELL_CHARS]}"
                if size + len(line) > MAX_PAPER_CHARS:
                    cut = True
                    break
                lines.append(line)
                size += len(line) + 1
            if cut:
                break
        if cut:
            break
    body = "\n".join(lines) or "(The workbook is empty.)"
    if cut:
        body += "\n(The rest of the workbook is not shown: it is longer than the assistant can read in one go.)"
    return body


def engagement_context(eng):
    """Plain-text record of the engagement for the assistant (identifiers are masked afterwards)."""
    client = eng.client
    lines = [f"Client: {client.name if client else ''}",
             f"Engagement: {eng.title} (type: {eng.type})",
             f"Period end: {wp_docx.format_date(eng.period_end) if eng.period_end else 'not set'}",
             f"Reporting framework: {eng.reporting_framework or 'not set'}",
             f"Industry: {(client.industry or '') if client else ''}",
             f"Today: {wp_docx.format_date(date.today())}"]
    key = eng.id
    hit = _fin_cache.get(key)
    if hit and time.time() - hit[0] < 300:
        fin = hit[1]
    else:
        fin = _fin_facts(eng) or {}
        _fin_cache[key] = (time.time(), fin)
    shown = [(k, v) for k, v in (("Revenue", fin.get("revenue")), ("Revenue, prior year", fin.get("revenue_prior")), ("Profit before tax", fin.get("pbt")),
                                 ("Total assets", fin.get("total_assets"))) if v is not None]
    if shown:
        lines.append("Figures from the trial balance as it stands (draft): " + "; ".join(f"{k} {v:,.0f}" for k, v in shown))
    for label, k in (("Gross margin", "gross_margin"), ("Net margin", "net_margin"), ("Current ratio", "current_ratio")):
        cur, pri = fin.get(k), fin.get(k + "_prior")
        if cur is not None:
            lines.append(f"{label}: {cur:.2f}" + (f" (prior year {pri:.2f})" if pri is not None else ""))
    return "\n".join(lines)


def library_block(query, mask):
    pairs = zimra_ai.library_excerpts(query)
    if not pairs:
        return ""
    try:
        from legislation_search import _candidate_block
        return mask.out("\n\n---\n\n".join(_candidate_block(u, c) for u, c in pairs))
    except Exception:
        return ""


# ------------------------------------------------------------------ the AI call (tests replace it)

def _is_model_missing(exc):
    t = str(exc).lower()
    return "model" in t and ("not_found" in t or "not found" in t or "invalid" in t or "does not exist" in t)


def _call_model(system, messages):
    """Returns ({message, edits}, None) or (None, error text)."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=TIMEOUT)
    response, last = None, None
    for model in dict.fromkeys([MODEL, FALLBACK_MODEL]):
        try:
            response = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system, tools=[TOOL],
                                              tool_choice={"type": "tool", "name": TOOL["name"]}, messages=messages)
            break
        except Exception as exc:
            last = exc
            if not _is_model_missing(exc):
                break
    if response is None:
        try:
            import legislation_search
            return None, legislation_search.friendly_error(last)
        except Exception:
            return None, str(last)[:400]
    if getattr(response, "stop_reason", None) == "max_tokens":
        return None, "That answer was too long to finish. Ask for one part at a time."
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL["name"]:
            return (block.input or {}), None
    text = "".join(getattr(b, "text", "") or "" for b in response.content if getattr(b, "type", None) == "text").strip()
    return ({"message": text, "edits": []}, None) if text else (None, "Claude sent no reply. Try again.")


# ------------------------------------------------------------------ checking what comes back

_NUM_TOKEN = zimra_ai._NUM_RE


def _unknown_figures(text, known):
    out, seen = [], set()
    for m in _NUM_TOKEN.finditer(text or ""):
        norm = zimra_ai._norm_num(m.group(1))
        if norm and norm not in known and norm not in seen:
            seen.add(norm)
            out.append(m.group(1))
    return out


def clean_edits(raw, d, known, mask):
    """Keep only suggestions that point at a real paragraph, are not sign-off cells, and say what is new in them."""
    out = []
    if not isinstance(raw, list):
        return out
    for e in raw:
        if len(out) >= MAX_EDITS:
            break
        if not isinstance(e, dict):
            continue
        kind = e.get("kind")
        path_text = str(e.get("path") or "").strip().strip("[]")
        path = wp_docx.parse_path(path_text) if path_text else None
        if kind not in ("replace", "insert_after") or not path:
            continue
        text = mask.back(str(e.get("text") or "")).strip()
        if not text or len(text) > MAX_EDIT_CHARS:
            continue
        p = wp_docx.resolve(d, path)
        if p is None or p.tag != qn("w:p"):
            continue
        before = wp_docx.para_text(p)
        if not before.strip():
            before = ""
        if _SIGNOFF.search(before) or _SIGNOFF.search(text):
            continue
        if kind == "replace" and p.find(".//" + qn("w:drawing")) is not None:
            continue
        item = {"path": ".".join(str(x) for x in path), "kind": kind, "text": text, "before": before,
                "why": mask.back(str(e.get("why") or ""))[:300], "warn": ""}
        unknown = _unknown_figures(text, known)
        if unknown:
            item["warn"] = "Check these figures: they are not in the paper, the engagement record or your message: " + ", ".join(unknown[:6]) + "."
        out.append(item)
    return out


def clean_cell_edits(raw, path, known, mask):
    """Excel: keep only suggestions that name a real sheet and a cell in the grid, are not sign-off cells, and say what is new in them."""
    import openpyxl
    from openpyxl.utils import column_index_from_string
    out = []
    if not isinstance(raw, list):
        return out
    try:
        wb = openpyxl.load_workbook(path)
    except Exception:
        return out
    sheets = {ws.title: ws for ws in wb.worksheets if ws.sheet_state == "visible"}
    for e in raw:
        if len(out) >= MAX_EDITS:
            break
        if not isinstance(e, dict) or e.get("kind", "replace") != "replace":
            continue
        ref_text = str(e.get("path") or "").strip().strip("[]")
        if "!" not in ref_text:
            continue
        sname, ref = ref_text.rsplit("!", 1)
        sname = sname.strip().strip("'")
        m = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?(\d{1,4})", ref.strip())
        ws = sheets.get(sname)
        if not m or ws is None:
            continue
        col, row = column_index_from_string(m.group(1).upper()), int(m.group(2))
        if col > wp_xlsx.MAX_COLS or row > wp_xlsx.MAX_ROWS or row < 1:
            continue
        text = mask.back(str(e.get("text") if e.get("text") is not None else "")).strip()
        if not text or len(text) > 1500:
            continue
        cell = ws.cell(row=row, column=col)
        before = "" if cell.value is None else str(cell.value)
        if _SIGNOFF.search(before) or _SIGNOFF.search(text):
            continue
        item = {"path": f"{sname}!{m.group(1).upper()}{row}", "kind": "replace", "text": text, "before": before,
                "why": mask.back(str(e.get("why") or ""))[:300], "warn": ""}
        unknown = [] if text.startswith("=") else _unknown_figures(text, known)
        if unknown:
            item["warn"] = "Check these figures: they are not in the paper, the engagement record or your message: " + ", ".join(unknown[:6]) + "."
        out.append(item)
    return out


def clean_images(raw):
    """[(media_type, base64)] for the pictures the person attached, or (None, error). Each must really be the picture type it says."""
    import base64
    if not raw:
        return [], None
    if not isinstance(raw, list) or len(raw) > MAX_PICTURES:
        return None, f"You can attach up to {MAX_PICTURES} pictures at a time."
    out = []
    for im in raw:
        mt = str((im or {}).get("media_type") or "").lower() if isinstance(im, dict) else ""
        data = str(im.get("data") or "") if isinstance(im, dict) else ""
        if mt not in _PIC_TYPES or not data:
            return None, "Only PNG, JPEG, GIF or WebP pictures can be attached."
        if len(data) > MAX_PICTURE_CHARS:
            return None, "A picture is too large. Crop it or use a smaller one."
        try:
            head = base64.b64decode(data[:64] + "=" * (-len(data[:64]) % 4))
            base64.b64decode(data, validate=True)
        except Exception:
            return None, "That picture could not be read."
        if not head.startswith(_PIC_TYPES[mt]):
            return None, "That file is not the kind of picture it says it is."
        out.append((mt, data))
    return out, None


def _history(raw, mask):
    """The earlier turns as plain text, alternating user / assistant, starting with the user."""
    turns = []
    for h in (raw or [])[-MAX_HISTORY:]:
        if not isinstance(h, dict) or h.get("role") not in ("user", "assistant"):
            continue
        content = mask.out(str(h.get("content") or "")[:MAX_MESSAGE]).strip()
        if not content:
            continue
        if turns and turns[-1]["role"] == h["role"]:
            turns[-1]["content"] += "\n\n" + content
        else:
            turns.append({"role": h["role"], "content": content})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    while turns and turns[-1]["role"] != "assistant":
        turns.pop()
    return turns


# ------------------------------------------------------------------ routes

@wpedit_bp.route("/<int:doc_id>/assist", methods=["POST"])
@login_required
def assist(doc_id):
    doc = _doc_or_404(doc_id)
    if _ext(doc) not in ("docx", "xlsx") or not doc.in_app or not os.path.exists(_path(doc)):
        return jsonify(ok=False, error="Claude works in papers that are open in the app."), 400
    if not is_configured():
        return jsonify(ok=False, error="Claude is not switched on yet: ANTHROPIC_API_KEY is not set on the server. Ask the administrator to add it on Render."), 200
    if not _rate_ok(current_user.id):
        return jsonify(ok=False, error="That is a lot of questions in a short time. Give it a few minutes."), 429
    data = request.get_json(silent=True) or {}
    pictures, pic_error = clean_images(data.get("images"))
    if pic_error:
        return jsonify(ok=False, error=pic_error), 400
    message = str(data.get("message") or "").strip()[:MAX_MESSAGE]
    if not message and pictures:
        message = "Look at the picture" + ("s" if len(pictures) > 1 else "") + " and help me with this paper."
    if not message:
        return jsonify(ok=False, error="Type a question or choose one of the suggestions."), 400
    sel = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    sel_text = str(sel.get("text") or "").strip()[:MAX_SELECTION]
    sel_path = str(sel.get("path") or "").strip()[:60]
    sel_picked = bool(sel.get("selected"))

    eng = doc.engagement
    mask = Mask(eng.client)
    is_docx = _ext(doc) == "docx"
    d = DocxDocument(_path(doc)) if is_docx else None
    paper_pics = []
    paper = docx_paper_text(d, paper_pics) if is_docx else xlsx_paper_text(_path(doc))
    shown_pics, pics_note = prepare_pictures(paper_pics) if paper_pics else ([], "")
    context = engagement_context(eng)

    head = f"THE WORKING PAPER ({doc.original_filename}, {'Word' if is_docx else 'Excel'}" + (f", reference {doc.reference}" if doc.reference else "") + ")\n" + paper
    parts = ["ENGAGEMENT\n" + mask.out(context), mask.out(head)]      # the file name usually carries the client's name, so it is masked too
    if sel_text:
        if is_docx:
            where = f" (at position [{sel_path}])" if sel_path else ""
            parts.append(("SELECTED TEXT" if sel_picked else "THE PARAGRAPH THE PERSON IS IN") + where + "\n" + mask.out(sel_text))
        else:
            parts.append("THE CELL THE PERSON HAS SELECTED" + (f" ({sel_path})" if sel_path else "") + "\n" + mask.out(sel_text))
    lib = library_block(" ".join([message, sel_text, eng.title or ""])[:1800], mask)
    if lib:
        parts.append("LIBRARY EXCERPTS (the firm's filed legislation and guidance, for anything about the law)\n\n" + lib)
    if not is_docx:
        parts.append("NOTE: this is an Excel paper. Cells are shown as Sheet!Cell: value (a formula is shown with = and what it currently shows). To change or fill in a cell, "
                     "suggest kind replace with the path Sheet!Cell (for example CGT Computation!B16) and the new value as the text - a number, words, or a simple "
                     "formula starting with = that uses only + - * / ( ), SUM, AVERAGE, MIN, MAX, ROUND, IF and cell references. Any cell in the grid can be filled in, "
                     "even if it is not listed because it is empty. Do not suggest changes to formulas that are correct.")
    if pictures:
        parts.append(f"PICTURES\nThe person attached {len(pictures)} picture{'s' if len(pictures) > 1 else ''} to this message (shown above this text, in order). They are not masked.")
    parts.append("THE PERSON'S MESSAGE\n" + mask.out(message))
    final = "\n\n=====\n\n".join(parts)

    if shown_pics:
        final += "\n\n=====\n\nPICTURES IN THE PAPER\nThe paper's own pictures (logos, scans, images) are shown above this text, labelled PICTURE n, matching the [PICTURE n] marks in the paper." + (" " + pics_note if pics_note else "") + " They are the firm's or the client's own pictures and are not masked."
    elif pics_note:
        final += "\n\n=====\n\nPICTURES IN THE PAPER\n" + pics_note
    content = final
    if pictures or shown_pics:
        content = []
        for label, mt, b64 in shown_pics:
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}})
        for n, (mt, b64) in enumerate(pictures, 1):
            content.append({"type": "text", "text": f"ATTACHED picture {n} of {len(pictures)} (from the person's message):"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}})
        content.append({"type": "text", "text": final})
    messages = _history(data.get("history"), mask) + [{"role": "user", "content": content}]
    result, error = _call_model(SYSTEM, messages)
    if error or not result:
        current_app.logger.info("wp_assist doc=%s user=%s failed: %s", doc.id, current_user.id, error)
        return jsonify(ok=False, error=error or "Claude could not answer. Try again."), 200

    # figures that are already in the paper, the record or what the person typed are not "new"
    corpus = " ".join([paper, context, message, sel_text, " ".join(str(h.get("content") or "") for h in (data.get("history") or []) if isinstance(h, dict))])
    known = zimra_ai.numbers_in(corpus)
    this_year = date.today().year
    known |= {f"{y:.2f}" for y in (this_year - 1, this_year, this_year + 1)}
    raw_edits = result.get("edits") if isinstance(result.get("edits"), list) else []
    edits = clean_edits(raw_edits, d, known, mask) if is_docx else clean_cell_edits(raw_edits, _path(doc), known, mask)
    reply = mask.back(str(result.get("message") or "")).strip() or ("I have suggested the changes below." if edits else "I have nothing to add.")
    dropped = len(raw_edits) - len(edits)
    if dropped > 0:
        reply += f"\n\n({dropped} of my suggestions could not be placed in the paper: " + ("the position was not a real paragraph, was a sign-off cell, or the text was empty" if is_docx else "the sheet or cell was not valid, was a sign-off cell, or the value was empty") + ". Ask me again, or select the place you mean first.)"
    current_app.logger.warning("wp_assist doc=%s user=%s ok chars_in=%s pictures=%s edits=%s dropped=%s", doc.id, current_user.id, len(final), len(pictures), len(edits), max(dropped, 0))
    return jsonify(ok=True, reply=reply, edits=edits, can_edit=bool(not doc.is_partner_signed))


@wpedit_bp.route("/<int:doc_id>/assist/insert", methods=["POST"])
@login_required
def assist_insert(doc_id):
    """Apply a 'new paragraph below' suggestion: add a paragraph under the one at `path` (it copies that paragraph's formatting) and put the text in."""
    doc = _doc_or_404(doc_id)
    bad = _check_writable(doc)
    if bad:
        return bad
    if _ext(doc) != "docx":
        return jsonify(ok=False, error="Only Word papers can take a new paragraph."), 400
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()[:MAX_EDIT_CHARS]
    path_text = str(data.get("path") or "")
    if not text:
        return jsonify(ok=False, error="There is no text to add."), 400
    d = DocxDocument(_path(doc))
    old = wp_docx.parse_path(path_text)
    ok, msg = wp_docx.structural(d, "para_add", path_text)
    if not ok:
        return jsonify(ok=False, error=msg), 400
    new_path = old[:-1] + [old[-1] + 1]
    changed = wp_docx.apply_changes(d, [{"path": ".".join(str(x) for x in new_path), "runs": {"0": text}}])
    if not changed:
        wp_docx.apply_changes(d, [{"path": ".".join(str(x) for x in new_path), "segments": [{"t": text}]}])
    wp_docx.save_atomic(d, _path(doc))
    doc.uploaded_at = datetime.utcnow()
    doc.reviewed_by_id = None
    doc.reviewed_at = None
    db.session.commit()
    current_app.logger.info("wp_assist doc=%s user=%s inserted a paragraph after %s", doc.id, current_user.id, path_text)
    return jsonify(ok=True, message="Paragraph added.")


@wpedit_bp.route("/<int:doc_id>/assist/fill", methods=["POST"])
@login_required
def assist_fill(doc_id):
    """Apply a suggestion that fills in an empty paragraph (a blank field in a table, say): the text goes into the paragraph at `path`."""
    doc = _doc_or_404(doc_id)
    bad = _check_writable(doc)
    if bad:
        return bad
    if _ext(doc) != "docx":
        return jsonify(ok=False, error="Only Word papers are filled in this way."), 400
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()[:MAX_EDIT_CHARS]
    path_text = str(data.get("path") or "")
    if not text:
        return jsonify(ok=False, error="There is no text to add."), 400
    d = DocxDocument(_path(doc))
    p = wp_docx.resolve(d, wp_docx.parse_path(path_text) or [])
    if p is None or p.tag != qn("w:p"):
        return jsonify(ok=False, error="That place is no longer in the paper."), 400
    if _SIGNOFF.search(wp_docx.para_text(p)) or _SIGNOFF.search(text):
        return jsonify(ok=False, error="Sign-off cells cannot be filled in by Claude."), 400
    wp_docx.apply_changes(d, [{"path": path_text, "segments": [{"t": text}]}])
    wp_docx.save_atomic(d, _path(doc))
    doc.uploaded_at = datetime.utcnow()
    doc.reviewed_by_id = None
    doc.reviewed_at = None
    db.session.commit()
    current_app.logger.info("wp_assist doc=%s user=%s filled %s", doc.id, current_user.id, path_text)
    return jsonify(ok=True, message="Filled in.")
