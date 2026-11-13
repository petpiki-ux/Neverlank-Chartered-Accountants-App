"""ZIMRA Matters: AI first draft of a reply. The model is replaced by a stand-in so this checks OUR rules: what is sent
(and what is not), the guards on the answer, the human-check gate, and the approval flow."""
import os, sys, tempfile, io, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flask
from flask.testing import FlaskClient
_orig = FlaskClient.open
def _open(self, *a, **k):
    flask.g.pop("_login_user", None) if flask.has_app_context() else None
    return _orig(self, *a, **k)
FlaskClient.open = _open
from datetime import date, timedelta
import docx
from app import create_app
from extensions import db
from models import *
import zimra_ai as ZAI
import zimra_letters as ZL
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

CLIENT = "Acme Holdings (Pvt) Ltd"
TIN = "2000123456"
VAT = "10099887"
EMAIL = "fd@acme.co.zw"
PHONE = "+263 77 123 4567"
OFFICER = "Mr T. Moyo"

# the incoming ZIMRA letter, as a Word file that names the client and carries its identifiers
tmp = tempfile.mkdtemp()
letter_path = os.path.join(tmp, "zimra_letter.docx")
d = docx.Document()
d.add_paragraph(f"Zimbabwe Revenue Authority. Attention: {CLIENT}, TIN {TIN}, VAT {VAT}. Contact {EMAIL} or {PHONE}.")
d.add_paragraph(f"Dear {OFFICER}. Following our VAT audit for 2024 we found input tax of USD 12,500.00 claimed without valid tax invoices "
                "and request the records within 14 days. Penalties of USD 1,800.00 apply under section 14 of the Value Added Tax Act.")
d.save(letter_path)

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    stranger = User(username="str", name="Stranger", role="staff"); stranger.set_password("x")
    db.session.add_all([partner, staff, stranger]); db.session.commit()
    cl = Client(name=CLIENT, created_by_id=staff.id, tin_number=TIN, vat_number=VAT)
    db.session.add(cl); db.session.commit()
    # filed library: one Act (primary), one Publication (commentary)
    act = LegislativeUpdate(title="Value Added Tax Act [Chapter 23:12]", tax_head="VAT", instrument_type="Act",
                            summary="VAT input tax claims", source_reference="Chapter 23:12",
                            extracted_text="Section 14 of the Value Added Tax Act provides that input tax may only be claimed "
                                           "where the registered operator holds a valid tax invoice for the supply. A valid tax invoice must show the TIN.")
    pub = LegislativeUpdate(title="VAT audit commentary", tax_head="VAT", instrument_type="Pub",
                            summary="Commentary on VAT input tax audits and tax invoice evidence",
                            extracted_text="Practitioners report ZIMRA accepts alternative evidence of input tax in some VAT audits.")
    db.session.add_all([act, pub]); db.session.commit()
    ids = dict(partner=partner.id, staff=staff.id, stranger=stranger.id, cl=cl.id, act=act.id, pub=pub.id)
def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
S, P, O = login(ids["staff"]), login(ids["partner"]), login(ids["stranger"])
Z = "/zimra"

S.post(f"{Z}/new", data={"client_id": ids["cl"], "title": "VAT audit 2024", "case_type": "VAT audit / refund verification",
                         "tax_head": "VAT", "period_from": "2024-01-01", "period_to": "2024-12-31", "zimra_reference": "ZIM/VAT/778",
                         "zimra_officer": OFFICER, "currency": "USD", "amount_assessed": "12500", "penalties_interest": "1800"})
with app.app_context():
    cid = ZimraCase.query.first().id
S.post(f"{Z}/{cid}/correspondence/add", data={
    "corr_date": (date.today() - timedelta(days=3)).isoformat(), "direction": "Incoming from ZIMRA", "kind": "Information request",
    "subject": "Request for VAT records 2024", "reference": "ZIM/IR/55", "response_due": (date.today() + timedelta(days=10)).isoformat(),
    "notes": f"Contact at {EMAIL}", "file": (open(letter_path, "rb"), "zimra_letter.docx")}, content_type="multipart/form-data")
with app.app_context():
    iid = ZimraCorrespondence.query.first().id
S.post(f"{Z}/{cid}/replies/new", data={"template": "info_response", "in_reply_to": iid})
with app.app_context():
    rid = ZimraReply.query.first().id
    original_body = ZimraReply.query.get(rid).body

# ---------------------------------------------------------------- unit: guards
class C:  # minimal client stand-in
    name = CLIENT; tin_number = TIN; vat_number = VAT
red = ZAI.redact(f"{CLIENT} and Acme Holdings, TIN {TIN}, VAT {VAT}, mail {EMAIL}, call {PHONE}, officer {OFFICER}, USD 12,500.00 on 01-02-2024", C, OFFICER)
check(CLIENT not in red and "Acme Holdings" not in red and TIN not in red and VAT not in red and EMAIL not in red and "123 4567" not in red and OFFICER not in red,
      "redact removes name, short name, TIN, VAT, e-mail, phone and officer")
check("{client_name}" in red and "{client_tin}" in red and "{client_vat}" in red and "12,500.00" in red and "01-02-2024" in red,
      "redact leaves tokens and keeps amounts and dates")
rest = ZAI.restore_tokens("For {client_name} (TIN {client_tin}, VAT {client_vat}) {client_bank}", C)
check(CLIENT in rest and TIN in rest and VAT in rest and "[CONFIRM: client bank]" in rest, "restore_tokens refills; an unknown token becomes a placeholder")
class NoTin: name = "X Ltd"; tin_number = None; vat_number = None
check("[CONFIRM: client's TIN" in ZAI.restore_tokens("{client_tin}", NoTin), "a missing TIN becomes a placeholder")
check(ZAI.strip_framing("Dear Sir/Madam\n\nWe write.\n\nYours faithfully\nPartner") == "We write.", "strip_framing removes salutation and sign-off")
long_tail = "Regards the findings, " + "we say more. " * 40
check(ZAI.strip_framing("Intro.\n\n" + long_tail).endswith("we say more."), "strip_framing never cuts a long passage that merely starts with 'Regards'")
b, flagged = ZAI.flag_figures("We paid 12,500.00 and 99,999.00 and 30 days, again 99,999.00. See [CONFIRM: amount 77,777 here]", "tax 12500 and 1,800.00")
check(flagged == ["99,999.00"] and b.count("[CONFIRM: verify 99,999.00") == 1 and "12,500.00 [CONFIRM" not in b and "30 days" in b and "77,777 here]" in b,
      "flag_figures flags only an unknown figure, once; known/small figures and existing placeholders are untouched")
b, flagged = ZAI.flag_legal_refs("Under section 14 of the Value Added Tax Act and section 55 and S.I. 12 of 2020 and the Income Tax Act.",
                                 "section 14 of the Value Added Tax Act provides")
check(flagged == ["section 55", "S.I. 12 of 2020", "Income Tax Act"], f"flag_legal_refs flags only unsupported references (got {flagged})")
check("section 14 of the Value Added Tax Act [CONFIRM" not in b, "supported references are left alone")
check(ZAI.numbers_in("USD 1,234.50 and 2024 and 12") == {"1234.50", "2024.00"}, "numbers_in ignores numbers under 3 digits")

# ---------------------------------------------------------------- not configured
r = S.post(f"{Z}/replies/{rid}/ai-draft", data={"confirm": "1", "instructions": "x"}, follow_redirects=True)
check(b"not set up yet" in r.data, "no API key -> clear message")
with app.app_context():
    check(ZimraReply.query.get(rid).body == original_body and ZimraReply.query.get(rid).ai is None, "nothing changed without a key")
r = S.get(f"{Z}/replies/{rid}/edit")
check(r.status_code == 200 and b"Draft with AI" in r.data and b"not set up on this server" in r.data, "edit page explains AI is not set up")

# ---------------------------------------------------------------- configured, stand-in model
os.environ["ANTHROPIC_API_KEY"] = "test-key"
captured = {}
RESULT = {
    "body": ("Dear Sir/Madam\n\nWe act for {client_name} (TIN {client_tin}, VAT {client_vat}) and refer to your letter.\n\n"
             "Input tax of USD 12,500.00 was claimed. We enclose invoices totalling USD 99,999.00 in support. "
             "The claim is made under section 14 of the Value Added Tax Act and section 55 of the Value Added Tax Act.\n\n"
             "Our client asks for 14 more days. [CONFIRM: the date the records will be supplied]\n\nYours faithfully\nNeverlank"),
    "positions_taken": ["Asks for 14 more days", "Says the claim is supported by invoices"],
    "citations": [{"update_id": ids["act"], "used_for": "Input tax needs a valid tax invoice"},
                  {"update_id": ids["pub"], "used_for": "Alternative evidence"}, {"update_id": 99999, "used_for": "ghost"}],
    "missing_information": ["Date the records will be supplied"],
    "reviewer_notes": ["The letter does not concede the penalties - confirm that is the intended position"],
}
def fake_model(system, user_message):
    captured["system"], captured["user"] = system, user_message
    return dict(RESULT), None
ZAI._call_model = fake_model

r = S.post(f"{Z}/replies/{rid}/ai-draft", data={"instructions": "Ask for 14 more days; do not admit penalties."}, follow_redirects=True)
check(b"Tick the box" in r.data, "must confirm what is sent")
with app.app_context():
    check(ZimraReply.query.get(rid).ai is None, "no draft without the confirmation")

r = O.post(f"{Z}/replies/{rid}/ai-draft", data={"confirm": "1"})
check(r.status_code == 403, "a user outside the case cannot run the AI draft")

r = S.post(f"{Z}/replies/{rid}/ai-draft", data={"confirm": "1", "instructions": "Ask for 14 more days; do not admit penalties."}, follow_redirects=True)
check(b"AI first draft written" in r.data, "AI draft written")
u, sysm = captured["user"], captured["system"]

# what was sent
check(CLIENT not in u and "Acme Holdings" not in u and TIN not in u and VAT not in u and EMAIL not in u and "123 4567" not in u and OFFICER not in u,
      "nothing identifying (name, TIN, VAT, e-mail, phone, officer) was sent to the model")
check("{client_name}" in u and "{client_tin}" in u, "tokens were sent instead")
check("12,500.00" in u and "ZIM/IR/55" in u and "Request for VAT records 2024" in u, "case facts and the letter's subject/reference were sent")
check("valid tax invoice" in u and "Value Added Tax Act [Chapter 23:12]" in u, "the Act excerpt from the library was sent")
check("Ask for 14 more days; do not admit penalties." in u, "the drafter's instructions were sent")
check("input tax of USD 12,500.00 claimed without valid tax invoices" in u, "the text of the uploaded ZIMRA letter was read and sent")
check("Never invent" in sysm and "do NOT choose" in sysm and "only source of law" in u, "the rules are in the prompt")
check("<client_tin>" in u or "client_tin" in u, "template skeleton sent")

with app.app_context():
    rp = ZimraReply.query.get(rid); ai = rp.ai; body = rp.body
    check(ai is not None and ai["prev_body"] == original_body, "previous text kept for restore")
    check(not body.lstrip().lower().startswith("dear") and "Yours faithfully" not in body, "salutation and closing stripped")
    check(CLIENT in body and TIN in body and VAT in body and "{client_" not in body, "tokens restored with the real identifiers")
    check("[CONFIRM: verify 99,999.00" in body, "invented figure flagged in the letter")
    check("12,500.00 [CONFIRM" not in body, "a figure that is in the ZIMRA letter is not flagged")
    check("[CONFIRM: verify section 55" in body and "section 14 of the Value Added Tax Act [CONFIRM" not in body,
          "unsupported section flagged; the one in the library is not")
    check([s["id"] for s in ai["sources"]] == [ids["act"], ids["pub"]], "valid citations kept, the ghost citation dropped")
    check(any("99999" in f for f in ai["flags"]) or any("99,999.00" in f for f in ai["flags"]), "flags list the figure")
    check(any("section 55" in f for f in ai["flags"]), "flags list the legal reference")
    check(any("#99999" in f for f in ai["flags"]), "flags list the dropped citation")
    check(any("VAT audit commentary" in f and "not law" in f for f in ai["flags"]), "a Publication is flagged as commentary, not law")
    check(ai["positions"] == RESULT["positions_taken"] and ai["notes"] == RESULT["reviewer_notes"], "positions and notes saved")
    check(ai["instructions"].startswith("Ask for 14 more days"), "instructions saved for the record")
    check(ai["letter_read"] is True, "records that the letter text was read")
    check(any(e["what"] == "AI first draft written" for e in rp.history), "event logged")
    check(len(ZL.unresolved(body)) >= 3, "placeholders block approval")

r = S.get(f"{Z}/replies/{rid}/edit"); h = r.data.decode()
check("AI-assisted draft" in h and "Flagged for checking" in h and "Positions this letter takes" in h and "Asks for 14 more days" in h
      and "Value Added Tax Act [Chapter 23:12]" in h and "Notes for the reviewing Partner" in h, "edit page shows the AI panel")
check("I have checked every fact" in h and "Discard AI draft" in h and "Draft again with AI" in h, "edit page has the check box, discard and redraft")

# ---------------------------------------------------------------- the human-check gate
def edit_post(c, action, **extra):
    with app.app_context():
        rp = ZimraReply.query.get(rid)
        data = dict(letter_date=rp.letter_date.isoformat(), subject=rp.subject, body=rp.body, our_reference=rp.our_reference or "",
                    addressee=rp.addressee, salutation=rp.salutation, signatory=rp.signatory or "", signatory_title=rp.signatory_title or "",
                    action=action)
    data.update(extra)
    return c.post(f"{Z}/replies/{rid}/edit", data=data, follow_redirects=True)
r = edit_post(S, "submit")
check(b"not submitted" in r.data, "submit refused without the tick")
with app.app_context():
    check(ZimraReply.query.get(rid).status == "Draft", "still a draft")
r = S.post(f"{Z}/replies/{rid}/submit", follow_redirects=True)
check(b"first drafted by AI" in r.data, "the plain submit button needs the tick too")
r = edit_post(S, "submit", ai_checked="1")
check(b"Submitted" in r.data and b"placeholder" in r.data, "submitted with the tick (placeholders still warned about)")
with app.app_context():
    rp = ZimraReply.query.get(rid)
    check(rp.status == "Awaiting approval" and rp.ai["checked_by"] == "Staff S" and rp.ai["checked_at"], "who checked it is recorded")
    check(any("AI-assisted draft" in (e["note"] or "") for e in rp.history if e["what"] == "Submitted for approval"), "submission log notes AI assistance")
r = P.get(f"{Z}/replies/{rid}")
check(b"AI-assisted draft" in r.data and b"Checked by Staff S" in r.data, "Partner sees the AI panel and who checked")
r = P.post(f"{Z}/replies/{rid}/approve", follow_redirects=True)
check(b"Cannot approve" in r.data, "cannot approve while placeholders remain")

# clean the letter by hand, resubmit via view page, approve
P.post(f"{Z}/replies/{rid}/return", data={"note": "Complete the placeholders"})
with app.app_context():
    rp = ZimraReply.query.get(rid)
    rp.body = ZL.PLACEHOLDER_RE.sub("30 June", rp.body); db.session.commit()
r = S.post(f"{Z}/replies/{rid}/submit", data={"ai_checked": "1"}, follow_redirects=True)
check(b"Submitted for a Partner" in r.data, "clean letter submits from the view page")
r = P.post(f"{Z}/replies/{rid}/approve", follow_redirects=True)
check(b"Approved" in r.data, "Partner approves the cleaned letter")
with app.app_context():
    rp = ZimraReply.query.get(rid)
    check(any(e["what"] == "Approved" and "AI-assisted draft" in (e["note"] or "") for e in rp.history), "approval log notes AI assistance")
r = P.post(f"{Z}/replies/{rid}/ai-draft", data={"confirm": "1"}, follow_redirects=True)
check(b"Only a draft can be redrafted" in r.data, "no AI redraft once awaiting/approved/sent")
for fmt in ("docx", "pdf"):
    r = P.get(f"{Z}/replies/{rid}/download/{fmt}")
    check(r.status_code == 200 and len(r.data) > 1000, f"{fmt} downloads")
doc_text = "\n".join(p.text for p in docx.Document(io.BytesIO(P.get(f"{Z}/replies/{rid}/download/docx").data)).paragraphs)
check("AI" not in doc_text.split() and "[CONFIRM" not in doc_text and CLIENT in doc_text, "the letter itself carries no AI wording or placeholders")

# ---------------------------------------------------------------- restore, redraft, errors
S.post(f"{Z}/{cid}/replies/new", data={"template": "general"})
with app.app_context():
    rid2 = ZimraReply.query.order_by(ZimraReply.id.desc()).first().id
    before2 = ZimraReply.query.get(rid2).body
r = S.post(f"{Z}/replies/{rid2}/ai-draft", data={"confirm": "1"}, follow_redirects=True)
check(b"AI first draft written" in r.data, "second letter (no incoming letter, no instructions) drafts")
check("not decide the client's position" in captured["user"] or "do not decide the client" in captured["user"], "no instructions -> prompt says not to decide the position")
check("not a reply to a logged ZIMRA letter" in captured["user"], "prompt says there is no incoming letter")
r = S.post(f"{Z}/replies/{rid2}/ai-restore", follow_redirects=True)
check(b"previous text restored" in r.data, "restore works")
with app.app_context():
    rp = ZimraReply.query.get(rid2)
    check(rp.body == before2 and rp.ai is None, "text back and AI record cleared")
    check(any(e["what"] == "AI draft discarded" for e in rp.history), "discard logged")
r = S.post(f"{Z}/replies/{rid2}/ai-restore", follow_redirects=True)
check(b"no AI draft to discard" in r.data, "restore with nothing to restore is harmless")
# no ai -> submit without tick works
r = S.post(f"{Z}/replies/{rid2}/submit", follow_redirects=True)
check(b"Submitted" in r.data, "a human-written draft submits without the AI tick")

S.post(f"{Z}/replies/{rid2}/reopen")
ZAI._call_model = lambda s, u: (None, "rate limit exceeded")
r = S.post(f"{Z}/replies/{rid2}/ai-draft", data={"confirm": "1"}, follow_redirects=True)
check(b"could not be made" in r.data and b"rate limit" in r.data, "model error is shown")
with app.app_context():
    check(ZimraReply.query.get(rid2).ai is None and ZimraReply.query.get(rid2).body == before2, "model error changes nothing")
ZAI._call_model = lambda s, u: ({"body": "Dear Sir\n\nYours faithfully", "positions_taken": [], "citations": [], "missing_information": [], "reviewer_notes": []}, None)
r = S.post(f"{Z}/replies/{rid2}/ai-draft", data={"confirm": "1"}, follow_redirects=True)
check(b"empty letter" in r.data, "an empty answer is refused")

# no library match -> still drafts, states no law, says so
with app.app_context():
    for _M in (LegislativeUpdateChunk, LegislativeProvision):
        _M.query.delete()
    LegislativeUpdate.query.delete(); db.session.commit()
cap2 = {}
def fm(system, user_message):
    cap2["u"] = user_message
    return {"body": "We refer to your letter. [CONFIRM: legal basis - the section relied on]", "positions_taken": [], "citations": [{"update_id": 1, "used_for": "x"}],
            "missing_information": ["Legal basis"], "reviewer_notes": []}, None
ZAI._call_model = fm
r = S.post(f"{Z}/replies/{rid2}/ai-draft", data={"confirm": "1"}, follow_redirects=True)
check("No filed material matched" in cap2["u"], "empty library is stated in the prompt")
with app.app_context():
    ai = ZimraReply.query.get(rid2).ai
    check(ai and any("No filed legislation matched" in f for f in ai["flags"]) and ai["sources"] == [], "empty library is flagged to the reviewer; no sources")

# ---------------------------------------------------------------- migration
with app.app_context():
    from sqlalchemy import text
    info = db.session.execute(text("PRAGMA table_info(zimra_reply)")).fetchall()
    keep = [row for row in info if row[1] != "ai_meta"]
    names = ", ".join(row[1] for row in keep); defs = ", ".join(f"{row[1]} {row[2]}" for row in keep)
    db.session.execute(text("ALTER TABLE zimra_reply RENAME TO zimra_reply_bak"))
    db.session.execute(text(f"CREATE TABLE zimra_reply ({defs})"))
    db.session.execute(text(f"INSERT INTO zimra_reply ({names}) SELECT {names} FROM zimra_reply_bak"))
    db.session.execute(text("DROP TABLE zimra_reply_bak")); db.session.commit()
    check("ai_meta" not in [r[1] for r in db.session.execute(text("PRAGMA table_info(zimra_reply)")).fetchall()], "old-style table has no ai_meta")
    from app import _add_missing_columns
    _add_missing_columns()
    check("ai_meta" in [r[1] for r in db.session.execute(text("PRAGMA table_info(zimra_reply)")).fetchall()], "_add_missing_columns adds ai_meta")
    check(ZimraReply.query.count() >= 2, "existing letters still readable")

print()
print("FAILED:" if fails else "ALL PASSED", len(fails) if fails else "")
for f in fails: print(" -", f)
sys.exit(1 if fails else 0)
