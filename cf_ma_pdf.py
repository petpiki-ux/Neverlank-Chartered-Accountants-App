"""Branded PDF for a management accounts pack (landscape A4, same letterhead as the invoice / forecast report).

Pages: cover and summary (facts, key messages, KPI strip) | income statement | balance sheet | cash flow | KPIs | variances |
commentary and basis of preparation. The three statements use the firm's layout:
LAST MONTH | line | CURRENT MONTH (actual, forecast, budget, last year) | FULL YEAR (forecast, budget, last year).
"""
import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import cf_ma as M
from cf_pdf import PAGE, MARGIN, WIDTH, GREEN, RED, _band, _footer, _kv, _h2, _h3, _p, _simple
from invoice_pdf import NAVY, GOLD, INK, MUTED, LINE, TINT, _base, _small

GREY_BAND = colors.HexColor("#d9dde3")
GREY_SUB = colors.HexColor("#eceef2")
AMBER = colors.HexColor("#b8730a")


# --------------------------------------------------------------------------- number formats

def _n(v):
    if v is None:
        return ""
    if abs(v) < 0.5:
        return "-"
    s = f"{abs(v):,.0f}"
    return f"({s})" if v < 0 else s


def _pc(v, dp=1):
    return "" if v is None else f"{v * 100:,.{dp}f}%"


def _pp(v):
    if v is None:
        return ""
    t = f"{v * 100:,.1f}"
    return "0.0" if t == "-0.0" else t


def _fmt(col, row_kind, v):
    if v is None:
        return ""
    fmt = col["fmt"]
    if row_kind == "pct":
        if fmt in ("money", "plain"):
            return _pc(v)
        if fmt == "var":
            return _pp(v)
        return ""
    if fmt == "varpct":
        return _pc(v)
    return _n(v)


# --------------------------------------------------------------------------- generic firm-layout table

def _layout(cols, rows, size=6.6, sections=False):
    """cols: [{head, group, get(row), fmt, w, kind}]; kind 'lm' (last month) or 'label' or 'val'. rows: dicts with kind/label."""
    n = len(cols)
    data, styles = [], [
        ("FONT", (0, 0), (-1, -1), "Helvetica", size), ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 2), (-1, -1), 0.2, LINE),
    ]
    # band row (merged group titles) and sub-heading row
    band, sub = [], []
    i = 0
    while i < n:
        g = cols[i].get("group", "")
        j = i
        while j + 1 < n and cols[j + 1].get("group", "") == g and cols[j + 1]["kind"] == "val" and cols[i]["kind"] == "val":
            j += 1
        band.append(g)
        band += [""] * (j - i)
        if j > i:
            styles.append(("SPAN", (i, 0), (j, 0)))
        i = j + 1
    sub = [c["head"] for c in cols]
    data += [band, sub]
    styles += [
        ("BACKGROUND", (0, 0), (-1, 0), GREY_BAND), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", size), ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BACKGROUND", (0, 1), (-1, 1), GREY_SUB), ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", size), ("ALIGN", (0, 1), (-1, 1), "RIGHT"),
        ("LINEAFTER", (0, 0), (0, -1), 0.4, LINE),
    ]
    for k, c in enumerate(cols):
        if c["kind"] == "label":
            styles.append(("ALIGN", (k, 1), (k, -1), "LEFT"))
    group = None
    for r in rows:
        kind = r["kind"]
        g = (("Revenue" if r.get("income") else "Operating expenses") if kind == "indent" else None) if sections else None
        if g and g != group:
            ri = len(data)
            data.append([""] * n)
            li = next(k for k, c in enumerate(cols) if c["kind"] == "label")
            data[ri][li] = g
            styles += [("BACKGROUND", (0, ri), (-1, ri), TINT), ("FONT", (0, ri), (-1, ri), "Helvetica-Bold", size)]
            styles.append(("ALIGN", (li, ri), (li, ri), "LEFT"))
        group = g
        ri = len(data)
        line = []
        for k, c in enumerate(cols):
            if c["kind"] == "label":
                line.append(("      " if kind == "indent" else "  ") + r["label"])
                continue
            v = c["get"](r)
            line.append(_fmt(c, kind, v))
            if c["fmt"] in ("var", "varpct") and v is not None and kind != "pct":
                col = GREEN if v > 0.0005 else (RED if v < -0.0005 else None)
                if c["fmt"] == "varpct":
                    fav = c.get("fav")
                    fv = fav(r) if fav else v
                    col = GREEN if (fv or 0) > 0.0005 else (RED if (fv or 0) < -0.0005 else None)
                if col is not None:
                    styles.append(("TEXTCOLOR", (k, ri), (k, ri), col))
        data.append(line)
        if kind in ("total", "grand"):
            styles += [("FONT", (0, ri), (-1, ri), "Helvetica-Bold", size), ("LINEABOVE", (0, ri), (-1, ri), 0.6, NAVY)]
        if kind == "grand":
            styles += [("BACKGROUND", (0, ri), (-1, ri), TINT), ("LINEBELOW", (0, ri), (-1, ri), 1.2, NAVY)]
        if kind in ("memo", "pct"):
            styles += [("FONT", (0, ri), (-1, ri), "Helvetica-Oblique", size), ("TEXTCOLOR", (0, ri), (-1, ri), MUTED)]
    widths = [c["w"] for c in cols]
    for k, c in enumerate(cols):
        if c["kind"] != "label":
            styles.append(("ALIGN", (k, 2), (k, -1), "RIGHT"))
    t = Table(data, colWidths=widths, repeatRows=2)
    t.setStyle(TableStyle(styles))
    return t


def _widths(cols, total=WIDTH, label=50 * mm, lm=17 * mm):
    vals = [c for c in cols if c["kind"] == "val"]
    each = (total - label - lm) / max(len(vals), 1)
    each = min(each, 19 * mm)
    for c in cols:
        c["w"] = label if c["kind"] == "label" else (lm if c["kind"] == "lm" else each)
    return cols


def _income_cols(labels, ytd, var):
    mid = ("YEAR TO DATE - " if ytd else "CURRENT MONTH - ") + labels["month"]
    fy = "FULL YEAR - " + labels["fy"]
    blk = (lambda r: r["ytd"]) if ytd else (lambda r: r["month"])
    cols = [{"head": labels["last_month"], "group": "LAST MONTH", "kind": "lm", "fmt": "money", "get": lambda r: r["lm"]},
            {"head": "", "group": "", "kind": "label", "fmt": "plain", "get": None},
            {"head": "Actual", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: blk(r)["a"]}]
    if not ytd:
        cols.append({"head": "Forecast", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: blk(r)["f"]})
    cols.append({"head": "Budget", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: blk(r)["b"]})
    if var:
        cols += [{"head": "Var", "group": mid, "kind": "val", "fmt": "var", "get": lambda r: blk(r)["var"]},
                 {"head": "Var %", "group": mid, "kind": "val", "fmt": "varpct", "get": lambda r: blk(r)["varpct"]}]
    cols.append({"head": labels["prior"], "group": mid, "kind": "val", "fmt": "money", "get": lambda r: blk(r)["p"]})
    if var:
        cols.append({"head": "Var", "group": mid, "kind": "val", "fmt": "var", "get": lambda r: blk(r)["pvar"]})
    cols += [{"head": "Forecast", "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["fy"]["f"]},
             {"head": "Budget", "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["fy"]["b"]}]
    if var:
        cols += [{"head": "Var", "group": fy, "kind": "val", "fmt": "var", "get": lambda r: r["fy"]["var"]},
                 {"head": "Var %", "group": fy, "kind": "val", "fmt": "varpct", "get": lambda r: r["fy"]["varpct"]}]
    cols.append({"head": labels["prior"], "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["fy"]["p"]})
    if var:
        cols.append({"head": "Var", "group": fy, "kind": "val", "fmt": "var", "get": lambda r: r["fy"]["pvar"]})
    return _widths(cols)


def _balance_cols(labels, var):
    mid, fy = "CURRENT MONTH - " + labels["month"], "FULL YEAR (YEAR END) - " + labels["fy"]
    cols = [{"head": labels["last_month"], "group": "LAST MONTH", "kind": "lm", "fmt": "money", "get": lambda r: r["lm"]},
            {"head": "", "group": "", "kind": "label", "fmt": "plain", "get": None},
            {"head": "Actual", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: r["a"]},
            {"head": "Forecast", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: r["f"]},
            {"head": "Budget", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: r["b"]}]
    if var:
        cols.append({"head": "Var", "group": mid, "kind": "val", "fmt": "plain", "get": lambda r: r["var"]})
    cols.append({"head": labels["prior"] + " YE", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: r["py"]})
    if var:
        cols.append({"head": "Change in year", "group": mid, "kind": "val", "fmt": "plain", "get": lambda r: r["chg_year"]})
    cols += [{"head": "Forecast", "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["ffy"]},
             {"head": "Budget", "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["bfy"]},
             {"head": labels["prior"], "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["py"]}]
    return _widths(cols)


def _cash_cols(labels, var):
    mid, ytd, fy = "CURRENT MONTH - " + labels["month"], "YEAR TO DATE", "FULL YEAR - " + labels["fy"]
    cols = [{"head": labels["last_month"], "group": "LAST MONTH", "kind": "lm", "fmt": "money", "get": lambda r: r["lm"]},
            {"head": "", "group": "", "kind": "label", "fmt": "plain", "get": None},
            {"head": "Actual", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: r["a"]},
            {"head": "Forecast", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: r["f"]},
            {"head": "Budget", "group": mid, "kind": "val", "fmt": "money", "get": lambda r: r["b"]}]
    if var:
        cols.append({"head": "Var", "group": mid, "kind": "val", "fmt": "var", "get": lambda r: r["var"]})
    cols += [{"head": "Actual", "group": ytd, "kind": "val", "fmt": "money", "get": lambda r: r["ya"]},
             {"head": "Budget", "group": ytd, "kind": "val", "fmt": "money", "get": lambda r: r["yb"]}]
    if var:
        cols.append({"head": "Var", "group": ytd, "kind": "val", "fmt": "var", "get": lambda r: r["yvar"]})
    cols += [{"head": "Forecast", "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["ffy"]},
             {"head": "Budget", "group": fy, "kind": "val", "fmt": "money", "get": lambda r: r["bfy"]}]
    return _widths(cols)


# --------------------------------------------------------------------------- the report

def _basis_lines(d, V, status):
    out = []
    modelled = [s["label"] for s in status if s["source"] == "modelled"]
    est = [s["label"] for s in status if s["est"]]
    tb = [s["label"] for s in status if s["source"] == "tb"]
    man = [s["label"] for s in status if s["source"] == "manual"]
    if tb:
        out.append("Trial balance: " + ", ".join(tb) + " (cumulative year-to-date trial balances, mapped to the lines of this pack).")
    if man:
        out.append("Management figures captured manually: " + ", ".join(man) + ".")
    if modelled:
        out.append("MODELLED months (not recorded actuals): " + ", ".join(modelled) + ". They are built from the " + M.prior_fy_label(d)
                   + " accounts grown at the stated rates and are used for illustration and funding analysis.")
    if est:
        out.append("Estimated months: " + ", ".join(est) + " had no figures and are spread evenly between the months either side.")
    p = d.get("prior") or {}
    if p.get("pl"):
        out.append(f"Last year comparatives: {M.prior_fy_label(d)} accounts ({ {'tb': 'trial balance', 'manual': 'typed in', 'statements': 'loaded from financial statements'}.get(p.get('source'), 'supplied')}), spread evenly across the months.")
    for name in ("budget", "forecast"):
        pl = d.get(name) or {}
        if pl.get("pl"):
            src = (f"forecast model {pl.get('model_number')}" + (f" ({pl.get('scenario')})" if pl.get("scenario") else "")) if pl.get("source") == "model" else (
                "copied from the " + pl["copied_from"] if pl.get("copied_from") else "typed in")
            out.append(f"{name.capitalize()}: {src}" + (", then adjusted" if pl.get("edited") else "") + ".")
    if V.get("outlook_basis"):
        out.append("Full-year forecast: " + V["outlook_basis"] + ".")
    out.append("These management accounts are unaudited, prepared for management purposes, and have not been reviewed or audited.")
    return out


def _waterfall_drawing(b, width, height=58 * mm):
    """Waterfall of a variance bridge as a reportlab drawing (green helped profit, red hurt it, navy totals)."""
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    bars = [("tot", b["start"]["value"], 0.0, b["start"]["value"], "Comparator")]
    run = b["start"]["value"]
    for st in b["steps"]:
        new = run + st["value"]
        bars.append(("up" if st["value"] >= 0 else "down", st["value"], run, new, st["label"]))
        run = new
    bars.append(("tot", b["end"]["value"], 0.0, b["end"]["value"], "Actual"))
    lo = min(0.0, *[min(x[2], x[3]) for x in bars])
    hi = max(0.0, *[max(x[2], x[3]) for x in bars])
    span = (hi - lo) or 1.0
    top, bottom, left = 8 * mm, 15 * mm, 4 * mm
    plot_h = height - top - bottom
    y = lambda v: bottom + (v - lo) / span * plot_h
    dr = Drawing(width, height)
    dr.add(Line(left, y(0), width - left, y(0), strokeColor=LINE, strokeWidth=0.6))
    slot = (width - 2 * left) / len(bars)
    bw = slot * 0.62
    for i, (kind, val, a, z, lab) in enumerate(bars):
        x = left + i * slot + (slot - bw) / 2
        col = NAVY if kind == "tot" else (GREEN if kind == "up" else RED)
        y0, y1 = y(min(a, z)), y(max(a, z))
        dr.add(Rect(x, y0, bw, max(y1 - y0, 0.6), fillColor=col, strokeColor=None))
        txt = _n(val)
        dr.add(String(x + bw / 2, (y1 + 1.2 * mm) if val >= 0 or kind == "tot" and val >= 0 else (y0 - 3 * mm), txt, fontName="Helvetica", fontSize=6, textAnchor="middle", fillColor=INK))
        words, lines_, cur_ = lab.split(), [], ""
        for w_ in words:
            if cur_ and len(cur_) + 1 + len(w_) > 13:
                lines_.append(cur_)
                cur_ = w_
            else:
                cur_ = (cur_ + " " + w_).strip()
        lines_.append(cur_)
        lines_ = [ln if len(ln) <= 14 else ln[:13] + "." for ln in lines_[:2]]
        for j_, ln in enumerate(lines_):
            dr.add(String(x + bw / 2, 8 * mm - j_ * 2.6 * mm, ln, fontName="Helvetica", fontSize=5.6, textAnchor="middle", fillColor=MUTED))
    return dr


def build_pack_pdf(pack, meta, d, firm, opts=None):
    opts = opts or {}
    var = opts.get("var", True)
    cur = pack.currency
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=11 * mm, bottomMargin=16 * mm,
                            title=f"{pack.pack_number} {pack.name}", author=firm.get("name", ""))
    months = M.fy_months(d)
    ridx = M.report_index(d)
    labels = {"month": M.month_label(months[ridx], False), "fy": M.fy_label(d), "prior": M.prior_fy_label(d),
              "last_month": M.month_label(months[ridx - 1], False) if ridx > 0 else "n/a", "n": ridx + 1}
    V = M.vectors(d)
    status = M.data_status(d)
    com = d.get("commentary") or {}
    title_style = ParagraphStyle("t", parent=_base, fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=NAVY)
    story = [_band(firm, "MANAGEMENT ACCOUNTS", pack.pack_number, f"{cur}  ·  {labels['month']}  ·  {pack.status}"), Spacer(1, 5 * mm)]
    story.append(Paragraph(escape(pack.name), title_style))
    story.append(Spacer(1, 2 * mm))
    story.append(_kv([
        ("Client", pack.client.name), ("Reporting month", labels["month"]), ("Financial year", labels["fy"]), ("Currency", cur),
        ("Prepared", date.today().strftime("%d %B %Y")), ("Prepared by", meta.get("prepared_by") or "-"),
        ("Engagement", pack.engagement.title if pack.engagement else "-"),
        ("Status", pack.status + (f" - finalised by {pack.finalised_by.name}" if pack.is_final and pack.finalised_by else "")),
    ]))

    A = V["actual_ytd"]
    if A is None:
        story += [Paragraph("Summary", _h2), _p("No actual figures have been captured for this reporting month.")]
    else:
        story.append(Paragraph("Summary", _h2))
        if com.get("summary"):
            story.append(_p(com["summary"]))
        else:
            for t in M.key_messages(d, ""):
                story.append(_p("•  " + t, _base))
        # headline table: month / ytd / full year against budget
        inc = M.income_rows(d)
        pick = {r["key"]: r for r in inc["rows"]}
        rows = [["", "Month actual", "Month budget", "Var", "YTD actual", "YTD budget", "Var", "FY forecast", "FY budget", "Var"]]
        for key, label in (("revenue", "Revenue"), ("gross_profit", "Gross profit"), ("ebitda", "EBITDA"), ("pat", "Profit after tax")):
            r = pick[key]
            rows.append([label, _n(r["month"]["a"]), _n(r["month"]["b"]), _n(r["month"]["var"]), _n(r["ytd"]["a"]), _n(r["ytd"]["b"]), _n(r["ytd"]["var"]),
                         _n(r["fy"]["f"]), _n(r["fy"]["b"]), _n(r["fy"]["var"])])
        story += [Spacer(1, 2 * mm), _simple(rows, [52 * mm] + [(WIDTH - 52 * mm) / 9] * 9, size=7.6)]
        kp = [k for k in M.kpi_table(d) if k["key"] in ("gross_margin", "ebitda_margin", "current_ratio", "cash", "net_debt", "receivable_days", "payable_days", "gearing")]
        if kp:
            def f(k, v):
                if v is None:
                    return "-"
                return {"pct": _pc(v), "x": f"{v:,.2f}x", "days": f"{v:,.0f}", "money": _n(v), "months": f"{v:,.1f}"}.get(k["fmt"], str(v))
            story += [Paragraph("Key indicators (year to date)", _h3),
                      _simple([["", *[k["label"] for k in kp]], ["Actual", *[f(k, k["actual"]) for k in kp]], ["Budget", *[f(k, k["budget"]) for k in kp]],
                               [labels["prior"], *[f(k, k["prior"]) for k in kp]]], [20 * mm] + [(WIDTH - 20 * mm) / len(kp)] * len(kp), size=7)]
    story.append(Spacer(1, 2 * mm))
    story += [_p("•  " + b, _note_style) for b in _basis_lines(d, V, status)[:3] if "MODELLED" in b or "Estimated" in b]

    # ---- income statement
    story += [PageBreak(), Paragraph(f"Income statement - {labels['month']}" + (" (year to date)" if opts.get("ytd") else ""), _h2)]
    if A is None:
        story.append(_p("No figures."))
    else:
        inc = M.income_rows(d)
        story.append(_layout(_income_cols(labels, bool(opts.get("ytd")), var), inc["rows"], sections=True))
        story.append(Spacer(1, 1.5 * mm))
        story.append(_p(f"{cur}. Variances: favourable positive, adverse in brackets. Last year is the {labels['prior']} accounts spread evenly across the months.", _note_style))
        if com.get("income"):
            story += [Paragraph("Commentary", _h3), _p(com["income"])]

    # ---- balance sheet
    bal = M.balance_rows(d)
    story += [PageBreak(), Paragraph(f"Balance sheet - {labels['month']}", _h2)]
    if bal["rows"][0]["a"] is None:
        story.append(_p("No balance sheet has been captured for this month."))
    else:
        story.append(_layout(_balance_cols(labels, var), bal["rows"]))
        story.append(Spacer(1, 1.5 * mm))
        story.append(_p(f"{cur}. Retained earnings include the current year's profit. Forecast and budget balance sheets are shown where they come from a forecast model.", _note_style))
        if com.get("balance"):
            story += [Paragraph("Commentary", _h3), _p(com["balance"])]

    # ---- cash flow
    cfl = M.cashflow_rows(d)
    story += [PageBreak(), Paragraph(f"Cash flow - {labels['month']}", _h2)]
    if cfl["rows"][0]["a"] is None and cfl["rows"][0]["ya"] is None:
        story.append(_p("The cash flow is derived from two balance sheets; the opening or closing balance sheet is missing."))
    else:
        story.append(_layout(_cash_cols(labels, var), cfl["rows"]))
        story.append(Spacer(1, 1.5 * mm))
        story.append(_p(f"{cur}. Indirect method, derived from the movement between balance sheets. Cash is shown net of overdraft.", _note_style))
        if com.get("cash"):
            story += [Paragraph("Commentary", _h3), _p(com["cash"])]

    # ---- KPIs
    if A is not None:
        story += [PageBreak(), Paragraph("Key performance indicators (year to date)", _h2)]
        rows = [["Indicator", "Actual", "Budget", labels["prior"], "Status"]]
        extra = []
        for k in M.kpi_table(d):
            def f(v, k=k):
                if v is None:
                    return "-"
                return {"pct": _pc(v), "x": ("n/m" if v == float("inf") else f"{v:,.2f}x"), "days": f"{v:,.0f}", "money": _n(v), "months": f"{v:,.1f}"}.get(k["fmt"], str(v))
            rows.append([k["label"], f(k["actual"]), f(k["budget"]), f(k["prior"]), {"green": "On track", "amber": "Watch", "red": "Concern"}.get(k["rag"], "")])
            extra.append(k["rag"])
        t = _simple(rows, [90 * mm, 35 * mm, 35 * mm, 35 * mm, 35 * mm])
        st = []
        for i, rg in enumerate(extra, start=1):
            col = {"green": GREEN, "amber": AMBER, "red": RED}.get(rg)
            if col:
                st.append(("TEXTCOLOR", (4, i), (4, i), col))
        t.setStyle(TableStyle(st))
        story.append(t)

    # ---- variances
    if A is not None and V["has_budget"]:
        story += [PageBreak(), Paragraph("Variance analysis - ranked by year-to-date variance to budget", _h2)]
        vl = sorted(M.variance_list(d), key=lambda x: -abs(x["ytd_var"] or 0))
        notes = d.get("line_notes") or {}
        rows = [["Line", "Month actual", "Month budget", "Var", "YTD actual", "YTD budget", "Var", "Var %", "Explanation"]]
        for x in vl:
            rows.append([x["label"], _n(x["month_actual"]), _n(x["month_budget"]), _n(x["month_var"]), _n(x["ytd_actual"]), _n(x["ytd_budget"]), _n(x["ytd_var"]),
                         _pc(x["ytd_pct"]), Paragraph(escape(notes.get(x["key"], "")), _note_style)])
        t = _simple(rows, [42 * mm, 20 * mm, 20 * mm, 17 * mm, 20 * mm, 20 * mm, 17 * mm, 14 * mm, WIDTH - 170 * mm], size=7.2)
        st = [("ALIGN", (8, 0), (8, -1), "LEFT")]
        for i, x in enumerate(vl, start=1):
            col = {"green": GREEN, "amber": AMBER, "red": RED}.get(x["rag"])
            if col and x["ytd_pct"] is not None:
                st.append(("TEXTCOLOR", (7, i), (7, i), col))
        t.setStyle(TableStyle(st))
        story.append(t)

    # ---- variance bridge and scenarios
    try:
        import cf_ma_var as VM
        ex = VM.export_sections(d, "")
    except Exception:
        ex = {"bridge": None, "scen": None}
    b = ex["bridge"]
    if A is not None and b:
        lab = b["labels"]
        story += [PageBreak(), Paragraph(f"Variance bridge - {b['measure_label']}: {lab['comparator']} to {lab['actual']}", _h2)]
        story.append(_waterfall_drawing(b, WIDTH))
        story.append(Spacer(1, 2 * mm))
        run = b["start"]["value"]
        rows = [["Step", f"Amount ({cur})", "Running total"], [b["start"]["label"], _n(b["start"]["value"]), _n(run)]]
        for st in b["steps"]:
            run += st["value"]
            rows.append([st["label"], _n(st["value"]), _n(run)])
        rows.append([b["end"]["label"], _n(b["total"]), _n(b["end"]["value"])])
        t = _simple(rows, [WIDTH - 70 * mm, 35 * mm, 35 * mm], size=7.6)
        t.setStyle(TableStyle([("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
        story.append(t)
        story.append(Spacer(1, 1.5 * mm))
        story += [_p("•  " + x, _base) for x in ex["narr"]]
        dr = ex["drivers"]
        if dr:
            cols = [(c, l) for c, l in VM.DRIVER_COLS if abs(dr["totals"].get(c) or 0) >= 0.5]
            if cols:
                story += [PageBreak(), Paragraph("Variance by cause", _h2)]
                rows = [["Line", "Variance", *[l for _, l in cols]]]
                for r in dr["rows"]:
                    if abs(r["total"]) >= 0.5:
                        rows.append([r["label"], _n(r["total"]), *[_n(r[c]) for c, _ in cols]])
                rows.append(["Profit after tax", _n(dr["totals"]["total"]), *[_n(dr["totals"][c]) for c, _ in cols]])
                w = (WIDTH - 52 * mm) / (len(cols) + 1)
                t = _simple(rows, [52 * mm] + [w] * (len(cols) + 1), size=7.2)
                t.setStyle(TableStyle([("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
                story.append(t)
                story.append(_p(f"{cur}. Favourable positive, adverse in brackets. Amounts not assigned to a cause by the units and tags entered in the pack are shown as Unexplained.", _note_style))
    sc = ex.get("scen")
    if A is not None and sc:
        story += [PageBreak(), Paragraph(f"Reforecast scenarios - full year {labels['fy']}", _h2)]
        head = ["", "Pack outlook", *[c["name"] for c in sc["cols"]], "Budget"]
        rows = [head]
        for t_ in sc["table"]:
            rows.append([t_["label"], _n(t_["current"]), *[_n(c["fy"].get(t_["key"])) for c in sc["cols"]], _n(t_["budget"])])
        cash_cur = None
        rows.append(["Year-end cash (net of overdraft)", "-", *[_n((c.get("cash") or {}).get("year_end")) for c in sc["cols"]], "-"])
        w = (WIDTH - 52 * mm) / (len(head) - 1)
        story.append(_simple(rows, [52 * mm] + [w] * (len(head) - 1), size=7.4))
        story.append(_p(f"{cur}. Actual months to {labels['month']} are as recorded; the remaining months are rebuilt from each scenario's starting point and changes. Scenarios are indicative and do not change the forecast in the pack.", _note_style))

    # ---- commentary & basis
    more = [(k, lab) for k, lab in (("outlook", "Full-year outlook"), ("actions", "Actions and recommendations")) if com.get(k)]
    story += [PageBreak(), Paragraph("Commentary", _h2)]
    if more:
        for k, lab in more:
            story += [Paragraph(lab, _h3), _p(com[k])]
    else:
        story.append(_p("No further commentary has been added to this pack."))
    story += [Paragraph("Basis of preparation", _h2)]
    for b in _basis_lines(d, V, status):
        story.append(_p("•  " + b, _base))
    doc.build(story, onFirstPage=_footer(firm, f"{pack.pack_number} - Management accounts - {labels['month']}"),
              onLaterPages=_footer(firm, f"{pack.pack_number} - Management accounts - {labels['month']}"))
    return buf.getvalue()


from cf_pdf import _note as _note_style  # noqa: E402
