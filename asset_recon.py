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
REST_TO = {"suspense": "Suspense (to be cleared before sign-off)", "profit_loss": "Profit or loss for the year",
           "retained_earnings": "Retained earnings"}

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


def _classify_ppe(name, net):
    """('cost' | 'acc', signed amount in that field's own direction) for a property, plant and equipment line."""
    n = name or ""
    if sched._ACC_DEP.search(n):
        return "acc", -net
    if sched._COST_WORDS.search(n):
        return "cost", net
    if net < 0:
        return "acc", -net
    return "cost", net


def _add_ppe(bucket, name, net, by=None):
    """net = debit - credit on a property, plant and equipment line. The NAME decides first (an account called accumulated depreciation is
    accumulated depreciation even when it carries a debit, and a 'cost' account is cost even when it carries a credit, as a disposal
    adjustment does); an account that says neither is cost when it is a debit and accumulated depreciation when it is a credit.
    With `by` the same amount is also added to the asset class the account belongs to."""
    if abs(net) < 1e-9:
        return
    field, amt = _classify_ppe(name, net)
    bucket[field] += amt
    if by is not None:
        _slot(by, *class_of(name))[field] += amt


# ---- asset classes: the trial balance names them in its accounts ('Motor vehicles - cost'), the register in its asset classes
_PARENS = re.compile(r"\(\s*(per|restated to)[^)]*asset register\s*\)", re.I)
_DEP_WORDS = re.compile(r"\b(depreciation|depn?|charge|expenses?|for\s+the\s+year|amorti[sz]ation)\b", re.I)


def norm_key(label):
    """A class name reduced so 'Motor Vehicle', 'motor vehicles' and 'Motor vehicles.' are the same: lower case, punctuation and 'and' gone, plural s dropped."""
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", (label or "").lower()).split() if w != "and"]
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words)


def class_of(account_name):
    """(key, label) of the asset class a property, plant and equipment account belongs to."""
    label = sched._tb_class_name(_PARENS.sub(" ", account_name or ""))
    return norm_key(label), label


def dep_class_of(account_name):
    """(key, label) of the class a depreciation EXPENSE account is for ('Depreciation - motor vehicles'); key '' when it names no class."""
    n = _DEP_WORDS.sub(" ", _PARENS.sub(" ", account_name or ""))
    n = " ".join(re.sub(r"[^A-Za-z0-9&]+", " ", n).split())
    return (norm_key(n), n) if n else ("", "")


def _slot(by, key, label):
    g = by.setdefault(key, {"label": label, "cost": 0.0, "acc": 0.0, "dep": 0.0})
    return g


_BUCKETS = ("prior", "current", "other_adj", "pushed", "py_prior", "py_current")


def position(tb):
    """What a trial balance and its adjustments say about cost, accumulated depreciation and the depreciation expense.

    Returns buckets of {"cost", "acc", "dep"}:
    prior     - the prior-year column plus every prior-year adjustment except PPE-REG-PY (the comparatives as they stand without it)
    current   - the current-year column of the lines
    other_adj - every adjustment except PPE-REG and PPE-REG-PY: current-year ones, and the part of a prior-year one that restates
                this year's opening balances
    pushed    - the PPE-REG adjustment (current year)
    py_prior / py_current - the PPE-REG-PY adjustment's effect on the comparatives and on this year's opening balances
    adjusted  - what the statements will show for the current year: current + other_adj + pushed + py_current
    by        - the same buckets analysed by asset class: {bucket: {class key: {"label", "cost", "acc", "dep"}}}; depreciation expense
                accounts that name no class sit under the key ''."""
    b = {n: _blank() for n in _BUCKETS}
    by = {n: {} for n in _BUCKETS}
    for l in tb.lines:
        cat, name = l.fs_category, l.account_name
        if cat == "ppe":
            _add_ppe(b["prior"], name, (l.prior_debit or 0.0) - (l.prior_credit or 0.0), by["prior"])
            _add_ppe(b["current"], name, (l.current_debit or 0.0) - (l.current_credit or 0.0), by["current"])
        elif _is_depreciation_expense(name, cat):
            for bn, net in (("prior", (l.prior_debit or 0.0) - (l.prior_credit or 0.0)), ("current", (l.current_debit or 0.0) - (l.current_credit or 0.0))):
                b[bn]["dep"] += net
                if abs(net) > 1e-9:
                    _slot(by[bn], *dep_class_of(name))["dep"] += net
    for adj in tb.adjustments:
        ref = (adj.reference or "").strip().upper()
        is_prior, carry = adj.is_prior_year, adj.carries_to_current
        for l in adj.lines:
            net = (l.debit or 0.0) - (l.credit or 0.0)
            ppe = l.fs_category == "ppe"
            dep = (not ppe) and _is_depreciation_expense(l.account_name, l.fs_category)
            if not (ppe or dep):
                continue

            def put(bn):
                if ppe:
                    _add_ppe(b[bn], l.account_name, net, by[bn])
                else:
                    b[bn]["dep"] += net
                    if abs(net) > 1e-9:
                        _slot(by[bn], *dep_class_of(l.account_name))["dep"] += net
            if ref == REFERENCE_PY:
                put("py_prior")
                if ppe and carry:                       # a balance sheet line is repeated in this year's opening balances
                    put("py_current")
            elif ref == REFERENCE:
                put("pushed")
            elif is_prior:
                put("prior")
                if ppe and carry:
                    put("other_adj")
            else:
                put("other_adj")
    adjusted = {k: b["current"][k] + b["other_adj"][k] + b["pushed"][k] + b["py_current"][k] for k in b["current"]}
    return {**b, "adjusted": adjusted, "by": by}


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
    by_class = [{"class_id": r["class_id"], "name": r["class_name"], "key": norm_key(r["class_name"]),
                 "open_cost": r["opening_cost"], "open_acc": r["opening_acc_dep"], "close_cost": r["closing_cost"],
                 "close_acc": r["closing_acc_dep"], "charge": r["charge"], "additions": r["additions"],
                 "disposals_cost": r["disposals_cost"]} for r in movement["by_class"]]
    return {
        "by_class": by_class,
        "open_cost": t["opening_cost"], "open_acc": t["opening_acc_dep"], "open_nbv": t["opening_nbv"],
        "additions": t["additions"], "disposals_cost": t["disposals_cost"], "disposals_acc": t["disposals_acc_dep"],
        "charge": t["charge"], "close_cost": t["closing_cost"], "close_acc": t["closing_acc_dep"], "close_nbv": t["closing_nbv"],
        "profit_loss": profit_loss, "disposals_with_proceeds": disposals_with_proceeds,
        "period_end": pe, "as_at": as_at,
    }


# ---------------------------------------------------------------------------------------------------- by asset class

def _aliases(engagement):
    """{class id: set of trial balance class keys the preparer matched to it}."""
    from models import PPEAssetClass
    out = {}
    for c in PPEAssetClass.query.filter_by(engagement_id=engagement.id).all():
        out[c.id] = {k for k in (c.tb_aliases or "").split("|") if k}
    return out


def build_units(reg, pos, aliases):
    """Pair each register asset class with the trial balance accounts of the same class. Returns a list of units:
    {"id", "label", "reg" (the register's class row, or None), "tb_keys", "tb_labels", "kind": 'matched' | 'register_only' | 'tb_only',
     "prior", "current", "other_adj", "pushed", "py_prior", "py_current", "base" (current + other_adj)} - each bucket {"cost","acc","dep"}.

    A trial balance class is matched to a register class by (1) the match the preparer chose, (2) the same name, or (3) one name
    containing the other's words when only one register class fits. Whatever matches nothing is its own 'tb_only' unit, so the totals
    of the units are always the totals of the trial balance and of the register."""
    by = pos["by"]
    tb_labels, tb_has_ppe = {}, {}
    for bn in _BUCKETS:
        for k, g in by[bn].items():
            if not k:
                continue
            tb_labels.setdefault(k, g["label"])
            if abs(g["cost"]) > 1e-9 or abs(g["acc"]) > 1e-9:
                tb_has_ppe[k] = True
    classes = list(reg.get("by_class") or [])
    by_key = {}
    for c in classes:
        by_key.setdefault(c["key"], c)
    alias_of = {}
    for c in classes:
        for k in aliases.get(c["class_id"], ()):
            alias_of[k] = c

    def tokens(k):
        return set(k.split())

    assign = {}                                                  # tb key -> register class row
    for k in tb_has_ppe:
        c = alias_of.get(k) or by_key.get(k)
        if c is None and tokens(k):
            cand = [x for x in classes if tokens(x["key"]) and (tokens(k) <= tokens(x["key"]) or tokens(x["key"]) <= tokens(k))]
            if len(cand) == 1:
                c = cand[0]
        if c is not None:
            assign[k] = c
    # a depreciation expense account named for a class that has no PPE account of its own follows the same matching
    for bn in _BUCKETS:
        for k in by[bn]:
            if k and k not in assign and k not in tb_has_ppe:
                c = alias_of.get(k) or by_key.get(k)
                if c is not None:
                    assign[k] = c

    units = []

    def new_unit(uid, label, regrow, kind):
        u = {"id": uid, "label": label, "reg": regrow, "tb_keys": [], "tb_labels": [], "kind": kind}
        for bn in _BUCKETS:
            u[bn] = _blank()
        units.append(u)
        return u
    by_unit = {}
    for c in classes:
        by_unit[c["key"]] = new_unit(f"c{c['class_id']}" if c["class_id"] is not None else "c0", c["name"], c, "register_only")
    for k in tb_has_ppe:
        c = assign.get(k)
        if c is None:
            u = new_unit("t:" + k, tb_labels[k], None, "tb_only")
        else:
            u = by_unit[c["key"]]
            u["kind"] = "matched"
        u["tb_keys"].append(k)
        u["tb_labels"].append(tb_labels[k])
        by_unit[("tb", k)] = u
    for bn in _BUCKETS:                                          # figures
        for k, g in by[bn].items():
            if not k:
                continue
            u = by_unit.get(("tb", k))
            if u is None:
                c = assign.get(k)
                u = by_unit.get(c["key"]) if c else None
            if u is None:
                continue                                         # depreciation expense named for a class nothing else knows
            for f in ("cost", "acc", "dep"):
                u[bn][f] += g[f]
    for u in units:
        u["base"] = {f: u["current"][f] + u["other_adj"][f] for f in ("cost", "acc", "dep")}
    return units


# ---------------------------------------------------------------------------------------------------- the proposed adjustment

def leg_label(choice, rc):
    if choice in REST_TO:
        return REST_TO[choice]
    for k, v in rc.get("leg_accounts") or ():
        if k == choice:
            return v
    return REST_TO["suspense"]


def legs_from(form):
    """{unit id or 'dep': chosen leg} from the fields leg_<unit id> / leg_dep of a form or query string."""
    return {k[4:]: v for k, v in form.items() if k.startswith("leg_") and v}


def _leg_line(choice, credit, label, accounts):
    """(account name, IAS 1 category, note) for a balancing leg that CREDITS `credit` (a negative amount is a debit).
    choice: 'suspense', 'profit_loss', 'retained_earnings', or the id of a trial balance account (a key of `accounts`)."""
    what = f" - {label}" if label else ""
    if choice in accounts:
        l = accounts[choice]
        return l["name"], l["category"], "The other side chosen by the preparer: the trial balance account that carries the matching entry"
    if choice == "retained_earnings":
        return "Retained earnings - Asset Register difference" + what, "retained_earnings", "The other side chosen by the preparer: retained earnings"
    if choice == "profit_loss":
        return "Profit / (loss) on disposal and other asset register differences", ("other_income" if credit > 0 else "other_expenses"), \
            "The other side chosen by the preparer: profit or loss for the year"
    return ("Asset Register reconciling difference" + what + " - clear before sign-off",
            "other_current_liabilities" if credit > 0 else "other_current_assets",
            "The balance of the entry: the other side of additions and disposals (bank, payables, proceeds) - investigate")


def _unit_note(u):
    if u["kind"] == "tb_only":
        return " - NOT in the Asset Register: the class is written off. If it is the same class under another name, match it to a register class"
    if u["kind"] == "register_only":
        return " - no trial balance account matched this class: the whole register balance is added. If the trial balance holds it under another name, match it"
    return ""


def _entry(lines, name, category, amount, note):
    """Append a line to the journal: a positive amount is a debit, a negative one a credit. Amounts under half a cent are dropped."""
    amount = r2(amount)
    if abs(amount) < TOL:
        return
    lines.append({"account_name": name, "fs_category": category, "debit": amount if amount > 0 else 0.0,
                  "credit": -amount if amount < 0 else 0.0, "note": note})


def proposal(reg, pos, open_to="retained_earnings", rest_to="suspense", units=None, legs=None, accounts=None):
    """The journals that take the trial balance (with every OTHER adjustment) to the register.

    * PPE-REG (current year): the three register accounts - cost, accumulated depreciation and the depreciation charge - brought to the
      register, then two balancing lines.
    * PPE-REG-PY (prior year, only when the opening difference is to be a restatement): cost and accumulated depreciation of the
      COMPARATIVES brought to the register's opening balances against retained earnings; with the usual carry-through it also restates
      this year's opening balances, so PPE-REG then has no opening difference left to explain.

    With `units` (see build_units) cost and accumulated depreciation are brought to the register CLASS BY CLASS - a cost line and an
    accumulated depreciation line for each class that differs - instead of one line for all property, plant and equipment; the
    totals, and the balancing lines, are the same either way. The depreciation charge stays one line (the expense is one category).

    Returns {"lines", "total_debit", "total_credit", "balanced", "adj_cost", "adj_acc", "adj_dep", "open_diff", "balancing",
    "py_lines", "py_balanced", ...}; a list is empty when there is nothing to post."""
    open_to = open_to if open_to in OPEN_TO else "retained_earnings"
    accounts = accounts or {}
    legs = legs or {}
    rest_to = rest_to if (rest_to in REST_TO or rest_to in accounts) else "suspense"

    # ---- the prior-year restatement of the opening balances
    py_lines, d_cost, d_acc = [], 0.0, 0.0
    open_diff = r2(reg["open_nbv"] - (pos["prior"]["cost"] - pos["prior"]["acc"]))
    unit_open = {}                                           # unit id -> (cost, acc) restatement
    if open_to == "restate_prior":
        if units:
            for u in units:
                uc = r2(u["reg"]["open_cost"] - u["prior"]["cost"]) if u["reg"] else r2(-u["prior"]["cost"])
                ua = r2(u["reg"]["open_acc"] - u["prior"]["acc"]) if u["reg"] else r2(-u["prior"]["acc"])
                unit_open[u["id"]] = (uc, ua)
                _entry(py_lines, f"{u['label']} - cost (restated to the Asset Register)", "ppe", uc,
                       "Cost brought forward per the register less the comparative column" + _unit_note(u))
                _entry(py_lines, f"{u['label']} - accumulated depreciation (restated to the Asset Register)", "ppe", -ua,
                       "Accumulated depreciation brought forward per the register less the comparative column" + _unit_note(u))
            d_cost, d_acc = r2(sum(v[0] for v in unit_open.values())), r2(sum(v[1] for v in unit_open.values()))
        else:
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
    unit_net, unit_odiff, unit_acc_up = {}, {}, {}
    if units:
        adj_cost = adj_acc = 0.0
        for u in units:
            oc, oa = unit_open.get(u["id"], (0.0, 0.0))
            rc_, ra_ = (u["reg"]["close_cost"], u["reg"]["close_acc"]) if u["reg"] else (0.0, 0.0)
            uc = rc_ - (u["base"]["cost"] + oc)
            ua = ra_ - (u["base"]["acc"] + oa)
            adj_cost += uc
            adj_acc += ua
            n0 = len(lines)
            _entry(lines, f"{u['label']} - cost (per Asset Register)", "ppe", uc,
                   "Cost carried forward per the register less the cost in the trial balance" + _unit_note(u))
            _entry(lines, f"{u['label']} - accumulated depreciation (per Asset Register)", "ppe", -ua,
                   "Accumulated depreciation carried forward per the register less the trial balance" + _unit_note(u))
            unit_net[u["id"]] = r2(sum(l["debit"] - l["credit"] for l in lines[n0:]))
            unit_acc_up[u["id"]] = r2(ua) if abs(ua) >= TOL else 0.0           # the rise in this class's accumulated depreciation
            ru = u["reg"]
            unit_odiff[u["id"]] = r2((ru["open_cost"] - ru["open_acc"] if ru else 0.0) - (u["prior"]["cost"] - u["prior"]["acc"]))
    else:
        _entry(lines, "Property, plant and equipment - cost (per Asset Register)", "ppe", adj_cost,
               "Cost carried forward per the register less the cost in the trial balance")
        _entry(lines, "Property, plant and equipment - accumulated depreciation (per Asset Register)", "ppe", -adj_acc,
               "Accumulated depreciation carried forward per the register less the trial balance")
    n_dep = len(lines)
    _entry(lines, "Depreciation charge for the year (per Asset Register)", "depreciation_amortisation", adj_dep,
           "Depreciation charge per the register less the depreciation expense in the trial balance")
    dep_net = r2(sum(l["debit"] - l["credit"] for l in lines[n_dep:]))
    net_debit = r2(sum(l["debit"] - l["credit"] for l in lines))          # what the balancing lines must credit, in total

    # the opening net book value difference (register higher = credit): a prior-period matter. It explains the part of the closing
    # change that has the same direction, never more than the whole of it - when the closing balances already agree nothing is posted.
    # Class by class the same rule is applied to each class on its own.
    def limit(odiff, net):
        return (r2(odiff if abs(odiff) <= abs(net) else net)) if net * odiff > 0 else 0.0
    items = []                                                # (label, amount to credit, chosen leg) - the balancing legs
    if units:
        # The depreciation expense is the other side of the rise in accumulated depreciation, so each class takes its share of the
        # expense (up to the whole of it): what is left on a class is then the cost difference, whose other side is the bank, a payable...
        left = dep_net
        for u in units:
            rise = unit_acc_up.get(u["id"], 0.0)
            share = 0.0
            if left * rise > 0:
                share = rise if abs(rise) <= abs(left) else left
            unit_net[u["id"]] = r2(unit_net.get(u["id"], 0.0) + share)
            left = r2(left - share)
        dep_net = left
        # the opening difference of the whole register, as in total: first against the class it belongs to, then against what is left
        credit_open = limit(0.0 if open_to == "restate_prior" else open_diff, net_debit)
        todo = credit_open
        entries = [[u["label"], unit_net.get(u["id"], 0.0), legs.get(u["id"]) or rest_to, unit_odiff.get(u["id"], 0.0)] for u in units]
        if abs(dep_net) >= TOL:
            entries.append(["Depreciation charge", dep_net, legs.get("dep") or rest_to, 0.0])
        for e in entries:                                     # pass 1: the class's own opening difference
            co = limit(e[3], e[1])
            co = r2(co if abs(co) <= abs(todo) and co * todo > 0 else (todo if co * todo > 0 else 0.0))
            e[1] = r2(e[1] - co)
            todo = r2(todo - co)
        for e in sorted(entries, key=lambda x: x[0] != "Depreciation charge"):   # pass 2: what is left, the depreciation residual first
            if abs(todo) < TOL:
                break
            if e[1] * todo > 0:
                co = r2(todo if abs(todo) <= abs(e[1]) else e[1])
                e[1] = r2(e[1] - co)
                todo = r2(todo - co)
        credit_open = r2(credit_open - todo)
        items = [(e[0], e[1], e[2]) for e in entries if abs(e[1]) >= TOL]
    else:
        remaining_open_diff = 0.0 if open_to == "restate_prior" else open_diff
        credit_open = limit(remaining_open_diff, net_debit)
        if abs(net_debit - credit_open) >= TOL:
            items.append((None, r2(net_debit - credit_open), rest_to))
    # the pennies lost in rounding go to the largest balancing item so the journal always balances to the cent
    drift = r2(net_debit - credit_open - sum(i[1] for i in items))
    if items and abs(drift) >= TOL:
        k = max(range(len(items)), key=lambda j: abs(items[j][1]))
        items[k] = (items[k][0], r2(items[k][1] + drift), items[k][2])
    credit_rest = r2(sum(i[1] for i in items))
    if open_to == "profit_loss":
        o_name = "Opening net book value difference on property, plant and equipment (per Asset Register)"
        o_cat = "other_income" if credit_open > 0 else "other_expenses"
    else:
        o_name, o_cat = "Retained earnings - correction of the opening net book value of property, plant and equipment", "retained_earnings"
    _entry(lines, o_name, o_cat, -credit_open, "Register net book value brought forward less the prior-year column of the trial balance")
    merged = {}
    for label, amt, choice in items:
        name, cat, note = _leg_line(choice, amt, label, accounts)
        key = (name, cat)
        if key in merged:
            merged[key]["amt"] += amt
            merged[key]["labels"].append(label)
        else:
            merged[key] = {"name": name, "cat": cat, "note": note, "amt": amt, "labels": [label]}
    for m in merged.values():
        labs = [x for x in m["labels"] if x]
        suffix = f" Covers: {', '.join(labs)}." if labs and not any(x in m["name"] for x in labs) else ""
        _entry(lines, m["name"], m["cat"], -r2(m["amt"]), m["note"] + suffix)
    td, tc = r2(sum(l["debit"] for l in lines)), r2(sum(l["credit"] for l in lines))
    return {"lines": lines, "total_debit": td, "total_credit": tc, "balanced": abs(td - tc) < 0.01,
            "py_lines": py_lines, "py_total_debit": py_debit, "py_total_credit": py_credit, "py_balanced": abs(py_debit - py_credit) < 0.01,
            "py_cost": d_cost, "py_acc": d_acc,
            "adj_cost": adj_cost, "adj_acc": adj_acc, "adj_dep": adj_dep, "open_diff": open_diff, "balancing": credit_rest,
            "open_to": open_to, "rest_to": rest_to, "legs": {k: v for k, v in legs.items() if v},
            "leg_items": [{"label": i[0], "amount": i[1], "choice": i[2]} for i in items]}


# ---------------------------------------------------------------------------------------------------- the reconciliation

def _row(label, register, tb, extra=None, pushed=None, hint=""):
    """One comparison line: register, trial balance, difference; with `pushed` the amount the PPE-REG adjustment already carries and
    what is still unreconciled after it."""
    diff = register - tb
    row = {"label": label, "register": register, "tb": tb, "diff": diff, "hint": hint, "extra": extra,
           "pushed": pushed, "remaining": (diff - pushed) if pushed is not None else None}
    return row


def reconcile(engagement, open_to="retained_earnings", rest_to="suspense", basis="class", legs=None):
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

    # ---- by asset class
    units = build_units(reg, pos, _aliases(engagement))
    use_units = basis != "total" and any(u["kind"] == "matched" for u in units)
    unit_rows = []
    for u in units:
        r = u["reg"] or {"open_cost": 0.0, "open_acc": 0.0, "close_cost": 0.0, "close_acc": 0.0, "charge": 0.0}
        pu = u["py_prior"]
        pn = {k: u["pushed"][k] + u["py_current"][k] for k in ("cost", "acc", "dep")}
        unit_rows.append({
            "unit": u,
            "open_cost": _row("Cost b/f", r["open_cost"], u["prior"]["cost"], pushed=pu["cost"]),
            "open_acc": _row("Accumulated depreciation b/f", r["open_acc"], u["prior"]["acc"], pushed=pu["acc"]),
            "close_cost": _row("Cost c/f", r["close_cost"], u["base"]["cost"], pushed=pn["cost"]),
            "close_acc": _row("Accumulated depreciation c/f", r["close_acc"], u["base"]["acc"], pushed=pn["acc"]),
            "charge": _row("Depreciation charge", r["charge"], u["base"]["dep"]),
        })
    unalloc_dep = base["dep"] - sum(u["base"]["dep"] for u in units)
    class_attention = [u for u in units if u["kind"] != "matched"]
    accounts = {f"acct:{l.id}": {"name": l.account_name, "category": l.fs_category} for l in tb.lines
                if l.fs_category and l.fs_category != "ppe" and not _is_depreciation_expense(l.account_name, l.fs_category)}
    legs = {k: v for k, v in (legs or {}).items() if v in REST_TO or v in accounts}
    prop = proposal(reg, pos, open_to, rest_to, units if use_units else None, legs, accounts)
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
        "units": unit_rows, "unalloc_dep": unalloc_dep, "class_attention": class_attention, "basis": "class" if use_units else "total",
        "basis_requested": "total" if basis == "total" else "class", "register_classes": reg.get("by_class") or [], "leg_accounts": [(k, f"{v['name']} ({v['category']})") for k, v in accounts.items()],
        "legs": legs,
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
    rc = reconcile(engagement, request.form.get("open_to"), request.form.get("rest_to"), request.form.get("basis") or "class", legs_from(request.form))
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
    desc = (f"Asset Register to trial balance, year ended {ye}: brings property, plant and equipment (cost and accumulated depreciation, "
            f"{'class by class' if rc['basis'] == 'class' else 'in total'}) "
            f"and the depreciation charge to the register. The opening net book value difference goes to {OPEN_TO[prop['open_to']].lower()}; "
            f"the balance to {leg_label(prop['rest_to'], rc).lower()}{' (with the legs chosen for individual classes)' if prop['legs'] else ''}. Pushed from the Asset Register tab.")
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


@assetrecon_bp.route("/<int:engagement_id>/assets/reconcile/match-class", methods=["POST"])
@login_required
def match_class(engagement_id):
    """Match a trial balance asset class (the name its accounts share) to a register asset class - or remove the match."""
    from models import PPEAssetClass
    engagement = sched._engagement(engagement_id)
    key = (request.form.get("tb_key") or "").strip()
    cid = request.form.get("class_id") or ""
    if not key:
        return sched._back(engagement, "reconcile")
    classes = PPEAssetClass.query.filter_by(engagement_id=engagement.id).all()
    for c in classes:
        ks = [k for k in (c.tb_aliases or "").split("|") if k and k != key]
        if cid.isdigit() and c.id == int(cid):
            ks.append(key)
        c.tb_aliases = "|".join(ks) or None
    db.session.commit()
    flash("Asset class match saved." if cid.isdigit() else "Asset class match removed.", "success")
    return sched._back(engagement, "reconcile")


def preview_for_template(engagement):
    """The journals the Finalisation tab offers beside the PPE note's tie-out: comparatives restated to the register's opening balances
    (PPE-REG-PY) and this year's balances brought to the register (PPE-REG), the balance in suspense. None when it cannot be worked."""
    try:
        if not engagement.has_asset_schedules:
            return None
        rc = reconcile(engagement, "restate_prior", "suspense")
        return rc if rc.get("ok") else None
    except Exception:                                        # never let the preview break the Finalisation page
        return None
