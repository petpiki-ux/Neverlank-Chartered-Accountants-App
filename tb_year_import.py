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
"""
import csv
import io
import re
from datetime import datetime

import openpyxl
from flask import Blueprint, redirect, url_for, request, flash
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


def read_rows(file_storage, filename, year):
    """[(row cells)] of the sheet that looks most like a trial balance, plus the sheet name (None for a CSV)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("csv", "txt", "tsv"):
        text = file_storage.read().decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [list(r) for r in csv.reader(io.StringIO(text), dialect)], None
    wb = openpyxl.load_workbook(file_storage, data_only=True)
    best = (-1, None, None)
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        i, cols = _find_table(rows, year)
        if i is None:
            continue
        score = len(rows) + (1000 if year and year in norm(ws.title) else 0)
        if score > best[0]:
            best = (score, rows, ws.title)
    if best[1] is None:
        raise ValueError("Could not find a trial balance in that file. It needs a header row with an account name column and either "
                         "Debit and Credit columns or a single Balance / Closing balance column.")
    return best[1], best[2]


def parse_year_file(rows, year):
    """(accounts, problems): accounts = [{"code", "name", "debit", "credit"}] for the chosen year; a duplicate name or code is summed."""
    i, cols = _find_table(rows, year)
    if i is None:
        raise ValueError("Could not find the header row (an account name column and Debit/Credit or Balance columns).")
    out, order, problems = {}, [], []
    for n, row in enumerate(rows[i + 1:], start=i + 2):
        def cell(k):
            j = cols.get(k)
            return row[j] if j is not None and j < len(row) else None
        name = str(cell("name") or "").strip()
        if not name or _TOTAL.match(name):
            continue
        code = str(cell("code") or "").strip()
        if code.endswith(".0") and code[:-2].isdigit():
            code = code[:-2]
        net, anything = 0.0, False
        if "debit" in cols or "credit" in cols:
            d, dm = to_num(cell("debit"))
            c, cm = to_num(cell("credit"))
            for x in (d, c):
                anything = anything or x is not None
            net = (d or 0.0) - (c or 0.0)
        if "balance" in cols and not anything:
            b, mark = to_num(cell("balance"))
            if b is not None:
                anything = True
                net = -abs(b) if mark == "cr" else abs(b) if mark == "dr" else b
        if not anything:
            continue                                        # a heading or a blank line
        key = code or fin.normalize_account_name(name)
        if key in out:
            out[key]["net"] += net
        else:
            out[key] = {"code": code, "name": name, "net": net}
            order.append(key)
    accounts = []
    for k in order:
        a = out[k]
        accounts.append({"code": a["code"], "name": a["name"], "debit": a["net"] if a["net"] > 0 else 0.0, "credit": -a["net"] if a["net"] < 0 else 0.0})
    if not accounts:
        raise ValueError("No account rows with figures were found below the header row.")
    return accounts, problems


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


@tbyear_bp.route("/<int:engagement_id>/trial-balance/upload-year", methods=["POST"])
@login_required
def upload_year(engagement_id):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    back = redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    year_kind = "prior" if request.form.get("year") == "prior" else "current"
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to upload.", "danger")
        return back
    if not eng._allowed_tb_file(file.filename) and not file.filename.lower().endswith((".txt", ".tsv")):
        flash("Please upload a .xlsx or .csv file.", "danger")
        return back
    ye = engagement.period_end.year if engagement.period_end else None
    year = str(ye - 1 if (year_kind == "prior" and ye) else ye) if ye else ""
    label = f"{year} " if year else ("prior-year " if year_kind == "prior" else "current-year ")
    name = secure_filename(file.filename)
    try:
        rows, sheet = read_rows(file, name, year)
        accounts, _ = parse_year_file(rows, year)
    except ValueError as e:
        flash(str(e), "danger")
        return back
    except Exception:
        flash("Could not read that file - make sure it is an .xlsx or .csv trial balance.", "danger")
        return back
    tb = eng._get_or_create_trial_balance(engagement_id, source="upload")
    if tb.lines:
        for l in tb.lines:                                  # keep the categories chosen on this trial balance for next time
            if l.fs_category:
                eng._upsert_coa_mapping(engagement.client_id, l.account_name, l.fs_category, current_user.id, l.coa_account_number)
    std_sub = {a.account_number: a.sub_category for a in StandardChartOfAccounts.query.all()}
    summary = merge_year(tb, accounts, year_kind, engagement.client_id, eng._lookup_coa_mapping, std_sub)
    tb.source = "upload"
    tb.original_filename = name
    eng._touch_trial_balance(tb)
    db.session.commit()
    msg = (f"{label.strip().capitalize()} trial balance read from '{name}'{' (sheet ' + sheet + ')' if sheet else ''}: {len(accounts)} account(s) - "
           f"{summary['matched']} matched, {summary['added']} new")
    if summary["zeroed"]:
        msg += f", {summary['zeroed']} existing account(s) not in the file set to nil for that year"
    msg += ". The other year's figures were kept."
    flash(msg, "success")
    if not summary["balanced"]:
        flash(f"The file does not balance: debits {summary['debit']:,.2f}, credits {summary['credit']:,.2f}, difference {summary['debit'] - summary['credit']:,.2f}. "
              "Check the file for a missing account or total row.", "danger")
    unmapped = tb.unmapped_count
    if unmapped:
        flash(f"{unmapped} account(s) still need an IAS 1 category (Account Mapping below).", "info")
    return back
