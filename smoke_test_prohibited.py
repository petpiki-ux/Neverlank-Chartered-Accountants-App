"""Section 16(1) prohibited deductions engine: every rule, thin capitalisation, add-back only when charged to profit
or loss, ledger pull, CSV import, posting to the Income Tax Computation, access, rule verification and the exports."""
import os, sys, tempfile, io
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
from datetime import date
from types import SimpleNamespace as NS
from app import create_app
from extensions import db
from models import *
import prohibited_deductions as PD
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def close(a, b): return abs(a - b) < 0.005

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    other_partner = User(username="p2", name="Other Partner", role="partner"); other_partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, other_partner, staff]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Tax FY2025", type="Audit", partner_id=partner.id, period_end=date(2025, 12, 31), acceptance_required=False)
    db.session.add(eng); db.session.commit()
    tb = TrialBalance(engagement_id=eng.id, source="manual"); db.session.add(tb); db.session.commit()
    db.session.add_all([TrialBalanceLine(trial_balance_id=tb.id, account_code="4000", account_name="Sales", fs_category="revenue", current_credit=100000.0),
                        TrialBalanceLine(trial_balance_id=tb.id, account_code="6000", account_name="Admin", fs_category="admin_expenses", current_debit=60000.0)])
    db.session.commit()
    ids = dict(partner=partner.id, p2=other_partner.id, staff=staff.id, eng=eng.id)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, O, S = login(ids["partner"]), login(ids["p2"]), login(ids["staff"])
E = ids["eng"]
B = f"/engagements/{E}/prohibited-deductions"

# ------------------------------------------------------------ unit: rules and evaluation
with app.app_context():
    PD.ensure_rules()
    R = PD.rules_by_code()
    check(len(R) >= 39 and R["client_entertainment"].clause_label == "16(1)(m)" and R["thin_capitalisation"].clause_label == "16(1)(q)", "rule table seeded with clauses")
    check(not any(r.is_verified for r in R.values()), "no rule starts verified")
    check(PD.normalise_type("Interest Local Financial Institution Loan Deposit") == "expense_earning_local_fi_interest", "the spec's own type names are accepted as aliases")
    def it(**kw):
        d = dict(amount=1000.0, charged_to_pl=True, is_insured=False, reimbursed_amount=0.0, business_necessity=False, linked_to_exempt=False,
                 prohibited_percent=None, vehicle_cost=None); d.update(kw); return NS(**d)
    setup = NS(pmv_cap=100000.0, mining_foreign_parent=False)
    def ev(code, setup=setup, **kw): return PD.evaluate(it(**kw), R[code], setup)
    for code in ("personal_maintenance", "family_support", "personal_establishment", "home_to_work_travel", "domestic_private_expense"):
        r = ev(code); check(r["status"] == "PROHIBITED_SECTION_16" and close(r["addback"], 1000) and r["clause"] in ("16(1)(a)", "16(1)(b)"), f"{code}: 100% added back")
    r = ev("inter_trade_travel"); check(r["status"] == "PROHIBITED_SECTION_16", "travel between trades without necessity is disallowed")
    r = ev("inter_trade_travel", business_necessity=True); check(r["status"] == "PROHIBITED_SECTION_16" and close(r["addback"], 1000) and r["clause"] == "16(1)(b)", "16(1)(b) names travel between two places of trade: no business-necessity exception")
    r = ev("loss_or_damage", is_insured=True, reimbursed_amount=400); check(r["status"] == "PARTIALLY_PROHIBITED" and close(r["addback"], 400) and r["clause"] == "16(1)(c)", "insured loss: only the recovered part")
    r = ev("loss_or_damage", is_insured=True, reimbursed_amount=5000); check(close(r["addback"], 1000) and r["status"] == "PROHIBITED_SECTION_16", "recovery above the loss is capped at the loss")
    r = ev("loss_or_damage"); check(r["status"] == "ALLOWABLE" and r["addback"] == 0, "uninsured loss is allowable")
    r = ev("loss_or_damage", is_insured=True, reimbursed_amount=0); check(r["addback"] == 0, "insured but nothing recovered: nothing added back")
    for code in ("corporate_income_tax", "capital_gains_tax", "presumptive_tax", "withholding_tax", "tax_interest"):
        r = ev(code); check(r["clause"] == "16(1)(d)" and close(r["addback"], 1000) and r["status"] == "PROHIBITED_SECTION_16", f"{code}: 16(1)(d) add-back")
    for code in ("vat_expense", "paye_expense", "carbon_tax", "tax_penalties"):
        r = ev(code); check(r["clause"] == "16(1)(d)" and close(r["addback"], 1000) and r["status"] == "REVIEW_REQUIRED", f"{code}: not named in (d) - added back and flagged for a Partner")
    r = ev("imtt_expense"); check(r["clause"] == "16(1)(d1)" and close(r["addback"], 1000) and r["status"] == "PROHIBITED_SECTION_16", "intermediated money transfer tax is 16(1)(d1)")
    for code in ("transfer_to_reserve", "capitalized_expense"):
        r = ev(code); check(r["clause"] == "16(1)(e)" and close(r["addback"], 1000), f"{code}: added back when charged")
        r = ev(code, charged_to_pl=False); check(r["status"] == "PROHIBITED_SECTION_16" and r["addback"] == 0 and "Not charged" in r["note"], f"{code}: nothing to add back when never charged to profit or loss (no double count)")
    r = ev("ordinary_business_expense", linked_to_exempt=True); check(r["clause"] == "16(1)(f)" and close(r["addback"], 1000), "any expense directly linked to exempt income is refused (f)")
    r = ev("ordinary_business_expense", linked_to_exempt=True, prohibited_percent=25); check(r["status"] == "PARTIALLY_PROHIBITED" and close(r["addback"], 250), "shared cost: only the exempt-income percentage")
    r = ev("exempt_income_cost"); check(close(r["addback"], 1000), "exempt income cost with no percentage: in full")
    r = ev("contribution_unapproved_fund"); check(r["clause"] == "16(1)(g)" and close(r["addback"], 1000), "unapproved fund contribution")
    r = ev("notional_capital_interest"); check(r["clause"] == "16(1)(h)" and close(r["addback"], 1000), "notional interest")
    r = ev("dwelling_house_expense"); check(r["clause"] == "16(1)(i)" and close(r["addback"], 1000), "non-trade premises: 100% by default")
    r = ev("non_trade_repairs", prohibited_percent=40); check(r["status"] == "PARTIALLY_PROHIBITED" and close(r["addback"], 400), "non-trade premises: the trade part is kept")
    r = ev("sole_selling_rights"); check(r["clause"] == "16(1)(j)" and close(r["addback"], 1000), "sole selling rights is (j)")
    r = ev("restraint_of_trade"); check(r["status"] == "REVIEW_REQUIRED" and close(r["addback"], 1000) and r["clause"] == "", "restraint of trade: added back, flagged for a Partner, no (j) label")
    r = ev("pmv_lease", vehicle_cost=100600); check(r["status"] == "PARTIALLY_PROHIBITED" and close(r["addback"], 600) and r["clause"] == "16(1)(k)", "lease cost to date 100,600 against a cap of 100,000: only the 600 above it")
    r = ev("pmv_lease", vehicle_cost=150000); check(r["status"] == "PROHIBITED_SECTION_16" and close(r["addback"], 1000), "lease cost already far above the cap: this year's whole amount")
    r = ev("pmv_lease", vehicle_cost=80000); check(r["addback"] == 0 and r["status"] == "ALLOWABLE", "lease cost to date within the cap: allowed")
    r = ev("pmv_lease"); check(r["addback"] == 0, "no lease total entered: this amount alone is taken, within the cap")
    r = ev("pmv_lease", vehicle_cost=150000, setup=NS(pmv_cap=None)); check(r["addback"] == 0 and "Enter the passenger" in r["note"], "no cap entered: not guessed, asks for it")
    r = ev("pmv_operating_cost", vehicle_cost=150000); check(r["addback"] == 0, "running costs are not capped under the default rule")
    r = ev("share_award_expense"); check(r["clause"] == "16(1)(l)" and close(r["addback"], 1000), "share awards")
    for code in ("client_entertainment", "business_lunch", "hospitality_allowance"):
        r = ev(code); check(r["clause"] == "16(1)(m)" and close(r["addback"], 1000), f"{code}: 16(1)(m)")
    r = ev("expense_earning_stocks_shares_income"); check(r["clause"] == "16(1)(n)" and close(r["addback"], 1000), "cost of earning share income (n)")
    r = ev("expense_earning_local_fi_interest"); check(r["clause"] == "16(1)(o)" and close(r["addback"], 1000), "cost of earning local FI interest (o)")
    r = ev("foreign_management_fee"); check(r["addback"] == 0 and "Enter A" in r["note"], "associated-company fee: asks for A, B and C rather than guessing")
    fee_setup = NS(pmv_cap=None, assoc_fee_a=1_000_000.0, assoc_fee_b=100_000.0, assoc_fee_c=0.0, assoc_fee_pretrade=False, assoc_fee_total=None)
    r = ev("foreign_management_fee", setup=fee_setup); check(r["addback"] == 0 and "within the limit 9,000.00" in r["note"], "fee of 1,000 against a limit of 1% of (1,000,000 - 100,000) = 9,000: allowed")
    fee_setup.assoc_fee_total = 30_000.0
    r = ev("foreign_management_fee", setup=fee_setup); check(r["clause"] == "16(1)(r)" and close(r["addback"], 700), "all such fees 30,000 against 9,000: 70% of this line is refused (700 of 1,000)")
    fee_setup.assoc_fee_pretrade = True
    r = ev("foreign_management_fee", setup=fee_setup); check(close(r["addback"], 1000 * (30000 - 6750) / 30000), "before trade starts the limit is 0.75%: 6,750")
    r = ev("foreign_management_fee", setup=NS(pmv_cap=None, assoc_fee_a=100_000.0, assoc_fee_b=80_000.0, assoc_fee_c=30_000.0, assoc_fee_pretrade=False, assoc_fee_total=1000.0)); check("Enter A" in r["note"] and r["addback"] == 0, "A - (B + C) of nil or less: asks for the figures")
    r = ev("foreign_loan_interest_excess"); check(r["clause"] == "16(1)(s)" and close(r["addback"], 1000), "foreign loan interest excess (s)")
    r = ev("client_entertainment", amount=-300.0); check(close(r["addback"], -300), "a credit/reversal reduces the add-back")
    check(PD.evaluate(it(), None, setup)["status"] == "ALLOWABLE", "an unknown type is allowable and says so")
    # thin capitalisation
    t = PD.thin_cap(NS(thin_cap_ratio=3, related_debt=6_000_000, qualifying_equity=1_000_000, related_interest=600_000))
    check(close(t["disallowed"], 300_000) and close(t["excess_debt"], 3_000_000), "thin cap: 6m debt, 1m equity, 600k interest -> 300k disallowed")
    check(close(PD.thin_cap(NS(thin_cap_ratio=3, related_debt=2_000_000, qualifying_equity=1_000_000, related_interest=100_000))["disallowed"], 0), "thin cap: within the limit")
    t = PD.thin_cap(NS(thin_cap_ratio=3, related_debt=1_000_000, qualifying_equity=0, related_interest=50_000)); check(close(t["disallowed"], 50_000), "thin cap: no equity -> all interest, no division by zero")
    t = PD.thin_cap(NS(thin_cap_ratio=3, related_debt=1_000_000, qualifying_equity=-5, related_interest=50_000)); check(close(t["disallowed"], 50_000), "thin cap: negative equity -> all interest")
    t = PD.thin_cap(NS(thin_cap_ratio=None, related_debt=6_000_000, qualifying_equity=1_000_000, related_interest=600_000)); check(t["disallowed"] == 0 and "Enter the debt" in t["note"], "thin cap: limit not entered -> not assumed")
    check(PD.thin_cap(NS(thin_cap_ratio=3, related_debt=0, qualifying_equity=0, related_interest=0)) is None, "thin cap: nothing entered -> nothing")
    check(PD.thin_cap(None) is None, "thin cap: no setup")

# ------------------------------------------------------------ access
check(S.get(B).status_code == 403, "staff outside the engagement are refused")
check(P.get(B).status_code == 200, "the engagement partner opens the page")
r = P.get(B); check(b"Section 16(1) Prohibited Deductions" in r.data and b"Add an expense" in r.data, "page content")

# ------------------------------------------------------------ setup and items through the UI
P.post(f"{B}/setup", data={"pmv_cap": "100000", "thin_cap_ratio": "3", "related_debt": "6000000", "qualifying_equity": "1000000", "related_interest": "600000"})
def add(code, desc, amount, voucher, **kw):
    d = {"expense_type": code, "description": desc, "amount": str(amount), "voucher_ref": voucher, "charged_to_pl": "1"}
    for k, v in kw.items(): d[k] = v
    d = {k: v for k, v in d.items() if v is not False}
    return P.post(f"{B}/items/add", data=d, follow_redirects=True)
add("client_entertainment", "Client dinner", 1200, "PV-101")
add("client_entertainment", "Year-end function", 800, "PV-102")
add("imtt_expense", "IMTT on transfers", 450, "PV-103")
add("pmv_lease", "Director's SUV lease", 24000, "PV-104", vehicle_cost="112000")
add("loss_or_damage", "Warehouse fire", 10000, "JV-9", is_insured="1", reimbursed_amount="7000")
add("capitalized_expense", "Plant installation expensed", 5000, "PV-105", charged_to_pl="")
add("ordinary_business_expense", "Stationery", 300, "PV-106")
r = add("ordinary_business_expense", "", "", "x"); check(b"description and an amount" in r.data, "an empty line is refused")
r = add("thin_capitalisation", "x", 10, "x"); check(b"Choose an expense type" in r.data, "the setup-driven thin cap rule cannot be picked as an expense")
with app.app_context():
    items = ProhibitedDeductionItem.query.filter_by(engagement_id=E).all()
    check(len(items) == 7 and all(i.status for i in items), "seven lines stored, each with a status")
    by = {i.voucher_ref: i for i in items}
    check(by["PV-101"].status == "PROHIBITED_SECTION_16" and by["PV-101"].clause_label == "16(1)(m)" and close(by["PV-101"].addback_amount, 1200), "dinner: (m), 1,200")
    check(by["PV-104"].status == "PARTIALLY_PROHIBITED" and close(by["PV-104"].addback_amount, 12000), "SUV lease: lease cost to date 112,000 against a 100,000 cap: 12,000 refused")
    check(by["JV-9"].status == "PARTIALLY_PROHIBITED" and close(by["JV-9"].addback_amount, 7000), "fire loss: 7,000 recovered")
    check(by["PV-105"].addback_amount == 0 and by["PV-105"].status == "PROHIBITED_SECTION_16", "capitalised item not charged: prohibited but no add-back")
    check(by["PV-106"].status == "ALLOWABLE", "stationery allowable")
    check(by["PV-101"].rule_verified is False, "lines record that their rule is unverified")

    sched = PD.build_schedule(E)
    clauses = [g["clause"] for g in sched["groups"]]
    check(clauses == ["16(1)(c)", "16(1)(d1)", "16(1)(e)", "16(1)(k)", "16(1)(m)"], f"schedule grouped by clause in order (got {clauses})")
    g = {g["clause"]: g for g in sched["groups"]}
    check(close(g["16(1)(m)"]["addback"], 2000) and len(g["16(1)(m)"]["items"]) == 2, "(m) group: two items, 2,000")
    check(close(sched["thin_addback"], 300_000), "thin cap add-back 300,000")
    check(close(sched["total_addback"], 2000 + 450 + 12000 + 7000 + 300_000), f"total add-back {sched['total_addback']}")
    check(sched["counts"]["PROHIBITED_SECTION_16"] == 4 and sched["counts"]["PARTIALLY_PROHIBITED"] == 2 and sched["counts"]["ALLOWABLE"] == 1, "status counts")
    check("Entertainment" in sched["unverified_rules"] and sched["thin_unverified"], "unverified rules reported")
    st, basis = PD.computation_statement(Engagement.query.get(E), sched, PD.get_setup(E))
    check(basis == "trial balance" and close(st["profit_before_tax"], 40000), "profit before tax comes from the trial balance (40,000)")
    check(close(st["taxable_income_before_losses"], 40000 + sched["total_addback"]), "taxable income = profit + add-backs")

r = P.get(B)
check(b"Section 16(1)(m)" in r.data and b"Client dinner" in r.data and b"PV-101" in r.data and b"Clause references not yet verified" in r.data, "page shows the schedule, vouchers and the unverified warning")
check(b"Thin capitalisation" in r.data and b"300,000.00" in r.data, "page shows the thin capitalisation add-back")

# update, delete
with app.app_context():
    iid = ProhibitedDeductionItem.query.filter_by(voucher_ref="PV-102").first().id
P.post(f"/prohibited-deductions/items/{iid}/update", data={"expense_type": "client_entertainment", "description": "Year-end function", "amount": "1000", "voucher_ref": "PV-102", "charged_to_pl": "1"})
with app.app_context():
    check(close(ProhibitedDeductionItem.query.get(iid).addback_amount, 1000), "editing a line re-tests it")
P.post(f"/prohibited-deductions/items/{iid}/update", data={"expense_type": "business_lunch", "description": "Lunch", "amount": "500", "charged_to_pl": ""})
with app.app_context():
    x = ProhibitedDeductionItem.query.get(iid); check(x.addback_amount == 0 and x.status == "PROHIBITED_SECTION_16", "switching to 'not charged' removes the add-back")
P.post(f"/prohibited-deductions/items/{iid}/update", data={"expense_type": "business_lunch", "description": "Lunch", "amount": "500", "charged_to_pl": "1"})
check(S.post(f"/prohibited-deductions/items/{iid}/delete").status_code == 403, "staff cannot delete a line on an engagement they are not on")

# ------------------------------------------------------------ CSV import
csv_ok = ("date,voucher_ref,description,expense_type,amount,charged_to_pl,linked_to_exempt,prohibited_percent\n"
          "2025-03-01,PV-201,Golf day,client_entertainment,600,yes,,\n"
          "2025-03-02,PV-202,Costs of earning exempt dividends,ordinary_business_expense,1000,yes,yes,50\n"
          "2025-03-03,PV-203,Bad type,not_a_type,5,yes,,\n"
          "2025-03-04,PV-204,No amount,client_entertainment,,yes,,\n"
          "2025-03-05,PV-205,Local interest costs,interest_local_financial_institution_loan_deposit,200,yes,,\n")
r = P.post(f"{B}/import", data={"file": (io.BytesIO(csv_ok.encode()), "x.csv")}, content_type="multipart/form-data", follow_redirects=True)
check(b"3 line(s) imported" in r.data and b"unknown expense type" in r.data and b"numeric amount" in r.data, "CSV: good rows imported, bad rows reported")
with app.app_context():
    d = {i.voucher_ref: i for i in ProhibitedDeductionItem.query.filter_by(engagement_id=E, source="Import").all()}
    check(set(d) == {"PV-201", "PV-202", "PV-205"}, "only the good rows were imported")
    check(close(d["PV-202"].addback_amount, 500) and d["PV-202"].clause_label == "16(1)(f)", "exempt-linked row: 50% under (f)")
    check(d["PV-205"].expense_type == "expense_earning_local_fi_interest" and d["PV-205"].clause_label == "16(1)(o)", "the spec's long type name is mapped")
check(b"Choose a CSV" in P.post(f"{B}/import", data={}, content_type="multipart/form-data", follow_redirects=True).data, "import without a file")

# ------------------------------------------------------------ ledger mapping and pull
with app.app_context():
    a_ent = GLAccount(engagement_id=E, code="6100", name="Entertainment", account_type="Expense")
    a_stat = GLAccount(engagement_id=E, code="6200", name="Stationery", account_type="Expense")
    a_bank = GLAccount(engagement_id=E, code="1000", name="Bank", account_type="Asset")
    db.session.add_all([a_ent, a_stat, a_bank]); db.session.commit()
    je = JournalEntry(engagement_id=E, entry_date=date(2025, 5, 5), reference="JV-300", narration="Staff party")
    je.lines.append(JournalEntryLine(gl_account_id=a_ent.id, description="Staff party catering", debit=900.0, credit=0.0))
    je.lines.append(JournalEntryLine(gl_account_id=a_bank.id, description="Bank", debit=0.0, credit=900.0))
    je2 = JournalEntry(engagement_id=E, entry_date=date(2025, 5, 6), reference="JV-301", narration="Stationery")
    je2.lines.append(JournalEntryLine(gl_account_id=a_stat.id, description="Paper", debit=100.0, credit=0.0))
    je2.lines.append(JournalEntryLine(gl_account_id=a_bank.id, description="Bank", debit=0.0, credit=100.0))
    db.session.add_all([je, je2]); db.session.commit()
    gl = dict(ent=a_ent.id, stat=a_stat.id, bank=a_bank.id)
r = P.post(f"{B}/pull", follow_redirects=True); check(b"Map at least one" in r.data, "pull needs a mapping first")
r = P.get(B); check(b"Entertainment" in r.data and b"not tested" in r.data and b'name="map_%d"' % gl["ent"] in r.data and b'name="map_%d"' % gl["bank"] not in r.data, "only Expense accounts are offered for mapping")
P.post(f"{B}/mapping", data={f"map_{gl['ent']}": "client_entertainment", f"map_{gl['stat']}": "ordinary_business_expense", f"map_{gl['bank']}": "client_entertainment"})
with app.app_context():
    check(GLAccount.query.get(gl["ent"]).tax_expense_type == "client_entertainment" and GLAccount.query.get(gl["bank"]).tax_expense_type is None, "mapping saved; non-expense account ignored")
r = P.post(f"{B}/pull", follow_redirects=True); check(b"2 ledger line(s) pulled" in r.data, "journal lines pulled in")
r = P.post(f"{B}/pull", follow_redirects=True); check(b"0 ledger line(s) pulled" in r.data and b"2 already there" in r.data, "pulling again adds nothing")
with app.app_context():
    l = ProhibitedDeductionItem.query.filter_by(voucher_ref="JV-300").first()
    check(l and l.source == "Ledger" and close(l.amount, 900) and l.status == "PROHIBITED_SECTION_16" and l.gl_account_id == gl["ent"] and l.journal_line_id, "ledger line: voucher, account, amount and status carried")
    check(ProhibitedDeductionItem.query.filter_by(voucher_ref="JV-301").first().status == "ALLOWABLE", "mapped allowable account: tested and allowable")

# ------------------------------------------------------------ setup changes re-test everything
P.post(f"{B}/setup", data={"pmv_cap": "200000", "thin_cap_ratio": "3", "related_debt": "6000000", "qualifying_equity": "1000000", "related_interest": "600000"})
with app.app_context():
    check(ProhibitedDeductionItem.query.filter_by(voucher_ref="PV-104").first().addback_amount == 0, "raising the PMV cap re-tests the lease: now within the cap")
P.post(f"{B}/setup", data={"pmv_cap": "100000", "thin_cap_ratio": "3", "related_debt": "6000000", "qualifying_equity": "1000000", "related_interest": "600000"})
with app.app_context():
    check(close(ProhibitedDeductionItem.query.filter_by(voucher_ref="PV-104").first().addback_amount, 12000), "and back")

# ------------------------------------------------------------ rules page and verification
r = P.get("/prohibited-deductions/rules"); check(r.status_code == 200 and b"Not verified" in r.data and b"Save and mark verified" in r.data, "rules page for a Partner")
check(S.get("/prohibited-deductions/rules").status_code == 200, "staff can read the rules")
with app.app_context():
    rid = ProhibitedDeductionRule.query.filter_by(code="client_entertainment").first().id
check(S.post(f"/prohibited-deductions/rules/{rid}", data={"clause_label": "16(1)(m)", "treatment": "full", "is_active": "1"}).status_code == 403, "staff cannot change a rule")
P.post(f"/prohibited-deductions/rules/{rid}", data={"clause_label": "16(1)(m)", "treatment": "full", "is_active": "1", "verify": "1", "verified_note": "Checked to the Act"})
with app.app_context():
    r_ = ProhibitedDeductionRule.query.get(rid); check(r_.is_verified and r_.verified_by_id == ids["partner"] and r_.verified_note == "Checked to the Act", "a Partner can verify a rule")
    check(ProhibitedDeductionItem.query.filter_by(voucher_ref="PV-101").first().rule_verified is True, "lines under it now show as verified")
    check("Entertainment" not in PD.build_schedule(E)["unverified_rules"], "and it leaves the unverified list")
P.post(f"/prohibited-deductions/rules/{rid}", data={"clause_label": "16(1)(m)", "treatment": "full", "is_active": "1", "note": "edited note"})
with app.app_context():
    check(ProhibitedDeductionRule.query.get(rid).is_verified, "saving with no change keeps the verification")
P.post(f"/prohibited-deductions/rules/{rid}", data={"clause_label": "16(1)(n)", "treatment": "full", "is_active": "1"})
with app.app_context():
    check(not ProhibitedDeductionRule.query.get(rid).is_verified, "changing the clause removes the verification")
    check(ProhibitedDeductionItem.query.filter_by(voucher_ref="PV-101").first().clause_label == "16(1)(n)", "and every line is re-tested under the new clause")
P.post(f"/prohibited-deductions/rules/{rid}", data={"clause_label": "16(1)(m)", "treatment": "none", "is_active": "1"})
with app.app_context():
    check(ProhibitedDeductionItem.query.filter_by(voucher_ref="PV-101").first().addback_amount == 0, "switching the treatment to 'none' removes the add-back")
P.post(f"/prohibited-deductions/rules/{rid}", data={"clause_label": "16(1)(m)", "treatment": "full", "is_active": "1"})
r = P.post(f"/prohibited-deductions/rules/{rid}", data={"clause_label": "x", "treatment": "bogus"}, follow_redirects=True); check(b"Choose a treatment" in r.data, "a bad treatment is refused")

# ------------------------------------------------------------ posting to the Income Tax Computation
with app.app_context():
    comp = IncomeTaxComputation(engagement_id=E, tax_rate_percent=25.0, aids_levy_percent=3.0, tax_loss_brought_forward=10000.0,
                                reviewed_by_id=ids["partner"], partner_signed_by_id=ids["partner"])
    db.session.add(comp); db.session.flush()
    db.session.add(IncomeTaxAdjustmentLine(computation_id=comp.id, item_type="deduction", description="Capital allowances", amount=5000.0))
    db.session.add(IncomeTaxAdjustmentLine(computation_id=comp.id, item_type="addback", description="Depreciation", amount=3000.0))
    db.session.commit()
    total = PD.build_schedule(E)["total_addback"]
r = P.post(f"{B}/post", follow_redirects=True)
check(b"add-back line" in r.data and b"Posted from the section 16(1) schedule" in r.data, "posting works and the engagement tab marks the posted lines")
with app.app_context():
    comp = IncomeTaxComputation.query.filter_by(engagement_id=E).first()
    eng_lines = [l for l in comp.items if l.source == "s16_engine"]
    others = [l for l in comp.items if l.source != "s16_engine"]
    check(len(others) == 2 and {l.description for l in others} == {"Capital allowances", "Depreciation"}, "the preparer's own lines are untouched")
    check(close(sum(l.amount for l in eng_lines), total) and all(l.item_type == "addback" for l in eng_lines), "posted add-backs equal the schedule total")
    check(any("16(1)(q)" in l.description for l in eng_lines) and any("16(1)(m)" in l.description for l in eng_lines), "one line per clause plus thin capitalisation")
    check(comp.reviewed_by_id is None and comp.partner_signed_by_id is None, "review and sign-off are reset")
    n1 = len(comp.items)
P.post(f"{B}/post")
with app.app_context():
    comp = IncomeTaxComputation.query.filter_by(engagement_id=E).first()
    check(len(comp.items) == n1, "posting again replaces, never duplicates")
r = P.get(f"/engagements/{E}?tab=income_tax")
check(r.status_code == 200 and b"Open the prohibited deductions schedule" in r.data, "the Income Tax tab links to the schedule")
r = P.get(B); check(b"Posted to Income Tax Computation" in r.data and b"post again" not in r.data, "page shows posted = schedule")
P.post(f"{B}/items/add", data={"expense_type": "client_entertainment", "description": "Another", "amount": "10", "charged_to_pl": "1"})
r = P.get(B); check(b"post again" in r.data, "after a change the page says the posting is out of date")
with app.app_context():
    sched = PD.build_schedule(E); st, _ = PD.computation_statement(Engagement.query.get(E), sched, PD.get_setup(E))
    check(close(st["taxable_income_before_losses"], 40000 + sched["total_addback"] + 3000 - 5000), "statement: profit + add-backs + preparer add-backs - deductions")
    check(close(st["loss_utilised"], 10000) and close(st["total_tax_charge"], (st["taxable_income"]) * 0.25 * 1.03), "assessed loss and rate come from the working paper")

# ------------------------------------------------------------ exports
r = P.get(f"{B}/export.xlsx")
check(r.status_code == 200 and r.data[:2] == b"PK", "Excel export")
import openpyxl
wb = openpyxl.load_workbook(io.BytesIO(r.data))
check(wb.sheetnames == ["Tax computation", "Schedule by clause", "All expenses tested", "Rules relied on"], f"four sheets (got {wb.sheetnames})")
vals = [str(c.value) for row in wb["Schedule by clause"].iter_rows() for c in row if c.value is not None]
check(any("Section 16(1)(m)" in v for v in vals) and "PV-101" in vals and "JV-300" in vals and any("16(1)(q)" in v for v in vals), "schedule sheet has clauses and voucher references")
check(any("NOT VERIFIED" in str(c.value) for row in wb["Rules relied on"].iter_rows() for c in row), "rules sheet flags unverified rules")
r = P.get(f"{B}/print"); check(r.status_code == 200 and b"Add-backs by clause" in r.data and b"Taxable income" in r.data and b"PV-101" in r.data, "printable schedule")
check(S.get(f"{B}/export.xlsx").status_code == 403 and S.get(f"{B}/print").status_code == 403, "exports respect access")

# no trial balance -> entered profit
with app.app_context():
    e2 = Engagement(client_id=Client.query.first().id, title="No TB", type="Audit", partner_id=ids["partner"], acceptance_required=False); db.session.add(e2); db.session.commit(); e2id = e2.id
r = P.get(f"/engagements/{e2id}/prohibited-deductions"); check(r.status_code == 200 and b"isn't available" in r.data, "no trial balance: asks for profit")
P.post(f"/engagements/{e2id}/prohibited-deductions/setup", data={"net_profit_override": "12345.67"})
r = P.get(f"/engagements/{e2id}/prohibited-deductions"); check(b"12,345.67" in r.data, "entered profit is used")

# ------------------------------------------------------------ migration
with app.app_context():
    from sqlalchemy import text
    def rebuild(table, drop):
        info = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
        keep = [row for row in info if row[1] != drop]
        names = ", ".join(f'"{row[1]}"' for row in keep); defs = ", ".join(f'"{row[1]}" {row[2]}' for row in keep)
        db.session.execute(text(f"ALTER TABLE {table} RENAME TO {table}_bak"))
        db.session.execute(text(f"CREATE TABLE {table} ({defs})"))
        db.session.execute(text(f"INSERT INTO {table} ({names}) SELECT {names} FROM {table}_bak"))
        db.session.execute(text(f"DROP TABLE {table}_bak")); db.session.commit()
    cols = lambda t: [r[1] for r in db.session.execute(text(f"PRAGMA table_info({t})")).fetchall()]
    rebuild("gl_account", "tax_expense_type"); rebuild("income_tax_adjustment_line", "source")
    check("tax_expense_type" not in cols("gl_account") and "source" not in cols("income_tax_adjustment_line"), "old-style tables lack the new columns")
    from app import _add_missing_columns
    _add_missing_columns()
    check("tax_expense_type" in cols("gl_account") and "source" in cols("income_tax_adjustment_line"), "_add_missing_columns adds them")

print()
print("FAILED:" if fails else "ALL PASSED", len(fails) if fails else "")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
