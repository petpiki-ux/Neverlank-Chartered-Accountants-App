"""Notes: movement in deferred tax and in shareholder loans; money boxes (JS) are covered separately."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datetime as dtm
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Penanel Trading (Private) Limited", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25", type="Audit", partner_id=admin.id, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e); db.session.commit()
    lines = [TrialBalanceLine(account_name="Cash", fs_category="cash", current_debit=9000000, prior_debit=3000000),
             TrialBalanceLine(account_name="Shareholder loan - J Moyo", fs_category="long_term_borrowings", current_credit=5000000, prior_credit=2000000),
             TrialBalanceLine(account_name="Shareholder loan - T Dube", fs_category="long_term_borrowings", current_credit=2236756, prior_credit=634538),
             TrialBalanceLine(account_name="Deferred tax liability", fs_category="deferred_tax_liability", current_credit=126925, prior_credit=64128),
             TrialBalanceLine(account_name="Share capital", fs_category="share_capital", current_credit=100, prior_credit=100),
             TrialBalanceLine(account_name="Retained earnings", fs_category="other_reserves", current_credit=1636319, prior_credit=301234)]
    st = fin.build_all_statements(lines, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    notes = {n["title"]: n for n in st["notes"]}
    dt = notes["Deferred tax liabilities"]["extra_tables"][0]
    check(dt["rows"] == [["Balance at beginning of year", 64128], ["Charge to profit or loss", 62797], ["Balance at end of year", 126925]], "deferred tax roll-forward: %s" % dt["rows"])
    import client_notes
    client_notes.attach_client_notes(st, e)
    ll = notes["Long-term borrowings"]["loan_layout"]
    L = lambda lay: [(x["label"], x["cur"], x["pri"], x["style"]) for x in lay["lines"]]
    check(ll["headers"] == ["2025 USD", "2024 USD"], "loan headers: %s" % ll["headers"])
    check(L(ll) == [("Shareholder Loans", None, None, "head"),
                    ("Shareholder loan - J Moyo", None, None, "head"), ("Opening Balance", 2000000.0, None, "row"), ("Shareholder Funding", 3000000.0, None, "row"), ("", 5000000.0, 2000000.0, "sub"), ("", None, None, "gap"),
                    ("Shareholder loan - T Dube", None, None, "head"), ("Opening Balance", 634538.0, None, "row"), ("Shareholder Funding", 1602218.0, None, "row"), ("", 2236756.0, 634538.0, "sub"), ("", None, None, "gap"),
                    ("Total shareholder loans", None, None, "head"), ("Opening Balance", 2634538.0, None, "row"), ("Shareholder Funding", 4602218.0, None, "row"), ("", 7236756.0, 2634538.0, "total")], "one block per shareholder, then the total: %s" % L(ll))
    check(ll["replaces_breakdown"] and ll["after"] == client_notes.DEFAULT_LOAN_TERMS, "loan terms default sentence")
    cl.reporting_currency = "zwg"; cl.shareholder_loan_terms = "Repayable on demand."; st["_client_notes_done"] = False
    client_notes.attach_client_notes(st, e, prior_opening={"shareholder loan - j moyo": 1000000.0, "shareholder loan - t dube": 100000.0})
    ll = notes["Long-term borrowings"]["loan_layout"]
    rows = {(x["label"], i): x for i, x in enumerate(ll["lines"])}
    tot = ll["lines"][-3:]
    check(tot[0]["pri"] == 1100000.0 and tot[1]["pri"] == 1534538.0 and ll["headers"][0] == "2025 ZWG" and ll["after"] == "Repayable on demand.", "loan layout with prior openings per shareholder, currency and terms: %s" % L(ll)[-3:])
    # one loan account: the layout of the firm's own working
    one = [TrialBalanceLine(account_name="Shareholder loan", fs_category="long_term_borrowings", current_credit=7236756, prior_credit=2634538),
           TrialBalanceLine(account_name="Cash", fs_category="cash", current_debit=7236756, prior_debit=2634538)]
    st1 = fin.build_all_statements(one, [], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    client_notes.attach_client_notes(st1, e, prior_opening={"shareholder loan": 4461277.0})
    l1 = [n for n in st1["notes"] if n["title"] == "Long-term borrowings"][0]["loan_layout"]
    check(L(l1) == [("Shareholder Loans", None, None, "head"), ("Opening Balance", 2634538.0, 4461277.0, "row"), ("Shareholder Funding", 4602218.0, -1826739.0, "row"), ("", 7236756.0, 2634538.0, "total")], "single loan account layout: %s" % L(l1))
    # a previous-year engagement supplies the prior-year opening balance
    e0 = Engagement(client_id=cl.id, title="FY24", type="Audit", partner_id=admin.id, period_end=dtm.date(2024, 12, 31), reporting_framework="ifrs_for_smes")
    db.session.add(e0); db.session.commit()
    tb0 = TrialBalance(engagement_id=e0.id); db.session.add(tb0); db.session.commit()
    db.session.add(TrialBalanceLine(trial_balance_id=tb0.id, account_name="Shareholder loan", fs_category="long_term_borrowings", current_credit=2634538)); db.session.commit()
    check(client_notes.prior_owner_loan_openings(e) == {"shareholder loan": 2634538.0} and client_notes.prior_owner_loan_opening(e) == 2634538.0, "prior-year opening comes from the previous engagement's trial balance")
    cl.reporting_currency = None; cl.shareholder_loan_terms = None
    cl.authorised_shares = 2000; cl.issued_shares = 2000; cl.share_par_value = 12.75; cl.share_class = "Ordinary"
    st["_client_notes_done"] = False
    client_notes.attach_client_notes(st, e, prior_opening=None)
    sc = notes["Share capital"]["share_layout"]
    labels = [x["label"] for x in sc["lines"] if x["label"]]
    check(labels == ["Authorised:", "2,000 Ordinary Shares of $12.75", "Issued and fully paid:", "2,000 Ordinary Shares of $12.75 issued"], "share capital labels: %s" % labels)
    amts = [(x["cur"], x["pri"]) for x in sc["lines"] if x["style"] == "double"]
    check(amts == [(100, 100), (100, 100)], "share capital amounts from the trial balance when authorised = issued")
    check(sc["check"] is True, "mismatch between shares x nominal value and the trial balance is flagged")
    cl.authorised_shares = 5000; st["_client_notes_done"] = False
    client_notes.attach_client_notes(st, e, prior_opening=None)
    check([x for x in notes["Share capital"]["share_layout"]["lines"] if x["style"] == "double"][0]["cur"] == 63750, "authorised amount = shares x nominal value when it exceeds issued")
    cl.authorised_shares = None; st["_client_notes_done"] = False
    del notes["Share capital"]["share_layout"]
    client_notes.attach_client_notes(st, e, prior_opening=None)
    check("share_layout" not in notes["Share capital"], "no share particulars -> plain account list")
    check(all(not n.get("extra_tables") for t, n in notes.items() if t in ("Cash and cash equivalents", "Share capital")), "other notes have no extra tables")
    cl.authorised_shares = 2000; cl.issued_shares = 2000; cl.share_par_value = 12.75; cl.share_class = "Ordinary"; st["_client_notes_done"] = False
    notes["Share capital"].pop("share_layout", None)
    d = DocxDocument(wp.build_financial_statements_docx(e, st, None))
    txt = "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    check("Movement in deferred tax liability" in "\n".join(p.text for p in d.paragraphs) and "Balance at beginning of year" in txt and "62,797" in txt, "Word pack shows the deferred tax movement")
    check("Shareholder Loans" in txt and "Shareholder Funding" in txt and "4,602,218" in txt and "Opening Balance" in txt and "Shareholder loan - J Moyo" in txt and "Total shareholder loans" in txt, "Word pack shows a block per shareholder and the total")
    check("The loan is unsecured, interest free and has no fixed repayment terms." in "\n".join(p.text for p in d.paragraphs), "loan terms sentence printed under the loan table")
    check("Authorised:" in txt and "Issued and fully paid:" in txt and "2,000 Ordinary Shares of $12.75 issued" in txt, "Word pack shows the share capital layout")
    check("w:val=\"double\"" in d.element.body.xml, "double underline under the totals")
# ---- an account that is only in an audit adjustment is traceable to its journal
with app.app_context():
    class _L:
        def __init__(self, n, c, d, cr): self.account_name, self.fs_category, self.debit, self.credit = n, c, d, cr
    class _A:
        reference = "AJE 4"; applies_to = "current"; carry_to_current = True
        lines = [_L("Shareholder Loan", "long_term_borrowings", 39680.0, 0.0), _L("Cash", "cash", 0.0, 39680.0)]
    one = [TrialBalanceLine(account_name="Cash", fs_category="cash", current_debit=100000, prior_debit=1),
           TrialBalanceLine(account_name="Share capital", fs_category="share_capital", current_credit=100000, prior_credit=1)]
    stj = fin.build_all_statements(one, [_A()], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    nb = [n for n in stj["notes"] if n["title"] == "Long-term borrowings"][0]
    row = nb["accounts"][0]
    check(row["account_name"] == "Shareholder Loan" and row["current"] == -39680 and row["from_tb"] is False and row["adj"][0]["ref"] == "AJE 4",
          "a line that exists only in a journal says so and names the journal: %s" % row)
    check(nb["accounts"] and all(a["from_tb"] for a in [r for r in [n for n in st["notes"] if n["title"] == "Share capital"][0]["accounts"]]), "trial balance lines are marked as such")
# ---- a shareholder loan journal adjusts the shareholder loan account (and Shareholder Funding), not a line of its own
with app.app_context():
    class _A2:
        reference = "AJE 5"; applies_to = "current"; carry_to_current = True
        lines = [_L("Shareholder Loan", "other_current_liabilities" if False else "trade_payables", 39680.0, 0.0), _L("Cash", "cash", 0.0, 39680.0)]
    base = [TrialBalanceLine(account_name="Shareholder Loan Account", fs_category="long_term_borrowings", current_credit=7276436, prior_credit=2634538),
            TrialBalanceLine(account_name="Bank loan", fs_category="long_term_borrowings", current_credit=500000, prior_credit=0),
            TrialBalanceLine(account_name="Cash", fs_category="cash", current_debit=7776436, prior_debit=2634538)]
    st3 = fin.build_all_statements(base, [_A2()], reporting_framework="ifrs_for_smes", cash_flow_method="indirect", whole_numbers=True)
    import client_notes as cn
    cn.attach_client_notes(st3, e, prior_opening={"shareholder loan account": 2634538.0})
    nb = [n for n in st3["notes"] if n["title"] == "Long-term borrowings"][0]
    names = {a["account_name"]: a["current"] for a in nb["accounts"]}
    check(names == {"Shareholder Loan Account": 7236756.0, "Bank loan": 500000.0}, "the journal is on the shareholder loan account, not a line of its own: %s" % names)
    lines3 = nb["loan_layout"]["lines"]
    check([x["label"] for x in lines3] == ["Shareholder Loans", "Opening Balance", "Shareholder Funding", ""] and lines3[2]["cur"] == 4602218 and lines3[3]["cur"] == 7236756 and not nb["loan_layout"]["replaces_breakdown"],
          "Shareholder Funding is net of the journal: %s" % [(x["label"], x["cur"]) for x in lines3])
    check(not [n for n in st3["notes"] if n["title"] == "Trade and other payables"], "nothing is left on the journal's own category")
import financials as _fin
_t = _fin.ppe_accounting_policy_text([{"name": "MOTOR VEHICLES", "depreciation_method": "straight-line", "rate_percent": 25, "useful_life_years": 4},
                                      {"name": "COMPUTER SOFTWARE & EQUIPMENT", "depreciation_method": "straight-line"},
                                      {"name": "FURNITURE AND FITTINGS", "depreciation_method": "straight-line"}])
check([r[0] for r in _t.parts["table"]["rows"]] == ["Motor Vehicles"] and "Furniture" not in str(_t), "policy table leaves out classes with no rate and no useful life")
_t2 = _fin.ppe_accounting_policy_text([{"name": "A", "depreciation_method": "straight-line"}, {"name": "B", "depreciation_method": "straight-line"}])
check(len(_t2.parts["table"]["rows"]) == 2, "if no class has a rate or life, the table is kept")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
