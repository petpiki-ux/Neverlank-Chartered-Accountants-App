"""Valuation certificates (Valuations in the top menu).

A certificate is written once in the app (the particulars, the three figures, the approach, the assumptions and the certification),
previewed on screen, downloaded as a Word or PDF file on the firm letterhead (valuation_docs.py) and signed by the Managing Partner with the
same "Sign as Managing Partner" button as the firm's other documents (mpsign.py). Editing a signed certificate drops the signature until it is
signed again.

Partners and Admins write and change certificates; everyone who can see the linked client can open and download one. A signed certificate
cannot be deleted (remove the signature first), because it may already have been issued.
"""
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import valuation_docs as VD
from extensions import db
from models import Client, ValuationCertificate, user_can_access_client, visible_clients

valuations_bp = Blueprint("valuations", __name__, url_prefix="/valuations")

MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf"}
FLOAT_FIELDS = ("area_ha", "low_value", "adopted_value", "high_value")
TEXT_FIELDS = [f for f in VD.TEXT_FIELDS if f != "client_name"] + ["client_name", "asset_noun"]


def _is_senior():
    return current_user.role in ("partner", "admin")


def _require_senior():
    if not _is_senior():
        abort(403, description="Only a Partner or an Admin can write or change a valuation certificate.")


def _get(cert_id):
    cert = ValuationCertificate.query.get_or_404(cert_id)
    if cert.client_id and not user_can_access_client(current_user, cert.client_id):
        abort(403, description="You are not working on this certificate's client, so you cannot open it.")
    return cert


def _number(value):
    value = (value or "").replace(",", "").replace(" ", "").strip()
    if not value:
        return None
    return float(value)


def _date(value):
    value = (value or "").strip()
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _read_form(cert):
    """Copy the posted form onto the certificate. Returns an error message or None."""
    f = request.form
    for name in TEXT_FIELDS:
        if name in f:
            setattr(cert, name, f.get(name, "").strip())
    cert.currency = f.get("currency") if f.get("currency") in VD.CURRENCIES else "USD"
    try:
        for name in FLOAT_FIELDS:
            v = _number(f.get(name))
            if v is not None and v < 0:
                return "Values and the area cannot be negative."
            setattr(cert, name, v)
        cert.valuation_date = _date(f.get("valuation_date"))
        cert.certificate_date = _date(f.get("certificate_date"))
    except ValueError:
        return "Check the figures and dates: a number or a date could not be read."
    cid = f.get("client_id", "")
    cert.client_id = int(cid) if cid.isdigit() and user_can_access_client(current_user, int(cid)) else None
    if cert.client_id and not cert.client_name:
        cert.client_name = Client.query.get(cert.client_id).name
    if not cert.client_name:
        return "Enter the client the certificate is for."
    if not cert.valuation_date:
        return "Enter the valuation date."
    problems = VD.figures_problems(cert)
    if problems:
        return " ".join(problems)
    ref = (f.get("reference") or "").strip()
    if not ref:
        return "Enter the certificate reference."
    clash = ValuationCertificate.query.filter(ValuationCertificate.reference == ref, ValuationCertificate.id != (cert.id or 0)).first()
    if clash:
        return f"The reference {ref} is already used by another certificate."
    cert.reference = ref
    return None


def _form_context(cert, error=None, template_key=None):
    return {"cert": cert, "error": error, "clients": visible_clients(current_user), "currencies": VD.CURRENCIES,
            "template_key": template_key, "templates": VD.TEMPLATES, "left": VD.unresolved(cert) if cert.client_name or cert.intro else []}


# ------------------------------------------------------------------------------------------------ list / new / edit

@valuations_bp.route("/")
@login_required
def list_certificates():
    rows = [c for c in ValuationCertificate.query.order_by(ValuationCertificate.valuation_date.desc(), ValuationCertificate.id.desc()).all()
            if not c.client_id or user_can_access_client(current_user, c.client_id)]
    left = {c.id: len(VD.unresolved(c)) for c in rows}
    return render_template("valuations/list.html", certs=rows, left=left, money=VD.money, long_date=VD.long_date, is_senior=_is_senior())


@valuations_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_certificate():
    _require_senior()
    key = request.values.get("template", "")
    if request.method == "POST":
        cert = ValuationCertificate(created_by_id=current_user.id)
        error = _read_form(cert)
        if error:
            return render_template("valuations/form.html", **_form_context(cert, error, key))
        db.session.add(cert)
        db.session.commit()
        flash("Certificate saved. Complete any highlighted [CONFIRM: ...] parts, then sign it as Managing Partner.", "success")
        return redirect(url_for("valuations.view_certificate", cert_id=cert.id))
    if key not in VD.TEMPLATES:
        return render_template("valuations/pick.html", templates=VD.TEMPLATES)
    cert = ValuationCertificate(**VD.TEMPLATES[key][1])
    cert.reference = VD.next_reference()
    cert.certificate_date = date.today()
    cert.valuation_date = date.today()
    return render_template("valuations/form.html", **_form_context(cert, None, key))


@valuations_bp.route("/<int:cert_id>/edit", methods=["GET", "POST"])
@login_required
def edit_certificate(cert_id):
    _require_senior()
    cert = _get(cert_id)
    if request.method == "POST":
        was_signed = VD.sign_state(cert)
        error = _read_form(cert)
        if error:
            db.session.rollback()
            return render_template("valuations/form.html", **_form_context(cert, error))
        db.session.commit()
        if was_signed and not VD.sign_state(cert):
            flash("Certificate saved. Because the figures or wording changed, the Managing Partner's signature was dropped - sign it again.", "info")
        else:
            flash("Certificate saved.", "success")
        return redirect(url_for("valuations.view_certificate", cert_id=cert.id))
    return render_template("valuations/form.html", **_form_context(cert))


@valuations_bp.route("/<int:cert_id>/delete", methods=["POST"])
@login_required
def delete_certificate(cert_id):
    _require_senior()
    cert = _get(cert_id)
    if cert.mp_signed_at:
        flash("A signed certificate cannot be deleted. Remove the signature first if it was never issued.", "danger")
        return redirect(url_for("valuations.view_certificate", cert_id=cert.id))
    db.session.delete(cert)
    db.session.commit()
    flash("Certificate deleted.", "info")
    return redirect(url_for("valuations.list_certificates"))


# ------------------------------------------------------------------------------------------------ view / download

def _render_blocks(text):
    return [(kind, VD.pieces(t)) for kind, t in VD.blocks(text)]


@valuations_bp.route("/<int:cert_id>")
@login_required
def view_certificate(cert_id):
    cert = _get(cert_id)
    head, vrows = VD.value_table(cert) if VD.has_figures(cert) else ([], [])
    return render_template(
        "valuations/view.html", cert=cert, left=VD.unresolved(cert), particulars=[(k, VD.pieces(v)) for k, v in VD.particulars(cert)],
        conclusion=VD.conclusion_parts(cert), value_head=head, value_rows=vrows, money=VD.money, long_date=VD.long_date,
        intro=VD.pieces(cert.intro or ""), range_note=VD.pieces(cert.range_note or ""), approach=_render_blocks(cert.approach),
        assumptions=_render_blocks(cert.assumptions), certification=_render_blocks(cert.certification), sources=VD.sources_list(cert),
        prefix=VD.prefix(cert), firm_address=VD.firm_address(), signed=VD.sign_state(cert), is_senior=_is_senior())


@valuations_bp.route("/<int:cert_id>/download/<fmt>")
@login_required
def download_certificate(cert_id, fmt):
    cert = _get(cert_id)
    if fmt == "docx":
        data = VD.build_docx(cert)
    elif fmt == "pdf":
        data = VD.build_pdf(cert)
    else:
        abort(404)
    return Response(data, mimetype=MIME[fmt], headers={"Content-Disposition": f'attachment; filename="{VD.filename(cert, fmt)}"'})
