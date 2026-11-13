"""The Companies and Other Business Entities Act [Chapter 24:31] filed section by section, and set beside the Secretarial module: the Act
splits into its sections and schedules, replaces an older copy, resolves references such as 165(2) to its own words and is searched by Deep
Search; every section number quoted in the Secretarial wording exists in the Act; the plan's calendar follows the Act (annual return 21 days
after the registration anniversary, AGM within twelve months); wording already stored in an existing database is brought up to date without
touching text a person edited; and the COBE Act page puts the Act beside what the module does."""
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
import models as M
import act_import as AI, act_parser as AP, legislation_index as LI, cobe_checks as CC, cobe_text_updates as CU
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()

    older = LegislativeUpdate(title="Companies and Other Business Entities Act", instrument_type="Act", source_reference="COBE 2019",
                              extracted_text="Old company law text " * 30, extraction_status="extracted")
    vat = LegislativeUpdate(title="Value Added Tax Act [Chapter 23:12]", instrument_type="Act", extracted_text="vat text " * 30, extraction_status="extracted")
    db.session.add_all([older, vat]); db.session.commit()
    older_id, vat_id = older.id, vat.id
    st = AI.status("cobe")
    check(not st["loaded"] and st["existing_other_copy"] is not None and st["existing_other_copy"].id == older_id, "before filing: the older COBE copy is recognised (not the VAT Act)")

    # --- parsing
    text = AI.act_text("cobe")
    P = AP.parse_act(text)
    nums = [s["number"] for s in P["sections"]]
    check(len(P["sections"]) == 304 and len(P["schedules"]) == 10, "the Act splits into 304 sections and 10 schedules (got %d / %d)" % (len(P["sections"]), len(P["schedules"])))
    check(len(P["warnings"]) <= 1 and all("293" in w for w in P["warnings"]), "at most one warning, and it is the section 293 heading mismatch: %s" % P["warnings"])
    check(len(set(nums)) == len(nums) and nums[0] == "1" and nums[-1] == "304", "sections run 1..304 with none repeated")
    sec = {s["number"]: s for s in P["sections"]}
    check(sec["165"]["title"].startswith("Annual return") and "twenty-one (21) days" in sec["165"]["text"], "section 165 carries its title and the 21-day rule")
    check(sec["167"]["title"].rstrip(".") == "Annual general meeting" and "twelve months" in sec["167"]["text"], "section 167 is the AGM section")
    check(sec["165"]["part"].startswith("Chapter III, Part III") and "Sub-part B" in sec["165"]["part"], "section 165 sits in Chapter III, Part III, Sub-part B (%r)" % sec["165"]["part"])
    check(sec["1"]["part"] == "Chapter I, Part I" and "Companies and Other Business Entities Act" in sec["1"]["text"], "section 1 is in Chapter I, Part I with its text")
    check("Page " not in sec["72"]["text"][-120:] and "Veritas" not in "".join(s["text"] for s in P["sections"]), "page numbers and the Veritas footer are dropped")
    check([s["number"] for s in P["schedules"]] == list(range(1, 11)), "ten schedules numbered 1 to 10")
    check("MEMORANDUM" in P["schedules"][0]["text"].upper() and "RE-REGISTRATION" in P["schedules"][9]["text"].upper() and "PENALTIES" in P["schedules"][8]["text"].upper(), "Schedules 1, 9 and 10 hold the memorandum form, the penalties and the re-registration form")
    covered = sum(len(s["text"]) for s in P["sections"]) + sum(len(s["text"]) for s in P["schedules"])
    check(covered > 0.9 * len(text), "at least 90%% of the Act's text lands in a section or schedule (%.1f%%)" % (100.0 * covered / len(text)))
    PI = AP.parse_act(AI.act_text("ita")); PV = AP.parse_act(AI.act_text("vat"))
    check((len(PI["sections"]), len(PI["schedules"])) == (141, 36) and (len(PV["sections"]), len(PV["schedules"])) == (113, 2), "the Income Tax Act (141/36) and VAT Act (113/2) still split as before")

    # --- filing
    res = AI.load_act("cobe", user_id=partner.id)
    check(res["sections"] == 304 and res["schedules"] == 10 and res["update_id"] == older_id and not res["created"], "filing reuses and replaces the earlier copy")
    check(db.session.get(LegislativeUpdate, vat_id).extracted_text.startswith("vat text"), "the VAT Act entry is untouched")
    u = db.session.get(LegislativeUpdate, older_id)
    check(u.title == "Companies and Other Business Entities Act [Chapter 24:31]" and u.ai_status == "skipped" and len(u.chunks) == res["chunks"], "retitled, not auto-summarised, chunked")
    check("Companies & Corporate Law" in (u.areas_list if hasattr(u, "areas_list") else (u.areas or "")), "filed under Companies & Corporate Law")
    check(LegislativeProvision.query.filter_by(act_code="cobe").count() == 314, "314 provisions stored")
    heads = [c.heading for c in u.chunks]
    check(any(h.startswith("Section 165 - Annual return") for h in heads) and any(h.startswith("Ninth Schedule") for h in heads), "passages are headed by section number and title")
    check(max(len(c.text) for c in u.chunks) < 9000, "no passage is oversized")
    AI.load_act("cobe", user_id=partner.id)
    check(LegislativeProvision.query.filter_by(act_code="cobe").count() == 314 and LegislativeUpdate.query.filter(LegislativeUpdate.title.like("Companies and Other%")).count() == 1, "filing twice leaves no duplicates")
    AI.load_act("vat", user_id=partner.id)
    check(LegislativeProvision.query.filter_by(act_code="cobe").count() == 314 and LegislativeProvision.query.filter_by(act_code="vat").count() == 115, "COBE and VAT stay filed side by side")

    # --- references
    for ref, needle in [("165(2)", "twenty-one (21) days"), ("167(2)", "twelve months"), ("217(5)", "one month"), ("72(2)", "seven days"), ("198(1)", "ordinarily resident"),
                        ("303(9)", "twelve months"), ("121(2)", "one month"), ("178(1)", "one month"), ("sch9", "Three months"), ("sch10", "RE-REGISTRATION")]:
        f = AI.paragraph_text(ref, "cobe")
        check(f is not None and needle.lower() in re.sub(r"\s+", " ", f["text"]).lower(), "reference %s resolves to its own words (%s)" % (ref, needle))
    check(AI.paragraph_text("169(1)", "cobe")["text"].count("twenty-one days") >= 1, "169(1) holds the 21-day notice")
    with app.test_request_context():
        h = str(AI.ref_link("165(2)", code="cobe"))
        check('href="/tax/act/cobe/165(2)"' in h and "twenty-one" in h.lower(), "act_ref(code='cobe') links into the COBE Act with the paragraph as its tip")

    # --- Deep Search
    LI.invalidate()
    r = LI.search("annual return within 21 days of the anniversary of incorporation")
    check(r.hits and any("Section 165" in (p[0].heading or "") for h in r.hits[:3] for p in h.passages[:3]), "Deep Search for the annual return rule finds Section 165")
    r = LI.search("register of beneficial owners seven days material change")
    check(r.hits and any("Section 72" in (p[0].heading or "") for h in r.hits[:4] for p in h.passages[:3]), "Deep Search for beneficial owner updates finds Section 72")

    # --- every section number the Secretarial wording quotes exists in the Act, and the ones that were wrong are gone
    texts = []
    for name in ("SECRETARIAL_ENTITY_UNDERSTANDING_CHECKLIST_ITEMS", "SECRETARIAL_ANALYTICAL_REVIEW_POINTS", "SECRETARIAL_ACCEPTANCE_CHECKLIST_ITEMS", "SECRETARIAL_FINALISATION_CHECKLIST_ITEMS"):
        for it in getattr(M, name): texts.append(it[1])
    for q in M.SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS + M.SECRETARIAL_RISK_IMPACT_QUESTIONS:
        texts.append(q[1]); texts += list(q[2])
    for lst in M.SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES.values(): texts += lst
    import seed as SD
    texts += [t[1] for t in SD.SECRETARIAL_PROGRAM]
    blob = "\n".join(texts)
    cited = set()
    for m in re.finditer(r"\b(?:Sec(?:tion)?s?\.?)\s+((?:\d{1,3}[A-Z]?(?:\(\w+\))*(?:\s*(?:,|and|&|-|to)\s*)?)+)", blob):
        for n in re.findall(r"\d{1,3}[A-Z]?", re.sub(r"\(\w+\)", "", m.group(1))):
            cited.add(n)
    missing = sorted(n for n in cited if n not in sec)
    check(cited and not missing, "all %d section numbers quoted in the Secretarial wording exist in the Act (missing: %s)" % (len(cited), missing))
    for stale in ["Sec 310", "42 days", "Section 310", "(Sec 215)", "Sec 217/241", "under Sec 240", "(Sec 163)", "(Sec 162)", "Sec 157)", "(Sec 149)", "(Secs 93 and 95)", "Sec 93 & 95", "Section 93 & 95", "Secs 102 and 104", "Sec 196) and", "(Secs 16 and 25)", "Sec 170)", "Sec 176)", "20-25%", "within 14 days (Sec 160)"]:
        check(stale not in blob, "the wrong wording %r is gone" % stale)
    check("Form CR11) with the Registrar" not in blob, "the annual return is no longer called Form CR11")

    # --- the calendar logic
    ann, due = CC.annual_return_due(date(2010, 3, 15), date(2026, 1, 1))
    check(ann == date(2026, 3, 15) and due == date(2026, 4, 5), "registered 15 Mar 2010: anniversary 15 Mar 2026, return due 5 Apr 2026")
    ann, due = CC.annual_return_due(date(2012, 2, 29), date(2026, 1, 1))
    check(ann == date(2026, 2, 28) and due == date(2026, 3, 21), "a 29 February registration falls on 28 February in a non-leap year")
    ann, due = CC.annual_return_due(date(2010, 3, 15), date(2026, 3, 16))
    check(ann == date(2027, 3, 15), "after this year's anniversary the next one is used")
    check(CC.calendar_suggestions(None) is None, "no year-end, no suggestions")
    sg = CC.calendar_suggestions(date(2025, 12, 31), date(2010, 3, 15), date(2025, 2, 10), today=date(2025, 10, 1))
    check(sg["annual_return_target"] == date(2026, 4, 5), "annual return suggested 5 Apr 2026 (21 days after the 15 Mar anniversary)")
    check(sg["agm_target"] == date(2026, 2, 10) and sg["warnings"], "AGM held to twelve months after the last AGM (10 Feb 2026), with a warning")
    check(sg["agm_notice_target"] == date(2026, 1, 20), "notice 21 days before the AGM")
    check(sg["draft_financials_target"] <= date(2026, 2, 10) - __import__("datetime").timedelta(days=14), "draft statements ready 14 days before the AGM")
    sg2 = CC.calendar_suggestions(date(2025, 12, 31), None, None, today=date(2026, 1, 5))
    check(sg2["annual_return_target"] is None and sg2["agm_target"] == date(2026, 6, 30) and "registration" in sg2["basis"]["annual_return_target"].lower(), "no registration date: no annual return suggestion, AGM six months after year-end")
    check(sg["annual_return_target"] != sg["agm_target"] + __import__("datetime").timedelta(days=42), "the annual return is no longer AGM + 42 days")
    sg3 = CC.calendar_suggestions(date(2025, 12, 31), date(2010, 3, 15), date(2024, 1, 10), today=date(2026, 5, 1))
    check(any("missed" in w for w in sg3["warnings"]), "a last AGM more than twelve months ago is flagged")

    # --- findings and deadlines all point at real provisions
    bad = []
    for f in CC.FINDINGS:
        for r in f["refs"]:
            if AI.paragraph_text(r, "cobe") is None: bad.append(r)
    for t, refs, rule, pen in CC.DEADLINES:
        for r in refs:
            if AI.paragraph_text(r, "cobe") is None: bad.append(r)
    check(not bad, "every section in the findings and the time-limit table exists in the Act (bad: %s)" % bad)
    check({f["result"] for f in CC.FINDINGS} <= set(CC.RESULT_LABELS) and len(CC.FINDINGS) >= 20, "%d findings, all with a known result" % len(CC.FINDINGS))
    corr_bad = [c for c in CC.SECTION_CORRECTIONS if not re.search(r"\bs?\d", c[3])]
    check(not corr_bad, "every corrected section number says where the topic really is")

    # --- the pages
    S = app.test_client(); S.post("/auth/login", data={"username": "stf", "password": "x"})
    r = S.get("/tax/act/cobe")
    check(r.status_code == 200 and b"Annual return to be made by company" in r.data and b"Chapter III, Part III" in r.data and b"Tenth Schedule" in r.data and b"File the COBE Act now" not in r.data, "the COBE Act contents page lists sections, Parts and schedules")
    r = S.get("/tax/act/cobe/165(2)")
    check(r.status_code == 200 and b"twenty-one (21) days" in r.data, "165(2) opens")
    r = S.get("/tax/act/cobe/sch9")
    check(r.status_code == 200 and b"Ninth Schedule" in r.data, "the Ninth Schedule opens")
    r = S.post("/tax/act/cobe/load")
    check(r.status_code in (302, 403), "a staff user is turned away from filing")
    r = S.get("/tax/legislative-updates")
    check(r.status_code == 200 and b"Companies and Other Business Entities Act [Chapter 24:31]" in r.data and b"/tax/cobe-act" in r.data, "the library page links to the COBE Act and the comparison")
    r = S.get("/tax/cobe-act")
    html = r.data.decode()
    check(r.status_code == 200 and "beside the Secretarial module" in html and "Annual return - when it is due" in html and "21 days" in html, "the COBE Act page renders")
    check("twenty-one (21) days" in html and "Section numbers that pointed at a different topic" in html and "Civil penalty orders" in html, "it quotes the Act, lists the corrected numbers and the penalty categories")
    check("Differs from the Act" in html and "Not marked verified" not in html and "Nothing here is marked verified" in html, "it shows differences and marks nothing verified")

    # --- the Secretarial plan
    cl = Client(name="Zambezi Holdings (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Secretarial 2026", type="Secretarial", partner_id=partner.id, period_end=date(2025, 12, 31),
                     acceptance_required=False, secretarial_activities="meetings,statutory_records")
    db.session.add(eng); db.session.commit()
    eid = eng.id
    P_ = app.test_client()
    with P_.session_transaction() as s:
        s["_user_id"] = str(partner.id); s["_fresh"] = True
    r = P_.get(f"/engagements/{eid}?tab=planning")
    html = r.data.decode()
    check(r.status_code == 200 and "registration anniversary" in html and "Date of incorporation, registration or re-registration" in html and "What the COBE Act sets as time limits" in html, "the plan has the registration date, the last AGM date and the time-limit table")
    check("(suggested: 30 Jun 2026)" in html and "annual return filing target" not in html.lower().replace("annual return filing target - last day", ""), "AGM suggested 30 Jun 2026; no annual return suggestion until a registration date is entered")
    check("CR11/CR23" not in html and "42" not in re.sub(r"\d{4,}", "", re.search(r"2\. Annual Statutory Compliance Calendar.*?3\. Board", html, re.S).group(0)).replace("42px", ""), "no CR11/CR23 or 42-day wording on the calendar")
    r = P_.post(f"/engagements/{eid}/secretarial-plan/save", data={"entity_categorization": "Private company", "registration_date": "2010-03-15", "last_agm_date": "2025-02-10"})
    plan = SecretarialPlan.query.filter_by(engagement_id=eid).first()
    check(r.status_code == 302 and plan.registration_date == date(2010, 3, 15) and plan.last_agm_date == date(2025, 2, 10), "registration date and last AGM date are saved")
    r = P_.get(f"/engagements/{eid}?tab=planning")
    html = r.data.decode()
    check("(suggested: 05 Apr 2026)" in html and "(suggested: 10 Feb 2026)" in html and "later than twelve months after the last AGM" in html, "now the annual return is suggested 05 Apr 2026, the AGM 10 Feb 2026, with the warning")
    check('value="2010-03-15"' in html and 'value="2025-02-10"' in html, "the saved dates show in the boxes")
    r = P_.post(f"/engagements/{eid}/secretarial-plan/save", data={"entity_categorization": "Private company", "registration_date": "", "last_agm_date": ""})
    plan = SecretarialPlan.query.filter_by(engagement_id=eid).first()
    check(plan.registration_date is None and plan.last_agm_date is None, "the dates can be cleared")

    # --- the Execution Plan gets the new tasks
    import engagements as EG
    r = P_.post(f"/engagements/{eid}/substantive-procedures/generate"); db.session.expire_all()
    items = [i for a in SubstantiveProcedureArea.query.filter_by(engagement_id=eid).all() for i in a.items]
    texts_ = {i.procedure_text: i for i in items}
    t1 = [t for t in texts_ if t.startswith("Prepare the annual return certificates")]
    t2 = [t for t in texts_ if t.startswith("Confirm the company has a secretary ordinarily resident")]
    t3 = [t for t in texts_ if t.startswith("Diarise the next AGM")]
    check(t1 and t2 and t3, "the three new tasks are added to the Execution Plan")
    ar = [i for t, i in texts_.items() if t.startswith("Lodge the Annual Return")][0]
    check("21 days" in ar.procedure_text and ar.trigger_event.startswith("Anniversary") and ar.target_output == "Stamped annual return receipt", "the annual return task carries the 21-day rule, its trigger and its output")

    # --- stored wording is brought up to date, hand edits are left alone
    from seed import migrate_cobe_wording, migrate_cr_form_codes, migrate_secretarial_finalisation_company_summary, SECRETARIAL_FINALISATION_COMPANY_SUMMARY_ITEM
    eng2 = Engagement(client_id=cl.id, title="Old secretarial", type="Secretarial", partner_id=partner.id, acceptance_required=False)
    db.session.add(eng2); db.session.commit()
    eu = EntityUnderstanding(engagement_id=eng2.id); db.session.add(eu); db.session.flush()
    old_e, new_e = CU.ENTITY_PAIRS[1]
    e_old = EntityUnderstandingChecklistItem(entity_understanding_id=eu.id, section="Constitutional Documents & Corporate Governance", item_text=old_e, response="Yes", comment="two directors", order=1)
    e_edit = EntityUnderstandingChecklistItem(entity_understanding_id=eu.id, section="x", item_text=old_e + " (our wording)", order=2)
    db.session.add_all([e_old, e_edit])
    area = SubstantiveProcedureArea(engagement_id=eng2.id, area="Annual Returns & Filings Log"); db.session.add(area); db.session.flush()
    p_ar = [p for p in CU.EXEC_PAIRS if p[0].startswith("Lodge the Annual Return (Form CR11)")][0]
    x_old = SubstantiveProcedureItem(area_id=area.id, procedure_text=p_ar[0], source="baseline", order=0, trigger_event="Post-AGM", responsible_role="Compliance Officer", target_output="Stamped Form CR11 receipt")
    x_chain = SubstantiveProcedureItem(area_id=area.id, procedure_text="Lodge Form CR14 for changes in registered office address within 14 days (Sec 160).", source="baseline", order=1, target_output="Certified Form CR14 copy")
    x_edit = SubstantiveProcedureItem(area_id=area.id, procedure_text=p_ar[0], source="baseline", order=2, trigger_event="Our own trigger", target_output="Stamped Form CR11 receipt")
    db.session.add_all([x_old, x_chain, x_edit])
    fc = FinalisationChecklist(engagement_id=eng2.id); db.session.add(fc); db.session.flush()
    f_old = FinalisationChecklistItem(finalisation_checklist_id=fc.id, section="Registry & Reconciliations", item_text=CU.FINAL_PAIRS[0][0], response="No", order=1)
    f_cs = FinalisationChecklistItem(finalisation_checklist_id=fc.id, section="Registry & Reconciliations", item_text=CU.FINAL_PAIRS[4][0], order=2)
    db.session.add_all([f_old, f_cs])
    ca = ClientAcceptance(engagement_id=eng2.id); db.session.add(ca); db.session.flush()
    a_old = ClientAcceptanceChecklistItem(client_acceptance_id=ca.id, section="KYC & Ultimate Beneficial Ownership", item_text=CU.ACCEPTANCE_PAIRS[0][0], order=1)
    db.session.add(a_old)
    ar_ = AnalyticalReview(engagement_id=eng2.id); db.session.add(ar_); db.session.flush()
    l_old = AnalyticalReviewLine(analytical_review_id=ar_.id, label=CU.ANALYTICAL_PAIRS[2][0], source="manual", explanation="Done")
    db.session.add(l_old)
    ct = ChecklistTemplate.query.filter_by(name="Secretarial Engagement Checklist (COBE Act)").first()
    if ct is None:
        ct = ChecklistTemplate(name="Secretarial Engagement Checklist (COBE Act)", type="Secretarial"); db.session.add(ct); db.session.flush()
    t_old = ChecklistTemplateItem(template_id=ct.id, section="5. Annual & Event-driven Filings", item_text=CU.PROGRAM_PAIRS[11][0], order=900)
    ec_old = EngagementChecklistItem(engagement_id=eng2.id, section="5. Annual & Event-driven Filings", item_text=CU.PROGRAM_PAIRS[11][0], order=1)
    db.session.add_all([t_old, ec_old]); db.session.commit()
    ids2 = dict(e_old=e_old.id, e_edit=e_edit.id, x_old=x_old.id, x_chain=x_chain.id, x_edit=x_edit.id, f_old=f_old.id, f_cs=f_cs.id, a_old=a_old.id, l_old=l_old.id, t_old=t_old.id, ec_old=ec_old.id)

    migrate_cr_form_codes(); migrate_cobe_wording(); migrate_secretarial_finalisation_company_summary()
    g = lambda m, k: db.session.get(m, ids2[k])
    check(g(EntityUnderstandingChecklistItem, "e_old").item_text == new_e and g(EntityUnderstandingChecklistItem, "e_old").response == "Yes" and g(EntityUnderstandingChecklistItem, "e_old").comment == "two directors", "an entity question is reworded and its answer and comment are kept")
    check(g(EntityUnderstandingChecklistItem, "e_edit").item_text == old_e + " (our wording)", "a question a person edited by hand is left alone")
    xo = g(SubstantiveProcedureItem, "x_old")
    check(xo.procedure_text == p_ar[1] and xo.trigger_event == p_ar[3] and xo.target_output == p_ar[5] and xo.responsible_role == "Compliance Officer", "an Execution Plan task gets the new wording, trigger and output")
    xe = g(SubstantiveProcedureItem, "x_edit")
    check(xe.procedure_text == p_ar[1] and xe.trigger_event == "Our own trigger", "a trigger a person changed is kept")
    xc = g(SubstantiveProcedureItem, "x_chain")
    check(xc.procedure_text.startswith("Record any change of registered office or postal address") and "Sec 31" in xc.procedure_text, "an old CR14 task passes through the CR5 migration to the corrected wording")
    _chain = CU.FINAL_PAIRS[0][1]
    for _o, _n in CU.FINAL_PAIRS:
        if _o == _chain: _chain = _n
    check(g(FinalisationChecklistItem, "f_old").item_text == _chain and "Form CR17 (declaration that the AGM was held)" in _chain and g(FinalisationChecklistItem, "f_old").response == "No", "a finalisation item is reworded (through the d24 wording to the corrected one) and its answer kept")
    check(g(FinalisationChecklistItem, "f_cs").item_text == SECRETARIAL_FINALISATION_COMPANY_SUMMARY_ITEM[1], "the Company Summary line matches the new constant")
    cs_count = FinalisationChecklistItem.query.filter_by(finalisation_checklist_id=fc.id, item_text=SECRETARIAL_FINALISATION_COMPANY_SUMMARY_ITEM[1]).count()
    check(cs_count == 1, "the Company Summary line is not added a second time")
    check(g(ClientAcceptanceChecklistItem, "a_old").item_text == CU.ACCEPTANCE_PAIRS[0][1], "the acceptance question is reworded (more than 20%)")
    check(g(AnalyticalReviewLine, "l_old").label == CU.ANALYTICAL_PAIRS[2][1] and g(AnalyticalReviewLine, "l_old").explanation == "Done", "the analytical review point is reworded and its write-up kept")
    check(g(ChecklistTemplateItem, "t_old").item_text == CU.PROGRAM_PAIRS[11][1] and g(EngagementChecklistItem, "ec_old").item_text == CU.PROGRAM_PAIRS[11][1], "the checklist template and the checklist copied from it are reworded")
    before = (g(EntityUnderstandingChecklistItem, "e_old").item_text, g(SubstantiveProcedureItem, "x_old").procedure_text)
    migrate_cobe_wording()
    check(before == (g(EntityUnderstandingChecklistItem, "e_old").item_text, g(SubstantiveProcedureItem, "x_old").procedure_text), "running it again changes nothing")
    # the lists the constants carry are the NEW wording, so a freshly seeded checklist needs no migration
    olds = [o for o, n in CU.ENTITY_PAIRS] + [o for o, n in CU.FINAL_PAIRS] + [o for o, n in CU.PROGRAM_PAIRS] + [o for o, n in CU.ACCEPTANCE_PAIRS]
    allnew = "\n".join(texts)
    check(not any(o in allnew for o in olds), "no seeded constant still carries any of the old wording")
    # and the sync tops up an old checklist with the new questions without duplicating the reworded ones

print("\n%d failure(s)" % len(fails))
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
