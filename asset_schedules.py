"""Asset Register and Capital Allowance Schedule for an engagement, worked from the year before's closing balances.

The firm's way of working: the closing balances of the year before (31 December 2024 for a period ending 31 December 2025) are entered once
as the OPENING BALANCES - cost, accumulated depreciation and the tax value brought forward for every asset - and then the year's
ADDITIONS and DISPOSALS are entered as they happen. From those the app works:

* the Asset Register schedule (cost and accumulated depreciation, brought forward, additions, disposals, carried forward, by asset class)
  which is also the movement schedule behind the Property, plant and equipment note, and
* the Capital Allowance Schedule (tax value brought forward, additions, disposals, allowances, recoupment / scrapping allowance, tax
  value carried forward, by asset class) which is worked by the one allowance engine in firm_tax.py.

An asset's date acquired decides which list it is on: before the start of the period (or no date) it is an opening balance; inside the
period it is an addition. The tax value brought forward is stored on the asset (PPEAsset.opening_tax_value). Left blank, the tax value is
rebuilt from the cost and the date acquired, as it always was.

Nothing here is a hard-coded tax rule: the allowance rules stay on the Capital Allowances tab, marked "not verified" until a Partner or
Admin has ticked them.
"""
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

import financials as fin
from extensions import db
from models import Engagement, PPEAsset, PPEAssetClass, TrialBalance

assetsched_bp = Blueprint("assetsched", __name__, url_prefix="/engagements")

MAX_PASTE_ROWS = 500
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%y")


# ---------------------------------------------------------------------------------------------------- small helpers

def _engagement(engagement_id):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    if not engagement.has_asset_schedules:
        abort(404)
    return engagement


def _asset(engagement, asset_id):
    asset = PPEAsset.query.get_or_404(asset_id)
    if asset.engagement_id != engagement.id:
        abort(404)
    return asset


def _back(engagement, anchor=""):
    tab = "capital_allowances" if "tab=capital_allowances" in (request.referrer or "") else "asset_register"
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=tab) + (("#" + anchor) if anchor else ""))


def _num(v):
    """float for a typed or pasted figure ('1,234.50', '(500)' is negative, '-' or blank is nothing); None when it is not a number."""
    s = ("" if v is None else str(v)).strip().replace(" ", "").replace(" ", "")
    if s in ("", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def _date(v):
    s = (v or "").strip()
    if not s:
        return None
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def bounds(engagement):
    """(period start, period end, the date the opening balances stand at). All None when the engagement has no period end."""
    pe = engagement.period_end
    if not pe:
        return None, None, None
    ps = fin._infer_period_start(pe)
    return ps, pe, ps - timedelta(days=1)


def split_assets(assets, ps, pe):
    """(opening balances, additions, acquired after the period end). No date, or a date before the period, is an opening balance."""
    openings, additions, later = [], [], []
    for a in assets:
        d = a.date_acquired
        if d and ps and pe and ps <= d <= pe:
            additions.append(a)
        elif d and pe and d > pe:
            later.append(a)
        else:
            openings.append(a)
    return openings, additions, later


def _class_by_name(engagement_id, name, create=True):
    name = " ".join((name or "").split())
    if not name:
        return None
    for c in PPEAssetClass.query.filter_by(engagement_id=engagement_id).all():
        if c.name.strip().lower() == name.lower():
            return c
    if not create:
        return None
    c = PPEAssetClass(engagement_id=engagement_id, name=name[:120], order=PPEAssetClass.query.filter_by(engagement_id=engagement_id).count())
    db.session.add(c)
    db.session.flush()
    return c


def disposal_view(a):
    """One disposed asset: what it cost, the depreciation written off, its carrying amount, the proceeds and the profit or loss."""
    nbv = (a.disposal_cost or 0.0) - (a.disposal_accumulated_depreciation or 0.0)
    proceeds = a.tax_disposal_proceeds
    return {
        "asset": a, "date": a.tax_disposed_on, "cost": a.disposal_cost or 0.0, "acc_dep": a.disposal_accumulated_depreciation or 0.0,
        "nbv": nbv, "proceeds": proceeds, "profit_loss": (proceeds - nbv) if proceeds is not None else None,
    }


# ---------------------------------------------------------------------------------------------------- the tab context

def tab_context(engagement):
    """Template variables for the Asset Register tab (and the opening column of the Capital Allowances tab)."""
    import engagements as eng
    ps, pe, as_at = bounds(engagement)
    classes, assets, class_dicts, asset_dicts = eng._ppe_plain_data(engagement.id)
    openings, additions, later = split_assets(assets, ps, pe)
    disposals = [disposal_view(a) for a in assets if (a.disposal_cost or 0) or a.tax_disposed_on]
    movement = fin.build_ppe_movement_schedule(class_dicts, asset_dicts, pe)

    open_cost = sum(a.cost or 0.0 for a in openings)
    open_acc = sum(a.opening_accumulated_depreciation or 0.0 for a in openings)
    open_tax = sum(a.opening_tax_value or 0.0 for a in openings)
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    tb_prior_ppe = None
    if tb and tb.lines:
        tb_prior_ppe = fin.compute_totals(tb.lines)["ppe"]["prior"]
    open_nbv = open_cost - open_acc
    return {
        "as_period_start": ps, "as_period_end": pe, "as_opening_as_at": as_at,
        "as_openings": openings, "as_additions": additions, "as_later": later, "as_disposals": disposals,
        "as_disposable": [a for a in assets if not (a.disposal_cost or 0) and not a.tax_disposed_on],
        "as_classes": classes, "as_movement": movement,
        "as_open_cost": open_cost, "as_open_acc": open_acc, "as_open_nbv": open_nbv, "as_open_tax": open_tax,
        "as_no_tax_value": [a for a in openings if a.opening_tax_value is None],
        "as_tb_prior_ppe": tb_prior_ppe,
        "as_tb_diff": (open_nbv - tb_prior_ppe) if tb_prior_ppe is not None else None,
        "as_add_cost": sum(a.cost or 0.0 for a in additions),
    }


# ---------------------------------------------------------------------------------------------------- opening balances

def _apply_opening(asset, f):
    """The fields an opening balance carries, from a form. A field that is not in the form is left as it was."""
    if "asset_code" in f:
        asset.asset_code = (f.get("asset_code") or "").strip()[:40] or None
    if "description" in f and (f.get("description") or "").strip():
        asset.description = f.get("description").strip()[:200]
    if "asset_class_id" in f:
        cid = f.get("asset_class_id")
        asset.asset_class_id = int(cid) if cid and str(cid).isdigit() else None
    if "date_acquired" in f:
        asset.date_acquired = _date(f.get("date_acquired"))
    if "cost" in f:
        asset.cost = _num(f.get("cost")) or 0.0
    if "opening_accumulated_depreciation" in f:
        asset.opening_accumulated_depreciation = _num(f.get("opening_accumulated_depreciation")) or 0.0
    if "opening_tax_value" in f:
        asset.opening_tax_value = _num(f.get("opening_tax_value"))      # blank = rebuild from the cost and the date acquired


@assetsched_bp.route("/<int:engagement_id>/assets/opening/add", methods=["POST"])
@login_required
def add_opening(engagement_id):
    engagement = _engagement(engagement_id)
    f = request.form
    if not (f.get("description") or "").strip():
        flash("Enter a description for the asset.", "danger")
        return _back(engagement, "opening")
    ps, pe, as_at = bounds(engagement)
    d = _date(f.get("date_acquired"))
    if d and ps and d >= ps:
        flash(f"An opening balance was acquired before {ps.strftime('%d %b %Y')}. Use 'Additions' for an asset bought in the year.", "danger")
        return _back(engagement, "opening")
    asset = PPEAsset(engagement_id=engagement.id, description=f.get("description").strip()[:200],
                     order=PPEAsset.query.filter_by(engagement_id=engagement.id).count())
    _apply_opening(asset, f)
    db.session.add(asset)
    db.session.commit()
    flash("Opening balance added.", "success")
    return _back(engagement, "opening")


@assetsched_bp.route("/<int:engagement_id>/assets/<int:asset_id>/opening/save", methods=["POST"])
@login_required
def save_opening(engagement_id, asset_id):
    engagement = _engagement(engagement_id)
    asset = _asset(engagement, asset_id)
    ps, _, _ = bounds(engagement)
    d = _date(request.form.get("date_acquired")) if "date_acquired" in request.form else asset.date_acquired
    if d and ps and d >= ps:
        flash(f"That date is inside the period (from {ps.strftime('%d %b %Y')}), so the asset is an addition, not an opening balance. Not saved.", "danger")
        return _back(engagement, "opening")
    _apply_opening(asset, request.form)
    db.session.commit()
    flash("Opening balance saved.", "success")
    return _back(engagement, "opening")


def parse_opening_paste(text):
    """Rows pasted from Excel: Code | Description | Class | Date acquired | Cost | Accumulated depreciation | Tax value (tab or semicolon
    separated; a header row is skipped). Returns (rows, problems)."""
    rows, problems = [], []
    for n, raw in enumerate((text or "").splitlines(), start=1):
        if not raw.strip():
            continue
        cells = [c.strip() for c in (raw.split("\t") if "\t" in raw else re.split(r"\s*;\s*", raw))]
        cells += [""] * (7 - len(cells))
        code, desc, cls, dt, cost, acc, tax = cells[:7]
        if not desc:
            problems.append(f"Line {n}: no description.")
            continue
        c, a, t = _num(cost), _num(acc), _num(tax)
        if not rows and n == 1 and c is None and (cost or "").strip():
            continue                                       # a header row
        if (cost or "").strip() not in ("", "-") and c is None or (acc or "").strip() not in ("", "-") and a is None or (tax or "").strip() not in ("", "-") and t is None:
            problems.append(f"Line {n}: a figure could not be read.")
            continue
        d = _date(dt)
        if (dt or "").strip() and d is None:
            problems.append(f"Line {n}: the date '{dt}' could not be read (use 2023-06-30 or 30/06/2023).")
            continue
        rows.append({"code": code, "description": desc, "class": cls, "date": d, "cost": c or 0.0, "acc": a or 0.0, "tax": t})
        if len(rows) >= MAX_PASTE_ROWS:
            problems.append(f"Only the first {MAX_PASTE_ROWS} rows were read.")
            break
    return rows, problems


@assetsched_bp.route("/<int:engagement_id>/assets/opening/paste", methods=["POST"])
@login_required
def paste_opening(engagement_id):
    engagement = _engagement(engagement_id)
    ps, _, _ = bounds(engagement)
    rows, problems = parse_opening_paste(request.form.get("paste"))
    added = 0
    base = PPEAsset.query.filter_by(engagement_id=engagement.id).count()
    for r in rows:
        if r["date"] and ps and r["date"] >= ps:
            problems.append(f"'{r['description']}' was acquired inside the period - enter it under Additions.")
            continue
        cls = _class_by_name(engagement.id, r["class"])
        db.session.add(PPEAsset(
            engagement_id=engagement.id, asset_class_id=cls.id if cls else None, asset_code=r["code"][:40] or None,
            description=r["description"][:200], date_acquired=r["date"], cost=r["cost"],
            opening_accumulated_depreciation=r["acc"], opening_tax_value=r["tax"], order=base + added))
        added += 1
    db.session.commit()
    if added:
        flash(f"{added} opening balance(s) added." + (f" {len(problems)} line(s) skipped - see below." if problems else ""), "success")
    elif not problems:
        flash("Nothing to add - paste the rows first.", "danger")
    for p in problems[:8]:
        flash(p, "danger")
    return _back(engagement, "opening")


# ---------------------------------------------------------------------------------------------------- additions

@assetsched_bp.route("/<int:engagement_id>/assets/addition/add", methods=["POST"])
@login_required
def add_addition(engagement_id):
    engagement = _engagement(engagement_id)
    f = request.form
    ps, pe, _ = bounds(engagement)
    if not (f.get("description") or "").strip():
        flash("Enter a description for the asset.", "danger")
        return _back(engagement, "additions")
    d = _date(f.get("date_acquired"))
    if d is None:
        flash("Enter the date the asset was acquired.", "danger")
        return _back(engagement, "additions")
    if ps and pe and not (ps <= d <= pe):
        flash(f"An addition is acquired between {ps.strftime('%d %b %Y')} and {pe.strftime('%d %b %Y')}. For an earlier asset use 'Opening balances'.", "danger")
        return _back(engagement, "additions")
    asset = PPEAsset(engagement_id=engagement.id, description=f.get("description").strip()[:200], date_acquired=d,
                     order=PPEAsset.query.filter_by(engagement_id=engagement.id).count())
    asset.asset_code = (f.get("asset_code") or "").strip()[:40] or None
    cid = f.get("asset_class_id")
    asset.asset_class_id = int(cid) if cid and str(cid).isdigit() else None
    asset.cost = _num(f.get("cost")) or 0.0
    asset.current_year_depreciation = _num(f.get("current_year_depreciation")) or 0.0
    db.session.add(asset)
    db.session.commit()
    flash("Addition recorded.", "success")
    return _back(engagement, "additions")


@assetsched_bp.route("/<int:engagement_id>/assets/<int:asset_id>/addition/save", methods=["POST"])
@login_required
def save_addition(engagement_id, asset_id):
    engagement = _engagement(engagement_id)
    asset = _asset(engagement, asset_id)
    f = request.form
    ps, pe, _ = bounds(engagement)
    d = _date(f.get("date_acquired")) if "date_acquired" in f else asset.date_acquired
    if d is None or (ps and pe and not (ps <= d <= pe)):
        flash("An addition needs a date acquired inside the period. Not saved.", "danger")
        return _back(engagement, "additions")
    asset.date_acquired = d
    if (f.get("description") or "").strip():
        asset.description = f.get("description").strip()[:200]
    if "asset_code" in f:
        asset.asset_code = (f.get("asset_code") or "").strip()[:40] or None
    if "asset_class_id" in f:
        cid = f.get("asset_class_id")
        asset.asset_class_id = int(cid) if cid and str(cid).isdigit() else None
    if "cost" in f:
        asset.cost = _num(f.get("cost")) or 0.0
    if "current_year_depreciation" in f:
        asset.current_year_depreciation = _num(f.get("current_year_depreciation")) or 0.0
    db.session.commit()
    flash("Addition saved.", "success")
    return _back(engagement, "additions")


# ---------------------------------------------------------------------------------------------------- disposals

def apply_disposal(asset, on, proceeds, cost=None, acc=None):
    """Record a disposal on the asset. The cost removed is the whole cost and the depreciation removed what had built up to the sale (the
    opening accumulated depreciation, which an addition does not have, plus the year's charge), unless figures are given."""
    ps, _, _ = bounds(asset.engagement)
    is_addition = bool(asset.date_acquired and ps and asset.date_acquired >= ps)
    asset.tax_disposed_on = on
    asset.tax_disposal_proceeds = proceeds if proceeds is not None else 0.0
    asset.disposal_cost = cost if cost is not None else (asset.cost or 0.0)
    built_up = (0.0 if is_addition else (asset.opening_accumulated_depreciation or 0.0)) + (asset.current_year_depreciation or 0.0)
    asset.disposal_accumulated_depreciation = acc if acc is not None else built_up


def clear_disposal(asset):
    asset.tax_disposed_on = None
    asset.tax_disposal_proceeds = None
    asset.disposal_cost = 0.0
    asset.disposal_accumulated_depreciation = 0.0


@assetsched_bp.route("/<int:engagement_id>/assets/<int:asset_id>/dispose", methods=["POST"])
@login_required
def dispose(engagement_id, asset_id):
    """Record that an asset was sold or scrapped: the date, the proceeds, the cost removed and the accumulated depreciation removed with it.
    The cost removed is the asset's whole cost and the depreciation removed is what had built up to the date of sale (the opening
    accumulated depreciation plus the year's charge), unless the figures are typed. The same date and proceeds feed the capital
    allowance schedule (recoupment / scrapping allowance)."""
    engagement = _engagement(engagement_id)
    asset = _asset(engagement, asset_id)
    f = request.form
    ps, pe, _ = bounds(engagement)
    if f.get("clear"):
        clear_disposal(asset)
        db.session.commit()
        flash("Disposal removed.", "success")
        return _back(engagement, "disposals")
    d = _date(f.get("tax_disposed_on"))
    if d is None:
        flash("Enter the date the asset was sold or scrapped.", "danger")
        return _back(engagement, "disposals")
    if ps and pe and not (ps <= d <= pe):
        flash(f"A disposal in this period is dated between {ps.strftime('%d %b %Y')} and {pe.strftime('%d %b %Y')}.", "danger")
        return _back(engagement, "disposals")
    if asset.date_acquired and d < asset.date_acquired:
        flash("The asset cannot be sold before it was acquired.", "danger")
        return _back(engagement, "disposals")
    apply_disposal(asset, d, _num(f.get("tax_disposal_proceeds")), _num(f.get("disposal_cost")), _num(f.get("disposal_accumulated_depreciation")))
    db.session.commit()
    flash("Disposal recorded. The Asset Register and the Capital Allowances schedule both pick it up.", "success")
    return _back(engagement, "disposals")


# ---------------------------------------------------------------------------------------------------- Excel

@assetsched_bp.route("/<int:engagement_id>/assets/schedules/generate", methods=["POST"])
@login_required
def generate_schedules_xlsx(engagement_id):
    import engagements as eng
    import engagement_tax
    import workpapers as wp
    engagement = _engagement(engagement_id)
    ctx = tab_context(engagement)
    ca = engagement_tax.capital_allowance_context(engagement)
    if not (ctx["as_openings"] or ctx["as_additions"] or ctx["as_disposals"]):
        flash("Enter the opening balances first.", "danger")
        return _back(engagement)
    buf = wp.build_asset_schedules_xlsx(engagement, ctx, ca)
    doc = eng._file_generated_workpaper(engagement, "asset_schedules", "Property, Plant and Equipment", "N3300", "Asset_Register_and_Capital_Allowances", "xlsx", buf)
    flash(f"Asset Register and Capital Allowance Schedule filed (v{doc.version}).", "success")
    return _back(engagement)
