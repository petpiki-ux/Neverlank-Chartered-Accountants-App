"""Claude beside the working paper (wp_assist.py): the panel on the Word and Excel editors, what is sent to the AI, what comes back, and Apply.
The AI is simulated; nothing leaves this machine."""
import os, sys, tempfile, io, types
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
import datetime as dtm
from docx import Document as DocxDocument
from docx.oxml.ns import qn
import openpyxl
from app import create_app
from extensions import db
from models import *
import wp_docx, wp_assist
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    admin = User.query.filter_by(role="admin").first(); aid = admin.id
    cl = Client(name="Penanel Trading (Private) Limited", address="12 Samora Machel Ave, Harare", tin_number="2000123456", created_by_id=aid)
    db.session.add(cl); db.session.commit()
    e = Engagement(client_id=cl.id, title="FY25 Statutory Audit", type="Audit", partner_id=aid, period_end=dtm.date(2025, 12, 31), reporting_framework="ifrs_for_smes", acceptance_required=False)
    db.session.add(e); db.session.commit(); eid = e.id
    tb = TrialBalance(engagement_id=eid); db.session.add(tb); db.session.commit()
    for nm, cat, dr, cr, pdr, pcr in [("Cash", "cash", 90000, 0, 3000, 0), ("Receivables", "trade_receivables", 30000, 0, 1000, 0), ("Share capital", "share_capital", 0, 100, 0, 100),
                                      ("Sales", "revenue", 0, 120000, 0, 80000), ("Cost of sales", "cost_of_sales", 50000, 0, 30000, 0), ("Payables", "trade_payables", 0, 20000, 0, 2000),
                                      ("Retained earnings", "retained_earnings", 0, 49900, 0, 1900)]:
        db.session.add(TrialBalanceLine(trial_balance_id=tb.id, account_name=nm, fs_category=cat, current_debit=dr, current_credit=cr, prior_debit=pdr, prior_credit=pcr))
    db.session.commit()
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(aid); s["_fresh"] = True
    tpl = DocumentTemplate.query.filter_by(filename="FA-03 Materiality and Risk Assessment Workpaper.docx").first()
    c.post(f"/working-papers/engagements/{eid}/generate/{tpl.id}")
    doc = Document.query.filter_by(engagement_id=eid).first(); did = doc.id
    path = os.path.join(app.config["UPLOAD_FOLDER"], doc.stored_filename)

    # ---- the panel is on the page
    page = c.get(f"/working-papers/{did}/edit").get_data(as_text=True)
    check('id="claude"' in page and 'id="b-claude"' in page and "Ask Claude about this paper" in page and "Working paper assistant" in page and "WPAI" in page and "{{" not in page and "{%" not in page,
          "the Word editor has the Claude button and side panel")
    check("Apply" in page and "Summarise this paper" in page and "Check it for mistakes and inconsistencies" in page and "nothing in the paper until you press Apply" in page.replace("changes ", ""), "quick questions and the safety wording are there")

    # ---- not switched on
    r = c.post(f"/working-papers/{did}/assist", json={"message": "Summarise this paper"})
    j = r.get_json()
    check(r.status_code == 200 and j["ok"] is False and "ANTHROPIC_API_KEY" in j["error"], "with no API key the panel says Claude is not switched on")

    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    seen = {}
    d0 = DocxDocument(path)
    outline = wp_docx and wp_assist.docx_outline(d0)
    first_path, first_text, _ = outline[0]
    cell = [o for o in outline if o[2] and "Revenue" in o[1]]
    check(len(outline) > 20 and all(isinstance(p, str) and p.replace(".", "").isdigit() for p, _, _ in outline), "the paper is read as paragraphs with their positions: %d" % len(outline))
    check(any(wp_docx.resolve(d0, wp_docx.parse_path(p)) is not None for p, _, _ in outline[:30]), "every position points at a real paragraph")

    # give the paper an e-mail address and phone number so masking can be checked
    d1 = DocxDocument(path)
    for blk in wp_docx._blocks(d1.element.body):
        if blk.tag == qn("w:p") and wp_docx.para_text(blk).strip() and wp_docx.para_runs(blk):
            wp_docx.set_run_text(wp_docx.para_runs(blk)[0], "Contact: jane@penanel.co.zw or +263 77 123 4567 about Penanel Trading (Private) Limited, TIN 2000123456.")
            break
    wp_docx.save_atomic(d1, path)
    d1 = DocxDocument(path)
    contact_path = [p for p, t, _ in wp_assist.docx_outline(d1) if "jane@" in t][0]
    sign_path = None
    for p, t, _ in wp_assist.docx_outline(d1):
        if "prepared by" in t.lower():
            sign_path = p; break

    def fake(system, messages):
        seen["system"] = system; seen["messages"] = messages
        return seen["result"], None
    real_call = wp_assist._call_model
    wp_assist._call_model = fake
    seen["result"] = {"message": "The paper is for {client_name}. Contact {email_1} or {phone_1}.\n\n- Revenue is **120,000**.\n- Total is 987,654 (to be verified).",
                      "edits": [
                          {"path": contact_path, "kind": "replace", "text": "Contact {email_1} about {client_name}. Revenue was 120,000 against 80,000 and costs of 987,654.", "why": "Tidier."},
                          {"path": first_path, "kind": "insert_after", "text": "A new line below the first paragraph."},
                          {"path": "999.9.9.9", "kind": "replace", "text": "Nowhere."},
                          {"path": first_path, "kind": "delete", "text": "Bad kind."},
                          {"path": first_path, "kind": "replace", "text": ""},
                      ] + ([{"path": sign_path, "kind": "replace", "text": "Prepared by: someone else"}] if sign_path else [])}
    hist = [{"role": "assistant", "content": "stray first"}, {"role": "user", "content": "Earlier question about jane@penanel.co.zw"}, {"role": "assistant", "content": "Earlier answer"}, {"role": "user", "content": "unanswered"}]
    r = c.post(f"/working-papers/{did}/assist", json={"message": "Check the figures in Penanel Trading's paper", "history": hist, "selection": {"path": contact_path, "text": "Contact: jane@penanel.co.zw", "selected": True}})
    j = r.get_json()
    msgs = seen["messages"]; final = msgs[-1]["content"]
    check(j["ok"], "an answer comes back: %s" % (j.get("error") or ""))
    check("Penanel" not in final and "2000123456" not in final and "jane@penanel.co.zw" not in final and "+263 77 123 4567" not in final and all("Penanel" not in m["content"] and "jane@" not in m["content"] for m in msgs),
          "the client's name, TIN, e-mail and phone never leave the app")
    check("{client_name}" in final and "{client_tin}" in final and "{email_1}" in final and "{phone_1}" in final, "they are swapped for tokens")
    check("Revenue 120,000" in final and "FY25 Statutory Audit" in final and "THE WORKING PAPER" in final and f"[{contact_path}]" in final and "SELECTED TEXT" in final, "the engagement record, the paper with positions and the selection are sent")
    check([m["role"] for m in msgs] == ["user", "assistant", "user"] and msgs[0]["content"].startswith("Earlier question"), "earlier turns alternate user / assistant, starting with the user, and the unanswered one is dropped")
    check("Penanel Trading (Private) Limited" in j["reply"] and "jane@penanel.co.zw" in j["reply"] and "+263 77 123 4567" in j["reply"] and "{" not in j["reply"], "tokens are put back in the answer")
    eds = j["edits"]
    kinds = [(x["kind"], x["path"]) for x in eds]
    check(kinds == [("replace", contact_path), ("insert_after", first_path)], "only real, allowed suggestions are kept (bad position, bad kind, empty text and sign-off cells dropped): %s" % kinds)
    ed0 = eds[0]
    check("jane@penanel.co.zw" in ed0["text"] and "Penanel Trading (Private) Limited" in ed0["text"] and ed0["before"].startswith("Contact: jane@penanel.co.zw"), "a suggestion has the real wording restored and the paragraph as it is now")
    check("987,654" in ed0["warn"] and "120,000" not in ed0["warn"] and "80,000" not in ed0["warn"], "a figure that is not in the paper or the record is flagged; known figures are not: %s" % ed0["warn"])
    check(eds[1]["warn"] == "" and j["can_edit"] is True, "a clean suggestion has no warning and the paper can be changed")
    check("never change the paper yourself" in seen["system"].lower() or "you never change the paper yourself" in seen["system"].lower(), "the AI is told it only suggests")

    # a long reply is capped at six suggestions
    seen["result"] = {"message": "ok", "edits": [{"path": first_path, "kind": "insert_after", "text": f"Line {i}"} for i in range(10)]}
    j = c.post(f"/working-papers/{did}/assist", json={"message": "Add lines"}).get_json()
    check(len(j["edits"]) == 6, "at most six suggestions in a reply")
    seen["result"] = {"message": "", "edits": []}
    j = c.post(f"/working-papers/{did}/assist", json={"message": "x"}).get_json()
    check(j["ok"] and j["reply"], "an empty answer is replaced by a short sentence")
    r = c.post(f"/working-papers/{did}/assist", json={"message": "   "})
    check(r.status_code == 400, "an empty question is refused")

    # ---- Apply: new paragraph below
    d2 = DocxDocument(path)
    fp = wp_docx.parse_path(first_path)
    parent = wp_docx.resolve(d2, fp).getparent(); n_before = len(wp_docx._blocks(parent))
    doc = Document.query.get(did); doc.reviewed_by_id = aid; db.session.commit()
    r = c.post(f"/working-papers/{did}/assist/insert", json={"path": first_path, "text": "A new line below the first paragraph.\nSecond line."})
    d2 = DocxDocument(path)
    newp = wp_docx.resolve(d2, fp[:-1] + [fp[-1] + 1])
    check(r.get_json()["ok"] and len(wp_docx._blocks(wp_docx.resolve(d2, fp).getparent())) == n_before + 1 and wp_docx.para_text(newp) == "A new line below the first paragraph.\nSecond line.", "Apply (new paragraph) adds it directly under the paragraph")
    check(Document.query.get(did).reviewed_by_id is None, "and clears the reviewer's tick like any other change")
    check(c.post(f"/working-papers/{did}/assist/insert", json={"path": first_path, "text": ""}).status_code == 400 and c.post(f"/working-papers/{did}/assist/insert", json={"path": "999.0", "text": "x"}).status_code == 400, "an empty text or a missing position is refused")

    # ---- a signed paper: Claude can still read it, but Apply is refused
    sg = User(username="pat", name="Pat Partner", email="pat@example.com", role="partner"); sg.set_password("x"); db.session.add(sg); db.session.commit()
    doc = Document.query.get(did); doc.partner_signed_by_id = sg.id; doc.partner_signed_at = dtm.datetime.utcnow(); db.session.commit()
    seen["result"] = {"message": "Fine.", "edits": [{"path": first_path, "kind": "replace", "text": "New first line"}]}
    j = c.post(f"/working-papers/{did}/assist", json={"message": "Review it"}).get_json()
    check(j["ok"] and j["can_edit"] is False, "a signed paper can still be read by Claude, but is marked as not changeable")
    r = c.post(f"/working-papers/{did}/assist/insert", json={"path": first_path, "text": "Should not be added"})
    check(r.status_code == 409 and "signed off" in r.get_json()["error"], "and a new paragraph cannot be added to it")
    page = c.get(f"/working-papers/{did}/edit").get_data(as_text=True)
    check('id="claude"' in page and "signed off by the partner" in page, "the panel is there on a read-only paper too")
    doc.partner_signed_by_id = None; doc.partner_signed_at = None; db.session.commit()

    # ---- Excel: questions and answers only
    tx = DocumentTemplate.query.filter_by(ref_code="TX-03", type="Tax Advisory").first()
    e2 = Engagement(client_id=cl.id, title="CGT on Stand 5", type="Tax Advisory", partner_id=aid, period_end=dtm.date(2025, 12, 31), acceptance_required=False); db.session.add(e2); db.session.commit()
    c.post(f"/working-papers/engagements/{e2.id}/generate/{tx.id}")
    xd = Document.query.filter_by(engagement_id=e2.id).first(); xp = os.path.join(app.config["UPLOAD_FOLDER"], xd.stored_filename)
    c.post(f"/working-papers/{xd.id}/save", json={"changes": [{"sheet": "CGT Computation", "cell": "B13", "value": "1,000,000"}, {"sheet": "CGT Computation", "cell": "B16", "value": "=B13+B15"}]})
    xpage = c.get(f"/working-papers/{xd.id}/edit").get_data(as_text=True)
    check('id="claude"' in xpage and 'id="b-claude"' in xpage and "Explain this sheet" in xpage and "Check the formulas and totals" in xpage, "the Excel editor has the panel too")
    seen["result"] = {"message": "B16 adds B13 and B15.", "edits": [{"path": "1", "kind": "replace", "text": "x"}]}
    j = c.post(f"/working-papers/{xd.id}/assist", json={"message": "Check the formulas", "selection": {"path": "CGT Computation!B16", "text": "=B13+B15", "selected": True}}).get_json()
    final = seen["messages"][-1]["content"]
    check(j["ok"] and j["edits"] == [] and j["can_edit"] is False, "Excel answers carry no suggested changes")
    check("=== Sheet: CGT Computation ===" in final and "B13: 1000000" in final and "B16: =B13+B15" in final and "THE CELL THE PERSON HAS SELECTED (CGT Computation!B16)" in final and "leave edits empty" in final and "Penanel" not in final,
          "the workbook is sent with cell addresses, formulas and the selected cell, and the client is masked")

    # ---- a paper that is not open in the app, a stranger
    up = DocxDocument(); up.add_paragraph("Uploaded."); buf = io.BytesIO(); up.save(buf); buf.seek(0)
    c.post(f"/engagements/{eid}/documents/upload", data={"file": (buf, "notes.docx"), "category": "General"}, content_type="multipart/form-data")
    ud = Document.query.filter_by(engagement_id=eid, original_filename="notes.docx").first()
    check(c.post(f"/working-papers/{ud.id}/assist", json={"message": "hi"}).status_code == 400, "a file not opened in the app is refused")
    anon = app.test_client()
    check(anon.post(f"/working-papers/{did}/assist", json={"message": "hi"}).status_code in (302, 401) and anon.post(f"/working-papers/{did}/assist/insert", json={"path": "0", "text": "x"}).status_code in (302, 401), "signed-out visitors are refused")

    # ---- rate limit
    wp_assist._calls.clear(); wp_assist.RATE = (2, 600)
    codes = [c.post(f"/working-papers/{did}/assist", json={"message": "q"}).status_code for _ in range(3)]
    check(codes == [200, 200, 429], "a person cannot fire off endless questions: %s" % codes)
    wp_assist.RATE = (30, 600); wp_assist._calls.clear()

    # ---- the real call, with a stand-in for the AI library
    class Block:
        def __init__(self, **k): self.__dict__.update(k)
    calls = []
    def make_client(behaviour):
        class Msgs:
            def create(self, **kw):
                calls.append(kw)
                return behaviour(kw)
        class Cl:
            def __init__(self, **kw): self.messages = Msgs()
        return Cl
    wp_assist._call_model = real_call
    def run(behaviour):
        fake_mod = types.ModuleType("anthropic"); fake_mod.Anthropic = make_client(behaviour); sys.modules["anthropic"] = fake_mod
        calls.clear()
        return wp_assist._call_model("SYS", [{"role": "user", "content": "hi"}])
    tool_block = lambda inp: Block(type="tool_use", name=wp_assist.TOOL["name"], input=inp)
    res, err = run(lambda kw: Block(content=[tool_block({"message": "hello", "edits": []})], stop_reason="tool_use"))
    check(res == {"message": "hello", "edits": []} and err is None and calls[0]["tool_choice"] == {"type": "tool", "name": wp_assist.TOOL["name"]} and calls[0]["system"] == "SYS" and "temperature" not in calls[0] and "timeout" not in calls[0],
          "the call forces the reply tool and sends no temperature or per-call timeout (the pinned library rejects them)")
    def missing_then_ok(kw):
        if kw["model"] == wp_assist.MODEL and wp_assist.MODEL != wp_assist.FALLBACK_MODEL:
            raise Exception("model: not_found_error")
        return Block(content=[tool_block({"message": "ok"})], stop_reason="tool_use")
    wp_assist.MODEL = "some-strong-model"; wp_assist.FALLBACK_MODEL = "claude-sonnet-4-5"
    res, err = run(missing_then_ok)
    check(res == {"message": "ok"} and [k["model"] for k in calls] == ["some-strong-model", "claude-sonnet-4-5"], "a model name the key cannot use falls back to the default")
    res, err = run(lambda kw: Block(content=[tool_block({"message": "cut"})], stop_reason="max_tokens"))
    check(res is None and "too long" in err, "an answer cut off at the token limit gives a clear message")
    def boom(kw): raise Exception("Request timed out.")
    res, err = run(boom)
    check(res is None and "took too long" in err, "a timeout gives a sentence a person can act on: %s" % err)
    res, err = run(lambda kw: Block(content=[Block(type="text", text="Plain words")], stop_reason="end_turn"))
    check(res == {"message": "Plain words", "edits": []}, "a plain-text reply is still shown")
    res, err = run(lambda kw: Block(content=[], stop_reason="end_turn"))
    check(res is None and "no reply" in err, "an empty reply is reported")
    sys.modules.pop("anthropic", None)

print("\nFAILED: %d" % len(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
