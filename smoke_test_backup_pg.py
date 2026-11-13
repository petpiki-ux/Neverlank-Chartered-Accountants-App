"""Backup page on PostgreSQL: the zip holds a plain-file dump of every table that can be read back. (Skipped on SQLite; run_tests.py --pg runs it.)"""
import os, sys, tempfile, zipfile, gzip, json
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
if not os.environ.get("DATABASE_URL", "").startswith("postgres"):
    print("PASS skipped: this test only applies when the app runs on PostgreSQL")
    sys.exit(0)
from app import create_app
from extensions import db
from models import *
import backup as bk, db_dump
app = create_app()
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    cl = Client(name="Dump Client", created_by_id=admin.id); db.session.add(cl); db.session.commit()
    res = bk.make_backup(app, "manual", "tester", "database")
    check(res["ok"], "a database backup is made on PostgreSQL: " + str(res.get("error", "")))
    z = zipfile.ZipFile(os.path.join(bk.backup_dir(), res["name"]))
    names = z.namelist()
    check("database/neverlank_pg.jsonl.gz" in names and "backup_info.json" in names, "the zip holds the PostgreSQL dump and backup_info.json")
    info = json.loads(z.read("backup_info.json"))
    check(info["has_db"] and info["integrity"] == "ok", "backup_info says the database is included")
    raw = gzip.decompress(z.read("database/neverlank_pg.jsonl.gz")).decode().splitlines()
    rows = [json.loads(l) for l in raw[1:]]
    check(json.loads(raw[0])["format"] == db_dump.FORMAT, "the dump starts with its format line")
    check(sum(1 for r in rows if r["t"] == "client") == Client.query.count() and sum(1 for r in rows if r["t"] == "user") == User.query.count(), "the dump holds every client and user")
    check(any(r["t"] == "client" and "Dump Client" in r["r"] for r in rows), "the new client is in the dump")
    out = bk.restore_backup(app, res["name"])
    check(not out["ok"] and "SQLite" in out["error"], "restoring from the page is refused on PostgreSQL, with an explanation")
print("\n%d failing" % len(fails))
sys.exit(1 if fails else 0)
