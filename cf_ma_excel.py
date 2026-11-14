"""Excel management accounts pack with LIVE formulas.

Sheets: Pack (cover, reporting-month selector, commentary) | Income statement | Balance sheet | Cash flow | KPIs | Variances | Data.
The Data sheet holds every input (monthly actuals, budget, forecast, prior year; blue on yellow). The three statements are built
from it with formulas in the firm's layout: LAST MONTH | line | CURRENT MONTH | YEAR TO DATE | FULL YEAR. Change the reporting month
number on the Pack sheet, or any figure on the Data sheet, and the statements recalculate. KPIs and Variances are values as at export.
Structural choices (the lines, which datasets exist, the full-year forecast basis) are fixed when the workbook is exported.
"""
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as CL
from openpyxl.worksheet.datavalidation import DataValidation

import brand_docs as brand
import cf_ma as M

NUM = '#,##0;(#,##0);"-"'
PCT = '0.0%;(0.0%);"-"'
NAVY, GOLD, LINE, TINT, MUTED = brand.NAVY, brand.GOLD, brand.LINE, brand.TINT, brand.MUTED
F_INPUT = Font(color="0000FF")
FILL_INPUT = PatternFill(start_color="FFF9E0", end_color="FFF9E0", fill_type="solid")
FILL_BAND = PatternFill(start_color="D9DDE3", end_color="D9DDE3", fill_type="solid")
FILL_SUB = PatternFill(start_color="ECEEF2", end_color="ECEEF2", fill_type="solid")
FILL_SEC = PatternFill(start_color=TINT, end_color=TINT, fill_type="solid")
THIN = Side(style="thin", color=LINE)
TOP = Border(top=Side(style="thin", color=NAVY))
GREEN, RED = "1B7A3D", "B3261E"

IDX = "Pack!$C$9"           # the reporting month number (1-12)
MN = "Data!$C$3:$N$3"       # month numbers 1..12 on the Data sheet
DATA = "Data"


def _q(sheet):
    return f"'{sheet}'" if " " in sheet else sheet


# =========================================================================== the Data sheet

class DataSheet:
    """Writes the input blocks and remembers where every row lives."""

    def __init__(self, wb, d):
        self.ws = wb.create_sheet(DATA)
        self.d = d
        self.row = {}      # (block, key) -> row number
        self.flag = {}     # block -> row of the "captured" flag
        ws = self.ws
        ws["A1"] = "Data - every input of the pack"
        ws["A1"].font = Font(bold=True, size=14, color=NAVY)
        ws["A2"] = "Blue cells on yellow are inputs. Income statement figures are each month's own amounts; balance sheet figures are closing balances."
        ws["A2"].font = Font(italic=True, color=MUTED)
        ws["A3"], ws["B3"] = "Month number", "Opening / annual"
        ws["A4"] = "Month"
        months = M.fy_months(d)
        for i, m in enumerate(months):
            ws.cell(row=3, column=3 + i, value=i + 1)
            ws.cell(row=4, column=3 + i, value=M.month_label(m))
        for c in range(1, 15):
            for r in (3, 4):
                cell = ws.cell(row=r, column=c)
                cell.font, cell.fill = Font(bold=True), FILL_SUB
                cell.alignment = Alignment(horizontal="right" if c > 1 else "left")
        ws.column_dimensions["A"].width = 44
        for c in range(2, 15):
            ws.column_dimensions[CL(c)].width = 13
        ws.freeze_panes = "C5"
        self.next = 6

    def _inp(self, cell, v):
        if v is None:
            return
        cell.value = float(v)
        cell.font, cell.fill, cell.number_format = F_INPUT, FILL_INPUT, NUM
        cell.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    def block(self, name, title, keys_labels, months_vals, opening=None, flag=False):
        """months_vals: list of 12 dicts-or-None; opening: dict or None (column B)."""
        ws = self.ws
        r = self.next
        for c in range(1, 15):
            ws.cell(row=r, column=c).fill = FILL_SEC
        ws.cell(row=r, column=1, value=title).font = Font(bold=True, color=NAVY)
        r += 1
        first = r
        for key, label in keys_labels:
            ws.cell(row=r, column=1, value=label)
            self.row[(name, key)] = r
            if opening is not None:
                self._inp(ws.cell(row=r, column=2), opening.get(key))
            for i, vals in enumerate(months_vals):
                if vals is not None and vals.get(key) is not None:
                    self._inp(ws.cell(row=r, column=3 + i), vals.get(key))
            r += 1
        if flag:
            ws.cell(row=r, column=1, value="Balance sheet captured (1 = yes)").font = Font(italic=True, color=MUTED)
            for c in range(2, 15):
                col = CL(c)
                ws.cell(row=r, column=c, value=f"=IF(COUNT({col}{first}:{col}{r - 1})>0,1,0)").font = Font(italic=True, color=MUTED)
            self.flag[name] = r
            r += 1
        self.next = r + 1

    # ---- references into a block
    def rng(self, name, key, a="C", b="N"):
        r = self.row[(name, key)]
        return f"{DATA}!${a}${r}:${b}${r}"

    def at(self, name, key, mode):
        """Formula text for one value. mode: idx (reporting month), prev (month before), idx1/prev1 (the month before the reporting month),
        open (column B), end (month 12)."""
        r = self.row[(name, key)]
        return {
            "idx": f"INDEX({DATA}!$C${r}:$N${r},{IDX})",
            "prev": f"INDEX({DATA}!$B${r}:$N${r},{IDX})",
            "idx1": f"INDEX({DATA}!$C${r}:$N${r},{IDX}-1)",
            "prev1": f"INDEX({DATA}!$B${r}:$N${r},{IDX}-1)",
            "open": f"{DATA}!$B${r}",
            "end": f"{DATA}!$N${r}",
        }[mode]

    def has(self, name, mode):
        r = self.flag[name]
        return {
            "idx": f"INDEX({DATA}!$C${r}:$N${r},{IDX})",
            "prev": f"INDEX({DATA}!$B${r}:$N${r},{IDX})",
            "idx1": f"INDEX({DATA}!$C${r}:$N${r},{IDX}-1)",
            "prev1": f"INDEX({DATA}!$B${r}:$N${r},{IDX}-1)",
            "open": f"{DATA}!$B${r}",
            "end": f"{DATA}!$N${r}",
        }[mode]


class View:
    """Lets the statement builders say ('act', key) for both income statement and balance sheet lines."""

    def __init__(self, ds):
        self.ds = ds

    def _n(self, name, key):
        return name + "bs" if key in M.BS_KEYS and name in ("act", "bud", "fc") else name

    def at(self, name, key, mode):
        return self.ds.at(self._n(name, key), key, mode)

    def rng(self, name, key, a="C", b="N"):
        return self.ds.rng(self._n(name, key), key, a, b)

    def rowof(self, name, key):
        return self.ds.row[(self._n(name, key), key)]

    def has(self, name, mode):
        return self.ds.has(name + "bs", mode)


# =========================================================================== statement sheet scaffolding

def _statement_sheet(wb, title, meta, specs, subtitle):
    """Create a sheet with the grey band / sub-heading rows; return (ws, first_data_row, col_of)."""
    ws = wb.create_sheet(title)
    ncols = len(specs)
    row = brand.xlsx_title_band(ws, title, [meta.get("client", ""), meta.get("name", ""), subtitle], ncols=max(ncols, 8))
    brand.xlsx_page_setup(ws)
    band, sub = row, row + 1
    col_of = {}
    for i, sp in enumerate(specs, start=1):
        col_of[sp["id"]] = i
        c = ws.cell(row=sub, column=i, value=sp["head"])
        c.font, c.fill = Font(bold=True), FILL_SUB
        c.alignment = Alignment(horizontal="left" if sp["kind"] == "label" else "right", wrap_text=True)
        b = ws.cell(row=band, column=i)
        b.fill = FILL_BAND
        b.font = Font(bold=True, color="1D2733")
        b.alignment = Alignment(horizontal="center")
        ws.column_dimensions[CL(i)].width = 44 if sp["kind"] == "label" else (13 if sp["kind"] == "lm" else 12.5)
    # merged group titles
    i = 0
    while i < ncols:
        g = specs[i].get("group", "")
        j = i
        while j + 1 < ncols and specs[j + 1].get("group", "") == g and specs[i]["kind"] == "val" and specs[j + 1]["kind"] == "val":
            j += 1
        ws.cell(row=band, column=i + 1, value=g)
        if j > i:
            ws.merge_cells(start_row=band, start_column=i + 1, end_row=band, end_column=j + 1)
        i = j + 1
    ws.freeze_panes = ws.cell(row=sub + 1, column=3)
    return ws, sub + 1, col_of


def _put(ws, r, c, formula, fmt=NUM, bold=False, total=False, color=None):
    cell = ws.cell(row=r, column=c, value=formula)
    cell.number_format = fmt
    cell.font = Font(bold=bold or total, color=color) if (bold or total or color) else Font()
    if total:
        cell.border = TOP
    return cell


# =========================================================================== income statement

def _income_sheet(wb, meta, d, ds, V, labels, basis):
    structure = M.normalise_structure(d["structure"])
    mid, ytd, fy = f"CURRENT MONTH", "YEAR TO DATE", f"FULL YEAR - {labels['fy']}"
    K = lambda i, head, group, kind="val": {"id": i, "head": head, "group": group, "kind": kind}
    specs = [K("lm", "Last month", "LAST MONTH", "lm"), K("label", "", "", "label"),
             K("m_a", "Actual", mid), K("m_f", "Forecast", mid), K("m_b", "Budget", mid), K("m_v", "Var", mid), K("m_vp", "Var %", mid),
             K("m_p", labels["prior"], mid), K("m_pv", "Var", mid),
             K("y_a", "Actual", ytd), K("y_b", "Budget", ytd), K("y_v", "Var", ytd), K("y_vp", "Var %", ytd), K("y_p", labels["prior"], ytd), K("y_pv", "Var", ytd),
             K("f_f", "Forecast", fy), K("f_b", "Budget", fy), K("f_v", "Var", fy), K("f_vp", "Var %", fy), K("f_p", labels["prior"], fy), K("f_pv", "Var", fy)]
    ws, r0, C = _statement_sheet(wb, "Income statement", meta, specs, "Live formulas - change the reporting month on the Pack sheet")
    has_b, has_f, has_p = V["has_budget"], V["has_forecast"], V["has_prior"]
    template = M._rows_template(structure)
    rowmap = {}
    r = r0
    group = None
    plan = []   # (row index, template row)
    for t in template:
        g = ("Revenue" if t["income"] else "Operating expenses") if t["kind"] == "indent" else None
        if g and g != group:
            ws.cell(row=r, column=2, value=g).font = Font(bold=True, color=NAVY)
            ws.cell(row=r, column=2).alignment = Alignment(horizontal="center")
            for c in range(1, len(specs) + 1):
                ws.cell(row=r, column=c).fill = FILL_SEC
            r += 1
        group = g
        rowmap[t["key"]] = r
        plan.append((r, t))
        r += 1
    inputs = set(M.pl_keys(structure))
    ids = [s["id"] for s in specs if s["kind"] == "val"] + ["lm"]

    def cell(col_id, key):
        return f"{CL(C[col_id])}{rowmap[key]}"

    def subtotal(col_id, key):
        c = lambda k: cell(col_id, k)
        revs = "+".join(c(x["key"]) for x in structure["revenue"])
        opx = "+".join(c(x["key"]) for x in structure["opex"])
        return {
            "revenue": f"={revs}", "gross_profit": f"={c('revenue')}-{c('cogs')}",
            "opex_total": f"={opx}", "ebitda": f"={c('gross_profit')}+{c('oth_inc')}-{c('opex_total')}",
            "ebit": f"={c('ebitda')}-{c('dep')}", "pbt": f"={c('ebit')}+{c('int_inc')}-{c('int_exp')}", "pat": f"={c('pbt')}-{c('tax')}",
            "gross_margin": f'=IF({c("revenue")}=0,"",{c("gross_profit")}/{c("revenue")})',
            "ebitda_margin": f'=IF({c("revenue")}=0,"",{c("ebitda")}/{c("revenue")})',
            "net_margin": f'=IF({c("revenue")}=0,"",{c("pat")}/{c("revenue")})',
        }[key]

    def line(col_id, key):
        if col_id == "lm":
            return f"=IF({IDX}>1,{ds.at('act', key, 'idx1')},0)"
        if col_id == "m_a":
            return f"={ds.at('act', key, 'idx')}"
        if col_id == "m_f" and has_f:
            return f"={ds.at('fc', key, 'idx')}"
        if col_id == "m_b" and has_b:
            return f"={ds.at('bud', key, 'idx')}"
        if col_id == "m_p" and has_p:
            return f"={ds.at('pri', key, 'open')}/12"
        if col_id == "y_a":
            return f"=SUMPRODUCT(({MN}<={IDX})*{ds.rng('act', key)})"
        if col_id == "y_b" and has_b:
            return f"=SUMPRODUCT(({MN}<={IDX})*{ds.rng('bud', key)})"
        if col_id == "y_p" and has_p:
            return f"={ds.at('pri', key, 'open')}*{IDX}/12"
        if col_id == "f_f" and V["outlook"] is not None:
            ya = f"SUMPRODUCT(({MN}<={IDX})*{ds.rng('act', key)})"
            if basis == "forecast":
                return f"={ya}+SUMPRODUCT(({MN}>{IDX})*{ds.rng('fc', key)})"
            if basis == "budget":
                return f"={ya}+SUMPRODUCT(({MN}>{IDX})*{ds.rng('bud', key)})"
            return f"={ya}*12/{IDX}"
        if col_id == "f_b" and V["budget_fy"] is not None:
            return f"=SUM({ds.rng('bud', key)})"
        if col_id == "f_p" and has_p:
            return f"={ds.at('pri', key, 'open')}"
        return None

    pairs = {"m_v": ("m_a", "m_b"), "m_pv": ("m_a", "m_p"), "y_v": ("y_a", "y_b"), "y_pv": ("y_a", "y_p"), "f_v": ("f_f", "f_b"), "f_pv": ("f_f", "f_p")}
    pct_of = {"m_vp": ("m_v", "m_b"), "y_vp": ("y_v", "y_b"), "f_vp": ("f_v", "f_b")}
    present = {"m_a": True, "m_f": has_f, "m_b": has_b, "m_p": has_p, "y_a": True, "y_b": has_b, "y_p": has_p,
               "f_f": V["outlook"] is not None, "f_b": V["budget_fy"] is not None, "f_p": has_p, "lm": True}
    for r, t in plan:
        k, kind, inc = t["key"], t["kind"], t["income"]
        ws.cell(row=r, column=2, value=("    " if kind == "indent" else "") + t["label"])
        bold = kind in ("total", "grand")
        pct = kind == "pct"
        fmt = PCT if pct else NUM
        for col_id in ids:
            if col_id in pairs:
                a, b = pairs[col_id]
                if present[a] and present[b]:
                    ca, cb = cell(a, k), cell(b, k)
                    f = f"={ca}-{cb}" if (inc or pct) else f"={cb}-{ca}"
                    _put(ws, r, C[col_id], f, fmt, total=bold)
                continue
            if col_id in pct_of:
                v, b = pct_of[col_id]
                a = {"m_vp": "m_a", "y_vp": "y_a", "f_vp": "f_f"}[col_id]
                if not pct and present[b] and present[a]:
                    _put(ws, r, C[col_id], f'=IF({cell(b, k)}=0,"",{cell(v, k)}/ABS({cell(b, k)}))', PCT, total=bold)
                continue
            if not present.get(col_id):
                continue
            f = line(col_id, k) if k in inputs else subtotal(col_id, k)
            if f:
                _put(ws, r, C[col_id], f, fmt, total=bold)
        if kind == "grand":
            for c in range(1, len(specs) + 1):
                ws.cell(row=r, column=c).fill = FILL_SEC
        if kind in ("pct", "memo"):
            for c in range(1, len(specs) + 1):
                ws.cell(row=r, column=c).font = Font(italic=True, color=MUTED)
    # conditional colour: favourable green, adverse red on variance columns
    from openpyxl.formatting.rule import CellIsRule
    for col_id in list(pairs) + list(pct_of):
        col = CL(C[col_id])
        rng = f"{col}{r0}:{col}{r - 1}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["0.0005"], font=Font(color=GREEN)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["-0.0005"], font=Font(color=RED)))
    ws.cell(row=r + 1, column=2, value=f"{meta.get('currency', '')}. Variances: favourable positive, adverse in brackets. Last year is the {labels['prior']} accounts spread evenly across the months."
            ).font = Font(italic=True, color=MUTED, size=9)
    return ws, rowmap, C


# =========================================================================== balance sheet

def _balance_sheet(wb, meta, d, ds, V, labels):
    mid, fy = "CURRENT MONTH", f"FULL YEAR (YEAR END) - {labels['fy']}"
    K = lambda i, head, group, kind="val": {"id": i, "head": head, "group": group, "kind": kind}
    specs = [K("lm", "Last month", "LAST MONTH", "lm"), K("label", "", "", "label"), K("a", "Actual", mid), K("f", "Forecast", mid), K("b", "Budget", mid),
             K("v", "Var", mid), K("py", labels["prior"] + " year end", mid), K("chg", "Change in year", mid),
             K("ffy", "Forecast", fy), K("bfy", "Budget", fy), K("pfy", labels["prior"], fy)]
    ws, r0, C = _statement_sheet(wb, "Balance sheet", meta, specs, "Live formulas - closing balances")
    has_b = bool(M.budget_bs(d))
    has_f = bool(M.forecast_bs(d))
    has_p = V["has_prior"] and bool((d.get("prior") or {}).get("bs"))
    rows = [("ppe", "line"), ("inventory", "indent"), ("receivables", "indent"), ("other_assets", "indent"), ("cash", "indent"), ("assets", "total"),
            ("share_capital", "line"), ("retained", "line"), ("equity", "total"),
            ("borrowings", "line"), ("overdraft", "line"), ("payables", "line"), ("other_liabilities", "line"), ("liabilities", "total"),
            ("el", "grand"), ("check", "memo"), ("wc", "memo"), ("netdebt", "memo")]
    labels_ = {**M.BS_LABEL, "assets": "Total assets", "equity": "Total equity", "liabilities": "Total liabilities", "el": "Total equity and liabilities",
               "check": "Balance check (should be nil)", "wc": "Net working capital (excl. cash and debt)", "netdebt": "Net debt / (net cash)"}
    rowmap = {k: r0 + i for i, (k, _) in enumerate(rows)}
    sources = {"lm": ("act", "prev1"), "a": ("act", "idx"), "f": ("fc", "idx"), "b": ("bud", "idx"), "py": ("act", "open"), "ffy": ("fc", "end"),
               "bfy": ("bud", "end"), "pfy": ("act", "open")}
    present = {"lm": True, "a": True, "f": has_f, "b": has_b, "py": has_p, "ffy": has_f, "bfy": has_b, "pfy": has_p}
    # last month for the first month of the year is the opening (prior year-end) balance sheet
    def ref(col_id, key):
        blk, mode = sources[col_id]
        if col_id == "lm":
            rr = ds.rowof("act", key)
            return f"INDEX({DATA}!$B${rr}:$N${rr},{IDX})"
        return ds.at(blk, key, mode)
    for key, kind in rows:
        r = rowmap[key]
        ws.cell(row=r, column=2, value=("    " if kind == "indent" else "") + labels_[key])
        bold = kind in ("total", "grand")
        for col_id, ok in present.items():
            if not ok:
                continue
            c = lambda k, col_id=col_id: f"{CL(C[col_id])}{rowmap[k]}"
            if key in M.BS_KEYS:
                f = f"={ref(col_id, key)}"
            else:
                f = {
                    "assets": f"={c('ppe')}+{c('inventory')}+{c('receivables')}+{c('other_assets')}+{c('cash')}",
                    "equity": f"={c('share_capital')}+{c('retained')}",
                    "liabilities": f"={c('borrowings')}+{c('overdraft')}+{c('payables')}+{c('other_liabilities')}",
                    "el": f"={c('equity')}+{c('liabilities')}",
                    "check": f"={c('assets')}-{c('el')}",
                    "wc": f"={c('inventory')}+{c('receivables')}+{c('other_assets')}-{c('payables')}-{c('other_liabilities')}",
                    "netdebt": f"={c('borrowings')}+{c('overdraft')}-{c('cash')}",
                }[key]
            _put(ws, r, C[col_id], f, NUM, total=bold)
        if present["a"] and present["b"]:
            _put(ws, r, C["v"], f"={CL(C['a'])}{r}-{CL(C['b'])}{r}", NUM, total=bold)
        if present["py"]:
            _put(ws, r, C["chg"], f"={CL(C['a'])}{r}-{CL(C['py'])}{r}", NUM, total=bold)
        if kind == "grand":
            for cc in range(1, len(specs) + 1):
                ws.cell(row=r, column=cc).fill = FILL_SEC
        if kind == "memo":
            for cc in range(1, len(specs) + 1):
                ws.cell(row=r, column=cc).font = Font(italic=True, color=MUTED)
    ws.cell(row=r0 + len(rows) + 1, column=2, value="Retained earnings include the current year's profit. Forecast and budget balance sheets are shown where they come from a forecast model."
            ).font = Font(italic=True, color=MUTED, size=9)
    return ws


# =========================================================================== cash flow

def _cash_sheet(wb, meta, d, ds, V, labels, inc_rows, inc_C, cfl, basis):
    mid, ytd, fy = "CURRENT MONTH", "YEAR TO DATE", f"FULL YEAR - {labels['fy']}"
    K = lambda i, head, group, kind="val": {"id": i, "head": head, "group": group, "kind": kind}
    specs = [K("lm", "Last month", "LAST MONTH", "lm"), K("label", "", "", "label"), K("a", "Actual", mid), K("f", "Forecast", mid), K("b", "Budget", mid), K("v", "Var", mid),
             K("ya", "Actual", ytd), K("yb", "Budget", ytd), K("yv", "Var", ytd), K("ffy", "Forecast", fy), K("bfy", "Budget", fy)]
    ws, r0, C = _statement_sheet(wb, "Cash flow", meta, specs, "Live formulas - indirect method from consecutive balance sheets")
    ISH = "'Income statement'"

    def pl(col_id, key):
        return f"{ISH}!${CL(inc_C[col_id])}${inc_rows[key]}"

    # each column: (income column id, bs0 (block, mode), bs1 (block, mode), guard)
    defs = {
        "lm": ("lm", ("act", "prev1"), ("act", "idx1"), True),
        "a": ("m_a", ("act", "prev"), ("act", "idx"), True),
        "f": ("m_f", ("fc", "prev"), ("fc", "idx"), bool(M.forecast_bs(d)) and V["has_forecast"]),
        "b": ("m_b", ("bud", "prev"), ("bud", "idx"), bool(M.budget_bs(d)) and V["has_budget"]),
        "ya": ("y_a", ("act", "open"), ("act", "idx"), True),
        "yb": ("y_b", ("bud", "open"), ("bud", "idx"), bool(M.budget_bs(d)) and V["has_budget"]),
        "ffy": ("f_f", ("act", "open"), ("fc", "end"), any(r["ffy"] is not None for r in cfl["rows"])),
        "bfy": ("f_b", ("bud", "open"), ("bud", "end"), any(r["bfy"] is not None for r in cfl["rows"])),
    }
    rows = M.CF_ROWS
    rowmap = {k: r0 + i for i, (k, _, _) in enumerate(rows)}
    for key, label, kind in rows:
        r = rowmap[key]
        ws.cell(row=r, column=2, value=("    " if kind == "indent" else "") + label)
        bold = kind in ("total", "grand")
        for col_id, (icol, b0, b1, ok) in defs.items():
            if not ok:
                continue
            g = lambda k, mode=b1: ds.at(mode[0], k, mode[1])
            d_ = lambda k: f"({ds.at(b1[0], k, b1[1])}-{ds.at(b0[0], k, b0[1])})"
            P = lambda k: pl(icol, k)
            c = lambda k, col_id=col_id: f"{CL(C[col_id])}{rowmap[k]}"
            expr = {
                "ebitda": P("ebitda"),
                "d_inventory": f"-{d_('inventory')}", "d_receivables": f"-{d_('receivables')}", "d_other_assets": f"-{d_('other_assets')}",
                "d_payables": d_("payables"), "d_other_liabilities": d_("other_liabilities"),
                "cash_ops": f"{c('ebitda')}+{c('d_inventory')}+{c('d_receivables')}+{c('d_other_assets')}+{c('d_payables')}+{c('d_other_liabilities')}",
                "interest": f"{P('int_inc')}-{P('int_exp')}", "tax": f"-{P('tax')}",
                "cfo": f"{c('cash_ops')}+{c('interest')}+{c('tax')}",
                "capex": f"-({d_('ppe')}+{P('dep')})", "cfi": c("capex"),
                "loans": d_("borrowings"), "equity_issue": d_("share_capital"), "dividends": f"-{P('div')}",
                "other_equity": f"{d_('retained')}-({P('pat')}-{P('div')})",
                "cff": f"{c('loans')}+{c('equity_issue')}+{c('dividends')}+{c('other_equity')}",
                "net": f"{c('cfo')}+{c('cfi')}+{c('cff')}",
                "open_net_cash": f"{ds.at(b0[0], 'cash', b0[1])}-{ds.at(b0[0], 'overdraft', b0[1])}",
                "close_net_cash": f"{ds.at(b1[0], 'cash', b1[1])}-{ds.at(b1[0], 'overdraft', b1[1])}",
            }[key]
            # both balance sheets must exist, and for the last-month column the reporting month must not be the first
            guard = f"AND({ds.has(b0[0], b0[1])}=1,{ds.has(b1[0], b1[1])}=1" + (f",{IDX}>1" if col_id == "lm" else "") + ")"
            if key in ("ebitda",) or key.startswith("d_") or key in ("interest", "tax", "capex", "loans", "equity_issue", "dividends", "other_equity", "open_net_cash", "close_net_cash"):
                f = f'=IF({guard},{expr},"")'
            else:
                f = f'=IF({guard},{expr},"")'
            _put(ws, r, C[col_id], f, NUM, total=bold)
        for a, b, v in (("a", "b", "v"), ("ya", "yb", "yv")):
            if defs[a][3] and defs[b][3]:
                _put(ws, r, C[v], f'=IF(AND(ISNUMBER({CL(C[a])}{r}),ISNUMBER({CL(C[b])}{r})),{CL(C[a])}{r}-{CL(C[b])}{r},"")', NUM, total=bold)
        if kind == "grand":
            for cc in range(1, len(specs) + 1):
                ws.cell(row=r, column=cc).fill = FILL_SEC
    ws.cell(row=r0 + len(rows) + 1, column=2, value="Cash is shown net of overdraft. A column is blank when either balance sheet it needs has not been captured."
            ).font = Font(italic=True, color=MUTED, size=9)
    return ws


# =========================================================================== static sheets

def _kpi_sheet(wb, meta, d, labels):
    ws = wb.create_sheet("KPIs")
    r = brand.xlsx_title_band(ws, "Key performance indicators", [meta.get("client", ""), meta.get("name", ""), f"Year to date, {labels['month']} (values as at export)"], ncols=6)
    brand.xlsx_page_setup(ws)
    for i, h in enumerate(["Indicator", "Actual", "Budget", labels["prior"], "Status"], start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font, c.fill = Font(bold=True), FILL_SUB
        c.alignment = Alignment(horizontal="left" if i == 1 else "right")
    ws.column_dimensions["A"].width = 44
    for col in "BCDE":
        ws.column_dimensions[col].width = 15
    fm = {"pct": PCT, "x": '0.00"x"', "days": "0", "money": NUM, "months": "0.0"}
    for k in M.kpi_table(d):
        r += 1
        ws.cell(row=r, column=1, value=k["label"])
        for i, key in enumerate(("actual", "budget", "prior"), start=2):
            v = k[key]
            c = ws.cell(row=r, column=i, value=(None if v is None or v == float("inf") else v))
            c.number_format = fm.get(k["fmt"], "General")
        ws.cell(row=r, column=5, value={"green": "On track", "amber": "Watch", "red": "Concern"}.get(k["rag"], "")).alignment = Alignment(horizontal="right")
    return ws


def _variance_sheet(wb, meta, d, labels):
    ws = wb.create_sheet("Variances")
    r = brand.xlsx_title_band(ws, "Variance analysis", [meta.get("client", ""), meta.get("name", ""), "Ranked by year-to-date variance to budget (values as at export)"], ncols=10)
    brand.xlsx_page_setup(ws)
    heads = ["Line", "Month actual", "Month budget", "Var", "YTD actual", "YTD budget", "Var", "Var %", labels["prior"] + " YTD", "Explanation"]
    for i, h in enumerate(heads, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font, c.fill = Font(bold=True), FILL_SUB
        c.alignment = Alignment(horizontal="left" if i in (1, 10) else "right")
    ws.column_dimensions["A"].width = 38
    for col in "BCDEFGHI":
        ws.column_dimensions[col].width = 14
    ws.column_dimensions["J"].width = 60
    notes = d.get("line_notes") or {}
    for x in sorted(M.variance_list(d), key=lambda x: -abs(x["ytd_var"] or 0)):
        r += 1
        vals = [x["label"], x["month_actual"], x["month_budget"], x["month_var"], x["ytd_actual"], x["ytd_budget"], x["ytd_var"], x["ytd_pct"], x["ytd_prior"], notes.get(x["key"], "")]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=i, value=v)
            if 2 <= i <= 9:
                c.number_format = PCT if i == 8 else NUM
    return ws


def _bridge_sheet(wb, meta, d, ex):
    """Variance bridge (values as at export; the end total is a formula so the walk can be checked) and the by-cause split."""
    import cf_ma_var as VM
    b, dr = ex["bridge"], ex["drivers"]
    lab = b["labels"]
    ws = wb.create_sheet("Variance bridge")
    r = brand.xlsx_title_band(ws, "Variance bridge", [meta.get("client", ""), meta.get("name", ""),
                              f"{b['measure_label']}: {lab['comparator']} to {lab['actual']} (values as at export; favourable positive)"], ncols=9)
    brand.xlsx_page_setup(ws)
    for i, h in enumerate(["Step", "Amount", "Running total"], start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font, c.fill = Font(bold=True), FILL_SUB
        c.alignment = Alignment(horizontal="left" if i == 1 else "right")
    ws.column_dimensions["A"].width = 44
    for col in "BCDEFGHI":
        ws.column_dimensions[col].width = 14
    r += 1
    first = r
    ws.cell(row=r, column=1, value=b["start"]["label"]).font = Font(bold=True)
    _put(ws, r, 2, round(b["start"]["value"], 2), bold=True)
    _put(ws, r, 3, f"=B{r}", bold=True)
    for st in b["steps"]:
        r += 1
        ws.cell(row=r, column=1, value=st["label"])
        _put(ws, r, 2, round(st["value"], 2), color=GREEN if st["value"] >= 0 else RED)
        _put(ws, r, 3, f"=C{r - 1}+B{r}")
    last_step = r
    r += 1
    ws.cell(row=r, column=1, value=b["end"]["label"] + " (walk)").font = Font(bold=True)
    _put(ws, r, 2, f"=SUM(B{first}:B{last_step})", total=True)
    _put(ws, r, 3, f"=C{last_step}", total=True)
    r += 1
    ws.cell(row=r, column=1, value=b["end"]["label"] + " (reported)")
    _put(ws, r, 3, round(b["end"]["value"], 2))
    r += 1
    ws.cell(row=r, column=1, value="Difference (should be nil)")
    _put(ws, r, 3, f"=C{r - 2}-C{r - 1}")
    r += 2
    for t in ex["narr"]:
        ws.cell(row=r, column=1, value=t).alignment = Alignment(wrap_text=False)
        r += 1
    if dr:
        r += 1
        cols = [(c, l) for c, l in VM.DRIVER_COLS]
        ws.cell(row=r, column=1, value="Variance by cause").font = Font(bold=True, size=12, color=NAVY)
        r += 1
        for i, h in enumerate(["Line", "Variance", *[l for _, l in cols]], start=1):
            c = ws.cell(row=r, column=i, value=h)
            c.font, c.fill = Font(bold=True), FILL_SUB
            c.alignment = Alignment(horizontal="left" if i == 1 else "right")
        r0 = r + 1
        for row in dr["rows"]:
            if abs(row["total"]) < 0.5:
                continue
            r += 1
            ws.cell(row=r, column=1, value=row["label"])
            _put(ws, r, 2, round(row["total"], 2))
            for j, (c, _) in enumerate(cols, start=3):
                _put(ws, r, j, round(row[c], 2))
        r += 1
        ws.cell(row=r, column=1, value="Profit after tax").font = Font(bold=True)
        for j in range(2, 3 + len(cols)):
            _put(ws, r, j, f"=SUM({CL(j)}{r0}:{CL(j)}{r - 1})", total=True)
    ws.freeze_panes = "A5"
    return ws


def _scenario_sheet(wb, meta, d, ex, labels):
    sc = ex["scen"]
    ws = wb.create_sheet("Scenarios")
    r = brand.xlsx_title_band(ws, "Reforecast scenarios", [meta.get("client", ""), meta.get("name", ""),
                              f"Full year {labels['fy']}: actuals to {labels['month']} plus each scenario for the remaining months (values as at export)"], ncols=8)
    brand.xlsx_page_setup(ws)
    head = ["", "Pack outlook", *[c["name"] for c in sc["cols"]], "Budget"]
    for i, h in enumerate(head, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font, c.fill = Font(bold=True), FILL_SUB
        c.alignment = Alignment(horizontal="left" if i == 1 else "right", wrap_text=True)
        ws.column_dimensions[CL(i)].width = 38 if i == 1 else 16
    for t in sc["table"]:
        r += 1
        ws.cell(row=r, column=1, value=t["label"])
        _put(ws, r, 2, None if t["current"] is None else round(t["current"], 2))
        for j, c in enumerate(sc["cols"], start=3):
            v = c["fy"].get(t["key"])
            _put(ws, r, j, None if v is None else round(v, 2))
        _put(ws, r, 3 + len(sc["cols"]), None if t["budget"] is None else round(t["budget"], 2))
    r += 1
    ws.cell(row=r, column=1, value="Year-end cash (net of overdraft)")
    for j, c in enumerate(sc["cols"], start=3):
        v = (c.get("cash") or {}).get("year_end")
        _put(ws, r, j, None if v is None else round(v, 2))
    ws.freeze_panes = "B5"
    return ws


def _pack_sheet(wb, meta, d, labels, V, status, basis_lines, msgs):
    ws = wb["Pack"]
    r = brand.xlsx_title_band(ws, "Management accounts", [meta.get("client", ""), meta.get("name", ""), meta.get("currency", "")], ncols=8)
    brand.xlsx_page_setup(ws)
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 36
    ws.column_dimensions["C"].width = 34
    ws.column_dimensions["D"].width = 60
    facts = [("Client", meta.get("client")), ("Pack", f"{meta.get('number')} - {meta.get('name')}"), ("Financial year", labels["fy"]),
             ("Status", meta.get("status")), ("Prepared by", meta.get("prepared_by") or "-"), ("Prepared", date.today().strftime("%d %B %Y"))]
    assert r <= 9, "title band moved"   # row 9 must hold the reporting month number (IDX = Pack!$C$9)
    ws.cell(row=9, column=2, value="Reporting month number (1-12)").font = Font(bold=True, color=NAVY)
    c = ws.cell(row=9, column=3, value=labels["n"])
    c.font, c.fill = F_INPUT, FILL_INPUT
    c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    dv = DataValidation(type="whole", operator="between", formula1="1", formula2="12", showErrorMessage=True, error="Enter a month number from 1 to 12")
    ws.add_data_validation(dv)
    dv.add(c)
    ws.cell(row=9, column=4, value="Change this to roll the statements to another month").font = Font(italic=True, color=MUTED)
    ws.cell(row=10, column=2, value="Reporting month").font = Font(bold=True, color=NAVY)
    ws.cell(row=10, column=3, value=f"=INDEX({DATA}!$C$4:$N$4,{IDX})")
    for i, (k, v) in enumerate(facts):
        ws.cell(row=12 + i, column=2, value=k).font = Font(bold=True, color=NAVY)
        ws.cell(row=12 + i, column=3, value=v)
    row = 12 + len(facts) + 1
    ws.cell(row=row, column=2, value="Key messages").font = Font(bold=True, size=12, color=NAVY)
    com = d.get("commentary") or {}
    texts = [com["summary"]] if com.get("summary") else msgs
    for t in texts:
        row += 1
        ws.cell(row=row, column=2, value=t).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        ws.row_dimensions[row].height = max(15, 15 * (len(t) // 110 + 1))
    for key, lab in (("income", "Income statement"), ("balance", "Balance sheet"), ("cash", "Cash flow and funding"), ("outlook", "Full-year outlook"), ("actions", "Actions and recommendations")):
        if com.get(key):
            row += 2
            ws.cell(row=row, column=2, value=lab).font = Font(bold=True, color=NAVY)
            row += 1
            ws.cell(row=row, column=2, value=com[key]).alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
            ws.row_dimensions[row].height = max(15, 15 * (len(com[key]) // 110 + 1))
    row += 2
    ws.cell(row=row, column=2, value="Basis of preparation").font = Font(bold=True, size=12, color=NAVY)
    for b in basis_lines:
        row += 1
        ws.cell(row=row, column=2, value=b).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        ws.row_dimensions[row].height = max(15, 15 * (len(b) // 110 + 1))


# =========================================================================== entry point

# --------------------------------------------------------------------------- loan facilities (live formulas)

DATEFMT = "mmm-yy"


def _mdate(key):
    return date(int(key[:4]), int(key[5:7]), 1)


def _ddate(fac):
    """The facility's drawdown date; the 1st of the month when only a month was given."""
    d = fac.get("draw_date") or ""
    return date(int(d[:4]), int(d[5:7]), int(d[8:])) if d else _mdate(fac["draw_month"])


def _rdate(text):
    """A YYYY-MM-DD string as a date."""
    return date(int(text[:4]), int(text[5:7]), int(text[8:10]))


def _loan_sheets(wb, meta, d):
    """'Loan facilities' (the year month by month) and 'Loan schedules' (every facility from drawdown to final repayment).
    The schedules hold the inputs (blue) and the formulas; the year view adds them up by month with SUMIFS. Returns the first sheet or None."""
    import cf_ma_loans as FL
    facs = [x for x in FL.facilities_of(d) if x["include"] and x["amount"] > 0]
    if not facs:
        return None
    months = M.fy_months(d)
    ridx = M.report_index(d)
    cur = meta.get("currency", "")
    ws = wb.create_sheet("Loan facilities")
    wl = wb.create_sheet("Loan schedules")

    # ---- schedules: one block per facility
    brand.xlsx_title_band(wl, "Loan schedules", [meta.get("client", ""), meta.get("name", ""),
                          "Every facility from drawdown to final repayment. Blue cells are inputs; change them and the year view recalculates."], ncols=9)
    brand.xlsx_page_setup(wl)
    for col, w in zip("ABCDEFGHI", (40, 14, 14, 14, 14, 14, 14, 14, 4)):
        wl.column_dimensions[col].width = w
    r = 6
    blocks = []
    for fac in facs:
        lab = FL.label_of(fac)
        wl.cell(row=r, column=1, value=f"{lab} - facility {fac['id']}").font = Font(bold=True, size=12, color=NAVY)
        rows = {}
        tr = FL.tranches(fac)
        nrows = len(FL.schedule(fac))
        items = (("rate", "Interest rate (% a year)", fac["rate"], "0.00"),
                 ("feepct", "Establishment fee (% of each amount drawn)", fac["fee_pct"], "0.00"), ("feefix", "Establishment fee (fixed amount, with the first drawdown)", fac["fee_fixed"], NUM),
                 ("firstrep", "First repayment date (instalments fall at each month end from that month)", _rdate(FL.repay_start(fac)[:10]), "dd mmm yyyy"),
                 ("ninst", "Number of instalments (a bullet has one)", FL.instalments_of(fac), "0"),
                 ("type", "Repayment (level / straight / bullet / custom)", fac["repay"], "@"))
        rr = r + 1
        for key, label, val, fmt in items:
            wl.cell(row=rr, column=1, value=label)
            c = wl.cell(row=rr, column=2, value=val)
            c.number_format, c.font, c.fill = fmt, F_INPUT, FILL_INPUT
            rows[key] = f"$B${rr}"
            rr += 1
        # the drawdowns: a date and an amount each (two spare lines to add more); the share of the month outstanding is worked out per line
        for j, h in enumerate(("Drawdowns", "Date", "Amount", "Share of month outstanding"), start=1):
            c = wl.cell(row=rr, column=j, value=h)
            c.font, c.fill = Font(bold=True), FILL_SUB
            c.alignment = Alignment(horizontal="left" if j == 1 else "right")
        t0 = rr + 1
        lines = [(x["date"] and _rdate(x["date"]) or _mdate(x["month"]), x["amount"]) for x in tr] or [(_mdate(fac["draw_month"]), 0.0)]
        n_lines = len(lines) + 2
        for k in range(n_lines):
            x = t0 + k
            wl.cell(row=x, column=1, value=f"   Drawdown {k + 1}")
            dc = wl.cell(row=x, column=2, value=lines[k][0] if k < len(lines) else None)
            dc.number_format, dc.font, dc.fill = "dd mmm yyyy", F_INPUT, FILL_INPUT
            ac = wl.cell(row=x, column=3, value=lines[k][1] if k < len(lines) else 0)
            ac.number_format, ac.font, ac.fill = NUM, F_INPUT, FILL_INPUT
            wl.cell(row=x, column=4, value=f'=IF(B{x}="",0,(DAY(EOMONTH(B{x},0))-DAY(B{x})+1)/DAY(EOMONTH(B{x},0)))').number_format = "0.0000"
        t1 = t0 + n_lines - 1
        d_rng, a_rng, f_rng = f"$B${t0}:$B${t1}", f"$C${t0}:$C${t1}", f"$D${t0}:$D${t1}"
        rr = t1 + 1
        rows["tr"] = (d_rng, a_rng, f_rng)
        custom = fac["repay"] == "custom"
        if custom:
            # repayments on set dates: a date and a % (or an amount) each, two spare lines; the amount scheduled is worked out per line
            wl.cell(row=rr, column=1, value="")
            for j, h in enumerate(("Repayments (capital, paid at the end of the month of the date)", "Date", "% of facility", "Amount (used instead of %)", "Scheduled"), start=1):
                c = wl.cell(row=rr, column=j, value=h)
                c.font, c.fill = Font(bold=True), FILL_SUB
                c.alignment = Alignment(horizontal="left" if j == 1 else "right")
            reps = [x for x in fac["repays"] if x["month"]]
            u0 = rr + 1
            nrp = len(reps) + 2
            u1 = u0 + nrp - 1
            amt_addr = f"$B${u1 + 1}"                       # the 'Amount (all drawdowns)' row that follows the table
            for k in range(nrp):
                x = u0 + k
                wl.cell(row=x, column=1, value=f"   Repayment {k + 1}")
                rx = reps[k] if k < len(reps) else None
                dc = wl.cell(row=x, column=2, value=(_rdate(rx["date"]) if rx and rx["date"] else (_mdate(rx["month"]) if rx else None)))
                dc.number_format, dc.font, dc.fill = "dd mmm yyyy", F_INPUT, FILL_INPUT
                pc = wl.cell(row=x, column=3, value=(rx["pct"] or None) if rx else None)
                pc.number_format, pc.font, pc.fill = "0.00", F_INPUT, FILL_INPUT
                ac = wl.cell(row=x, column=4, value=(rx["amount"] or None) if rx else None)
                ac.number_format, ac.font, ac.fill = NUM, F_INPUT, FILL_INPUT
                wl.cell(row=x, column=5, value=f'=IF(B{x}="",0,IF(N(D{x})>0,D{x},N(C{x})/100*{amt_addr}))').number_format = NUM
            rd_rng, rs_rng = f"$B${u0}:$B${u1}", f"$E${u0}:$E${u1}"
            for key, formula in (("firstrep", f"=MIN({rd_rng})"),
                                 ("ninst", f"=(YEAR(MAX({rd_rng}))-YEAR(MIN({rd_rng})))*12+MONTH(MAX({rd_rng}))-MONTH(MIN({rd_rng}))+1")):
                cc = wl[rows[key].replace("$", "")]
                cc.value = formula
                cc.font, cc.fill = Font(color="000000"), PatternFill(fill_type=None)
                cc.number_format = "dd mmm yyyy" if key == "firstrep" else "0"
            wl.cell(row=int(rows["ninst"].replace("$", "")[1:]), column=1, value="Months from the first to the last repayment (calculated)")
            wl.cell(row=int(rows["firstrep"].replace("$", "")[1:]), column=1, value="First repayment date (calculated from the repayments below)")
            rr = u1 + 1
        calc = (("amt", "Amount (all drawdowns)", f"=SUM({a_rng})", NUM),
                ("draw", "First drawdown date", f"=MIN({d_rng})", "dd mmm yyyy"),
                ("fee", "Establishment fee in total (% of each drawdown + the fixed fee)", f"=$B${rr}*{rows['feepct']}/100+{rows['feefix']}", NUM),
                ("mr", "Monthly interest rate", f"={rows['rate']}/100/12", "0.0000%"),
                ("gap", "Interest-only months (first drawdown month to the first repayment month)",
                 f'=IF({rows["type"]}="bullet",0,MAX(0,(YEAR({rows["firstrep"]})-YEAR($B${rr + 1}))*12+MONTH({rows["firstrep"]})-MONTH($B${rr + 1})))', "0"))
        for key, label, formula, fmt in calc:
            wl.cell(row=rr, column=1, value=label)
            c = wl.cell(row=rr, column=2, value=formula)
            c.number_format = fmt
            rows[key] = f"$B${rr}"
            rr += 1
        more = (("grace", "Capital grace (months)", f"={rows['gap']}", "0"),
                ("term", "Term (months, drawdown to last repayment)",
                 f'=IF({rows["type"]}="bullet",MAX(0,(YEAR({rows["firstrep"]})-YEAR({rows["draw"]}))*12+MONTH({rows["firstrep"]})-MONTH({rows["draw"]}))+1,{rows["gap"]}+{rows["ninst"]})', "0"),
                ("k", "Number of capital instalments", None, "0"),
                ("nrows", "Months in this schedule", nrows, "0"))
        for key, label, formula, fmt in more:
            wl.cell(row=rr, column=1, value=label)
            if key == "k":
                formula = f"=MAX(1,{rows['term']}-{rows['grace']})"
            c = wl.cell(row=rr, column=2, value=formula)
            c.number_format = fmt
            rows[key] = f"$B${rr}"
            rr += 1
        wl.cell(row=rr, column=1, value="Equal monthly instalment when repayments start ('level' only; worked out again when more is drawn later)")
        c = wl.cell(row=rr, column=2, value=(f'=IF({rows["type"]}="level",IF({rows["mr"]}=0,{rows["amt"]}/{rows["k"]},{rows["amt"]}*{rows["mr"]}/(1-(1+{rows["mr"]})^(-{rows["k"]}))),0)'))
        c.number_format = NUM
        rows["pmt"] = f"$B${rr}"
        rr += 2
        hdr = rr
        for j, h in enumerate(("No.", "Month", "Opening", "Drawdown", "Interest", "Establishment fee", "Capital repaid", "Closing", "Instalment"), start=1):
            c = wl.cell(row=hdr, column=j, value=h)
            c.font, c.fill = Font(bold=True), FILL_SUB
            c.alignment = Alignment(horizontal="left" if j == 1 else "right")
        first = hdr + 1
        in_month = lambda x: f"({d_rng}>=B{x})*({d_rng}<EDATE(B{x},1))"
        for n in range(1, nrows + 1):
            x = first + n - 1
            wl.cell(row=x, column=1, value=n)
            wl.cell(row=x, column=2, value=f"=EDATE(DATE(YEAR({rows['draw']}),MONTH({rows['draw']}),1),A{x}-1)").number_format = DATEFMT
            wl.cell(row=x, column=3, value=0 if n == 1 else f"=H{x - 1}").number_format = NUM
            wl.cell(row=x, column=4, value=f'=SUMPRODUCT({a_rng}*{in_month(x)})').number_format = NUM
            wl.cell(row=x, column=5, value=f"=(C{x}+SUMPRODUCT({a_rng}*{f_rng}*{in_month(x)}))*{rows['mr']}").number_format = NUM
            wl.cell(row=x, column=6, value=f"=D{x}*{rows['feepct']}/100" + (f"+{rows['feefix']}" if n == 1 else "")).number_format = NUM
            # the instalment is set when repayments start and again whenever more is drawn after that
            left = f"({rows['term']}-A{x}+1)"
            ann = f'IF({rows["type"]}="level",IF({rows["mr"]}=0,(C{x}+D{x})/{left},(C{x}+D{x})*{rows["mr"]}/(1-(1+{rows["mr"]})^(-{left}))),(C{x}+D{x})/{left})'
            prev = "0" if n == 1 else f"I{x - 1}"
            wl.cell(row=x, column=9, value=f'=IF(AND(A{x}>{rows["grace"]},{left}>0,OR(A{x}={rows["grace"]}+1,D{x}>0)),{ann},{prev})').number_format = NUM
            cust = (f'IF({rows["type"]}="custom",MIN(C{x}+D{x},SUMPRODUCT({rs_rng}*({rd_rng}>=B{x})*({rd_rng}<EDATE(B{x},1)))),' if custom else "")
            tail = ")" if custom else ""
            wl.cell(row=x, column=7, value=(f'=IF(A{x}<={rows["grace"]},0,IF(OR(A{x}={rows["term"]},A{x}={rows["nrows"]}),C{x}+D{x},{cust}IF({rows["type"]}="bullet",0,'
                                            f'IF({rows["type"]}="straight",MIN(C{x}+D{x},I{x}),MIN(C{x}+D{x},MAX(0,I{x}-E{x})))){tail})')).number_format = NUM
            wl.cell(row=x, column=8, value=f"=C{x}+D{x}-G{x}").number_format = NUM
        last = first + nrows - 1
        tot = last + 1
        wl.cell(row=tot, column=1, value="Total over the life").font = Font(bold=True)
        for j, col in ((4, "D"), (5, "E"), (6, "F"), (7, "G")):
            c = wl.cell(row=tot, column=j, value=f"=SUM({col}{first}:{col}{last})")
            c.number_format, c.font, c.border = NUM, Font(bold=True), TOP
        blocks.append({"fac": fac, "label": lab, "first": first, "last": last})
        r = tot + 3

    # ---- the year view
    ncols = len(months) + 2
    brand.xlsx_title_band(ws, "Loan facilities", [meta.get("client", ""), meta.get("name", ""),
                          f"{M.fy_label(d)} month by month - {cur}. Actual months are a check on the books; forecast months are what the forecast model carries."], ncols=max(ncols, 8))
    brand.xlsx_page_setup(ws)
    ws.column_dimensions["A"].width = 40
    for k in range(2, ncols + 1):
        ws.column_dimensions[CL(k)].width = 12.5
    hr = 6
    for j, mk in enumerate(months, start=2):
        c = ws.cell(row=hr, column=j, value="Actual" if j - 2 <= ridx else "Forecast")
        c.font, c.fill, c.alignment = Font(bold=True), FILL_BAND, Alignment(horizontal="center")
        c = ws.cell(row=hr + 1, column=j, value=_mdate(mk))
        c.number_format, c.font, c.fill, c.alignment = DATEFMT, Font(bold=True), FILL_SUB, Alignment(horizontal="right")
    ws.cell(row=hr + 1, column=1, value=cur).font = Font(bold=True)
    ws.cell(row=hr + 1, column=1).fill = FILL_SUB
    c = ws.cell(row=hr + 1, column=ncols, value=M.fy_label(d))
    c.font, c.fill, c.alignment = Font(bold=True), FILL_SUB, Alignment(horizontal="right")
    r = hr + 2
    total_rows = {k: [] for k in ("open", "draw", "int", "fee", "rep", "close")}
    cols = (("open", "Opening balance", "C"), ("draw", "Drawdown", "D"), ("int", "Interest", "E"), ("fee", "Establishment fee", "F"),
            ("rep", "Capital repaid", "G"), ("close", "Closing balance", "H"))
    for b in blocks:
        ws.cell(row=r, column=1, value=f"{b['label']} - {b['fac']['amount']:,.0f} at {b['fac']['rate']:g}%").font = Font(bold=True, color=NAVY)
        r += 1
        for key, label, col in cols:
            ws.cell(row=r, column=1, value=label).font = Font(bold=(key == "close"))
            rng_m = f"'Loan schedules'!$B${b['first']}:$B${b['last']}"
            rng_v = f"'Loan schedules'!${col}${b['first']}:${col}${b['last']}"
            for j in range(2, len(months) + 2):
                c = ws.cell(row=r, column=j, value=f"=SUMIFS({rng_v},{rng_m},{CL(j)}${hr + 1})")
                c.number_format = NUM
                if key == "close":
                    c.font, c.border = Font(bold=True), TOP
            if key == "open":
                yf = f"=B{r}"
            elif key == "close":
                yf = f"={CL(len(months) + 1)}{r}"
            else:
                yf = f"=SUM(B{r}:{CL(len(months) + 1)}{r})"
            c = ws.cell(row=r, column=ncols, value=yf)
            c.number_format, c.font = NUM, Font(bold=True)
            total_rows[key].append(r)
            r += 1
        r += 1
    if len(blocks) > 1:
        ws.cell(row=r, column=1, value="All facilities").font = Font(bold=True, color=NAVY)
        r += 1
        for key, label, col in cols:
            ws.cell(row=r, column=1, value=label).font = Font(bold=(key == "close"))
            for j in range(2, ncols + 1):
                c = ws.cell(row=r, column=j, value="=" + "+".join(f"{CL(j)}{x}" for x in total_rows[key]))
                c.number_format = NUM
                if key == "close" or j == ncols:
                    c.font = Font(bold=True)
            r += 1
    ws.freeze_panes = ws.cell(row=hr + 2, column=2)
    return ws



def _loan_analysis_sheet(wb, meta, d):
    """'Loan analysis': the facilities against the income statement, balance sheet, cash flow and KPIs (values as at export)."""
    import cf_ma_loans as FL
    an = FL.statement_analysis(d)
    if not an:
        return None
    ws = wb.create_sheet("Loan analysis")
    brand.xlsx_title_band(ws, "Loan facilities in the statements", [meta.get("client", ""), meta.get("name", ""), "Values as at export."], ncols=6)
    brand.xlsx_page_setup(ws)
    ws.column_dimensions["A"].width = 62
    for col in "BCD":
        ws.column_dimensions[col].width = 16
    r = 6
    for key in ("income", "balance", "cashflow", "kpis"):
        t = an[key]
        ws.cell(row=r, column=1, value=t["title"]).font = Font(bold=True, size=12, color=NAVY)
        r += 1
        for j, h in enumerate([""] + list(t["cols"]), start=1):
            c = ws.cell(row=r, column=j, value=h)
            c.font, c.fill = Font(bold=True), FILL_SUB
            c.alignment = Alignment(horizontal="left" if j == 1 else "right")
        for row in t["rows"]:
            r += 1
            c = ws.cell(row=r, column=1, value=("   " if row["kind"] == "indent" else "") + row["label"])
            c.font = Font(bold=row["kind"] == "total", italic=row["kind"] == "memo")
            for j, v in enumerate(row["vals"], start=2):
                cell = ws.cell(row=r, column=j, value=None if v is None else round(v, 4))
                cell.number_format = '0.00"x"' if row["fmt"] == "x" else NUM
                cell.font = Font(bold=row["kind"] == "total", italic=row["kind"] == "memo")
        r += 1
        ws.cell(row=r, column=1, value=t["note"]).font = Font(italic=True, color=MUTED)
        r += 3
    return ws


def build_pack_workbook(meta, d):
    from cf_ma_pdf import _basis_lines
    structure = M.normalise_structure(d["structure"])
    months = M.fy_months(d)
    ridx = M.report_index(d)
    labels = {"month": M.month_label(months[ridx], False), "fy": M.fy_label(d), "prior": M.prior_fy_label(d), "n": ridx + 1}
    V = M.vectors(d)
    status = M.data_status(d)
    wb = Workbook()
    wb.active.title = "Pack"
    ds = DataSheet(wb, d)
    pl_kl = [(x["key"], x["label"]) for x in M.pl_line_defs(structure)]
    bs_kl = [(k, lab) for k, lab, _ in M.BS_LINES]
    mon = M.monthly_actuals(d)
    ds.block("act", "Actual - income statement (the month's own figures)", pl_kl, [m["values"] for m in mon])
    bud, fc = M.budget_months(d), M.forecast_months(d)
    ds.block("bud", "Budget - income statement", pl_kl, [bud.get(m) for m in months])
    ds.block("fc", "Forecast - income statement", pl_kl, [fc.get(m) for m in months])
    ds.block("pri", "Prior year - income statement (full year, column B)", pl_kl, [None] * 12, opening=M.prior_pl(d))
    pb = M.prior_bs(d)
    ser, bser, fser = M.bs_series(d), M.budget_bs(d), M.forecast_bs(d)
    ds.block("actbs", "Actual - balance sheet at month end (column B = prior year end)", bs_kl, [ser.get(m) for m in months], opening=pb, flag=True)
    ds.block("budbs", "Budget - balance sheet at month end (column B = opening)", bs_kl, [bser.get(m) for m in months],
             opening=M.budget_opening_bs(d) or pb if bser else None, flag=True)
    ds.block("fcbs", "Forecast - balance sheet at month end (column B = opening)", bs_kl, [fser.get(m) for m in months],
             opening=M.budget_opening_bs(d, "forecast") or pb if fser else None, flag=True)
    view = View(ds)

    basis = "forecast" if (V["outlook_basis"] or "").endswith("latest forecast for the remaining months") else (
        "budget" if "budget for the remaining months" in (V["outlook_basis"] or "") else "run")
    ws_i, inc_rows, inc_C = _income_sheet(wb, meta, d, view, V, labels, basis)
    _balance_sheet(wb, meta, d, view, V, labels)
    cfl = M.cashflow_rows(d)
    _cash_sheet(wb, meta, d, view, V, labels, inc_rows, inc_C, cfl, basis)
    _kpi_sheet(wb, meta, d, labels)
    if V["has_budget"]:
        _variance_sheet(wb, meta, d, labels)
    try:
        import cf_ma_var as VM
        ex = VM.export_sections(d, "")
    except Exception:
        ex = {"bridge": None, "scen": None}
    if V["actual_ytd"] is not None and ex["bridge"]:
        _bridge_sheet(wb, meta, d, ex)
    if V["actual_ytd"] is not None and ex.get("scen"):
        _scenario_sheet(wb, meta, d, ex, labels)
    _pack_sheet(wb, meta, d, labels, V, status, _basis_lines(d, V, status), M.key_messages(d, ""))
    _loan_sheets(wb, meta, d)
    _loan_analysis_sheet(wb, meta, d)
    order = ["Pack", "Income statement", "Balance sheet", "Cash flow", "KPIs", "Variances", "Variance bridge", "Scenarios", "Loan facilities", "Loan analysis", "Loan schedules", DATA]
    wb._sheets = [wb[n] for n in order if n in wb.sheetnames]
    for ws in wb.worksheets:
        ws.sheet_properties.tabColor = NAVY if ws.title == "Pack" else ("0000FF" if ws.title == DATA else GOLD)
    wb.properties.creator = "Neverlank Chartered Accountants"
    wb.properties.title = meta.get("name") or "Management accounts"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
