"""Management Accounts - monthly reporting packs inside Corporate Finance & Business Advisory.

A PACK sets actual performance against the budget, the latest forecast and the prior year, in the firm's layout:
LAST MONTH | CURRENT MONTH (actual, forecast, budget, last year) | FULL YEAR (forecast, budget, last year), for the income
statement, balance sheet and cash flow, with KPIs, variance analysis, commentary and a full-year outlook.

Where the figures come from (all held in one JSON document on the pack - see cf_ma.py):
  * actuals   - a trial balance upload, manual month capture, or MODELLED months built from the prior year's accounts
                (for example "9 months = 9/12 of last year grown 5%", so a funding model can be tested before books exist)
  * budget / forecast - snapshots of a forecast model, typed annual figures phased over the year, or a typed monthly grid
  * prior year - typed, from a trial balance, or loaded from existing financial statements (PDF / Word / Excel / CSV)

The statements loader is shared with the forecast models: existing financial statements are read (AI when an API key is set,
otherwise a built-in keyword reader), shown for checking, and then feed either a pack's prior year or a model's history.

This file only holds the web layer; the arithmetic is in cf_ma.py and the statements reader is in cf_fsload.py.
It is imported at the bottom of corporate_finance.py so its routes join the same blueprint (same permission gate).
"""
import copy
import hashlib
import json
import os
import tempfile
from datetime import date, datetime

from flask import render_template, redirect, url_for, request, flash, abort, Response, current_app
from flask_login import current_user

import cf_engine as E
import cf_ma as M
import cf_fsload as FS
import cf_recon as RC
import cf_ma_var as VM
import cf_ma_link as LK
import cf_tools as T
from extensions import db
from financials import suggest_fs_category
from invoicing import FIRM_LETTERHEAD, next_sequence_number
from models import (Client, Engagement, CFModel, CFScenario, CFMgmtPack, COAMapping, Document, user_can_access_client,
                    user_can_access_engagement, visible_clients)
from corporate_finance import (corpfin_bp, _guard, _can_see, _is_signoff_role, _engagement_choices, _valid_engagement,
                               _commit_with_number, _touch, _advisory_engagement_or_404, _acceptance_open, _run, _next_model_number, _base_scenario,
                               _pick_scenario, _safe_name, CURRENCIES, XLSX_MIME)

PACK_TABS = [
    ("summary", "Summary"), ("income", "Income statement"), ("balance", "Balance sheet"), ("cashflow", "Cash flow"),
    ("kpis", "KPIs"), ("variances", "Variances"), ("varmodel", "Variance model"), ("reforecast", "Reforecast"), ("commentary", "Commentary"), ("actuals", "Actuals"),
    ("budget", "Budget & forecast"), ("prior", "Prior year"), ("setup", "Setup"),
]
PLAN_NAMES = {"budget": "Budget", "forecast": "Forecast"}
MAX_UPLOAD = 15 * 1024 * 1024
LOADER_EXT = ("pdf", "docx", "xlsx", "xlsm", "csv", "txt")
OUTLOOKS = [("auto", "Automatic (forecast, else budget, else run-rate)"), ("forecast", "Latest forecast for the remaining months"),
            ("budget_remaining", "Budget for the remaining months"), ("run_rate", "Run-rate (year to date annualised)")]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


@corpfin_bp.context_processor
def _ma_ctx():
    return {"PACK_TABS": PACK_TABS, "PLAN_NAMES": PLAN_NAMES}


# =========================================================================== helpers

def _pack_or_404(pack_id):
    return _guard(CFMgmtPack.query.get_or_404(pack_id))


def _plocked(pack):
    if pack.is_final:
        flash("This pack is Final and locked. A Partner can reopen it to make changes.", "danger")
        return True
    return False


def _next_pack_number():
    return next_sequence_number(CFMgmtPack.pack_number, f"MA-{date.today().year}-")


def _data(pack):
    """The pack's document with every section present and the structure/settings normalised."""
    d = pack.data or {}
    d["structure"] = M.normalise_structure(d.get("structure"))
    d["settings"] = M.settings_of(d)
    for k in ("entries", "commentary", "line_notes", "mapping"):
        if not isinstance(d.get(k), dict):
            d[k] = {}
    return d


def _view_data(pack):
    """Data for display: ?month=YYYY-MM temporarily changes the reporting month (nothing is saved)."""
    d = _data(pack)
    m = request.args.get("month")
    if m and m in M.fy_months(d):
        d["settings"]["report_month"] = m
    return d


def _save(pack, d):
    pack.set_data(d)
    _touch(pack)
    db.session.commit()


def _stamp():
    return {"by": current_user.name, "at": datetime.utcnow().strftime("%Y-%m-%d %H:%M")}


def _back(pack, tab):
    return redirect(url_for("corpfin.pack_tab", pack_id=pack.id, tab=tab))


def _month_or_404(d, month):
    if month not in M.fy_months(d):
        abort(404)
    return month


def _meta(pack, d, cur_month=None):
    return {"name": pack.name, "client": pack.client.name, "number": pack.pack_number, "status": pack.status, "currency": pack.currency,
            "prepared_by": (pack.updated_by.name if pack.updated_by else (pack.created_by.name if pack.created_by else "")),
            "fy_label": M.fy_label(d), "prior_label": M.prior_fy_label(d), "month": M.report_month(d)}


def _chart_payload(d):
    structure = M.normalise_structure(d["structure"])
    months = M.fy_months(d)
    mon = M.monthly_actuals(d)
    bud, fc = M.budget_months(d), M.forecast_months(d)
    ser, bser, fser = M.bs_series(d), M.budget_bs(d), M.forecast_bs(d)
    vec = lambda vals: M.pl_vector(structure, vals) if vals is not None else None

    def pick(src, key):
        out = []
        for m in months:
            v = vec(src(m))
            out.append(round(v[key], 2) if v else None)
        return out
    act = lambda m: next((x["values"] for x in mon if x["month"] == m), None)
    cash = lambda s: [round(M.f(s[m]["cash"]) - M.f(s[m]["overdraft"]), 2) if m in s else None for m in months]
    return {"months": [M.month_label(m) for m in months],
            "rev_a": pick(act, "revenue"), "rev_b": pick(lambda m: bud.get(m), "revenue"), "rev_f": pick(lambda m: fc.get(m), "revenue"),
            "ebitda_a": pick(act, "ebitda"), "ebitda_b": pick(lambda m: bud.get(m), "ebitda"), "ebitda_f": pick(lambda m: fc.get(m), "ebitda"),
            "cash_a": cash(ser), "cash_b": cash(bser), "cash_f": cash(fser)}


# =========================================================================== list & create

@corpfin_bp.route("/engagements/<int:engagement_id>/management-accounts")
def engagement_packs(engagement_id):
    eng = _advisory_engagement_or_404(engagement_id)
    packs = [p for p in eng.cf_packs if _can_see(p)]
    return render_template("corpfin/engagement_packs.html", engagement=eng, packs=packs, unlocked=_acceptance_open(eng))


@corpfin_bp.route("/management-accounts/new", methods=["GET", "POST"])
def new_pack():
    clients = visible_clients(current_user)
    engagements = _engagement_choices(clients)
    ids = [c.id for c in clients]
    models = [m for m in (CFModel.query.filter(CFModel.client_id.in_(ids)).order_by(CFModel.id.desc()).all() if ids else []) if _can_see(m)]
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        try:
            client = Client.query.get(int(form.get("client_id")))
        except (TypeError, ValueError):
            client = None
        name = (form.get("name") or "").strip()
        if client is None or not user_can_access_client(current_user, client):
            flash("Choose the client this pack is for.", "danger")
        elif not name:
            flash("Give the pack a name.", "danger")
        else:
            eng = _valid_engagement(client.id, form.get("engagement_id"))
            try:
                fy_month = min(12, max(1, int(form.get("fy_start_month") or 1)))
                fy_year = int(form.get("fy_start_year") or date.today().year)
            except ValueError:
                fy_month, fy_year = 1, date.today().year
            d = {"structure": M.default_structure(), "settings": {"fy_start_month": fy_month, "fy_start_year": fy_year},
                 "entries": {}, "commentary": {}, "line_notes": {}, "mapping": {}}
            currency = (form.get("currency") or "USD").strip().upper()[:6] or "USD"
            notes = []
            if form.get("basis") == "model":
                try:
                    model = CFModel.query.get(int(form.get("model_id")))
                except (TypeError, ValueError):
                    model = None
                if model is None or model.client_id != client.id or not _can_see(model):
                    flash("Choose one of this client's forecast models, or start from a simple layout.", "danger")
                    return render_template("corpfin/ma_new.html", clients=clients, engagements=engagements, models=models, form=form,
                                           currencies=CURRENCIES, months=MONTH_NAMES, default_year=date.today().year)
                result = _run(model, _pick_scenario(model, form.get("scenario")))
                a = result["a"]
                d["structure"] = M.structure_from_model(a)
                currency = a["currency"]
                try:
                    sy, sm = M.parse_month(str(a["start_month"])[:7])
                    d["settings"]["fy_start_year"], d["settings"]["fy_start_month"] = sy, sm
                except ValueError:
                    pass
                plan, notes = M.budget_from_model(d["structure"], result, a, M.fy_months(d))
                d["budget"] = dict(plan, source="model", model_id=model.id, model_number=model.model_number, model_name=model.name,
                                   saved=_stamp())
            pack = _commit_with_number(lambda: CFMgmtPack(
                pack_number=_next_pack_number(), client_id=client.id, engagement_id=eng.id if eng else None, name=name[:200],
                currency=currency, status="Draft", created_by_id=current_user.id, updated_by_id=current_user.id))
            pack.set_data(d)
            db.session.commit()
            flash(f"Pack {pack.pack_number} created." + (" The budget was taken from the forecast model." if d.get("budget") else
                  " Next: load the prior year's accounts, then capture or upload the actuals."), "success")
            for n in notes:
                flash(n, "warning")
            return _back(pack, "actuals" if d.get("budget") else "prior")
    return render_template("corpfin/ma_new.html", clients=clients, engagements=engagements, models=models, form=form, currencies=CURRENCIES,
                           months=MONTH_NAMES, default_year=date.today().year)


@corpfin_bp.route("/management-accounts/<int:pack_id>")
def pack_home(pack_id):
    return _back(_pack_or_404(pack_id), "summary")


# =========================================================================== the tabs

@corpfin_bp.route("/management-accounts/<int:pack_id>/<tab>")
def pack_tab(pack_id, tab):
    pack = _pack_or_404(pack_id)
    if tab not in dict(PACK_TABS):
        abort(404)
    d = _view_data(pack)
    cur = pack.currency
    ridx = M.report_index(d)
    labels = {"month": M.month_label(M.report_month(d), False), "fy": M.fy_label(d), "prior": M.prior_fy_label(d),
              "last_month": M.month_label(M.fy_months(d)[ridx - 1], False) if ridx > 0 else "n/a", "n": ridx + 1}
    ctx = dict(pack=pack, d=d, tab=tab, labels=labels, cur=cur, engagement=pack.engagement, structure=d["structure"], settings=d["settings"],
               months=M.fy_months(d), report_month=M.report_month(d), fy=M.fy_label(d), prior_label=M.prior_fy_label(d),
               month_label=M.month_label, warnings=M.warnings(d), status=M.data_status(d), has_prior=bool((d.get("prior") or {}).get("pl")),
               engagements=_engagement_choices([pack.client]) if tab == "setup" else [], link_status=LK.link_status(_data(pack)))
    if tab == "summary":
        ctx.update(V=M.vectors(d), msgs=M.key_messages(d, ""), kpis=M.kpi_table(d), charts=_chart_payload(d))
    elif tab == "income":
        ctx.update(inc=M.income_rows(d), view="ytd" if request.args.get("view") == "ytd" else "month", var=request.args.get("var") != "0")
    elif tab == "balance":
        ctx.update(bal=M.balance_rows(d), var=request.args.get("var") != "0")
    elif tab == "cashflow":
        ctx.update(cfl=M.cashflow_rows(d), view="ytd" if request.args.get("view") == "ytd" else "month", var=request.args.get("var") != "0")
    elif tab == "kpis":
        ctx.update(kpis=M.kpi_table(d), V=M.vectors(d))
    elif tab == "variances":
        vl = M.variance_list(d)
        vl.sort(key=lambda x: -abs(x["ytd_var"] or 0))
        ctx.update(vl=vl, V=M.vectors(d))
    elif tab == "varmodel":
        ctx.update(_varmodel_ctx(d))
    elif tab == "reforecast":
        ctx.update(_reforecast_ctx(pack, d))
    elif tab == "commentary":
        ctx.update(msgs=M.key_messages(d, ""), com=d["commentary"], notes=d["line_notes"], line_defs=M.pl_line_defs(d["structure"]))
    elif tab == "actuals":
        ctx.update(prior_ok=bool((d.get("prior") or {}).get("pl") and (d.get("prior") or {}).get("bs")), months_full=MONTH_NAMES)
    elif tab == "budget":
        ctx.update(plans={n: d.get(n) or {} for n in PLAN_NAMES}, line_defs=M.pl_line_defs(d["structure"]),
                   plan_pl={n: M.budget_months(d, n) for n in PLAN_NAMES}, models=_pack_models(pack))
        ctx.update(_continuation_ctx(pack, d))
    elif tab == "prior":
        pb = M.prior_bs(d)
        ctx.update(prior=d.get("prior") or {}, line_defs=M.pl_line_defs(d["structure"]), bs_lines=M.BS_LINES, **loader_context(pack.client_id, pack.engagement_id),
                   fs_target=f"pack:{pack.id}", prior_gap=M.bs_total(pb)["check"] if pb else None)
    elif tab == "setup":
        ctx.update(currencies=CURRENCIES, outlooks=OUTLOOKS, months_full=MONTH_NAMES)
    return render_template(f"corpfin/ma_{tab}.html", **ctx)


def _pack_models(pack):
    return [m for m in CFModel.query.filter_by(client_id=pack.client_id).order_by(CFModel.id.desc()).all() if _can_see(m)]


# =========================================================================== setup, status

@corpfin_bp.route("/management-accounts/<int:pack_id>/setup", methods=["POST"])
def pack_setup(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "setup")
    d = _data(pack)
    form = request.form
    name = (form.get("name") or "").strip()
    if not name:
        flash("The pack needs a name.", "danger")
        return _back(pack, "setup")
    pack.name = name[:200]
    pack.currency = (form.get("currency") or pack.currency).strip().upper()[:6] or pack.currency
    eng = _valid_engagement(pack.client_id, form.get("engagement_id"))
    pack.engagement_id = eng.id if eng else None
    s = d["settings"]
    try:
        s["fy_start_month"] = min(12, max(1, int(form.get("fy_start_month") or s["fy_start_month"])))
        s["fy_start_year"] = int(form.get("fy_start_year") or s["fy_start_year"])
    except ValueError:
        pass
    rm = (form.get("report_month") or "").strip()
    s["report_month"] = rm if rm in M.fy_months({"settings": s}) else None
    if form.get("outlook") in dict(OUTLOOKS):
        s["outlook"] = form.get("outlook")
    s["rag_amber"] = max(0.0, M.f(form.get("rag_amber"), s["rag_amber"]))
    s["rag_red"] = max(s["rag_amber"], M.f(form.get("rag_red"), s["rag_red"]))
    # lines: posted as parallel lists; a blank label removes a line (its figures move to the first line of that section)
    old = d["structure"]
    new = {"revenue": [], "opex": []}
    for sec, pre in (("revenue", "rev"), ("opex", "opx")):
        keys, labels = form.getlist(f"{pre}_key"), form.getlist(f"{pre}_label")
        for k, lab in zip(keys, labels):
            if lab.strip():
                new[sec].append({"key": k.strip(), "label": lab.strip()})
    new = M.normalise_structure(new)
    kept = {r["key"] for sec in ("revenue", "opex") for r in new[sec]}
    removed = {}
    for sec in ("revenue", "opex"):
        for r in old[sec]:
            if r["key"] not in kept:
                removed[r["key"]] = new[sec][0]["key"]
    if removed:
        _merge_removed(d, removed)
    d["structure"] = new
    _save(pack, d)
    flash("Setup saved." + (f" {len(removed)} line(s) removed; their figures were added to the first line of the same section." if removed else ""), "success")
    return _back(pack, "setup")


def _merge_removed(d, removed):
    def fold(pl):
        if not isinstance(pl, dict):
            return
        for old, new in removed.items():
            if old in pl:
                pl[new] = M.f(pl.get(new)) + M.f(pl.pop(old))
    for e in (d.get("entries") or {}).values():
        fold(e.get("pl"))
    fold((d.get("prior") or {}).get("pl"))
    for name in PLAN_NAMES:
        for v in ((d.get(name) or {}).get("pl") or {}).values():
            fold(v)
    for n, t in list((d.get("mapping") or {}).items()):
        if t in removed:
            d["mapping"][n] = removed[t]
    for k in list((d.get("line_notes") or {}).keys()):
        if k in removed:
            d["line_notes"].pop(k, None)


@corpfin_bp.route("/management-accounts/<int:pack_id>/report-month", methods=["POST"])
def pack_report_month(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "summary")
    d = _data(pack)
    rm = request.form.get("report_month")
    d["settings"]["report_month"] = rm if rm in M.fy_months(d) else None
    _save(pack, d)
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/corporate-finance/") else url_for("corpfin.pack_tab", pack_id=pack.id, tab="summary"))


@corpfin_bp.route("/management-accounts/<int:pack_id>/finalise", methods=["POST"])
def pack_finalise(pack_id):
    pack = _pack_or_404(pack_id)
    if not _is_signoff_role():
        abort(403, description="Only a Partner or Admin can finalise a pack.")
    d = _data(pack)
    if M.vectors(d)["actual_ytd"] is None:
        flash("There are no actual figures for the reporting month, so the pack cannot be finalised.", "danger")
    else:
        pack.status = "Final"
        pack.finalised_by_id = current_user.id
        pack.finalised_at = datetime.utcnow()
        _touch(pack)
        db.session.commit()
        flash("Pack finalised and locked.", "success")
    return _back(pack, "summary")


@corpfin_bp.route("/management-accounts/<int:pack_id>/reopen", methods=["POST"])
def pack_reopen(pack_id):
    pack = _pack_or_404(pack_id)
    if not _is_signoff_role():
        abort(403, description="Only a Partner or Admin can reopen a pack.")
    pack.status = "Draft"
    pack.finalised_by_id = None
    pack.finalised_at = None
    _touch(pack)
    db.session.commit()
    flash("Pack reopened for editing.", "success")
    return _back(pack, "summary")


@corpfin_bp.route("/management-accounts/<int:pack_id>/delete", methods=["POST"])
def pack_delete(pack_id):
    pack = _pack_or_404(pack_id)
    if pack.is_final:
        flash("A Final pack cannot be deleted. Reopen it first.", "danger")
        return _back(pack, "summary")
    if not _is_signoff_role() and pack.created_by_id != current_user.id:
        abort(403, description="Only the person who created a pack, or a Partner / Admin, can delete it.")
    number, eng = pack.pack_number, pack.engagement_id
    db.session.delete(pack)
    db.session.commit()
    flash(f"Pack {number} deleted.", "success")
    return redirect(url_for("corpfin.engagement_packs", engagement_id=eng) if eng else url_for("corpfin.dashboard"))


# =========================================================================== commentary

@corpfin_bp.route("/management-accounts/<int:pack_id>/commentary", methods=["POST"])
def pack_commentary(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "commentary")
    d = _data(pack)
    d["commentary"] = {k: (request.form.get(f"c_{k}") or "").strip()[:6000] for k in ("summary", "income", "balance", "cash", "outlook", "actions")}
    notes = {}
    for x in M.pl_line_defs(d["structure"]):
        t = (request.form.get(f"ln_{x['key']}") or "").strip()[:500]
        if t:
            notes[x["key"]] = t
    d["line_notes"] = notes
    _save(pack, d)
    flash("Commentary saved.", "success")
    return _back(pack, "commentary")


# =========================================================================== actuals: manual, modelled, trial balance

@corpfin_bp.route("/management-accounts/<int:pack_id>/actuals/<month>/manual", methods=["GET", "POST"])
def pack_manual(pack_id, month):
    pack = _pack_or_404(pack_id)
    d = _data(pack)
    _month_or_404(d, month)
    if _plocked(pack):
        return _back(pack, "actuals")
    structure = d["structure"]
    existing = (d["entries"].get(month) or {})
    if request.method == "POST":
        form = request.form
        pl = {x["key"]: M.f(form.get(f"pl_{x['key']}")) for x in M.pl_line_defs(structure)}
        raw_bs = {k: form.get(f"bs_{k}") for k in M.BS_KEYS}
        has_bs = any((v or "").strip() != "" for k, v in raw_bs.items() if k != "retained")
        entry = {"kind": "month", "source": "manual", "pl": pl, "note": (form.get("note") or "").strip()[:300] or None, "saved": _stamp()}
        if has_bs:
            entry["bs"] = {k: (M.f(v) if (v or "").strip() != "" else None) for k, v in raw_bs.items()}
        d["entries"][month] = entry
        _save(pack, d)
        flash(f"{M.month_label(month, False)} saved (manual capture)." + ("" if has_bs else " No balance sheet was entered for this month."), "success")
        return _back(pack, "actuals")
    cur = {}
    if existing.get("kind") == "month":
        cur = existing
    return render_template("corpfin/ma_manual.html", pack=pack, d=d, tab="actuals", cur=pack.currency, engagement=pack.engagement, structure=structure,
                           settings=d["settings"], months=M.fy_months(d), report_month=M.report_month(d), fy=M.fy_label(d), prior_label=M.prior_fy_label(d),
                           month=month, month_name=M.month_label(month, False), line_defs=M.pl_line_defs(structure), bs_lines=M.BS_LINES, entry=cur,
                           replacing_tb=existing.get("kind") == "ytd", warnings=[], status=M.data_status(d), has_prior=bool((d.get("prior") or {}).get("pl")),
                           month_label=M.month_label)


@corpfin_bp.route("/management-accounts/<int:pack_id>/actuals/<month>/delete", methods=["POST"])
def pack_delete_month(pack_id, month):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "actuals")
    d = _data(pack)
    _month_or_404(d, month)
    if d["entries"].pop(month, None) is not None:
        _save(pack, d)
        flash(f"{M.month_label(month, False)} removed.", "success")
    return _back(pack, "actuals")


@corpfin_bp.route("/management-accounts/<int:pack_id>/actuals/modelled", methods=["POST"])
def pack_modelled(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "actuals")
    d = _data(pack)
    form = request.form
    try:
        n = max(1, min(12, int(form.get("n_months") or 0)))
    except ValueError:
        n = 0
    months = M.fy_months(d)
    start = form.get("start_month") if form.get("start_month") in months else months[0]
    start_idx = months.index(start)
    if not n or start_idx + n > 12:
        flash("Choose how many months to model (they must fall within the financial year).", "danger")
        return _back(pack, "actuals")
    gp, gb = M.f(form.get("growth_pl"), 0.0), M.f(form.get("growth_bs"), 0.0)
    line_growth = {}
    if (form.get("growth_rev") or "").strip() != "":
        for r in d["structure"]["revenue"]:
            line_growth[r["key"]] = M.f(form.get("growth_rev"))
    try:
        ents = M.modelled_entries(d, n, growth_pl=gp, growth_bs=gb, line_growth=line_growth, start_idx=start_idx)
    except ValueError as e:
        flash(str(e), "danger")
        return _back(pack, "prior")
    over = form.get("overwrite") == "1"
    done, skipped = [], []
    stamp = _stamp()
    for m, e in ents.items():
        old = d["entries"].get(m)
        if old and old.get("source") != "modelled" and not over:
            skipped.append(M.month_label(m, False))
            continue
        e["saved"] = stamp
        e["note"] = f"Modelled from {M.prior_fy_label(d)}: figures x (1 + {gp:g}%) / 12 per month" + (f"; revenue grown {M.f(form.get('growth_rev')):g}%" if line_growth else "")
        d["entries"][m] = e
        done.append(M.month_label(m, False))
    _save(pack, d)
    if done:
        flash(f"Modelled {len(done)} month(s) ({done[0]} to {done[-1]}) from the {M.prior_fy_label(d)} accounts grown {gp:g}%. They are marked MODELLED on every report.", "success")
    if skipped:
        flash("Left alone because real figures already exist: " + ", ".join(skipped) + ". Tick 'replace' to overwrite them.", "warning")
    return _back(pack, "actuals")


def _client_map(client_id):
    return {M.norm_name(m.account_name): m.fs_category for m in COAMapping.query.filter_by(client_id=client_id).all()}


def _mapping_page(pack, d, rows, chosen, hows, target, month, filename, label="", diag=None, fixlog=None):
    structure = d["structure"]
    fixlog = fixlog or []
    return render_template("corpfin/ma_mapping.html", pack=pack, d=d, tab="actuals" if target == "actual" else "prior", cur=pack.currency,
                           engagement=pack.engagement, structure=structure, settings=d["settings"], months=M.fy_months(d), report_month=M.report_month(d),
                           fy=M.fy_label(d), prior_label=M.prior_fy_label(d), warnings=[], status=M.data_status(d), has_prior=bool((d.get("prior") or {}).get("pl")),
                           month_label=M.month_label, rows=rows, chosen=chosen, hows=hows, target=target, month=month, filename=filename,
                           options=M.target_options(structure), rows_json=json.dumps(rows), label=label,
                           month_name=M.month_label(month, False) if month else "", diag=diag, fixlog=fixlog, fixlog_json=json.dumps(fixlog),
                           target_names=dict((k, lab) for _, opts in M.target_options(structure) for k, lab in opts))


@corpfin_bp.route("/management-accounts/<int:pack_id>/tb/upload", methods=["POST"])
def pack_tb_upload(pack_id):
    pack = _pack_or_404(pack_id)
    target = "prior" if request.form.get("target") == "prior" else "actual"
    back = _back(pack, "prior" if target == "prior" else "actuals")
    if _plocked(pack):
        return back
    d = _data(pack)
    month = request.form.get("month")
    if target == "actual" and month not in M.fy_months(d):
        flash("Choose the month this trial balance is for.", "danger")
        return back
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a trial balance file (.xlsx or .csv).", "danger")
        return back
    try:
        rows = M.read_tb_file(f.stream, f.filename)
    except ValueError as e:
        flash(str(e), "danger")
        return back
    except Exception:
        flash("That file could not be read. Use an .xlsx or .csv trial balance with an account name column and debit/credit (or balance) columns.", "danger")
        return back
    if len(rows) > 5000:
        flash("That trial balance has more than 5,000 accounts. Summarise it first.", "danger")
        return back
    cmap = _client_map(pack.client_id)
    chosen, hows = {}, {}
    for i, r in enumerate(rows):
        t, how = M.suggest_target(r["name"], d["structure"], d["mapping"], cmap, suggest_fs_category)
        if t:
            chosen[str(i)], hows[str(i)] = t, how
    return _mapping_page(pack, d, rows, chosen, hows, target, month, f.filename, label=(request.form.get("prior_label") or "").strip())


@corpfin_bp.route("/management-accounts/<int:pack_id>/tb/confirm", methods=["POST"])
def pack_tb_confirm(pack_id):
    pack = _pack_or_404(pack_id)
    target = "prior" if request.form.get("target") == "prior" else "actual"
    back = _back(pack, "prior" if target == "prior" else "actuals")
    if _plocked(pack):
        return back
    d = _data(pack)
    month = request.form.get("month")
    try:
        rows = json.loads(request.form.get("rows_json") or "[]")
        rows = [{"code": str(r.get("code") or "")[:40], "name": str(r["name"])[:200], "net_dr": float(r["net_dr"])} for r in rows][:5000]
    except (ValueError, KeyError, TypeError, AttributeError):
        flash("The trial balance could not be read back. Please upload it again.", "danger")
        return back
    if not rows or (target == "actual" and month not in M.fy_months(d)):
        flash("The trial balance could not be read back. Please upload it again.", "danger")
        return back
    valid = set(M.pl_keys(d["structure"])) | set(M.BS_KEYS) | {"ignore"}
    chosen = {str(i): request.form.get(f"map_{i}") for i in range(len(rows)) if request.form.get(f"map_{i}") in valid}
    filename = (request.form.get("filename") or "")[:200]
    label = (request.form.get("label") or "").strip()[:30]
    fixlog = _parse_fixlog(request.form)
    missing = [r["name"] for i, r in enumerate(rows) if str(i) not in chosen]
    if missing:
        flash(f"{len(missing)} account(s) still need a line (or 'Ignore'): " + ", ".join(missing[:5]) + ("..." if len(missing) > 5 else "") + ".", "danger")
        return _mapping_page(pack, d, rows, chosen, {}, target, month, filename, label, fixlog=fixlog)

    cmap = _client_map(pack.client_id)

    def fallback(i):
        t, _how = M.suggest_target(rows[i]["name"], d["structure"], d["mapping"], cmap, suggest_fs_category)
        return t if t and t != "ignore" else None

    fix = request.form.get("fix")
    if fix:
        diag = RC.diagnose_tb(rows, chosen, fallback_target=fallback)
        sug = next((x for x in diag["suggestions"] if x["id"] == fix and not x.get("advisory")), None)
        if sug:
            for i, v in (sug.get("row_changes") or {}).items():
                fixlog.append(f"{rows[int(i)]['name']}: {rows[int(i)]['net_dr']:,.2f} changed to {v:,.2f}")
                rows[int(i)]["net_dr"] = v
            for i, t in (sug.get("map_changes") or {}).items():
                fixlog.append(f"{rows[int(i)]['name']}: no longer ignored, mapped to {t.replace('_', ' ')}")
                chosen[str(i)] = t
            flash("Fix applied. Review the mapping below and save when it looks right.", "success")
        else:
            flash("That suggestion no longer applies to the figures as they are now. The checks have been re-run.", "warning")
    entry, stats = M.entry_from_tb(rows, chosen, d["structure"])
    unbalanced = abs(stats["tb_out_by"]) > 0.5 or abs(stats["bs_check"]) > 0.5
    if unbalanced and request.form.get("force") != "1":
        diag = RC.diagnose_tb(rows, chosen, fallback_target=fallback)
        return _mapping_page(pack, d, rows, chosen, {}, target, month, filename, label, diag=diag, fixlog=fixlog)
    if (request.form.get("recheck") or fix) and not unbalanced:
        flash("These figures now balance. Press Save to keep them.", "success")
        return _mapping_page(pack, d, rows, chosen, {}, target, month, filename, label, fixlog=fixlog)
    for i, r in enumerate(rows):
        if len(d["mapping"]) < 5000:
            d["mapping"][M.norm_name(r["name"])] = chosen[str(i)]
    entry["note"] = (f"Trial balance: {filename}" if filename else "Trial balance upload") + (". Corrections accepted at review: " + "; ".join(fixlog) if fixlog else "")
    if unbalanced:
        entry["note"] += ". Saved out of balance"
    entry["saved"] = _stamp()
    if target == "actual":
        d["entries"][month] = entry
        what = f"{M.month_label(month, False)} (year to date)"
    else:
        prior = M.to_prior(entry)
        prior.update(label=label or M.prior_fy_label(d), note=entry["note"], saved=entry["saved"])
        d["prior"] = prior
        what = "the prior year"
    _save(pack, d)
    flash(f"Trial balance loaded for {what}: {stats['mapped']} accounts mapped, profit after tax {stats['pat']:,.0f}.", "success")
    if abs(stats["tb_out_by"]) > 0.5:
        flash(f"Saved with the trial balance out of balance (debits less credits = {stats['tb_out_by']:,.2f}). The Actuals tab will keep flagging it.", "warning")
    elif abs(stats["bs_check"]) > 0.5:
        flash(f"Saved with the balance sheet out by {stats['bs_check']:,.2f}. The Actuals tab will keep flagging it.", "warning")
    return back


# =========================================================================== variance model

def _pick_view(d, args):
    """basis / comparator / measure from the query string, falling back to the first combination that has figures."""
    avail = VM.availability(d)
    basis = args.get("basis") if args.get("basis") in dict(VM.BASES) else "ytd"
    against = args.get("against") if args.get("against") in dict(VM.AGAINST) else "budget"
    measure = args.get("measure") if args.get("measure") in dict(VM.MEASURES) else "pat"
    if not avail[basis][against]:
        for b_ in (basis, "ytd", "month", "fy"):
            for a_ in (against, "budget", "forecast", "prior"):
                if avail[b_][a_]:
                    return b_, a_, measure, avail
    return basis, against, measure, avail


def _varmodel_ctx(d):
    basis, against, measure, avail = _pick_view(d, request.args)
    b = VM.bridge(d, basis, against, measure)
    dr = VM.drivers(d, basis, against)
    structure = d["structure"]
    cost_lines = [x for x in M.pl_line_defs(structure) if not x["income"] and x["key"] != "div"]
    return dict(basis=basis, against=against, measure=measure, avail=avail, br=b, svg=VM.waterfall_svg(b), dr=dr, narr=VM.narrative(b, dr, ""),
                BASES=VM.BASES, AGAINST=VM.AGAINST, MEASURES=VM.MEASURES, TAG_TYPES=VM.TAG_TYPES, DRIVER_COLS=VM.DRIVER_COLS, cost_lines=cost_lines,
                rev_lines=structure["revenue"], line_defs=[x for x in M.pl_line_defs(structure) if x["key"] != "div"], any_avail=any(v for row in avail.values() for v in row.values()))


@corpfin_bp.route("/management-accounts/<int:pack_id>/varmodel/save", methods=["POST"])
def pack_varmodel_save(pack_id):
    pack = _pack_or_404(pack_id)
    basis = request.form.get("basis") if request.form.get("basis") in dict(VM.BASES) else "ytd"
    against = request.form.get("against") if request.form.get("against") in dict(VM.AGAINST) else "budget"
    measure = request.form.get("measure") if request.form.get("measure") in dict(VM.MEASURES) else "pat"
    back = redirect(url_for("corpfin.pack_tab", pack_id=pack.id, tab="varmodel", basis=basis, against=against, measure=measure, month=request.form.get("month") or None))
    if _plocked(pack):
        return back
    d = _data(pack)
    vm = VM.vm_of(d)
    vm = {"units": dict(vm.get("units") or {}), "variable_pct": dict(vm.get("variable_pct") or {}), "tags": dict(vm.get("tags") or {})}
    key = f"{basis}:{against}"
    units = {}
    for r in d["structure"]["revenue"]:
        a_, c_ = M.f(request.form.get(f"u_a_{r['key']}")), M.f(request.form.get(f"u_c_{r['key']}"))
        if a_ > 0 and c_ > 0:
            units[r["key"]] = {"a": a_, "c": c_}
    vm["units"][key] = units
    for x in M.pl_line_defs(d["structure"]):
        if not x["income"] and x["key"] != "div":
            raw = (request.form.get(f"vp_{x['key']}") or "").strip()
            if raw != "":
                vm["variable_pct"][x["key"]] = max(0.0, min(100.0, M.f(raw)))
    valid_lines = {x["key"] for x in M.pl_line_defs(d["structure"]) if x["key"] != "div"}
    tags = []
    for i in range(20):
        line, typ, amt = request.form.get(f"tag_line_{i}"), request.form.get(f"tag_type_{i}"), (request.form.get(f"tag_amount_{i}") or "").strip()
        if line in valid_lines and typ in dict(VM.TAG_TYPES) and amt != "" and M.f(amt) != 0 and not request.form.get(f"tag_del_{i}"):
            tags.append({"line": line, "type": typ, "amount": M.f(amt), "note": (request.form.get(f"tag_note_{i}") or "").strip()[:200]})
    vm["tags"][key] = tags
    vm["saved"] = _stamp()
    d["var_model"] = vm
    _save(pack, d)
    flash("Variance drivers saved.", "success")
    return back


@corpfin_bp.route("/management-accounts/<int:pack_id>/varmodel/commentary", methods=["POST"])
def pack_varmodel_commentary(pack_id):
    pack = _pack_or_404(pack_id)
    basis = request.form.get("basis") if request.form.get("basis") in dict(VM.BASES) else "ytd"
    against = request.form.get("against") if request.form.get("against") in dict(VM.AGAINST) else "budget"
    measure = request.form.get("measure") if request.form.get("measure") in dict(VM.MEASURES) else "pat"
    if _plocked(pack):
        return _back(pack, "varmodel")
    d = _data(pack)
    b = VM.bridge(d, basis, against, measure)
    if not b:
        flash("There is nothing to write up for that comparison yet.", "warning")
        return _back(pack, "varmodel")
    text = "\n".join(VM.narrative(b, VM.drivers(d, basis, against), ""))
    cur = (d["commentary"].get("income") or "").strip()
    if text in cur:
        flash("That text is already in the income statement commentary.", "info")
    else:
        d["commentary"]["income"] = (cur + "\n\n" if cur else "") + text
        _save(pack, d)
        flash("Added to the income statement commentary. Edit it on the Commentary tab.", "success")
    return redirect(url_for("corpfin.pack_tab", pack_id=pack.id, tab="varmodel", basis=basis, against=against, measure=measure))


# =========================================================================== reforecast

def _saved_scenarios(d):
    sc = d.get("scenarios")
    return [s for s in sc if isinstance(s, dict) and s.get("id")] if isinstance(sc, list) else []


def _reforecast_ctx(pack, d):
    saved = _saved_scenarios(d)
    sid = request.args.get("scenario")
    chosen = next((s for s in saved if str(s["id"]) == sid), None)
    if request.args.get("sc_base") is not None:
        sc = VM.scenario_from_form(request.args, d)
    elif chosen:
        sc = VM.normalise_scenario(chosen.get("params"), d)
        sc["name"] = chosen.get("name") or sc["name"]
    else:
        sc = VM.normalise_scenario({}, d)
    res = VM.reforecast(d, sc)
    V = M.vectors(d)
    remaining = V["months"][V["n"]:]
    cols, _first = VM.compare_scenarios(d, saved) if saved else ([], None)
    base_res = VM.reforecast(d, {"base": "current"}) if not res.get("error") or saved else None
    return dict(sc=sc, res=res, saved=saved, chosen=chosen, remaining=remaining, remaining_labels=[(m, M.month_label(m, False)) for m in remaining],
                BASE_CHOICES=VM.BASE_CHOICES, line_defs=M.pl_line_defs(d["structure"]), compare_cols=cols, current_res=base_res,
                V=V, chart_rf=_rf_chart(res) if res and not res.get("error") else None)


def _rf_chart(res):
    return {"months": [r["label"] for r in res["months"]],
            "rev": [round(r["revenue"], 2) if r["revenue"] is not None else None for r in res["months"]],
            "rev_b": [round(r["b_revenue"], 2) if r["b_revenue"] is not None else None for r in res["months"]],
            "pat_cum": [round(r["cum_pat"], 2) for r in res["months"]],
            "pat_cum_b": [round(r["cum_b_pat"], 2) if r["cum_b_pat"] is not None else None for r in res["months"]],
            "kinds": [r["kind"] for r in res["months"]],
            "cash": ([None] * res["n"]) + [round(x["net_cash"], 2) for x in res["cash"]["months"]] if res.get("cash") else None,
            "cash_cur": ([None] * res["n"]) + [round(x["current_net_cash"], 2) for x in res["cash"]["months"]] if res.get("cash") else None,
            "cash_b": ([None] * res["n"]) + [round(x["budget_net_cash"], 2) if x["budget_net_cash"] is not None else None for x in res["cash"]["months"]] if res.get("cash") else None}


def _rf_back(pack, **kw):
    return redirect(url_for("corpfin.pack_tab", pack_id=pack.id, tab="reforecast", **kw))


@corpfin_bp.route("/management-accounts/<int:pack_id>/reforecast/save", methods=["POST"])
def pack_reforecast_save(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _rf_back(pack)
    d = _data(pack)
    sc = VM.scenario_from_form(request.form, d)
    if not sc["name"]:
        flash("Give the scenario a name so you can find it again.", "danger")
        return redirect(url_for("corpfin.pack_tab", pack_id=pack.id, tab="reforecast", **{k: v for k, v in request.form.items() if k.startswith(("sc_", "adj_"))}))
    saved = _saved_scenarios(d)
    existing = next((s for s in saved if str(s["id"]) == request.form.get("scenario_id")), None)
    entry = {"id": existing["id"] if existing else (max([s["id"] for s in saved], default=0) + 1), "name": sc["name"], "params": {k: v for k, v in sc.items() if k != "name"},
             "saved": _stamp()}
    if existing:
        saved = [entry if s["id"] == existing["id"] else s for s in saved]
    else:
        if len(saved) >= 8:
            flash("A pack can hold eight scenarios. Delete one first.", "danger")
            return _rf_back(pack)
        saved.append(entry)
    d["scenarios"] = saved
    _save(pack, d)
    flash(f"Scenario '{sc['name']}' saved.", "success")
    return _rf_back(pack, scenario=entry["id"])


@corpfin_bp.route("/management-accounts/<int:pack_id>/reforecast/<int:sid>/delete", methods=["POST"])
def pack_reforecast_delete(pack_id, sid):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _rf_back(pack)
    d = _data(pack)
    saved = _saved_scenarios(d)
    d["scenarios"] = [s for s in saved if s["id"] != sid]
    if len(d["scenarios"]) != len(saved):
        _save(pack, d)
        flash("Scenario deleted.", "success")
    return _rf_back(pack)


@corpfin_bp.route("/management-accounts/<int:pack_id>/reforecast/apply", methods=["POST"])
def pack_reforecast_apply(pack_id):
    """Make a scenario the pack's Forecast for the remaining months (earlier forecast months are kept)."""
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _rf_back(pack)
    d = _data(pack)
    sc = VM.scenario_from_form(request.form, d)
    res = VM.reforecast(d, sc)
    if res.get("error"):
        flash(res["error"], "danger")
        return _rf_back(pack)
    d["forecast"] = VM.forecast_dataset(d, res, _stamp())
    msg = f"The forecast for {len(res['remaining'])} remaining month(s) was replaced with this scenario."
    if d["settings"].get("outlook") in ("budget_remaining", "run_rate"):
        d["settings"]["outlook"] = "auto"
        msg += " The full-year outlook setting was changed to Automatic so that it uses the forecast."
    _save(pack, d)
    flash(msg + " Review the Budget & forecast tab and the statements.", "success")
    return _rf_back(pack, **{k: v for k, v in request.form.items() if k.startswith(("sc_", "adj_"))})


# =========================================================================== forecast continuation (pack <-> forecast model)

def _continuation_ctx(pack, d):
    """What the Budget & forecast tab needs to offer 'continue the actuals as a forecast'."""
    status = LK.link_status(d)
    link = d.get("forecast_link") or {}
    params = LK.normalise_params(link.get("params"))
    model = None
    if link.get("model_id"):
        model = CFModel.query.get(link["model_id"])
        if model is not None and (model.client_id != pack.client_id or not _can_see(model)):
            model = None
    preview, error = None, None
    try:
        a, info = LK.continuation_assumptions(d, params, pack.currency)
        n = M.report_index(d) + 1
        preview = dict(info, remaining=max(0, 12 - n), years=a["years"], first_label=M.month_label(a["start_month"], False))
    except LK.LinkError as e:
        error = str(e)
    return dict(link=link, link_status=status, link_params=params, link_model=model, link_preview=preview, link_error=error,
                basis_labels=LK.BASIS_LABELS, report_label=M.month_label(M.report_month(d), False),
                link_scenarios=list(model.scenarios) if model is not None else [])


def _params_from_form(form, d):
    raw = {k: form.get(k) for k in ("years", "basis", "season", "rev_adj", "cost_adj", "growth", "opex_growth", "capex", "tax_rate", "payout", "repay_years")}
    raw["keep_forward"] = bool(form.get("keep_forward"))
    return LK.normalise_params(raw)


def _apply_model_to_pack(pack, d, model, scenario, built_for, params, fingerprint):
    """Run the model and put its remaining-month results into the pack's Forecast. Returns (plan, result, link, notes)."""
    result = _run(model, scenario)
    a = result["a"]
    structure = d["structure"]
    n = M.report_index(d) + 1
    has_oth = any(s["name"].strip().lower() == "other income" for s in a["revenue"]) and len(a["revenue"]) > len(structure["revenue"])
    if len(a["revenue"]) < len(structure["revenue"]) or len(a["opex"]) < len(structure["opex"]):
        raise LK.LinkError("The linked model has fewer revenue or expense lines than the pack. Rebuild the continuation from the actuals.")
    plan = LK.plan_from_result(structure, result, a, M.fy_months(d), n, has_oth)
    link = {"model_id": model.id, "model_number": model.model_number, "model_name": model.name, "scenario": scenario.name,
            "built_for": built_for, "start_month": a["start_month"], "params": params, "fingerprint": fingerprint,
            "refreshed": _stamp(), "months": plan["months"]}
    d["forecast"] = LK.forecast_dataset(d, plan, link, _stamp())
    d["forecast_link"] = link
    notes = []
    if d["settings"].get("outlook") in ("budget_remaining", "run_rate"):
        d["settings"]["outlook"] = "auto"
        notes.append("The full-year outlook setting was changed to Automatic so that it uses the forecast.")
    expected = M.shift_month(M.report_month(d), 1)
    if a["start_month"] != expected:
        notes.append(f"The model starts in {M.month_label(a['start_month'], False)} but the actuals end in {M.month_label(M.report_month(d), False)}. "
                     "Roll the forecast forward so that the two join up.")
    elif plan["months"] and len(plan["months"]) < 12 - n:
        notes.append("The model does not cover every remaining month of this financial year.")
    return plan, result, link, notes


def _continue_core(pack, d, params, mode):
    """Create / roll forward / rebuild the continuation model and put its remaining months in d["forecast"].
    Returns (model, plan, link, notes, verb). Raises LK.LinkError with a message for the user."""
    a, info = LK.continuation_assumptions(d, params, pack.currency)
    old = d.get("forecast_link") or {}
    notes = []
    model = CFModel.query.get(old["model_id"]) if (old.get("model_id") and mode != "new") else None
    if model is not None and (model.client_id != pack.client_id or not _can_see(model)):
        model = None
    if model is not None and model.is_final:
        notes.append(f"{model.model_number} is Final and locked, so a new model was created for the rolled-forward forecast.")
        model = None
    if model is not None and mode == "roll" and params["keep_forward"]:
        a = LK.overlay_forward(a, model.assumptions)
    month_txt = M.month_label(M.report_month(d), False)
    purpose = f"Continues the actuals in Management Accounts {pack.pack_number} from {month_txt}. The opening balance sheet is the actual balance sheet."
    if model is None:
        def make():
            return CFModel(model_number=_next_model_number(), client_id=pack.client_id, engagement_id=pack.engagement_id,
                           name=f"{pack.name} - forecast from {month_txt}"[:200], purpose=purpose,
                           status="Draft", assumptions_json=None, created_by_id=current_user.id, updated_by_id=current_user.id)
        model = _commit_with_number(make)
        model.set_json("assumptions", a)
        model.set_json("historical", {})
        model.set_json("actuals", {})
        db.session.add(CFScenario(model_id=model.id, name="Base case", is_base=True, sort_order=0, shocks_json="{}"))
        db.session.commit()
        verb = "created"
    else:
        model.set_json("assumptions", a)
        model.purpose = purpose
        _touch(model)
        db.session.commit()
        verb = "rolled forward" if mode == "roll" else "rebuilt from the actuals"
    plan, result, link, more = _apply_model_to_pack(pack, d, model, _base_scenario(model), M.report_month(d), params, LK.fingerprint(d))
    return model, plan, link, notes + more + info["notes"], verb


def _roll_linked_forecast(pack, d):
    """After the actuals changed (a corrected Excel pack was imported), roll a linked forecast forward from them so that it does not
    carry the old opening position. Returns messages for the user; never raises."""
    link = d.get("forecast_link")
    if not isinstance(link, dict) or not link.get("model_id"):
        return []
    st = LK.link_status(d)
    if not st or not st["stale"]:
        return []
    try:
        model, plan, new_link, notes, verb = _continue_core(pack, d, LK.normalise_params(link.get("params")), "roll")
    except LK.LinkError as e:
        return [f"The forecast could not be rolled forward from the corrected actuals: {e}"]
    except Exception:
        current_app.logger.exception("auto roll forward failed")
        return ["The forecast was not rolled forward automatically. Open Budget & forecast and roll it forward."]
    msg = f"The forecast model {model.model_number} was {verb} from the corrected actuals (it now opens from the {M.month_label(M.report_month(d), False)} balance sheet)"
    if plan["months"]:
        msg += f" and covers {M.month_label(plan['months'][0], False)} to {M.month_label(plan['months'][-1], False)}"
    return [msg + "."]


@corpfin_bp.route("/management-accounts/<int:pack_id>/continuation/build", methods=["POST"])
def pack_continuation_build(pack_id):
    """Create (or roll forward) the forecast model that continues the pack's actuals, and make it the pack's forecast."""
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "budget")
    d = _data(pack)
    params = _params_from_form(request.form, d)
    mode = request.form.get("mode") if request.form.get("mode") in ("roll", "rebuild", "new") else "roll"
    try:
        model, plan, link, notes, verb = _continue_core(pack, d, params, mode)
    except LK.LinkError as e:
        flash(str(e), "danger")
        return _back(pack, "budget")
    _save(pack, d)
    month_txt = M.month_label(M.report_month(d), False)
    if plan["months"]:
        flash(f"Model {model.model_number} {verb}: it opens from the actual balance sheet at {month_txt} and runs from "
              f"{M.month_label(link['start_month'], False)}. The pack's forecast now covers {M.month_label(plan['months'][0], False)} to {M.month_label(plan['months'][-1], False)}.", "success")
    else:
        flash(f"Model {model.model_number} {verb} and opens from the actual balance sheet at {month_txt}. The actuals already reach the end of the financial year, "
              "so there are no remaining months to forecast in this pack.", "success")
    for n_ in notes:
        flash(n_, "warning")
    return _back(pack, "budget")


@corpfin_bp.route("/management-accounts/<int:pack_id>/continuation/refresh", methods=["POST"])
def pack_continuation_refresh(pack_id):
    """Re-read the linked model (after its assumptions were edited) and update the pack's forecast from it."""
    pack = _pack_or_404(pack_id)
    back_model = request.form.get("back") == "model"
    d = _data(pack)
    link = d.get("forecast_link") or {}

    def back():
        if back_model and link.get("model_id"):
            return redirect(url_for("corpfin.model_tab", model_id=link["model_id"], tab=request.form.get("tab") or "overview"))
        return _back(pack, "budget")
    if _plocked(pack):
        return back()
    model = CFModel.query.get(link.get("model_id")) if link.get("model_id") else None
    if model is None or model.client_id != pack.client_id or not _can_see(model):
        flash("The linked forecast model is no longer available. Build the continuation again.", "danger")
        return _back(pack, "budget")
    scen = _pick_scenario(model, request.form.get("scenario"))
    try:
        plan, result, new_link, notes = _apply_model_to_pack(pack, d, model, scen, link.get("built_for"), link.get("params"), link.get("fingerprint"))
    except LK.LinkError as e:
        flash(str(e), "danger")
        return back()
    _save(pack, d)
    flash(f"The pack's forecast was refreshed from {model.model_number} ({scen.name}) for {len(plan['months'])} remaining month(s).", "success")
    for n_ in notes:
        flash(n_, "warning")
    return back()


@corpfin_bp.route("/management-accounts/<int:pack_id>/continuation/unlink", methods=["POST"])
def pack_continuation_unlink(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "budget")
    d = _data(pack)
    if d.pop("forecast_link", None):
        _save(pack, d)
        flash("The forecast is no longer linked to the model. The forecast figures stay as they are.", "success")
    return _back(pack, "budget")


# =========================================================================== diagnose a saved balance sheet

_BS_SIDES = {k: side for k, _, side in M.BS_LINES}


def _base_ctx(pack, d, tab, **extra):
    ctx = dict(pack=pack, d=d, tab=tab, cur=pack.currency, engagement=pack.engagement, structure=d["structure"], settings=d["settings"],
               months=M.fy_months(d), report_month=M.report_month(d), fy=M.fy_label(d), prior_label=M.prior_fy_label(d), month_label=M.month_label,
               warnings=M.warnings(d), status=M.data_status(d), has_prior=bool((d.get("prior") or {}).get("pl")), link_status=LK.link_status(d))
    ctx.update(extra)
    return ctx


def _scope_bs(d, scope):
    """(balance sheet dict used for the pack, profit to test against, label, holder dict that can be edited) for 'prior' or a month."""
    structure = d["structure"]
    if scope == "prior":
        p = d.get("prior") or {}
        if not p.get("bs"):
            return None
        pat = M.pl_vector(structure, p.get("pl") or {})["pat"] if p.get("pl") else None
        return dict(M.prior_bs(d)), pat, "the prior year", p["bs"]
    if scope not in M.fy_months(d):
        return None
    e = (d.get("entries") or {}).get(scope)
    ser = M.bs_series(d)
    if not e or not e.get("bs") or scope not in ser:
        return None
    idx = M.fy_months(d).index(scope)
    if e.get("kind") == "ytd":
        cum = M.cumulative_actuals(d)[idx]["cum"]
        pat = M.pl_vector(structure, cum)["pat"] if cum else None
    else:
        vals = M.monthly_actuals(d)[idx]["values"]
        pat = M.pl_vector(structure, vals)["pat"] if vals else None
    return {k: v for k, v in ser[scope].items() if k in M.BS_KEYS}, pat, M.month_label(scope, False), e["bs"]


@corpfin_bp.route("/management-accounts/<int:pack_id>/diagnose/<scope>", methods=["GET", "POST"])
def pack_diagnose(pack_id, scope):
    pack = _pack_or_404(pack_id)
    d = _data(pack)
    got = _scope_bs(d, scope)
    if got is None:
        flash("There is no balance sheet to check there.", "warning")
        return _back(pack, "prior" if scope == "prior" else "actuals")
    bs, pat, label, holder = got
    diag = RC.diagnose_pack_bs(bs, M.BS_LABEL, _BS_SIDES, pat=pat)
    if request.method == "POST":
        if _plocked(pack):
            return redirect(url_for("corpfin.pack_diagnose", pack_id=pack.id, scope=scope))
        sug = next((x for x in diag["suggestions"] if x["id"] == request.form.get("fix") and not x.get("advisory")), None)
        if sug is None:
            flash("That suggestion no longer applies; the figures have changed. The check has been re-run.", "warning")
        else:
            for c in sug["changes"]:
                holder[c["key"]] = c["new"]
            note = f"Corrected at review: {sug['title']}"
            if scope == "prior":
                d["prior"]["note"] = ((d["prior"].get("note") or "") + ". " + note).strip(". ")
                d["prior"]["saved"] = _stamp()
            else:
                e = d["entries"][scope]
                e["note"] = ((e.get("note") or "") + ". " + note).strip(". ")
                e["saved"] = _stamp()
            _save(pack, d)
            flash("Fix applied and saved.", "success")
        return redirect(url_for("corpfin.pack_diagnose", pack_id=pack.id, scope=scope))
    return render_template("corpfin/ma_diagnose.html", **_base_ctx(pack, d, "prior" if scope == "prior" else "actuals", diag=diag, scope=scope, scope_label=label,
                                                                   locked=pack.is_final))


# =========================================================================== prior year (typed)

@corpfin_bp.route("/management-accounts/<int:pack_id>/prior/save", methods=["POST"])
def pack_prior_save(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "prior")
    d = _data(pack)
    form = request.form
    pl = {x["key"]: M.f(form.get(f"pl_{x['key']}")) for x in M.pl_line_defs(d["structure"])}
    bs = {k: M.f(form.get(f"bs_{k}")) for k in M.BS_KEYS}
    plugged = False
    if (form.get("bs_retained") or "").strip() == "":
        t = M.bs_total(dict(bs, retained=0.0))
        bs["retained"] = t["assets"] - t["liabilities"] - bs["share_capital"]
        plugged = any(bs[k] for k in M.BS_KEYS if k != "retained")
    d["prior"] = {"label": (form.get("label") or "").strip()[:30] or M.prior_fy_label(d), "source": "manual", "pl": pl, "bs": bs,
                  "note": (d.get("prior") or {}).get("note"), "saved": _stamp()}
    _save(pack, d)
    flash("Prior-year accounts saved." + (" Retained earnings was left blank, so it was calculated to balance the sheet." if plugged else ""), "success")
    return _back(pack, "prior")


@corpfin_bp.route("/management-accounts/<int:pack_id>/prior/clear", methods=["POST"])
def pack_prior_clear(pack_id):
    pack = _pack_or_404(pack_id)
    if _plocked(pack):
        return _back(pack, "prior")
    d = _data(pack)
    d.pop("prior", None)
    _save(pack, d)
    flash("Prior-year accounts cleared.", "success")
    return _back(pack, "prior")


# =========================================================================== budget & forecast

def _plan_name(name):
    if name not in PLAN_NAMES:
        abort(404)
    return name


@corpfin_bp.route("/management-accounts/<int:pack_id>/plan/<name>/model", methods=["POST"])
def pack_plan_model(pack_id, name):
    pack = _pack_or_404(pack_id)
    _plan_name(name)
    if _plocked(pack):
        return _back(pack, "budget")
    d = _data(pack)
    try:
        model = CFModel.query.get(int(request.form.get("model_id")))
    except (TypeError, ValueError):
        model = None
    if model is None or model.client_id != pack.client_id or not _can_see(model):
        flash("Choose one of this client's forecast models.", "danger")
        return _back(pack, "budget")
    scen = _pick_scenario(model, request.form.get("scenario"))
    result = _run(model, scen)
    plan, notes = M.budget_from_model(d["structure"], result, result["a"], M.fy_months(d))
    d[name] = dict(plan, source="model", model_id=model.id, model_number=model.model_number, model_name=model.name,
                   scenario=scen.name, saved=_stamp())
    _save(pack, d)
    flash(f"{PLAN_NAMES[name]} taken from {model.model_number} ({scen.name}). It is a snapshot: take it again if the model changes.", "success")
    for n in notes:
        flash(n, "warning")
    return _back(pack, "budget")


@corpfin_bp.route("/management-accounts/<int:pack_id>/plan/<name>/annual", methods=["POST"])
def pack_plan_annual(pack_id, name):
    pack = _pack_or_404(pack_id)
    _plan_name(name)
    if _plocked(pack):
        return _back(pack, "budget")
    d = _data(pack)
    form = request.form
    annual = {x["key"]: M.f(form.get(f"annual_{x['key']}")) for x in M.pl_line_defs(d["structure"])}
    weights = None
    if form.get("phasing") == "custom":
        weights = [max(0.0, M.f(form.get(f"w_{i}"))) for i in range(12)]
        if sum(weights) <= 0:
            weights = None
    pl = M.phase_annual(d["structure"], annual, M.fy_months(d), weights)
    d[name] = {"source": "typed", "pl": pl, "bs": {}, "annual": annual, "weights": weights, "saved": _stamp()}
    _save(pack, d)
    flash(f"{PLAN_NAMES[name]} saved from annual figures, phased {'by your weights' if weights else 'evenly'} over the year. A typed {PLAN_NAMES[name].lower()} has no balance sheet or cash flow.", "success")
    return _back(pack, "budget")


@corpfin_bp.route("/management-accounts/<int:pack_id>/plan/<name>/grid", methods=["POST"])
def pack_plan_grid(pack_id, name):
    pack = _pack_or_404(pack_id)
    _plan_name(name)
    if _plocked(pack):
        return _back(pack, "budget")
    d = _data(pack)
    months = M.fy_months(d)
    plan = d.get(name) or {"source": "typed", "pl": {}, "bs": {}}
    pl = {}
    for i, m in enumerate(months):
        raw = {x["key"]: request.form.get(f"g__{x['key']}__{i}") for x in M.pl_line_defs(d["structure"])}
        if any((v or "").strip() != "" for v in raw.values()):
            pl[m] = {k: M.f(v) for k, v in raw.items()}
    plan["pl"] = pl
    if plan.get("source") == "model":
        plan["edited"] = True
    plan["saved"] = _stamp()
    d[name] = plan
    _save(pack, d)
    flash(f"{PLAN_NAMES[name]} monthly figures saved ({len(pl)} month(s)).", "success")
    return _back(pack, "budget")


@corpfin_bp.route("/management-accounts/<int:pack_id>/plan/<name>/copy", methods=["POST"])
def pack_plan_copy(pack_id, name):
    pack = _pack_or_404(pack_id)
    _plan_name(name)
    if _plocked(pack):
        return _back(pack, "budget")
    d = _data(pack)
    src = "budget" if name == "forecast" else "forecast"
    if not (d.get(src) or {}).get("pl"):
        flash(f"There is no {src} to copy.", "danger")
        return _back(pack, "budget")
    d[name] = dict(copy.deepcopy(d[src]), copied_from=src, saved=_stamp())
    _save(pack, d)
    flash(f"{PLAN_NAMES[name]} started as a copy of the {src}. Adjust the months on the monthly grid.", "success")
    return _back(pack, "budget")


@corpfin_bp.route("/management-accounts/<int:pack_id>/plan/<name>/clear", methods=["POST"])
def pack_plan_clear(pack_id, name):
    pack = _pack_or_404(pack_id)
    _plan_name(name)
    if _plocked(pack):
        return _back(pack, "budget")
    d = _data(pack)
    d.pop(name, None)
    _save(pack, d)
    flash(f"{PLAN_NAMES[name]} cleared.", "success")
    return _back(pack, "budget")


# =========================================================================== exports

@corpfin_bp.route("/management-accounts/<int:pack_id>/excel")
def pack_excel(pack_id):
    import cf_ma_excel
    pack = _pack_or_404(pack_id)
    d = _view_data(pack)
    data = cf_ma_excel.build_pack_workbook(_meta(pack, d), d)
    fn = f"{pack.pack_number}_{_safe_name(pack.name)}_{M.report_month(d)}.xlsx"
    return Response(data, mimetype=XLSX_MIME, headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@corpfin_bp.route("/management-accounts/<int:pack_id>/excel/<tab>")
def pack_excel_page(pack_id, tab):
    """The page you are looking at, as a one-sheet Excel workbook (values as at export)."""
    import cf_ma_excel_page as XP
    pack = _pack_or_404(pack_id)
    if tab not in XP.PAGES:
        abort(404)
    d = _view_data(pack)
    ctx = None
    if tab == "varmodel":
        ctx = _varmodel_ctx(d)
    elif tab == "reforecast":
        ctx = _reforecast_ctx(pack, d)
    data = XP.build_page_workbook(_meta(pack, d), d, tab, request.args, ctx)
    fn = f"{pack.pack_number}_{_safe_name(pack.name)}_{_safe_name(XP.PAGES[tab])}_{M.report_month(d)}.xlsx"
    return Response(data, mimetype=XLSX_MIME, headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@corpfin_bp.route("/management-accounts/<int:pack_id>/pdf")
def pack_pdf(pack_id):
    import cf_ma_pdf
    pack = _pack_or_404(pack_id)
    d = _view_data(pack)
    opts = {"var": request.args.get("var") != "0", "ytd": request.args.get("view") == "ytd"}
    data = cf_ma_pdf.build_pack_pdf(pack, _meta(pack, d), d, FIRM_LETTERHEAD, opts)
    fn = f"{pack.pack_number}_{_safe_name(pack.name)}_{M.report_month(d)}.pdf"
    return Response(data, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{fn}"'})


# =========================================================================== import a corrected Excel pack

@corpfin_bp.route("/management-accounts/<int:pack_id>/import")
def pack_import(pack_id):
    pack = _pack_or_404(pack_id)
    d = _data(pack)
    return render_template("corpfin/ma_import.html", **_base_ctx(pack, d, "actuals", report=None, log=(d.get("import_log") or [])[-5:][::-1]))


@corpfin_bp.route("/management-accounts/<int:pack_id>/import/review", methods=["POST"])
def pack_import_review(pack_id):
    import cf_ma_import as IM
    pack = _pack_or_404(pack_id)
    back = redirect(url_for("corpfin.pack_import", pack_id=pack.id))
    if _plocked(pack):
        return back
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose the Excel pack you downloaded and corrected (.xlsx).", "danger")
        return back
    if not f.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Upload an .xlsx file: the Excel pack downloaded from this page.", "danger")
        return back
    d = _data(pack)
    raw = f.stream.read(IM.MAX_BYTES + 1)
    fhash = hashlib.sha1(raw).hexdigest()
    last = (d.get("import_log") or [{}])[-1]
    if last.get("hash") == fhash:
        flash(f"That exact file ({f.filename}) was already imported on {last.get('at', '')} by {last.get('by', '')}. Download the pack again to make further corrections.", "info")
        return back
    try:
        parsed = IM.read_workbook(raw, d, _meta(pack, d))
        nd, report = IM.plan_import(d, parsed, f.filename, _stamp())
    except IM.ImportError_ as e:
        flash(str(e), "danger")
        return back
    except Exception:
        current_app.logger.exception("excel pack import failed")
        flash("That workbook could not be read. Use the file downloaded with 'Excel pack', or a one-page Income statement, Balance sheet, Actuals or Budget and forecast export, and keep its layout and headings.", "danger")
        return back
    return render_template("corpfin/ma_import.html", **_base_ctx(pack, d, "actuals", report=report, filename=f.filename, filehash=fhash, payload=json.dumps(parsed), log=[]))


@corpfin_bp.route("/management-accounts/<int:pack_id>/import/apply", methods=["POST"])
def pack_import_apply(pack_id):
    import cf_ma_import as IM
    pack = _pack_or_404(pack_id)
    back = redirect(url_for("corpfin.pack_import", pack_id=pack.id))
    if _plocked(pack):
        return back
    d = _data(pack)
    try:
        parsed = IM.clean_parsed(json.loads(request.form.get("payload") or ""))
    except (ValueError, IM.ImportError_):
        flash("The upload could not be read back. Please upload the file again.", "danger")
        return back
    filename = (request.form.get("filename") or "workbook.xlsx")[:120]
    fhash = (request.form.get("filehash") or "")[:40]
    last = (d.get("import_log") or [{}])[-1]
    if fhash and last.get("hash") == fhash:
        flash("That workbook has already been imported, so nothing was changed.", "info")
        return _back(pack, "actuals")
    accepted = set(request.form.getlist("fix")) if request.form.get("fixes_shown") else None
    nd, report = IM.plan_import(d, parsed, filename, _stamp(), accepted=accepted)
    if report["unchanged"]:
        flash("Nothing in that workbook differs from the pack, so nothing was changed.", "info")
        return _back(pack, "actuals")
    log = list(d.get("import_log") or [])
    st = _stamp()
    log.append({"file": filename, "by": st["by"], "at": st["at"], "summary": report["sections"], "n_changes": len(report["changes"]), "hash": fhash})
    nd["import_log"] = log[-20:]
    for c in report.get("corrections") or []:
        if c["applied"]:
            log[-1].setdefault("corrections", []).append(f"{c['month_label']} {c['label']}: {c['old']:,.0f} to {c['new']:,.0f}")
    nd["import_log"] = log[-20:]
    if any(x.startswith("Forecast: updated") for x in report["sections"]):
        msgs = ["The forecast figures in the workbook were imported as they are, so the linked forecast model was not rolled forward automatically "
                "(that would replace them). Refresh or roll it forward on the Budget & forecast tab if you want the model to take over again."]
    else:
        msgs = _roll_linked_forecast(pack, nd)
    _save(pack, nd)
    flash("The workbook is now the pack's final version: " + " ".join(report["sections"]), "success")
    for m_ in msgs:
        flash(m_, "success")
    for w in report["warnings"][:6]:
        flash(w, "warning")
    return _back(pack, "actuals")


# =========================================================================== load existing financial statements

def loader_context(client_id, engagement_id=None):
    """Documents already filed on this client's engagements that can be read as financial statements."""
    docs = []
    if client_id:
        for eng in Engagement.query.filter_by(client_id=client_id).order_by(Engagement.id.desc()).all():
            if not user_can_access_engagement(current_user, eng):
                continue
            for doc in sorted(eng.documents, key=lambda x: x.uploaded_at or datetime.min, reverse=True):
                ext = (doc.original_filename or "").rsplit(".", 1)[-1].lower()
                if ext in LOADER_EXT:
                    docs.append({"id": doc.id, "name": doc.original_filename, "engagement": eng.title, "category": doc.category or "",
                                 "date": doc.uploaded_at.strftime("%d %b %Y") if doc.uploaded_at else ""})
    return {"fs_docs": docs[:150], "fs_ai": FS.ai_available()}


def _loader_target(raw):
    """('pack'|'model', object) for 'pack:5' / 'model:3', checked for access and lock."""
    kind, _, sid = (raw or "").partition(":")
    try:
        i = int(sid)
    except ValueError:
        abort(400)
    if kind == "pack":
        return "pack", _pack_or_404(i)
    if kind == "model":
        from corporate_finance import _model_or_404
        return "model", _model_or_404(i)
    abort(400)


def _target_back(kind, obj):
    if kind == "pack":
        return redirect(url_for("corpfin.pack_tab", pack_id=obj.id, tab="prior"))
    return redirect(url_for("corpfin.model_tab", model_id=obj.id, tab="history"))


@corpfin_bp.route("/statements/read", methods=["POST"])
def fs_read():
    kind, obj = _loader_target(request.form.get("target"))
    back = _target_back(kind, obj)
    if obj.is_final:
        flash("This is Final and locked. A Partner can reopen it first.", "danger")
        return back
    source = request.form.get("source") or "upload"
    text, images, err, name, tmp = None, None, None, "", None
    try:
        if source == "paste":
            text = (request.form.get("text") or "").strip()
            name = "Pasted statements"
            if len(text) < 30:
                flash("Paste the statements text (income statement and balance sheet) first.", "danger")
                return back
        elif source == "document":
            try:
                doc = Document.query.get(int(request.form.get("document_id")))
            except (TypeError, ValueError):
                doc = None
            if doc is None or doc.engagement is None or doc.engagement.client_id != obj.client_id or not user_can_access_engagement(current_user, doc.engagement):
                flash("Choose one of this client's documents.", "danger")
                return back
            path = os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_filename)
            if not os.path.isfile(path):
                flash("That document's file could not be found on the server.", "danger")
                return back
            name = doc.original_filename
            text, images, err = FS.file_to_text(path, doc.original_filename)
        else:
            f = request.files.get("file")
            if not f or not f.filename:
                flash("Choose a file to read, pick a filed document, or paste the text.", "danger")
                return back
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext not in LOADER_EXT:
                flash("Use a PDF, Word (.docx), Excel (.xlsx) or CSV file.", "danger")
                return back
            fd, tmp = tempfile.mkstemp(suffix="." + ext)
            os.close(fd)
            f.save(tmp)
            if os.path.getsize(tmp) > MAX_UPLOAD:
                flash("That file is larger than 15 MB.", "danger")
                return back
            name = f.filename
            text, images, err = FS.file_to_text(tmp, f.filename)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    if err and not text and not images:
        flash(err, "danger")
        return back
    try:
        ex, _ = FS.extract(text=text, page_images=images)
    except ValueError as e:
        flash(str(e), "danger")
        return back
    periods = ex.get("periods") or []
    if not periods or not any((p.get("is") or p.get("bs")) for p in periods):
        flash("No financial statement figures could be found in that document. Try the paste option, or enter the figures by hand.", "danger")
        return back
    return _review_page(kind, obj, ex, periods, name, [])


_STD_BS_LABELS = dict(FS.STD_BS)


def _review_page(kind, obj, ex, periods, source_name, fixlog, flash_msg=None):
    """The 'check the figures' page: the figures, the reconciliation checks, and - for any year whose balance sheet does not
    balance - a diagnosis with fixes the preparer can accept one at a time."""
    for p in periods:
        p["checks"] = FS.period_checks(p)
        p["diag"] = RC.diagnose_statement_period(p, _STD_BS_LABELS) if p.get("bs") else None
    return render_template("corpfin/fs_review.html", kind=kind, obj=obj, ex=ex, periods=periods, source_name=source_name, STD_IS=FS.STD_IS, STD_BS=FS.STD_BS,
                           target=f"{kind}:{obj.id}", scales=FS.SCALES, cur_code=ex.get("currency") or getattr(obj, "currency", ""),
                           fixlog=fixlog, fixlog_json=json.dumps(fixlog), ex_notes_json=json.dumps(ex.get("notes") or []))


def _parse_periods(form):
    """The editable figures of the review page, read back from the form."""
    try:
        n = min(8, int(form.get("n_periods") or 0))
    except ValueError:
        n = 0
    periods = []
    for p in range(n):
        label = (form.get(f"label_{p}") or "").strip()[:30] or f"Year {p + 1}"
        period = {"label": label, "is": {}, "bs": {}, "use": form.get(f"use_{p}") == "1"}
        for k in FS.IS_KEYS:
            v = (form.get(f"is_{k}_{p}") or "").strip()
            if v != "":
                period["is"][k] = M.f(v)
        for k in FS.BS_KEYS:
            v = (form.get(f"bs_{k}_{p}") or "").strip()
            if v != "":
                period["bs"][k] = M.f(v)
        periods.append(period)
    return periods


def _parse_fixlog(form):
    try:
        log = json.loads(form.get("fixlog") or "[]")
        return [str(x)[:300] for x in log][:30] if isinstance(log, list) else []
    except ValueError:
        return []


@corpfin_bp.route("/statements/recheck", methods=["POST"])
def fs_recheck():
    """Re-run the checks on the figures as currently typed, optionally applying one suggested fix first (button value 'period:id')."""
    kind, obj = _loader_target(request.form.get("target"))
    back = _target_back(kind, obj)
    if obj.is_final:
        flash("This is Final and locked. A Partner can reopen it first.", "danger")
        return back
    form = request.form
    periods = _parse_periods(form)
    fixlog = _parse_fixlog(form)
    try:
        notes = [str(x) for x in json.loads(form.get("ex_notes") or "[]")][:8]
    except ValueError:
        notes = []
    ex = {"method": form.get("ex_method") or "builtin", "entity": (form.get("ex_entity") or "")[:200], "currency": (form.get("ex_currency") or "")[:6] or None,
          "scale": FS.SCALES.get(form.get("scale") or "units", 1), "notes": notes}
    fix = form.get("fix") or ""
    if fix:
        pi, _, sid = fix.partition(":")
        try:
            period = periods[int(pi)]
        except (ValueError, IndexError):
            period = None
        done = False
        if period is not None:
            diag = RC.diagnose_statement_period(period, _STD_BS_LABELS)
            sug = next((x for x in diag["suggestions"] if x["id"] == sid and not x.get("advisory")), None)
            if sug:
                for c in sug["changes"]:
                    period["bs"][c["key"]] = c["new"]
                fixlog.append(f"{period['label']}: {sug['title']}")
                done = True
        if done:
            flash("Fix applied. Check the figures against the document before saving.", "success")
        else:
            flash("That suggestion no longer applies to the figures as they are now. The checks have been re-run.", "warning")
    return _review_page(kind, obj, ex, periods, (form.get("source_name") or "")[:200], fixlog)


@corpfin_bp.route("/statements/apply", methods=["POST"])
def fs_apply():
    kind, obj = _loader_target(request.form.get("target"))
    back = _target_back(kind, obj)
    if obj.is_final:
        flash("This is Final and locked. A Partner can reopen it first.", "danger")
        return back
    form = request.form
    mult = FS.SCALES.get(form.get("scale") or "units", 1)
    periods = _parse_periods(form)
    fixlog = _parse_fixlog(form)
    src = (form.get("source_name") or "")[:200]
    if kind == "pack":
        try:
            pick = periods[int(form.get("pick") or 0)]
        except (ValueError, IndexError):
            flash("Choose which year to use as the prior year.", "danger")
            return back
        d = _data(obj)
        pl, bs = FS.to_pack_prior(pick, d["structure"], mult)
        d["prior"] = {"label": pick["label"], "source": "statements", "pl": pl, "bs": bs, "note": (f"Loaded from {src}" if src else "Loaded from financial statements") + (". Corrections accepted at review: " + "; ".join(fixlog) if fixlog else ""),
                      "saved": _stamp()}
        derived = "retained" not in pick["bs"] and "total_equity" not in pick["bs"] and not RC.diagnose_statement_period(pick, _STD_BS_LABELS)["balanced"]
        if derived:
            d["prior"]["note"] += ". Retained earnings were not in the statements and were set to the balancing figure"
        _save(obj, d)
        chk = M.bs_total(bs)["check"]
        if derived:
            flash(f"Retained earnings and reserves were not in the statements, so they were set to the balancing figure ({bs['retained']:,.0f}). Check it against the document.", "warning")
        flash(f"Prior year ({pick['label']}) loaded from the statements. Revenue and operating expenses sit on the first line of each section: split them on the "
              "Prior year tab if you want the same lines as the pack.", "success")
        if abs(chk) > 0.5:
            flash(f"The balance sheet is out by {chk:,.0f}. Correct the figures below.", "warning")
        return back
    used = [p for p in periods if p["use"]]
    if not used:
        flash("Tick at least one year to load.", "danger")
        return back
    new = FS.to_model_history(used, mult)
    hist = FS.merge_history(T.normalise_hist(obj.historical), new)
    obj.set_json("historical", T.normalise_hist(hist))
    msg = f"{len(used)} year(s) loaded into the historical analysis."
    if form.get("apply_assumptions") == "1":
        a = E.normalise(obj.assumptions)
        sug = T.suggest_assumptions(obj.historical, a["years"])
        if sug:
            a.update(sug)
            obj.set_json("assumptions", E.normalise(a))
            msg += " Starting assumptions, opening balance sheet and existing borrowings were taken from them; review the Assumptions tab."
    _touch(obj)
    db.session.commit()
    flash(msg, "success")
    return back
