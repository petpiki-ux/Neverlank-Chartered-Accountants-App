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
