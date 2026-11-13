"""Journals get their reference numbers automatically: pushed ones (Asset Register, Deferred Tax) and hand-made ones share one AJE sequence per
trial balance; pushing again refreshes the same journal and keeps its number; journals made before numbering was automatic are numbered at start-up."""
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
from datetime import date
from app import create_app, _number_existing_journals
from extensions import db
from models import *
import asset_recon
import engagements as _eng
_eng._touch_adjustment = lambda adj: None      # the helper needs a signed-in user; these checks call _upsert directly
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

# ---- the helpers
check(journal_number("AJE 7") == 7 and journal_number("aje7") == 7 and journal_number("AJE-12") == 12, "AJE numbers are read in the usual spellings")
check(journal_number("PPE-REG") is None and journal_number("PY 1") is None and journal_number("") is None and journal_number(None) is None, "other references are not AJE numbers")
class _A: pass
a = _A(); a.source_key = None; a.reference = " ppe-reg "
check(adjustment_key(a) == "PPE-REG", "an old journal is recognised by its reference")
a.source_key = "dt-cur"; a.reference = "AJE 3"
check(adjustment_key(a) == "DT-CUR", "a numbered journal is recognised by its source code")

with app.app_context():
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Penanel", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    e2 = Engagement(client_id=cl.id, title="Audit 2024", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2024, 12, 31))
    db.session.add_all([e, e2]); db.session.commit()
    tb = TrialBalance(engagement_id=e.id); tb2 = TrialBalance(engagement_id=e2.id); db.session.add_all([tb, tb2]); db.session.commit()
    EID, TB, TB2, U = e.id, tb.id, tb2.id, u.id
    check(next_journal_reference(TB) == "AJE 1", "the first journal on a trial balance is AJE 1")

c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(U); s["_fresh"] = True
URL = f"/engagements/{EID}?tab=finalisation"

# ---- hand-made journals: the next number, also after a deletion in the middle
for i in range(3):
    r = c.post(f"/engagements/trial-balance/{TB}/adjustments/add", data={"description": f"manual {i}"}, headers={"Referer": URL})
with app.app_context():
    refs = [a.reference for a in AuditAdjustment.query.filter_by(trial_balance_id=TB).order_by(AuditAdjustment.id)]
    check(refs == ["AJE 1", "AJE 2", "AJE 3"], f"journals added by hand are numbered AJE 1, 2, 3 (got {refs})")
    db.session.delete(AuditAdjustment.query.filter_by(trial_balance_id=TB, reference="AJE 2").first()); db.session.commit()
r = c.post(f"/engagements/trial-balance/{TB}/adjustments/add", data={"description": "after delete"}, headers={"Referer": URL})
with app.app_context():
    refs = sorted(a.reference for a in AuditAdjustment.query.filter_by(trial_balance_id=TB))
    check(refs == ["AJE 1", "AJE 3", "AJE 4"], f"after deleting AJE 2 the next is AJE 4, not a repeat of 3 (got {refs})")
r = c.post(f"/engagements/trial-balance/{TB}/adjustments/add", data={"reference": "Client query 9", "description": "typed"}, headers={"Referer": URL})
with app.app_context():
    check(AuditAdjustment.query.filter_by(trial_balance_id=TB, reference="Client query 9").count() == 1, "a reference typed by hand is kept as typed")
    check(next_journal_reference(TB) == "AJE 5", "typed references do not disturb the AJE sequence")
    check(next_journal_reference(TB2) == "AJE 1", "each trial balance has its own sequence")

# ---- pushed journals share the sequence and keep their number
with app.app_context():
    tb = TrialBalance.query.get(TB)
    lines = [{"account_name": "Depreciation", "fs_category": "depreciation_amortisation", "debit": 100.0, "credit": 0.0},
             {"account_name": "Accumulated depreciation", "fs_category": "ppe", "debit": 0.0, "credit": 100.0}]
    st = asset_recon._upsert(tb, "PPE-REG", lines, "from the register", "current"); db.session.commit()
    a = [x for x in AuditAdjustment.query.filter_by(trial_balance_id=TB) if x.source_key == "PPE-REG"]
    check(st == "added" and len(a) == 1 and a[0].reference == "AJE 5", f"a pushed journal gets the next number (AJE 5), got {a[0].reference if a else None}")
    st = asset_recon._upsert(tb, "DT-CUR", lines, "deferred tax", "current"); db.session.commit()
    d = [x for x in AuditAdjustment.query.filter_by(trial_balance_id=TB) if x.source_key == "DT-CUR"]
    check(d and d[0].reference == "AJE 6", "the next push (deferred tax) takes AJE 6")
    st = asset_recon._upsert(tb, "PPE-REG", lines[:1] + [{"account_name": "Accumulated depreciation", "fs_category": "ppe", "debit": 0.0, "credit": 100.0}], "again", "current"); db.session.commit()
    a = [x for x in AuditAdjustment.query.filter_by(trial_balance_id=TB) if x.source_key == "PPE-REG"]
    check(st == "refreshed" and len(a) == 1 and a[0].reference == "AJE 5", "pushing again refreshes the same journal and it keeps AJE 5")
    check(asset_recon.journal_label(tb, "PPE-REG") == "AJE 5 (PPE-REG)", "the message names the journal as AJE 5 (PPE-REG)")
    st = asset_recon._upsert(tb, "PPE-REG", [], "none needed", "current"); db.session.commit()
    check(st == "removed" and next((x for x in AuditAdjustment.query.filter_by(trial_balance_id=TB) if x.source_key == "PPE-REG"), None) is None, "a journal that is no longer needed is removed")

# ---- journals made before numbering was automatic
with app.app_context():
    old = [("PPE-REG-PY", "prior"), ("PPE-REG", "current"), ("DT-PY", "prior"), ("DT-CUR", "current"), ("My own ref", "current"), ("", "current")]
    for ref, ap in old:
        db.session.add(AuditAdjustment(trial_balance_id=TB2, reference=ref, applies_to=ap))
    db.session.add(AuditAdjustment(trial_balance_id=TB2, reference="AJE 2"))
    db.session.commit()
    _number_existing_journals()
    rows = AuditAdjustment.query.filter_by(trial_balance_id=TB2).order_by(AuditAdjustment.id).all()
    got = [(r.source_key, r.reference) for r in rows]
    exp = [("PPE-REG-PY", "AJE 3"), ("PPE-REG", "AJE 4"), ("DT-PY", "AJE 5"), ("DT-CUR", "AJE 6"), (None, "My own ref"), (None, "AJE 7"), (None, "AJE 2")]
    check(got == exp, f"old journals are numbered in the order made, after the numbers in use; a hand-typed reference is kept (got {got})")
    _number_existing_journals()
    check([(r.source_key, r.reference) for r in AuditAdjustment.query.filter_by(trial_balance_id=TB2).order_by(AuditAdjustment.id)] == got, "running it again changes nothing")
    # an old PPE-REG journal that is pushed again is refreshed, not duplicated
    tb2 = TrialBalance.query.get(TB2)
    asset_recon._upsert(tb2, "PPE-REG", lines, "refresh", "current"); db.session.commit()
    check(AuditAdjustment.query.filter_by(trial_balance_id=TB2, source_key="PPE-REG").count() == 1 and AuditAdjustment.query.filter_by(trial_balance_id=TB2, reference="AJE 4").count() == 1, "pushing again after the renumbering refreshes the same journal")
    # a journal made by the previous version and never renumbered is renumbered when next pushed
    db.session.add(AuditAdjustment(trial_balance_id=TB, reference="DT-PY", applies_to="prior")); db.session.commit()
    tb = TrialBalance.query.get(TB)
    asset_recon._upsert(tb, "DT-PY", lines, "refresh", "prior"); db.session.commit()
    j = AuditAdjustment.query.filter_by(trial_balance_id=TB, source_key="DT-PY").all()
    check(len(j) == 1 and journal_number(j[0].reference) is not None, f"an old unnumbered journal is numbered when pushed again (got {j[0].reference if j else None})")

# ---- the page
h = c.get(URL).data.decode()
check("AJE 3" in h and "AJE 6" in h, "the Finalisation tab lists the numbered journals")
print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
