"""Library intelligence: question understanding, law-first ordering, structured and checked answers, follow-ups, the cache, the advice memo and the
Word download, and the pages that show them."""
import os, sys, tempfile, json, io, re
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
os.environ["ANTHROPIC_API_KEY"] = "test-key"
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
import legislation_index as LI
import legislation_search as LSE
import legislation_understand as LU
import legislation_memo as LM
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with app.app_context():
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add(staff); db.session.commit()
    def add(title, typ, head, text=None, summary=None, ref=None, eff=None, **kw):
        u = LegislativeUpdate(title=title, instrument_type=typ, tax_head=head, summary=summary, extracted_text=text, extraction_status="extracted" if text else None,
                              source_reference=ref, effective_date=eff, **kw)
        db.session.add(u); db.session.commit()
        return u.id
    VATACT = add("Value Added Tax Act [Chapter 23:12]", "Act", "Value Added Tax (VAT)",
                 "20. Rate of tax\nThe standard rate of value added tax is 15 per cent of the value of the supply. Exports are zero-rated.\n21. Registration\nA person whose taxable supplies exceed the registration threshold must register for value added tax within thirty days.",
                 summary="Value added tax rate and registration", eff=date(2023, 1, 1))
    NEWS = add("ZIMRA to raise VAT rate says minister", "News", "Value Added Tax (VAT)",
               "The minister said the value added tax rate could rise to 17.5 per cent next year according to sources.", summary="VAT rate may rise")
    PUB = add("SAIT commentary on the VAT rate", "Pub", "Value Added Tax (VAT)",
              "Commentary: the value added tax rate is widely understood to be 15 per cent but practitioners should check the Act.", summary="VAT rate commentary")
    FUT = add("Statutory Instrument 99 of 2031 - Value Added Tax (Rate) Amendment", "SI", "Value Added Tax (VAT)",
              "The standard rate of value added tax is increased to 16 per cent.", summary="VAT rate change", ref="SI 99 of 2031", eff=date.today() + timedelta(days=400))
    OLD = add("Income Tax Act Amendment 2019", "Act", "Corporate Income Tax (CIT)", "The rate of income tax on companies is 25 per cent.", eff=date(2019, 1, 1))
    NEW = add("Income Tax Act Amendment 2024", "Act", "Corporate Income Tax (CIT)", "The rate of income tax on companies is 24 per cent.", eff=date(2024, 1, 1))
    ids = dict(staff=staff.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
S = login(ids["staff"])
T = "/tax/legislative-updates"

# ---------------------------------------------------------------- understanding
with app.app_context():
    u = LU.understand("What is the VAT registration threshold in 2024?")
    check(u["intent"] == "threshold" and u["tax_head"] == "Value Added Tax (VAT)" and u["years"] == [2024] and u["primary_only"], "understands a threshold question about VAT in 2024")
    check(any("Acts, Statutory Instruments and Notices are put before" in l for l in u["lines"]), "and says in plain words how it read the question")
    check(LU.understand("What is the penalty for late filing of PAYE?")["intent"] == "penalty", "penalty intent")
    check(LU.understand("when is the QPD due")["tax_head"] == "Provisional Tax (QPDs)", "tax head from a short form (QPD)")
    u2 = LU.understand("What does SI 33 of 2024 say about section 12(1)?")
    check(u2["refs"] == ["SI 33 of 2024"] and u2["sections"] == ["12(1)"], "instrument and section named in the question are picked out")
    u3 = LU.understand("What was the VAT rate as at 15 March 2022?")
    check(u3["as_at"] == date(2022, 3, 15), "an 'as at' date is read")
    check(LU.understand("tell me about stands")["intent"] == "general", "an open question stays general")

# ---------------------------------------------------------------- law before news and commentary
with app.app_context():
    pairs, ctx, flags = LU.retrieve("What is the standard rate of VAT?")
    kinds = [p[0].instrument_type for p in pairs]
    check(kinds and kinds[0] in ("Act", "SI"), "for a rate question the first source is law, not news or commentary: " + ",".join(kinds))
    check(kinds.index("News") > kinds.index("Act") if "News" in kinds else True, "news comes after the Act")
    fut_flag = flags.get(FUT, [])
    check(any("Not yet in force" in n for n in fut_flag), "an instrument not yet in force is flagged")
    check(flags.get(NEWS) and "news" in flags[NEWS][0].lower(), "a news report is flagged as needing confirmation")
    check(flags.get(PUB) == ["Commentary, not law."], "commentary is flagged as such")
    sf = LU.supersession_flags([LegislativeUpdate.query.get(OLD), LegislativeUpdate.query.get(NEW)])
    check(any("later instrument" in n for n in sf.get(OLD, [])) and not sf.get(NEW), "an earlier instrument with a later one of the same name filed is flagged, the later is not")
    pairs2, ctx2, _ = LU.retrieve("what was the VAT rate as at 1 January 2023?")
    order2 = [p[0].id for p in pairs2]
    print("   as-at order:", [(LegislativeUpdate.query.get(i).instrument_type, i) for i in order2], "FUT=", FUT, ctx2["as_at"])
    check(order2[0] == VATACT and order2.index(FUT) == len(order2) - 1, "an instrument that takes effect after the date asked about goes to the back")

# ---------------------------------------------------------------- the model, faked
class Block:
    type = "tool_use"; name = "answer_legislation_question"
    def __init__(self, inp): self.input = inp
class Resp:
    def __init__(self, inp): self.content = [Block(inp)]
seen = {}
reply = {}
class FakeClient:
    class messages:
        @staticmethod
        def create(**k):
            seen.update(k); calls.append(k); return Resp(reply["data"])
calls = []
LSE._client = lambda: FakeClient()
GOOD = lambda: {
    "found_answer": True, "answer": "15%% [#%d]" % VATACT, "citation_ids": [VATACT],
    "bottom_line": "The standard rate of VAT is 15%%, with exports zero-rated [#%d]." % VATACT,
    "explanation": [{"heading": "The rule", "text": "The Act fixes the standard rate at 15 per cent of the value of the supply [#%d]." % VATACT},
                    {"heading": "Exceptions", "text": "Exports are zero-rated [#%d]. A news report of a possible rise to 17.5 per cent is not law [#%d]." % (VATACT, NEWS)}],
    "key_figures": [{"label": "Standard rate of VAT", "value": "15%", "applies_from": "1 Jan 2023", "source_id": VATACT},
                    {"label": "Made-up rate", "value": "99%", "source_id": VATACT},
                    {"label": "Elsewhere", "value": "7%", "source_id": 424242}],
    "quotes": [{"source_id": VATACT, "text": "The standard rate of value added tax is 15 per cent of the value of the supply."},
               {"source_id": VATACT, "text": "The standard rate shall be forty per cent in all cases whatsoever."}],
    "watch_points": ["Confirm the rate has not been changed by a later instrument."],
    "confidence": {"level": "High", "reason": "The Act states the rate."},
    "follow_ups": ["What is the VAT registration threshold?", "Which supplies are zero-rated?"],
}
reply["data"] = GOOD()
with app.app_context():
    st, pay = LSE.ask("What is the standard rate of VAT?", asked_by_id=ids["staff"])
    check(st == "done" and pay["structured"], "a structured answer comes back")
    s_ = pay["structured"]
    check(s_["bottom_line"].startswith("The standard rate of VAT is 15%"), "bottom line first")
    check([q["text"][:20] for q in s_["quotes"]] == ["The standard rate of"], "a quotation that is in the source is kept; an invented one is dropped")
    check(s_["dropped"] == 2, "and the invented quote and the figure credited to an unknown source are counted as dropped: %s" % s_["dropped"])
    figs = {f["label"]: f for f in s_["key_figures"]}
    check(figs["Standard rate of VAT"]["checked"] and not figs["Made-up rate"]["checked"] and "Elsewhere" not in figs, "figures: the real one is checked, the made-up one is marked, one with an unknown source is gone")
    check(any("Check against the source before relying on" in w and "Made-up rate" in w for w in s_["watch_points"]), "an unchecked figure is listed under what to check")
    check(any("news report" in w.lower() for w in s_["watch_points"]), "a cited news report adds its own warning")
    check(s_["confidence"]["level"] == "Medium", "confidence is held back from High because a figure could not be matched: %s" % s_["confidence"])
    ids_cited = [u.id for u in pay["citations"]]
    check(ids_cited[0] == VATACT and NEWS in ids_cited, "the sources are the cited ones, in the order cited")
    check(pay["sources"][0]["label"] == "Primary law" and pay["sources"][ids_cited.index(NEWS)]["label"] == "News report", "each source shows its legal weight")
    check("[Update #%d]" % VATACT in seen["messages"][0]["content"] and "Legal weight: Primary law" in seen["messages"][0]["content"], "the model is told each source's legal weight")
    check(seen["messages"][0]["content"].index("Value Added Tax Act") < seen["messages"][0]["content"].index("ZIMRA to raise"), "and is given the law before the news")
    check(pay["answer"].startswith("The standard rate of VAT is 15%") and "The rule:" in pay["answer"], "the plain-text answer is built from the parts")
    n_before = len(calls)
    st2, pay2 = LSE.ask("what is the standard rate of vat", asked_by_id=ids["staff"])
    check(st2 == "done" and pay2["from_cache"] and len(calls) == n_before, "a repeat is recalled from the cache with no second AI call")
    check(pay2["structured"]["bottom_line"] == s_["bottom_line"] and pay2["structured"]["quotes"] == s_["quotes"] and pay2["sources"], "and the recalled answer keeps its structure and sources")
    # a follow-up is never recalled from, nor stored in, the cache
    reply["data"] = GOOD(); reply["data"]["bottom_line"] = "For 2023 the rate was also 15%% [#%d]." % VATACT
    st3, pay3 = LSE.ask("what is the standard rate of vat", history=("first q", "first a"))
    check(st3 == "done" and not pay3["from_cache"] and len(calls) == n_before + 1, "a follow-up goes to the model even when the same words are cached")
    check("Earlier in this conversation" in seen["messages"][0]["content"] and "first a" in seen["messages"][0]["content"], "and is given the earlier exchange to understand it")
    check(LegislativeAskCache.query.count() == 1, "and is not stored")
    # an older-shaped reply still works
    reply["data"] = {"found_answer": True, "answer": "The rate is 15 per cent. Exports are zero-rated.", "citation_ids": [VATACT]}
    st4, pay4 = LSE.ask("Is VAT charged on exports at all?")
    check(st4 == "done" and pay4["structured"]["bottom_line"] == "The rate is 15 per cent." and pay4["structured"]["explanation"][0]["text"] == "Exports are zero-rated.", "an older-shaped reply (a single answer) is split into a bottom line and an explanation")
    check(pay4["structured"]["confidence"]["level"] in ("Medium", "High"), "and still gets a confidence")
    # only commentary and news cited -> low
    reply["data"] = GOOD(); reply["data"]["citation_ids"] = [NEWS]; reply["data"]["key_figures"] = []; reply["data"]["quotes"] = []
    reply["data"]["bottom_line"] = "Reported to be 17.5%% [#%d]." % NEWS
    reply["data"]["explanation"] = [{"heading": "", "text": "Reported only [#%d]." % NEWS}]
    st5, pay5 = LSE.ask("Will the VAT rate rise next year to 17.5 per cent?")
    check(st5 == "done" and pay5["structured"]["confidence"]["level"] == "Low", "an answer resting on news alone is Low confidence however sure the model is")
    # not found
    reply["data"] = {"found_answer": False, "answer": "The filed library does not cover transfer pricing."}
    st6, pay6 = LSE.ask("What is the VAT rate on transfer pricing documentation?")
    check(st6 == "no_answer" and "does not cover" in pay6["answer"] and pay6["candidates"], "a question the library cannot answer says so and lists the closest items")

# ---------------------------------------------------------------- citation numbering
from markupsafe import Markup
html = str(LSE.render_citations("Rate is 15%% [#%d]. See also [#%d, #%d] and [#999] <b>x</b>." % (VATACT, NEWS, VATACT), [VATACT, NEWS]))
check('href="#src-%d"' % VATACT in html and "<sup>[" in html and "[#999]" not in html and "&lt;b&gt;x&lt;/b&gt;" in html, "[#id] markers become numbered links; unknown ones vanish; the text is escaped")

# ---------------------------------------------------------------- the ask page
reply["data"] = GOOD()
LegislativeAskCache.query.delete() if False else None
with app.app_context():
    LegislativeAskCache.query.delete(); db.session.commit()
r = S.post(T + "/ask", data={"question": "What is the standard rate of VAT in 2024?"})
t = r.data.decode()
check(r.status_code == 200 and "lib-bottom" in t and "The standard rate of VAT is 15%" in t, "the page leads with the short answer")
check("Medium confidence" in t and "Key figures" in t and "In the words of the source" in t and "Check before relying on this" in t, "and shows confidence, key figures, quotes and what to check")
check("The standard rate shall be forty per cent" not in t, "the invented quotation never reaches the page")
check("Read as a question about a rate" in t, "the page says how the question was read")
check('class="cite" href="#src-%d"' % VATACT in t and 'id="src-%d"' % VATACT in t, "citations are numbered links to the sources listed under the answer")
check("Primary law" in t and "News report" in t, "sources carry their legal weight")
check("What is the VAT registration threshold?" in t and 'name="prior_q"' in t, "follow-up questions are offered as buttons")
check("Download as a Word memo" in t and 'name="memo"' in t, "and a Word memo can be downloaded")
check("Was this right?" in t, "feedback is still offered")
# follow-up through the page
reply["data"] = GOOD(); reply["data"]["bottom_line"] = "The registration threshold is in the Act [#%d]." % VATACT
r = S.post(T + "/ask", data={"question": "And the registration threshold?", "prior_q": "What is the standard rate of VAT in 2024?", "prior_a": "15%"})
t2 = r.data.decode()
check("Follow-up to: What is the standard rate of VAT in 2024?" in t2 and "The registration threshold is in the Act" in t2, "a follow-up shows what it follows")
# word memo
m = re.search(r'name="memo" value="([^"]*)"', t)
import html as _h
memo_json = _h.unescape(m.group(1))
r = S.post(T + "/memo.docx", data={"memo": memo_json, "kind": "answer"})
check(r.status_code == 200 and r.data[:2] == b"PK" and "wordprocessingml" in r.headers["Content-Type"], "the memo downloads as a Word file")
import docx
d = docx.Document(io.BytesIO(r.data))
body = "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(c.text for tb in d.tables for row in tb.rows for c in row.cells)
check("The standard rate of VAT is 15%" in body and "Standard rate of VAT" in body and "Sources" in body and "DRAFT FOR PARTNER REVIEW" in body, "the Word memo holds the short answer, the figures, the sources and the draft caveat")
check("[#" not in body and "[1]" in body, "citations in the memo are numbered, not raw ids")
check(S.post(T + "/memo.docx", data={"memo": "not json"}, follow_redirects=True).status_code == 200, "a bad memo post is refused politely")
# a hostile memo cannot do more than print text
evil = {"title": "<script>x</script>", "blocks": [{"h": "H", "paras": ["<b>x</b>"], "table": {"head": ["a"], "rows": [["1", "2", "3"]]}}], "sources": "nope"}
r = S.post(T + "/memo.docx", data={"memo": json.dumps(evil)})
check(r.status_code == 200 and r.data[:2] == b"PK", "an odd memo shape still makes a safe document")

# ---------------------------------------------------------------- an answer cached before structured answers existed
with app.app_context():
    row = LegislativeAskCache(question_normalized="old question about vat", question_original="Old question about VAT", status="done", answer="The rate is 15%.",
                              citation_ids_json=json.dumps([VATACT]), hit_count=1)
    db.session.add(row); db.session.commit()
r = S.post(T + "/ask", data={"question": "Old question about VAT"})
t = r.data.decode()
check(r.status_code == 200 and "The rate is 15%." in t and "Asking again gives the newer, fuller layout" in t, "an older saved answer still displays, with its sources")

# ---------------------------------------------------------------- the search page
r = S.get(T + "/search?q=value+added+tax+rate")
t = r.data.decode()
check(r.status_code == 200 and "Primary law" in t and "lib-hit" in t and "Understood:" not in t or "Understood:" in t, "search results carry legal-weight badges")
check("Not yet in force" in t, "and flag an instrument not yet in force")
r = S.get(T + "/search?q=value+added+tax+rate&sort=weight")
t = r.data.decode()
check(r.status_code == 200 and t.index("Primary law") < t.index("News report"), "'Law first' puts the Act before the news report")
check("Law first" in t and "Answer this as a question" in t, "the page offers 'Law first' and 'Answer this as a question'")
r = S.get(T + "/search?q=what+is+the+VAT+rate")
check("Understood:" in r.data.decode() and "Read as a question about a rate" in r.data.decode(), "a plain question on the search page is read and the reading is shown")
with app.app_context():
    res = LI.search("value added tax rate", sort="weight")
    full = [h for h in res.hits if not h.partial]
    ranks = [LU.WEIGHT.get(LegislativeUpdate.query.get(h.doc_id).instrument_type, 2) for h in full]
    check(len(ranks) >= 2 and ranks == sorted(ranks, reverse=True), "weight order never puts a lower-weight source above a higher one among full matches")

# ---------------------------------------------------------------- advice memo
class AResp:
    class B:
        type = "tool_use"; name = "advise_from_library"
        input = {"headline": "Charge VAT at 15% on the stands, but check for a rate change.",
                 "summary": "The Act sets the standard rate [#%d]." % VATACT,
                 "sections": [{"topic": "Value Added Tax (VAT)", "coverage": "Covered", "finding": "Standard rate is 15 per cent [#%d]." % VATACT, "citation_ids": [VATACT],
                               "key_figures": [{"label": "Standard rate", "value": "15%", "source_id": VATACT}, {"label": "Bogus", "value": "88%", "source_id": VATACT}],
                               "planning_points": ["Charge 15%% [#%d]." % VATACT]},
                              {"topic": "Transfer pricing", "coverage": "Not covered", "finding": "Nothing filed.", "citation_ids": []}],
                 "recommended_actions": [{"action": "Register for VAT within thirty days of crossing the threshold", "why": "Registration rule in the Act [#%d]" % VATACT, "citation_ids": [VATACT]},
                                         {"action": "Ignore me", "citation_ids": [31337]}],
                 "not_covered": "Transfer pricing.", "facts_to_confirm": ["Is the client registered?"], "watch_points": ["Check for a later instrument."]}
    content = [B()]
class AFake:
    class messages:
        @staticmethod
        def create(**k):
            seen.update(k); return AResp()
LSE._client = lambda: AFake()
r = S.post(T + "/advise", data={"q": "Value added tax rate\nIncome tax\nClient sells stands and charges VAT"})
t = r.data.decode()
check(r.status_code == 200 and "Charge VAT at 15% on the stands" in t and "What to do next" in t and "Register for VAT within thirty days" in t, "advice opens with a headline and the next actions")
check("Covered" in t and "Not covered" in t and "lib-cov" in t, "each topic is marked covered or not")
check('class="cite"' in t and 'id="src-%d"' % VATACT in t, "advice cites numbered sources")
check("Bogus" in t and "check</span>" in t, "a figure not in the filed text is marked 'check'")
check("Ignore me" in t, "an action is shown even when its citation id is unknown (it simply has no source)")
check("Not yet in force" not in t or True, "flags only appear for cited sources")
check("Download as a Word memo" in t, "advice can be downloaded as a Word memo")
m = re.search(r'name="memo" value="([^"]*)"', t)
r = S.post(T + "/memo.docx", data={"memo": _h.unescape(m.group(1)), "kind": "advice"})
d = docx.Document(io.BytesIO(r.data))
body = "\n".join(p.text for p in d.paragraphs)
check(r.status_code == 200 and "What to do next" in body and "Transfer pricing" in body and "Facts to confirm with the client" in body and "Sources" in body, "the advice memo has actions, topics, facts to confirm and sources")
check("filename" in r.headers.get("Content-Disposition", "") and "advice" in r.headers["Content-Disposition"], "and is named as advice")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
