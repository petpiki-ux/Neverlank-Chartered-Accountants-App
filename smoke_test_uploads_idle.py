"""Upload guard (one check for every upload box) and idle sign-out."""
import os, sys, tempfile, io, time
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from werkzeug.datastructures import FileStorage
from app import create_app
from extensions import db
from models import *
import security
app = create_app()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def fs(name, data):
    return FileStorage(stream=io.BytesIO(data), filename=name)
def prob(name, data):
    return security.upload_problem(fs(name, data))

PDF = b"%PDF-1.7\n1 0 obj"; ZIP = b"PK\x03\x04" + b"\0" * 30; OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 30
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20; JPG = b"\xff\xd8\xff\xe0" + b"\0" * 20
good = {"a.pdf": PDF, "a.docx": ZIP, "a.xlsx": ZIP, "a.xlsm": ZIP, "a.pptx": ZIP, "a.zip": ZIP, "a.doc": OLE, "a.xls": OLE, "a.ppt": OLE,
        "a.png": PNG, "a.jpg": JPG, "a.JPEG": JPG, "a.gif": b"GIF89a" + b"\0" * 10, "a.bmp": b"BM" + b"\0" * 20, "a.tif": b"II*\x00" + b"\0" * 10,
        "a.webp": b"RIFF\0\0\0\0WEBPVP8 ", "a.csv": b"a,b\n1,2\n", "a.txt": b"hello", "C:\\fakepath\\Report 2025.pdf": PDF, "a.xls ": b"<?xml version='1.0'?><Workbook/>"}
for n, d in good.items():
    check(prob(n, d) is None, f"genuine file accepted: {n.strip()}")
check(prob("", b"x") is None, "an empty box (no file chosen) is ignored")
check(prob("a.pdf", b"") is None, "an empty file is left to the page's own check")
bad = {"run.exe": b"MZ\x90\0", "run.bat": b"@echo off", "page.html": b"<html>", "x.svg": b"<svg onload=1>", "x.php": b"<?php", "x.js": b"alert(1)", "noext": b"abc",
       "evil.pdf": b"MZ\x90\0\x03", "evil2.pdf": b"<script>alert(1)</script>", "fake.pdf": b"just text", "fake.docx": b"just text", "fake.xlsx": b"<html>x",
       "fake.png": b"<html><script>", "fake.jpg": PNG, "fake.png2": PNG, "fake.webp": PNG, "fake.doc": b"plain text with no binary" + b"\0" * 600,
       "elf.csv": b"\x7fELF\x02", "shebang.txt": b"#!/bin/sh\nrm -rf /", "evil.docx": b"MZ\x90\0", "a.pdf.exe": b"MZ\0\0", "x.html.pdf": b"<!DOCTYPE html><script>"}
for n, d in bad.items():
    check(prob(n, d) is not None, f"refused: {n}")
check(prob("notes.csv", b"<script>x</script>,1\n") is None, "a CSV that merely contains angle brackets is fine (data, not a page)")
st = fs("a.pdf", PDF + b"x" * 9000); security.upload_problem(st)
check(st.stream.read(4) == b"%PDF", "checking a file leaves its stream at the start for the page to save")

# --------------------------------------------------------------- through a real route
with app.app_context():
    u = User(username="alice", name="Alice", role="partner"); u.set_password("Correct-horse-9"); db.session.add(u); db.session.commit()
    uid = u.id
def login_as(i):
    c = app.test_client()
    with c.session_transaction() as s: s["_user_id"] = str(i); s["_fresh"] = True
    return c
c = login_as(uid)
hits = []
@app.route("/__probe_upload", methods=["POST"])
def _probe_upload():
    f = flask.request.files.get("f")
    if f is not None:
        f.save(io.BytesIO())                 # the guard sits on save(): this is what every page does to store a file
    hits.append(1); return "ok"
@app.route("/__probe_read", methods=["POST"])
def _probe_read():
    flask.request.files["f"].read(); return "read"
r = c.post("/__probe_upload", data={"f": (io.BytesIO(b"MZ\x90\0malware"), "report.pdf")}, content_type="multipart/form-data")
check(r.status_code == 400 and not hits, "a program disguised as a PDF is refused when the page tries to store it (no referrer: 400)")
r = c.post("/__probe_upload", data={"f": (io.BytesIO(b"MZ\x90\0malware"), "report.pdf")}, content_type="multipart/form-data", headers={"Referer": "http://localhost/clients/", "Accept": "text/html"})
check(r.status_code == 302 and r.headers["Location"].endswith("/clients/") and not hits, "from a browser form the person is sent back with a message")
r = c.post("/__probe_read", data={"f": (io.BytesIO(b"MZ\x90\0x"), "x.xlsx")}, content_type="multipart/form-data")
check(r.status_code == 200 and r.data == b"read", "a file a page only reads in memory (never stored) is left to the page's own checks")
r = c.post("/__probe_upload", data={"f": (io.BytesIO(PDF), "report.pdf"), "note": "hi"}, content_type="multipart/form-data")
check(r.status_code == 200 and hits, "a genuine upload passes straight through")

# --------------------------------------------------------------- idle sign-out
def page(c): return c.get("/auth/account")
os.environ["NL_IDLE_MINUTES"] = "10"
c = login_as(uid)
check(page(c).status_code == 200, "an active session works")
with c.session_transaction() as s: s["_last_active"] = time.time() - 9 * 60
check(page(c).status_code == 200, "9 minutes idle with a 10-minute limit: still signed in")
with c.session_transaction() as s: s["_last_active"] = time.time() - 11 * 60
r = page(c)
check(r.status_code == 302 and "/auth/login" in r.headers["Location"], "11 minutes idle: signed out and sent to the sign-in page")
check(page(c).status_code == 302, "and stays signed out")
c = login_as(uid); page(c)
with c.session_transaction() as s: s["_last_active"] = time.time() - 10
page(c)
with c.session_transaction() as s: check(time.time() - s["_last_active"] > 8, "activity is only stamped every 30 seconds (no write on every click)")
with c.session_transaction() as s: s["_last_active"] = time.time() - 40
page(c)
with c.session_transaction() as s: check(time.time() - s["_last_active"] < 5, "activity older than 30 seconds is refreshed")
os.environ["NL_IDLE_MINUTES"] = "0"
c = login_as(uid)
with c.session_transaction() as s: s["_last_active"] = time.time() - 30 * 86400
check(page(c).status_code == 200, "0 switches the idle sign-out off")
os.environ["NL_IDLE_MINUTES"] = "abc"
check(page(login_as(uid)).status_code == 200, "a nonsense setting falls back to the 2-hour default")
os.environ.pop("NL_IDLE_MINUTES")
c = login_as(uid)
with c.session_transaction() as s: s["_last_active"] = time.time() - 3 * 3600
check(page(c).status_code == 302, "the default is 2 hours")
print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
