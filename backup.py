"""Backups: a daily automatic backup, "Back up now", download, and restore.

WHAT IS BACKED UP - everything the app writes, which on Render is the persistent disk (DATA_DIR, /var/data):
  * the database, copied with SQLite's online backup so it is consistent even while people are working in the app;
  * every other file under DATA_DIR (uploads, editable document templates, the policy / notice / legislation libraries, ZIMRA files...).
The code itself is in git, and the Render environment variables are not in either - keep a private copy of those.

WHERE - DATA_DIR/backups/neverlank_backup_<date>_<time>_<kind>.zip, each with a small .json file beside it (what, when, how big, whether
the copy of the database passed SQLite's integrity check). A zip holds database/neverlank.db, files/<path under DATA_DIR>, backup_info.json
and README.txt. Backups on the same disk protect against mistakes and bad imports; to survive losing the disk, download one regularly.

WHEN - a background thread checks every few minutes and backs up once a day (BACKUP_HOUR_UTC, default 00 = 02:00 in Harare), and straight
after a start-up if the last good automatic backup is older than the most recent daily slot (Render restarts the app on every deploy).
Environment: BACKUP_SCHEDULER=off turns the thread off; BACKUP_HOUR_UTC; BACKUP_KEEP_DAILY (default 14); BACKUP_KEEP_MANUAL (default 10).

RESTORE - from a backup listed on the page, by an admin who types RESTORE. A "pre-restore" backup of the current state is taken first. The
database is replaced as a whole; the files are optional and only ever added/overwritten, never deleted.
"""
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.engine import make_url

from config import DATA_DIR
from extensions import db

backup_bp = Blueprint("backup", __name__, url_prefix="/backup")

BACKUP_DIR = os.path.join(DATA_DIR, "backups")
STATUS_FILE = "status.json"
LOCK_FILE = "backup.lock"
NAME_RE = re.compile(r"^neverlank_backup_\d{4}-\d{2}-\d{2}_\d{4}(\d{2})?_(auto|manual|pre-restore)\.zip$")
KINDS = ("auto", "manual", "pre-restore")
_local_lock = threading.Lock()


def _env_int(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def keep_limits():
    return {"auto": max(1, _env_int("BACKUP_KEEP_DAILY", 14)), "manual": max(1, _env_int("BACKUP_KEEP_MANUAL", 10)), "pre-restore": 5}


def backup_hour():
    return min(23, _env_int("BACKUP_HOUR_UTC", 0))


def backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def db_file(app=None):
    """Path of the SQLite database file, or None when the app runs on another database (then the files are still backed up, the database is not)."""
    app = app or current_app
    try:
        url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    except Exception:
        return None
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    return os.path.abspath(url.database)


def _is_inside(path, folder):
    path, folder = os.path.abspath(path), os.path.abspath(folder)
    return path == folder or path.startswith(folder + os.sep)


# ---------------------------------------------------------------------------------------------------- status and listing

def _read_status():
    try:
        with open(os.path.join(backup_dir(), STATUS_FILE), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_status(**changes):
    st = _read_status()
    st.update(changes)
    try:
        tmp = os.path.join(backup_dir(), STATUS_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, os.path.join(backup_dir(), STATUS_FILE))
    except OSError:
        pass
    return st


def _parse_iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


def list_backups():
    """Every backup on the disk, newest first: [{"name", "kind", "created_at" (datetime, UTC), "size", "by", "integrity", "db_bytes",
    "file_count", "has_db"}]. A zip with no .json beside it (copied in by hand) is listed with what the file name says."""
    out = []
    try:
        names = os.listdir(backup_dir())
    except OSError:
        return out
    for n in names:
        if not NAME_RE.match(n):
            continue
        path = os.path.join(BACKUP_DIR, n)
        meta = {}
        try:
            with open(path[:-4] + ".json", "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            pass
        try:
            size = os.path.getsize(path)
            mtime = datetime.utcfromtimestamp(os.path.getmtime(path))
        except OSError:
            continue
        m = re.match(r"neverlank_backup_(\d{4}-\d{2}-\d{2})_(\d{4})", n)
        try:
            when = datetime.strptime(m.group(1) + m.group(2), "%Y-%m-%d%H%M")
        except (ValueError, AttributeError):
            when = mtime
        out.append({"name": n, "kind": meta.get("kind") or n.rsplit("_", 1)[-1][:-4], "created_at": _parse_iso(meta.get("created_at")) or when,
                    "size": size, "by": meta.get("by") or "", "integrity": meta.get("integrity"), "db_bytes": meta.get("db_bytes"),
                    "file_count": meta.get("file_count"), "has_db": meta.get("has_db", True)})
    out.sort(key=lambda b: (b["created_at"], b["name"]), reverse=True)
    return out


def human_size(n):
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} bytes" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024.0


def slot_before(now):
    """The most recent daily backup time (BACKUP_HOUR_UTC o'clock) at or before `now`."""
    s = now.replace(hour=backup_hour(), minute=0, second=0, microsecond=0)
    return s if s <= now else s - timedelta(days=1)


def is_due(now=None):
    """True when the automatic backup has not run since the most recent daily slot."""
    now = now or datetime.utcnow()
    last = _parse_iso(_read_status().get("last_auto_success"))
    return last is None or last < slot_before(now)


def health(now=None):
    """('ok' | 'warn' | 'bad', sentence) for the badge and the banner."""
    now = now or datetime.utcnow()
    st = _read_status()
    last_ok = _parse_iso(st.get("last_success"))
    if st.get("last_error") and (not last_ok or (_parse_iso(st.get("last_attempt")) or now) >= last_ok):
        return "bad", "The last backup failed: " + str(st["last_error"])
    last_auto = _parse_iso(st.get("last_auto_success"))
    if last_auto is None:
        return "warn", "No automatic backup has completed yet."
    if now - last_auto > timedelta(hours=36):
        return "warn", f"The last automatic backup was on {last_auto.strftime('%d %b %Y %H:%M')} UTC - more than a day and a half ago."
    return "ok", f"Last automatic backup: {last_auto.strftime('%d %b %Y %H:%M')} UTC."


# ---------------------------------------------------------------------------------------------------- making a backup

def _walk_files(app, extra_skip=()):
    """(absolute path, path relative to DATA_DIR) of every file to put in a backup."""
    dbp = db_file(app)
    skip_dirs = [BACKUP_DIR] + list(extra_skip)
    skip_files = set()
    if dbp:
        skip_files = {dbp, dbp + "-wal", dbp + "-shm", dbp + "-journal"}
    seen = set()
    for top in _roots():
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not any(_is_inside(os.path.join(root, d), s) for s in skip_dirs) and d != "__pycache__"]
            for f in files:
                p = os.path.join(root, f)
                if p in seen or p in skip_files or f.endswith((".tmp", ".partial", ".lock")) or os.path.islink(p):
                    continue
                seen.add(p)
                yield p, os.path.relpath(p, DATA_DIR).replace(os.sep, "/")


def _roots():
    """The folders to back up. On Render (DATA_DIR is its own disk) that is the whole disk. On a local / .exe install the data folder IS the
    program folder, so only the folders the app writes to are taken - never the program files."""
    from config import Config, RESOURCE_DIR, INSTANCE_DIR, UPLOAD_DIR
    if os.path.abspath(DATA_DIR) != os.path.abspath(RESOURCE_DIR):
        return [DATA_DIR]
    roots = [INSTANCE_DIR, UPLOAD_DIR]
    roots += [v for k, v in vars(Config).items() if k.endswith("_DATA_DIR") and isinstance(v, str)]
    return [r for r in dict.fromkeys(roots) if os.path.isdir(r)]


def _snapshot_db(app, dest):
    """A consistent copy of the SQLite database at `dest` (SQLite's online backup), checked with PRAGMA integrity_check. Returns "ok" or the problem."""
    dbp = db_file(app)
    src = sqlite3.connect(dbp, timeout=60)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
            res = out.execute("PRAGMA integrity_check").fetchone()
        finally:
            out.close()
    finally:
        src.close()
    return "ok" if res and res[0] == "ok" else (res[0] if res else "no result")


def _acquire_file_lock():
    path = os.path.join(backup_dir(), LOCK_FILE)
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) > 1800:
            os.remove(path)                                  # a crashed run left it behind
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return path
    except FileExistsError:
        return None


def make_backup(app, kind="manual", by=None):
    """Make one backup now. Returns {"ok": True, "name", "size", ...} or {"ok": False, "error": "..."}. Never raises; the outcome is also
    written to the status file the page and the badge read. Only one runs at a time."""
    kind = kind if kind in KINDS else "manual"
    if not _local_lock.acquire(blocking=False):
        return {"ok": False, "error": "Another backup is already running."}
    lock_path = None
    tmp = None
    now = datetime.utcnow()
    try:
        lock_path = _acquire_file_lock()
        if lock_path is None:
            return {"ok": False, "error": "Another backup is already running."}
        _write_status(last_attempt=now.strftime("%Y-%m-%dT%H:%M:%S"))
        tmp = tempfile.mkdtemp(prefix="nlbackup_", dir=backup_dir())
        files = list(_walk_files(app, extra_skip=[tmp]))
        data_bytes = sum(os.path.getsize(p) for p, _ in files if os.path.exists(p))
        dbp = db_file(app)
        db_bytes = os.path.getsize(dbp) if dbp and os.path.exists(dbp) else 0
        free = shutil.disk_usage(backup_dir()).free
        need = int((data_bytes + db_bytes) * 1.05) + db_bytes + 20 * 1024 * 1024        # the snapshot, then the zip
        total_disk = shutil.disk_usage(backup_dir()).total
        reserve = min(total_disk * 0.05, 200 * 1024 * 1024)          # always leave the live app some room
        if free < need + reserve:
            raise RuntimeError(f"Not enough free space on the disk for a backup (it needs about {human_size(need)} and the disk should keep {human_size(reserve)} spare; "
                               f"{human_size(free)} is free). Delete old backups or enlarge the disk.")
        integrity, snap = None, None
        if dbp and os.path.exists(dbp):
            snap = os.path.join(tmp, "neverlank.db")
            integrity = _snapshot_db(app, snap)
            if integrity != "ok":
                raise RuntimeError("The database failed SQLite's integrity check, so it was not backed up: " + str(integrity)[:200])
        stamp = now.strftime("%Y-%m-%d_%H%M%S")
        name = f"neverlank_backup_{stamp}_{kind}.zip"
        final = os.path.join(BACKUP_DIR, name)
        partial = final + ".partial"
        count = 0
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            if snap:
                z.write(snap, "database/neverlank.db")
            for p, rel in files:
                try:
                    z.write(p, "files/" + rel)
                    count += 1
                except (OSError, ValueError):
                    continue                                   # a file removed or locked while we were reading - skip it
            info = {"created_at": now.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "by": by or "", "has_db": bool(snap), "integrity": integrity,
                    "db_bytes": db_bytes, "file_count": count, "app": "Neverlank Audit App"}
            z.writestr("backup_info.json", json.dumps(info, indent=2))
            z.writestr("README.txt", README_TEXT)
        os.replace(partial, final)
        size = os.path.getsize(final)
        meta = {**info, "size": size}
        with open(final[:-4] + ".json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        ok_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        changes = {"last_success": ok_at, "last_error": None, "last_name": name}
        if kind == "auto":
            changes["last_auto_success"] = ok_at
        _write_status(**changes)
        removed = prune()
        return {"ok": True, "name": name, "size": size, "file_count": count, "db_bytes": db_bytes, "removed": removed, "has_db": bool(snap)}
    except Exception as e:                                       # a backup must never take the app down
        try:
            for f in os.listdir(backup_dir()):
                if f.endswith(".partial"):
                    os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass
        msg = str(e) or e.__class__.__name__
        _write_status(last_error=msg[:500], last_attempt=now.strftime("%Y-%m-%dT%H:%M:%S"))
        return {"ok": False, "error": msg}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        if lock_path:
            try:
                os.remove(lock_path)
            except OSError:
                pass
        _local_lock.release()


def prune():
    """Keep the newest few of each kind (BACKUP_KEEP_DAILY / _MANUAL; 5 pre-restore). Returns the number removed."""
    removed = 0
    limits = keep_limits()
    for kind in KINDS:
        mine = [b for b in list_backups() if b["kind"] == kind]
        for b in mine[limits[kind]:]:
            if delete_backup(b["name"]):
                removed += 1
    # the backups must never crowd out the live data: when they take more than BACKUP_MAX_PERCENT (default 40) of the disk, the oldest
    # go first - automatic, then pre-restore, then manual - always leaving the newest two automatic ones and the newest of the others
    try:
        total = shutil.disk_usage(backup_dir()).total
    except OSError:
        return removed
    cap = total * min(90, max(5, _env_int("BACKUP_MAX_PERCENT", 40))) / 100.0
    for kind, floor in (("auto", 2), ("pre-restore", 1), ("manual", 1)):
        while True:
            allb = list_backups()
            if sum(b["size"] for b in allb) <= cap:
                break
            mine = [b for b in allb if b["kind"] == kind]
            if len(mine) <= floor:
                break
            if delete_backup(mine[-1]["name"]):
                removed += 1
            else:
                break
    return removed


def delete_backup(name):
    if not NAME_RE.match(name or ""):
        return False
    base = os.path.join(backup_dir(), name)
    ok = False
    for p in (base, base[:-4] + ".json"):
        try:
            os.remove(p)
            ok = ok or p == base
        except OSError:
            pass
    return ok


README_TEXT = """NEVERLANK AUDIT APP - BACKUP

database/neverlank.db   the whole database (every client, engagement, trial balance, invoice...), a consistent copy.
files/...               every other file the app keeps (uploads, document templates, libraries), under the same path as on the disk.
backup_info.json        when it was made, by whom, and whether the database passed SQLite's integrity check.

TO RESTORE
1. Easiest: sign in as an admin, open Backup in the menu, find this backup in the list and use Restore. A backup of the current state is taken first.
2. By hand (for example on a new disk): put database/neverlank.db at <data folder>/instance/neverlank.db and the contents of files/ into
   <data folder>/ (on Render the data folder is /var/data), then restart the app.

The code is in git; the Render environment variables (SECRET_KEY, email and API keys) are NOT in this backup - keep a private copy of them.
"""


# ---------------------------------------------------------------------------------------------------- restoring

def restore_backup(app, name, with_files=False, by=None):
    """Replace the live database (and, if asked, add/overwrite the files) with those in backup `name`. A pre-restore backup is made first.
    Returns {"ok": bool, "error"?, "safety"?}."""
    if not NAME_RE.match(name or ""):
        return {"ok": False, "error": "That is not a backup file."}
    path = os.path.join(backup_dir(), name)
    if not os.path.exists(path):
        return {"ok": False, "error": "That backup no longer exists."}
    dbp = db_file(app)
    if not dbp:
        return {"ok": False, "error": "Restore from this page only works with the built-in SQLite database."}
    tmp = tempfile.mkdtemp(prefix="nlrestore_", dir=backup_dir())
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                return {"ok": False, "error": f"The backup file is damaged ({bad})."}
            if "database/neverlank.db" not in z.namelist():
                return {"ok": False, "error": "This backup holds no database."}
            staged = os.path.join(tmp, "neverlank.db")
            with z.open("database/neverlank.db") as src, open(staged, "wb") as dst:
                shutil.copyfileobj(src, dst)
            con = sqlite3.connect(staged)
            try:
                res = con.execute("PRAGMA integrity_check").fetchone()
                has_users = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='user'").fetchone()[0]
            finally:
                con.close()
            if not res or res[0] != "ok" or not has_users:
                return {"ok": False, "error": "The database inside this backup did not pass the integrity check, so nothing was changed."}
            safety = make_backup(app, "pre-restore", by)
            if not safety.get("ok"):
                return {"ok": False, "error": "A safety backup of the current data could not be made, so nothing was changed: " + safety.get("error", "")}
            db.session.remove()
            db.engine.dispose()
            for suffix in ("-wal", "-shm", "-journal"):
                try:
                    os.remove(dbp + suffix)
                except OSError:
                    pass
            os.makedirs(os.path.dirname(dbp), exist_ok=True)
            shutil.copyfile(staged, dbp + ".restoring")
            os.replace(dbp + ".restoring", dbp)
            restored = 0
            if with_files:
                root = os.path.abspath(DATA_DIR)
                for member in z.namelist():
                    if not member.startswith("files/") or member.endswith("/"):
                        continue
                    rel = member[len("files/"):]
                    target = os.path.abspath(os.path.join(root, rel))
                    if not _is_inside(target, root) or _is_inside(target, BACKUP_DIR) or target == dbp or target.startswith(dbp + "-"):
                        continue                               # never outside the data folder, never into the backups, never over the database
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    restored += 1
        try:                                                    # an older backup may lack columns added since
            from app import _add_missing_columns
            db.create_all()
            _add_missing_columns()
        except Exception:
            pass
        _write_status(last_restore=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), last_restore_name=name)
        return {"ok": True, "safety": safety.get("name"), "files": restored}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "The backup file is damaged or incomplete."}
    except Exception as e:
        return {"ok": False, "error": "Restore failed: " + (str(e) or e.__class__.__name__)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------- the schedule

_thread_started = False


def start_scheduler(app):
    """Start the background thread once per process (not under the test scripts, not when BACKUP_SCHEDULER=off)."""
    global _thread_started
    if _thread_started or os.environ.get("BACKUP_SCHEDULER", "on").lower() == "off":
        return False
    if "smoke_test" in os.path.basename(sys.argv[0] or ""):
        return False
    _thread_started = True

    def loop():
        time.sleep(90)                                          # let the app finish starting up
        while True:
            try:
                with app.app_context():
                    if is_due():
                        make_backup(app, "auto", "automatic")
            except Exception:
                pass
            time.sleep(600)
    threading.Thread(target=loop, name="nl-backup", daemon=True).start()
    return True


# ---------------------------------------------------------------------------------------------------- the page

def _can_use():
    return current_user.is_authenticated and current_user.role in ("admin", "partner")


def _guard():
    if not _can_use():
        abort(403, "Only an Admin or a Partner can use Backups.")


@backup_bp.route("/")
@login_required
def index():
    _guard()
    st = _read_status()
    level, message = health()
    try:
        usage = shutil.disk_usage(backup_dir())
        disk = {"free": usage.free, "total": usage.total, "used": usage.used}
    except OSError:
        disk = None
    return render_template("backup/index.html", backups=list_backups(), level=level, message=message, status=st, disk=disk,
                           human_size=human_size, can_restore=current_user.role == "admin", sqlite_ok=bool(db_file()),
                           limits=keep_limits(), hour=backup_hour(), scheduler_on=os.environ.get("BACKUP_SCHEDULER", "on").lower() != "off",
                           last_auto=_parse_iso(st.get("last_auto_success")), data_dir=DATA_DIR)


@backup_bp.route("/now", methods=["POST"])
@login_required
def backup_now():
    _guard()
    res = make_backup(current_app._get_current_object(), "manual", current_user.name)
    if res["ok"]:
        flash(f"Backup made: {res['name']} ({human_size(res['size'])}, {res['file_count']} file(s) and the database).", "success")
    else:
        flash("The backup could not be made: " + res["error"], "danger")
    return redirect(url_for("backup.index"))


@backup_bp.route("/download/<name>")
@login_required
def download(name):
    _guard()
    if not NAME_RE.match(name):
        abort(404)
    path = os.path.join(backup_dir(), name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/zip")


@backup_bp.route("/delete/<name>", methods=["POST"])
@login_required
def delete(name):
    _guard()
    if delete_backup(name):
        flash("Backup deleted.", "success")
    else:
        flash("That backup could not be deleted.", "danger")
    return redirect(url_for("backup.index"))


@backup_bp.route("/restore/<name>", methods=["POST"])
@login_required
def restore(name):
    if not (current_user.is_authenticated and current_user.role == "admin"):
        abort(403, "Only an Admin can restore a backup.")
    if (request.form.get("confirm") or "").strip() != "RESTORE":
        flash("Nothing was restored - type RESTORE in the box to confirm.", "danger")
        return redirect(url_for("backup.index"))
    res = restore_backup(current_app._get_current_object(), name, request.form.get("with_files") == "1", current_user.name)
    if res["ok"]:
        flash(f"Restored from {name}. A backup of the data as it was just before is {res['safety']}. Please sign in again.", "success")
        return redirect(url_for("auth.login"))
    flash(res["error"], "danger")
    return redirect(url_for("backup.index"))


def nav_state():
    """For the menu badge: '' when all is well, else the message."""
    try:
        level, message = health()
        return "" if level == "ok" else message
    except Exception:
        return ""
