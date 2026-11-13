"""A database made BEFORE the Suppliers engine existed (expense table without supplier_id, no supplier tables) must be upgraded on startup,
and the Expense register, the cash book and the analytics must keep working on the old bills."""
import os, subprocess, sys, tempfile, sqlite3
HERE = os.path.dirname(os.path.abspath(__file__))
data = tempfile.mkdtemp(prefix="nl_up_")
env = dict(os.environ, DATA_DIR=data)
def run(code):
    return subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=HERE, env=env, capture_output=True, text=True)
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

r = run("from app import create_app; a = create_app(); print('ok')")
check(r.returncode == 0, "a fresh database starts")
db_path = os.path.join(data, "instance", "neverlank.db")
con = sqlite3.connect(db_path)
con.execute("DROP TABLE supplier_payment"); con.execute("DROP TABLE supplier")
# rebuild the table as it was before (SQLite cannot drop a column that has a foreign key)
cols = [c for c in con.execute("pragma table_info(expense)") if c[1] != "supplier_id"]
ddl = ", ".join(f'"{c[1]}" {c[2]}' + (" PRIMARY KEY" if c[5] else "") + (" NOT NULL" if c[3] and not c[5] else "") for c in cols)
con.execute("PRAGMA foreign_keys=OFF")
con.execute("DROP TABLE expense"); con.execute(f"CREATE TABLE expense ({ddl})")
con.execute("INSERT INTO expense (expense_date, supplier, category, amount, currency, status) VALUES ('2026-01-05', 'Old Landlord', 'Rent & premises', 500, 'USD', 'Unpaid')")
con.commit(); con.close()
con = sqlite3.connect(db_path)
check("supplier_id" not in {c[1] for c in con.execute("pragma table_info(expense)")}, "the old database has no supplier_id on expenses")
con.close()
code = ("from app import create_app; a = create_app()\n"
        "from models import *\nimport suppliers as sp, analytics, cashbook\n"
        "with a.app_context():\n"
        "    e = Expense.query.first(); print(e.supplier, e.supplier_id, sp.outstanding(e), Supplier.query.count(), SupplierPayment.query.count())\n"
        "    print(len(analytics._expense_rows('USD')), len(cashbook.collect_transactions()))\n"
        "    c, l = sp.import_from_register(); db.session.commit(); print(c, l, Expense.query.first().supplier_id)\n")
r = run(code)
if r.returncode != 0: print(r.stderr[-1200:])
check(r.returncode == 0 and "Old Landlord None 500.0 0 0" in r.stdout, "the app starts on the old database; the old bill is read and still owed")
check("1 0" in r.stdout.splitlines()[1] or r.stdout.splitlines()[1].startswith("1 "), "analytics and the cash book read the old bills")
check(r.stdout.strip().splitlines()[-1] == "1 1 1", "the old bill can be turned into a supplier and linked")
con = sqlite3.connect(db_path)
check("supplier_id" in {c[1] for c in con.execute("pragma table_info(expense)")}, "supplier_id was added to expenses")
check(con.execute("select count(*) from supplier").fetchone()[0] == 1, "the supplier tables exist")
con.close()
# a database from the first Suppliers release (supplier_payment without advance_id, no advance tables) is upgraded too
con = sqlite3.connect(db_path)
con.execute("PRAGMA foreign_keys=OFF")
cols = [c for c in con.execute("pragma table_info(supplier_payment)") if c[1] != "advance_id"]
ddl = ", ".join(f'"{c[1]}" {c[2]}' + (" PRIMARY KEY" if c[5] else "") + (" NOT NULL" if c[3] and not c[5] else "") for c in cols)
con.execute("DROP TABLE supplier_payment"); con.execute(f"CREATE TABLE supplier_payment ({ddl})")
con.execute("DROP TABLE supplier_advance_refund"); con.execute("DROP TABLE supplier_advance")
con.commit(); con.close()
r = run("from app import create_app; a = create_app()\nfrom models import *\nimport suppliers as sp\nwith a.app_context():\n"
        "    print(SupplierPayment.query.count(), SupplierAdvance.query.count(), SupplierAdvanceRefund.query.count(), sp.overview()['advances'])")
if r.returncode != 0: print(r.stderr[-1200:])
check(r.returncode == 0 and r.stdout.strip().endswith("0 0 0 {}"), "the app starts on a first-release Suppliers database and the advance tables can be used")
con = sqlite3.connect(db_path)
check("advance_id" in {c[1] for c in con.execute("pragma table_info(supplier_payment)")}, "advance_id was added to supplier_payment")
con.close()
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
