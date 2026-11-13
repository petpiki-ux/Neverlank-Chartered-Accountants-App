"""Main bankers on the client page (form, saved, shown) and the thousands-separator script on every page."""
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
from app import create_app
from extensions import db
from models import *
import financials as fin
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(aid); s["_fresh"] = True
    r = c.get("/clients/new")
    check(r.status_code == 200 and 'name="bankers"' in r.get_data(as_text=True), "new-client form has the Main bankers box")
    r = c.post("/clients/new", data={"name": "Bankers Test Ltd", "bankers": "CBZ Bank\nStanbic Bank"}, follow_redirects=True)
    cl = Client.query.filter_by(name="Bankers Test Ltd").first()
    check(cl is not None and cl.bankers == "CBZ Bank\nStanbic Bank", "bankers saved on create")
    html = c.get(f"/clients/{cl.id}").get_data(as_text=True)
    check("Main bankers:" in html and "CBZ Bank; Stanbic Bank" in html, "client page shows the bankers on one line")
    r = c.post(f"/clients/{cl.id}/edit", data={"name": "Bankers Test Ltd", "bankers": "FBC Bank"}, follow_redirects=True)
    check(Client.query.get(cl.id).bankers == "FBC Bank", "bankers edited")
    check("FBC Bank" in c.get(f"/clients/{cl.id}/edit").get_data(as_text=True), "edit form shows the saved bankers")
    check("js/money-inputs.js" in html, "thousands-separator script is on every page")
    check(fin.bankers_list(Client.query.get(cl.id)) == ["FBC Bank"], "bankers_list splits the lines")
    cl.bankers = "A Bank; B Bank\nC Bank"
    check(fin.bankers_list(cl) == ["A Bank", "B Bank", "C Bank"], "bankers split on ; and newline")
    import datetime as dtm
    e = Engagement(client_id=cl.id, title="x", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes"); db.session.add(e); db.session.commit()
    note = fin.general_information_note(cl, e)
    check("A Bank" in note and "C Bank" in note, "note 1 names the bankers")

    # share particulars and loan terms from the client form
    r = c.post(f"/clients/{cl.id}/edit", data={"name": "Bankers Test Ltd", "bankers": "FBC Bank", "reporting_currency": "zwg", "share_class": "Ordinary",
               "authorised_shares": "2,000", "issued_shares": "2000", "share_par_value": "12.75", "shareholder_loan_terms": "Unsecured and interest free."}, follow_redirects=True)
    cc = db.session.get(Client, cl.id)
    check((cc.reporting_currency, cc.authorised_shares, cc.issued_shares, cc.share_par_value, cc.share_class) == ("ZWG", 2000, 2000, 12.75, "Ordinary"), "share particulars saved")
    check("Unsecured and interest free." in c.get(f"/clients/{cl.id}/edit").get_data(as_text=True), "loan terms shown on the edit form")
    # the Finalisation tab draws the share capital and loan layouts
    tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.commit()
    for nm, cat, dr, cr, pdr, pcr in [("Cash", "cash", 9000000, 0, 3000000, 0), ("Shareholder loan", "long_term_borrowings", 0, 7236756, 0, 2634538),
                                      ("Share capital", "share_capital", 0, 339040, 0, 339040), ("Retained earnings", "other_reserves", 0, 1424204, 0, 26422)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=nm, fs_category=cat, current_debit=dr, current_credit=cr, prior_debit=pdr, prior_credit=pcr))
    db.session.commit()
    page = c.get(f"/engagements/{e.id}?tab=finalisation").get_data(as_text=True)
    check("Issued and fully paid:" in page and "2,000 Ordinary Shares of ZWG 12.75 issued" in page, "Finalisation tab shows the share capital note layout")
    check("Shareholder Funding" in page and "Unsecured and interest free." in page and "2025\nZWG" in page, "Finalisation tab shows the shareholder loan layout")
print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
