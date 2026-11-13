"""Section 16 rules read against the Income Tax Act filed in the library: starting rules brought into line with the Act's wording (only while
unedited, and no longer 'verified'), the associated-company fee limit shared by all fees on an engagement, the rules page showing the Act's own
words, and the setup page carrying the Act's figures."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ.pop("ANTHROPIC_API_KEY", None)
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
import prohibited_deductions as PD
import act_import as AI
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def close(a, b): return abs(a - b) < 0.005

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Tax FY2025", type="Audit", partner_id=partner.id, period_end=date(2025, 12, 31), acceptance_required=False)
    db.session.add(eng); db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, eng=eng.id)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P = login(ids["partner"]); E = ids["eng"]; B = f"/engagements/{E}/prohibited-deductions"

# --- rules page before the Act is filed
r = P.get("/prohibited-deductions/rules")
check(r.status_code == 200 and b"file it" in r.data and b"What the Act says" not in r.data, "rules page without the Act filed: invites filing, shows no wording")
check(r.status_code == 200 and b"Fees, administration and management paid to an associated company" in r.data, "a fresh database is seeded with the Act-based rule names")

# --- alignment of rules seeded with the earlier reading
with app.app_context():
    R = PD.rules_by_code()
    def setold(code, **kw):
        x = PD.ProhibitedDeductionRule.query.filter_by(code=code).first()
        for k, v in kw.items(): setattr(x, k, v)
        x.verified_at = datetime.utcnow(); x.verified_by_id = ids["partner"]
    setold("inter_trade_travel", treatment="travel_conditional", note="old")
    setold("imtt_expense", clause_label="16(1)(d)")
    setold("foreign_management_fee", treatment="mining_fee", name="Management or administration fee to a foreign parent")
    setold("pmv_lease", treatment="pmv_cap")
    setold("carbon_tax", treatment="none")        # a Partner's own decision: must not be overwritten
    setold("vat_expense", treatment="full")
    db.session.commit()
    n = PD.align_with_act()
    R = PD.rules_by_code()
    check(n == 5, "five unedited rules brought into line (got %d)" % n)
    check(R["inter_trade_travel"].treatment == "full" and "no business-necessity exception" in R["inter_trade_travel"].note, "travel between trades: now disallowed outright")
    check(R["imtt_expense"].clause_label == "16(1)(d1)", "IMTT: 16(1)(d1)")
    check(R["foreign_management_fee"].treatment == "assoc_fee" and R["foreign_management_fee"].name.startswith("Fees, administration and management"), "associated-company fee rule follows the Act")
    check(R["pmv_lease"].treatment == "pmv_lease", "PMV lease rule: the cumulative lease cap")
    check(R["vat_expense"].treatment == "review", "VAT stand-alone: not named in (d), flagged for a Partner")
    check(R["carbon_tax"].treatment == "none", "a rule the Partner had already changed is left alone")
    check(all(not R[c].is_verified for c in ("inter_trade_travel", "imtt_expense", "foreign_management_fee", "pmv_lease", "vat_expense")), "rules that changed are no longer verified")
    check(R["carbon_tax"].is_verified, "...but an untouched rule keeps its verification")
    check(PD.align_with_act() == 0, "running it again changes nothing")

# --- the Act is filed: the rules page shows its words
with app.app_context():
    AI.load_act("ita", user_id=ids["partner"])
r = P.get("/prohibited-deductions/rules")
check(r.status_code == 200 and b"What the Act says" in r.data and b"entertainment" in r.data and b"/tax/act/ita/16(1)(m)" in r.data, "rules page shows the Act's wording and links each clause")
check(b"Read section 16 in full" in r.data, "...with a link to the whole section")
r = P.get("/tax/act/ita/16(1)(r)")
check(r.status_code == 200 and b"associated enterprise" in r.data, "the (r) clause link opens the Act's paragraph")
r = P.get(B)
check(r.status_code == 200 and b"US$50,000" in r.data and b"three to one" in r.data and b"Fees to associated companies" in r.data and b"/tax/act/ita/16(1)(q)" in r.data, "setup shows the Act's figures and links")

# --- associated-company fees: the limit is shared by every such fee
def add(code, desc, amt, ref):
    return P.post(f"{B}/items/add", data={"expense_type": code, "description": desc, "amount": amt, "voucher_ref": ref, "charged_to_pl": "1"}, follow_redirects=True)
P.post(f"{B}/setup", data={"assoc_fee_a": "1000000", "assoc_fee_b": "100000", "assoc_fee_c": "0"})   # limit 1% of 900,000 = 9,000
add("foreign_management_fee", "Group management fee Q1", 6000, "F-1")
with app.app_context():
    x = ProhibitedDeductionItem.query.filter_by(voucher_ref="F-1").first()
    check(x.addback_amount == 0 and x.status == "ALLOWABLE", "first fee 6,000 is within the 9,000 limit")
add("foreign_management_fee", "Group management fee Q2", 6000, "F-2")
with app.app_context():
    a, b = (ProhibitedDeductionItem.query.filter_by(voucher_ref=v).first() for v in ("F-1", "F-2"))
    check(close(a.addback_amount, 1500) and close(b.addback_amount, 1500) and a.clause_label == "16(1)(r)", "second fee takes the total to 12,000: 3,000 refused, shared evenly (1,500 each)")
    check(close(a.addback_amount + b.addback_amount, 12000 - 9000), "the refused total is exactly the excess over the limit")
P.post(f"{B}/setup", data={"assoc_fee_a": "1000000", "assoc_fee_b": "100000", "assoc_fee_c": "0", "assoc_fee_pretrade": "1"})   # 0.75% = 6,750
with app.app_context():
    t = sum(ProhibitedDeductionItem.query.filter_by(voucher_ref=v).first().addback_amount for v in ("F-1", "F-2"))
    check(close(t, 12000 - 6750), "before trade starts: the limit is 0.75% (6,750), so 5,250 is refused")
with app.app_context():
    it = ProhibitedDeductionItem.query.filter_by(voucher_ref="F-2").first(); iid = it.id
P.post(f"/prohibited-deductions/items/{iid}/delete", follow_redirects=True)
with app.app_context():
    a = ProhibitedDeductionItem.query.filter_by(voucher_ref="F-1").first()
    check(a.addback_amount == 0, "deleting a fee re-tests the others (6,000 is within 6,750 again)")
P.post(f"{B}/setup", data={"assoc_fee_a": "", "assoc_fee_b": "", "assoc_fee_c": ""})
with app.app_context():
    a = ProhibitedDeductionItem.query.filter_by(voucher_ref="F-1").first()
    check(a.addback_amount == 0 and "Enter A" in (a.eval_note or ""), "with no A, B, C entered the fee is not guessed at")
r = P.get(B)
check(b"Enter A" in r.data or b"enter" in r.data.lower(), "the page tells the user to enter the figures")

# --- PMV lease: cumulative
P.post(f"{B}/setup", data={"pmv_cap": "50000"})
add("pmv_lease", "Lease of the MD's vehicle", 30000, "L-1")
with app.app_context():
    check(ProhibitedDeductionItem.query.filter_by(voucher_ref="L-1").first().addback_amount == 0, "lease 30,000, no earlier lease cost: within the 50,000 cap")
P.post(f"{B}/items/add", data={"expense_type": "pmv_lease", "description": "Lease of the MD's vehicle, year 2", "amount": "30000", "voucher_ref": "L-2", "vehicle_cost": "60000", "charged_to_pl": "1"}, follow_redirects=True)
with app.app_context():
    check(close(ProhibitedDeductionItem.query.filter_by(voucher_ref="L-2").first().addback_amount, 10000), "year 2: 60,000 to date against a 50,000 cap: 10,000 refused")

print("\nALL PASSED" if not fails else "\n%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
