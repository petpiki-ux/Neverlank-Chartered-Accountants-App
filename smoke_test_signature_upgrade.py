"""An existing database made BEFORE the e-signature columns existed must be upgraded on startup (this broke the live dashboard once: a repeated key in
the upgrade table overrode the new columns for the invoice and receipt tables)."""
import os, subprocess, sys, tempfile, sqlite3
HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = ("quotation", "invoice", "credit_note", "receipt", "client_advance", "zimra_reply", "workpaper_narrative")
COLS = ("mp_signed_by_id", "mp_signed_at", "mp_signed_key")
data = tempfile.mkdtemp(prefix="nl_up_")
env = dict(os.environ, DATA_DIR=data)
def run(code):
    return subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True)
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

r = run("from app import create_app; a = create_app(); print('ok')")
check(r.returncode == 0, "a fresh database starts")
db_path = os.path.join(data, "instance", "neverlank.db")
con = sqlite3.connect(db_path)
for t in TABLES:
    for c in COLS:
        con.execute(f"ALTER TABLE {t} DROP COLUMN {c}")
con.commit(); con.close()
con = sqlite3.connect(db_path)
check(not any(c[1] in COLS for t in TABLES for c in con.execute(f"pragma table_info({t})")), "the old database has none of the signature columns")
con.close()

r = run("from app import create_app; a = create_app()\nfrom models import *\nwith a.app_context():\n"
        "    print(Invoice.query.count(), Receipt.query.count(), Quotation.query.count(), CreditNote.query.count(), ClientAdvance.query.count(), ZimraReply.query.count(), WorkpaperNarrative.query.count())")
check(r.returncode == 0 and r.stdout.strip().endswith("0 0 0 0 0 0 0"), "the app starts on the old database and every signed table can be queried")
if r.returncode != 0: print(r.stderr[-800:])
con = sqlite3.connect(db_path)
for t in TABLES:
    have = {c[1] for c in con.execute(f"pragma table_info({t})")}
    check(set(COLS) <= have, f"{t}: the signature columns were added")
con.close()
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
