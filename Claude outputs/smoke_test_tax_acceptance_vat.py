"""Tax Advisory engagements: a Client Acceptance written for tax work (five tax sections instead of the audit ones), and a VAT tab whenever the client has a
VAT number. Other engagement types keep the audit acceptance sections."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import date, datetime
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

with app.app_context():
    mp = User(username="mp", name="Managing Partner", role="admin"); mp.set_password("x")
    ptn = User(username="ptn", name="Tax Partner", role="partner"); ptn.set_password("x")
    db.session.add_all([mp, ptn]); db.session.commit()
    c_vat = Client(name="Acme Holdings (Pvt) Ltd", vat_number="10012345", tin_number="2000111222", created_by_id=mp.id)
    c_no = Client(name="Small Trader", created_by_id=mp.id)
    db.session.add_all([c_vat, c_no]); db.session.commit()
    tax = Engagement(client_id=c_vat.id, title="Tax 2026", type=TAX_ADVISORY_TYPE, partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=True)
    tax2 = Engagement(client_id=c_no.id, title="Tax 2026 small", type=TAX_ADVISORY_TYPE, partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=False)
    aud = Engagement(client_id=c_vat.id, title="Audit 2026", type="Audit", partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=True)
    audtax = Engagement(client_id=c_vat.id, title="Audit with tax", type="Audit", partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=False, tax_services="compliance")
    audnotax = Engagement(client_id=c_vat.id, title="Plain audit", type="Audit", partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=False)
    db.session.add_all([tax, tax2, aud, audtax, audnotax]); db.session.commit()
    T, T2, A, AT, AN = tax.id, tax2.id, aud.id, audtax.id, audnotax.id
    M = login(mp.id)
    def page(eid, tab, **kw):
        r = M.get(f"/engagements/{eid}", query_string=dict(tab=tab, **kw), follow_redirects=True)
        return r, r.get_data(as_text=True)
    def link(h, tab): return f"tab={tab}\"" in h

    # ---------------- Client Acceptance for tax work
    r, h = page(T, "acceptance")
    check(r.status_code == 200, "the Tax Advisory acceptance tab opens")
    for t in ("1. Taxpayer Identity, KYC", "2. Tax Compliance History", "3. Scope", "4. Integrity", "5. Conflicts, Independence"):
        check(t in h, f"acceptance section: {t}")
    for t in ("Predecessor communication", "2. Independence assessment", "Background check", "Competence check"):
        check(t not in h, f"no audit section: {t}")
    check("Before tax work starts" in h and "tax services, tax heads and periods" in h, "tax wording in the introduction and the engagement letter")
    r, h = page(A, "acceptance")
    check("Predecessor communication" in h and "Taxpayer Identity" not in h, "an Audit engagement keeps the audit acceptance sections")

    ca = lambda: ClientAcceptance.query.filter_by(engagement_id=T).first()
    saves = [("tax-kyc", "tax_kyc_satisfactory", "tax_kyc_notes"), ("tax-history", "tax_history_satisfactory", "tax_history_notes"), ("tax-scope", "tax_scope_clear", "tax_scope_notes"),
             ("tax-integrity", "tax_integrity_acceptable", "tax_integrity_notes"), ("tax-conflict-competence", "tax_conflict_competence_clear", "tax_conflict_competence_notes")]
    for i, (url, flag, notes) in enumerate(saves):
        r = M.post(f"/engagements/{T}/acceptance/{url}/save", data={flag: "on", notes: f"note {i}"})
        db.session.expire_all()
        check(r.status_code == 302 and getattr(ca(), flag) is True and getattr(ca(), notes) == f"note {i}", f"saving {flag}")
    check(not ca().items_complete, "not complete until the engagement letter is signed")
    ca().engagement_letter_signed_at = datetime.utcnow(); db.session.commit()
    db.session.expire_all()
    check(ca().items_complete, "complete once the five tax sections and the signed letter are in")
    # an untouched section keeps it incomplete
    ca().tax_scope_clear = None; db.session.commit(); db.session.expire_all()
    check(not ca().items_complete, "a section left unassessed keeps it incomplete")
    ca().tax_scope_clear = True; db.session.commit()
    # flagged issues
    M.post(f"/engagements/{T}/acceptance/tax-history/save", data={"tax_history_notes": "TCC expired, 2 returns outstanding"})
    db.session.expire_all()
    fl = [i for i in ca().flagged_issues if i["key"] == "section:tax_history_satisfactory"]
    check(len(fl) == 1 and fl[0]["label"] == "Tax Compliance History & Standing" and "TCC expired" in fl[0]["detail"], "an unfavourable tax section is flagged for the Partner")
    check(not any(i["key"].startswith("section:predecessor") for i in ca().flagged_issues), "no predecessor-auditor flags on tax work")
    # checklist
    r = M.post(f"/engagements/{T}/acceptance/checklist/seed")
    db.session.expire_all()
    secs = {i.section for i in ca().checklist_items}
    check(secs == {"Taxpayer Identity, KYC & Registrations", "Tax Compliance History & Standing", "Scope & Nature of Tax Work", "Integrity & Tax Risk Appetite", "Conflicts, Independence & Competence", "Engagement letter"},
          "the default checklist is the tax checklist")
    check(len(ca().checklist_items) == len(TAX_ACCEPTANCE_CHECKLIST_ITEMS) and any("tax clearance" in i.item_text.lower() for i in ca().checklist_items), "...with the tax questions (e.g. tax clearance certificate)")
    r, h = page(T, "acceptance")
    check("tax clearance certificate" in h.lower() and "Other checklist items" not in h, "the tax checklist shows under its sections, none left over")
    r = M.post(f"/engagements/{T}/workpapers/acceptance-checklist/generate", follow_redirects=True)
    check(r.status_code == 200, "the acceptance checklist workpaper still files")
    # audit checklist unchanged
    M.post(f"/engagements/{A}/acceptance/checklist/seed")
    check({i.section for i in ClientAcceptance.query.filter_by(engagement_id=A).first().checklist_items} >= {"Background check", "Independence"}, "Audit: the default checklist is unchanged")

    # ---------------- VAT tab
    r, h = page(T, "overview")
    check(link(h, "vat"), "the client has a VAT number: the VAT tab is there")
    r, h = page(T2, "overview")
    check(not link(h, "vat"), "no VAT number: no VAT tab")
    r, h = page(AT, "overview")
    check(link(h, "vat"), "an Audit engagement with a tax service also gets the VAT tab for a VAT-registered client")
    r, h = page(AN, "overview")
    check(not link(h, "vat"), "an engagement with no tax service has no VAT tab")
    r, h = page(AT, "overview")
    check(link(h, "capital_allowances") and link(h, "qpd") and link(h, "substantive"), "an Audit engagement with a tax service gets Capital Allowances and QPD and keeps its audit tabs")
    r, h = page(AN, "overview")
    check(not link(h, "capital_allowances") and not link(h, "qpd"), "an engagement with no tax service has neither")
    r, h = page(AT, "qpd")
    check(r.status_code == 200 and "This engagement's computation" in h, "the QPD tab opens on an Audit engagement with a tax service")
    r, h = page(T, "vat")
    check(r.status_code == 200 and "Monthly VAT Computations" in h and "10012345" in h, "the VAT tab holds the VAT register and shows the client's VAT number")
    r, h = page(T, "tax")
    check("Monthly VAT Computations" not in h and "own <a" in h and "VAT tab" in h, "the Tax tab points to the VAT tab instead of repeating the register")
    r, h = page(T2, "tax")
    check("Monthly VAT Computations" in h, "no VAT number: the register stays on the Tax tab")
    r, h = page(T2, "vat")
    check(r.status_code == 200 and "Monthly VAT Computations" in h and "Add the client's VAT number" not in h, "a VAT link on an engagement without the tab lands on the Tax tab")
    r, h = page(T, "tax", vat_period="2026-09")
    check("Client VAT number" in h, "an old Tax-tab VAT month link lands on the VAT tab")
    # capturing a VAT document returns to the VAT tab
    M.post("/tax/vat-rates/add", data={"rate_type": "Standard", "rate_percent": "15", "effective_from": "2020-01-01", "description": "Standard"})
    r = M.post(f"/tax/{T}/vat/invoices/add", data={"kind": "output_invoice", "invoice_date": "2026-09-10", "invoice_number": "INV-1", "taxable_amount": "1000", "vat_amount": "150", "currency": "USD"})
    loc = r.headers.get("Location", "")
    check(r.status_code == 302 and "tab=vat" in loc and "vat_period=2026-09" in loc, f"capturing a VAT document returns to the VAT tab ({loc})")
    check(VATInvoice.query.filter_by(engagement_id=T).count() == 1, "...and the document was stored")
    r, h = page(T, "vat", vat_period="2026-09")
    check("INV-1" in h and "Client VAT number" in h, "the month opens on the VAT tab")
    r = M.post(f"/tax/{T2}/vat/invoices/add", data={"kind": "output_invoice", "invoice_date": "2026-09-10", "invoice_number": "INV-2", "taxable_amount": "100", "vat_amount": "15", "currency": "USD"})
    check("tab=tax" in r.headers.get("Location", ""), "no VAT number: capturing still returns to the Tax tab")
    inv = VATInvoice.query.filter_by(engagement_id=T).first()
    r = M.post(f"/tax/vat/invoices/{inv.id}/delete")
    check("tab=vat" in r.headers.get("Location", "") and VATInvoice.query.filter_by(engagement_id=T).count() == 0, "deleting returns to the VAT tab")
    # other tax actions still return to the Tax tab
    r = M.post(f"/tax/{T}/info-requests/add", data={"request_text": "Bank statements", "description": "Bank statements"})
    check("tab=tax" in r.headers.get("Location", "") and "tab=vat" not in r.headers.get("Location", ""), "other Tax-tab actions still return to the Tax tab")

print("\n%d failure(s)" % len(fails))
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
