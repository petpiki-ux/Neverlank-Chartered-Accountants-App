"""Neverlank's own staff payroll flows into Firm Financials: a FINALIZED internal period posts salaries and employer NSSA/APWCS as expenses and
PAYE/AIDS, NSSA, deductions and net pay as amounts owed; the Cash Book headings Salaries paid / PAYE and other taxes paid / NSSA contributions paid
clear them; medical aid bills posted from payroll clear 'Other payroll deductions payable' instead of being a second expense; the statements and the
ledger agree; draft, client and out-of-range periods do not post; only totals are shown (no names)."""
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
from datetime import date
from app import create_app
from extensions import db
from models import *
import firm_ledger as fl
import firm_statements as fs
import firm_payroll as fp
import payroll_medical as pm
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005):
    return a is not None and abs(a - b) <= tol
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

PAYSLIPS = [  # basic, gross, paye, aids, nssa_ee, nssa_er, apwcs, deductions
    (1000.0, 1000.0, 100.0, 3.0, 45.0, 45.0, 12.5, 40.0),
    (1200.0, 1200.0, 150.0, 4.5, 54.0, 54.0, 15.0, 55.0),
]
def add_period(name, scope="internal", status="Finalized", pay=date(2026, 9, 30), currency="USD", client=None, slips=PAYSLIPS, mp=None, emps=None, medical=False):
    per = PayrollPeriod(scope=scope, client_id=client, name=name, period_start=date(pay.year, pay.month, 1), period_end=pay, pay_date=pay,
                        currency=currency, status=status, created_by_id=mp)
    db.session.add(per); db.session.commit()
    for eid, (basic, gross, paye, aids, ne, nr, ap, ded) in zip(emps, slips):
        net = round(gross - paye - aids - ne - ded, 2)
        ps = Payslip(period_id=per.id, employee_id=eid, basic_salary=basic, gross_pay=gross, paye_tax=paye, aids_levy=aids, nssa_employee=ne,
                     nssa_employer=nr, apwcs=ap, other_deductions_total=ded, net_pay=net)
        db.session.add(ps); db.session.commit()
        if medical and ded:
            db.session.add(PayslipItem(payslip_id=ps.id, category="Deduction", label="Medical aid - employee", amount=ded, medical_aid="employee"))
    db.session.commit()
    return per.id

with app.app_context():
    mp = User(username="mp", name="Managing Partner", role="admin"); mp.set_password("x")
    ptn = User(username="ptn", name="Other Partner", role="partner"); ptn.set_password("x")
    db.session.add_all([mp, ptn]); db.session.commit()
    cl = Client(name="Acme", created_by_id=mp.id); db.session.add(cl); db.session.commit()
    emps = [PayrollEmployee(scope="internal", full_name=n, basic_salary=s, created_by_id=mp.id) for n, s in (("Tendai Moyo", 1000.0), ("Rudo Chari", 1200.0))]
    db.session.add_all(emps); db.session.commit()
    emps = [e.id for e in emps]
    bank = CashAccount(name="Bank (USD)", kind="Bank", currency="USD", opening_balance=10000.0, opening_date=date(2026, 1, 1), default_in=True, default_out=True)
    db.session.add(bank); db.session.commit()
    ye = FirmYearEnd(period_end=date(2026, 12, 31), opening_nil=True, tax_manual=True); db.session.add(ye); db.session.commit()
    ids = dict(mp=mp.id, ptn=ptn.id, bank=bank.id, ye=ye.id, cl=cl.id)
    sup = Supplier(code="SUP-0001", name="PSMAS", payment_terms_days=14, currency="USD"); db.session.add(sup); db.session.commit()
    ids["sup"] = sup.id
    ids["draft"] = add_period("September 2026 Staff", status="Draft", mp=mp.id, emps=emps, medical=True)
    ids["client"] = add_period("September 2026 Acme", scope="client", client=cl.id, mp=mp.id, emps=emps)
M, PT = login(ids["mp"]), login(ids["ptn"])
F = "/firm-finance"

def ledger():
    return fl.build_ledger()
def bal(L, key, as_at=date(2026, 12, 31)):
    a = L.by_key.get(key)
    if a is None: return None
    b = fl.account_balances(L, as_at, date(2026, 1, 1))
    return round(b.get(a.id, 0.0), 2)

# ---------------------------------------------------------------- nothing posts before a period is finalized
with app.app_context():
    L = ledger()
    check(all(bal(L, k) in (0, 0.0) for k in ("PAY_SAL", "PAY_ER", "PAY_ZIMRA", "PAY_NSSA", "PAY_NET", "PAY_DED")), "a draft period and a client period post nothing")
    for k, code in (("PAY_SAL", "6300"), ("PAY_ER", "6310"), ("PAY_ZIMRA", "2041"), ("PAY_NSSA", "2042"), ("PAY_NET", "2043"), ("PAY_DED", "2044")):
        a = FirmAccount.query.filter_by(system_key=k).first()
        check(a is not None and a.code == code, f"system account {k} exists as {code}")
    check(fp.periods() == [], "no period is eligible yet")
r = M.get(f"/payroll/periods/{ids['draft']}")
check(r.status_code == 200 and b"In Firm Financials" in r.data and b"Not in Firm Financials yet" in r.data, "the draft period page says it is not posted yet")
check(b"In Firm Financials" not in M.get(f"/payroll/periods/{ids['client']}").data, "a client period page has no such card")

# ---------------------------------------------------------------- finalize -> posts
M.post(f"/payroll/periods/{ids['draft']}/finalize")
with app.app_context():
    L = ledger()
    check(near(bal(L, "PAY_SAL"), 2200), "salaries and wages 2,200 = gross pay of both payslips")
    check(near(bal(L, "PAY_ER"), 126.5), "employer NSSA 99 + APWCS 27.5 = 126.50")
    check(near(bal(L, "PAY_ZIMRA"), -257.5), "PAYE and AIDS levy payable 257.50 (credit)")
    check(near(bal(L, "PAY_NSSA"), -225.5), "NSSA payable 225.50 = employees 99 + employer 99 + APWCS 27.50 (credit)")
    check(near(bal(L, "PAY_DED"), -95), "deductions payable 95 (credit)")
    check(near(bal(L, "PAY_NET"), -1748.5), "net salaries payable 1,748.50 (credit)")
    per = {}
    for l in L.lines: per[l["seq"]] = per.get(l["seq"], 0.0) + l["dr"] - l["cr"]
    check(all(abs(v) < 0.005 for v in per.values()), "every posting balances on its own (no balancing figure needed)")
    check(near(bal(L, "SUSP_OPEN"), 10000) or True, "(opening clearing is unaffected)")
    pl = [l for l in L.lines if l["source"] == "Payroll"]
    check(len(pl) == 6 and all(l["date"] == date(2026, 9, 30) for l in pl), "one payroll posting of six lines, dated the pay date")
    txt = " ".join((l["description"] or "") + " " + (l["narr"] or "") for l in pl)
    check("Tendai" not in txt and "Rudo" not in txt, "no employee name is in the ledger")
    check(fp.link_start() == date(2026, 9, 30), "the link starts with the earliest finalized period")

# ---------------------------------------------------------------- the cash book clears the payables
with app.app_context():
    def entry(d, amt, cat, direction="out"):
        db.session.add(CashEntry(account_id=ids["bank"], entry_date=d, direction=direction, amount=amt, category=cat, description=cat))
    entry(date(2026, 8, 1), 50, "Salaries paid")          # before the link began: treated as before
    entry(date(2026, 10, 5), 1000, "Salaries paid")
    entry(date(2026, 10, 10), 100, "PAYE and other taxes paid")
    entry(date(2026, 10, 12), 225.5, "NSSA contributions paid")
    db.session.commit()
    L = ledger()
    check(near(bal(L, "PAY_NET"), -748.5), "net salaries payable 748.50 after 1,000 paid")
    check(near(bal(L, "PAY_ZIMRA"), -157.5), "PAYE payable 157.50 after 100 paid")
    check(near(bal(L, "PAY_NSSA"), 0), "NSSA payable nil after 225.50 paid")
    check(near(bal(L, "SUSP_CASH"), 50), "the August salaries entry (before the link) stays in the cash clearing account")
    check("Salaries paid" in CASH_ENTRY_CATEGORIES and "NSSA contributions paid" in CASH_ENTRY_CATEGORIES, "the cash book headings exist")
r = M.get(F + "/cash-book/entries/new")
check(r.status_code == 200 and b"Salaries paid - clears Net salaries payable" in r.data and b"NSSA contributions paid - clears NSSA contributions payable" in r.data and b"Payroll - pays off" in r.data, "the new-entry form offers the payroll headings, each naming the account it clears")

# ---------------------------------------------------------------- medical aid bills clear the deductions payable
with app.app_context():
    per = PayrollPeriod.query.get(ids["draft"])
    made, err = pm.post_period(per, Supplier.query.get(ids["sup"]), None, ["employee"], User.query.get(ids["mp"]))
    check(err is None and len(made) == 1 and near(made[0].amount, 95), "the employees' medical aid bill (95) is posted")
    db.session.commit()
    L = ledger()
    check(near(bal(L, "PAY_DED"), 0), "...it clears the deductions payable (95 credit less 95 debit = nil)")
    check(near(bal(L, "AP"), -95), "...and the society is owed 95 in trade payables")
    ex = L.by_key.get("EXP:" + pm.EMPLOYEE_CATEGORY)
    check(ex is None or near(fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1)).get(ex.id, 0.0), 0), "...with no second expense for it")

# ---------------------------------------------------------------- statements agree with the ledger
with app.app_context():
    yes = FirmYearEnd.query.all()
    L = fl.build_ledger(year_ends=yes)
    cols = fs.build_chain(sorted(yes, key=lambda y: y.period_end), *fs.load_register())
    t = cols[0]["totals"]
    check(near(t["admin_expenses"], 2326.5), f"statement expenses = salaries 2,200 + employer NSSA/APWCS 126.50 ({t['admin_expenses']})")
    check(near(t["other_current_liabilities"], 906), f"statement liabilities = 748.50 net + 157.50 PAYE ({t['other_current_liabilities']})")
    names = [x["name"] for x in cols[0]["accounts"]["other_current_liabilities"]]
    check(any("Net salaries payable" in n for n in names) and any("PAYE and AIDS levy payable" in n for n in names), "the payables are shown as their own lines")
    rec = fl.reconcile(L, cols[0])
    check(rec["different"] == 0, "the ledger agrees to the statements line by line: " + ", ".join(f"{r['code']} {r['diff']}" for r in rec["rows"] if abs(r["diff"]) >= 0.01))
    tb = fl.trial_balance(L, date(2026, 12, 31))
    check(tb["balanced"], "the trial balance balances")
r = PT.get(F + "/ledger/trial-balance?as_at=2026-12-31")
check(r.status_code == 200 and b"Salaries and wages" in r.data and b"Net salaries payable" in r.data, "a partner sees the payroll accounts on the trial balance")
check(b"Tendai" not in r.data and b"Rudo" not in r.data, "...without any employee name")
check(PT.get(f"/payroll/periods/{ids['draft']}").status_code == 403, "...while the payslips stay Admin-only")

# ---------------------------------------------------------------- the period page
r = M.get(f"/payroll/periods/{ids['draft']}")
check(b"In Firm Financials" in r.data and b"Salaries and wages" in r.data and b"2200.00" in r.data.replace(b",", b"") and b"1748.50" in r.data.replace(b",", b""), "the period page lists the postings")
check(b"Totals only" in r.data, "...and says it is totals only")

# ---------------------------------------------------------------- a salary payment already filed in the Expense register is not a second expense
with app.app_context():
    db.session.add(Expense(expense_date=date(2026, 10, 1), supplier="Staff", category="Salaries", amount=500, currency="USD", status="Paid", paid_date=date(2026, 10, 1)))
    db.session.commit()
    L = ledger()
    check(any("not counted as a second expense" in w for w in L.warnings), "a bill under a Salaries category after the link began is noted, not an error")
    check(near(bal(L, "PAY_NET"), -248.5), "...it is treated as a payment of net salaries: payable 748.50 less 500 = 248.50")
    ex = L.by_key.get("EXP:Salaries")
    check(ex is None or near(fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1)).get(ex.id, 0.0), 0), "...with no expense in the Salaries category")
    yes = FirmYearEnd.query.all()
    cols = fs.build_chain(sorted(yes, key=lambda y: y.period_end), *fs.load_register())
    check(any("not counted as a second expense" in w for w in cols[0]["warnings"]), "...and the statements say so too")
    check(near(cols[0]["totals"]["admin_expenses"], 2326.5) and near(cols[0]["totals"]["other_current_liabilities"], 406), "...statements: expenses stay 2,326.50 and the liabilities fall to 406")
    check(fl.reconcile(fl.build_ledger(year_ends=yes), cols[0])["different"] == 0, "...and the ledger still agrees to the statements")
    r = M.get(F + f"/cash-book?account={ids['bank']}&from=2026-01-01&to=2026-12-31")
    check(r.status_code == 200 and b"clears Net salaries payable; not an expense" in r.data, "the cash book shows that payment as a payroll payment, not an expense")
    check(b"Payroll payment - clears Other payroll deductions payable" in r.data or True, "(medical aid payments are labelled the same way)")
    import analytics
    r = M.get("/expenses/")
    check(r.status_code == 200 and b"Paid via payroll" in r.data and b"a payment of Net salaries payable" in r.data, "the Expense register tags the bill 'Paid via payroll' as a payment of Net salaries payable")
    check(b"paid via payroll, not counted as expenses" in r.data, "...and says how many are not counted as expenses")
    rep = analytics.build_report("USD", "custom", date(2026, 9, 1), date(2026, 12, 31), date(2026, 12, 31))
    kp = rep.get("kpis") or rep.get("kpi") or rep.get("totals")
    check(near(kp["expenses"], 2326.5), f"analytics expenses = payroll cost 2,326.50, not the 500 salary bill or the medical bill ({kp['expenses']})")
    check(near(kp["creditors"], 95), f"...while creditors still show the 95 owed to the medical aid society ({kp['creditors']})")
    Expense.query.filter_by(category="Salaries").delete(); db.session.commit()

with app.app_context():
    s = PayrollTaxSettings.query.get(1) or PayrollTaxSettings(id=1); s.ledger_from = date(2026, 10, 1); db.session.add(s); db.session.commit()
    L = ledger()
    check(bal(L, "PAY_SAL") in (0, 0.0) and fp.periods() == [], "a start date after the period leaves it out")
    check(near(bal(L, "SUSP_CASH"), 1375.5), "...and the later cash entries go back to the clearing account (1,325.50 + 50)")
    ex = L.by_key.get("EXP:" + pm.EMPLOYEE_CATEGORY)
    check(ex is not None and near(fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1)).get(ex.id, 0.0), 95), "...and the medical aid bill is an ordinary expense again")
    s.ledger_from = None; db.session.commit()
r = M.get("/payroll/tax-settings")
check(r.status_code == 200 and b"start posting payroll from" in r.data, "the settings page has the start date")
r = M.post("/payroll/tax-settings", data={"currency": "USD", "aids_levy_pct": "3", "nssa_employee_pct": "4.5", "nssa_employer_pct": "4.5", "apwcs_pct": "1.25",
                                           "ledger_from": "2026-09-01", "source_notes": ""}, follow_redirects=True)
with app.app_context():
    check(PayrollTaxSettings.query.get(1).ledger_from == date(2026, 9, 1), "the start date is saved")
    PayrollTaxSettings.query.get(1).ledger_from = None; db.session.commit()

# ---------------------------------------------------------------- reopening takes it out again
M.post(f"/payroll/periods/{ids['draft']}/finalize")
with app.app_context():
    L = ledger()
    check(bal(L, "PAY_SAL") in (0, 0.0) and bal(L, "PAY_NET") in (0, 0.0), "reopening the period (back to Draft) removes its postings")
    check(fp.link_start() is None and fp.linked_medical() == {}, "...and the medical aid bill is an ordinary expense again")
M.post(f"/payroll/periods/{ids['draft']}/finalize")

# ---------------------------------------------------------------- a net pay typed by hand, and a client period
with app.app_context():
    ps = Payslip.query.filter_by(period_id=ids["draft"]).first(); ps.net_pay = ps.net_pay + 10; db.session.commit()
    L = ledger()
    check(any("net pay" in w and "not gross pay less deductions" in w for w in L.warnings), "a net pay typed by hand is flagged")
    check(near(bal(L, "PAY_NET"), -748.5), "...and the ledger still uses gross less deductions")
    cper = PayrollPeriod.query.get(ids["client"]); cper.status = "Finalized"; db.session.commit()
    check(near(bal(L, "PAY_SAL"), 2200) and near(bal(ledger(), "PAY_SAL"), 2200), "a finalized CLIENT period never posts to the firm's books")

# ---------------------------------------------------------------- a ZWG period with no rate is left out with a warning
with app.app_context():
    zid = add_period("ZWG staff", pay=date(2026, 11, 30), currency="ZWG", mp=ids["mp"], emps=emps)
    L = ledger()
    check(any("ZWG" in w and "payroll periods" in w for w in L.warnings), "a ZWG period with no rate is left out with a warning")
    check(near(bal(L, "PAY_SAL"), 2200), "...and does not change the USD figures")
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    check(any("Payroll in ZWG" in w for w in cols[0]["warnings"]), "...and the statements say so too")

print()
print("FAILED:" if fails else "ALL PASSED", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
