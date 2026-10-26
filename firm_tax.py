"""Firm Finance > Tax: capital allowances on the firm's own assets, the income tax computation, deferred tax and the
QPD (provisional tax) estimate. The routes are in firm_taxpages.py. This module is the engine; firm_statements.py calls
column_tax() so the tax charge, the tax payable and the deferred tax reach the income statement, balance sheet, cash
flow and notes by themselves.

Nothing here is a hard-coded ZIMRA figure: asset rules, expense disallowances and the tax rate are editable data, and
the starter asset rules are marked "not verified" until the Partner confirms them against the current Act.

Capital allowances (per asset, by fiscal year ending on the anniversary of the year-end being reported):
* Special initial allowance (SIA): sia_percent of the (capped) cost in each of the first sia_years years.
* Wear and tear: wt_rate_percent of cost (straight-line) or of the opening tax value (reducing balance).
* No allowance in the year of disposal. On disposal, proceeds (not more than cost) above the tax value are recouped
  (taxable), proceeds below it give a scrapping allowance (deductible).
* Allowances never exceed the tax value left.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta

import cashbook
import financials
import qpd_calc
from extensions import db
from models import (
    FirmAssetClass, FirmTaxAssetRule, FirmTaxCategoryRule, FirmTaxLine, FirmDeferredItem, FirmYearEnd, QPDInstalmentRate,
)

TAX_CODES = ("income_tax_expense", "current_tax_payable", "deferred_tax_asset", "deferred_tax_liability")
QPD_CATEGORY = "QPD (provisional tax) paid"
FINAL_PAYMENT_CATEGORY = "Income tax paid"
LONG_AGO = date(1900, 1, 1)


def r2(v):
    return round(float(v or 0.0), 2)


# ====================================================================== fiscal years

def fy_end(ref_end, year):
    """The fiscal year end falling in `year`: same month and day as ref_end (a month-end stays a month-end)."""
    last_ref = calendar.monthrange(ref_end.year, ref_end.month)[1]
    last = calendar.monthrange(year, ref_end.month)[1]
    return date(year, ref_end.month, last if ref_end.day == last_ref else min(ref_end.day, last))


def fy_end_on_or_after(day, ref_end):
    e = fy_end(ref_end, day.year)
    return e if e >= day else fy_end(ref_end, day.year + 1)


def fy_start(end, ref_end):
    return fy_end(ref_end, end.year - 1) + timedelta(days=1)


# ====================================================================== asset rules

def default_rule(class_name):
    """Starter treatment by class name; the Partner edits it and marks it verified."""
    n = (class_name or "").lower()
    if any(w in n for w in ("land", "work in progress", "wip")):
        return dict(method="NONE", wt=0.0, note="Land and work in progress: no capital allowance")
    if "building" in n:
        return dict(method="WT_SL", wt=2.5, note="Commercial building: 2.5% of cost a year (Fourth Schedule para 6(1)(a)); industrial buildings and farm improvements are 5% (para 6(1)(b)). Check the building qualifies")
    if any(w in n for w in ("plant", "machinery")):
        return dict(method="SIA", wt=0.0, note="Starter rule: special initial allowance")
    if "vehicle" in n:
        return dict(method="WT_SL", wt=20.0, note="Starter rule; enter the passenger vehicle cost cap if one applies")
    if "computer" in n:
        return dict(method="WT_SL", wt=25.0, note="Starter rule")
    if "furniture" in n:
        return dict(method="WT_SL", wt=10.0, note="Starter rule")
    return dict(method="WT_SL", wt=10.0, note="Starter rule")


def ensure_rules():
    """One rule per asset class (created on first use with the starter treatment)."""
    have = {r.asset_class_id for r in FirmTaxAssetRule.query.all()}
    added = False
    for c in FirmAssetClass.query.all():
        if c.id not in have:
            d = default_rule(c.name)
            db.session.add(FirmTaxAssetRule(asset_class_id=c.id, method=d["method"], wt_rate_percent=d["wt"], note=d["note"]))
            added = True
    if added:
        db.session.commit()
    return {r.asset_class_id: r for r in FirmTaxAssetRule.query.all()}


def rule_dict(r):
    if r is None:
        return dict(method="NONE", wt=0.0, sia=25.0, sia_years=4, sia_first=None, cap=None, prorata=False, verified=False)
    return dict(method=r.method, wt=r.wt_rate_percent or 0.0, sia=r.sia_percent if r.sia_percent is not None else 25.0,
                sia_years=r.sia_years if r.sia_years is not None else 4, sia_first=r.sia_first_percent, cap=(r.cost_cap if r.cost_cap and r.cost_cap > 0 else None),
                prorata=bool(r.prorata_first_year), verified=bool(r.verified))


def category_rules():
    return {r.category: (r.disallowed_percent or 0.0) for r in FirmTaxCategoryRule.query.all()}


# ====================================================================== capital allowances

def simulate_asset(asset, rule, ref_end, target_end):
    """Year-by-year tax value of one asset from its fiscal year of acquisition to the fiscal year ending target_end.
    Each row: fy_end, opening, additions, allowance, recoupment, scrapping, closing."""
    cost = float(asset.get("cost") or 0.0)
    tcost = min(cost, rule["cap"]) if rule.get("cap") else cost
    acq, disp = asset.get("date_acquired"), asset.get("disposed_on")
    disp_fy = fy_end_on_or_after(disp, ref_end) if disp else None
    method = rule["method"]
    rows, tv, k = [], 0.0, 0
    # A tax value brought forward (the closing tax value of an earlier year, entered rather than rebuilt from the purchase) starts the
    # working at the year after it. `opening_as_at` is that earlier year end. The number of allowance years already used (for the
    # special initial allowance) comes from the date acquired; with no date it is taken as all used.
    opening_tv, opening_as_at = asset.get("opening_tax_value"), asset.get("opening_as_at")
    carried = (opening_tv is not None and opening_as_at is not None and (acq is None or acq <= opening_as_at))
    if carried and target_end < opening_as_at:
        carried = False                          # asking about a year before the one entered: rebuild it from the purchase
    if carried:
        tv = float(opening_tv)
        k = 10 ** 6 if acq is None else max((opening_as_at.year - fy_end_on_or_after(acq, ref_end).year) + 1, 1)
        fy = fy_end(ref_end, opening_as_at.year + 1) if target_end > opening_as_at else opening_as_at
        if target_end == opening_as_at:          # the balance itself: nothing worked for this year
            return [{"fy_end": opening_as_at, "opening": tv, "additions": 0.0, "allowance": 0.0, "recoupment": 0.0,
                     "scrapping": 0.0, "closing": tv, "carried": True}]
    elif acq is None:
        return []
    else:
        fy = fy_end_on_or_after(acq, ref_end)
    first_carried = carried
    while fy <= target_end:
        fresh = (k == 0)
        start_tv = tcost if fresh else tv
        row = {"fy_end": fy, "opening": 0.0 if fresh else tv, "additions": tcost if fresh else 0.0,
               "allowance": 0.0, "recoupment": 0.0, "scrapping": 0.0, "closing": 0.0}
        if first_carried:
            row["carried"] = True
        if disp_fy is not None and fy == disp_fy:
            proceeds = float(asset.get("disposal_proceeds") or 0.0)
            row["recoupment"] = max(0.0, min(proceeds, tcost) - start_tv)
            row["scrapping"] = max(0.0, start_tv - proceeds)
            row["closing"] = 0.0
            row["disposed"] = True
            rows.append(row)
            break
        if method == "SIA":
            if k < int(rule.get("sia_years") or 0):
                pct = rule["sia_first"] if (k == 0 and rule.get("sia_first") is not None) else rule["sia"]
                row["allowance"] = min(tcost * pct / 100.0, start_tv)
        elif method in ("WT_SL", "WT_RB"):
            f = 1.0
            if k == 0 and rule.get("prorata"):
                months = (fy.year * 12 + fy.month) - (acq.year * 12 + acq.month) + 1
                f = max(min(months, 12), 0) / 12.0
            base = tcost if method == "WT_SL" else start_tv
            row["allowance"] = min(base * rule["wt"] / 100.0 * f, start_tv)
        row["closing"] = start_tv - row["allowance"]
        rows.append(row)
        tv = row["closing"]
        k += 1
        first_carried = False
        fy = fy_end(ref_end, fy.year + 1)
    return rows


def capital_allowances(assets, classes, rules, target_end, ref_end=None):
    """The schedule for the fiscal year ending target_end. `rules`: {class_id: rule dict}. Returns
    {"rows": [...], "by_class": [...], "total": {...}, "tax_value_end": x}."""
    ref_end = ref_end or target_end
    cls_by_id = {c["id"]: c for c in classes}
    rows = []
    for a in assets:
        rule = rules.get(a.get("asset_class_id")) or rule_dict(None)
        sim = simulate_asset(a, rule, ref_end, target_end)
        if not sim or sim[-1]["fy_end"] != target_end:
            continue
        last = sim[-1]
        cname = (cls_by_id.get(a.get("asset_class_id")) or {}).get("name", "Unclassified")
        rows.append({
            "asset_code": a.get("asset_code"), "description": a.get("description"), "class_name": cname,
            "class_id": a.get("asset_class_id"), "method": rule["method"], "cost": float(a.get("cost") or 0.0),
            "tax_cost": min(float(a.get("cost") or 0.0), rule["cap"]) if rule.get("cap") else float(a.get("cost") or 0.0),
            "date_acquired": a.get("date_acquired"), "disposed_on": a.get("disposed_on"),
            "disposed": bool(last.get("disposed")),
            "proceeds": float(a.get("disposal_proceeds") or 0.0) if last.get("disposed") else 0.0,
            "carried": bool(last.get("carried")), "opening_tax_value": a.get("opening_tax_value"),
            "disposed_tv": (last["opening"] + last["additions"]) if last.get("disposed") else 0.0,
            "opening": last["opening"], "additions": last["additions"], "allowance": last["allowance"],
            "recoupment": last["recoupment"], "scrapping": last["scrapping"], "closing": last["closing"],
            "capped": bool(rule.get("cap")) and float(a.get("cost") or 0.0) > rule["cap"],
        })
    keys = ("opening", "additions", "allowance", "recoupment", "scrapping", "closing", "cost", "tax_cost", "proceeds", "disposed_tv")
    total = {k: 0.0 for k in keys}
    by_class = {}
    for r in rows:
        c = by_class.setdefault(r["class_name"], {k: 0.0 for k in keys})
        for k in keys:
            c[k] += r[k]
            total[k] += r[k]
    return {"rows": rows, "by_class": [dict(v, class_name=n) for n, v in by_class.items()], "total": total,
            "tax_value_end": total["closing"]}


def tax_value_at(assets, classes, rules, day, ref_end):
    """Total tax value of the register at a fiscal year end."""
    return capital_allowances(assets, classes, rules, day, ref_end)["total"]["closing"]


# ====================================================================== the computation

def _lines_for(ye_id, dep, gain, expenses, cap, cat_rules, scale=1.0):
    """Computation lines (financials.build_income_tax_computation format) from the register, the Expense register,
    the allowance schedule and the hand-entered lines. `scale` annualises expenses for an estimate."""
    lines = []
    if abs(dep) >= 0.005:
        lines.append({"item_type": "addback", "description": "Depreciation charged in the accounts", "amount": r2(dep)})
    if gain < -0.004:
        lines.append({"item_type": "addback", "description": "Loss on disposal of assets", "amount": r2(-gain)})
    elif gain > 0.004:
        lines.append({"item_type": "deduction", "description": "Profit on disposal of assets", "amount": r2(gain)})
    for cat, amt in sorted(expenses.items()):
        pct = cat_rules.get(cat, 0.0)
        if pct > 0 and abs(amt) >= 0.005:
            lines.append({"item_type": "addback", "description": f"Not deductible: {cat} ({pct:g}% of {r2(amt * scale):,.2f})", "amount": r2(amt * scale * pct / 100.0)})
    if cap["total"]["recoupment"] > 0.004:
        lines.append({"item_type": "addback", "description": "Recoupment on disposal of assets", "amount": r2(cap["total"]["recoupment"])})
    if cap["total"]["allowance"] > 0.004:
        lines.append({"item_type": "capital_allowance", "description": "Capital allowances (SIA and wear and tear)", "amount": r2(cap["total"]["allowance"])})
    if cap["total"]["scrapping"] > 0.004:
        lines.append({"item_type": "capital_allowance", "description": "Scrapping allowance on disposal of assets", "amount": r2(cap["total"]["scrapping"])})
    for ln in FirmTaxLine.query.filter_by(year_end_id=ye_id).order_by(FirmTaxLine.id).all():
        t = "addback" if ln.item_type == "addback" else "deduction"
        desc = ln.description if ln.item_type != "exempt" else f"Exempt income: {ln.description}"
        lines.append({"item_type": t, "description": desc, "amount": r2(ln.amount), "manual": True, "line_id": ln.id})
    return lines


def _deferred(ye, nbv, tv, rate, items):
    others = [{"description": i.description, "accounting_amount": i.accounting_amount, "tax_base_amount": i.tax_base_amount} for i in items]
    return financials.build_deferred_tax_computation(rate, nbv, tv, others)


def column_tax(ye, start, end, pbt, d, sched, classes, assets, prev_tax, prev_ye=None):
    """The tax for one year-end column. `pbt` is profit before tax as the accounts stand without any tax lines.
    prev_tax is the previous column's result (or None). Returns a dict used by firm_statements and the Tax pages."""
    import firm_statements as fs
    warnings = []
    rules = {cid: rule_dict(r) for cid, r in ensure_rules().items()}
    cap = capital_allowances(assets, classes, rules, end, end)
    cat_rules = category_rules()
    dep = sched["total"]["charge"]
    gain = sched["total"]["gain_loss"]
    lines = _lines_for(ye.id, dep, gain, d["expenses"], cap, cat_rules)
    rate = ye.tax_rate_percent
    if rate is None:
        warnings.append("No income tax rate is entered for this year-end, so the tax charge is nil. Enter it in the year-end settings.")
    loss_bf = ye.tax_loss_bf if ye.tax_loss_bf is not None else ((prev_tax or {}).get("loss_cf", 0.0))
    comp = financials.build_income_tax_computation(pbt, lines, loss_bf, rate, 0.0)
    current_tax = r2(comp["total_tax_charge"])

    qpd_paid, qpd_rows, w1 = cashbook.category_total([QPD_CATEGORY], start, end, ye.zwg_rate)
    settled, settled_rows, w2 = cashbook.category_total([FINAL_PAYMENT_CATEGORY], start, end, ye.zwg_rate)
    warnings += w1 + w2
    payable_bf = (prev_tax or {}).get("payable", 0.0)
    payable = r2(payable_bf + current_tax - qpd_paid - settled)

    # deferred tax: temporary differences at the year-end against those at the previous year-end
    nbv_end = fs.register_schedule(assets, classes, LONG_AGO, end)["total"]["closing_nbv"]
    tv_end = cap["tax_value_end"]
    items = FirmDeferredItem.query.filter_by(year_end_id=ye.id).order_by(FirmDeferredItem.id).all()
    dtc = _deferred(ye, nbv_end, tv_end, rate, items)
    closing = r2(dtc["total_deferred_tax"])
    if prev_tax is not None:
        opening = prev_tax["deferred_closing"]
    elif ye.opening_nil:
        opening = 0.0
    else:
        prior_end = fy_end(end, end.year - 1)
        nbv_open = fs.register_schedule(assets, classes, LONG_AGO, prior_end)["total"]["closing_nbv"]
        tv_open = tax_value_at(assets, classes, rules, prior_end, end)
        opening = r2(((nbv_open - tv_open) * (rate or 0.0)) / 100.0)
    deferred_expense = r2(closing - opening)
    if closing < -0.004:
        warnings.append("The temporary differences give a deferred tax ASSET. It is shown as an asset; check that future taxable profits will be available to use it (IAS 12 / IFRS for SMEs section 29).")
    if comp["tax_loss_carried_forward"] > 0.004:
        warnings.append(f"An assessed loss of USD {comp['tax_loss_carried_forward']:,.2f} is carried forward. No deferred tax asset is recognised on it.")
    return {
        "comp": comp, "lines": lines, "cap": cap, "rules": rules, "pbt": r2(pbt), "dep": r2(dep), "gain": r2(gain),
        "current_tax": current_tax, "loss_bf": loss_bf, "loss_cf": r2(comp["tax_loss_carried_forward"]),
        "qpd_paid": qpd_paid, "qpd_rows": qpd_rows, "settled": settled, "settled_rows": settled_rows,
        "payable_bf": payable_bf, "payable": payable,
        "nbv_end": r2(nbv_end), "tv_end": r2(tv_end), "deferred": dtc, "deferred_items": items,
        "deferred_opening": opening, "deferred_closing": closing, "deferred_expense": deferred_expense,
        "income_tax_expense": r2(current_tax + deferred_expense), "start": start, "end": end, "warnings": warnings,
    }


# ====================================================================== QPD estimate

def qpd_estimate(ye, start, as_at, classes, assets, prev_tax):
    """Provisional tax from the profit so far, annualised. Revenue and expenses come from invoices and the Expense
    register to `as_at`; depreciation, capital allowances and the hand-entered lines are the full year's."""
    import firm_statements as fs
    end = ye.period_end
    as_at = max(min(as_at, end), start)
    d = fs.derive_period(start, as_at, ye.zwg_rate)
    days_el = (as_at - start).days + 1
    days_tot = (end - start).days + 1
    scale = days_tot / float(days_el) if days_el > 0 else 1.0
    sched_full = fs.register_schedule(assets, classes, start, end)
    rules = {cid: rule_dict(r) for cid, r in ensure_rules().items()}
    cap = capital_allowances(assets, classes, rules, end, end)
    revenue_ytd = d["revenue"]
    exp_ytd = sum(d["expenses"].values())
    dep = sched_full["total"]["charge"]
    gain = sched_full["total"]["gain_loss"]
    pbt = (revenue_ytd - exp_ytd) * scale - dep + gain
    lines = _lines_for(ye.id, dep, gain, d["expenses"], cap, category_rules(), scale=scale)
    loss_bf = ye.tax_loss_bf if ye.tax_loss_bf is not None else ((prev_tax or {}).get("loss_cf", 0.0))
    comp = financials.build_income_tax_computation(pbt, lines, loss_bf, ye.tax_rate_percent, 0.0)
    annual_tax = r2(comp["total_tax_charge"])

    rates = QPDInstalmentRate.query.order_by(QPDInstalmentRate.order).all()
    inst = qpd_calc.build_instalments(annual_tax, rates) if rates else []
    year = end.year
    paid_total, paid_rows, warnings = cashbook.category_total([QPD_CATEGORY], date(year, 1, 1), date(year, 12, 31), ye.zwg_rate)
    cum_due = 0.0
    for i in inst:
        try:
            due = date(year, i["due_month"], i["due_day"])
        except ValueError:
            due = date(year, i["due_month"], calendar.monthrange(year, i["due_month"])[1])
        i["due_date"] = due
        cum_due += i["amount"]
        i["cumulative_amount"] = r2(cum_due)
        i["paid_by_then"] = r2(sum(p["usd"] for p in paid_rows if p["date"] <= due))
        i["short"] = r2(i["cumulative_amount"] - i["paid_by_then"])
        i["past"] = due <= date.today()
    if not rates:
        warnings.append("No QPD instalments are set up. They are the firm-wide table the client QPD estimator uses; add them there first.")
    return {"as_at": as_at, "start": start, "end": end, "scale": scale, "revenue_ytd": r2(revenue_ytd), "expenses_ytd": r2(exp_ytd),
            "pbt_annual": r2(pbt), "comp": comp, "lines": lines, "annual_tax": annual_tax, "instalments": inst,
            "paid_total": paid_total, "paid_rows": paid_rows, "warnings": warnings, "depreciation": r2(dep)}
