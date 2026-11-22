"""Social media management: plan, draft, check, approve, post by hand, and track the firm's posts on WhatsApp, LinkedIn, X, Facebook, Instagram, YouTube and TikTok.

How it works, in plain words
* One post is written once (or drafted by the AI from a short brief) and adapted for each platform, with that platform's length limit shown live.
* Nothing can be posted until a person with the approval permission has ticked the compliance checklist and approved it. Changing the wording after approval
  sends it back for approval again.
* The app does not post for you: each platform has a "Copy" and a "Copy and open" button, then you press "Mark as posted" and paste the post's address.
  (Posting for you needs a separate approved developer account on each platform; this module is built so that can be added one platform at a time.)
* Links in a post are tracked: every platform gets its own copy with utm tags, so the Leads page shows which post and platform brought each sign-up.
* Results (views, likes, comments, shares, clicks) are typed in by hand on the post.
* WhatsApp: a list of people who agreed to be messaged, message templates, click-to-send links and a log of what was sent and received. Messages are sent one by
  one by a person from the firm's own WhatsApp: no bulk sending.

Settings: the AI drafting uses ANTHROPIC_API_KEY (and SOCIAL_MODEL / NEWS_WATCH_MODEL / ANTHROPIC_MODEL if set), like News Watch.
"""
import csv
import io
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode, urlparse, urlsplit, urlunsplit, parse_qsl

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from extensions import db
from models import (POST_STATUSES, Client, MarketingLead, NewsItem, SocialAccount, SocialPost, SocialVersion, User, WhatsAppContact, WhatsAppMessage,
                    user_has_permission)
from timeutil import today_cat

social_bp = Blueprint("social", __name__, url_prefix="/social")

# key: label, character limit, what works well, link that opens the platform's own "write a post" screen ({text} = the post), where to go otherwise, hashtag range, needs a picture or video
PLATFORMS = {
    "linkedin": dict(label="LinkedIn", limit=3000, ideal="800 to 1,300 characters; first two lines must earn the click on 'see more'", hashtags=(3, 5, 10),
                     compose="https://www.linkedin.com/feed/?shareActive=true&text={text}", home="https://www.linkedin.com/", visual=False),
    "x": dict(label="X (Twitter)", limit=280, ideal="under 280 characters; a link counts as 23", hashtags=(0, 2, 5),
              compose="https://x.com/intent/post?text={text}", home="https://x.com/", visual=False),
    "facebook": dict(label="Facebook", limit=63206, ideal="under 500 characters; a photo or a link preview helps", hashtags=(0, 3, 10),
                     compose="", home="https://www.facebook.com/", visual=False),
    "instagram": dict(label="Instagram", limit=2200, ideal="first line is the hook; needs a picture, carousel or reel; the link goes in your profile, not the caption", hashtags=(5, 15, 30),
                      compose="", home="https://www.instagram.com/", visual=True),
    "youtube": dict(label="YouTube", limit=5000, ideal="title up to 100 characters; description up to 5,000, first two lines matter most", hashtags=(0, 3, 15),
                    compose="", home="https://studio.youtube.com/", visual=True),
    "tiktok": dict(label="TikTok", limit=2200, ideal="a short caption with a hook; needs a video", hashtags=(3, 5, 10),
                   compose="", home="https://www.tiktok.com/upload", visual=True),
    "whatsapp": dict(label="WhatsApp", limit=700, ideal="a Status or Channel update: short and plain; people reply to you directly", hashtags=(0, 0, 3),
                     compose="https://wa.me/?text={text}", home="https://web.whatsapp.com/", visual=False),
}
PLATFORM_ORDER = tuple(PLATFORMS)
HEADLINE_LIMIT = {"youtube": 100}
X_LINK_LENGTH = 23
PILLARS = ("Authority (expert view)", "Education (how it works)", "Proof (results and case studies)", "Offer (free check, calendar)", "Firm life and people")

# What the approver confirms before a post can be approved: key, wording
CHECKLIST = (
    ("client", "No client is named or identifiable (names, figures, photos), or I hold their written consent."),
    ("facts", "Every figure, date, rate and rule is checked against the source (the Act, Statutory Instrument or ZIMRA notice), not against a news story."),
    ("advice", "Where it explains tax or law, it says it is general information and not advice."),
    ("promise", "It makes no promise or guarantee of results and does not promote tax avoidance or evasion."),
    ("rules", "It follows the professional rules on advertising and publicity (PAAB and ICAZ) for a firm of chartered accountants and registered auditors."),
    ("details", "The firm's name, contacts and registration details, and the link, are right."),
    ("tone", "Spelling, tone and the picture or video are fine, and it reads like Neverlank."),
)

_URL = re.compile(r"https?://\S+")
_HASHTAG = re.compile(r"(?<!\w)#\w+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MONEY = re.compile(r"(?:US\$|\$|USD|ZWG|ZiG|ZWL)\s?\d|\d[\d,\.]*\s?(?:%|percent|million|billion|USD|ZWG|ZiG)", re.I)
_DATE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b", re.I)
_PROMISE = re.compile(r"\b(guarantee[sd]?|100\s?%|risk[- ]free|never pay tax|avoid(?:ing)? (?:paying )?tax|evade|evasion|no tax|zero tax|beat zimra)\b", re.I)
_TAXY = re.compile(r"\b(tax|vat|paye|zimra|withholding|income tax|capital gains|finance act|statutory|the act|law)\b", re.I)
_DISCLAIMER = re.compile(r"(general information|not (?:tax |legal |financial )?advice|for information only|general guidance)", re.I)
_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,60}\]|\{(?!link\})[^}\n]{1,40}\}")


# ------------------------------------------------------------------ permissions and small helpers

def can_manage(user=None):
    user = user or current_user
    return bool(user and user.is_authenticated and user_has_permission(user, "manage_social"))


def can_approve(user=None):
    user = user or current_user
    return bool(user and user.is_authenticated and user_has_permission(user, "approve_social"))


def _need_manage():
    if not can_manage():
        abort(403, description="You do not have permission to manage social media. Ask an Admin (Team > Permissions).")


def _need_approve():
    if not can_approve():
        abort(403, description="You do not have permission to approve social media posts. Ask an Admin (Team > Permissions).")


def clean(value, limit=None):
    text = _CONTROL.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return text[:limit] if limit else text


def one_line(value, limit):
    return re.sub(r"\s+", " ", _CONTROL.sub("", str(value or ""))).strip()[:limit]


def safe_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    parts = urlparse(value)
    return value[:500] if parts.scheme in ("http", "https") and parts.netloc else None


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:30] or "post"


def tracked_link(post, platform):
    """The post's link with this platform's tracking tags added (the Leads page reads them); '' when the post has no link."""
    if not post.link_url:
        return ""
    parts = urlsplit(post.link_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("utm_source", "utm_medium", "utm_campaign")]
    query += [("utm_source", platform), ("utm_medium", "social"), ("utm_campaign", post.campaign or slug(post.title))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def final_text(post, version):
    """What actually gets pasted into the platform: the wording with {link} replaced by the tracked link."""
    link = tracked_link(post, version.platform)
    text = (version.body or "").replace("{link}", link)
    return re.sub(r"[ \t]+\n", "\n", text).strip() if link else re.sub(r"\n{3,}", "\n\n", text.replace("{link}", "")).strip()


def measure(platform, text):
    """Length as the platform counts it (on X every link counts as 23 characters)."""
    if platform == "x":
        text = _URL.sub("x" * X_LINK_LENGTH, text)
    return len(text)


def hashtags(text):
    return _HASHTAG.findall(text or "")


def _words(text):
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "


def client_mentions(text):
    """Clients whose name appears in the text (a confidentiality risk). Names are compared without 'Pvt', 'Ltd' and similar endings."""
    import news_watch as NW
    haystack = _words(text)
    found = []
    for (name,) in db.session.query(Client.name).all():
        tokens = NW._norm_name(name)
        if not tokens or (len(tokens) == 1 and len(tokens[0]) < 5):
            continue
        if f" {' '.join(tokens)} " in haystack:
            found.append(name)
    return found


def scan(post):
    """Automatic checks of every version: [{level: error|warn|info, platform, text}]. Errors stop approval; warnings ask the approver to look."""
    out = []
    versions = [v for v in post.versions if v.status != "skipped"]
    if not any((v.body or "").strip() for v in versions):
        out.append({"level": "error", "platform": "", "text": "There is no wording yet."})
    for v in versions:
        cfg = PLATFORMS.get(v.platform)
        if not cfg or not (v.body or "").strip():
            continue
        label = cfg["label"]
        text = final_text(post, v)
        n = measure(v.platform, text)
        if n > cfg["limit"]:
            out.append({"level": "error", "platform": v.platform, "text": f"{label}: {n:,} characters, {n - cfg['limit']:,} over the {cfg['limit']:,} limit."})
        if v.platform in HEADLINE_LIMIT and len(v.headline or "") > HEADLINE_LIMIT[v.platform]:
            out.append({"level": "error", "platform": v.platform, "text": f"{label}: the title is over {HEADLINE_LIMIT[v.platform]} characters."})
        if v.platform == "youtube" and not (v.headline or "").strip():
            out.append({"level": "error", "platform": v.platform, "text": "YouTube: add a title."})
        if _PLACEHOLDER.search(v.body or "") or _PLACEHOLDER.search(v.headline or ""):
            out.append({"level": "error", "platform": v.platform, "text": f"{label}: a [bracket] or {{brace}} placeholder is still in the wording."})
        tags = hashtags(text)
        low, high, maximum = cfg["hashtags"]
        if len(tags) > maximum:
            out.append({"level": "error" if v.platform == "instagram" and len(tags) > 30 else "warn", "platform": v.platform, "text": f"{label}: {len(tags)} hashtags; {high} or fewer reads better."})
        elif len(tags) > high:
            out.append({"level": "warn", "platform": v.platform, "text": f"{label}: {len(tags)} hashtags; {high} or fewer reads better."})
        if cfg["visual"] and not (v.media_note or "").strip():
            out.append({"level": "warn", "platform": v.platform, "text": f"{label}: say which picture or video goes with it."})
        if v.platform == "instagram" and tracked_link(post, v.platform) and tracked_link(post, v.platform) in text:
            out.append({"level": "info", "platform": v.platform, "text": "Instagram does not make links in captions clickable: put the link in your profile."})
        if _MONEY.search(text):
            out.append({"level": "warn", "platform": v.platform, "text": f"{label}: it has figures. Confirm each against the source."})
        if _DATE.search(text):
            out.append({"level": "warn", "platform": v.platform, "text": f"{label}: it has a date. Confirm it against the Act, Statutory Instrument or ZIMRA notice."})
        if _PROMISE.search(text):
            out.append({"level": "warn", "platform": v.platform, "text": f"{label}: wording that sounds like a promise or a tax-avoidance claim ('{_PROMISE.search(text).group(0)}')."})
        if _TAXY.search(text) and not _DISCLAIMER.search(text) and v.platform in ("linkedin", "facebook", "youtube", "instagram", "tiktok") and len(text) > 200:
            out.append({"level": "warn", "platform": v.platform, "text": f"{label}: it explains tax or law but does not say it is general information, not advice."})
    joined = "\n".join(f"{v.headline or ''}\n{v.body or ''}" for v in versions) + f"\n{post.title}"
    hits = client_mentions(joined)
    if hits:
        out.append({"level": "warn", "platform": "", "text": "It mentions a client by name: " + ", ".join(sorted(set(hits))[:5]) + ". Only with their written consent."})
    if post.link_url and not any("{link}" in (v.body or "") or tracked_link(post, v.platform) in (v.body or "") for v in versions):
        out.append({"level": "info", "platform": "", "text": "The post has a link but no wording uses {link}, so nothing will be tracked."})
    return out


def blocking(issues):
    return [i for i in issues if i["level"] == "error"]


# ------------------------------------------------------------------ AI drafting

DRAFT_TOOL = {
    "name": "draft_posts",
    "description": "Return the post wording for each requested platform, and the facts a person must check before it is approved.",
    "input_schema": {
        "type": "object",
        "properties": {
            "posts": {"type": "array", "items": {"type": "object", "properties": {
                "platform": {"type": "string", "enum": list(PLATFORMS)},
                "headline": {"type": "string", "description": "YouTube title only (100 characters or fewer); empty for every other platform."},
                "body": {"type": "string", "description": "The post, ready to paste. Put {link} exactly where the link belongs, if a link was supplied."},
                "media_note": {"type": "string", "description": "For Instagram, TikTok and YouTube: the picture, carousel or video it needs, described so a person can make it. Otherwise empty."},
            }, "required": ["platform", "body"]}},
            "check_these": {"type": "array", "items": {"type": "string"}, "description": "Every figure, date, rate, rule or claim in the posts that a person must verify against the source."},
        },
        "required": ["posts", "check_these"],
    },
}

DRAFT_SYSTEM = (
    "You write social media posts for Neverlank Chartered Accountants, a Zimbabwean firm of chartered accountants, registered auditors and tax practitioners with six "
    "service lines: audit and assurance, tax, accounting, business advisory, company secretarial, and business intelligence and technology. Its Managing Partner is a "
    "Duff Award winner. The voice is authoritative, trustworthy, modern and consultative: plain words, no jargon for its own sake, speaking about risk protection, cash flow "
    "clarity, regulatory peace of mind and smarter decisions. Brand colours are navy and gold.\n\n"
    "You are given a BRIEF written by a team member, the platforms wanted with their limits, and sometimes a link. Write one version per platform, each in the style that works "
    "on that platform: LinkedIn a short professional post with a strong first two lines and white space; X a tight post under its limit; Facebook friendly and short; Instagram "
    "a caption with a hook and a picture or carousel idea; YouTube a title and a description for a short video; TikTok a short caption and a video idea; WhatsApp a short, plain "
    "Status or Channel message.\n\n"
    "HARD RULES: use ONLY facts, figures, dates, names and rules that appear in the brief. Never invent or recall a tax rate, threshold, deadline, section or statistic. If the "
    "brief is thin, write a post that stays general and ask for the missing facts in check_these. Never name a client or describe one so they could be identified. Never promise "
    "or guarantee a result, and never encourage tax avoidance or evasion. Where a post explains tax or law, end with 'General information, not advice.' If the brief is a "
    "news story, say it was 'reported' and tell people to check the source; never present news as law. No emojis except at most one on WhatsApp or Instagram. "
    "Use the exact text {link} where a link belongs, only if a link was supplied, and never write a web address yourself. Do not use placeholders in [brackets]. "
    "Stay within each platform's limit (on X a link counts as 23 characters). Hashtags: LinkedIn 3 to 5, X at most 2, Instagram 5 to 15, TikTok 3 to 5, others at most 3. "
    "List in check_these every figure, date, rate, rule or claim that a person must check before posting."
)


def _call_ai(system, message):
    """(tool input dict, None) or (None, error). The only place the AI is called here (tests replace it)."""
    import news_watch as NW
    return NW._call_triage(system, message, tool=DRAFT_TOOL, max_tokens=5000)


def ai_available():
    import news_watch as NW
    return NW.is_configured()


def draft_with_ai(post, platforms):
    """Fill the post's versions from its brief. Returns (True, '') or (False, reason). Nothing is lost if it fails."""
    lines = [f"BRIEF:\n{post.brief or post.title}", f"\nPILLAR: {post.pillar or 'not specified'}", f"TITLE: {post.title}",
             "LINK SUPPLIED: yes (use {link})" if post.link_url else "LINK SUPPLIED: no (do not use {link})", "\nPLATFORMS WANTED:"]
    for key in platforms:
        cfg = PLATFORMS[key]
        lines.append(f"- {key}: {cfg['label']}, limit {cfg['limit']} characters. {cfg['ideal']}.")
    data, err = _call_ai(DRAFT_SYSTEM, "\n".join(lines))
    if not data:
        return False, err or "The AI did not answer."
    wanted = {v.platform: v for v in post.versions}
    made = 0
    for item in data.get("posts") or []:
        key = item.get("platform")
        if key not in PLATFORMS or key not in platforms:
            continue
        body = clean(item.get("body"), 6000)
        if not body:
            continue
        v = wanted.get(key)
        if v is None:
            v = SocialVersion(post=post, platform=key)
            db.session.add(v)
        v.body = body
        v.headline = one_line(item.get("headline"), 150) if key in HEADLINE_LIMIT else ""
        v.media_note = clean(item.get("media_note"), 1000)
        made += 1
    flags = [one_line(f, 300) for f in (data.get("check_these") or []) if str(f).strip()][:20]
    post.ai_flags = "\n".join(flags)
    post.ai_used = True
    return (True, "") if made else (False, "The AI answered but wrote nothing usable.")


# ------------------------------------------------------------------ calendar and lists

def _calendar(posts_query, weeks=4):
    today = today_cat()
    monday = today - timedelta(days=today.weekday())
    end = monday + timedelta(days=7 * weeks)
    scheduled = posts_query.filter(SocialPost.planned_date >= monday, SocialPost.planned_date < end).order_by(SocialPost.planned_date, SocialPost.id).all()
    by_day = {}
    for p in scheduled:
        by_day.setdefault(p.planned_date, []).append(p)
    return [[(monday + timedelta(days=7 * w + d), by_day.get(monday + timedelta(days=7 * w + d), [])) for d in range(7)] for w in range(weeks)], today


def pending_count():
    try:
        return SocialPost.query.filter_by(status="Pending approval").count()
    except Exception:
        db.session.rollback()
        return 0


def _parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _selected_platforms(form):
    return [p for p in PLATFORM_ORDER if p in form.getlist("platforms")]


def _ensure_accounts():
    for key in PLATFORM_ORDER:
        if db.session.get(SocialAccount, key) is None:
            db.session.add(SocialAccount(platform=key))
    db.session.commit()


@social_bp.route("/")
@login_required
def index():
    _need_manage()
    query = SocialPost.query
    status = request.args.get("status") or ""
    if status in POST_STATUSES:
        query = query.filter(SocialPost.status == status)
    platform = request.args.get("platform") or ""
    if platform in PLATFORMS:
        query = query.filter(SocialPost.id.in_(db.session.query(SocialVersion.post_id).filter(SocialVersion.platform == platform)))
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(func.lower(SocialPost.title).like(like), func.lower(SocialPost.brief).like(like)))
    weeks, today = _calendar(query)
    posts = query.order_by(SocialPost.planned_date.is_(None), SocialPost.planned_date.desc(), SocialPost.id.desc()).limit(200).all()
    counts = dict(db.session.query(SocialPost.status, func.count(SocialPost.id)).group_by(SocialPost.status).all())
    waiting = SocialPost.query.filter_by(status="Pending approval").order_by(SocialPost.updated_at).all()
    ready = SocialPost.query.filter_by(status="Approved").order_by(SocialPost.planned_date.is_(None), SocialPost.planned_date).all()
    return render_template("social/index.html", weeks=weeks, today=today, posts=posts, counts=counts, waiting=waiting, ready=ready, statuses=POST_STATUSES,
                           platforms=PLATFORMS, args=request.args, approve=can_approve())


@social_bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    _need_manage()
    if request.method == "POST":
        title = one_line(request.form.get("title"), 160)
        if not title:
            flash("Give the post a short title.", "danger")
            return redirect(url_for("social.new", **{k: v for k, v in request.form.items() if k in ("news",)}))
        link = safe_url(request.form.get("link_url"))
        if link is None:
            flash("The link must start with http:// or https://", "danger")
            return redirect(url_for("social.new"))
        platforms = _selected_platforms(request.form) or ["linkedin"]
        news_id = request.form.get("news_item_id", type=int)
        post = SocialPost(title=title, brief=clean(request.form.get("brief"), 6000), pillar=one_line(request.form.get("pillar"), 40), link_url=link,
                          status="Idea" if request.form.get("mode") == "idea" else "Draft", planned_date=_parse_date(request.form.get("planned_date")),
                          author_id=current_user.id, news_item_id=news_id if news_id and db.session.get(NewsItem, news_id) else None)
        db.session.add(post)
        db.session.flush()
        post.campaign = f"{slug(title)}-{post.id}"
        for key in platforms:
            db.session.add(SocialVersion(post=post, platform=key))
        db.session.flush()
        if request.form.get("mode") == "ai":
            if not ai_available():
                flash("The AI is not switched on on this server (no API key), so the wording is blank for you to write.", "warning")
            elif not (post.brief or "").strip():
                flash("Write a few lines in the brief first so the AI has facts to work from. The wording is blank for now.", "warning")
            else:
                ok, err = draft_with_ai(post, platforms)
                flash("Drafted by the AI. Read every line and check the facts listed before you submit it." if ok else f"The AI could not draft it: {err} You can write it yourself.", "success" if ok else "warning")
        db.session.commit()
        return redirect(url_for("social.detail", post_id=post.id))
    prefill = {}
    news_id = request.args.get("news", type=int)
    item = db.session.get(NewsItem, news_id) if news_id else None
    if item:
        reported = "posted on social media (unconfirmed)" if item.unconfirmed else "reported"
        prefill = {"title": (item.title or "")[:160], "news_item_id": item.id, "pillar": PILLARS[0],
                   "brief": "\n".join(x for x in (f"A news story, {reported} by {item.publisher or 'a news source'}: {item.title}", item.summary or item.snippet or "",
                                                  f"Why it matters to our clients: {item.why}" if item.why else "",
                                                  "Treat this as reported news, not law: say so, and tell readers to check the source.") if x)}
    return render_template("social/new.html", platforms=PLATFORMS, order=PLATFORM_ORDER, pillars=PILLARS, prefill=prefill, ai=ai_available())


@social_bp.route("/<int:post_id>")
@login_required
def detail(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    versions = {v.platform: v for v in post.versions}
    cards = []
    for key in PLATFORM_ORDER:
        v = versions.get(key)
        if v is not None:
            cards.append((key, PLATFORMS[key], v, tracked_link(post, key)))
    issues = scan(post)
    leads = dict(db.session.query(MarketingLead.utm_source, func.count(MarketingLead.id)).filter(MarketingLead.utm_campaign == post.campaign).group_by(MarketingLead.utm_source).all()) if post.campaign else {}
    news = db.session.get(NewsItem, post.news_item_id) if post.news_item_id else None
    return render_template("social/post.html", post=post, cards=cards, issues=issues, blocked=bool(blocking(issues)), checklist=CHECKLIST, ticked=set(post.checks), leads=leads,
                           missing=[k for k in PLATFORM_ORDER if k not in versions], platforms=PLATFORMS, pillars=PILLARS, approve=can_approve(), news=news,
                           can_delete=current_user.role in ("partner", "admin") or (post.author_id == current_user.id and post.status in ("Idea", "Draft", "Rejected")),
                           ai=ai_available(), statuses=POST_STATUSES, whatsapp_version=versions.get("whatsapp"))


def _reopen_if_changed(post, changed):
    if changed and post.status in ("Pending approval", "Approved"):
        was = post.status
        post.status = "Draft"
        post.checks_json = "[]"
        post.approver_id, post.approved_at = None, None
        flash(f"The wording changed, so it needs approval again (it was {was.lower()}).", "warning")


@social_bp.route("/<int:post_id>/save", methods=["POST"])
@login_required
def save(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    if post.status == "Posted" and not can_approve():
        flash("This post has already been posted. Only an approver can change it.", "warning")
        return redirect(url_for("social.detail", post_id=post.id))
    changed = False
    title = one_line(request.form.get("title"), 160)
    if title and title != post.title:
        post.title, changed = title, True
    brief = clean(request.form.get("brief"), 6000)
    if brief != (post.brief or ""):
        post.brief = brief
    link = safe_url(request.form.get("link_url"))
    if link is None:
        flash("The link must start with http:// or https:// (nothing was saved).", "danger")
        return redirect(url_for("social.detail", post_id=post.id))
    if link != (post.link_url or ""):
        post.link_url, changed = link, True
    post.pillar = one_line(request.form.get("pillar"), 40)
    post.planned_date = _parse_date(request.form.get("planned_date"))
    for v in post.versions:
        body = clean(request.form.get(f"body_{v.platform}", v.body), 6000)
        headline = one_line(request.form.get(f"headline_{v.platform}", v.headline), 150) if v.platform in HEADLINE_LIMIT else ""
        media = clean(request.form.get(f"media_{v.platform}", v.media_note), 1000)
        if body != (v.body or "") or headline != (v.headline or ""):
            changed = True
        v.body, v.headline, v.media_note = body, headline, media
    for key in _selected_platforms(request.form):
        if key not in {v.platform for v in post.versions}:
            db.session.add(SocialVersion(post=post, platform=key))
    _reopen_if_changed(post, changed)
    post.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Saved.", "success")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/redraft", methods=["POST"])
@login_required
def redraft(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    if post.status in ("Approved", "Posted"):
        flash("Change the wording by hand (it goes back for approval), or ask for a new draft before approving.", "warning")
        return redirect(url_for("social.detail", post_id=post.id))
    if not ai_available():
        flash("The AI is not switched on on this server (no API key).", "warning")
    elif not (post.brief or "").strip():
        flash("Write a few lines in the brief first.", "warning")
    else:
        keys = _selected_platforms(request.form) or [v.platform for v in post.versions if v.status != "skipped"]
        ok, err = draft_with_ai(post, keys)
        post.updated_at = datetime.utcnow()
        _reopen_if_changed(post, ok)
        db.session.commit()
        flash("Drafted by the AI. Read every line and check the facts listed." if ok else f"The AI could not draft it: {err}", "success" if ok else "warning")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/submit", methods=["POST"])
@login_required
def submit(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    problems = blocking(scan(post))
    if post.status not in ("Idea", "Draft", "Rejected"):
        flash("It is already past the draft stage.", "info")
    elif problems:
        flash("Fix this first: " + " ".join(p["text"] for p in problems[:3]), "danger")
    else:
        post.status, post.review_note, post.updated_at = "Pending approval", "", datetime.utcnow()
        db.session.commit()
        flash("Sent for approval.", "success")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/approve", methods=["POST"])
@login_required
def approve(post_id):
    _need_approve()
    post = SocialPost.query.get_or_404(post_id)
    ticked = [k for k, _ in CHECKLIST if request.form.get(f"check_{k}")]
    problems = blocking(scan(post))
    if post.status not in ("Draft", "Pending approval", "Idea", "Rejected"):
        flash("It is already approved or posted.", "info")
    elif problems:
        flash("It cannot be approved yet: " + " ".join(p["text"] for p in problems[:3]), "danger")
    elif len(ticked) < len(CHECKLIST):
        flash("Tick every item on the checklist before approving.", "danger")
    else:
        post.status, post.approver_id, post.approved_at = "Approved", current_user.id, datetime.utcnow()
        post.checks_json, post.review_note, post.updated_at = json.dumps(ticked), "", datetime.utcnow()
        db.session.commit()
        flash("Approved. It can now be posted.", "success")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/reject", methods=["POST"])
@login_required
def reject(post_id):
    _need_approve()
    post = SocialPost.query.get_or_404(post_id)
    note = clean(request.form.get("review_note"), 1000)
    if not note:
        flash("Say what needs to change, so the author knows.", "danger")
    elif post.status in ("Posted",):
        flash("It has already been posted.", "info")
    else:
        post.status, post.review_note, post.checks_json = "Rejected", note, "[]"
        post.approver_id, post.approved_at, post.updated_at = None, None, datetime.utcnow()
        db.session.commit()
        flash("Sent back with your note.", "info")
    return redirect(url_for("social.detail", post_id=post.id))


def _version_or_404(post_id, platform):
    post = SocialPost.query.get_or_404(post_id)
    version = SocialVersion.query.filter_by(post_id=post.id, platform=platform).first_or_404()
    return post, version


def _refresh_posted(post):
    live = [v for v in post.versions if v.status != "skipped"]
    if live and all(v.status == "posted" for v in live):
        post.status = "Posted"
    elif post.status == "Posted":
        post.status = "Approved"


@social_bp.route("/<int:post_id>/version/<platform>/posted", methods=["POST"])
@login_required
def mark_posted(post_id, platform):
    _need_manage()
    post, v = _version_or_404(post_id, platform)
    url = safe_url(request.form.get("post_url"))
    if post.status not in ("Approved", "Posted"):
        flash("It must be approved before it is posted.", "danger")
    elif url is None:
        flash("The post's address must start with http:// or https://", "danger")
    else:
        v.status, v.posted_at, v.posted_by_id, v.post_url = "posted", datetime.utcnow(), current_user.id, url
        _refresh_posted(post)
        db.session.commit()
        flash(f"Marked as posted on {PLATFORMS[platform]['label']}.", "success")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/version/<platform>/skip", methods=["POST"])
@login_required
def skip_version(post_id, platform):
    _need_manage()
    post, v = _version_or_404(post_id, platform)
    v.status = "pending" if v.status == "skipped" else "skipped"
    if v.status == "skipped":
        v.posted_at = v.posted_by_id = None
    _refresh_posted(post)
    db.session.commit()
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/version/<platform>/reopen", methods=["POST"])
@login_required
def reopen_version(post_id, platform):
    _need_manage()
    post, v = _version_or_404(post_id, platform)
    v.status, v.posted_at, v.posted_by_id = "pending", None, None
    _refresh_posted(post)
    db.session.commit()
    flash("Marked as not posted.", "info")
    return redirect(url_for("social.detail", post_id=post.id))


def _count(value):
    value = (value or "").replace(",", "").replace(" ", "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise ValueError
    return min(int(value), 2_000_000_000)


@social_bp.route("/<int:post_id>/version/<platform>/results", methods=["POST"])
@login_required
def results_save(post_id, platform):
    _need_manage()
    post, v = _version_or_404(post_id, platform)
    try:
        values = {f: _count(request.form.get(f)) for f in ("impressions", "likes", "comments", "shares", "clicks")}
    except ValueError:
        flash("Results are whole numbers only.", "danger")
        return redirect(url_for("social.detail", post_id=post.id))
    for field, value in values.items():
        setattr(v, field, value)
    v.metrics_at = datetime.utcnow()
    db.session.commit()
    flash("Results saved.", "success")
    return redirect(url_for("social.detail", post_id=post.id))


@social_bp.route("/<int:post_id>/delete", methods=["POST"])
@login_required
def delete(post_id):
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    if not (current_user.role in ("partner", "admin") or (post.author_id == current_user.id and post.status in ("Idea", "Draft", "Rejected"))):
        abort(403, description="Only a Partner or Admin can delete this post.")
    db.session.delete(post)
    db.session.commit()
    flash("Post deleted.", "info")
    return redirect(url_for("social.index"))


# ------------------------------------------------------------------ results across posts

def _result_rows():
    rows = []
    versions = (SocialVersion.query.join(SocialPost).filter(SocialVersion.status == "posted").order_by(SocialVersion.posted_at.desc()).all())
    leads = {(c, s): n for c, s, n in db.session.query(MarketingLead.utm_campaign, MarketingLead.utm_source, func.count(MarketingLead.id)).filter(MarketingLead.utm_medium == "social").group_by(MarketingLead.utm_campaign, MarketingLead.utm_source).all()}
    for v in versions:
        rows.append({"version": v, "post": v.post, "label": PLATFORMS.get(v.platform, {}).get("label", v.platform), "leads": leads.get((v.post.campaign, v.platform), 0)})
    return rows


@social_bp.route("/results")
@login_required
def results():
    _need_manage()
    rows = _result_rows()
    totals = {}
    for r in rows:
        t = totals.setdefault(r["version"].platform, {"label": r["label"], "posts": 0, "impressions": 0, "likes": 0, "comments": 0, "shares": 0, "clicks": 0, "leads": 0})
        t["posts"] += 1
        t["leads"] += r["leads"]
        for f in ("impressions", "likes", "comments", "shares", "clicks"):
            t[f] += getattr(r["version"], f) or 0
    order = sorted(totals.items(), key=lambda kv: (-kv[1]["leads"], -kv[1]["clicks"], kv[1]["label"]))
    return render_template("social/results.html", rows=rows, totals=order)


def _csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t") else text


@social_bp.route("/results.csv")
@login_required
def results_csv():
    _need_manage()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Posted", "Post", "Platform", "Pillar", "Address", "Views", "Likes", "Comments", "Shares", "Clicks", "Leads (tracked)"])
    for r in _result_rows():
        v = r["version"]
        w.writerow([_csv_safe(x) for x in (v.posted_at.strftime("%Y-%m-%d") if v.posted_at else "", r["post"].title, r["label"], r["post"].pillar, v.post_url,
                                           v.impressions, v.likes, v.comments, v.shares, v.clicks, r["leads"])])
    return Response("﻿" + out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="social-results-{datetime.utcnow():%Y-%m-%d}.csv"', "Cache-Control": "no-store"})


# ------------------------------------------------------------------ the firm's accounts

@social_bp.route("/accounts", methods=["GET", "POST"])
@login_required
def accounts():
    _need_manage()
    _ensure_accounts()
    if request.method == "POST":
        for key in PLATFORM_ORDER:
            acc = db.session.get(SocialAccount, key)
            url = safe_url(request.form.get(f"url_{key}"))
            if url is None:
                flash(f"{PLATFORMS[key]['label']}: the address must start with http:// or https:// (that one was not saved).", "danger")
                continue
            acc.profile_url = url
            acc.handle = one_line(request.form.get(f"handle_{key}"), 120)
            owner = request.form.get(f"owner_{key}") or ""
            acc.owner_id = int(owner) if owner.isdigit() and db.session.get(User, int(owner)) else None
            acc.notes = clean(request.form.get(f"notes_{key}"), 500)
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("social.accounts"))
    users = [u for u in User.query.filter(User.is_active_flag.is_(True)).order_by(User.name).all() if user_has_permission(u, "manage_social")]
    return render_template("social/accounts.html", accounts={a.platform: a for a in SocialAccount.query.all()}, platforms=PLATFORMS, order=PLATFORM_ORDER, users=users)


# ------------------------------------------------------------------ WhatsApp

WA_SOURCES = ("Signed up on our page", "Is a client", "Asked us on WhatsApp", "Met at an event", "Other")
WA_LISTS = ("Clients", "Leads", "Broadcast")
STOP_WORDS = {"stop", "unsubscribe", "opt out", "optout", "no thanks", "remove me", "stop messaging"}

# key: (label, needs a topic, wording)
WA_TEMPLATES = {
    "calendar": ("Did the compliance calendar reach you?", False,
                 "Hello {first}, it is {sender} from Neverlank. Did the {year} compliance calendar reach you? Happy to do a free 20-minute check of {company_possessive} dates if that would be useful."),
    "reminder": ("Friendly deadline reminder", True,
                 "Hello {first}, a friendly reminder from Neverlank: {topic}. Reply here if you would like us to help."),
    "thanks": ("Thank you after a meeting", True,
               "Thank you for your time today, {first}. As discussed, {topic}. Please reply here if anything is unclear."),
    "update": ("Update on your matter", True,
               "Hello {first}, a quick update from Neverlank: {topic}. Call or message us if you have any questions."),
    "intro": ("Introduction", False,
              "Hello {first}, this is {sender} from Neverlank Chartered Accountants in Harare. We help businesses with tax, audit, accounting and company secretarial work. May I send you a short note about how we could help {company_object}?"),
}


def can_message(contact):
    return bool(contact.consent and not contact.opted_out)


def render_template_text(key, contact, topic=""):
    """(text, None) or (None, why). Never leaves a {placeholder} in the message."""
    spec = WA_TEMPLATES.get(key)
    if not spec:
        return None, "Choose a message."
    label, needs_topic, text = spec
    topic = one_line(topic, 300).rstrip(".")
    if needs_topic and not topic:
        return None, "Type what the message is about."
    first = (contact.name or "there").split()[0]
    company = contact.company or ""
    from leads import calendar_year
    return text.format(first=first, sender=current_user.name.split()[0] if current_user.is_authenticated else "the team", year=calendar_year(), topic=topic,
                       company_possessive=(f"{company}'s" if company else "your"), company_object=(company if company else "your business")), None


def wa_link(contact, text):
    return f"https://wa.me/{contact.phone}?text={quote(text)}"


def _phone(value):
    from leads import phone_digits
    digits = phone_digits(value)
    return digits if 9 <= len(digits) <= 15 else ""


@social_bp.route("/whatsapp", methods=["GET", "POST"])
@login_required
def whatsapp():
    _need_manage()
    if request.method == "POST":
        name = one_line(request.form.get("name"), 160)
        phone = _phone(request.form.get("phone"))
        consent = bool(request.form.get("consent"))
        source = request.form.get("consent_source") if request.form.get("consent_source") in WA_SOURCES else ""
        if not name or not phone:
            flash("Enter a name and a valid mobile number.", "danger")
        elif WhatsAppContact.query.filter_by(phone=phone).first():
            flash("That number is already in the list.", "warning")
        elif consent and not source:
            flash("Say how they agreed to be messaged.", "danger")
        else:
            c = WhatsAppContact(name=name, phone=phone, company=one_line(request.form.get("company"), 160),
                                list_name=request.form.get("list_name") if request.form.get("list_name") in WA_LISTS else "Clients", consent=consent,
                                consent_source=source if consent else "", consent_at=datetime.utcnow() if consent else None, created_by_id=current_user.id)
            db.session.add(c)
            db.session.commit()
            flash("Added." if consent else "Added, but no messages can be sent until they have agreed to be messaged.", "success" if consent else "warning")
            return redirect(url_for("social.whatsapp_contact", contact_id=c.id))
    query = WhatsAppContact.query
    list_name = request.args.get("list") or ""
    if list_name in WA_LISTS:
        query = query.filter(WhatsAppContact.list_name == list_name)
    q = (request.args.get("q") or "").strip().lower()
    if q:
        like = f"%{q}%"
        query = query.filter(or_(func.lower(WhatsAppContact.name).like(like), func.lower(WhatsAppContact.company).like(like), WhatsAppContact.phone.like(like)))
    contacts = query.order_by(WhatsAppContact.name).limit(500).all()
    stats = {"all": WhatsAppContact.query.count(), "ok": WhatsAppContact.query.filter(WhatsAppContact.consent.is_(True), WhatsAppContact.opted_out.is_(False)).count(),
             "out": WhatsAppContact.query.filter(WhatsAppContact.opted_out.is_(True)).count()}
    waiting_leads = 0
    have = {c.phone for c in WhatsAppContact.query.all()}
    for lead in MarketingLead.query.filter(MarketingLead.consent_whatsapp.is_(True)).all():
        if _phone(lead.mobile) and _phone(lead.mobile) not in have:
            waiting_leads += 1
    return render_template("social/whatsapp.html", contacts=contacts, stats=stats, sources=WA_SOURCES, lists=WA_LISTS, args=request.args, waiting_leads=waiting_leads,
                           broadcasts=SocialVersion.query.join(SocialPost).filter(SocialVersion.platform == "whatsapp", SocialPost.status.in_(("Approved", "Posted"))).order_by(SocialPost.id.desc()).limit(10).all())


@social_bp.route("/whatsapp/import-leads", methods=["POST"])
@login_required
def whatsapp_import_leads():
    _need_manage()
    have = {c.phone for c in WhatsAppContact.query.all()}
    added = 0
    for lead in MarketingLead.query.filter(MarketingLead.consent_whatsapp.is_(True), MarketingLead.status != "Unsubscribed").all():
        phone = _phone(lead.mobile)
        if phone and phone not in have:
            db.session.add(WhatsAppContact(name=lead.full_name, phone=phone, company=lead.company or "", list_name="Leads", consent=True, consent_source="Signed up on our page",
                                           consent_at=lead.consent_at or datetime.utcnow(), lead_id=lead.id, created_by_id=current_user.id))
            have.add(phone)
            added += 1
    db.session.commit()
    flash(f"{added} lead(s) who agreed to WhatsApp were added." if added else "No new leads with WhatsApp consent to add.", "success" if added else "info")
    return redirect(url_for("social.whatsapp"))


@social_bp.route("/whatsapp/<int:contact_id>")
@login_required
def whatsapp_contact(contact_id):
    _need_manage()
    c = WhatsAppContact.query.get_or_404(contact_id)
    key = request.args.get("template") or ""
    topic = request.args.get("topic") or ""
    text, why, link = None, None, None
    if key:
        text, why = render_template_text(key, c, topic)
        if text and can_message(c):
            link = wa_link(c, text)
    return render_template("social/whatsapp_contact.html", c=c, templates=WA_TEMPLATES, key=key, topic=topic, text=text, why=why, link=link, ok=can_message(c),
                           sources=WA_SOURCES, lists=WA_LISTS)


@social_bp.route("/whatsapp/<int:contact_id>/update", methods=["POST"])
@login_required
def whatsapp_update(contact_id):
    _need_manage()
    c = WhatsAppContact.query.get_or_404(contact_id)
    c.name = one_line(request.form.get("name"), 160) or c.name
    c.company = one_line(request.form.get("company"), 160)
    c.list_name = request.form.get("list_name") if request.form.get("list_name") in WA_LISTS else c.list_name
    c.notes = clean(request.form.get("notes"), 2000)
    consent = bool(request.form.get("consent"))
    source = request.form.get("consent_source") if request.form.get("consent_source") in WA_SOURCES else ""
    if consent and not c.consent:
        if not source:
            flash("Say how they agreed to be messaged.", "danger")
            return redirect(url_for("social.whatsapp_contact", contact_id=c.id))
        c.consent, c.consent_source, c.consent_at, c.opted_out = True, source, datetime.utcnow(), False
    elif not consent:
        c.consent = False
    c.opted_out = bool(request.form.get("opted_out")) if c.consent or request.form.get("opted_out") else c.opted_out
    db.session.commit()
    flash("Saved.", "success")
    return redirect(url_for("social.whatsapp_contact", contact_id=c.id))


@social_bp.route("/whatsapp/<int:contact_id>/log", methods=["POST"])
@login_required
def whatsapp_log(contact_id):
    """Record a message that was sent (after pressing the send link) or received (pasted in)."""
    _need_manage()
    c = WhatsAppContact.query.get_or_404(contact_id)
    direction = "in" if request.form.get("direction") == "in" else "out"
    body = clean(request.form.get("body"), 3000)
    if not body:
        flash("There is no message to log.", "danger")
    elif direction == "out" and not can_message(c):
        flash("This person has not agreed to be messaged (or has opted out), so nothing can be logged as sent.", "danger")
    else:
        post_id = request.form.get("post_id", type=int)
        db.session.add(WhatsAppMessage(contact_id=c.id, direction=direction, body=body, template=one_line(request.form.get("template"), 30), post_id=post_id,
                                       by_user_id=current_user.id))
        c.last_message_at = datetime.utcnow()
        if direction == "in" and re.sub(r"[^a-z ]", "", body.lower()).strip() in STOP_WORDS:
            c.opted_out = True
            flash("They asked us to stop, so they have been marked as opted out and will not be messaged.", "warning")
        else:
            flash("Logged.", "success")
        db.session.commit()
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/") and not (request.form.get("next") or "").startswith("//") else url_for("social.whatsapp_contact", contact_id=c.id))


@social_bp.route("/whatsapp/<int:contact_id>/delete", methods=["POST"])
@login_required
def whatsapp_delete(contact_id):
    _need_manage()
    if current_user.role not in ("partner", "admin"):
        abort(403, description="Only a Partner or Admin can delete a contact.")
    db.session.delete(WhatsAppContact.query.get_or_404(contact_id))
    db.session.commit()
    flash("Contact deleted.", "info")
    return redirect(url_for("social.whatsapp"))


@social_bp.route("/whatsapp/broadcast/<int:post_id>")
@login_required
def whatsapp_broadcast(post_id):
    """An approved WhatsApp message and the people who agreed to receive it, each with their own send link. A person presses each one; nothing is sent in bulk."""
    _need_manage()
    post = SocialPost.query.get_or_404(post_id)
    v = SocialVersion.query.filter_by(post_id=post.id, platform="whatsapp").first()
    if v is None or post.status not in ("Approved", "Posted"):
        flash("Only an approved post with a WhatsApp version can be sent to the list.", "warning")
        return redirect(url_for("social.detail", post_id=post.id))
    list_name = request.args.get("list") if request.args.get("list") in WA_LISTS else ""
    query = WhatsAppContact.query.filter(WhatsAppContact.consent.is_(True), WhatsAppContact.opted_out.is_(False))
    if list_name:
        query = query.filter(WhatsAppContact.list_name == list_name)
    contacts = query.order_by(WhatsAppContact.name).all()
    sent = {m.contact_id for m in WhatsAppMessage.query.filter_by(post_id=post.id, direction="out").all()}
    base = final_text(post, v)
    rows = []
    for c in contacts:
        text = f"Hello {c.name.split()[0]}, {base}"
        rows.append((c, text, wa_link(c, text), c.id in sent))
    return render_template("social/whatsapp_broadcast.html", post=post, rows=rows, lists=WA_LISTS, list_name=list_name, text=base)
