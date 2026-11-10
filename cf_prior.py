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



_STOP = {"the", "of", "in", "and", "from", "to", "for", "on", "a", "an", "net", "year", "during", "other", "trade", "movement", "movements",
         "change", "changes", "increase", "decrease", "loss", "profit_", "total", "by", "per", "as",
         "proceeds", "received", "raised", "obtained", "repayment", "repayments", "repaid"}
_SYN = {"taxation": "tax", "debtors": "receivables", "debtor": "receivables", "creditors": "payables", "creditor": "payables",
        "stock": "inventories", "stocks": "inventories", "inventory": "inventories", "ppe": "property plant equipment",
        "interest": "finance cost", "depreciation": "depreciation", "amortisation": "depreciation", "amortization": "depreciation",
        "issued": "issue", "borrowings": "borrowing", "loans": "borrowing", "loan": "borrowing", "dividends": "dividend", "shares": "share", "costs": "cost",
        "prepayments": "current asset", "prepayment": "current asset", "prepaid": "current asset", "deposits": "current asset", "deposit": "current asset",
        "charges": "cost", "charge": "cost", "provisions": "provision", "payables": "payables", "receivables": "receivables", "liabilities": "liability", "assets": "asset"}


def _tokens(label):
    s = (label or "").casefold().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    out = []
    for w in s.split():
        for x in _SYN.get(w, w).split():
            if x not in _STOP:
                out.append(x)
    return frozenset(out)


def match_label(label, candidates, taken=()):
    """The candidate heading that `label` means - a typed or imported line name against this year's headings: the same words (taxation = tax,
    debtors = receivables, "Profit/(loss) before tax" = "Profit before tax"), or one name containing all the words of the other (three or more:
    "Purchase of property, plant and equipment" is "Net movement in property, plant and equipment"). Exact (same words) wins; otherwise the
    best, and only if clearly better than the runner-up. None when unsure."""
    a = _tokens(label)
    if not a:
        return None
    scored = []
    for c in candidates:
        if c in taken:
            continue
        b = _tokens(c)
        if not b:
            continue
        if a == b:
            return c
        inter = len(a & b)
        jac = inter / len(a | b)
        contain = (a <= b or b <= a) and inter >= 3
        scored.append((max(jac, 0.7 if contain else 0.0), c))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < 0.6:
        return None
    if len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.05:
        return None
    return scored[0][1]


DEP_LABEL = "Depreciation and amortisation"      # an add-back line the prior year may have (this year's statement shows it as a memo only)
EXCH_LABEL = "Exchange differences"                # sits directly under Profit before tax
QUIET_HEADINGS = {"Net movement in intangible assets", "Net movement in investment property", "Net movement in long-term investments",
                  "Net movement in other non-current assets"}      # no box on screen unless this year or last year has a figure
FIN_LABEL, FIN_PAID_LABEL, TAX_PAID_LABEL = "Finance costs", "Finance costs paid", "Income tax paid"


def refine(section, label, amount, flip_exact=True):
    """The heading a typed / imported operating line really is, where the wording alone is ambiguous: finance costs / interest is the
    add-back (a positive amount) or the payment (a negative amount, or worded paid); any tax that is not deferred or before-tax is tax paid;
    a depreciation / amortisation charge is the add-back line. Returns (section, label)."""
    raw = (label or "").casefold()
    tk = _tokens(label)
    if section != "operating":
        return section, label
    if "depreciation" in tk and "accumulated" not in raw:
        return "operating", DEP_LABEL
    if "exchange" in tk or "forex" in raw:
        return "operating", EXCH_LABEL
    if {"finance", "cost"} <= tk and not (tk & {"income", "lease"}):
        if _key(label) == _key(FIN_LABEL) and not flip_exact:
            return "operating", FIN_LABEL                 # typed on the add-back row itself: it stays there
        if re.search(r"\bpaid\b|payment", raw) or (amount or 0) < 0:
            return "operating", FIN_PAID_LABEL
        return "operating", FIN_LABEL
    if {"deferred", "tax"} <= tk and "paid" not in tk:
        return "operating", "Movement in deferred tax"
    if "tax" in tk and not (tk & {"deferred", "profit", "before", "liability", "asset"}):
        return "operating", TAX_PAID_LABEL
    return section, label


PBT_LABEL = "Profit before tax"          # linked to the prior-year income statement, never typed


def _prior_pbt(statements):
    """The prior year's profit before tax from the income statement, or None."""
    for r in ((statements or {}).get("pl") or {}).get("rows", []):
        if (r.get("label") or "").strip().casefold() == PBT_LABEL.casefold():
            return r.get("prior")
    return None


def _rank_fn(std_sec):
    """Position of a statement row among this year's headings of one section: the heading's place in the standard list, with the subtotal
    rows ("... before working capital", "Cash generated from operations") and the depreciation memo line fitting between the groups."""
    pos = {_key(l): i for i, l in enumerate(std_sec)}
    wc = pos.get(_key("(Increase)/decrease in inventories"))
    paid = pos.get(_key("Finance costs paid"))
    adj_end = pos.get(_key("Movement in deferred tax"))
    pbt_pos = pos.get(_key(PBT_LABEL))

    def rank(row):
        k = _key(row["label"])
        if k in pos:
            return pos[k]
        low = k
        if k == _key(EXCH_LABEL) and pbt_pos is not None:
            return pbt_pos + 0.5
        if k == _key(DEP_LABEL) and adj_end is not None:
            return adj_end + 0.25
        if row.get("memo") and adj_end is not None:
            return adj_end + 0.5
        if "before working capital" in low and wc is not None:
            return wc - 0.5
        if low.startswith("cash generated") and paid is not None:
            return paid - 0.5
        return None
    return rank


def attach_prior_cash_flow(statements, engagement, editable=False):
    """Adds the captured comparatives to statements["cf"]: row["prior"] on every row, prior-only rows for lines with no counterpart this
    year, cf["prior"] = the prior year's totals. Each typed / imported line sits on the current-year row it means (same name, else the nearest
    wording, in any section); Profit before tax is not typed at all: it is the prior-year profit before tax of the income statement.
    Leaves cf["prior"] = None when nothing has been captured. Idempotent.
    editable=True (the screen): every heading of the statement gets a row, with its section in row["sec"], so last year's figure can be typed
    straight into the prior-year column; cf["prior"] is then always a dict ("empty" while nothing is saved)."""
    cf = (statements or {}).get("cf")
    if not cf or "prior" in cf:
        return statements
    lines = (CashFlowPriorLine.query.filter_by(engagement_id=engagement.id).order_by(CashFlowPriorLine.sort_order, CashFlowPriorLine.id).all())
    if not lines and not editable:
        cf["prior"] = None
        return statements
    cash_open = sum(l.amount or 0.0 for l in lines if l.section == "cash_open")
    keys = (("operating", "operating_rows"), ("investing", "investing_rows"), ("financing", "financing_rows"))
    std = standard_labels(cf.get("method") or engagement.cash_flow_method or "indirect")
    std_names = [n for ls in std.values() for n in ls]
    std_section = {n: sec for sec, ls in std.items() for n in ls}
    cur_rows = [(sec, r) for sec, key in keys for r in cf[key] if not r.get("bold") and not r.get("memo")]
    cur_names = [r["label"] for _, r in cur_rows]
    by_key = {}
    for sec, r in cur_rows:
        by_key.setdefault((sec, _key(r["label"])), r)
    pbt_prior = _prior_pbt(statements)
    typed_pbt = 0.0
    placed = {}                                  # id(row) -> amount
    leftovers = []                               # (section, label, amount) with no current-year row
    has_pos_fin = any(l.section == "operating" and _key(l.label) == _key(FIN_LABEL) and (l.amount or 0) > 0 for l in lines)
    for l in lines:
        if l.section == "cash_open":
            continue
        l_sec, l_label = refine(l.section, l.label, l.amount, flip_exact=has_pos_fin)
        if l_label in (DEP_LABEL, EXCH_LABEL):
            leftovers.append(("operating", l_label, l.amount or 0.0))
            continue
        row = by_key.get((l_sec, _key(l_label)))
        if row is None:
            hit = match_label(l_label, cur_names)
            row = next((r for _, r in cur_rows if r["label"] == hit), None) if hit else None
        label, sec = l_label, l_sec
        if row is not None:
            label = row["label"]
        else:
            hit = match_label(l_label, std_names)        # a heading this year has no row for (nil this year): still its own heading
            if hit:
                label, sec = hit, std_section[hit]
        if _key(label) == _key(PBT_LABEL):
            typed_pbt += l.amount or 0.0
            continue                                      # linked to the income statement instead
        if row is not None:
            placed[id(row)] = placed.get(id(row), 0.0) + (l.amount or 0.0)
        else:
            leftovers.append((sec, label, l.amount or 0.0))
    pbt_row = next((r for _, r in cur_rows if _key(r["label"]) == _key(PBT_LABEL)), None)
    if editable:
        have = {(sec, _key(r["label"])) for sec, r in cur_rows} | {(sec, _key(lab)) for sec, lab, _ in leftovers}
        for sec, labels in std.items():
            for lab in labels:
                if (sec, _key(lab)) not in have and _key(lab) != _key(PBT_LABEL) and lab not in QUIET_HEADINGS:
                    leftovers.append((sec, lab, 0.0))
        for special in (DEP_LABEL, EXCH_LABEL):
            if not any(lab == special for _, lab, _ in leftovers):
                leftovers.append(("operating", special, 0.0))
    if pbt_prior is not None:
        if pbt_row is not None:
            placed[id(pbt_row)] = pbt_prior
            pbt_row["linked"] = "income statement"
        elif (abs(pbt_prior) >= 0.5 or editable) and PBT_LABEL in std.get("operating", []):
            leftovers.append(("operating", PBT_LABEL, pbt_prior))
    totals = {}
    for sec, key in keys:
        rows = cf[key]
        rank = _rank_fn(std.get(sec, []))
        for lsec, label, amount in leftovers:
            if lsec != sec:
                continue
            if _key(label) == _key(DEP_LABEL):
                memo = next((r for r in rows if r.get("memo") and "depreciation" in r["label"].casefold()), None)
                if memo is not None:                      # one depreciation line: this year's memo line carries last year's add-back too
                    memo["prior_dep"] = memo.get("prior_dep", 0.0) + amount
                    continue
            hit = next((r for r in rows if r.get("prior_only") and _key(r["label"]) == _key(label)), None)
            if hit:
                hit["prior"] += amount
                continue
            new = {"label": label, "current": 0.0, "prior": amount, "note": None, "prior_only": True}
            if _key(label) == _key(PBT_LABEL):
                new["linked"] = "income statement"
            mine = rank(new)
            at = None
            if mine is not None:
                for k, r in enumerate(rows):
                    rk = rank(r)
                    if rk is not None and rk > mine:
                        at = k
                        break
            if at is None:
                rows.append(new)
            else:
                rows.insert(at, new)
        running = 0.0
        for r in rows:
            r["sec"] = sec
            if r.get("memo") and "prior_dep" in r:
                r["prior"] = r["prior_dep"]
                r["dep_merged"] = True
                r["save_label"] = DEP_LABEL
                r["label"] = "Depreciation and amortisation (current year: memo only - see note)"
                running += r["prior"]
            elif r.get("memo"):
                r["prior"] = None
            elif r.get("bold"):
                r["prior"] = running
            else:
                r["prior"] = placed.get(id(r), r.get("prior") or 0.0)
                running += r["prior"]
        totals[sec] = running
    net = totals["operating"] + totals["investing"] + totals["financing"]
    close = cash_open + net
    cf["prior"] = {"net_operating": totals["operating"], "net_investing": totals["investing"], "net_financing": totals["financing"],
                   "net_movement": net, "cash_open": cash_open, "cash_close_computed": close,
                   "cash_close_actual": cf["cash_open"], "variance": (close - cf["cash_open"]) if lines else 0.0, "empty": not lines,
                   "pbt": {"amount": pbt_prior, "typed": typed_pbt if abs(typed_pbt) >= 0.5 else None}}
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
            if _key(label) == _key(PBT_LABEL):
                continue                       # taken from the prior-year income statement
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


@cfprior_bp.route("/<int:engagement_id>/cash-flow-prior/save-inline", methods=["POST"])
@login_required
def save_inline(engagement_id):
    """The prior-year column of the Statement of Cash Flows itself: one box per line (row_sec / row_lab / row_amt in step), the opening cash
    (cash_open_amt) and any extra lines (x_sec / x_lab / x_amt). Replaces the saved lines; blank or zero boxes save nothing; Profit before tax
    has no box (it comes from the income statement)."""
    _engagement(engagement_id)
    f = request.form
    valid = {s for s, _ in SECTIONS}
    items = list(zip(f.getlist("row_sec"), f.getlist("row_lab"), f.getlist("row_amt")))
    items += list(zip(f.getlist("x_sec"), f.getlist("x_lab"), f.getlist("x_amt")))
    CashFlowPriorLine.query.filter_by(engagement_id=engagement_id).delete()
    kept = 0
    op = parse_amount(f.get("cash_open_amt"))
    if abs(op) > 0.004:
        db.session.add(CashFlowPriorLine(engagement_id=engagement_id, section="cash_open", label=OPEN_LABEL, amount=op, sort_order=kept)); kept += 1
    for sec, label, amt in items:
        label = (label or "").strip()
        amount = parse_amount(amt)
        if sec not in valid or not label or abs(amount) < 0.004 or _key(label) == _key(PBT_LABEL):
            continue
        db.session.add(CashFlowPriorLine(engagement_id=engagement_id, section=sec, label=label[:200], amount=amount, sort_order=kept)); kept += 1
    db.session.commit()
    flash(f"Prior-year figures saved ({kept} line(s)). Totals and the closing cash are worked out and checked against the trial balance.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation") + "#cf-statement")


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
    # name each imported line as this year's heading it means, so it sits beside it (and in the same section)
    std = standard_labels(engagement.cash_flow_method or "indirect")
    names = [(sec, l) for sec, ls in std.items() for l in ls]
    renamed = 0
    fixed = []
    for sec, label, amount in lines:
        if sec != "cash_open":
            sec, label = refine(sec, label, amount)
        if sec != "cash_open" and label != DEP_LABEL:
            hit = match_label(label, [n for _, n in names])
            if hit:
                if hit != label:
                    renamed += 1
                sec = next(s_ for s_, n in names if n == hit)
                label = hit
        fixed.append((sec, label, amount))
    dropped_pbt = [a for sec_, l_, a in fixed if _key(l_) == _key(PBT_LABEL)]
    lines = [x for x in fixed if _key(x[1]) != _key(PBT_LABEL)]          # profit before tax comes from the income statement
    CashFlowPriorLine.query.filter_by(engagement_id=engagement_id).delete()
    for i, (sec, label, amount) in enumerate(lines):
        db.session.add(CashFlowPriorLine(engagement_id=engagement_id, section=sec, label=label[:200], amount=amount, sort_order=i))
    db.session.commit()
    msg = f"Imported {len(lines)} line(s) from the amount column \"{info['column']}\"; the earlier lines were replaced."
    if dropped_pbt:
        msg += " Profit before tax was not imported: it is taken from the prior-year income statement."
    if renamed:
        msg += f" {renamed} line name(s) were matched to this year's headings so the figures sit beside this year's."
    if info["skipped"]:
        msg += f" Left out as subtotals (they are worked out): {', '.join(info['skipped'][:6])}{'...' if len(info['skipped']) > 6 else ''}."
    flash(msg + " Check the lines below and the tie-out to the trial balance, correct anything, and Save.", "success")
    return _back(engagement_id)
