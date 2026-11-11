"""Valuation Model - bringing COMPONENT valuations in.

Sources
  * the app's own Excel template (one row per component) - downloaded, completed and uploaded back;
  * any other Excel valuation workbook (a Low / Adopted / High table, or a labelled value such as "Equity value");
  * Word / PDF valuation reports and certificates (the conclusion and the range are read from the text);
  * a forecast model's saved valuation and a valuation certificate already in the app.

Two readers for files, same output: Claude when ANTHROPIC_API_KEY is set (any layout, scans) and a built-in reader (templates, tables, and
the usual "value of US$1,000,000 within a range of US$500,000 to US$2,000,000" wording). Either way the figures are shown on a review page to be
checked and edited before anything is added to the model.

A candidate is {"comp": {...component fields...}, "selected": bool, "confidence": "high"|"medium"|"low", "where": str}.
"""
import io
import os
import re
from datetime import date, datetime

import cf_valmodel as V

CURRENCY_WORDS = [("USD", [r"\bUS\$", r"\bUSD\b", r"United States dollar"]), ("ZWG", [r"\bZWG\b", r"\bZiG\b", r"Zimbabwe Gold"]),
                  ("ZAR", [r"\bZAR\b", r"\bR\s?\d", r"South African rand"]), ("GBP", [r"\bGBP\b", "£"]), ("EUR", [r"\bEUR\b", "€"]),
                  ("BWP", [r"\bBWP\b"])]
VALUE_WORD = re.compile(r"\b(value|valuation|worth|equity|enterprise|ev)\b", re.I)
NOT_VALUE = re.compile(r"per share|per hectare|per ha\b|discount|growth|rate|wacc|multiple|margin|%|ratio|premium|debt|cash|tax|years?\b|beta|factor|weight", re.I)
LOW_H = re.compile(r"^(low|downside|minimum|min|conservative|pessimistic)\b", re.I)
HIGH_H = re.compile(r"^(high|upside|maximum|max|optimistic)\b", re.I)
MID_H = re.compile(r"^(adopted|base|mid|midpoint|mid-point|central|point estimate|most likely|expected|concluded|best estimate|median)\b", re.I)
SCALE_WORDS = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "mil": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9}

TEMPLATE_HEADERS = [
    ("Component", 30, "Name of the subsidiary, business unit or asset"),
    ("Type", 22, "Subsidiary, Associate / joint venture, Business unit, Property, Mining / resource asset, Investment / securities, Intangible asset, Plant and equipment, Other asset"),
    ("Valuation method", 24, "e.g. Discounted cash flow, Market multiples, Net asset value"),
    ("Currency", 10, "USD, ZWG, ZAR, GBP, EUR, BWP"),
    ("Basis", 12, "equity (equity value) or enterprise (enterprise value - the net debt below is deducted)"),
    ("Valuation date", 14, "dd/mm/yyyy"),
    ("Low", 16, "Low case value, whole currency units"),
    ("Adopted", 16, "Adopted (central) value"),
    ("High", 16, "High case value"),
    ("Net debt", 14, "Only for an enterprise value: debt less cash"),
    ("Ownership %", 12, "The group's interest in the component, 0 - 100"),
    ("Minority discount %", 12, "Discount for lack of control, if any"),
    ("DLOM %", 12, "Discount for lack of marketability, if any"),
    ("Control premium %", 12, "Premium for control, if any"),
    ("Notes", 40, "Key assumptions, source report"),
]
HEADER_MAP = {
    "component": "name", "name": "name", "entity": "name", "subsidiary": "name", "asset": "name", "type": "type",
    "valuation method": "method", "method": "method", "currency": "currency", "basis": "basis", "valuation date": "date", "date": "date",
    "low": "low", "adopted": "adopted", "high": "high", "net debt": "net_debt", "ownership %": "ownership_pct", "ownership": "ownership_pct",
    "minority discount %": "minority_pct", "minority discount": "minority_pct", "dlom %": "dlom_pct", "dlom": "dlom_pct",
    "control premium %": "control_pct", "control premium": "control_pct", "notes": "notes",
}


# --------------------------------------------------------------------------- the Excel template

def template_workbook(reporting_currency="USD", client_name="", rows=None):
    """The component template: an Instructions sheet and a Components sheet (optionally pre-filled)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    import brand_docs as brand
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Instructions"
    brand.xlsx_title_band(ws0, "Component valuation template", [client_name or "Neverlank Chartered Accountants", "Fill the Components sheet, then upload it to the Valuation Model"], ncols=6)
    ws0.column_dimensions["A"].width = 34
    ws0.column_dimensions["B"].width = 110
    r = 8
    ws0.cell(row=r, column=1, value="Column").font = Font(bold=True)
    ws0.cell(row=r, column=2, value="What to enter").font = Font(bold=True)
    for h, _, note in TEMPLATE_HEADERS:
        r += 1
        ws0.cell(row=r, column=1, value=h)
        ws0.cell(row=r, column=2, value=note)
    r += 2
    for line in ("One row per component. Values are in whole currency units of the component's own currency (the model converts them).",
                 "Give a Low, Adopted and High value; with only an Adopted value, low and high are taken as the same figure.",
                 "Use Basis = enterprise only when the figure is an enterprise value; the Net debt is then deducted to reach equity.",
                 "You will see every figure on a review screen and can correct it before it joins the model."):
        ws0.cell(row=r, column=1, value="-")
        ws0.cell(row=r, column=2, value=line)
        r += 1
    ws = wb.create_sheet("Components")
    head_fill = PatternFill(start_color=brand.NAVY, end_color=brand.NAVY, fill_type="solid")
    for i, (h, w, _) in enumerate(TEMPLATE_HEADERS, start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), head_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(w, 12)
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "B2"
    data = rows or [{"name": "Example Subsidiary (Pvt) Ltd", "type": "Subsidiary", "method": "Discounted cash flow", "currency": reporting_currency, "basis": "equity",
                     "date": "", "low": 800000, "adopted": 1000000, "high": 1250000, "ownership_pct": 100}]
    for ri, comp in enumerate(data, start=2):
        for ci, key in enumerate(("name", "type", "method", "currency", "basis", "date", "low", "adopted", "high", "net_debt", "ownership_pct", "minority_pct",
                                  "dlom_pct", "control_pct", "notes"), start=1):
            v = comp.get(key)
            cell = ws.cell(row=ri, column=ci, value=v if v not in ("", None) else None)
            if key in ("low", "adopted", "high", "net_debt"):
                cell.number_format = '#,##0;(#,##0);"-"'
            if key == "date" and v:
                d = V.parse_date(v)
                if d:
                    cell.value = d
                    cell.number_format = "dd/mm/yyyy"
    dv1 = DataValidation(type="list", formula1='"' + ",".join(V.COMPONENT_TYPES) + '"', allow_blank=True)
    dv2 = DataValidation(type="list", formula1='"equity,enterprise"', allow_blank=True)
    dv3 = DataValidation(type="list", formula1='"USD,ZWG,ZAR,GBP,EUR,BWP"', allow_blank=True)
    for dv, col in ((dv1, "B"), (dv2, "E"), (dv3, "D")):
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}200")
    wb.properties.creator = "Neverlank Chartered Accountants"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- helpers

def detect_currency(text, default="USD"):
    best, hits = default, 0
    for code, pats in CURRENCY_WORDS:
        n = sum(len(re.findall(p, text or "")) for p in pats)
        if n > hits:
            best, hits = code, n
    return best


def parse_amount(s):
    """'US$1,000,000' -> 1000000; 'USD 1.2 million' -> 1200000. None when there is no amount."""
    m = re.search(r"(\d[\d,\s]*(?:\.\d+)?)\s*(thousand|million|billion|mn|mil|bn|k|m|b)?\b", str(s or ""), re.I)
    if not m:
        return None
    raw = m.group(1).replace(",", "").replace(" ", "")
    try:
        n = float(raw)
    except ValueError:
        return None
    w = (m.group(2) or "").lower()
    return n * SCALE_WORDS.get(w, 1.0)


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "").replace(" ", " ")).strip()


def _cand(comp, confidence="medium", where="", selected=True):
    comp = dict(comp)
    return {"comp": V.normalise_component(comp), "selected": selected, "confidence": confidence, "where": where}


def _stem(filename):
    return re.sub(r"[_\-]+", " ", os.path.splitext(os.path.basename(filename or "Component"))[0]).strip()[:120] or "Component"


# --------------------------------------------------------------------------- Excel reader

def read_excel(path, filename, default_currency="USD"):
    """(candidates, notes) from an Excel workbook: the app's template first, then Low / Adopted / High tables, then labelled values."""
    import openpyxl
    notes, cands = [], []
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception:
        return [], ["That workbook could not be opened."]
    formulas_blank = False
    saw_template = False
    try:
        wbf = openpyxl.load_workbook(path, data_only=False)
    except Exception:
        wbf = None
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        rows = [[c for c in r] for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 600), max_col=min(ws.max_column or 1, 40), values_only=True)]
        is_tpl, found = _template_rows(rows, ws.title, default_currency)
        saw_template = saw_template or is_tpl
        cands.extend(found)
    if saw_template:
        if not cands:
            return [], notes + ["The template has no completed rows - fill in at least a Component name and an Adopted value."]
        return cands, notes + ["Read from the component template."]
    text_blob = ""
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        rows = [[c for c in r] for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 600), max_col=min(ws.max_column or 1, 40), values_only=True)]
        text_blob += "\n".join(" ".join(str(x) for x in r if x is not None) for r in rows[:60]) + "\n"
        meta = _sheet_meta(rows, filename, default_currency)
        cands.extend(_table_candidates(rows, ws.title, meta))
    if not cands:
        for ws in wb.worksheets:
            if ws.sheet_state != "visible":
                continue
            rows = [[c for c in r] for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 600), max_col=min(ws.max_column or 1, 40), values_only=True)]
            meta = _sheet_meta(rows, filename, default_currency)
            cands.extend(_labelled_candidates(rows, ws.title, meta))
    if not cands and wbf is not None:
        nform = sum(1 for ws in wbf.worksheets for r in ws.iter_rows(max_row=400) for c in r if isinstance(c.value, str) and c.value.startswith("="))
        if nform:
            formulas_blank = True
    if formulas_blank:
        notes.append("This workbook contains formulas with no calculated results (it was not saved by Excel). Open it in Excel, save it, and upload it again.")
    _pick_best(cands)
    if not cands and not formulas_blank:
        notes.append("No low / adopted / high table or labelled value was found in the workbook.")
    return cands, notes


def _template_rows(rows, sheet, default_currency):
    hdr_i, colmap = None, {}
    for i, r in enumerate(rows[:40]):
        cm = {}
        for j, v in enumerate(r):
            k = HEADER_MAP.get(_clean(v).lower())
            if k and k not in cm.values():
                cm[j] = k
        if "adopted" in cm.values() and "name" in cm.values():
            hdr_i, colmap = i, cm
            break
    if hdr_i is None:
        return False, []
    out = []
    for n, r in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        rec = {k: (r[j] if j < len(r) else None) for j, k in colmap.items()}
        name = _clean(rec.get("name"))
        if not name:
            continue
        if all(rec.get(k) in (None, "") for k in ("low", "adopted", "high")):
            continue
        if name.lower().startswith("example subsidiary"):
            continue
        rec["name"] = name
        rec["currency"] = _clean(rec.get("currency")).upper() or default_currency
        b = _clean(rec.get("basis")).lower()
        rec["basis"] = "enterprise" if b.startswith("ent") or b == "ev" else "equity"
        out.append(_cand(rec, "high", f"{sheet} row {n}"))
    return True, out


def _sheet_meta(rows, filename, default_currency):
    head = rows[:25]
    blob = "\n".join(" ".join(str(x) for x in r if x is not None) for r in head)
    meta = {"name": None, "date": None, "currency": detect_currency(blob, default_currency), "method": ""}
    for r in head:
        for j, v in enumerate(r):
            t = _clean(v).lower().rstrip(":")
            nxt = next((x for x in r[j + 1:] if x not in (None, "")), None)
            if t in ("company", "company name", "entity", "entity name", "client", "client name", "subject", "subject asset", "target", "business", "valuation of") and nxt and not meta["name"]:
                meta["name"] = _clean(nxt)[:160]
            if t in ("valuation date", "effective date", "as at", "date of valuation", "valuation as at") and nxt and not meta["date"]:
                d = V.parse_date(nxt)
                meta["date"] = d.isoformat() if d else None
            if t in ("method", "valuation method", "approach") and nxt and not meta["method"]:
                meta["method"] = _clean(nxt)[:100]
    return meta


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _table_candidates(rows, sheet, meta):
    out = []
    for i, r in enumerate(rows):
        lo = hi = mid = None
        for j, v in enumerate(r):
            t = _clean(v)
            if not t or len(t) > 40:
                continue
            if LOW_H.match(t) and lo is None:
                lo = j
            elif HIGH_H.match(t) and hi is None:
                hi = j
            elif MID_H.match(t) and mid is None:
                mid = j
        if lo is None or hi is None or mid is None:
            continue
        first = min(lo, hi, mid)
        seen = False
        for k in range(i + 1, min(i + 45, len(rows))):
            rr = rows[k]
            vals = [rr[x] if x < len(rr) else None for x in (lo, mid, hi)]
            if not any(_is_num(v) for v in vals):
                if seen and not any(x not in (None, "") for x in rr):
                    break
                continue
            label = next((_clean(x) for x in rr[:first][::-1] if isinstance(x, str) and _clean(x)), "") or next((_clean(x) for x in rr if isinstance(x, str) and _clean(x)), "")
            if not label or NOT_VALUE.search(label) or not VALUE_WORD.search(label):
                continue
            seen = True
            basis = "enterprise" if re.search(r"enterprise|\bev\b", label, re.I) else "equity"
            name = meta["name"] or _stem(sheet if sheet.lower() not in ("sheet1", "valuation", "summary") else "")
            comp = {"name": name, "type": "Subsidiary", "method": meta["method"] or label, "currency": meta["currency"], "basis": basis,
                    "date": meta["date"] or "", "low": vals[0] if _is_num(vals[0]) else None, "adopted": vals[1] if _is_num(vals[1]) else None,
                    "high": vals[2] if _is_num(vals[2]) else None, "notes": f"Read from '{label}' on sheet {sheet}"}
            conf = "high" if re.search(r"equity value|fair value|market value|adopted", label, re.I) else "medium"
            out.append(_cand(comp, conf, f"{sheet} row {k + 1}", selected=False))
    return out


def _labelled_candidates(rows, sheet, meta):
    out = []
    for k, rr in enumerate(rows):
        label = next((_clean(x) for x in rr if isinstance(x, str) and _clean(x)), "")
        if not label or len(label) > 70 or NOT_VALUE.search(label):
            continue
        if not re.search(r"equity value|enterprise value|fair value|market value|valuation|value of|total value|concluded value|adopted value", label, re.I):
            continue
        nums = [x for x in rr if _is_num(x)]
        if not nums:
            continue
        if len(nums) >= 3 and nums[0] <= nums[1] <= nums[2]:
            lo, ad, hi = nums[0], nums[1], nums[2]
        else:
            lo, ad, hi = None, nums[0], None
        basis = "enterprise" if re.search(r"enterprise|\bev\b", label, re.I) else "equity"
        comp = {"name": meta["name"] or _stem(sheet), "type": "Subsidiary", "method": meta["method"] or label, "currency": meta["currency"], "basis": basis,
                "date": meta["date"] or "", "low": lo, "adopted": ad, "high": hi, "notes": f"Read from '{label}' on sheet {sheet}"}
        out.append(_cand(comp, "low", f"{sheet} row {k + 1}", selected=False))
    return out


def _pick_best(cands):
    """Select the single most likely candidate when the file held several (equity before enterprise, high confidence first)."""
    if not cands or all(c["selected"] for c in cands):
        return
    rank = {"high": 0, "medium": 1, "low": 2}
    best = sorted(range(len(cands)), key=lambda i: (cands[i]["comp"]["basis"] != "equity", rank[cands[i]["confidence"]], i))[0]
    for i, c in enumerate(cands):
        c["selected"] = (i == best)


# --------------------------------------------------------------------------- Word / PDF / text reader

AMT = r"(?:US\$|USD|ZWG|ZiG|ZAR|GBP|EUR|BWP|\$|R|£|€)?\s?\d[\d,]*(?:\.\d+)?\s?(?:thousand|million|billion|mn|bn|m|k)?"


def read_text(text, filename, default_currency="USD"):
    """(candidates, notes) from the text of a Word / PDF report or a pasted conclusion."""
    text = text or ""
    cur = detect_currency(text, default_currency)
    flat = _clean(text)
    low = high = adopted = None
    where = ""
    notes = []
    m = re.search(r"range of\s*(" + AMT + r")\s*(?:to|and|-|–)\s*(" + AMT + r")", flat, re.I) or \
        re.search(r"between\s*(" + AMT + r")\s*and\s*(" + AMT + r")", flat, re.I)
    if m:
        low, high = parse_amount(m.group(1)), parse_amount(m.group(2))
        where = "range sentence"
    m2 = re.search(r"(?:adopted|concluded|conclusion|fair|market|equity|enterprise|indicative|estimated)?\s*value[^.\n]{0,120}?\b(?:of|is|at|:)\s*(" + AMT + r")", flat, re.I)
    if m2:
        adopted = parse_amount(m2.group(1))
    # table: a header line with low / adopted / high followed by a "value" line holding three numbers
    lines = [l for l in re.split(r"[\r\n]+", text) if l.strip()]
    for i, l in enumerate(lines):
        ll = l.lower()
        if "low" in ll and "high" in ll and re.search(r"adopted|base|mid|central", ll):
            for l2 in lines[i + 1:i + 8]:
                nums = [parse_amount(x) for x in re.findall(AMT, l2)]
                nums = [n for n in nums if n is not None and n >= 100]
                if len(nums) >= 3 and VALUE_WORD.search(l2) and not NOT_VALUE.search(l2):
                    low, adopted, high = nums[0], nums[1], nums[2]
                    where = "value table"
                    break
            if where == "value table":
                break
    if adopted is None and low is not None and high is not None:
        adopted = (low + high) / 2.0
        notes.append("Only the range was found, so the adopted value is the midpoint - please check it.")
    if adopted is None:
        return [], ["No value or range was found in the text. Use the AI reader, the Excel template, or type the component in."]
    name = ""
    for pat in (r"(?:subject asset|prepared for|client|valuation of|entity|company)\s*[:\-]\s*([^\n|]{3,120})",):
        mm = re.search(pat, text, re.I)
        if mm:
            name = _clean(mm.group(1))
            break
    name = name or _stem(filename)
    d = None
    mm = re.search(r"(?:valuation date|effective date|as at|date of valuation)\s*[:\-]?\s*(\d{1,2}[ /.-][A-Za-z0-9]{1,9}[ /.-]\d{4})", text, re.I)
    if mm:
        dd = V.parse_date(mm.group(1).replace("-", " ") if re.search(r"[A-Za-z]", mm.group(1)) else mm.group(1))
        d = dd.isoformat() if dd else None
    basis = "enterprise" if re.search(r"enterprise value", flat, re.I) and not re.search(r"equity value", flat, re.I) else "equity"
    method = ""
    for kw, label in (("discounted cash flow", "Discounted cash flow"), ("market multiple", "Market multiples"), ("net asset value", "Net asset value"),
                      ("comparable", "Market multiples"), ("cost approach", "Cost approach")):
        if kw in flat.lower():
            method = label
            break
    comp = {"name": name, "type": "Subsidiary", "method": method or "Independent valuation report", "currency": cur, "basis": basis, "date": d or "",
            "low": low, "adopted": adopted, "high": high, "notes": f"Read from {os.path.basename(filename or 'the document')} ({where or 'text'})"}
    return [_cand(comp, "medium" if low is not None else "low", where or "text")], notes


# --------------------------------------------------------------------------- AI reader

_AI_SYSTEM = (
    "You read valuation reports, certificates and workbooks for an audit and advisory firm and record the VALUE CONCLUSIONS of each valuation found. "
    "Record one component per entity or asset valued. Give low, adopted and high values as whole currency units (apply any stated scale such as "
    "thousands or millions). If only a single value is stated, give it as adopted. Say whether the value is an equity value or an enterprise value; "
    "for an enterprise value also give the net debt if the report states it. Never invent a figure; leave fields out when the document does not state them."
)


def _tool_schema():
    num = {"type": "number"}
    return {
        "name": "record_component_valuations",
        "description": "Record the value conclusion of every valuation found in the document.",
        "input_schema": {"type": "object", "properties": {
            "components": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "type": {"type": "string", "enum": V.COMPONENT_TYPES}, "method": {"type": "string"},
                "currency": {"type": "string"}, "basis": {"type": "string", "enum": ["equity", "enterprise"]},
                "valuation_date": {"type": "string", "description": "yyyy-mm-dd if stated"},
                "low": num, "adopted": num, "high": num, "net_debt": num, "ownership_pct": num, "notes": {"type": "string"}},
                "required": ["name", "adopted"]}},
            "notes": {"type": "array", "items": {"type": "string"}}}, "required": ["components"]},
    }


def ai_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ai_extract(text=None, page_images=None, default_currency="USD"):
    """(candidates, notes, error). Never raises."""
    if not ai_available():
        return [], [], "ANTHROPIC_API_KEY is not set"
    try:
        import base64
        import entity_extraction as EX
        content = []
        if page_images:
            for img in page_images:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode("ascii")}})
            content.append({"type": "text", "text": "Record the valuation conclusion(s) shown in the page image(s) above."})
        else:
            content.append({"type": "text", "text": "Record the valuation conclusion(s) in this document:\n\n" + (text or "")[:90000]})
        resp = EX._client().messages.create(model=EX.DEFAULT_MODEL, max_tokens=4096, system=_AI_SYSTEM, tools=[_tool_schema()],
                                            tool_choice={"type": "tool", "name": "record_component_valuations"},
                                            messages=[{"role": "user", "content": content}])
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_component_valuations":
                return parse_ai(block.input or {}, default_currency) + (None,)
        return [], [], "The reader returned no structured result."
    except Exception as exc:
        return [], [], str(exc)[:300]


def parse_ai(raw, default_currency="USD"):
    cands = []
    for c in (raw.get("components") or [])[:30]:
        comp = {"name": c.get("name"), "type": c.get("type") or "Subsidiary", "method": c.get("method") or "", "currency": (c.get("currency") or default_currency),
                "basis": c.get("basis") or "equity", "date": c.get("valuation_date") or "", "low": c.get("low"), "adopted": c.get("adopted"),
                "high": c.get("high"), "net_debt": c.get("net_debt"), "ownership_pct": c.get("ownership_pct") if c.get("ownership_pct") is not None else 100,
                "notes": c.get("notes") or ""}
        if comp["adopted"] is None and comp["low"] is None and comp["high"] is None:
            continue
        cands.append(_cand(comp, "medium", "read by AI"))
    return cands, [str(n)[:300] for n in (raw.get("notes") or [])][:6]


# --------------------------------------------------------------------------- one entry point for a file

def read_file(path, filename, default_currency="USD"):
    """(candidates, notes, method). method is 'template', 'excel', 'ai' or 'text'. Raises ValueError with a message for the user."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    notes = []
    if ext in ("xlsx", "xlsm"):
        cands, n = read_excel(path, filename, default_currency)
        notes += n
        if cands:
            return cands, notes, ("template" if "Read from the component template." in n else "excel")
    import cf_fsload as FS
    text, images, err = FS.file_to_text(path, filename)
    if err and not text and not images:
        if ext in ("xlsx", "xlsm"):
            return [], notes, "excel"
        raise ValueError(err)
    if ai_available():
        cands, ainotes, aerr = ai_extract(text=text, page_images=images, default_currency=default_currency)
        if cands:
            return cands, notes + ainotes, "ai"
        notes.append(f"The AI reader could not be used ({aerr}); the built-in reader was used instead." if aerr else "The AI reader found no valuation conclusion.")
    if not text:
        raise ValueError("This document has no text layer and the AI reader is not available, so it cannot be read. Upload a text-based PDF, Word or Excel version, or type the component in.")
    cands, tn = read_text(text, filename, default_currency)
    return cands, notes + tn, "text"


# --------------------------------------------------------------------------- the app's own valuations

def from_model_valuation(model, val_obj, result=None, scenario_label=""):
    """A component from a forecast model's saved valuation: DCF equity value (100%) with low / high from the +/-1% WACC, +/-0.5% growth
    football-field row; the valuation's own stake % and marketability discount become the component's ownership and DLOM."""
    import cf_tools as T
    import cf_engine as E
    if result is None:
        result = E.run(E.normalise(model.assumptions))
    v = T.normalise_valuation(val_obj.inputs)
    row = T.football_field(result, v)[0]
    base = T.dcf(result, v)
    comp = {"name": model.client.name if model.client else model.name, "type": "Subsidiary", "method": "Forecast model valuation (DCF)",
            "currency": model.currency, "basis": "equity", "date": val_obj.valuation_date.isoformat() if val_obj.valuation_date else "",
            "low": min(row["low"], base["equity"]), "adopted": base["equity"], "high": max(row["high"], base["equity"]),
            "ownership_pct": v["stake_pct"], "dlom_pct": v["dlom"],
            "notes": f"{model.model_number} {model.name} - valuation '{val_obj.name}'" + (f" ({scenario_label})" if scenario_label else "")}
    return _cand(comp, "high", f"{model.model_number} / {val_obj.name}")


def from_certificate(cert):
    comp = {"name": (cert.client_name or "") + (" - " if cert.client_name and cert.subject_asset else "") + (_clean(cert.subject_asset)[:90] if cert.subject_asset else ""),
            "type": "Mining / resource asset" if re.search(r"claim|mine|mining|coal|gold|lithium", (cert.subject_asset or "") + (cert.asset_noun or ""), re.I) else "Other asset",
            "method": cert.valuation_type and _clean(cert.valuation_type)[:100] or "Valuation certificate", "currency": cert.currency or "USD", "basis": "equity",
            "date": cert.valuation_date.isoformat() if cert.valuation_date else "", "low": cert.low_value, "adopted": cert.adopted_value, "high": cert.high_value,
            "ownership_pct": 100, "notes": f"Valuation certificate {cert.reference}"}
    comp["name"] = comp["name"].strip(" -") or cert.reference
    return _cand(comp, "high", cert.reference)
