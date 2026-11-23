"""Publish an approved News Watch story to the firm's website (WordPress, neverlank.co.zw) - only when a Partner or Admin presses the button.

What goes on the site is the firm's own short note ("What this means"), the headline, the date, the publisher's name and a link to the original report. The
article itself is never copied. Stories added by hand or taken from social media (unconfirmed) cannot be published, and a note that names a client is refused.

Settings (Render > Environment):
  WP_SITE_URL      the site address, https only (default https://neverlank.co.zw)
  WP_USERNAME      the WordPress user the posts are made as
  WP_APP_PASSWORD  an Application Password made for that user (WordPress > Users > Profile > Application Passwords). Never the login password.
  WP_NEWS_CATEGORY the category the posts go in (default "Tax and Compliance Updates"; made if it does not exist)
"""
import os
import re
from datetime import datetime
from html import escape
from urllib.parse import urlparse

import requests

from models import Client

DEFAULT_SITE = "https://neverlank.co.zw"
DEFAULT_CATEGORY = "Tax and Compliance Updates"
TIMEOUT = 20
TITLE_MIN, TITLE_MAX = 10, 200
NOTE_MIN, NOTE_MAX = 40, 1200
DISCLAIMER = ("General information only, not tax, audit or legal advice. News reports can be incomplete: check the law or the official notice "
              "before acting, or speak to Neverlank Chartered Accountants about your own position.")


def settings():
    site = (os.environ.get("WP_SITE_URL") or DEFAULT_SITE).strip().rstrip("/")
    return {"site": site, "user": (os.environ.get("WP_USERNAME") or "").strip(), "password": (os.environ.get("WP_APP_PASSWORD") or "").strip(),
            "category": (os.environ.get("WP_NEWS_CATEGORY") or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY}


def configured():
    s = settings()
    return bool(s["user"] and s["password"] and urlparse(s["site"]).scheme == "https" and urlparse(s["site"]).hostname)


def wp_request(method, path, json=None, params=None):
    """One call to the WordPress REST API. Returns (True, data) or (False, short plain-words reason). Never raises."""
    s = settings()
    if not configured():
        return False, "The website connection is not set up yet (WP_USERNAME and WP_APP_PASSWORD on Render)."
    try:
        r = requests.request(method, s["site"] + "/wp-json/wp/v2/" + path.lstrip("/"), json=json, params=params, auth=(s["user"], s["password"]),
                             timeout=TIMEOUT, allow_redirects=False, headers={"User-Agent": "NeverlankApp/1.0"})
    except requests.RequestException:
        return False, "The website could not be reached. Try again in a few minutes."
    if r.status_code in (401, 403):
        return False, "The website refused the login. Check WP_USERNAME and the Application Password (not the normal password)."
    if r.status_code >= 300:
        return False, f"The website answered with an error ({r.status_code})."
    try:
        return True, r.json()
    except ValueError:
        return False, "The website sent an answer the app could not read."


def ensure_category():
    """The id of the news category, made if it is not there yet. (id, None) or (None, reason)."""
    name = settings()["category"]
    ok, data = wp_request("GET", "categories", params={"search": name, "per_page": 20})
    if not ok:
        return None, data
    for c in data if isinstance(data, list) else []:
        if (c.get("name") or "").strip().lower() == name.lower():
            return c["id"], None
    ok, data = wp_request("POST", "categories", json={"name": name})
    if not ok:
        return None, data
    return data.get("id"), None


def named_client(text):
    """The name of a client that appears in the text, or None. A public post must never name or hint at a client."""
    low = (text or "").lower()
    for (name,) in Client.query.with_entities(Client.name).all():
        n = (name or "").strip()
        if len(n) >= 4 and re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", low):
            return n
    return None


def default_note(item):
    """A starting point for the note, from the AI's summary: the person rewrites it in the firm's voice."""
    bits = [b.strip() for b in (item.summary, item.why) if b and b.strip()]
    return "\n\n".join(bits)


def blocked_reason(item):
    if item.unconfirmed:
        return "This story is unconfirmed (added by hand or from social media). Only stories from a news source can go on the website."
    if item.status == "irrelevant":
        return "This story is marked not relevant."
    if (urlparse(item.url or "").scheme or "").lower() not in ("http", "https"):
        return "The story has no usable web link."
    return None


def check_text(title, note, ticked):
    """Reasons the form cannot be published, in plain words (empty list = fine)."""
    problems = []
    t, n = (title or "").strip(), (note or "").strip()
    if not TITLE_MIN <= len(t) <= TITLE_MAX:
        problems.append(f"The headline must be {TITLE_MIN} to {TITLE_MAX} characters.")
    if not NOTE_MIN <= len(n) <= NOTE_MAX:
        problems.append(f"The note must be {NOTE_MIN} to {NOTE_MAX} characters, in your own words.")
    client = named_client(t + " " + n)
    if client:
        problems.append(f"The text names a client ({client}). Take the name out: the website must never name or hint at a client.")
    if not ticked:
        problems.append("Tick the box to confirm you checked the story against the source.")
    return problems


def build_content(item, title, note):
    pub = (item.publisher or "").strip() or (urlparse(item.url).hostname or "the publisher")
    when = (item.published_at or datetime.utcnow()).strftime("%d %B %Y")
    paras = "".join(f"<p>{escape(p.strip())}</p>" for p in re.split(r"\n\s*\n", note.strip()) if p.strip())
    return (f'<p><em>{escape(when)} &middot; Reported by {escape(pub)}</em></p>{paras}'
            f'<p><a href="{escape(item.url, quote=True)}" target="_blank" rel="noopener noreferrer nofollow">Read the original report ({escape(pub)})</a></p>'
            f'<p><small>{escape(DISCLAIMER)}</small></p>')


def publish(item, title, note, draft=False):
    """Make (or update) the post. Returns (True, post_dict) or (False, reason)."""
    cat_id, err = ensure_category()
    if err:
        return False, err
    body = {"title": title.strip(), "content": build_content(item, title, note), "status": "draft" if draft else "publish",
            "categories": [cat_id], "excerpt": (note.strip()[:200]).strip()}
    if item.wp_post_id:
        return wp_request("POST", f"posts/{item.wp_post_id}", json=body)
    return wp_request("POST", "posts", json=body)


def take_down(item):
    """Back to a draft on the site (not deleted, so it can be put up again)."""
    if not item.wp_post_id:
        return False, "This story is not on the website."
    return wp_request("POST", f"posts/{item.wp_post_id}", json={"status": "draft"})
