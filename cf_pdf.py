"""PDF reports for the Corporate Finance & Business Advisory module (forecast model report and investment
appraisal report). Same letterhead design as the invoice PDF; landscape A4 so ten-year statements fit."""
import io
import os
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

import cf_engine as E
import cf_report as R
from invoice_pdf import NAVY, NAVY_DARK, GOLD, GOLD_LIGHT, INK, MUTED, LINE, TINT, LOGO, _base, _small

PAGE = landscape(A4)
MARGIN = 14 * mm
WIDTH = PAGE[0] - 2 * MARGIN
GREEN = colors.HexColor("#1b7a3d")
RED = colors.HexColor("#b3261e")

_h2 = ParagraphStyle("cfh2", parent=_base, fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=NAVY, spaceBefore=8, spaceAfter=4)
_h3 = ParagraphStyle("cfh3", parent=_base, fontName="Helvetica-Bold", fontSize=9.5, leading=13, textColor=NAVY, spaceBefore=6, spaceAfter=2)
_note = ParagraphStyle("cfnote", parent=_small, fontSize=7.5, leading=10)


def _p(text, style=_base):
    return Paragraph(escape(str(text if text is not None else "")).replace("\n", "<br/>"), style)


def _band(firm, title, number, sub):
    white = ParagraphStyle("w", parent=_base, textColor=colors.white)
    contact = [firm.get("address"), "  ·  ".join(x for x in (firm.get("web"), firm.get("email"), firm.get("phone")) if x)]
    firm_cell = [
        Paragraph(f"<font name='Helvetica-Bold' size='15'>{escape(firm.get('name', ''))}</font>", ParagraphStyle("fn", parent=white, leading=19)),
        Paragraph(f"<font color='#d4af6a' size='7.5'>{escape((firm.get('strapline') or firm.get('tagline') or '').upper())}</font>", ParagraphStyle("st", parent=white, leading=11)),
        Spacer(1, 3),
    ] + [Paragraph(f"<font color='#cdd8e4' size='8'>{escape(c)}</font>", white) for c in contact if c]
    r = lambda n: ParagraphStyle(n, parent=white, alignment=TA_RIGHT)
    doc_cell = [
        Paragraph(f"<font name='Helvetica-Bold' size='15' color='#d4af6a'>{escape(title)}</font>", ParagraphStyle("r", parent=white, alignment=TA_RIGHT, leading=19)),
        Paragraph(f"<font name='Helvetica-Bold' size='11'>{escape(number)}</font>", r("r2")),
        Paragraph(f"<font color='#cdd8e4' size='8'>{escape(sub)}</font>", r("r3")),
    ]
    logo = Image(LOGO, 22 * mm, 22 * mm) if os.path.exists(LOGO) else ""
    band = Table([[logo, firm_cell, doc_cell]], colWidths=[28 * mm, WIDTH - 28 * mm - 90 * mm, 90 * mm])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LINEBELOW", (0, 0), (-1, -1), 3, GOLD),
    ]))
    return band


def _footer(firm, label):
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY_DARK)
        canvas.rect(0, 0, PAGE[0], 9 * mm, stroke=0, fill=1)
        canvas.setFillColor(GOLD)
        canvas.rect(0, 9 * mm, PAGE[0], 0.8 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor("#cdd8e4"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(MARGIN, 3.4 * mm, f"{firm.get('name', '')}  ·  {label}")
        canvas.drawRightString(PAGE[0] - MARGIN, 3.4 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return footer


def _grid(columns, lines, first_w=62 * mm, size=None, heads=False):
    """A statement-style table from cf_report lines."""
    n = len(columns)
    if size is None:
        size = 7.6 if n <= 8 else (7.0 if n <= 11 else 6.4)
    col_w = (WIDTH - first_w) / max(n, 1)
    if col_w > 27 * mm:
        col_w = 27 * mm
        first_w = WIDTH - col_w * n if WIDTH - col_w * n > first_w else first_w
    data = [[""] + list(columns)]
    styles = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", size), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), TINT), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", size),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
    ]
    for ln in lines:
        i = len(data)
        if ln["kind"] == "gap":
            data.append([""] * (n + 1))
            styles += [("TOPPADDING", (0, i), (-1, i), 0.5), ("BOTTOMPADDING", (0, i), (-1, i), 0.5), ("LINEBELOW", (0, i), (-1, i), 0, LINE)]
            continue
        if ln["kind"] == "head":
            if heads:
                data.append([ln["label"]] + [""] * n)
                styles += [("FONT", (0, i), (-1, i), "Helvetica-Bold", size), ("BACKGROUND", (0, i), (-1, i), TINT)]
            continue  # otherwise the section title above the table already says it
        label = ("    " if ln["kind"] == "indent" else "") + ln["label"]
        data.append([label] + [R.fmt_value(v, ln.get("fmt", "money")) for v in ln["values"]])
        if ln["kind"] in ("total", "grand"):
            styles += [("FONT", (0, i), (-1, i), "Helvetica-Bold", size), ("LINEABOVE", (0, i), (-1, i), 0.6, NAVY)]
        if ln["kind"] == "grand":
            styles += [("BACKGROUND", (0, i), (-1, i), TINT), ("LINEBELOW", (0, i), (-1, i), 1.2, NAVY)]
        if ln["kind"] == "memo":
            styles += [("FONT", (0, i), (-1, i), "Helvetica-Oblique", size), ("TEXTCOLOR", (0, i), (-1, i), MUTED)]
        if ln["kind"] == "indent":
            styles += [("TEXTCOLOR", (0, i), (0, i), MUTED)]
    t = Table(data, colWidths=[first_w] + [col_w] * n, repeatRows=1)
    t.setStyle(TableStyle(styles))
    return t


def _simple(rows, widths, header=True, size=8, align_right_from=1, bold_last=False):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [("FONT", (0, 0), (-1, -1), "Helvetica", size), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
          ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), TINT), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", size)]
    if bold_last:
        st += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", size), ("LINEABOVE", (0, -1), (-1, -1), 0.6, NAVY)]
    t.setStyle(TableStyle(st))
    return t


def _kv(pairs, size=8.5):
    """A 4-column facts strip: label/value pairs laid out two per row-pair."""
    rows = []
    for i in range(0, len(pairs), 4):
        chunk = pairs[i:i + 4]
        rows.append([Paragraph(f"<font size='7' color='#6b7684'>{escape(k.upper())}</font><br/><font name='Helvetica-Bold' size='{size}'>{escape(str(v))}</font>", _base)
                     for k, v in chunk] + [""] * (4 - len(chunk)))
    t = Table(rows, colWidths=[WIDTH / 4] * 4)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE)]))
    return t


def _n(v, dp=0):
    return R.fmt_value(v, "money", dp)


def _x(v):
    return "-" if v is None else f"{v:,.2f}x"


def _pc(v, dp=1):
    return "-" if v is None else f"{v:,.{dp}f}%"


# =========================================================================== forecast model report

def build_model_pdf(model, scenario, result, firm, valuation=None, scenarios=None, bva=None):
    a = result["a"]
    cur = a["currency"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=11 * mm, bottomMargin=16 * mm,
                            title=f"{model.model_number} {model.name}", author=firm.get("name", ""))
    story = [_band(firm, "FINANCIAL FORECAST", model.model_number, f"{cur}  ·  {model.status}"), Spacer(1, 5 * mm)]
    head = R.headline(result)
    story.append(Paragraph(escape(model.name), ParagraphStyle("t", parent=_base, fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=NAVY)))
    story.append(Spacer(1, 2 * mm))
    story.append(_kv([
        ("Client", model.client.name), ("Forecast period", f"{a['years']} year{'s' if a['years'] != 1 else ''} from {result['periods'][0]['label']}"),
        ("Scenario", scenario.name if scenario is not None else "Base case"), ("Currency", cur),
        ("Prepared", date.today().strftime("%d %B %Y")),
        ("Prepared by", (model.updated_by.name if model.updated_by else (model.created_by.name if model.created_by else "-"))),
        ("Engagement", model.engagement.title if model.engagement else "-"),
        ("Status", model.status + (f" - finalised by {model.finalised_by.name}" if model.is_final and model.finalised_by else "")),
    ]))
    if model.purpose:
        story += [Spacer(1, 3 * mm), Paragraph("Purpose and basis of preparation", _h3), _p(model.purpose)]

    # ---- headline
    story += [Paragraph("Summary", _h2)]
    ann = result["annual"]
    rt = E.ratios(result)
    rows = [["", *[d["label"] for d in ann]]]
    for label, key in [("Revenue", "revenue"), ("EBITDA", "ebitda"), ("Profit after tax", "pat"), ("Net cash from operations", "cfo"),
                       ("Closing cash", "cash"), ("Borrowings", "borrowings"), ("Total equity", "total_equity")]:
        rows.append([label, *[_n(d[key]) for d in ann]])
    rows.append(["Debt service cover", *[_x(r["dscr"]) for r in rt]])
    cw = min(26 * mm, (WIDTH - 60 * mm) / len(ann))
    story.append(_simple(rows, [60 * mm] + [cw] * len(ann)))
    facts = [f"Revenue grows from {_n(head['revenue_first'])} to {_n(head['revenue_last'])}"
             + (f" ({head['cagr'] * 100:.1f}% a year)" if head["cagr"] is not None else "") + ".",
             f"Total profit after tax over the period: {_n(head['pat_total'])}. Lowest month-end cash balance: {_n(head['cash_min'])}."]
    if head["covenants_tested"]:
        facts.append(f"Covenant tests: {head['breaches']} breach(es) across {head['covenants_tested']} covenant(s) tested." if head["breaches"]
                     else f"All {head['covenants_tested']} covenant test(s) are met in every year.")
    for w in result["warnings"]:
        facts.append("Note: " + w)
    story += [Spacer(1, 2 * mm)] + [_p("•  " + f, _small) for f in facts]

    # ---- key assumptions
    story += [Paragraph("Key assumptions", _h2)]
    ar = [["", *[E.year_label(a, y) for y in range(1, a["years"] + 1)]]]
    ar.append(["Revenue growth (first stream)", *["-" if y == 0 else _pc(a["revenue"][0]["growth"][y]) for y in range(a["years"])]])
    ar.append(["Cost of sales (% of revenue)", *[_pc(v) for v in a["cogs_pct"]]])
    ar.append(["Capital expenditure", *[_n(v) for v in a["capex"]]])
    ar.append(["Receivable / inventory / payable days", *[f"{a['rec_days'][y]:.0f}/{a['inv_days'][y]:.0f}/{a['pay_days'][y]:.0f}" for y in range(a["years"])]])
    ar.append(["Dividend payout (% of profit)", *[_pc(v, 0) for v in a["dividend_payout"]]])
    story.append(_simple(ar, [60 * mm] + [cw] * a["years"], size=7.6))
    story.append(Spacer(1, 1.5 * mm))
    story.append(_p(f"Income tax {a['tax_rate']:.2f}%. Depreciation {a['dep_rate']:.1f}% of cost a year. Year 1 is forecast monthly with seasonal phasing; later years are annual. "
                    "Interest is charged on opening balances; amounts exclude VAT.", _note))

    # ---- statements
    tabs = R.statement_tables(result, "annual")
    for title, lines in tabs["statements"]:
        story.append(KeepTogether([Paragraph(f"{title} ({cur})", _h2), _grid(tabs["columns"], lines)]))
        story.append(Spacer(1, 3 * mm))
    story.append(PageBreak())
    mt = R.statement_tables(result, "monthly")
    story.append(Paragraph(f"Year 1 by month ({cur})", _h2))
    for title, lines in mt["statements"]:
        story.append(KeepTogether([Paragraph(title, _h3), _grid(mt["columns"], lines, first_w=44 * mm, size=6.2)]))
        story.append(Spacer(1, 3 * mm))

    # ---- cash flow forecast (monthly, reconciled to the cash flow statement)
    cff = R.cashflow_forecast(result, later_years=False)
    story += [PageBreak(), Paragraph(f"Cash flow forecast - year 1 by month ({cur})", _h2),
              _p("Every cash movement under its heading, built separately from the cash flow statement and reconciled to it: "
                 + ("the two agree in every month." if cff["ok"] else "<b>WARNING: the two do not agree.</b>"), _note),
              Spacer(1, 1.5 * mm), _grid(cff["columns"], cff["lines"], first_w=46 * mm, size=5.9, heads=True)]

    # ---- loans & covenants
    story += [PageBreak(), Paragraph("Loans and covenants", _h2)]
    if a["loans"]:
        for k, L in enumerate(a["loans"]):
            lines = [{"kind": "line", "label": "Opening balance", "values": [d["loans"][k]["open"] for d in ann], "fmt": "money"},
                     {"kind": "line", "label": "Drawdown", "values": [d["loans"][k]["draw"] for d in ann], "fmt": "money"},
                     {"kind": "line", "label": "Repayment", "values": [-d["loans"][k]["repay"] for d in ann], "fmt": "money"},
                     {"kind": "total", "label": "Closing balance", "values": [d["loans"][k]["close"] for d in ann], "fmt": "money"},
                     {"kind": "line", "label": "Interest charged", "values": [d["loans"][k]["interest"] for d in ann], "fmt": "money"}]
            terms = (f"{L['rate']:.2f}% a year; " + (f"bullet repayment in year {L['maturity_year']}" if L["repay_type"] == "bullet"
                     else f"equal capital over {L['repay_years']} year(s)" + (f" after {L['grace_years']} year(s) grace" if L["grace_years"] else "")))
            story.append(KeepTogether([Paragraph(f"{escape(L['name'])} - {terms}", _h3), _grid([d["label"] for d in ann], lines)]))
            story.append(Spacer(1, 2 * mm))
    else:
        story.append(_p("There are no loans in the model.", _small))
    tests = E.covenant_tests(result, rt)
    if tests:
        story.append(Paragraph("Covenant tests", _h3))
        data = [["Covenant", "Limit", *[d["label"] for d in ann], "Result"]]
        sty = []
        for i, t in enumerate(tests, start=1):
            data.append([t["label"][:46], f"{'min' if t['kind'] == 'min' else 'max'} {t['limit']:.2f}x",
                         *[("n/a" if c["value"] is None else f"{c['value']:.2f}x") for c in t["cells"]],
                         "All pass" if not t["breaches"] else f"{t['breaches']} breach(es)"])
            for j, c in enumerate(t["cells"], start=2):
                sty.append(("TEXTCOLOR", (j, i), (j, i), GREEN if c["ok"] else RED))
                if not c["ok"]:
                    sty.append(("FONT", (j, i), (j, i), "Helvetica-Bold", 7.6))
        tb = _simple(data, [66 * mm, 22 * mm] + [cw] * len(ann) + [22 * mm], size=7.6)
        tb.setStyle(TableStyle(sty))
        story.append(tb)

    # ---- ratios and break-even
    story += [PageBreak(), Paragraph("Key ratios", _h2)]
    rtab = R.ratio_table(result)
    data = [[""] + rtab["columns"]]
    for r in rtab["rows"]:
        data.append([r["label"]] + [R.fmt_value(v, r["fmt"]) for v in r["values"]])
    story.append(_simple(data, [60 * mm] + [cw] * len(rtab["columns"]), size=7.6))
    be = E.break_even(result)
    data = [[""] + [b["label"] for b in be],
            ["Break-even revenue"] + [("n/a" if b["break_even_revenue"] is None else _n(b["break_even_revenue"])) for b in be],
            ["Revenue"] + [_n(b["revenue"]) for b in be],
            ["Margin of safety"] + [("n/a" if b["margin_of_safety"] is None else f"{b['margin_of_safety'] * 100:.1f}%") for b in be]]
    story.append(KeepTogether([Paragraph("Break-even", _h3), _simple(data, [60 * mm] + [cw] * len(be), size=7.6)]))

    # ---- scenarios
    if scenarios:
        story += [PageBreak(), Paragraph("Scenario comparison", _h2)]
        data = [["", *[s["name"] for s in scenarios]]]
        for label, key in [("Revenue - year 1", "revenue_y1"), ("Revenue - final year", "revenue_last"), ("EBITDA - final year", "ebitda_last"),
                           ("Total profit after tax", "pat_total"), ("Closing cash - final year", "cash_last"), ("Lowest cash balance", "cash_min"),
                           ("Borrowings - final year", "borrowings_last")]:
            data.append([label, *[_n(s["summary"][key]) for s in scenarios]])
        data.append(["Lowest debt service cover", *[_x(s["summary"]["dscr_min"]) for s in scenarios]])
        w = min(36 * mm, (WIDTH - 60 * mm) / len(scenarios))
        story.append(_simple(data, [60 * mm] + [w] * len(scenarios)))
        notes = [f"{s['name']}: {s['notes']}" for s in scenarios if s.get("notes")]
        story += [Spacer(1, 2 * mm)] + [_p(n, _note) for n in notes]

    # ---- valuation
    if valuation:
        d, v, w_ = valuation["dcf"], valuation["v"], valuation["wacc"]
        vo = valuation["obj"]
        story += [PageBreak(), Paragraph(f"Valuation - {escape(vo.name)}", _h2)]
        story.append(_kv([("Valuation date", vo.valuation_date.strftime("%d %B %Y") if vo.valuation_date else "-"),
                          ("Discount rate (WACC)", f"{d['wacc']:.2f}%"),
                          ("Terminal value", f"{'Gordon growth ' + format(d['growth'], '.1f') + '%' if d['method'] == 'gordon' else 'Exit multiple ' + format(d['exit_multiple'], '.1f') + 'x'}"),
                          ("Cost of equity / after-tax debt", f"{w_['ke']:.2f}% / {w_['kd_after_tax']:.2f}%")]))
        story.append(Spacer(1, 2 * mm))
        rows = [["", *[r["label"] for r in d["rows"]]],
                ["Free cash flow", *[_n(r["fcff"]) for r in d["rows"]]],
                ["Discount period", *[f"{r['t']:.1f}" for r in d["rows"]]],
                ["Present value", *[_n(r["pv"]) for r in d["rows"]]]]
        story.append(_simple(rows, [40 * mm] + [min(26 * mm, (WIDTH - 40 * mm) / len(d["rows"]))] * len(d["rows"]), size=7.6))
        story.append(Spacer(1, 2 * mm))
        bridge = [["Sum of present values of cash flows", _n(d["pv_fcff"])], ["Present value of terminal value", _n(d["pv_tv"])],
                  ["Enterprise value", _n(d["ev"])], ["Less: debt", _n(-d["debt"])], ["Add: cash", _n(d["cash"])],
                  ["Other adjustments less minorities", _n(d["other"] - d["minorities"])], ["Equity value (100%)", _n(d["equity"])],
                  [f"Stake {d['stake_pct']:.0f}% after {d['dlom']:.0f}% marketability discount", _n(d["equity_after_dlom"])]]
        story.append(_simple(bridge, [90 * mm, 34 * mm], header=False, size=8, bold_last=True))
        if d["tv_share"] is not None:
            story.append(_p(f"Terminal value is {d['tv_share'] * 100:.0f}% of enterprise value.", _note))
        mult = valuation["mult"]
        if mult["methods"]:
            story.append(Paragraph("Trading multiples", _h3))
            rows = [["Method", "Metric", "Low", "Median", "High", "Equity (low)", "Equity (median)", "Equity (high)"]]
            for mm_ in mult["methods"]:
                rows.append([mm_["label"], _n(mm_["metric_value"]), f"{mm_['stats']['low']:.1f}x", f"{mm_['stats']['median']:.1f}x", f"{mm_['stats']['high']:.1f}x",
                             _n(mm_["equity"]["low"]), _n(mm_["equity"]["median"]), _n(mm_["equity"]["high"])])
            story.append(_simple(rows, [48 * mm] + [28 * mm] * 7, size=7.6))
        ff = valuation["ff"]
        story.append(Paragraph("Equity value by method (football field)", _h3))
        rows = [["Method", "Low", "Mid", "High"]] + [[r["label"], _n(r["low"]), _n(r["mid"]), _n(r["high"])] for r in ff]
        story.append(_simple(rows, [90 * mm, 34 * mm, 34 * mm, 34 * mm], size=7.6))
        if v["notes"]:
            story += [Paragraph("Notes", _h3), _p(v["notes"], _small)]

    # ---- budget vs actual
    if bva and bva["n"]:
        story += [PageBreak(), Paragraph(f"Budget versus actual - cumulative to {bva['periods'][-1]['label']}", _h2)]
        rows = [["Line", "Budget", "Actual", "Variance", "Variance %", "Result"]]
        sty = []
        for i, ln in enumerate(bva["cumulative"], start=1):
            res = "-" if ln["var"] is None else ("On budget" if ln["fav"] is None else ("Favourable" if ln["fav"] else "Adverse"))
            rows.append([ln["label"], _n(ln["budget"]), _n(ln["actual"]), _n(ln["var"]), "-" if ln["pct"] is None else f"{ln['pct']:.1f}%", res])
            if ln["fav"] is not None:
                sty.append(("TEXTCOLOR", (3, i), (5, i), GREEN if ln["fav"] else RED))
        tb = _simple(rows, [60 * mm, 32 * mm, 32 * mm, 32 * mm, 26 * mm, 28 * mm], size=8)
        tb.setStyle(TableStyle(sty))
        story.append(tb)

    story += [Spacer(1, 6 * mm), _p("This forecast is based on the assumptions stated above, which are the responsibility of the client's management. "
                                    "It is a projection of what may happen, not a statement of fact; actual results will differ, and the "
                                    "differences may be material. It has not been audited or reviewed and gives no assurance.", _note)]
    foot = _footer(firm, f"{model.model_number}  ·  {model.name}")
    doc.build(story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()


# =========================================================================== investment appraisal report

def build_project_pdf(proj, inputs, an, sens, narrative, firm):
    cur = inputs["currency"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=11 * mm, bottomMargin=16 * mm,
                            title=f"{proj.project_number} {proj.name}", author=firm.get("name", ""))
    story = [_band(firm, "INVESTMENT APPRAISAL", proj.project_number, f"{cur}  ·  {proj.status}"), Spacer(1, 5 * mm)]
    story.append(Paragraph(escape(proj.name), ParagraphStyle("t", parent=_base, fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=NAVY)))
    story.append(Spacer(1, 2 * mm))
    story.append(_kv([("Client", proj.client.name), ("Engagement", proj.engagement.title if proj.engagement else "-"),
                      ("Prepared", date.today().strftime("%d %B %Y")), ("Discount rate", f"{inputs['discount_rate']:.2f}%")]))
    story.append(Paragraph("Result", _h2))
    verdict = "Accept - the project adds value at the required rate of return." if an["accept"] else "Reject - the project does not earn the required rate of return."
    story.append(Paragraph(f"<b>{escape(verdict)}</b>", ParagraphStyle("v", parent=_base, textColor=GREEN if an["accept"] else RED, fontSize=10)))
    story.append(Spacer(1, 2 * mm))
    rows = [["Net present value", _n(an["npv"])],
            ["Internal rate of return", "n/a" if an["irr"] is None else f"{an['irr']:.2f}%"],
            ["Modified IRR (reinvesting at " + f"{inputs['reinvest_rate']:.1f}%)", "n/a" if an["mirr"] is None else f"{an['mirr']:.2f}%"],
            ["Payback period", "not repaid" if an["payback"] is None else f"{an['payback']:.2f} years"],
            ["Discounted payback", "not repaid" if an["discounted_payback"] is None else f"{an['discounted_payback']:.2f} years"],
            ["Profitability index", "n/a" if an["pi"] is None else f"{an['pi']:.2f}x"],
            ["Equivalent annual annuity", "n/a" if an["eaa"] is None else _n(an["eaa"])]]
    story.append(_simple(rows, [80 * mm, 40 * mm], header=False, size=8.5))
    story.append(Paragraph("Cash flows", _h2))
    n = len(an["rows"])
    cw = min(24 * mm, (WIDTH - 44 * mm) / n)
    data = [["", *[f"Year {r['year']}" for r in an["rows"]]],
            ["Cash flow", *[_n(r["cash_flow"]) for r in an["rows"]]],
            ["Cumulative", *[_n(r["cumulative"]) for r in an["rows"]]],
            ["Discount factor", *[f"{r['df']:.4f}" for r in an["rows"]]],
            ["Present value", *[_n(r["pv"]) for r in an["rows"]]],
            ["Cumulative present value", *[_n(r["cum_pv"]) for r in an["rows"]]]]
    story.append(_simple(data, [44 * mm] + [cw] * n, size=7.6))
    story.append(Paragraph("Sensitivity", _h2))
    t1 = _simple([["Discount rate", "NPV"]] + [[f"{r['rate']:.1f}%", _n(r["npv"])] for r in sens["by_rate"]], [30 * mm, 34 * mm], size=7.6)
    t2 = _simple([["Change in inflows", "NPV"]] + [[f"{r['change']:+d}%" if r["change"] else "0%", _n(r["npv"])] for r in sens["by_inflow"]], [34 * mm, 34 * mm], size=7.6)
    t3 = _simple([["Change in investment", "NPV"]] + [[f"{r['change']:+d}%" if r["change"] else "0%", _n(r["npv"])] for r in sens["by_invest"]], [38 * mm, 34 * mm], size=7.6)
    wrap = Table([[t1, t2, t3]], colWidths=[WIDTH / 3] * 3)
    wrap.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(wrap)
    from cf_tools import FEASIBILITY_SECTIONS
    filled = [(label, (narrative or {}).get(key, "")) for key, label in FEASIBILITY_SECTIONS if (narrative or {}).get(key)]
    if filled:
        story.append(PageBreak())
        story.append(Paragraph("Feasibility notes", _h2))
        for label, text in filled:
            story += [Paragraph(escape(label), _h3), _p(text)]
    story += [Spacer(1, 6 * mm), _p("The appraisal is based on cash flow estimates supplied by or agreed with the client's management. "
                                    "Results are only as reliable as those estimates.", _note)]
    foot = _footer(firm, f"{proj.project_number}  ·  {proj.name}")
    doc.build(story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()
