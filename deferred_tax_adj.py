"""Compare the deferred tax the Deferred Tax Computation works out with what the trial balance carries, for the prior year and the
current year, and propose the adjustment journals that bring the trial balance to the computation. The preparer confirms on the
Deferred Tax tab; the journals are posted as the draft adjustments DT-PY (prior year, restates the comparatives and this year's
opening balances) and DT-CUR (current year), exactly like the Asset Register's PPE-REG: one of each at most, refreshed on every
confirmation, with review and partner sign-off cleared.

Everything is worked on the unrounded, ADJUSTED trial balance without DT-PY and DT-CUR themselves, so proposing again after a posting gives
the same journal (never a second layer), and DT-CUR is worked after the effect of DT-PY on this year's opening balances.
"""
from types import SimpleNamespace as NS

from flask import Blueprint, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Engagement, TrialBalance, DeferredTaxComputation, adjustment_key
import financials as fin

dtadj_bp = Blueprint("dtadj", __name__, url_prefix="/engagements")

REF_PRIOR, REF_CURRENT = "DT-PY", "DT-CUR"
TOL = 0.5          # whole dollars: the statements are in whole numbers


def r2(v):
    return round(float(v or 0.0) + 0.0, 2)


def _acct(tb, cat, default, words=()):
    """The name to use for a category: the trial balance's own account in it (preferring one with the given words), else a default."""
    lines = [l for l in tb.lines if l.fs_category == cat]
    for w in words:
        for l in lines:
            if w in (l.account_name or "").casefold():
                return l.account_name
    return lines[0].account_name if lines else default


def _lines(tb, delta_l, delta_a, balance_cat, balance_default, balance_words, oci=0.0):
    """Journal lines that move the deferred tax liability by delta_l and the deferred tax asset by delta_a (increases), the other side
    to the balancing account (and, for the current year, part of it to other comprehensive income)."""
    dtl = _acct(tb, "deferred_tax_liability", "Deferred tax liability", ("deferred",))
    dta = _acct(tb, "deferred_tax_asset", "Deferred tax asset", ("deferred",))
    out = []

    def add(name, cat, debit, credit):
        if abs(debit) < 0.005 and abs(credit) < 0.005:
            return
        if debit < 0:
            debit, credit = 0.0, credit - debit
        if credit < 0:
            debit, credit = debit - credit, 0.0
        out.append({"account_name": name, "fs_category": cat, "debit": r2(debit), "credit": r2(credit)})

    add(dtl, "deferred_tax_liability", 0.0, delta_l)
    add(dta, "deferred_tax_asset", delta_a, 0.0)
    d = delta_l - delta_a                                    # the increase in the net liability, to the balancing account(s)
    oci = (min(abs(oci), abs(d)) * (1 if d > 0 else -1)) if abs(d) >= 0.005 else 0.0      # the part of the movement that goes to OCI, on the movement's side
    if abs(oci) >= 0.005:
        add(_acct(tb, "oci_items", "Other comprehensive income - deferred tax", ("deferred", "revaluation")), "oci_items", oci, 0.0)
    add(_acct(tb, balance_cat, balance_default, balance_words), balance_cat, d - oci, 0.0)
    return out


def _like(lines, applies_to):
    return NS(applies_to=applies_to, carry_to_current=True,
              lines=[NS(account_name=l["account_name"], fs_category=l["fs_category"], debit=l["debit"], credit=l["credit"]) for l in lines])


def _bump(totals, adj_like):
    out = {k: dict(v) for k, v in totals.items()}
    for period, code, amount in fin.adjustment_effects(adj_like):
        if code in out:
            out[code][period] += amount
    return out


def _move(tb, totals, period, computed_net, balance_cat, balance_default, balance_words, oci=0.0):
    """Journal lines taking the trial balance's deferred tax (liability and asset, in `period`) to the computed net position."""
    cur_l = totals["deferred_tax_liability"][period]
    cur_a = totals["deferred_tax_asset"][period]
    target_l, target_a = (computed_net, 0.0) if computed_net >= 0 else (0.0, -computed_net)
    delta_l, delta_a = target_l - cur_l, target_a - cur_a
    info = {"tb_l": r2(cur_l), "tb_a": r2(cur_a), "tb_net": r2(cur_l - cur_a), "computed": r2(computed_net),
            "diff": r2(computed_net - (cur_l - cur_a)), "lines": []}
    if abs(info["diff"]) >= TOL:
        info["lines"] = _lines(tb, delta_l, delta_a, balance_cat, balance_default, balance_words, oci)
    return info


def position(engagement, oci_current=0.0):
    """The comparison and the proposed journals for an engagement, or {"ok": False, "reason": ...}. Works only from what is on file;
    nothing is written."""
    import engagements as eng
    import engagement_tax
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    if not tb or not tb.lines:
        return {"ok": False, "reason": "There is no trial balance yet, so there is nothing to compare the computation with."}
    rec = DeferredTaxComputation.query.filter_by(engagement_id=engagement.id).first()
    if rec is None or rec.tax_rate_percent is None:
        return {"ok": False, "reason": "Enter the tax rate and save the computation first."}
    rate = rec.tax_rate_percent / 100.0
    prate = (rec.prior_tax_rate_percent if rec.prior_tax_rate_percent is not None else rec.tax_rate_percent) / 100.0
    _, _, cdicts, adicts = eng._ppe_plain_data(engagement.id)
    movement, _txt = eng._ppe_movement_and_policy(cdicts, adicts, engagement.period_end)
    ca_ctx = engagement_tax.capital_allowance_context(engagement) if (engagement.has_tax_module or engagement.has_asset_schedules) else None
    ppe = engagement_tax.deferred_tax_ppe(ca_ctx, movement, rec)

    # current year: the computation as shown on the tab
    items = [{"description": i.description, "accounting_amount": i.accounting_amount, "tax_base_amount": i.tax_base_amount} for i in rec.items]
    nbv = movement["total"]["closing_nbv"] if movement else None
    cur = fin.build_deferred_tax_computation(rec.tax_rate_percent, nbv, ppe["tax_base_total"], items, ppe_rows=ppe["rows"])
    computed_cur = cur["total_deferred_tax"]
    # prior year: property, plant and equipment at the start of the year (carrying amount b/f against tax value b/f), and the other
    # differences that have a prior-year figure
    pri_rows, prior_notes = [], []
    if movement and ca_ctx and ca_ctx.get("ca_result"):
        pri_rows.append(movement["total"]["opening_nbv"] - ca_ctx["ca_result"]["total"]["opening"])
    elif movement:
        prior_notes.append("No Capital Allowance schedule, so the prior-year tax base of property, plant and equipment is not known.")
    missing = []
    for i in rec.items:
        if i.prior_accounting_amount is None and i.prior_tax_base_amount is None:
            missing.append(i.description)
        else:
            pri_rows.append((i.prior_accounting_amount or 0.0) - (i.prior_tax_base_amount or 0.0))
    if missing:
        prior_notes.append("No prior-year figures for: " + ", ".join(missing) + ". They are left out of the prior-year computation.")
    computed_pri = sum(pri_rows) * prate if pri_rows else None

    base_adj = [a for a in tb.adjustments if adjustment_key(a) not in (REF_PRIOR, REF_CURRENT)]
    totals = fin.apply_adjustments(fin.compute_totals(tb.lines), base_adj)

    out = {"ok": True, "tb": tb, "rate": rec.tax_rate_percent, "prior_rate": prate * 100.0, "prior_notes": prior_notes, "warnings": list(ppe["warnings"]),
           "prior": None, "current": None, "oci": oci_current}
    after_py = totals
    if computed_pri is not None:
        p = _move(tb, totals, "prior", computed_pri, "retained_earnings", "Retained earnings", ("brought forward", "b/f", "retained"))
        p["reference"] = REF_PRIOR
        out["prior"] = p
        if p["lines"]:
            after_py = _bump(totals, _like(p["lines"], "prior"))
    c = _move(tb, after_py, "current", computed_cur, "income_tax_expense", "Deferred tax expense", ("deferred",), oci_current)
    c["reference"] = REF_CURRENT
    out["current"] = c
    # what the trial balance itself records as the year's deferred tax charge, for information
    exp = sum((l.current_debit or 0.0) - (l.current_credit or 0.0) for l in tb.lines
              if l.fs_category == "income_tax_expense" and "deferred" in (l.account_name or "").casefold())
    out["tb_deferred_expense"] = r2(exp)
    if computed_pri is not None:
        out["computed_movement"] = r2(computed_cur - computed_pri)
    for key in ("prior", "current"):
        blk = out[key]
        if not blk:
            continue
        ex = next((a for a in tb.adjustments if adjustment_key(a) == blk["reference"]), None)
        blk["existing"] = ex
        want = sorted((l["account_name"], l["fs_category"], l["debit"], l["credit"]) for l in blk["lines"])
        have = sorted((l.account_name, l.fs_category, r2(l.debit), r2(l.credit)) for l in (ex.lines if ex else []))
        blk["in_sync"] = want == have
    return out


@dtadj_bp.route("/<int:engagement_id>/deferred-tax/post-journals", methods=["POST"])
@login_required
def post_journals(engagement_id):
    import engagements as eng
    import asset_recon
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    back = redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="deferred_tax"))
    try:
        oci = float((request.form.get("oci_amount") or "0").replace(",", "") or 0)
    except ValueError:
        oci = 0.0
    pos = position(engagement, oci)
    if not pos["ok"]:
        flash(pos["reason"], "danger")
        return back
    tb = pos["tb"]
    which = request.form.getlist("post")                  # "prior" and/or "current"
    done = []
    ye = engagement.period_end.strftime("%d %B %Y") if engagement.period_end else "the year"
    if "prior" in which and pos["prior"] is not None:
        p = pos["prior"]
        st = asset_recon._upsert(tb, REF_PRIOR, p["lines"],
                                 f"Deferred tax brought to the Deferred Tax Computation at the start of the year ended {ye} (IAS 8 restatement of the comparatives). "
                                 f"Computed net deferred tax liability/(asset) {p['computed']:,.2f}; trial balance {p['tb_net']:,.2f}. Proposed from the Deferred Tax tab.",
                                 "prior")
        if st:
            done.append(f"{asset_recon.journal_label(tb, REF_PRIOR)} {st}")
    if "current" in which and pos["current"] is not None:
        c = pos["current"]
        st = asset_recon._upsert(tb, REF_CURRENT, c["lines"],
                                 f"Deferred tax brought to the Deferred Tax Computation at {ye}: computed net deferred tax liability/(asset) {c['computed']:,.2f}; "
                                 f"trial balance {c['tb_net']:,.2f}. Proposed from the Deferred Tax tab.", "current")
        if st:
            done.append(f"{asset_recon.journal_label(tb, REF_CURRENT)} {st}")
    if not which:
        flash("Tick the journal(s) you confirm.", "info")
    elif not done:
        flash("The trial balance already agrees to the computation - no journal needed.", "info")
    else:
        db.session.commit()
        flash(f"Adjustments {', '.join(done)}. They are drafts: review and partner sign-off are on the Finalisation tab.", "success")
    return back
