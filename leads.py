"""Marketing leads: people who ask for the Compliance Calendar (or another lead magnet) on the landing page.

What this does, in plain words
* /compliance-calendar is the public landing page (no sign-in). Its form posts to /leads/capture, which stores the person as a lead.
* Every new lead gets an owner (a named person), a priority and a follow-up plan: a personal note within 24 hours, then emails on days 0, 3, 7
  and 14 and a WhatsApp check-in on day 5. The Leads page lists what is due today, with the wording already written.
* Emails can go out by themselves (set LEADS_AUTO_EMAIL=1, LEADS_CALENDAR_URL and the usual EMAIL_API_KEY / EMAIL_FROM). Until then the
  same emails appear as "send now" buttons, so a person stays in control. Every automatic email carries an unsubscribe link.
* A lead who replies, books, is won or lost, or unsubscribes stops getting follow-ups.

Settings (Render > Environment), all optional:
    LEADS_AUTO_EMAIL        1 / yes / on: send the follow-up emails automatically (default off)
    LEADS_CALENDAR_URL      where the calendar file can be downloaded (needed before any automatic email goes out)
    LEADS_BOOKING_URL       a booking page for the free 20-minute check (otherwise the emails point to WhatsApp)
    LEADS_NOTIFY_EMAIL      who is told when someone signs up (addresses separated by commas; default: the lead's owner)
    LEADS_DEFAULT_OWNER     username or email of the person who owns new leads (default: the first active partner)
    LEADS_ALLOWED_ORIGINS   other websites allowed to post sign-ups here (only needed if the page is hosted elsewhere)
    LEADS_CRON_TOKEN        lets a timer call /leads/cron?token=... so follow-ups go out even when nobody opens the app
    LEADS_YEAR              the year printed on the page and emails (default: next year from September, else this year)
"""
import csv
import hmac
import io
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from html import escape
from urllib.parse import quote

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_

import emailer
from extensions import db
from models import (LEAD_STATUSES, LEAD_STOP_STATUSES, LeadTouch, MarketingLead, User, user_has_permission)

leads_bp = Blueprint("leads", __name__, url_prefix="/leads")
page_bp = Blueprint("leads_page", __name__)          # the public landing page lives at /compliance-calendar

SOURCE_DEFAULT = "Magnet 1: Compliance Calendar"
ROLES = ("Owner or Director", "CEO or MD", "CFO or Finance Manager", "Other")
HEADACHES = ("Tax and penalties", "Audit or bank requirements", "Late or messy books", "Company registry and governance",
             "Not sure what I need", "Other")
PRIORITIES = ("High", "Normal")
FIRM_ADDRESS = "2nd Floor, Michael House, 62 Nelson Mandela Avenue, Harare"
WHATSAPP = "263718110832"
WHATSAPP_SHOWN = "+263 71 811 0832"
DEFAULT_COUNTRY_CODE = "263"
DUPLICATE_WINDOW_DAYS = 90
MAX_TRIES = 3
RETRY_AFTER = timedelta(hours=1)
BACKGROUND = True                 # send the first email / notification in a background thread (tests switch this off)

# step: (channel, mode, days after sign-up, short name shown to staff)
STEPS = {
    "note24h": ("email", "manual", 0, "Personal note (send within 24 hours)"),
    "email1": ("email", "auto", 0, "Email 1: the calendar"),
    "email2": ("email", "auto", 3, "Email 2: the habit that makes it work"),
    "whatsapp5": ("whatsapp", "manual", 5, "WhatsApp check-in"),
    "email3": ("email", "auto", 7, "Email 3: the free 20-minute check"),
    "reengage": ("email", "auto", 14, "Email 4: still useful?"),
}
STEP_ORDER = ("note24h", "email1", "email2", "whatsapp5", "email3", "reengage")


# ------------------------------------------------------------------ settings

def _env(name):
    return (os.environ.get(name) or "").strip()


def auto_requested():
    return _env("LEADS_AUTO_EMAIL").lower() in ("1", "true", "yes", "on", "y")


def calendar_url():
    return _env("LEADS_CALENDAR_URL")


def booking_url():
    return _env("LEADS_BOOKING_URL")


def auto_enabled():
    """Emails go out by themselves only when it was asked for, the email service is set up and there is a calendar to send."""
    return auto_requested() and emailer.is_configured() and bool(calendar_url())


def calendar_year():
    configured = _env("LEADS_YEAR")
    if configured:
        return configured
    today = datetime.utcnow()
    return str(today.year + 1 if today.month >= 9 else today.year)


def base_url():
    return (_env("PUBLIC_BASE_URL") or request.url_root).rstrip("/")


def can_manage(user=None):
    user = user or current_user
    return bool(user and user.is_authenticated and user_has_permission(user, "manage_leads"))


def _need_permission():
    if not can_manage():
        abort(403, description="You do not have permission to manage marketing leads. Ask an Admin (Team > Permissions).")


# ------------------------------------------------------------------ small helpers

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(value, limit):
    """Plain, single-spaced text no longer than `limit`, for anything typed into the public form."""
    if value is None:
        return ""
    text = _CONTROL.sub("", str(value)).replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def phone_digits(raw):
    """A WhatsApp-ready number: 077 123 4567 -> 263771234567; +263 77... and 00263... keep their country code."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = DEFAULT_COUNTRY_CODE + digits[1:]
    return digits


def _truthy(value):
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t") else text


def default_owner():
    ident = _env("LEADS_DEFAULT_OWNER")
    if ident:
        user = User.query.filter(User.is_active_flag.is_(True), or_(func.lower(User.username) == ident.lower(), func.lower(User.email) == ident.lower())).first()
        if user:
            return user
    return User.query.filter(User.is_active_flag.is_(True), User.role == "partner").order_by(User.id).first()


def _priority_for(role, headache):
    return "High" if role == "Owner or Director" or role == "CEO or MD" or headache == "Audit or bank requirements" else "Normal"


# ------------------------------------------------------------------ the follow-up plan

def schedule(lead, start=None):
    """Plan the follow-ups for a new lead (nothing is sent here)."""
    start = start or datetime.utcnow()
    for step in STEP_ORDER:
        channel, mode, days, _name = STEPS[step]
        if step == "whatsapp5" and not (lead.consent_whatsapp and phone_digits(lead.mobile)):
            continue
        db.session.add(LeadTouch(lead=lead, step=step, channel=channel, mode=mode, due_at=start + timedelta(days=days), status="pending"))


def stop_sequence(lead, why="skipped"):
    for touch in LeadTouch.query.filter_by(lead_id=lead.id, status="pending").all():
        touch.status = why


def is_live(lead):
    return lead.status not in LEAD_STOP_STATUSES


HEADACHE_LINES = {
    "Tax and penalties": "You told us tax and penalties are the headache. Most penalties are for something late, not something wrong, "
                         "so the fix is boring and cheap: every date has a named person against it, and someone looks at the list once a month.",
    "Audit or bank requirements": "You told us audit or bank requirements are the headache. Banks and funders ask for statements on their timetable, "
                                  "not yours, so work backwards from the date they need them and put that date on the calendar first.",
    "Late or messy books": "You told us late or messy books are the headache. Nothing can be filed, audited or borrowed against until the books are "
                           "reconciled, so a simple monthly close (everything reconciled within a set number of working days of month-end) fixes more than it seems to.",
    "Company registry and governance": "You told us company registry and governance is the headache. Annual returns, director changes and registers are "
                                       "easy to forget because they do not arrive as a tax demand, so keep them on the same calendar as everything else.",
}
HEADACHE_GENERIC = ("The calendar only works if someone owns each date. Put a name next to every line, add your own year-end and any lender "
                    "or customer deadlines, and look at it once a month.")


def build_draft(lead, touch, root=""):
    """The wording of one follow-up: {subject, text, html, wa}. No placeholders are ever left in it: where a link is not set, the line is simply left out."""
    step = touch.step
    first = lead.first_name or "there"
    company = lead.company or ""
    year = calendar_year()
    cal, book = calendar_url(), booking_url()
    owner = (lead.owner.name if lead.owner else "") or "Petros Piki"
    unsub = f"{root}/leads/unsubscribe/{lead.unsub_token}" if lead.unsub_token else ""
    paras, cta, subject = [], None, ""
    booking_line = ("Book it here whenever suits you." if book else f"Reply to this email or WhatsApp us on {WHATSAPP_SHOWN} and we will fix a time.")
    cta_book = ("Book your free 20-minute check", book) if book else None
    wa = ""

    if step == "note24h":
        subject = f"Your {year} compliance calendar"
        paras = [f"Hello {first},",
                 f"Thank you for asking for the {year} Compliance Calendar" + (f" for {company}." if company else "."),
                 (f"You mentioned that {lead.headache.lower()} is your biggest headache. " if lead.headache and lead.headache not in ("Not sure what I need", "Other") else "")
                 + "If it would help, I can spend 20 minutes with you, free of charge, going through your dates and telling you where the real risks are. "
                 + ("There is no obligation and no sales pitch." ),
                 f"{booking_line}", f"Kind regards,\n{owner}\nNeverlank Chartered Accountants"]
        cta = cta_book
    elif step == "email1":
        subject = f"Your {year} Compliance Calendar"
        paras = [f"Hello {first},",
                 f"Thank you for asking for the {year} Compliance Calendar" + (f" for {company}." if company else "."),
                 "Ten minutes is enough to make it work: put a name next to every date, add your own year-end and any lender or customer deadlines, "
                 "and put a 15-minute review in the diary for the first week of each month.",
                 "Dates can change when the law changes, so please confirm anything critical with us before you rely on it.",
                 "If you would like a second pair of eyes, we do a free 20-minute compliance check. " + booking_line,
                 "Reply to this email with any question and a person will answer.",
                 "Kind regards,\nNeverlank Chartered Accountants"]
        cta = ("Download your calendar", cal) if cal else None
    elif step == "email2":
        subject = "The 15-minute habit that makes the calendar work"
        paras = [f"Hello {first},", HEADACHE_LINES.get(lead.headache, HEADACHE_GENERIC),
                 "That is the whole habit: a named person per date and a short monthly look. If you would like us to run that check with you, it is free and takes 20 minutes. " + booking_line,
                 "Kind regards,\nNeverlank Chartered Accountants"]
        cta = cta_book
    elif step == "email3":
        subject = "A free 20-minute compliance check" + (f" for {company}" if company else "")
        paras = [f"Hello {first},",
                 "Many of the businesses that ask for the calendar tell us the same thing afterwards: the dates were not the hard part, knowing what could go wrong was.",
                 "In 20 minutes we will go through your calendar with you and tell you plainly where you are exposed (tax, audit, accounting or company records), and what to do first. It is free, and you keep the notes whether or not we ever work together.",
                 booking_line, "Kind regards,\nNeverlank Chartered Accountants"]
        cta = cta_book
    elif step == "reengage":
        subject = f"Still useful, {first}?"
        paras = [f"Hello {first},",
                 f"A couple of weeks ago you asked for the {year} Compliance Calendar. Is it helping? If a date has become a problem, or you would like a hand with tax, audit or your books, just reply and say so. A person will answer.",
                 "If these emails are not for you, the link below stops them straight away.",
                 "Kind regards,\nNeverlank Chartered Accountants"]
    elif step == "whatsapp5":
        subject = "WhatsApp check-in"
        wa_text = (f"Hello {first}, it is {owner} from Neverlank. Did the {year} compliance calendar reach you? "
                   + (f"Happy to do a free 20-minute check of {company}'s dates if that would be useful." if company else "Happy to do a free 20-minute check of your dates if that would be useful."))
        paras = [wa_text]
        digits = phone_digits(lead.mobile)
        wa = f"https://wa.me/{digits}?text={quote(wa_text)}" if digits else ""

    paras = [p for p in paras if p]
    is_note = step in ("note24h", "whatsapp5")
    text = "\n\n".join(paras)
    if cta:
        text += f"\n\n{cta[0]}: {cta[1]}"
    footer_text = ""
    if not is_note and unsub:
        footer_text = (f"\n\n--\nYou are receiving this because you asked for the {year} Compliance Calendar from Neverlank Chartered Accountants, "
                       f"{FIRM_ADDRESS}.\nStop these emails: {unsub}")
        text += footer_text
    body = "".join(f'<p style="margin:0 0 14px;">{escape(p).replace(chr(10), "<br>")}</p>' for p in paras)
    if cta:
        body += (f'<p style="margin:18px 0;"><a href="{escape(cta[1], quote=True)}" style="background:#C9A227;color:#0B1F3A;font-weight:bold;'
                 f'text-decoration:none;padding:12px 18px;border-radius:5px;display:inline-block;">{escape(cta[0])}</a></p>')
    if not is_note and unsub:
        body += (f'<p style="margin:24px 0 0;font-size:12px;color:#667;">You are receiving this because you asked for the {escape(year)} Compliance Calendar from '
                 f'Neverlank Chartered Accountants, {escape(FIRM_ADDRESS)}. <a href="{escape(unsub, quote=True)}" style="color:#667;">Stop these emails</a>.</p>')
    html = f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;color:#13203A;max-width:560px;">{body}</div>'
    return {"subject": subject, "text": text, "html": html, "wa": wa}


# ------------------------------------------------------------------ doing the follow-ups

def _mark_contacted(lead, when=None):
    lead.last_touch_at = when or datetime.utcnow()
    lead.updated_at = lead.last_touch_at
    if lead.status == "New":
        lead.status = "Contacted"


def deliver(touch, root, by_user=None):
    """Send one email follow-up through the email service. Returns (True, "") or (False, reason). Does not commit on failure beyond the retry note."""
    lead = touch.lead
    if touch.channel != "email":
        return False, "This step is not an email."
    if not lead.consent_email and touch.step != "note24h":
        return False, "This person has not agreed to emails."
    draft = build_draft(lead, touch, root)
    ok, err = emailer.send_email([lead.email], draft["subject"], draft["text"], draft["html"])
    now = datetime.utcnow()
    touch.tries = (touch.tries or 0) + 1
    if ok:
        touch.status = "sent"
        touch.done_at = now
        touch.done_by_id = by_user.id if by_user else None
        touch.error = None
        _mark_contacted(lead, now)
    else:
        touch.error = (err or "")[:300]
    return ok, err


def process_due(root):
    """Send the automatic emails that are due (when automatic sending is on). Safe to call often; returns how many went out."""
    now = datetime.utcnow()
    sent = 0
    # people who have stopped (replied, booked, won, lost, unsubscribed) get nothing more
    for touch in (LeadTouch.query.join(MarketingLead).filter(LeadTouch.status == "pending", MarketingLead.status.in_(LEAD_STOP_STATUSES)).all()):
        touch.status = "skipped"
    db.session.commit()
    if not auto_enabled():
        return 0
    due = (LeadTouch.query.join(MarketingLead)
           .filter(LeadTouch.status == "pending", LeadTouch.mode == "auto", LeadTouch.channel == "email", LeadTouch.due_at <= now,
                   MarketingLead.consent_email.is_(True), MarketingLead.status.notin_(LEAD_STOP_STATUSES))
           .order_by(LeadTouch.due_at).limit(25).all())
    for touch in due:
        # claim it first so two requests at once cannot send the same email twice
        claimed = LeadTouch.query.filter_by(id=touch.id, status="pending").update({"status": "sending"})
        db.session.commit()
        if not claimed:
            continue
        touch = LeadTouch.query.get(touch.id)
        try:
            ok, err = deliver(touch, root)
        except Exception as exc:                                  # never leave a touch stuck in "sending"
            ok, err = False, f"{exc.__class__.__name__}"
            touch.tries = (touch.tries or 0) + 1
            touch.error = err
        if ok:
            sent += 1
        else:
            touch.status = "failed" if (touch.tries or 0) >= MAX_TRIES else "pending"
            touch.due_at = now + RETRY_AFTER
        db.session.commit()
    return sent


_last_background = {"at": 0.0}


def process_in_background(root, min_gap=600):
    """Run process_due in a thread at most every `min_gap` seconds (called from pages staff open). Never raises."""
    if not auto_enabled() or time.time() - _last_background["at"] < min_gap:
        return
    _last_background["at"] = time.time()
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            try:
                process_due(root)
            except Exception:
                db.session.rollback()
            finally:
                db.session.remove()
    threading.Thread(target=work, daemon=True).start()


def due_now(limit=None):
    """Follow-ups a person should deal with now: manual steps, and (while automatic sending is off) the emails too."""
    now = datetime.utcnow()
    query = (LeadTouch.query.join(MarketingLead)
             .filter(LeadTouch.status.in_(("pending", "failed")), LeadTouch.due_at <= now, MarketingLead.status.notin_(LEAD_STOP_STATUSES)))
    if auto_enabled():
        query = query.filter(or_(LeadTouch.mode == "manual", LeadTouch.status == "failed"))
    query = query.filter(or_(LeadTouch.channel != "email", LeadTouch.step == "note24h", MarketingLead.consent_email.is_(True)))
    query = query.order_by(MarketingLead.priority.asc(), LeadTouch.due_at)          # "High" sorts before "Normal"
    return query.limit(limit).all() if limit else query.all()


def due_count():
    try:
        return len({t.lead_id for t in due_now(200)})
    except Exception:
        db.session.rollback()
        return 0


def notify_team(lead, root, repeat=False):
    to = emailer.split_addresses(_env("LEADS_NOTIFY_EMAIL"))
    if not to and lead.owner and lead.owner.email:
        to = [lead.owner.email]
    to = [a for a in to if emailer.valid_address(a)]
    if not to or not emailer.is_configured():
        return False
    link = f"{root}/leads/{lead.id}"
    lines = [("Asked again" if repeat else "New lead") + f": {lead.full_name}" + (f", {lead.company}" if lead.company else ""),
             f"Role: {lead.job_role or '-'}", f"Biggest headache: {lead.headache or '-'}", f"Email: {lead.email}", f"Mobile: {lead.mobile or '-'}",
             f"Priority: {lead.priority}", f"Owner: {lead.owner.name if lead.owner else 'nobody yet'}", f"Source: {lead.source}", "",
             "A personal note within 24 hours turns many more of these into conversations.", link]
    subject = ("Lead asked again: " if repeat else "New lead: ") + (lead.company or lead.full_name) + (" (high priority)" if lead.priority == "High" else "")
    ok, _err = emailer.send_email(to, subject, "\n".join(lines))
    return ok


def _after_capture(lead_id, root, repeat):
    lead = MarketingLead.query.get(lead_id)
    if not lead:
        return
    try:
        notify_team(lead, root, repeat)
    except Exception:
        pass
    try:
        process_due(root)
    except Exception:
        db.session.rollback()


def run_after_capture(lead_id, root, repeat=False):
    if not BACKGROUND:
        _after_capture(lead_id, root, repeat)
        return
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            try:
                _after_capture(lead_id, root, repeat)
            except Exception:
                db.session.rollback()
            finally:
                db.session.remove()
    threading.Thread(target=work, daemon=True).start()


# ------------------------------------------------------------------ public: the landing page and the sign-up route

_hits = {}
_hits_lock = threading.Lock()
RATE_LIMIT, RATE_WINDOW = 10, 600          # at most 10 sign-ups from one address in 10 minutes


def _rate_limited(ip):
    now = time.time()
    with _hits_lock:
        recent = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
        limited = len(recent) >= RATE_LIMIT
        if not limited:
            recent.append(now)
        _hits[ip] = recent
        if len(_hits) > 5000:
            for key in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW]:
                _hits.pop(key, None)
    return limited


def allowed_origins():
    return {o.strip().rstrip("/").lower() for o in _env("LEADS_ALLOWED_ORIGINS").split(",") if o.strip()}


def _cors(response):
    origin = (request.headers.get("Origin") or "").strip().rstrip("/").lower()
    if origin and origin in allowed_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Vary"] = "Origin"
    return response


@page_bp.route("/compliance-calendar")
def landing():
    year = calendar_year()
    resp = Response(render_template("leads/compliance_calendar.html", endpoint=url_for("leads.capture"), calendar_url=calendar_url(),
                                    booking_url=booking_url(), year=year, whatsapp=WHATSAPP, auto_email=auto_enabled()), mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@leads_bp.route("/capture", methods=["POST", "OPTIONS"])
def capture():
    """Receives one sign-up (JSON or an ordinary form post). Public, so it is checked hard and answers briefly."""
    if request.method == "OPTIONS":
        return _cors(Response(status=204))

    def reply(payload, status=200):
        return _cors(jsonify(payload)), status

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict()
    if clean(data.get("website"), 200):                                   # the hidden "website" box: only robots fill it in
        return reply({"ok": True})
    from security import client_ip
    if _rate_limited(client_ip()):
        return reply({"ok": False, "error": "Too many requests. Please try again in a few minutes."}, 429)

    first, last = clean(data.get("first_name"), 80), clean(data.get("last_name"), 80)
    email = clean(data.get("email"), 200).lower()
    mobile = clean(data.get("mobile"), 40)
    if not first:
        return reply({"ok": False, "error": "Please enter your first name."}, 400)
    if not emailer.valid_address(email):
        return reply({"ok": False, "error": "Please enter a valid email address."}, 400)
    if mobile and len(re.sub(r"\D", "", mobile)) < 9:
        return reply({"ok": False, "error": "That mobile number looks too short."}, 400)
    if not _truthy(data.get("consent_email")):
        return reply({"ok": False, "error": "Please tick the box to say we may email you."}, 400)
    role = clean(data.get("role"), 60)
    headache = clean(data.get("headache"), 80)
    company = clean(data.get("company"), 160)
    consent_wa = _truthy(data.get("consent_whatsapp")) and bool(mobile)
    source = clean(data.get("source"), 120) or SOURCE_DEFAULT
    utm = {k: clean(data.get(k), 80) for k in ("utm_source", "utm_medium", "utm_campaign")}
    now = datetime.utcnow()
    root = base_url()

    existing = MarketingLead.query.filter(func.lower(MarketingLead.email) == email).order_by(MarketingLead.created_at.desc()).first()
    if existing and (now - (existing.created_at or now)) <= timedelta(days=DUPLICATE_WINDOW_DAYS):
        lead = existing
        if lead.status == "Unsubscribed":                                  # asking again is fresh consent: start over
            stop_sequence(lead)
            lead.status, lead.consent_email, lead.consent_at = "New", True, now
            lead.consent_whatsapp = lead.consent_whatsapp or consent_wa
            lead.notes = (lead.notes or "") + f"\n[{now:%Y-%m-%d}] Signed up again after unsubscribing; follow-ups restarted."
            schedule(lead, now)
        else:
            lead.notes = (lead.notes or "") + f"\n[{now:%Y-%m-%d}] Asked again ({source})."
            if consent_wa and not lead.consent_whatsapp:
                lead.consent_whatsapp = True
        for attr, value in (("company", company), ("job_role", role), ("mobile", mobile), ("headache", headache), ("last_name", last)):
            if value and not getattr(lead, attr):
                setattr(lead, attr, value)
        lead.updated_at = now
        db.session.commit()
        run_after_capture(lead.id, root, repeat=True)
        return reply({"ok": True})

    owner = default_owner()
    lead = MarketingLead(first_name=first, last_name=last, company=company, job_role=role, email=email, mobile=mobile, headache=headache,
                         source=source, consent_email=True, consent_whatsapp=consent_wa, consent_at=now, status="New",
                         priority=_priority_for(role, headache), owner_id=owner.id if owner else None, notes="",
                         unsub_token=secrets.token_urlsafe(24), created_at=now, updated_at=now, **utm)
    db.session.add(lead)
    db.session.flush()
    schedule(lead, now)
    db.session.commit()
    run_after_capture(lead.id, root)
    return reply({"ok": True})


@leads_bp.route("/unsubscribe/<token>", methods=["GET", "POST"])
def unsubscribe(token):
    """The link at the bottom of every automatic email. GET shows a button (so mail scanners that open links do not unsubscribe anyone); POST does it."""
    lead = MarketingLead.query.filter_by(unsub_token=token).first() if token and len(token) >= 16 else None
    if lead is None:
        abort(404)
    done = lead.status == "Unsubscribed"
    if request.method == "POST":
        lead.status, lead.consent_email, lead.updated_at = "Unsubscribed", False, datetime.utcnow()
        stop_sequence(lead)
        db.session.commit()
        done = True
    resp = Response(render_template("leads/unsubscribe.html", done=done, lead=lead), mimetype="text/html")
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@leads_bp.route("/cron", methods=["GET", "POST"])
def cron():
    """For a timer: /leads/cron?token=THE_SECRET (set LEADS_CRON_TOKEN on the server). Sends the automatic emails that are due."""
    secret = _env("LEADS_CRON_TOKEN")
    given = request.values.get("token") or ""
    if not secret or not hmac.compare_digest(secret.encode(), given.encode()):
        abort(404)
    return jsonify(ok=True, sent=process_due(base_url()), automatic=auto_enabled())


# ------------------------------------------------------------------ staff pages

def _filtered(args):
    query = MarketingLead.query
    status = args.get("status") or ""
    if status in LEAD_STATUSES:
        query = query.filter(MarketingLead.status == status)
    elif status == "open":
        query = query.filter(MarketingLead.status.notin_(LEAD_STOP_STATUSES))
    owner = args.get("owner") or ""
    if owner == "me":
        query = query.filter(MarketingLead.owner_id == current_user.id)
    elif owner == "none":
        query = query.filter(MarketingLead.owner_id.is_(None))
    elif owner.isdigit():
        query = query.filter(MarketingLead.owner_id == int(owner))
    if args.get("priority") in PRIORITIES:
        query = query.filter(MarketingLead.priority == args.get("priority"))
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(func.lower(MarketingLead.first_name).like(like), func.lower(MarketingLead.last_name).like(like),
                                 func.lower(MarketingLead.company).like(like), func.lower(MarketingLead.email).like(like)))
    return query.order_by(MarketingLead.created_at.desc())


def _owners():
    return [u for u in User.query.filter(User.is_active_flag.is_(True)).order_by(User.name).all() if user_has_permission(u, "manage_leads")]


@leads_bp.route("/")
@login_required
def index():
    _need_permission()
    root = base_url()
    process_in_background(root)
    leads = _filtered(request.args).limit(300).all()
    counts = dict(db.session.query(MarketingLead.status, func.count(MarketingLead.id)).group_by(MarketingLead.status).all())
    due = []
    for touch in due_now(40):
        due.append((touch, STEPS[touch.step][3] if touch.step in STEPS else touch.step, build_draft(touch.lead, touch, root)))
    setup = {
        "email": emailer.is_configured(), "auto_requested": auto_requested(), "auto": auto_enabled(), "calendar": bool(calendar_url()),
        "booking": bool(booking_url()), "notify": bool(emailer.split_addresses(_env("LEADS_NOTIFY_EMAIL"))), "cron": bool(_env("LEADS_CRON_TOKEN")),
        "owner": default_owner(), "page": f"{root}/compliance-calendar", "year": calendar_year(),
    }
    return render_template("leads/index.html", leads=leads, counts=counts, due=due, setup=setup, statuses=LEAD_STATUSES, priorities=PRIORITIES,
                           owners=_owners(), args=request.args, total=sum(counts.values()), can_delete=current_user.role in ("partner", "admin"),
                           email_ready=emailer.is_configured())


@leads_bp.route("/<int:lead_id>")
@login_required
def detail(lead_id):
    _need_permission()
    lead = MarketingLead.query.get_or_404(lead_id)
    root = base_url()
    rows = []
    for touch in lead.touches:
        rows.append((touch, STEPS[touch.step][3] if touch.step in STEPS else touch.step, build_draft(lead, touch, root)))
    return render_template("leads/detail.html", lead=lead, rows=rows, statuses=LEAD_STATUSES, priorities=PRIORITIES, owners=_owners(),
                           can_delete=current_user.role in ("partner", "admin"), email_ready=emailer.is_configured(), wa_number=phone_digits(lead.mobile))


@leads_bp.route("/<int:lead_id>/update", methods=["POST"])
@login_required
def update(lead_id):
    _need_permission()
    lead = MarketingLead.query.get_or_404(lead_id)
    status = request.form.get("status") or lead.status
    if status in LEAD_STATUSES:
        lead.status = status
        if status in LEAD_STOP_STATUSES:
            stop_sequence(lead)
    priority = request.form.get("priority")
    if priority in PRIORITIES:
        lead.priority = priority
    owner = request.form.get("owner_id") or ""
    if owner == "":
        lead.owner_id = None
    elif owner.isdigit() and User.query.get(int(owner)):
        lead.owner_id = int(owner)
    lead.notes = (request.form.get("notes") or "")[:4000]
    lead.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Lead updated.", "success")
    return redirect(request.form.get("next") or url_for("leads.detail", lead_id=lead.id))


def _touch_or_404(touch_id):
    touch = LeadTouch.query.get_or_404(touch_id)
    return touch


def _back(lead_id):
    target = request.form.get("next") or ""
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("leads.index"))


@leads_bp.route("/touch/<int:touch_id>/send", methods=["POST"])
@login_required
def send_touch(touch_id):
    _need_permission()
    touch = _touch_or_404(touch_id)
    lead = touch.lead
    if touch.status not in ("pending", "failed"):
        flash("That one has already been dealt with.", "info")
    elif not is_live(lead):
        flash("This lead has stopped follow-ups (status: " + lead.status + ").", "warning")
    else:
        ok, err = deliver(touch, base_url(), current_user)
        db.session.commit()
        flash(f"Sent to {lead.email}." if ok else f"Not sent: {err}", "success" if ok else "danger")
    return _back(lead.id)


@leads_bp.route("/touch/<int:touch_id>/done", methods=["POST"])
@login_required
def done_touch(touch_id):
    _need_permission()
    touch = _touch_or_404(touch_id)
    if touch.status in ("pending", "failed"):
        touch.status, touch.done_at, touch.done_by_id = "done", datetime.utcnow(), current_user.id
        _mark_contacted(touch.lead, touch.done_at)
        db.session.commit()
        flash("Marked as done.", "success")
    return _back(touch.lead_id)


@leads_bp.route("/touch/<int:touch_id>/skip", methods=["POST"])
@login_required
def skip_touch(touch_id):
    _need_permission()
    touch = _touch_or_404(touch_id)
    if touch.status in ("pending", "failed"):
        touch.status = "skipped"
        db.session.commit()
        flash("Skipped.", "info")
    return _back(touch.lead_id)


@leads_bp.route("/export.csv")
@login_required
def export():
    _need_permission()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Received", "First name", "Last name", "Company", "Role", "Email", "Mobile", "Biggest headache", "Source", "Campaign",
                     "Status", "Priority", "Owner", "Email consent", "WhatsApp consent", "Last follow-up", "Notes"])
    for lead in _filtered(request.args).all():
        writer.writerow([_csv_safe(v) for v in (
            lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "", lead.first_name, lead.last_name, lead.company, lead.job_role,
            lead.email, lead.mobile, lead.headache, lead.source, lead.utm_campaign, lead.status, lead.priority, lead.owner.name if lead.owner else "",
            "yes" if lead.consent_email else "no", "yes" if lead.consent_whatsapp else "no",
            lead.last_touch_at.strftime("%Y-%m-%d %H:%M") if lead.last_touch_at else "", (lead.notes or "").strip())])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="leads-{datetime.utcnow():%Y-%m-%d}.csv"', "Cache-Control": "no-store"})


@leads_bp.route("/<int:lead_id>/delete", methods=["POST"])
@login_required
def delete(lead_id):
    _need_permission()
    if current_user.role not in ("partner", "admin"):
        abort(403, description="Only a Partner or Admin can delete a lead.")
    lead = MarketingLead.query.get_or_404(lead_id)
    db.session.delete(lead)
    db.session.commit()
    flash("Lead deleted.", "info")
    return redirect(url_for("leads.index"))
