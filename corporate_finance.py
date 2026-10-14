"""Corporate Finance & Business Advisory (replaces the old "Consulting" service line).

What lives here
* Financial forecast MODELS - driver-based income statement, balance sheet and cash flow for 1-10 years
  (monthly in year 1, annual after), with loans & covenants, ratios and break-even.
* SCENARIOS (what-ifs on the base assumptions) with comparison and two-way sensitivity grids.
* VALUATIONS (WACC build-up, DCF, trading multiples, football field), saved per model.
* BUDGET vs ACTUAL tracking against the forecast, and HISTORICAL analysis that suggests starting assumptions.
* PROJECTS - capital budgeting / feasibility (NPV, IRR, MIRR, payback, PI) with written feasibility notes.
* A stand-alone LOAN calculator.
* Excel exports with live formulas (cf_excel) and PDF reports (cf_pdf).

The arithmetic is in cf_engine.py / cf_tools.py (pure Python, unit tested) - this file is only the web layer.
Access: the "manage_corporate_finance" permission, plus the usual client scope and engagement confidentiality.
"""
import copy
from datetime import date, datetime

from jinja2 import Undefined
from flask import (Blueprint, render_template, redirect, url_for, request, flash, abort, Response, current_app)
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

import cf_engine as E
import cf_tools as T
import cf_report as R
from extensions import db
from models import (
    Client, Engagement, CFModel, CFScenario, CFValuation, CFProject, CF_STATUSES,
    user_has_permission, user_can_access_client, user_can_access_engagement, visible_clients,
    engagement_acceptance_cleared,
)
from invoicing import FIRM_LETTERHEAD, next_sequence_number

corpfin_bp = Blueprint("corpfin", __name__, url_prefix="/corporate-finance")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MODEL_TABS = [
    ("overview", "Overview"), ("assumptions", "Assumptions"), ("statements", "Statements"), ("loans", "Loans & covenants"),
    ("ratios", "Ratios & break-even"), ("scenarios", "Scenarios"), ("valuation", "Valuation"),
    ("bva", "Budget vs actual"), ("history", "Historical analysis"),
]

CURRENCIES = ["USD", "ZWG", "ZAR", "GBP", "EUR", "BWP"]

SENS_STEPS = {
    "revenue_pct": [-20, -10, 0, 10, 20], "growth_pp": [-4, -2, 0, 2, 4], "cogs_pp": [-4, -2, 0, 2, 4],
    "opex_pct": [-20, -10, 0, 10, 20], "rec_days": [-30, -15, 0, 15, 30], "inv_days": [-30, -15, 0, 15, 30],
    "pay_days": [-30, -15, 0, 15, 30], "capex_pct": [-40, -20, 0, 20, 40], "rate_pp": [-4, -2, 0, 2, 4],
}
SENS_METRICS = [("cash_last", "Closing cash (final year)"), ("cash_min", "Lowest cash balance"), ("pat_total", "Total profit after tax"),
                ("ebitda_last", "EBITDA (final year)"), ("equity_last", "Equity (final year)"), ("dscr_min", "Lowest debt service cover")]


# =========================================================================== access & helpers

@corpfin_bp.before_request
def _gate():
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not user_has_permission(current_user, "manage_corporate_finance"):
        abort(403, description="You do not have access to Corporate Finance & Business Advisory.")


def _guard(obj):
    """Client scope + engagement confidentiality for a model / project."""
    if not user_can_access_client(current_user, obj.client_id):
        abort(403, description="You are not working on this client, so you cannot open this.")
    if obj.engagement_id and obj.engagement is not None and not user_can_access_engagement(current_user, obj.engagement):
        abort(403, description="You are not on the engagement this belongs to.")
    return obj


def _can_see(obj):
    try:
        _guard(obj)
        return True
    except Exception:
        return False


def _model_or_404(model_id):
    return _guard(CFModel.query.get_or_404(model_id))


def _project_or_404(project_id):
    return _guard(CFProject.query.get_or_404(project_id))


def _is_signoff_role():
    return current_user.role in ("partner", "admin")


def _locked(model):
    if model.is_final:
        flash("This model is Final and locked. A Partner can reopen it to make changes.", "danger")
        return True
    return False


def _engagement_choices(clients):
    ids = [c.id for c in clients]
    if not ids:
        return []
    out = []
    for e in Engagement.query.filter(Engagement.client_id.in_(ids)).order_by(Engagement.id.desc()).all():
        if user_can_access_engagement(current_user, e):
            out.append(e)
    return out


def _valid_engagement(client_id, raw):
    """Engagement id from a form, only if it belongs to the client and the user may open it."""
    try:
        eng = Engagement.query.get(int(raw))
    except (TypeError, ValueError):
        return None
    if eng is None or eng.client_id != client_id or not user_can_access_engagement(current_user, eng):
        return None
    return eng


def _commit_with_number(make):
    """Create a numbered row; retry once if two people grabbed the same number."""
    for attempt in range(2):
        obj = make()
        db.session.add(obj)
        try:
            db.session.commit()
            return obj
        except IntegrityError:
            db.session.rollback()
    raise RuntimeError("Could not allocate a document number")


def _next_model_number():
    return next_sequence_number(CFModel.model_number, f"CF-{date.today().year}-")


def _next_project_number():
    return next_sequence_number(CFProject.project_number, f"CP-{date.today().year}-")


def _touch(model):
    model.updated_by_id = current_user.id
    model.updated_at = datetime.utcnow()


def _base_scenario(model):
    for s in model.scenarios:
        if s.is_base:
            return s
    s = CFScenario(model_id=model.id, name="Base case", is_base=True, sort_order=0, shocks_json="{}")
    db.session.add(s)
    db.session.commit()
    return s


def _pick_scenario(model, raw=None):
    raw = raw if raw is not None else request.args.get("scenario")
    scen = None
    try:
        sid = int(raw)
        scen = next((s for s in model.scenarios if s.id == sid), None)
    except (TypeError, ValueError):
        pass
    return scen or _base_scenario(model)


def _run(model, scenario=None):
    a = E.normalise(model.assumptions)
    shocks = scenario.shocks if scenario is not None and not scenario.is_base else {}
    return E.run(E.apply_shocks(a, shocks) if shocks else a)


def _scenario_label(scenario):
    return scenario.name if scenario is not None else "Base case"


# =========================================================================== template filters

@corpfin_bp.app_template_filter("cfn")
def _cfn(v, dp=0):
    """Money: thousands separators, brackets for negatives, dash for nil."""
    if v is None or isinstance(v, Undefined):
        return "-"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    if abs(v) < 0.5 * (10 ** -dp) and dp == 0:
        return "-"
    s = f"{abs(v):,.{dp}f}"
    return f"({s})" if v < 0 else s


@corpfin_bp.app_template_filter("cfp")
def _cfp(v, dp=1):
    """A fraction (0.123) as a percentage."""
    return "-" if v is None else f"{v * 100:,.{dp}f}%"


@corpfin_bp.app_template_filter("cfpp")
def _cfpp(v, dp=1):
    """A number that is already a percentage (12.3)."""
    return "-" if v is None else f"{v:,.{dp}f}%"


@corpfin_bp.app_template_filter("cfx")
def _cfx(v, dp=2):
    return "-" if v is None else f"{v:,.{dp}f}x"


@corpfin_bp.app_template_filter("cffmt")
def _cffmt(v, fmt="money"):
    if fmt == "pct":
        return _cfp(v)
    if fmt == "x":
        return _cfx(v)
    if fmt == "days":
        return "-" if v is None else f"{v:,.0f}"
    return _cfn(v)


@corpfin_bp.app_template_filter("cfin")
def _cfin(v, dp=2):
    """A value for an <input>: plain number, no thousands separators."""
    if v is None or v == "" or isinstance(v, Undefined):
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    s = f"{v:.{dp}f}".rstrip("0").rstrip(".")
    return s or "0"


@corpfin_bp.context_processor
def _ctx():
    return {"MODEL_TABS": MODEL_TABS, "CF_STATUSES": CF_STATUSES, "user_is_signoff": _is_signoff_role()}


# =========================================================================== dashboard

def _visible_models(client_filter=None):
    clients = visible_clients(current_user)
    ids = [c.id for c in clients]
    if client_filter:
        ids = [i for i in ids if i == client_filter]
    models = CFModel.query.filter(CFModel.client_id.in_(ids)).order_by(CFModel.updated_at.desc()).all() if ids else []
    projects = CFProject.query.filter(CFProject.client_id.in_(ids)).order_by(CFProject.updated_at.desc()).all() if ids else []
    return clients, [m for m in models if _can_see(m)], [p for p in projects if _can_see(p)]


@corpfin_bp.route("/")
def dashboard():
    try:
        client_filter = int(request.args.get("client_id") or 0) or None
    except ValueError:
        client_filter = None
    clients, models, projects = _visible_models(client_filter)
    return render_template("corpfin/dashboard.html", clients=clients, models=models, projects=projects, client_filter=client_filter)


# =========================================================================== engagement workspace
# A Corporate Finance engagement has its own tab bar (Overview, Client Acceptance, Models & Forecasts,
# Investment Appraisals, Checklist, Documents, Tasks - see templates/services/_tabs.html) in place of the audit
# tabs. These two pages are the "Models & Forecasts" and "Investment Appraisals" tabs.

def _advisory_engagement_or_404(engagement_id):
    eng = Engagement.query.get_or_404(engagement_id)
    if not eng.is_corporate_finance:
        abort(404)
    if not user_can_access_client(current_user, eng.client_id):
        abort(403, description="You are not working on this client, so you cannot open this.")
    if not user_can_access_engagement(current_user, eng):
        abort(403, description="You are not on this engagement's team, so you cannot open anything inside it.")
    return eng


def _acceptance_open(eng):
    return current_user.role == "admin" or engagement_acceptance_cleared(eng)


@corpfin_bp.route("/engagements/<int:engagement_id>/models")
def engagement_models(engagement_id):
    eng = _advisory_engagement_or_404(engagement_id)
    models = [m for m in eng.cf_models if _can_see(m)]
    return render_template("corpfin/engagement_models.html", engagement=eng, models=models, unlocked=_acceptance_open(eng))


@corpfin_bp.route("/engagements/<int:engagement_id>/appraisals")
def engagement_projects(engagement_id):
    eng = _advisory_engagement_or_404(engagement_id)
    projects = [p for p in eng.cf_projects if _can_see(p)]
    return render_template("corpfin/engagement_projects.html", engagement=eng, projects=projects, unlocked=_acceptance_open(eng))


# =========================================================================== models: create / settings

@corpfin_bp.route("/models/new", methods=["GET", "POST"])
def new_model():
    clients = visible_clients(current_user)
    engagements = _engagement_choices(clients)
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        try:
            client = Client.query.get(int(form.get("client_id")))
        except (TypeError, ValueError):
            client = None
        name = (form.get("name") or "").strip()
        if client is None or not user_can_access_client(current_user, client):
            flash("Choose the client this model is for.", "danger")
        elif not name:
            flash("Give the model a name.", "danger")
        else:
            years = E.clamp_years(form.get("years"), 5)
            currency = (form.get("currency") or "USD").strip().upper()[:6] or "USD"
            preset = form.get("preset") or "blank"
            a = E.default_assumptions(years, currency, preset, form.get("start_month") or None)
            eng = _valid_engagement(client.id, form.get("engagement_id"))

            def make():
                return CFModel(model_number=_next_model_number(), client_id=client.id, engagement_id=eng.id if eng else None,
                               name=name[:200], purpose=(form.get("purpose") or "").strip() or None, status="Draft",
                               assumptions_json=None, created_by_id=current_user.id, updated_by_id=current_user.id)
            model = _commit_with_number(make)
            model.set_json("assumptions", a)
            model.set_json("historical", {})
            model.set_json("actuals", {})
            db.session.add(CFScenario(model_id=model.id, name="Base case", is_base=True, sort_order=0, shocks_json="{}"))
            db.session.commit()
            flash(f"Model {model.model_number} created with starter assumptions - replace them with the client's figures.", "success")
            return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="assumptions"))
    next_year = date.today().year + (1 if date.today().month > 1 else 0)
    return render_template("corpfin/model_new.html", clients=clients, engagements=engagements, form=form,
                           presets=[(k, k.title()) for k in E.BUSINESS_PRESETS], currencies=CURRENCIES,
                           default_start=f"{next_year}-01", max_years=E.MAX_YEARS)


@corpfin_bp.route("/models/<int:model_id>/settings", methods=["POST"])
def save_settings(model_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="overview"))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("The model needs a name.", "danger")
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="overview"))
    model.name = name[:200]
    model.purpose = (request.form.get("purpose") or "").strip() or None
    eng = _valid_engagement(model.client_id, request.form.get("engagement_id"))
    model.engagement_id = eng.id if eng else None
    a = E.normalise(model.assumptions)
    old_years = a["years"]
    a["years"] = E.clamp_years(request.form.get("years"), old_years)
    a["start_month"] = E.parse_start_month(request.form.get("start_month") or a["start_month"]).isoformat()[:7]
    a["currency"] = (request.form.get("currency") or a["currency"]).strip().upper()[:6] or a["currency"]
    a = E.normalise(a)  # resizes every yearly list to the new number of years
    model.set_json("assumptions", a)
    _touch(model)
    db.session.commit()
    if a["years"] != old_years:
        flash(f"Forecast length changed from {old_years} to {a['years']} year(s). Check the new years on the Assumptions tab "
              "(added years copy the last year's drivers).", "success")
    else:
        flash("Model details saved.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="overview"))


@corpfin_bp.route("/models/<int:model_id>/finalise", methods=["POST"])
def finalise_model(model_id):
    model = _model_or_404(model_id)
    if not _is_signoff_role():
        abort(403, description="Only a Partner or Admin can finalise a model.")
    result = _run(model)
    if not result["checks"]["balance_ok"]:
        flash("The balance sheet does not balance, so the model cannot be finalised. Check the Statements tab.", "danger")
    else:
        model.status = "Final"
        model.finalised_by_id = current_user.id
        model.finalised_at = datetime.utcnow()
        _touch(model)
        db.session.commit()
        flash("Model finalised and locked.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="overview"))


@corpfin_bp.route("/models/<int:model_id>/reopen", methods=["POST"])
def reopen_model(model_id):
    model = _model_or_404(model_id)
    if not _is_signoff_role():
        abort(403, description="Only a Partner or Admin can reopen a model.")
    model.status = "Draft"
    model.finalised_by_id = None
    model.finalised_at = None
    _touch(model)
    db.session.commit()
    flash("Model reopened for editing.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="overview"))


@corpfin_bp.route("/models/<int:model_id>/duplicate", methods=["POST"])
def duplicate_model(model_id):
    src = _model_or_404(model_id)

    def make():
        return CFModel(model_number=_next_model_number(), client_id=src.client_id, engagement_id=src.engagement_id,
                       name=(src.name + " (copy)")[:200], purpose=src.purpose, status="Draft",
                       assumptions_json=src.assumptions_json, historical_json=src.historical_json,
                       actuals_json=None, created_by_id=current_user.id, updated_by_id=current_user.id)
    new = _commit_with_number(make)
    for s in src.scenarios:
        db.session.add(CFScenario(model_id=new.id, name=s.name, is_base=s.is_base, sort_order=s.sort_order,
                                  shocks_json=s.shocks_json, notes=s.notes))
    db.session.commit()
    flash(f"Copied to {new.model_number}. Valuations and actuals are not copied.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=new.id, tab="overview"))


@corpfin_bp.route("/models/<int:model_id>/delete", methods=["POST"])
def delete_model(model_id):
    model = _model_or_404(model_id)
    if model.is_final:
        flash("A Final model cannot be deleted. Reopen it first.", "danger")
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="overview"))
    if not _is_signoff_role() and model.created_by_id != current_user.id:
        abort(403, description="Only the person who created a model, or a Partner / Admin, can delete it.")
    CFProject.query.filter_by(source_model_id=model.id).update({"source_model_id": None})
    number = model.model_number
    db.session.delete(model)
    db.session.commit()
    flash(f"Model {number} deleted.", "success")
    return redirect(url_for("corpfin.dashboard"))


# =========================================================================== model tabs

@corpfin_bp.route("/models/<int:model_id>")
def model_home(model_id):
    return redirect(url_for("corpfin.model_tab", model_id=model_id, tab="overview"))


def _chart_payload(result):
    ann = result["annual"]
    first12 = result["periods"][:12]
    return {
        "currency": result["a"]["currency"],
        "years": [d["label"] for d in ann],
        "revenue": [d["revenue"] for d in ann], "ebitda": [d["ebitda"] for d in ann], "pat": [d["pat"] for d in ann],
        "cash": [d["cash"] for d in ann], "borrowings": [d["borrowings"] for d in ann], "equity": [d["total_equity"] for d in ann],
        "cfo": [d["cfo"] for d in ann], "cfi": [d["cfi"] for d in ann], "cff": [d["cff"] for d in ann],
        "months": [r["label"] for r in first12], "month_cash": [r["cash"] for r in first12],
        "month_revenue": [r["revenue"] for r in first12], "month_ebitda": [r["ebitda"] for r in first12],
    }


@corpfin_bp.route("/models/<int:model_id>/<tab>")
def model_tab(model_id, tab):
    model = _model_or_404(model_id)
    if tab not in dict(MODEL_TABS):
        abort(404)
    scenario = _pick_scenario(model)
    result = _run(model, scenario)
    a = result["a"]
    ctx = dict(model=model, tab=tab, scenario=scenario, scenarios=model.scenarios, result=result, a=a,
               cur=a["currency"], engagement=model.engagement)
    if tab == "overview":
        ctx.update(head=R.headline(result), charts=_chart_payload(result), ratio_rows=E.ratios(result))
        ctx["engagements"] = _engagement_choices([model.client])
        ctx["currencies"] = CURRENCIES
        ctx["max_years"] = E.MAX_YEARS
    elif tab == "assumptions":
        ctx.update(base=E.normalise(model.assumptions), shock_fields=E.SHOCK_FIELDS, years_idx=list(range(a["years"])),
                   year_labels=[E.year_label(a, y) for y in range(1, a["years"] + 1)],
                   month_labels=E.MONTH_ABBR, opening_reserves=E.opening_reserves(a), presets=list(E.BUSINESS_PRESETS))
        ctx["scenario"] = _base_scenario(model)  # assumptions are always edited on the base case
    elif tab == "statements":
        view = "monthly" if request.args.get("view") == "monthly" else "annual"
        ctx.update(view=view, tables=R.statement_tables(result, view), charts=_chart_payload(result))
    elif tab == "loans":
        rt = E.ratios(result)
        totals = [{key: sum(d["loans"][k][key] for d in result["annual"]) for key in ("draw", "repay", "interest")}
                  for k in range(len(a["loans"]))]
        ctx.update(tests=E.covenant_tests(result, rt), ratio_rows=rt, loan_totals=totals)
    elif tab == "ratios":
        ctx.update(rtable=R.ratio_table(result), be=E.break_even(result), fcf=E.free_cash_flows(result))
    elif tab == "scenarios":
        ctx.update(_scenario_context(model, result))
    elif tab == "valuation":
        ctx.update(_valuation_context(model, result, scenario))
    elif tab == "bva":
        bva = T.budget_vs_actual(result, model.actuals)
        actuals = T.normalise_actuals(model.actuals)
        ctx.update(bva=bva, actuals=actuals, input_keys=T.BVA_INPUT_KEYS, periods=result["periods"],
                   input_labels=dict(zip(T.BVA_INPUT_KEYS, ["Revenue", "Cost of sales", "Operating expenses", "Depreciation",
                                                              "Net finance cost", "Tax", "Closing cash"])))
    elif tab == "history":
        hist = T.normalise_hist(model.historical)
        ctx.update(hist=hist, analysis=T.historical_analysis(hist), hist_is=T.HIST_IS, hist_bs=T.HIST_BS,
                   suggestion=T.suggest_assumptions(hist, a["years"]))
    return render_template(f"corpfin/tab_{tab}.html", **ctx)


# --------------------------------------------------------------------------- assumptions

def _parse_assumptions(form, base):
    """Build assumptions from the big assumptions form. Fields missing from the form keep their current value."""
    a = copy.deepcopy(base)
    n = a["years"]
    g = lambda k, d=0.0: E.f(form.get(k), d)

    def vec(prefix, current):
        return [g(f"{prefix}__{y}", current[y]) if f"{prefix}__{y}" in form else current[y] for y in range(n)]

    if "tax_rate" in form:
        a["tax_rate"] = g("tax_rate", a["tax_rate"])
    if "dep_rate" in form:
        a["dep_rate"] = g("dep_rate", a["dep_rate"])
    for k in ("deposit_rate", "overdraft_rate"):
        if k in form:
            a[k] = g(k, a[k])
    if "season__0" in form:
        a["seasonality"] = [g(f"season__{m}", a["seasonality"][m]) for m in range(12)]

    streams = []
    count = int(g("n_rev", len(a["revenue"])))
    for i in range(count):
        if f"rev_name_{i}" not in form:
            continue
        cur = a["revenue"][i] if i < len(a["revenue"]) else {"growth": [0.0] * n}
        streams.append({"name": form.get(f"rev_name_{i}", "Revenue"), "y1": g(f"rev_y1_{i}"),
                        "growth": [0.0] + [g(f"rev_g_{i}__{y}", cur["growth"][y] if y < len(cur["growth"]) else 0.0) for y in range(1, n)]})
    if streams:
        a["revenue"] = streams

    a["cogs_pct"] = vec("cogs", a["cogs_pct"])

    lines = []
    count = int(g("n_ox", len(a["opex"])))
    for j in range(count):
        if f"ox_name_{j}" not in form:
            continue
        cur = a["opex"][j] if j < len(a["opex"]) else {"growth": [0.0] * n, "pct": [0.0] * n}
        lines.append({"name": form.get(f"ox_name_{j}", "Expense"), "kind": "pct" if form.get(f"ox_kind_{j}") == "pct" else "fixed",
                      "y1": g(f"ox_y1_{j}"), "creditors": f"ox_cred_{j}" in form,
                      "growth": [0.0] + [g(f"ox_g_{j}__{y}", cur["growth"][y] if y < len(cur["growth"]) else 0.0) for y in range(1, n)],
                      "pct": [g(f"ox_p_{j}__{y}", cur["pct"][y] if y < len(cur["pct"]) else 0.0) for y in range(n)]})
    if lines or "n_ox" in form:
        a["opex"] = lines

    a["capex"] = vec("capex", a["capex"])
    a["rec_days"] = vec("rec", a["rec_days"])
    a["inv_days"] = vec("inv", a["inv_days"])
    a["pay_days"] = vec("pay", a["pay_days"])
    a["equity_issue"] = vec("eq", a["equity_issue"])
    a["dividend_payout"] = vec("div", a["dividend_payout"])

    for k in list(a["opening"]):
        if f"op_{k}" in form:
            a["opening"][k] = g(f"op_{k}", a["opening"][k])

    loans = []
    count = int(g("n_ln", len(a["loans"])))
    for k in range(count):
        if f"ln_name_{k}" not in form:
            continue
        loans.append({"name": form.get(f"ln_name_{k}", "Loan"), "opening_balance": g(f"ln_open_{k}"),
                      "drawdown_amount": g(f"ln_draw_{k}"), "drawdown_year": g(f"ln_dyear_{k}", 1),
                      "rate": g(f"ln_rate_{k}", 12.0), "repay_type": form.get(f"ln_type_{k}", "straight"),
                      "repay_years": g(f"ln_ryears_{k}", 5), "grace_years": g(f"ln_grace_{k}", 0),
                      "maturity_year": g(f"ln_mat_{k}", n)})
    if loans or "n_ln" in form:
        a["loans"] = loans

    for k in list(a["covenants"]):
        if f"cov_{k}" in form:
            raw = (form.get(f"cov_{k}") or "").strip()
            a["covenants"][k] = None if raw == "" else E.f(raw)
    return a


def _apply_line_action(a, action):
    """add_rev / add_ox / add_ln / del_rev:i / del_ox:i / del_ln:i"""
    n = a["years"]
    verb, _, idx = action.partition(":")
    try:
        idx = int(idx)
    except ValueError:
        idx = -1
    msg = None
    if verb == "add_rev":
        last_g = a["revenue"][-1]["growth"][-1] if a["revenue"] else 5.0
        a["revenue"].append({"name": "New revenue stream", "y1": 0.0, "growth": [0.0] + [last_g] * (n - 1)})
        msg = "Revenue stream added."
    elif verb == "del_rev" and 0 <= idx < len(a["revenue"]) and len(a["revenue"]) > 1:
        a["revenue"].pop(idx)
        msg = "Revenue stream removed."
    elif verb == "add_ox":
        a["opex"].append({"name": "New expense", "kind": "fixed", "y1": 0.0, "growth": [0.0] + [5.0] * (n - 1),
                          "pct": [0.0] * n, "creditors": True})
        msg = "Expense line added."
    elif verb == "del_ox" and 0 <= idx < len(a["opex"]):
        a["opex"].pop(idx)
        msg = "Expense line removed."
    elif verb == "add_ln":
        a["loans"].append({"name": "New loan", "opening_balance": 0.0, "drawdown_amount": 100000.0, "drawdown_year": 1, "rate": 12.0,
                           "repay_type": "straight", "repay_years": min(5, n) or 5, "grace_years": 0, "maturity_year": n})
        msg = "Loan added."
    elif verb == "del_ln" and 0 <= idx < len(a["loans"]):
        a["loans"].pop(idx)
        msg = "Loan removed."
    return msg


@corpfin_bp.route("/models/<int:model_id>/assumptions", methods=["POST"])
def save_assumptions(model_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="assumptions"))
    base = E.normalise(model.assumptions)
    a = _parse_assumptions(request.form, base)
    a = E.normalise(a)
    action = request.form.get("action", "save")
    msg = _apply_line_action(a, action) if action != "save" else None
    a = E.normalise(a)
    model.set_json("assumptions", a)
    _touch(model)
    db.session.commit()
    res = E.run(a)
    if msg:
        flash(msg + " Other changes on the page were saved too.", "success")
    else:
        flash("Assumptions saved.", "success")
    for w in res["warnings"]:
        flash(w, "warning")
    if not res["checks"]["balance_ok"]:
        flash(f"The balance sheet is out by {res['checks']['max_imbalance']:,.2f}. Please report this - the model should always balance.", "danger")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="assumptions") + ("#lines" if msg else ""))


# --------------------------------------------------------------------------- scenarios

def _scenario_context(model, base_result):
    base_sc = _base_scenario(model)
    rows = []
    a = E.normalise(model.assumptions)
    for s in model.scenarios:
        res = E.run(E.apply_shocks(a, s.shocks if not s.is_base else {}))
        rows.append({"scenario": s, "summary": E.scenario_summary(res), "result": res})
    sx = request.args.get("sx") if request.args.get("sx") in SENS_STEPS else "revenue_pct"
    sy = request.args.get("sy") if request.args.get("sy") in SENS_STEPS else "cogs_pp"
    if sy == sx:
        sy = "cogs_pp" if sx != "cogs_pp" else "revenue_pct"
    metric = request.args.get("sm") if request.args.get("sm") in dict(SENS_METRICS) else "cash_last"
    chosen = _pick_scenario(model, request.args.get("scenario"))
    shocks = chosen.shocks if not chosen.is_base else {}
    xs, ys = SENS_STEPS[sx], SENS_STEPS[sy]
    grid = E.sensitivity_grid(a, sx, xs, sy, ys, metric, shocks=shocks)
    labels = {k: lab for k, lab, _ in E.SHOCK_FIELDS}
    return {"scen_rows": rows, "base_sc": base_sc, "shock_fields": E.SHOCK_FIELDS, "sens": {
        "sx": sx, "sy": sy, "metric": metric, "xs": xs, "ys": ys, "grid": grid, "x_label": labels[sx], "y_label": labels[sy],
        "metric_label": dict(SENS_METRICS)[metric]}, "sens_fields": [(k, labels[k]) for k in SENS_STEPS], "sens_metrics": SENS_METRICS,
        "chosen": chosen}


@corpfin_bp.route("/models/<int:model_id>/scenarios/save", methods=["POST"])
def save_scenario(model_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="scenarios"))
    sid = request.form.get("scenario_id")
    name = (request.form.get("name") or "").strip()[:100]
    shocks = {k: E.f(request.form.get(f"s_{k}")) for k, _, _ in E.SHOCK_FIELDS}
    if sid:
        sc = next((s for s in model.scenarios if str(s.id) == sid), None)
        if sc is None:
            abort(404)
        if not sc.is_base:
            if not name:
                flash("A scenario needs a name.", "danger")
                return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="scenarios"))
            sc.name = name
            sc.shocks_json = _dumps(shocks)
        sc.notes = (request.form.get("notes") or "").strip() or None
        flash("Scenario saved.", "success")
    else:
        if not name:
            flash("Give the new scenario a name.", "danger")
            return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="scenarios"))
        if len(model.scenarios) >= 12:
            flash("A model can have up to 12 scenarios.", "danger")
            return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="scenarios"))
        db.session.add(CFScenario(model_id=model.id, name=name, is_base=False, sort_order=len(model.scenarios),
                                  shocks_json=_dumps(shocks), notes=(request.form.get("notes") or "").strip() or None))
        flash("Scenario added.", "success")
    _touch(model)
    db.session.commit()
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="scenarios"))


@corpfin_bp.route("/models/<int:model_id>/scenarios/<int:sid>/delete", methods=["POST"])
def delete_scenario(model_id, sid):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="scenarios"))
    sc = next((s for s in model.scenarios if s.id == sid), None)
    if sc is None:
        abort(404)
    if sc.is_base:
        flash("The Base case cannot be deleted.", "danger")
    else:
        CFValuation.query.filter_by(scenario_id=sc.id).update({"scenario_id": None})
        db.session.delete(sc)
        _touch(model)
        db.session.commit()
        flash("Scenario deleted.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="scenarios"))


def _dumps(obj):
    import json
    return json.dumps(obj)


# --------------------------------------------------------------------------- valuation

def _valuation_context(model, result, scenario):
    vals = model.valuations
    chosen = None
    raw = request.args.get("val")
    if raw and raw != "new":
        try:
            chosen = next((v for v in vals if v.id == int(raw)), None)
        except ValueError:
            chosen = None
    elif not raw and vals:
        chosen = vals[-1]
    ctx = {"valuations": vals, "val_obj": chosen, "creating": chosen is None}
    if chosen is not None:
        # a saved valuation is run under the scenario it was saved with, unless one is chosen in the URL
        sc = scenario
        if not request.args.get("scenario") and chosen.scenario_id:
            sc = next((s for s in model.scenarios if s.id == chosen.scenario_id), scenario)
        res = _run(model, sc)
        v = T.normalise_valuation(chosen.inputs)
        d = T.dcf(res, v)
        ctx.update(scenario=sc, result=res, a=res["a"], v=v, dcf=d, wacc=T.wacc(dict(v["wacc"], tax=res["a"]["tax_rate"])),
                   sens=T.dcf_sensitivity(res, v), mult=T.multiples_valuation(res, v), ff=_football(res, v))
    else:
        ctx.update(v=T.normalise_valuation(None), wacc=None)
    return ctx


def _football(res, v):
    rows = T.football_field(res, v)
    lo = min([r["low"] for r in rows] + [0.0])
    hi = max([r["high"] for r in rows] + [1.0])
    span = (hi - lo) or 1.0
    for r in rows:
        r["left"] = (r["low"] - lo) / span * 100.0
        r["width"] = max(0.8, (r["high"] - r["low"]) / span * 100.0)
        r["mid_at"] = (r["mid"] - lo) / span * 100.0
    return rows


def _parse_valuation(form):
    names = form.getlist("comp_name")
    comps = []
    for i, nm in enumerate(names):
        def col(key):
            vals = form.getlist(key)
            return vals[i] if i < len(vals) else ""
        comps.append({"name": nm, "ev_ebitda": col("comp_evebitda"), "ev_revenue": col("comp_evrev"), "pe": col("comp_pe")})
    raw = {
        "wacc": {k: form.get(f"w_{k}") for k in ("rf", "erp", "beta", "crp", "size", "specific", "kd", "debt_weight", "override")},
        "terminal": {"method": form.get("t_method"), "growth": form.get("t_growth"), "exit_multiple": form.get("t_exit")},
        "mid_year": "mid_year" in form,
        "bridge": {"debt": form.get("b_debt"), "cash": form.get("b_cash"), "other": form.get("b_other"), "minorities": form.get("b_min")},
        "dlom": form.get("dlom"), "stake_pct": form.get("stake_pct"), "comparables": comps,
        "ltm": {"revenue": form.get("ltm_revenue"), "ebitda": form.get("ltm_ebitda"), "pat": form.get("ltm_pat")},
        "notes": form.get("notes"),
    }
    return T.normalise_valuation(raw)


@corpfin_bp.route("/models/<int:model_id>/valuations/save", methods=["POST"])
def save_valuation(model_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="valuation"))
    form = request.form
    add_comp = form.get("action") == "add_comp"
    inputs = _parse_valuation(form)
    if add_comp:
        inputs["comparables"].append({"name": "New comparable", "ev_ebitda": None, "ev_revenue": None, "pe": None})
    name = (form.get("name") or "").strip()[:200] or "Valuation"
    try:
        vdate = datetime.strptime(form.get("valuation_date") or "", "%Y-%m-%d").date()
    except ValueError:
        vdate = date.today()
    sc = next((s for s in model.scenarios if str(s.id) == form.get("scenario_id")), None)
    vid = form.get("valuation_id")
    obj = next((v for v in model.valuations if str(v.id) == vid), None) if vid else None
    if obj is None:
        obj = CFValuation(model_id=model.id, created_by_id=current_user.id)
        db.session.add(obj)
    obj.name = name
    obj.valuation_date = vdate
    obj.scenario_id = sc.id if sc and not sc.is_base else None
    obj.inputs_json = _dumps(inputs)
    _touch(model)
    db.session.commit()
    flash("Valuation saved." if not add_comp else "Comparable row added - fill it in and save.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="valuation", val=obj.id))


@corpfin_bp.route("/models/<int:model_id>/valuations/<int:val_id>/delete", methods=["POST"])
def delete_valuation(model_id, val_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="valuation"))
    obj = next((v for v in model.valuations if v.id == val_id), None)
    if obj is None:
        abort(404)
    db.session.delete(obj)
    db.session.commit()
    flash("Valuation deleted.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="valuation"))


# --------------------------------------------------------------------------- budget vs actual & history

@corpfin_bp.route("/models/<int:model_id>/actuals", methods=["POST"])
def save_actuals(model_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="bva"))
    result = _run(model)
    raw = {}
    for r in result["periods"]:
        key = f"p{r['idx']}"
        raw[key] = {k: request.form.get(f"{key}__{k}") for k in T.BVA_INPUT_KEYS}
    actuals = T.normalise_actuals(raw)
    model.set_json("actuals", actuals)
    _touch(model)
    db.session.commit()
    flash(f"Actuals saved for {len(actuals)} period(s).", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="bva") + "#variance")


@corpfin_bp.route("/models/<int:model_id>/history", methods=["POST"])
def save_history(model_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="history"))
    form = request.form
    years = [form.get(f"year__{i}", "").strip() for i in range(6)]
    years = [y for y in years if y]
    n = len(years)
    h = {"years": years, "is": {}, "bs": {}}
    # inputs are posted by column position; a blank year heading drops that column
    keep = [i for i in range(6) if form.get(f"year__{i}", "").strip()]
    for k, _ in T.HIST_IS:
        h["is"][k] = [E.f(form.get(f"is_{k}__{i}")) for i in keep]
    for k, _ in T.HIST_BS:
        h["bs"][k] = [E.f(form.get(f"bs_{k}__{i}")) for i in keep]
    model.set_json("historical", T.normalise_hist(h))
    _touch(model)
    db.session.commit()
    flash(f"Historical figures saved ({n} year(s)).", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="history") + "#analysis")


@corpfin_bp.route("/models/<int:model_id>/history/apply", methods=["POST"])
def apply_history(model_id):
    model = _model_or_404(model_id)
    if _locked(model):
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="history"))
    a = E.normalise(model.assumptions)
    sug = T.suggest_assumptions(model.historical, a["years"])
    if not sug:
        flash("Enter at least one year of history with revenue first.", "danger")
        return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="history"))
    a.update(sug)
    model.set_json("assumptions", E.normalise(a))
    _touch(model)
    db.session.commit()
    flash("Starting assumptions taken from the history (revenue, cost of sales, expenses, working-capital days, opening balance sheet "
          "and existing borrowings). Review each one on the Assumptions tab.", "success")
    return redirect(url_for("corpfin.model_tab", model_id=model.id, tab="assumptions"))


# =========================================================================== exports

def _meta(model):
    return {"name": model.name, "client": model.client.name, "number": model.model_number, "status": model.status,
            "prepared_by": (model.updated_by.name if model.updated_by else (model.created_by.name if model.created_by else "")),
            "currency": model.currency}


def _safe_name(text):
    return "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in text).strip().replace(" ", "_")[:60] or "model"


@corpfin_bp.route("/models/<int:model_id>/excel")
def export_excel(model_id):
    import cf_excel
    model = _model_or_404(model_id)
    a = E.normalise(model.assumptions)
    val = None
    if model.valuations:
        chosen = None
        try:
            chosen = next((v for v in model.valuations if v.id == int(request.args.get("val", 0))), None)
        except ValueError:
            pass
        val = (chosen or model.valuations[-1]).inputs
    scen = []
    for s in model.scenarios:
        res = E.run(E.apply_shocks(a, s.shocks if not s.is_base else {}))
        scen.append({"name": s.name, "summary": E.scenario_summary(res)})
    labels = {k: lab for k, lab, _ in E.SHOCK_FIELDS}
    sens = []
    for sx, sy, metric in (("revenue_pct", "cogs_pp", "cash_last"), ("revenue_pct", "opex_pct", "pat_total")):
        xs, ys = SENS_STEPS[sx], SENS_STEPS[sy]
        sens.append({"title": f"{dict(SENS_METRICS)[metric]}: {labels[sx]} vs {labels[sy]}", "x_label": labels[sx], "y_label": labels[sy],
                     "x_values": xs, "y_values": ys, "rows": E.sensitivity_grid(a, sx, xs, sy, ys, metric)})
    data, _ = cf_excel.build_model_workbook(_meta(model), a, valuation=val, scenarios=scen if len(scen) > 1 else None,
                                            sensitivity=sens, actuals=T.normalise_actuals(model.actuals) or None)
    fn = f"{model.model_number}_{_safe_name(model.name)}.xlsx"
    return Response(data, mimetype=XLSX_MIME, headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@corpfin_bp.route("/models/<int:model_id>/pdf")
def export_pdf(model_id):
    import cf_pdf
    model = _model_or_404(model_id)
    scenario = _pick_scenario(model)
    result = _run(model, scenario)
    val_obj = None
    try:
        val_obj = next((v for v in model.valuations if v.id == int(request.args.get("val", 0))), None)
    except ValueError:
        pass
    if val_obj is None and model.valuations:
        val_obj = model.valuations[-1]
    valuation = None
    if val_obj is not None:
        v = T.normalise_valuation(val_obj.inputs)
        valuation = {"obj": val_obj, "v": v, "dcf": T.dcf(result, v), "mult": T.multiples_valuation(result, v), "ff": T.football_field(result, v),
                     "wacc": T.wacc(dict(v["wacc"], tax=result["a"]["tax_rate"]))}
    a = E.normalise(model.assumptions)
    scen = []
    if len(model.scenarios) > 1:
        for s in model.scenarios:
            res = E.run(E.apply_shocks(a, s.shocks if not s.is_base else {}))
            scen.append({"name": s.name, "notes": s.notes, "summary": E.scenario_summary(res)})
    data = cf_pdf.build_model_pdf(model, scenario, result, FIRM_LETTERHEAD, valuation=valuation, scenarios=scen,
                                  bva=T.budget_vs_actual(result, model.actuals))
    fn = f"{model.model_number}_{_safe_name(model.name)}.pdf"
    return Response(data, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{fn}"'})


# =========================================================================== projects (capital budgeting / feasibility)

def _project_inputs_from_form(form, current):
    flows = []
    count = int(E.f(form.get("n_flows"), len(current["cash_flows"])))
    for i in range(count):
        if f"cf_{i}" in form:
            flows.append(E.f(form.get(f"cf_{i}")))
    return {"currency": (form.get("currency") or current["currency"]).strip().upper()[:6] or "USD",
            "discount_rate": E.f(form.get("discount_rate"), current["discount_rate"]),
            "reinvest_rate": E.f(form.get("reinvest_rate"), current["reinvest_rate"]),
            "initial": E.f(form.get("initial"), current["initial"]), "residual": E.f(form.get("residual"), current["residual"]),
            "cash_flows": flows or current["cash_flows"]}


@corpfin_bp.route("/projects/new", methods=["GET", "POST"])
def new_project():
    clients = visible_clients(current_user)
    engagements = _engagement_choices(clients)
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        try:
            client = Client.query.get(int(form.get("client_id")))
        except (TypeError, ValueError):
            client = None
        name = (form.get("name") or "").strip()
        if client is None or not user_can_access_client(current_user, client):
            flash("Choose the client this study is for.", "danger")
        elif not name:
            flash("Give the study a name.", "danger")
        else:
            eng = _valid_engagement(client.id, form.get("engagement_id"))
            inputs = T.normalise_project(dict(T.DEFAULT_PROJECT, currency=(form.get("currency") or "USD").upper()[:6]))

            def make():
                return CFProject(project_number=_next_project_number(), client_id=client.id, engagement_id=eng.id if eng else None,
                                 name=name[:200], status="Draft", created_by_id=current_user.id, updated_by_id=current_user.id)
            proj = _commit_with_number(make)
            proj.inputs_json = _dumps(inputs)
            proj.narrative_json = _dumps({})
            db.session.commit()
            flash(f"Study {proj.project_number} created with example figures - replace them with the client's.", "success")
            return redirect(url_for("corpfin.view_project", project_id=proj.id))
    return render_template("corpfin/project_new.html", clients=clients, engagements=engagements, form=form, currencies=CURRENCIES)


@corpfin_bp.route("/models/<int:model_id>/to-project", methods=["POST"])
def project_from_model(model_id):
    model = _model_or_404(model_id)
    result = _run(model, _pick_scenario(model, request.form.get("scenario")))
    flows = T.project_from_model(result)
    inputs = T.normalise_project({"currency": model.currency, "discount_rate": 15.0, "reinvest_rate": 10.0, "initial": 0.0,
                                  "residual": 0.0, "cash_flows": flows})

    def make():
        return CFProject(project_number=_next_project_number(), client_id=model.client_id, engagement_id=model.engagement_id,
                         name=f"Investment appraisal - {model.name}"[:200], status="Draft", source_model_id=model.id,
                         created_by_id=current_user.id, updated_by_id=current_user.id)
    proj = _commit_with_number(make)
    proj.inputs_json = _dumps(inputs)
    proj.narrative_json = _dumps({})
    db.session.commit()
    flash("Study created from the model's free cash flows. Enter the investment amount (year 0) and the discount rate.", "success")
    return redirect(url_for("corpfin.view_project", project_id=proj.id))


@corpfin_bp.route("/projects/<int:project_id>")
def view_project(project_id):
    proj = _project_or_404(project_id)
    inputs = T.normalise_project(proj.inputs)
    an = T.project_analysis(inputs)
    payload = {"currency": inputs["currency"], "labels": [f"Year {r['year']}" for r in an["rows"]],
               "cum": [r["cumulative"] for r in an["rows"]], "cum_pv": [r["cum_pv"] for r in an["rows"]],
               "flows": [r["cash_flow"] for r in an["rows"]]}
    return render_template("corpfin/project.html", proj=proj, inputs=inputs, an=an, sens=T.project_sensitivity(inputs),
                           sections=T.FEASIBILITY_SECTIONS, narrative=proj.narrative, charts=payload, currencies=CURRENCIES,
                           engagements=_engagement_choices([proj.client]))


@corpfin_bp.route("/projects/<int:project_id>/save", methods=["POST"])
def save_project(project_id):
    proj = _project_or_404(project_id)
    if proj.status == "Final":
        flash("This study is Final and locked. A Partner can reopen it.", "danger")
        return redirect(url_for("corpfin.view_project", project_id=proj.id))
    form = request.form
    current = T.normalise_project(proj.inputs)
    inputs = _project_inputs_from_form(form, current)
    action = form.get("action", "save")
    if action == "add_year":
        inputs["cash_flows"].append(inputs["cash_flows"][-1] if inputs["cash_flows"] else 0.0)
        inputs["cash_flows"] = inputs["cash_flows"][:20]
    elif action.startswith("del_year:"):
        try:
            idx = int(action.split(":")[1])
            if len(inputs["cash_flows"]) > 1:
                inputs["cash_flows"].pop(idx)
        except (ValueError, IndexError):
            pass
    proj.inputs_json = _dumps(T.normalise_project(inputs))
    if "name" in form and form.get("name", "").strip():
        proj.name = form.get("name").strip()[:200]
    if "engagement_id" in form:
        eng = _valid_engagement(proj.client_id, form.get("engagement_id"))
        proj.engagement_id = eng.id if eng else None
    narrative = proj.narrative
    for key, _ in T.FEASIBILITY_SECTIONS:
        if f"nar_{key}" in form:
            narrative[key] = (form.get(f"nar_{key}") or "").strip()[:8000]
    proj.narrative_json = _dumps(narrative)
    proj.updated_by_id = current_user.id
    proj.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Study saved.", "success")
    return redirect(url_for("corpfin.view_project", project_id=proj.id) + ("#flows" if action != "save" else ""))


@corpfin_bp.route("/projects/<int:project_id>/status", methods=["POST"])
def project_status(project_id):
    proj = _project_or_404(project_id)
    if not _is_signoff_role():
        abort(403, description="Only a Partner or Admin can finalise or reopen a study.")
    proj.status = "Draft" if proj.status == "Final" else "Final"
    db.session.commit()
    flash(f"Study marked {proj.status}.", "success")
    return redirect(url_for("corpfin.view_project", project_id=proj.id))


@corpfin_bp.route("/projects/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    proj = _project_or_404(project_id)
    if proj.status == "Final":
        flash("A Final study cannot be deleted. Reopen it first.", "danger")
        return redirect(url_for("corpfin.view_project", project_id=proj.id))
    if not _is_signoff_role() and proj.created_by_id != current_user.id:
        abort(403, description="Only the person who created a study, or a Partner / Admin, can delete it.")
    number = proj.project_number
    db.session.delete(proj)
    db.session.commit()
    flash(f"Study {number} deleted.", "success")
    return redirect(url_for("corpfin.dashboard"))


@corpfin_bp.route("/projects/<int:project_id>/excel")
def project_excel(project_id):
    import cf_excel
    proj = _project_or_404(project_id)
    meta = {"name": proj.name, "client": proj.client.name, "number": proj.project_number, "status": proj.status,
            "prepared_by": proj.updated_by.name if proj.updated_by else "", "currency": T.normalise_project(proj.inputs)["currency"]}
    data = cf_excel.build_project_workbook(meta, proj.inputs, proj.narrative)
    fn = f"{proj.project_number}_{_safe_name(proj.name)}.xlsx"
    return Response(data if isinstance(data, (bytes, bytearray)) else data[0], mimetype=XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@corpfin_bp.route("/projects/<int:project_id>/pdf")
def project_pdf(project_id):
    import cf_pdf
    proj = _project_or_404(project_id)
    inputs = T.normalise_project(proj.inputs)
    data = cf_pdf.build_project_pdf(proj, inputs, T.project_analysis(inputs), T.project_sensitivity(inputs), proj.narrative, FIRM_LETTERHEAD)
    fn = f"{proj.project_number}_{_safe_name(proj.name)}.pdf"
    return Response(data, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{fn}"'})


# =========================================================================== loan calculator

LOAN_TYPES = [("annuity", "Equal instalments (annuity)"), ("straight", "Equal capital repayments"), ("bullet", "Interest only, capital at the end")]


def _loan_args(src):
    return {
        "principal": max(0.0, E.f(src.get("principal"), 100000.0)),
        "rate": max(0.0, E.f(src.get("rate"), 12.0)),
        "months": max(1, min(360, int(E.f(src.get("months"), 60)))),
        "repay_type": src.get("repay_type") if src.get("repay_type") in dict(LOAN_TYPES) else "annuity",
        "grace_months": max(0, int(E.f(src.get("grace_months"), 0))),
        "balloon_pct": max(0.0, min(100.0, E.f(src.get("balloon_pct"), 0.0))),
        "start": src.get("start") or date.today().strftime("%Y-%m"),
        "currency": (src.get("currency") or "USD").upper()[:6],
    }


@corpfin_bp.route("/tools/loan")
def loan_calculator():
    args = _loan_args(request.args)
    calc = None
    if request.args.get("principal"):
        sched = E.loan_schedule(args["principal"], args["rate"], args["months"], args["repay_type"], args["grace_months"], args["start"],
                                args["balloon_pct"] if args["repay_type"] == "annuity" else 0.0)
        calc = {"sched": sched, "annual": E.loan_schedule_annual(sched["rows"])}
    return render_template("corpfin/loan_calculator.html", args=args, calc=calc, loan_types=LOAN_TYPES, currencies=CURRENCIES,
                           query=request.query_string.decode())


@corpfin_bp.route("/tools/loan/excel")
def loan_excel():
    import cf_excel
    args = _loan_args(request.args)
    meta = {"name": "Loan amortisation schedule", "client": "", "number": "", "status": "", "prepared_by": current_user.name,
            "currency": args["currency"]}
    data = cf_excel.build_loan_workbook(meta, args["principal"], args["rate"], args["months"], args["repay_type"], args["grace_months"],
                                        args["balloon_pct"] if args["repay_type"] == "annuity" else 0.0, args["start"])
    return Response(data, mimetype=XLSX_MIME, headers={"Content-Disposition": 'attachment; filename="Loan_schedule.xlsx"'})
