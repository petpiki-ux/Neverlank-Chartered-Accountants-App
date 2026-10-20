"""Files a whole Act in the Legislative Updates library section by section,
and lets the rest of the app point at "section 16(1)(m)" or "Fourth Schedule,
paragraph 3" and show the Act's own words.

What loading an Act does (load_act):
  1. act_parser.parse_act() splits the text into sections and schedules.
  2. One LegislativeUpdate (type "Act") holds the full text. An older copy of
     the same Act already in the library (for example the ZimLII 2019 text) is
     REPLACED rather than duplicated, so Deep Search never reads two versions.
  3. One LegislativeProvision row per section / schedule (for direct links).
  4. One search chunk per section (long sections split at paragraph breaks),
     headed "Section 15 - Deductions allowed ...", so Deep Search, Ask the
     library and Draft advice cite section numbers.

Pure Python, no network. The bundled text of the Income Tax Act sits in
legislation_data/. A newer consolidation is loaded by replacing that text file
(or uploading a .txt on the Act page) - nothing else changes.
"""
import os
import re
from datetime import date, datetime

from extensions import db
from models import LegislativeUpdate, LegislativeUpdateChunk, LegislativeProvision
import act_parser

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legislation_data")

ACTS = {
    "ita": {
        "code": "ita",
        "title": "Income Tax Act [Chapter 23:06]",
        "short": "Income Tax Act",
        "source_reference": "Income Tax Act [Chapter 23:06]",
        "file": "ita_chapter_23_06.txt",
        "tax_head": "Corporate Income Tax (CIT)",
        "areas": ["Tax"],
        "consolidated_note": "Consolidated text, amended up to Act 13 of 2023 (Finance Act 2023) and SI 60 of 2024.",
        # how the Act is recognised if an older copy is already filed
        "match_titles": ["income tax act"],
        "exclude_titles": ["capital gains", "value added"],
    },
    "vat": {
        "code": "vat",
        "title": "Value Added Tax Act [Chapter 23:12]",
        "short": "VAT Act",
        "source_reference": "Value Added Tax Act [Chapter 23:12]",
        "file": "vat_chapter_23_12.txt",
        "tax_head": "Value Added Tax (VAT)",
        "areas": ["Tax"],
        "consolidated_note": "Consolidated text updated to 1st December 2024 (includes Finance Act 13 of 2023 and Act 2 of 2024), with the editor's notes and case references as printed.",
        "match_titles": ["value added tax act", "value-added tax act", "vat act"],
        "exclude_titles": ["income tax", "capital gains"],
        # the printed list of sections calls the First Schedule "Repealed"; the text re-inserted by Finance Act 13 of 2023 is Civil Penalty Orders
        "schedule_titles": {1: "Civil Penalty Orders (re-inserted by Finance Act 13 of 2023)"},
    },
    "cobe": {
        "code": "cobe",
        "title": "Companies and Other Business Entities Act [Chapter 24:31]",
        "short": "COBE Act",
        "source_reference": "Companies and Other Business Entities Act [Chapter 24:31]",
        "file": "cobe_chapter_24_31.txt",
        "tax_head": None,
        "areas": ["Companies & Corporate Law"],
        "consolidated_note": "Consolidated text as printed in the Veritas consolidation of the Companies and Other Business Entities Act (Acts 4/2019 and 8/2020; commenced 13 February 2020). Later amendments and Statutory Instruments must be confirmed against the Gazette.",
        "match_titles": ["companies and other business entities act", "cobe act"],
        "exclude_titles": [],
        "schedule_titles": {
            1: "Form of Memorandum of Association of a Company",
            2: "Form of statement in lieu of prospectus (private company ceasing to be private)",
            3: "Form of statement in lieu of prospectus (company not issuing a prospectus)",
            4: "Form of Annual Return of a Company",
            6: "Model Articles and By-laws",
            9: "Penalties for Late Submissions of Documents or Notices",
        },
    },
    "cgt": {
        "code": "cgt",
        "title": "Capital Gains Tax Act [Chapter 23:01]",
        "short": "Capital Gains Tax Act",
        "source_reference": "Capital Gains Tax Act [Chapter 23:01]",
        "file": "cgt_chapter_23_01.txt",
        "tax_head": "Capital Gains Tax (CGT)",
        "areas": ["Tax"],
        "consolidated_note": "Consolidated text updated to 1st December 2024 (amended up to Act 13 of 2023 and SI 211 of 2022), with the editor's notes and case references as printed. The Act sets no rate of tax: section 7 sends the rate and the calculation to the Finance Act [Chapter 23:04]. The four formulas (s11(2)(c), s18(1), s21(2)(b), s22(1)(b)) are printed in the Act as pictures and are written out here in words from the printed page. A section 32 is printed at the end of the body (the editor notes it as wrongly numbered; it repeats section 30A(1)); it is not in the printed list of sections, so it is added to the list here so that it can be opened on its own.",
        "match_titles": ["capital gains tax act", "capital gains tax"],
        "exclude_titles": ["income tax", "value added", "companies and other", "finance act", "rate of interest", "charging act"],
    },
}

CHUNK_TARGET = 4500      # characters; a long section is split at paragraph breaks near this size
CHUNK_MAX = 7000

_cache = {}


def invalidate():
    _cache.clear()


# ---------------------------------------------------------------- loading

def act_text(code):
    path = os.path.join(DATA_DIR, ACTS[code]["file"])
    with open(path, encoding="utf-8") as f:
        return f.read()


def _provision_heading(p):
    if p["kind"] == "section":
        return ("Section %s - %s" % (p["number"], p["title"].rstrip("."))).strip()[:290]
    extra = (" (%s)" % p["refers_to"]) if p.get("refers_to") else ""
    t = (p.get("title") or "").rstrip(".")
    if len(t) > 110:
        t = t[:110].rsplit(" ", 1)[0] + "..."
    return ("%s - %s%s" % (p["label"], t, extra)).strip()[:290]


def _split_text(text):
    """Split a long provision into pieces of roughly CHUNK_TARGET characters at
    line breaks, preferring a line that opens a subsection or paragraph."""
    if len(text) <= CHUNK_MAX:
        return [text]
    lines = text.split("\n")
    pieces, buf, size = [], [], 0
    opener = re.compile(r"^\s*(\(\d+[a-z]?\)|\([a-z]{1,2}\d?\)|\d{1,2}\s+[A-Z])")
    for ln in lines:
        if size >= CHUNK_TARGET and opener.match(ln):
            pieces.append("\n".join(buf).strip("\n"))
            buf, size = [], 0
        elif size >= CHUNK_MAX:
            pieces.append("\n".join(buf).strip("\n"))
            buf, size = [], 0
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        pieces.append("\n".join(buf).strip("\n"))
    return [p for p in pieces if p.strip()]


def _find_existing(cfg):
    q = LegislativeUpdate.query.filter(LegislativeUpdate.instrument_type == "Act")
    best = None
    for u in q.all():
        t = (u.title or "").lower()
        if any(x in t for x in cfg["exclude_titles"]):
            continue
        if any(m in t for m in cfg["match_titles"]):
            if u.source_reference == cfg["source_reference"]:
                return u
            best = best or u
    return best


def status(code="ita"):
    cfg = ACTS[code]
    n = LegislativeProvision.query.filter_by(act_code=code).count()
    upd = None
    if n:
        first = LegislativeProvision.query.filter_by(act_code=code).first()
        upd = first.legislative_update
    existing = None if upd else _find_existing(cfg)
    return {"cfg": cfg, "loaded": bool(n), "provisions": n, "act_update": upd, "existing_other_copy": existing,
            "sections": LegislativeProvision.query.filter_by(act_code=code, kind="section").count(),
            "schedules": LegislativeProvision.query.filter_by(act_code=code, kind="schedule").count()}


def load_act(code="ita", text=None, user_id=None, replace_existing=True):
    """Parse and file the Act. Returns a summary dict; raises ValueError if the
    text does not look like the Act. Commits."""
    cfg = ACTS[code]
    text = text if text is not None else act_text(code)
    parsed = act_parser.parse_act(text)
    if len(parsed["sections"]) < 20:
        raise ValueError("That text does not look like a full Act: only %d sections were found." % len(parsed["sections"]))

    prov = []
    for s in parsed["sections"]:
        prov.append({"kind": "section", "number": s["number"], "label": "Section %s" % s["number"], "title": s["title"],
                     "part": s["part"], "part_title": s["part_title"], "refers_to": None, "text": s["text"],
                     "repealed": s["repealed"]})
    for s in parsed["schedules"]:
        if s["number"] in cfg.get("schedule_titles", {}):
            s["title"] = cfg["schedule_titles"][s["number"]]
        prov.append({"kind": "schedule", "number": str(s["number"]), "label": s["label"], "title": s["title"],
                     "part": None, "part_title": None, "refers_to": s["refers_to"], "text": s["text"], "repealed": False})

    upd = None
    first = LegislativeProvision.query.filter_by(act_code=code).first()
    if first is not None:
        upd = first.legislative_update
    if upd is None and replace_existing:
        upd = _find_existing(cfg)
    created = upd is None
    if created:
        upd = LegislativeUpdate(instrument_type="Act", created_by_id=user_id)
        db.session.add(upd)
    else:
        # drop the old provisions and chunks - they are rebuilt below
        LegislativeProvision.query.filter_by(legislative_update_id=upd.id).delete()
        LegislativeUpdateChunk.query.filter_by(legislative_update_id=upd.id).delete()
        db.session.flush()
        db.session.expire_all()   # the rows just deleted are still in the session's memory; let go of them before new rows reuse their ids

    full = "\n".join(
        [cfg["title"], cfg["consolidated_note"], ""] +
        sum([[ _provision_heading(p), p["text"], ""] for p in prov], [])
    )
    upd.title = cfg["title"]
    upd.source_reference = cfg["source_reference"]
    upd.tax_head = upd.tax_head or cfg.get("tax_head")
    upd.set_areas(cfg["areas"])
    upd.summary = (cfg["consolidated_note"] + " Filed section by section: every section and schedule can be opened by its number, "
                   "and Deep Search cites them.")
    upd.extracted_text = full
    upd.extraction_status = "extracted"
    upd.ai_status = "skipped"
    upd.ai_error = "Full Acts aren't auto-summarised. The full text is filed section by section and searchable."
    upd.ai_processed_at = datetime.utcnow()
    db.session.flush()

    order = 0
    chunk_index = 0
    for p in prov:
        order += 1
        db.session.add(LegislativeProvision(
            legislative_update_id=upd.id, act_code=code, kind=p["kind"], number=p["number"], label=p["label"],
            title=(p["title"] or "")[:300], part=p["part"], part_title=(p["part_title"] or "")[:200] or None,
            refers_to=p["refers_to"], sort_order=order, text=p["text"], is_repealed=bool(p["repealed"]),
        ))
        heading = _provision_heading(p)
        pieces = _split_text(p["text"])
        for k, piece in enumerate(pieces):
            h = heading if len(pieces) == 1 else "%s (part %d of %d)" % (heading[:250], k + 1, len(pieces))
            body = h + "\n" + piece
            db.session.add(LegislativeUpdateChunk(legislative_update_id=upd.id, chunk_index=chunk_index, heading=h[:300], text=body))
            chunk_index += 1
    db.session.commit()
    invalidate()
    try:
        import legislation_index
        legislation_index.invalidate()
    except Exception:
        pass
    return {"update_id": upd.id, "created": created, "sections": len(parsed["sections"]), "schedules": len(parsed["schedules"]),
            "chunks": chunk_index, "warnings": parsed["warnings"]}


# ------------------------------------------------------------- references

_REF_SECTION = re.compile(r"^\s*(?:s(?:ection)?\.?\s*)?(\d{1,3}[A-Z]{0,3})\s*((?:\([A-Za-z0-9]{1,4}\))*)\s*$", re.I)
_REF_SCHED = re.compile(r"^\s*(?:sch(?:edule)?)\s*(\d{1,2})\s*(?:[,.:]?\s*(?:para(?:graph)?\.?\s*)?(\d{1,2}[A-Z]?)?\s*((?:\([A-Za-z0-9]{1,4}\))*))?\s*$", re.I)


def parse_ref(ref):
    """'16(1)(m)' -> ('section', '16', ['1', 'm']); 'sch4.3(1)' -> ('schedule', '4', ['3', '1']).
    Returns None if it cannot be read."""
    ref = (ref or "").strip()
    m = _REF_SCHED.match(ref)
    if m:
        path = []
        if m.group(2):
            path.append(m.group(2))
        path += re.findall(r"\(([A-Za-z0-9]{1,4})\)", m.group(3) or "")
        return ("schedule", m.group(1), path)
    m = _REF_SECTION.match(ref)
    if m:
        num = m.group(1).upper() if not re.fullmatch(r"\d+", m.group(1)) else m.group(1)
        return ("section", num, re.findall(r"\(([A-Za-z0-9]{1,4})\)", m.group(2) or ""))
    return None


def canonical(kind, number, path=None):
    path = path or []
    if kind == "schedule":
        out = "sch%s" % number
        if path:
            out += "." + path[0] + "".join("(%s)" % p for p in path[1:])
        return out
    return str(number) + "".join("(%s)" % p for p in (path or []))


from collections import namedtuple
Prov = namedtuple("Prov", "id label title text")


def get_provision(code, kind, number):
    """Plain (id, label, title, text) for a section / schedule, or None. Cached as plain values (never ORM rows,
    which belong to one request's session) and cleared whenever an Act is filed."""
    key = (code, kind, str(number))
    if key in _cache:
        return _cache[key]
    row = LegislativeProvision.query.filter_by(act_code=code, kind=kind, number=str(number)).first()
    val = Prov(row.id, row.label, row.title or "", row.text) if row is not None else None
    _cache[key] = val
    return val


# ---- paragraph extraction ----------------------------------------------------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
          "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20}
_LINE_LABEL = re.compile(r"^\s*\(([A-Za-z0-9]{1,5})\)(?=\s|[A-Za-z“\"(])")


def _letter_rank(lbl):
    """a..z -> 1..26, aa..zz -> 27..52 (the Act doubles letters after z); a trailing digit is a sub-number (d1)."""
    m = re.fullmatch(r"([a-z])(\1?)(\d?)", lbl)
    if not m:
        return None
    base = ord(m.group(1)) - 96 + (26 if m.group(2) else 0)
    return base, int(m.group(3)) if m.group(3) else 0


def _num_rank(lbl):
    m = re.fullmatch(r"(\d{1,3})([a-z]?)", lbl)
    if not m:
        return None
    return int(m.group(1)), (ord(m.group(2)) - 96 if m.group(2) else 0)


def _chain(lines, kind):
    """Walk `lines` and return [(label, line_index)] for labels that form the expected unbroken sequence at
    this level: numbers 1,2,3 (2a between); letters a,b,c (d1 between); roman i,ii,iii."""
    out = []
    last = None
    for i, ln in enumerate(lines):
        m = _LINE_LABEL.match(ln)
        if not m:
            continue
        lbl = m.group(1)
        if kind == "num":
            r = _num_rank(lbl)
            if r is None or lbl.isalpha():
                continue
            ok = (last is None and r[0] == 1 and r[1] == 0) or (last is not None and ((r[0] == last[0] + 1 and r[1] == 0) or (r[0] == last[0] and r[1] > last[1])))
        elif kind == "letter":
            r = _letter_rank(lbl)
            if r is None:
                continue
            ok = (last is None and r == (1, 0)) or (last is not None and ((r[0] == last[0] + 1 and r[1] == 0) or (r[0] == last[0] and r[1] > last[1]) or (last[0] == 34 and r == (36, 0))))
            if lbl == "ii":
                ok = False   # the Act skips (ii) among doubled letters (hh, jj): (ii) is always a roman sub-item
            if ok and lbl in ("i", "v", "x"):
                # could be a roman sub-item: "(i)" followed by "(ii)" before the next paragraph, "(v)" after "(iv)", "(x)" after "(ix)"
                prev = None
                for j in range(i - 1, -1, -1):
                    mm = _LINE_LABEL.match(lines[j])
                    if mm:
                        prev = mm.group(1)
                        break
                nxt = None
                for j in range(i + 1, min(i + 40, len(lines))):
                    mm = _LINE_LABEL.match(lines[j])
                    if mm:
                        nxt = mm.group(1)
                        break
                if (lbl == "i" and nxt == "ii") or (lbl == "v" and prev == "iv") or (lbl == "x" and prev == "ix"):
                    ok = False
        else:  # roman
            v = _ROMAN.get(lbl)
            if v is None:
                continue
            ok = (last is None and v == 1) or (last is not None and v == last + 1)
            r = v
        if ok:
            out.append((lbl, i))
            last = r
    return out


def _span(lines, label, kind):
    ch = _chain(lines, kind)
    for k, (lbl, i) in enumerate(ch):
        if lbl == label:
            end = ch[k + 1][1] if k + 1 < len(ch) else len(lines)
            return i, end
    return None


def extract_path(text, path, kind="section"):
    """Return the text of the sub-part named by `path` (['1','m'] = subsection (1), paragraph (m)); None if
    it cannot be located. For a schedule the first element is the paragraph number ('3')."""
    lines = (text or "").split("\n")
    start, end = 0, len(lines)
    levels = []
    path = list(path or [])
    if kind == "schedule" and path:
        # schedule paragraph "3 Deduction of allowance for wear and tear" starts a line with the number and a capital
        num = path.pop(0)
        idx = [i for i, ln in enumerate(lines) if re.match(r"^\s*%s\s+[A-Z]" % re.escape(num), ln)]
        if not idx:
            return None
        start = idx[0]
        nxt = [i for i, ln in enumerate(lines) if i > start and re.match(r"^\s*(\d{1,2})\s+[A-Z][a-z]", ln) and int(re.match(r"^\s*(\d{1,2})", ln).group(1)) == int(num) + 1]
        end = nxt[0] if nxt else len(lines)
        if not path:
            return "\n".join(lines[start:end]).strip("\n")
    sub = lines[start:end]
    for depth, label in enumerate(path):
        low = label.lower()
        if depth == 0 and re.fullmatch(r"\d{1,3}[a-z]?", low):
            kind_l = "num"
        elif re.fullmatch(r"[a-z]{1,2}\d?", low) and low not in ("ii", "iii", "iv", "vi", "vii", "viii", "ix", "xi", "xii") and not (depth >= 2 and low in _ROMAN):
            kind_l = "letter"
        elif low in _ROMAN:
            kind_l = "roman"
        else:
            kind_l = "letter"
        sp = _span(sub, low, kind_l)
        if sp is None:
            return None
        sub = sub[sp[0]:sp[1]]
    return "\n".join(sub).strip("\n")


def paragraph_text(ref, code="ita"):
    """The Act's own words for a reference such as '16(1)(m)'. Returns a dict (label, title, text, exact, url_ref)
    or None if the Act is not loaded / the reference is unknown. `exact` False means the section was found but
    the sub-part could not be isolated (the whole section is returned)."""
    pr = parse_ref(ref)
    if not pr:
        return None
    kind, number, path = pr
    row = get_provision(code, kind, number)
    if row is None:
        return None
    exact = True
    text = row.text
    if path:
        part = extract_path(row.text, path, kind)
        if part:
            text = part
        else:
            exact = False
    return {"label": row.label, "title": row.title, "text": text, "exact": exact, "ref": canonical(kind, number, path),
            "section_ref": canonical(kind, number), "kind": kind, "number": number, "path": path, "provision_id": row.id}


# ---------------------------------------------------------- template helper

def tidy(text, width=None):
    """Run a paragraph's lines together for a tooltip or a quote: undo the printer's hyphenation at line ends."""
    t = re.sub(r"-\n\s*", "", text or "")
    t = re.sub(r"\s*\n\s*", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    if width and len(t) > width:
        t = t[:width].rsplit(" ", 1)[0] + "..."
    return t


def ref_link(ref, text=None, code="ita", tip=True, cls="act-ref"):
    """{{ act_ref('16(1)(m)') }} -> a link to the Act with the paragraph's wording as a hover tip.
    Plain text (no link) while the Act is not filed, so pages never break."""
    from flask import url_for
    from markupsafe import Markup, escape
    ref = (ref or "").strip()
    pr = parse_ref(ref)
    label = text if text is not None else ("s" + ref if pr and pr[0] == "section" else ref)
    if not pr:
        return Markup(escape(label))
    try:
        found = paragraph_text(ref, code)
    except Exception:
        found = None
    if not found:
        return Markup(escape(label))
    href = url_for("tax.act_provision", code=code, ref=found["ref"])
    title = ""
    if tip:
        title = ' title="%s"' % escape("%s %s - %s" % (found["label"], found["title"].rstrip("."), tidy(found["text"], 380)))
    return Markup('<a class="%s" href="%s"%s>%s</a>' % (cls, href, title, escape(label)))


def words(ref, width=None, code="ita"):
    """The Act's wording for a reference as plain running text ('' if the Act is not filed or the reference is unknown)."""
    try:
        found = paragraph_text(ref, code)
    except Exception:
        return ""
    return tidy(found["text"], width) if found else ""
