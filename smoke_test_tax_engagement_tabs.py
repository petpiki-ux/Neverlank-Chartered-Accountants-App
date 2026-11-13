"""Tax Advisory engagements: the lean tab set (no Analytical Review, Risk Assessment, Trial Balance or Substantive Procedures), the tax-head Execution
Plan on the Tax tab, Capital Allowances worked with the Firm Finance engine, the Income Tax Computation without a trial balance, the PPE tax base
for deferred tax, and Provisional Tax (QPD) fed from the computation. Other engagement types keep their tabs."""
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
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.006): return a is not None and abs(a - b) <= tol

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

with app.app_context():
    mp = User(username="mp", name="Managing Partner", role="admin"); mp.set_password("x")
    ptn = User(username="ptn", name="Tax Partner", role="partner"); ptn.set_password("x")
    sup = User(username="sup", name="Supervisor", role="supervisor"); sup.set_password("x")
    db.session.add_all([mp, ptn, sup]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=mp.id); db.session.add(cl); db.session.commit()
    tax = Engagement(client_id=cl.id, title="Tax 2026", type=TAX_ADVISORY_TYPE, partner_id=ptn.id, manager_id=sup.id, period_end=date(2026, 12, 31), acceptance_required=False)
    aud = Engagement(client_id=cl.id, title="Audit 2026", type="Audit", partner_id=ptn.id, period_end=date(2026, 12, 31), acceptance_required=False)
    db.session.add_all([tax, aud]); db.session.commit()
    T, A = tax.id, aud.id
    M = login(mp.id); P = login(ptn.id); S = login(sup.id)
    def page(c, eid, tab, **kw):
        r = c.get(f"/engagements/{eid}", query_string=dict(tab=tab, **kw), follow_redirects=True)
        return r, r.get_data(as_text=True)
    def link(h, tab): return f"tab={tab}\"" in h

    # ---- the tab bar
    r, h = page(M, T, "overview")
    check(r.status_code == 200, "a Tax Advisory engagement opens")
    for t in ("analytical", "risks", "entity", "planning", "substantive"):
        check(not link(h, t), f"Tax Advisory: no {t} tab")
    for t in ("overview", "acceptance", "trial_balance", "asset_register", "accounting", "checklist", "documents", "tasks", "income_tax", "deferred_tax", "capital_allowances", "qpd", "tax", "finalisation"):
        check(link(h, t), f"Tax Advisory: the {t} tab is there")
    r, h = page(M, A, "overview")
    for t in ("analytical", "risks", "trial_balance", "substantive", "income_tax", "deferred_tax"):
        check(link(h, t), f"Audit: the {t} tab is kept")
    for t in ("capital_allowances", "asset_register"):
        check(link(h, t), f"Audit: the {t} tab is there (asset schedules)")
    for t in ("qpd", "accounting"):
        check(not link(h, t), f"Audit: no {t} tab")
    for t in ("entity", "planning"):
        check(link(h, t), f"Audit: the {t} tab is kept")
    r, h = page(M, A, "capital_allowances")
    check(r.status_code == 200 and "Allowance schedule" in h, "a Capital Allowances link on an Audit engagement opens the schedule")
    # old links on the Tax Advisory engagement
    r, h = page(M, T, "substantive")
    import re as _re
    page_body = _re.sub(r"<nav>.*?</nav>", "", h, flags=_re.S)   # the top menu has a Valuations link; the check is about the engagement page
    check(r.status_code == 200 and "Execution Plan" in h and "Value Added Tax (VAT)" in h and "Valuation" not in page_body, "an old Substantive Procedures link lands on the Tax tab's Execution Plan")
    for t in ("risks", "analytical", "entity", "planning"):
        r, h = page(M, T, t)
        check(r.status_code == 200 and "Risk Assessment (" not in h and "Execution Plan" not in h, f"an old {t} link lands on the overview")

    # ---- Execution Plan on the Tax tab
    r = M.post(f"/engagements/{T}/substantive-procedures/areas/Value Added Tax (VAT)/items/add", data={"procedure_text": "Reconcile output VAT to the returns"})
    check(r.status_code == 302 and "tab=substantive" in r.headers["Location"], "adding an Execution Plan procedure works")
    r, h = page(M, T, "substantive")
    check("Reconcile output VAT to the returns" in h, "...and it shows on the Tax tab")
    item = SubstantiveProcedureItem.query.filter_by(procedure_text="Reconcile output VAT to the returns").first()
    r = M.post(f"/engagements/substantive-procedures/items/{item.id}/update", data={"procedure_text": item.procedure_text, "status": "Completed", "notes": "agreed"})
    db.session.expire_all()
    check(db.session.get(SubstantiveProcedureItem, item.id).status == "Completed", "...its status can be changed")
    n0 = SubstantiveProcedureArea.query.filter_by(engagement_id=T).count()
    r = M.post(f"/engagements/{T}/substantive-procedures/generate")
    check(SubstantiveProcedureArea.query.filter_by(engagement_id=T).count() == n0 and SubstantiveProcedureArea.query.filter_by(engagement_id=T, area="Revenue").count() == 0,
          "'Generate suggested procedures' no longer drops the audit areas into a Tax Advisory engagement")

    # ---- Income Tax Computation without a trial balance
    r, h = page(M, T, "income_tax")
    check("Start the computation" in h and "Trial Balance</a> tab first" not in h, "no trial balance: the Income Tax tab asks for profit before tax")
    r = M.post(f"/engagements/{T}/income-tax/save", data={"profit_before_tax_manual": "100000", "tax_rate_percent": "24", "aids_levy_percent": "3", "tax_loss_brought_forward": "0", "notes": ""})
    r, h = page(M, T, "income_tax")
    check("100,000.00" in h and "Start the computation" not in h, "...the figure entered starts the computation")
    comp = IncomeTaxComputation.query.filter_by(engagement_id=T).first()
    check(comp.profit_before_tax_manual == 100000.0 and comp.tax_rate_percent == 24.0, "...and is stored with the rate")
    # an Audit engagement still insists on a trial balance
    r, h = page(M, A, "income_tax")
    check("Trial Balance</a> tab first" in h, "Audit: the Income Tax tab still needs the trial balance")

    # ---- Capital Allowances
    r, h = page(M, T, "capital_allowances")
    check(r.status_code == 200 and "Asset classes and their allowance rules" in h, "the Capital Allowances tab opens")
    for nm in ("Computers", "Plant and machinery", "Land"):
        M.post(f"/engagements/{T}/ppe/asset-classes/add", data={"name": nm})
    cls = {c.name: c.id for c in PPEAssetClass.query.filter_by(engagement_id=T).all()}
    def add(code, desc, klass, acq, cost, dep=0):
        return M.post(f"/engagements/{T}/ppe/assets/add", data={"asset_code": code, "description": desc, "asset_class_id": cls[klass] if klass else "", "date_acquired": acq, "cost": cost, "current_year_depreciation": dep,
                                                         "opening_accumulated_depreciation": "7"})
    r = add("A1", "Laptop", "Computers", "2026-03-01", 1000, 300)
    check(r.status_code == 302 and "tab=asset_register" in r.headers["Location"], "adding an asset returns to the Asset Register tab")
    add("A2", "Old PC", "Computers", "2025-06-30", 800, 150)
    add("A3", "Machine", "Plant and machinery", "2026-02-01", 10000)
    add("A4", "Plot", "Land", "2020-01-01", 50000)
    add("A5", "Old printer", "Computers", "2024-01-10", 600)
    add("A6", "Old van", "Computers", "2025-01-01", 400)
    add("A7", "Undated thing", "Computers", "", 123)
    add("A8", "No class", None, "2026-01-01", 321)
    ids = {a.asset_code: a.id for a in PPEAsset.query.filter_by(engagement_id=T).all()}
    M.post(f"/engagements/{T}/capital-allowances/assets/{ids['A5']}/disposal", data={"tax_disposed_on": "2026-06-30", "tax_disposal_proceeds": "100"})
    M.post(f"/engagements/{T}/capital-allowances/assets/{ids['A6']}/disposal", data={"tax_disposed_on": "2026-03-01", "tax_disposal_proceeds": "380"})
    page(M, T, "capital_allowances")   # opening the tab creates a starter rule for each class
    rules = {r.asset_class.name: r for r in EngagementTaxAssetRule.query.filter_by(engagement_id=T).all()}
    check(rules["Computers"].method == "WT_SL" and rules["Computers"].wt_rate_percent == 25 and rules["Plant and machinery"].method == "SIA" and rules["Land"].method == "NONE",
          "starter rules by class name (computers 25% wear and tear, plant SIA, land none)")
    check(not any(r.verified for r in rules.values()), "...all starting not verified")
    import engagement_tax
    ctx = engagement_tax.capital_allowance_context(db.session.get(Engagement, T))
    res = ctx["ca_result"]; tot = res["total"]
    rows = {r["asset_code"]: r for r in res["rows"]}
    check(near(rows["A1"]["allowance"], 250) and near(rows["A1"]["closing"], 750), "laptop: 25% of cost in its first year, tax value 750")
    check(near(rows["A2"]["opening"], 600) and near(rows["A2"]["allowance"], 200) and near(rows["A2"]["closing"], 400), "old PC: second year 200 on an opening tax value of 600")
    check(near(rows["A3"]["allowance"], 2500) and near(rows["A3"]["closing"], 7500), "machine: special initial allowance 25% of 10,000")
    check(near(rows["A4"]["allowance"], 0) and near(rows["A4"]["closing"], 50000), "land: no allowance, tax value stays at cost")
    check(near(rows["A5"]["scrapping"], 200) and near(rows["A5"]["recoupment"], 0) and near(rows["A5"]["closing"], 0), "printer sold below its tax value (300): scrapping allowance 200")
    check(near(rows["A6"]["recoupment"], 80) and near(rows["A6"]["scrapping"], 0), "van sold above its tax value (300) for 380: recoupment 80")
    check("A7" not in rows and "A8" in rows and near(rows["A8"]["allowance"], 0), "an undated asset is left out; an unclassified one gets no allowance")
    check(near(tot["allowance"], 2950) and near(tot["recoupment"], 80) and near(tot["scrapping"], 200) and near(tot["closing"], 58650 + 321), "schedule totals (allowance 2,950; recoupment 80; scrapping 200)")
    check(len(ctx["ca_undated"]) == 1 and len(ctx["ca_unverified"]) >= 2, "warnings: one undated asset, unverified rules")
    check(near(ctx["ca_depreciation"], 450), "depreciation charged in the accounts is 450")
    r, h = page(M, T, "capital_allowances")
    check("Undated thing" in h and "not verified" in h.lower() and "2,950.00" in h and "Old printer" in h, "the tab shows the schedule and the warnings")

    # ---- rule verification
    cid = cls["Computers"]
    r = M.post(f"/engagements/{T}/capital-allowances/rules/{cid}", data={"method": "WT_SL", "wt_rate_percent": "25", "sia_percent": "25", "sia_years": "4", "verified": "on"})
    db.session.expire_all()
    check(EngagementTaxAssetRule.query.filter_by(engagement_id=T, asset_class_id=cid).first().verified, "an Admin can verify a rule")
    S.post(f"/engagements/{T}/capital-allowances/rules/{cid}", data={"method": "WT_SL", "wt_rate_percent": "20", "sia_percent": "25", "sia_years": "4", "verified": "on"})
    db.session.expire_all()
    rr = EngagementTaxAssetRule.query.filter_by(engagement_id=T, asset_class_id=cid).first()
    check(rr.wt_rate_percent == 20 and not rr.verified, "a Supervisor changing a rule clears the tick and cannot set it")
    M.post(f"/engagements/{T}/capital-allowances/rules/{cid}", data={"method": "WT_SL", "wt_rate_percent": "25", "sia_percent": "25", "sia_years": "4", "verified": "on"})
    P.post(f"/engagements/{T}/capital-allowances/rules/{cid}", data={"method": "WT_SL", "wt_rate_percent": "25", "sia_percent": "25", "sia_years": "4", "verified": "on"})
    db.session.expire_all()
    check(EngagementTaxAssetRule.query.filter_by(engagement_id=T, asset_class_id=cid).first().verified, "a Partner can verify a rule")

    # ---- post to the Income Tax Computation
    M.post(f"/engagements/{T}/income-tax/lines/add", data={"item_type": "addback", "description": "Fines", "amount": "10"})
    r = M.post(f"/engagements/{T}/capital-allowances/post-to-income-tax")
    check(r.status_code == 302 and "tab=income_tax" in r.headers["Location"], "posting goes to the Income Tax tab")
    db.session.expire_all()
    comp = IncomeTaxComputation.query.filter_by(engagement_id=T).first()
    eng_lines = {l.description: l for l in comp.items if l.source == "cap_allow_engine"}
    check(len(eng_lines) == 4, "four lines posted (depreciation, recoupment, allowances, scrapping)")
    check(near(sum(l.amount for l in eng_lines.values() if l.item_type == "capital_allowance"), 3150) and near(sum(l.amount for l in eng_lines.values() if l.item_type == "addback"), 530),
          "allowance and scrapping as deductions (3,150); depreciation and recoupment as add-backs (530)")
    check(any(l.description == "Fines" and l.source is None for l in comp.items), "the preparer's own lines are untouched")
    M.post(f"/engagements/{T}/capital-allowances/post-to-income-tax")
    db.session.expire_all()
    comp = IncomeTaxComputation.query.filter_by(engagement_id=T).first()
    check(len([l for l in comp.items if l.source == "cap_allow_engine"]) == 4 and len(comp.items) == 5, "posting again replaces, never duplicates")
    res = engagement_tax.income_tax_result_for(db.session.get(Engagement, T))
    # 100,000 + 10 fines + 450 + 80 - 2,950 - 200 = 97,390 ; x 24% = 23,373.60 ; levy 3% = 701.208
    check(near(res["taxable_income"], 97390) and near(res["total_tax_charge"], 97390 * 0.24 * 1.03, 0.01), "taxable income 97,390 and tax 24,074.81 at 24% + 3% levy")
    r, h = page(M, T, "income_tax")
    check("Posted from the Capital Allowances schedule" in h, "the Income Tax tab marks the posted lines")
    # a change to the schedule is flagged
    a = db.session.get(PPEAsset, ids["A1"])
    M.post(f"/engagements/ppe/assets/{a.id}/update", data={"asset_code": "A1", "description": "Laptop", "asset_class_id": cid, "date_acquired": "2026-03-01", "cost": "2000", "current_year_depreciation": "300",
                                               "opening_accumulated_depreciation": "7", "disposal_cost": "0", "disposal_accumulated_depreciation": "0"})
    r, h = page(M, T, "capital_allowances")
    check("post again" in h, "after the register changes the tab says to post again")
    a = db.session.get(PPEAsset, ids["A1"])
    check(a.opening_accumulated_depreciation == 7.0, "the Capital Allowances form keeps the register's other columns")

    # ---- deferred tax base
    ctx = engagement_tax.capital_allowance_context(db.session.get(Engagement, T))
    r = M.post(f"/engagements/{T}/capital-allowances/use-tax-base")
    db.session.expire_all()
    dt = DeferredTaxComputation.query.filter_by(engagement_id=T).first()
    check(r.status_code == 302 and dt and near(dt.ppe_tax_base, round(ctx["ca_result"]["tax_value_end"], 2)), "the tax value becomes the PPE tax base of the Deferred Tax Computation")
    r, h = page(M, T, "deferred_tax")
    check("Pick the tax base up automatically" in h and "Capital Allowance schedule" in h, "the Deferred Tax tab picks the tax base up from the schedule")

    # ---- Provisional tax
    r, h = page(M, T, "qpd")
    check(r.status_code == 200 and "Provisional Tax (QPD)" in h, "the QPD tab opens")
    if QPDInstalmentRate.query.count() == 0:
        for i, (lab, m, d, pct) in enumerate([("1st", 3, 25, 10), ("2nd", 6, 25, 35), ("3rd", 9, 25, 65), ("4th", 12, 20, 100)]):
            db.session.add(QPDInstalmentRate(label=lab, due_month=m, due_day=d, cumulative_pct=pct, order=i))
        db.session.commit()
    nrates = QPDInstalmentRate.query.count()
    r = M.post(f"/engagements/{T}/qpd/use-computation", data={"tax_year": "2026"})
    check(r.status_code == 302 and "tab=qpd" in r.headers["Location"], "using the computation returns to the QPD tab")
    db.session.expire_all()
    est = QPDEstimate.query.filter_by(client_id=cl.id, tax_year=2026).first()
    res = engagement_tax.income_tax_result_for(db.session.get(Engagement, T))
    check(est and near(est.estimated_annual_tax_charge, round(res["total_tax_charge"], 2)) and near(est.estimated_annual_taxable_income, round(res["taxable_income"], 2)) and est.tax_rate_pct == 24.0,
          "the QPD estimate takes the computation's taxable income, rate, levy and tax")
    check(len(est.instalments) == nrates and near(sum(i.computed_amount for i in est.instalments), est.estimated_annual_tax_charge), "the instalments are computed and add up to the annual tax")
    first = sorted(est.instalments, key=lambda i: i.order)[0]
    first.paid_amount, first.paid_date = first.computed_amount, date(2026, 3, 25); db.session.commit()
    M.post(f"/engagements/{T}/income-tax/save", data={"profit_before_tax_manual": "120000", "tax_rate_percent": "24", "aids_levy_percent": "3", "tax_loss_brought_forward": "0"})
    r, h = page(M, T, "qpd")
    check("differs from this engagement" in h, "when the computation moves on, the tab says the estimate is out of step")
    M.post(f"/engagements/{T}/qpd/use-computation", data={"tax_year": "2026"})
    db.session.expire_all()
    est = QPDEstimate.query.filter_by(client_id=cl.id, tax_year=2026).first()
    first = sorted(est.instalments, key=lambda i: i.order)[0]
    check(near(first.paid_amount, first.computed_amount) and first.paid_date == date(2026, 3, 25) and len(est.instalments) == nrates, "an instalment already paid is left alone when the instalments are recomputed")
    r, h = page(M, T, "qpd")
    check("differs from this engagement" not in h and "Not paid" in h, "...and the estimate is back in line")
    # no rate -> refused, never assumed
    comp = IncomeTaxComputation.query.filter_by(engagement_id=T).first(); rate = comp.tax_rate_percent; comp.tax_rate_percent = None; db.session.commit()
    n = len(QPDEstimate.query.all())
    r = M.post(f"/engagements/{T}/qpd/use-computation", data={"tax_year": "2027"})
    check(QPDEstimate.query.filter_by(tax_year=2027).count() == 0, "with no tax rate entered nothing is estimated")
    comp.tax_rate_percent = rate; db.session.commit()
    # permission: a Supervisor without manage_qpd
    db.session.add(UserPermissionOverride(user_id=sup.id, permission_key="manage_qpd", allowed=False)); db.session.commit()
    r = S.post(f"/engagements/{T}/qpd/use-computation", data={"tax_year": "2028"})
    check(r.status_code == 403 and QPDEstimate.query.filter_by(tax_year=2028).count() == 0, "without manage_qpd the QPD estimate cannot be changed from here")

    # ---- confidentiality: someone not on the engagement
    out = User(username="out", name="Outsider", role="staff"); out.set_password("x"); db.session.add(out); db.session.commit()
    O = login(out.id)
    for url, data in [(f"/engagements/{T}/capital-allowances/post-to-income-tax", {}), (f"/engagements/{T}/capital-allowances/use-tax-base", {}),
                      (f"/engagements/{T}/capital-allowances/rules/{cid}", {"method": "NONE"}), (f"/engagements/{T}/capital-allowances/assets/{ids['A1']}/disposal", {"tax_disposed_on": "2026-01-01"}),
                      (f"/engagements/{T}/qpd/use-computation", {})]:
        check(O.post(url, data=data).status_code == 403, f"someone not on the engagement is refused: {url.split('/', 3)[3]}")

    # ---- other engagement types keep their register behaviour
    r = M.post(f"/engagements/{A}/ppe/asset-classes/add", data={"name": "Vehicles"})
    check(r.status_code == 302 and "tab=substantive" in r.headers["Location"], "Audit: the Asset Register still returns to Substantive Procedures")
    n = SubstantiveProcedureArea.query.filter_by(engagement_id=A).count()
    M.post(f"/engagements/{A}/substantive-procedures/generate")
    check(SubstantiveProcedureArea.query.filter_by(engagement_id=A).count() > n, "Audit: 'Generate suggested procedures' still seeds the audit areas")

print("\n%d failure(s)" % len(fails))
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
