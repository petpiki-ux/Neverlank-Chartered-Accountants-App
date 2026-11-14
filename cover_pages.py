"""Cover pages for every client document: one place that decides what a cover says and looks like, and hands it to the exporters.

    cover_kit.py   draws a cover (8 designs, colour themes) from a plain dictionary - no database, no Flask.
    cover_pages.py (this file) works out the dictionary for a real document, remembers the choices (CoverSetting), has the editor page
                   with its live preview, and gives the exporters three small helpers:
                       png_for(kind, obj, ...)            the cover as a picture (BytesIO) or None
                       pdf_cover(kind, obj, pagesize)     [picture flowable, PageBreak] to put first in a reportlab story, or []
                       word_cover(doc, kind, obj)         puts the cover on page 1 of a python-docx document; True when it did

A cover must never cost anyone their document: every helper catches its own failures and returns None / [] / False, and the exporter
carries on with its old first page.

Kinds: "client" (a client's defaults), "fs" (an engagement's Financial Statements), "ma" (management accounts pack), "vm" (valuation model and
valuation report), "fm" (forecast model), "ip" (investment appraisal).
Which choice wins: the document's own > the client's defaults > the firm's (a design that suits that sort of document, the client's colours).
"""
import io
import json
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import cover_art
import cover_kit as K
from extensions import db

covers_bp = Blueprint("covers", __name__, url_prefix="/covers")

KINDS = ("client", "fs", "ma", "vm", "fm", "ip")
KIND_LABELS = {"client": "Client defaults", "fs": "Financial Statements", "ma": "Management Accounts", "vm": "Valuation",
               "fm": "Financial Forecast", "ip": "Investment Appraisal"}
# a design that suits each sort of document when nobody has chosen one
DEFAULT_DESIGN = {"client": "bands", "fs": "bands", "ma": "blocks", "vm": "framed", "fm": "waves", "ip": "header"}
# the page shape the document is printed on (False = portrait): the preview and the thumbnails use it
LANDSCAPE = {"client": False, "fs": False, "ma": True, "vm": False, "fm": True, "ip": True}
PORTRAIT_RATIO, LANDSCAPE_RATIO = 1.4142, 0.7071

STATUS_MODES = [("auto", "Follow the document (Draft / For review / Final)"), ("custom", "Write my own"), ("none", "No status line")]
STATUS_CHOICES = ["Draft", "For review", "Final", "Confidential", "Signed"]
DEFAULT_KICKER = "STRICTLY PRIVATE AND CONFIDENTIAL"

# the choices that a client's defaults set for all of its covers
INHERIT = ("design", "theme", "prepared_by", "kicker", "status_mode", "show_logo", "show_reg", "show_framework", "show_bankers")
TEXT_FIELDS = ("title", "subtitle", "period", "big", "prepared_for", "prepared_by", "issue_date", "reference", "version", "kicker", "status_text")
BOOL_FIELDS = ("enabled", "show_logo", "show_reg", "show_framework", "show_bankers")

BASE_DEFAULTS = {"design": "", "theme": "client", "enabled": True, "status_mode": "auto", "show_logo": True, "show_reg": True,
                 "show_framework": True, "show_bankers": False}


# ------------------------------------------------------------------------------------------------------------------ settings storage
def _model():
    from models import CoverSetting
    return CoverSetting


def _row(kind, ref_id):
    return _model().query.filter_by(kind=kind, ref_id=ref_id).first()


def _load_json(row):
    try:
        d = json.loads(row.data_json) if row is not None and row.data_json else {}
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _clean(d):
    """Only the known choices, as text; booleans as True/False."""
    out = {}
    for k in ("design", "theme", "status_mode"):
        v = str(d.get(k) or "").strip()
        if v:
            out[k] = v
    for k in TEXT_FIELDS:
        if k in d:
            out[k] = str(d.get(k) or "").strip()[:300]
    for k in BOOL_FIELDS:
        if k in d and d[k] is not None and d[k] != "":
            out[k] = d[k] in (True, 1, "1", "true", "on", "yes")
    if out.get("design") not in K.DESIGN_KEYS:
        out.pop("design", None)
    if out.get("theme") not in K.THEME_KEYS:
        out.pop("theme", None)
    if out.get("status_mode") not in [m for m, _ in STATUS_MODES]:
        out.pop("status_mode", None)
    return out


def saved_settings(kind, ref_id):
    return _load_json(_row(kind, ref_id))


def effective_settings(kind, obj, client_id=None, override=None):
    """The choices a cover is drawn from: firm defaults, then the client's, then this document's (and `override`, which is the unsaved form
    in the editor's live preview)."""
    st = dict(BASE_DEFAULTS)
    cid = client_id or getattr(obj, "client_id", None)
    if kind != "client" and cid:
        st.update({k: v for k, v in _load_json(_row("client", cid)).items() if k in INHERIT and v not in ("", None)})
    own = _load_json(_row(kind, obj.id))
    if override is not None:
        own = dict(own)
        own.update(override)
    for k, v in own.items():
        if v in ("", None) and k in INHERIT and k not in BOOL_FIELDS:
            continue                    # left blank here = keep the client's choice
        st[k] = v
    if not st.get("design"):
        st["design"] = DEFAULT_DESIGN.get(kind, "bands")
    return st


def save_settings(kind, ref_id, data, user=None):
    data = _clean(data)
    row = _row(kind, ref_id)
    if row is None:
        row = _model()(kind=kind, ref_id=ref_id)
        db.session.add(row)
    row.data_json = json.dumps(data)
    row.updated_by_id = getattr(user, "id", None)
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return data


def reset_settings(kind, ref_id):
    row = _row(kind, ref_id)
    if row is not None:
        db.session.delete(row)
        db.session.commit()


# ------------------------------------------------------------------------------------------------------------------ what a cover says
def _dmy(d):
    return f"{d.day} {d.strftime('%B %Y')}" if d else ""


def _parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def client_of(kind, obj):
    return obj if kind == "client" else getattr(obj, "client", None)


def _json(text, default):
    try:
        v = json.loads(text) if text else default
        return v if isinstance(v, type(default)) else default
    except (TypeError, ValueError):
        return default


def _facts(kind, obj, variant=None):
    """What the document itself knows: the cover's title, subtitle, period line, big year, reference and automatic status."""
    f = {"title": "", "subtitle": "", "period": "", "big": "", "reference": "", "status": "Draft", "framework": "", "prepared_for": "",
         "year": None}
    if kind == "client":
        f.update(title="Financial Statements", period="for the year ended 31 December %d" % (date.today().year - 1),
                 big=str(date.today().year - 1), prepared_for="The Directors", framework="")
        return f
    if kind == "fs":
        pe = obj.period_end
        f.update(title="Financial Statements", period=f"for the year ended {_dmy(pe)}" if pe else "", big=str(pe.year) if pe else "",
                 prepared_for="The Directors", framework=cover_art.FRAMEWORK_LABELS.get(getattr(obj, "reporting_framework", None), ""),
                 status={"Completed": "Final", "Review": "For review"}.get(obj.status, "Draft"))
        return f
    final = "Final" if getattr(obj, "is_final", False) else "Draft"
    if kind == "ma":
        label, year = "", None
        try:
            import cf_ma as M
            d = obj.data
            months = M.fy_months(d)
            ridx = M.report_index(d)
            label = M.month_label(months[ridx], False)
            year = int(months[ridx][:4])
        except Exception:
            pass
        f.update(title="Management Accounts", subtitle=obj.name, period=f"for the month of {label}" if label else "", big=str(year or ""),
                 reference=obj.pack_number, status=final)
    elif kind == "fm":
        a = _json(obj.assumptions_json, {})
        years, start = a.get("years"), str(a.get("start_month") or "")
        period = ""
        try:
            sd = datetime.strptime(start[:7], "%Y-%m").date()
            period = f"{years}-year forecast from {sd.strftime('%B %Y')}" if years else f"forecast from {sd.strftime('%B %Y')}"
            f["big"] = str(sd.year)
        except ValueError:
            pass
        f.update(title="Financial Forecast", subtitle=obj.name, period=period, reference=obj.model_number, status=final)
    elif kind == "ip":
        f.update(title="Investment Appraisal", subtitle=obj.name, reference=obj.project_number, status=final,
                 big=str(date.today().year))
    elif kind == "vm":
        vd = obj.valuation_date
        f.update(title="Valuation Report" if variant != "model" else "Valuation Model", subtitle=obj.name,
                 period=f"as at {_dmy(vd)}" if vd else "", big=str(vd.year) if vd else "", reference=obj.vm_number, status=final)
    return f


def build_content(kind, obj, st, variant=None, signed=False):
    """The dictionary cover_kit draws from, for this document and these settings."""
    f = _facts(kind, obj, variant)
    client = client_of(kind, obj)
    firm = _firm()
    issue = _parse_date(st.get("issue_date")) or date.today()
    mode = st.get("status_mode") or "auto"
    if mode == "none":
        status = ""
    elif mode == "custom":
        status = (st.get("status_text") or "").strip()
    else:
        status = "Signed" if signed else f["status"]
    lines = [("Prepared for", st.get("prepared_for") or f["prepared_for"]),
             ("Prepared by", st.get("prepared_by") or firm.get("name", "")),
             ("Issued", _dmy(issue)),
             ("Reference", st.get("reference") or f["reference"]),
             ("Version", st.get("version") or "")]
    bankers = ""
    if st.get("show_bankers") and client is not None and getattr(client, "bankers", None):
        bankers = "; ".join(x.strip() for x in client.bankers.splitlines() if x.strip())
    lines.append(("Bankers", bankers))
    footer_bits = [firm.get("name", ""), firm.get("address", ""), firm.get("web", "")]
    show_fw = bool(st.get("show_framework")) and bool(f["framework"])
    return K.normalise_content({
        "kicker": "" if (st.get("kicker") or "").strip() == "-" else (st.get("kicker") or DEFAULT_KICKER),
        "title": st.get("title") or f["title"],
        "subtitle": st.get("subtitle") if st.get("subtitle") not in (None, "") else (f["period"] if not f["subtitle"] else
                                                                                         (f["subtitle"] + ("  -  " + f["period"] if f["period"] else ""))),
        "big": st.get("big") if st.get("big") not in (None, "") else f["big"],
        "client": client.name if client is not None else "",
        "reg_no": (client.company_number or "") if (client is not None and st.get("show_reg")) else "",
        "tag_label": "Reporting framework" if show_fw else "", "tag_value": f["framework"] if show_fw else "",
        "lines": lines, "status": status,
        "logo_path": cover_art.logo_path_for(client) if (client is not None and st.get("show_logo")) else None,
        "firm_name": firm.get("name", ""), "firm_descriptor": firm.get("tagline", "") or "",
        "footer": "  -  ".join(b for b in footer_bits if b),
    })


def _firm():
    try:
        from invoicing import FIRM_LETTERHEAD
        return FIRM_LETTERHEAD
    except Exception:
        return {"name": cover_art.FIRM_NAME}


def palette_for(client, theme):
    base = cover_art.palette_for_client(client) if client is not None else cover_art.build_palette()
    return K.palette_for_theme(theme or "client", base)


def render_png(kind, obj, st, aspect, width_px=1654, variant=None, signed=False, design=None, theme=None):
    client = client_of(kind, obj)
    content = build_content(kind, obj, st, variant=variant, signed=signed)
    pal = palette_for(client, theme or st.get("theme"))
    return K.render(design or st.get("design") or DEFAULT_DESIGN.get(kind, "bands"), pal, content, width_px=width_px, aspect=aspect)


# ------------------------------------------------------------------------------------------------------------------ helpers for the exporters
def is_enabled(kind, obj):
    try:
        return bool(effective_settings(kind, obj).get("enabled", True))
    except Exception:
        return True


def png_for(kind, obj, aspect=None, width_px=1654, variant=None, signed=False):
    """The saved cover as a PNG in a BytesIO, or None when covers are switched off for it or anything goes wrong."""
    try:
        st = effective_settings(kind, obj)
        if not st.get("enabled", True):
            return None
        if aspect is None:
            aspect = LANDSCAPE_RATIO if LANDSCAPE.get(kind) else PORTRAIT_RATIO
        return render_png(kind, obj, st, aspect, width_px=width_px, variant=variant, signed=signed)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def pdf_cover(kind, obj, pagesize, variant=None, signed=False, width_px=1500):
    """[full-page picture, PageBreak] to put at the start of a reportlab story, or [] (covers off, or a failure)."""
    try:
        from reportlab.platypus import PageBreak
        w, h = pagesize
        png = png_for(kind, obj, aspect=h / float(w), width_px=width_px, variant=variant, signed=signed)
        if png is None:
            return []
        return [_full_page_flowable(png.getvalue(), w, h), PageBreak()]
    except Exception:
        return []


def _full_page_flowable(data, page_w, page_h):
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Flowable

    class CoverImage(Flowable):
        """Fills the whole sheet, whatever the margins: takes all the room the frame gives and paints from the page's corner."""
        def __init__(self):
            super().__init__()
            self._img = ImageReader(io.BytesIO(data))

        def wrap(self, avail_w, avail_h):
            self.width, self.height = avail_w, max(avail_h - 0.5, 1)
            return self.width, self.height

        def drawOn(self, canvas, x, y, _sW=0):
            canvas.saveState()
            canvas.drawImage(self._img, 0, 0, width=page_w, height=page_h)
            canvas.restoreState()

        def draw(self):
            pass

    return CoverImage()


def word_cover(doc, kind, obj, variant=None, signed=False, alt=None):
    """Puts the cover on the first page of a python-docx document (picture behind the page, first page without header or footer) followed
    by a page break. Returns True if it did; on any failure the document is left exactly as it was."""
    body = doc.element.body
    before = [c for c in body]
    try:
        sec = doc.sections[0]
        page_w, page_h = int(sec.page_width), int(sec.page_height)
        png = png_for(kind, obj, aspect=page_h / float(page_w), variant=variant, signed=signed)
        if png is None:
            return False
        place_word_picture(doc, png, alt or (KIND_LABELS.get(kind, "") + " cover page"))
        doc.add_page_break()
        return True
    except Exception:
        for c in list(body):
            if c not in before and not c.tag.endswith("}sectPr"):
                body.remove(c)
        return False


def place_word_picture(doc, png, alt):
    """A full-page picture anchored to the page corner and set behind the text, so margins and the rest of the document are untouched."""
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn
    sec = doc.sections[0]
    page_w, page_h = int(sec.page_width), int(sec.page_height)
    sec.different_first_page_header_footer = True
    p = doc.add_paragraph()
    run = p.add_run()
    run.add_picture(png, width=page_w, height=page_h)
    inline = run._r.find(".//" + qn("wp:inline"))
    anchor = parse_xml(
        '<wp:anchor %s distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="0" behindDoc="1" locked="1" '
        'layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="page"><wp:posOffset>0</wp:posOffset></wp:positionH>'
        '<wp:positionV relativeFrom="page"><wp:posOffset>0</wp:posOffset></wp:positionV></wp:anchor>' % nsdecls("wp"))
    anchor.append(inline.find(qn("wp:extent")))
    anchor.append(parse_xml('<wp:effectExtent %s l="0" t="0" r="0" b="0"/>' % nsdecls("wp")))
    anchor.append(parse_xml("<wp:wrapNone %s/>" % nsdecls("wp")))
    docpr = inline.find(qn("wp:docPr"))
    docpr.set("name", "Cover page")
    docpr.set("descr", alt)
    anchor.append(docpr)
    for tag in ("wp:cNvGraphicFramePr", "a:graphic"):
        el = inline.find(qn(tag))
        if el is not None:
            anchor.append(el)
    inline.getparent().replace(inline, anchor)


# ------------------------------------------------------------------------------------------------------------------ pages
def _load(kind, ref_id):
    """The document (or client) a cover belongs to, after the same access checks the rest of the app makes."""
    from models import (CFMgmtPack, CFModel, CFProject, CFValuationModel, Client, Engagement, user_can_access_client,
                        user_can_access_engagement)
    if kind not in KINDS:
        abort(404)
    cls = {"client": Client, "fs": Engagement, "ma": CFMgmtPack, "vm": CFValuationModel, "fm": CFModel, "ip": CFProject}[kind]
    obj = cls.query.get_or_404(ref_id)
    cid = obj.id if kind == "client" else obj.client_id
    if not user_can_access_client(current_user, cid):
        abort(403, description="You are not working on this client.")
    if kind == "fs" and not user_can_access_engagement(current_user, obj):
        abort(403, description="You are not on this engagement.")
    if kind in ("ma", "vm", "fm", "ip") and getattr(obj, "engagement_id", None) and obj.engagement is not None \
            and not user_can_access_engagement(current_user, obj.engagement):
        abort(403, description="You are not on the engagement this belongs to.")
    return obj


def _back_url(kind, obj):
    try:
        return {"client": lambda: url_for("clients.view_client", client_id=obj.id),
                "fs": lambda: url_for("engagements.view_engagement", engagement_id=obj.id),
                "ma": lambda: url_for("corpfin.pack_home", pack_id=obj.id),
                "vm": lambda: url_for("corpfin.vm_home", vm_id=obj.id),
                "fm": lambda: url_for("corpfin.model_home", model_id=obj.id),
                "ip": lambda: url_for("corpfin.view_project", project_id=obj.id)}[kind]()
    except Exception:
        return url_for("clients.view_client", client_id=obj.id if kind == "client" else obj.client_id)


def _title_of(kind, obj):
    if kind == "client":
        return obj.name
    return {"fs": lambda: obj.title, "ma": lambda: obj.name, "vm": lambda: obj.name, "fm": lambda: obj.name, "ip": lambda: obj.name}[kind]()


def _form_settings(form):
    d = {}
    for k in ("design", "theme", "status_mode"):
        d[k] = (form.get(k) or "").strip()
    for k in TEXT_FIELDS:
        if k in form:
            d[k] = form.get(k, "")
    for k in BOOL_FIELDS:
        d[k] = "1" if form.get(k) else "0"
    return d


def _aspect_for(kind, orient):
    land = LANDSCAPE.get(kind, False)
    if orient == "l":
        land = True
    elif orient == "p":
        land = False
    return LANDSCAPE_RATIO if land else PORTRAIT_RATIO


def _png_response(buf):
    resp = Response(buf.getvalue(), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@covers_bp.route("/<kind>/<int:ref_id>", methods=["GET", "POST"])
@login_required
def edit(kind, ref_id):
    obj = _load(kind, ref_id)
    cid = obj.id if kind == "client" else obj.client_id
    if request.method == "POST":
        if request.form.get("reset"):
            reset_settings(kind, ref_id)
            flash("Back to the client's defaults." if kind != "client" else "Back to the firm's standard cover.", "success")
        else:
            save_settings(kind, ref_id, _form_settings(request.form), current_user)
            flash("Cover saved. It is used the next time the document is downloaded.", "success")
        return redirect(url_for("covers.edit", kind=kind, ref_id=ref_id))
    saved = saved_settings(kind, ref_id)
    eff = effective_settings(kind, obj)
    client = client_of(kind, obj)
    facts = _facts(kind, obj)
    return render_template("covers/edit.html", kind=kind, obj=obj, ref_id=ref_id, client=client, client_id=cid, saved=saved, st=eff,
                           has_own=bool(saved), kind_label=KIND_LABELS[kind], doc_title=_title_of(kind, obj), back_url=_back_url(kind, obj),
                           designs=[(k, lab, desc) for k, lab, desc, _f in K.DESIGNS], themes=K.THEMES, status_modes=STATUS_MODES,
                           status_choices=STATUS_CHOICES, facts=facts, land=LANDSCAPE.get(kind, False), has_logo=bool(cover_art.logo_path_for(client)),
                           default_kicker=DEFAULT_KICKER, firm=_firm(), has_framework=bool(facts["framework"]), has_bankers=bool(getattr(client, "bankers", None)),
                           client_has_defaults=bool(kind != "client" and saved_settings("client", cid)),
                           today=date.today().isoformat())


@covers_bp.route("/<kind>/<int:ref_id>/preview.png", methods=["GET", "POST"])
@login_required
def preview(kind, ref_id):
    """The cover as the form stands now (nothing is saved). GET shows the saved one."""
    obj = _load(kind, ref_id)
    orient = (request.values.get("o") or "").strip()
    try:
        if request.method == "POST":
            st = effective_settings(kind, obj, override=_clean(_form_settings(request.form)))
        else:
            st = effective_settings(kind, obj)
        variant = request.values.get("variant") or None
        buf = render_png(kind, obj, st, _aspect_for(kind, orient), width_px=int(min(max(int(request.values.get("w", 900)), 300), 1700)),
                         variant=variant)
    except Exception as exc:                                    # a bad field must not break the page: say so in the picture's place
        abort(400, description=f"The cover could not be drawn: {exc}")
    return _png_response(buf)


@covers_bp.route("/<kind>/<int:ref_id>/thumb/<design>.png")
@login_required
def thumb(kind, ref_id, design):
    """A small picture of one design with the chosen colour theme, for the design gallery."""
    obj = _load(kind, ref_id)
    if design not in K.DESIGN_KEYS:
        abort(404)
    st = effective_settings(kind, obj)
    theme = request.args.get("theme") or st.get("theme")
    if theme not in K.THEME_KEYS:
        theme = "client"
    buf = render_png(kind, obj, st, _aspect_for(kind, request.args.get("o")), width_px=int(min(max(int(request.args.get("w", 360)), 200), 700)),
                     design=design, theme=theme, variant=request.args.get("variant") or None)
    resp = Response(buf.getvalue(), mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


def init_app(app):
    app.register_blueprint(covers_bp)
