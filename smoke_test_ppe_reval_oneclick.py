"""Revaluation, the one-click route: (1) carry-over of the old register's value into the fair-value import, (2) the "Post to Movement Schedule and
Financial Statements" button, (3) the same button when the assets were first entered at their closing values (so the revaluation has to start from
the trial balance). The asset list is generated here, so the test needs no outside file. Replaces smoke_test_ppe_reval_carryover / _quick / _quick2."""
import os, sys, tempfile, io, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
from datetime import date
import openpyxl
from app import create_app
from extensions import db
from models import *
import financials as fin
import ppe_revaluation as pr
import engagements as E
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

VALUES = [45000, 30000, 25000, 10000, 10000, 5000]            # fair values, total 125 000
OLD_COST = 100000.0                                              # what the old register carried
FV_TOTAL = float(sum(VALUES))

def workbook():
    wb = openpyxl.Workbook(); ws = wb.active
    ws.cell(1, 4, "Machinery & Equipment Register")
    for i, h in enumerate(["Department", "Item No.", "Category", "Make/Model", "Serial Number", "Year Of Man", "Value US$"], 1):
        ws.cell(3, i, h)
    for n, v in enumerate(VALUES, 1):
        for i, x in enumerate(["PLANT", n, "Machinery", f"Machine {n}", f"SN-{n:04d}", None, v], 1):
            ws.cell(3 + n, i, x)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()
XL = workbook()

with app.app_context():
    db.create_all()
    u = User(username="p", name="P", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="Oakbrook", created_by_id=u.id); db.session.add(cl); db.session.commit()
    UID, CID = u.id, cl.id

def make_engagement(title):
    with app.app_context():
        e = Engagement(client_id=CID, title=title, type="Audit", partner_id=UID, manager_id=UID, acceptance_required=False,
                       period_end=date(2025, 12, 31), reporting_framework="ifrs_for_smes")
        db.session.add(e); db.session.commit(); eid = e.id
        tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.flush()
        def add(n, c, cd=0, cc=0):
            db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=n, fs_category=c, current_debit=cd, current_credit=cc, prior_debit=cd, prior_credit=cc))
        add("Property plant and equipment", "ppe", cd=OLD_COST); add("Bank", "cash", cd=500000)
        add("Share capital", "share_capital", cc=100000); add("Retained earnings", "retained_earnings", cc=OLD_COST + 500000 - 100000)
        db.session.add(PPEAssetClass(engagement_id=eid, name="Property plant and equipment", order=1)); db.session.commit()
        cls = PPEAssetClass.query.filter_by(engagement_id=eid).first()
        db.session.add(PPEAsset(engagement_id=eid, asset_class_id=cls.id, description="Property plant and equipment", cost=OLD_COST)); db.session.commit()
        return eid

def client_for():
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(UID); s["_fresh"] = True
    return c

def upload(c, eid):
    r = c.post(f"/engagements/{eid}/revaluation/import/upload", data={"file": (io.BytesIO(XL), "v.xlsx")}, content_type="multipart/form-data")
    return re.search(r"/import/([0-9a-f]{32})/confirm", r.data.decode()).group(1), r.data.decode()

def result(eid):
    with app.app_context():
        tbr = TrialBalance.query.filter_by(engagement_id=eid).first()
        _, _, cd, ad = E._ppe_plain_data(eid)
        mv, pol = E._ppe_movement_and_policy(cd, ad, date(2025, 12, 31))
        st = fin.build_all_statements(tbr.lines, tbr.adjustments, reporting_framework="ifrs_for_smes", ppe_movement=mv, ppe_policy_text=pol, whole_numbers=True)
        note = next(n for n in st["notes"] if n["title"] == "Property, plant and equipment")
        return dict(keys=[a.source_key for a in tbr.adjustments], balanced=all(a.is_balanced for a in tbr.adjustments), ppe=st["totals"]["ppe"]["current"],
                    ta=st["sfp"]["total_assets"]["current"], tel=st["sfp"]["total_equity_and_liabilities"]["current"], ties=note["reconciliation"]["ties_out"],
                    reserves=st["totals"]["other_reserves"]["current"])

# ---- 1. carry-over: the fair-value import takes the old register's value as the carrying amount, then Apply
eid = make_engagement("Carry-over"); c = client_for()
tok, _ = upload(c, eid)
c.post(f"/engagements/{eid}/revaluation/import/{tok}/confirm", data={"ext": ".xlsx", "filename": "v", "create_missing": "1", "use": "fair_value", "carry_over": "1"}, follow_redirects=True)
with app.app_context():
    rec = pr.get(eid); rec.valuer_name = "V"; rec.report_reference = "R1"; rec.policy_confirmed = True; rec.basis = "Market value (open market)"; db.session.commit()
    calc = pr.calculate(Engagement.query.get(eid), rec)
    check(abs(calc["totals"]["fair_value"] - FV_TOTAL) < 0.5, f"carry-over: the fair values add up to {FV_TOTAL:,.0f}")
    check(abs(calc["totals"]["nbv"] - OLD_COST) < 0.5, "carry-over: the carrying amount is the old register's value")
c.post(f"/engagements/{eid}/revaluation/apply", data={}, follow_redirects=True)
r1 = result(eid)
check(r1["keys"] == ["REVAL"] and r1["balanced"], "carry-over: one balanced revaluation journal is posted")
check(abs(r1["ppe"] - FV_TOTAL) < 0.5, "carry-over: property, plant and equipment in the statements equals the fair value")
check(r1["ties"] and abs(r1["ta"] - r1["tel"]) < 0.5, "carry-over: the note ties out and the statement of financial position balances")

# ---- 2. the one-click button
eid = make_engagement("Quick post"); c = client_for()
tok, _ = upload(c, eid)
c.post(f"/engagements/{eid}/revaluation/import/{tok}/confirm", data={"ext": ".xlsx", "filename": "v", "create_missing": "1", "use": "fair_value"}, follow_redirects=True)
h0 = c.get(f"/engagements/{eid}?tab=asset_register").data.decode()
check("Post to Movement Schedule and Financial Statements" in h0 and 'name="quick"' in h0, "quick post: the one-click button is on the Asset Register")
c.post(f"/engagements/{eid}/revaluation/apply", data={"quick": "1"}, follow_redirects=True)
r2 = result(eid)
check(r2["keys"] == ["REVAL"] and r2["balanced"] and abs(r2["ppe"] - FV_TOTAL) < 0.5, "quick post: posts the journal and the statements show the fair value")
check(r2["ties"] and abs(r2["ta"] - r2["tel"]) < 0.5, "quick post: the note ties out and the balance sheet balances")
check(abs(r2["reserves"]) > 0, "quick post: a revaluation reserve now appears in equity")

# ---- 3. assets first entered at closing values, then the fair-value list imported (what happened on the live Oakbrook file)
eid = make_engagement("Closing then fair value"); c = client_for()
tok, _ = upload(c, eid)
c.post(f"/engagements/{eid}/revaluation/import/{tok}/confirm", data={"ext": ".xlsx", "filename": "v", "create_missing": "1", "use": "closing", "remove_others": "1"}, follow_redirects=True)
tok, page = upload(c, eid)
form = {"ext": ".xlsx", "filename": "v", "create_missing": "1", "use": "fair_value"}
for m in re.finditer(r'<select name="(asset_\d+)".*?</select>', page, re.S):
    sel = re.search(r'<option value="(\d+)"\s+selected', m.group(0))
    if sel: form[m.group(1)] = sel.group(1)
c.post(f"/engagements/{eid}/revaluation/import/{tok}/confirm", data=form, follow_redirects=True)
with app.app_context():
    check(PPEAsset.query.filter_by(engagement_id=eid).count() == len(VALUES), "closing then fair value: the second import matches the existing assets (no duplicates)")
c.post(f"/engagements/{eid}/revaluation/apply", data={"quick": "1"}, follow_redirects=True)
r3 = result(eid)
check(r3["keys"] == ["REVAL"] and r3["balanced"] and abs(r3["ppe"] - FV_TOTAL) < 0.5, "closing then fair value: one-click post carries the old value from the trial balance and reaches the statements")
check(r3["ties"] and abs(r3["ta"] - r3["tel"]) < 0.5, "closing then fair value: the note ties out and the balance sheet balances")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
