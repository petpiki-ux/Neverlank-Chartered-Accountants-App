"""The Registrar's CR forms on a Secretarial engagement: the list CR1 to CR35 (with the two forms the Act itself prescribes, the annual
return and the re-registration form), each tied to a section of the COBE Act that exists in the filed Act; the last day for lodging worked
out from the Act's time limit; the Registry Forms tab and its filing log (add, update, delete, overdue flag, Secretarial engagements only);
and the checklist wording that named the wrong form (CR2, CR12, CR23) corrected in existing databases without touching hand edits."""
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
from datetime import date, timedelta
from app import create_app
from extensions import db
from models import *
import act_import as AI, act_parser as AP, cr_forms as CF, cobe_text_updates as CU, cobe_checks as CC
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    # --- the list
    codes = [f["code"] for f in CF.CR_FORMS]
    check(len(codes) == len(set(codes)) == 37, "37 distinct entries (got %d)" % len(codes))
    check(all(("CR%d" % n) in codes for n in range(1, 36)), "CR1 to CR35 are all there")
    check({"AR", "RR"} <= set(codes), "the annual return (AR) and the re-registration form (RR) are added")
    check(all(f["title"] and f["used_when"] and f["group"] in CF.GROUPS and f["rule_text"] for f in CF.CR_FORMS), "every form has a title, a 'used when', a group and a time-limit note")
    t = {f["code"]: f["title"] for f in CF.CR_FORMS}
    check(t["CR2"].startswith("Application for search as to availability of name") and t["CR35"].startswith("Application to inspect"), "CR2 is the name search and CR35 the inspection application")
    check(t["CR23"] == "Statutory report" and t["CR12"].startswith("Notice of place where register of mortgages") and t["CR11"] == "Return of allotments", "CR23 is the statutory report, CR12 the mortgage register notice, CR11 the allotments return")
    check(t["CR3"] == t["CR4"], "CR3 and CR4 carry the same title, as on the list supplied")
    check([CF.sort_key(c) for c in sorted(codes, key=CF.sort_key)][:3] == [(0, 0), (0, 1), (1, 1)], "AR and RR sort ahead of CR1")

    # --- every section the list points at exists in the Act
    P = AP.parse_act(AI.act_text("cobe"))
    secs = {s["number"] for s in P["sections"]}
    def exists(ref):
        if ref.startswith("Sch"): return int(ref[3:]) in {x["number"] for x in P["schedules"]}
        return re.match(r"\d+[A-Za-z]?", ref).group(0) in secs
    bad = [(f["code"], r) for f in CF.CR_FORMS for r in f["refs"] if not exists(r)]
    check(not bad, "every section the forms point at is in the Act: %s" % bad)
    sec = {s["number"]: s["text"].lower() for s in P["sections"]}
    check("one month" in sec["155"] and "one month" in sec["217"] and "21" in sec["165"] and "seven days" in sec["72"], "the Act texts behind the time limits say what the rules say (s155, s217, s165, s72)")

    # --- due dates
    check(CF.due_date_for("CR6", date(2026, 1, 31)) == date(2026, 2, 28), "CR6: one month from 31 Jan is 28 Feb")
    check(CF.due_date_for("CR11", date(2026, 3, 10)) == date(2026, 4, 10), "CR11: one month from 10 Mar")
    check(CF.due_date_for("AR", date(2026, 3, 15)) == date(2026, 4, 5), "annual return: 21 days from 15 Mar is 5 Apr")
    check(CF.due_date_for("CR16", date(2026, 3, 1)) == date(2026, 3, 8), "CR16: seven days")
    check(CF.due_date_for("CR17", date(2026, 3, 1)) is None and CF.due_date_for("CR6", None) is None and CF.due_date_for("ZZ", date(2026, 3, 1)) is None, "no limit, no date or an unknown form gives no due date")
    check(CF.is_overdue("Not started", date(2020, 1, 1), date(2026, 1, 1)) and not CF.is_overdue("Lodged", date(2020, 1, 1), date(2026, 1, 1)) and not CF.is_overdue("Prepared", None, date(2026, 1, 1)), "overdue only while open and past the date")
    sel = CF.forms_for_activities(["directorship"])
    check(any(f["code"] == "CR6" for f in sel) and not any(f["code"] == "CR8" for f in sel), "forms for 'directorship' include CR6, not CR8")
    check(len(CF.forms_for_activities([])) == 37, "no activity chosen gives every form")

    # --- the tab
    AI.load_act("cobe")
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    other = User(username="oth", name="Other O", role="staff"); other.set_password("x")
    db.session.add_all([partner, staff, other]); db.session.commit()
    cl = Client(name="Zambezi Holdings (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="Secretarial 2026", type="Secretarial", partner_id=partner.id, period_end=date(2025, 12, 31),
                     acceptance_required=False, secretarial_activities="directorship,share_capital")
    aud = Engagement(client_id=cl.id, title="Audit 2026", type="Audit", partner_id=partner.id, period_end=date(2025, 12, 31), acceptance_required=False)
    gated = Engagement(client_id=cl.id, title="Gated secretarial", type="Secretarial", partner_id=partner.id, acceptance_required=True)
    db.session.add_all([eng, aud, gated]); db.session.commit()
    eid, aid, gid = eng.id, aud.id, gated.id
    C = app.test_client()
    with C.session_transaction() as s:
        s["_user_id"] = str(partner.id); s["_fresh"] = True
    r = C.get(f"/engagements/{eid}?tab=overview"); html = r.data.decode()
    check(r.status_code == 200 and "tab=registry_forms" in html and ">Registry Forms" in html, "a Secretarial engagement has a Registry Forms tab link")
    r = C.get(f"/engagements/{aid}?tab=overview"); html = r.data.decode()
    check("tab=registry_forms" not in html, "an Audit engagement does not")
    r = C.get(f"/engagements/{aid}?tab=registry_forms"); html = r.data.decode()
    check(r.status_code == 200 and "Registry filing log" not in html, "the tab address on an Audit engagement just shows the overview")
    r = C.get(f"/engagements/{eid}?tab=registry_forms"); html = r.data.decode()
    check(r.status_code == 200 and "Registry filing log" in html and "The Registrar's forms (CR1 to CR35)" in html, "the Registry Forms tab renders")
    check(all(("<strong>CR%d</strong>" % n) in html for n in range(1, 36)) and "<strong>AR</strong>" in html and "<strong>RR</strong>" in html, "every form is listed")
    check("Application for search as to availability of name" in html and "Used when" in html and "Within one month of the change" in html, "descriptions, 'used when' and time limits show")
    check("/tax/act/cobe/217" in html and "/tax/act/cobe/165" in html, "the section references link to the COBE Act in the library")
    check(html.count("for this engagement") >= 5 and 'background:#fff8e1' in html, "forms for the engagement's chosen activities are highlighted")
    check("Nothing logged yet" in html, "the log starts empty")
    r = C.get(f"/engagements/{gid}?tab=registry_forms"); html = r.data.decode()
    check(r.status_code == 200 and "Registry filing log" not in html and "Client Acceptance" in html, "the tab is locked behind Client Acceptance like the rest of the work")

    # --- the filing log
    r = C.post(f"/engagements/{eid}/registry-forms/add", data={"form_code": "cr6", "subject": "Appointment of J. Moyo", "event_date": "2026-01-31"})
    f = RegistryFormFiling.query.filter_by(engagement_id=eid).first()
    check(r.status_code == 302 and f and f.form_code == "CR6" and f.due_date == date(2026, 2, 28) and f.status == "Not started" and f.created_by_id == partner.id, "a filing is added, the code upper-cased and the due date worked out")
    r = C.post(f"/engagements/{eid}/registry-forms/add", data={"form_code": "CR99"})
    check(RegistryFormFiling.query.filter_by(engagement_id=eid).count() == 1, "an unknown form is refused")
    r = C.post(f"/engagements/{eid}/registry-forms/add", data={"form_code": "CR17", "event_date": "2026-03-01", "due_date": "2026-03-20", "status": "Prepared"})
    g17 = RegistryFormFiling.query.filter_by(form_code="CR17").first()
    check(g17.due_date == date(2026, 3, 20) and g17.status == "Prepared", "a typed due date is kept")
    r = C.post(f"/engagements/{eid}/registry-forms/add", data={"form_code": "CR11", "event_date": "2026-02-01", "status": "Lodged"})
    g11 = RegistryFormFiling.query.filter_by(form_code="CR11").first()
    check(g11.status == "Lodged" and g11.lodged_date == date.today(), "adding a filing as Lodged records today as the lodging date")
    r = C.get(f"/engagements/{eid}?tab=registry_forms"); html = r.data.decode()
    check("Appointment of J. Moyo" in html and "28 Feb 2026" not in html or "2026-02-28" in html, "the log shows the filing")
    check("past the last day for lodging" in html and "CR6" in html and 'class="badge overdue"' in html, "an open filing past its date is flagged overdue")
    check(">Registry Forms (3)" in html, "the tab shows the number of entries")
    r = C.post(f"/engagements/registry-forms/{f.id}/update", data={"subject": "Appointment of J. Moyo", "event_date": "2026-01-31", "due_date": "", "status": "Registered", "lodged_date": "2026-02-10", "registrar_reference": "R-123", "notes": "Stamped copy on file"})
    db.session.expire_all(); f = db.session.get(RegistryFormFiling, f.id)
    check(f.status == "Registered" and f.lodged_date == date(2026, 2, 10) and f.registrar_reference == "R-123" and f.due_date == date(2026, 2, 28) and not f.is_overdue, "an entry is updated; a blank due date is worked out again; Registered is not overdue")
    r = C.post(f"/engagements/registry-forms/{f.id}/update", data={"status": "Lodged"})
    db.session.expire_all(); f = db.session.get(RegistryFormFiling, f.id)
    check(f.status == "Lodged" and f.lodged_date == date.today(), "Lodged with no date records today")
    r = C.post(f"/engagements/registry-forms/{g17.id}/delete")
    check(db.session.get(RegistryFormFiling, g17.id) is None and RegistryFormFiling.query.filter_by(engagement_id=eid).count() == 2, "an entry is deleted")
    r = C.post(f"/engagements/{aid}/registry-forms/add", data={"form_code": "CR6"})
    check(r.status_code == 404 and RegistryFormFiling.query.filter_by(engagement_id=aid).count() == 0, "a filing cannot be added to a non-Secretarial engagement")
    O = app.test_client()
    with O.session_transaction() as s:
        s["_user_id"] = str(other.id); s["_fresh"] = True
    r = O.post(f"/engagements/{eid}/registry-forms/add", data={"form_code": "CR6"})
    check(r.status_code == 403 and RegistryFormFiling.query.filter_by(engagement_id=eid).count() == 2, "someone not on the engagement cannot add")
    r = O.post(f"/engagements/registry-forms/{f.id}/delete")
    check(r.status_code == 403 and db.session.get(RegistryFormFiling, f.id) is not None, "someone not on the engagement cannot delete")
    db.session.delete(db.session.get(Engagement, eid)); db.session.commit()
    check(RegistryFormFiling.query.count() == 0, "deleting the engagement removes its filing log")

    # --- the corrected checklist wording
    from seed import migrate_cobe_wording
    eng2 = Engagement(client_id=cl.id, title="Old secretarial", type="Secretarial", partner_id=partner.id, acceptance_required=False)
    db.session.add(eng2); db.session.commit()
    fc = FinalisationChecklist(engagement_id=eng2.id); db.session.add(fc); db.session.flush()
    olds = {
        "search": "Final registry search executed via Form CR2 (Search Application) to verify all lodgments processed during the period reflect on the public register? [SEC-FIN-01]",
        "ar_orig": CU.FINAL_PAIRS[0][0],
        "ar_d24": CU.FINAL_PAIRS[0][1],
        "cr12_d24": [o for o, n in CU.FINAL_PAIRS if "Form CR12 (Satisfaction" in o][0],
        "cr12_orig": "Register of Charges and Debentures reconciled - all charges, mortgages, or satisfactions created or discharged during the year (Sec 162-164) matched against filings on Form CR12 (Satisfaction/Register of Mortgages & Charges)?",
        "edited": CU.FINAL_PAIRS[0][1] + " (our wording)",
    }
    rows = {}
    for i, (k, tx) in enumerate(olds.items()):
        rows[k] = FinalisationChecklistItem(finalisation_checklist_id=fc.id, section="Registry & Reconciliations", item_text=tx, response="Yes", order=i)
        db.session.add(rows[k])
    db.session.commit(); ids = {k: v.id for k, v in rows.items()}
    migrate_cobe_wording(); db.session.expire_all()
    txt = lambda k: db.session.get(FinalisationChecklistItem, ids[k]).item_text
    check("Form CR35" in txt("search") and "CR2" not in txt("search") and "[SEC-FIN-01]" in txt("search"), "the registry-search item now names CR35, not CR2")
    check(txt("ar_orig") == txt("ar_d24") and "Form CR17 (declaration that the AGM was held)" in txt("ar_d24") and "Form CR23 within one month" in txt("ar_d24") and "CR23 / Form CR17" not in txt("ar_d24"), "the annual-return item, from the original or the d24 wording, names CR17 and CR23 correctly")
    check(txt("cr12_orig") == txt("cr12_d24") and "notice of the place where the register is kept" in txt("cr12_d24") and "Satisfaction/Register" not in txt("cr12_d24"), "the mortgage-register item, from either wording, describes CR12 correctly")
    check(txt("edited").endswith("(our wording)"), "an item a person edited is left alone")
    check(all(db.session.get(FinalisationChecklistItem, i).response == "Yes" for i in ids.values()), "answers are kept")
    seeded = [t for s_, t in SECRETARIAL_FINALISATION_CHECKLIST_ITEMS]
    check(txt("ar_d24") in seeded and txt("search") in seeded and txt("cr12_d24") in seeded, "the migrated wording is exactly what a new engagement is seeded with")
    allold = [o for o, n in CU.FINAL_PAIRS]
    check(not any(o in seeded for o in allold), "no seeded finalisation item still carries any old wording")
    check("CR2 (Search" not in "".join(seeded) and "CR23 / Form CR17" not in "".join(seeded), "the wrong form labels are gone from the seeded items")
    ff = [x for x in CC.FINDINGS if x["topic"].startswith("Form numbers")][0]
    check("CR1 to CR35" in ff["topic"] and "CR35" in ff["note"] and "Registry Forms" in ff["note"], "the COBE comparison finding on form numbers reflects the supplied list")

print("\n%d failure(s)" % len(fails))
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
