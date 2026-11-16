"""Roll a year forward: bring last year's finished figures into this year's engagement.

For a client's engagement (say Broxmen 2026) this takes the SAME client's engagement for the year before (Broxmen 2025) and brings across,
as chosen on the page (Trial Balance tab > "Roll forward from last year's engagement"):

1. COMPARATIVE BALANCES - this year's prior-year column of the trial balance is filled from last year's FINAL trial balance: its current
   column plus every adjustment posted on it (a prior-year journal counts as it does in last year's own statements). Accounts are matched by
   account code, then by name; accounts only in last year's trial balance are added (this year's figures nil), accounts only in this year's
   trial balance get a nil comparative. This year's own (current) column is never touched. The statements, notes, statement of changes in
   equity and tax pages all read the prior column, so the comparatives follow by themselves.
2. OPENING POSITION (journal ROLL-FWD) - last year's audit adjustments live on last year's engagement, not in the client's books. The client's
   trial balance for this year carries the client's own balances, so this year's closing balances miss them. ROLL-FWD repeats them in THIS
   year's opening position: balance sheet lines as they stand (deferred tax, revaluation surplus, property, plant and equipment ...), last
   year's profit and loss effect in retained earnings brought forward and its other comprehensive income in other reserves. Untick it when the
   client has already booked last year's adjustments in this year's trial balance. It is one idempotent journal, refreshed (never stacked)
   on every roll forward, visible and signed off like any other adjustment.
3. PRIOR-YEAR CASH FLOW - last year's finished statement of cash flows becomes this year's comparative (instead of typing or importing it).
4. OPENING ASSET REGISTER - last year's closing register (cost less disposals, accumulated depreciation, the tax value carried forward)
   becomes this year's opening balances. Assets already in this year's register are skipped, so doing it twice never doubles up.

Nothing is written until the preparer confirms on the preview page. The roll forward never edits last year's engagement (apart from the
capital allowance rule rows the Capital Allowances tab itself creates on first use).
"""
from datetime import date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

import financials as fin
from extensions import db
from models import (Engagement, TrialBalance, TrialBalanceLine, AuditAdjustment, PPEAsset, PPEAssetClass, CashFlowPriorLine,
                    StandardChartOfAccounts, user_can_access_engagement)
import asset_recon
import asset_schedules as sched

rollfwd_bp = Blueprint("rollfwd", __name__, url_prefix="/engagements")

REFERENCE = "ROLL-FWD"
TOL = 0.005
RE_NAME = "Retained earnings brought forward (prior year audit adjustments)"
RES_NAME = "Revaluation and other reserves brought forward (prior year)"


def r2(x):
    return round(float(x or 0.0) + 0.0, 2) + 0.0


def _norm(name):
    return fin.normalize_account_name(name)


# ---------------------------------------------------------------------------------------------------- which engagement

def target_start(engagement):
    if not engagement.period_end:
        return None
    return fin._infer_period_start(engagement.period_end, getattr(engagement, "period_start", None))


def candidate_sources(engagement):
    """The same client's other engagements that have a trial balance and finished before this one starts, the year before first:
    [(engagement, exact)] where exact = it ended on the day before this period starts."""
    start = target_start(engagement)
    out = []
    for e in Engagement.query.filter(Engagement.client_id == engagement.client_id, Engagement.id != engagement.id).all():
        if not e.period_end or not user_can_access_engagement(current_user, e):
            continue
        if start and e.period_end >= start:
            continue
        tb = TrialBalance.query.filter_by(engagement_id=e.id).first()
        if not tb or tb.not_applicable or not tb.lines:
            continue
        out.append((e, bool(start and e.period_end == start - timedelta(days=1))))
    out.sort(key=lambda x: (not x[1], -(x[0].period_end.toordinal())))
    return out


# ---------------------------------------------------------------------------------------------------- 1. last year's final trial balance

def current_effect_lines(adj):
    """What one adjustment does to the current column of the engagement it sits on, line by line: [(account name, category code, net debit)].
    A current-year journal: every line. A prior-year journal (restating the year before): only when it carries to the current year, with
    balance sheet lines as they are and everything else as a credit/debit to retained earnings (the same rule as financials.adjustment_effects)."""
    prior = fin.is_prior_adjustment(adj)
    carry = prior and getattr(adj, "carry_to_current", True) is not False
    out = []
    for l in adj.lines:
        cat = fin.CATEGORY_BY_CODE.get(l.fs_category)
        if not cat or cat["normal"] is None:
            continue
        net = (l.debit or 0.0) - (l.credit or 0.0)
        name = (l.account_name or "").strip() or "Adjustment"
        if not prior:
            out.append((name, cat["code"], net))
        elif carry:
            if cat["statement"] == "SFP":
                out.append((name, cat["code"], net))
            else:
                out.append(("Retained earnings brought forward (restated)", "retained_earnings", net))
    return out


def final_accounts(tb):
    """[{code, name, category, coa, sub, net}] - the trial balance's current column with every adjustment on it layered in. An adjustment line
    joins the account of the same name (and category, when it has one); a line with no such account becomes an account of its own."""
    rows, by_name = [], {}
    for l in tb.lines:
        net = (l.current_debit or 0.0) - (l.current_credit or 0.0)
        row = {"code": (l.account_code or "").strip(), "name": l.account_name, "category": l.fs_category, "coa": l.coa_account_number,
               "sub": l.sub_category, "net": net}
        rows.append(row)
        by_name.setdefault(_norm(l.account_name), []).append(row)
    for adj in tb.adjustments:
        for name, cat, net in current_effect_lines(adj):
            hit = None
            for row in by_name.get(_norm(name), []):
                if row["category"] in (cat, None, ""):
                    hit = row
                    break
            if hit is None:
                hit = {"code": "", "name": name, "category": cat, "coa": None, "sub": None, "net": 0.0}
                rows.append(hit)
                by_name.setdefault(_norm(name), []).append(hit)
            hit["net"] += net
            if not hit["category"]:
                hit["category"] = cat
    return [r for r in rows if abs(r["net"]) >= TOL]


def as_accounts(rows):
    """The shape tb_year_import.merge_year reads."""
    return [{"code": r["code"], "name": r["name"], "debit": r2(r["net"]) if r["net"] > 0 else 0.0, "credit": r2(-r["net"]) if r["net"] < 0 else 0.0}
            for r in rows]


def plan_merge(tb, accounts):
    """What merging `accounts` into the prior column of `tb` will do, read-only (the same matching as tb_year_import.merge_year):
    {matched, added: [names], zeroed: [names], debit, credit}."""
    from tb_align import name_key
    by_code, by_name, by_key = {}, {}, {}
    for l in tb.lines:
        if (l.account_code or "").strip():
            by_code.setdefault(l.account_code.strip(), l)
        by_name.setdefault(_norm(l.account_name), l)
        by_key.setdefault(name_key(l.account_name), []).append(l)
    touched, matched, added = set(), 0, []
    for a in accounts:
        line = by_code.get(a["code"]) if a["code"] else None
        if line is None:
            line = by_name.get(_norm(a["name"]))
        if line is None:
            same = [l for l in by_key.get(name_key(a["name"]), []) if l.id not in touched]
            if len(same) == 1 and name_key(a["name"]):
                line = same[0]
        if line is not None and line.id in touched:
            line = None
        if line is None:
            added.append(a["name"])
        else:
            matched += 1
            touched.add(line.id)
    zeroed = [l.account_name for l in tb.lines if l.id not in touched and ((l.prior_debit or 0) or (l.prior_credit or 0))]
    return {"matched": matched, "added": added, "zeroed": zeroed, "debit": sum(a["debit"] for a in accounts), "credit": sum(a["credit"] for a in accounts)}


# ---------------------------------------------------------------------------------------------------- 2. the opening journal

def carry_in_lines(src_tb):
    """The ROLL-FWD journal: last year's adjustments as they stand at the start of this year. Balance sheet lines as they are, profit and loss
    (and dividends) folded into retained earnings brought forward, other comprehensive income into other reserves.
    Returns (lines [{account_name, fs_category, debit, credit}], balanced)."""
    agg = {}
    order = []
    for adj in src_tb.adjustments:
        for name, cat, net in current_effect_lines(adj):
            c = fin.CATEGORY_BY_CODE[cat]
            if cat == "retained_earnings":
                key = (RE_NAME, "retained_earnings")
            elif c["statement"] == "SFP":
                key = (name, cat)
            elif cat == "oci_items":
                key = (RES_NAME, "other_reserves")
            else:
                key = (RE_NAME, "retained_earnings")
            if key not in agg:
                agg[key] = 0.0
                order.append(key)
            agg[key] += net
    lines = []
    for key in order:
        net = agg[key]
        if abs(net) < TOL:
            continue
        lines.append({"account_name": key[0][:200], "fs_category": key[1], "debit": r2(net) if net > 0 else 0.0, "credit": r2(-net) if net < 0 else 0.0})
    total = sum(l["debit"] - l["credit"] for l in lines)
    return lines, abs(total) < 0.01


def adjustment_is_roll(adj):
    return (getattr(adj, "source_key", None) or "").strip().upper() == REFERENCE


# ---------------------------------------------------------------------------------------------------- 3. last year's cash flow

def source_cash_flow(src):
    """Last year's finished statement of cash flows as CashFlowPriorLine rows: [(section, label, amount)], cash at the beginning of that
    year, and whether it was built. Profit before tax is left out (the comparative links to the prior-year income statement)."""
    import engagements as eng
    import cf_prior
    tb = TrialBalance.query.filter_by(engagement_id=src.id).first()
    _, _, cdicts, adicts = eng._ppe_plain_data(src.id)
    movement, policy = eng._ppe_movement_and_policy(cdicts, adicts, src.period_end, getattr(src, "period_start", None))
    st = fin.build_all_statements(tb.lines, tb.adjustments, reporting_framework=src.reporting_framework, cash_flow_method=src.cash_flow_method,
                                  ppe_movement=movement, ppe_policy_text=policy, whole_numbers=False, asset_models=eng._asset_model_overrides(src.id))
    cf = st.get("cf")
    if not cf:
        return [], 0.0, False
    lines = []
    for sec, key in (("operating", "operating_rows"), ("investing", "investing_rows"), ("financing", "financing_rows")):
        for r in cf.get(key) or []:
            if r.get("bold") or abs(r.get("current") or 0.0) < TOL:
                continue
            label = r["label"]
            if r.get("memo"):
                continue            # the depreciation memo line: the statement shows property, plant and equipment net, so nothing is added back
            if cf_prior._key(label) == cf_prior._key(cf_prior.PBT_LABEL):
                continue
            lines.append((sec, label[:200], r2(r["current"])))
    return lines, r2(cf.get("cash_open") or 0.0), True


# ---------------------------------------------------------------------------------------------------- 4. the asset register

def source_assets(src):
    """This year's opening register from last year's closing: [{asset fields..., "class_name", "partial"}], skipped (fully sold) count."""
    import engagement_tax as et
    import firm_tax
    classes, rules = et.ensure_rules(src)
    rule_dicts = {cid: firm_tax.rule_dict(r) for cid, r in rules.items()}
    class_by_id = {c.id: c for c in classes}
    as_at = et.opening_as_at(src)
    out, sold = [], 0
    for a in PPEAsset.query.filter_by(engagement_id=src.id).order_by(PPEAsset.order, PPEAsset.id).all():
        remaining = (a.cost or 0.0) - (a.disposal_cost or 0.0)
        if remaining <= TOL:
            sold += 1
            continue
        acc = max((a.opening_accumulated_depreciation or 0.0) + (a.current_year_depreciation or 0.0) - (a.disposal_accumulated_depreciation or 0.0), 0.0)
        partial = (a.disposal_cost or 0.0) > TOL
        tax_value = None
        if not partial and src.period_end and (a.date_acquired or a.opening_tax_value is not None):
            sim = firm_tax.simulate_asset(et.asset_dict(a, as_at), rule_dicts.get(a.asset_class_id) or firm_tax.rule_dict(None), src.period_end, src.period_end)
            if sim and sim[-1]["fy_end"] == src.period_end:
                tax_value = r2(sim[-1]["closing"])
        cls = class_by_id.get(a.asset_class_id)
        out.append({"asset_code": a.asset_code, "description": a.description, "date_acquired": a.date_acquired, "cost": r2(remaining),
                    "acc": r2(acc), "tax_value": tax_value, "partial": partial, "order": a.order,
                    "class_name": cls.name if cls else None, "class": cls})
    return out, sold


def plan_assets(target, rows):
    have = PPEAsset.query.filter_by(engagement_id=target.id).all()
    codes = {(a.asset_code or "").strip().lower() for a in have if (a.asset_code or "").strip()}
    descs = {((a.description or "").strip().lower(), r2(a.cost)) for a in have}
    new, skipped = [], 0
    for r in rows:
        code = (r["asset_code"] or "").strip().lower()
        if (code and code in codes) or ((r["description"] or "").strip().lower(), r["cost"]) in descs:
            skipped += 1
        else:
            new.append(r)
    return new, skipped


# ---------------------------------------------------------------------------------------------------- the whole plan

def build_plan(target, src):
    """Everything the roll forward would do, read-only, for the preview."""
    plan = {"src": src, "errors": []}
    src_tb = TrialBalance.query.filter_by(engagement_id=src.id).first()
    tgt_tb = TrialBalance.query.filter_by(engagement_id=target.id).first()
    rows = final_accounts(src_tb)
    accounts = as_accounts(rows)
    plan["rows"] = rows
    plan["accounts"] = accounts
    plan["src_debit"] = sum(a["debit"] for a in accounts)
    plan["src_credit"] = sum(a["credit"] for a in accounts)
    plan["src_balanced"] = abs(plan["src_debit"] - plan["src_credit"]) < 0.01
    plan["merge"] = plan_merge(tgt_tb, accounts) if tgt_tb is not None else {"matched": 0, "added": [a["name"] for a in accounts], "zeroed": [],
                                                                            "debit": plan["src_debit"], "credit": plan["src_credit"]}
    plan["src_adjustments"] = len(src_tb.adjustments)
    plan["carry"], plan["carry_balanced"] = carry_in_lines(src_tb)
    plan["carry_existing"] = next((a for a in (tgt_tb.adjustments if tgt_tb is not None else []) if adjustment_is_roll(a)), None)
    try:
        plan["cf"], plan["cf_open"], plan["cf_ok"] = source_cash_flow(src)
    except Exception as e:                                           # a half-built source must not stop the other three
        db.session.rollback()
        plan["cf"], plan["cf_open"], plan["cf_ok"] = [], 0.0, False
        plan["errors"].append(f"Last year's cash flow could not be built ({e}).")
    plan["cf_existing"] = CashFlowPriorLine.query.filter_by(engagement_id=target.id).count()
    try:
        assets, sold = source_assets(src)
    except Exception as e:
        db.session.rollback()
        assets, sold = [], 0
        plan["errors"].append(f"Last year's asset register could not be read ({e}).")
    plan["assets"], plan["assets_skipped"] = plan_assets(target, assets)
    plan["assets_sold"] = sold
    plan["assets_partial"] = sum(1 for a in plan["assets"] if a["partial"])
    plan["assets_have"] = PPEAsset.query.filter_by(engagement_id=target.id).count()
    return plan


# ---------------------------------------------------------------------------------------------------- applying

def apply_comparatives(target, src, plan):
    import engagements as eng
    from tb_year_import import merge_year
    tgt_tb = eng._get_or_create_trial_balance(target.id, source="upload")
    std_sub = {a.account_number: a.sub_category for a in StandardChartOfAccounts.query.all()}
    summary = merge_year(tgt_tb, plan["accounts"], "prior", target.client_id, eng._lookup_coa_mapping, std_sub)
    # accounts added by the merge take last year's category when the client has no saved mapping for them yet
    src_cat = {_norm(r["name"]): r for r in plan["rows"] if r.get("category")}
    db.session.flush()
    for l in TrialBalanceLine.query.filter_by(trial_balance_id=tgt_tb.id).all():
        if not l.fs_category:
            r = src_cat.get(_norm(l.account_name))
            if r:
                l.fs_category, l.coa_account_number, l.sub_category = r["category"], r.get("coa"), r.get("sub")
    eng._touch_trial_balance(tgt_tb)
    return summary


def apply_carry_in(target, plan, want):
    import engagements as eng
    tgt_tb = eng._get_or_create_trial_balance(target.id, source="upload")
    if not want or not plan["carry"] or not plan["carry_balanced"]:
        return asset_recon._upsert(tgt_tb, REFERENCE, [], "")
    desc = (f"Opening position brought forward from {plan['src'].title} (year ended {plan['src'].period_end.strftime('%d %B %Y')}): "
            f"the audit adjustments of that year, which are not in the client's own trial balance. Balance sheet lines as they stand there; "
            f"that year's profit and loss effect in retained earnings brought forward. Posted by Roll forward on the Trial Balance tab; "
            f"remove it if the client has already booked those adjustments.")
    return asset_recon._upsert(tgt_tb, REFERENCE, plan["carry"], desc, "current")


def apply_cash_flow(target, plan):
    CashFlowPriorLine.query.filter_by(engagement_id=target.id).delete()
    n = 0
    db.session.add(CashFlowPriorLine(engagement_id=target.id, section="cash_open", label="Cash at beginning of prior year", amount=plan["cf_open"], sort_order=n))
    n += 1
    for sec, label, amount in plan["cf"]:
        db.session.add(CashFlowPriorLine(engagement_id=target.id, section=sec, label=label, amount=amount, sort_order=n))
        n += 1
    return n - 1


def apply_assets(target, plan):
    added = 0
    base = PPEAsset.query.filter_by(engagement_id=target.id).count()
    for r in plan["assets"]:
        cls = None
        src_cls = r.get("class")
        if src_cls is not None:
            cls = sched._class_by_name(target.id, src_cls.name, create=False)
            if cls is None:
                cls = sched._class_by_name(target.id, src_cls.name, create=True)
                cls.depreciation_method, cls.rate_percent, cls.useful_life_years = src_cls.depreciation_method, src_cls.rate_percent, src_cls.useful_life_years
                cls.tb_aliases, cls.order = src_cls.tb_aliases, src_cls.order
        db.session.add(PPEAsset(engagement_id=target.id, asset_class_id=cls.id if cls else None, asset_code=r["asset_code"], description=r["description"],
                                date_acquired=r["date_acquired"], cost=r["cost"], opening_accumulated_depreciation=r["acc"], current_year_depreciation=0.0,
                                disposal_cost=0.0, disposal_accumulated_depreciation=0.0, opening_tax_value=r["tax_value"], order=base + (r["order"] or 0) + added))
        added += 1
    return added


# ---------------------------------------------------------------------------------------------------- routes

def _target_or_404(engagement_id):
    import engagements as eng
    target = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(target)
    return target


def _source_or_none(target, raw):
    try:
        src = Engagement.query.get(int(raw))
    except (TypeError, ValueError):
        return None
    if src is None or src.id == target.id or src.client_id != target.client_id or not user_can_access_engagement(current_user, src):
        return None
    return src


@rollfwd_bp.route("/<int:engagement_id>/roll-forward")
@login_required
def page(engagement_id):
    target = _target_or_404(engagement_id)
    cands = candidate_sources(target)
    src = _source_or_none(target, request.args.get("source")) or (cands[0][0] if cands else None)
    plan = None
    if src is not None and any(c[0].id == src.id for c in cands):
        plan = build_plan(target, src)
    return render_template("rollforward/page.html", engagement=target, candidates=cands, src=src, plan=plan, start=target_start(target),
                           label=fin.category_label, REFERENCE=REFERENCE, one_day=timedelta(days=1))


@rollfwd_bp.route("/<int:engagement_id>/roll-forward/apply", methods=["POST"])
@login_required
def apply(engagement_id):
    target = _target_or_404(engagement_id)
    back = redirect(url_for("rollfwd.page", engagement_id=target.id, source=request.form.get("source")))
    src = _source_or_none(target, request.form.get("source"))
    if src is None or not any(c[0].id == src.id for c in candidate_sources(target)):
        flash("Choose last year's engagement first.", "danger")
        return back
    do = {k: request.form.get(k) == "1" for k in ("comparatives", "opening", "cashflow", "assets")}
    if request.form.get("confirm") != "1":
        flash("Tick the box to confirm the figures on this page replace what is in this year's engagement.", "danger")
        return back
    if not any(do.values()):
        flash("Tick at least one thing to bring across.", "warning")
        return back
    plan = build_plan(target, src)
    done = []
    if do["comparatives"]:
        if not plan["src_balanced"]:
            flash(f"Last year's final trial balance does not balance (debits {plan['src_debit']:,.2f}, credits {plan['src_credit']:,.2f}), so the comparatives were not changed. "
                  f"Fix last year's trial balance or its adjustments first.", "danger")
        else:
            s = apply_comparatives(target, src, plan)
            done.append(f"comparative balances ({s['matched']} accounts matched, {s['added']} added, {s['zeroed']} set to nil)")
    if do["opening"] or plan["carry_existing"] is not None:
        if do["opening"] and plan["carry"] and not plan["carry_balanced"]:
            flash("Last year's adjustments do not balance, so the opening journal was not posted.", "danger")
        else:
            st = apply_carry_in(target, plan, do["opening"])
            if st:
                done.append(f"opening position journal {REFERENCE} {st}")
    if do["cashflow"]:
        if plan["cf_ok"]:
            n = apply_cash_flow(target, plan)
            done.append(f"prior-year cash flow ({n} lines)")
        else:
            flash("Last year's cash flow could not be built, so the comparative cash flow was not changed.", "warning")
    if do["assets"]:
        n = apply_assets(target, plan)
        done.append(f"opening asset register ({n} assets)")
    db.session.commit()
    flash(f"Rolled forward from {src.title}: " + "; ".join(done) + "." if done else "Nothing was changed.", "success" if done else "info")
    for e in plan["errors"]:
        flash(e, "warning")
    return redirect(url_for("engagements.view_engagement", engagement_id=target.id, tab="trial_balance"))
