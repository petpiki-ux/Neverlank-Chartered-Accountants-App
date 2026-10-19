"""Splits the text of a consolidated Zimbabwean Act (built for the Income Tax
Act [Chapter 23:06], but written generally) into its sections and schedules,
so each can be filed, searched and cited by number ("section 15(2)(c)").

Pure Python, no dependencies - same rule as the rest of the legislative
library (runs on Render and in the packaged .exe).

How it works
- The "ARRANGEMENT OF SECTIONS" list at the front is the authority for which
  sections exist, their titles and the Part each belongs to.
- The body is then walked in order. A section starts on the line that begins
  with its number followed by (the start of) its title. Spacing in the source
  is irregular, so the title is compared loosely.
- Schedules start on a line in capitals: "FOURTH SCHEDULE (Section 15(2)(c))".
- Anything that cannot be placed is reported in `warnings`, never guessed.
"""
import re

_ORD = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
    "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11,
    "TWELFTH": 12, "THIRTEENTH": 13, "FOURTEENTH": 14, "FIFTEENTH": 15,
    "SIXTEENTH": 16, "SEVENTEENTH": 17, "EIGHTEENTH": 18, "NINETEENTH": 19,
    "TWENTIETH": 20, "THIRTIETH": 30,
}
_UNITS = ["", "FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH"]


def _schedule_number(word):
    """'TWENTY-FIRST' -> 21, 'FOURTH' -> 4, 'THIRTY-SIXTH' -> 36 (None if not a number)."""
    w = word.upper().replace(" ", "-")
    if w in _ORD:
        return _ORD[w]
    m = re.match(r"^(TWENTY|THIRTY)-([A-Z]+)$", w)
    if m and m.group(2) in _UNITS[1:]:
        return (20 if m.group(1) == "TWENTY" else 30) + _UNITS.index(m.group(2))
    return None


_SCHED_RE = re.compile(r"^\s*([A-Z][A-Z\- ]{3,22}?)\s+SCHEDULE\b(.*)$")
_TOC_RE = re.compile(r"^\s*(\d{1,3}[A-Z]{0,2})(?:\.\s*(?=[A-Z\[\u2026])|\.\s+|\s+(?=[A-Z\[\u2026]))(\S.*)$")
_SUBPART_RE = re.compile(r"^\s*SUB-?\s?PART\s+([A-Z]{1,2})\s*:\s*(.*)$")
_PAGE_RE = re.compile(r"^\s*Page\s+\d+\s+of\s+\d+\s*$", re.I)
_CHAPTER_RE = re.compile(r"^\s*CHAPTER\s+([IVXLCDM]+)\s*$")
# a printed heading that divides the Act (never part of the section above it)
_HEAD_RE = re.compile(r"^\s*(?:CHAPTER\s+[IVXLCDM]+\s*$|PART\s+[IVXLCDM]+[A-Z]*\s*$|SUBPART\s+[A-Z]\b)")
_NOISE_RE = re.compile(r"DISTRIBUTED BY VERITAS|veritas@mango|veritaszim\.net|Veritas makes every effort|cannot take legal responsibility", re.I)
# "PROVISIONS COMMON TO COMPANIES AND PBCs PART I": a Part number printed at the end of a Chapter's title line
_TITLE_PART_RE = re.compile(r"^\s*([A-Z][A-Z ,\-&']{8,}?)\s+PART\s+([IVXLCDM]+[A-Z]*)\s*$")


def _is_repealed_title(t):
    t = (t or "").strip()
    return t.startswith("[Repealed") or t.startswith("\u2026") or t.startswith("...") or t.lower().startswith("repealed")
_PART_RE = re.compile(r"^\s*PART\s+([IVXLCDM]+[A-Z]*)\s*$")
_BODY_START_RE = re.compile(r"AN ACT to provide|^\s*ENACTED by the (?:President|Parliament)")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _clean_lines(text):
    """Drop form-feed characters and bare page-number lines."""
    out = []
    for ln in (text or "").replace("\x0c", "\n").split("\n"):
        if re.match(r"^\s*\d{1,3}\s*$", ln) or _PAGE_RE.match(ln) or _NOISE_RE.search(ln):
            continue
        out.append(ln.rstrip())
    return out


def _parse_toc(lines):
    """Returns (entries, body_start_index). entries = [{number,title,part,part_title}]."""
    body_start = None
    for i, ln in enumerate(lines):
        if _BODY_START_RE.search(ln):
            body_start = i
            break
    if body_start is None:
        return [], None
    toc_start = 0
    for i, ln in enumerate(lines[:body_start]):
        if "ARRANGEMENT OF SECTIONS" in ln.upper():
            toc_start = i
            break
    entries = []
    part = None
    part_title = None
    chapter = None
    part_base = None
    part_title_base = None
    i = toc_start + 1
    while i < body_start:
        ln = lines[i]
        if re.match(r"^\s*SCHEDULES\s*$", ln):
            break
        mc = _CHAPTER_RE.match(ln)
        if mc:
            chapter = "Chapter " + mc.group(1)
            part, part_title = chapter, None
            part_base, part_title_base = chapter, None
            j = i + 1
            while j < body_start and not lines[j].strip():
                j += 1
            nxt = lines[j] if j < body_start else ""
            mtp = _TITLE_PART_RE.match(nxt)
            if mtp:   # the chapter title and its first Part share a line
                part = "%s, Part %s" % (chapter, mtp.group(2))
                part_base = part
                k = j + 1
                while k < body_start and not lines[k].strip():
                    k += 1
                part_title = lines[k].strip().title() if k < body_start else ""
                part_title_base = part_title
                i = k + 1
            else:
                part_title = nxt.strip().title()
                i = j + 1
            continue
        mp = _PART_RE.match(ln)
        if mp:
            part = ("%s, Part %s" % (chapter, mp.group(1))) if chapter else ("Part " + mp.group(1))
            part_base = part
            j = i + 1
            while j < body_start and not lines[j].strip():
                j += 1
            part_title = lines[j].strip().title() if j < body_start else ""
            part_title_base = part_title
            i = j + 1
            continue
        msp = _SUBPART_RE.match(ln)
        if msp and part:
            # "SUB-PART A: INCORPORATION OF COMPANIES ..." (may run over a second line): a division inside a Part
            t = msp.group(2).strip()
            j = i + 1
            while j < body_start and lines[j].strip() and not _TOC_RE.match(lines[j]) and lines[j].strip() == lines[j].strip().upper() and not _HEAD_RE.match(lines[j]):
                t += " " + lines[j].strip()
                j += 1
            part = "%s, Sub-part %s" % (part_base, msp.group(1))
            part_title = "%s: %s" % (part_title_base, t.title())
            i = j
            continue
        mt = _TOC_RE.match(ln)
        if mt:
            title = mt.group(2).strip()
            j = i + 1
            # continuation lines: indented, not a new entry / Part
            while j < body_start and lines[j].strip() and not _TOC_RE.match(lines[j]) and not _PART_RE.match(lines[j]) and not re.match(r"^\s*SCHEDULES", lines[j]) and not re.match(r"^\s*[A-Za-z\- ]{3,24}\s+schedule\s*:", lines[j], re.I):
                title = (title.rstrip("-") + " " + lines[j].strip()) if not title.endswith("-") else (title[:-1] + lines[j].strip())
                j += 1
            entries.append({"number": mt.group(1), "title": title, "part": part, "part_title": part_title})
            i = j
            continue
        i += 1
    return entries, body_start


def _schedule_titles(toc_lines):
    """{number: title} from the "SCHEDULES" part of the arrangement list ("Fourth Schedule: Deductions to be allowed ...")."""
    out = {}
    start = None
    for i, ln in enumerate(toc_lines):
        if re.match(r"^\s*SCHEDULES\s*$", ln):
            start = i
    if start is None:
        # no "SCHEDULES" heading: begin at the first "FIRST SCHEDULE: ..." line
        for i, ln in enumerate(toc_lines):
            if re.match(r"^\s*[A-Za-z][A-Za-z\- ]{2,24}?\s+schedule\s*:", ln, re.I):
                start = i - 1
                break
    if start is None:
        return out
    cur = None
    for ln in toc_lines[start + 1:]:
        if re.match(r"^\s*ACT\s*$", ln):
            break
        m = re.match(r"^\s*([A-Za-z][A-Za-z\- ]{2,24}?)\s+schedule\s*:\s*(.*)$", ln, re.I)
        if m:
            num = _schedule_number(m.group(1).strip())
            if num is not None:
                cur = num
                out[cur] = m.group(2).strip()
                continue
        if cur is not None and ln.strip():
            prev = out[cur]
            out[cur] = (prev[:-1] + ln.strip()) if prev.endswith("-") else (prev + " " + ln.strip())
    return {k: re.sub(r"\s+", " ", v).strip() for k, v in out.items()}


def _tidy_caps_title(text):
    """A schedule heading printed in capitals over one or two lines -> sentence case."""
    parts = []
    for ln in (text or "").split("\n")[:4]:
        if not ln.strip():
            if parts:
                break
            continue
        if ln.strip() != ln.strip().upper():
            break
        parts.append(ln.strip())
    t = " ".join(parts)
    t = re.sub(r"-\s+", "", t)
    return t.capitalize() if t else ""


def parse_act(text):
    """Returns {sections:[...], schedules:[...], front:str, warnings:[...]}.
    Each section: {order, number, title, part, part_title, text, repealed}.
    Each schedule: {order, number, label, title, refers_to, text}."""
    lines = _clean_lines(text)
    entries, body_start = _parse_toc(lines)
    warnings = []
    if body_start is None or not entries:
        return {"sections": [], "schedules": [], "front": text or "", "warnings": ["Could not find the list of sections or the start of the Act."]}

    # --- schedules: find their headings first so the last section stops there
    sched_idx = []
    for i in range(body_start, len(lines)):
        m = _SCHED_RE.match(lines[i])
        if not m:
            continue
        num = _schedule_number(m.group(1).strip())
        if num is None or lines[i].strip() != lines[i].strip().upper().replace("(SECTION", "(Section"):
            # require the heading to be in capitals apart from the bracketed reference
            head = lines[i].split("(")[0]
            if num is None or head != head.upper():
                continue
        head_only = lines[i].split("(")[0]
        if head_only != head_only.upper():
            continue
        # a real heading is followed within a few lines by text, and is short
        if len(lines[i].strip()) > 120:
            continue
        sched_idx.append((i, num, m.group(1).strip().title(), m.group(2).strip()))
    # keep the first heading seen for each schedule number, in order
    seen = set()
    sched_heads = []
    for i, num, word, rest in sched_idx:
        if num in seen:
            continue
        seen.add(num)
        sched_heads.append((i, num, word, rest))
    first_sched_line = sched_heads[0][0] if sched_heads else len(lines)

    # --- sections: walk the body in order
    sections = []
    pos = body_start + 1
    found = []   # (entry, line_index)
    used = set()
    for ent in entries:
        num = ent["number"]
        want = _norm(ent["title"])[:10]
        repealed = _is_repealed_title(ent["title"])
        pat = re.compile(r"^\s{0,10}" + re.escape(num) + r"(?![0-9A-Za-z])\s*(.*)$")
        hit = None
        for start in (pos, body_start + 1):
            for i in range(start, first_sched_line):
                if i in used:
                    continue
                m = pat.match(lines[i])
                if not m:
                    continue
                rest = m.group(1)
                if repealed:
                    ok = (not rest.strip()) or "repeal" in rest.lower() or rest.strip() in ("…", "...")
                else:
                    ok = bool(rest.strip()) and _norm(rest)[:7] == want[:7] and not re.match(r"^\s*[a-z]\)", rest)
                if ok:
                    hit = i
                    break
            if hit is not None:
                break
        body_number = num
        if hit is None and not repealed:
            # The list of sections and the body sometimes number the same
            # section differently (the list says 80B, the body 80BB). Find it by title.
            loose = re.compile(r"^\s{0,10}(\d{1,3}[A-Z]{0,3})\s+(\S.*)$")
            for i in range(body_start + 1, first_sched_line):
                if i in used:
                    continue
                m = loose.match(lines[i])
                if m and _norm(m.group(2))[:24] == _norm(ent["title"])[:24] and m.group(1).startswith(num[:2]):
                    hit = i
                    body_number = m.group(1)
                    warnings.append("Section %s (%s) is numbered %s in the body of the Act; filed as %s." % (num, ent["title"][:40], body_number, body_number))
                    break
        if hit is None and not repealed:
            # The list and the body word a title differently ("Shell companies and shelf companies" / "Shell and shelf companies"):
            # same number, same first word, and nothing between the number and the title but spaces.
            first_word = re.sub(r"[^a-z]", "", (ent["title"].split() or [""])[0].lower())
            if first_word:
                for i in range(pos, first_sched_line):
                    if i in used:
                        continue
                    m = pat.match(lines[i])
                    if m and m.group(1).strip() and re.sub(r"[^a-z]", "", m.group(1).split()[0].lower()) == first_word and not re.match(r"^\s*[a-z]\)", m.group(1)):
                        hit = i
                        warnings.append("Section %s is headed '%s' in the list and '%s' in the body." % (num, ent["title"][:40], m.group(1).strip()[:40]))
                        break
        if hit is None:
            warnings.append("Section %s (%s) was not found in the body." % (num, ent["title"][:50]))
            continue
        used.add(hit)
        pos = hit + 1
        ent = dict(ent, number=body_number)
        found.append((ent, hit))

    # order by position in body (the list of sections is not always in body order)
    found.sort(key=lambda t: t[1])
    for k, (ent, idx) in enumerate(found):
        end = found[k + 1][1] if k + 1 < len(found) else first_sched_line
        body = lines[idx + 1:end]
        # strip a trailing Part heading that belongs to the next section
        while body and not body[-1].strip():
            body.pop()
        while True:
            j = len(body)
            for back in range(1, min(9, len(body)) + 1):
                if _HEAD_RE.match(body[-back]):
                    j = len(body) - back
                    break
            if j == len(body):
                break
            body = body[:j]
            while body and not body[-1].strip():
                body.pop()
        txt = "\n".join(body).strip("\n")
        sections.append({
            "order": k + 1, "number": ent["number"], "title": ent["title"],
            "part": ent["part"], "part_title": ent["part_title"],
            "text": txt, "repealed": _is_repealed_title(ent["title"]),
        })

    schedules = []
    for k, (i, num, word, rest) in enumerate(sched_heads):
        end = sched_heads[k + 1][0] if k + 1 < len(sched_heads) else len(lines)
        body = lines[i + 1:end]
        title = ""
        for ln in body[:6]:
            if ln.strip():
                title = ln.strip()
                break
        refers = rest.strip()
        if not refers:
            for ln in body[:8]:
                mr = re.match(r"^\s*(\(\s*Sections?\b[^)]*\))\s*$", ln)
                if mr:
                    refers = mr.group(1)
                    break
        if refers.startswith("(") and refers.endswith(")"):
            refers = refers[1:-1].strip()
        elif refers.startswith("("):
            refers = refers[1:].strip()
        else:
            refers = ""
        schedules.append({
            "order": k + 1, "number": num, "label": "%s Schedule" % word.title(),
            "title": title[:200], "refers_to": refers[:120],
            "text": "\n".join(body).strip("\n"),
        })

    toc_titles = _schedule_titles(lines[:body_start])
    for sc in schedules:
        if sc["number"] in toc_titles:
            sc["title"] = toc_titles[sc["number"]][:300]
        else:
            sc["title"] = _tidy_caps_title(sc["text"]) or sc["title"]
    front = "\n".join(lines[:body_start + 1])
    return {"sections": sections, "schedules": schedules, "front": front, "warnings": warnings}
