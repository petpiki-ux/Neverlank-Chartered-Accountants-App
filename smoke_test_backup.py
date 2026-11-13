"""Backups: daily automatic backup logic, Back up now, download, delete, restore (with a safety backup), retention, failure reporting."""
import os, sys, tempfile, zipfile, sqlite3, json, shutil
DATA = tempfile.mkdtemp(prefix="nl_")
SCRATCH = tempfile.mkdtemp(prefix="nl_scratch_")
os.environ["DATA_DIR"] = DATA
os.environ["BACKUP_KEEP_MANUAL"] = "2"
os.environ["BACKUP_KEEP_DAILY"] = "3"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import datetime, timedelta
from app import create_app
from extensions import db
from models import *
import backup as bk
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c

with app.app_context():
    db.create_all()
    ids = {}
    BASE_USERS = User.query.count()
    for u, role in (("adm", "admin"), ("ptn", "partner"), ("stf", "staff")):
        x = User(username=u, name=u.upper(), role=role); x.set_password("x"); db.session.add(x); db.session.commit(); ids[u] = x.id
os.makedirs(os.path.join(DATA, "uploads"), exist_ok=True); os.makedirs(os.path.join(DATA, "policies_data"), exist_ok=True)
open(os.path.join(DATA, "uploads", "contract.pdf"), "wb").write(b"%PDF-1.4 contract")
open(os.path.join(DATA, "policies_data", "p.txt"), "w").write("policy")
A, P, S = login(ids["adm"]), login(ids["ptn"]), login(ids["stf"])

# ---- the schedule logic
check(not bk.start_scheduler(app), "the background thread does not start inside the test scripts")
t = datetime(2026, 10, 28, 1, 30)
check(bk.slot_before(t) == datetime(2026, 10, 28, 0, 0) and bk.slot_before(datetime(2026, 10, 28, 0, 0)) == datetime(2026, 10, 28, 0, 0), "the daily slot is 00:00 UTC (02:00 Harare)")
with app.app_context():
    check(bk.is_due(t), "no backup yet: one is due")
    bk._write_status(last_auto_success="2026-10-28T00:10:00")
    check(not bk.is_due(t) and not bk.is_due(datetime(2026, 10, 28, 23, 0)), "backed up after today's slot: not due again today")
    check(bk.is_due(datetime(2026, 10, 29, 0, 5)), "next day's slot passed: due")
    bk._write_status(last_auto_success="2026-10-27T00:10:00")
    check(bk.is_due(t), "a missed day (the app was restarting): due straight away")
    st = os.path.join(bk.BACKUP_DIR, "status.json"); os.remove(st)

# ---- a backup
with app.app_context():
    r = bk.make_backup(app, "manual", "Tester")
    check(r["ok"] and r["name"].endswith("_manual.zip") and r["has_db"], "a manual backup is made: %s" % r.get("error", ""))
    path = os.path.join(bk.BACKUP_DIR, r["name"])
    z = zipfile.ZipFile(path); names = z.namelist()
    check("database/neverlank.db" in names and "backup_info.json" in names and "README.txt" in names, "the zip holds the database, backup_info.json and a README")
    check("files/uploads/contract.pdf" in names and "files/policies_data/p.txt" in names, "uploaded files and library files are included under files/")
    check(not any(n.startswith("files/backups") for n in names) and not any(n.endswith("neverlank.db") and n.startswith("files/") for n in names), "backups are not nested in backups, and the live database file is not copied twice")
    snap = os.path.join(SCRATCH, "chk.db"); open(snap, "wb").write(z.read("database/neverlank.db"))
    con = sqlite3.connect(snap); n = con.execute("select count(*) from user").fetchone()[0]; ic = con.execute("pragma integrity_check").fetchone()[0]; con.close()
    check(n == BASE_USERS + 3 and ic == "ok", "the copy of the database opens, is intact and has all the users")
    lst = bk.list_backups()
    check(len(lst) == 1 and lst[0]["kind"] == "manual" and lst[0]["by"] == "Tester" and lst[0]["integrity"] == "ok" and lst[0]["file_count"] >= 2, "the backup is listed with who, kind, check and file count")
    check(bk.health()[0] == "warn", "a manual backup alone does not satisfy 'automatic backup has run'")
    ra = bk.make_backup(app, "auto", "automatic")
    check(ra["ok"] and bk.health()[0] == "ok" and not bk.is_due(), "an automatic backup makes the status healthy and not due")

# ---- retention
with app.app_context():
    import time
    for i in range(4):
        time.sleep(1.1); bk.make_backup(app, "manual", "T")
    man = [b for b in bk.list_backups() if b["kind"] == "manual"]
    check(len(man) == 2, "only the newest 2 manual backups are kept (setting): %d" % len(man))
    check(len([b for b in bk.list_backups() if b["kind"] == "auto"]) == 1, "pruning one kind leaves the others")
    for i in range(4):
        time.sleep(1.1); bk.make_backup(app, "auto", "automatic")
    check(len([b for b in bk.list_backups() if b["kind"] == "auto"]) == 3, "only the newest 3 automatic backups are kept (setting)")
    check(not [f for f in os.listdir(bk.BACKUP_DIR) if f.endswith(".partial") or f.startswith("nlbackup_") or f == "backup.lock"], "no temporary files are left behind")

# ---- failure is reported, then cleared
with app.app_context():
    real = shutil.disk_usage
    shutil.disk_usage = lambda p: type("U", (), {"free": 1000, "total": 10**9, "used": 10**9})()
    rf = bk.make_backup(app, "auto", "automatic")
    shutil.disk_usage = real
    check(not rf["ok"] and "Not enough free space" in rf["error"], "too little disk space: refused with a clear reason")
    lvl, msg = bk.health()
    check(lvl == "bad" and "failed" in msg, "the status turns to 'bad'")
h = A.get("/backup/").data.decode()
check("The last backup failed" in h and 'class="badge overdue"' in h and "!" in h, "the page and the menu badge show the failure")
r = A.post("/backup/now", follow_redirects=True)
h = r.data.decode()
check("Backup made" in h and "The last backup failed" not in h, "Back up now works and clears the failure")

# ---- the backups never crowd out the live data
with app.app_context():
    before = len(bk.list_backups())
    one = max(b["size"] for b in bk.list_backups())
    real = shutil.disk_usage
    shutil.disk_usage = lambda p: type("U", (), {"free": 10**12, "total": int(one * 4 / 0.40), "used": 0})()      # a disk where 40% = 4 backups
    bk.prune()
    shutil.disk_usage = real
    left = bk.list_backups()
    check(len(left) < before and sum(b["size"] for b in left) <= one * 4 * 1.01, "backups over 40%% of the disk are trimmed, oldest first (%d -> %d)" % (before, len(left)))
    check(len([b for b in left if b["kind"] == "auto"]) >= 2, "…but the newest automatic backups are always kept")
    shutil.disk_usage = lambda p: type("U", (), {"free": 10**12 - 1, "total": 10**12, "used": 1})()
    shutil.disk_usage = real
    # free space after the backup would be too little spare room: refused
    shutil.disk_usage = lambda p: type("U", (), {"free": 30 * 1024 * 1024, "total": 10**9, "used": 10**9})()
    rr = bk.make_backup(app, "manual", "T")
    shutil.disk_usage = real
    check(not rr["ok"] and "spare" in rr["error"], "a backup that would leave the live app no spare room on the disk is refused")
    bk.make_backup(app, "auto", "automatic")

# ---- not enough room for the files: the database alone is backed up, with a warning
with app.app_context():
    big = os.path.join(DATA, "uploads", "big.pdf"); open(big, "wb").write(os.urandom(30 * 1024 * 1024))
    check(bk.estimate_zip_bytes([(big, "uploads/big.pdf")], 0) > 29 * 1024 * 1024, "the estimate treats a PDF as already compressed")
    check(bk.estimate_zip_bytes([(os.path.join(DATA, "uploads", "x.txt"), "x")], 10 * 10**6) < 6 * 10**6, "…and a database as compressible")
    real = shutil.disk_usage
    shutil.disk_usage = lambda p: type("U", (), {"free": 22 * 1024 * 1024, "total": 10**8, "used": 0})()
    full = bk.make_backup(app, "manual", "T", "full")
    ra = bk.make_backup_auto(app, "auto", "automatic")
    shutil.disk_usage = real
    check(not full["ok"] and "Not enough free space" in full["error"], "a full backup is refused when the files will not fit")
    check(ra["ok"] and ra.get("scope") == "database" and ra["has_db"], "the scheduled backup falls back to the database alone: %s" % ra.get("error", ""))
    lvl, msg = bk.health()
    check(lvl == "warn" and "database was backed up" in msg, "…and warns that the files were not included")
    last = [b for b in bk.list_backups() if b["kind"] == "auto"][0]
    check(last["scope"] == "database" and last["has_db"], "the list shows it as database only")
    zz = zipfile.ZipFile(os.path.join(bk.BACKUP_DIR, last["name"]))
    check("database/neverlank.db" in zz.namelist() and not any(n.startswith("files/") for n in zz.namelist()), "its zip holds the database and no files")
    os.remove(big)
    full = bk.make_backup(app, "auto", "automatic", "full")
    check(full["ok"] and bk.health()[0] == "ok" and "database only" not in bk.health()[1], "a later full backup clears the warning")
    bd = bk.disk_breakdown()
    check(bd and bd[0][1] >= bd[-1][1] and any(l.startswith("uploads") for l, _ in bd), "the disk breakdown lists folders, biggest first")
h = A.post("/backup/now", data={"scope": "database"}, follow_redirects=True).data.decode()
check("the database only" in h, "the page can make a database-only backup on request")
h = A.get("/backup/").data.decode()
check("What is using the disk" in h and "database only" in h, "the page shows what is using the disk")

# ---- who can use it
check(S.get("/backup/").status_code == 403 and S.post("/backup/now").status_code == 403, "staff cannot open or run backups")
check(P.get("/backup/").status_code == 200 and "Backup" in P.get("/backup/").data.decode(), "a partner can")
check("Restore from this backup" not in P.get("/backup/").data.decode() and "Restore from this backup" in A.get("/backup/").data.decode(), "only an admin sees Restore")
with app.app_context():
    name = [b for b in bk.list_backups() if b["kind"] == "manual"][0]["name"]
d = P.get(f"/backup/download/{name}")
check(d.status_code == 200 and d.data[:2] == b"PK", "a backup can be downloaded as a zip")
check(P.get("/backup/download/../../etc/passwd").status_code == 404 and P.get("/backup/download/evil.zip").status_code == 404, "only real backup names can be downloaded")

# ---- restore
with app.app_context():
    keep = bk.make_backup(app, "manual", "T")["name"]
    x = User(username="late", name="Late", role="staff"); x.set_password("x"); db.session.add(x); db.session.commit()
    check(User.query.count() == BASE_USERS + 4, "(a user is added after the backup)")
os.remove(os.path.join(DATA, "uploads", "contract.pdf"))
r = A.post(f"/backup/restore/{keep}", data={"confirm": "restore"}, follow_redirects=True)
with app.app_context():
    check(User.query.count() == BASE_USERS + 4, "wrong confirmation word: nothing restored")
check(P.post(f"/backup/restore/{keep}", data={"confirm": "RESTORE"}).status_code == 403, "a partner cannot restore")
r = A.post(f"/backup/restore/{keep}", data={"confirm": "RESTORE"}, follow_redirects=True)
with app.app_context():
    check(User.query.count() == BASE_USERS + 3 and User.query.filter_by(username="late").first() is None, "database restored: the later user is gone")
    check(User.query.filter_by(username="adm").first() is not None, "…and the data from the backup is back")
    pre = [b for b in bk.list_backups() if b["kind"] == "pre-restore"]
    check(len(pre) == 1, "a pre-restore backup of the state before was made")
    z = zipfile.ZipFile(os.path.join(bk.BACKUP_DIR, pre[0]["name"])); snap = os.path.join(SCRATCH, "pre.db"); open(snap, "wb").write(z.read("database/neverlank.db"))
    con = sqlite3.connect(snap); check(con.execute("select count(*) from user where username='late'").fetchone()[0] == 1, "…and it still holds the user added after the backup (so a restore can be undone)"); con.close()
check(not os.path.exists(os.path.join(DATA, "uploads", "contract.pdf")), "files are not touched unless asked")
check("Restored from" in r.data.decode() or "sign in" in r.data.decode().lower(), "after restoring the person is sent to sign in")
A = login(ids["adm"])
r = A.post(f"/backup/restore/{keep}", data={"confirm": "RESTORE", "with_files": "1"}, follow_redirects=True)
check(os.path.exists(os.path.join(DATA, "uploads", "contract.pdf")) and open(os.path.join(DATA, "uploads", "contract.pdf"), "rb").read().startswith(b"%PDF"), "with files ticked, the deleted upload comes back")

# ---- a hostile zip cannot write outside the data folder
with app.app_context():
    evil = os.path.join(bk.BACKUP_DIR, "neverlank_backup_2026-01-01_0000_manual.zip")
    good = zipfile.ZipFile(os.path.join(bk.BACKUP_DIR, keep))
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("database/neverlank.db", good.read("database/neverlank.db"))
        z.writestr("files/../escaped.txt", "x"); z.writestr("files/backups/neverlank_backup_2026-02-02_0000_manual.zip", "x")
        z.writestr("files/uploads/ok.txt", "fine")
    res = bk.restore_backup(app, os.path.basename(evil), True, "T")
    check(res["ok"] and os.path.exists(os.path.join(DATA, "uploads", "ok.txt")), "a normal file in a hand-made zip is restored")
    check(not os.path.exists(os.path.join(os.path.dirname(DATA), "escaped.txt")) and not os.path.exists(os.path.join(DATA, "escaped.txt")), "a '..' path in a zip is ignored")
    check(not os.path.exists(os.path.join(bk.BACKUP_DIR, "neverlank_backup_2026-02-02_0000_manual.zip")), "nothing is written into the backups folder")
    junk = os.path.join(bk.BACKUP_DIR, "neverlank_backup_2026-01-02_0000_manual.zip"); open(junk, "wb").write(b"not a zip")
    check(not bk.restore_backup(app, os.path.basename(junk), False, "T")["ok"], "a damaged file is refused, nothing changed")
    check(not bk.restore_backup(app, "../../x.zip", False, "T")["ok"], "a bad name is refused")

# ---- delete
with app.app_context():
    nm = [b for b in bk.list_backups() if b["kind"] == "manual"][0]["name"]
r = P.post(f"/backup/delete/{nm}", follow_redirects=True)
with app.app_context():
    check(nm not in [b["name"] for b in bk.list_backups()] and not os.path.exists(os.path.join(bk.BACKUP_DIR, nm[:-4] + ".json")), "a backup and its info file can be deleted")
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"); sys.exit(1 if fails else 0)
