"""Monthly trial balances for Accounting & Bookkeeping engagements.

Each month the client's (or our) trial balance is uploaded, either as that month's MOVEMENTS or as the CLOSING balances at month-end;
an OPENING balance upload starts the year. The uploads are consolidated cumulatively - month by month - and at year end the cumulative
trial balance is reconciled to the engagement's year-end Trial Balance. The cumulative trial balance for a month is also what feeds
that month into the Management Accounts pack (corporate_finance_ma.py, route pack_tb_from_monthly).

The arithmetic is plain functions on lists of {code, name, net_dr} (debit positive, credit negative) so it can be tested without a database.
"""
import io
import re
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user

from extensions import db
from models import Engagement, MonthlyTB, MonthlyTBLine, MONTHLY_TB_BASES, TrialBalance
from engagements import _ensure_engagement_access

monthly_tb_bp = Blueprint("monthly_tb", __name__, url_prefix="/monthly-tb")

TOLERANCE = 0.5
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
BASIS_LABEL = {"movement": "Month's movements", "closing": "Closing balances", "opening": "Opening balances"}
_BASIS_ORDER = {"opening": 0, "movement": 1, "closing": 1}


# --------------------------------------------------------------------------- months

def parse_month(key):
    m = re.fullmatch(r"(\d{4})-(\d{2})", str(key or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"bad month {key!r}")
    return int(m.group(1)), int(m.group(2))


def mkey(y, m):
    return f"{y:04d}-{m:02d}"


def shift_month(key, n):
    y, m = parse_month(key)
    i = y * 12 + (m - 1) + n
    return mkey(i // 12, i % 12 + 1)


def month_label(key):
    y, m = parse_month(key)
    return f"{MONTH_ABBR[m - 1]} {y}"


def months_between(first, last):
    out, cur = [], first
    while cur <= last and len(out) < 36:
        out.append(cur)
        cur = shift_month(cur, 1)
    return out


def period_months(period_start, period_end):
    """The months of the engagement's financial year: period_start's month to period_end's month, or the twelve months ending on period_end."""
    if not period_end:
        return []
    end = mkey(period_end.year, period_end.month)
    if period_start:
        start = mkey(period_start.year, period_start.month)
        if start > end:
            start = shift_month(end, -11)
    else:
        start = shift_month(end, -11)
    return months_between(start, end)


# --------------------------------------------------------------------------- keys

def norm_name(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _key_resolver(rows_by_upload):
    """Account key = the client's account code when it has one, otherwise the normalised name. A name seen with a code in any upload uses that
    code everywhere, so a file that leaves the code column out still lands on the same account."""
    name_to_code = {}
    for rows in rows_by_upload:
        for r in rows:
            code = (r.get("code") or "").strip()
            if code:
                name_to_code.setdefault(norm_name(r["name"]), code)

    def key(r):
        code = (r.get("code") or "").strip()
        if code:
            return "c:" + code.lower()
        n = norm_name(r["name"])
        return ("c:" + name_to_code[n].lower()) if n in name_to_code else "n:" + n
    return key


# --------------------------------------------------------------------------- consolidation

def consolidate(uploads):
    """uploads: [{id, period 'YYYY-MM', basis, filename, rows:[{code,name,net_dr}]}].

    Returns {months: [{period, label, cum:{key:amt}, mov:{key:amt}, tb_sum, cum_sum, uploads:[...], basis}], accounts:{key:{code,name}},
             order:[keys], warnings:[...]}.  Uploads are applied in month order; an opening upload sets the starting balances, a movements
    upload is added to the running total, a closing upload replaces it (that month's movement is the closing less the running total)."""
    ups = sorted(uploads, key=lambda u: (u["period"], _BASIS_ORDER.get(u["basis"], 1), u.get("id") or 0))
    key = _key_resolver([u["rows"] for u in ups])
    accounts, warnings = {}, []
    cum = {}
    months = []
    seen_opening = False
    for u in ups:
        mov = {}
        sums = {}
        for r in u["rows"]:
            k = key(r)
            sums[k] = sums.get(k, 0.0) + float(r["net_dr"] or 0)
            a = accounts.setdefault(k, {"code": (r.get("code") or "").strip(), "name": r["name"]})
            if not a["code"] and (r.get("code") or "").strip():
                a["code"] = r["code"].strip()
        if u["basis"] == "opening":
            if seen_opening:
                warnings.append(f"More than one opening balance upload - the later one ({u.get('filename') or u['period']}) replaced the earlier.")
            seen_opening = True
            mov = dict(sums)
            cum = dict(sums)
        elif u["basis"] == "closing":
            for k in set(cum) | set(sums):
                mov[k] = round(sums.get(k, 0.0) - cum.get(k, 0.0), 2)
            cum = dict(sums)
        else:
            for k, v in sums.items():
                mov[k] = v
                cum[k] = round(cum.get(k, 0.0) + v, 2)
        existing = next((m for m in months if m["period"] == u["period"]), None)
        entry = {"basis": u["basis"], "filename": u.get("filename"), "id": u.get("id"), "tb_sum": round(sum(sums.values()), 2)}
        snap = {k: round(v, 2) for k, v in cum.items()}
        if existing:     # two uploads for one month (e.g. an opening plus the first month): the month shows the position after both
            existing["cum"] = snap
            for k, v in mov.items():
                existing["mov"][k] = round(existing["mov"].get(k, 0.0) + v, 2)
            existing["uploads"].append(entry)
            existing["basis"] = "+".join(sorted({x["basis"] for x in existing["uploads"]}))
        else:
            months.append({"period": u["period"], "label": month_label(u["period"]), "cum": snap,
                           "mov": {k: round(v, 2) for k, v in mov.items()}, "uploads": [entry], "basis": u["basis"]})
    for m in months:
        m["cum_sum"] = round(sum(m["cum"].values()), 2)
        m["mov_sum"] = round(sum(m["mov"].values()), 2)
    real = [m["period"] for m in months if any(x["basis"] != "opening" for x in m["uploads"])]
    if len(real) >= 2:
        missing = [month_label(p) for p in months_between(real[0], real[-1]) if p not in real]
        if missing:
            warnings.append("No trial balance uploaded for " + ", ".join(missing) + ". " +
                            "Movements for those months are missing from the cumulative figures unless the next upload is a closing balance.")
    if months and not any(x["basis"] in ("opening", "closing") for m in months for x in m["uploads"]):
        warnings.append("No opening balances or closing balances have been uploaded, so balance sheet accounts show only the movements since the first upload. "
                        "Upload the opening balances (the previous year-end balance sheet) to start the year.")
    for m in months:
        for x in m["uploads"]:
            if abs(x["tb_sum"]) > TOLERANCE:
                warnings.append(f"{m['label']} ({BASIS_LABEL.get(x['basis'], x['basis'])}): debits less credits = {x['tb_sum']:,.2f} - the file does not balance.")
    order = sorted(accounts, key=lambda k: (accounts[k]["code"] == "", _code_sort(accounts[k]["code"]), norm_name(accounts[k]["name"])))
    return {"months": months, "accounts": accounts, "order": order, "warnings": warnings}


def _code_sort(code):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", code or "") if p != ""] or [""]


def rows_for_month(cons, period):
    """The cumulative trial balance at `period` (the latest upload on or before it) as [{code,name,net_dr}] - the shape the Management
    Accounts upload reads. Returns None when nothing has been uploaded up to that month."""
    snap = None
    for m in cons["months"]:
        if m["period"] <= period:
            snap = m
    if snap is None:
        return None
    out = []
    for k in cons["order"]:
        v = snap["cum"].get(k)
        if v is None or abs(v) < 0.005:
            continue
        a = cons["accounts"][k]
        out.append({"code": a["code"], "name": a["name"], "net_dr": round(v, 2)})
    return out


# --------------------------------------------------------------------------- reconciliation

def reconcile(cum_rows, yearend_rows):
    """cum_rows / yearend_rows: [{code,name,net_dr}]. Matches by account code, then by name. Returns rows with the difference
    (cumulative less year-end), the accounts found on one side only, totals and a Reconciled flag."""
    ye = [dict(r, used=False) for r in yearend_rows]
    by_code, by_name = {}, {}
    for r in ye:
        c = (r.get("code") or "").strip().lower()
        if c:
            by_code.setdefault(c, []).append(r)
        by_name.setdefault(norm_name(r["name"]), []).append(r)

    def take(pool):
        for r in pool or []:
            if not r["used"]:
                r["used"] = True
                return r
        return None

    lines = []
    for c in cum_rows:
        code = (c.get("code") or "").strip().lower()
        hit = take(by_code.get(code)) if code else None
        if hit is None:
            hit = take(by_name.get(norm_name(c["name"])))
        ye_amt = hit["net_dr"] if hit else None
        lines.append({"code": c.get("code") or (hit or {}).get("code") or "", "name": c["name"], "cum": c["net_dr"], "yearend": ye_amt,
                      "diff": round(c["net_dr"] - (ye_amt or 0.0), 2), "where": "both" if hit else "monthly only"})
    for r in ye:
        if not r["used"]:
            lines.append({"code": r.get("code") or "", "name": r["name"], "cum": None, "yearend": r["net_dr"],
                          "diff": round(0.0 - r["net_dr"], 2), "where": "year-end only"})
    lines.sort(key=lambda x: (x["code"] == "", _code_sort(x["code"]), norm_name(x["name"])))
    off = [x for x in lines if abs(x["diff"]) > TOLERANCE]
    totals = {"cum": round(sum(x["cum"] or 0 for x in lines), 2), "yearend": round(sum(x["yearend"] or 0 for x in lines), 2),
              "diff": round(sum(x["diff"] for x in lines), 2), "abs_diff": round(sum(abs(x["diff"]) for x in off), 2)}
    return {"lines": lines, "off": off, "totals": totals, "reconciled": not off,
            "monthly_only": [x for x in lines if x["where"] == "monthly only" and abs(x["diff"]) > TOLERANCE],
            "yearend_only": [x for x in lines if x["where"] == "year-end only" and abs(x["diff"]) > TOLERANCE]}


# --------------------------------------------------------------------------- loading from the database

def load_uploads(engagement):
    ups = []
    for t in MonthlyTB.query.filter_by(engagement_id=engagement.id).order_by(MonthlyTB.period, MonthlyTB.id).all():
        ups.append({"id": t.id, "period": t.period, "basis": t.basis, "filename": t.original_filename, "note": t.note,
                    "created_at": t.created_at, "created_by": t.created_by.name if t.created_by else "",
                    "rows": [{"code": l.account_code or "", "name": l.account_name, "net_dr": l.net_dr or 0.0} for l in t.lines]})
    return ups


def yearend_rows(engagement):
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    if not tb:
        return None
    return [{"code": l.account_code or "", "name": l.account_name, "net_dr": round((l.current_debit or 0) - (l.current_credit or 0), 2)}
            for l in tb.lines if (l.current_debit or l.current_credit)]


def consolidated_for(engagement):
    return consolidate(load_uploads(engagement))


# --------------------------------------------------------------------------- Excel

def build_workbook(engagement, cons, rec=None, rec_period=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="1F3A5F")
    white = Font(bold=True, color="FFFFFF")
    num = '#,##0.00;[Red](#,##0.00);-'

    def sheet(title, key):
        ws = wb.create_sheet(title)
        ws.append([f"{engagement.client.name} - {title}"])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append(["Debit positive, credit negative"])
        ws.append([])
        ws.append(["Code", "Account"] + [m["label"] for m in cons["months"]])
        for c in ws[4]:
            c.font, c.fill = white, head
        for k in cons["order"]:
            a = cons["accounts"][k]
            ws.append([a["code"], a["name"]] + [m[key].get(k, 0.0) for m in cons["months"]])
        n = ws.max_row
        ws.append(["", "Total"] + [f"=SUM({chr(67 + i)}5:{chr(67 + i)}{n})" if i < 24 else None for i in range(len(cons["months"]))])
        for c in ws[ws.max_row]:
            c.font = bold
        for row in ws.iter_rows(min_row=5, min_col=3):
            for c in row:
                c.number_format = num
        ws.column_dimensions["A"].width = 12
        ws.column_dimensions["B"].width = 42
        for i in range(len(cons["months"])):
            ws.column_dimensions[chr(67 + i) if i < 24 else "Z"].width = 15
        ws.freeze_panes = "C5"
        return ws

    wb.remove(wb.active)
    sheet("Cumulative", "cum")
    sheet("Monthly movements", "mov")
    if rec is not None:
        ws = wb.create_sheet("Reconciliation")
        ws.append([f"{engagement.client.name} - cumulative trial balance at {month_label(rec_period)} reconciled to the year-end trial balance"])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append(["Result: " + ("RECONCILED" if rec["reconciled"] else f"NOT RECONCILED - {len(rec['off'])} account(s) differ")])
        ws.append([])
        ws.append(["Code", "Account", "Cumulative monthly TB", "Year-end TB", "Difference", "Found in"])
        for c in ws[4]:
            c.font, c.fill = white, head
        for x in rec["lines"]:
            ws.append([x["code"], x["name"], x["cum"], x["yearend"], x["diff"], x["where"]])
        n = ws.max_row
        ws.append(["", "Total", f"=SUM(C5:C{n})", f"=SUM(D5:D{n})", f"=SUM(E5:E{n})"])
        for c in ws[ws.max_row]:
            c.font = bold
        for row in ws.iter_rows(min_row=5, min_col=3, max_col=5):
            for c in row:
                c.number_format = num
        for col, w in zip("ABCDEF", (12, 42, 20, 16, 14, 16)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "C5"
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# --------------------------------------------------------------------------- routes

def _eng_or_404(engagement_id):
    eng = Engagement.query.get_or_404(engagement_id)
    if not eng.has_accounting_module:
        abort(404)
    _ensure_engagement_access(eng)
    return eng


@monthly_tb_bp.route("/<int:engagement_id>")
@login_required
def page(engagement_id):
    eng = _eng_or_404(engagement_id)
    ups = load_uploads(eng)
    cons = consolidate(ups)
    fy = period_months(eng.period_start, eng.period_end)
    ye = yearend_rows(eng)
    periods = [m["period"] for m in cons["months"]]
    sel = request.args.get("month")
    if sel not in periods:
        sel = periods[-1] if periods else None
    rec = None
    if sel and ye is not None:
        rec = reconcile(rows_for_month(cons, sel) or [], ye)
    last_fy = fy[-1] if fy else None
    return render_template("monthly_tb/page.html", engagement=eng, uploads=ups, cons=cons, fy=fy, month_label=month_label, rec=rec, sel=sel,
                           has_yearend=ye is not None, bases=MONTHLY_TB_BASES, basis_label=BASIS_LABEL, last_fy=last_fy,
                           covers_year=bool(fy) and bool(periods) and fy[-1] in periods,
                           packs=list(eng.cf_packs), default_month=(next((p for p in fy if p not in periods), fy[-1] if fy else "")),
                           ye_total=sum(r["net_dr"] for r in ye) if ye else None)


@monthly_tb_bp.route("/<int:engagement_id>/upload", methods=["POST"])
@login_required
def upload(engagement_id):
    import cf_ma as M
    eng = _eng_or_404(engagement_id)
    back = redirect(url_for("monthly_tb.page", engagement_id=eng.id))
    basis = request.form.get("basis")
    if basis not in dict(MONTHLY_TB_BASES):
        flash("Say whether the file holds the month's movements, closing balances or opening balances.", "danger")
        return back
    period = request.form.get("month") or ""
    try:
        parse_month(period)
    except ValueError:
        flash("Choose the month this trial balance is for.", "danger")
        return back
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a trial balance file (.xlsx or .csv).", "danger")
        return back
    try:
        rows = M.read_tb_file(f.stream, f.filename)
    except ValueError as e:
        flash(str(e), "danger")
        return back
    except Exception:
        flash("That file could not be read. Use an .xlsx or .csv trial balance with an account name column and debit/credit (or balance) columns.", "danger")
        return back
    if len(rows) > 5000:
        flash("That trial balance has more than 5,000 accounts.", "danger")
        return back
    replaced = MonthlyTB.query.filter_by(engagement_id=eng.id, period=period, basis=basis).all()
    for old in replaced:
        db.session.delete(old)
    t = MonthlyTB(engagement_id=eng.id, period=period, basis=basis, original_filename=f.filename[:255], note=(request.form.get("note") or "").strip()[:300],
                  created_by_id=current_user.id)
    for r in rows:
        t.lines.append(MonthlyTBLine(account_code=(r["code"] or "")[:50], account_name=r["name"][:200], net_dr=float(r["net_dr"])))
    db.session.add(t)
    db.session.commit()
    total = round(sum(r["net_dr"] for r in rows), 2)
    flash(f"{month_label(period)} {BASIS_LABEL[basis].lower()} loaded: {len(rows)} accounts" + (" (it replaced an earlier upload for the same month and basis)" if replaced else "") + ".", "success")
    if abs(total) > TOLERANCE:
        flash(f"The file does not balance: debits less credits = {total:,.2f}. Check it is the whole trial balance.", "warning")
    return back


@monthly_tb_bp.route("/<int:engagement_id>/delete/<int:tb_id>", methods=["POST"])
@login_required
def delete(engagement_id, tb_id):
    eng = _eng_or_404(engagement_id)
    t = MonthlyTB.query.get_or_404(tb_id)
    if t.engagement_id != eng.id:
        abort(404)
    db.session.delete(t)
    db.session.commit()
    flash("Upload removed. The cumulative trial balance has been recalculated without it.", "success")
    return redirect(url_for("monthly_tb.page", engagement_id=eng.id))


@monthly_tb_bp.route("/<int:engagement_id>/download")
@login_required
def download(engagement_id):
    eng = _eng_or_404(engagement_id)
    cons = consolidate(load_uploads(eng))
    if not cons["months"]:
        flash("Nothing to download yet - upload a month first.", "warning")
        return redirect(url_for("monthly_tb.page", engagement_id=eng.id))
    ye = yearend_rows(eng)
    sel = request.args.get("month")
    periods = [m["period"] for m in cons["months"]]
    if sel not in periods:
        sel = periods[-1]
    rec = reconcile(rows_for_month(cons, sel) or [], ye) if ye is not None else None
    bio = build_workbook(eng, cons, rec, sel)
    name = re.sub(r"[^A-Za-z0-9]+", "_", eng.client.name).strip("_") or "client"
    return send_file(bio, as_attachment=True, download_name=f"{name}_Monthly_TB_Consolidation.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
