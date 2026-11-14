"""Understanding a question before the library is searched, and weighing what comes back.

`understand(question)` reads the plain-English question the way a tax practitioner would: what kind of answer is wanted (a rate, a limit, a
deadline, a penalty, a definition, a procedure, a deduction, an exemption, a comparison), which tax head it is about, which year or date it is
about, and any instrument or section it names. No AI call: it is pattern reading, so it works with or without the AI key and costs nothing.

`retrieve(question, ...)` then asks the deep-search engine (legislation_index) for more passages than are needed and re-orders them by
what a careful practitioner would trust first:

  * legal weight - an Act, Statutory Instrument or Government Notice before a case, a case before a publication, a publication before news;
    for a question about a rate, limit or deadline the commentary and news are pushed well down;
  * the tax head asked about;
  * the year or date asked about - an instrument not yet in force on that date is put down and flagged; an instrument with a later one of
    the same name filed is flagged as possibly superseded;
  * a named instrument or section.

Every re-ordering is explained in plain words (`notes`) and per document (`flags`) so a person can see why a source came first and what to
check. The weights only re-order passages the search found: they never add a document that did not match the words.
"""
import re
from datetime import date

from models import TAX_HEADS, LegislativeUpdate

# how much a source counts as law, most to least
WEIGHT = {"Act": 5, "SI": 5, "Notice": 4, "Case": 3, "Pub": 2, "News": 1, None: 2, "": 2}
WEIGHT_LABEL = {5: "Primary law", 4: "Primary law (notice)", 3: "Case law", 2: "Commentary", 1: "News report"}
WEIGHT_COLOUR = {5: "#1F3A5F", 4: "#1F3A5F", 3: "#5b3f8c", 2: "#6b6b6b", 1: "#9a6b00"}

_HEADS = [
    ("Value Added Tax (VAT)", r"\bvat\b|value[- ]added|input tax|output tax|zero[- ]rated|vat registration"),
    ("PAYE", r"\bpaye\b|pay as you earn|employees'? tax|\bnssa\b|\bfringe benefit"),
    ("Capital Gains Tax (CGT)", r"\bcgt\b|capital gains?|specified asset"),
    ("Withholding Tax (WHT)", r"withholding|\bwht\b|non[- ]resident tax|tax on fees|royalt|\bnrtf\b|\bnrst\b"),
    ("Provisional Tax (QPDs)", r"\bqpd\b|quarterly payment|provisional tax"),
    ("Customs & Excise", r"customs|excise|duty on|import duty|bill of entry|tariff"),
    ("Corporate Income Tax (CIT)", r"income tax|corporate tax|\bcit\b|taxable income|capital allowance|wear and tear|special initial allowance|assessed loss|deductib|tax rate|\bimtt\b"),
]

_INTENTS = [
    ("penalty", r"penalt|interest on|fine\b|surcharge|late (filing|payment|submission)|default|offence|prosecut"),
    ("deadline", r"\bwhen\b.*\b(due|file|submit|pay|lodge|register)|deadline|due date|by what date|within (how many|\d+) (days|months)|time limit|how long|\bby the\b \d"),
    ("rate", r"\brate\b|how much|what percent|percentage|%|\bat what rate\b"),
    ("threshold", r"threshold|limit|cap\b|maximum|minimum|ceiling|exceed|more than|less than|at or above|turnover of"),
    ("exemption", r"exempt|relief|not (be )?taxable|no tax|non[- ]taxable|deferral|rebate"),
    ("deduction", r"deduct|allowable|claim|write[- ]off|expense.*(allowed|deductible)|disallow"),
    ("definition", r"^\s*what (is|are|does)\b.*\b(mean|definition|defined)|\bdefine\b|meaning of|what is (a|an|the)\b"),
    ("procedure", r"how (do|can|should|must) (i|we|a|the)|procedure|process|apply for|steps to|register for|how to\b|requirements? (for|to)"),
    ("compare", r"difference between|compare|\bversus\b|\bvs\.?\b|which (is|applies)|or\b.*\bwhich\b"),
]
# for these, only the law itself should settle the answer
_PRIMARY_INTENTS = {"rate", "threshold", "deadline", "penalty", "exemption", "deduction", "definition"}

_YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
_SI_REF = re.compile(r"\b(?:s\.?\s?i\.?|statutory\s+instrument)\s*(?:no\.?\s*)?(\d{1,4})\s*(?:of|/|-)\s*(\d{4})\b", re.I)
_GN_REF = re.compile(r"\b(?:g\.?\s?n\.?|government\s+notice)\s*(?:no\.?\s*)?(\d{1,4})\s*(?:of|/|-)\s*(\d{4})\b", re.I)
_SECTION = re.compile(r"\b(?:section|sec\.?|s\.?)\s*(\d{1,3}(?:\s*\(\s*[a-z0-9]{1,4}\s*\))*)", re.I)
_DATE_WORDS = re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(19[89]\d|20[0-4]\d)\b", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def understand(question, today=None):
    """What the question is about. Returns {"intent", "tax_head", "years", "as_at", "refs", "sections", "primary_only", "lines"}; `lines` is a
    plain-English description for the page."""
    q = (question or "").strip()
    low = q.lower()
    today = today or date.today()
    out = {"intent": "general", "tax_head": None, "years": [], "as_at": None, "refs": [], "sections": [], "primary_only": False, "lines": []}
    for intent, pat in _INTENTS:
        if re.search(pat, low):
            out["intent"] = intent
            break
    best, best_n = None, 0
    for head, pat in _HEADS:
        n = len(re.findall(pat, low))
        if n > best_n:
            best, best_n = head, n
    out["tax_head"] = best if best in TAX_HEADS else None
    out["years"] = sorted({int(y) for y in _YEAR.findall(q)})
    m = _DATE_WORDS.search(q)
    if m:
        try:
            out["as_at"] = date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        except ValueError:
            pass
    if out["as_at"] is None and out["years"]:
        y = out["years"][-1]
        if re.search(r"\b(in|for|during|year of|year ended|from|as at|since)\b[^.?]*\b" + str(y), low):
            out["as_at"] = date(y, 12, 31) if y < today.year else today
    out["refs"] = [f"SI {n} of {y}" for n, y in _SI_REF.findall(q)] + [f"GN {n} of {y}" for n, y in _GN_REF.findall(q)]
    out["sections"] = [re.sub(r"\s+", "", s) for s in _SECTION.findall(q)][:4]
    out["primary_only"] = out["intent"] in _PRIMARY_INTENTS
    lines = []
    what = {"rate": "a rate", "threshold": "a threshold or limit", "deadline": "a deadline or time limit", "penalty": "a penalty or interest",
            "exemption": "an exemption or relief", "deduction": "whether something is deductible", "definition": "a definition",
            "procedure": "a procedure or requirement", "compare": "a comparison"}.get(out["intent"])
    if what:
        lines.append(f"Read as a question about {what}")
        if out["primary_only"]:
            lines[-1] += ", so the Acts, Statutory Instruments and Notices are put before cases, commentary and news"
        lines[-1] += "."
    if out["tax_head"]:
        lines.append(f"Tax head: {out['tax_head']}.")
    if out["as_at"]:
        lines.append(f"Position as at {out['as_at'].strftime('%d %B %Y')}.")
    elif out["years"]:
        lines.append("Year" + ("s" if len(out["years"]) > 1 else "") + " mentioned: " + ", ".join(str(y) for y in out["years"]) + ".")
    if out["refs"]:
        lines.append("Instrument named: " + ", ".join(out["refs"]) + ".")
    if out["sections"]:
        lines.append("Section named: " + ", ".join("s " + s for s in out["sections"]) + ".")
    out["lines"] = lines
    return out


def _stem_of_title(title):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", (title or "").lower())
    t = re.sub(r"\b(19|20)\d\d\b|\bno\.?\s*\d+\b|\bnumber\s*\d+\b|\bamendment\b|\bamended\b|\bregulations?\b|\bnotice\b|\border\b", " ", t)
    return " ".join(re.sub(r"[^a-z]+", " ", t).split())


def supersession_flags(updates):
    """{update id: [notes]} for the filed updates given: not yet in force, effective much earlier than a later instrument of the same name,
    commentary or news mixed in with law. Only what the filed dates show - nothing is invented."""
    today = date.today()
    flags = {}
    by_stem = {}
    for u in updates:
        if u.instrument_type in ("Act", "SI", "Notice") and u.effective_date:
            by_stem.setdefault(_stem_of_title(u.title), []).append(u)
    for u in updates:
        notes = flags.setdefault(u.id, [])
        if u.effective_date and u.effective_date > today and u.instrument_type in ("Act", "SI", "Notice", "Case"):
            notes.append(f"Not yet in force: effective {u.effective_date.strftime('%d %b %Y')}.")
        stem = _stem_of_title(u.title)
        if stem and u.instrument_type in ("Act", "SI", "Notice") and u.effective_date:
            later = [v for v in by_stem.get(stem, []) if v.id != u.id and v.effective_date and v.effective_date > u.effective_date and v.effective_date <= today]
            # an Act and its amending Acts share a stem only when the title is the same: say "later instrument", never "repealed"
            if later:
                v = max(later, key=lambda x: x.effective_date)
                notes.append(f"A later instrument with the same name is filed (effective {v.effective_date.strftime('%d %b %Y')}): check whether it changes this.")
        if u.instrument_type == "News":
            notes.append("A news report: confirm against the instrument itself.")
        elif u.instrument_type == "Pub":
            notes.append("Commentary, not law.")
        elif u.instrument_type == "Case" and not (u.case_authority_status):
            notes.append("Authority for Zimbabwean practice not yet confirmed by a person.")
    return {k: v for k, v in flags.items() if v}


def _score_pair(u, base_rank, ctx, n_pairs):
    """A sort key (bigger = earlier). The search order is kept as the base, then law, head, date and named instrument adjust it."""
    s = float(n_pairs - base_rank) / max(n_pairs, 1)          # 0..1, the engine's own order
    w = WEIGHT.get(u.instrument_type, 2)
    s += w * (0.5 if ctx["primary_only"] else 0.18)
    if ctx["tax_head"] and u.tax_head:
        s += 0.6 if u.tax_head == ctx["tax_head"] else -0.25
    as_at = ctx["as_at"]
    if u.effective_date and u.effective_date > (as_at or date.today()):
        s -= 4.0 if as_at else 1.6   # not in force on the date asked about: out of the picture; if no date was given, it is an upcoming change - after the law in force
    elif as_at and u.effective_date:
        s += 0.15
    elif ctx["years"] and u.effective_date:
        s += 0.3 if u.effective_date.year in ctx["years"] else 0.0
    if ctx["refs"]:
        ref = (u.source_reference or "") + " " + (u.title or "")
        norm = re.sub(r"[^a-z0-9]+", "", ref.lower())
        for r in ctx["refs"]:
            kind, n, _of, y = r.split()
            if re.sub(r"[^a-z0-9]+", "", f"{kind}{n}of{y}".lower()) in norm or re.sub(r"[^a-z0-9]+", "", f"{kind}{n}/{y}".lower()) in norm:
                s += 2.0
    if ctx["intent"] in ("rate", "threshold", "deadline") and u.effective_date and w >= 4 and not as_at:
        # several versions of a rate or limit: the one in force most recently is the best first answer
        s += min(max((u.effective_date.year - 2000) / 100.0, 0.0), 0.3)
    return s


def retrieve(question, limit=12, per_doc=3, ctx=None):
    """(pairs, ctx, notes): (update, chunk) pairs best first for the question, the understanding of it, and the flags per update."""
    import legislation_index
    ctx = ctx or understand(question)
    wide = legislation_index.rank_chunks(question, limit=max(limit * 2, 24), per_doc=per_doc)
    if not wide:
        return [], ctx, {}
    n = len(wide)
    keyed = [(_score_pair(u, i, ctx, n), i, (u, c)) for i, (u, c) in enumerate(wide)]
    keyed.sort(key=lambda t: (-t[0], t[1]))
    pairs = [p for _s, _i, p in keyed]
    # a question settled by law: keep commentary and news only if there is little law (they are then the only help), and at the back
    if ctx["primary_only"]:
        cutoff = ctx["as_at"] or date.today()
        law = [p for p in pairs if WEIGHT.get(p[0].instrument_type, 2) >= 3]
        not_yet = [p for p in law if p[0].effective_date and p[0].effective_date > cutoff]     # law, but not in force on the date asked about
        law = [p for p in law if p not in not_yet]
        rest = [p for p in pairs if WEIGHT.get(p[0].instrument_type, 2) < 3]
        pairs = law + rest[: max(2, limit - len(law)) if len(law) < limit else 0] + not_yet
    pairs = pairs[:limit]
    flags = supersession_flags(list({u.id: u for u, _c in pairs}.values()))
    return pairs, ctx, flags


def weight_of(update):
    return WEIGHT.get(update.instrument_type, 2)


def weight_label(update):
    return WEIGHT_LABEL.get(weight_of(update), "Commentary")


def weight_colour(update):
    return WEIGHT_COLOUR.get(weight_of(update), "#6b6b6b")
