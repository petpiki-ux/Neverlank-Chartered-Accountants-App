"""Automatic posting at a chosen date and time (part of the Social module; routes belong to the same blueprint).

How it works, in plain words
* The app does not hold the logins to LinkedIn, Facebook, Instagram or X. It hands each approved post (the text, the tracked link and the graphic) to a scheduling
  service the firm already uses (Make, Buffer, Zapier, n8n ...) through a web address that service gives out (a "webhook"). That service holds the platform logins and posts.
* A post is sent only when (1) a person with the approval permission has approved it, (2) it has a date and time with "send automatically" on, (3) that time has come and
  (4) the automatic checks still show no errors. Changing the wording or a graphic after approval cancels the approval, so nothing changed and unchecked is ever sent.
* If the time passed more than six hours ago (the app was asleep, or the post was approved late) it is held, not sent: press "Send now" or choose a new time.
* A failed send is tried again on the next run, up to three times, and then shown in red with the reason.
* Only LinkedIn, X, Facebook and Instagram are sent automatically. YouTube and TikTok need a video, and WhatsApp is always sent one person at a time, so those stay by hand.
* There is no background timer on this hosting, so the due posts are sent (a) whenever someone opens the Social pages and (b) whenever a timer calls
  /social/cron?token=SOCIAL_CRON_TOKEN (a free service such as cron-job.org can call it every 5 minutes; that also wakes a sleeping server).

Settings (Render > Environment): SOCIAL_WEBHOOK_URL (the service's web address), SOCIAL_WEBHOOK_SECRET (a long random text; sent with every request and
used to protect the graphics' web addresses and the reply address), SOCIAL_CRON_TOKEN, PUBLIC_BASE_URL (https://app.neverlank.co.zw), SOCIAL_SERVICE_NAME (optional label, e.g. Make).
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta

from flask import Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import graphics as G
from extensions import db
from models import SocialDispatch, SocialImage, SocialPost, SocialSchedule, SocialVersion
from social import (PLATFORMS, _need_manage, _refresh_posted, blocking, final_text, one_line, scan, social_bp, tracked_link)
from timeutil import CAT_OFFSET, to_cat

AUTO_PLATFORMS = ("linkedin", "x", "facebook", "instagram")      # the ones a scheduling service can post with text and pictures
MAX_TRIES = 3
LATE_HOURS = 6
# which graphic sizes suit which platform, best first
SIZE_FAMILY = {"linkedin": ("linkedin", "linkedin_portrait"), "x": ("x", "linkedin"), "facebook": ("facebook", "linkedin"),
               "instagram": ("instagram_portrait", "instagram"), "youtube": ("youtube",), "tiktok": ("story",)}


def _env(name):
    return (os.environ.get(name) or "").strip()


def webhook_url():
    return _env("SOCIAL_WEBHOOK_URL")


def secret():
    return _env("SOCIAL_WEBHOOK_SECRET")


def service_name():
    return _env("SOCIAL_SERVICE_NAME") or "the scheduling service"


def configured():
    url = webhook_url()
    return bool(url.startswith(("https://", "http://")) and secret())


def base_url():
    return (_env("PUBLIC_BASE_URL") or request.url_root).rstrip("/")


def to_utc(local_naive):
    return local_naive - CAT_OFFSET


def cat_text(value):
    return to_cat(value).strftime("%a %d %b %Y, %H:%M") if value else ""


# ------------------------------------------------------------------ the graphics' public web addresses

def _signature(image, n):
    key = (secret() or current_app.config.get("SECRET_KEY") or "x").encode()
    return hmac.new(key, f"{image.id}:{n}:{hashlib.sha256((image.spec_json or '').encode()).hexdigest()[:16]}".encode(), hashlib.sha256).hexdigest()[:32]


def media_url(image, n, base):
    return f"{base}/social/media/{image.id}/{n}.png?t={_signature(image, n)}"


def images_for(post, platform, base):
    """The pictures to send with this platform's wording: the graphics at this platform's size (every slide of a carousel), or else the first graphic."""
    imgs = list(post.images)
    chosen = []
    for size in SIZE_FAMILY.get(platform, ()):
        chosen = [i for i in imgs if i.spec.get("size") == size]
        if chosen:
            break
    if not chosen and imgs:
        chosen = imgs[:1]
    out = []
    for img in chosen:
        spec = img.spec
        for n in range(1, G.slide_count(spec) + 1):
            w, h = G.SIZES[spec.get("size") if spec.get("size") in G.SIZES else "linkedin"][1:3]
            out.append({"url": media_url(img, n, base), "width": w, "height": h, "alt": G.alt_text(spec)})
    return out


# ------------------------------------------------------------------ sending

def payload(post, version, base, event="post"):
    sched = post.schedule
    link = tracked_link(post, version.platform)
    return {"event": event, "post_id": post.id, "campaign": post.campaign, "platform": version.platform, "platform_label": PLATFORMS[version.platform]["label"],
            "text": final_text(post, version), "headline": version.headline or "", "link": link,
            "publish_at": (to_cat(sched.publish_at).strftime("%Y-%m-%dT%H:%M:00+02:00") if sched else ""),
            "images": images_for(post, version.platform, base),
            "callback_url": f"{base}/social/callback?token={secret()}"}


def _send(body):
    """(ok, error). The only place the web address is called (tests replace it)."""
    import requests
    try:
        r = requests.post(webhook_url(), json=body, headers={"X-Neverlank-Token": secret(), "User-Agent": "Neverlank-Social/1"}, timeout=15)
    except requests.RequestException as exc:
        return False, f"Could not reach {service_name()}: {exc.__class__.__name__}."
    if 200 <= r.status_code < 300:
        return True, ""
    return False, f"{service_name()} refused it (HTTP {r.status_code}). {one_line(r.text, 120)}"


def _dispatch_one(post, v, base, now):
    d = v.dispatch or SocialDispatch(post_id=post.id, version=v, platform=v.platform)
    if d.id is None:
        db.session.add(d)
    if v.platform == "instagram" and not post.images:
        ok, err = False, "Instagram needs a picture: add a graphic to the post."
        d.attempts = MAX_TRIES
    else:
        ok, err = _send(payload(post, v, base))
        d.attempts = (d.attempts or 0) + 1
    d.updated_at = now
    if ok:
        d.status, d.error, d.sent_at = "sent", "", now
        v.status, v.posted_at, v.posted_by_id = "posted", now, None
    else:
        d.status, d.error = "failed", err[:500]
    return ok


def _auto_versions(post):
    return [v for v in post.versions if v.platform in AUTO_PLATFORMS and v.status == "pending" and (v.body or "").strip()]


def send_post(post, base, now=None, force=False):
    """Sends every automatic platform of an approved post that has not been sent. Returns (sent, failed, problem_text)."""
    now = now or datetime.utcnow()
    if post.status != "Approved":
        return 0, 0, "It is not approved."
    errors = blocking(scan(post))
    if errors:
        return 0, 0, "The checks show a problem: " + errors[0]["text"]
    sent = failed = 0
    for v in _auto_versions(post):
        d = v.dispatch
        if d and d.status == "failed" and (d.attempts or 0) >= MAX_TRIES and not force:
            failed += 1
            continue
        if d and d.status == "sent" and not force:
            continue                                                   # never post the same wording twice by accident
        if force and d:
            d.attempts = 0
        if _dispatch_one(post, v, base, now):
            sent += 1
        else:
            failed += 1
    _refresh_posted(post)
    post.updated_at = now
    return sent, failed, ""


def process_due(base, now=None):
    """Sends what is due. Returns {'sent': n, 'failed': n, 'held': n}."""
    result = {"sent": 0, "failed": 0, "held": 0}
    if not configured():
        return result
    now = now or datetime.utcnow()
    due = (SocialSchedule.query.join(SocialPost, SocialPost.id == SocialSchedule.post_id)
           .filter(SocialSchedule.enabled.is_(True), SocialSchedule.publish_at <= now, SocialPost.status == "Approved").all())
    for sched in due:
        post = sched.post
        if not _auto_versions(post):
            continue
        if now - sched.publish_at > timedelta(hours=LATE_HOURS) and not any(v.dispatch and v.dispatch.status == "failed" and (v.dispatch.attempts or 0) < MAX_TRIES for v in post.versions):
            sched.note = f"Held: the time passed more than {LATE_HOURS} hours ago. Press Send now, or choose a new time."
            result["held"] += 1
            continue
        sent, failed, problem = send_post(post, base, now)
        sched.note = problem or (f"{failed} could not be sent; it will be tried again." if failed else "")
        result["sent"] += sent
        result["failed"] += failed
        if problem:
            result["held"] += 1
    db.session.commit()
    return result


def lazy_process():
    """Called when the Social pages open: sends what is due, never breaks the page."""
    try:
        if configured():
            process_due(base_url())
    except Exception as exc:                                         # noqa: BLE001 - a page must still open
        db.session.rollback()
        current_app.logger.warning("Social: automatic sending failed: %s", exc)


def failed_count():
    return SocialDispatch.query.filter(SocialDispatch.status == "failed").join(SocialPost, SocialPost.id == SocialDispatch.post_id).filter(SocialPost.status == "Approved").count()


# ------------------------------------------------------------------ routes

@social_bp.route("/<int:post_id>/schedule", methods=["POST"])
@login_required
def schedule_save(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    if post.status == "Posted":
        flash("It has already been posted.", "info")
        return redirect(url_for("social.detail", post_id=post.id))
    if request.form.get("clear"):
        if post.schedule:
            db.session.delete(post.schedule)
        db.session.commit()
        flash("The scheduled time was removed.", "info")
        return redirect(url_for("social.detail", post_id=post.id))
    try:
        local = datetime.strptime(f"{request.form.get('date', '').strip()} {request.form.get('time', '').strip()}", "%Y-%m-%d %H:%M")
    except ValueError:
        flash("Choose a date and a time.", "danger")
        return redirect(url_for("social.detail", post_id=post.id))
    when = to_utc(local)
    auto = bool(request.form.get("auto"))
    if auto and when < datetime.utcnow() - timedelta(minutes=2):
        flash("That time has already passed. Choose a later time, or press Send now once it is approved.", "danger")
        return redirect(url_for("social.detail", post_id=post.id))
    sched = post.schedule or SocialSchedule(post=post, created_by_id=current_user.id, publish_at=when)
    sched.publish_at, sched.enabled, sched.note, sched.updated_at = when, auto, "", datetime.utcnow()
    db.session.add(sched)
    post.planned_date = local.date()
    post.updated_at = datetime.utcnow()
    db.session.commit()
    if auto and not configured():
        flash("Saved, but nothing can be sent automatically yet: the scheduling service is not connected (see Automatic posting).", "warning")
    elif auto and post.status != "Approved":
        flash(f"Saved for {cat_text(when)}. It will be sent at that time only if it is approved by then.", "success")
    else:
        flash(f"Saved for {cat_text(when)}." + (" It will be sent automatically." if auto else " Automatic sending is off, so this is a reminder of the date."), "success")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/send-now", methods=["POST"])
@login_required
def send_now(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    if not configured():
        flash("The scheduling service is not connected yet (see Automatic posting).", "danger")
    elif post.status != "Approved":
        flash("It must be approved before it is sent.", "danger")
    else:
        if not post.schedule:
            db.session.add(SocialSchedule(post=post, publish_at=datetime.utcnow(), enabled=True, created_by_id=current_user.id))
        sent, failed, problem = send_post(post, base_url(), force=True)
        if post.schedule:
            post.schedule.note = problem or (f"{failed} could not be sent." if failed else "")
        db.session.commit()
        if problem:
            flash(problem, "danger")
        elif not sent and not failed:
            flash("Nothing to send: LinkedIn, X, Facebook and Instagram are the ones sent automatically.", "info")
        else:
            flash(f"Sent {sent} to {service_name()}." + (f" {failed} failed: see below." if failed else ""), "success" if sent and not failed else "warning")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/publishing")
@login_required
def publishing():
    _need_manage()
    lazy_process()
    now = datetime.utcnow()
    rows = (SocialSchedule.query.join(SocialPost, SocialPost.id == SocialSchedule.post_id).filter(SocialPost.status.in_(("Approved", "Pending approval", "Draft")))
            .order_by(SocialSchedule.publish_at).all())
    recent = SocialDispatch.query.order_by(SocialDispatch.updated_at.desc()).limit(15).all()
    return render_template("social/publishing.html", configured=configured(), url_set=bool(webhook_url()), secret_set=bool(secret()), cron_set=bool(_env("SOCIAL_CRON_TOKEN")),
                           base=base_url(), service=service_name(), rows=rows, recent=recent, now=now, cat_text=cat_text, platforms=PLATFORMS, auto=AUTO_PLATFORMS,
                           late_hours=LATE_HOURS, tries=MAX_TRIES)


@social_bp.route("/publishing/test", methods=["POST"])
@login_required
def publishing_test():
    _need_manage()
    if not configured():
        flash("Set SOCIAL_WEBHOOK_URL and SOCIAL_WEBHOOK_SECRET first.", "danger")
    else:
        ok, err = _send({"event": "test", "text": "Test from the Neverlank app. Nothing is posted.", "platform": "test", "images": [], "callback_url": f"{base_url()}/social/callback?token={secret()}"})
        flash(f"The test reached {service_name()}." if ok else err, "success" if ok else "danger")
    return redirect(url_for("social.publishing"))


@social_bp.route("/media/<int:image_id>/<int:n>.png")
def media(image_id, n):
    """A graphic's picture for the scheduling service to fetch (no sign-in; the address carries a signature and only approved posts' graphics are served)."""
    img = db.session.get(SocialImage, image_id)
    token = request.args.get("t") or ""
    if not img or not hmac.compare_digest(_signature(img, n), token) or img.post.status not in ("Approved", "Posted"):
        abort(404)
    spec = img.spec
    if n < 1 or n > G.slide_count(spec):
        abort(404)
    data, _ = G.render(spec, n - 1)
    return Response(data, mimetype="image/png", headers={"Cache-Control": "private, max-age=300"})


@social_bp.route("/callback", methods=["GET", "POST"])
def callback():
    """The scheduling service may tell the app how it went: ?token=SECRET with post_id, platform, and url (the live address) or ok=false and error."""
    given = request.values.get("token") or ""
    if not secret() or not hmac.compare_digest(secret().encode(), given.encode()):
        abort(404)
    data = request.get_json(silent=True) or request.values
    try:
        post_id = int(data.get("post_id"))
    except (TypeError, ValueError):
        abort(400)
    v = SocialVersion.query.filter_by(post_id=post_id, platform=str(data.get("platform") or "")).first_or_404()
    ok = str(data.get("ok", "true")).lower() not in ("false", "0", "no")
    d = v.dispatch
    if ok:
        url = str(data.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            v.post_url = url[:500]
    elif d:
        d.status, d.error = "failed", one_line(data.get("error") or "The service reported a failure.", 500)
        v.status, v.posted_at = "pending", None
        _refresh_posted(v.post)
    db.session.commit()
    return jsonify(ok=True)


@social_bp.route("/cron", methods=["GET", "POST"])
def cron():
    """For a timer: /social/cron?token=SOCIAL_CRON_TOKEN. Sends the posts that are due."""
    given = request.values.get("token") or ""
    token = _env("SOCIAL_CRON_TOKEN")
    if not token or not hmac.compare_digest(token.encode(), given.encode()):
        abort(404)
    return jsonify(ok=True, configured=configured(), **process_due(base_url()))
