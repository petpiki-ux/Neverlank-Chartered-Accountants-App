"""Deep search over the firm's Legislative Update Control library.

Dependency-free like the rest of the library (no SQLite FTS extension, no vector database, no embeddings), so it runs the same on
Render and in the packaged .exe (see legislation_search.py). It is a small in-memory search engine:

* an inverted index over every CHUNK of every filed instrument (models.LegislativeUpdateChunk - see legislation_chunking.py), so a word
  buried on page 90 of an Act is found, plus one "front matter" entry per update (title, type, tax head, areas, citation, source
  reference, summaries, key changes);
* BM25 ranking (rare words count for more, long passages do not win just by being long), with extra weight for a word in the title or
  in the section heading, and credit for a passage that holds the whole phrase;
* light stemming (deduct / deducted / deduction / deductible all meet), so word endings do not matter;
* the firm's own vocabulary: VAT = value added tax, QPD = quarterly payment dates / provisional tax, SI = statutory instrument, wear and
  tear = capital allowance, ... both ways round (see SYNONYMS);
* typo tolerance ("deprecation" finds depreciation, and the page says so) and wildcards ("deprec*");
* "exact phrases", -excluded words, and references written any way ("SI 33 of 2024", "S.I. 33/2024", "statutory instrument 33 of 2024");
* a document matches when ALL the words appear somewhere in it (title, front matter or any passage), not necessarily in one passage;
  when that finds little, partial matches are added below and labelled;
* filters (type, tax head, practice area, year, reviewed only) and sorting (best match, newest);
* for each hit, the best passages with the matching words highlighted, and a reader that opens the full text at the right passage.

The index is built in memory the first time it is needed and rebuilt automatically when the library changes (a filing, a new summary,
a deletion), checked with one cheap query per search. It reads text only; it never files or changes anything."""
import bisect
import difflib
import math
import re
import threading
from array import array
from collections import defaultdict
from datetime import date

from markupsafe import Markup, escape

from extensions import db
from models import LegislativeUpdate, LegislativeUpdateChunk

import legislation_chunking

K1, B = 1.4, 0.75
SYNONYM_WEIGHT = 0.7           # a word reached through the vocabulary counts a little less than the word typed
FUZZY_WEIGHT = 0.6
STRICT_MIN_DOCS = 5            # fewer full matches than this: partial matches are added underneath
SNIPPET_CHARS = 300
PER_DOC_PASSAGES = 3
PAGE_SIZE = 15

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "and", "or", "but", "of", "in", "on", "at", "to", "for", "with",
    "about", "as", "by", "from", "into", "than", "what", "when", "where", "who", "whom", "which", "why", "how", "does", "did", "do", "can",
    "could", "should", "would", "will", "shall", "may", "might", "must", "has", "have", "had", "this", "that", "these", "those", "it", "its",
    "any", "all", "there", "our", "we", "you", "current", "currently", "now", "please", "tell", "know", "whats", "affect", "affects",
    "affected", "relevant", "notice", "notices", "update", "updates", "if", "then", "so", "such", "not", "no", "per", "under", "any",
}

# The firm's own words. Each row lists ways of writing the same thing; the first is the short form. A row is used both ways: a search for
# any of them also looks for the others (at a slightly lower weight).
SYNONYMS = [
    ("vat", "value added tax"),
    ("paye", "pay as you earn"),
    ("cgt", "capital gains tax"),
    ("wht", "withholding tax"),
    ("nrt", "non resident tax", "non-resident tax"),
    ("qpd", "quarterly payment dates", "quarterly payment date", "provisional tax"),
    ("zimra", "zimbabwe revenue authority"),
    ("imtt", "intermediated money transfer tax", "money transfer tax"),
    ("tcc", "tax clearance certificate", "tax clearance"),
    ("si", "statutory instrument"),
    ("gn", "government notice"),
    ("fdms", "fiscal device management system", "fiscalisation", "fiscalization"),
    ("ifrs", "international financial reporting standards"),
    ("isa", "international standards on auditing"),
    ("sme", "small and medium enterprises", "small medium enterprise"),
    ("rbz", "reserve bank of zimbabwe", "reserve bank"),
    ("zwg", "zimbabwe gold", "zig"),
    ("pbc", "private business corporation"),
    ("cit", "corporate income tax", "corporate tax"),
    ("itf", "income tax"),
    ("tin", "taxpayer identification number"),
    ("bp", "business partner number"),
    ("aml", "anti money laundering", "anti-money laundering"),
    ("cft", "combating the financing of terrorism"),
    ("ppe", "property plant and equipment"),
    ("npo", "non profit organisation", "non-profit organisation", "pvo", "private voluntary organisation"),
    ("depreciation", "wear and tear", "capital allowance", "capital allowances", "initial allowance"),
    ("penalty", "penalties", "fine", "additional tax"),
    ("tax clearance", "tcc"),
    ("trust", "trustee"),
    ("director", "directors"),
    ("auditor", "audit"),
    ("returns", "return"),
    ("deductible", "deduction", "allowable"),
    ("threshold", "limit", "ceiling"),
    ("dividend", "dividends"),
    ("royalty", "royalties"),
    ("customs", "duty", "duties"),
    ("excise", "excise duty"),
]

_WORD = re.compile(r"[a-z0-9]+")
_SI_REF = re.compile(r"\b(?:s\.?\s?i\.?|statutory\s+instrument)\s*(?:no\.?\s*)?(\d{1,4})\s*(?:of|/|-)\s*(\d{4})\b", re.I)
_GN_REF = re.compile(r"\b(?:g\.?\s?n\.?|government\s+notice)\s*(?:no\.?\s*)?(\d{1,4})\s*(?:of|/|-)\s*(\d{4})\b", re.I)
_QUOTE = re.compile(r'"([^"]+)"')
_EXCLUDE = re.compile(r'(?<![\w"])-([A-Za-z0-9]+)')
_PREFIX = re.compile(r"([A-Za-z0-9]{2,})\*")


# ---------------------------------------------------------------- text handling

def normalize(text):
    """Lower-case text with the ways of writing a reference made the same ('S.I. 33/2024' -> 'si 33 of 2024')."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = _SI_REF.sub(lambda m: f"si {int(m.group(1))} of {m.group(2)}", t)
    t = _GN_REF.sub(lambda m: f"gn {int(m.group(1))} of {m.group(2)}", t)
    return t


def stem(word):
    """A light, predictable stem: the same function is used on the library and on the query, so only consistency matters."""
    w = word.lower()
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]
    if w.endswith("ing") and len(w) > 5:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
    for suf in ("ation", "ions", "ion", "ment", "ible", "able", "ity"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break
    if len(w) > 4 and w[-1] == w[-2] and w[-1] not in "sl":
        w = w[:-1]
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    if w.endswith("at") and len(w) > 5:
        w = w[:-2]
    return w


def words(text):
    return _WORD.findall(normalize(text))


def stems_of(text, keep_stop=False):
    out = []
    for w in words(text):
        if not keep_stop and w in STOPWORDS:
            continue
        if len(w) < 2 and not w.isdigit():
            continue
        out.append(stem(w))
    return out


_SYN_BY_STEMS = None


def _synonym_table():
    """{tuple of stems: [other tuples of stems]} for every way of writing every row of SYNONYMS."""
    global _SYN_BY_STEMS
    if _SYN_BY_STEMS is None:
        table = defaultdict(list)
        for row in SYNONYMS:
            forms = [tuple(stems_of(f, keep_stop=True)) for f in row]
            for f in forms:
                for g in forms:
                    if f != g and g not in table[f]:
                        table[f].append(g)
        _SYN_BY_STEMS = dict(table)
    return _SYN_BY_STEMS


# ---------------------------------------------------------------- the index

class _Index:
    def __init__(self):
        self.sig = None
        self.docs = {}                  # update id -> dict of what filters and sorting need
        self.chunk_meta = []            # position -> (chunk id, update id, chunk index, heading stems, heading text)
        self.chunk_len = array("I")
        self.post_pos, self.post_tf = {}, {}      # stem -> array of chunk positions, array of term frequencies
        self.meta_pos, self.meta_tf = {}, {}      # stem -> array of positions in meta_ids, array of weighted frequencies
        self.meta_ids = []              # position -> update id
        self.meta_len = array("I")
        self.title_stems = {}           # update id -> set of stems in the title
        self.vocab = {}                 # stem -> document frequency (chunks)
        self.sorted_vocab = []
        self.by_first = defaultdict(list)
        self.n_chunks = 0
        self.avg_chunk = 1.0
        self.avg_meta = 1.0


_INDEX = _Index()
_LOCK = threading.Lock()


def _signature():
    """One cheap query: changes whenever a filing, summary, review or deletion changes what can be searched."""
    from sqlalchemy import func
    u = db.session.query(
        func.count(LegislativeUpdate.id), func.max(LegislativeUpdate.id), func.max(LegislativeUpdate.ai_processed_at),
        func.max(LegislativeUpdate.reviewed_at),
        func.coalesce(func.sum(func.length(func.coalesce(LegislativeUpdate.summary, "")) + func.length(func.coalesce(LegislativeUpdate.ai_summary, ""))
                               + func.length(func.coalesce(LegislativeUpdate.impact_assessment, "")) + func.length(func.coalesce(LegislativeUpdate.title, ""))
                               + func.length(func.coalesce(LegislativeUpdate.areas_json, "")) + func.length(func.coalesce(LegislativeUpdate.source_reference, ""))
                               + func.length(func.coalesce(LegislativeUpdate.tax_head, "")) + func.length(func.coalesce(LegislativeUpdate.case_citation, ""))), 0),
    ).one()
    c = db.session.query(func.count(LegislativeUpdateChunk.id), func.max(LegislativeUpdateChunk.id)).one()
    return (tuple(u), tuple(c))


def _ensure_chunks():
    """Chunk anything filed with text but no chunks yet (everything filed before chunk search existed). Cheap when there is nothing to do."""
    have = db.session.query(LegislativeUpdateChunk.legislative_update_id).distinct().subquery()
    missing = (LegislativeUpdate.query.filter(LegislativeUpdate.extracted_text.isnot(None), LegislativeUpdate.extracted_text != "")
               .filter(~LegislativeUpdate.id.in_(db.session.query(have.c.legislative_update_id))).all())
    for upd in missing:
        legislation_chunking.ensure_chunks(upd)
    if missing:
        db.session.commit()


def _meta_fields(u):
    """(text, weight) pairs of the front matter of an update."""
    return [
        (u.title or "", 4), (u.source_reference or "", 2), (u.case_citation or "", 2), (u.type_label or "", 1), (u.tax_head or "", 2),
        (" ".join(u.areas or []), 1), (u.case_court or "", 1), (u.summary or "", 2), (u.ai_summary or "", 2),
        (" ".join(u.ai_key_changes or []), 2), (u.impact_assessment or "", 1),
    ]


def _build():
    idx = _Index()
    idx.sig = _signature()
    pp, pt = defaultdict(lambda: array("I")), defaultdict(lambda: array("H"))
    mp, mt = defaultdict(lambda: array("I")), defaultdict(lambda: array("I"))
    for u in LegislativeUpdate.query.all():
        areas = list(u.areas or [])
        idx.docs[u.id] = {
            "id": u.id, "type": u.instrument_type or "", "tax_head": u.tax_head or "", "areas": areas, "reviewed": bool(u.is_reviewed),
            "effective": u.effective_date, "gazette": u.gazette_date, "created": u.created_at,
            "years": {d.year for d in (u.effective_date, u.gazette_date) if d} | {int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", f"{u.title or ''} {u.source_reference or ''}")},
            "has_text": bool(u.extracted_text),
        }
        idx.title_stems[u.id] = set(stems_of(u.title or ""))
        tf = defaultdict(int)
        total = 0
        for text, weight in _meta_fields(u):
            for s in stems_of(text):
                tf[s] += weight
                total += weight
        pos = len(idx.meta_ids)
        idx.meta_ids.append(u.id)
        idx.meta_len.append(max(total, 1))
        for s, f in tf.items():
            mp[s].append(pos)
            mt[s].append(f)
    n = 0
    total_len = 0
    for cid, uid, cidx, heading, text in (db.session.query(LegislativeUpdateChunk.id, LegislativeUpdateChunk.legislative_update_id, LegislativeUpdateChunk.chunk_index,
                                                            LegislativeUpdateChunk.heading, LegislativeUpdateChunk.text)
                                          .order_by(LegislativeUpdateChunk.legislative_update_id, LegislativeUpdateChunk.chunk_index).yield_per(500)):
        if uid not in idx.docs:
            continue
        st = stems_of(text)
        tf = defaultdict(int)
        for s in st:
            tf[s] += 1
        pos = len(idx.chunk_meta)
        idx.chunk_meta.append((cid, uid, cidx, frozenset(stems_of(heading or "")), heading or ""))
        idx.chunk_len.append(max(len(st), 1))
        total_len += len(st)
        for s, f in tf.items():
            pp[s].append(pos)
            pt[s].append(min(f, 65535))
        n += 1
    idx.n_chunks = n
    idx.avg_chunk = (total_len / n) if n else 1.0
    idx.avg_meta = (sum(idx.meta_len) / len(idx.meta_len)) if idx.meta_len else 1.0
    idx.post_pos, idx.post_tf, idx.meta_pos, idx.meta_tf = dict(pp), dict(pt), dict(mp), dict(mt)
    idx.vocab = {s: len(a) for s, a in idx.post_pos.items()}
    for s in idx.meta_pos:
        idx.vocab.setdefault(s, 0)
    idx.sorted_vocab = sorted(idx.vocab)
    for s in idx.sorted_vocab:
        idx.by_first[s[:1]].append(s)
    return idx


def get_index():
    """The index, rebuilt first if the library changed since it was built."""
    global _INDEX
    try:
        _ensure_chunks()
        sig = _signature()
    except Exception:
        db.session.rollback()
        sig = None
    if sig is None or _INDEX.sig != sig:
        with _LOCK:
            if sig is None or _INDEX.sig != sig:
                _INDEX = _build()
    return _INDEX


def invalidate():
    global _INDEX
    _INDEX = _Index()


# ---------------------------------------------------------------- reading a query

class Plan:
    """What a typed query means: groups of interchangeable ways to match one word each, required phrases, excluded words."""
    def __init__(self):
        self.raw = ""
        self.groups = []        # each: {"label": typed word, "alts": [(stems tuple, weight, kind)]}
        self.phrases = []       # normalized strings that must appear (and their stems)
        self.excluded = []      # stems
        self.notes = []         # ("fuzzy", typed, found) / ("synonym", typed, [alternatives]) / ("prefix", typed, n)
        self.match_stems = set()
        self.scenario = False   # a long description rather than a few words: key concepts are picked out and nothing is required
        self.bigrams = []       # (stem, stem) pairs typed side by side, rewarded when they sit side by side in a passage
        self.concepts = []      # the words a scenario was reduced to, shown to the person
        self.dropped = []       # words of a scenario that the library has no trace of

    @property
    def empty(self):
        return not self.groups and not self.phrases


MAX_CONCEPTS = 16


def is_scenario(q):
    """A description of a situation, a pasted outline or a long question - not a few search words."""
    q = (q or "").strip()
    return len(q) > 160 or len(words(q)) > 14 or ("\n" in q and len(words(q)) > 8)


def parse_query(q, idx=None, scenario=None):
    idx = idx or get_index()
    plan = Plan()
    plan.raw = q or ""
    if scenario is None:
        scenario = is_scenario(q) and '"' not in (q or "")
    plan.scenario = bool(scenario)
    if plan.scenario:
        return _parse_scenario(plan, q, idx)
    text = normalize(q or "")
    for m in _QUOTE.finditer(text):
        ph = " ".join(words(m.group(1)))
        if ph:
            plan.phrases.append(ph)
    text = _QUOTE.sub(" ", text)
    for m in _EXCLUDE.finditer(text):
        plan.excluded.extend(stems_of(m.group(1)))
    text = _EXCLUDE.sub(" ", text)
    prefixes = [m.group(1) for m in _PREFIX.finditer(text)]
    text = _PREFIX.sub(" ", text)
    syn = _synonym_table()
    # known multi-word forms typed out in full ("value added tax") become their short form so they form one group
    for row in SYNONYMS:
        for form in row[1:]:
            f = normalize(form)
            if " " in f and re.search(r"\b" + re.escape(f) + r"\b", text):
                text = re.sub(r"\b" + re.escape(f) + r"\b", " " + row[0] + " ", text)
    typed = [w for w in _WORD.findall(text) if (w not in STOPWORDS or w.isdigit()) and (len(w) > 1 or w.isdigit())]
    seen = set()
    for w in typed:
        s = stem(w)
        if s in seen:
            continue
        seen.add(s)
        alts = [((s,), 1.0, "typed")]
        if s not in idx.vocab and not s.isdigit() and len(s) >= 4:
            close = [c for c in idx.by_first.get(s[:1], []) if abs(len(c) - len(s)) <= 2]
            for c in difflib.get_close_matches(s, close, n=2, cutoff=0.78):
                alts.append(((c,), FUZZY_WEIGHT, "fuzzy"))
                plan.notes.append(("fuzzy", w, c))
        extra = [a for a in syn.get((s,), [])]
        for a in extra:
            alts.append((a, SYNONYM_WEIGHT, "synonym"))
        if extra:
            plan.notes.append(("synonym", w, [" ".join(a) for a in extra[:3]]))
        plan.groups.append({"label": w, "alts": alts})
    for p in prefixes:
        lo = bisect.bisect_left(idx.sorted_vocab, p)
        hits = []
        while lo < len(idx.sorted_vocab) and idx.sorted_vocab[lo].startswith(p) and len(hits) < 60:
            hits.append(idx.sorted_vocab[lo])
            lo += 1
        if hits:
            plan.groups.append({"label": p + "*", "alts": [((h,), 1.0, "prefix") for h in hits]})
            plan.notes.append(("prefix", p + "*", len(hits)))
        else:
            plan.groups.append({"label": p + "*", "alts": [((stem(p),), 1.0, "typed")]})
    for ph in plan.phrases:
        # a phrase is also a requirement that its words are all present, which narrows the passages worth reading for it
        pass
    for g in plan.groups:
        for alts, _w, _k in g["alts"]:
            plan.match_stems.update(alts)
    for ph in plan.phrases:
        plan.match_stems.update(stems_of(ph))
    return plan


def _parse_scenario(plan, q, idx):
    """Reduce a long description to its key concepts: the words the library knows, weighted by how rare (informative) they are and by how
    often they were repeated, the strongest few kept, plus the pairs of words that were typed side by side ('land sales', 'development
    costs'). Nothing is required to match: documents are ranked by how much of the picture they cover."""
    text = normalize(q or "")
    syn = _synonym_table()
    for row in SYNONYMS:
        for form in row[1:]:
            f = normalize(form)
            if " " in f and re.search(r"\b" + re.escape(f) + r"\b", text):
                text = re.sub(r"\b" + re.escape(f) + r"\b", " " + row[0] + " ", text)
    raw = [w for w in _WORD.findall(text)]
    count, first = defaultdict(int), {}
    for i, w in enumerate(raw):
        if w in STOPWORDS or (len(w) < 2 and not w.isdigit()) or w.isdigit() and len(w) < 3:
            continue
        sw = stem(w)
        count[sw] += 1
        first.setdefault(sw, w)
    n = max(idx.n_chunks, 1)
    scored = []
    for sw, c in count.items():
        df = idx.vocab.get(sw)
        if df is None:
            if sw in syn:                     # a short form the library may only hold in full
                df = 1
            else:
                plan.dropped.append(first[sw])
                continue
        if df > 0.6 * n and n > 20:
            continue                          # in almost every passage: says nothing
        scored.append((_idf(max(df, 1), n) * (1 + 0.3 * (c - 1)), sw))
    scored.sort(reverse=True)
    for _w, sw in scored[:MAX_CONCEPTS]:
        alts = [((sw,), 1.0, "typed")]
        for a in syn.get((sw,), []):
            alts.append((a, SYNONYM_WEIGHT, "synonym"))
        plan.groups.append({"label": first[sw], "alts": alts})
        plan.concepts.append(first[sw])
        plan.match_stems.update(x for alts_, _w2, _k in alts for x in alts_)
    kept = {sw for _w, sw in scored[:MAX_CONCEPTS]}
    prev = None
    for w in raw:
        if w in STOPWORDS or (len(w) < 2 and not w.isdigit()):
            prev = None
            continue
        sw = stem(w)
        if prev is not None and prev != sw and prev in kept and sw in kept and (prev, sw) not in plan.bigrams:
            plan.bigrams.append((prev, sw))
        prev = sw
    return plan


# ---------------------------------------------------------------- scoring

def _idf(df, n):
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


def _chunk_scores_for(idx, stems_tuple):
    """{chunk position: bm25 contribution} for a word, or for several words that must all be in the same passage."""
    n = idx.n_chunks
    if len(stems_tuple) == 1:
        s = stems_tuple[0]
        pos, tfs = idx.post_pos.get(s), idx.post_tf.get(s)
        if not pos:
            return {}
        idf = _idf(len(pos), n)
        out = {}
        for p, tf in zip(pos, tfs):
            dl = idx.chunk_len[p]
            out[p] = idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * dl / idx.avg_chunk))
        return out
    sets = []
    for s in stems_tuple:
        pos, tfs = idx.post_pos.get(s), idx.post_tf.get(s)
        if not pos:
            return {}
        sets.append(dict(zip(pos, tfs)))
    common = set(sets[0])
    for d in sets[1:]:
        common &= set(d)
    if not common:
        return {}
    idf = sum(_idf(len(idx.post_pos[s]), n) for s in stems_tuple) / len(stems_tuple) * 1.3
    out = {}
    for p in common:
        tf = min(d[p] for d in sets)
        dl = idx.chunk_len[p]
        out[p] = idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * dl / idx.avg_chunk))
    return out


def _meta_scores_for(idx, stems_tuple):
    n = max(len(idx.meta_ids), 1)
    if len(stems_tuple) == 1:
        s = stems_tuple[0]
        pos, tfs = idx.meta_pos.get(s), idx.meta_tf.get(s)
        if not pos:
            return {}
        idf = _idf(len(pos), n)
        return {idx.meta_ids[p]: idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * idx.meta_len[p] / idx.avg_meta)) for p, tf in zip(pos, tfs)}
    sets = []
    for s in stems_tuple:
        pos, tfs = idx.meta_pos.get(s), idx.meta_tf.get(s)
        if not pos:
            return {}
        sets.append(dict(zip(pos, tfs)))
    common = set(sets[0])
    for d in sets[1:]:
        common &= set(d)
    return {idx.meta_ids[p]: 2.0 * (1 + math.log(min(d[p] for d in sets))) for p in common}


class Hit:
    def __init__(self, doc_id):
        self.doc_id = doc_id
        self.update = None
        self.score = 0.0
        self.coverage = 0.0
        self.partial = False
        self.passages = []      # [(chunk row, score)]
        self.in_front_matter = False
        self.in_title = False
        self.matched_labels = []
        self.missing_labels = []


class Result:
    def __init__(self, plan):
        self.plan = plan
        self.hits = []
        self.total = 0
        self.partial_from = None        # index in hits where partial matches start
        self.scenario = False
        self.facets = {}
        self.page = 1
        self.pages = 1


def _passes_filters(d, f):
    if f.get("types") and d["type"] not in f["types"]:
        return False
    if f.get("tax_head") and d["tax_head"] != f["tax_head"]:
        return False
    if f.get("area") and f["area"] not in d["areas"]:
        return False
    if f.get("reviewed") and not d["reviewed"]:
        return False
    if f.get("year") and f["year"] not in d["years"] and not (d["created"] and d["created"].year == f["year"] and not d["years"]):
        return False
    if f.get("date_from") or f.get("date_to"):
        when = d["effective"] or d["gazette"]
        if when is None:
            return False
        if f.get("date_from") and when < f["date_from"]:
            return False
        if f.get("date_to") and when > f["date_to"]:
            return False
    return True


def _score(idx, plan, filters=None, keep_type_facet=True):
    """Core: per-chunk and per-document scores for a plan. Returns (docs, chunk_scores) where docs maps update id -> Hit."""
    filters = filters or {}
    G = len(plan.groups)
    chunk_total = defaultdict(float)
    chunk_groups = defaultdict(set)
    doc_groups = defaultdict(set)
    meta_total = defaultdict(float)
    title_groups = defaultdict(set)
    for gi, g in enumerate(plan.groups):
        best_chunk, best_meta = {}, {}
        for stems_tuple, w, _kind in g["alts"]:
            for p, sc in _chunk_scores_for(idx, stems_tuple).items():
                sc *= w
                if sc > best_chunk.get(p, 0):
                    best_chunk[p] = sc
            for did, sc in _meta_scores_for(idx, stems_tuple).items():
                sc *= w
                if sc > best_meta.get(did, 0):
                    best_meta[did] = sc
        allowed = {s for alts, _w, _k in g["alts"] for s in alts}
        for p, sc in best_chunk.items():
            cid, uid, cidx, hstems, _h = idx.chunk_meta[p]
            if hstems & allowed:
                sc += 0.8 * _idf(max(idx.vocab.get(next(iter(hstems & allowed)), 1), 1), idx.n_chunks)
            chunk_total[p] += sc
            chunk_groups[p].add(gi)
            doc_groups[uid].add(gi)
        for did, sc in best_meta.items():
            meta_total[did] += sc
            doc_groups[did].add(gi)
            if idx.title_stems.get(did, set()) & allowed:
                title_groups[did].add(gi)
    # excluded words: any document holding one anywhere is out
    banned = set()
    for s in plan.excluded:
        for p in idx.post_pos.get(s, ()):
            banned.add(idx.chunk_meta[p][1])
        for p in idx.meta_pos.get(s, ()):
            banned.add(idx.meta_ids[p])
    # phrases: the passages (and front matter) that really hold them
    phrase_chunks = defaultdict(set)         # doc id -> chunk positions holding every phrase (when there are phrases)
    phrase_docs = None
    if plan.phrases:
        phrase_docs = None
        per_phrase_docs = []
        for ph in plan.phrases:
            pst = stems_of(ph, keep_stop=True)
            cand = None
            for s in set(pst):
                posset = set(idx.post_pos.get(s, ()))
                cand = posset if cand is None else cand & posset
            cand = cand or set()
            ids = [idx.chunk_meta[p][0] for p in cand]
            found_pos = set()
            docs_with = set()
            if ids:
                rows = {r.id: r.text for r in LegislativeUpdateChunk.query.filter(LegislativeUpdateChunk.id.in_(ids[:3000])).all()}
                pos_by_id = {idx.chunk_meta[p][0]: p for p in cand}
                for cid, text in rows.items():
                    if ph in " ".join(words(text)):
                        found_pos.add(pos_by_id[cid])
                        docs_with.add(idx.chunk_meta[pos_by_id[cid]][1])
            for u in LegislativeUpdate.query.filter(LegislativeUpdate.id.in_(list(idx.docs))).all() if not docs_with and len(idx.docs) <= 5000 and ph else ():
                front = " ".join(words(" ".join(t for t, _w in _meta_fields(u))))
                if ph in front:
                    docs_with.add(u.id)
            per_phrase_docs.append(docs_with)
            for p in found_pos:
                phrase_chunks[idx.chunk_meta[p][1]].add(p)
                chunk_total[p] = chunk_total.get(p, 0.0) + 6.0
        phrase_docs = set.intersection(*per_phrase_docs) if per_phrase_docs else set()
    # documents
    hits = {}
    candidate_ids = set(doc_groups) | (phrase_docs or set())
    for did in candidate_ids:
        if did in banned or did not in idx.docs:
            continue
        if phrase_docs is not None and did not in phrase_docs:
            continue
        cov = (len(doc_groups[did]) / G) if G else 1.0
        h = Hit(did)
        h.coverage = cov
        h.in_title = bool(title_groups.get(did))
        h.in_front_matter = did in meta_total
        positions = [p for p, uid in ((p, idx.chunk_meta[p][1]) for p in chunk_total if True) if uid == did] if False else None
        hits[did] = h
    by_doc = defaultdict(list)
    for p, sc in chunk_total.items():
        uid = idx.chunk_meta[p][1]
        if uid in hits:
            cg = len(chunk_groups.get(p, ())) / G if G else 1.0
            by_doc[uid].append((sc * (0.5 + 0.5 * cg), p, cg))
    for did, h in hits.items():
        passages = sorted(by_doc.get(did, []), reverse=True)
        top = [s for s, _p, _c in passages[:PER_DOC_PASSAGES + 1]]
        chunk_part = (top[0] + 0.25 * sum(top[1:4])) if top else 0.0
        ms = meta_total.get(did, 0.0)
        phrase_bonus = 4.0 if did in phrase_chunks else 0.0
        h.score = (chunk_part + 1.2 * ms + phrase_bonus) * (0.35 + 0.65 * h.coverage ** 2)
        h.passages = [(p, s, cg) for s, p, cg in passages[:PER_DOC_PASSAGES]]
        names = [g["label"] for gi, g in enumerate(plan.groups) if gi in doc_groups.get(did, ())]
        h.matched_labels = names
        h.missing_labels = [g["label"] for gi, g in enumerate(plan.groups) if gi not in doc_groups.get(did, ())]
    return hits


def search(query, filters=None, sort="relevance", page=1, per_page=PAGE_SIZE):
    """Run a deep search. `filters`: types (list of instrument types), tax_head, area, year (int), date_from/date_to (dates), reviewed (bool).
    Returns a Result with Hit objects (update, score, passages, ...) for the requested page."""
    idx = get_index()
    plan = parse_query(query, idx)
    res = Result(plan)
    filters = filters or {}
    if plan.empty or not idx.docs:
        return res
    hits = _score(idx, plan)
    G = len(plan.groups)
    base = {d: h for d, h in hits.items() if _passes_filters_ex(idx, d, filters, ignore_type=True)}
    res.facets = defaultdict(int)
    if plan.scenario:
        # a description: nothing is required, the documents covering most of the picture rise to the top
        _rerank_with_pairs(idx, plan, base)
        for h in base.values():
            h.partial = False
        floor = max(2, math.ceil(G * 0.25))
        show = [h for h in base.values() if len(h.matched_labels) >= floor]
        if len(show) < 5:
            show = sorted(base.values(), key=lambda h: -h.score)[:30]
        res.scenario = True
    else:
        # strict = every word somewhere in the document; for long queries allow one in four to be missing
        need = G if G <= 3 else math.ceil(G * 0.75)
        for h in base.values():
            h.partial = len(h.matched_labels) < need
        strict_all = [h for h in base.values() if not h.partial]
        partial_all = [h for h in base.values() if h.partial and len(h.matched_labels) >= (1 if G <= 2 else math.ceil(G * 0.5))]
        show = strict_all
        if len(strict_all) < STRICT_MIN_DOCS:
            show = strict_all + partial_all
    for h in show:
        res.facets[idx.docs[h.doc_id]["type"]] += 1
    show = [h for h in show if not filters.get("types") or idx.docs[h.doc_id]["type"] in filters["types"]]
    if sort == "newest":
        def when(h):
            d = idx.docs[h.doc_id]
            return d["effective"] or d["gazette"] or (d["created"].date() if d["created"] else date.min)
        show.sort(key=lambda h: (h.partial, -when(h).toordinal(), -h.score))
    else:
        show.sort(key=lambda h: (h.partial, -h.score))
    res.total = len(show)
    res.pages = max(1, math.ceil(len(show) / per_page))
    res.page = min(max(1, page), res.pages)
    chunk_page = show[(res.page - 1) * per_page: res.page * per_page]
    updates = {u.id: u for u in LegislativeUpdate.query.filter(LegislativeUpdate.id.in_([h.doc_id for h in chunk_page])).all()} if chunk_page else {}
    chunk_ids = [idx.chunk_meta[p][0] for h in chunk_page for p, _s, _c in h.passages]
    chunks = {c.id: c for c in LegislativeUpdateChunk.query.filter(LegislativeUpdateChunk.id.in_(chunk_ids)).all()} if chunk_ids else {}
    out = []
    res.scenario = plan.scenario
    for h in chunk_page:
        h.update = updates.get(h.doc_id)
        if h.update is None:
            continue
        h.passages = [(chunks[idx.chunk_meta[p][0]], s, cg) for p, s, cg in h.passages if idx.chunk_meta[p][0] in chunks]
        out.append(h)
    res.hits = out
    first_partial = next((i for i, h in enumerate(out) if h.partial), None)
    res.partial_from = first_partial
    return res


def _rerank_with_pairs(idx, plan, hits):
    """Second look at the best passages of a scenario search: a passage holding two of the typed words side by side ('land sales',
    'development costs') is worth more than one that merely contains both somewhere. Reads the text of at most 150 passages."""
    if not plan.bigrams:
        return
    cands = []
    for h in hits.values():
        for p, s_, cg in h.passages:
            cands.append((s_, h, p))
    cands.sort(key=lambda t: -t[0])
    cands = cands[:150]
    if not cands:
        return
    ids = {idx.chunk_meta[p][0]: (h, p) for _s, h, p in cands}
    pairs = set(plan.bigrams)
    for c in LegislativeUpdateChunk.query.filter(LegislativeUpdateChunk.id.in_(list(ids))).all():
        h, p = ids[c.id]
        st = stems_of(c.text)
        found = {(a, b) for a, b in zip(st, st[1:]) if (a, b) in pairs}
        if found:
            h.score += 0.9 * len(found)
            h.pair_hits = getattr(h, "pair_hits", 0) + len(found)


def _passes_filters_ex(idx, did, filters, ignore_type=False):
    f = dict(filters)
    if ignore_type:
        f.pop("types", None)
    return _passes_filters(idx.docs[did], f)


def rank_chunks(question, limit=12, per_doc=3, idx=None):
    """For "Ask the library": the best (update, chunk) pairs for a plain-English question, best first, with at most `per_doc` passages from
    one document so a long Act cannot crowd out everything else. Documents holding more of the question's words come first."""
    idx = idx or get_index()
    plan = parse_query(question, idx)
    if plan.empty or not idx.docs:
        return []
    hits = _score(idx, plan)
    if not hits:
        return []
    G = len(plan.groups)
    ranked = sorted(hits.values(), key=lambda h: -h.score)
    # keep the documents that hold most of the words; fall back to whatever matched
    best_cov = max(h.coverage for h in ranked)
    keep = [h for h in ranked if h.coverage >= min(best_cov, 0.5)] or ranked
    pairs = []
    positions = []
    for h in keep:
        if h.passages:
            for p, _s, _cg in h.passages[:per_doc]:
                positions.append((h.score, h.doc_id, p))
        else:
            positions.append((h.score, h.doc_id, None))
    positions.sort(key=lambda t: -t[0])
    positions = positions[:limit]
    updates = {u.id: u for u in LegislativeUpdate.query.filter(LegislativeUpdate.id.in_({d for _s, d, _p in positions})).all()}
    chunk_ids = [idx.chunk_meta[p][0] for _s, _d, p in positions if p is not None]
    chunks = {c.id: c for c in LegislativeUpdateChunk.query.filter(LegislativeUpdateChunk.id.in_(chunk_ids)).all()} if chunk_ids else {}
    for _s, did, p in positions:
        u = updates.get(did)
        if u is None:
            continue
        pairs.append((u, chunks.get(idx.chunk_meta[p][0]) if p is not None else None))
    return pairs


# ---------------------------------------------------------------- showing matches

def _match_words(plan):
    return plan.match_stems


def highlight(text, plan, limit=None):
    """`text` as safe HTML with the words (and stems) of the query wrapped in <mark>. `limit` cuts the text first."""
    stems = _match_words(plan)
    t = text if limit is None else text[:limit]
    out, last = [], 0
    for m in re.finditer(r"[A-Za-z0-9]+", t):
        w = m.group(0)
        if stem(normalize(w)) in stems and normalize(w) not in STOPWORDS:
            out.append(escape(t[last:m.start()]))
            out.append(Markup("<mark>") + escape(w) + Markup("</mark>"))
            last = m.end()
    out.append(escape(t[last:]))
    return Markup("").join(out)


def snippet(text, plan, width=SNIPPET_CHARS):
    """The densest ~`width` characters of `text` for the query, as safe highlighted HTML with ellipses."""
    text = " ".join((text or "").split())
    if not text:
        return Markup("")
    stems = _match_words(plan)
    spots = []
    for m in re.finditer(r"[A-Za-z0-9]+", text):
        s = stem(normalize(m.group(0)))
        if s in stems:
            spots.append((m.start(), s))
    if not spots:
        return highlight(text[:width] + ("..." if len(text) > width else ""), plan)
    best, best_i = -1, 0
    j = 0
    for i, (pos, _s) in enumerate(spots):
        while spots[j][0] < pos - 40 and j < i:
            j += 1
        # window starts a little before the first match; count distinct words inside it
        end = pos - 40 + width if False else spots[j][0] + width - 40
        inside = {s for p, s in spots[j:] if p <= end}
        if len(inside) > best or (len(inside) == best and False):
            best, best_i = len(inside), j
    start = max(0, spots[best_i][0] - 40)
    if start > 0:
        sp = text.rfind(" ", 0, start)
        start = sp + 1 if sp != -1 and start - sp < 25 else start
    end = min(len(text), start + width)
    if end < len(text):
        sp = text.rfind(" ", start, end)
        end = sp if sp > start + width * 0.6 else end
    piece = text[start:end]
    return Markup("&hellip; " if start > 0 else "") + highlight(piece, plan) + Markup(" &hellip;" if end < len(text) else "")


def describe(plan):
    """Short sentences telling the person how their query was read (typo corrections, vocabulary, wildcards, phrases, exclusions)."""
    out = []
    for kind, typed, found in plan.notes:
        if kind == "fuzzy":
            out.append(f"Also searched “{found}” for “{typed}”.")
        elif kind == "synonym":
            out.append(f"“{typed}” also searched as: {', '.join(found)}.")
        elif kind == "prefix":
            out.append(f"“{typed}” matched {found} word forms.")
    for ph in plan.phrases:
        out.append(f"The exact phrase “{ph}” must appear.")
    if plan.excluded:
        out.append("Left out anything containing: " + ", ".join(plan.excluded) + ".")
    return out


def reader_passages(update, plan):
    """For the full-text reader: every chunk of an update with the matching words marked, and which chunks match."""
    chunks = list(update.chunks) if update.chunks else []
    stems = _match_words(plan)
    out = []
    for c in chunks:
        hits = sum(1 for m in re.finditer(r"[A-Za-z0-9]+", c.text) if stem(normalize(m.group(0))) in stems)
        out.append({"chunk": c, "html": highlight(c.text, plan), "matches": hits})
    return out


# ---------------------------------------------------------------- a pasted brief: topics + the facts

_NUMBERED = re.compile(r"^\(?\s*(?:\d{1,2}|[a-hA-H]|[ivxIVX]{1,4})\s*[.)]\s+(.+)$")


def split_brief(text):
    """Split pasted text into (topics, facts). Topics are numbered or bulleted lines and short heading-like lines ('Value Added Tax (VAT)',
    'Land Sales and Property Development Tax'); the facts are the running sentences (who owns what, what happened). A line followed by a run of
    dots, or an ellipsis, ends there - what comes after it is read as the facts."""
    pieces = [p.strip() for p in re.split(r"[\r\n]+|\.{3,}|…", text or "") if p and p.strip()]
    topics, facts = [], []
    for piece in pieces:
        m = _NUMBERED.match(piece)
        body = (m.group(1) if m else piece).strip(" -•*\t")
        n_words = len(words(body))
        sentence_like = body.endswith((".", "?", "!")) or n_words > 14 or "," in body and n_words > 9
        if m and not sentence_like:
            topics.append(body)
        elif not sentence_like and 2 <= n_words <= 9 and not facts:
            topics.append(body)                                # a heading above the list
        else:
            facts.append(piece)
    seen, uniq = set(), []
    for t in topics:
        k = " ".join(stems_of(t))
        if k and k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq[:12], " ".join(facts).strip()


def is_brief(text):
    topics, facts = split_brief(text)
    return len(topics) >= 2 and is_scenario(text)


def search_brief(text, filters=None, per_topic=4):
    """A pasted brief searched topic by topic, and as a whole. Returns
    {"topics": [{"title", "result"}], "facts": str, "overall": Result (scenario search of everything), "concepts": [...]}.
    A topic is searched the ordinary way with the key words of the facts added only as tie-breakers, so a topic on its own page is not
    swamped by the story around it."""
    filters = filters or {}
    topics, facts = split_brief(text)
    out = {"topics": [], "facts": facts}
    for t in topics:
        res = search(t, filters=filters, per_page=per_topic)
        res.hits = res.hits[:per_topic]
        out["topics"].append({"title": t, "result": res})
    overall = search(text, filters=filters, per_page=PAGE_SIZE)
    out["overall"] = overall
    out["concepts"] = overall.plan.concepts
    return out
