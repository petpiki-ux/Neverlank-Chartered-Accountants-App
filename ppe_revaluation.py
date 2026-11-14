"""Revaluation of property, plant and equipment on an independent valuation (IAS 16.31 to 42 and 77; IFRS for SMEs section 17.15B).

The valuer's fair values, asset by asset, are entered on the Asset Register tab. Nothing changes in the statements until the revaluation is
APPLIED; applying it posts ONE journal, REVAL, to the trial balance and makes the Asset Register, the Property, plant and equipment note, the
statement of changes in equity and the cash flow statement carry the revalued amounts. Undoing it removes the journal and puts everything back.

Per asset (the valuation is as at the year end, so there is no depreciation split between the date of valuation and the year end):

  carrying amount  = cost - accumulated depreciation, at the year end, as the register stands before the revaluation
  change           = fair value - carrying amount
  an INCREASE      goes to other comprehensive income (the revaluation reserve), except that it is first taken to profit or loss to the
                   extent it reverses an earlier revaluation DECREASE of the same asset that was charged there (IAS 16.39)
  a DECREASE       is charged against the revaluation reserve held for the same asset, and only the rest to profit or loss (IAS 16.40)
  accumulated depreciation is ELIMINATED against the gross carrying amount (IAS 16.35(b)): cost becomes the valuation, accumulated
                   depreciation becomes nil
  deferred tax     is provided on the increase at the rate typed for the asset class: on the part in other comprehensive income it goes to
                   other comprehensive income, on the part in profit or loss to the tax charge (IAS 12.61A)

The journal, aggregated by asset class:

  Dr/Cr  <Class> - cost (revaluation)                                        the valuation less the cost before it
  Dr     <Class> - accumulated depreciation (eliminated on revaluation)      the accumulated depreciation taken out
  Cr/Dr  Revaluation surplus on property, plant and equipment (OCI)          the part of the change that goes to the reserve
  Dr/Cr  Deferred tax on revaluation surplus (OCI)                           the deferred tax on that part
  Cr     Reversal of previous revaluation loss  /  Dr  Revaluation deficit charged to profit or loss
  Dr/Cr  Income tax expense                                                  the deferred tax on the part in profit or loss
  Cr/Dr  Deferred tax liability                                              all the deferred tax

Known limits of this first version: the excess of the depreciation on the revalued amount over the depreciation on cost is not transferred
from the revaluation reserve to retained earnings (IAS 16.41 permits, does not require it), and the valuation date is the year end.
"""
from datetime import datetime

from flask import Blueprint, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import (Engagement, PPEAsset, PPEAssetClass, TrialBalance, PPERevaluation, PPERevaluationLine, DeferredTaxComputation)

ppereval_bp = Blueprint("ppereval", __name__, url_prefix="/engagements")

REFERENCE = "REVAL"
BASES = ["Market value (open market)", "Depreciated replacement cost", "Income (capitalisation) approach", "Market value for existing use", "Other fair value basis"]

SURPLUS_ACCOUNT = "Revaluation surplus on property, plant and equipment (OCI)"
DT_OCI_ACCOUNT = "Deferred tax on revaluation surplus (OCI)"
REVERSAL_ACCOUNT = "Reversal of previous revaluation loss"
DEFICIT_ACCOUNT = "Revaluation deficit charged to profit or loss"


def r2(v):
    return round(float(v or 0.0) + 0.0, 2)


# ---------------------------------------------------------------------------------------------------- the stored revaluation

def get(engagement_id):
    return PPERevaluation.query.filter_by(engagement_id=engagement_id).first()


def get_or_create(engagement):
    rec = get(engagement.id)
    if rec is None:
        rec = PPERevaluation(engagement_id=engagement.id, valuation_date=engagement.period_end, independent=True, status="Draft")
        db.session.add(rec)
        db.session.flush()
    return rec


def is_applied(rec):
    return rec is not None and rec.status == "Applied"


def applied_values(engagement_id):
    """{asset id: fair value} of the assets revalued by an APPLIED revaluation; {} while it is a draft or there is none. This is what
    feeds the Asset Register movement schedule (as "revalued_to")."""
    rec = get(engagement_id)
    if not is_applied(rec):
        return {}
    return {l.asset_id: float(l.fair_value) for l in rec.lines if l.fair_value is not None}


def revalued_class_ids(engagement_id):
    """Ids of the asset classes holding at least one asset revalued by the applied revaluation."""
    rec = get(engagement_id)
    if not is_applied(rec):
        return set()
    return {l.asset.asset_class_id for l in rec.lines if l.fair_value is not None and l.asset is not None}


# ---------------------------------------------------------------------------------------------------- the register's figures

def asset_figures(asset, period_start, period_end):
    """The year-end cost, accumulated depreciation and carrying amount of one asset from the register BEFORE any revaluation, or
    {"eligible": False, "reason": ...} when it cannot be revalued (sold in the year, or acquired after the year end)."""
    d = asset.date_acquired
    if d and period_end and d > period_end:
        return {"eligible": False, "reason": "acquired after the year end"}
    if (asset.disposal_cost or 0.0) > 0:
        return {"eligible": False, "reason": "disposed of in the year"}
    is_addition = bool(d and period_start and period_end and period_start <= d <= period_end)
    cost = asset.cost or 0.0
    opening_acc = 0.0 if is_addition else (asset.opening_accumulated_depreciation or 0.0)
    acc = opening_acc + (asset.current_year_depreciation or 0.0) - (asset.disposal_accumulated_depreciation or 0.0)
    return {"eligible": True, "cost": cost, "acc": acc, "nbv": cost - acc}


def split_change(change, prior_reserve, prior_deficit_pl):
    """(to other comprehensive income, to profit or loss) of one asset's revaluation change, each signed (an increase positive).
    An increase first reverses an earlier decrease that was charged to profit or loss; a decrease first uses up the revaluation reserve
    the asset already has (IAS 16.39 and 16.40)."""
    prior_reserve = max(prior_reserve or 0.0, 0.0)
    prior_deficit_pl = max(prior_deficit_pl or 0.0, 0.0)
    if change >= 0:
        pl = min(change, prior_deficit_pl)
        return change - pl, pl
    dec = -change
    oci_dec = min(dec, prior_reserve)
    return -oci_dec, -(dec - oci_dec)


def default_dt_rate(engagement_id):
    rec = DeferredTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    return float(rec.tax_rate_percent) if rec is not None and rec.tax_rate_percent is not None else None


def class_name_of(asset):
    return (asset.asset_class.name if getattr(asset, "asset_class", None) is not None and asset.asset_class.name else "Property, plant and equipment")


def calculate(engagement, rec):
    """Everything about the revaluation as it stands: the lines with their carrying amount, change and split, the totals by asset class,
    the journal lines, and what is wrong with it. Nothing is written."""
    import asset_schedules as sched
    ps, pe, _ = sched.bounds(engagement)
    default_rate = default_dt_rate(engagement.id)
    lines, issues, warnings = [], [], []
    by_class = {}
    for l in (rec.lines if rec is not None else []):
        a = l.asset
        if a is None or a.engagement_id != engagement.id:
            continue
        fig = asset_figures(a, ps, pe)
        row = {"line": l, "asset": a, "class_name": class_name_of(a), "fair_value": l.fair_value, "eligible": fig["eligible"],
               "reason": fig.get("reason", ""), "prior_reserve": l.prior_reserve or 0.0, "prior_deficit_pl": l.prior_deficit_pl or 0.0,
               "rate": l.dt_rate if l.dt_rate is not None else (default_rate if default_rate is not None else 0.0)}
        if not fig["eligible"]:
            row.update(cost=0.0, acc=0.0, nbv=0.0, change=0.0, oci=0.0, pl=0.0, dt_oci=0.0, dt_pl=0.0)
            if l.fair_value is not None:
                issues.append(f"{a.description or 'An asset'} was {fig['reason']} and cannot be revalued - remove it from the valuation.")
            lines.append(row)
            continue
        row.update(cost=fig["cost"], acc=fig["acc"], nbv=fig["nbv"])
        if l.fair_value is None:
            row.update(change=0.0, oci=0.0, pl=0.0, dt_oci=0.0, dt_pl=0.0)
            lines.append(row)
            continue
        if l.fair_value < 0:
            issues.append(f"{a.description or 'An asset'}: the fair value cannot be negative.")
        change = l.fair_value - fig["nbv"]
        oci, pl = split_change(change, l.prior_reserve, l.prior_deficit_pl)
        rate = row["rate"] / 100.0
        row.update(change=change, oci=oci, pl=pl, dt_oci=oci * rate, dt_pl=pl * rate)
        lines.append(row)
        g = by_class.setdefault(row["class_name"], {"class_name": row["class_name"], "cost": 0.0, "acc": 0.0, "nbv": 0.0, "fair_value": 0.0,
                                                    "change": 0.0, "oci": 0.0, "pl": 0.0, "pl_inc": 0.0, "pl_dec": 0.0, "dt_oci": 0.0, "dt_pl": 0.0, "prior_reserve": 0.0,
                                                    "prior_reserve_net": 0.0, "count": 0, "rate": row["rate"], "class_id": a.asset_class_id})
        g["cost"] += fig["cost"]; g["acc"] += fig["acc"]; g["nbv"] += fig["nbv"]; g["fair_value"] += l.fair_value
        g["change"] += change; g["oci"] += oci; g["pl"] += pl; g["pl_inc"] += max(pl, 0.0); g["pl_dec"] += max(-pl, 0.0); g["dt_oci"] += oci * rate; g["dt_pl"] += pl * rate
        g["prior_reserve"] += l.prior_reserve or 0.0; g["prior_reserve_net"] += (l.prior_reserve or 0.0) * (1 - rate); g["count"] += 1

    classes = sorted(by_class.values(), key=lambda g: g["class_name"].lower())
    tot = {k: sum(g[k] for g in classes) for k in ("cost", "acc", "nbv", "fair_value", "change", "oci", "pl", "pl_inc", "pl_dec", "dt_oci", "dt_pl", "prior_reserve", "prior_reserve_net")}
    tot["count"] = sum(g["count"] for g in classes)

    # the whole-class rule (IAS 16.36): every asset in a class that has one revalued should be revalued
    valued_ids = {r["asset"].id for r in lines if r["fair_value"] is not None}
    for g in classes:
        missing = [a for a in PPEAsset.query.filter_by(engagement_id=engagement.id, asset_class_id=g["class_id"]).all()
                   if a.id not in valued_ids and asset_figures(a, ps, pe)["eligible"]]
        g["unvalued"] = len(missing)
        if missing:
            warnings.append(f"{g['class_name']}: {len(missing)} asset(s) in the class have no valuation. The revaluation model applies to a whole class "
                            f"(IAS 16.36) - add them, or explain in the note why they are not revalued.")

    if rec is None or not tot["count"]:
        issues.append("Enter the valuer's fair value for at least one asset.")
    if rec is not None:
        if rec.valuation_date is None:
            issues.append("Enter the date of the valuation.")
        elif pe and rec.valuation_date > pe:
            issues.append("The valuation is dated after the year end - it is an event after the reporting period, not part of these statements.")
        elif pe and rec.valuation_date != pe:
            warnings.append("The valuation is not as at the year end. The app revalues at the year end without splitting the depreciation "
                            "between the valuation date and the year end - confirm the valuer's figures are fair value at the year end.")
        if not (rec.valuer_name or "").strip():
            warnings.append("Name the valuer (IAS 16.77(b) requires the valuer's involvement and independence to be disclosed).")
        if not (rec.report_reference or "").strip():
            warnings.append("Enter the valuation report's reference so the audit file points to the evidence.")
        if not rec.independent:
            warnings.append("The valuer is marked as not independent - say so in the note, and consider whether the valuation is reliable audit evidence.")
        if not rec.policy_confirmed:
            issues.append("Confirm that the revaluation model is adopted for the whole of each class revalued (the accounting policy choice, IAS 16.29 and 36).")
        if not (rec.basis or "").strip():
            warnings.append("State the basis of the valuation (market value, depreciated replacement cost ...).")
    if any(abs(g["change"]) >= 0.005 and g["rate"] == 0 for g in classes):
        warnings.append("The deferred tax rate is nil for a class with a revaluation change - confirm no deferred tax arises on it, or type the rate.")
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    if not tb or not tb.lines or tb.not_applicable:
        issues.append("There is no trial balance with lines for this engagement yet - the journal needs one.")
        jl = []
    else:
        jl = journal_lines(tb, classes)
    return {"lines": lines, "classes": classes, "totals": tot, "journal": jl, "issues": issues, "warnings": warnings, "tb": tb,
            "balanced": abs(sum(x["debit"] for x in jl) - sum(x["credit"] for x in jl)) < 0.005, "period_end": pe, "default_rate": default_rate}


# ---------------------------------------------------------------------------------------------------- the journal

def _acct(tb, cat, default, words=()):
    from deferred_tax_adj import _acct as pick
    return pick(tb, cat, default, words)


def journal_lines(tb, classes):
    """The REVAL journal lines for the revalued classes (aggregated by class), balanced to the cent."""
    out = []

    def add(name, cat, amount_dr):
        """amount_dr positive = debit, negative = credit"""
        amount_dr = r2(amount_dr)
        if abs(amount_dr) < 0.005:
            return
        out.append({"account_name": name, "fs_category": cat, "debit": amount_dr if amount_dr > 0 else 0.0, "credit": -amount_dr if amount_dr < 0 else 0.0})

    oci_sum = pl_sum = dt_oci_sum = dt_pl_sum = pl_inc_sum = pl_dec_sum = 0.0
    for g in classes:
        cost_change = g["fair_value"] - g["cost"]
        add(f"{g['class_name']} - cost (revaluation)", "ppe", cost_change)
        add(f"{g['class_name']} - accumulated depreciation (eliminated on revaluation)", "ppe", g["acc"])
        oci_sum += g["oci"]; pl_sum += g["pl"]; pl_inc_sum += g["pl_inc"]; pl_dec_sum += g["pl_dec"]; dt_oci_sum += g["dt_oci"]; dt_pl_sum += g["dt_pl"]
    # the other side: each figure rounded once, the residual of rounding put on the surplus so the journal always balances
    ppe_net = sum(x["debit"] - x["credit"] for x in out)                       # = the total change, as posted
    dt_oci, dt_pl = r2(dt_oci_sum), r2(dt_pl_sum)
    pl_inc = r2(pl_inc_sum)                      # gains that reverse earlier losses and losses not covered by a reserve, each shown gross
    pl_dec = r2(pl_dec_sum)
    # Cr surplus (oci - dt_oci) etc. Build the credits/debits, then balance on the surplus line
    add(REVERSAL_ACCOUNT, "other_income", -pl_inc)
    add(DEFICIT_ACCOUNT, "other_expenses", pl_dec)
    add(_acct(tb, "income_tax_expense", "Income tax expense", ("deferred",)), "income_tax_expense", dt_pl)
    add(_acct(tb, "deferred_tax_liability", "Deferred tax liability", ("deferred",)), "deferred_tax_liability", -(dt_oci + dt_pl))
    add(DT_OCI_ACCOUNT, "oci_items", dt_oci)
    so_far = sum(x["debit"] - x["credit"] for x in out)
    add(SURPLUS_ACCOUNT, "oci_items", -so_far)                                 # the residual: equals -(gross OCI) to the cent
    return out


def journal_description(engagement, rec, calc):
    ye = engagement.period_end.strftime("%d %B %Y") if engagement.period_end else "the year end"
    vd = rec.valuation_date.strftime("%d %B %Y") if rec.valuation_date else ye
    who = (rec.valuer_name or "the independent valuer").strip()
    ref = f" (report {rec.report_reference.strip()})" if (rec.report_reference or "").strip() else ""
    t = calc["totals"]
    return (f"Revaluation of property, plant and equipment to fair value at {ye} per the valuation by {who}{ref} dated {vd} (IAS 16.31): "
            f"carrying amount {t['nbv']:,.2f}, fair value {t['fair_value']:,.2f}, change {t['change']:,.2f}; accumulated depreciation eliminated against cost "
            f"(IAS 16.35(b)); {t['oci']:,.2f} to other comprehensive income (revaluation reserve) and {t['pl']:,.2f} to profit or loss; deferred tax {t['dt_oci'] + t['dt_pl']:,.2f}. "
            f"Posted from the Asset Register tab.")


def posted_journal(tb):
    import asset_recon
    from models import AuditAdjustment
    if tb is None:
        return None
    return next((a for a in AuditAdjustment.query.filter_by(trial_balance_id=tb.id).all() if asset_recon.adjustment_key(a) == REFERENCE), None)


def in_sync(tb, journal):
    """Whether the journal on the trial balance is what the register and the valuation say now (False when the register or the valuation
    has changed since it was applied)."""
    adj = posted_journal(tb)
    if adj is None:
        return False
    have = sorted((l.account_name, r2(l.debit), r2(l.credit)) for l in adj.lines)
    want = sorted((l["account_name"], l["debit"], l["credit"]) for l in journal)
    return have == want


# ---------------------------------------------------------------------------------------------------- the disclosures

def note_for(engagement_id):
    """The revaluation disclosures for the Property, plant and equipment note (IAS 16.77), or None while there is no applied revaluation:
    {"date", "valuer", "independent", "basis", "method", "report", "classes": [...], "totals": {...}, "reserve": {...}, "paragraphs": [...]}"""
    rec = get(engagement_id)
    if not is_applied(rec):
        return None
    engagement = Engagement.query.get(engagement_id)
    calc = calculate(engagement, rec)
    t = calc["totals"]
    if not t["count"]:
        return None
    vd = rec.valuation_date or engagement.period_end
    date_txt = vd.strftime("%d %B %Y") if vd else "the year end"
    valuer = (rec.valuer_name or "").strip()
    qual = (rec.valuer_qualification or "").strip()
    indep = bool(rec.independent)
    reserve_open = t["prior_reserve_net"]
    surplus_oci = t["oci"]
    dt_oci = t["dt_oci"]
    reserve_close = reserve_open + surplus_oci - dt_oci
    classes = []
    for g in calc["classes"]:
        classes.append({"class_name": g["class_name"], "carrying_before": g["nbv"], "cost_model": g["nbv"] - g["prior_reserve"], "fair_value": g["fair_value"],
                        "change": g["change"], "oci": g["oci"], "pl": g["pl"], "deferred_tax": g["dt_oci"] + g["dt_pl"], "rate": g["rate"]})
    names = [g["class_name"] for g in calc["classes"]]
    who = (f"{valuer}{' (' + qual + ')' if qual else ''}" if valuer else "an independent valuer")
    paras = [
        f"Property, plant and equipment in the {'classes' if len(names) > 1 else 'class'} {_join(names)} was revalued to fair value as at {date_txt} by {who}"
        f"{', who is independent of the company' if indep else ', who is not independent of the company'}"
        f"{' (report ' + rec.report_reference.strip() + ')' if (rec.report_reference or '').strip() else ''}."
    ]
    if (rec.basis or "").strip():
        paras.append(f"The basis of the valuation is {rec.basis.strip().lower() if rec.basis.strip() in BASES else rec.basis.strip()}.")
    if (rec.method_note or "").strip():
        paras.append(rec.method_note.strip())
    paras.append("Accumulated depreciation at the date of revaluation was eliminated against the gross carrying amount of the assets, which was restated to the revalued amount. "
                 "The revaluation surplus is recognised in other comprehensive income, net of deferred tax, and accumulated in the revaluation reserve in equity"
                 f"{'; the part of the increase that reverses a revaluation decrease previously charged to profit or loss is recognised in profit or loss' if t['pl'] > 0.5 else ''}"
                 f"{'; the decrease not covered by the revaluation reserve is charged to profit or loss' if t['pl'] < -0.5 else ''}.")
    return {"date": date_txt, "valuer": valuer, "qualification": qual, "independent": indep, "basis": (rec.basis or "").strip(),
            "method": (rec.method_note or "").strip(), "report": (rec.report_reference or "").strip(), "classes": classes,
            "totals": {"carrying_before": t["nbv"], "cost_model": t["nbv"] - t["prior_reserve"], "fair_value": t["fair_value"], "change": t["change"],
                       "oci": t["oci"], "pl": t["pl"], "deferred_tax": t["dt_oci"] + t["dt_pl"]},
            "reserve": {"opening": reserve_open, "surplus": surplus_oci, "deferred_tax": -dt_oci, "closing": reserve_close},
            "paragraphs": paras}


def _join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def policy_paragraphs(engagement_id, class_names=None):
    """The accounting policy wording for the revalued classes, or [] while there is no applied revaluation."""
    rec = get(engagement_id)
    if not is_applied(rec):
        return []
    names = sorted({class_name_of(l.asset) for l in rec.lines if l.fair_value is not None and l.asset is not None}, key=str.lower)
    if not names:
        return []
    return [f"Property, plant and equipment in the {'classes' if len(names) > 1 else 'class'} {_join(names)} {'are' if len(names) > 1 else 'is'} stated at fair value at the date of revaluation less any subsequent accumulated depreciation "
            "and impairment losses. Revaluations are made with sufficient regularity that the carrying amount does not differ materially from fair value at the end of the reporting period. "
            "A revaluation increase is recognised in other comprehensive income and accumulated in equity as a revaluation surplus, except to the extent that it reverses a revaluation decrease "
            "of the same asset previously recognised in profit or loss; a decrease is recognised in profit or loss except to the extent it offsets an existing surplus on that asset. "
            "Accumulated depreciation at the date of revaluation is eliminated against the gross carrying amount of the asset."]


# ---------------------------------------------------------------------------------------------------- the card

def card_context(engagement):
    """Template variables for the Revaluation card on the Asset Register tab."""
    import asset_schedules as sched
    ps, pe, _ = sched.bounds(engagement)
    rec = get(engagement.id)
    assets = PPEAsset.query.filter_by(engagement_id=engagement.id).order_by(PPEAsset.order, PPEAsset.id).all()
    by_asset = {l.asset_id: l for l in rec.lines} if rec is not None else {}
    calc = calculate(engagement, rec) if rec is not None else None
    default_rate = default_dt_rate(engagement.id)
    class_rows = {}
    rows = []
    for a in assets:
        fig = asset_figures(a, ps, pe)
        l = by_asset.get(a.id)
        cn = class_name_of(a)
        rate = l.dt_rate if (l is not None and l.dt_rate is not None) else default_rate
        c = class_rows.setdefault(cn, {"name": cn, "class_id": a.asset_class_id, "nbv": 0.0, "n": 0, "rate": rate})
        if fig["eligible"]:
            c["nbv"] += fig["nbv"]; c["n"] += 1
        if l is not None and l.dt_rate is not None:
            c["rate"] = l.dt_rate
        rows.append({"asset": a, "line": l, "fig": fig, "class_name": cn})
    calc_by_asset = {r["asset"].id: r for r in calc["lines"]} if calc else {}
    for r in rows:
        r["calc"] = calc_by_asset.get(r["asset"].id)
    tb = calc["tb"] if calc else TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    stale = bool(rec is not None and is_applied(rec) and calc is not None and not in_sync(tb, calc["journal"]))
    for c in class_rows.values():
        if c["rate"] is None:
            c["rate"] = 0.0
    return {"rec": rec, "rows": rows, "classes": sorted(class_rows.values(), key=lambda c: c["name"].lower()), "calc": calc,
            "applied": is_applied(rec), "stale": stale, "bases": BASES, "default_rate": default_rate, "period_end": pe,
            "journal_ref": (posted_journal(tb).reference if tb is not None and posted_journal(tb) is not None else None)}


# ---------------------------------------------------------------------------------------------------- routes

def _engagement(engagement_id):
    import asset_schedules as sched
    return sched._engagement(engagement_id)


def _back(engagement):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="asset_register") + "#revaluation")


def _num(v):
    import asset_schedules as sched
    return sched._num(v)


def _locked(rec, engagement):
    if is_applied(rec):
        flash("The revaluation is applied. Undo it first to change the valuation.", "warning")
        return True
    return False


@ppereval_bp.route("/<int:engagement_id>/revaluation/save", methods=["POST"])
@login_required
def save(engagement_id):
    """Save the valuation's particulars (valuer, date, basis, method) and the valuer's fair values and the deferred tax rate per class."""
    import asset_schedules as sched
    engagement = _engagement(engagement_id)
    rec = get_or_create(engagement)
    if _locked(rec, engagement):
        return _back(engagement)
    f = request.form
    rec.valuation_date = sched._date(f.get("valuation_date")) or rec.valuation_date
    rec.valuer_name = (f.get("valuer_name") or "").strip()[:200] or None
    rec.valuer_qualification = (f.get("valuer_qualification") or "").strip()[:200] or None
    rec.report_reference = (f.get("report_reference") or "").strip()[:120] or None
    rec.report_date = sched._date(f.get("report_date"))
    rec.basis = (f.get("basis") or "").strip()[:80] or None
    rec.independent = f.get("independent") == "1"
    rec.policy_confirmed = f.get("policy_confirmed") == "1"
    rec.method_note = (f.get("method_note") or "").strip() or None
    class_rate = {}
    for k, v in f.items():
        if k.startswith("rate_"):
            n = _num(v)
            class_rate[k[5:]] = n
    existing = {l.asset_id: l for l in rec.lines}
    assets = PPEAsset.query.filter_by(engagement_id=engagement.id).all()
    ps, pe, _ = sched.bounds(engagement)
    order = 0
    for a in assets:
        raw = f.get(f"fv_{a.id}")
        fv = _num(raw)
        l = existing.get(a.id)
        if fv is None:
            if l is not None:
                db.session.delete(l)
            continue
        if l is None:
            l = PPERevaluationLine(revaluation_id=rec.id, asset_id=a.id)
            db.session.add(l)
        l.fair_value = fv
        pr = _num(f.get(f"pr_{a.id}"))
        pd = _num(f.get(f"pd_{a.id}"))
        l.prior_reserve = pr if pr is not None else 0.0
        l.prior_deficit_pl = pd if pd is not None else 0.0
        cname = class_name_of(a)
        key = str(a.asset_class_id)
        rate = class_rate.get(key)
        l.dt_rate = rate
        order += 1
        l.order = order
    db.session.commit()
    flash("Valuation saved. It changes nothing in the statements until you apply it.", "success")
    return _back(engagement)


@ppereval_bp.route("/<int:engagement_id>/revaluation/spread", methods=["POST"])
@login_required
def spread(engagement_id):
    """Spread a class's total fair value over its assets in proportion to their carrying amounts (for a valuer who values the class, not
    each asset)."""
    import asset_schedules as sched
    engagement = _engagement(engagement_id)
    rec = get_or_create(engagement)
    if _locked(rec, engagement):
        return _back(engagement)
    total = _num(request.form.get("class_total"))
    cid = request.form.get("class_id", type=int)
    if total is None or total < 0:
        flash("Enter the valuer's total for the class.", "danger")
        return _back(engagement)
    ps, pe, _ = sched.bounds(engagement)
    assets = [a for a in PPEAsset.query.filter_by(engagement_id=engagement.id, asset_class_id=cid).all() if asset_figures(a, ps, pe)["eligible"]]
    if not assets:
        flash("That class has no asset that can be revalued.", "danger")
        return _back(engagement)
    nbvs = {a.id: max(asset_figures(a, ps, pe)["nbv"], 0.0) for a in assets}
    base = sum(nbvs.values())
    shares = {a.id: (nbvs[a.id] / base if base > 0 else 1.0 / len(assets)) for a in assets}
    values = {a.id: r2(total * shares[a.id]) for a in assets}
    values[assets[-1].id] = r2(values[assets[-1].id] + (total - sum(values.values())))     # the cents of rounding on the last asset
    existing = {l.asset_id: l for l in rec.lines}
    for a in assets:
        l = existing.get(a.id)
        if l is None:
            l = PPERevaluationLine(revaluation_id=rec.id, asset_id=a.id, prior_reserve=0.0, prior_deficit_pl=0.0)
            db.session.add(l)
        l.fair_value = values[a.id]
    db.session.commit()
    flash(f"The class total of {total:,.2f} was spread over {len(assets)} asset(s) in proportion to their carrying amounts. Replace any figure the valuer gave asset by asset.", "success")
    return _back(engagement)


@ppereval_bp.route("/<int:engagement_id>/revaluation/apply", methods=["POST"])
@login_required
def apply(engagement_id):
    import asset_recon
    engagement = _engagement(engagement_id)
    rec = get(engagement.id)
    if rec is None:
        flash("Enter the valuation first.", "danger")
        return _back(engagement)
    calc = calculate(engagement, rec)
    if calc["issues"]:
        flash("Not applied: " + " ".join(calc["issues"]), "danger")
        return _back(engagement)
    if not calc["balanced"] or not calc["journal"]:
        flash("The revaluation journal would not balance, so nothing was posted.", "danger")
        return _back(engagement)
    tb = calc["tb"]
    st = asset_recon._upsert(tb, REFERENCE, calc["journal"], journal_description(engagement, rec, calc), "current")
    rec.status = "Applied"
    rec.applied_at = datetime.utcnow()
    rec.applied_by_id = current_user.id
    db.session.commit()
    flash(f"Revaluation applied: adjustment {asset_recon.journal_label(tb, REFERENCE)} {st}. The Asset Register, the property, plant and equipment note, "
          "the equity statement and the cash flow now carry the revalued amounts. Review and partner sign-off are on the Finalisation tab; "
          "refresh the deferred tax journal on the Deferred Tax tab if it was posted before.", "success")
    return _back(engagement)


@ppereval_bp.route("/<int:engagement_id>/revaluation/undo", methods=["POST"])
@login_required
def undo(engagement_id):
    import asset_recon
    engagement = _engagement(engagement_id)
    rec = get(engagement.id)
    if rec is None or not is_applied(rec):
        flash("The revaluation is not applied.", "info")
        return _back(engagement)
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    if tb is not None:
        asset_recon._upsert(tb, REFERENCE, [], "", "current")
    rec.status = "Draft"
    rec.applied_at = None
    rec.applied_by_id = None
    db.session.commit()
    flash("Revaluation undone: the REVAL journal was removed and the register and notes are back at cost less accumulated depreciation. The valuation is kept as a draft.", "success")
    return _back(engagement)


@ppereval_bp.route("/<int:engagement_id>/revaluation/delete", methods=["POST"])
@login_required
def delete(engagement_id):
    engagement = _engagement(engagement_id)
    rec = get(engagement.id)
    if rec is None:
        return _back(engagement)
    if _locked(rec, engagement):
        return _back(engagement)
    db.session.delete(rec)
    db.session.commit()
    flash("The draft valuation was deleted.", "success")
    return _back(engagement)


# ---------------------------------------------------------------------------------------------------- importing the valuer's list (Excel / CSV)

import os
import re
import uuid
import difflib
from flask import render_template, current_app

MAX_IMPORT_BYTES = 8 * 1024 * 1024

_H_VALUE = re.compile(r"^\W*((fair|market|assessed|insured|replacement|revalued?)\W*)?(value|valuation|amount)s?\b|\bvalue\s*(us\$|usd|zwg|zig|\$)", re.I)
_H_SERIAL = re.compile(r"serial|registration|\breg\b|reg\.|chassis|\bvin\b|plate", re.I)
_H_YEAR = re.compile(r"^\W*year", re.I)
_H_DEPT = re.compile(r"depart|location|\bsite\b|\barea\b|section", re.I)
_H_CAT = re.compile(r"categ|\bclass\b|\btype\b", re.I)
_H_ITEM = re.compile(r"^\W*(item|no\b|code|asset\s*(code|no))", re.I)
_H_DESC = re.compile(r"make|model|descr|asset|name|particular|\bitem\b", re.I)
_TOTALS = re.compile(r"^\W*(grand\s+|sub\s*-?\s*)?totals?\b", re.I)


def _t(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _norm(v):
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


def _header_map(row):
    """{role: column} when the row is a header row (it names a value column and something to identify the asset by), else None."""
    m = {}
    for i, c in enumerate(row):
        s = _t(c)
        if not s or len(s) > 40:
            continue
        for role, rx in (("value", _H_VALUE), ("serial", _H_SERIAL), ("year", _H_YEAR), ("dept", _H_DEPT), ("category", _H_CAT), ("item", _H_ITEM), ("desc", _H_DESC)):
            if role not in m and rx.search(s):
                m[role] = i
                break
    if "value" in m and ("desc" in m or "serial" in m):
        return m
    return None


def parse_valuation(rows):
    """The valuer's list as [{"row", "section", "category", "dept", "item", "description", "serial", "year", "value"}] plus the totals the
    file states for itself. A file may hold several tables (machinery, then vehicles), each with its own header row and title."""
    from asset_import import to_num
    items, stated, notes = [], [], []
    hmap, section, title = None, "", ""
    for ri, row in enumerate(rows):
        row = list(row) + [None] * 12
        new = _header_map(row)
        if new is not None:
            hmap = new
            section = title or section
            continue
        texts = [_t(c) for c in row if _t(c)]
        if hmap is None or not any(to_num(c) is not None for c in row):
            if texts and not any(to_num(c) is not None for c in row):
                title = texts[0]                             # a title line such as "Motor Vehicles Register" ahead of the next table
            if hmap is None:
                continue
        val = to_num(row[hmap["value"]])
        desc = _t(row[hmap["desc"]]) if "desc" in hmap else ""
        serial = _t(row[hmap["serial"]]) if "serial" in hmap else ""
        if val is None:
            if len(texts) == 1 or (texts and not any(to_num(c) is not None for c in row)):
                title = texts[0]                             # a title line such as "Motor Vehicles Register" ahead of the next table
            continue
        if not desc and not serial:
            if val and not _t(row[hmap["item"]] if "item" in hmap else ""):
                stated.append({"row": ri + 1, "section": section, "value": val})
            continue
        if _TOTALS.search(desc):
            stated.append({"row": ri + 1, "section": section, "value": val})
            continue
        if val <= 0:
            continue
        items.append({"row": ri + 1, "idx": len(items), "section": section,
                      "category": _t(row[hmap["category"]]) if "category" in hmap else "", "dept": _t(row[hmap["dept"]]) if "dept" in hmap else "",
                      "item": _t(row[hmap["item"]]) if "item" in hmap else "", "description": desc or serial, "serial": serial,
                      "year": _t(row[hmap["year"]]) if "year" in hmap else "", "value": float(val)})
    return {"items": items, "stated": stated, "notes": notes}


def _tokens(s):
    return {w[:6] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def match_items(engagement, items):
    """Suggest the register asset for each line of the valuer's list. Returns the eligible assets and, per line, "asset_id" and "how"
    (serial / description / similar description / only asset of the class / ""). A register asset is claimed by one line at a time."""
    import asset_schedules as sched
    ps, pe, _ = sched.bounds(engagement)
    assets = [a for a in PPEAsset.query.filter_by(engagement_id=engagement.id).order_by(PPEAsset.order, PPEAsset.id).all() if asset_figures(a, ps, pe)["eligible"]]
    taken = set()
    for it in items:
        it["asset_id"], it["how"] = None, ""

    def claim(it, a, how):
        it["asset_id"], it["how"] = a.id, how
        taken.add(a.id)

    # 1. serial or registration number found in the asset's code or description
    for it in items:
        sn = _norm(it["serial"])
        if len(sn) >= 4:
            for a in assets:
                if a.id not in taken and (sn in _norm(a.asset_code) or sn in _norm(a.description) or _norm(a.asset_code) == sn):
                    claim(it, a, "serial number")
                    break
    # 2. the same description
    for it in items:
        if it["asset_id"] is None:
            d = _norm(it["description"])
            for a in assets:
                if a.id not in taken and d and d == _norm(a.description):
                    claim(it, a, "same description")
                    break
    # 3. a similar description (best, and clearly the best)
    for it in items:
        if it["asset_id"] is None:
            d = _norm(it["description"])
            scored = sorted(((difflib.SequenceMatcher(None, d, _norm(a.description)).ratio(), a) for a in assets if a.id not in taken), key=lambda x: -x[0])
            if scored and d and scored[0][0] >= 0.82 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
                claim(it, scored[0][1], "similar description")
    # 4. the only asset in a class whose name fits the table's title or category (the register holds the class as one asset)
    by_class = {}
    for a in assets:
        by_class.setdefault(a.asset_class_id, []).append(a)
    for it in items:
        if it["asset_id"] is None:
            hint = _tokens(" ".join([it["category"], it["section"]]))
            fits = [lst for cid, lst in by_class.items() if lst and lst[0].asset_class is not None and hint & _tokens(lst[0].asset_class.name)]
            if len(fits) == 1 and len(fits[0]) == 1:
                it["asset_id"], it["how"] = fits[0][0].id, "only asset in the class"   # many lines may share it, so it is not "taken"
    return assets


def _import_redirect(engagement):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="asset_register") + "#revaluation")


def _import_path(engagement_id, token, ext):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        abort(404)
    return os.path.join(current_app.config["UPLOAD_FOLDER"], f"revalimp_{engagement_id}_{token}{ext}")


def _summaries(parsed):
    from collections import OrderedDict
    secs = OrderedDict()
    for it in parsed["items"]:
        secs.setdefault(it["section"] or "(untitled)", []).append(it)
    out = []
    for name, lst in secs.items():
        total = sum(i["value"] for i in lst)
        stated = [s["value"] for s in parsed["stated"] if (s["section"] or "(untitled)") == name]
        out.append({"name": name, "count": len(lst), "total": total, "agrees": any(abs(s - total) < 0.5 for s in stated) if stated else None,
                    "stated": max(stated) if stated else None})
    return out


@ppereval_bp.route("/<int:engagement_id>/revaluation/import/upload", methods=["POST"])
@login_required
def import_upload(engagement_id):
    from asset_import import read_rows
    engagement = _engagement(engagement_id)
    rec = get(engagement.id)
    if _locked(rec, engagement):
        return _back(engagement)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose the valuer's list first.", "danger")
        return _back(engagement)
    data = f.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        flash("That file is too large (8 MB limit).", "danger")
        return _back(engagement)
    ext = os.path.splitext(f.filename)[1].lower()
    try:
        rows = _all_rows(data, f.filename)
    except Exception as e:
        flash(str(e) if isinstance(e, ValueError) else f"The file could not be read: {e}", "danger")
        return _back(engagement)
    token = uuid.uuid4().hex
    with open(_import_path(engagement.id, token, ext), "wb") as out:
        out.write(data)
    return _import_preview(engagement, token, ext, os.path.splitext(f.filename)[0], rows)


def _all_rows(data, filename):
    """Every row of every sheet of the file, one after the other (a valuer may put the vehicles on a second sheet)."""
    import io
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.extend([list(r) for r in ws.iter_rows(values_only=True)][:5000])
        return rows
    from asset_import import read_rows
    return read_rows(data, filename)


def _import_preview(engagement, token, ext, filename, rows):
    parsed = parse_valuation(rows)
    if not parsed["items"]:
        flash("No valuation lines were found. The file needs a header row with a value column (Value, Fair value, Amount ...) and a description or serial number column.", "danger")
        return _import_redirect(engagement)
    assets = match_items(engagement, parsed["items"])
    taken_total = sum(i["value"] for i in parsed["items"] if i["asset_id"])
    return render_template("engagements/ppe_reval_import.html", engagement=engagement, token=token, ext=ext, filename=filename, items=parsed["items"],
                           assets=assets, sections=_summaries(parsed), file_total=sum(i["value"] for i in parsed["items"]), matched_total=taken_total,
                           n_matched=sum(1 for i in parsed["items"] if i["asset_id"]))


@ppereval_bp.route("/<int:engagement_id>/revaluation/import/<token>/confirm", methods=["POST"])
@login_required
def import_confirm(engagement_id, token):
    engagement = _engagement(engagement_id)
    ext, filename = request.form.get("ext", ""), request.form.get("filename", "")
    path = _import_path(engagement.id, token, ext)
    if not os.path.exists(path):
        flash("That upload has expired - please choose the file again.", "danger")
        return _import_redirect(engagement)
    rec = get_or_create(engagement)
    if _locked(rec, engagement):
        return _import_redirect(engagement)
    with open(path, "rb") as fh:
        rows = _all_rows(fh.read(), filename + ext)
    parsed = parse_valuation(rows)
    import asset_schedules as sched
    ps, pe, _ = sched.bounds(engagement)
    eligible = {a.id: a for a in PPEAsset.query.filter_by(engagement_id=engagement.id).all() if asset_figures(a, ps, pe)["eligible"]}
    bulk = request.form.get("bulk_asset", type=int)
    sums, counts, left, left_total = {}, {}, 0, 0.0
    for it in parsed["items"]:
        aid = request.form.get(f"asset_{it['idx']}", type=int)
        if aid is None and bulk:
            aid = bulk
        if aid in eligible:
            sums[aid] = sums.get(aid, 0.0) + it["value"]
            counts[aid] = counts.get(aid, 0) + 1
        else:
            left += 1
            left_total += it["value"]
    if not sums:
        flash("Nothing was assigned to a register asset, so nothing was imported.", "danger")
        return _import_redirect(engagement)
    existing = {l.asset_id: l for l in rec.lines}
    n_new = n_upd = 0
    for aid, total in sums.items():
        l = existing.get(aid)
        if l is None:
            l = PPERevaluationLine(revaluation_id=rec.id, asset_id=aid, prior_reserve=0.0, prior_deficit_pl=0.0)
            db.session.add(l)
            n_new += 1
        else:
            n_upd += 1
        l.fair_value = r2(total)
    db.session.commit()
    try:
        os.remove(path)
    except OSError:
        pass
    msg = (f"Imported the valuer's figures: {len(sums)} register asset(s) now carry a fair value ({n_new} new, {n_upd} replaced) from "
           f"{sum(counts.values())} line(s) totalling {sum(sums.values()):,.2f}.")
    if left:
        msg += f" {left} line(s) totalling {left_total:,.2f} were not assigned to an asset and are not in the revaluation - add them to the register or assign them and import again."
    flash(msg, "warning" if left else "success")
    return _import_redirect(engagement)
