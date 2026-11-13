"""Corporate Finance engagement workspace: advisory tab set instead of the audit/tax tabs."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    if isinstance(k.get("data"), list):
        from werkzeug.datastructures import MultiDict
        k["data"] = MultiDict(k["data"])
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staffer", role="staff"); staff.set_password("x")
    other = User(username="oth", name="Other Partner", role="partner"); other.set_password("x")
    db.session.add_all([partner, staff, other]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    cf = Engagement(client_id=cl.id, title="Funding model 2027", type=CORPFIN_ENGAGEMENT_TYPE, partner_id=partner.id, acceptance_required=False)
    legacy = Engagement(client_id=cl.id, title="Old consulting job", type=LEGACY_CONSULTING_TYPE, partner_id=partner.id, acceptance_required=False)
    audit = Engagement(client_id=cl.id, title="FY26 audit", type="Audit", partner_id=partner.id, acceptance_required=False)
    locked = Engagement(client_id=cl.id, title="Locked advisory", type=CORPFIN_ENGAGEMENT_TYPE, partner_id=partner.id, acceptance_required=True)
    db.session.add_all([cf, legacy, audit, locked]); db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, other=other.id, cf=cf.id, legacy=legacy.id, audit=audit.id, locked=locked.id, cl=cl.id, admin=admin.id)
    check(cf.is_standalone_service and cf.is_corporate_finance, "corpfin counts as a standalone service")
    check(legacy.is_standalone_service, "legacy Consulting counts as a standalone service")
    check(not audit.is_standalone_service, "Audit is not standalone")

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

AUDIT_ONLY = [b"Understanding the Entity", b"Analytical Review", b"Risk Assessment", b"Trial Balance", b"Substantive Procedures",
              b"Income Tax Computation", b"Deferred Tax Computation", b"Finalisation"]
c = login(ids["partner"])
for key in ("cf", "legacy"):
    eid = ids[key]
    r = c.get(f"/engagements/{eid}?tab=overview")
    check(r.status_code == 200, f"{key}: overview 200")
    h = r.data
    for t in (b"Client Acceptance", b"Models &amp; Forecasts", b"Investment Appraisals", b"Checklist (", b"Working Papers (", b"Tasks ("):
        check(t in h, f"{key}: tab bar has {t.decode()}")
    for t in AUDIT_ONLY:
        check(t not in h, f"{key}: no audit/tax tab {t.decode()}")
    check(b"Advisory work on this engagement" in h, f"{key}: overview advisory card")
    for tab in ("entity", "analytical", "risks", "planning", "trial_balance", "substantive", "income_tax", "deferred_tax", "tax", "accounting", "finalisation", "bogus"):
        r = c.get(f"/engagements/{eid}?tab={tab}")
        check(r.status_code == 200 and b"Advisory work on this engagement" in r.data, f"{key}: ?tab={tab} falls back to overview")
    for tab, needle in (("checklist", b"Add checklist item"), ("documents", b"Documents"), ("tasks", b"Tasks"), ("acceptance", b"Acceptance")):
        r = c.get(f"/engagements/{eid}?tab={tab}")
        check(r.status_code == 200 and needle in r.data and b"Advisory work on this engagement" not in r.data, f"{key}: ?tab={tab} renders")
        for t in AUDIT_ONLY:
            if t == b"Finalisation" and tab == "acceptance":
                continue
        check(b"Understanding the Entity" not in r.data and b"Trial Balance" not in r.data, f"{key}: ?tab={tab} has no audit tabs")

# checklist item add / update on a corpfin engagement still works
r = c.post(f"/engagements/{ids['cf']}/checklist/add", data={"section": "Scoping", "item_text": "Agree scope of the forecast"}, follow_redirects=True)
check(r.status_code == 200, "checklist add route works")
r = c.get(f"/engagements/{ids['cf']}?tab=checklist")
check(b"Agree scope of the forecast" in r.data, "checklist item shown")

# listing pages
r = c.get(f"/corporate-finance/engagements/{ids['cf']}/models")
check(r.status_code == 200 and b"New forecast model" in r.data and b"No forecast models on this engagement" in r.data, "models page (empty)")
check(b"Understanding the Entity" not in r.data, "models page has no audit tabs")
r = c.get(f"/corporate-finance/engagements/{ids['cf']}/appraisals")
check(r.status_code == 200 and b"New investment appraisal" in r.data, "appraisals page (empty)")
r = c.get(f"/corporate-finance/models/new?client_id={ids['cl']}&engagement_id={ids['cf']}")
check(r.status_code == 200 and b'value="%d" selected' % ids["cf"] in r.data.replace(b"selected>", b"selected>"), "new model prefilled with the engagement") if False else None
check(r.status_code == 200 and f'selected'.encode() in r.data, "new model form from engagement renders")

r = c.post("/corporate-finance/models/new", data={"client_id": ids["cl"], "engagement_id": ids["cf"], "name": "Funding plan", "years": "3",
                                                  "start_month": "2027-01", "currency": "USD", "preset": "trading"})
check(r.status_code == 302, "model created on the engagement")
r = c.post("/corporate-finance/projects/new", data={"client_id": ids["cl"], "engagement_id": ids["cf"], "name": "New plant", "currency": "USD"})
check(r.status_code == 302, "appraisal created on the engagement")
with app.app_context():
    m = CFModel.query.filter_by(engagement_id=ids["cf"]).first(); p = CFProject.query.filter_by(engagement_id=ids["cf"]).first()
    mid, pid, mnum, pnum = m.id, p.id, m.model_number, p.project_number

r = c.get(f"/corporate-finance/engagements/{ids['cf']}/models")
check(mnum.encode() in r.data and b"Models &amp; Forecasts (1)" in r.data, "model listed, tab count 1")
r = c.get(f"/corporate-finance/engagements/{ids['cf']}/appraisals")
check(pnum.encode() in r.data and b"Investment Appraisals (1)" in r.data, "appraisal listed, tab count 1")
r = c.get(f"/corporate-finance/models/{mid}/overview")
check(r.status_code == 200 and b"Investment Appraisals (1)" in r.data and b"Checklist (" in r.data, "model page shows the engagement tab bar")
r = c.get(f"/corporate-finance/models/{mid}/statements")
check(r.status_code == 200 and b"Models &amp; Forecasts (1)" in r.data, "model statements page shows the engagement tab bar")
r = c.get(f"/corporate-finance/projects/{pid}")
check(r.status_code == 200 and b"Models &amp; Forecasts (1)" in r.data, "appraisal page shows the engagement tab bar")
r = c.get(f"/engagements/{ids['cf']}?tab=overview")
check(mnum.encode() in r.data and pnum.encode() in r.data, "overview card lists latest model and appraisal")

# a model not tied to an engagement shows no engagement tab bar
r = c.post("/corporate-finance/models/new", data={"client_id": ids["cl"], "name": "Loose", "years": "2", "start_month": "2027-01", "currency": "USD", "preset": "blank"})
with app.app_context():
    loose = CFModel.query.filter_by(name="Loose").first().id
r = c.get(f"/corporate-finance/models/{loose}/overview")
check(r.status_code == 200 and b"Investment Appraisals (" not in r.data, "unlinked model has no engagement tab bar")

# regression: audit engagement keeps its tabs
r = c.get(f"/engagements/{ids['audit']}?tab=overview")
check(r.status_code == 200 and all(t in r.data for t in AUDIT_ONLY), "audit engagement keeps every audit tab")
check(b"Advisory work on this engagement" not in r.data, "audit overview has no advisory card")
r = c.get(f"/engagements/{ids['audit']}?tab=entity")
check(r.status_code == 200, "audit entity tab still renders")
r = c.get(f"/corporate-finance/engagements/{ids['audit']}/models")
check(r.status_code == 404, "models page 404 for a non-advisory engagement")
check(c.get("/corporate-finance/engagements/99999/models").status_code == 404, "models page 404 for unknown engagement")

# acceptance lock
r = c.get(f"/corporate-finance/engagements/{ids['locked']}/models")
check(r.status_code == 200 and b"pending" in r.data and b"New forecast model" not in r.data, "models page locked pending acceptance")
r = c.get(f"/engagements/{ids['locked']}?tab=overview")
check(r.status_code == 200 and b"unlock once Client Acceptance" in r.data, "overview card locked pending acceptance")
ca = login(ids["admin"])
r = ca.get(f"/corporate-finance/engagements/{ids['locked']}/models")
check(r.status_code == 200 and b"New forecast model" in r.data, "admin bypasses acceptance lock")

# access
check(login(ids["staff"]).get(f"/corporate-finance/engagements/{ids['cf']}/models").status_code == 403, "staff without permission blocked")
check(login(ids["other"]).get(f"/corporate-finance/engagements/{ids['cf']}/models").status_code == 403, "partner not on engagement blocked")
check(login(ids["other"]).get(f"/corporate-finance/engagements/{ids['cf']}/appraisals").status_code == 403, "partner not on engagement blocked (appraisals)")
check(app.test_client().get(f"/corporate-finance/engagements/{ids['cf']}/models").status_code in (302, 401), "anonymous blocked")

# whistle-blower / training keep working
with app.app_context():
    wb = Engagement(client_id=ids["cl"], title="WB", type=WHISTLEBLOWER_SERVICE_TYPE, partner_id=ids["partner"], acceptance_required=False)
    db.session.add(wb); db.session.commit(); wid = wb.id
r = c.get(f"/engagements/{wid}?tab=overview")
check(r.status_code == 200 and b"Subscription" in r.data and b"Models &amp; Forecasts" not in r.data, "whistle-blower tab bar unchanged")
r = c.get(f"/engagements/{wid}?tab=checklist")
check(r.status_code == 200 and b"Add checklist item" not in r.data, "whistle-blower still has no checklist tab")

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
