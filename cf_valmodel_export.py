"""Valuation Model - the Excel workbook (live formulas), the PDF report and the Word report.

The Excel workbook rebuilds the consolidation in formulas: change a value, an ownership %, a discount or an exchange rate and the sum of the parts, the
holding items, the group adjustments and the stake recalculate. The test-suite recalculates it in LibreOffice and compares every figure with
cf_valmodel.consolidate. The PDF and Word reports carry the same figures on the firm letterhead, with the Managing Partner's signature when signed.
"""
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as CL
from openpyxl.worksheet.datavalidation import DataValidation

import brand_docs as brand
import cf_valmodel as V

NUM = '#,##0;(#,##0);"-"'
NUM4 = '#,##0.0000'
NAVY, GOLD, LINE, TINT, MUTED = brand.NAVY, brand.GOLD, brand.LINE, brand.TINT, brand.MUTED
F_INPUT = Font(color="0000FF")
FILL_INPUT = PatternFill(start_color="FFF9E0", end_color="FFF9E0", fill_type="solid")
FILL_HEAD = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
FILL_SEC = PatternFill(start_color="E9EEF5", end_color="E9EEF5", fill_type="solid")
FILL_TOT = PatternFill(start_color=TINT, end_color=TINT, fill_type="solid")
THIN = Side(style="thin", color=LINE)
TOP = Border(top=Side(style="thin", color=NAVY))
SPARE_COMP = 8
SPARE_HOLD = 5
SPARE_FX = 5


def _inp(cell, value, fmt=None):
    cell.value = value
    cell.font, cell.fill = F_INPUT, FILL_INPUT
    if fmt:
        cell.number_format = fmt
    cell.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _head(ws, row, values, start_col=1):
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=start_col + i, value=v)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), FILL_HEAD
        c.alignment = Alignment(horizontal="center" if i else "left", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32


def _section(ws, row, text, ncols):
    for c in range(1, ncols + 1):
        ws.cell(row=row, column=c).fill = FILL_SEC
    ws.cell(row=row, column=1, value=text).font = Font(bold=True, color=NAVY)


def _setw(ws, widths):
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _subtitle(vm):
    return [vm.client.name, vm.name, f"{vm.vm_number} - {vm.currency}"]


# =========================================================================== Excel

def build_excel(vm):
    d = V.normalise(vm.data, vm.currency)
    res = V.consolidate(d, vm.currency, vm.valuation_date)
    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = "Summary"
    ws_set = wb.create_sheet("Settings")
    ws_c = wb.create_sheet("Components")
    ws_h = wb.create_sheet("Holding items")
    ws_g = wb.create_sheet("Going concern")

    # ---------------------------------------------------------------- Settings: group inputs + FX
    brand.xlsx_title_band(ws_set, "Settings, exchange rates and group adjustments", _subtitle(vm) + ["Blue cells on yellow are inputs"], ncols=6)
    brand.xlsx_page_setup(ws_set)
    _setw(ws_set, {"A": 44, "B": 18, "C": 16, "D": 16})
    r = 8
    _section(ws_set, r, "Group adjustments", 4)
    r += 1
    g = d["group"]
    S = {}
    for label, key, val, fmt in (("Control premium (%)", "ctl", g["control_pct"], "0.0"), ("Minority discount (%)", "min", g["minority_pct"], "0.0"),
                                 ("Discount for lack of marketability, DLOM (%)", "dlom", g["dlom_pct"], "0.0"),
                                 ("Stake valued (%)", "stake", g["stake_pct"], "0.0"), ("Shares in issue (optional)", "shares", g["shares"], "#,##0"),
                                 ("Include going-concern value (1 = yes, 0 = no)", "gcon", 1 if d["goingconcern"]["enabled"] else 0, "0")):
        ws_set.cell(row=r, column=1, value=label)
        _inp(ws_set.cell(row=r, column=2), val, fmt)
        S[key] = f"Settings!$B${r}"
        r += 1
    r += 1
    _section(ws_set, r, f"Exchange rates (1 unit of the currency = ? {vm.currency})", 4)
    r += 1
    _head(ws_set, r, ["Currency", f"Rate to {vm.currency}"])
    r += 1
    fx_first = r
    codes = [vm.currency] + V.currencies_needed(d, vm.currency)
    for code in codes:
        ws_set.cell(row=r, column=1, value=code)
        if code == vm.currency:
            ws_set.cell(row=r, column=2, value=1).number_format = NUM4
        else:
            _inp(ws_set.cell(row=r, column=2), d["fx"].get(code), NUM4)
        r += 1
    for _ in range(SPARE_FX):
        _inp(ws_set.cell(row=r, column=1), None)
        _inp(ws_set.cell(row=r, column=2), None, NUM4)
        r += 1
    fx_last = r - 1
    FXC, FXR = f"Settings!$A${fx_first}:$A${fx_last}", f"Settings!$B${fx_first}:$B${fx_last}"

    # ---------------------------------------------------------------- Components
    ncol = 24
    brand.xlsx_title_band(ws_c, "Component valuations", _subtitle(vm) + ["Include = 1 to count a component; values in the component's own currency"], ncols=ncol)
    brand.xlsx_page_setup(ws_c)
    heads = ["Include (1/0)", "Component", "Type", "Method", "Currency", "Basis (equity / enterprise)", "Valuation date", "Low", "Adopted", "High", "Net debt",
             "Ownership %", "Minority discount %", "DLOM %", "Control premium %", "Rate to " + vm.currency, "Low (used)", "Adopted (used)", "High (used)",
             f"Low ({vm.currency})", f"Adopted ({vm.currency})", f"High ({vm.currency})", "% of total", "Source"]
    hr = 8
    _head(ws_c, hr, heads)
    _setw(ws_c, {"A": 9, "B": 34, "C": 22, "D": 24, "E": 9, "F": 14, "G": 13, "H": 15, "I": 15, "J": 15, "K": 13, "L": 11, "M": 11, "N": 9, "O": 11,
                 "P": 11, "Q": 15, "R": 15, "S": 15, "T": 16, "U": 16, "V": 16, "W": 9, "X": 40})
    ws_c.freeze_panes = ws_c.cell(row=hr + 1, column=3)
    first = hr + 1
    comps = d["components"]
    nrows = len(comps) + SPARE_COMP
    last = first + nrows - 1
    for i in range(nrows):
        rr = first + i
        c = comps[i] if i < len(comps) else None
        vals = [(1 if c["include"] else 0) if c else None, c["name"] if c else None, c["type"] if c else None, c["method"] if c else None,
                c["currency"] if c else None, c["basis"] if c else None, V.parse_date(c["date"]) if c and c["date"] else None,
                c["low"] if c else None, c["adopted"] if c else None, c["high"] if c else None, c["net_debt"] if c and c["net_debt"] else None,
                c["ownership_pct"] if c else None, c["minority_pct"] if c else None, c["dlom_pct"] if c else None, c["control_pct"] if c else None]
        fmts = [None, None, None, None, None, None, "dd/mm/yyyy", NUM, NUM, NUM, NUM, "0.0", "0.0", "0.0", "0.0"]
        for ci, (v, fm) in enumerate(zip(vals, fmts), start=1):
            _inp(ws_c.cell(row=rr, column=ci), v, fm)
        R = rr
        ws_c.cell(row=R, column=16, value=f'=IFERROR(INDEX({FXR},MATCH($E{R},{FXC},0)),"n/a")').number_format = NUM4
        ws_c.cell(row=R, column=18, value=f'=IF(I{R}<>"",I{R},IF(AND(H{R}<>"",J{R}<>""),(H{R}+J{R})/2,IF(H{R}<>"",H{R},IF(J{R}<>"",J{R},0))))')
        ws_c.cell(row=R, column=17, value=f'=IF(H{R}<>"",H{R},R{R})')
        ws_c.cell(row=R, column=19, value=f'=IF(J{R}<>"",J{R},R{R})')
        for k, src in ((20, "Q"), (21, "R"), (22, "S")):
            ws_c.cell(row=R, column=k, value=(f'=IF(AND($A{R}=1,ISNUMBER($P{R}),COUNT($H{R}:$J{R})>0),({src}{R}-IF($F{R}="enterprise",$K{R},0))*$P{R}*$L{R}/100'
                                              f'*(1+$O{R}/100)*(1-$M{R}/100)*(1-$N{R}/100),0)'))
        for k in (17, 18, 19, 20, 21, 22):
            ws_c.cell(row=R, column=k).number_format = NUM
        ws_c.cell(row=R, column=23, value=f'=IF($U${last + 1}<>0,U{R}/$U${last + 1},0)').number_format = "0.0%"
        ws_c.cell(row=R, column=24, value=(c["source"]["label"] or "typed in") if c else None)
    tot = last + 1
    ws_c.cell(row=tot, column=2, value="Sum of the parts").font = Font(bold=True)
    for k in (20, 21, 22):
        cell = ws_c.cell(row=tot, column=k, value=f"=SUM({CL(k)}{first}:{CL(k)}{last})")
        cell.number_format, cell.font, cell.fill, cell.border = NUM, Font(bold=True), FILL_TOT, TOP
    ws_c.cell(row=tot, column=23, value=f"=SUM(W{first}:W{last})").number_format = "0.0%"
    dv_b = DataValidation(type="list", formula1='"equity,enterprise"', allow_blank=True)
    dv_i = DataValidation(type="list", formula1='"0,1"', allow_blank=True)
    ws_c.add_data_validation(dv_b)
    ws_c.add_data_validation(dv_i)
    dv_b.add(f"F{first}:F{last}")
    dv_i.add(f"A{first}:A{last}")
    PARTS = {"low": f"Components!$T${tot}", "adopted": f"Components!$U${tot}", "high": f"Components!$V${tot}"}

    # ---------------------------------------------------------------- Holding items
    brand.xlsx_title_band(ws_h, "Holding-company items and eliminations", _subtitle(vm) + ["Enter amounts as positives; the treatment decides the sign"], ncols=15)
    brand.xlsx_page_setup(ws_h)
    _head(ws_h, 8, ["Item", "Treatment (add / deduct / eliminate / capcost)", "Currency", "Low", "Adopted", "High", "Annual cost", "Multiple", "Rate",
                    "Low (used)", "Adopted (used)", "High (used)", f"Effect: low", "Effect: adopted", "Effect: high"])
    _setw(ws_h, {"A": 38, "B": 24, "C": 10, "D": 15, "E": 15, "F": 15, "G": 14, "H": 10, "I": 10, "J": 14, "K": 14, "L": 14, "M": 15, "N": 15, "O": 15})
    hfirst = 9
    items = d["holding"]
    hn = len(items) + SPARE_HOLD
    hlast = hfirst + hn - 1
    for i in range(hn):
        R = hfirst + i
        h = items[i] if i < len(items) else None
        vals = [h["label"] if h else None, h["kind"] if h else None, (h["currency"] or vm.currency) if h else None, h["low"] if h else None,
                h["adopted"] if h else None, h["high"] if h else None, h["annual"] if h and h["annual"] else None, h["multiple"] if h and h["multiple"] else None]
        for ci, (v, fm) in enumerate(zip(vals, [None, None, None, NUM, NUM, NUM, NUM, "0.0"]), start=1):
            _inp(ws_h.cell(row=R, column=ci), v, fm)
        ws_h.cell(row=R, column=9, value=f'=IF($C{R}="",1,IFERROR(INDEX({FXR},MATCH($C{R},{FXC},0)),"n/a"))').number_format = NUM4
        ws_h.cell(row=R, column=11, value=f'=IF(E{R}<>"",E{R},IF(AND(D{R}<>"",F{R}<>""),(D{R}+F{R})/2,IF(D{R}<>"",D{R},IF(F{R}<>"",F{R},0))))')
        ws_h.cell(row=R, column=10, value=f'=IF(D{R}<>"",D{R},K{R})')
        ws_h.cell(row=R, column=12, value=f'=IF(F{R}<>"",F{R},K{R})')
        for k, src in ((13, "J"), (14, "K"), (15, "L")):
            ws_h.cell(row=R, column=k, value=(f'=IF(ISNUMBER($I{R}),IF($B{R}="capcost",-$G{R}*$H{R}*$I{R},IF($B{R}="add",1,-1)*{src}{R}*$I{R}),0)'))
        for k in (10, 11, 12, 13, 14, 15):
            ws_h.cell(row=R, column=k).number_format = NUM
    dv_k = DataValidation(type="list", formula1='"add,deduct,eliminate,capcost"', allow_blank=True)
    ws_h.add_data_validation(dv_k)
    dv_k.add(f"B{hfirst}:B{hlast}")


    # ---------------------------------------------------------------- Going concern
    gcd = d["goingconcern"]
    gy = V.GC_MAX_YEARS
    cf0 = 13                                  # first cash-flow column (M)
    cfl = cf0 + gy - 1                        # last cash-flow column
    c_rate, c_lo, c_ad, c_hi, c_elo, c_ead, c_ehi = cfl + 1, cfl + 2, cfl + 3, cfl + 4, cfl + 5, cfl + 6, cfl + 7
    brand.xlsx_title_band(ws_g, "Going-concern and development value", _subtitle(vm) + [f"Stage: {gcd['stage'] or 'not stated'}. Present values at the valuation date; each item x its probability"], ncols=c_ehi)
    brand.xlsx_page_setup(ws_g)
    heads = ["Item", "Treatment", "Currency", "Low", "Adopted", "High", "Discount rate %", "Rate range +/- pts", "Terminal growth %", "Mid-year (1/0)", "Probability %", "Include (1/0)"]
    heads += [f"Year {k + 1}" for k in range(gy)] + ["Rate to " + vm.currency, "Value low", "Value adopted", "Value high", f"Effect low ({vm.currency})", f"Effect adopted ({vm.currency})", f"Effect high ({vm.currency})"]
    _head(ws_g, 8, heads)
    _setw(ws_g, {"A": 36, "B": 16, "C": 9, "D": 14, "E": 14, "F": 14, "G": 11, "H": 10, "I": 11, "J": 9, "K": 11, "L": 9})
    for k in range(gy):
        ws_g.column_dimensions[CL(cf0 + k)].width = 13
    for k in (c_rate, c_lo, c_ad, c_hi, c_elo, c_ead, c_ehi):
        ws_g.column_dimensions[CL(k)].width = 15
    ws_g.freeze_panes = ws_g.cell(row=9, column=3)
    gfirst = 9
    gitems = gcd["items"]
    gn = len(gitems) + 4
    glast = gfirst + gn - 1
    CFR = lambda R: f"${CL(cf0)}{R}:${CL(cfl)}{R}"
    for i in range(gn):
        R = gfirst + i
        it = gitems[i] if i < len(gitems) else None
        vals = [it["label"] if it else None, it["kind"] if it else None, (it["currency"] or vm.currency) if it else None, it["low"] if it else None,
                it["adopted"] if it else None, it["high"] if it else None, it["rate_pct"] if it else None, it["rate_range"] if it else None,
                it["growth_pct"] if it else None, (1 if it["mid_year"] else 0) if it else None, it["probability_pct"] if it else None, (1 if it["include"] else 0) if it else None]
        for ci, (v, fm) in enumerate(zip(vals, [None, None, None, NUM, NUM, NUM, "0.0", "0.0", "0.0", "0", "0.0", "0"]), start=1):
            _inp(ws_g.cell(row=R, column=ci), v, fm)
        for k in range(gy):
            v = it["cash_flows"][k] if it and k < len(it["cash_flows"]) else None
            _inp(ws_g.cell(row=R, column=cf0 + k), v, NUM)
        ws_g.cell(row=R, column=c_rate, value=f'=IF($C{R}="",1,IFERROR(INDEX({FXR},MATCH($C{R},{FXC},0)),"n/a"))').number_format = NUM4

        def dcf(shift, R=R):
            r = f"($G{R}/100{shift})"
            n = f"COUNT({CFR(R)})"
            return (f"IF(OR(NOT(ISNUMBER($G{R})),{n}=0),0,NPV({r},{CFR(R)})*(1+{r})^(0.5*N($J{R}))"
                    f"+IF(AND(ISNUMBER($I{R}),{r}>$I{R}/100),INDEX({CFR(R)},{n})*(1+$I{R}/100)/({r}-$I{R}/100)/(1+{r})^({n}-0.5*N($J{R})),0))")
        adopted_fill = f'IF($E{R}<>"",$E{R},IF(AND($D{R}<>"",$F{R}<>""),($D{R}+$F{R})/2,IF($D{R}<>"",$D{R},IF($F{R}<>"",$F{R},0))))'
        ws_g.cell(row=R, column=c_ad, value=f'=IF($B{R}="dcf",{dcf("")},{adopted_fill})')
        ws_g.cell(row=R, column=c_lo, value=f'=IF($B{R}="dcf",{dcf(f"+$H{R}/100")},IF($D{R}<>"",$D{R},{CL(c_ad)}{R}))')
        ws_g.cell(row=R, column=c_hi, value=f'=IF($B{R}="dcf",{dcf(f"-$H{R}/100")},IF($F{R}<>"",$F{R},{CL(c_ad)}{R}))')
        for k in (c_lo, c_ad, c_hi):
            ws_g.cell(row=R, column=k).number_format = NUM
        for ek, vk in ((c_elo, c_lo), (c_ead, c_ad), (c_ehi, c_hi)):
            ws_g.cell(row=R, column=ek, value=(f'=IF(AND($L{R}=1,ISNUMBER(${CL(c_rate)}{R}),{S["gcon"]}=1),IF(OR($B{R}="dcf",$B{R}="synergy",$B{R}="development",$B{R}="other_add"),1,-1)'
                                               f'*{CL(vk)}{R}*${CL(c_rate)}{R}*IF($K{R}="",100,$K{R})/100,0)')).number_format = NUM
    gtot = glast + 1
    ws_g.cell(row=gtot, column=1, value="Net going-concern and development value").font = Font(bold=True)
    for k in (c_elo, c_ead, c_ehi):
        cell = ws_g.cell(row=gtot, column=k, value=f"=SUM({CL(k)}{gfirst}:{CL(k)}{glast})")
        cell.number_format, cell.font, cell.fill, cell.border = NUM, Font(bold=True), FILL_TOT, TOP
    dv_g = DataValidation(type="list", formula1='"dcf,synergy,development,other_add,capex,rampup,overlap,other_deduct"', allow_blank=True)
    dv_gi = DataValidation(type="list", formula1='"0,1"', allow_blank=True)
    ws_g.add_data_validation(dv_g)
    ws_g.add_data_validation(dv_gi)
    dv_g.add(f"B{gfirst}:B{glast}")
    dv_gi.add(f"L{gfirst}:L{glast}")
    dv_gi.add(f"J{gfirst}:J{glast}")
    GCT = {"low": f"'Going concern'!${CL(c_elo)}${gtot}", "adopted": f"'Going concern'!${CL(c_ead)}${gtot}", "high": f"'Going concern'!${CL(c_ehi)}${gtot}"}
    ws_g.cell(row=gtot + 2, column=1, value="Treatments: dcf, synergy, development, other_add add to value; capex, rampup, overlap, other_deduct reduce it. Enter amounts as positives. "
                                           "For dcf rows, enter yearly net cash flows (year 1 first); low and high use the discount rate plus / minus the range.").font = Font(italic=True, color=MUTED)

    def hsum(kind, col):
        return f'SUMIF(\'Holding items\'!$B${hfirst}:$B${hlast},"{kind}",\'Holding items\'!${col}${hfirst}:${col}${hlast})'

    # ---------------------------------------------------------------- Summary
    brand.xlsx_title_band(ws_sum, "Group valuation summary", _subtitle(vm) + [f"{d['basis_of_value']} as at {vm.valuation_date.strftime('%d %B %Y') if vm.valuation_date else ''}"], ncols=7)
    brand.xlsx_page_setup(ws_sum)
    _setw(ws_sum, {"A": 52, "B": 18, "C": 18, "D": 18, "E": 4, "F": 22, "G": 18})
    _head(ws_sum, 8, ["", "Low", "Adopted", "High"])
    rows = {}
    r = 9
    cols = (("low", "B", "M"), ("adopted", "C", "N"), ("high", "D", "O"))

    def put(label, key, fn, bold=False, total=False, fmt=NUM):
        nonlocal r
        ws_sum.cell(row=r, column=1, value=label).font = Font(bold=bold or total)
        for case, col, hc in cols:
            cell = ws_sum[f"{col}{r}"]
            cell.value = fn(case, col, hc)
            cell.number_format = fmt
            if bold or total:
                cell.font = Font(bold=True)
            if total:
                cell.fill, cell.border = FILL_TOT, TOP
        rows[key] = r
        r += 1

    put("Sum of the components (after ownership and adjustments)", "parts", lambda case, col, hc: f"={PARTS[case]}")
    put("Add: holding-company assets", "add", lambda case, col, hc: "=" + hsum("add", hc))
    put("Less: holding-company liabilities", "ded", lambda case, col, hc: "=" + hsum("deduct", hc))
    put("Less: eliminations (cross-holdings, intercompany)", "elim", lambda case, col, hc: "=" + hsum("eliminate", hc))
    put("Less: capitalised central costs", "cap", lambda case, col, hc: "=" + hsum("capcost", hc))
    put("Add: going-concern and development value (risk-adjusted, net)", "gc", lambda case, col, hc: f"={GCT[case]}")
    put("Group equity value before group adjustments", "pre", lambda case, col, hc: f"=SUM({col}{rows['parts']}:{col}{rows['gc']})", total=True)
    put("Group adjustment factor (control, minority, DLOM)", "factor",
        lambda case, col, hc: f"=(1+{S['ctl']}/100)*(1-{S['min']}/100)*(1-{S['dlom']}/100)", fmt="0.0000")
    put("Group equity value", "group", lambda case, col, hc: f"={col}{rows['pre']}*{col}{rows['factor']}", total=True)
    put("Stake valued (%)", "stakepct", lambda case, col, hc: f"={S['stake']}", fmt="0.0")
    put("Value of the stake", "stake", lambda case, col, hc: f"={col}{rows['group']}*{col}{rows['stakepct']}/100", total=True)
    put("Value per share", "ps", lambda case, col, hc: f'=IF(N({S["shares"]})>0,{col}{rows["stake"]}/{S["shares"]},"n/a")', fmt="#,##0.0000")
    r += 1
    ws_sum.cell(row=r, column=1, value="Check against the app").font = Font(bold=True, color=NAVY)
    r += 1
    ws_sum.cell(row=r, column=1, value="Group equity value when this workbook was exported")
    for (case, col, hc), idx in zip(cols, range(3)):
        ws_sum[f"{col}{r}"] = round(res["group_equity"][idx], 2)
        ws_sum[f"{col}{r}"].number_format = NUM
    app_row = r
    r += 1
    ws_sum.cell(row=r, column=1, value="Difference (zero until you change an input)")
    for case, col, hc in cols:
        ws_sum[f"{col}{r}"] = f"=ROUND({col}{rows['group']}-{col}{app_row},2)"
        ws_sum[f"{col}{r}"].number_format = NUM
    r += 2
    if res["warnings"]:
        ws_sum.cell(row=r, column=1, value="Points to note").font = Font(bold=True, color=NAVY)
        for w in res["warnings"][:12]:
            r += 1
            ws_sum.cell(row=r, column=1, value=w).alignment = Alignment(wrap_text=True, vertical="top")
    ws_sum.cell(row=7, column=1, value=f"Reporting currency: {vm.currency}. Edit values on the Components, Holding items and Settings sheets; this sheet recalculates.").font = Font(italic=True, color=MUTED)

    for ws in wb.worksheets:
        ws.sheet_properties.tabColor = NAVY if ws.title == "Summary" else GOLD
    wb.properties.creator = "Neverlank Chartered Accountants"
    wb.properties.title = vm.name
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =========================================================================== shared report text

def _m(v, dp=0):
    if v is None:
        return "-"
    s = f"{abs(v):,.{dp}f}"
    return f"({s})" if v < -0.5 * 10 ** (-dp) else (s if round(v, dp) != 0 else "-")


def _facts(vm, d):
    return [("Client", vm.client.name), ("Engagement", vm.engagement.title if vm.engagement else "-"),
            ("Valuation date", vm.valuation_date.strftime("%d %B %Y") if vm.valuation_date else "-"),
            ("Basis of value", f"{d['basis_of_value']} ({d['premise']})" if d.get("premise") else d["basis_of_value"]),
            ("Reporting currency", vm.currency), ("Status", vm.status), ("Prepared", date.today().strftime("%d %B %Y")),
            ("Standard", d.get("standard") or "-")]


def _adj_text(c):
    bits = []
    if c["control_pct"]:
        bits.append(f"+{c['control_pct']:g}% control")
    if c["minority_pct"]:
        bits.append(f"-{c['minority_pct']:g}% minority")
    if c["dlom_pct"]:
        bits.append(f"-{c['dlom_pct']:g}% DLOM")
    return ", ".join(bits) or "none"


def _bridge_lines(res):
    d = res["data"]
    g = d["group"]
    lines = [("Sum of the components", res["parts"], False)]
    for key, label in (("additions", "Add: holding-company assets"), ("deductions", "Less: holding-company liabilities"),
                       ("eliminations", "Less: eliminations (cross-holdings, intercompany)"), ("capcost", "Less: capitalised central costs")):
        if any(abs(x) > 0.005 for x in res[key]):
            lines.append((label, res[key], False))
    if res.get("gc_on") and any(abs(x) > 0.005 for x in res.get("gc", (0, 0, 0))):
        lines.append(("Add: going-concern and development value (risk-adjusted)", res["gc"], False))
    lines.append(("Group equity value before group adjustments", res["pre"], True))
    if res["group_factor"] != 1:
        lines.append((f"Group adjustments (control {g['control_pct']:g}%, minority {g['minority_pct']:g}%, DLOM {g['dlom_pct']:g}%)",
                      tuple(res["group_equity"][i] - res["pre"][i] for i in range(3)), False))
    lines.append(("Group equity value", res["group_equity"], True))
    if g["stake_pct"] != 100:
        lines.append((f"Value of a {g['stake_pct']:g}% stake", res["stake"], True))
    return lines


def _conclusion(vm, res):
    d = res["data"]
    g = d["group"]
    tgt = res["stake"] if g["stake_pct"] != 100 else res["group_equity"]
    what = f"{g['stake_pct']:g}% stake in the group" if g["stake_pct"] != 100 else "group"
    return (f"On the basis of {d['basis_of_value'].lower()}, the equity value of the {what} at "
            f"{vm.valuation_date.strftime('%d %B %Y') if vm.valuation_date else 'the valuation date'} is {vm.currency} {_m(tgt[1])}, "
            f"within a range of {vm.currency} {_m(tgt[0])} to {vm.currency} {_m(tgt[2])}. The value is built up from {res['count']} component valuation"
            f"{'s' if res['count'] != 1 else ''}, converted to {vm.currency}, adjusted for the group's ownership, and combined with the holding-company items shown.")


# =========================================================================== PDF

def build_pdf(vm, signed=False):
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer
    import cf_pdf as P
    from invoicing import FIRM_LETTERHEAD as firm

    d = V.normalise(vm.data, vm.currency)
    res = V.consolidate(d, vm.currency, vm.valuation_date)
    cur = vm.currency
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=P.PAGE, leftMargin=P.MARGIN, rightMargin=P.MARGIN, topMargin=11 * mm, bottomMargin=16 * mm,
                            title=f"{vm.vm_number} {vm.name}", author=firm.get("name", ""))
    story = [P._band(firm, "VALUATION MODEL", vm.vm_number, f"{cur}  ·  {vm.status}" + ("  ·  Signed" if signed else "")), Spacer(1, 5 * mm)]
    story.append(Paragraph(P.escape(vm.name), ParagraphStyle("t", parent=P._base, fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=P.NAVY)))
    story.append(Spacer(1, 2 * mm))
    story.append(P._kv(_facts(vm, d)))
    if d["purpose"]:
        story += [Spacer(1, 2 * mm), P._p("Purpose: " + d["purpose"])]
    story.append(Paragraph("Conclusion", P._h2))
    story.append(P._p(_conclusion(vm, res)))
    story.append(Spacer(1, 2 * mm))
    g = d["group"]
    head = [["", "Low", "Adopted", "High"], ["Group equity value", _m(res["group_equity"][0]), _m(res["group_equity"][1]), _m(res["group_equity"][2])]]
    if g["stake_pct"] != 100:
        head.append([f"{g['stake_pct']:g}% stake", _m(res["stake"][0]), _m(res["stake"][1]), _m(res["stake"][2])])
    if res["per_share"]:
        head.append([f"Per share ({g['shares']:,.0f} shares)", *[f"{x:,.4f}" for x in res["per_share"]]])
    story.append(P._simple(head, [90 * mm, 38 * mm, 38 * mm, 38 * mm], size=9))
    if res["warnings"]:
        story.append(Paragraph("Points to note", P._h3))
        for w in res["warnings"][:10]:
            story.append(P._p("- " + w, P._note))

    # football field
    bars = V.football(res)
    if bars:
        story.append(Paragraph("Valuation ranges", P._h2))
        lo = min([b["low"] for b in bars] + [0.0])
        hi = max([b["high"] for b in bars] + [1.0])
        span = (hi - lo) or 1.0
        label_w, plot_w, row_h = 70 * mm, P.WIDTH - 70 * mm - 6 * mm, 14
        dr = Drawing(P.WIDTH, row_h * len(bars) + 16)
        for k, b in enumerate(bars):
            y = row_h * (len(bars) - 1 - k) + 12
            dr.add(String(0, y + 3, (b["label"][:48]), fontName="Helvetica-Bold" if b["kind"] != "component" else "Helvetica", fontSize=7.5, fillColor=P.INK))
            x0 = label_w + (b["low"] - lo) / span * plot_w
            w = max(1.5, (b["high"] - b["low"]) / span * plot_w)
            dr.add(Rect(x0, y, w, 9, fillColor=P.GOLD if b["kind"] != "component" else P.NAVY, strokeColor=None))
            xm = label_w + (b["mid"] - lo) / span * plot_w
            dr.add(Line(xm, y - 1, xm, y + 10, strokeColor=colors.white, strokeWidth=1.5))
        dr.add(String(label_w, 0, _m(lo), fontName="Helvetica", fontSize=6.5, fillColor=P.MUTED))
        dr.add(String(label_w + plot_w, 0, _m(hi), fontName="Helvetica", fontSize=6.5, fillColor=P.MUTED, textAnchor="end"))
        story.append(dr)

    # component schedule
    story.append(PageBreak())
    story.append(Paragraph("Component valuations", P._h2))
    story.append(P._p(f"Values converted to {cur}. Equity value is after deducting net debt where the component was valued on an enterprise basis; adjustments are applied "
                      f"in the order control premium, minority discount, then marketability discount (DLOM).", P._note))
    head = ["Component", "Method", "Cur", "Rate", "Equity 100%", "Own %", "Adjustments", "Low", "Adopted", "High", "% total"]
    data = [head]
    for r in res["rows"]:
        c = r["c"]
        if r["adjusted"] is None:
            data.append([c["name"][:44], c["method"][:26], c["currency"], "", "", "", "excluded" if r["problem"] == "excluded" else "NOT IN TOTAL: " + (r["problem"] or ""), "", "", "", ""])
            continue
        data.append([c["name"][:44], c["method"][:26], c["currency"], f"{r['rate']:.4f}", _m(r["equity"][1]), f"{c['ownership_pct']:g}", _adj_text(c)[:28],
                     _m(r["adjusted"][0]), _m(r["adjusted"][1]), _m(r["adjusted"][2]), f"{r['share']:.1f}" if r["share"] is not None else ""])
    data.append(["Sum of the components", "", "", "", "", "", "", _m(res["parts"][0]), _m(res["parts"][1]), _m(res["parts"][2]), "100.0"])
    w = [58 * mm, 36 * mm, 10 * mm, 15 * mm, 26 * mm, 12 * mm, 34 * mm, 22 * mm, 22 * mm, 22 * mm, 13 * mm]
    scale = P.WIDTH / sum(w)
    story.append(P._simple(data, [x * scale for x in w], size=7.2, align_right_from=3, bold_last=True))

    # bridge
    story.append(Paragraph("From the components to the group value", P._h2))
    bl = _bridge_lines(res)
    data = [["", "Low", "Adopted", "High"]] + [[lbl, _m(v[0]), _m(v[1]), _m(v[2])] for lbl, v, _ in bl]
    t = P._simple(data, [118 * mm, 36 * mm, 36 * mm, 36 * mm], size=8.2)
    for i, (_, _, bold) in enumerate(bl, start=1):
        if bold:
            t.setStyle(P.TableStyle([("FONT", (0, i), (-1, i), "Helvetica-Bold", 8.2), ("LINEABOVE", (0, i), (-1, i), 0.6, P.NAVY)]))
    story.append(KeepTogether([t]))
    if d["holding"]:
        story.append(Paragraph("Holding-company items", P._h3))
        names = {k: lbl.split(":")[0] if ":" in lbl else lbl for k, lbl in V.HOLDING_KINDS}
        hd = [["Item", "Treatment", "Cur", "Effect: low", "Effect: adopted", "Effect: high"]]
        for hr in res["holding_rows"]:
            h = hr["h"]
            e = hr["effect"] or (None, None, None)
            hd.append([h["label"][:60], {"add": "Added", "deduct": "Deducted", "eliminate": "Eliminated", "capcost": "Capitalised cost (deducted)"}[h["kind"]],
                       h["currency"] or cur, _m(e[0]), _m(e[1]), _m(e[2])])
        story.append(P._simple(hd, [90 * mm, 50 * mm, 14 * mm, 30 * mm, 30 * mm, 30 * mm], size=7.8, align_right_from=3))

    if d["goingconcern"]["enabled"] and res["gc_rows"]:
        story.append(Paragraph("Going-concern and development value", P._h3))
        story.append(P._p(f"Stage: {d['goingconcern']['stage'] or 'not stated'}. Present values at the valuation date, each multiplied by its probability of being achieved. "
                          f"Before probability weighting the net value is {_m(res['gc_unrisked'][1])}.", P._note))
        gd = [["Item", "Treatment", "Probability", "Low", "Adopted", "High"]]
        for x in res["gc_rows"]:
            it = x["g"]
            lab = dict((k, l) for k, l, _ in V.GC_KINDS)[it["kind"]].split(" (")[0]
            if x["counted"]:
                gd.append([it["label"][:60], lab[:44], f"{it['probability_pct']:g}%", _m(x["effect"][0]), _m(x["effect"][1]), _m(x["effect"][2])])
            else:
                gd.append([it["label"][:60], lab[:44], "", "", "not in total", ""])
        gd.append(["Net going-concern and development value", "", "", _m(res["gc"][0]), _m(res["gc"][1]), _m(res["gc"][2])])
        story.append(P._simple(gd, [80 * mm, 70 * mm, 24 * mm, 30 * mm, 30 * mm, 30 * mm], size=7.8, align_right_from=2, bold_last=True))

    fxs = [c for c in V.currencies_needed(d, cur)]
    if fxs:
        story.append(Paragraph("Exchange rates", P._h3))
        story.append(P._simple([["Currency", f"Rate to {cur}"]] + [[c, f"{d['fx'].get(c):.4f}" if d["fx"].get(c) else "not set"] for c in fxs], [30 * mm, 36 * mm], size=7.8))

    story.append(Paragraph("Sources of the component valuations", P._h3))
    sr = [["Component", "Valuation date", "Source"]] + [[c["name"][:50], c["date"] or "-", (c["source"]["label"] or "typed in")[:90]] for c in d["components"]]
    story.append(P._simple(sr, [80 * mm, 30 * mm, 150 * mm], size=7.5, align_right_from=9))
    nar = d["narrative"]
    for key, title in (("scope", "Scope and approach"), ("assumptions", "Key assumptions"), ("limiting", "Limiting conditions")):
        if nar.get(key):
            story += [Paragraph(title, P._h2), P._p(nar[key])]
    story += [Spacer(1, 4 * mm), P._p("This valuation consolidates component valuations prepared by or for the client. The group value is only as reliable as the component "
                                      "valuations, the ownership percentages and the adjustments applied.", P._note)]
    if signed:
        story.append(KeepTogether(brand.pdf_signature_flowables(firm_name=firm.get("name"), signed_on=vm.mp_signed_at)))
    foot = P._footer(firm, f"{vm.vm_number}  ·  {vm.name}")
    doc.build(story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()


# =========================================================================== Word

def build_docx(vm, signed=False):
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    d = V.normalise(vm.data, vm.currency)
    res = V.consolidate(d, vm.currency, vm.valuation_date)
    cur = vm.currency
    firm = brand.firm_info()
    doc = Document()
    for s in doc.sections:
        s.orientation = WD_ORIENT.LANDSCAPE
        s.page_width, s.page_height = Mm(297), Mm(210)
    brand.setup_document(doc, margins_in=0.7, base_size=10)
    brand.branded_footer(doc, firm, center=vm.vm_number)
    brand.letterhead_band(doc, "Valuation Model", sub=f"{cur} · {vm.status}" + (" · Signed" if signed else ""), number=vm.vm_number, firm=firm)
    brand.meta_strip(doc, [("Valuation date", vm.valuation_date.strftime("%d %B %Y") if vm.valuation_date else "-"), ("Basis of value", d["basis_of_value"]),
                           ("Currency", cur), ("Components", str(res["count"]))])
    brand.party_block(doc, "Prepared for", vm.client.name, [d["purpose"]] if d["purpose"] else [])

    def para(text, size=10, bold=False, after=6, colour=brand.INK):
        p = doc.add_paragraph()
        brand._tight(p, before=0, after=after, line=1.1)
        brand._run(p, text, size=size, bold=bold, colour=colour)
        return p

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

    brand.heading(doc, "Conclusion", 1)
    para(_conclusion(vm, res))
    g = d["group"]
    rows = [["Group equity value", _m(res["group_equity"][0]), _m(res["group_equity"][1]), _m(res["group_equity"][2])]]
    if g["stake_pct"] != 100:
        rows.append([f"{g['stake_pct']:g}% stake", _m(res["stake"][0]), _m(res["stake"][1]), _m(res["stake"][2])])
    if res["per_share"]:
        rows.append([f"Per share ({g['shares']:,.0f} shares)", *[f"{x:,.4f}" for x in res["per_share"]]])
    table(["", "Low", "Adopted", "High"], rows, [4.0, 1.7, 1.7, 1.7], size=9.5)
    if res["warnings"]:
        brand.note_box(doc, "\n".join("- " + w for w in res["warnings"][:10]), title="Points to note")

    doc.add_page_break()
    brand.heading(doc, "Component valuations", 1)
    para(f"Values converted to {cur}. Equity value is after deducting net debt for enterprise-basis valuations; adjustments apply in the order control premium, minority discount, "
         "then marketability discount (DLOM).", size=9, colour=brand.MUTED)
    rows = []
    for r in res["rows"]:
        c = r["c"]
        if r["adjusted"] is None:
            rows.append([c["name"], c["method"], c["currency"], "", "", "", "excluded" if r["problem"] == "excluded" else "NOT IN TOTAL", "", "", "", ""])
        else:
            rows.append([c["name"], c["method"], c["currency"], f"{r['rate']:.4f}", _m(r["equity"][1]), f"{c['ownership_pct']:g}", _adj_text(c),
                         _m(r["adjusted"][0]), _m(r["adjusted"][1]), _m(r["adjusted"][2]), f"{r['share']:.1f}" if r["share"] is not None else ""])
    rows.append(["Sum of the components", "", "", "", "", "", "", _m(res["parts"][0]), _m(res["parts"][1]), _m(res["parts"][2]), "100.0"])
    table(["Component", "Method", "Cur", "Rate", "Equity 100%", "Own %", "Adjustments", "Low", "Adopted", "High", "% total"], rows,
          [1.9, 1.25, 0.4, 0.55, 0.85, 0.45, 1.15, 0.8, 0.8, 0.8, 0.5], num_from=3, size=7.5, bold_rows=(len(rows),))

    brand.heading(doc, "From the components to the group value", 1)
    bl = _bridge_lines(res)
    table(["", "Low", "Adopted", "High"], [[lbl, _m(v[0]), _m(v[1]), _m(v[2])] for lbl, v, _ in bl], [4.6, 1.5, 1.5, 1.5], size=9,
          bold_rows=[i for i, (_, _, b) in enumerate(bl, start=1) if b])
    if d["holding"]:
        hr_rows = []
        for hr in res["holding_rows"]:
            h = hr["h"]
            e = hr["effect"] or (None, None, None)
            hr_rows.append([h["label"], {"add": "Added", "deduct": "Deducted", "eliminate": "Eliminated", "capcost": "Capitalised cost (deducted)"}[h["kind"]],
                            h["currency"] or cur, _m(e[0]), _m(e[1]), _m(e[2])])
        brand.heading(doc, "Holding-company items", 2)
        table(["Item", "Treatment", "Cur", "Effect: low", "Effect: adopted", "Effect: high"], hr_rows, [3.3, 2.0, 0.6, 1.2, 1.2, 1.2], num_from=3, size=8.5)
    if d["goingconcern"]["enabled"] and res["gc_rows"]:
        brand.heading(doc, "Going-concern and development value", 2)
        para(f"Stage: {d['goingconcern']['stage'] or 'not stated'}. Present values at the valuation date, each multiplied by its probability of being achieved. "
             f"Before probability weighting the net value is {_m(res['gc_unrisked'][1])}.", size=9, colour=brand.MUTED)
        gd = []
        for x in res["gc_rows"]:
            it = x["g"]
            lab = dict((k, l) for k, l, _ in V.GC_KINDS)[it["kind"]].split(" (")[0]
            gd.append([it["label"], lab, f"{it['probability_pct']:g}%", *( [_m(v) for v in x["effect"]] if x["counted"] else ["", "not in total", ""] )])
        gd.append(["Net going-concern and development value", "", "", _m(res["gc"][0]), _m(res["gc"][1]), _m(res["gc"][2])])
        table(["Item", "Treatment", "Probability", "Low", "Adopted", "High"], gd, [2.8, 2.5, 0.9, 1.1, 1.1, 1.1], num_from=2, size=8.5, bold_rows=(len(gd),))
    fxs = V.currencies_needed(d, cur)
    if fxs:
        brand.heading(doc, "Exchange rates", 2)
        table(["Currency", f"Rate to {cur}"], [[c, f"{d['fx'].get(c):.4f}" if d["fx"].get(c) else "not set"] for c in fxs], [1.4, 1.6], size=9)
    brand.heading(doc, "Sources of the component valuations", 2)
    table(["Component", "Valuation date", "Source"], [[c["name"], c["date"] or "-", c["source"]["label"] or "typed in"] for c in d["components"]], [3.5, 1.3, 5.0], num_from=None, size=8.5)
    for key, title in (("scope", "Scope and approach"), ("assumptions", "Key assumptions"), ("limiting", "Limiting conditions")):
        if d["narrative"].get(key):
            brand.heading(doc, title, 2)
            for chunk in d["narrative"][key].split("\n"):
                if chunk.strip():
                    para(chunk.strip())
    para("This valuation consolidates component valuations prepared by or for the client. The group value is only as reliable as the component valuations, the "
         "ownership percentages and the adjustments applied.", size=8.5, colour=brand.MUTED)
    if signed:
        brand.add_signature_block(doc, firm_name=firm.get("name"), signed_on=vm.mp_signed_at)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
