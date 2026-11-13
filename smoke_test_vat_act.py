"""The Value Added Tax Act [Chapter 23:12] filed section by section, and set beside the VAT register: the Act splits into its sections and
schedules, replaces an older copy, resolves references such as 16(2)(a) to its own words and is searched by Deep Search; the VAT register
shows exempt and zero-rated sales apart, lets input tax be marked not deductible (section 16(2)), flags points to look at in a month, shows the
return due date, and the VAT Act page puts the Act beside what the register does."""
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
import act_import as AI, act_parser as AP, legislation_index as LI, vat_checks as VC
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
def close(a, b): return abs(a - b) < 0.005

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()

    # an older copy of the VAT Act and the Income Tax Act already in the library
    old = LegislativeUpdate(title="Value Added Tax Act", instrument_type="Act", source_reference="VAT Act 2019",
                            extracted_text="Old VAT text " * 30, extraction_status="extracted")
    ita = LegislativeUpdate(title="Income Tax Act [Chapter 23:06]", instrument_type="Act", extracted_text="Income tax text " * 30, extraction_status="extracted")
    db.session.add_all([old, ita]); db.session.commit()
    old_id, ita_id = old.id, ita.id
    st = AI.status("vat")
    check(not st["loaded"] and st["existing_other_copy"] is not None and st["existing_other_copy"].id == old_id, "before filing: the older VAT Act is recognised (not the Income Tax Act)")

    # --- parsing
    text = AI.act_text("vat")
    P = AP.parse_act(text)
    nums = [s["number"] for s in P["sections"]]
    check(len(P["sections"]) == 113 and len(P["schedules"]) == 2 and not P["warnings"], "the Act splits into 113 sections and 2 schedules with no warnings (got %d / %d: %s)" % (len(P["sections"]), len(P["schedules"]), P["warnings"]))
    check(all(n in nums for n in ["1", "2", "3", "6", "8", "10", "11", "12B", "12F", "13A", "15", "16", "20", "20A", "21", "22", "23", "27", "28", "38A", "39", "39A", "44", "45A", "50A", "63A", "68CC", "68EE", "81A", "81B", "85"]), "key sections are all found, including the lettered ones")
    check(len(set(nums)) == len(nums), "no section is found twice")
    sec = {s["number"]: s for s in P["sections"]}
    check(sec["6"]["title"] == "Value-added tax" and sec["6"]["part"] == "Part III" and "Charging Act" in sec["6"]["text"], "section 6 carries its title, Part and text")
    check(sec["40"]["repealed"] and sec["51"]["repealed"] and not sec["39"]["repealed"], "repealed sections 40 and 51 are marked, 39 is not")
    check("Fiscal Tax Invoice" in sec["20"]["text"] and "Exempt supplies" in sec["11"]["title"] and "zero" in sec["10"]["text"].lower(), "sections 10, 11 and 20 hold their own text")
    check("PART IXA" not in sec["56"]["text"] and "PART X" not in sec["56"]["text"][-200:], "a Part heading is not left at the end of the section before it")
    check(sec["85"]["title"] == "Amendment of Acts", "the last section's title does not swallow the schedule list (%r)" % sec["85"]["title"])
    check([s["number"] for s in P["schedules"]] == [1, 2] and "Civil Penalty" in AI.ACTS["vat"]["schedule_titles"][1], "two schedules, the first re-inserted as Civil Penalty Orders")
    check(not re.search(r"Page \d+ of 178", "\n".join(s["text"] for s in P["sections"])), "the 'Page n of 178' footers are dropped")
    covered = sum(len(s["text"]) for s in P["sections"]) + sum(len(s["text"]) for s in P["schedules"])
    check(covered > 0.9 * len(text), "at least 90%% of the Act's text lands in a section or schedule (%.1f%%)" % (100.0 * covered / len(text)))
    flat = "\n".join(l.strip() for l in text.split("\n"))
    P2 = AP.parse_act(flat)
    check(len(P2["sections"]) == 113 and len(P2["schedules"]) == 2, "the same split from text with no indentation (as the PDF reader returns it)")
    # the Income Tax Act still splits as before
    PI = AP.parse_act(AI.act_text("ita"))
    check(len(PI["sections"]) == 141 and len(PI["schedules"]) == 36, "the Income Tax Act still splits into 141 sections and 36 schedules")

    # --- filing
    res = AI.load_act("vat", user_id=partner.id)
    check(res["sections"] == 113 and res["schedules"] == 2 and res["update_id"] == old_id and not res["created"], "filing reuses and replaces the earlier copy of the VAT Act")
    check(db.session.get(LegislativeUpdate, ita_id).extracted_text.startswith("Income tax"), "the Income Tax Act entry is untouched")
    u = db.session.get(LegislativeUpdate, old_id)
    check(u.title == "Value Added Tax Act [Chapter 23:12]" and u.ai_status == "skipped" and len(u.chunks) == res["chunks"] and u.tax_head, "retitled, not auto-summarised, chunked")
    check(LegislativeProvision.query.filter_by(act_code="vat").count() == 115, "115 provisions stored")
    heads = [c.heading for c in u.chunks]
    check(any(h.startswith("Section 16 - Permissible deductions in respect of input tax") for h in heads) and any(h.startswith("Second Schedule") for h in heads), "passages are headed by section number and title")
    check(max(len(c.text) for c in u.chunks) < 9000, "no passage is oversized")
    again = AI.load_act("vat", user_id=partner.id)
    check(again["update_id"] == old_id and LegislativeProvision.query.filter_by(act_code="vat").count() == 115, "filing twice leaves no duplicates")
    AI.load_act("ita", user_id=partner.id)
    check(LegislativeProvision.query.filter_by(act_code="vat").count() == 115 and LegislativeProvision.query.filter_by(act_code="ita").count() == 177, "both Acts stay filed side by side")
    check(LegislativeUpdate.query.filter(LegislativeUpdate.instrument_type == "Act").count() == 2, "two Acts in the library, no duplicates")
    # the Income Tax Act loading must not have replaced the VAT Act (title 'income tax act' vs 'value added tax act')
    check(db.session.get(LegislativeUpdate, old_id).title.startswith("Value Added"), "the VAT Act was not overwritten by the Income Tax Act")

    # --- references
    for ref, word in [("6(1)", "Charging Act"), ("8(1)", "whichever time is earlier"), ("8(1)(a)", "invoice is issued"), ("10(1)(a)", "exported"), ("10(3)", "documentary proof"),
                      ("11(h)", "medical services"), ("15(2)(a)", "tax invoice"), ("15(2)(d)", "bill of entry"), ("16(1)", "intended use"), ("16(2)(a)", "entertainment"),
                      ("16(2)(b)", "subscriptions"), ("16(2)(d)", "motor vehicle"), ("16(2)(e)", "exportation"), ("20(4)(a)", "Fiscal Tax Invoice"), ("20(5)", "does not exceed"),
                      ("21(3)", "credit note"), ("22(1)", "irrecoverable"), ("23(1)", "registered"), ("27(1)", "Category C"), ("28(1)", "25th day"), ("39(2)", "penalty"), ("44(1)", "6 years")]:
        x = AI.paragraph_text(ref, "vat")
        check(x and x["exact"] and word.lower() in x["text"].lower(), "VAT Act %s is found (%s)" % (ref, word))
    pa = AI.paragraph_text("16(2)(a)", "vat")
    check("(b) in respect of any fees" not in pa["text"], "16(2)(a) stops before (b)")
    check(AI.paragraph_text("8(1a)", "vat")["exact"] and "imported services" in AI.paragraph_text("8(1a)", "vat")["text"], "8(1a) is the imported services time of supply")
    check(AI.paragraph_text("999", "vat") is None, "an unknown section is None")

    # --- Deep Search reads it
    LI.invalidate()
    r = LI.search("input tax denied entertainment motor vehicle club subscription")
    check(r.hits and any("Section 16" in (p[0].heading or "") for h in r.hits[:3] for p in h.passages[:3]), "Deep Search for denied input tax finds Section 16")
    r = LI.search("time of supply invoice issued payment received whichever is earlier")
    check(r.hits and any("Section 8" in (p[0].heading or "") for h in r.hits[:3] for p in h.passages[:3]), "Deep Search for time of supply finds Section 8")
    r = LI.search("tax invoice must contain Fiscal Tax Invoice registration number")
    check(r.hits and any("Section 20" in (p[0].heading or "") for h in r.hits[:3] for p in h.passages[:3]), "Deep Search for tax invoice particulars finds Section 20")

    # --- helper for templates
    with app.test_request_context():
        h = str(AI.ref_link("16(2)(a)", code="vat"))
        check('href="/tax/act/vat/16(2)(a)"' in h and "entertainment" in h.lower(), "act_ref(code='vat') links into the VAT Act with the paragraph as its tip")

    # --- Act pages
    S = app.test_client(); S.post("/auth/login", data={"username": "stf", "password": "x"})
    r = S.get("/tax/act/vat")
    check(r.status_code == 200 and b"Permissible deductions in respect of input tax" in r.data and b"Second Schedule" in r.data and b"File the VAT Act now" not in r.data, "the VAT Act contents page lists sections and schedules")
    r = S.get("/tax/act/vat/16(2)(d)")
    check(r.status_code == 200 and b"motor vehicle" in r.data and b"Section 16" in r.data, "16(2)(d) opens the motor vehicle paragraph")
    r = S.get("/tax/act/vat/sch1")
    check(r.status_code == 200 and b"First Schedule" in r.data, "the First Schedule opens")
    r = S.get("/tax/act/vat/20?q=tax+invoice")
    check(r.status_code == 200 and b"<mark>" in r.data, "words are marked in a section")
    r = S.post("/tax/act/vat/load")
    check(r.status_code in (302, 403), "a staff user is turned away from filing")
    check(AI.status("vat")["loaded"], "(still filed)")
    r = S.get("/tax/legislative-updates")
    check(r.status_code in (200, 302), "legislative updates page loads")

    # --- the VAT register
    cl = Client(name="Acme Trading (Pvt) Ltd", created_by_id=staff.id); db.session.add(cl); db.session.commit()
    eng = Engagement(client_id=cl.id, title="VAT 2026", type="Audit", partner_id=partner.id, period_end=date(2026, 12, 31), acceptance_required=False, tax_services="compliance")
    db.session.add(eng); db.session.commit()
    std = VATRate(rate_type="Standard", rate_percent=15.0, effective_from=date(2020, 1, 1))
    zr = VATRate(rate_type="Zero-rated", rate_percent=0.0, effective_from=date(2020, 1, 1))
    ex = VATRate(rate_type="Exempt", rate_percent=0.0, effective_from=date(2020, 1, 1))
    db.session.add_all([std, zr, ex]); db.session.commit()
    D = date(2026, 9, 12)
    def doc(direction, doc_type, taxable, vat, rate=None, **kw):
        d = VATInvoice(engagement_id=eng.id, direction=direction, doc_type=doc_type, currency="USD", invoice_date=kw.pop("invoice_date", D),
                       taxable_amount=taxable, vat_amount=vat, vat_rate_id=rate.id if rate else None, vat_rate_percent_used=rate.rate_percent if rate else None, **kw)
        db.session.add(d); return d
    o1 = doc("Output", "Invoice", 1000.0, 150.0, std, invoice_number="S1", counterparty_name="Customer A")
    o2 = doc("Output", "Invoice", 400.0, 0.0, ex, invoice_number="S2", counterparty_name="School")
    o3 = doc("Output", "Invoice", 300.0, 0.0, zr, invoice_number="S3", counterparty_name="Customer B")
    o4 = doc("Output", "Export Sale", 500.0, 0.0, zr, invoice_number="E1", counterparty_name="Buyer abroad")
    o5 = doc("Output", "Credit Note", 100.0, 15.0, std, invoice_number="C1", counterparty_name="Customer A")
    i1 = doc("Input", "Invoice", 200.0, 30.0, std, invoice_number="P1", counterparty_name="Cafe Roma", description="Client entertainment dinner")
    i2 = doc("Input", "Invoice", 1000.0, 150.0, std, counterparty_name="", description="Toyota sedan vehicle purchase")
    i3 = doc("Input", "Invoice", 5.0, 0.75, std, description="stationery")
    i4 = doc("Input", "Bill of Entry", 400.0, 60.0, std, invoice_number="B1", counterparty_name="ZIMRA", related_reference="BOE-77")
    db.session.commit()
    ids = dict(partner=partner.id, eng=eng.id, i1=i1.id, i2=i2.id, i4=i4.id, o1=o1.id)
    docs = VATInvoice.query.filter_by(engagement_id=eng.id).all()

    sm = vat_working_paper_summary(docs)
    check(close(sm["output_tax"], 135.0) and close(sm["input_tax"], 240.75) and close(sm["input_tax_claimable"], 240.75) and close(sm["net_position"], 135.0 - 240.75), "with nothing denied the net position is as before (output 135.00, input 240.75)")
    check(close(sm["output_exempt_taxable"], 400.0) and close(sm["output_zero_rated_taxable"], 800.0) and close(sm["output_taxable"], 2100.0) and close(sm["output_taxable_excl_exempt"], 1700.0), "exempt sales 400, zero-rated 800 (a zero-rated invoice plus the export), taxable excluding exempt 1700")

    P_ = app.test_client()
    with P_.session_transaction() as s:
        s["_user_id"] = str(ids["partner"]); s["_fresh"] = True

    # --- mark input tax not deductible (section 16(2))
    r = P_.post(f"/tax/vat/invoices/{ids['i1']}/denial", data={"ground": "entertainment"})
    check(r.status_code == 302, "marking a document not deductible redirects back")
    r = P_.post(f"/tax/vat/invoices/{ids['i2']}/denial", data={"ground": "vehicle"})
    r = P_.post(f"/tax/vat/invoices/{ids['o1']}/denial", data={"ground": "entertainment"})
    docs = VATInvoice.query.filter_by(engagement_id=eng.id).all()
    check(db.session.get(VATInvoice, ids["o1"]).input_tax_denied is False, "an output document cannot be marked not deductible")
    sm = vat_working_paper_summary(docs)
    check(sm["input_tax_denied_count"] == 2 and close(sm["input_tax_denied"], 180.0) and close(sm["input_tax"], 240.75) and close(sm["input_tax_claimable"], 60.75) and close(sm["net_position"], 135.0 - 60.75), "denied VAT (30 + 150) is taken out of the claim: claimable 60.75, net payable 74.25")
    r = P_.post(f"/tax/vat/invoices/{ids['i2']}/denial", data={"action": "restore"})
    docs = VATInvoice.query.filter_by(engagement_id=eng.id).all()
    check(close(vat_working_paper_summary(docs)["input_tax_denied"], 30.0), "'Claim again' puts the document back")
    P_.post(f"/tax/vat/invoices/{ids['i2']}/denial", data={"ground": "vehicle"})

    # --- the month page
    r = P_.get(f"/engagements/{ids['eng']}?tab=tax&vat_period=2026-09")
    html = r.data.decode()
    check(r.status_code == 200 and "Return and payment due 25 October 2026" in html, "the month page shows the return due date (25 October 2026)")
    check("Input tax not deductible" in html or "input tax not deductible" in html, "the working paper shows the input tax not deductible")
    check("Not deductible: Entertainment" in html and "Not deductible: Motor vehicle" in html, "documents marked not deductible say why")
    check("exempt sales, not taxable supplies" in html and "of which 800.00 zero-rated" in html, "exempt and zero-rated sales are shown apart")
    check("Points to look at in this month" in html and "no customs or export entry reference" in html, "points to look at, including the export with no proof")
    check('href="/tax/act/vat/10(3)"' in html and 'href="/tax/act/vat/28(1)"' in html, "the points link into the VAT Act")
    check("The VAT Act beside this register" in html, "the month page links to the VAT Act page")
    links = sorted(set(re.findall(r'href="(/tax/act/vat/[^"]+)"', html)))
    bad = [h for h in links if P_.get(h).status_code != 200 or b"Couldn" in P_.get(h, follow_redirects=True).data]
    check(len(links) >= 4 and not bad, "all %d VAT Act links on the month page open (%s)" % (len(links), bad))

    # --- lock: a completed month cannot be changed
    P_.post(f"/tax/{ids['eng']}/vat/periods/2026-09/done")
    r = P_.post(f"/tax/vat/invoices/{ids['i4']}/denial", data={"ground": "other"}, follow_redirects=True)
    check(db.session.get(VATInvoice, ids["i4"]).input_tax_denied is False and b"locked" in r.data, "a completed month is locked against marking a document not deductible")
    r = P_.get(f"/engagements/{ids['eng']}?tab=tax&vat_period=2026-09")
    check("Not deductible</button>" not in r.data.decode(), "a locked month offers no 'Not deductible' button")

    # --- month checks, directly
    fl = {f.key: f for f in VC.month_checks(docs, 2026, 9, is_done=False, today=date(2026, 10, 19))}
    check("export_proof" in fl and fl["export_proof"].count == 1 and fl["export_proof"].ref == "10(3)", "export sale with no reference is flagged (10(3))")
    check("credit_note_ref" in fl and fl["credit_note_ref"].count == 1, "credit note with no original reference is flagged (21(3))")
    check("invoice_particulars" in fl and fl["invoice_particulars"].count == 1, "one input invoice with no number or supplier is flagged; the US$5 one is not (20(5))")
    check("apportion" in fl, "exempt sales plus input tax raises the apportionment flag (16(1))")
    check("time_of_supply" in fl and fl["time_of_supply"].level == "note", "the time of supply note is always there (8(1))")
    check("overdue" not in fl, "due 25 October, so not overdue on 19 October")
    fl2 = {f.key: f for f in VC.month_checks(docs, 2026, 9, is_done=False, today=date(2026, 10, 26))}
    check("overdue" in fl2 and "penalty" in fl2["overdue"].text, "overdue after 25 October, and the flag mentions the penalty (s39)")
    fl3 = {f.key: f for f in VC.month_checks(docs, 2026, 9, is_done=True, today=date(2026, 10, 26))}
    check("overdue" not in fl3, "a completed month is never overdue")
    # hints are suggestions: with the two documents already marked, no hint is raised for them
    check("deny_entertainment" not in fl and "deny_vehicle" not in fl, "documents already marked not deductible raise no further hint")
    fresh = [VATInvoice(engagement_id=eng.id, direction="Input", doc_type="Invoice", currency="USD", invoice_date=D, taxable_amount=100.0, vat_amount=15.0, invoice_number="Z1",
                        counterparty_name="Club", description="Golf club subscription"),
             VATInvoice(engagement_id=eng.id, direction="Input", doc_type="Invoice", currency="USD", invoice_date=D, taxable_amount=100.0, vat_amount=15.0, invoice_number="Z2",
                        counterparty_name="Hotel", description="Staff party catering")]
    flh = {f.key: f for f in VC.month_checks(fresh, 2026, 9, today=date(2026, 10, 1))}
    check("deny_club" in flh and flh["deny_club"].ref == "16(2)(b)" and "deny_entertainment" in flh and flh["deny_entertainment"].ref == "16(2)(a)", "a club subscription and staff-party catering are suggested for review (16(2)(b), 16(2)(a))")
    old_boe = VATInvoice(engagement_id=eng.id, direction="Input", doc_type="Bill of Entry", currency="USD", invoice_date=date(2025, 6, 10), taxable_amount=100.0, vat_amount=15.0,
                         invoice_number="B9", counterparty_name="ZIMRA", related_reference="X1")
    flo = {f.key: f for f in VC.month_checks([old_boe], 2025, 6, is_done=False, today=date(2026, 10, 19))}
    check("old_input" in flo and flo["old_input"].ref == "15(2)", "an input document more than 12 months old and still being claimed is flagged (15(2))")
    mism = VATInvoice(engagement_id=eng.id, direction="Output", doc_type="Invoice", currency="USD", invoice_date=D, taxable_amount=1000.0, vat_amount=100.0, invoice_number="M1")
    mism.vat_rate = std
    check("rate_mismatch" in {f.key for f in VC.month_checks([mism], 2026, 9, today=date(2026, 10, 1))}, "VAT that is not the rate applied to the value is flagged")
    nilstd = VATInvoice(engagement_id=eng.id, direction="Output", doc_type="Invoice", currency="USD", invoice_date=D, taxable_amount=1000.0, vat_amount=0.0, invoice_number="M2")
    nilstd.vat_rate = std
    check("standard_nil" in {f.key for f in VC.month_checks([nilstd], 2026, 9, today=date(2026, 10, 1))}, "a Standard-rated sale with no VAT is flagged")
    check(VC.return_due_date(2026, 12) == date(2027, 1, 25) and VC.return_due_date(2026, 9) == date(2026, 10, 25), "return due dates: 25th of the following month, across the year end")

    # --- the VAT Act beside the register
    r = P_.get("/tax/vat-act")
    html = r.data.decode()
    check(r.status_code == 200 and "The VAT Act beside the VAT register" in html and "Time of supply" in html and "Late payment" in html, "the VAT Act page lists the findings")
    check("such rate as may be fixed by the Charging Act" in html.replace("\n", " ") or "Charging Act" in html, "...with the Act's own words beside them")
    check("Nothing here is marked verified" in html and "verified</span>" not in html, "...and nothing is marked verified")
    check(len(VC.FINDINGS) >= 15 and {f["result"] for f in VC.FINDINGS} <= set(VC.RESULT_LABELS), "every finding has a known result")
    bad = []
    for f in VC.FINDINGS:
        for ref in f["refs"]:
            x = AI.paragraph_text(ref, "vat")
            if not x:
                bad.append(ref)
    check(not bad, "every section the findings cite exists in the filed Act (%s)" % bad)
    links = sorted(set(re.findall(r'href="(/tax/act/vat/[^"]+)"', html)))
    bad = [h for h in links if P_.get(h).status_code != 200]
    check(len(links) >= 20 and not bad, "all %d links on the VAT Act page open" % len(links))
    # not filed: the page still works, with an invitation
    with app.app_context():
        LegislativeProvision.query.filter_by(act_code="vat").delete(); db.session.commit(); AI.invalidate()
    r = P_.get("/tax/vat-act")
    check(r.status_code == 200 and b"File the VAT Act now" in r.data and b'href="/tax/act/vat/' not in r.data, "VAT Act page with the Act not filed: plain text and a button to file it")
    r = P_.get(f"/engagements/{ids['eng']}?tab=tax&vat_period=2026-09")
    check(r.status_code == 200 and b"Return and payment due" in r.data and b'href="/tax/act/vat/' not in r.data, "month page with the Act not filed: still works, no broken links")

print("\n%d failed" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
