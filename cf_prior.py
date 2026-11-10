"""Comparative (prior-year) Statement of Cash Flows, captured by hand.

The trial balance holds the current and the prior year only, so last year's cash flow (which needs the year before) cannot be worked out:
it is typed in under the Statement of Cash Flows on the Finalisation tab, line by line, under the same headings as this year's. The
statement then shows two columns, and the Word pack too. Subtotals, the net movement and the closing cash are computed, and the closing
cash is checked against the cash in the trial balance."""
import re

from flask import Blueprint, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Engagement, CashFlowPriorLine
import financials as fin

cfprior_bp = Blueprint("cfprior", __name__, url_prefix="/engagements")

SECTIONS = [("operating", "Operating activities"), ("investing", "Investing activities"), ("financing", "Financing activities")]
OPEN_LABEL = "Cash at beginning of prior year"


def _key(label):
    return re.sub(r"\s+", " ", (label or "").strip().casefold())


def parse_amount(text):
    """'1,234.50', '(1,234)', '-1234', '' -> float (brackets are negative)."""
    s = str(text or "").strip().replace(",", "").replace(" ", "")
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = s.strip("()-")
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def standard_labels(method="indirect"):
    """The line items of this year's cash flow statement (section -> labels), taken from an empty statement of the same method."""
    t = fin.compute_totals([])
    for v in t.values():                       # non-nil, so no heading is suppressed as "nothing to show"
        v["current"], v["prior"] = 1.0, 0.0
    pl = fin.build_income_statement(t)
    cf = fin.build_cash_flow(t, pl, method)
    out = {}
    for sec, key in (("operating", "operating_rows"), ("investing", "investing_rows"), ("financing", "financing_rows")):
        out[sec] = [r["label"] for r in cf[key] if not r.get("bold") and not r.get("memo")]
    return out


def attach_prior_cash_flow(statements, engagement):
    """Adds the captured comparatives to statements["cf"]: row["prior"] on every row, prior-only rows for lines typed under a heading this
    year does not have, cf["prior"] = the prior year's totals. Leaves cf["prior"] = None when nothing has been captured. Idempotent."""
    cf = (statements or {}).get("cf")
    if not cf or "prior" in cf:
        return statements
    lines = (CashFlowPriorLine.query.filter_by(engagement_id=engagement.id).order_by(CashFlowPriorLine.sort_order, CashFlowPriorLine.id).all())
    if not lines:
        cf["prior"] = None
        return statements
    cash_open = sum(l.amount or 0.0 for l in lines if l.section == "cash_open")
    totals = {}
    for sec, key in (("operating", "operating_rows"), ("investing", "investing_rows"), ("financing", "financing_rows")):
        rows = cf[key]
        pool = {}
        for l in lines:
            if l.section == sec:
                pool.setdefault(_key(l.label), []).append(l)
        used = set()
        for r in rows:
            hit = pool.get(_key(r["label"]))
            if hit and not r.get("bold") and not r.get("memo"):
                r["prior"] = sum(x.amount or 0.0 for x in hit)
                used.add(_key(r["label"]))
        for k, hit in pool.items():
            if k not in used:
                rows.append({"label": hit[0].label, "current": 0.0, "prior": sum(x.amount or 0.0 for x in hit), "note": None, "prior_only": True})
        running = 0.0
        for r in rows:
            if r.get("memo"):
                r["prior"] = None
            elif r.get("bold"):
                r["prior"] = running
            else:
                r["prior"] = r.get("prior") or 0.0
                running += r["prior"]
        totals[sec] = running
    net = totals["operating"] + totals["investing"] + totals["financing"]
    close = cash_open + net
    cf["prior"] = {"net_operating": totals["operating"], "net_investing": totals["investing"], "net_financing": totals["financing"],
                   "net_movement": net, "cash_open": cash_open, "cash_close_computed": close,
                   "cash_close_actual": cf["cash_open"], "variance": close - cf["cash_open"]}
    return statements


def _engagement(engagement_id):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    return engagement


def _back(engagement_id):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation") + "#cf-prior")


def lines_for(engagement_id):
    return CashFlowPriorLine.query.filter_by(engagement_id=engagement_id).order_by(CashFlowPriorLine.sort_order, CashFlowPriorLine.id).all()


@cfprior_bp.route("/<int:engagement_id>/cash-flow-prior/seed", methods=["POST"])
@login_required
def seed(engagement_id):
    engagement = _engagement(engagement_id)
    have = {(l.section, _key(l.label)) for l in lines_for(engagement_id)}
    n = CashFlowPriorLine.query.filter_by(engagement_id=engagement_id).count()
    added = 0
    if ("cash_open", _key(OPEN_LABEL)) not in have:
        db.session.add(CashFlowPriorLine(engagement_id=engagement_id, section="cash_open", label=OPEN_LABEL, amount=0.0, sort_order=n)); n += 1; added += 1
    for sec, labels in standard_labels(engagement.cash_flow_method or "indirect").items():
        for label in labels:
            if (sec, _key(label)) not in have:
                db.session.add(CashFlowPriorLine(engagement_id=engagement_id, section=sec, label=label, amount=0.0, sort_order=n)); n += 1; added += 1
    db.session.commit()
    flash(f"{added} line(s) added under this year's headings. Type last year's amounts and press Save.", "success")
    return _back(engagement_id)


@cfprior_bp.route("/<int:engagement_id>/cash-flow-prior/save", methods=["POST"])
@login_required
def save(engagement_id):
    _engagement(engagement_id)
    f = request.form
    sections, labels, amounts = f.getlist("section"), f.getlist("label"), f.getlist("amount")
    removed = set(f.getlist("remove"))
    ids = f.getlist("line_key")
    CashFlowPriorLine.query.filter_by(engagement_id=engagement_id).delete()
    kept = 0
    valid = {s for s, _ in SECTIONS} | {"cash_open"}
    for i, (sec, label, amt) in enumerate(zip(sections, labels, amounts)):
        key = ids[i] if i < len(ids) else ""
        if key in removed and key:
            continue
        label = (label or "").strip()
        if not label or sec not in valid:
            continue
        db.session.add(CashFlowPriorLine(engagement_id=engagement_id, section=sec, label=label[:200], amount=parse_amount(amt), sort_order=kept))
        kept += 1
    db.session.commit()
    flash(f"Prior-year cash flow saved ({kept} line(s)).", "success")
    return _back(engagement_id)


# ------------------------------------------------------------------ import from Excel / CSV / pasted text

_SUBTOTAL = re.compile(r"^\s*(net\s+(cash|increase|decrease)|cash\s+generated|total\b|cash\s+(and\s+cash\s+equivalents\s+)?at\s+(the\s+)?end|"
                       r"(cash|cash\s+and\s+cash\s+equivalents)\s+(balance\s+)?(at\s+)?(the\s+)?(close|end)|closing\s+(cash|balance)|operating\s+cash\s+flow\s+before|"
                       r"cash\s+flows?\s+from|\(?increase\)?\s*/?\s*\(?decrease\)?\s+in\s+cash\s*$)", re.I)
_OPENING = re.compile(r"(cash|equivalents|balance).*(beginning|opening|start|b/?f|brought)|(beginning|opening).*(cash|balance)", re.I)
_HEADING = re.compile(r"\b(operating|investing|financing)\b", re.I)


def _is_amount(v):
    from tb_year_import import to_num
    return to_num(v)[0] is not None


def _guess_section(label):
    """For a line met before any section heading: investing / financing by its wording, else operating."""
    if re.search(r"purchase|acquisition|disposal|proceeds from (the )?sale|property|plant|equipment|vehicle|intangible|investment", label, re.I):
        return "investing"
    if re.search(r"borrowing|loan|dividend|share (capital|issue)|shares issued|repayment|lease liabilit|debenture", label, re.I):
        return "financing"
    return "operating"


def parse_rows(rows, prior_year=None):
    """Lines found in a sheet of rows: (lines, info). A line is (section, label, amount). The label is the text column; the amount
    column is the one headed with the prior year when there is one, else the last column of figures (not a "Note" column). Section headings
    ("Operating activities" ...) switch the section; subtotals and closing cash are left out (they are worked out); the opening cash line goes
    to cash_open. A line before any heading is placed under this year's heading of the same name, or operating."""
    from tb_year_import import to_num
    rows = [list(r) for r in rows if any(c is not None and str(c).strip() for c in r)]
    width = max((len(r) for r in rows), default=0)
    rows = [r + [None] * (width - len(r)) for r in rows]
    header_idx, amt_col, used_header = None, None, None
    def yearish(c):
        return c is not None and re.search(r"\b(19|20)\d\d\b", str(c)) is not None and len(str(c)) <= 40
    for i, r in enumerate(rows[:15]):
        years = {j: str(c) for j, c in enumerate(r) if yearish(c)}
        stray = [c for c in r if c is not None and not yearish(c) and _is_amount(c) and re.fullmatch(r"[\d.,()\s-]+", str(c).strip() or "x")]
        if years and not stray:
            header_idx = i
            if prior_year:
                for j, txt in years.items():
                    if str(prior_year) in txt:
                        amt_col, used_header = j, txt.strip()
            break
    body = rows[(header_idx + 1) if header_idx is not None else 0:]
    notes_cols = set()
    if header_idx is not None:
        notes_cols = {j for j, c in enumerate(rows[header_idx]) if re.search(r"\bnotes?\b", str(c or ""), re.I)}
    numeric = [sum(1 for r in body if r[j] is not None and _is_amount(r[j]) and not isinstance(r[j], bool)) for j in range(width)]
    text = [sum(1 for r in body if isinstance(r[j], str) and r[j].strip() and not _is_amount(r[j])) for j in range(width)]
    label_col = max(range(width), key=lambda j: text[j]) if width else 0
    if amt_col is None:
        cands = [j for j in range(width) if j != label_col and j not in notes_cols and numeric[j] >= max(1, len(body) // 5)]
        amt_col = cands[-1] if cands else None
        if amt_col is not None and header_idx is not None:
            used_header = str(rows[header_idx][amt_col] or "").strip() or None
    lines, skipped, section = [], [], None
    std = {}
    for sec, labels in standard_labels("indirect").items():
        for l in labels:
            std[_key(l)] = sec
    if amt_col is None:
        return [], {"column": None, "skipped": [], "reason": "No column of figures was found."}
    for r in body:
        label = str(r[label_col] or "").strip()
        if not label:
            continue
        amount = to_num(r[amt_col])[0]
        if amount is None:
            m = _HEADING.search(label)
            if m and len(label) < 60:
                section = m.group(1).lower()
            continue
        if _OPENING.search(label):
            lines.append(("cash_open", OPEN_LABEL, amount))
            continue
        if _SUBTOTAL.match(label):
            skipped.append(label)
            continue
        sec = section or std.get(_key(label)) or _guess_section(label)
        lines.append((sec, label, amount))
    return lines, {"column": used_header or (f"column {amt_col + 1}"), "skipped": skipped, "reason": None}


def rows_from_text(text):
    """Pasted text (copied from Excel: tab separated; or ; or two-plus spaces) as rows."""
    out = []
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        parts = re.split(r"\t|;|\s{2,}", ln.strip())
        out.append([p.strip() for p in parts])
    return out


@cfprior_bp.route("/<int:engagement_id>/cash-flow-prior/import", methods=["POST"])
@login_required
def import_lines(engagement_id):
    engagement = _engagement(engagement_id)
    from tb_year_import import load_sheets, choose_sheet
    year = engagement.period_end.year - 1 if engagement.period_end else None
    rows = None
    f = request.files.get("file")
    try:
        if f and f.filename:
            sheets = load_sheets(f.read(), f.filename)
            name = choose_sheet(sheets, year) if len(sheets) > 1 else next(iter(sheets))
            rows = sheets[name]
        elif (request.form.get("pasted") or "").strip():
            rows = rows_from_text(request.form["pasted"])
    except Exception:
        flash("That file could not be read. Use an Excel (.xlsx) or CSV file, or paste the rows.", "danger")
        return _back(engagement_id)
    if not rows:
        flash("Choose a file or paste the rows first.", "info")
        return _back(engagement_id)
    lines, info = parse_rows(rows, year)
    if not lines:
        flash(info.get("reason") or "No cash flow lines were found: the sheet needs a column of line names and a column of amounts.", "danger")
        return _back(engagement_id)
    CashFlowPriorLine.query.filter_by(engagement_id=engagement_id).delete()
    for i, (sec, label, amount) in enumerate(lines):
        db.session.add(CashFlowPriorLine(engagement_id=engagement_id, section=sec, label=label[:200], amount=amount, sort_order=i))
    db.session.commit()
    msg = f"Imported {len(lines)} line(s) from the amount column \"{info['column']}\"; the earlier lines were replaced."
    if info["skipped"]:
        msg += f" Left out as subtotals (they are worked out): {', '.join(info['skipped'][:6])}{'...' if len(info['skipped']) > 6 else ''}."
    flash(msg + " Check the lines below and the tie-out to the trial balance, correct anything, and Save.", "success")
    return _back(engagement_id)
