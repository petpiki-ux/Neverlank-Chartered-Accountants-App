"""Financial statements for a period that is not twelve months (Broxmen 2026: 1 January to 31 October, ten months): the engagement's period start,
the headings, column heading, Note 1, the basis of preparation and the opening/addition split of the asset register."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_o = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _o(self, *a, **k)
FlaskClient.open = _open
import datetime as dtm
from docx import Document as DocxDocument
from app import create_app
from extensions import db
from models import *
import financials as fin
import workpapers as wp
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

from datetime import date as D
import client_notes
check(fin.period_phrase(D(2026,1,1), D(2026,10,31)) == "10 months ended 31 October 2026", "ten whole months: '10 months ended 31 October 2026'")
check(fin.period_phrase(None, D(2026,10,31)) == "year ended 31 October 2026" and fin.period_phrase(D(2025,11,1), D(2026,10,31)) == "year ended 31 October 2026", "no period start, or a full year: 'year ended'")
check(fin.period_phrase(D(2025,3,15), D(2026,3,14)) == "year ended 14 March 2026", "15 March to 14 March is a year")
check(fin.period_phrase(D(2026,1,5), D(2026,10,31)) == "period from 5 January 2026 to 31 October 2026", "an odd period reads 'period from ... to ...'")
check(fin.period_phrase(D(2025,7,1), D(2026,10,31)) == "16 months ended 31 October 2026", "a long period is named too")
check(fin.period_column_label(D(2026,1,1), D(2026,10,31)) == "2026 (10 months)" and fin.period_column_label(None, D(2026,10,31)) == "2026", "column heading year part")
check(fin.short_period_basis_sentence(D(2025,11,1), D(2026,10,31)) == "", "no disclosure for a normal year")
with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Broxmen Investment (Private) Limited", created_by_id=aid, reporting_currency="USD"); db.session.add(cl); db.session.commit(); cid = cl.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(aid); s["_fresh"] = True
form = {"client_id": str(cid), "title": "FY2026", "type": "Audit", "partner_id": str(aid), "manager_id": str(aid), "period_end": "2026-10-31", "period_start": "2026-01-01",
        "start_date": "2026-11-01", "status": "Planning", "reporting_framework": "full_ifrs", "acceptance_required": ""}
r = c.post("/engagements/new", data=form, follow_redirects=False)
with app.app_context():
    e = Engagement.query.filter_by(title="FY2026").first()
    check(e is not None and e.period_start == D(2026,1,1) and e.period_end == D(2026,10,31), "the new-engagement form saves the period start: %s" % (e and e.period_start))
    eid = e.id
page = c.get(f"/engagements/{eid}/edit").get_data(as_text=True)
check('name="period_start"' in page and 'value="2026-01-01"' in page, "the edit form shows the period start")
r = c.post(f"/engagements/{eid}/edit", data={**form, "period_start": "2026-12-01"}, follow_redirects=True)
with app.app_context():
    check(Engagement.query.get(eid).period_start is None, "a period start that is not before the period end is not saved")
r = c.post(f"/engagements/{eid}/edit", data=form, follow_redirects=True)
with app.app_context():
    e = Engagement.query.get(eid)
    check(e.period_start == D(2026,1,1), "saved again on edit")
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for code, nm, cat, cd, cc in [("1641", "Plant", "ppe", 150000, 0), ("4001", "Capital", "share_capital", 0, 150000)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=code, account_name=nm, fs_category=cat, current_debit=cd, current_credit=cc, prior_debit=cd, prior_credit=cc))
    db.session.commit()
    hd = client_notes.year_headers(e, e.client)
    check(hd[0] == "2026 (10 months) USD" and hd[1] == "2025 USD", "column headings: %s" % hd)
    check("for the 10 months ended 31 October 2026." in fin.general_information_note(e.client, e), "Note 1 says: for the 10 months ended 31 October 2026")
    b = fin.default_basis_of_preparation("full_ifrs", None, e.period_start, e.period_end)
    check("10 months from 1 January 2026 to 31 October 2026" in b and "not entirely comparable" in b, "basis of preparation carries the short-period disclosure")
    lines = list(TrialBalance.query.filter_by(engagement_id=eid).first().lines)
    st = fin.build_all_statements(lines, [], reporting_framework="full_ifrs")
    import cover_pages, workpapers as wp
    from docx import Document as Dx
    d = Dx(wp.build_financial_statements_docx(e, st, None))
    text = "\n".join(p.text for p in d.paragraphs)
    check("for the 10 months ended 31 October 2026" in text.lower() or "10 months ended 31 October 2026" in text, "the Word pack's statement headings and cover say 10 months ended")
    check("year ended 31 October 2026" not in text, "and no longer 'year ended 31 October 2026'")
    hdrs = []
    for sec in d.sections:
        hdrs += [p.text for p in sec.header.paragraphs]
    check(any("10 months ended 31 October 2026" in h for h in hdrs), "the notes page header says: For the 10 months ended 31 October 2026")
    cells = " ".join(c_.text.replace("\n", " ") for t in d.tables for r_ in t.rows for c_ in r_.cells)
    check("2026 (10 months)" in cells, "the statements' column heading reads 2026 (10 months)")
    # asset register: an asset bought on 31 Dec 2025 is an OPENING balance of a period that starts on 1 Jan 2026 (a 12-month guess would call it an addition)
    import asset_schedules as sched
    ps, pe, as_at = sched.bounds(e)
    check(ps == D(2026,1,1) and as_at == D(2025,12,31), "the asset register's period starts on the engagement's period start")
    a1 = PPEAsset(engagement_id=eid, description="Bought Dec 2025", date_acquired=D(2025,12,31), cost=100); a2 = PPEAsset(engagement_id=eid, description="Bought Mar 2026", date_acquired=D(2026,3,1), cost=50)
    o, add, later = sched.split_assets([a1, a2], ps, pe)
    check(o == [a1] and add == [a2], "bought 31 Dec 2025 = opening balance, bought 1 Mar 2026 = addition")
    op = fin.default_audit_opinion_paragraphs("audit", "unmodified", "Broxmen", e.period_end, "IFRS", e.period_start)
    check(any("for the 10 months then ended" in str(v) for v in op.values()), "the auditor's opinion says: for the 10 months then ended")
    check(any("for the year then ended" in str(v) for v in fin.default_audit_opinion_paragraphs("audit", "unmodified", "X", e.period_end, "IFRS").values()), "and still 'for the year then ended' for twelve months")
    e.period_start = None; db.session.commit()
    ps2, _, _ = sched.bounds(e)
    check(ps2 == D(2025,11,1), "without a period start the twelve months before the period end are assumed, as before")
    text2 = "\n".join(p.text for p in Dx(wp.build_financial_statements_docx(e, fin.build_all_statements(lines, [], reporting_framework="full_ifrs"), None)).paragraphs)
    check("year ended 31 October 2026" in text2 and "10 months" not in text2, "a 12-month engagement still reads 'year ended'")
print("\nALL PASSED" if not fails else "\nFAILED: %d" % len(fails)); sys.exit(1 if fails else 0)
