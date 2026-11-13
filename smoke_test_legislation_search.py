"""Deep search of the Legislative Update Control library (legislation_index.py): ranking over every passage, word endings, typos, the firm's
short forms, references, phrases, exclusions, wildcards, filters, a pasted brief of topics plus facts, the full-text reader, drafting
advice from the library, and the index following changes to the library."""
import os, sys, tempfile, time
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
from datetime import date
from app import create_app
from extensions import db
from models import *
import legislation_index as LI
import legislation_search as LSE
app = create_app(); app.config["WTF_CSRF_ENABLED"] = False
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

FILL = ("The Commissioner shall keep a register of every return lodged and every person shall furnish such information as may be required by notice in "
        "writing. A notice under this section shall be served at the last known address of the person and shall state the time within which it must be "
        "complied with. Where a person fails to comply the Commissioner may proceed to act on the information in his possession. ")
def filler(n):
    out = []
    for i in range(n):
        out.append(f"{i + 2}. Administration of part {i + 1}\n" + FILL * 3)
    return "\n".join(out)

with app.app_context():
    partner = User(username="ptn", name="Partner P", role="partner"); partner.set_password("x")
    staff = User(username="stf", name="Staff S", role="staff"); staff.set_password("x")
    db.session.add_all([partner, staff]); db.session.commit()
    def add(title, typ, head, text=None, summary=None, ref=None, eff=None, **kw):
        u = LegislativeUpdate(title=title, instrument_type=typ, tax_head=head, summary=summary, extracted_text=text, extraction_status="extracted" if text else None,
                              source_reference=ref, effective_date=eff, **kw)
        db.session.add(u); db.session.commit()
        return u.id
    A = add("Income Tax Act [Chapter 23:06]", "Act", "Corporate Income Tax (CIT)",
            "1. Short title\nThis Act may be cited as the Income Tax Act.\n" + filler(9) + "\n15. Deductions\nExpenditure incurred in the production of income, including the cost of developing "
            "stands, is deductible when incurred by the taxpayer who derives the income from the sale of the stands. A related company that bears development costs for "
            "another cannot deduct them against its own income.", summary="Income tax on companies", eff=date(2024, 1, 1))
    B = add("Value Added Tax Act [Chapter 23:12]", "Act", "Value Added Tax (VAT)",
            "14. Input tax\nInput tax may only be claimed where the operator holds a valid tax invoice.\n" + filler(2) + "\n30. Supply of land\nThe sale of stands by a registered developer is a taxable supply at the standard rate.",
            summary="VAT on supplies", eff=date(2024, 1, 1))
    C = add("Statutory Instrument 33 of 2024 - Withholding Tax on Contractors", "SI", "Withholding Tax (WHT)",
            "1. Citation\nContractors and suppliers shall be subject to withholding tax unless they hold a valid tax clearance certificate. The rate is ten percent of the payment.",
            summary="Withholding tax on contractors and suppliers", ref="SI 33 of 2024", eff=date(2024, 3, 1))
    D = add("Capital Gains Tax Act [Chapter 23:01]", "Act", "Capital Gains Tax (CGT)",
            "5. Immovable property\nCapital gains tax applies to the disposal of immovable property. Stands sold by a developer who holds them as trading stock are taxed as income and not as a capital gain.",
            summary="Capital gains on property", eff=date(2023, 1, 1))
    E = add("SAIT Guide: transfer of development costs between group companies", "Pub", None,
            "Where one company incurs development costs for stands owned by a related company, the costs should be recharged at arm's length. If they are not, the company that earns the income "
            "from the sale of the stands may be denied the deduction, and the company that paid may face a challenge on its own return.", summary="Commentary on recharging development costs")
    F = add("Finance Act 2024 - wear and tear rates", "Act", None, None, summary="Changes the wear and tear allowance and depreciation rates for plant", eff=date(2024, 1, 1))
    G = add("Customs tariff changes for motor vehicles", "News", "Customs & Excise", "Importers of motor vehicles will pay revised customs duty from next month.")
    ids = dict(A=A, B=B, C=C, D=D, E=E, F=F, G=G, partner=partner.id, staff=staff.id)
    r = LegislativeUpdate.query.get(C); r.reviewed_at = None
    db.session.commit()

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c
P, S = login(ids["partner"]), login(ids["staff"])
T = "/tax/legislative-updates"

def top(q, n=3, **kw):
    with app.app_context():
        res = LI.search(q, **kw)
        return [h.doc_id for h in res.hits[:n]], res

# ---------------------------------------------------------------- stemming
st = LI.stem
check(len({st(w) for w in ("deduct", "deducted", "deducting", "deduction", "deductions", "deductible")}) == 1, "deduct / deducted / deduction / deductible meet")
check(len({st(w) for w in ("depreciate", "depreciated", "depreciating", "depreciation")}) == 1, "depreciate / depreciation meet")
check(st("taxes") == st("taxed") == st("tax") and st("companies") == st("company") and st("stands") == st("stand"), "tax / taxes / taxed, company / companies, stand / stands meet")
check(LI.normalize("S.I. 33/2024") == LI.normalize("statutory instrument 33 of 2024") == "si 33 of 2024", "references are written one way: S.I. 33/2024 = SI 33 of 2024")

# ---------------------------------------------------------------- the engine
with app.app_context():
    ids_top, res = top("deducted")
    check(ids_top[0] == A, "word endings: 'deducted' finds the Income Tax Act (deductible) first")
    hit = res.hits[0]
    check(hit.passages and hit.passages[0][0].chunk_index >= 2, "a word on the last pages is found: the passage is a late chunk of the Act")
    html = str(LI.snippet(hit.passages[0][0].text, res.plan))
    check("<mark>deductible</mark>" in html, "and marked in the snippet")
    check(top("stands")[0][0] in (A, B, D, E), "'stands' finds documents that mention stands")

    check(top("VAT")[0][0] == B and top("value added tax")[0][0] == B, "VAT and 'value added tax' both find the VAT Act first")
    check(top("S.I. 33/2024")[0][0] == C and top("statutory instrument 33 of 2024")[0][0] == C and top("SI 33 of 2024")[0][0] == C, "a reference is found however it is written")
    check(top("wear and tear")[0][0] == F and top("capital allowance")[0][0] == F, "the firm's vocabulary: wear and tear = capital allowance = depreciation")
    ids_top, res = top("withholdng tax contractrs")
    check(ids_top and ids_top[0] == C and any(n[0] == "fuzzy" for n in res.plan.notes), "typos are forgiven, and the page says what it also searched")
    check(any("“withholdng”" in x or "withholdng" in x for x in LI.describe(res.plan)), "describe() reports the correction")
    ids_top, res = top('"related company"')
    check(set(ids_top) == {A, E}, "an exact phrase finds only documents holding it (A and E)")
    ids_top, res = top('stands -capital')
    check(D not in [h.doc_id for h in res.hits] and A in [h.doc_id for h in res.hits], "-word leaves out documents containing it")
    ids_top, res = top("deprec*")
    check(ids_top[:1] == [F], "a stem with a wildcard finds depreciation")
    check(top("zebra giraffe")[0] == [], "words the library does not have find nothing")

    # all the words somewhere in the document, not one passage; partial matches underneath and labelled
    ids_top, res = top("contractors invoice")
    check(res.total >= 1 and all(h.partial for h in res.hits) and {C, B} & set(ids_top), "words split across documents give labelled partial matches")
    ids_top, res = top("input tax invoice")
    check(ids_top[0] == B and not res.hits[0].partial, "all words in one document is a full match")

    # filters and order
    ids_top, res = top("tax", filters={"types": ["SI"]})
    check([h.doc_id for h in res.hits] == [C], "filter by type")
    ids_top, res = top("tax", filters={"year": 2024})
    check({h.doc_id for h in res.hits} <= {A, B, C, F} and C in {h.doc_id for h in res.hits}, "filter by year (effective 2024 or 2024 in the reference)")
    ids_top, res = top("tax", filters={"tax_head": "Capital Gains Tax (CGT)"})
    check([h.doc_id for h in res.hits] == [D], "filter by tax head")
    ids_top, res = top("tax", sort="newest")
    check(ids_top[0] in (C,) , "newest first puts the latest effective date first")
    check(sum(res.facets.values()) >= 4 and res.facets.get("Act", 0) >= 2, "type counts are given")

    # a brief
    BRIEF = ("Core tax planning and compliance areas\n2. Value Added Tax (VAT)\n3. Corporate Income Tax\n4. Contractor and Supplier Tax Compliance\n"
             "5. Land Sales and Property Development Tax\n6. Withholding Taxes and Other Statutory Obligations.............................here is Homelux Property Developers "
             "which is the one which runs the day to day then Homelux Land developers this one owns Kutandara and Bridgepoint. However previously all development costs and "
             "expeses would come under HPD yet the income would go to the company selling the stands like HLD..... Want to give best advisory")
    topics, facts = LI.split_brief(BRIEF)
    check(topics == ["Core tax planning and compliance areas", "Value Added Tax (VAT)", "Corporate Income Tax", "Contractor and Supplier Tax Compliance",
                     "Land Sales and Property Development Tax", "Withholding Taxes and Other Statutory Obligations"], "the pasted outline is read as six topics")
    check("Homelux Land developers" in facts and "Want to give best advisory" in facts and "Value Added" not in facts, "and the rest as the facts")
    check(LI.is_brief(BRIEF) and not LI.is_brief("input tax invoice") and not LI.is_brief("VAT"), "a brief is recognised; a few search words are not")
    br = LI.search_brief(BRIEF)
    tr = {t["title"]: t["result"] for t in br["topics"]}
    check(tr["Value Added Tax (VAT)"].hits[0].doc_id == B, "topic VAT: the VAT Act first")
    check(tr["Corporate Income Tax"].hits[0].doc_id in (A, D), "topic Corporate Income Tax: the Income Tax Act among the first")
    check(A in [h.doc_id for h in tr["Corporate Income Tax"].hits[:2]], "... the Income Tax Act in the first two")
    check(tr["Contractor and Supplier Tax Compliance"].hits[0].doc_id == C, "topic contractors: the withholding tax SI first")
    check(tr["Withholding Taxes and Other Statutory Obligations"].hits[0].doc_id == C, "topic withholding taxes: the same SI first")
    check({D, B} & {h.doc_id for h in tr["Land Sales and Property Development Tax"].hits}, "topic land sales: property / stands documents found")
    ov = br["overall"]
    check(ov.scenario and len(ov.plan.concepts) >= 6, "the whole story is reduced to key concepts: " + ", ".join(ov.plan.concepts[:10]))
    check("homelux" in [d.lower() for d in ov.plan.dropped] or "Homelux" in ov.plan.dropped, "names the library has never seen are set aside, not required")
    ov_ids = [h.doc_id for h in ov.hits[:3]]
    check(A in ov_ids and E in ov_ids, "overall: the Income Tax Act and the commentary on development costs are in the top three")
    eh = next(h for h in ov.hits if h.doc_id == E)
    check(getattr(eh, "pair_hits", 0) >= 1, "'development costs' side by side in a passage is rewarded")
    pairs = LSE.search_candidate_chunks(BRIEF, limit=12)
    check(pairs and any(u.id == A and c is not None and c.chunk_index >= 2 for u, c in pairs), "Ask the library gets the deep passage of the Act for the same story")
    check(len({(u.id, c.id if c else None) for u, c in pairs}) == len(pairs) and sum(1 for u, c in pairs if u.id == A) <= 3, "with at most three passages from one document")

# ---------------------------------------------------------------- pages
check(app.test_client().get(T + "/search?q=tax").status_code in (301, 302, 401), "the search page needs a sign-in")
r = S.get(T + "/search?q=deducted")
check(r.status_code == 200 and b"<mark>deductible</mark>" in r.data and b"Income Tax Act" in r.data and b"open this passage" in r.data, "any signed-in user can search; the page shows highlighted passages with a link")
r = S.get(T + "/search?q=")
check(r.status_code == 200 and b"Search the library" in r.data, "an empty search shows the form")
r = S.get(T + "/search?q=tax&type=SI")
_plain = __import__("re").sub(rb"<[^>]+>", b"", r.data)
check(r.status_code == 200 and b"Withholding Tax on Contractors" in _plain and b"Capital Gains Tax Act" not in _plain, "the type filter works on the page")
r = S.get(T + "/search?q=" + "%22related+company%22")
check(r.status_code == 200 and b"The exact phrase" in r.data, "the page explains how a phrase was read")
import urllib.parse
r = S.get(T + "/search?q=" + urllib.parse.quote(BRIEF))
check(r.status_code == 200 and b"Read as a description" in r.data and b"Land Sales and Property Development Tax" in r.data and b"The whole situation" in r.data, "a pasted brief gets a section per topic and the whole situation")
check(b"Draft advice from the library" in r.data, "and the button to draft advice")
r = S.get(T + "/search?q=zebra+giraffe")
check(r.status_code == 200 and b"Nothing filed matches that" in r.data, "no results is said plainly")
r = S.get(T)
check(r.status_code == 200 and b"Search the library" in r.data and b"Ask the library" in r.data, "the log page offers the search box beside Ask the library")

with app.app_context():
    a_chunks = LegislativeUpdateChunk.query.filter_by(legislative_update_id=A).order_by(LegislativeUpdateChunk.chunk_index).all()
    last = a_chunks[-1]
r = S.get(T + f"/{A}/read?q=deducted")
check(r.status_code == 200 and f'id="chunk-{last.id}"'.encode() in r.data and b"<mark>deductible</mark>" in r.data and b"Jump to the strongest passage" in r.data, "the reader shows the full text with the words marked and a jump link")
r = S.get(T + f"/{A}/read")
check(r.status_code == 200 and b"Short title" in r.data or b"This Act may be cited" in r.data, "the reader opens without a search")
r = S.get(T + f"/{F}/read?q=wear")
check(r.status_code == 200 and b"No filed text" in r.data, "a text-only log entry says it has no filed text")
check(S.get(T + "/99999/read").status_code == 404, "an unknown document is a 404")

# ---------------------------------------------------------------- advice from the library
class Resp:
    class B:
        type = "tool_use"; name = "advise_from_library"
        input = {"summary": "Costs and income sit in different companies.", "not_covered": "Transfer pricing rules between related companies.",
                 "facts_to_confirm": ["Who owns the stands legally?"],
                 "sections": [{"topic": "Corporate Income Tax", "finding": "Deduction follows the taxpayer who earns the income (section 15).", "citation_ids": [ids["A"]],
                               "planning_points": ["Recharge the costs at arm's length."]},
                              {"topic": "Value Added Tax (VAT)", "finding": "The sale of stands by a registered developer is taxable.", "citation_ids": [ids["B"], 99999]}]}
    content = [B()]
seen = {}
class FakeClient:
    class messages:
        @staticmethod
        def create(**k):
            seen.update(k); return Resp()
LSE._client = lambda: FakeClient()
r = S.post(T + "/advise", data={"q": BRIEF})
check(r.status_code == 200 and b"Draft advice from the filed library" in r.data and b"Costs and income sit in different companies" in r.data
      and b"Recharge the costs" in r.data and b"Transfer pricing" in r.data and b"Who owns the stands" in r.data, "advice is drafted topic by topic with the gaps and facts to confirm")
check(b"Income Tax Act" in r.data and b"/read" in r.data, "and cites the filed documents with links into the text")
msg = seen["messages"][0]["content"]
check("Homelux Land developers" in msg and "[Update #%d]" % ids["A"] in msg and "stands" in msg and seen["tool_choice"]["name"] == "advise_from_library", "the model is given the facts and the filed excerpts, and a structured result is required")
check("ONLY the excerpts" in seen["system"] and "not law" in seen["system"], "and told to use only the excerpts, treating the facts as the partner's own statement")
r = S.post(T + "/advise", data={"q": ""}, follow_redirects=True)
check(b"Describe the situation first" in r.data, "an empty description is refused")
r = S.post(T + "/advise", data={"q": "zebra giraffe unicorn"})
check(b"Nothing filed in the library matches" in r.data, "nothing matching means no advice is invented")
del os.environ["ANTHROPIC_API_KEY"]
with app.app_context():
    st_, pay = LSE.advise(BRIEF)
    check(st_ == "not_configured", "without the AI key it says so (search still works)")
os.environ["ANTHROPIC_API_KEY"] = "test-key"

# ---------------------------------------------------------------- the index follows the library
with app.app_context():
    check(top("quokka")[0] == [], "before: nothing about quokkas")
    nid = add("Quokka Protection Notice", "Notice", None, "A notice about the protection of the quokka.", summary="Wildlife")
    check(top("quokka")[0] == [nid], "a newly filed document is searchable at once")
    u = LegislativeUpdate.query.get(nid); u.summary = "Wildlife and the numbat"; db.session.commit()
    check(top("numbat")[0] == [nid], "an edited summary is searchable at once")
    db.session.delete(u); db.session.commit()
    check(top("quokka")[0] == [], "a deleted document is gone from the results")

# ---------------------------------------------------------------- speed on a bigger library
with app.app_context():
    for i in range(120):
        add(f"Notice {i}", "Notice", "Value Added Tax (VAT)", (filler(2) + f"\n{i}. Rule {i}\nThe operator shall account for output tax on supplies number {i}.\n") * 2)
    t0 = time.time(); LI.search("output tax supplies"); built = time.time() - t0
    t0 = time.time(); res = LI.search("output tax supplies"); q1 = time.time() - t0
    t0 = time.time(); LI.search(BRIEF); q2 = time.time() - t0
    check(res.total >= 100 and q1 < 1.0 and q2 < 2.0, f"a library of 127 documents: build {built:.1f}s, search {q1 * 1000:.0f} ms, pasted brief {q2 * 1000:.0f} ms")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
