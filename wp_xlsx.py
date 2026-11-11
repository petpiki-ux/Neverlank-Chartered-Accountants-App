"""Working papers in the app, Excel side: the first sheet(s) of a workbook as an editable grid (values, formulas, fills, fonts, merged
cells, column widths), written back into the same .xlsx cell by cell. Formulas are evaluated in the browser while you type; the saved file
keeps the formulas, so Excel recalculates them when it is opened."""
import datetime as dtm
import os
import re
import tempfile

import openpyxl
from openpyxl.utils import get_column_letter, column_index_from_string

MAX_ROWS = 400
MAX_COLS = 40


def _rgb(color):
    try:
        if color is not None and color.type == "rgb" and isinstance(color.rgb, str) and len(color.rgb) == 8 and color.rgb != "00000000":
            return "#" + color.rgb[2:]
        if color is not None and color.type == "theme":
            return None
    except Exception:
        pass
    return None


def _border(b):
    return b is not None and b.style not in (None, "")


def _fill(cell):
    f = cell.fill
    if f is not None and f.fill_type == "solid":
        return _rgb(f.fgColor)
    return None


def _kind_of(cell):
    v = cell.value
    if isinstance(v, str) and v.startswith("="):
        return "f"
    if isinstance(v, bool):
        return "b"
    if isinstance(v, (int, float)):
        return "n"
    if isinstance(v, (dtm.datetime, dtm.date)):
        return "d"
    return "s"


def _jsonable(v):
    if isinstance(v, dtm.datetime):
        return v.date().isoformat() if v.time() == dtm.time(0) else v.isoformat()
    if isinstance(v, dtm.date):
        return v.isoformat()
    return v


def sheet_model(wb_path):
    """All sheets as {"sheets": [{name, rows, cols, widths, heights, merges, cells: {"A1": {...}}}]}."""
    wb = openpyxl.load_workbook(wb_path)
    sheets = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        max_r = min(ws.max_row or 1, MAX_ROWS)
        max_c = min(ws.max_column or 1, MAX_COLS)
        widths = []
        for ci in range(1, max_c + 1):
            cd = ws.column_dimensions.get(get_column_letter(ci))
            w = cd.width if cd is not None and cd.width else 8.43
            if cd is not None and cd.hidden:
                w = 0
            widths.append(round(w * 7 + 5))
        heights = []
        for ri in range(1, max_r + 1):
            rd = ws.row_dimensions.get(ri)
            heights.append(round(rd.height * 1.333) if rd is not None and rd.height else None)
        cells = {}
        for row in ws.iter_rows(min_row=1, max_row=max_r, max_col=max_c):
            for c in row:
                d = {}
                if c.value is not None:
                    d["v"] = _jsonable(c.value)
                    d["k"] = _kind_of(c)
                fill = _fill(c)
                if fill:
                    d["bg"] = fill
                if c.font is not None:
                    if c.font.b:
                        d["b"] = 1
                    if c.font.i:
                        d["i"] = 1
                    col = _rgb(c.font.color)
                    if col:
                        d["c"] = col
                    if c.font.sz and abs(c.font.sz - 11) > 0.1:
                        d["sz"] = c.font.sz
                if c.alignment is not None:
                    if c.alignment.horizontal in ("center", "right", "left"):
                        d["al"] = c.alignment.horizontal
                    if c.alignment.wrap_text:
                        d["wr"] = 1
                    if c.alignment.vertical in ("center", "top", "bottom"):
                        d["va"] = c.alignment.vertical
                if c.number_format and c.number_format != "General":
                    d["nf"] = c.number_format
                bd = c.border
                if bd is not None and any(_border(x) for x in (bd.left, bd.right, bd.top, bd.bottom)):
                    d["bd"] = "".join(s for s, x in (("l", bd.left), ("r", bd.right), ("t", bd.top), ("b", bd.bottom)) if _border(x))
                if d:
                    cells[c.coordinate] = d
        merges = [str(m) for m in ws.merged_cells.ranges]
        sheets.append({"name": ws.title, "rows": max_r, "cols": max_c, "widths": widths, "heights": heights, "merges": merges, "cells": cells})
    return {"sheets": sheets}


def _parse_input(text, cell):
    """What a typed string means for this cell: a formula, a number, a date (when the cell is date-formatted) or text."""
    if text is None:
        return None
    t = str(text)
    if t.strip() == "":
        return None
    if t.startswith("="):
        return t
    if re.fullmatch(r"\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}|\d{4}-\d{2}-\d{2})\s*", t):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                return dtm.datetime.strptime(t.strip(), fmt)
            except ValueError:
                continue
    s = t.strip().replace(",", "").replace("$", "")
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    if re.fullmatch(r"[-+]?\(?\d+(\.\d+)?\)?", s):
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        try:
            n = float(s)
        except ValueError:
            return t
        if neg:
            n = -n
        if pct:
            n /= 100.0
        return int(n) if n == int(n) and "." not in s and not pct else n
    return t


def apply_changes(path, changes):
    """changes: [{"sheet": name, "cell": "B5", "value": "text typed"}]. Writes them into the workbook; returns the number applied."""
    wb = openpyxl.load_workbook(path)
    n = 0
    for ch in changes or []:
        ws = wb[ch["sheet"]] if ch.get("sheet") in wb.sheetnames else wb.worksheets[0]
        coord = str(ch.get("cell", "")).upper()
        if not re.fullmatch(r"[A-Z]{1,3}[0-9]{1,5}", coord):
            continue
        cell = ws[coord]
        if type(cell).__name__ == "MergedCell":
            continue
        prev_nf = cell.number_format or "General"
        cell.value = _parse_input(ch.get("value"), cell)
        if isinstance(cell.value, dtm.datetime):
            cell.number_format = "dd/mm/yyyy" if prev_nf == "General" else prev_nf
        n += 1
    save_atomic(wb, path)
    return n


def save_atomic(wb, filename):
    d = os.path.dirname(filename)
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=d)
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, filename)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def prefill(path, facts):
    """Types what the engagement knows into the empty value cell to the right of 'Client name:' style labels."""
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    wanted = {"file reference": facts.get("file_ref"), "client name": facts.get("client"), "prepared by": facts.get("user"),
              "date prepared": facts.get("today"), "tax year": facts.get("tax_year"), "date": facts.get("today"),
              "financial year end": facts.get("period_end"), "tin": facts.get("tin")}
    merged = {}
    for m in ws.merged_cells.ranges:
        merged[(m.min_row, m.min_col)] = m
    filled = 0
    for row in ws.iter_rows():
        for c in row:
            if type(c).__name__ == "MergedCell" or not isinstance(c.value, str):
                continue
            key = c.value.strip().rstrip(":").strip().lower()
            if key not in wanted or not wanted[key]:
                continue
            m = merged.get((c.row, c.column))
            nxt_col = (m.max_col if m else c.column) + 1
            tgt = ws.cell(row=c.row, column=nxt_col)
            if type(tgt).__name__ != "MergedCell" and tgt.value in (None, ""):
                tgt.value = wanted[key]
                filled += 1
    save_atomic(wb, path)
    return filled
