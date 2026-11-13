"""Run every smoke_test_*.py, several at a time, and print one summary.

    python run_tests.py              run all tests, 2 at a time (set -j N for more)
    python run_tests.py payroll tax  run only the tests whose file name contains one of the words
    python run_tests.py -j 4         four at a time
    python run_tests.py --pg "postgresql+psycopg2://USER@/{db}?host=/tmp/pgdata&port=55432"
                                     run the tests on PostgreSQL instead of SQLite; each test gets its own new database,
                                     removed afterwards ({db} is replaced by the name; the server must already be running)

Each test builds its own temporary database, so they cannot disturb one another. Exit code 0 means everything passed.
"""
import glob, os, subprocess, sys, tempfile, time, shutil
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))


PG_TEMPLATE = None
# These check the start-up repair of an OLD SQLite file (they rebuild tables with SQLite's own commands), so they have no meaning on PostgreSQL.
SQLITE_ONLY = {"smoke_test_advances.py", "smoke_test_learning.py", "smoke_test_prohibited.py", "smoke_test_signature_upgrade.py", "smoke_test_suppliers_upgrade.py",
               "smoke_test_zimra_ai.py", "smoke_test_backup.py", "smoke_test_access_matrix.py"}   # the access test copies the SQLite file between probes


def _pg_admin(template, sql):
    import psycopg2
    from urllib.parse import urlparse, parse_qs
    u = urlparse(template.replace("{db}", "postgres").replace("+psycopg2", ""))
    q = parse_qs(u.query)
    con = psycopg2.connect(dbname="postgres", user=u.username, host=(q.get("host") or [u.hostname])[0], port=int((q.get("port") or [u.port or 5432])[0]), password=u.password)
    con.autocommit = True
    try:
        con.cursor().execute(sql)
    finally:
        con.close()


def run_one(path, timeout=900):
    start = time.time()
    env = dict(os.environ, TMPDIR=tempfile.mkdtemp(prefix="nlrun_"), NL_STRICT_LENGTHS="1")
    dbname = None
    if PG_TEMPLATE:
        dbname = "nl_" + os.path.basename(path)[11:-3][:40] + "_" + str(os.getpid()) + str(int(time.time() * 1000) % 100000)
        _pg_admin(PG_TEMPLATE, f'CREATE DATABASE "{dbname}"')
        env["DATABASE_URL"] = PG_TEMPLATE.replace("{db}", dbname)
    try:
        p = subprocess.run([sys.executable, path], cwd=HERE, capture_output=True, text=True, timeout=timeout, env=env)
        out, rc = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        out, rc = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), -9
        out += "\nFAIL timed out"
    finally:
        shutil.rmtree(env["TMPDIR"], ignore_errors=True)
        if dbname:
            try:
                _pg_admin(PG_TEMPLATE, f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            except Exception:
                pass
    lines = out.splitlines()
    fails = [l for l in lines if l.startswith("FAIL ")]
    passed = sum(1 for l in lines if l.startswith("PASS "))
    bad = rc != 0 or bool(fails)
    tb = [l for l in lines if "Traceback" in l or l.startswith(("AssertionError", "Error", "  File")) or "Error:" in l][-4:] if rc != 0 else []
    return os.path.basename(path), bad, passed, fails[:6], tb, time.time() - start


def main(argv):
    global PG_TEMPLATE
    jobs, words = 2, []
    it = iter(argv)
    for a in it:
        if a == "-j":
            jobs = int(next(it))
        elif a == "--pg":
            PG_TEMPLATE = next(it)
        else:
            words.append(a)
    files = sorted(glob.glob(os.path.join(HERE, "smoke_test_*.py")))
    if words:
        files = [f for f in files if any(w in os.path.basename(f) for w in words)]
    if PG_TEMPLATE:
        files = [f for f in files if os.path.basename(f) not in SQLITE_ONLY]
    if not files:
        print("No tests match.")
        return 1
    t0 = time.time()
    failed, total_pass = [], 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for name, bad, passed, fails, tb, secs in pool.map(run_one, files):
            total_pass += passed
            print(("FAILED  " if bad else "ok      ") + f"{name}  ({passed} checks, {secs:.0f}s)", flush=True)
            if bad:
                failed.append(name)
                for l in fails + tb:
                    print("        " + l)
    print(f"\n{len(files) - len(failed)} of {len(files)} test files passed, {total_pass} checks, {time.time() - t0:.0f}s")
    if failed:
        print("Failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
