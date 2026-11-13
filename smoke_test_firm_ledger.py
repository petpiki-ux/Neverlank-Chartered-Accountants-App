"""Firm Finance > Ledger: double entry derived from the app's records, manual journals with draft/post/reverse controls and a
period lock, trial balance, ledger by account, and agreement of the ledger to the financial statements - checked against
figures worked out by hand."""
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
import firm_statements as fs
import firm_ledger as fl
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def near(a, b, tol=0.005):
    return a is not None and abs(a - b) <= tol

# ------------------------------------------------------------------ the scenario
with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    cl = Client(name="Acme Holdings (Pvt) Ltd", created_by_id=partner.id)
    db.session.add(cl); db.session.commit()
    invA = Invoice(invoice_number="INV-A", client_id=cl.id, currency="USD", vat_pct=15.0, status="Sent", issue_date=date(2025, 5, 5), due_date=date(2025, 5, 5))
    invA.lines.append(InvoiceLineItem(description="Audit fee", quantity=1, unit_price=5000)); db.session.add(invA)
    invB = Invoice(invoice_number="INV-B", client_id=cl.id, currency="USD", vat_pct=0.0, status="Sent", issue_date=date(2026, 3, 1), due_date=date(2026, 3, 1))
    invB.lines.append(InvoiceLineItem(description="Tax fee", quantity=1, unit_price=2000)); db.session.add(invB)
    db.session.commit()
    db.session.add(Receipt(receipt_number="RCT-1", invoice_id=invA.id, amount=2000, currency="USD", paid_on=date(2025, 6, 1), method="Bank transfer"))
    db.session.add(Receipt(receipt_number="RCT-2", invoice_id=invB.id, amount=2000, currency="USD", paid_on=date(2026, 4, 1), method="Bank transfer"))
    cn = CreditNote(credit_note_number="CN-1", invoice_id=invA.id, issue_date=date(2025, 9, 1), currency="USD", vat_pct=15.0, reason="Discount")
    cn.lines.append(CreditNoteLine(description="Discount", quantity=1, unit_price=200)); db.session.add(cn)
    adv = ClientAdvance(advance_number="ADV-1", client_id=cl.id, currency="USD", amount=1000, received_on=date(2025, 7, 1), method="Bank transfer")
    db.session.add(adv); db.session.commit()
    app_ = ClientAdvanceApplication(advance_id=adv.id, invoice_id=invA.id, amount=500, applied_on=date(2025, 8, 1))
    db.session.add(app_); db.session.commit()
    db.session.add(Receipt(receipt_number="RCT-AP", invoice_id=invA.id, amount=500, currency="USD", paid_on=date(2025, 8, 1), advance_application_id=app_.id))
    def exp(d, sup, cat, amt, paid=True, cur="USD"):
        db.session.add(Expense(expense_date=d, supplier=sup, category=cat, amount=amt, currency=cur, status="Paid" if paid else "Unpaid", paid_date=d if paid else None))
    exp(date(2025, 2, 1), "Landlord", "Rent & premises", 1200)
    exp(date(2025, 3, 10), "Airline", "Travel & transport", 400, paid=False)
    exp(date(2026, 2, 1), "Landlord", "Rent & premises", 600)
    exp(date(2026, 9, 1), "Harare supplier", "Rent & premises", 500, paid=False, cur="ZWG")
    comp = FirmAssetClass(name="Computer equipment", depreciation_method="Straight-line", rate_percent=25.0, order=0)
    db.session.add(comp); db.session.commit()
    db.session.add(FirmAsset(asset_code="FA-0001", description="Laptop", asset_class_id=comp.id, date_acquired=date(2025, 3, 15), cost=1200.0))
    bank = CashAccount(name="Bank (USD)", kind="Bank", currency="USD", opening_balance=100.0, opening_date=date(2025, 1, 1), default_in=True, default_out=True)
    db.session.add(bank); db.session.commit()
    for d, direction, amt, cat in ((date(2025, 1, 10), "in", 5000, "Owner / shareholder contribution"),
                                   (date(2026, 6, 25), "out", 100, "QPD (provisional tax) paid"),
                                   (date(2026, 3, 31), "out", 50, "Income tax paid")):
        db.session.add(CashEntry(account_id=bank.id, entry_date=d, direction=direction, amount=amt, category=cat, description="entry"))
    db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, bank=bank.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(ids["partner"]), login(ids["staff"])
F = "/firm-finance"

# staff cannot reach any ledger page or action
for path in ("/ledger", "/ledger/journal/new", "/ledger/accounts", "/ledger/account", "/ledger/trial-balance", "/ledger/trial-balance.xlsx", "/ledger/reconcile", "/ledger/lock"):
    check(S.get(F + path).status_code == 403, f"staff cannot open {path}")
check(S.post(F + "/ledger/lock", data={"locked_through": "2025-12-31"}).status_code == 403, "staff cannot close the books")
check(S.post(F + "/ledger/accounts/save", data={"code": "9999", "name": "x", "account_type": "Asset"}).status_code == 403, "staff cannot add an account")

# year-end 1 (manual tax, opening nil) with the figures the app cannot know
P.post(F + "/year-ends/new", data={"period_end": "2025-12-31", "zwg_rate": "", "opening_nil": "1"})
with app.app_context():
    y1 = FirmYearEnd.query.filter_by(period_end=date(2025, 12, 31)).first()
    y1.tax_manual = True; db.session.commit(); y1 = y1.id
for cat, desc, amt in (("share_capital", "Share capital", 5000), ("long_term_borrowings", "Director loan", 1200),
                       ("other_current_liabilities", "VAT payable", 720), ("retained_earnings", "Retained earnings b/f", 100)):
    P.post(F + f"/year-ends/{y1}/lines/add", data={"fs_category": cat, "description": desc, "amount": str(amt), "currency": "USD"})

r = P.get(F + "/ledger")
check(r.status_code == 200 and b"No journals yet" in r.data, "the journals page opens empty")
with app.app_context():
    n = FirmAccount.query.count()
    check(n >= 55, f"the starter chart was created ({n} accounts)")
    for key in ("AR", "AP", "ADV", "RE", "TAX_PAY", "DTL", "DTA", "DEPR", "PPE_COST", "PPE_ACCDEP", "SUSP_OPEN", "SUSP_CASH", "SUSP_ASSET", "CLR_TRANSFER", "VAT_CTRL", "DISP_GL"):
        if not FirmAccount.query.filter_by(system_key=key).first():
            check(False, f"system account {key} exists")
    check(FirmAccount.query.filter_by(system_key=f"CASH:{ids['bank']}").first().fs_category == "cash", "a ledger account was created for the cash book account")
    check(FirmAccount.query.filter_by(system_key="EXP:Travel & transport").first() is not None, "an account exists for each expense category")
    check(FirmAccount.query.filter_by(system_key="REV:" + "Not linked to an engagement").first() is not None, "an account exists for the service invoiced")
    check(len({a.code for a in FirmAccount.query.all()}) == FirmAccount.query.count(), "account codes are unique")

# ------------------------------------------------------------------ derived postings at year-end 1, by hand
with app.app_context():
    yes = FirmYearEnd.query.all()
    L = fl.build_ledger(year_ends=yes)
    byk = {k: a.id for k, a in L.by_key.items()}
    bal = fl.account_balances(L, date(2025, 12, 31), date(2025, 1, 1))
    def b(key): return round(bal.get(byk[key], 0.0), 2)
    def bg(key): return round(sum(v for k, v in ((k, bal.get(i, 0.0)) for k, i in byk.items()) if k == key or k.startswith(key + ":")), 2)   # a parent and its asset-class sub-accounts
    check(near(b("AR"), 3020), "receivables 3,020 = invoice 5,750 - receipt 2,000 - advance applied 500 - credit note 230")
    check(near(b("REV:Not linked to an engagement"), -4800), "fees 4,800 = 5,000 less the 200 credit note (credit)")
    check(near(b("VAT_CTRL"), -720), "VAT on invoices 720 = 750 less 30 on the credit note (credit)")
    check(near(b("AP"), -400), "trade payables 400 = the unpaid travel bill (1,200 rent was paid)")
    check(near(b("ADV"), -500), "customer advances 500 = 1,000 received - 500 applied")
    check(near(b(f"CASH:{ids['bank']}"), 6900), "bank 6,900 = 100 opening + 5,000 contribution + 2,000 receipt + 1,000 advance - 1,200 rent")
    check(near(b("EXP:Rent & premises"), 1200) and near(b("EXP:Travel & transport"), 400), "expenses 1,200 rent and 400 travel")
    check(near(bg("PPE_COST"), 1200) and near(bg("PPE_ACCDEP"), -250) and near(b("DEPR"), 250), "laptop cost 1,200, depreciation 250 (10 months at 25%)")
    subs = [k for k in byk if k.startswith("PPE_COST:")]
    check(len(subs) >= 1 and near(b(subs[0]), 1200) and near(b("PPE_COST"), 0), "the laptop's class has its own cost sub-account under 1500, and the parent holds none of it")
    sub = L.by_key[subs[0]]
    check(sub.parent_id == L.by_key["PPE_COST"].id and sub.code.startswith(L.by_key["PPE_COST"].code + "-"), "the sub-account is numbered under its parent")
    check(near(b("SUSP_ASSET"), -1200), "the laptop's funding sits in its clearing account (credit 1,200)")
    check(near(b("SUSP_CASH"), -5000), "the owner contribution sits in the cash entries clearing account (credit 5,000)")
    check(near(b("SUSP_OPEN"), 6920), "entered figures 7,020 less the cash opening balance 100 sit in the opening clearing account (debit 6,920)")
    check(near(b("RE"), -100), "opening retained earnings 100 is posted to retained earnings")
    check(near(sum(l["dr"] for l in L.lines), sum(l["cr"] for l in L.lines)), "every debit has a credit: total debits equal total credits")
    bad = 0
    per = {}
    for l in L.lines:
        per.setdefault(l["seq"], 0.0); per[l["seq"]] += l["dr"] - l["cr"]
    check(all(abs(v) < 0.005 for v in per.values()), "every single posting balances on its own")
    tb = fl.trial_balance(L, date(2025, 12, 31))
    check(tb["balanced"] and near(tb["debit"], 19890) and near(tb["credit"], 19890), "trial balance totals 19,890 each side")
    check(near(sum(r["debit"] - r["credit"] for r in tb["suspense"]), 0), "the clearing accounts net to nil because the statements balance")
    cols = fs.build_chain(sorted(yes, key=lambda y: y.period_end), *fs.load_register())
    t = cols[0]["totals"]
    check(near(t["revenue"], 4800) and near(t["admin_expenses"], 1600) and near(t["trade_receivables"], 3020) and near(t["cash"], 6900), "statement totals as expected before any journal")
    rec = fl.reconcile(L, cols[0])
    check(rec["different"] == 0, "the ledger agrees to the statements line by line: " + ", ".join(f"{r['code']} {r['diff']}" for r in rec["rows"] if abs(r["diff"]) >= 0.01))
    gl = fl.general_ledger(L, L.by_key["AR"], date(2025, 1, 1), date(2025, 12, 31))
    check([round(x["dr"] - x["cr"], 2) for x in gl["lines"]] == [5750, -2000, -500, -230] and near(gl["closing"], 3020), "ledger for receivables: +5,750, -2,000, -500, -230 = 3,020")
    rv = fl.general_ledger(L, L.by_key["REV:Not linked to an engagement"], date(2026, 1, 1), date(2026, 12, 31))
    check(near(rv["opening"], 0), "an income account opens at nil in the new financial year")

# pages render
for path, needle in (("/ledger/trial-balance", b"19,890.00"), ("/ledger/accounts", b"Chart of accounts"), ("/ledger/reconcile", b"Ledger agreed to the statements"),
                     ("/ledger/lock", b"Close the books"), ("/ledger/journal/new", b"Post to the ledger"), ("/ledger/account", b"Choose an account")):
    r = P.get(F + path + (f"?ye={y1}" if "journal/new" not in path else ""))
    check(r.status_code == 200 and needle in r.data, f"{path} opens")
r = P.get(F + "/ledger/trial-balance?date=2025-06-30")
check(r.status_code == 200 and b"2025" in r.data, "trial balance at any date opens")
r = P.get(F + f"/ledger/account?account={ids['bank']}&ye={y1}")
check(r.status_code == 200, "ledger page with an account id that is not a ledger account still opens")
with app.app_context():
    arid = FirmAccount.query.filter_by(system_key="AR").first().id
r = P.get(F + f"/ledger/account?account={arid}&ye={y1}")
check(r.status_code == 200 and b"INV-A" in r.data and b"3,020.00" in r.data, "the receivables ledger shows its invoice and closing balance")
r = P.get(F + f"/ledger/trial-balance.xlsx?ye={y1}")
check(r.status_code == 200 and r.data[:2] == b"PK", "trial balance downloads as Excel")
import io, openpyxl
wb = openpyxl.load_workbook(io.BytesIO(r.data))
ws = wb["Trial balance"]
tot = [row for row in ws.iter_rows(values_only=True) if row[1] == "Total"][0]
check(near(tot[4], 19890) and near(tot[5], 19890) and "Postings" in wb.sheetnames, "the Excel file has the trial balance (19,890) and the postings")

# ------------------------------------------------------------------ journals
with app.app_context():
    acc = {k: FirmAccount.query.filter_by(system_key=k).first().id for k in ("EXP:Travel & transport", "AR", "TAX_PAY")}
    acc["accruals"] = FirmAccount.query.filter_by(code="2010").first().id
    acc["bank"] = FirmAccount.query.filter_by(system_key=f"CASH:{ids['bank']}").first().id
def jpost(extra=None, **kw):
    data = {"journal_date": "2025-12-31", "description": "Accrue travel", "reference": "ACC-1",
            "line_account": [acc["EXP:Travel & transport"], acc["accruals"]], "line_debit": ["300", ""], "line_credit": ["", "300"], "line_narration": ["", ""]}
    data.update(kw)
    return P.post(F + "/ledger/journal/new", data=data, follow_redirects=True)

r = jpost(line_credit=["", "250"], action="post")
check(b"does not balance" in r.data, "an unbalanced journal cannot be posted")
with app.app_context():
    check(FirmJournal.query.count() == 0, "nothing was saved when the post was refused")
r = jpost(line_credit=["", "250"], action="save")
check(b"saved as a draft" in r.data, "an unbalanced journal can be saved as a draft")
with app.app_context():
    d1 = FirmJournal.query.first()
    check(d1.status == "draft" and d1.number == "JE-2025-0001" and not d1.is_balanced, "the draft is numbered JE-2025-0001 and is not balanced")
    d1id = d1.id
r = P.post(F + f"/ledger/journal/{d1id}/post", follow_redirects=True)
check(b"does not balance" in r.data, "a draft that does not balance cannot be posted from its page")
with app.app_context():
    L2 = fl.build_ledger()
    check(not any(l["kind"] == "journal" for l in L2.lines), "a draft is not in the ledger")
r = P.post(F + f"/ledger/journal/{d1id}/edit", data={"journal_date": "2025-12-31", "description": "Accrue travel", "reference": "ACC-1",
           "line_account": [acc["EXP:Travel & transport"], acc["accruals"]], "line_debit": ["300", ""], "line_credit": ["", "300"], "line_narration": ["", ""], "action": "post"}, follow_redirects=True)
check(b"posted to the ledger" in r.data, "the draft was corrected and posted")
with app.app_context():
    j = FirmJournal.query.get(d1id)
    check(j.status == "posted" and j.posted_by_id == ids["partner"] and j.posted_at is not None and j.created_by_id == ids["partner"], "a posted journal records who prepared and posted it, and when")
r = P.post(F + f"/ledger/journal/{d1id}/edit", data={"journal_date": "2025-12-31", "description": "changed", "action": "save"}, follow_redirects=True)
check(b"never changed" in r.data, "a posted journal cannot be edited")
r = P.post(F + f"/ledger/journal/{d1id}/delete", follow_redirects=True)
check(b"cannot be deleted" in r.data, "a posted journal cannot be deleted")
with app.app_context():
    check(FirmJournal.query.get(d1id) is not None, "the posted journal is still there")

# the posted journal reaches the statements and the ledger, and the two still agree
with app.app_context():
    yes = FirmYearEnd.query.all()
    cols = fs.build_chain(sorted(yes, key=lambda y: y.period_end), *fs.load_register())
    t = cols[0]["totals"]
    check(near(t["admin_expenses"], 1900) and near(t["trade_payables"], 700), "the accrual lifts expenses to 1,900 and payables to 700 in the statements")
    check(not any("Journals - " in x["name"] for x in cols[0]["accounts"]["trade_payables"]) and cols[0]["accounts"]["trade_payables"], "the journal sits on its account line, not on a separate Journals line")
    L = fl.build_ledger(year_ends=yes)
    rec = fl.reconcile(L, cols[0])
    check(rec["different"] == 0, "ledger still agrees to the statements with the journal")
    check(near(fs.column_profit(t)[1], 2650), "profit for the year falls to 2,650 (4,800 - 1,900 - 250)")
    check(near(sum(r["debit"] - r["credit"] for r in rec["suspense"]), 0), "clearing accounts still net to nil")
    tb = fl.trial_balance(L, date(2025, 12, 31))
    check(tb["balanced"] and near(tb["debit"], 20190), "trial balance still balances (20,190 each side)")

# refusals: cash accounts and app-posted accounts
r = jpost(line_account=[acc["bank"], acc["accruals"]], line_debit=["50", ""], line_credit=["", "50"], action="post")
check(b"Cash Book" in r.data, "a journal to a cash account is refused (use the Cash Book)")
r = jpost(line_account=[acc["TAX_PAY"], acc["accruals"]], line_debit=["50", ""], line_credit=["", "50"], action="post")
check(b"posted by the app" in r.data, "a journal to an app-posted tax account is refused")
r = jpost(line_debit=["300", "5"], line_credit=["", "300"], action="post")
with app.app_context():
    check(FirmJournal.query.count() == 1, "refused journals leave nothing behind")
r = jpost(line_account=[acc["EXP:Travel & transport"], ""], line_debit=["50", ""], line_credit=["", ""], action="post")
check(b"at least two lines" in r.data or b"choose an account" in r.data.lower() or b"does not balance" in r.data, "a one-sided journal is refused")
r = jpost(line_account=[acc["EXP:Travel & transport"], acc["accruals"]], line_debit=["30", "40"], line_credit=["", "30"], action="post")
check(b"not both" in r.data or b"does not balance" in r.data, "a line cannot carry both a debit and a credit")

# control account warning
with app.app_context():
    arj = FirmJournal(number="JE-2025-0099", journal_date=date(2025, 12, 31), description="x", status="draft")
    arj.lines.append(FirmJournalLine(account_id=acc["AR"], debit=1, credit=0)); arj.lines.append(FirmJournalLine(account_id=acc["accruals"], debit=0, credit=1))
    db.session.add(arj); db.session.flush()
    check(len(fl.journal_warnings(arj)) == 1, "a journal to a control account (receivables) carries a warning")
    db.session.rollback()

# reversal
r = P.post(F + f"/ledger/journal/{d1id}/reverse", data={"reverse_date": "2026-01-02"}, follow_redirects=True)
check(b"reversed by" in r.data, "the journal was reversed")
with app.app_context():
    orig = FirmJournal.query.get(d1id)
    rev = orig.reversed_by
    check(rev is not None and rev.reverses_id == orig.id and rev.status == "posted" and rev.journal_date == date(2026, 1, 2), "a posted reversal is dated as chosen and linked to the original")
    check([(l.account_id, l.debit, l.credit) for l in rev.lines] == [(l.account_id, l.credit, l.debit) for l in orig.lines], "the reversal swaps every debit and credit")
    revid = rev.id
r = P.post(F + f"/ledger/journal/{d1id}/reverse", data={"reverse_date": "2026-01-03"}, follow_redirects=True)
check(b"has not been reversed" in r.data, "a journal cannot be reversed twice")
r = P.post(F + f"/ledger/journal/{revid}/reverse", data={"reverse_date": "2026-01-03"}, follow_redirects=True)
check(b"has not been reversed" in r.data, "a reversal cannot itself be reversed")
with app.app_context():
    check(FirmJournal.query.count() == 2, "two journals: the original and its reversal")
    cols = fs.build_chain(sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end), *fs.load_register())
    check(near(cols[0]["totals"]["admin_expenses"], 1900), "year-end 1 still carries the accrual: the reversal is dated in the next period")

# ------------------------------------------------------------------ closing the books
r = P.post(F + "/ledger/lock", data={"locked_through": "2025-12-31", "note": "Year 1 signed off", "action": "lock"}, follow_redirects=True)
check(b"closed through 31 Dec 2025" in r.data, "the books were closed through 31 Dec 2025")
r = jpost(action="post")
check(b"books are closed" in r.data, "a journal dated in a closed period is refused")
r = jpost(action="save")
check(b"books are closed" in r.data, "even a draft cannot be dated in a closed period")
r = jpost(journal_date="2026-01-15", description="After the lock", action="post")
check(b"posted to the ledger" in r.data, "a journal dated after the closing date is accepted")
with app.app_context():
    j3 = FirmJournal.query.filter_by(description="After the lock").first()
    check(j3.number == "JE-2026-0002", "numbers restart each year and continue within it (the reversal was JE-2026-0001)")
    j3id = j3.id
r = P.post(F + f"/ledger/journal/{j3id}/reverse", data={"reverse_date": "2025-12-30"}, follow_redirects=True)
check(b"cannot be posted" in r.data and b"closed" in r.data, "a reversal cannot be dated in a closed period either")
r = P.post(F + "/ledger/lock", data={"action": "unlock", "note": "Reopened for audit adjustment"}, follow_redirects=True)
check(b"open again" in r.data, "the books can be reopened")
with app.app_context():
    hist = FirmPeriodLock.query.order_by(FirmPeriodLock.id).all()
    check(len(hist) == 2 and hist[0].locked_through == date(2025, 12, 31) and hist[1].locked_through is None and hist[0].set_by_id == ids["partner"], "the lock history records both changes and who made them")
    check(fl.current_lock() is None, "no lock is in force after reopening")

# ------------------------------------------------------------------ chart of accounts
r = P.post(F + "/ledger/accounts/save", data={"code": "6350", "name": "Training costs", "account_type": "Expense", "fs_category": "admin_expenses", "description": "staff courses"}, follow_redirects=True)
check(b"saved" in r.data, "an account was added")
r = P.post(F + "/ledger/accounts/save", data={"code": "6350", "name": "Duplicate", "account_type": "Expense", "fs_category": "admin_expenses"}, follow_redirects=True)
check(b"already used" in r.data, "a duplicate code is refused")
r = P.post(F + "/ledger/accounts/save", data={"code": "6310", "name": "Bad line", "account_type": "Expense", "fs_category": "not_a_line"}, follow_redirects=True)
check(b"Choose a statement line" in r.data, "an unknown statement line is refused")
with app.app_context():
    tr = FirmAccount.query.filter_by(code="6350").first()
    sysacc = FirmAccount.query.filter_by(system_key="AR").first()
    trid, sysid = tr.id, sysacc.id
r = P.post(F + f"/ledger/accounts/{trid}/toggle", follow_redirects=True)
with app.app_context():
    check(not FirmAccount.query.get(trid).is_active, "an own account can be switched off")
r = P.post(F + f"/ledger/accounts/{sysid}/toggle", follow_redirects=True)
check(b"cannot be switched off" in r.data, "an account the app posts to cannot be switched off")
r = P.post(F + f"/ledger/accounts/{sysid}/delete", follow_redirects=True)
check(b"cannot be deleted" in r.data, "an account the app posts to cannot be deleted")
r = P.post(F + "/ledger/accounts/save", data={"id": sysid, "code": "1100", "name": "Fees receivable", "account_type": "Liability", "fs_category": "revenue"}, follow_redirects=True)
with app.app_context():
    a = FirmAccount.query.get(sysid)
    check(a.name == "Fees receivable" and a.account_type == "Asset" and a.fs_category == "trade_receivables", "an app account can be renamed but keeps its type and statement line")
r = P.post(F + f"/ledger/accounts/{trid}/toggle")
r = jpost(line_account=[trid, acc["accruals"]], line_debit=["80", ""], line_credit=["", "80"], journal_date="2026-02-01", description="Course fees accrued", action="post")
check(b"posted to the ledger" in r.data, "a journal can use an account added by the user")
r = P.post(F + f"/ledger/accounts/{trid}/delete", follow_redirects=True)
check(b"switch it off" in r.data.lower() or b"has journals" in r.data, "an account with journals cannot be deleted")
r = P.post(F + "/ledger/accounts/sync", follow_redirects=True)
check(r.status_code == 200, "the sync button works")

# ------------------------------------------------------------------ year-end 2: automatic tax, a ZWG bill with no rate
P.post(F + "/year-ends/new", data={"period_end": "2026-12-31", "zwg_rate": ""})
with app.app_context():
    y2 = FirmYearEnd.query.filter_by(period_end=date(2026, 12, 31)).first().id
for cat, desc, amt in (("share_capital", "Share capital", 5000), ("long_term_borrowings", "Director loan", 1200), ("other_current_liabilities", "VAT payable", 720)):
    P.post(F + f"/year-ends/{y2}/lines/add", data={"fs_category": cat, "description": desc, "amount": str(amt), "currency": "USD"})
with app.app_context():
    yes = sorted(FirmYearEnd.query.all(), key=lambda y: y.period_end)
    cols = fs.build_chain(yes, *fs.load_register())
    L = fl.build_ledger(year_ends=yes)
    c2 = cols[1]
    check(c2["tax"] is not None, "year-end 2 takes its tax from the Tax pages")
    check(any("ZWG" in w and "left out" in w for w in L.warnings), "the ZWG bill with no rate is left out of the ledger with a warning")
    rec = fl.reconcile(L, c2)
    check(rec["different"] == 0, "year-end 2: ledger agrees to the statements: " + ", ".join(f"{r['code']} {r['ledger']} vs {r['stmt']}" for r in rec["rows"] if abs(r["diff"]) >= 0.01))
    tax = c2["tax"]
    ex = fl.account_balances(L, date(2026, 12, 31), date(2026, 1, 1))
    byk = {k: a.id for k, a in L.by_key.items()}
    check(near(ex.get(byk["TAX_EXP_CUR"], 0) + ex.get(byk["TAX_EXP_DEF"], 0), tax["income_tax_expense"]), "the ledger's tax expense equals the Tax pages' charge")
    check(near(-ex.get(byk["TAX_PAY"], 0), tax["payable"]), "the ledger's tax payable equals the statement's: charge less the QPD (100) and tax paid (50) in the cash book")
    tb = fl.trial_balance(L, date(2026, 12, 31))
    check(tb["balanced"], "year-end 2 trial balance balances")
    rv = fl.general_ledger(L, L.by_key["REV:Not linked to an engagement"], date(2026, 1, 1), date(2026, 12, 31))
    check(near(rv["closing"], -2000), "2026 fees 2,000 on the income account, which opened at nil")
    re_ = [r for r in tb["rows"] if r["account"] is None]
    check(len(re_) == 1 and near(re_[0]["credit"] - re_[0]["debit"], 2650), "year 1's result (2,650 after the accrual) is rolled into retained earnings")
    # the accrual was reversed on 2 Jan 2026 (credit 300) and the lock test journal re-booked it on 15 Jan (debit 300)
    check(near(ex.get(byk["EXP:Travel & transport"], 0), 0), "in 2026 the reversal (-300) and the later journal (+300) net to nil on travel")
r = P.get(F + f"/ledger/reconcile?ye={y2}")
check(r.status_code == 200 and b"Ledger agrees to the statements" in r.data, "reconciliation page for year-end 2 shows agreement")

# a journal in an automatic-tax year is refused on a tax-feeding account
with app.app_context():
    dtl = FirmAccount.query.filter_by(system_key="DTL").first()
    check(fl.line_refusal(dtl, date(2026, 6, 1)) is not None, "deferred tax is refused for journals")

# remaining pages render: a journal, its edit form (draft), the copy action, ledgers whose postings link to other records
with app.app_context():
    first = FirmJournal.query.order_by(FirmJournal.id).first().id
    exp_acc = FirmAccount.query.filter_by(system_key="EXP:Rent & premises").first().id
    cash_acc = FirmAccount.query.filter_by(system_key=f"CASH:{ids['bank']}").first().id
    rev_acc = FirmAccount.query.filter_by(system_key="REV:Not linked to an engagement").first().id
r = P.get(F + f"/ledger/journal/{first}")
check(r.status_code == 200 and b"Reverse" in r.data or b"reversed" in r.data, "a posted journal opens")
r = P.post(F + f"/ledger/journal/{first}/copy", follow_redirects=True)
check(r.status_code == 200 and b"Copied to draft" in r.data and b"Post to the ledger" in r.data, "copy to a new draft opens the editable draft")
with app.app_context():
    dr = FirmJournal.query.filter_by(status="draft").first()
    drid = dr.id
r = P.get(F + f"/ledger/journal/{drid}")
check(r.status_code == 200 and b"Delete draft" in r.data, "a draft offers edit, post and delete")
r = P.post(F + f"/ledger/journal/{drid}/delete", follow_redirects=True)
check(b"deleted" in r.data, "a draft can be deleted")
for aid, label, needle in ((exp_acc, "expenses", b"Landlord"), (cash_acc, "cash book", b"Bank (USD)"), (rev_acc, "fees", b"INV-A")):
    r = P.get(F + f"/ledger/account?account={aid}&ye={y1}")
    check(r.status_code == 200 and needle in r.data, f"the {label} ledger opens and shows its postings")
r = P.get(F + f"/ledger?status=posted&q=travel")
check(r.status_code == 200 and b"JE-2025-0001" in r.data, "the journal list filters by status and text")
r = P.get(F + "/ledger?status=draft")
check(r.status_code == 200 and b"JE-2025-0001" not in r.data, "the draft filter hides posted journals")
r = P.get(F + "/")
check(r.status_code == 200 and b"Ledger" in r.data, "the Firm Finance menu links to the ledger")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
