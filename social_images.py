"""Graphics for social media posts (part of the Social module; see graphics.py for how they are drawn).

The AI writes only the short words for a graphic from the post (it never draws): the app draws the picture in the firm's navy and gold, at the size the
platform wants. A person edits the words, sees the picture, and the graphic goes through the same checks and approval as the post: changing it after approval
sends the post back for approval.

Loaded by social.py; the routes belong to the same blueprint.
"""
import json
import re
from datetime import datetime

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import graphics as G
from extensions import db
from models import SocialImage, SocialPost
from social import PLATFORMS, _need_manage, _reopen_if_changed, final_text, one_line, social_bp

DESIGN_TOOL = {
    "name": "design_graphic",
    "description": "Return the short words for one branded graphic. You do not draw: the app draws it from these words.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kicker": {"type": "string", "description": "Small heading in capitals, 40 characters or fewer. Empty if none."},
            "title": {"type": "string", "description": "The headline (or the quote, or the cover headline of a carousel), 90 characters or fewer, no full stop needed."},
            "body": {"type": "string", "description": "One or two short sentences, 160 characters or fewer."},
            "points": {"type": "array", "items": {"type": "string"}, "description": "Up to 4 short points of 60 characters or fewer (tip style only)."},
            "date_label": {"type": "string", "description": "Deadline style only: the date exactly as given in the post, e.g. 30 NOV (14 characters or fewer)."},
            "cta": {"type": "string", "description": "Button text (deadline, banner) or the last carousel slide's headline. 50 characters or fewer."},
            "attribution": {"type": "string", "description": "Quote style only: who said it, only if the post says so."},
            "big_value": {"type": "string", "description": "Stat style only: the one figure exactly as given in the post, 14 characters or fewer."},
            "caption": {"type": "string", "description": "Stat style: what the figure means. Carousel: the line under the last slide's headline. 120 characters or fewer."},
            "source": {"type": "string", "description": "Stat style only: where the figure comes from, only if the post says so."},
            "slides": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title"]},
                       "description": "Carousel only: 2 to 6 slides, each a title of 50 characters or fewer and a body of 140 characters or fewer."},
            "check_these": {"type": "array", "items": {"type": "string"}, "description": "Every figure, date or claim on the graphic that a person must verify."},
        },
        "required": ["title", "check_these"],
    },
}

DESIGN_SYSTEM = (
    "You write the short words that appear on a branded social media graphic for Neverlank Chartered Accountants, a Zimbabwean firm of chartered accountants, "
    "registered auditors and tax practitioners. The voice is authoritative, trustworthy, modern and consultative: plain words, no jargon for its own sake. "
    "You do NOT draw anything: you only fill in the fields of the style you are given, and the app draws the picture in the firm's navy and gold.\n\n"
    "RULES: graphics have very little room, so every field must be short and punchy; never exceed the limits in the field descriptions. Use ONLY facts, figures, dates and names "
    "that appear in the post you are given: never invent or recall a tax rate, deadline, statistic, quote or source. If the style needs a date, figure, quote or source the post does "
    "not contain, leave that field empty and say so in check_these. Never name a client. Never promise or guarantee a result. No emojis, no hashtags, no web addresses. "
    "For a quote, use only words the post attributes to a named person. List in check_these every figure, date or claim on the graphic that a person must verify."
)

STYLE_HELP = {
    "deadline": "Fill kicker, date_label, title, body, cta.", "tip": "Fill kicker, title, and either body or points.", "quote": "Fill kicker (optional), title (the quote) and attribution.",
    "stat": "Fill kicker, big_value, caption and source.", "banner": "Fill kicker, title, body and cta.", "carousel": "Fill kicker, title (cover), body (cover), slides (2 to 6), cta (last slide headline) and caption.",
}


def _call_design(system, message):
    """(tool input dict, None) or (None, error). The only place the AI is called here (tests replace it)."""
    import news_watch as NW
    return NW._call_triage(system, message, tool=DESIGN_TOOL, max_tokens=2500)


def _post_text(post):
    parts = [f"POST TITLE: {post.title}", f"BRIEF:\n{post.brief or ''}"]
    for v in post.versions:
        if (v.body or "").strip() and v.status != "skipped":
            parts.append(f"WORDING FOR {PLATFORMS[v.platform]['label']}:\n{final_text(post, v)}")
    return "\n\n".join(parts)[:7000]


def design_with_ai(post, style, size_key, instruction=""):
    """(spec fields dict, flags list) or (None, error)."""
    label = G.SIZES[size_key][0]
    message = f"{_post_text(post)}\n\nSTYLE: {style} ({G.STYLES[style]}). {STYLE_HELP[style]}\nSIZE: {label}"
    if instruction:
        message += f"\n\nREQUESTED CHANGE (apply it, keep every fact as in the post): {instruction[:500]}"
    data, err = _call_design(DESIGN_SYSTEM, message)
    if not data:
        return None, err or "The AI did not answer."
    fields = {k: data.get(k) for k in ("kicker", "title", "body", "points", "date_label", "cta", "attribution", "big_value", "caption", "source", "slides")}
    spec = G.clean_spec(dict(fields, style=style, size=size_key))
    if G.is_blank(spec):
        return None, "The AI answered but wrote nothing usable."
    flags = [one_line(f, 300) for f in (data.get("check_these") or []) if str(f).strip()][:10]
    return spec, flags


def _default_size(post):
    for v in post.versions:
        if v.platform in G.PLATFORM_SIZE:
            return G.PLATFORM_SIZE[v.platform]
    return "linkedin"


def _image_or_404(image_id):
    return SocialImage.query.get_or_404(image_id)


def _save_spec(img, spec):
    img.spec_json = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    img.updated_at = datetime.utcnow()


def make_graphic(post, style, theme, size, use_ai):
    """Adds one graphic to the post (words by the AI when asked and possible). Returns (image, [(message, category)]); the caller commits."""
    notes = []
    style = style if style in G.STYLES else "tip"
    theme = theme if theme in G.THEMES else "navy"
    size = size if size in G.SIZES else _default_size(post)
    base = {"style": style, "theme": theme, "size": size, "title": "" if use_ai else (post.title if style != "carousel" else "")}
    img = SocialImage(post=post, label=f"{G.STYLES[style].split(' (')[0]} ({G.SIZES[size][0].split(' (')[0]})"[:80], created_by_id=current_user.id)
    spec = G.clean_spec(base)
    if use_ai:
        from social import ai_available
        if not ai_available():
            notes.append(("The AI is not switched on on this server (no API key), so the graphic's words are blank for you to write.", "warning"))
        elif not ((post.brief or "").strip() or any((v.body or "").strip() for v in post.versions)):
            notes.append(("Write the post (or its brief) first so the AI has facts to work from.", "warning"))
        else:
            drafted, info = design_with_ai(post, style, size)
            if drafted:
                spec, img.ai_used = dict(drafted, theme=theme), True
                if info:
                    post.ai_flags = "\n".join(filter(None, [post.ai_flags or ""] + info))[:3000]
                notes.append(("The AI wrote the words. Check them, then look at the picture.", "success"))
            else:
                notes.append((f"The AI could not write the words: {info} You can type them yourself.", "warning"))
    _save_spec(img, spec)
    db.session.add(img)
    return img, notes


@social_bp.route("/<int:post_id>/graphics/new", methods=["POST"])
@login_required
def graphic_new(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    img, notes = make_graphic(post, request.form.get("style"), request.form.get("theme"), request.form.get("size"), request.form.get("mode") == "ai")
    for text, cat in notes:
        flash(text, cat)
    _reopen_if_changed(post, True)
    post.updated_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("social.graphic_edit", image_id=img.id))


@social_bp.route("/graphics/<int:image_id>", methods=["GET", "POST"])
@login_required
def graphic_edit(image_id):
    _need_manage()
    img = _image_or_404(image_id)
    post = img.post
    if request.method == "POST":
        f = request.form
        raw = {k: f.get(k, "") for k in G.LIMITS}
        raw.update(style=f.get("style"), theme=f.get("theme"), size=f.get("size"), points=f.get("points", ""), paab=bool(f.get("paab")))
        raw["slides"] = [{"title": f.get(f"slide_title_{i}", ""), "body": f.get(f"slide_body_{i}", "")} for i in range(1, G.MAX_SLIDES + 1)]
        spec = G.clean_spec(raw)
        img.label = one_line(f.get("label"), 80) or img.label
        if spec != G.clean_spec(img.spec):
            _reopen_if_changed(post, True)
        _save_spec(img, spec)
        post.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("social.graphic_edit", image_id=img.id))
    spec = G.clean_spec(img.spec)
    problems = G.problems(spec)
    return render_template("social/graphic.html", img=img, post=post, spec=spec, fields=G.STYLE_FIELDS[spec["style"]], styles=G.STYLES, themes=G.THEMES, sizes=G.SIZES,
                           count=G.slide_count(spec), problems=problems, max_slides=G.MAX_SLIDES, limits=G.LIMITS, alt=G.alt_text(spec))


@social_bp.route("/graphics/<int:image_id>/redraft", methods=["POST"])
@login_required
def graphic_redraft(image_id):
    _need_manage()
    img = _image_or_404(image_id)
    post = img.post
    from social import ai_available
    spec = G.clean_spec(img.spec)
    if not ai_available():
        flash("The AI is not switched on on this server (no API key).", "warning")
    else:
        drafted, info = design_with_ai(post, spec["style"], spec["size"], one_line(request.form.get("instruction"), 500))
        if drafted:
            _save_spec(img, dict(drafted, theme=spec["theme"], paab=spec["paab"], footer=spec["footer"]))
            img.ai_used = True
            _reopen_if_changed(post, True)
            if info:
                post.ai_flags = "\n".join(filter(None, [post.ai_flags or ""] + info))[:3000]
            db.session.commit()
            flash("The AI wrote new words. Check them, then look at the picture.", "success")
        else:
            flash(f"The AI could not write the words: {info}", "warning")
    return redirect(url_for("social.graphic_edit", image_id=img.id))


@social_bp.route("/graphics/<int:image_id>/<int:n>.png")
@login_required
def graphic_png(image_id, n):
    _need_manage()
    img = _image_or_404(image_id)
    spec = G.clean_spec(img.spec)
    size = request.args.get("size")
    if size in G.SIZES:
        spec = G.at_size(spec, size)                                  # the same graphic at another platform's size
    if n < 1 or n > G.slide_count(spec):
        abort(404)
    png, _problems = G.render(spec, n - 1)
    headers = {"Cache-Control": "private, max-age=60"}
    if request.args.get("download"):
        name = re.sub(r"[^a-z0-9]+", "-", (img.post.title or "graphic").lower()).strip("-")[:40] or "graphic"
        suffix = f"-{size.replace('_', '-')}" if size in G.SIZES else ""
        headers["Content-Disposition"] = f'attachment; filename="neverlank-{name}{suffix}-{n:02d}.png"'
    return Response(png, mimetype="image/png", headers=headers)


@social_bp.route("/graphics/<int:image_id>.zip")
@login_required
def graphic_zip(image_id):
    _need_manage()
    img = _image_or_404(image_id)
    name = re.sub(r"[^a-z0-9]+", "-", (img.post.title or "graphic").lower()).strip("-")[:40] or "graphic"
    return Response(G.zip_of(G.clean_spec(img.spec), f"neverlank-{name}"), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="neverlank-{name}.zip"', "Cache-Control": "private, no-store"})


@social_bp.route("/graphics/<int:image_id>/sizes.zip")
@login_required
def graphic_sizes_zip(image_id):
    """The graphic at every platform's size in one download, so nothing has to be resized by hand."""
    _need_manage()
    img = _image_or_404(image_id)
    name = re.sub(r"[^a-z0-9]+", "-", (img.post.title or "graphic").lower()).strip("-")[:40] or "graphic"
    return Response(G.zip_all_sizes(G.clean_spec(img.spec), f"neverlank-{name}"), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="neverlank-{name}-all-sizes.zip"', "Cache-Control": "private, no-store"})


@social_bp.route("/graphics/<int:image_id>/delete", methods=["POST"])
@login_required
def graphic_delete(image_id):
    _need_manage()
    img = _image_or_404(image_id)
    post = img.post
    db.session.delete(img)
    _reopen_if_changed(post, True)
    db.session.commit()
    flash("Graphic deleted.", "info")
    return redirect(url_for("social.detail", post_id=post.id))
