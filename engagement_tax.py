"""Tax Advisory engagements: capital allowances and provisional tax (QPD), in line with Firm Finance > Tax.

A dedicated Tax Advisory engagement works from the client's finished accounts, so it has no Trial Balance, Analytical
Review, Risk Assessment or Substantive Procedures tabs. What it needs instead is the same tax machinery the firm uses
on its own books:

* Capital Allowances - the asset register of the client (PPEAsset / PPEAssetClass, the same rows the audit tab set
  keeps), one allowance rule per asset class (EngagementTaxAssetRule, same fields as the firm's FirmTaxAssetRule) and
  the ONE engine in firm_tax.py (SIA, wear and tear straight-line or reducing balance, recoupment, scrapping) worked
  for the engagement's period end. The result is posted to the engagement's Income Tax Computation as lines, and its
  closing tax value can be used as the PPE tax base of the Deferred Tax Computation.
* Provisional tax (QPD) - the client's QPD estimate for the tax year and its instalments, fed from this engagement's
  Income Tax Computation.

Nothing here is hard-coded law. Starter allowance rules are marked "not verified" until a Partner or Admin has checked
them against the Income Tax Act and ticked them; changing a rule clears its tick again, exactly as on the firm's own
Capital Allowances page. The rates themselves (tax rate, AIDS levy) are entered on the Income Tax Computation.
"""
from datetime import date, datetime

from flask import Blueprint, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

import firm_tax
import financials as fin
from extensions import db
from models import (
    Engagement, PPEAsset, PPEAssetClass, EngagementTaxAssetRule, IncomeTaxComputation, IncomeTaxAdjustmentLine,
    DeferredTaxComputation, TrialBalance, QPDEstimate, QPDInstalmentRecord, QPDInstalmentRate, user_has_permission,
)

engtax_bp = Blueprint("engtax", __name__)

ENGINE_SOURCE = "cap_allow_engine"
RULE_METHODS = [
    ("WT_SL", "Wear and tear - straight line on cost"),
    ("WT_RB", "Wear and tear - reducing balance"),
    ("SIA", "Special initial allowance"),
    ("NONE", "No allowance"),
]
RULE_METHOD_LABELS = dict(RULE_METHODS)


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace(" ", "").strip())
    except ValueError:
        return None


def _date(v):
    if not v:
        return None
    try:
        return datetime.strptime(v.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _senior():
    return getattr(current_user, "role", None) in ("partner", "admin")


def _access(engagement_id):
    from engagements import _ensure_engagement_access
    eng = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(eng)
    return eng


def _back(engagement_id, tab):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=tab))


def r2(v):
    return round(float(v or 0.0), 2)


# ====================================================================== capital allowances

def ensure_rules(engagement):
    """One rule per asset class of this engagement (created on first use with the starter treatment)."""
    classes = PPEAssetClass.query.filter_by(engagement_id=engagement.id).order_by(PPEAssetClass.order, PPEAssetClass.id).all()
    have = {r.asset_class_id: r for r in EngagementTaxAssetRule.query.filter_by(engagement_id=engagement.id).all()}
    added = False
    for c in classes:
        if c.id not in have:
            d = firm_tax.default_rule(c.name)
            rule = EngagementTaxAssetRule(engagement_id=engagement.id, asset_class_id=c.id, method=d["method"],
                                          wt_rate_percent=d["wt"], note=d["note"])
            db.session.add(rule)
            have[c.id] = rule
            added = True
    if added:
        db.session.commit()
    return classes, have


def opening_as_at(engagement):
    """The date the opening balances stand at: the year end before the one being reported (31 Dec 2024 for a period ending 31 Dec 2025)."""
    end = engagement.period_end
    return firm_tax.fy_end(end, end.year - 1) if end else None


def asset_dict(a, as_at):
    """One register row as the capital allowance engine reads it. An asset with a tax value brought forward is worked from it."""
    return {
        "asset_code": a.asset_code, "description": a.description, "asset_class_id": a.asset_class_id,
        "date_acquired": a.date_acquired, "cost": a.cost or 0.0, "disposed_on": a.tax_disposed_on,
        "disposal_proceeds": a.tax_disposal_proceeds or 0.0, "id": a.id,
        "opening_tax_value": a.opening_tax_value, "opening_as_at": as_at if a.opening_tax_value is not None else None,
    }


def capital_allowance_context(engagement):
    """Everything the Capital Allowances tab shows. Worked for the fiscal year ending on the engagement's period end."""
    classes, rules = ensure_rules(engagement)
    assets = PPEAsset.query.filter_by(engagement_id=engagement.id).order_by(PPEAsset.order, PPEAsset.id).all()
    target_end = engagement.period_end
    ctx = {
        "ca_classes": classes, "ca_rules": rules, "ca_assets": assets, "ca_target_end": target_end,
        "ca_methods": RULE_METHODS, "ca_method_labels": RULE_METHOD_LABELS,
        "ca_opening_as_at": opening_as_at(engagement),
        "ca_result": None, "ca_undated": [a for a in assets if not a.date_acquired and a.opening_tax_value is None],
        "ca_unclassified": [a for a in assets if a.date_acquired and not a.asset_class_id],
        "ca_unverified": [], "ca_depreciation": 0.0, "ca_posted": [], "ca_stale": False,
        "ca_is_senior": _senior(),
    }
    comp = IncomeTaxComputation.query.filter_by(engagement_id=engagement.id).first()
    ctx["ca_posted"] = [l for l in (comp.items if comp else []) if l.source == ENGINE_SOURCE]
    if not target_end:
        return ctx
    class_dicts = [{"id": c.id, "name": c.name} for c in classes]
    rule_dicts = {cid: firm_tax.rule_dict(r) for cid, r in rules.items()}
    asset_dicts = [asset_dict(a, ctx["ca_opening_as_at"]) for a in assets if a.date_acquired or a.opening_tax_value is not None]
    result = firm_tax.capital_allowances(asset_dicts, class_dicts, rule_dicts, target_end, ref_end=target_end)
    ctx["ca_result"] = result
    used = {r["class_id"] for r in result["rows"] if r.get("class_id")}
    ctx["ca_unverified"] = [c for c in classes if c.id in used and not rules[c.id].verified and rules[c.id].method != "NONE"]
    # depreciation the accounts charged on the assets in the register (an add-back; the allowance replaces it)
    ctx["ca_depreciation"] = r2(sum((a.current_year_depreciation or 0.0) for a in assets))
    have = sorted((l.description, r2(l.amount)) for l in ctx["ca_posted"])
    want = sorted((l["description"], r2(l["amount"])) for l in posted_lines(result, ctx["ca_depreciation"]))
    ctx["ca_stale"] = bool(ctx["ca_posted"]) and have != want
    return ctx


def posted_lines(result, depreciation):
    """The lines this schedule contributes to the Income Tax Computation."""
    t = result["total"]
    lines = []
    if depreciation > 0.004:
        lines.append(dict(key="dep", item_type="addback", description="Depreciation charged in the accounts (asset register)", amount=r2(depreciation)))
    if t["recoupment"] > 0.004:
        lines.append(dict(key="rec", item_type="addback", description="Recoupment on assets sold above their tax value", amount=r2(t["recoupment"])))
    if t["allowance"] > 0.004:
        lines.append(dict(key="all", item_type="capital_allowance", description="Capital allowances for the year (special initial allowance / wear and tear)", amount=r2(t["allowance"])))
    if t["scrapping"] > 0.004:
        lines.append(dict(key="scr", item_type="capital_allowance", description="Scrapping allowance on assets sold below their tax value", amount=r2(t["scrapping"])))
    return lines


@engtax_bp.route("/engagements/<int:engagement_id>/capital-allowances/rules/<int:class_id>", methods=["POST"])
@login_required
def save_rule(engagement_id, class_id):
    eng = _access(engagement_id)
    cls = PPEAssetClass.query.get_or_404(class_id)
    if cls.engagement_id != eng.id:
        abort(404)
    ensure_rules(eng)
    rule = EngagementTaxAssetRule.query.filter_by(engagement_id=eng.id, asset_class_id=class_id).first_or_404()
    f = request.form
    method = f.get("method") if f.get("method") in RULE_METHOD_LABELS else rule.method
    before = (rule.method, rule.wt_rate_percent, rule.sia_percent, rule.sia_years, rule.sia_first_percent, rule.cost_cap, bool(rule.prorata_first_year))
    rule.method = method
    rule.wt_rate_percent = _num(f.get("wt_rate_percent")) or 0.0
    sia = _num(f.get("sia_percent"))
    rule.sia_percent = 25.0 if sia is None else sia
    years = _num(f.get("sia_years"))
    rule.sia_years = 4 if years is None else int(years)
    rule.sia_first_percent = _num(f.get("sia_first_percent"))
    rule.cost_cap = _num(f.get("cost_cap"))
    rule.prorata_first_year = bool(f.get("prorata_first_year"))
    rule.note = (f.get("note") or "").strip()[:250] or None
    after = (rule.method, rule.wt_rate_percent, rule.sia_percent, rule.sia_years, rule.sia_first_percent, rule.cost_cap, bool(rule.prorata_first_year))
    changed = after != before
    if _senior():
        rule.verified = bool(f.get("verified"))       # a Partner/Admin's tick is the verification, also when they edit and tick together
    elif changed:
        rule.verified = False                          # anyone else changing a rule clears the tick and can never set one
    db.session.commit()
    flash(f"Allowance rule for {cls.name} saved." + ("" if rule.verified else " It is not marked verified yet."), "success")
    return _back(eng.id, "capital_allowances")


@engtax_bp.route("/engagements/<int:engagement_id>/capital-allowances/assets/<int:asset_id>/disposal", methods=["POST"])
@login_required
def save_disposal(engagement_id, asset_id):
    eng = _access(engagement_id)
    asset = PPEAsset.query.get_or_404(asset_id)
    if asset.engagement_id != eng.id:
        abort(404)
    on = _date(request.form.get("tax_disposed_on"))
    proceeds = _num(request.form.get("tax_disposal_proceeds"))
    if on and proceeds is None:
        proceeds = 0.0
    import asset_schedules
    if on:
        # Dated here, the disposal also takes the cost and depreciation out of the Asset Register (as on its Disposals card), unless
        # figures for those were already entered there.
        keep_cost, keep_acc = asset.disposal_cost or None, asset.disposal_accumulated_depreciation or None
        asset_schedules.apply_disposal(asset, on, proceeds, keep_cost, keep_acc)
    else:
        asset_schedules.clear_disposal(asset)
    db.session.commit()
    flash("Disposal details saved for the allowance schedule." if on else "Disposal details cleared.", "success")
    return _back(eng.id, "capital_allowances")


def _computation(engagement_id):
    comp = IncomeTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    if comp is None:
        comp = IncomeTaxComputation(engagement_id=engagement_id, tax_loss_brought_forward=0.0)
        db.session.add(comp)
        db.session.flush()
    return comp


@engtax_bp.route("/engagements/<int:engagement_id>/capital-allowances/post-to-income-tax", methods=["POST"])
@login_required
def post_to_income_tax(engagement_id):
    eng = _access(engagement_id)
    ctx = capital_allowance_context(eng)
    if not ctx["ca_result"]:
        flash("Set the engagement's period end and add dated assets before posting the allowances.", "danger")
        return _back(eng.id, "capital_allowances")
    comp = _computation(eng.id)
    for l in list(comp.items):
        if l.source == ENGINE_SOURCE:
            db.session.delete(l)
    db.session.flush()
    order = IncomeTaxAdjustmentLine.query.filter_by(computation_id=comp.id).count()
    lines = posted_lines(ctx["ca_result"], ctx["ca_depreciation"])
    for l in lines:
        db.session.add(IncomeTaxAdjustmentLine(computation_id=comp.id, item_type=l["item_type"], description=l["description"],
                                               amount=l["amount"], order=order, source=ENGINE_SOURCE))
        order += 1
    comp.completed_by_id, comp.completed_at = current_user.id, datetime.utcnow()
    comp.reviewed_by_id = comp.reviewed_at = comp.partner_signed_by_id = comp.partner_signed_at = None
    db.session.commit()
    flash(f"{len(lines)} line(s) posted to the Income Tax Computation (any earlier lines from this schedule were replaced)." if lines
          else "Nothing to post - the schedule has no allowance, recoupment or depreciation this year.", "success")
    return _back(eng.id, "income_tax")


@engtax_bp.route("/engagements/<int:engagement_id>/capital-allowances/use-tax-base", methods=["POST"])
@login_required
def use_tax_base(engagement_id):
    eng = _access(engagement_id)
    ctx = capital_allowance_context(eng)
    if not ctx["ca_result"]:
        flash("Set the engagement's period end and add dated assets first.", "danger")
        return _back(eng.id, "capital_allowances")
    rec = DeferredTaxComputation.query.filter_by(engagement_id=eng.id).first()
    if rec is None:
        rec = DeferredTaxComputation(engagement_id=eng.id)
        db.session.add(rec)
    rec.ppe_tax_base = r2(ctx["ca_result"]["tax_value_end"])
    rec.completed_by_id, rec.completed_at = current_user.id, datetime.utcnow()
    rec.reviewed_by_id = rec.reviewed_at = rec.partner_signed_by_id = rec.partner_signed_at = None
    db.session.commit()
    flash(f"The PPE tax base of the Deferred Tax Computation is now {rec.ppe_tax_base:,.2f}, the tax value at the period end.", "success")
    return _back(eng.id, "deferred_tax")


# ====================================================================== provisional tax (QPD)

def profit_before_tax_for(engagement):
    """The computation's profit before tax: the trial balance's, when the engagement has one; otherwise the figure typed on the
    Income Tax Computation. None when neither exists."""
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    if tb and tb.lines:
        st = fin.build_all_statements(tb.lines, tb.adjustments, reporting_framework=engagement.reporting_framework,
                                      cash_flow_method=engagement.cash_flow_method)
        return st["pl"]["profit_before_tax"]["current"]
    comp = IncomeTaxComputation.query.filter_by(engagement_id=engagement.id).first()
    return comp.profit_before_tax_manual if comp else None


def income_tax_result_for(engagement):
    comp = IncomeTaxComputation.query.filter_by(engagement_id=engagement.id).first()
    pbt = profit_before_tax_for(engagement)
    if pbt is None:
        return None
    lines = [{"item_type": l.item_type, "description": l.description, "amount": l.amount} for l in (comp.items if comp else [])]
    return fin.build_income_tax_computation(
        pbt, lines, tax_loss_brought_forward=comp.tax_loss_brought_forward if comp else 0.0,
        tax_rate_percent=comp.tax_rate_percent if comp else None, aids_levy_percent=comp.aids_levy_percent if comp else None)


def qpd_tax_year(engagement):
    y = request.args.get("tax_year", type=int)
    if y:
        return y
    return engagement.period_end.year if engagement.period_end else date.today().year


def qpd_context(engagement):
    tax_year = qpd_tax_year(engagement)
    estimate = QPDEstimate.query.filter_by(client_id=engagement.client_id, tax_year=tax_year).first()
    instalments = sorted(estimate.instalments, key=lambda r: r.order or 0) if estimate else []
    result = income_tax_result_for(engagement)
    return {
        "qpd_tax_year": tax_year, "qpd_estimate": estimate, "qpd_instalments": instalments, "qpd_result": result,
        "qpd_can_manage": user_has_permission(current_user, "manage_qpd"),
        "qpd_has_structure": QPDInstalmentRate.query.count() > 0,
        "qpd_out_of_step": bool(estimate and result and abs((estimate.estimated_annual_tax_charge or 0.0) - result["total_tax_charge"]) > 0.005),
    }


@engtax_bp.route("/engagements/<int:engagement_id>/qpd/use-computation", methods=["POST"])
@login_required
def qpd_use_computation(engagement_id):
    eng = _access(engagement_id)
    if not user_has_permission(current_user, "manage_qpd"):
        abort(403, description="You do not have permission to change QPD estimates.")
    if not eng.client.qualifies_for_qpd:
        abort(403)
    tax_year = request.form.get("tax_year", type=int) or (eng.period_end.year if eng.period_end else date.today().year)
    result = income_tax_result_for(eng)
    if result is None:
        flash("Enter the profit before tax on the Income Tax Computation first.", "danger")
        return _back(eng.id, "qpd")
    if not result["tax_rate_percent"]:
        flash("Enter the corporate tax rate on the Income Tax Computation first - the app never assumes one.", "danger")
        return _back(eng.id, "qpd")
    est = QPDEstimate.query.filter_by(client_id=eng.client_id, tax_year=tax_year).first()
    if est is None:
        est = QPDEstimate(client_id=eng.client_id, tax_year=tax_year)
        db.session.add(est)
        db.session.flush()
    est.estimated_annual_taxable_income = r2(result["taxable_income"])
    est.tax_rate_pct = result["tax_rate_percent"]
    est.aids_levy_pct = result["aids_levy_percent"]
    est.estimated_annual_tax_charge = r2(result["total_tax_charge"])
    est.estimation_method = "Manual"
    note = f"From the Income Tax Computation of engagement \"{eng.title}\" ({datetime.utcnow().strftime('%d %b %Y')})."
    est.notes = ((est.notes + "\n") if est.notes else "") + note
    est.updated_by_id = current_user.id
    est.updated_at = datetime.utcnow()
    if not QPDInstalmentRate.query.count():
        db.session.commit()
        flash("QPD estimate set from this engagement's Income Tax Computation. No QPD instalment structure is configured yet, so no instalments were computed.", "warning")
        return _back(eng.id, "qpd")
    from qpd import build_instalment_records
    skipped = build_instalment_records(est)
    db.session.commit()
    flash("QPD estimate set from this engagement's Income Tax Computation and the instalments recomputed"
          + (f" ({skipped} already-paid instalment(s) left untouched)." if skipped else "."), "success")
    return _back(eng.id, "qpd")
