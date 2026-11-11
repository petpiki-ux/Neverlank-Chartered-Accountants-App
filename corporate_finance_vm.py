"""Valuation Models - group / sum-of-the-parts valuations inside Corporate Finance & Business Advisory.

A VALUATION MODEL consolidates COMPONENT valuations (a subsidiary, associate, business unit or asset) into one group value: each component is
brought in by upload (Excel template, any Excel valuation workbook, Word / PDF report - read by Claude when an API key is set, otherwise a built-in
reader - always shown for checking first), taken from a forecast model's saved valuation or a valuation certificate already in the app, or typed.
The consolidation applies ownership %, minority / marketability / control adjustments, currency conversion, holding-company items and eliminations,
and group adjustments (cf_valmodel.py); the report goes out as live-formula Excel, PDF and Word on the firm letterhead, can be signed by the
Managing Partner, and is filed in the engagement's Working Papers.

This file holds the web layer. It is imported at the bottom of corporate_finance.py so its routes join the same blueprint (same permission gate).
"""
import json
import os
import re
import uuid
from datetime import date, datetime

from flask import render_template, redirect, url_for, request, flash, abort, Response, current_app
from flask_login import current_user
from werkzeug.utils import secure_filename

import cf_valmodel as V
import cf_valimport as VI
from extensions import db
from invoicing import next_sequence_number
from models import (Client, Engagement, CFModel, CFValuationModel, Document, ValuationCertificate, user_can_access_client,
                    user_can_access_engagement, visible_clients)
from corporate_finance import (corpfin_bp, _guard, _can_see, _is_signoff_role, _engagement_choices, _valid_engagement, _commit_with_number,
                               _advisory_engagement_or_404, _acceptance_open, _run, _safe_name, CURRENCIES, XLSX_MIME)

VM_TABS = [("components", "Components"), ("consolidation", "Consolidation"), ("group", "Holding items & group"), ("report", "Settings & report")]
BASIS_OF_VALUE = ["Fair value", "Fair market value", "Market value", "Investment value", "Liquidation value", "Other"]
MAX_UPLOAD = 15 * 1024 * 1024
UPLOAD_EXT = ("xlsx", "xlsm", "docx", "pdf", "csv", "txt")
CATEGORY = "Working Papers"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@corpfin_bp.context_processor
def _vm_ctx():
    return {"VM_TABS": VM_TABS, "VM_TYPES": V.COMPONENT_TYPES, "VM_BASES": V.BASES, "VM_METHODS": V.METHODS, "VM_HOLDING_KINDS": V.HOLDING_KINDS}


@corpfin_bp.app_template_filter("vmnum")
def _vmnum(v):
    """A number for an input box: no thousands separators, no trailing zeros, blank for None."""
    if v is None or v == "":
        return ""
    try:
        return "%.12g" % float(v)
    except (TypeError, ValueError):
        return ""


# =========================================================================== helpers

def _vm_or_404(vm_id):
    return _guard(CFValuationModel.query.get_or_404(vm_id))


def _vm_locked(vm):
    if vm.is_final:
        flash("This valuation model is Final and locked. A Partner can reopen it to make changes.", "danger")
        return True
    return False


def _data(vm):
    return V.normalise(vm.data, vm.currency)


def _save(vm, d):
    vm.data_json = json.dumps(V.normalise(d, vm.currency), default=str)
    vm.updated_by_id = current_user.id
    vm.updated_at = datetime.utcnow()
    db.session.commit()


def _back(vm, tab="components"):
    return redirect(url_for("corpfin.vm_tab", vm_id=vm.id, tab=tab))


def _next_vm_number():
    return next_sequence_number(CFValuationModel.vm_number, f"VM-{date.today().year}-")


def _fn(vm, ext):
    return f"{vm.vm_number}_{_safe_name(vm.name)}.{ext}"


def _bars(res):
    bars = V.football(res)
    lo = min([b["low"] for b in bars] + [0.0])
    hi = max([b["high"] for b in bars] + [1.0])
    span = (hi - lo) or 1.0
    for b in bars:
        b["left"] = (b["low"] - lo) / span * 100.0
        b["width"] = max(0.8, (b["high"] - b["low"]) / span * 100.0)
        b["mid_at"] = (b["mid"] - lo) / span * 100.0
    return bars


def _saved_sources(vm):
    """Forecast-model valuations and valuation certificates the user can bring in as components."""
    models = []
    for m in CFModel.query.filter_by(client_id=vm.client_id).order_by(CFModel.id.desc()).all():
        if _can_see(m) and m.valuations:
            models.append(m)
    certs = []
    for c in ValuationCertificate.query.order_by(ValuationCertificate.id.desc()).all():
        if c.adopted_value is None:
            continue
        if c.client_id == vm.client_id or (c.client_id is None and c.client_name and c.client_name.strip().lower() == vm.client.name.strip().lower()):
            certs.append(c)
    return models, certs


# =========================================================================== list / create

@corpfin_bp.route("/engagements/<int:engagement_id>/valuation-models")
def engagement_valuations(engagement_id):
    eng = _advisory_engagement_or_404(engagement_id)
    items = [v for v in eng.cf_valuation_models if _can_see(v)]
    rows = []
    for v in items:
        try:
            res = V.consolidate(v.data, v.currency, v.valuation_date)
            rows.append({"vm": v, "res": res})
        except Exception:
            current_app.logger.exception("valuation model summary failed")
            rows.append({"vm": v, "res": None})
    return render_template("corpfin/engagement_valuations.html", engagement=eng, rows=rows, unlocked=_acceptance_open(eng))


@corpfin_bp.route("/valuation-models/new", methods=["GET", "POST"])
def vm_new():
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
            flash("Choose the client this valuation is for.", "danger")
        elif not name:
            flash("Give the valuation model a name.", "danger")
        else:
            eng = _valid_engagement(client.id, form.get("engagement_id"))
            cur = (form.get("currency") or "USD").strip().upper()[:6] or "USD"
            vdate = V.parse_date(form.get("valuation_date")) or date.today()
            d = V.normalise({"basis_of_value": form.get("basis_of_value") or "Fair value", "purpose": (form.get("purpose") or "").strip()}, cur)

            def make():
                return CFValuationModel(vm_number=_next_vm_number(), client_id=client.id, engagement_id=eng.id if eng else None, name=name[:200],
                                        currency=cur, valuation_date=vdate, status="Draft", created_by_id=current_user.id, updated_by_id=current_user.id)
            vm = _commit_with_number(make)
            vm.data_json = json.dumps(d)
            db.session.commit()
            flash(f"Valuation model {vm.vm_number} created. Add the component valuations: upload them, bring in saved valuations, or type them.", "success")
            return _back(vm, "components")
    return render_template("corpfin/vm_new.html", clients=clients, engagements=engagements, form=form, currencies=CURRENCIES,
                           bases=BASIS_OF_VALUE, today=date.today().isoformat())


@corpfin_bp.route("/valuation-models/<int:vm_id>")
def vm_home(vm_id):
    vm = _vm_or_404(vm_id)
    return _back(vm, "consolidation" if _data(vm)["components"] else "components")


# =========================================================================== tabs

@corpfin_bp.route("/valuation-models/<int:vm_id>/<tab>")
def vm_tab(vm_id, tab):
    vm = _vm_or_404(vm_id)
    if tab not in dict(VM_TABS):
        abort(404)
    d = _data(vm)
    res = V.consolidate(d, vm.currency, vm.valuation_date)
    ctx = dict(vm=vm, d=d, res=res, tab=tab, cur=vm.currency, bars=_bars(res), currencies=CURRENCIES, bases=BASIS_OF_VALUE,
               fx_needed=V.currencies_needed(d, vm.currency), today=date.today().isoformat())
    if tab == "components":
        ctx["saved_models"], ctx["saved_certs"] = _saved_sources(vm)
        ctx["ai_on"] = VI.ai_available()
    if tab == "report":
        ctx["engagements"] = _engagement_choices([vm.client])
        ctx["filed"] = ([x for x in vm.engagement.documents if x.workpaper_kind == "valuation_model" and x.reference == vm.vm_number and x.is_current_version]
                        if vm.engagement else [])
    return render_template(f"corpfin/vm_{tab}.html", **ctx)


# =========================================================================== components: save / add / delete

def _form_component(form, i, existing):
    cid = form.get(f"c{i}_id") or ""
    old = existing.get(cid, {})
    comp = {
        "id": cid or None, "name": form.get(f"c{i}_name"), "type": form.get(f"c{i}_type"), "method": form.get(f"c{i}_method"),
        "currency": form.get(f"c{i}_currency"), "basis": form.get(f"c{i}_basis"), "date": form.get(f"c{i}_date"),
        "low": form.get(f"c{i}_low"), "adopted": form.get(f"c{i}_adopted"), "high": form.get(f"c{i}_high"),
        "net_debt": form.get(f"c{i}_net_debt"), "ownership_pct": form.get(f"c{i}_ownership_pct"),
        "minority_pct": form.get(f"c{i}_minority_pct"), "dlom_pct": form.get(f"c{i}_dlom_pct"), "control_pct": form.get(f"c{i}_control_pct"),
        "include": f"c{i}_include" in form, "notes": form.get(f"c{i}_notes"), "source": old.get("source"),
    }
    return comp


@corpfin_bp.route("/valuation-models/<int:vm_id>/components/save", methods=["POST"])
def vm_save_components(vm_id):
    vm = _vm_or_404(vm_id)
    if _vm_locked(vm):
        return _back(vm)
    d = _data(vm)
    form = request.form
    existing = {c["id"]: c for c in d["components"]}
    try:
        n = int(form.get("n") or 0)
    except ValueError:
        n = 0
    comps = [_form_component(form, i, existing) for i in range(n) if f"c{i}_id" in form]
    action = form.get("action", "save")
    if action.startswith("delete:"):
        gone = action.split(":", 1)[1]
        comps = [c for c in comps if c["id"] != gone]
    elif action == "add":
        comps.append({"name": "New component", "type": "Subsidiary", "currency": vm.currency, "basis": "equity", "ownership_pct": 100})
    d["components"] = comps
    _save(vm, d)
    flash("Component added - fill it in." if action == "add" else ("Component removed." if action.startswith("delete:") else "Components saved."), "success")
    return redirect(url_for("corpfin.vm_tab", vm_id=vm.id, tab="components") + ("#components" if action != "save" else ""))


# =========================================================================== components: template, upload, saved valuations

@corpfin_bp.route("/valuation-models/<int:vm_id>/template.xlsx")
def vm_template(vm_id):
    vm = _vm_or_404(vm_id)
    data = VI.template_workbook(vm.currency, vm.client.name)
    return Response(data, mimetype=XLSX_MIME, headers={"Content-Disposition": 'attachment; filename="Component_valuation_template.xlsx"'})


@corpfin_bp.route("/valuation-models/<int:vm_id>/components/excel")
def vm_components_excel(vm_id):
    """The model's current components in the template layout - edit them in Excel and upload them back."""
    vm = _vm_or_404(vm_id)
    d = _data(vm)
    data = VI.template_workbook(vm.currency, vm.client.name, rows=d["components"])
    return Response(data, mimetype=XLSX_MIME, headers={"Content-Disposition": f'attachment; filename="{vm.vm_number}_components.xlsx"'})


def _review(vm, cands, notes, method, stored="", src_name=""):
    d = _data(vm)
    return render_template("corpfin/vm_import_review.html", vm=vm, cands=cands, notes=notes, method=method, stored=stored, src_name=src_name,
                           existing=d["components"], cur=vm.currency, currencies=CURRENCIES, tab="components", d=d,
                           res=V.consolidate(d, vm.currency, vm.valuation_date))


@corpfin_bp.route("/valuation-models/<int:vm_id>/import/read", methods=["POST"])
def vm_import_read(vm_id):
    vm = _vm_or_404(vm_id)
    if _vm_locked(vm):
        return _back(vm)
    pasted = (request.form.get("text") or "").strip()
    f = request.files.get("file")
    if (not f or not f.filename) and len(pasted) < 20:
        flash("Choose a file (Excel, Word or PDF) or paste the valuation conclusion.", "danger")
        return _back(vm)
    if not f or not f.filename:
        cands, notes = VI.read_text(pasted, "Pasted conclusion", vm.currency)
        if not cands:
            flash(notes[0] if notes else "Nothing could be read from that text.", "danger")
            return _back(vm)
        return _review(vm, cands, notes, "text")
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in UPLOAD_EXT:
        flash("Use an Excel (.xlsx), Word (.docx) or PDF file.", "danger")
        return _back(vm)
    stored = f"vm{vm.id}_src_{uuid.uuid4().hex[:10]}_{secure_filename(f.filename) or 'source.' + ext}"
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored)
    f.save(path)
    if os.path.getsize(path) > MAX_UPLOAD:
        os.remove(path)
        flash("That file is larger than 15 MB.", "danger")
        return _back(vm)
    try:
        cands, notes, method = VI.read_file(path, f.filename, vm.currency)
    except ValueError as e:
        os.remove(path)
        flash(str(e), "danger")
        return _back(vm)
    except Exception:
        current_app.logger.exception("component import failed")
        os.remove(path)
        flash("That file could not be read.", "danger")
        return _back(vm)
    if not cands:
        os.remove(path)
        flash((notes[0] if notes else "No valuation was found in that file.") + " You can download the Excel template, complete it and upload it.", "danger")
        return _back(vm)
    return _review(vm, cands, notes, method, stored, f.filename)


@corpfin_bp.route("/valuation-models/<int:vm_id>/import/saved", methods=["POST"])
def vm_import_saved(vm_id):
    vm = _vm_or_404(vm_id)
    if _vm_locked(vm):
        return _back(vm)
    picks = request.form.getlist("pick")
    if not picks:
        flash("Tick at least one saved valuation to bring in.", "danger")
        return _back(vm)
    cands, notes = [], []
    models, certs = _saved_sources(vm)
    mmap, cmap = {m.id: m for m in models}, {c.id: c for c in certs}
    for p in picks[:20]:
        parts = p.split(":")
        try:
            if parts[0] == "m" and len(parts) == 3:
                m = mmap.get(int(parts[1]))
                val = next((x for x in (m.valuations if m else []) if x.id == int(parts[2])), None)
                if m is None or val is None:
                    continue
                sc = next((s for s in m.scenarios if s.id == val.scenario_id), None) if val.scenario_id else None
                cands.append(VI.from_model_valuation(m, val, _run(m, sc), sc.name if sc else ""))
            elif parts[0] == "c" and len(parts) == 2:
                c = cmap.get(int(parts[1]))
                if c is not None:
                    cands.append(VI.from_certificate(c))
        except Exception:
            current_app.logger.exception("saved valuation import failed")
            notes.append("One saved valuation could not be read.")
    if not cands:
        flash("Nothing could be brought in from those choices.", "danger")
        return _back(vm)
    for c in cands:
        c["comp"]["source"] = dict(c["comp"].get("source") or {})
    return _review(vm, cands, notes, "saved")


@corpfin_bp.route("/valuation-models/<int:vm_id>/import/apply", methods=["POST"])
def vm_import_apply(vm_id):
    vm = _vm_or_404(vm_id)
    if _vm_locked(vm):
        return _back(vm)
    form = request.form
    d = _data(vm)
    method = form.get("method") or "upload"
    stored, src_name = form.get("stored") or "", form.get("src_name") or ""
    doc = None
    if stored:
        ok = stored.startswith(f"vm{vm.id}_src_") and "/" not in stored and "\\" not in stored and os.path.isfile(os.path.join(current_app.config["UPLOAD_FOLDER"], stored))
        if not ok:
            stored = ""
    try:
        n = int(form.get("n") or 0)
    except ValueError:
        n = 0
    chosen = [i for i in range(n) if f"c{i}_use" in form]
    if not chosen:
        if stored:
            try:
                os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], stored))
            except OSError:
                pass
        flash("Nothing was ticked, so nothing was added.", "info")
        return _back(vm)
    if stored and vm.engagement_id:
        doc = Document(engagement_id=vm.engagement_id, original_filename=src_name or stored, stored_filename=stored, category=CATEGORY, reference=vm.vm_number,
                       version=1, uploaded_by_id=current_user.id, uploaded_at=datetime.utcnow(), is_current_version=True,
                       notes=f"Component valuation source for {vm.vm_number}")
        db.session.add(doc)
        db.session.flush()
    existing = {c["id"]: c for c in d["components"]}
    added = replaced = 0
    label = {"template": "Excel template", "excel": "Excel workbook", "ai": "uploaded report (AI reader)", "text": "uploaded report", "saved": "saved valuation",
             "upload": "upload"}.get(method, "upload")
    for i in chosen:
        comp = {k: form.get(f"c{i}_{k}") for k in ("name", "type", "method", "currency", "basis", "date", "low", "adopted", "high", "net_debt", "ownership_pct",
                                                 "minority_pct", "dlom_pct", "control_pct", "notes")}
        src_kind = "saved" if method == "saved" else ("upload" if stored else "paste")
        comp["source"] = {"kind": src_kind, "label": (src_name or form.get(f"c{i}_where") or label) + (f" ({form.get(f'c{i}_where')})" if src_name and form.get(f"c{i}_where") else ""),
                          "ref": form.get(f"c{i}_ref") or "", "document_id": doc.id if doc else None, "at": datetime.utcnow().strftime("%Y-%m-%d %H:%M")}
        target = form.get(f"c{i}_target") or "new"
        if target != "new" and target in existing:
            old = existing[target]
            for k in ("type", "method", "currency", "basis", "date", "low", "adopted", "high", "net_debt"):
                old[k] = comp[k] if comp[k] not in (None, "") or k in ("low", "high", "net_debt") else old.get(k)
            old["source"] = comp["source"]
            replaced += 1
        else:
            comp["include"] = True
            d["components"].append(comp)
            added += 1
    d["import_log"].append({"at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "by": current_user.name, "method": method, "file": src_name, "added": added, "replaced": replaced})
    _save(vm, d)
    flash(f"{added} component(s) added" + (f", {replaced} updated" if replaced else "") + ". Check the ownership % and any discounts for each.", "success")
    return _back(vm, "components")


# =========================================================================== holding items & group adjustments

@corpfin_bp.route("/valuation-models/<int:vm_id>/group/save", methods=["POST"])
def vm_save_group(vm_id):
    vm = _vm_or_404(vm_id)
    if _vm_locked(vm):
        return _back(vm, "group")
    d = _data(vm)
    form = request.form
    try:
        n = int(form.get("n") or 0)
    except ValueError:
        n = 0
    items = []
    for i in range(n):
        if f"h{i}_id" not in form:
            continue
        items.append({"id": form.get(f"h{i}_id") or None, "label": form.get(f"h{i}_label"), "kind": form.get(f"h{i}_kind"), "currency": form.get(f"h{i}_currency"),
                      "low": form.get(f"h{i}_low"), "adopted": form.get(f"h{i}_adopted"), "high": form.get(f"h{i}_high"),
                      "annual": form.get(f"h{i}_annual"), "multiple": form.get(f"h{i}_multiple"), "notes": form.get(f"h{i}_notes")})
    action = form.get("action", "save")
    if action.startswith("delete:"):
        gone = action.split(":", 1)[1]
        items = [h for h in items if h["id"] != gone]
    elif action.startswith("add:"):
        kind = action.split(":", 1)[1]
        items.append({"label": {"add": "Holding-company cash", "deduct": "Holding-company debt", "eliminate": "Elimination of intercompany balance",
                                "capcost": "Capitalised central costs"}.get(kind, "Item"), "kind": kind, "currency": vm.currency})
    d["holding"] = items
    d["group"] = {"control_pct": form.get("g_control_pct"), "minority_pct": form.get("g_minority_pct"), "dlom_pct": form.get("g_dlom_pct"),
                  "stake_pct": form.get("g_stake_pct") if form.get("g_stake_pct") not in (None, "") else 100, "shares": form.get("g_shares")}
    _save(vm, d)
    flash("Item added - fill it in." if action.startswith("add:") else ("Item removed." if action.startswith("delete:") else "Holding items and group adjustments saved."), "success")
    return redirect(url_for("corpfin.vm_tab", vm_id=vm.id, tab="group") + ("#items" if action != "save" else ""))


# =========================================================================== settings, status, delete

@corpfin_bp.route("/valuation-models/<int:vm_id>/settings", methods=["POST"])
def vm_save_settings(vm_id):
    vm = _vm_or_404(vm_id)
    if _vm_locked(vm):
        return _back(vm, "report")
    form = request.form
    name = (form.get("name") or "").strip()
    if name:
        vm.name = name[:200]
    cur = (form.get("currency") or vm.currency).strip().upper()[:6] or vm.currency
    vdate = V.parse_date(form.get("valuation_date"))
    if vdate:
        vm.valuation_date = vdate
    if "engagement_id" in form:
        eng = _valid_engagement(vm.client_id, form.get("engagement_id"))
        vm.engagement_id = eng.id if eng else None
    d = _data(vm)
    d["basis_of_value"] = form.get("basis_of_value") or d["basis_of_value"]
    d["purpose"] = form.get("purpose") or ""
    d["standard"] = form.get("standard") or ""
    d["premise"] = form.get("premise") or d["premise"]
    fx = {}
    for k, v in form.items():
        if k.startswith("fx_") and v.strip():
            fx[k[3:]] = v
    d["fx"] = fx
    d["narrative"] = {k: form.get(f"nar_{k}") or "" for k in ("scope", "assumptions", "limiting")}
    vm.currency = cur
    _save(vm, d)
    flash("Settings saved.", "success")
    return _back(vm, "report")


@corpfin_bp.route("/valuation-models/<int:vm_id>/status", methods=["POST"])
def vm_status(vm_id):
    vm = _vm_or_404(vm_id)
    if not _is_signoff_role():
        abort(403, description="Only a Partner or Admin can finalise or reopen a valuation model.")
    if vm.is_final:
        vm.status = "Draft"
        vm.finalised_by_id = None
        vm.finalised_at = None
        db.session.commit()
        flash("Valuation model reopened.", "success")
        return _back(vm, "report")
    res = V.consolidate(vm.data, vm.currency, vm.valuation_date)
    if not res["count"]:
        flash("Add at least one component with a value before finalising.", "danger")
        return _back(vm, "report")
    if not res["complete"]:
        flash("The model is incomplete - a component has no value or no exchange rate. Fix that before finalising.", "danger")
        return _back(vm, "report")
    vm.status = "Final"
    vm.finalised_by_id = current_user.id
    vm.finalised_at = datetime.utcnow()
    db.session.commit()
    msg = "Valuation model marked Final."
    if vm.engagement_id:
        try:
            n = file_into_working_papers(vm)
            msg += f" {n} file(s) filed in the engagement's Working Papers."
        except Exception:
            db.session.rollback()
            current_app.logger.exception("filing valuation model failed")
            msg += " (The files could not be filed automatically - use 'File in Working Papers'.)"
    flash(msg, "success")
    return _back(vm, "report")


@corpfin_bp.route("/valuation-models/<int:vm_id>/delete", methods=["POST"])
def vm_delete(vm_id):
    vm = _vm_or_404(vm_id)
    if vm.is_final:
        flash("A Final valuation model cannot be deleted. Reopen it first.", "danger")
        return _back(vm, "report")
    if vm.mp_signed_at:
        flash("Remove the Managing Partner's signature before deleting this valuation model.", "danger")
        return _back(vm, "report")
    if not _is_signoff_role() and vm.created_by_id != current_user.id:
        abort(403, description="Only the person who created a valuation model, or a Partner / Admin, can delete it.")
    number, eng_id = vm.vm_number, vm.engagement_id
    db.session.delete(vm)
    db.session.commit()
    flash(f"Valuation model {number} deleted.", "success")
    return redirect(url_for("corpfin.engagement_valuations", engagement_id=eng_id) if eng_id else url_for("corpfin.dashboard"))


# =========================================================================== exports & filing

def _builders():
    import cf_valmodel_export as X
    return X


def _sig(vm):
    import brand_docs
    return brand_docs.signature_valid(vm)


@corpfin_bp.route("/valuation-models/<int:vm_id>/excel")
def vm_excel(vm_id):
    vm = _vm_or_404(vm_id)
    return Response(_builders().build_excel(vm), mimetype=XLSX_MIME, headers={"Content-Disposition": f'attachment; filename="{_fn(vm, "xlsx")}"'})


@corpfin_bp.route("/valuation-models/<int:vm_id>/pdf")
def vm_pdf(vm_id):
    vm = _vm_or_404(vm_id)
    return Response(_builders().build_pdf(vm, signed=_sig(vm)), mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{_fn(vm, "pdf")}"'})


@corpfin_bp.route("/valuation-models/<int:vm_id>/docx")
def vm_docx(vm_id):
    vm = _vm_or_404(vm_id)
    return Response(_builders().build_docx(vm, signed=_sig(vm)), mimetype=DOCX_MIME, headers={"Content-Disposition": f'attachment; filename="{_fn(vm, "docx")}"'})


def file_into_working_papers(vm):
    """Save the Excel, PDF and Word versions as Working Papers on the engagement (a later filing replaces the earlier as current). Returns the count."""
    if not vm.engagement_id:
        raise ValueError("This valuation model is not linked to an engagement.")
    X = _builders()
    signed = _sig(vm)
    files = [("xlsx", X.build_excel(vm)), ("pdf", X.build_pdf(vm, signed=signed)), ("docx", X.build_docx(vm, signed=signed))]
    client = re.sub(r'[\\/:*?"<>|\r\n]+', "", vm.client.name)
    n = 0
    for ext, data in files:
        name = f"{vm.vm_number} Valuation Model - {client}.{ext}"[:180]
        stored = f"eng{vm.engagement_id}_vm_{uuid.uuid4().hex[:10]}_{secure_filename(name) or 'valuation.' + ext}"
        with open(os.path.join(current_app.config["UPLOAD_FOLDER"], stored), "wb") as fh:
            fh.write(data)
        prior = Document.query.filter_by(engagement_id=vm.engagement_id, original_filename=name, category=CATEGORY).all()
        for p in prior:
            p.is_current_version = False
        db.session.add(Document(engagement_id=vm.engagement_id, original_filename=name, stored_filename=stored, category=CATEGORY, reference=vm.vm_number,
                                version=len(prior) + 1, uploaded_by_id=current_user.id, uploaded_at=datetime.utcnow(), is_generated=True,
                                workpaper_kind="valuation_model", is_current_version=True,
                                notes=f"Generated from valuation model {vm.vm_number} ({vm.status})" + (" - signed by the Managing Partner" if signed else "")))
        n += 1
    db.session.commit()
    return n


@corpfin_bp.route("/valuation-models/<int:vm_id>/file", methods=["POST"])
def vm_file(vm_id):
    vm = _vm_or_404(vm_id)
    if not vm.engagement_id:
        flash("Link this valuation model to an engagement (Settings) before filing it in Working Papers.", "danger")
        return _back(vm, "report")
    try:
        n = file_into_working_papers(vm)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("filing valuation model failed")
        flash("The files could not be filed.", "danger")
        return _back(vm, "report")
    flash(f"{n} file(s) filed in Working Papers: Excel, PDF and Word.", "success")
    return _back(vm, "report")
