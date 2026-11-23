"""Short videos for TikTok and YouTube Shorts (part of the Social module; see video.py for how they are made).

A video belongs to a post. The AI writes only the words (a hook, a few scenes, a closing line); the app draws the video in the firm's navy and gold. The words go through the same
automatic checks and approval as the post, and changing a video after approval sends the post back for approval. The MP4 is made on request in the background (one at a time) and
kept on the server's disk; changing the words makes it out of date until it is made again.
"""
import hmac
import json
import os
import re
import threading
from datetime import datetime, timedelta

from flask import Response, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

import config
import graphics as G
import video as V
from extensions import db
from models import SocialPost, SocialVideo
from social import (PLATFORMS, _DATE, _MONEY, _PLACEHOLDER, _PROMISE, _need_manage, _reopen_if_changed, client_mentions, final_text, one_line, social_bp)

VIDEO_TOOL = {
    "name": "design_video",
    "description": "Return the words for one short captioned video. You do not draw or film: the app makes the video from these words.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kicker": {"type": "string", "description": "A small label in capitals shown with the opening line, 40 characters or fewer, e.g. TAX TIP. Empty if none."},
            "hook": {"type": "string", "description": "The opening line that makes a viewer stop scrolling: a question or a surprising true statement from the post, 70 characters or fewer."},
            "scenes": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "object", "properties": {"title": {"type": "string", "description": "One idea, 45 characters or fewer."}, "body": {"type": "string", "description": "One short sentence, 110 characters or fewer."}}, "required": ["title"]},
                       "description": "Two to four scenes, one idea each, in the order a viewer should take them in."},
            "cta": {"type": "string", "description": "The closing line telling the viewer what to do next, 55 characters or fewer."},
            "caption": {"type": "string", "description": "The line under the closing line, 100 characters or fewer, e.g. Link in our profile."},
            "check_these": {"type": "array", "items": {"type": "string"}, "description": "Every figure, date or claim in the video that a person must verify."},
        },
        "required": ["hook", "scenes", "check_these"],
    },
}

VIDEO_SYSTEM = (
    "You write the words for a short captioned video (15 to 40 seconds, silent, words on the screen) for Neverlank Chartered Accountants, a Zimbabwean firm of chartered accountants, "
    "registered auditors and tax practitioners. The voice is authoritative, trustworthy, modern and consultative: plain words. You do NOT draw or film anything: you only fill in the fields, "
    "and the app makes the video in the firm's navy and gold.\n\n"
    "SHAPE: an opening line (the hook) that makes a viewer stop; two to four scenes, each ONE idea in a short title and at most one short sentence; then a closing line that says what to do next. "
    "Short sentences. Nothing a viewer must read for more than three seconds.\n\n"
    "RULES: use ONLY facts, figures, dates and names that appear in the post you are given: never invent or recall a tax rate, deadline, statistic, quote or source. If the post has no date or "
    "figure, the video has none. Never name a client. Never promise or guarantee a result. No emojis, no hashtags, no web addresses. List in check_these every figure, date or claim in the video "
    "that a person must verify."
)


def _call_video(system, message):
    """(tool input dict, None) or (None, error). The only place the AI is called here (tests replace it)."""
    import news_watch as NW
    return NW._call_triage(system, message, tool=VIDEO_TOOL, max_tokens=2500)


def _post_text(post):
    parts = [f"POST TITLE: {post.title}", f"BRIEF:\n{post.brief or ''}"]
    for v in post.versions:
        if (v.body or "").strip() and v.status != "skipped" and v.platform != "whatsapp":
            parts.append(f"WORDING FOR {PLATFORMS[v.platform]['label']}:\n{final_text(post, v)}")
    return "\n\n".join(parts)[:7000]


def design_with_ai(post, instruction=""):
    """(spec fields dict, flags) or (None, error)."""
    message = _post_text(post)
    if instruction:
        message += f"\n\nREQUESTED CHANGE (apply it, keep every fact as in the post): {instruction[:500]}"
    data, err = _call_video(VIDEO_SYSTEM, message)
    if not data:
        return None, err or "The AI did not answer."
    spec = V.clean_spec({k: data.get(k) for k in ("kicker", "hook", "scenes", "cta", "caption")})
    if V.is_blank(spec):
        return None, "The AI answered but wrote nothing usable."
    flags = [one_line(f, 300) for f in (data.get("check_these") or []) if str(f).strip()][:10]
    return spec, flags


# ------------------------------------------------------------------ files and the background job

def video_dir():
    return os.path.join(config.DATA_DIR, "social_videos")


def file_path(video):
    return os.path.join(video_dir(), video.file_name) if video.file_name else ""


def is_current(video):
    """The file on disk exists and was made from the words as they are now."""
    path = file_path(video)
    return video.status == "ready" and video.file_key == V.key_of(video.spec) and bool(path) and os.path.exists(path)


def display_status(video):
    if video.status == "rendering" and video.updated_at and datetime.utcnow() - video.updated_at > timedelta(minutes=10):
        return "failed"                                              # the server restarted while it was being made
    if video.status == "ready" and not is_current(video):
        return "stale"
    return video.status or "none"


_job_lock = threading.Lock()
_progress = {}


def _forget_file(video):
    path = file_path(video)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    video.file_name, video.file_key, video.seconds = "", "", None


def _worker(app, video_id, spec, key):
    with app.app_context():
        try:
            name = f"video-{video_id}-{key[:12]}.mp4"
            ok, err = V.render_to_temp(spec, video_dir(), name, progress=lambda f: _progress.__setitem__(video_id, f))
            video = db.session.get(SocialVideo, video_id)
            if video is None:
                return
            if ok and key == V.key_of(video.spec):
                old = file_path(video)
                video.status, video.error, video.file_name, video.file_key = "ready", "", name, key
                video.seconds, video.rendered_at = V.total_seconds(spec), datetime.utcnow()
                if old and os.path.basename(old) != name and os.path.exists(old):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
            elif ok:                                                  # the words changed while it was being made
                video.status, video.error = "none", ""
                try:
                    os.remove(os.path.join(video_dir(), name))
                except OSError:
                    pass
            else:
                video.status, video.error = "failed", (err or "")[:500]
            video.updated_at = datetime.utcnow()
            db.session.commit()
        except Exception as exc:                                       # noqa: BLE001 - the job must always finish
            db.session.rollback()
            try:
                video = db.session.get(SocialVideo, video_id)
                if video:
                    video.status, video.error, video.updated_at = "failed", f"The video could not be made: {exc}"[:500], datetime.utcnow()
                    db.session.commit()
            except Exception:
                db.session.rollback()
        finally:
            _progress.pop(video_id, None)
            _job_lock.release()


def start_render(video, wait=False):
    """Starts making the MP4 in the background. Returns (started, message)."""
    spec = V.clean_spec(video.spec)
    problems = V.problems(spec)
    if problems:
        return False, problems[0]
    if V.ffmpeg_path() is None:
        return False, "The video maker (ffmpeg) is not installed on this server."
    if display_status(video) == "rendering":
        return False, "It is already being made."
    if not _job_lock.acquire(blocking=False):
        return False, "Another video is being made. Try again in a minute."
    video.status, video.error, video.updated_at = "rendering", "", datetime.utcnow()
    db.session.commit()
    app = current_app._get_current_object()
    t = threading.Thread(target=_worker, args=(app, video.id, spec, V.key_of(spec)), daemon=True)
    t.start()
    if wait:
        t.join()
        db.session.refresh(video)
    return True, "Making the video."


# ------------------------------------------------------------------ the public address for the scheduling service

def _vsig(video):
    key = (os.environ.get("SOCIAL_WEBHOOK_SECRET") or current_app.config.get("SECRET_KEY") or "x").encode()
    return hmac.new(key, f"video:{video.id}:{video.file_key}".encode(), "sha256").hexdigest()[:32]


def vmedia_url(video, base):
    return f"{base}/social/vmedia/{video.id}.mp4?t={_vsig(video)}"


def video_for(post):
    """The first video of the post that is made and up to date, or None."""
    for v in post.videos:
        if is_current(v):
            return v
    return None


# ------------------------------------------------------------------ the checks (used by social.scan)

def scan_items(post):
    out = []
    for v in post.videos:
        spec = v.spec
        name = v.label or "Video"
        text = V.text_of(spec)
        for problem in V.problems(spec):
            out.append({"level": "error", "platform": "", "text": f"Video '{name}': {problem}"})
        if _PLACEHOLDER.search(text):
            out.append({"level": "error", "platform": "", "text": f"Video '{name}': a [bracket] or {{brace}} placeholder is still in the words."})
        if _MONEY.search(text):
            out.append({"level": "warn", "platform": "", "text": f"Video '{name}': it shows a figure. Confirm it against the source."})
        if _DATE.search(text):
            out.append({"level": "warn", "platform": "", "text": f"Video '{name}': it shows a date. Confirm it against the Act, Statutory Instrument or ZIMRA notice."})
        if _PROMISE.search(text):
            out.append({"level": "warn", "platform": "", "text": f"Video '{name}': wording that sounds like a promise or a tax-avoidance claim."})
        if display_status(v) != "ready":
            out.append({"level": "warn", "platform": "", "text": f"Video '{name}': the video file has not been made (or is out of date). Press Make the video."})
    return out


def texts(post):
    return [V.text_of(v.spec) for v in post.videos]


# ------------------------------------------------------------------ routes

def _video_or_404(video_id):
    return SocialVideo.query.get_or_404(video_id)


def _save_spec(video, spec, ai_used=None):
    video.spec_json = json.dumps(V.clean_spec(spec), ensure_ascii=False, sort_keys=True)
    video.updated_at = datetime.utcnow()
    if ai_used is not None:
        video.ai_used = ai_used
    if video.status in ("ready", "failed"):
        pass                                                         # shown as out of date by display_status until it is made again


@social_bp.route("/<int:post_id>/videos/new", methods=["POST"])
@login_required
def video_new(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    theme = request.form.get("theme") if request.form.get("theme") in G.THEMES else "navy"
    use_ai = request.form.get("mode") == "ai"
    video = SocialVideo(post=post, label="Short video", created_by_id=current_user.id)
    spec = V.clean_spec({"theme": theme, "hook": "" if use_ai else post.title})
    if use_ai:
        from social import ai_available
        if not ai_available():
            flash("The AI is not switched on on this server (no API key), so the words are blank for you to write.", "warning")
        elif not ((post.brief or "").strip() or any((v.body or "").strip() for v in post.versions)):
            flash("Write the post (or its brief) first so the AI has facts to work from.", "warning")
        else:
            drafted, info = design_with_ai(post, one_line(request.form.get("instruction"), 500))
            if drafted:
                spec, video.ai_used = dict(drafted, theme=theme), True
                if info:
                    post.ai_flags = "\n".join(filter(None, [post.ai_flags or ""] + info))[:3000]
                flash("The AI wrote the words. Check them, then make the video.", "success")
            else:
                flash(f"The AI could not write the words: {info} You can type them yourself.", "warning")
    _save_spec(video, spec)
    db.session.add(video)
    _reopen_if_changed(post, True)
    post.updated_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("social.video_edit", video_id=video.id))


@social_bp.route("/videos/<int:video_id>", methods=["GET", "POST"])
@login_required
def video_edit(video_id):
    _need_manage()
    video = _video_or_404(video_id)
    post = video.post
    if request.method == "POST":
        form = request.form
        spec = {"theme": form.get("theme"), "kicker": form.get("kicker"), "hook": form.get("hook"), "cta": form.get("cta"), "caption": form.get("caption"),
                "footer": form.get("footer"), "paab": bool(form.get("paab")), "scenes": []}
        for i in range(1, V.MAX_SCENES + 1):
            spec["scenes"].append({"title": form.get(f"scene_title_{i}"), "body": form.get(f"scene_body_{i}")})
        new = V.clean_spec(spec)
        changed = V.key_of(new) != V.key_of(video.spec)
        label = one_line(form.get("label"), 80) or video.label
        video.label = label
        _save_spec(video, new)
        if changed:
            _reopen_if_changed(post, True)
        post.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Saved." + (" Make the video again to see the change." if changed and video.file_key else ""), "success")
        return redirect(url_for("social.video_edit", video_id=video.id))
    spec = V.clean_spec(video.spec)
    scenes = V.scenes_of(spec)
    rows = spec["scenes"] + [{"title": "", "body": ""}] * (V.MAX_SCENES - len(spec["scenes"]))
    from social import ai_available
    return render_template("social/video.html", video=video, post=post, spec=spec, rows=rows, scenes=scenes, total=V.total_seconds(spec), problems=V.problems(spec),
                           status=display_status(video), themes=G.THEMES, ai=ai_available(), max_scenes=V.MAX_SCENES, min_scenes=V.MIN_SCENES,
                           ffmpeg=V.ffmpeg_path() is not None)


@social_bp.route("/videos/<int:video_id>/make", methods=["POST"])
@login_required
def video_make(video_id):
    _need_manage()
    video = _video_or_404(video_id)
    started, message = start_render(video)
    flash(message, "success" if started else "warning")
    return redirect(url_for("social.video_edit", video_id=video.id))


@social_bp.route("/videos/<int:video_id>/status.json")
@login_required
def video_status(video_id):
    _need_manage()
    video = _video_or_404(video_id)
    status = display_status(video)
    return jsonify(status=status, progress=round(_progress.get(video.id, 1.0 if status == "ready" else 0.0), 3), error=video.error or "", seconds=video.seconds)


@social_bp.route("/videos/<int:video_id>/redraft", methods=["POST"])
@login_required
def video_redraft(video_id):
    _need_manage()
    video = _video_or_404(video_id)
    post = video.post
    from social import ai_available
    if not ai_available():
        flash("The AI is not switched on on this server (no API key).", "warning")
    else:
        spec = V.clean_spec(video.spec)
        drafted, info = design_with_ai(post, one_line(request.form.get("instruction"), 500))
        if drafted:
            _save_spec(video, dict(drafted, theme=spec["theme"], paab=spec["paab"], footer=spec["footer"]), ai_used=True)
            _reopen_if_changed(post, True)
            if info:
                post.ai_flags = "\n".join(filter(None, [post.ai_flags or ""] + info))[:3000]
            db.session.commit()
            flash("The AI wrote new words. Check them, then make the video again.", "success")
        else:
            flash(f"The AI could not write the words: {info}", "warning")
    return redirect(url_for("social.video_edit", video_id=video.id))


@social_bp.route("/videos/<int:video_id>/scene/<int:n>.png")
@login_required
def video_scene(video_id, n):
    _need_manage()
    video = _video_or_404(video_id)
    scenes = V.scenes_of(video.spec)
    if n < 1 or n > max(1, len(scenes)):
        abort(404)
    png, _ = V.still(video.spec, n - 1)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "private, max-age=60"})


@social_bp.route("/videos/<int:video_id>.mp4")
@login_required
def video_file(video_id):
    _need_manage()
    video = _video_or_404(video_id)
    if not is_current(video):
        abort(404)
    name = re.sub(r"[^a-z0-9]+", "-", (video.post.title or "video").lower()).strip("-")[:40] or "video"
    return send_file(file_path(video), mimetype="video/mp4", conditional=True, as_attachment=bool(request.args.get("download")), download_name=f"neverlank-{name}.mp4",
                     max_age=60)


@social_bp.route("/vmedia/<int:video_id>.mp4")
def vmedia(video_id):
    """The video for the scheduling service to fetch (no sign-in; the address carries a signature and only approved posts' finished videos are served)."""
    video = db.session.get(SocialVideo, video_id)
    token = request.args.get("t") or ""
    if not video or not is_current(video) or not hmac.compare_digest(_vsig(video), token) or video.post.status not in ("Approved", "Posted"):
        abort(404)
    return send_file(file_path(video), mimetype="video/mp4", conditional=True, max_age=300)


@social_bp.route("/videos/<int:video_id>/delete", methods=["POST"])
@login_required
def video_delete(video_id):
    _need_manage()
    video = _video_or_404(video_id)
    post = video.post
    _forget_file(video)
    db.session.delete(video)
    _reopen_if_changed(post, True)
    post.updated_at = datetime.utcnow()
    db.session.commit()
    flash("The video was deleted.", "info")
    return redirect(url_for("social.detail", post_id=post.id))
