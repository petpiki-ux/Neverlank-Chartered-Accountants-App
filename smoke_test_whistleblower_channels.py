"""Whistle-blower service: WhatsApp / email / phone links, QR codes, the client poster, and logging a WhatsApp or email report."""
import os, sys, tempfile, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datetime as dtm
from app import create_app
from extensions import db
from models import *
import whistleblower as wb
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); UID = admin.id
    cl = Client(name="Sunrise Foods (Private) Limited", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Whistle-blower service 2026", type=WHISTLEBLOWER_SERVICE_TYPE, partner_id=admin.id, period_end=dtm.date(2026, 12, 31))
    db.session.add(e); db.session.commit(); EID = e.id
    svc = wb._get_or_create_service(e); TOKEN = svc.public_token
    svc.portal_enabled = True; svc.hotline_whatsapp = "0771 234 567"; svc.hotline_email = "speakup@neverlank.co.zw"; svc.hotline_phone = "+263 242 123 456"
    db.session.commit()
    links = wb.channel_links(svc, "https://app.example/speak-up/" + TOKEN)
    check(links["whatsapp_number"] == "263771234567" and links["whatsapp"].startswith("https://wa.me/263771234567?text=Hello"), f"WhatsApp link made from a local number: {links['whatsapp'][:50]}")
    check(links["mailto"].startswith("mailto:speakup@neverlank.co.zw?subject="), "email link")
    check(links["tel"] == "tel:+263242123456", "call link")
    svc.hotline_whatsapp = "abc"; svc.hotline_email = "not an email"
    bad = wb.channel_links(svc, "x"); check("whatsapp" not in bad and "mailto" not in bad, "a bad number or address makes no link")
    svc.hotline_whatsapp = "+263 77 123 4567"; svc.hotline_email = "speakup@neverlank.co.zw"; db.session.commit()
    check(wb.qr_svg("https://x.example/a").lstrip().startswith("<svg"), "QR code is an inline SVG")
c = app.test_client()
with c.session_transaction() as s: s["_user_id"] = str(UID); s["_fresh"] = True
r = c.get(f"/speak-up/{TOKEN}"); h = r.data.decode()
check(r.status_code == 200 and "https://wa.me/263771234567" in h and "mailto:speakup@neverlank.co.zw" in h and "tel:+263242123456" in h, "public page shows WhatsApp, email and call buttons")
check("but not anonymous" in h, "public page says WhatsApp/email are not anonymous")
r = c.get(f"/whistleblower/engagement/{EID}/subscription"); h = r.data.decode()
check(r.status_code == 200 and "<svg" in h and "Print the poster for the client" in h and "Log a WhatsApp report" in h, "subscription page: QR codes, poster button, log buttons")
r = c.get(f"/whistleblower/engagement/{EID}/poster"); h = r.data.decode()
check(r.status_code == 200 and "SPEAK UP" in h and h.count("<svg") == 2 and "speakup@neverlank.co.zw" in h and "Sunrise Foods" in h, "poster has both QR codes, email and the client's name")
r = c.get(f"/whistleblower/engagement/{EID}/cases?channel=WhatsApp%20/%20SMS"); h = r.data.decode()
check(re.search(r"<option selected>WhatsApp / SMS</option>", h) is not None, "cases page preselects the WhatsApp channel")
r = c.post(f"/whistleblower/engagement/{EID}/cases/add", data={"channel": "WhatsApp / SMS", "category": "fraud", "severity": "High", "description": "Stock is being sold at the back gate at night.", "subject": "Stock theft"}, follow_redirects=False)
with app.app_context():
    case = WhistleblowerCase.query.filter_by(engagement_id=EID).first()
    check(case is not None and case.channel == "WhatsApp / SMS" and case.source == "staff", "a WhatsApp report is logged with its channel")
# switched off page: poster has no web QR
with app.app_context():
    sv = WhistleblowerService.query.filter_by(engagement_id=EID).first(); sv.portal_enabled = False; db.session.commit()
r = c.get(f"/whistleblower/engagement/{EID}/poster"); check(r.data.decode().count("<svg") == 1, "page switched off: poster has the WhatsApp QR only")
print("ALL PASSED" if not fails else f"FAILED {len(fails)}")
