"""Sending email from the app through an HTTPS email service (Resend or Brevo).

Why an API rather than SMTP: it works from any host, needs no mailbox
password, and the service signs the mail for your own domain so it lands in
the inbox. Set these environment variables (Render > Environment):

    EMAIL_PROVIDER   resend (default) or brevo
    EMAIL_API_KEY    the API key from that service
    EMAIL_FROM       the sender, e.g.  Neverlank Chartered Accountants <accounts@neverlank.co.zw>
                     (the domain must be verified in the email service)
    EMAIL_REPLY_TO   optional - where client replies should go

Nothing here is used until EMAIL_API_KEY and EMAIL_FROM are both set;
is_configured() lets the pages say so plainly instead of failing.
"""
import base64
import os
import re

import requests

TIMEOUT_SECONDS = 25
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def provider():
    return (os.environ.get("EMAIL_PROVIDER") or "resend").strip().lower()


def is_configured():
    return bool(os.environ.get("EMAIL_API_KEY") and os.environ.get("EMAIL_FROM"))


def valid_address(value):
    return bool(_ADDRESS.match((value or "").strip()))


def split_addresses(value):
    """'a@x.com, b@y.com; c@z.com' -> ['a@x.com', 'b@y.com', 'c@z.com'] (order kept, duplicates dropped)."""
    seen, out = set(), []
    for part in re.split(r"[,;\s]+", value or ""):
        part = part.strip()
        if part and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return out


def _parse_from(value):
    """'Name <a@b.com>' -> ('Name', 'a@b.com'); a bare address gives name ''."""
    m = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", value or "")
    if m:
        return m.group(1).strip('" '), m.group(2).strip()
    return "", (value or "").strip()


def send_email(to, subject, text, html=None, attachments=None, cc=None):
    """Send one email. `to`/`cc` are lists of addresses; `attachments` is a list of
    (filename, bytes, mimetype). Returns (True, "") or (False, reason)."""
    if not is_configured():
        return False, "Email is not set up yet (EMAIL_API_KEY and EMAIL_FROM are missing)."
    to = [a for a in (to or []) if a]
    cc = [a for a in (cc or []) if a]
    if not to or not all(valid_address(a) for a in to + cc):
        return False, "Enter a valid email address."
    key = os.environ["EMAIL_API_KEY"]
    sender = os.environ["EMAIL_FROM"]
    reply_to = (os.environ.get("EMAIL_REPLY_TO") or "").strip()
    files = [(name, base64.b64encode(data).decode("ascii")) for name, data, _mime in (attachments or [])]
    try:
        if provider() == "brevo":
            name, address = _parse_from(sender)
            body = {"sender": {"email": address, **({"name": name} if name else {})},
                    "to": [{"email": a} for a in to], "subject": subject, "textContent": text}
            if html:
                body["htmlContent"] = html
            if cc:
                body["cc"] = [{"email": a} for a in cc]
            if reply_to:
                body["replyTo"] = {"email": reply_to}
            if files:
                body["attachment"] = [{"name": n, "content": c} for n, c in files]
            resp = requests.post("https://api.brevo.com/v3/smtp/email", json=body, timeout=TIMEOUT_SECONDS,
                                 headers={"api-key": key, "accept": "application/json"})
        else:
            body = {"from": sender, "to": to, "subject": subject, "text": text}
            if html:
                body["html"] = html
            if cc:
                body["cc"] = cc
            if reply_to:
                body["reply_to"] = reply_to
            if files:
                body["attachments"] = [{"filename": n, "content": c} for n, c in files]
            resp = requests.post("https://api.resend.com/emails", json=body, timeout=TIMEOUT_SECONDS,
                                 headers={"Authorization": f"Bearer {key}"})
    except requests.RequestException as exc:
        return False, f"Could not reach the email service: {exc.__class__.__name__}."
    if 200 <= resp.status_code < 300:
        return True, ""
    try:
        detail = resp.json()
        detail = detail.get("message") or detail.get("error") or str(detail)
    except ValueError:
        detail = resp.text[:200]
    return False, f"The email service refused it ({resp.status_code}): {str(detail)[:200]}"
