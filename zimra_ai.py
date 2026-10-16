"""AI first draft for a reply to ZIMRA (ZIMRA Matters > letter > "Draft with AI").

What it does: given a draft reply, it sends Claude the case facts, the ZIMRA letter being answered, the case's
correspondence log, the drafter's own instructions and the best-matching passages of the firm's filed Legislative Update
Control library, and gets back the BODY of a letter plus the positions it took, the sources it relied on and the points
a person must check. The text lands in the draft; nothing is approved, sent or filed by it.

Safeguards, in the order they matter:

1. A person decides. The result is a draft inside the existing Draft -> Awaiting approval -> Approved -> Sent flow. The
   drafter has to tick that they checked the facts and citations before it can be submitted, and only a Partner or Admin
   approves. The previous text is kept so the draft can be thrown away.
2. Nothing invented. The model may only use the facts it is given and the legislation excerpts it is given. Anything else
   must be a ``[CONFIRM: ...]`` placeholder, and a letter with a placeholder cannot be approved or sent.
3. Checked in code, not just asked nicely. After the model answers, every money figure and every section / SI / Act it
   mentions is looked for in the material the model was given. Anything it cannot find is wrapped in a ``[CONFIRM: ...]``
   so it has to be looked at. Citations that point at an excerpt that was not supplied are dropped.
4. Privacy. The client's name, TIN, VAT number, e-mail addresses, phone numbers and the ZIMRA officer's name are replaced
   by tokens before anything leaves the app and restored afterwards. The model never sees them.
5. No position taken for the firm. If the drafter does not say what the client's position is, the model leaves it as a
   placeholder and lists the options in its notes - it does not decide to concede, dispute or promise payment.
"""
import json
import os
import re
from datetime import date, datetime

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 120
MAX_LIBRARY_CHUNKS = 8
MAX_LETTER_CHARS = 12000      # the ZIMRA letter text sent to the model
MAX_LOG_ITEMS = 15            # earlier correspondence lines sent
MAX_INSTRUCTION_CHARS = 4000
PRIMARY_TYPES = {"Act", "Notice", "SI"}

PLACEHOLDER_RE = re.compile(r"\[CONFIRM:[^\]]*\]")

COMPOSE_TOOL = {
    "name": "compose_zimra_reply",
    "description": "Return the first draft of the body of a letter from the firm to ZIMRA, with the points a reviewer must check.",
    "input_schema": {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "description": ("The body of the letter only: no date, addressee, salutation, subject line, 'Yours faithfully' or "
                                "signature. Paragraphs separated by a blank line; bullets start with '- '; numbered points start "
                                "with '1. '. Refer to the client as 'our client' or with the token {client_name}, and use "
                                "{client_tin} / {client_vat} for its numbers. Anything not given to you is a "
                                "[CONFIRM: what is needed] placeholder."),
            },
            "positions_taken": {
                "type": "array", "items": {"type": "string"},
                "description": "Each substantive position, request or statement of fact the letter commits the client to, one short line each (e.g. 'Asks for an extension to 30 June', 'Disputes finding 2: input tax claimed on ...').",
            },
            "citations": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "update_id": {"type": "integer", "description": "The [Update #N] id of the library excerpt relied on."},
                    "used_for": {"type": "string", "description": "What the letter relies on it for, in one line."}},
                    "required": ["update_id", "used_for"]},
                "description": "Every library excerpt the letter's legal statements rely on. Empty if the letter states no law.",
            },
            "missing_information": {
                "type": "array", "items": {"type": "string"},
                "description": "Facts, documents or decisions you needed but were not given (each should match a [CONFIRM] in the body).",
            },
            "reviewer_notes": {
                "type": "array", "items": {"type": "string"},
                "description": "What the reviewing Partner should check or decide: assumptions made, risks (an admission, a deadline, a right that could be lost), and options where the position was not given.",
            },
        },
        "required": ["body", "positions_taken", "citations", "missing_information", "reviewer_notes"],
    },
}

SYSTEM_PROMPT = (
    "You are drafting the BODY of a letter from Neverlank Chartered Accountants, a Zimbabwean firm of chartered "
    "accountants and tax practitioners, to the Zimbabwe Revenue Authority (ZIMRA), on behalf of a client. A Partner will "
    "review and approve it before it is sent, so your job is a careful, accurate first draft, not a finished letter.\n\n"
    "STRICT RULES\n"
    "1. FACTS: use only the facts in CASE FACTS, THE ZIMRA LETTER BEING ANSWERED, CORRESPONDENCE SO FAR and DRAFTER'S "
    "INSTRUCTIONS. Never invent or estimate an amount, date, reference number, name, document, enclosure, payment or event. "
    "If the letter needs something you were not given, write a specific placeholder in the form [CONFIRM: what is needed] "
    "and list it under missing_information.\n"
    "2. LAW: state law only from the LIBRARY EXCERPTS, which are the firm's own filed material. Mention an Act, section, "
    "statutory instrument or case only if it appears in an excerpt, and give a section number only if the excerpt shows "
    "it. Otherwise write [CONFIRM: legal basis - what is needed]. Acts, Notices and SIs are primary law. A Zimbabwean "
    "court case is binding authority; a foreign case is persuasive only; a Publication is commentary and a News Article "
    "is an unconfirmed report - never present those two as law or cite them to ZIMRA as authority. If no excerpts are "
    "supplied, state no law at all. List each excerpt you rely on under citations, using its [Update #N] id.\n"
    "3. POSITION: if the DRAFTER'S INSTRUCTIONS say what the client's position is, state it faithfully and no more "
    "strongly than instructed. If they do not, do NOT choose whether the client accepts, disputes or will pay - put "
    "[CONFIRM: the client's position on ...] and set out the realistic options in reviewer_notes. Make no admission of "
    "liability, no promise to pay and no commitment to a date unless the instructions or the facts give it. Where "
    "appropriate keep the client's rights open (for example the right to object), but do not state a time limit unless it "
    "is in the material.\n"
    "4. IDENTITY: the client's name, TIN and VAT number are replaced by the tokens {client_name}, {client_tin} and "
    "{client_vat} in the material. Use those tokens (or 'our client'); never guess a name or number.\n"
    "5. STYLE: formal, courteous, plain English, concise. Answer what ZIMRA actually asked, in the order it asked. Follow "
    "the structure of the TEMPLATE SKELETON, improving it with the facts, and keep a placeholder from it where the "
    "information is still missing. Write only the body: no date, addressee, salutation, subject, closing or signature.\n"
    "6. Return everything through the compose_zimra_reply tool."
)


# --------------------------------------------------------------------- setup

def is_configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=REQUEST_TIMEOUT)


def _call_model(system, user_message):
    """Returns (tool_input_dict, None) or (None, error message). The only place that talks to the API."""
    try:
        response = _client().messages.create(
            model=DEFAULT_MODEL, max_tokens=4000, temperature=0.2, system=system, tools=[COMPOSE_TOOL],
            tool_choice={"type": "tool", "name": COMPOSE_TOOL["name"]},
            messages=[{"role": "user", "content": user_message}])
    except Exception as exc:  # network, quota, bad key ...
        return None, str(exc)[:600]
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == COMPOSE_TOOL["name"]:
            return (block.input or {}), None
    return None, "The model did not return a structured draft - try again."


# ---------------------------------------------------------------- privacy

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+263|\(?0\)?)\s?\d{2,3}[\s\-]?\d{3}[\s\-]?\d{3,4}(?!\d)")


def _name_variants(name):
    name = (name or "").strip()
    if not name:
        return []
    variants = {name}
    base = re.sub(r"\(\s*(?:pvt|private)\s*\)\s*(?:ltd|limited)?|\b(?:pvt|private)\s+(?:ltd|limited)\b|\blimited\b|\bltd\b", "", name,
                  flags=re.I)
    base = re.sub(r"\s+", " ", base).strip(" ,.-")
    if len(base) >= 4:
        variants.add(base)
    return sorted(variants, key=len, reverse=True)


def redact(text, client, officer=None):
    """Swap identifiers for tokens before text leaves the app."""
    out = text or ""
    for name in _name_variants(client.name):
        out = re.sub(re.escape(name), "{client_name}", out, flags=re.I)
    for value, token in (((client.tin_number or "").strip(), "{client_tin}"), ((getattr(client, "vat_number", "") or "").strip(), "{client_vat}")):
        if value:
            out = re.sub(re.escape(value), token, out)
    if officer and officer.strip():
        out = re.sub(re.escape(officer.strip()), "the ZIMRA officer", out, flags=re.I)
    out = _EMAIL_RE.sub("[email removed]", out)
    out = _PHONE_RE.sub("[phone removed]", out)
    return out


def restore_tokens(text, client):
    """Put the real identifiers back; a token with no value becomes a visible placeholder."""
    tin = (client.tin_number or "").strip()
    vat = (getattr(client, "vat_number", "") or "").strip()
    out = text or ""
    out = out.replace("{client_name}", client.name or "our client")
    out = out.replace("{client_tin}", tin or "[CONFIRM: client's TIN / BP number]")
    out = out.replace("{client_vat}", vat or "[CONFIRM: client's VAT number]")
    out = re.sub(r"\{(client_[a-z_]+|zimra_[a-z_]+)\}", lambda m: f"[CONFIRM: {m.group(1).replace('_', ' ')}]", out)
    out = re.sub(r"\[(?:email|phone) removed\]", lambda m: "[CONFIRM: contact detail]", out)
    return out


# ------------------------------------------------------------------ inputs

def letter_text(item, files_dir):
    """Text of the uploaded ZIMRA letter (PDF, Word, Excel, PowerPoint), or ''."""
    if item is None or not item.stored_filename:
        return ""
    path = os.path.join(files_dir, item.stored_filename)
    ext = item.stored_filename.rsplit(".", 1)[-1].lower() if "." in item.stored_filename else ""
    if not os.path.exists(path):
        return ""
    try:
        import file_text_extraction
        text, status, _pages = file_text_extraction.extract_text_for_file(path, ext)
    except Exception:
        return ""
    return (text or "").strip() if status == "extracted" else ""


def _d(value):
    return value.strftime("%d %B %Y").lstrip("0") if value else "-"


def _money(value, cur):
    return f"{cur} {value:,.2f}" if value is not None else "-"


def case_facts(case):
    cur = case.currency or "USD"
    lines = [
        f"Case: {case.case_number} - {case.title}",
        f"Type: {case.case_type or '-'}; tax head: {case.tax_head or '-'}; period: {case.period_label or '-'}",
        f"Status: {case.status}; opened: {_d(case.opened_on)}",
        f"ZIMRA reference: {case.zimra_reference or '-'}",
        f"Tax assessed by ZIMRA: {_money(case.amount_assessed, cur)}",
        f"Penalties and interest raised: {_money(case.penalties_interest, cur)}",
        f"Amount agreed: {_money(case.amount_agreed, cur)}; paid to date: {_money(case.amount_paid, cur)}; balance: {_money(case.balance, cur)}",
    ]
    if case.plan_status or case.plan_notes:
        lines.append(f"Payment plan: {case.plan_status or '-'} {case.plan_notes or ''}".strip())
    for inst in case.instalments:
        lines.append(f"  Instalment due {_d(inst.due_date)}: {_money(inst.amount, cur)}" + (f", paid {_d(inst.paid_on)}" if inst.paid_on else ", unpaid"))
    if case.tcc_status or case.tcc_number:
        lines.append(f"Tax clearance: {case.tcc_status or '-'}; certificate no. {case.tcc_number or '-'}; expiry {_d(case.tcc_expiry)}")
    if case.description:
        lines.append(f"Background notes: {case.description}")
    if case.outcome:
        lines.append(f"Outcome notes: {case.outcome}")
    return "\n".join(lines)


def correspondence_log(case, exclude_id=None):
    rows = [c for c in case.correspondence if c.id != exclude_id][:MAX_LOG_ITEMS]
    out = []
    for c in sorted(rows, key=lambda c: (c.corr_date, c.id)):
        line = f"{_d(c.corr_date)} | {c.direction} | {c.kind or 'letter'} | {c.subject}"
        if c.reference:
            line += f" | ref {c.reference}"
        if c.notes:
            line += f" | {c.notes[:300]}"
        out.append(line)
    return "\n".join(out) or "(none logged)"


def library_query(case, incoming, letter, instructions):
    parts = [case.tax_head or "", case.case_type or "", case.title or "",
             incoming.subject if incoming else "", incoming.notes if incoming and incoming.notes else "",
             (letter or "")[:800], instructions or ""]
    return " ".join(p for p in parts if p)[:1800]


def library_excerpts(query):
    """[(update, chunk)] best matches in the firm's filed library; [] if nothing matches. Never raises."""
    try:
        import legislation_search
        return legislation_search.search_candidate_chunks(query, limit=MAX_LIBRARY_CHUNKS)
    except Exception:
        return []


def build_user_message(reply, instructions, letter, pairs, client, template_body):
    case, incoming = reply.case, reply.in_reply_to
    red = lambda t: redact(t, client, case.zimra_officer)
    parts = ["CASE FACTS\n" + red(case_facts(case))]
    if incoming is not None:
        head = (f"Subject: {incoming.subject}\nKind: {incoming.kind or '-'}; dated {_d(incoming.corr_date)}; ZIMRA ref {incoming.reference or '-'}; "
                f"reply due {_d(incoming.response_due)}")
        if incoming.notes:
            head += f"\nStaff notes on the letter: {incoming.notes}"
        body = (f"\n--- text of the letter ---\n{letter[:MAX_LETTER_CHARS]}" if letter else
                "\n(The text of the letter could not be read from the file; rely on the subject and notes above and "
                "use [CONFIRM] for anything the letter asked that you cannot see.)")
        parts.append("THE ZIMRA LETTER BEING ANSWERED\n" + red(head + body))
    else:
        parts.append("THE ZIMRA LETTER BEING ANSWERED\n(This letter is not a reply to a logged ZIMRA letter.)")
    parts.append("CORRESPONDENCE SO FAR\n" + red(correspondence_log(case, incoming.id if incoming else None)))
    parts.append("DRAFTER'S INSTRUCTIONS (the firm's position and what the letter must say)\n" +
                 (red(instructions) if instructions else "(none given - do not decide the client's position; use [CONFIRM] and list the options in reviewer_notes)"))
    if pairs:
        from legislation_search import _candidate_block
        parts.append("LIBRARY EXCERPTS (the firm's filed legislation and material - the only source of law you may use)\n\n" +
                     "\n\n---\n\n".join(_candidate_block(u, c) for u, c in pairs))
    else:
        parts.append("LIBRARY EXCERPTS\n(No filed material matched this matter. State no law; use [CONFIRM: legal basis ...] where law is needed.)")
    parts.append("TEMPLATE SKELETON (structure to follow; its [CONFIRM] parts are still missing information)\n" + red(template_body))
    parts.append(f"Today's date: {_d(date.today())}. Write the body of the letter now.")
    return "\n\n=====\n\n".join(parts)


def source_corpus(reply, instructions, letter, pairs):
    """Everything the model was allowed to rely on, unredacted, for checking its answer."""
    case, incoming = reply.case, reply.in_reply_to
    bits = [case_facts(case), correspondence_log(case), instructions or "", letter or ""]
    if incoming is not None:
        bits += [incoming.subject or "", incoming.notes or "", incoming.reference or ""]
    for u, c in pairs:
        bits += [u.title or "", u.summary or "", u.ai_summary or "", " ".join(u.ai_key_changes or []), u.source_reference or "",
                 u.case_citation or "", c.text if c is not None else (u.extracted_text or "")[:3000]]
    return "\n".join(bits)


# ----------------------------------------------------------------- guards

_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{3,}(?:\.\d+)?)(?![\w])")


def _norm_num(token):
    try:
        return f"{float(token.replace(',', '')):.2f}"
    except ValueError:
        return None


def numbers_in(text):
    return {n for n in (_norm_num(m.group(1)) for m in _NUM_RE.finditer(text or "")) if n}


def _apply_outside(text, fn):
    """Run fn on the parts of `text` that are not already inside a [CONFIRM: ...] placeholder."""
    parts = re.split(r"(\[CONFIRM:[^\]]*\])", text or "")
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


def flag_figures(body, corpus):
    """Wrap every figure of 3+ digits that is not in the supplied material in a [CONFIRM] so it must be looked at."""
    known = numbers_in(corpus)
    this_year = date.today().year
    known |= {f"{y:.2f}" for y in (this_year - 1, this_year, this_year + 1)}
    flagged, seen = [], set()

    def repl(match):
        norm = _norm_num(match.group(1))
        if norm is None or norm in known or norm in seen:
            return match.group(0)
        seen.add(norm)
        flagged.append(match.group(1))
        return f"{match.group(0)} [CONFIRM: verify {match.group(1)} - not found in the case file, the ZIMRA letter or your instructions]"

    return _apply_outside(body, lambda seg: _NUM_RE.sub(repl, seg)), flagged


_SECTION_RE = re.compile(r"\b(?:sections?|ss?\.|regulations?)\s+\d+[A-Za-z]?(?:\s?\([A-Za-z0-9]+\))*", re.I)
_SI_RE = re.compile(r"\b(?:S\.?I\.?|Statutory Instrument|General Notice|Government Notice)\s*(?:No\.?\s*)?\d+\s*(?:of|/)\s*\d{4}", re.I)
_ACT_RE = re.compile(r"\b(?:[A-Z][A-Za-z&]+\s+){1,5}Act\b")
_ACT_SKIP = {"the act", "this act", "that act"}


def _squash(text):
    return re.sub(r"[\s ]+", " ", (text or "").lower())


def flag_legal_refs(body, corpus):
    """Wrap any section / SI / named Act that the supplied material never mentions."""
    haystack = _squash(corpus)
    flagged, seen = [], set()

    def repl(match):
        ref = match.group(0).strip()
        key = _squash(ref)
        if key in seen or key in _ACT_SKIP or key in haystack:
            return match.group(0)
        # a section reference may be quoted with or without its subsection letters
        if key.rstrip(")").split("(")[0].strip() in haystack and "(" in key:
            return match.group(0)
        seen.add(key)
        flagged.append(ref)
        return f"{match.group(0)} [CONFIRM: verify {ref} - not in your filed library or the ZIMRA letter]"

    for rx in (_SECTION_RE, _SI_RE, _ACT_RE):
        body = _apply_outside(body, lambda seg, rx=rx: rx.sub(repl, seg))
    return body, flagged


_OPEN_RE = re.compile(r"^\s*(dear\b[^\n]*|to whom it may concern[^\n]*)\n+", re.I)
_CLOSE_RE = re.compile(r"(?:^|\n+)\s*(yours\s+(?:faithfully|sincerely|truly)|kind\s+regards|regards|sincerely)[\s\S]*$", re.I)


def strip_framing(body):
    """The app adds the salutation, closing and signature itself."""
    body = (body or "").replace("\r\n", "\n").strip()
    body = _OPEN_RE.sub("", body, count=1)
    m = _CLOSE_RE.search(body)
    if m and len(m.group(0)) <= 250:  # a short sign-off only; never cut away a long passage that merely starts with such a word
        body = body[:m.start()]
    return body.strip()


def clean_list(values, limit=12, width=400):
    out = []
    for v in values or []:
        text = re.sub(r"\s+", " ", str(v)).strip()
        if text:
            out.append(text[:width])
    return out[:limit]


def build_sources(citations, pairs):
    """Citations the model gave, kept only if they point at an excerpt it was actually shown."""
    shown = {}
    for u, _c in pairs:
        shown.setdefault(u.id, u)
    cited, dropped = {}, []
    for c in citations or []:
        try:
            uid = int(c.get("update_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if uid in shown:
            cited.setdefault(uid, str(c.get("used_for") or "").strip()[:300])
        else:
            dropped.append(uid)
    sources = []
    for uid, used in cited.items():
        u = shown[uid]
        sources.append({"id": uid, "title": u.title, "type": u.type_label, "ref": u.source_reference or "",
                        "used_for": used, "primary": (u.instrument_type in PRIMARY_TYPES)})
    considered = [{"id": u.id, "title": u.title, "type": u.type_label} for u in shown.values()]
    return sources, considered, dropped


# --------------------------------------------------------------- the entry

def generate(reply, instructions, user, files_dir):
    """Draft the body of `reply`. Returns (status, payload):
      "not_configured" - no ANTHROPIC_API_KEY (payload None)
      "error"          - payload is a message
      "done"           - payload {"body": str, "meta": dict}
    Changes nothing in the database; the caller applies the result."""
    if not is_configured():
        return "not_configured", None
    import zimra_letters as ZL
    case, incoming, client = reply.case, reply.in_reply_to, reply.case.client
    instructions = (instructions or "").strip()[:MAX_INSTRUCTION_CHARS]
    letter = letter_text(incoming, files_dir)
    pairs = library_excerpts(library_query(case, incoming, letter, instructions))
    template = ZL.TEMPLATE_BY_KEY.get(reply.template_key) or ZL.TEMPLATE_BY_KEY["general"]
    skeleton = template["body"].replace("{", "<").replace("}", ">")
    message = build_user_message(reply, instructions, letter, pairs, client, skeleton)
    data, error = _call_model(SYSTEM_PROMPT, message)
    if error:
        return "error", error
    raw = strip_framing(data.get("body") or "")
    if not raw:
        return "error", "The model returned an empty letter - try again, with fuller instructions."

    body = restore_tokens(raw, client)
    corpus = source_corpus(reply, instructions, letter, pairs)
    body, bad_figures = flag_figures(body, corpus)
    body, bad_refs = flag_legal_refs(body, corpus)
    sources, considered, dropped = build_sources(data.get("citations"), pairs)
    flags = [f"Figure {f} was not found in the case file, the ZIMRA letter or your instructions" for f in bad_figures]
    flags += [f"Legal reference \"{r}\" is not in your filed library or the ZIMRA letter" for r in bad_refs]
    flags += [f"The model cited library item #{i}, which it was not shown; the citation was dropped" for i in dropped]
    if not pairs:
        flags.append("No filed legislation matched this matter, so the draft states no law. Add the relevant Act or SI to Legislative Update Control and draft again if law is needed")
    for s in sources:
        if not s["primary"]:
            flags.append(f"\"{s['title']}\" is a {s['type']} - commentary or an unconfirmed report, not law; check before relying on it")
    meta = {
        "model": DEFAULT_MODEL, "at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "by": getattr(user, "name", None) or "-",
        "instructions": instructions, "letter_read": bool(letter),
        "positions": clean_list(data.get("positions_taken")), "sources": sources, "considered": considered,
        "missing": clean_list(data.get("missing_information")), "notes": clean_list(data.get("reviewer_notes")),
        "flags": flags, "checked_by": None, "checked_at": None,
    }
    return "done", {"body": body, "meta": meta}
