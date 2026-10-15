"""Re-import a management accounts Excel pack that was downloaded from the app, corrected or updated in Excel, and uploaded again.

The workbook's Data sheet holds every input (monthly actuals, budget, forecast, prior year, balance sheets). This module reads it,
works out what differs from the pack as it stands, and builds the updated pack data, so the app ends up holding the final version.

Design rules
- Only the Data sheet (and the reporting month and commentary on the Pack sheet) are read. The statement sheets are formulas on top of the
  Data sheet; if cells there were typed over, that is detected and reported, because those changes cannot be imported.
- Months that were not changed keep their original entry (trial balance source, modelled flag, notes). Changed months become manual month
  entries. If any earlier month changed, later cumulative (trial balance) entries are converted to monthly entries so the corrections carry through.
- Retained earnings that were only the roll-forward in the original are left to roll forward again, so a corrected profit still balances.
- Nothing is applied here: plan_import() returns the new data and a readable list of changes; the route applies it after the user confirms.
"""
import copy
import io
import re
from datetime import datetime

from openpyxl import load_workbook

import cf_ma as M
import cf_recon as RC

TOL = 0.005
MAX_BYTES = 12 * 1024 * 1024

BLOCKS = [  # name, title prefix on the Data sheet
    ("act", "Actual - income statement"), ("bud", "Budget - income statement"), ("fc", "Forecast - income statement"),
    ("pri", "Prior year - income statement"), ("actbs", "Actual - balance sheet"), ("budbs", "Budget - balance sheet"),
    ("fcbs", "Forecast - balance sheet"),
]
COMMENT_SECTIONS = {"Income statement": "income", "Balance sheet": "balance", "Cash flow and funding": "cash",
                    "Full-year outlook": "outlook", "Actions and recommendations": "actions"}


class ImportError_(Exception):
    """A problem the user can fix (shown as a message)."""


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.replace(",", "").replace(" ", "").strip()
        neg = t.startswith("(") and t.endswith(")")
        t = t.strip("()")
        try:
            x = float(t)
        except ValueError:
            return None
        return -x if neg else x
    return None


# =========================================================================== reading

def read_workbook(raw, d, meta=None):
    """Parse an uploaded workbook against the pack `d`. It is either the full Excel pack (Data sheet) or one of the one-page
    exports that hold figures to correct (Income statement, Balance sheet, Actuals, Budget and forecast). Returns a plain dict
    (JSON-safe) or raises ImportError_."""
    if not raw or len(raw) > MAX_BYTES:
        raise ImportError_("The file is empty or larger than 12 MB.")
    try:
        wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=False)
    except Exception:
        raise ImportError_("That file could not be opened. Upload the .xlsx pack you downloaded from this app (File > Save as .xlsx if you used another program).")
    if "Data" not in wb.sheetnames:
        return _read_page(wb, d, meta)
    ws = wb["Data"]
    structure = M.normalise_structure(d["structure"])
    months = M.fy_months(d)
    heads = [ws.cell(row=4, column=3 + i).value for i in range(12)]
    expect = [M.month_label(m) for m in months]
    if [_norm(h) for h in heads] != [_norm(e) for e in expect]:
        raise ImportError_(f"This workbook is for a different financial year or pack (its months run {heads[0]} to {heads[-1]}; this pack runs {expect[0]} to {expect[-1]}).")
    pl_kl = [(x["key"], x["label"]) for x in M.pl_line_defs(structure)]
    bs_kl = [(k, lab) for k, lab, _ in M.BS_LINES]
    spec = {"act": pl_kl, "bud": pl_kl, "fc": pl_kl, "pri": pl_kl, "actbs": bs_kl, "budbs": bs_kl, "fcbs": bs_kl}
    # find the title row of each block
    titles = {}
    for r in range(5, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if isinstance(v, str):
            for name, prefix in BLOCKS:
                if v.strip().startswith(prefix) and name not in titles:
                    titles[name] = r
    missing = [n for n, _ in BLOCKS if n not in titles]
    if missing:
        raise ImportError_("The Data sheet is missing its standard sections (" + ", ".join(missing) + "). Do not delete or rename the section headings.")
    out = {"months": months, "blocks": {}, "warnings": []}
    for name, _ in BLOCKS:
        kl = spec[name]
        r0 = titles[name] + 1
        cols = {}
        opening = {}
        for j, (key, label) in enumerate(kl):
            r = r0 + j
            got = ws.cell(row=r, column=1).value
            if _norm(got) != _norm(label):
                raise ImportError_(f"Row {r} of the Data sheet reads '{got}' where this pack has '{label}'. Lines cannot be added, removed or renamed in Excel; "
                                   "rename them in the pack's Setup tab and download again.")
            ov = _num(ws.cell(row=r, column=2).value)
            if ov is not None:
                opening[key] = ov
            for i in range(12):
                cell = ws.cell(row=r, column=3 + i).value
                v = _num(cell)
                if v is None and cell not in (None, ""):
                    out["warnings"].append(f"Data sheet {CL_(3 + i)}{r} ({label}, {M.month_label(months[i])}) is not a number and was ignored.")
                if v is not None:
                    cols.setdefault(months[i], {})[key] = v
        out["blocks"][name] = {"months": cols, "opening": opening}
    # reporting month
    wp = wb["Pack"] if "Pack" in wb.sheetnames else None
    n = _num(wp["C9"].value) if wp is not None else None
    out["report_n"] = int(n) if n and 1 <= n <= 12 else None
    out["commentary"] = _read_commentary(wp) if wp is not None else {}
    out["statements"] = _read_statements(wb, d)
    return out



# --------------------------------------------------------------------------- one-page exports

PAGE_HELP = ("Upload the Excel pack (the 'Excel pack' button), or one of the one-page exports that hold figures you can correct: Income statement, "
             "Balance sheet, Actuals, or Budget and forecast. The Cash flow, KPIs, Variances and similar pages are calculated from those, so correct the figures on one of them instead.")
_MONTH_RE = re.compile(r"(Month|Year to date|As at):?\s+([A-Z][a-z]{2} \d{4})")


def _sheet_text(ws, rows=10):
    out = []
    for row in ws.iter_rows(min_row=1, max_row=min(rows, ws.max_row)):
        for c in row:
            if isinstance(c.value, str):
                out.append(c.value)
    return out


def _page_month(ws, d):
    """(view, month key) from the subtitle on a statement page: 'Month: Jun 2026', 'Year to date: Jun 2026' or 'As at Jun 2026'."""
    lab = {M.month_label(m, False): m for m in M.fy_months(d)}
    for t in _sheet_text(ws):
        mt = _MONTH_RE.search(t)
        if mt:
            view = {"Month": "month", "Year to date": "ytd", "As at": "asat"}[mt.group(1)]
            if mt.group(2) not in lab:
                raise ImportError_(f"This page is for {mt.group(2)}, which is outside this pack's financial year ({M.month_label(M.fy_months(d)[0], False)} to "
                                   f"{M.month_label(M.fy_months(d)[-1], False)}). Download it again from this pack.")
            return view, lab[mt.group(2)]
    raise ImportError_("The month this page is for could not be found. Download the page again with 'Excel: this page' and correct the figures without moving the headings.")


def _page_rows(ws, value_col_head="Actual"):
    """Header row, label column and value column of a statement page, and its rows as [(normalised label, value or None)]."""
    hdr = None
    for r in range(1, min(ws.max_row, 20) + 1):
        for c in range(1, ws.max_column + 1):
            if ws.cell(row=r, column=c).value == value_col_head:
                hdr, vcol = r, c
                break
        if hdr:
            break
    if not hdr:
        raise ImportError_(f"The '{value_col_head}' column was not found. Download the page again with 'Excel: this page' and correct the figures without moving the headings.")
    best, lcol = -1, 1
    for c in range(1, vcol):
        n = sum(1 for r in range(hdr + 1, ws.max_row + 1) if isinstance(ws.cell(row=r, column=c).value, str))
        if n > best:
            best, lcol = n, c
    rows = []
    for r in range(hdr + 1, ws.max_row + 1):
        lab = ws.cell(row=r, column=lcol).value
        if not isinstance(lab, str) or not lab.strip():
            continue
        if lab.strip().lower().startswith("values as at export") or "spread evenly" in lab:
            continue
        v = _num(ws.cell(row=r, column=vcol).value)
        rows.append((_norm(lab), v, r))
    return rows


def _sequence(template, rows):
    """Match template [(key, label)] to the sheet rows in order. Returns {key: value} for the lines that have a number."""
    out, j = {}, 0
    for key, label in template:
        nl = _norm(label)
        for k in range(j, len(rows)):
            if rows[k][0] == nl and rows[k][1] is not None:
                out[key] = rows[k][1]
                j = k + 1
                break
    return out


def _baseline(d, meta):
    import cf_ma_excel as X
    raw = X.build_pack_workbook(meta or {"name": "", "client": "", "number": "", "status": "", "currency": "", "prepared_by": "", "fy_label": M.fy_label(d)}, d)
    base = read_workbook(raw, d)
    base["report_n"], base["commentary"], base["statements"] = None, {}, {}
    return base


def _read_page(wb, d, meta):
    names = wb.sheetnames
    if "Pack" in names and "Data" not in names:
        raise ImportError_("There is no 'Data' sheet in this workbook. Do not delete the Data sheet from the Excel pack: it holds the figures that are imported.")
    kind = next((k for k, nm in (("income", "Income statement"), ("balance", "Balance sheet"), ("actuals", "Actuals"), ("plan", "Budget and forecast")) if nm in names), None)
    if kind is None:
        raise ImportError_("There is no 'Data' sheet in this workbook, and it is not one of the pages that can be imported. " + PAGE_HELP)
    structure = M.normalise_structure(d["structure"])
    months = M.fy_months(d)
    base = _baseline(d, meta)
    B = base["blocks"]
    ws = wb[{"income": "Income statement", "balance": "Balance sheet", "actuals": "Actuals", "plan": "Budget and forecast"}[kind]]
    if kind == "income":
        view, m = _page_month(ws, d)
        rows = _page_rows(ws)
        tmpl = [(t["key"], t["label"]) for t in M._rows_template(structure) if t["kind"] in ("indent", "line", "memo") and t["key"] in M.pl_keys(structure)]
        got = _sequence(tmpl, rows)
        if not got:
            raise ImportError_("No income statement lines were found in the Actual column. Download the page again and correct the figures without renaming the lines.")
        i = months.index(m)
        if view == "ytd":
            cum = M.cumulative_actuals(d)
            prev = (cum[i - 1]["cum"] if i > 0 else None) or {}
            vals = {k: v - M.f(prev.get(k)) for k, v in got.items()}
            base["warnings"].append(f"This is the year-to-date view: the figures were read as the cumulative position to {M.month_label(m, False)} and {M.month_label(m, False)} itself was adjusted to match.")
        else:
            vals = dict(got)
        B["act"]["months"].setdefault(m, {}).update(vals)
        # totals typed over on the sheet are not read: say so if they disagree with the lines
        merged = {k: M.f(B["act"]["months"][m].get(k)) for k in M.pl_keys(structure)}
        if view == "ytd":
            merged = {k: M.f((got.get(k) if k in got else (M.cumulative_actuals(d)[i]["cum"] or {}).get(k))) for k in M.pl_keys(structure)}
        calc = M.pl_vector(structure, merged)
        for t in M._rows_template(structure):
            if t["kind"] in ("total", "grand") and t["key"] in ("revenue", "gross_profit", "ebitda", "ebit", "pbt", "pat"):
                shown = next((x[1] for x in rows if x[0] == _norm(t["label"]) and x[1] is not None), None)
                if shown is not None and abs(shown - calc[t["key"]]) > 1.0:
                    base["warnings"].append(f"'{t['label']}' on the sheet is {shown:,.0f} but its lines add up to {calc[t['key']]:,.0f}. Totals are recalculated and not imported; correct the individual lines.")
    elif kind == "balance":
        view, m = _page_month(ws, d)
        rows = _page_rows(ws)
        order = ["ppe", "inventory", "receivables", "other_assets", "cash", "share_capital", "retained", "borrowings", "overdraft", "payables", "other_liabilities"]
        got = _sequence([(k, M.BS_LABEL[k]) for k in order], rows)
        if not got:
            raise ImportError_("No balance sheet lines were found in the Actual column. Download the page again and correct the figures without renaming the lines.")
        B["actbs"]["months"].setdefault(m, {}).update(got)
        calc = M.bs_total({**{k: 0.0 for k in M.BS_KEYS}, **{k: M.f(v) for k, v in B["actbs"]["months"][m].items()}})
        for label, key in (("Total assets", "assets"), ("Total equity", "equity"), ("Total liabilities", "liabilities")):
            shown = next((x[1] for x in rows if x[0] == _norm(label) and x[1] is not None), None)
            if shown is not None and abs(shown - calc[key]) > 1.0:
                base["warnings"].append(f"'{label}' on the sheet is {shown:,.0f} but its lines add up to {calc[key]:,.0f}. Totals are recalculated and not imported; correct the individual lines.")
    else:
        lab = {_norm(M.month_label(m)): m for m in months}
        grids = []
        for r in range(1, ws.max_row + 1):
            if ws.cell(row=r, column=1).value == "Line":
                title = ws.cell(row=r - 1, column=1).value
                grids.append((r, str(title or "")))
        if not grids:
            raise ImportError_("No month-by-month table was found on this page. Download it again with 'Excel: this page'.")
        wanted = {"Income statement by month": "act", "Budget": "bud", "Forecast": "fc"}
        defs = [x for x in M.pl_line_defs(structure) if x["key"] != "div"]
        n_done = 0
        for r0, title in grids:
            blk = wanted.get(title.strip())
            if not blk:
                continue
            cols = {}
            for c in range(2, ws.max_column + 1):
                h = ws.cell(row=r0, column=c).value
                if isinstance(h, str) and _norm(h) in lab:
                    cols[c] = lab[_norm(h)]
            r, j = r0 + 1, 0
            for x in defs:
                got = ws.cell(row=r + j, column=1).value
                if _norm(got) != _norm(x["label"]):
                    raise ImportError_(f"Row {r + j} of the {title} table reads '{got}' where this pack has '{x['label']}'. Lines cannot be added, removed or renamed in Excel.")
                for c, mk in cols.items():
                    v = _num(ws.cell(row=r + j, column=c).value)
                    if v is not None:
                        B[blk]["months"].setdefault(mk, {})[x["key"]] = v
                j += 1
            n_done += 1
        if not n_done:
            raise ImportError_("No Actual, Budget or Forecast table was found on this page.")
    base["warnings"].append("This is a one-page export, so only the figures on this page were read; everything else in the pack is unchanged.")
    return base


def CL_(c):
    from openpyxl.utils import get_column_letter
    return get_column_letter(c)


def _read_commentary(ws):
    """Texts on the Pack sheet: key messages then the commentary sections, by heading in column B."""
    out, cur, buf = {}, None, []
    started = False
    for r in range(11, ws.max_row + 1):
        v = ws.cell(row=r, column=2).value
        if not isinstance(v, str) or not v.strip():
            continue
        t = v.strip()
        if t == "Key messages":
            started, cur, buf = True, "summary", []
            continue
        if not started:
            continue
        if t == "Basis of preparation":
            break
        if t in COMMENT_SECTIONS:
            if cur:
                out[cur] = buf
            cur, buf = COMMENT_SECTIONS[t], []
            continue
        buf.append(t)
    if cur and cur not in out:
        out[cur] = buf
    return {k: "\n".join(v).strip() for k, v in out.items() if v}


_INC_COLS = {"m_a": 3, "y_a": 10, "f_f": 16, "f_b": 17}   # month actual, ytd actual, full-year forecast / budget (layout with variance columns)


def _read_statements(wb, d):
    """Cached values on the Income statement and Balance sheet sheets, to spot cells typed over. {sheet: {label: {col: value}}} or {}."""
    out = {}
    for sheet, cols in (("Income statement", _INC_COLS), ("Balance sheet", {"a": 3, "ffy": 9})):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        rows = {}
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
            lab = row[1].value if len(row) > 1 else None
            if not isinstance(lab, str) or not lab.strip():
                continue
            vals = {}
            for k, c in cols.items():
                if c - 1 < len(row):
                    v = row[c - 1].value
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        vals[k] = float(v)
            if vals:
                rows.setdefault(lab.strip(), vals)
        out[sheet] = rows
    return out


# =========================================================================== planning

def _close(a, b):
    return abs((a or 0.0) - (b or 0.0)) <= TOL


def _vals_equal(x, y, keys):
    return all(_close(M.f((x or {}).get(k)), M.f((y or {}).get(k))) for k in keys)


def _diff_list(label, old, new, keys, labels, limit=40):
    out = []
    for k in keys:
        a, b = M.f((old or {}).get(k)), M.f((new or {}).get(k))
        if not _close(a, b):
            out.append({"where": label, "line": labels.get(k, k), "old": a, "new": b})
    return out[:limit]


def plan_import(d, parsed, filename="", stamp=None, accepted=None):
    """Returns (new_data, report). report = {sections, changes, warnings, corrections, unchanged}.
    `accepted` is the set of correction ids the user kept (None = all of them)."""
    stamp = stamp or {"by": "", "at": ""}
    when = (stamp.get("at") or "")[:10]
    nd = copy.deepcopy(d)
    structure = M.normalise_structure(d["structure"])
    months = M.fy_months(d)
    pl_keys_ = M.pl_keys(structure)
    pl_labels = {x["key"]: x["label"] for x in M.pl_line_defs(structure)}
    bs_labels = M.BS_LABEL
    B = parsed["blocks"]
    changes, sections, warnings = [], [], list(parsed.get("warnings") or [])
    entries = copy.deepcopy(d.get("entries") or {})
    monthly = {m["month"]: m for m in M.monthly_actuals(d)}
    ser = M.bs_series(d)

    # ---- actuals
    new_entries = {}
    n_changed = n_kept = n_added = n_removed = 0
    cumdelta = {k: 0.0 for k in pl_keys_}
    bs_keys_in = [k for k in M.BS_KEYS if k != "retained"]
    for i, m in enumerate(months):
        imp_pl = B["act"]["months"].get(m)
        imp_bs = B["actbs"]["months"].get(m)
        orig = entries.get(m)
        orig_pl = (monthly.get(m) or {}).get("values")
        orig_bs = ser.get(m)
        has_imp = bool(imp_pl) or bool(imp_bs)
        label_m = M.month_label(m)
        # this month's change in its own result, carried into the cumulative position of later trial balance entries
        if imp_pl is not None and imp_pl != {} and orig_pl is not None:
            for k in pl_keys_:
                cumdelta[k] += M.f(imp_pl.get(k)) - M.f(orig_pl.get(k))
        elif imp_pl and orig_pl is None:
            for k in pl_keys_:
                cumdelta[k] += M.f(imp_pl.get(k))
        elif not imp_pl and orig_pl is not None and (has_imp or orig):
            for k in pl_keys_:
                cumdelta[k] -= M.f(orig_pl.get(k))
        if not has_imp:
            if orig:
                n_removed += 1
                changes.append({"where": f"Actuals {label_m}", "line": "(whole month)", "old": None, "new": None, "note": "removed: the month is empty in the workbook"})
            continue
        pl_same = (not imp_pl and orig_pl is None) or (bool(imp_pl) and orig_pl is not None and _vals_equal(imp_pl, orig_pl, pl_keys_))
        if orig is None:
            if imp_pl and orig_pl is not None and pl_same and not imp_bs:
                continue            # an estimated month the user did not touch stays an estimate
            if orig_pl is not None and not pl_same:
                warnings.append(f"{label_m} was an estimate (no figures captured) and you changed it; it is stored as a manual month.")
        has_orig_bs = bool(orig and orig.get("bs"))
        bs_same = (not imp_bs and not has_orig_bs) or (bool(imp_bs) and orig_bs is not None and has_orig_bs and _vals_equal(imp_bs, orig_bs, bs_keys_in))
        ret_imp = (imp_bs or {}).get("retained")
        ret_orig = (orig_bs or {}).get("retained")
        ret_touched = ret_imp is not None and not _close(ret_imp, ret_orig)
        any_shift = any(abs(v) > TOL for v in cumdelta.values())
        is_ytd = bool(orig) and orig.get("kind") == "ytd"
        if orig is not None and pl_same and bs_same and not ret_touched and not (is_ytd and any_shift):
            new_entries[m] = copy.deepcopy(orig)
            n_kept += 1
            continue
        # changed, new, or a trial balance month whose cumulative position moved because of earlier corrections
        if is_ytd:
            e = copy.deepcopy(orig)
            e["pl"] = {k: M.f((orig.get("pl") or {}).get(k)) + cumdelta[k] for k in pl_keys_}
            tag = f"Cumulative figures adjusted for corrections made in Excel ({filename}, {when})"
        else:
            keep_src = orig.get("source") if (orig and pl_same and orig.get("source") == "modelled") else "manual"
            e = {"kind": "month", "source": keep_src, "pl": {k: (imp_pl or {}).get(k, 0.0) for k in pl_keys_}}
            if orig and orig.get("note") and pl_same:
                e["note"] = orig["note"]
            tag = f"From the corrected Excel pack {filename} ({when})"
        e["note"] = ((e.get("note") or "").rstrip(". ") + (". " if e.get("note") else "") + tag).strip()
        e["saved"] = stamp
        if imp_bs:
            if bs_same and not ret_touched and orig is not None and orig.get("bs"):
                e["bs"] = copy.deepcopy(orig["bs"])
            else:
                bs = {k: imp_bs.get(k) for k in M.BS_KEYS}
                if not ret_touched and not (orig and (orig.get("bs") or {}).get("retained") not in (None, "")):
                    bs["retained"] = None      # let it roll forward from the corrected profit
                e["bs"] = bs
        elif orig and orig.get("bs"):
            e["bs"] = copy.deepcopy(orig["bs"])
        if orig is None:
            n_added += 1
            changes.append({"where": f"Actuals {label_m}", "line": "(new month)", "old": None, "new": None, "note": "added"})
        else:
            n_changed += 1
            if not pl_same:
                changes += _diff_list(f"Actuals {label_m}", orig_pl, imp_pl, pl_keys_, pl_labels)
            else:
                changes.append({"where": f"Actuals {label_m}", "line": "(cumulative)", "old": None, "new": None,
                                "note": "figures unchanged; the year-to-date position moves because of earlier corrections"})
            if imp_bs and orig_bs and not (bs_same and not ret_touched):
                changes += _diff_list(f"Balance sheet {label_m}", orig_bs, imp_bs, bs_keys_in + (["retained"] if ret_touched else []), bs_labels)
        new_entries[m] = e
    nd["entries"] = new_entries
    if n_changed or n_added or n_removed:
        sections.append(f"Actuals: {n_changed} month(s) changed, {n_added} added, {n_removed} removed, {n_kept} unchanged.")
    else:
        sections.append("Actuals: no changes.")

    # ---- budget and forecast
    for name, pl_blk, bs_blk, title in (("budget", "bud", "budbs", "Budget"), ("forecast", "fc", "fcbs", "Forecast")):
        orig_pl = M.budget_months(d, name)
        orig_bs = M.budget_bs(d, name)
        orig_open = M.budget_opening_bs(d, name)
        imp_pl, imp_bs, imp_open = B[pl_blk]["months"], B[bs_blk]["months"], B[bs_blk]["opening"]
        diffs = []
        for m in months:
            a, b = orig_pl.get(m), imp_pl.get(m)
            if (a is None) != (not b):
                diffs.append({"where": f"{title} {M.month_label(m)}", "line": "(whole month)", "old": None, "new": None,
                              "note": "added" if b else "removed"})
            elif b:
                diffs += _diff_list(f"{title} {M.month_label(m)}", a, b, pl_keys_, pl_labels, 6)
        bs_diff = False
        for m in months:
            a, b = orig_bs.get(m), imp_bs.get(m)
            if (a is None) != (not b) or (b and not _vals_equal(a, b, M.BS_KEYS)):
                bs_diff = True
        open_diff = (bool(orig_open) != bool(imp_open)) or (bool(imp_open) and not _vals_equal(orig_open, imp_open, M.BS_KEYS))
        if diffs or bs_diff or open_diff:
            ds = copy.deepcopy(d.get(name) or {"source": "typed", "pl": {}, "bs": {}})
            ds["pl"] = {m: {k: imp_pl[m].get(k, 0.0) for k in pl_keys_} for m in months if imp_pl.get(m)}
            ds["bs"] = {m: {k: imp_bs[m].get(k, 0.0) for k in M.BS_KEYS} for m in months if imp_bs.get(m)}
            if imp_open:
                ds["opening_bs"] = {k: imp_open.get(k, 0.0) for k in M.BS_KEYS}
            else:
                ds.pop("opening_bs", None)
            ds["edited"] = True
            ds["saved"] = stamp
            ds["note"] = (ds.get("note") or "") + f" Corrected in Excel ({filename}, {when})."
            nd[name] = ds
            changes += diffs
            if bs_diff or open_diff:
                changes.append({"where": f"{title} balance sheet", "line": "(figures)", "old": None, "new": None, "note": "changed"})
            sections.append(f"{title}: updated from the workbook.")
        else:
            sections.append(f"{title}: no changes.")

    # ---- prior year
    pri_pl = B["pri"]["opening"]
    pri_bs = B["actbs"]["opening"]
    op = d.get("prior") or {}
    old_pl, old_bs = M.prior_pl(d) or {}, M.prior_bs(d) or {}
    pdiff = _diff_list("Prior year", old_pl, pri_pl, pl_keys_, pl_labels) if (pri_pl or old_pl) else []
    bdiff = _diff_list("Prior year balance sheet", old_bs, pri_bs, M.BS_KEYS, bs_labels) if (pri_bs or old_bs) else []
    if pdiff or bdiff:
        p = copy.deepcopy(op) if op else {"label": M.prior_fy_label(d), "source": "manual"}
        if pri_pl:
            p["pl"] = {k: pri_pl.get(k, 0.0) for k in pl_keys_}
        if pri_bs:
            p["bs"] = {k: pri_bs.get(k, 0.0) for k in M.BS_KEYS}
        p["saved"] = stamp
        p["note"] = ((p.get("note") or "") + f" Corrected in Excel ({filename}, {when}).").strip()
        nd["prior"] = p
        changes += pdiff + bdiff
        sections.append("Prior year: updated from the workbook.")
    else:
        sections.append("Prior year: no changes.")

    # ---- reporting month and commentary
    n = parsed.get("report_n")
    if n and months[n - 1] != M.report_month(d):
        nd.setdefault("settings", {})["report_month"] = months[n - 1]
        sections.append(f"Reporting month: {M.month_label(M.report_month(d), False)} to {M.month_label(months[n - 1], False)}.")
    com_old = dict(d.get("commentary") or {})
    com_new = dict(com_old)
    auto = "\n".join(M.key_messages(d, "")).strip()
    n_com = 0
    for key, text in (parsed.get("commentary") or {}).items():
        cur = (com_old.get(key) or "").strip()
        if key == "summary" and not cur and text.strip() == auto:
            continue
        if text.strip() != cur:
            com_new[key] = text.strip()
            n_com += 1
    if n_com:
        nd["commentary"] = com_new
        sections.append(f"Commentary: {n_com} section(s) updated.")

    # ---- knock-on corrections: put right what the adjustments themselves broke
    corrections = []
    try:
        corrections = _auto_corrections(d, nd, months, structure, accepted, stamp)
    except Exception:
        corrections = []

    # ---- checks on the result: report only balance sheets that are out and were not out by the same amount before
    try:
        ser2, ser_old = M.bs_series(nd), M.bs_series(d)
        for m in months:
            if m in ser2:
                t = M.bs_total(ser2[m])["check"]
                t0 = M.bs_total(ser_old[m])["check"] if m in ser_old else 0.0
                if abs(t) > 0.5 and not ser2[m].get("_plug") and abs(t - t0) > 0.5:
                    warnings.append(f"The balance sheet for {M.month_label(m, False)} is out by {t:,.0f} after the import"
                                    + (f" (it was out by {t0:,.0f} before)" if abs(t0) > 0.5 else "") + ". After applying, use 'Find out why' on the Actuals tab.")
    except Exception:
        pass
    warnings += _overtype_warnings(nd, parsed, months)
    report_changed = any(x.startswith("Reporting month") for x in sections)
    unchanged = not (changes or n_com or report_changed or n_changed or n_added or n_removed or any(x.endswith("updated from the workbook.") for x in sections))
    if corrections:
        applied = [c for c in corrections if c["applied"]]
        sections.append(f"Knock-on corrections: {len(applied)} balance sheet line(s) corrected automatically" + (f", {len(corrections) - len(applied)} left as you chose." if len(applied) != len(corrections) else "."))
        unchanged = False if applied else unchanged
    return nd, {"sections": sections, "changes": changes, "warnings": warnings, "corrections": corrections, "unchanged": bool(unchanged)}


def _overtype_warnings(nd, parsed, months):
    """Cells on the statement sheets whose cached value disagrees with what the Data sheet gives (typed over in Excel)."""
    st = parsed.get("statements") or {}
    if not st:
        return []
    n = parsed.get("report_n")
    check = copy.deepcopy(nd)
    if n:
        check.setdefault("settings", {})["report_month"] = months[n - 1]
    out = []
    try:
        inc = {r["label"].strip(): r for r in M.income_rows(check)["rows"]}
        bal = {r["label"].strip(): r for r in M.balance_rows(check)["rows"]}
    except Exception:
        return []
    def cmp(sheet, rows, getters, tol=0.51):
        for lab, vals in (st.get(sheet) or {}).items():
            r = rows.get(lab)
            if r is None:
                continue
            for k, g in getters.items():
                if k in vals:
                    try:
                        exp = g(r)
                    except Exception:
                        exp = None
                    if exp is not None and abs(vals[k] - exp) > tol and r.get("kind") != "pct":
                        out.append(f"{sheet}: '{lab}' shows {vals[k]:,.0f} in the workbook but the Data sheet gives {exp:,.0f}.")
                        break
    cmp("Income statement", inc, {"m_a": lambda r: r["month"]["a"], "y_a": lambda r: r["ytd"]["a"], "f_f": lambda r: r["fy"]["f"], "f_b": lambda r: r["fy"]["b"]})
    cmp("Balance sheet", bal, {"a": lambda r: r["a"], "ffy": lambda r: r["ffy"]})
    if out:
        out = out[:8] + ([f"...and {len(out) - 8} more."] if len(out) > 8 else [])
        out.append("Figures typed over on the statement sheets cannot be imported. Make the correction on the Data sheet (blue cells), save, and upload again.")
    return out


# =========================================================================== applying

def clean_parsed(obj):
    """Rebuild a parsed dict from untrusted JSON (the hidden field on the review page): known keys, floats only."""
    if not isinstance(obj, dict) or not isinstance(obj.get("blocks"), dict):
        raise ImportError_("The upload could not be read back. Please upload the file again.")
    out = {"blocks": {}, "warnings": [str(w)[:300] for w in (obj.get("warnings") or [])][:30], "report_n": None, "commentary": {}, "statements": {}}
    for name, _ in BLOCKS:
        b = obj["blocks"].get(name) or {}
        mons = {}
        for m, vals in (b.get("months") or {}).items():
            if re.fullmatch(r"\d{4}-\d{2}", str(m)) and isinstance(vals, dict):
                mons[str(m)] = {str(k)[:40]: float(v) for k, v in vals.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        opening = {str(k)[:40]: float(v) for k, v in (b.get("opening") or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        out["blocks"][name] = {"months": mons, "opening": opening}
    n = obj.get("report_n")
    out["report_n"] = int(n) if isinstance(n, (int, float)) and 1 <= n <= 12 else None
    out["commentary"] = {k: str(v)[:6000] for k, v in (obj.get("commentary") or {}).items() if k in COMMENT_SECTIONS.values() or k == "summary"}
    out["statements"] = {}
    for sheet, rows in (obj.get("statements") or {}).items():
        if sheet in ("Income statement", "Balance sheet") and isinstance(rows, dict):
            out["statements"][sheet] = {str(l)[:120]: {str(k)[:10]: float(v) for k, v in vals.items() if isinstance(v, (int, float))}
                                        for l, vals in list(rows.items())[:200] if isinstance(vals, dict)}
    return out


def stamp_now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- knock-on corrections

# where the other side of a change in each profit-and-loss line is recorded on the balance sheet, and on which side that line sits
_COUNTER = {"income": ("receivables", "A"), "expense": ("payables", "L"), "dep": ("ppe", "A"), "tax": ("other_liabilities", "L"),
            "div": ("cash", "A"), "int_inc": ("receivables", "A")}
_WHY = {"receivables": "assumes the income is not yet collected", "payables": "assumes the cost is not yet paid",
        "ppe": "depreciation reduces the carrying value of fixed assets", "other_liabilities": "tax is owed until it is paid",
        "cash": "dividends are paid out of cash"}


def _counter_for(structure, key):
    if key in ("dep", "tax", "div", "int_inc"):
        return _COUNTER[key]
    income = {r["key"] for r in structure["revenue"]} | {"oth_inc"}
    return _COUNTER["income"] if key in income else _COUNTER["expense"]


def _auto_corrections(d, nd, months, structure, accepted, stamp):
    """Find balance sheets that the import left inconsistent with the corrected profit and put the other side right.

    When profit in an earlier or the same month is corrected in Excel, equity should move by the same amount in every later balance
    sheet, and so should whatever the profit is owed in (income raises receivables unless it was collected, costs raise payables unless
    they were paid, depreciation lowers fixed assets, tax raises amounts owed, dividends lower cash). Months whose retained earnings are
    the roll-forward follow automatically, so only the other side is added; months with typed retained earnings (for example from a trial
    balance) are moved too. A month whose retained earnings you typed over yourself is left alone, and balance sheet lines you edited are never overwritten. Each correction is listed with its reason and
    can be declined. Anything still out afterwards is only reported, never plugged. Applies to nd in place; returns the corrections."""
    old_entries = d.get("entries") or {}
    cum_old, cum_new = M.cumulative_actuals(d), M.cumulative_actuals(nd)
    keys = M.pl_keys(structure)
    inc_like = {r["key"] for r in structure["revenue"]} | {"oth_inc", "int_inc"}
    bs_in = [k for k in M.BS_KEYS if k != "retained"]
    out = []
    for i, m in enumerate(months):
        e = (nd.get("entries") or {}).get(m)
        oe = old_entries.get(m)
        if not e or not e.get("bs") or not oe or not oe.get("bs"):
            continue
        co, cn = cum_old[i]["cum"], cum_new[i]["cum"]
        if co is None or cn is None:
            continue
        explicit = e["bs"].get("retained") not in (None, "")
        if explicit and not _close(M.f(e["bs"].get("retained")), M.f(oe["bs"].get("retained"))):
            continue
        eff = {}
        for k in keys:
            delta = M.f(cn.get(k)) - M.f(co.get(k))
            if abs(delta) >= 0.005:
                eff[k] = delta if k in inc_like else -delta        # effect on equity of this line's change
        total = sum(eff.values())
        if not eff or abs(total) <= 0.5:
            continue
        ser_now, ser_old = M.bs_series(nd), M.bs_series(d)
        if m not in ser_now or m not in ser_old:
            continue
        scale = 1.0
        if not explicit:                                          # retained earnings follow the roll-forward: add only what it already moved by
            d_ret = ser_now[m]["retained"] - ser_old[m]["retained"]
            scale = d_ret / total
            if abs(d_ret) <= 0.5 or scale < 0 or scale > 1.0001:
                continue
        per_line = {}
        for k, v in eff.items():
            line, side = _counter_for(structure, k)
            per_line[line] = per_line.get(line, 0.0) + (v if side == "A" else -v) * scale
        if explicit:
            old_r = M.f(e["bs"].get("retained"))
            ok = accepted is None or f"{m}:retained" in accepted
            out.append({"id": f"{m}:retained", "month": m, "month_label": M.month_label(m, False), "line": "retained", "label": M.BS_LABEL["retained"],
                        "old": old_r, "new": old_r + total, "applied": bool(ok),
                        "reason": f"{M.month_label(m, False)}: the corrected profit to date is {total:,.0f} different, so retained earnings move by the same amount."})
            if ok:
                e["bs"]["retained"] = old_r + total
        else:
            ok = True
        for line, amt in per_line.items():
            if abs(amt) < 0.5:
                continue
            cid = f"{m}:{line}"
            old_v = M.f(e["bs"].get(line))
            ok_l = (accepted is None or cid in accepted)
            out.append({"id": cid, "month": m, "month_label": M.month_label(m, False), "line": line, "label": M.BS_LABEL.get(line, line),
                        "old": old_v, "new": old_v + amt, "applied": bool(ok_l),
                        "reason": (f"{M.month_label(m, False)}: equity moves with the corrected profit, so {M.BS_LABEL.get(line, line).lower()} moves by "
                                   f"{amt:,.0f} to keep the balance sheet balanced ({_WHY.get(line, '')}). Decline it if the cash has moved instead.")})
            if ok_l:
                e["bs"][line] = old_v + amt
                e["note"] = ((e.get("note") or "") + (" " if e.get("note") else "")
                             + f"Balance sheet corrected on Excel import: {M.BS_LABEL.get(line, line).lower()} {amt:+,.0f} to follow the corrected profit.").strip()
        if explicit and ok:
            e["note"] = ((e.get("note") or "") + (" " if e.get("note") else "") + "Retained earnings moved with the corrected profit on Excel import.").strip()
    # anything else the import broke that a single, high-confidence fix puts right
    ser_old, ser_new = M.bs_series(d), M.bs_series(nd)
    cum_new = M.cumulative_actuals(nd)
    sides = {k: side for k, _, side in M.BS_LINES}
    for i, m in enumerate(months):
        e = (nd.get("entries") or {}).get(m)
        if not e or not e.get("bs") or m not in ser_new or m not in ser_old:
            continue
        gap_new = M.bs_total(ser_new[m])["check"]
        gap_old = M.bs_total(ser_old[m])["check"]
        if abs(gap_new) <= 0.5 or abs(gap_new - gap_old) <= 0.5:
            continue
        cum = cum_new[i]["cum"]
        pat = None
        if cum:
            pat = M.pl_vector(structure, cum)["pat"] if e.get("kind") == "ytd" else None
            if pat is None:
                vals = M.monthly_actuals(nd)[i]["values"]
                pat = M.pl_vector(structure, vals)["pat"] if vals else None
        diag = RC.diagnose_pack_bs({k: v for k, v in ser_new[m].items() if k in M.BS_KEYS}, M.BS_LABEL, sides, pat=pat)
        sug = next((x for x in diag["suggestions"] if x["confidence"] == "high" and x["cleared"] and not x.get("advisory")), None)
        if not sug:
            continue
        cid = f"{m}:{sug['kind']}"
        ok = accepted is None or cid in accepted
        for c in sug["changes"]:
            out.append({"id": cid, "month": m, "month_label": M.month_label(m, False), "line": c["key"], "label": c["label"], "old": c["old"], "new": c["new"],
                        "applied": bool(ok), "reason": f"{M.month_label(m, False)}: {sug['title']}. {sug['detail']}"})
            if ok:
                e["bs"][c["key"]] = c["new"]
        if ok:
            e["note"] = ((e.get("note") or "") + (" " if e.get("note") else "") + f"Balance sheet corrected on Excel import: {sug['title']}.").strip()
    return out
