"""The Capital Gains Tax Act [Chapter 23:01] filed section by section and set beside what the app does for CGT: the Act splits into its sections
(including Part IIIA and the sections 30A, 30B and the printed section 32), replaces an older copy without touching other Acts or the CGT
notices, resolves references such as 11(2)(c) or 22C(1) to its own words and is searched by Deep Search; the dates the Act sets are worked out;
every section the findings, time limits and checklist quote exists in the Act; the Tax / CGT Advisory checklist already stored in a database is
brought into line without touching a line a person edited; and the comparison page says the Act holds no rate and marks nothing verified."""
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
from datetime import date
from app import create_app
from extensions import db
from models import *
import act_import as AI, act_parser as AP, legislation_index as LI, cgt_checks as CG
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
norm = lambda t: re.sub(r"\s+", " ", t or "")

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    older = LegislativeUpdate(title="Capital Gains Tax Act", instrument_type="Act", source_reference="CGT 2019", extracted_text="Old CGT text " * 30, extraction_status="extracted")
    notice = LegislativeUpdate(title="Capital Gains Tax (Rate of Interest) Notice, 2022", instrument_type="SI", extracted_text="interest notice " * 30, extraction_status="extracted")
    ita = LegislativeUpdate(title="Income Tax Act [Chapter 23:06]", instrument_type="Act", extracted_text="ita text " * 30, extraction_status="extracted")
    db.session.add_all([older, notice, ita]); db.session.commit()
    older_id, notice_id, ita_id = older.id, notice.id, ita.id
    st = AI.status("cgt")
    check(not st["loaded"] and st["existing_other_copy"] is not None and st["existing_other_copy"].id == older_id, "before filing: the older CGT Act copy is recognised, not the notice or the Income Tax Act")

    # --- parsing
    text = AI.act_text("cgt")
    P = AP.parse_act(text)
    nums = [s["number"] for s in P["sections"]]
    sec = {s["number"]: s for s in P["sections"]}
    check(len(P["sections"]) == 47 and len(P["schedules"]) == 0 and not P["warnings"], "47 sections, no schedules, no warnings (got %d / %d / %s)" % (len(P["sections"]), len(P["schedules"]), P["warnings"]))
    check(len(set(nums)) == len(nums) and nums[0] == "1" and nums[-1] == "32", "sections run 1 to 32 with none repeated")
    for n in ("22A", "22B", "22C", "22D", "22E", "22F", "22FA", "22G", "22H", "22I", "22J", "22K", "22L", "30A", "30B", "31", "32"):
        check(n in sec, "section %s is there" % n)
    check(sec["4"]["repealed"] and sec["5"]["repealed"] and not sec["6"]["repealed"], "sections 4 and 5 are marked repealed")
    check(sec["22C"]["part"] == "Part IIIA" and sec["11"]["part"] == "Part III" and sec["25"]["part"] == "Part VI" and sec["1"]["part"] == "Part I", "sections sit in the right Parts")
    check(sec["6"]["title"].startswith("Charging of capital gains tax") and "Consolidated Revenue Fund" in sec["6"]["text"], "section 6 carries its title and text")
    check("Page " not in "".join(s["text"][-60:] for s in P["sections"]) and "of 46" not in "".join(s["text"] for s in P["sections"]), "page footers are dropped")
    check("subsection" in sec["31"]["text"] and "wrongly numbered" not in sec["31"]["text"] and "wrongly numbered" in sec["32"]["text"], "the printed section 32 is its own section, not part of 31")
    covered = sum(len(s["text"]) for s in P["sections"])
    check(covered > 0.9 * len(text), "at least 90%% of the Act's text lands in a section (%.1f%%)" % (100.0 * covered / len(text)))
    check("¾" not in text and "\x0c" not in "".join(s["text"] for s in P["sections"]), "stray characters are cleaned")
    PI = AP.parse_act(AI.act_text("ita")); PV = AP.parse_act(AI.act_text("vat")); PC = AP.parse_act(AI.act_text("cobe"))
    check((len(PI["sections"]), len(PI["schedules"])) == (141, 36) and (len(PV["sections"]), len(PV["schedules"])) == (113, 2) and (len(PC["sections"]), len(PC["schedules"])) == (304, 10), "the Income Tax, VAT and COBE Acts still split as before")
    for tag, n in [("[Formula as printed: (A - B) / C x C]", 1), ("[Formula: A x (B - C) / D]", 1), ("[Formula: A x C / B]", 2)]:
        check(text.count(tag) == n, "the picture formula %s is written out %d time(s)" % (tag, n))

    # --- filing
    res = AI.load_act("cgt", user_id=partner.id)
    check(res["sections"] == 47 and res["schedules"] == 0 and res["update_id"] == older_id and not res["created"], "filing reuses and replaces the earlier copy")
    check(db.session.get(LegislativeUpdate, notice_id).extracted_text.startswith("interest notice") and db.session.get(LegislativeUpdate, ita_id).extracted_text.startswith("ita text"), "the interest notice and the Income Tax Act entry are untouched")
    u = db.session.get(LegislativeUpdate, older_id)
    check(u.title == "Capital Gains Tax Act [Chapter 23:01]" and u.ai_status == "skipped" and len(u.chunks) == res["chunks"], "retitled, not auto-summarised, chunked")
    check(LegislativeProvision.query.filter_by(act_code="cgt").count() == 47, "47 provisions stored")
    heads = [c.heading for c in u.chunks]
    check(any(h.startswith("Section 22C - Depositaries to withhold tax") for h in heads), "passages are headed by section number and title")
    check(max(len(c.text) for c in u.chunks) < 9000, "no passage is oversized")
    AI.load_act("cgt", user_id=partner.id)
    check(LegislativeProvision.query.filter_by(act_code="cgt").count() == 47 and LegislativeUpdate.query.filter(LegislativeUpdate.title.like("Capital Gains Tax Act%")).count() == 1, "filing twice leaves no duplicates")
    AI.load_act("ita", user_id=partner.id); AI.load_act("vat", user_id=partner.id); AI.load_act("cobe", user_id=partner.id)
    check(all(LegislativeProvision.query.filter_by(act_code=c).count() == n for c, n in [("cgt", 47), ("ita", 177), ("vat", 115), ("cobe", 314)]), "the four Acts stay filed side by side")
    check(LegislativeUpdate.query.filter(LegislativeUpdate.title.like("Income Tax Act%")).count() == 1, "the Income Tax Act is still a single entry")

    # --- references
    for ref, needle in [("11(2)(c)", "Consumer Price Index"), ("10(l)", "55"), ("22C(1)", "3rd working day"), ("22C(5)", "clearance certificate"), ("26(1)(b)", "30 days"),
                        ("25(1)", "30 days"), ("30B(5)(a)", "20%"), ("22H(1)", "15%"), ("22I(1)", "6 years"), ("21(2)", "next following the sale"), ("13(3)", "2 years"),
                        ("2", "specified asset"), ("11(3)", "previous year"), ("22FA(1)", "30 days"), ("30A(1)", "Registrar of Deeds"), ("32", "Registrar of Deeds")]:
        f = AI.paragraph_text(ref, "cgt")
        check(f is not None and needle.lower() in norm(f["text"]).lower(), "reference %s resolves to its own words (%s)" % (ref, needle))
    with app.test_request_context():
        h = str(AI.ref_link("22C(1)", code="cgt"))
        check('href="/tax/act/cgt/22C(1)"' in h and "working day" in h.lower(), "act_ref(code='cgt') links into the CGT Act with the paragraph as its tip")

    # --- Deep Search
    LI.invalidate()
    r = LI.search("capital gains withholding tax depositary third working day clearance certificate")
    check(r.hits and any("Section 22C" in (p[0].heading or "") for h in r.hits[:4] for p in h.passages[:3]), "Deep Search for depositary withholding finds Section 22C")
    r = LI.search("principal private residence reinvest next year of assessment")
    check(r.hits and any("Section 21" in (p[0].heading or "") for h in r.hits[:4] for p in h.passages[:3]), "Deep Search for the residence rollover finds Section 21")
    r = LI.search("special capital gains tax mining title")
    check(r.hits and any("Section 30B" in (p[0].heading or "") for h in r.hits[:4] for p in h.passages[:3]), "Deep Search for the mining title tax finds Section 30B")

    # --- dates
    check(CG.payment_due(date(2026, 3, 10)) == date(2026, 4, 9) and CG.payment_due(None) is None, "tax due 30 days from transfer of title")
    check(CG.withholding_due(date(2026, 10, 7)) == date(2026, 10, 12), "paid Wed 7 Oct 2026: 3rd working day is Mon 12 Oct")
    check(CG.withholding_due(date(2026, 10, 9)) == date(2026, 10, 14), "paid Fri 9 Oct 2026: 3rd working day is Wed 14 Oct")
    check(CG.withholding_due(date(2026, 10, 10)) == date(2026, 10, 14), "paid on a Saturday: counted from the next working day")
    check(CG.reinvestment_deadline(date(2026, 3, 10)) == date(2027, 12, 31) and CG.reinvestment_deadline(date(2026, 12, 31)) == date(2027, 12, 31), "reinvestment by the end of the next year of assessment")
    check(CG.damage_replacement_deadline(date(2024, 2, 29)) == date(2026, 2, 28) and CG.damage_replacement_deadline(date(2026, 5, 1)) == date(2028, 5, 1), "damaged asset: 2 years, 29 Feb handled")
    check(CG.objection_due(date(2026, 1, 15)) == date(2026, 2, 14), "objection 30 days from the notice")

    # --- findings, time limits and checklist all point at real provisions
    bad = []
    for f in CG.FINDINGS:
        for r in f["refs"]:
            if AI.paragraph_text(r, "cgt") is None: bad.append(r)
    for t, refs, rule in CG.DEADLINES:
        for r in refs:
            if AI.paragraph_text(r, "cgt") is None: bad.append(r)
    for item, refs in CG.EXEMPTIONS_NOTED:
        for r in refs:
            if AI.paragraph_text(r, "cgt") is None: bad.append(r)
    check(not bad, "every section in the findings, time limits and exemptions exists in the Act (bad: %s)" % bad)
    check({f["result"] for f in CG.FINDINGS} <= set(CG.RESULT_LABELS) and len(CG.FINDINGS) >= 15, "%d findings, all with a known result" % len(CG.FINDINGS))
    import seed as SD
    prog = "\n".join(t for s_, t in SD.TAX_CGT_PROGRAM)
    cited = set()
    for m in re.finditer(r"\bs(\d{1,3}[A-Z]{0,2})\b", prog):
        cited.add(m.group(1))
    for m in re.finditer(r"\bs(\d{1,3}) to s(\d{1,3})", prog):
        for n in range(int(m.group(1)), int(m.group(2)) + 1): cited.add(str(n))
    cited -= {"91", "37"}  # Income Tax Act sections, named as such in the lines
    miss = sorted(n for n in cited if n not in sec)
    check(cited and not miss, "all %d CGT Act section numbers quoted in the checklist exist in the Act (missing: %s)" % (len(cited), miss))
    check(max(len(t) for s_, t in SD.TAX_CGT_PROGRAM) <= 500, "every checklist line fits the 500-character column")
    check("flat rate" in prog and "30 days" in prog and "3rd working day" in prog and "s30A" in prog and "s30B" in prog and "s25" in prog, "the checklist carries the inflation formula, 30 days, 3rd working day, s30A, s30B and s25")
    for o, n in CG.PROGRAM_PAIRS: check(o not in prog.replace(n, ""), "old wording gone: %s" % o[:50])

    # --- the pages
    S = app.test_client(); S.post("/auth/login", data={"username": "stf", "password": "x"})
    r = S.get("/tax/act/cgt")
    check(r.status_code == 200 and b"Capital gains withholding tax" in r.data and b"Part IIIA" in r.data and b"Special capital gains tax on entities acquiring mining title" in r.data and b"File the Capital Gains Tax Act now" not in r.data, "the CGT Act contents page lists sections and Parts")
    r = S.get("/tax/act/cgt/22C(1)")
    check(r.status_code == 200 and "3rd working day" in norm(r.data.decode()), "22C(1) opens")
    r = S.get("/tax/act/cgt/30B")
    check(r.status_code == 200 and b"mining title" in r.data, "section 30B opens")
    r = S.post("/tax/act/cgt/load")
    check(r.status_code in (302, 403), "a staff user is turned away from filing")
    r = S.get("/tax/legislative-updates")
    check(r.status_code == 200 and b"Capital Gains Tax Act [Chapter 23:01]" in r.data and b"/tax/cgt-act" in r.data, "the library page links to the CGT Act and the comparison")
    r = S.get("/tax/cgt-act")
    html = r.data.decode()
    check(r.status_code == 200 and "beside the app" in html and "contains no rate of tax" in html and "Inflation allowance" in html and "Dates the Act sets" in html, "the comparison page renders")
    check("/tax/act/cgt/11(2)(c)" in html and "Consumer Price Index" in html and "Nothing here is marked verified" in html and "marked verified:</strong>" not in html.replace("Nothing here is marked verified", ""), "it links the sections, quotes the Act, and marks nothing verified")
    check("Differs from the Act" in html and "Not in the app" in html and "Brought into line in this update" in html, "it shows differences and what was brought into line")
    check("(A - B) / C x C" in html, "it warns about the printed inflation formula")
    r = S.get("/tax/cgt-act?title_date=2026-03-10&pay_date=2026-10-09&sale_date=2026-03-10&notice_date=2026-01-15&damage_date=2026-05-01")
    html = r.data.decode()
    check("Thursday 09 April 2026" in html and "Wednesday 14 October 2026" in html and "Friday 31 December 2027" in html and "Saturday 14 February 2026" in html and "Monday 01 May 2028" in html, "the date calculator shows each last day")
    r = S.get("/tax/cgt-act?title_date=garbage")
    check(r.status_code == 200, "a bad date is ignored")

    # --- the Tax / CGT Advisory checklist already stored
    from seed import migrate_cgt_wording
    t = ChecklistTemplate(name="Tax / CGT Advisory", type=TAX_ADVISORY_TYPE, description="x"); db.session.add(t); db.session.flush()
    olds = [o for o, n in CG.PROGRAM_PAIRS]
    for i, o in enumerate(olds):
        db.session.add(ChecklistTemplateItem(template_id=t.id, section="Fact Finding", item_text=o, order=i))
    edited = ChecklistTemplateItem(template_id=t.id, section="Fact Finding", item_text="Obtain details of the asset(s)/transaction(s) in question (our wording)", order=50)
    db.session.add(edited)
    cl = Client(name="Test Client", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="CGT advice", type=TAX_ADVISORY_TYPE, partner_id=partner.id, acceptance_required=False); db.session.add(eng); db.session.commit()
    ei = EngagementChecklistItem(engagement_id=eng.id, section="Compliance & Filing", item_text="Confirm payment of tax due", status="Completed", order=1)
    ei2 = EngagementChecklistItem(engagement_id=eng.id, section="Compliance & Filing", item_text="Confirm payment of tax due (agreed with client)", order=2)
    db.session.add_all([ei, ei2]); db.session.commit()
    eid, e2id, eng_id, edited_id, tid = ei.id, ei2.id, eng.id, edited.id, t.id
    migrate_cgt_wording(); db.session.expire_all()
    new_texts = {i.item_text for i in ChecklistTemplateItem.query.filter_by(template_id=tid).all()}
    check(all(n in new_texts for o, n in CG.PROGRAM_PAIRS) and not any(o in new_texts for o in olds), "the stored template lines are reworded")
    check(all(text in new_texts for s_, text in CG.NEW_PROGRAM_ITEMS), "the template gets the three new lines")
    check(db.session.get(ChecklistTemplateItem, edited_id).item_text.endswith("(our wording)"), "a line a person edited is left alone")
    a = db.session.get(EngagementChecklistItem, eid); b = db.session.get(EngagementChecklistItem, e2id)
    check(a.item_text == dict(CG.PROGRAM_PAIRS)["Confirm payment of tax due"] and a.status == "Completed", "an engagement's checklist line is reworded and its tick kept")
    check(b.item_text == "Confirm payment of tax due (agreed with client)", "an edited engagement line is left alone")
    check(EngagementChecklistItem.query.filter_by(engagement_id=eng_id).count() == 2, "existing engagements do not get the new lines")
    n1 = ChecklistTemplateItem.query.filter_by(template_id=tid).count()
    migrate_cgt_wording()
    check(ChecklistTemplateItem.query.filter_by(template_id=tid).count() == n1, "running it again adds nothing")
    seeded = {text for s_, text in SD.TAX_CGT_PROGRAM}
    check(all(n in seeded for o, n in CG.PROGRAM_PAIRS) and all(text in seeded for s_, text in CG.NEW_PROGRAM_ITEMS), "the seeded constant is the new wording, so a new install needs no migration")
    P_ = app.test_client()
    with P_.session_transaction() as s_:
        s_["_user_id"] = str(partner.id); s_["_fresh"] = True
    r = P_.get(f"/engagements/{eng_id}?tab=tax"); html = r.data.decode()
    check(r.status_code == 200 and "/tax/act/cgt" in html and "/tax/cgt-act" in html and "/tax/act/vat" in html, "the Tax tab of a Tax Advisory engagement links to the Acts and the CGT comparison")
    t2 = ChecklistTemplate(name="Tax / CGT Advisory", type=TAX_ADVISORY_TYPE, description="replaced"); db.session.add(t2); db.session.flush()
    db.session.add(ChecklistTemplateItem(template_id=t2.id, section="x", item_text="Our own list", order=0)); db.session.commit()
    t2id = t2.id
    migrate_cgt_wording()
    check(ChecklistTemplateItem.query.filter_by(template_id=t2id).count() == 1, "a template the firm replaced with its own list is not topped up")

print("\n%d failure(s)" % len(fails))
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
