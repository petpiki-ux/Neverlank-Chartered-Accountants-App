"""Reconcile the Asset Register to the trial balance, and push the difference to the trial balance as one adjustment.

What is compared (all on the Asset Register tab, "5. Reconcile to the trial balance"):

* OPENING - the register's cost and accumulated depreciation brought forward against the PRIOR-YEAR column of this engagement's trial
  balance (the closing balances of the year before). When the same client has an engagement for the year before, with its own trial
  balance, its closing balances are shown as a third column, so the comparatives can be traced back to last year's signed accounts.
* CLOSING - the register's cost and accumulated depreciation carried forward against the CURRENT-year column of the trial balance, with
  every other adjustment already posted layered on top (the same adjusted trial balance the financial statements are built from).
* DEPRECIATION - the register's charge against the depreciation expense in the current-year column. The prior-year charge and the
  movement in accumulated depreciation implied by the trial balance are shown for information.

PUSH - one idempotent adjustment, reference PPE-REG, on the trial balance's adjustments (Finalisation tab): it brings property, plant
and equipment at cost, accumulated depreciation and the depreciation expense to the register's figures and balances itself with
(a) the difference in the OPENING net book value, which is a prior-period matter - retained earnings by default (the comparative
column is not restated; IAS 8 applies) - and (b) whatever is left over, which is usually the other side of an addition or a disposal
(the bank, a payable, the proceeds): a suspense balance by default, so nothing slips into profit unnoticed. The preparer picks where
(a) and (b) go before pushing and can still edit any line on the Finalisation tab afterwards. Pushing again replaces the lines (it
never stacks a second adjustment) and clears the review and partner sign-off, like every other change to an adjustment.

The client's own trial balance lines are never overwritten: the register reaches the trial balance as an adjustment, so there is an
audit trail and a sign-off.
"""
import re
from datetime import datetime

from flask import Blueprint, redirect, url_for, request, flash
from flask_login import login_required, current_user

import financials as fin
from extensions import db
from models import Engagement, TrialBalance, AuditAdjustment, AuditAdjustmentLine
import asset_schedules as sched

assetrecon_bp = Blueprint("assetrecon", __name__, url_prefix="/engagements")

REFERENCE = "PPE-REG"
REFERENCE_PY = "PPE-REG-PY"          # the prior-year (comparatives) adjustment, when the opening difference is a restatement
TOL = 0.005
OPEN_TO = {"retained_earnings": "Retained earnings of this year (no restatement)",
           "restate_prior": "Restate the comparatives (prior-year adjustment PPE-REG-PY)",
           "profit_loss": "Profit or loss for the year"}
REST_TO = {"suspense": "Suspense (to be cleared before sign-off)", "profit_loss": "Profit or loss for the year"}

_PL_EXPENSE = {"cost_of_sales", "distribution_costs", "admin_expenses", "other_expenses", "depreciation_amortisation"}
_DEPN = re.compile(r"deprec|\bdepn?\b", re.I)
_AMORT = re.compile(r"amorti[sz]", re.I)


def r2(x):
    return round(float(x or 0.0) + 0.0, 2) + 0.0


# ---------------------------------------------------------------------------------------------------- what the trial balance holds

def _is_depreciation_expense(name, category):
    """A depreciation expense line: the depreciation category (not amortisation of intangibles), or an expense account whose name says depreciation."""
    n = name or ""
    if sched._ACC_DEP.search(n):
        return False
    if category == "depreciation_amortisation":
        return not (_AMORT.search(n) and not _DEPN.search(n))
    return category in _PL_EXPENSE and bool(_DEPN.search(n))


def _blank():
    return {"cost": 0.0, "acc": 0.0, "dep": 0.0}


def _add_ppe(bucket, name, net):
    """net = debit - credit on a property, plant and equipment line. The NAME decides first (an account called accumulated depreciation is
    accumulated depreciation even when it carries a debit, and a 'cost' account is cost even when it carries a credit, as a disposal
    adjustment does); an account that says neither is cost when it is a debit and accumulated depreciation when it is a credit."""
    if abs(net) < 1e-9:
        return
    n = name or ""
    if sched._ACC_DEP.search(n):
        bucket["acc"] += -net
    elif sched._COST_WORDS.search(n):
        bucket["cost"] += net
    elif net < 0:
        bucket["acc"] += -net
    else:
        bucket["cost"] += net


def position(tb):
    """What a trial balance and its adjustments say about cost, accumulated depreciation and the depreciation expense.

    Returns buckets of {"cost", "acc", "dep"}:
    prior     - the prior-year column plus every prior-year adjustment except PPE-REG-PY (the comparatives as they stand without it)
    current   - the current-year column of the lines
    other_adj - every adjustment except PPE-REG and PPE-REG-PY: current-year ones, and the part of a prior-year one that restates this
                year's opening balances
    pushed    - the PPE-REG adjustment (current year)
    py_prior / py_current - the PPE-REG-PY adjustment's effect on the comparatives and on this year's opening balances
    adjusted  - what the statements will show for the current year: current + other_adj + pushed + py_current"""
    prior, current, other_adj, pushed, py_prior, py_current = (_blank() for _ in range(6))
    for l in tb.lines:
        cat, name = l.fs_category, l.account_name
        if cat == "ppe":
            _add_ppe(prior, name, (l.prior_debit or 0.0) - (l.prior_credit or 0.0))
            _add_ppe(current, name, (l.current_debit or 0.0) - (l.current_credit or 0.0))
        elif _is_depreciation_expense(name, cat):
            prior["dep"] += (l.prior_debit or 0.0) - (l.prior_credit or 0.0)
            current["dep"] += (l.current_debit or 0.0) - (l.current_credit or 0.0)
    for adj in tb.adjustments:
        ref = (adj.reference or "").strip().upper()
        is_prior, carry = adj.is_prior_year, adj.carries_to_current
        for l in adj.lines:
            net = (l.debit or 0.0) - (l.credit or 0.0)
            ppe = l.fs_category == "ppe"
            dep = (not ppe) and _is_depreciation_expense(l.account_name, l.fs_category)
            if not (ppe or dep):
                continue
            def put(bucket):
                if ppe:
                    _add_ppe(bucket, l.account_name, net)
                else:
                    bucket["dep"] += net
            if ref == REFERENCE_PY:
                put(py_prior)
                if ppe and carry:                       # a balance sheet line is repeated in this year's opening balances
                    put(py_current)
            elif ref == REFERENCE:
                put(pushed)
            elif is_prior:
                put(prior)
                if ppe and carry:
                    put(other_adj)
            else:
                put(other_adj)
    adjusted = {k: current[k] + other_adj[k] + pushed[k] + py_current[k] for k in current}
    return {"prior": prior, "current": current, "other_adj": other_adj, "pushed": pushed, "py_prior": py_prior,
            "py_current": py_current, "adjusted": adjusted}


def _prior_engagement_closing(engagement, as_at):
    """The same client's engagement for the year before (period ended on the opening date), when it has a trial balance:
    (engagement, its closing cost / accumulated depreciation / depreciation after its own adjustments), else (None, None)."""
    if not as_at:
        return None, None
    for e in Engagement.query.filter(Engagement.client_id == engagement.client_id, Engagement.id != engagement.id,
                                     Engagement.period_end == as_at).order_by(Engagement.id.desc()).all():
        tb = TrialBalance.query.filter_by(engagement_id=e.id).first()
        if tb and tb.lines and not tb.not_applicable:
            p = position(tb)
            return e, dict(p["adjusted"])
    return None, None


# ---------------------------------------------------------------------------------------------------- the register

def register_figures(engagement):
    """The register's totals (from the same movement schedule as the PPE note), or None when the register is empty."""
    import engagements as eng
    ps, pe, as_at = sched.bounds(engagement)
    classes, assets, class_dicts, asset_dicts = eng._ppe_plain_data(engagement.id)
    movement = fin.build_ppe_movement_schedule(class_dicts, asset_dicts, pe)
    if not movement:
        return None
    t = movement["total"]
    profit_loss, disposals_with_proceeds = 0.0, 0
    for a in assets:
        if (a.disposal_cost or 0) or a.tax_disposed_on:
            d = sched.disposal_view(a)
            if d["profit_loss"] is not None:
                profit_loss += d["profit_loss"]
                disposals_with_proceeds += 1
    return {
        "open_cost": t["opening_cost"], "open_acc": t["opening_acc_dep"], "open_nbv": t["opening_nbv"],
        "additions": t["additions"], "disposals_cost": t["disposals_cost"], "disposals_acc": t["disposals_acc_dep"],
        "charge": t["charge"], "close_cost": t["closing_cost"], "close_acc": t["closing_acc_dep"], "close_nbv": t["closing_nbv"],
        "profit_loss": profit_loss, "disposals_with_proceeds": disposals_with_proceeds,
        "period_end": pe, "as_at": as_at,
    }


# ---------------------------------------------------------------------------------------------------- the proposed adjustment

def _entry(lines, name, category, amount, note):
    """Append a line to the journal: a positive amount is a debit, a negative one a credit. Amounts under half a cent are dropped."""
    amount = r2(amount)
    if abs(amount) < TOL:
        return
    lines.append({"account_name": name, "fs_category": category, "debit": amount if amount > 0 else 0.0,
                  "credit": -amount if amount < 0 else 0.0, "note": note})


def proposal(reg, pos, open_to="retained_earnings", rest_to="suspense"):
    """The journals that take the trial balance (with every OTHER adjustment) to the register.

    * PPE-REG (current year): the three register accounts - cost, accumulated depreciation and the depreciation charge - brought to the
      register, then two balancing lines.
    * PPE-REG-PY (prior year, only when the opening difference is to be a restatement): cost and accumulated depreciation of the
      COMPARATIVES brought to the register's opening balances against retained earnings; with the usual carry-through it also restates
      this year's opening balances, so PPE-REG then has no opening difference left to explain.

    Returns {"lines", "total_debit", "total_credit", "balanced", "adj_cost", "adj_acc", "adj_dep", "open_diff", "balancing",
    "py_lines", "py_balanced", ...}; a list is empty when there is nothing to post."""
    open_to = open_to if open_to in OPEN_TO else "retained_earnings"
    rest_to = rest_to if rest_to in REST_TO else "suspense"

    # ---- the prior-year restatement of the opening balances
    py_lines, d_cost, d_acc = [], 0.0, 0.0
    open_diff = r2(reg["open_nbv"] - (pos["prior"]["cost"] - pos["prior"]["acc"]))
    if open_to == "restate_prior":
        d_cost = r2(reg["open_cost"] - pos["prior"]["cost"])
        d_acc = r2(reg["open_acc"] - pos["prior"]["acc"])
        _entry(py_lines, "Property, plant and equipment - cost (restated to the Asset Register)", "ppe", d_cost,
               "Cost brought forward per the register less the comparative column")
        _entry(py_lines, "Property, plant and equipment - accumulated depreciation (restated to the Asset Register)", "ppe", -d_acc,
               "Accumulated depreciation brought forward per the register less the comparative column")
        net = r2(sum(l["debit"] - l["credit"] for l in py_lines))
        _entry(py_lines, "Retained earnings - restatement of property, plant and equipment (IAS 8)", "retained_earnings", -net,
               "Opening net book value difference: a prior period error")
    py_debit, py_credit = r2(sum(l["debit"] for l in py_lines)), r2(sum(l["credit"] for l in py_lines))

    # ---- the current-year journal; the restatement (when posted) already repeats in this year's opening balances
    base = {k: pos["current"][k] + pos["other_adj"][k] for k in pos["current"]}
    base["cost"] += d_cost
    base["acc"] += d_acc
    adj_cost = reg["close_cost"] - base["cost"]
    adj_acc = reg["close_acc"] - base["acc"]
    adj_dep = reg["charge"] - base["dep"]
    lines = []
    _entry(lines, "Property, plant and equipment - cost (per Asset Register)", "ppe", adj_cost,
           "Cost carried forward per the register less the cost in the trial balance")
    _entry(lines, "Property, plant and equipment - accumulated depreciation (per Asset Register)", "ppe", -adj_acc,
           "Accumulated depreciation carried forward per the register less the trial balance")
    _entry(lines, "Depreciation charge for the year (per Asset Register)", "depreciation_amortisation", adj_dep,
           "Depreciation charge per the register less the depreciation expense in the trial balance")
    net_debit = r2(sum(l["debit"] - l["credit"] for l in lines))          # what the balancing lines must credit, in total

    # the opening net book value difference (register higher = credit): a prior-period matter. It explains the part of the closing
    # change that has the same direction, never more than the whole of it - when the closing balances already agree nothing is posted.
    remaining_open_diff = 0.0 if open_to == "restate_prior" else open_diff
    if net_debit * remaining_open_diff > 0:
        credit_open = r2(remaining_open_diff if abs(remaining_open_diff) <= abs(net_debit) else net_debit)
    else:
        credit_open = 0.0
    credit_rest = r2(net_debit - credit_open)
    if open_to == "profit_loss":
        o_name = "Opening net book value difference on property, plant and equipment (per Asset Register)"
        o_cat = "other_income" if credit_open > 0 else "other_expenses"
    else:
        o_name, o_cat = "Retained earnings - correction of the opening net book value of property, plant and equipment", "retained_earnings"
    _entry(lines, o_name, o_cat, -credit_open, "Register net book value brought forward less the prior-year column of the trial balance")
    if rest_to == "suspense":
        r_name = "Asset Register reconciling difference - clear before sign-off"
        r_cat = "other_current_liabilities" if credit_rest > 0 else "other_current_assets"
    else:
        r_name = "Profit / (loss) on disposal and other asset register differences"
        r_cat = "other_income" if credit_rest > 0 else "other_expenses"
    _entry(lines, r_name, r_cat, -credit_rest, "The balance of the entry: the other side of additions and disposals (bank, payables, proceeds) - investigate")
    td, tc = r2(sum(l["debit"] for l in lines)), r2(sum(l["credit"] for l in lines))
    return {"lines": lines, "total_debit": td, "total_credit": tc, "balanced": abs(td - tc) < 0.01,
            "py_lines": py_lines, "py_total_debit": py_debit, "py_total_credit": py_credit, "py_balanced": abs(py_debit - py_credit) < 0.01,
            "py_cost": d_cost, "py_acc": d_acc,
            "adj_cost": adj_cost, "adj_acc": adj_acc, "adj_dep": adj_dep, "open_diff": open_diff, "balancing": credit_rest,
            "open_to": open_to, "rest_to": rest_to}


# ---------------------------------------------------------------------------------------------------- the reconciliation

def _row(label, register, tb, extra=None, pushed=None, hint=""):
    """One comparison line: register, trial balance, difference; with `pushed` the amount the PPE-REG adjustment already carries and
    what is still unreconciled after it."""
    diff = register - tb
    row = {"label": label, "register": register, "tb": tb, "diff": diff, "hint": hint, "extra": extra,
           "pushed": pushed, "remaining": (diff - pushed) if pushed is not None else None}
    return row


def reconcile(engagement, open_to="retained_earnings", rest_to="suspense"):
    """Everything the reconciliation card shows. Returns None when it cannot be done (no register, or no trial balance with lines),
    else {"opening", "closing", "depreciation", "info", "proposal", "pushed_adjustment", ...}. {"reason": ...} says what is missing."""
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    reg = register_figures(engagement)
    if reg is None:
        return {"ok": False, "reason": "Enter the opening balances and the year's movements in the register first."}
    if not tb or not tb.lines or tb.not_applicable:
        return {"ok": False, "reason": "There is no trial balance with lines for this engagement yet - import or enter it on the Trial Balance tab first."}
    pos = position(tb)
    prior_eng, prior_close = _prior_engagement_closing(engagement, reg["as_at"])
    tbp, adj = pos["prior"], pos["adjusted"]
    base = {k: pos["current"][k] + pos["other_adj"][k] for k in pos["current"]}
    pushed = {k: pos["pushed"][k] + pos["py_current"][k] for k in pos["pushed"]}      # PPE-REG and the PPE-REG-PY carried into this year
    pyp = pos["py_prior"]

    def with_cont(row, key):
        if prior_close is not None:
            row["cont"] = prior_close[key]
            row["cont_diff"] = prior_close[key] - row["tb"]
        return row

    open_rows = [
        with_cont(_row("Cost brought forward", reg["open_cost"], tbp["cost"], pushed=pyp["cost"]), "cost"),
        with_cont(_row("Accumulated depreciation brought forward", reg["open_acc"], tbp["acc"], pushed=pyp["acc"]), "acc"),
    ]
    open_nbv = _row("Net book value brought forward", reg["open_nbv"], tbp["cost"] - tbp["acc"], pushed=pyp["cost"] - pyp["acc"])
    if prior_close is not None:
        open_nbv["cont"] = prior_close["cost"] - prior_close["acc"]
        open_nbv["cont_diff"] = open_nbv["cont"] - open_nbv["tb"]
    open_rows.append(open_nbv)

    close_rows = [
        _row("Cost carried forward", reg["close_cost"], base["cost"], pushed=pushed["cost"]),
        _row("Accumulated depreciation carried forward", reg["close_acc"], base["acc"], pushed=pushed["acc"]),
    ]
    # the net book value row: pushed net of the cost and accumulated depreciation lines
    close_rows.append(_row("Net book value carried forward", reg["close_nbv"], base["cost"] - base["acc"],
                           pushed=pushed["cost"] - pushed["acc"]))
    dep_row = _row("Depreciation charge for the year", reg["charge"], base["dep"], pushed=pushed["dep"])

    tb_acc_movement = base["acc"] - tbp["acc"]
    reg_acc_movement = reg["charge"] - reg["disposals_acc"]
    info = [
        {"label": "Depreciation charge of the year before (prior-year column of the trial balance)", "value": tbp["dep"]},
        {"label": "Movement in accumulated depreciation per the trial balance (carried forward less brought forward)", "value": tb_acc_movement},
        {"label": "Movement in accumulated depreciation per the register (charge less depreciation on disposals)", "value": reg_acc_movement},
    ]
    if reg["disposals_with_proceeds"]:
        info.append({"label": "Profit / (loss) on disposal per the register (proceeds less carrying amount)", "value": reg["profit_loss"]})

    prop = proposal(reg, pos, open_to, rest_to)
    existing = existing_py = None
    for a in tb.adjustments:
        ref = (a.reference or "").strip().upper()
        if ref == REFERENCE:
            existing = a
        elif ref == REFERENCE_PY:
            existing_py = a
    open_diff = open_nbv["diff"]
    remaining_all = [r["remaining"] for r in close_rows + [dep_row] if r["remaining"] is not None]
    unreconciled = any(abs(x) >= 0.5 for x in remaining_all)
    return {
        "ok": True, "tb": tb, "reg": reg, "opening": open_rows, "closing": close_rows, "depreciation": dep_row, "info": info,
        "open_diff": open_diff, "open_differs": any(abs(r["remaining"]) >= 0.5 for r in open_rows),
        "continuity": prior_eng, "continuity_differs": prior_close is not None and any(abs(r.get("cont_diff", 0)) >= 0.5 for r in open_rows),
        "proposal": prop, "existing": existing, "existing_py": existing_py, "unreconciled": unreconciled,
        "dep_in_other_category": _dep_outside_category(tb),
        "open_to": prop["open_to"], "rest_to": prop["rest_to"], "open_to_options": OPEN_TO, "rest_to_options": REST_TO,
    }


def _dep_outside_category(tb):
    """Depreciation expense accounts found in an expense category other than Depreciation & amortisation (they are counted, but worth saying)."""
    return [l.account_name for l in tb.lines if l.fs_category != "depreciation_amortisation" and _is_depreciation_expense(l.account_name, l.fs_category)]


# ---------------------------------------------------------------------------------------------------- push

def _upsert(tb, reference, lines, description, applies_to="current"):
    """Create or refresh the one adjustment with this reference on the trial balance (never a second one): lines replaced, sign-offs
    cleared. With no lines an existing one is removed. Returns "added" / "refreshed" / "removed" / None."""
    import engagements as eng
    existing = None
    for a in tb.adjustments:
        if (a.reference or "").strip().upper() == reference:
            existing = a
            break
    if not lines:
        if existing is not None:
            db.session.delete(existing)
            return "removed"
        return None
    status = "refreshed"
    if existing is None:
        existing = AuditAdjustment(trial_balance_id=tb.id, reference=reference)
        db.session.add(existing)
        status = "added"
    else:
        for l in list(existing.lines):
            db.session.delete(l)
    existing.description = description
    existing.applies_to = applies_to
    existing.carry_to_current = True
    db.session.flush()
    for l in lines:
        db.session.add(AuditAdjustmentLine(adjustment_id=existing.id, account_name=l["account_name"], fs_category=l["fs_category"],
                                           debit=l["debit"], credit=l["credit"]))
    db.session.flush()
    db.session.refresh(existing)
    eng._touch_adjustment(existing)
    return status


@assetrecon_bp.route("/<int:engagement_id>/assets/reconcile/push", methods=["POST"])
@login_required
def push_to_tb(engagement_id):
    engagement = sched._engagement(engagement_id)
    rc = reconcile(engagement, request.form.get("open_to"), request.form.get("rest_to"))
    if not rc.get("ok"):
        flash(rc.get("reason") or "Nothing to push.", "danger")
        return sched._back(engagement, "reconcile")
    tb, prop = rc["tb"], rc["proposal"]
    if not prop["balanced"] or not prop["py_balanced"]:
        flash("The adjustment would not balance, so nothing was posted.", "danger")
        return sched._back(engagement, "reconcile")
    reg = rc["reg"]
    ye = reg["period_end"].strftime("%d %B %Y") if reg["period_end"] else ""
    done = []
    py_desc = (f"Restatement of the comparatives (IAS 8): property, plant and equipment brought forward at "
               f"{reg['as_at'].strftime('%d %B %Y') if reg['as_at'] else 'the start of the period'} restated to the Asset Register. "
               f"Also restates the opening balances of the year ended {ye}. Pushed from the Asset Register tab.")
    st = _upsert(tb, REFERENCE_PY, prop["py_lines"], py_desc, "prior")
    if st:
        done.append(f"{REFERENCE_PY} {st}")
    desc = (f"Asset Register to trial balance, year ended {ye}: brings property, plant and equipment (cost and accumulated depreciation) "
            f"and the depreciation charge to the register. The opening net book value difference goes to {OPEN_TO[prop['open_to']].lower()}; "
            f"the balance to {REST_TO[prop['rest_to']].lower()}. Pushed from the Asset Register tab.")
    st = _upsert(tb, REFERENCE, prop["lines"], desc, "current")
    if st:
        done.append(f"{REFERENCE} {st}")
    db.session.commit()
    if not done:
        flash("The register already agrees to the trial balance - nothing to push.", "info")
    else:
        n = len(prop["lines"]) + len(prop["py_lines"])
        flash(f"Trial balance adjustments: {', '.join(done)} ({n} lines). New or refreshed adjustments need review and partner sign-off on the Finalisation tab.", "success")
    return sched._back(engagement, "reconcile")
