"""Roll forward from last year's engagement: comparatives, opening position journal, prior-year cash flow, opening asset register."""
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
import datetime as dtm
from app import create_app
from extensions import db
from models import *
import financials as fin
import roll_forward as rf
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

LINES25 = [  # name, category, dr, cr, prior dr, prior cr
    ("Cash", "cash", 80000, 0, 50000, 0), ("Property", "ppe", 18000, 0, 10000, 0), ("Receivables", "trade_receivables", 12000, 0, 5000, 0),
    ("Admin expenses", "admin_expenses", 30000, 0, 20000, 0),
    ("Share capital", "share_capital", 0, 30000, 0, 30000), ("Borrowings", "long_term_borrowings", 0, 5000, 0, 5000),
    ("Payables", "trade_payables", 0, 15000, 0, 10000), ("Sales", "revenue", 0, 90000, 0, 40000),
]
LINES26 = [  # the client's own 2026 trial balance (no audit adjustments in it), prior column holds something stale
    ("Cash", "cash", 95000, 0, 1, 0), ("Property", "ppe", 23000, 0, 1, 0), ("Receivables", "trade_receivables", 14000, 0, 0, 0),
    ("Admin expenses", "admin_expenses", 40000, 0, 0, 0), ("New loan", "short_term_borrowings", 0, 10000, 0, 0),
    ("Share capital", "share_capital", 0, 30000, 0, 0), ("Payables", "trade_payables", 0, 12000, 0, 0),
    ("Retained earnings", "retained_earnings", 0, 20000, 0, 0), ("Sales", "revenue", 0, 100000, 0, 0),
]
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Broxmen Investment (Private) Limited", created_by_id=aid, industry="Mining"); db.session.add(cl); db.session.commit()
    other = Client(name="Other Co", created_by_id=aid, industry="Retail"); db.session.add(other); db.session.commit()
    def mk(client, title, y, lines):
        e = Engagement(client_id=client.id, title=title, type="Audit", partner_id=aid, manager_id=aid, period_end=dtm.date(y, 12, 31),
                       reporting_framework="ifrs_for_smes", acceptance_required=False)
        db.session.add(e); db.session.commit()
        tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.commit()
        for nm, cat, dr, cr, pdr, pcr in lines:
            db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=nm, fs_category=cat, current_debit=dr, current_credit=cr, prior_debit=pdr, prior_credit=pcr))
        db.session.commit()
        return e, tb
    e25, tb25 = mk(cl, "FY25", 2025, LINES25)
    e26, tb26 = mk(cl, "FY26", 2026, LINES26)
    e24, tb24 = mk(cl, "FY24", 2024, LINES25[:3])
    eo, tbo = mk(other, "Other FY25", 2025, LINES25)
    def adj(tb, ref, desc, lines, applies="current"):
        a = AuditAdjustment(trial_balance_id=tb.id, reference=ref, description=desc, applies_to=applies); db.session.add(a); db.session.commit()
        for n, c, d, cr in lines:
            db.session.add(AuditAdjustmentLine(adjustment_id=a.id, account_name=n, fs_category=c, debit=d, credit=cr))
        db.session.commit(); return a
    adj(tb25, "AJE 1", "deferred tax", [("Deferred tax charge", "income_tax_expense", 3000, 0), ("Deferred tax", "deferred_tax_liability", 0, 3000)])
    adj(tb25, "AJE 2", "revaluation", [("Property", "ppe", 5000, 0), ("Revaluation surplus", "oci_items", 0, 5000)])
    # last year's register: A held, B fully sold, C partly sold
    cls = PPEAssetClass(engagement_id=e25.id, name="Plant and machinery", depreciation_method="straight_line", rate_percent=20, order=0); db.session.add(cls); db.session.commit()
    db.session.add_all([
        PPEAsset(engagement_id=e25.id, asset_class_id=cls.id, asset_code="A1", description="Crusher", date_acquired=dtm.date(2022, 3, 1), cost=20000,
                 opening_accumulated_depreciation=4000, current_year_depreciation=2000, order=0),
        PPEAsset(engagement_id=e25.id, asset_class_id=cls.id, asset_code="B1", description="Old truck", date_acquired=dtm.date(2019, 3, 1), cost=3000,
                 opening_accumulated_depreciation=3000, disposal_cost=3000, disposal_accumulated_depreciation=3000, order=1),
        PPEAsset(engagement_id=e25.id, asset_class_id=cls.id, asset_code="C1", description="Pumps (lot)", date_acquired=dtm.date(2021, 1, 1), cost=10000,
                 opening_accumulated_depreciation=5000, current_year_depreciation=1000, disposal_cost=4000, disposal_accumulated_depreciation=2000, order=2)])
    db.session.commit()
    ids = dict(e25=e25.id, e26=e26.id, e24=e24.id, eo=eo.id, tb26=tb26.id)

with app.app_context():
    from flask_login import login_user
    with app.test_request_context():
        login_user(User.query.filter_by(role="admin").first())
        t = Engagement.query.get(ids["e26"])
        cands = rf.candidate_sources(t)
        check([c[0].id for c in cands] == [ids["e25"], ids["e24"]] and cands[0][1] and not cands[1][1], "sources: the same client's earlier years, the year before first; other clients and later years excluded")
        s25 = TrialBalance.query.filter_by(engagement_id=ids["e25"]).first()
        rows = rf.final_accounts(s25)
        d = sum(r["net"] for r in rows if r["net"] > 0); c = -sum(r["net"] for r in rows if r["net"] < 0)
        check(abs(d - 148000) < 0.01 and abs(c - 148000) < 0.01, "last year's final trial balance = figures + adjustments, balanced at 148,000 (%s / %s)" % (d, c))
        prop = next(r for r in rows if r["name"] == "Property")
        check(abs(prop["net"] - 23000) < 0.01 and prop["category"] == "ppe", "adjustment joins the account of the same name (Property 18,000 + 5,000)")
        check(any(r["name"] == "Deferred tax" and abs(r["net"] + 3000) < 0.01 for r in rows), "an adjustment account with no ledger line becomes an account of its own")
        carry, bal = rf.carry_in_lines(s25)
        by = {(l["account_name"], l["fs_category"]): l for l in carry}
        check(bal and len(carry) == 4, "opening journal balances, 4 lines: %s" % [(k, v["debit"], v["credit"]) for k, v in by.items()])
        check(abs(by[("Deferred tax", "deferred_tax_liability")]["credit"] - 3000) < 0.01 and abs(by[("Property", "ppe")]["debit"] - 5000) < 0.01, "balance sheet lines repeated as they stand")
        check(abs(by[(rf.RE_NAME, "retained_earnings")]["debit"] - 3000) < 0.01, "last year's tax charge goes to retained earnings brought forward (debit 3,000)")
        check(abs(by[(rf.RES_NAME, "other_reserves")]["credit"] - 5000) < 0.01, "last year's OCI goes to other reserves (credit 5,000)")

client = app.test_client()
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
with client.session_transaction() as sess:
    sess["_user_id"] = str(aid); sess["_fresh"] = True

r = client.get(f"/engagements/{ids['e26']}/roll-forward")
html = r.get_data(as_text=True)
check(r.status_code == 200 and "Comparative balances" in html and "Opening asset register" in html and "FY25" in html, "page opens with the four items and last year's engagement")
check("Crusher" in html and "Old truck" not in html and "Pumps" in html, "preview lists assets still held, not the fully sold one")
check(client.get(f"/engagements/{ids['eo']}/roll-forward").status_code in (200,), "an engagement of another client: page opens (own candidates only)")
r = client.get(f"/engagements/{ids['e26']}/roll-forward?source={ids['eo']}")
check(r.status_code == 200 and "Other FY25" not in r.get_data(as_text=True), "another client's engagement cannot be chosen as the source")

def apply(**kw):
    data = {"source": ids["e25"], "confirm": "1"}
    for k in ("comparatives", "opening", "cashflow", "assets"):
        if kw.get(k, True): data[k] = "1"
    if "confirm" in kw: data["confirm"] = kw["confirm"]
    return client.post(f"/engagements/{ids['e26']}/roll-forward/apply", data=data)

def state():
    tb = TrialBalance.query.get(ids["tb26"])
    return tb, {l.account_name: l for l in tb.lines}

r = apply(confirm="")
with app.app_context():
    tb, L = state()
    check(L["Cash"].prior_debit == 1 and not tb.adjustments and PPEAsset.query.filter_by(engagement_id=ids["e26"]).count() == 0, "without the confirmation tick nothing is written")

r = apply()
check(r.status_code == 302, "roll forward applies (%s)" % r.status_code)
with app.app_context():
    tb, L = state()
    check(abs(L["Cash"].prior_debit - 80000) < 0.01 and L["Cash"].prior_credit == 0, "Cash comparative = 80,000")
    check(abs(L["Property"].prior_debit - 23000) < 0.01, "Property comparative includes last year's revaluation (23,000)")
    check("Deferred tax" in L and abs(L["Deferred tax"].prior_credit - 3000) < 0.01 and L["Deferred tax"].current_debit == 0 and L["Deferred tax"].fs_category == "deferred_tax_liability",
          "account only in last year's audit adjustments is added with nil for this year and keeps its category")
    check(abs(L["Sales"].prior_credit - 90000) < 0.01 and abs(L["Sales"].current_credit - 100000) < 0.01, "this year's own figures are untouched")
    check(L["New loan"].prior_debit == 0 and L["New loan"].prior_credit == 0, "account only in this year gets a nil comparative")
    check(abs(sum(l.prior_debit or 0 for l in tb.lines) - sum(l.prior_credit or 0 for l in tb.lines)) < 0.01, "the prior column balances")
    rolls = [a for a in tb.adjustments if rf.adjustment_is_roll(a)]
    check(len(rolls) == 1 and rolls[0].applies_to == "current" and len(rolls[0].lines) == 4, "one ROLL-FWD journal with 4 lines")
    check(abs(sum(l.debit for l in rolls[0].lines) - sum(l.credit for l in rolls[0].lines)) < 0.01, "ROLL-FWD journal balances")
    # statements: the 2026 closing deferred tax and retained earnings now include last year's adjustments
    st = fin.build_all_statements(tb.lines, tb.adjustments, reporting_framework="ifrs_for_smes", cash_flow_method="indirect")
    sfp = {r_["label"]: r_ for r_ in st["sfp"]["rows"]} if isinstance(st["sfp"], dict) and "rows" in st["sfp"] else {}
    check(abs(st["totals"]["deferred_tax_liability"]["current"] - 3000) < 0.01 and abs(st["totals"]["deferred_tax_liability"]["prior"] - 3000) < 0.01,
          "deferred tax liability: 3,000 closing this year (brought forward) and 3,000 comparative")
    check(abs(st["totals"]["ppe"]["current"] - 28000) < 0.01 and abs(st["totals"]["ppe"]["prior"] - 23000) < 0.01, "property: 23,000 + 5,000 revaluation carried (28,000) vs 23,000 comparative (%s)" % st["totals"]["ppe"])
    cf = CashFlowPriorLine.query.filter_by(engagement_id=ids["e26"]).all()
    check(any(l.section == "cash_open" and abs(l.amount - 50000) < 0.01 for l in cf), "prior-year cash flow: cash at the beginning of last year 50,000")
    check(not any(l.label == "Profit before tax" for l in cf) and len(cf) >= 5, "prior-year cash flow lines saved, profit before tax left to the income statement (%d lines)" % len(cf))
    assets = PPEAsset.query.filter_by(engagement_id=ids["e26"]).order_by(PPEAsset.order).all()
    check([a.asset_code for a in assets] == ["A1", "C1"], "opening register: the held and partly held assets, not the fully sold one (%s)" % [a.asset_code for a in assets])
    a1, c1 = assets
    check(abs(a1.cost - 20000) < 0.01 and abs(a1.opening_accumulated_depreciation - 6000) < 0.01 and a1.current_year_depreciation == 0, "Crusher opens at cost 20,000, accumulated depreciation 6,000")
    check(abs(c1.cost - 6000) < 0.01 and abs(c1.opening_accumulated_depreciation - 4000) < 0.01, "Pumps open at the part still held: cost 6,000, accumulated depreciation 4,000")
    check(a1.asset_class_id is not None and PPEAssetClass.query.get(a1.asset_class_id).name == "Plant and machinery" and PPEAssetClass.query.get(a1.asset_class_id).engagement_id == ids["e26"], "class copied into this year's register")

# twice: nothing doubles
apply()
with app.app_context():
    tb, L = state()
    check(len([a for a in tb.adjustments if rf.adjustment_is_roll(a)]) == 1, "second roll forward refreshes the journal, never stacks a second")
    check(PPEAsset.query.filter_by(engagement_id=ids["e26"]).count() == 2, "assets already in the register are skipped")
    check(CashFlowPriorLine.query.filter_by(engagement_id=ids["e26"], section="cash_open").count() == 1, "comparative cash flow replaced, not doubled")
    check(abs(L["Cash"].prior_debit - 80000) < 0.01 and len([l for l in tb.lines if l.account_name == "Deferred tax"]) == 1, "comparatives merged again without duplicating accounts")

# untick the opening journal: it is removed
apply(opening=False, cashflow=False, assets=False)
with app.app_context():
    tb, L = state()
    check(not [a for a in tb.adjustments if rf.adjustment_is_roll(a)], "opening position unticked: the ROLL-FWD journal is removed")

# a source whose final trial balance does not balance does not touch the comparatives
with app.app_context():
    s = TrialBalance.query.filter_by(engagement_id=ids["e25"]).first()
    ln = next(l for l in s.lines if l.account_name == "Cash"); ln.current_debit = 81000; db.session.commit()
r = apply(opening=False, cashflow=False, assets=False)
with app.app_context():
    tb, L = state()
    check(abs(L["Cash"].prior_debit - 80000) < 0.01, "last year not balanced: comparatives left as they were")

print("\n%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
