"""Leads: the public landing page and sign-up route, safety checks, follow-up plan and wording, automatic sending (simulated), the staff pages, unsubscribe, timer."""
import os, sys, tempfile, re, json, datetime as dtm
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
for k in ("LEADS_AUTO_EMAIL", "LEADS_CALENDAR_URL", "LEADS_BOOKING_URL", "LEADS_NOTIFY_EMAIL", "LEADS_DEFAULT_OWNER", "LEADS_ALLOWED_ORIGINS", "LEADS_CRON_TOKEN", "LEADS_YEAR", "EMAIL_API_KEY", "EMAIL_FROM", "PUBLIC_BASE_URL"):
    os.environ.pop(k, None)
from app import create_app
from extensions import db
from models import *
import emailer
import leads as LD
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
LD.BACKGROUND = False
LD.process_in_background = lambda root, min_gap=600: None
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

sent = []
def fake_send(to, subject, text, html=None, attachments=None, cc=None):
    if fake_send.fail:
        return False, "The email service refused it (500): test"
    sent.append({"to": list(to), "subject": subject, "text": text, "html": html}); return True, ""
fake_send.fail = False
emailer.send_email = fake_send
emailer.is_configured = lambda: False

with app.app_context():
    aid = User.query.filter_by(role="admin").first().id
    pu = User(username="part1", name="Pat Partner", email="p@x.co", role="partner"); pu.set_password("pw")
    st = User(username="staff1", name="Sam Staff", email="s@x.co", role="staff"); st.set_password("pw")
    sv = User(username="sup1", name="Sue Supervisor", email="sv@x.co", role="supervisor"); sv.set_password("pw")
    db.session.add_all([pu, st, sv]); db.session.commit(); pid, sid, svid = pu.id, st.id, sv.id
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return c
admin, partner, staff, sup = login(aid), login(pid), login(sid), login(svid)
anon = app.test_client()

check(any(p[0] == "manage_leads" and p[3] == ("supervisor", "partner", "admin") for p in PERMISSIONS), "the Manage Marketing Leads permission exists (supervisor, partner, admin)")

# ---- the public page
r = anon.get("/compliance-calendar")
html = r.get_data(as_text=True)
check(r.status_code == 200 and "Compliance Calendar" in html, "the landing page opens without signing in")
check('var LEAD_ENDPOINT = "/leads/capture";' in html and 'name="website"' in html and "{{" not in html and "{%" not in html, "the page posts to the app's own route, has the hidden robot trap and no template leftovers")
check('var CALENDAR_URL  = "";' in html and "var AUTO_EMAIL    = false;" in html, "no calendar link and no automatic email until they are set")
os.environ["LEADS_CALENDAR_URL"] = "https://files.example.org/calendar.pdf"; os.environ["LEADS_BOOKING_URL"] = "https://cal.example.org/neverlank"; os.environ["LEADS_YEAR"] = "2027"
html = anon.get("/compliance-calendar").get_data(as_text=True)
check('"https://files.example.org/calendar.pdf"' in html and '"https://cal.example.org/neverlank"' in html, "the calendar and booking links are put on the page when set")
os.environ["LEADS_CALENDAR_URL"] = ""; os.environ["LEADS_BOOKING_URL"] = ""

# ---- the firm's website may show the page inside its own page; nobody else may
csp = r.headers.get("Content-Security-Policy", "")
check("frame-ancestors 'self' https://neverlank.co.zw https://www.neverlank.co.zw" == csp.strip() and "X-Frame-Options" not in r.headers, "the Compliance Calendar page may be framed by neverlank.co.zw and www.neverlank.co.zw only")
check("neverlankHeight" in html and "postMessage" in html, "the page tells the page around it how tall it is")
os.environ["LEADS_EMBED_ORIGINS"] = "https://other.example.org, http://insecure.example.org"
check("https://other.example.org" in anon.get("/compliance-calendar").headers["Content-Security-Policy"] and "insecure" not in anon.get("/compliance-calendar").headers["Content-Security-Policy"], "LEADS_EMBED_ORIGINS replaces the list and only https addresses count")
os.environ.pop("LEADS_EMBED_ORIGINS")
r2 = anon.get("/leads/unsubscribe/nothing")
check(r2.headers.get("X-Frame-Options") == "SAMEORIGIN", "every other page is still not allowed to be framed by other sites")

# ---- sign-up
def form(**kw):
    d = {"first_name": "Tendai", "last_name": "Moyo", "company": "Moyo Logistics (Pvt) Ltd", "role": "Owner or Director", "email": "Tendai@MoyoLogistics.co.zw",
         "mobile": "077 123 4567", "headache": "Tax and penalties", "consent_email": True, "consent_whatsapp": True, "source": "Magnet 1: Compliance Calendar",
         "utm_source": "linkedin", "utm_campaign": "oct"}
    d.update(kw); return d
LD._hits.clear()
r = anon.post("/leads/capture", json=form())
check(r.status_code == 200 and r.get_json() == {"ok": True}, "a valid sign-up is accepted")
with app.app_context():
    L = MarketingLead.query.all()
    check(len(L) == 1, "one lead stored")
    lead = L[0]; lid = lead.id
    check(lead.email == "tendai@moyologistics.co.zw" and lead.first_name == "Tendai" and lead.owner_id == pid and lead.priority == "High" and lead.status == "New", "email in lower case, owner is the partner, owner or director is high priority")
    check(lead.consent_email and lead.consent_whatsapp and lead.consent_at and lead.utm_source == "linkedin" and lead.utm_campaign == "oct" and lead.unsub_token and len(lead.unsub_token) >= 24, "consent, campaign and a private unsubscribe key are kept")
    steps = [t.step for t in lead.touches]
    check(steps == ["note24h", "email1", "email2", "whatsapp5", "email3", "reengage"] or sorted(steps) == sorted(["note24h", "email1", "email2", "whatsapp5", "email3", "reengage"]), "six follow-ups are planned")
    days = {t.step: (t.due_at - lead.created_at).days for t in lead.touches}
    check(days == {"note24h": 0, "email1": 0, "email2": 3, "whatsapp5": 5, "email3": 7, "reengage": 14}, "on days 0, 0, 3, 5, 7 and 14")
    check({t.step: t.mode for t in lead.touches} == {"note24h": "manual", "email1": "auto", "email2": "auto", "whatsapp5": "manual", "email3": "auto", "reengage": "auto"}, "personal steps are manual, emails are automatic")

bad = [(form(first_name=""), "first name"), (form(email="not-an-email"), "valid email"), (form(mobile="123"), "too short"), (form(consent_email=False), "tick the box"), (form(consent_email=""), "tick the box"), (form(email="a@b"), "valid email")]
for item in bad:
    r = anon.post("/leads/capture", json=item[0])
    check(r.status_code == 400 and r.get_json()["ok"] is False and item[1] in r.get_json()["error"], f"refused: {item[1]} ({item[0].get('email')})")
r = anon.post("/leads/capture", json=form(email="bot@spam.com", website="http://spam.example"))
with app.app_context():
    check(r.get_json() == {"ok": True} and MarketingLead.query.filter_by(email="bot@spam.com").count() == 0, "the hidden robot trap: answered politely, nothing stored")
r = anon.post("/leads/capture", data="garbage", content_type="text/plain")
check(r.status_code == 400, "a body that is not a sign-up is refused")

r = anon.post("/leads/capture", json=form(email="TENDAI@moyologistics.co.zw", company="Other name"))
with app.app_context():
    check(MarketingLead.query.count() == 1 and "Asked again" in MarketingLead.query.get(lid).notes, "the same person asking again is not a second lead; it is noted")
    check(MarketingLead.query.get(lid).company == "Moyo Logistics (Pvt) Ltd" and len(MarketingLead.query.get(lid).touches) == 6, "their details and plan are not overwritten or doubled")

r = anon.post("/leads/capture", data={"first_name": "Rudo", "last_name": "<b>Dube</b>\r\n", "company": "x" * 500, "role": "CFO or Finance Manager", "email": "rudo@dube.co.zw",
                                       "consent_email": "on", "headache": "Late or messy books"})
with app.app_context():
    r2 = MarketingLead.query.filter_by(email="rudo@dube.co.zw").first()
    check(r.status_code == 200 and r2 and len(r2.company) == 160 and "\n" not in r2.last_name and r2.priority == "Normal" and r2.consent_whatsapp is False, "an ordinary form post works; over-long text is cut and line breaks removed; CFO is normal priority")
    check(not [t for t in r2.touches if t.step == "whatsapp5"] and len(r2.touches) == 5, "no WhatsApp step without a mobile number and consent")
    rid = r2.id
LD._hits.clear()
r = anon.post("/leads/capture", json=form(email="audit@firm.co.zw", role="Other", headache="Audit or bank requirements", mobile=""))
with app.app_context():
    check(MarketingLead.query.filter_by(email="audit@firm.co.zw").first().priority == "High", "an audit or bank requirement headache is high priority")

LD._hits.clear()
codes = [anon.post("/leads/capture", json=form(email=f"x{i}@rate.test")).status_code for i in range(12)]
check(codes[:10] == [200] * 10 and codes[10:] == [429, 429], "more than 10 sign-ups from one address in 10 minutes are refused")
LD._hits.clear()

# ---- cross-site protection stays on, except for this one route and only for listed sites
r = anon.post("/leads/capture", json=form(email="x@cross.test"), headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
check(r.status_code == 403, "a cross-site post from an unlisted site is refused")
os.environ["LEADS_ALLOWED_ORIGINS"] = "https://www.neverlank.co.zw, https://neverlank.co.zw/"
r = anon.post("/leads/capture", json=form(email="x@cross.test"), headers={"Origin": "https://www.neverlank.co.zw", "Sec-Fetch-Site": "cross-site"})
check(r.status_code == 200 and r.headers.get("Access-Control-Allow-Origin") == "https://www.neverlank.co.zw", "a listed site may post, and gets the answer back")
r = anon.post("/leads/capture", json=form(email="y@cross.test"), headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
check(r.status_code == 403 and "Access-Control-Allow-Origin" not in r.headers, "an unlisted site is still refused when others are listed")
r = anon.options("/leads/capture", headers={"Origin": "https://neverlank.co.zw", "Access-Control-Request-Method": "POST"})
check(r.status_code == 204 and r.headers.get("Access-Control-Allow-Origin") == "https://neverlank.co.zw" and "POST" in r.headers.get("Access-Control-Allow-Methods", ""), "the browser's pre-check is answered for a listed site")
r = partner.post(f"/leads/{lid}/update", data={"status": "Won"}, headers={"Origin": "https://www.neverlank.co.zw", "Sec-Fetch-Site": "cross-site"})
check(r.status_code == 403, "no other page is opened to the listed sites")
os.environ.pop("LEADS_ALLOWED_ORIGINS")

# ---- the wording
class _T:  # a stand-in for a follow-up step
    def __init__(self, step): self.step = step
with app.app_context():
    lead = MarketingLead.query.get(lid)
    for step in LD.STEP_ORDER:
        d = LD.build_draft(lead, _T(step), "https://app.neverlank.co.zw")
        blob = d["subject"] + d["text"]
        check(bool(d["text"]) and not re.search(r"[\[\]{}]|None|TODO", blob) and "Tendai" in blob, f"{step}: wording is complete, no placeholders")
        if step in ("email1", "email2", "email3", "reengage"):
            check("/leads/unsubscribe/" + lead.unsub_token in d["text"] and "/leads/unsubscribe/" + lead.unsub_token in d["html"] and "Michael House" in d["text"], f"{step}: has the address and an unsubscribe link")
        else:
            check("unsubscribe" not in d["text"].lower(), f"{step}: a personal message carries no unsubscribe footer")
    e1 = LD.build_draft(lead, _T("email1"), "")["text"]
    check("files.example.org" not in e1 and "Download your calendar" not in e1 and "WhatsApp us on +263 71 811 0832" in e1 and "2027" in e1, "email 1 without links set: the download line is left out and WhatsApp is offered")
    check("Tax and penalties" not in LD.build_draft(lead, _T("email2"), "")["text"] and "penalties" in LD.build_draft(lead, _T("email2"), "")["text"], "email 2 speaks to the headache they named")
    other = MarketingLead(first_name="Z", email="z@z.co", headache="Other", unsub_token="t" * 30)
    check("named person" in LD.build_draft(other, _T("email2"), "")["text"].lower() or "name next to every line" in LD.build_draft(other, _T("email2"), "")["text"], "email 2 has a general version")
    wa = LD.build_draft(lead, _T("whatsapp5"), "")["wa"]
    check(wa.startswith("https://wa.me/263771234567?text=") and "Hello%20Tendai" in wa, "WhatsApp link: 077 number turned into 263 and the message is filled in")
    evil = MarketingLead(first_name="Eve", company="<script>alert(1)</script>", email="e@e.co", unsub_token="u" * 30)
    h = LD.build_draft(evil, _T("note24h"), "")["html"]
    check("<script>" not in h and "&lt;script&gt;" in h, "text typed on the public page is escaped in the HTML email")
    os.environ["LEADS_CALENDAR_URL"] = "https://files.example.org/calendar.pdf"; os.environ["LEADS_BOOKING_URL"] = "https://cal.example.org/neverlank"
    d1 = LD.build_draft(lead, _T("email1"), "https://app.neverlank.co.zw")
    check("Download your calendar: https://files.example.org/calendar.pdf" in d1["text"] and 'href="https://files.example.org/calendar.pdf"' in d1["html"], "with the links set they appear in the text and the button")
    check("Book it here" in LD.build_draft(lead, _T("email3"), "")["text"] and "cal.example.org" in LD.build_draft(lead, _T("email3"), "")["text"], "the booking page is used when set")
    os.environ["LEADS_CALENDAR_URL"] = ""; os.environ["LEADS_BOOKING_URL"] = ""

# ---- phone numbers
check(LD.phone_digits("077 123 4567") == "263771234567" and LD.phone_digits("+263 77 123 4567") == "263771234567" and LD.phone_digits("00263771234567") == "263771234567" and LD.phone_digits("") == "", "phone numbers are made WhatsApp-ready")

# ---- sending: off until asked for, then through the email service
with app.app_context():
    check(LD.auto_enabled() is False and LD.process_due("https://x") == 0 and not sent, "automatic sending is off by default and nothing goes out")
    due = LD.due_now()
    check({t.step for t in due if t.lead_id == lid} == {"note24h", "email1"}, "meanwhile a person sees the personal note and email 1 as due now")
    check(LD.due_count() >= 3, "the menu badge counts leads with something due")
os.environ["LEADS_AUTO_EMAIL"] = "1"
with app.app_context():
    check(LD.auto_enabled() is False, "asking for automatic sending is not enough without the email service and a calendar link")
emailer.is_configured = lambda: True
os.environ["LEADS_CALENDAR_URL"] = "https://files.example.org/calendar.pdf"
with app.app_context():
    check(LD.auto_enabled() is True, "with all three it is on")
    n = LD.process_due("https://app.neverlank.co.zw")
    mails = [m for m in sent if m["to"] == ["tendai@moyologistics.co.zw"]]
    check(n >= 1 and len(mails) == 1 and mails[0]["subject"] == "Your 2027 Compliance Calendar", "email 1 goes out by itself, once")
    check(LD.process_due("https://app.neverlank.co.zw") == 0 and len([m for m in sent if m["to"] == ["tendai@moyologistics.co.zw"]]) == 1, "running it again does not send it twice")
    lead = MarketingLead.query.get(lid)
    check(lead.status == "Contacted" and lead.last_touch_at is not None and {t.step: t.status for t in lead.touches}["email1"] == "sent", "the lead becomes Contacted and the touch is recorded")
    check({t.step for t in LD.due_now() if t.lead_id == lid} == {"note24h"}, "now only the personal note is left for a person")
    # not yet due emails are not sent early
    check({t.step: t.status for t in lead.touches}["email2"] == "pending", "email 2 waits for day 3")
    # time passes: day 3 and day 7
    for t in lead.touches:
        if t.step in ("email2", "email3"): t.due_at = dtm.datetime.utcnow() - dtm.timedelta(hours=1)
    db.session.commit()
    before = len(sent)
    LD.process_due("https://x")
    check(len(sent) - before >= 2 and {t.step: t.status for t in MarketingLead.query.get(lid).touches}["email3"] == "sent", "later emails go out when their day comes")

    # failures are retried a little later, then given up
    r3 = MarketingLead(first_name="Fail", email="fail@x.co", consent_email=True, status="New", unsub_token="f" * 30, created_at=dtm.datetime.utcnow())
    db.session.add(r3); db.session.flush(); LD.schedule(r3, dtm.datetime.utcnow() - dtm.timedelta(minutes=5)); db.session.commit(); fid = r3.id
    fake_send.fail = True
    LD.process_due("https://x")
    t1 = [t for t in MarketingLead.query.get(fid).touches if t.step == "email1"][0]
    check(t1.status == "pending" and t1.tries == 1 and t1.due_at > dtm.datetime.utcnow() and "refused" in t1.error, "a failed send is kept and tried again in an hour")
    for _ in range(3):
        t1 = LeadTouch.query.get(t1.id); t1.due_at = dtm.datetime.utcnow() - dtm.timedelta(minutes=1); db.session.commit(); LD.process_due("https://x")
    t1 = LeadTouch.query.get(t1.id)
    check(t1.status == "failed" and t1.tries == 3, "after three tries it is marked failed")
    check(t1.id in {t.id for t in LD.due_now()}, "a failed email shows up for a person to deal with")
    fake_send.fail = False

    # people who stopped get nothing
    lead = MarketingLead(first_name="Stop", email="stop@x.co", consent_email=True, status="Replied", unsub_token="s" * 30, created_at=dtm.datetime.utcnow())
    db.session.add(lead); db.session.flush(); LD.schedule(lead, dtm.datetime.utcnow() - dtm.timedelta(days=20)); db.session.commit()
    before = len(sent)
    LD.process_due("https://x")
    check(len(sent) == before and all(t.status == "skipped" for t in MarketingLead.query.get(lead.id).touches), "a lead who replied gets nothing more and the plan is closed")
    nc = MarketingLead(first_name="NoConsent", email="nc@x.co", consent_email=False, status="New", unsub_token="n" * 30, created_at=dtm.datetime.utcnow())
    db.session.add(nc); db.session.flush(); LD.schedule(nc, dtm.datetime.utcnow() - dtm.timedelta(days=20)); db.session.commit()
    before = len(sent); LD.process_due("https://x")
    check(len(sent) == before, "no email to someone who has not agreed to emails")
emailer.is_configured = lambda: False
os.environ.pop("LEADS_AUTO_EMAIL")

# ---- who is told
sent.clear()
emailer.is_configured = lambda: True
LD._hits.clear()
anon.post("/leads/capture", json=form(email="new@person.co.zw", first_name="Nyasha", company="Nyasha Farms"))
check(len(sent) == 1 and sent[0]["to"] == ["p@x.co"] and sent[0]["subject"].startswith("New lead: Nyasha Farms") and "(high priority)" in sent[0]["subject"], "the owner is told of a new sign-up (no automatic emails while that is off)")
sent.clear(); os.environ["LEADS_NOTIFY_EMAIL"] = "a@firm.co.zw; b@firm.co.zw"
anon.post("/leads/capture", json=form(email="new2@person.co.zw", company="Two"))
check(len(sent) == 1 and sent[0]["to"] == ["a@firm.co.zw", "b@firm.co.zw"], "or the people named in LEADS_NOTIFY_EMAIL")
os.environ.pop("LEADS_NOTIFY_EMAIL"); sent.clear(); emailer.is_configured = lambda: False

# ---- staff pages
check(anon.get("/leads/", follow_redirects=False).status_code in (302, 401), "the list needs a sign-in")
check(staff.get("/leads/").status_code == 403, "ordinary staff are not allowed in")
check(partner.get("/leads/").status_code == 200 and sup.get("/leads/").status_code == 200 and admin.get("/leads/").status_code == 200, "supervisors, partners and admins are")
page = partner.get("/leads/").get_data(as_text=True)
check("Tendai Moyo" in page and "Moyo Logistics" in page and "Do these now" in page and "Pat Partner" in page, "the list shows the lead, the company and the owner")
check('href="/leads/"' in page, "the menu has a Leads link for those allowed")
check('href="/leads/"' not in staff.get("/", follow_redirects=True).get_data(as_text=True) and 'href="/leads/"' in partner.get("/", follow_redirects=True).get_data(as_text=True), "and not for staff")
check(partner.get("/leads/?status=open&owner=me&priority=High&q=moyo").status_code == 200 and "Tendai" in partner.get("/leads/?q=MOYO").get_data(as_text=True), "search and filters work")
check("No leads match these filters" in partner.get("/leads/?q=nobody-by-this-name").get_data(as_text=True), "a search with no match shows nothing")
check("No leads match these filters" in partner.get("/leads/?status=Won").get_data(as_text=True), "the status filter works")
d = partner.get(f"/leads/{lid}")
check(d.status_code == 200 and "Follow-up plan" in d.get_data(as_text=True) and "tendai@moyologistics.co.zw" in d.get_data(as_text=True), "the lead page opens")
check(staff.get(f"/leads/{lid}").status_code == 403 and anon.get(f"/leads/{lid}").status_code in (302, 401), "and is closed to others")

# send / done / skip from the page
emailer.is_configured = lambda: True
with app.app_context():
    sn = MarketingLead(first_name="Send", email="sendnow@x.co", consent_email=True, status="New", unsub_token="g" * 30, created_at=dtm.datetime.utcnow())
    db.session.add(sn); db.session.flush(); LD.schedule(sn, dtm.datetime.utcnow()); db.session.commit(); snid = sn.id
    tid = [t for t in MarketingLead.query.get(snid).touches if t.step == "email1"][0].id
    tnote = [t for t in MarketingLead.query.get(lid).touches if t.step == "note24h"][0].id
    twa = [t for t in MarketingLead.query.get(lid).touches if t.step == "whatsapp5"][0].id
sent.clear()
r = partner.post(f"/leads/touch/{tid}/send", data={"next": "/leads/"}, follow_redirects=True)
with app.app_context():
    tt = LeadTouch.query.get(tid)
    check(len(sent) == 1 and sent[0]["to"] == ["sendnow@x.co"] and tt.status == "sent" and tt.done_by_id == pid and MarketingLead.query.get(snid).status == "Contacted", "Send now sends it, records who did and moves the lead to Contacted")
r = partner.post(f"/leads/touch/{tid}/send", data={}, follow_redirects=True)
check(len(sent) == 1 and "already been dealt with" in r.get_data(as_text=True), "pressing it twice does not send twice")
partner.post(f"/leads/touch/{tnote}/done", data={})
partner.post(f"/leads/touch/{twa}/skip", data={})
with app.app_context():
    check(LeadTouch.query.get(tnote).status == "done" and LeadTouch.query.get(tnote).done_by_id == pid and LeadTouch.query.get(twa).status == "skipped", "mark done and skip work")
check(staff.post(f"/leads/touch/{tid}/skip", data={}).status_code == 403, "staff cannot act on follow-ups")
r = partner.post(f"/leads/touch/{tid}/skip", data={"next": "https://evil.example/"}, follow_redirects=False)
check(r.status_code == 302 and "evil.example" not in r.headers["Location"], "the 'back to' address cannot point to another site")
emailer.is_configured = lambda: False

# update: owner, status, notes
r = partner.post(f"/leads/{rid}/update", data={"status": "Booked", "owner_id": str(svid), "priority": "High", "notes": "Met at ICAZ event"}, follow_redirects=True)
with app.app_context():
    x = MarketingLead.query.get(rid)
    check(x.status == "Booked" and x.owner_id == svid and x.priority == "High" and x.notes == "Met at ICAZ event", "owner, status, priority and notes can be changed")
    check(all(t.status != "pending" for t in x.touches), "a booked lead has nothing left pending")
partner.post(f"/leads/{rid}/update", data={"status": "Contacted", "owner_id": ""})
with app.app_context():
    check(MarketingLead.query.get(rid).owner_id is None and MarketingLead.query.get(rid).status == "Contacted", "the owner can be cleared")
check(staff.post(f"/leads/{rid}/update", data={"status": "Won"}).status_code == 403, "staff cannot change a lead")

# export
r = partner.get("/leads/export.csv")
check(r.status_code == 200 and r.mimetype == "text/csv" and "Tendai" in r.get_data(as_text=True) and "attachment" in r.headers["Content-Disposition"], "the list downloads as CSV")
LD._hits.clear()
anon.post("/leads/capture", json=form(email="f@formula.co", first_name="=HYPERLINK(\"http://evil\")", company="+cmd|' /C calc'!A0"))
csvtxt = partner.get("/leads/export.csv").get_data(as_text=True)
check("'=HYPERLINK" in csvtxt and "'+cmd" in csvtxt and ",=HYPERLINK" not in csvtxt, "spreadsheet formulas typed on the public form are defused in the CSV")
check(staff.get("/leads/export.csv").status_code == 403, "staff cannot download the list")

# ---- unsubscribe
with app.app_context():
    tok = MarketingLead.query.get(lid).unsub_token
    MarketingLead.query.get(lid).status = "Contacted"; db.session.commit()
r = anon.get(f"/leads/unsubscribe/{tok}")
with app.app_context():
    check(r.status_code == 200 and "Yes, stop the emails" in r.get_data(as_text=True) and MarketingLead.query.get(lid).status == "Contacted", "opening the link only shows a button (a mail scanner opening it changes nothing)")
r = anon.post(f"/leads/unsubscribe/{tok}")
with app.app_context():
    x = MarketingLead.query.get(lid)
    check(r.status_code == 200 and "You are unsubscribed" in r.get_data(as_text=True) and x.status == "Unsubscribed" and not x.consent_email and all(t.status != "pending" for t in x.touches), "pressing it unsubscribes and ends the plan")
check(anon.get("/leads/unsubscribe/short").status_code == 404 and anon.get("/leads/unsubscribe/" + "z" * 30).status_code == 404, "a wrong link is a 404")
LD._hits.clear()
anon.post("/leads/capture", json=form())
with app.app_context():
    x = MarketingLead.query.get(lid)
    check(MarketingLead.query.filter_by(email="tendai@moyologistics.co.zw").count() == 1 and x.status == "New" and x.consent_email and len([t for t in x.touches if t.status == "pending"]) >= 5, "someone who signs up again after unsubscribing has given fresh consent: the plan restarts")

# ---- timer
r = anon.get("/leads/cron?token=anything")
check(r.status_code == 404, "the timer address is closed until a token is set")
os.environ["LEADS_CRON_TOKEN"] = "cron-secret-value"
check(anon.get("/leads/cron?token=wrong").status_code == 404 and anon.get("/leads/cron").status_code == 404, "a wrong or missing token is refused")
r = anon.get("/leads/cron?token=cron-secret-value")
check(r.status_code == 200 and r.get_json()["ok"] is True and r.get_json()["automatic"] is False, "the right token runs it")
os.environ.pop("LEADS_CRON_TOKEN")

# ---- delete
with app.app_context():
    did = MarketingLead.query.filter_by(email="f@formula.co").first().id
check(sup.post(f"/leads/{did}/delete").status_code == 403, "a supervisor cannot delete")
partner.post(f"/leads/{did}/delete")
with app.app_context():
    check(MarketingLead.query.get(did) is None and LeadTouch.query.filter_by(lead_id=did).count() == 0, "a partner can delete a lead and its plan")

# ---- default owner setting
os.environ["LEADS_DEFAULT_OWNER"] = "sue.nobody"
with app.app_context():
    check(LD.default_owner().id == pid, "an unknown owner setting falls back to the first partner")
os.environ["LEADS_DEFAULT_OWNER"] = "SV@x.co"
with app.app_context():
    check(LD.default_owner().id == svid, "the owner can be set by email")
os.environ.pop("LEADS_DEFAULT_OWNER")
with app.app_context():
    pu_ = User.query.get(pid); pu_.is_active_flag = False; db.session.commit()
    check(LD.default_owner().id == User.query.filter_by(role="admin").order_by(User.id).first().id, "with no active partner, an admin owns new leads")
    pu_.is_active_flag = True; db.session.commit()

print(len(fails), "failure(s)")
sys.exit(1 if fails else 0)
