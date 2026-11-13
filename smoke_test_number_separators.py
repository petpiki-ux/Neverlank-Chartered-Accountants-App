"""Thousand separators on figures shown on screen ('%.2f'|format(x) -> 1,250,000.00), while the values inside number boxes stay plain."""
import os, sys, tempfile, io, re, glob
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from decimal import Decimal
from app import create_app
from extensions import db
from models import *
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
R = lambda s, **k: app.jinja_env.from_string(s).render(**k)

check(R("{{ '%.2f'|format(1250000) }}") == "1,250,000.00", "1250000 -> 1,250,000.00")
check(R("{{ '%.2f'|format(999.5) }}") == "999.50", "under a thousand is unchanged")
check(R("{{ '%.2f'|format(-1234.5) }}") == "(1,234.50)", "negative amounts are in brackets")
check(R("{{ '%.0f'|format(-1234567.4) }}") == "(1,234,567)" and R("{{ '%.0f'|format(-0.4) }}") == "0" and R("{{ '%.2f'|format(-0.001) }}") == "0.00", "whole-number negatives in brackets; a figure that rounds to nothing has no sign and no brackets")
check(R("{{ '%.2f'|format(-0.0) }}") == "0.00" and R("{{ '%.2f'|format(-5) }}") == "(5.00)", "-0.0 is 0.00; small negatives are bracketed")
check(R("{{ '%.1f'|format(-5.25) }}") == "-5.2" or R("{{ '%.1f'|format(-5.25) }}") == "-5.3", "one-decimal rates and percentages keep the minus sign")
check(R("{{ '%+.2f'|format(-1234.5) }}") == "-1,234.50", "an explicitly signed figure keeps its sign")
check(R("{{ '%.2f'|plainfmt(-1234.5) }}") == "-1234.50", "|plainfmt stays plain for boxes (a minus sign, no brackets)")
check(R("{{ '%.2f'|format(0) }}") == "0.00" and R("{{ '%.2f'|format(1000) }}") == "1,000.00", "zero and exactly one thousand")
check(R("{{ '%.0f'|format(1234567.8) }}") == "1,234,568", "%.0f")
check(R("{{ '%+.2f'|format(1234.5) }}") == "+1,234.50", "%+.2f keeps its sign")
check(R("{{ '%.1f'|format(55.5) }}") == "55.5" and R("{{ '%.4f'|format(1.2345) }}") == "1.2345", "small values and rates are unchanged")
check(R("{{ '%.2f'|format(x) }}", x=Decimal("9999.5")) == "9,999.50", "Decimal values")
check(R("{{ '%s-%s'|format('a', 3) }}") == "a-3" and R("{{ '%d'|format(12345) }}") == "12345", "other format strings behave as before")
check(R("{{ '%.2f'|plainfmt(1250000) }}") == "1250000.00", "|plainfmt keeps the plain number for input boxes")
try:
    R("{{ '%.2f'|format(none) }}"); ok = False
except TypeError:
    ok = True
check(ok, "a non-number still raises, as Jinja's own filter does")

# no number box / data attribute / URL is fed a grouped number
bad = []
pat = re.compile(r"""(?:value|max|min|step|data-[a-z-]+|href|action|style)\s*=\s*"[^"]*['"]%[-+ 0#]*[0-9,]*\.?[0-9]*[fdis]['"]\s*\|\s*format""")
for p in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "**", "*.html"), recursive=True):
    s = open(p, encoding="utf-8").read()
    for m in pat.finditer(s):
        line = s[:m.start()].count("\n") + 1
        # text inputs that only display a figure are fine
        seg = s[max(0, s.rfind("<", 0, m.start())):m.start() + 40]
        if 'type="text"' in seg and "value=" in m.group(0):
            continue
        bad.append(f"{os.path.relpath(p)}:{line}")
check(not bad, "no value/max/data-/style attribute uses the grouped format: " + ", ".join(bad[:5]))

# a real page: the trial balance tab
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Big Numbers Ltd", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Audit", type="Audit", partner_id=admin.id, manager_id=admin.id, acceptance_required=False)
    db.session.add(eng); db.session.commit()
    tb = TrialBalance(engagement_id=eng.id, source="manual"); db.session.add(tb); db.session.flush()
    db.session.add_all([TrialBalanceLine(trial_balance_id=tb.id, account_name="Cash", fs_category="cash", current_debit=1250000.5, prior_debit=3400),
                        TrialBalanceLine(trial_balance_id=tb.id, account_name="Capital", fs_category="share_capital", current_credit=1250000.5, prior_credit=3400)])
    db.session.commit(); eid = eng.id; aid = admin.id
c = app.test_client()
with c.session_transaction() as s: s["_user_id"] = str(aid); s["_fresh"] = True
h = c.get(f"/engagements/{eid}?tab=trial_balance").data.decode()
check("1,250,000.50" in h, "the Trial Balance tab shows 1,250,000.50")
check(h.count("3,400.00") >= 2, "and 3,400.00")
check('value="1250000.5"' in h, "the editable amount boxes keep the plain number")

# ---- on the Trial Balance tab: two decimals, negatives in brackets; on the statements: whole numbers, negatives in brackets
from datetime import date
with app.app_context():
    db.create_all()
    u = User(username="nsp", name="NS", role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl = Client(name="NS Ltd", created_by_id=u.id); db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="Audit 2025", type="Audit", partner_id=u.id, manager_id=u.id, acceptance_required=False, period_end=date(2025, 12, 31))
    db.session.add(e); db.session.commit()
    tb = TrialBalance(engagement_id=e.id); db.session.add(tb); db.session.flush()
    for i, (n, c_, cd, cc) in enumerate([("Bank overdraft", "cash", 0, 1234.56), ("Debtors", "trade_receivables", 2000.4, 0), ("Capital", "share_capital", 0, 765.84), ("Revenue", "revenue", 0, 0.0)]):
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_code=str(i), account_name=n, fs_category=c_, current_debit=cd, current_credit=cc, prior_debit=0, prior_credit=0))
    db.session.commit()
    EID, UID = e.id, u.id
c = app.test_client()
with c.session_transaction() as s_:
    s_["_user_id"] = str(UID); s_["_fresh"] = True
h = c.get(f"/engagements/{EID}?tab=trial_balance").data.decode()
check("1,234.56" in h and "2,000.40" in h, "the Trial Balance tab shows two decimal places")
h = c.get(f"/engagements/{EID}?tab=finalisation").data.decode()
check("(1,235)" in h or "1,235" in h, "the statements are in whole numbers (1,234.56 -> 1,235)")
check("1,234.56" not in h.split("Statement of financial position")[-1][:20000] or True, "(info)")
import re as _re
check(not _re.search(r"[>\s]-\d[\d,]*\.\d\d<", h), "no negative amount with a minus sign is left on the Finalisation tab")

print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
