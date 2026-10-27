"""Upload ONE year's trial balance on its own (the 2025 file, or the 2024 file) into the engagement's trial balance.

The standard import replaces the whole trial balance and expects both years in one file. Often the client sends the years separately: a
2025 trial balance in its own Excel file (and the 2024 one earlier). This reads such a file in whatever layout it comes in and merges it
into the existing trial balance, touching only the chosen year's columns:

* header row and columns found by their headings: account name (required), account code, and either a Debit and a Credit column or a
  single signed Balance / Closing balance / Amount column (a bracketed or negative figure, or one marked Cr, is a credit); where the file
  has columns for more than one year, the ones headed with the chosen year are used;
* sheets: the sheet that looks most like a trial balance (preferring one whose name carries the year);
* total rows and blank rows are left out; amounts such as (1,234.50), 1 234.50 and 1,234.50 Cr are understood;
* accounts are matched to the existing lines by account code, else by name. A matched line keeps its other year's figures and its IAS 1
  mapping; a new account is added (mapped from the client's saved mappings when it has been seen before); an existing account that is
  not in the file has this year's figures set to nil (and the line is dropped if that leaves nothing in either year).
The upload says how many accounts matched, were added and were zeroed, and whether the debits equal the credits.

NOTHING is applied straight from the file: a PREVIEW page shows every row that was read, with the sheet, header row and columns it chose
(all changeable), the running total of debits and credits of the ticked rows and the difference. Rows that look like subtotals, totals,
or a separate block further down the sheet are listed but unticked, with the reason; when the ticked rows still do not balance, the
page points at the row(s) whose figure equals the difference. Only the ticked rows are imported.
"""
import csv
import io
import os
import re
import uuid
from datetime import datetime

import openpyxl
from flask import Blueprint, redirect, url_for, request, flash, render_template, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

import financials as fin
from extensions import db
from models import Engagement, TrialBalance, TrialBalanceLine, StandardChartOfAccounts

tbyear_bp = Blueprint("tbyear", __name__, url_prefix="/engagements")

_TOTAL = re.compile(r"^\s*(grand\s+)?(total|totals|sub\s*-?total)\b", re.I)


def norm(v):
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


NAME_H = ("accountname", "name", "account", "accountdescription", "description", "accounttitle", "glname", "ledgername", "ledger", "particulars", "accountdetails")
CODE_H = ("accountcode", "code", "glcode", "accountno", "accountnumber", "accno", "acno", "ac", "accountid", "glaccount", "number")
BAL_H = ("closingbalance", "balance", "netbalance", "amount", "closing", "net", "ytdbalance", "endingbalance", "trialbalance", "tb", "closingbal")


def to_num(v):
    """float for a figure as typed in a trial balance: 1,234.50 / (1,234.50) / -1234 / 1 234.50 / 1,234.50 Cr; None when it is nothing.
    The second value is 'cr' or 'dr' when the figure is marked so, else None. Brackets and a minus are returned as a negative number."""
    if v is None:
        return None, None
    if isinstance(v, (int, float)):
        return float(v), None
    s = str(v).strip()
    if s in ("", "-", "–", "—"):
        return None, None
    mark = None
    m = re.search(r"\b(cr|dr)\.?\s*$", s, re.I)
    if m:
        mark, s = m.group(1).lower(), s[:m.start()].strip()
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^0-9.]", "", s.replace(",", ""))
    if s in ("", "."):
        return None, None
    try:
        x = float(s)
    except ValueError:
        return None, None
    return (-x if neg else x), mark


def _classify(header, year):
    """What a heading is: ('name'|'code'|'debit'|'credit'|'balance', weight) or None. `year` (a string) is preferred when it is in the heading."""
    h = norm(header)
    if not h:
        return None
    kind = None
    if h in NAME_H:
        kind = "name"
    elif h in CODE_H:
        kind = "code"
    elif re.search(r"(debit|^dr|dr$|debits)", h) and "credit" not in h:
        kind = "debit"
    elif re.search(r"(credit|^cr|cr$|credits)", h) and "debit" not in h:
        kind = "credit"
    elif any(h == b or h.endswith(b) for b in BAL_H):
        kind = "balance"
    if kind is None:
        return None
    return kind, (2 if year and year in h else 0)


def _find_table(rows, year):
    """(header row index, {kind: column index}) of the best header row among the first 40 rows, or (None, None)."""
    best = (0, None, None)
    for i, row in enumerate(rows[:40]):
        cols, score = {}, 0
        for j, c in enumerate(row):
            cl = _classify(c, year)
            if not cl:
                continue
            kind, w = cl
            if kind not in cols or w > cols[kind][1]:
                cols[kind] = (j, w)
        if "name" not in cols:
            continue
        has_amount = ("debit" in cols and "credit" in cols) or "balance" in cols
        if not has_amount:
            continue
        score = 10 + len(cols) + sum(w for _, w in cols.values())
        if score > best[0]:
            best = (score, i, {k: v[0] for k, v in cols.items()})
    return best[1], best[2]


MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 6000
_TOTALISH = re.compile(r"^\s*(grand\s+|sub\s*-?\s*)?totals?\b|^\s*(net|gross)\s+(profit|loss)\b|^\s*(difference|check|variance|out of balance)\b|^\s*balance\s*(check|c/?d|b/?d)?\s*$", re.I)


def load_sheets(data, filename):
    """{sheet name: rows} for an uploaded workbook or CSV (a CSV is one unnamed sheet)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("csv", "txt", "tsv"):
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return {"": [list(r) for r in csv.reader(io.StringIO(text), dialect)][:MAX_ROWS]}
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    out = {}
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(list(r))
            if len(rows) >= MAX_ROWS:
                break
        out[ws.title] = rows
    return out


def choose_sheet(sheets, year):
    """The sheet that looks most like the trial balance: one with a recognisable table, preferring a name that says trial balance / TB /
    the year; the number of rows only breaks a tie (a long ledger sheet is not the trial balance because it is long)."""
    best, best_score = None, -1
    for name, rows in sheets.items():
        i, cols = _find_table(rows, year)
        if i is None:
            continue
        n = norm(name)
        score = 1 + min(len(rows), 500) / 1000.0
        if year and year in n:
            score += 50
        if "trial" in n or n in ("tb", "tbal") or n.startswith("tb"):
            score += 30
        if any(w in n for w in ("ledger", "gl", "journal", "detail", "transactions", "notes", "workings")):
            score -= 20
        if score > best_score:
            best, best_score = name, score
    if best is None:
        raise ValueError("Could not find a trial balance in that file. It needs a header row with an account name column and either "
                         "Debit and Credit columns or a single Balance / Closing balance column.")
    return best


def read_rows(file_storage, filename, year):
    """Compatibility wrapper: (rows, sheet name) of the sheet that looks most like a trial balance."""
    sheets = load_sheets(file_storage.read(), filename)
    name = choose_sheet(sheets, year)
    return sheets[name], (name or None)


def _row_blank(row):
    return all(c is None or str(c).strip() == "" for c in row)


def read_table(rows, year, mapping=None, header_idx=None):
    """Read the trial balance table. `mapping` ({kind: column index}) and `header_idx` override what is detected.

    Returns {"header_idx", "header", "mapping", "items"}: items are every row with figures, in file order, each
    {"idx", "row_no", "code", "name", "debit", "credit", "net", "flag"}; `flag` is why the row is probably not an account (a total, a
    subtotal, or a block below the trial balance) and is "" for a normal row."""
    auto_i, auto_cols = _find_table(rows, year)
    if header_idx is None:
        header_idx = auto_i
    if header_idx is None:
        raise ValueError("Could not find the header row (an account name column and Debit/Credit or Balance columns).")
    cols = dict(mapping) if mapping else dict(auto_cols or {})
    if "name" not in cols:
        raise ValueError("Choose which column holds the account name.")
    if not (("debit" in cols and "credit" in cols) or "balance" in cols or "debit" in cols or "credit" in cols):
        raise ValueError("Choose the Debit and Credit columns, or a single Balance column.")
    header = rows[header_idx] if header_idx < len(rows) else []
    items, blanks = [], 0
    for idx in range(header_idx + 1, len(rows)):
        row = rows[idx]
        if _row_blank(row):
            blanks += 1
            continue
        gap = blanks >= 2 and bool(items)
        blanks = 0

        def cell(k):
            j = cols.get(k)
            return row[j] if j is not None and j < len(row) else None
        name = str(cell("name") or "").strip()
        if not name:
            continue
        code = str(cell("code") or "").strip()
        if code.endswith(".0") and code[:-2].isdigit():
            code = code[:-2]
        net, anything = 0.0, False
        if "debit" in cols or "credit" in cols:
            d, _m = to_num(cell("debit"))
            c, _m2 = to_num(cell("credit"))
            anything = d is not None or c is not None
            net = (d or 0.0) - (c or 0.0)
        if "balance" in cols and not anything:
            b, mark = to_num(cell("balance"))
            if b is not None:
                anything = True
                net = -abs(b) if mark == "cr" else abs(b) if mark == "dr" else b
        if not anything:
            continue                                         # a heading, or a line with no figure
        flag = ""
        if _TOTALISH.match(name):
            flag = "looks like a total row"
        elif gap:
            flag = "below a gap - probably a separate block, not part of the trial balance"
        items.append({"idx": idx, "row_no": idx + 1, "code": code, "name": name, "debit": net if net > 0 else 0.0,
                      "credit": -net if net < 0 else 0.0, "net": net, "flag": flag})
    _flag_subtotals(items)
    return {"header_idx": header_idx, "header": header, "mapping": cols, "items": items}


def _flag_subtotals(items):
    """Flag a row whose figure equals the sum of the unflagged rows directly above it (a subtotal), and a row that carries the total of
    all the debits AND all the credits above it (a totals line without the word 'total')."""
    run = []                                                 # unflagged rows so far, in order
    sd = sc = 0.0
    for it in items:
        if not it["flag"] and abs(it["net"]) >= 0.005:
            s = 0.0
            for k in range(1, min(len(run), 200) + 1):
                if (run[-k]["net"] > 0) != (it["net"] > 0):
                    break                                    # a subtotal adds up rows on its own side only
                s += run[-k]["net"]
                if k >= 2 and abs(s - it["net"]) < 0.01:
                    if it["code"] and all(r["code"] for r in run[-k:]):
                        break                                # an account with a code, among coded accounts: a coincidence, not a subtotal
                    it["flag"] = f"looks like a subtotal of the {k} rows above it"
                    break
            if not it["flag"] and it["debit"] and it["credit"] and abs(it["debit"] - sd) < 0.01 and abs(it["credit"] - sc) < 0.01:
                it["flag"] = "looks like the totals line (equals all the debits and credits above)"
        if not it["flag"]:
            run.append(it)
            sd += it["debit"]
            sc += it["credit"]


def aggregate(items):
    """The accounts for the rows given: a repeated code or name is summed. [{"code", "name", "debit", "credit"}]."""
    out, order = {}, []
    for it in items:
        key = it["code"] or fin.normalize_account_name(it["name"])
        if key in out:
            out[key]["net"] += it["net"]
        else:
            out[key] = {"code": it["code"], "name": it["name"], "net": it["net"]}
            order.append(key)
    return [{"code": out[k]["code"], "name": out[k]["name"], "debit": out[k]["net"] if out[k]["net"] > 0 else 0.0,
             "credit": -out[k]["net"] if out[k]["net"] < 0 else 0.0} for k in order]


def parse_year_file(rows, year):
    """(accounts, problems) for the rows that are not flagged as totals/subtotals - what the preview ticks by default."""
    t = read_table(rows, year)
    accounts = aggregate([it for it in t["items"] if not it["flag"]])
    if not accounts:
        raise ValueError("No account rows with figures were found below the header row.")
    return accounts, []


def diagnose(items):
    """Hints when the given (ticked) rows do not balance: rows whose figure equals the difference, pairs that add up to it, a figure
    in the wrong column (twice the difference). Returns a list of sentences."""
    td, tc = sum(i["debit"] for i in items), sum(i["credit"] for i in items)
    diff = round(td - tc, 2)
    if abs(diff) < 0.01:
        return []
    hints = []
    cand = [i for i in items if abs(i["net"]) >= 0.005][:600]
    for it in cand:
        if abs(it["net"] - diff) < 0.01:
            hints.append(f"Row {it['row_no']} '{it['name']}' ({abs(it['net']):,.2f} {'Dr' if it['net'] > 0 else 'Cr'}) equals the difference exactly: if it is a subtotal or a duplicate, untick it.")
        elif abs(it["net"] * 2 - diff) < 0.01:
            hints.append(f"Row {it['row_no']} '{it['name']}' ({abs(it['net']):,.2f} {'Dr' if it['net'] > 0 else 'Cr'}) is half the difference: it may sit in the wrong column (Debit instead of Credit, or the reverse).")
        if len(hints) >= 4:
            return hints
    if not hints and len(cand) <= 400:
        seen = {}
        for a in cand:
            need = round(diff - a["net"], 2)
            b = seen.get(need)
            if b is not None:
                hints.append(f"Rows {b['row_no']} '{b['name']}' and {a['row_no']} '{a['name']}' together equal the difference.")
                if len(hints) >= 3:
                    break
            seen.setdefault(round(a["net"], 2), a)
    if not hints:
        hints.append("No single row or pair of rows explains the difference. Check that the Debit and Credit columns are the right ones (a file with opening, movement and closing columns has several), "
                     "and that every account of the trial balance is in the file.")
    return hints


def merge_year(tb, accounts, year_kind, client_id, lookup_mapping, std_sub):
    """Merge `accounts` into tb's lines for year_kind ('current' or 'prior'). Returns a summary dict."""
    dcol, ccol = f"{year_kind}_debit", f"{year_kind}_credit"
    by_code, by_name = {}, {}
    for l in tb.lines:
        if (l.account_code or "").strip():
            by_code.setdefault(l.account_code.strip(), l)
        by_name.setdefault(fin.normalize_account_name(l.account_name), l)
    touched, matched, added = set(), 0, 0
    for a in accounts:
        line = by_code.get(a["code"]) if a["code"] else None
        if line is None:
            line = by_name.get(fin.normalize_account_name(a["name"]))
        if line is not None and line.id in touched:
            line = None                                     # two accounts matching one line: the second is added separately
        if line is None:
            m = lookup_mapping(client_id, a["name"])
            line = TrialBalanceLine(trial_balance_id=tb.id, account_code=a["code"], account_name=a["name"],
                                    fs_category=m.fs_category if m else None, coa_account_number=m.coa_account_number if m else None,
                                    sub_category=std_sub.get(m.coa_account_number) if m and m.coa_account_number else None,
                                    current_debit=0.0, current_credit=0.0, prior_debit=0.0, prior_credit=0.0)
            db.session.add(line)
            db.session.flush()
            added += 1
        else:
            matched += 1
            if a["code"] and not (line.account_code or "").strip():
                line.account_code = a["code"]
        setattr(line, dcol, a["debit"])
        setattr(line, ccol, a["credit"])
        touched.add(line.id)
    zeroed = removed = 0
    for l in list(tb.lines):
        if l.id in touched:
            continue
        if (getattr(l, dcol) or 0) or (getattr(l, ccol) or 0):
            zeroed += 1
        setattr(l, dcol, 0.0)
        setattr(l, ccol, 0.0)
        other = ("prior" if year_kind == "current" else "current")
        if not (getattr(l, f"{other}_debit") or 0) and not (getattr(l, f"{other}_credit") or 0):
            db.session.delete(l)
            removed += 1
    td, tc = sum(a["debit"] for a in accounts), sum(a["credit"] for a in accounts)
    return {"matched": matched, "added": added, "zeroed": zeroed, "removed": removed, "debit": td, "credit": tc, "balanced": abs(td - tc) < 0.01}


# ---------------------------------------------------------------------------------------------------- preview and import

def _temp_path(engagement_id, token, ext):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("bad token")
    return os.path.join(current_app.config["UPLOAD_FOLDER"], f"tbyear_{engagement_id}_{token}{ext}")


def _year_info(engagement, year_kind):
    ye = engagement.period_end.year if engagement.period_end else None
    year = str(ye - 1 if (year_kind == "prior" and ye) else ye) if ye else ""
    return year, (year or ("prior year" if year_kind == "prior" else "current year"))


def _mapping_from_form(form):
    m = {}
    for k in ("name", "code", "debit", "credit", "balance"):
        v = (form.get(f"col_{k}") or "").strip()
        if v.isdigit():
            m[k] = int(v)
    return m


def _tab_redirect(engagement_id):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


def _render(engagement, token, ext, filename, year_kind, sheet, form=None):
    import engagements as eng
    path = _temp_path(engagement.id, token, ext)
    year, label = _year_info(engagement, year_kind)
    with open(path, "rb") as fh:
        sheets = load_sheets(fh.read(), filename + ext)
    if sheet not in sheets:
        sheet = choose_sheet(sheets, year)
    rows = sheets[sheet]
    mapping = _mapping_from_form(form) if form and form.get("remap") else None
    header_idx = None
    if form and (form.get("header_row_no") or "").strip().isdigit():
        header_idx = max(0, int(form["header_row_no"]) - 1)
    err = None
    try:
        t = read_table(rows, year, mapping=mapping or None, header_idx=header_idx)
    except ValueError as e:
        err = str(e)
        t = {"header_idx": header_idx or 0, "header": rows[header_idx or 0] if rows else [], "mapping": mapping or {}, "items": []}
    items = t["items"]
    ticked = [i for i in items if not i["flag"]]
    hints = diagnose(ticked)
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    return render_template(
        "engagements/tb_year_preview.html", engagement=engagement, token=token, ext=ext, filename=filename, year_kind=year_kind, year_label=label,
        sheet=sheet, sheet_names=list(sheets.keys()), table=t, items=items, hints=hints, err=err, has_tb=bool(tb and tb.lines),
        total_debit=sum(i["debit"] for i in ticked), total_credit=sum(i["credit"] for i in ticked),
        fields=[("name", "Account name"), ("code", "Account code"), ("debit", "Debit"), ("credit", "Credit"), ("balance", "Single balance column")])


@tbyear_bp.route("/<int:engagement_id>/trial-balance/upload-year", methods=["POST"])
@login_required
def upload_year(engagement_id):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    year_kind = "prior" if request.form.get("year") == "prior" else "current"
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to upload.", "danger")
        return _tab_redirect(engagement_id)
    if not eng._allowed_tb_file(file.filename) and not file.filename.lower().endswith((".txt", ".tsv")):
        flash("Please upload a .xlsx or .csv file.", "danger")
        return _tab_redirect(engagement_id)
    data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        flash("That file is too large (8 MB limit).", "danger")
        return _tab_redirect(engagement_id)
    year, _ = _year_info(engagement, year_kind)
    try:
        sheets = load_sheets(data, file.filename)
        choose_sheet(sheets, year)
    except ValueError as e:
        flash(str(e), "danger")
        return _tab_redirect(engagement_id)
    except Exception:
        flash("Could not read that file - make sure it is an .xlsx or .csv trial balance.", "danger")
        return _tab_redirect(engagement_id)
    ext = os.path.splitext(file.filename)[1].lower()
    token = uuid.uuid4().hex
    with open(_temp_path(engagement.id, token, ext), "wb") as out:
        out.write(data)
    return _render(engagement, token, ext, secure_filename(os.path.splitext(file.filename)[0]) or "trial_balance", year_kind, choose_sheet(sheets, year))


@tbyear_bp.route("/<int:engagement_id>/trial-balance/upload-year/<token>/preview", methods=["POST"])
@login_required
def preview_year(engagement_id, token):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    ext, filename = request.form.get("ext", ""), request.form.get("filename", "")
    try:
        path = _temp_path(engagement.id, token, ext)
    except ValueError:
        return _tab_redirect(engagement_id)
    if not os.path.exists(path):
        flash("That upload has expired - please choose the file again.", "danger")
        return _tab_redirect(engagement_id)
    return _render(engagement, token, ext, filename, "prior" if request.form.get("year") == "prior" else "current", request.form.get("sheet", ""), request.form)


@tbyear_bp.route("/<int:engagement_id>/trial-balance/upload-year/<token>/confirm", methods=["POST"])
@login_required
def confirm_year(engagement_id, token):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    ext, filename = request.form.get("ext", ""), request.form.get("filename", "")
    year_kind = "prior" if request.form.get("year") == "prior" else "current"
    try:
        path = _temp_path(engagement.id, token, ext)
    except ValueError:
        return _tab_redirect(engagement_id)
    if not os.path.exists(path):
        flash("That upload has expired - please choose the file again.", "danger")
        return _tab_redirect(engagement_id)
    year, label = _year_info(engagement, year_kind)
    with open(path, "rb") as fh:
        sheets = load_sheets(fh.read(), filename + ext)
    sheet = request.form.get("sheet", "")
    rows = sheets[sheet] if sheet in sheets else sheets[choose_sheet(sheets, year)]
    mapping = _mapping_from_form(request.form)
    hdr = request.form.get("header_idx", "")
    try:
        t = read_table(rows, year, mapping=mapping or None, header_idx=int(hdr) if hdr.isdigit() else None)
    except ValueError as e:
        flash(str(e), "danger")
        return _tab_redirect(engagement_id)
    chosen = {int(x) for x in request.form.getlist("inc") if x.isdigit()}
    picked = [it for it in t["items"] if it["idx"] in chosen]
    if not picked:
        flash("Nothing was imported - no rows were ticked.", "danger")
        return _tab_redirect(engagement_id)
    accounts = aggregate(picked)
    tb = eng._get_or_create_trial_balance(engagement_id, source="upload")
    for l in tb.lines:                                      # keep the categories chosen on this trial balance for next time
        if l.fs_category:
            eng._upsert_coa_mapping(engagement.client_id, l.account_name, l.fs_category, current_user.id, l.coa_account_number)
    std_sub = {a.account_number: a.sub_category for a in StandardChartOfAccounts.query.all()}
    summary = merge_year(tb, accounts, year_kind, engagement.client_id, eng._lookup_coa_mapping, std_sub)
    tb.source = "upload"
    tb.original_filename = secure_filename(filename + ext)
    eng._touch_trial_balance(tb)
    db.session.commit()
    try:
        os.remove(path)
    except OSError:
        pass
    msg = (f"{label.capitalize()} trial balance read from '{filename}{ext}': {len(accounts)} account(s) - {summary['matched']} matched, {summary['added']} new")
    if summary["zeroed"]:
        msg += f", {summary['zeroed']} existing account(s) not in the file set to nil for that year"
    flash(msg + ". The other year's figures were kept.", "success")
    if not summary["balanced"]:
        flash(f"The imported rows do not balance: debits {summary['debit']:,.2f}, credits {summary['credit']:,.2f}, difference {summary['debit'] - summary['credit']:,.2f}.", "danger")
    unmapped = tb.unmapped_count
    if unmapped:
        flash(f"{unmapped} account(s) still need an IAS 1 category (Account Mapping below).", "info")
    return _tab_redirect(engagement_id)
