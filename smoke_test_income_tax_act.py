"""The Income Tax Act [Chapter 23:06] filed section by section (act_parser.py, act_import.py): sections and schedules found, one
searchable passage per section, an older copy of the Act replaced rather than duplicated, references such as 16(1)(m) resolving to the Act's
own words, the Act pages, and the permission to file it."""
import os, sys, tempfile, re
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
import act_import as AI, act_parser as AP, legislation_index as LI
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()

    # --- an earlier copy of the Act (as the ZimLII import would have filed it) is already in the library
    old = LegislativeUpdate(title="Income Tax Act", instrument_type="Act", source_reference="Income Tax Act [Chapter 23:06]",
                            extracted_text="1. Short title\nThis is the 2019 consolidation.\n" * 5, extraction_status="extracted")
    cgt = LegislativeUpdate(title="Capital Gains Tax Act [Chapter 23:01]", instrument_type="Act", extracted_text="Capital gains text " * 20, extraction_status="extracted")
    db.session.add_all([old, cgt]); db.session.commit()
    old_id, cgt_id = old.id, cgt.id

    st = AI.status("ita")
    check(not st["loaded"] and st["existing_other_copy"] is not None and st["existing_other_copy"].id == old_id, "before filing: not loaded, and the older copy is recognised (not the CGT Act)")

    # --- parsing
    text = AI.act_text("ita")
    P = AP.parse_act(text)
    nums = [s["number"] for s in P["sections"]]
    check(len(P["sections"]) == 141 and len(P["schedules"]) == 36, "the Act splits into 141 sections and 36 schedules (got %d / %d)" % (len(P["sections"]), len(P["schedules"])))
    check(all(n in nums for n in ["1", "2", "2A", "8", "12B", "14", "15", "16", "17", "36G", "36N", "37A", "37B", "80B", "80BB", "98", "98B", "99"]), "key sections are all found, including the lettered ones")
    check(len(set(nums)) == len(nums), "no section is found twice")
    sec = {s["number"]: s for s in P["sections"]}
    check("entertainment" in sec["16"]["text"] and "Deductions allowed" in sec["15"]["title"], "section 15 and 16 carry their own text and title")
    check(sec["16"]["part"] == "Part III" and sec["37A"]["part"] == "Part V", "sections know their Part")
    check(sec["3"]["repealed"] and sec["3"]["title"].startswith("[Repealed"), "repealed sections are marked")
    check([s["number"] for s in P["schedules"]] == list(range(1, 37)), "schedules 1 to 36 in order")
    check("wear and tear" in {s["number"]: s for s in P["schedules"]}[4]["text"].lower(), "the Fourth Schedule holds wear and tear")
    covered = sum(len(s["text"]) for s in P["sections"]) + sum(len(s["text"]) for s in P["schedules"])
    check(covered > 0.95 * len(text), "at least 95%% of the Act's text lands in a section or schedule (%.1f%%)" % (100.0 * covered / len(text)))
    # the layout the app's own PDF reader gives (no indentation) parses the same way
    flat = "\n".join(l.strip() for l in text.split("\n"))
    P2 = AP.parse_act(flat)
    check(len(P2["sections"]) == 141 and len(P2["schedules"]) == 36, "the same split from text with no indentation (as the PDF reader returns it)")

    # --- filing
    res = AI.load_act("ita", user_id=partner.id)
    check(res["sections"] == 141 and res["schedules"] == 36 and res["update_id"] == old_id and not res["created"], "filing reuses and replaces the earlier copy of the Act")
    check(LegislativeUpdate.query.filter(LegislativeUpdate.title.ilike("%income tax act%"), LegislativeUpdate.instrument_type == "Act").count() == 1, "only one Income Tax Act in the library")
    check(db.session.get(LegislativeUpdate, cgt_id).extracted_text.startswith("Capital gains"), "the Capital Gains Tax Act is untouched")
    u = db.session.get(LegislativeUpdate, old_id)
    check(u.title == "Income Tax Act [Chapter 23:06]" and u.ai_status == "skipped" and "1 April" not in (u.summary or "") and len(u.chunks) == res["chunks"], "the update is retitled, not auto-summarised, and chunked")
    check(LegislativeProvision.query.filter_by(act_code="ita").count() == 177, "177 provisions stored")
    heads = [c.heading for c in u.chunks]
    check(any(h.startswith("Section 15 - Deductions allowed") for h in heads) and any(h.startswith("Fourth Schedule - Deductions to be allowed") and "(Section 15(2)(c))" in h for h in heads), "passages are headed by section number and title")
    check(any("(part 2 of" in h for h in heads if h.startswith("Section 15 ")), "a long section is split into numbered parts")
    check(max(len(c.text) for c in u.chunks) < 9000, "no passage is oversized")
    again = AI.load_act("ita", user_id=partner.id)
    check(again["update_id"] == old_id and LegislativeProvision.query.filter_by(act_code="ita").count() == 177 and len(db.session.get(LegislativeUpdate, old_id).chunks) == again["chunks"], "filing twice leaves no duplicates")
    st = AI.status("ita")
    check(st["loaded"] and st["sections"] == 141 and st["schedules"] == 36 and st["act_update"].id == old_id, "status reports it loaded")
    try:
        AI.load_act("ita", text="short text only")
        check(False, "a text that is not an Act is refused")
    except ValueError:
        db.session.rollback(); check(True, "a text that is not an Act is refused")

    # --- references
    check(AI.parse_ref("16(1)(m)") == ("section", "16", ["1", "m"]) and AI.parse_ref("s.36g") == ("section", "36G", []) and AI.parse_ref("sch4.3") == ("schedule", "4", ["3"]) and AI.parse_ref("Schedule 4 para 3(1)") == ("schedule", "4", ["3", "1"]), "references are read: 16(1)(m), s.36g, sch4.3, Schedule 4 para 3(1)")
    check(AI.parse_ref("hello there") is None, "nonsense is not a reference")
    pm = AI.paragraph_text("16(1)(m)")
    check(pm and pm["exact"] and "entertainment" in pm["text"] and "(n)" not in pm["text"].split("\n")[0] and "hospitality" in pm["text"], "16(1)(m) gives the entertainment paragraph")
    check(not re.search(r"\n\s*\(n\)\s", pm["text"]), "...and stops before (n)")
    pd1 = AI.paragraph_text("16(1)(d1)")
    check(pd1 and pd1["exact"] and "Intermediated Money Transfer Tax" in pd1["text"] and "36G" in pd1["text"], "16(1)(d1) is the Intermediated Money Transfer Tax paragraph")
    pi = AI.paragraph_text("16(1)(i)")
    check(pi and pi["exact"] and "dwelling" in pi["text"] and "(j)" not in pi["text"][:40], "16(1)(i) is rent of premises, not mistaken for the sub-item (i) of (f)")
    pf = AI.paragraph_text("16(1)(f)(i)")
    check(pf and "exempt from income tax" in pf["text"], "16(1)(f)(i) is found")
    for lab, word in [("r", "associated enterprise"), ("s", "foreign loans"), ("q", "debt to equity ratio of three"), ("k", "passenger motor vehicle"), ("l", "shares awarded"), ("g", "pensions")]:
        x = AI.paragraph_text("16(1)(%s)" % lab)
        check(x and x["exact"] and word in x["text"], "16(1)(%s) is found (%s)" % (lab, word))
    ps = AI.paragraph_text("15(2)(c)")
    check(ps and ps["exact"] and "allowances in respect of" in ps["text"] and "(d)" not in ps["text"].split("\n")[0], "15(2)(c) is the capital allowances paragraph")
    check(AI.paragraph_text("15(2)(f)")["exact"] and AI.paragraph_text("15(2)(jj)")["exact"] and AI.paragraph_text("15(2)(r1)")["exact"], "15(2)(f), (jj) and (r1) are found (the Act skips (ii) and numbers (r1))")
    sw = AI.paragraph_text("sch4.3")
    check(sw and sw["exact"] and "wear and tear" in sw["text"] and "scrapping" not in sw["text"].split("\n")[1] , "Fourth Schedule paragraph 3 is wear and tear")
    sw7 = AI.paragraph_text("sch4.7")
    check(sw7 and "twenty-five per centum" in sw7["text"], "Fourth Schedule paragraph 7 (machinery wear and tear)")
    s9 = AI.paragraph_text("sch4.9")
    check(s9 and "Rates of special initial allowance" in s9["text"] and "fifty per centum" in s9["text"], "Fourth Schedule paragraph 9 (special initial allowance rates)")
    check(AI.paragraph_text("999") is None and AI.paragraph_text("16(1)(zz)")["exact"] is False, "an unknown section is None; an unknown paragraph falls back to the section")
    check(AI.tidy("pro-\n   duce the\n goods") == "produce the goods", "hyphenated line ends are joined")

    # --- Deep Search reads it
    LI.invalidate()
    r = LI.search("entertainment hospitality expenditure deduction")
    top = r.hits[0] if r.hits else None
    check(top and top.update.id == old_id and any("Section 16" in (p[0].heading or "") for p in top.passages[:3]), "Deep Search for entertainment finds Section 16")
    r = LI.search("wear and tear allowance machinery")
    check(r.hits and any("Fourth Schedule" in (p[0].heading or "") for p in r.hits[0].passages[:4]), "Deep Search for wear and tear finds the Fourth Schedule")
    r = LI.search("thin capitalisation debt to equity ratio three to one")
    check(r.hits and any("Section 16" in (p[0].heading or "") for p in r.hits[0].passages[:3]), "Deep Search for the debt to equity ratio finds Section 16")
    r = LI.search("intermediated money transfer tax")
    check(r.hits and any("36G" in (p[0].heading or "") or "Section 16" in (p[0].heading or "") for p in r.hits[0].passages[:4]), "Deep Search finds the intermediated money transfer tax provisions")

    # --- helper for templates
    with app.test_request_context():
        h = str(AI.ref_link("16(1)(m)"))
        check('href="/tax/act/ita/16(1)(m)"' in h and "entertainment" in h and ">s16(1)(m)<" in h, "act_ref gives a link with the paragraph as its tip")
        check(str(AI.ref_link("16(1)(m)", text="Section 16(1)(m)")).count("Section 16(1)(m)") == 1, "act_ref accepts a label")
        check("<a" not in str(AI.ref_link("not a ref")) and "<a" not in str(AI.ref_link("500")), "act_ref gives plain text for what it cannot find")

    # --- pages
    S = app.test_client(); S.post("/auth/login", data={"username": "stf", "password": "x"})
    r = S.get("/tax/act/ita")
    check(r.status_code == 200 and b"Deductions allowed in determination of taxable income" in r.data and b"Fourth Schedule" in r.data and b"Go to a section" in r.data, "the contents page lists sections and schedules")
    check(b"File the Income Tax Act now" not in r.data, "...with no filing button once it is filed")
    r = S.get("/tax/act/ita/16(1)(m)")
    check(r.status_code == 200 and b"entertainment" in r.data and b"Section 16" in r.data and b"Section 17" in r.data, "section 16(1)(m) page shows the paragraph, the section and the next section")
    r = S.get("/tax/act/ita/sch4.3")
    check(r.status_code == 200 and b"wear and tear" in r.data and b"paragraph 3" in r.data, "schedule paragraph page")
    r = S.get("/tax/act/ita/15?q=deduction+trade")
    check(r.status_code == 200 and b"<mark>" in r.data, "words are marked in a section")
    r = S.get("/tax/act/ita/777", follow_redirects=True)
    check(r.status_code == 200 and b"find" in r.data.lower() and b"777" in r.data, "an unknown section goes back to the contents with a message")
    check(S.get("/tax/act/nope").status_code == 404, "an unknown Act is a 404")
    r = S.post("/tax/act/ita/load", follow_redirects=False)
    check(r.status_code == 403, "staff cannot file the Act")
    r = S.get("/tax/legislative-updates/search?q=entertainment+expenditure+deduction")
    check(r.status_code == 200 and b"Section 16" in r.data, "the search page shows the Act's section headings")

    P1 = app.test_client(); P1.post("/auth/login", data={"username": "ptn", "password": "x"})
    r = P1.post("/tax/act/ita/load", follow_redirects=True)
    check(r.status_code == 200 and b"141 sections and 36 schedules" in r.data and b"replaced" in r.data, "a Partner can refresh it")
    import io
    r = P1.post("/tax/act/ita/load", data={"file": (io.BytesIO(b"too short"), "x.txt")}, content_type="multipart/form-data", follow_redirects=True)
    check(b"does not look like a full Act" in r.data, "a bad upload is refused with a message")
    r = P1.post("/tax/act/ita/load", data={"file": (io.BytesIO(text.encode("utf-8")), "act.txt")}, content_type="multipart/form-data", follow_redirects=True)
    check(r.status_code == 200 and b"141 sections" in r.data, "a text file can be uploaded to refresh it")
    r = P1.post("/tax/act/ita/load", data={"file": (io.BytesIO(b"x"), "act.pdf")}, content_type="multipart/form-data", follow_redirects=True)
    check(b".txt" in r.data, "only .txt uploads are accepted")

print("\nALL PASSED" if not fails else "\n%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
