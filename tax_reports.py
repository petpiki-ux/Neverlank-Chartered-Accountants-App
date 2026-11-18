"""AI Report Writer for a Tax Advisory engagement's Finalisation tab.

Four kinds of written report - a tax advisory opinion / memo, an advisory report to the client (letter), a tax position and risk review,
and a submission to ZIMRA - drafted by Claude from the engagement's own record and the firm's Firm Library (Legislative Update Control),
then edited section by section by a person, marked reviewed by a Partner and, for a letter, signed by the Managing Partner.

What the AI is given (and nothing else):
  * the partner's own instructions - the facts, the question and the position to take;
  * the engagement's Technical Research Log, tax positions, structuring options, disputes, return records, penalty runs and income tax
    computation, and (when a trial balance exists) the headline figures of the accounts;
  * the text of the uploaded engagement documents the person ticked;
  * the linked ZIMRA matter, for a ZIMRA submission;
  * the best-matching passages of the Firm Library (the filed Acts, Statutory Instruments, cases and articles), each with its legal weight.

Safeguards (the same as the ZIMRA letter drafter, whose checks this reuses):
  1. A person decides. The result is a draft in an editor; "Reviewed" is a person's tick, and a report with a [CONFIRM: ...] left in
     it cannot be signed.
  2. Nothing invented. Facts only from the material above; law only from the Library excerpts, cited as [#id] and checked against what
     was actually supplied. Any figure, section, SI or Act the material does not contain is wrapped in a [CONFIRM: ...].
  3. Privacy. The client's name, TIN, VAT number, e-mails and phone numbers are swapped for tokens before anything leaves the app.
  4. Without an API key, or when the AI fails, a structured skeleton with [CONFIRM] placeholders is made instead and the page says so.
"""
import io
import json
import os
import re
from collections import OrderedDict
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

import brand_docs as brand
import zimra_ai
from extensions import db
from models import (Document, Engagement, IncomeTaxComputation, PenaltyInterestCalculation, TaxAdvisoryReport, TaxDispute,
                    TaxPosition, TaxResearchLogEntry, TaxReturnRecord, TaxStructuringOption, TrialBalance, ZimraCase)

taxreports_bp = Blueprint("taxreports", __name__, url_prefix="/tax-reports")

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 150
MAX_PAIRS = 14
MAX_DOC_CHARS = 9000          # per uploaded document
MAX_DOCS_CHARS = 26000        # all uploaded documents together
MAX_SECTIONS = 14

# ------------------------------------------------------------------ the four kinds

KINDS = OrderedDict([
    ("opinion", {
        "label": "Tax advisory opinion / memo", "short": "Opinion", "letter": False,
        "title": "Tax Advisory Opinion",
        "sections": [
            ("Issue", "The question the client needs answered, in one or two sentences."),
            ("Facts relied on", "The facts this opinion rests on, taken only from the record and the partner's instructions."),
            ("Law and authorities", "The provisions, instruments and cases that bear on the issue, from the Firm Library, with their legal weight."),
            ("Analysis", "How the law applies to these facts, point by point, including the counter-arguments ZIMRA could raise."),
            ("Conclusion", "The answer, and how confident the firm is in it."),
            ("Recommendations and next steps", "What the client should do, in order of priority."),
            ("Assumptions and limitations", "What was assumed, what was not checked, and what would change the opinion."),
        ],
        "guide": ("An internal-quality tax opinion for the client file, written the way a partner would sign off: issue, facts, law, analysis, "
                  "conclusion, recommendations. Reason from the facts to the conclusion; do not just list the law."),
    }),
    ("client_report", {
        "label": "Advisory report to the client (letter)", "short": "Client report", "letter": True,
        "title": "Advisory Report",
        "sections": [
            ("Executive summary", "The answer first: what we found and what we recommend, in a short paragraph."),
            ("Background and scope", "What we were asked to do and the facts we worked from."),
            ("Our findings", "What the law means for the client, in plain language, with the sources."),
            ("Risks and exposure", "What could go wrong, how likely it is, and what it could cost."),
            ("Recommendations", "What we advise the client to do."),
            ("Next steps", "Who does what, and by when."),
        ],
        "guide": ("A letter-report to the client's directors, who are not tax specialists. Plain English, short paragraphs, the conclusion "
                  "first, no jargon without a one-line explanation. Write the body only: no date, addressee, salutation or sign-off."),
    }),
    ("risk_review", {
        "label": "Tax position and risk review", "short": "Risk review", "letter": False,
        "title": "Tax Position and Risk Review",
        "sections": [
            ("Overview", "The client's overall tax position and the main risks, in a short paragraph."),
            ("Position by tax head", "For each tax head in play (income tax, VAT, PAYE, withholding taxes, CGT, other), where the client stands."),
            ("Penalties and interest exposure", "The exposure from late or incorrect filing, using only the penalty runs on the file or figures in the material."),
            ("Mitigation plan", "The steps that reduce the risk, in priority order, with who should do them."),
        ],
        "guide": ("A risk review. Fill the risk_rows table with one row per issue (tax head, the issue, exposure with its currency if the "
                  "material gives it, likelihood Low/Medium/High, and the mitigation). Exposure may be left blank, never estimated. "
                  "The sections explain the table; do not repeat it."),
    }),
    ("zimra_submission", {
        "label": "ZIMRA submission / objection draft", "short": "ZIMRA submission", "letter": True,
        "title": "Submission to ZIMRA",
        "sections": [
            ("Introduction and reference", "Who we act for, the ZIMRA reference, and what this submission is about."),
            ("Background facts", "The facts ZIMRA needs, in date order."),
            ("Grounds and basis", "Why the client's position is right, citing the law that supports it."),
            ("Relief requested", "Exactly what the client asks ZIMRA to do."),
            ("Enclosures and reservation of rights", "What is attached, and the client's rights kept open."),
        ],
        "guide": ("A formal submission or notice of objection to ZIMRA, written for a ZIMRA officer: courteous, precise, no admissions, no "
                  "promises to pay or dates unless the instructions give them. Write the body only: no date, addressee, salutation or sign-off. "
                  "Never cite a Publication or News Article to ZIMRA as authority."),
    }),
])


def is_configured():
    return zimra_ai.is_configured()


# ------------------------------------------------------------------ the engagement's own record

def _d(value):
    return value.strftime("%d %B %Y").lstrip("0") if value else "-"


def _amt(value, cur=None):
    return f"{(cur + ' ') if cur else ''}{value:,.2f}" if value is not None else "-"


def financial_headlines(engagement):
    """The non-zero IAS 1 category totals of the engagement's trial balance (current and prior year), one line each, or []."""
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    if tb is None or not tb.lines:
        return []
    try:
        import financials as fin
        totals = fin.compute_totals(tb.lines)
        out = []
        for code, v in totals.items():
            if abs(v["current"]) < 0.005 and abs(v["prior"]) < 0.005:
                continue
            cat = fin.CATEGORY_BY_CODE.get(code) or {}
            out.append(f"{cat.get('label', code)} ({cat.get('statement', '')}): current year {v['current']:,.2f}; prior year {v['prior']:,.2f}")
        return out
    except Exception:
        return []


def engagement_facts(engagement):
    """The engagement's record as plain text (the client's name and numbers are still in it; redact before sending)."""
    c = engagement.client
    L = [f"Client: {c.name}" + (f" ({c.industry})" if c and c.industry else ""),
         f"Client TIN: {c.tin_number or '-'}; VAT number: {getattr(c, 'vat_number', None) or '-'}",
         f"Engagement: {engagement.title} ({engagement.type}); status {engagement.status}",
         f"Period: {_d(engagement.period_start)} to {_d(engagement.period_end)}",
         f"Tax services on this engagement: {', '.join(engagement.tax_service_list) or '-'}"]
    if c and c.principal_activities:
        L.append(f"Principal activities: {c.principal_activities}")
    if engagement.description:
        L.append(f"Engagement description: {engagement.description}")
    log = TaxResearchLogEntry.query.filter_by(engagement_id=engagement.id).order_by(TaxResearchLogEntry.id).all()
    if log:
        L.append("\nTECHNICAL RESEARCH LOG")
        for e in log:
            L.append(f"- Topic: {e.topic}" + (f"\n  Question: {e.question}" if e.question else "") + (f"\n  Research notes: {e.research_notes}" if e.research_notes else "")
                     + (f"\n  Conclusion reached: {e.conclusion}" if e.conclusion else "") + (f"\n  Authorities relied on: {e.references}" if e.references else ""))
    pos = TaxPosition.query.filter_by(engagement_id=engagement.id).order_by(TaxPosition.id).all()
    if pos:
        L.append("\nTAX POSITIONS TAKEN")
        for p in pos:
            L.append(f"- {p.tax_head or 'General'}: {p.position_description} (classification {p.classification}; exposure {_amt(p.estimated_exposure)}; "
                     f"approval {p.approval_status})" + (f" Notes: {p.notes}" if p.notes else ""))
    opts = TaxStructuringOption.query.filter_by(engagement_id=engagement.id).order_by(TaxStructuringOption.id).all()
    if opts:
        L.append("\nSTRUCTURING OPTIONS COMPARED")
        for o in opts:
            L.append(f"- {o.option_name}: {o.description or ''} Estimated tax impact {_amt(o.estimated_tax_impact)}; risk {o.risk_rating or '-'}; "
                     f"{'RECOMMENDED' if o.recommended else 'not recommended'}." + (f" Notes: {o.notes}" if o.notes else ""))
    disp = TaxDispute.query.filter_by(engagement_id=engagement.id).order_by(TaxDispute.id).all()
    if disp:
        L.append("\nTAX DISPUTES")
        for d in disp:
            L.append(f"- {d.tax_head or 'General'}: {d.issue_description} (stage {d.stage}; amount in dispute {_amt(d.amount_in_dispute)}; "
                     f"next action {d.next_action or '-'} by {_d(d.next_action_date)})" + (f" Notes: {d.notes}" if d.notes else ""))
    rets = TaxReturnRecord.query.filter_by(engagement_id=engagement.id).order_by(TaxReturnRecord.id).all()
    if rets:
        L.append("\nTAX RETURNS ON FILE")
        for r in rets:
            L.append(f"- {r.tax_head} {r.period_label}: status {r.status}; amount due {_amt(r.amount_due)}; assessed {_amt(r.assessed_amount)}")
    pens = PenaltyInterestCalculation.query.filter_by(engagement_id=engagement.id).order_by(PenaltyInterestCalculation.id).all()
    if pens:
        L.append("\nPENALTY AND INTEREST RUNS (from the firm's own engine)")
        for p in pens:
            L.append(f"- {p.tax_head or 'General'}: principal {_amt(p.principal_amount)}; {p.days_late} days late; rate used {p.rate_percent_used}%; "
                     f"computed {_amt(p.computed_amount)}" + (f" ({p.notes})" if p.notes else ""))
    itc = IncomeTaxComputation.query.filter_by(engagement_id=engagement.id).first()
    if itc:
        bits = [f"tax rate {itc.tax_rate_percent}%" if itc.tax_rate_percent is not None else "", f"AIDS levy {itc.aids_levy_percent}%" if itc.aids_levy_percent is not None else "",
                f"tax loss brought forward {_amt(itc.tax_loss_brought_forward)}", f"profit before tax typed in {_amt(itc.profit_before_tax_manual)}" if itc.profit_before_tax_manual is not None else ""]
        L.append("\nINCOME TAX COMPUTATION\n- " + "; ".join(b for b in bits if b) + (f"\n- Notes: {itc.notes}" if itc.notes else ""))
    heads = financial_headlines(engagement)
    if heads:
        L.append("\nHEADLINE FIGURES FROM THE CLIENT'S ACCOUNTS (trial balance)")
        L += [f"- {h}" for h in heads]
    return "\n".join(L)


def zimra_matter_facts(report):
    if not report.zimra_case_id:
        return ""
    case = ZimraCase.query.get(report.zimra_case_id)
    if case is None or case.client_id != report.engagement.client_id:
        return ""
    return zimra_ai.case_facts(case) + "\n\nCORRESPONDENCE\n" + zimra_ai.correspondence_log(case)


def document_texts(report):
    """[(file name, text)] for the uploaded documents the person ticked, within the size caps."""
    out, used = [], 0
    folder = current_app.config.get("UPLOAD_FOLDER", "")
    for doc_id in report.doc_ids:
        doc = Document.query.get(doc_id)
        if doc is None or doc.engagement_id != report.engagement_id or not doc.stored_filename or used >= MAX_DOCS_CHARS:
            continue
        text = zimra_ai.letter_text(doc, folder)
        if text:
            text = text[:MAX_DOC_CHARS]
            used += len(text)
            out.append((doc.original_filename, text))
    return out


# ------------------------------------------------------------------ the Firm Library

def library_query(report, extra=""):
    parts = [report.instructions or "", extra or "", KINDS[report.kind]["label"]]
    eng = report.engagement
    parts += [e.topic + " " + (e.question or "") for e in TaxResearchLogEntry.query.filter_by(engagement_id=eng.id).all()][:8]
    parts += [(p.tax_head or "") + " " + p.position_description for p in TaxPosition.query.filter_by(engagement_id=eng.id).all()][:6]
    parts += [(d.tax_head or "") + " " + d.issue_description for d in TaxDispute.query.filter_by(engagement_id=eng.id).all()][:4]
    if report.zimra_case_id:
        case = ZimraCase.query.get(report.zimra_case_id)
        if case is not None:
            parts += [case.tax_head or "", case.title or "", case.description or ""]
    return " ".join(p for p in parts if p).strip()[:2200]


def library_pairs(query, limit=MAX_PAIRS):
    """[(update, chunk)] best passages of the Firm Library for `query`, with weight-aware ranking; [] when nothing matches. Never raises."""
    if not (query or "").strip():
        return []
    try:
        import legislation_understand
        pairs, _ctx, _flags = legislation_understand.retrieve(query, limit=limit)
        return list(pairs)
    except Exception:
        db.session.rollback()
    try:
        import legislation_search
        return list(legislation_search.search_candidate_chunks(query, limit=limit))
    except Exception:
        db.session.rollback()
        return []


def excerpt_block(pairs):
    from legislation_search import _candidate_block
    return "\n\n---\n\n".join(_candidate_block(u, c) for u, c in pairs)


# ------------------------------------------------------------------ the model

COMPOSE_TOOL = {
    "name": "compose_tax_report",
    "description": "Return the written tax report, section by section, with the library sources relied on and the points a Partner must check.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sections": {
                "type": "array", "description": "The report's sections, in order.",
                "items": {"type": "object", "properties": {
                    "heading": {"type": "string"},
                    "text": {"type": "string", "description": "The section's wording. Plain paragraphs separated by a blank line; no markdown symbols. Cite library law inline as [#id] using the Update id."},
                }, "required": ["heading", "text"]},
            },
            "risk_rows": {
                "type": "array", "description": "Only for a risk review: one row per issue.",
                "items": {"type": "object", "properties": {
                    "tax_head": {"type": "string"}, "issue": {"type": "string"},
                    "exposure": {"type": "string", "description": "Only a figure the material gives, with its currency. Otherwise empty."},
                    "likelihood": {"type": "string", "enum": ["Low", "Medium", "High"]},
                    "mitigation": {"type": "string"},
                }, "required": ["tax_head", "issue", "likelihood", "mitigation"]},
            },
            "citations": {
                "type": "array", "description": "Every library excerpt the wording relies on.",
                "items": {"type": "object", "properties": {"update_id": {"type": "integer"}, "used_for": {"type": "string"}}, "required": ["update_id", "used_for"]},
            },
            "missing_information": {"type": "array", "items": {"type": "string"}, "description": "Facts, documents or decisions needed but not supplied (each matches a [CONFIRM] in the wording)."},
            "reviewer_notes": {"type": "array", "items": {"type": "string"}, "description": "What the reviewing Partner should check or decide: assumptions, risks, options where the position was not given."},
        },
        "required": ["sections", "citations", "missing_information", "reviewer_notes"],
    },
}

REWRITE_TOOL = {
    "name": "rewrite_report_section",
    "description": "Return the rewritten section.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The section's new wording, plain paragraphs, library law cited inline as [#id]."},
            "citations": {"type": "array", "items": {"type": "object", "properties": {"update_id": {"type": "integer"}, "used_for": {"type": "string"}}, "required": ["update_id", "used_for"]}},
            "missing_information": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text", "citations", "missing_information"],
    },
}

RULES = (
    "You are drafting a written report for a Partner at Neverlank Chartered Accountants, a Zimbabwean firm of chartered accountants and tax "
    "practitioners. A Partner will review, edit and sign it, so your job is a careful, accurate, well-argued first draft.\n\n"
    "STRICT RULES\n"
    "1. FACTS: use only the facts in ENGAGEMENT RECORD, UPLOADED DOCUMENTS, ZIMRA MATTER and PARTNER'S INSTRUCTIONS. Never invent or estimate an "
    "amount, date, reference, name, document or event. Where the report needs something you were not given, write [CONFIRM: what is needed] and "
    "list it under missing_information.\n"
    "2. LAW: state law only from the LIBRARY EXCERPTS (the firm's own filed material). Name an Act, section, statutory instrument or case only "
    "if it appears in an excerpt, and give a section number only if the excerpt shows it; otherwise write [CONFIRM: legal basis - what is "
    "needed]. Acts, Notices and SIs are primary law; a Zimbabwean court case is binding authority, a foreign case persuasive only; a "
    "Publication is commentary and a News Article an unconfirmed report - never present those two as law. Where excerpts disagree or carry a "
    "'Check:' line (a later instrument, a date not reached), say which you relied on and why. Cite every legal statement inline as [#id] with the "
    "excerpt's Update id, and list each excerpt relied on under citations. If no excerpts are supplied, state no law at all.\n"
    "3. POSITION: if the PARTNER'S INSTRUCTIONS state the position to take, state it faithfully and no more strongly than instructed. If they do "
    "not, do NOT choose one: put [CONFIRM: the position on ...] and set the realistic options out in reviewer_notes. Make no admission of "
    "liability, no promise to pay and no commitment to a date unless the instructions or facts give it.\n"
    "4. IDENTITY: the client's name, TIN and VAT number are replaced by the tokens {client_name}, {client_tin} and {client_vat}. Use those tokens "
    "(or 'the client'); never guess a name or number.\n"
    "5. FIGURES: use a figure only if it is in the material, exactly as given. Never calculate tax, penalties or interest yourself; quote the "
    "firm's penalty runs and the accounts' figures as they are.\n"
    "6. STYLE: formal, plain English, concise, no repetition between sections, no markdown symbols (no #, *, or bullet characters; use plain "
    "paragraphs or numbered points typed as '1.', '2.'). Follow the section plan, changing a heading only to make it more specific.\n"
    "7. Return everything through the tool."
)


def system_prompt(kind):
    k = KINDS[kind]
    plan = "\n".join(f"- {h}: {hint}" for h, hint in k["sections"])
    return f"{RULES}\n\nTHIS REPORT: {k['label']}.\n{k['guide']}\n\nSECTION PLAN\n{plan}"


def _call_model(system, user_message, tool, max_tokens=6000):
    """Returns (tool_input_dict, None) or (None, error message). The only place that talks to the API (tests replace it)."""
    try:
        response = zimra_ai._client().messages.create(
            model=DEFAULT_MODEL, max_tokens=max_tokens, temperature=0.2, system=system, tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]}, messages=[{"role": "user", "content": user_message}], timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        try:
            import legislation_search
            return None, legislation_search.friendly_error(exc)
        except Exception:
            return None, str(exc)[:400]
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool["name"]:
            return (block.input or {}), None
    return None, "The AI did not return a structured draft - try again."


def build_user_message(report, extra, pairs, docs, case_text):
    client = report.engagement.client
    red = lambda t: zimra_ai.redact(t, client)
    parts = ["ENGAGEMENT RECORD\n" + red(engagement_facts(report.engagement))]
    if docs:
        parts.append("UPLOADED DOCUMENTS (read from the files the Partner chose)\n" + "\n\n".join(red(f"--- {n} ---\n{t}") for n, t in docs))
    if case_text:
        parts.append("ZIMRA MATTER THIS SUBMISSION ANSWERS\n" + red(case_text))
    ins = (report.instructions or "").strip()
    if extra:
        ins = (ins + "\n\nFURTHER INSTRUCTION FOR THIS DRAFT: " + extra).strip()
    parts.append("PARTNER'S INSTRUCTIONS (the facts, the question and the position to take)\n" +
                 (red(ins) if ins else "(none given - do not decide the position; use [CONFIRM] and list the options in reviewer_notes)"))
    if report.addressee:
        parts.append("ADDRESSEE (for tone only; the app prints the address block itself)\n" + red(report.addressee))
    if pairs:
        parts.append("LIBRARY EXCERPTS (the firm's filed legislation and material - the only source of law you may use)\n\n" + excerpt_block(pairs))
    else:
        parts.append("LIBRARY EXCERPTS\n(No filed material matched this matter. State no law; use [CONFIRM: legal basis ...] where law is needed.)")
    parts.append(f"Today's date: {_d(date.today())}. Write the report now.")
    return "\n\n=====\n\n".join(parts)


def source_corpus(report, extra, pairs, docs, case_text):
    """Everything the model was allowed to rely on, un-redacted, for checking its answer."""
    eng = report.engagement
    bits = [engagement_facts(eng), report.instructions or "", extra or "", case_text or "", report.addressee or "",
            str(eng.period_start or ""), str(eng.period_end or ""), str(eng.period_end.year if eng.period_end else "")]
    bits += [t for _n, t in docs]
    for u, c in pairs:
        bits += [u.title or "", u.summary or "", u.ai_summary or "", " ".join(u.ai_key_changes or []), u.source_reference or "", u.case_citation or "",
                 c.text if c is not None else (u.extracted_text or "")[:3000]]
    return "\n".join(bits)


# ------------------------------------------------------------------ the guards

_MARK = re.compile(r"\[#\s*(\d+(?:\s*[,;]\s*#?\s*\d+)*)\s*\]")


def _protect(text):
    return _MARK.sub(lambda m: "[#N" + re.sub(r"\D+", "n", m.group(1)) + "N]", text or "")


def _unprotect(text):
    return re.sub(r"\[#N([\dn]+)N\]", lambda m: "[#" + ", ".join(x for x in m.group(1).split("n") if x) + "]", text or "")


def guard_text(text, corpus, client, shown_ids, flagged_figs, flagged_refs):
    """Clean one block of model wording: drop citation markers for excerpts that were not supplied, flag every figure and legal reference the
    material does not contain, put the client's real identifiers back."""
    text = (text or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[ \t]*\*\*|^#{1,4}\s+", "", text, flags=re.M)

    def keep(m):
        ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
        ok = [i for i in ids if i in shown_ids]
        return "[#" + ", ".join(str(i) for i in ok) + "]" if ok else ""
    text = _MARK.sub(keep, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = _protect(text)
    text, f1 = zimra_ai.flag_figures(text, corpus)
    text, f2 = zimra_ai.flag_legal_refs(text, corpus)
    flagged_figs += f1
    flagged_refs += f2
    return zimra_ai.restore_tokens(_unprotect(text), client).strip()


def _cites_order(sections):
    """Update ids in the order they are first cited in the sections."""
    order = []
    for s in sections:
        for m in _MARK.finditer(s.get("text") or ""):
            for x in re.findall(r"\d+", m.group(1)):
                if int(x) not in order:
                    order.append(int(x))
    return order


# ------------------------------------------------------------------ making and changing a draft

def skeleton(report):
    """The structure with [CONFIRM] placeholders and the engagement's own record dropped in, used when the AI is off or failed."""
    k = KINDS[report.kind]
    eng = report.engagement
    facts = []
    for e in TaxResearchLogEntry.query.filter_by(engagement_id=eng.id).order_by(TaxResearchLogEntry.id).all():
        facts.append(f"{e.topic}: {e.conclusion or e.question or ''}".strip(": "))
    sections = []
    for h, hint in k["sections"]:
        text = f"[CONFIRM: {hint}]"
        if h in ("Facts relied on", "Background and scope", "Background facts") and facts:
            text = "\n".join(f"{i}. {f}" for i, f in enumerate(facts, 1)) + "\n\n[CONFIRM: add any other facts relied on]"
        if h in ("Issue", "Introduction and reference") and (report.instructions or "").strip():
            text = report.instructions.strip() + "\n\n[CONFIRM: tighten into the issue / introduction]"
        sections.append({"heading": h, "text": text})
    rows = [{"tax_head": "[CONFIRM]", "issue": "[CONFIRM: describe the issue]", "exposure": "", "likelihood": "Medium", "mitigation": "[CONFIRM: mitigation]"}] \
        if report.kind == "risk_review" else []
    return {"sections": sections, "risk_rows": rows, "sources": [], "missing": ["The AI draft was not made, so every section is a placeholder for you to write."],
            "notes": [], "flags": []}


def generate(report, extra=""):
    """(ok, message). Writes a fresh draft into `report` (keeping the previous wording so it can be restored)."""
    old = report.data
    if old["sections"]:
        report_prev = {"sections": old["sections"], "risk_rows": old["risk_rows"]}
    else:
        report_prev = None
    client = report.engagement.client
    pairs = library_pairs(library_query(report, extra))
    docs = document_texts(report)
    case_text = zimra_matter_facts(report)
    if not is_configured():
        if old["sections"]:
            report.ai_note = "AI is not switched on (no ANTHROPIC_API_KEY on the server), so your existing draft was left exactly as it is."
            return False, report.ai_note
        d = skeleton(report)
        d["considered"] = [{"id": u.id, "title": u.title, "type": u.type_label} for u in {u.id: u for u, _c in pairs}.values()]
        if report_prev:
            d["previous"] = report_prev
        report.set_data(d)
        report.ai_used = False
        report.ai_note = "AI is not switched on (no ANTHROPIC_API_KEY on the server), so this is the structure with placeholders to fill in."
        return False, report.ai_note
    data, err = _call_model(system_prompt(report.kind), build_user_message(report, extra, pairs, docs, case_text), COMPOSE_TOOL)
    if err:
        if not old["sections"]:
            d = skeleton(report)
            d["missing"].insert(0, "The AI could not be reached: " + err)
            report.set_data(d)
        report.ai_used = False
        report.ai_note = ("The AI draft failed: " + err)[:390]
        return False, report.ai_note
    corpus = source_corpus(report, extra, pairs, docs, case_text)
    shown = {u.id for u, _c in pairs}
    figs, refs = [], []
    sections = []
    for s in (data.get("sections") or [])[:MAX_SECTIONS]:
        head = zimra_ai.restore_tokens(re.sub(r"\s+", " ", str(s.get("heading") or "")).strip()[:160], client)
        txt = guard_text(str(s.get("text") or ""), corpus, client, shown, figs, refs)
        txt = zimra_ai.strip_framing(txt)
        if head or txt:
            sections.append({"heading": head or "Section", "text": txt})
    if not sections:
        report.ai_note = "The AI returned no wording - try again."
        return False, report.ai_note
    rows = []
    if report.kind == "risk_review":
        for r in (data.get("risk_rows") or [])[:30]:
            rows.append({"tax_head": guard_text(str(r.get("tax_head") or ""), corpus, client, shown, figs, refs)[:80],
                         "issue": guard_text(str(r.get("issue") or ""), corpus, client, shown, figs, refs)[:600],
                         "exposure": guard_text(str(r.get("exposure") or ""), corpus, client, shown, figs, refs)[:120],
                         "likelihood": r.get("likelihood") if r.get("likelihood") in ("Low", "Medium", "High") else "Medium",
                         "mitigation": guard_text(str(r.get("mitigation") or ""), corpus, client, shown, figs, refs)[:600]})
    sources, considered, dropped = zimra_ai.build_sources(data.get("citations"), pairs)
    flags = []
    if figs:
        flags.append("Figures not found in the file, documents or your instructions (each marked in the text): " + ", ".join(dict.fromkeys(figs)))
    if refs:
        flags.append("Legal references not found in the Firm Library (each marked in the text): " + ", ".join(dict.fromkeys(refs)))
    if dropped:
        flags.append(f"{len(dropped)} citation(s) pointed at material that was not supplied and were removed.")
    d = {"sections": sections, "risk_rows": rows, "sources": sources, "considered": considered,
         "missing": zimra_ai.clean_list(data.get("missing_information")), "notes": zimra_ai.clean_list(data.get("reviewer_notes")), "flags": flags}
    if report_prev:
        d["previous"] = report_prev
    report.set_data(d)
    report.ai_used = True
    report.ai_note = f"Drafted by AI from the engagement record, {len(pairs)} Firm Library passage(s) and {len(docs)} uploaded document(s)."
    report.status = "Draft"
    return True, report.ai_note


def rewrite_section(report, idx, instruction):
    """(ok, message): ask the AI to rework one section as instructed, using the Library for that section."""
    d = report.data
    if not (0 <= idx < len(d["sections"])):
        return False, "That section no longer exists."
    if not is_configured():
        return False, "AI is not switched on on this server, so a section cannot be rewritten automatically."
    client = report.engagement.client
    sec = d["sections"][idx]
    pairs = library_pairs(library_query(report, (sec["heading"] + " " + sec["text"][:400] + " " + instruction)))
    red = lambda t: zimra_ai.redact(t, client)
    whole = "\n\n".join(f"## {s['heading']}\n{s['text']}" for s in d["sections"])
    msg = "\n\n=====\n\n".join([
        "ENGAGEMENT RECORD\n" + red(engagement_facts(report.engagement)),
        "THE REPORT SO FAR (for context; do not change other sections)\n" + red(whole),
        f"SECTION TO REWRITE: {sec['heading']}\nCurrent wording:\n{red(sec['text'])}",
        "WHAT THE PARTNER WANTS CHANGED\n" + red(instruction or "Improve this section; keep to the facts and the law supplied."),
        ("LIBRARY EXCERPTS (the only source of law you may use)\n\n" + excerpt_block(pairs)) if pairs else "LIBRARY EXCERPTS\n(None matched. State no law.)",
        "Rewrite only this section and return its new wording through the tool."])
    data, err = _call_model(system_prompt(report.kind), msg, REWRITE_TOOL, max_tokens=3000)
    if err:
        return False, "The AI could not rewrite the section: " + err
    corpus = source_corpus(report, instruction, pairs, [], "") + "\n" + sec["text"]
    shown = {u.id for u, _c in pairs} | set(_cites_order([sec]))
    figs, refs = [], []
    txt = guard_text(str(data.get("text") or ""), corpus, client, shown, figs, refs)
    txt = zimra_ai.strip_framing(txt)
    if not txt:
        return False, "The AI returned no wording - try again."
    d["sections"][idx] = {"heading": sec["heading"], "text": txt}
    sources, _c, _dr = zimra_ai.build_sources(data.get("citations"), pairs)
    have = {s["id"] for s in d["sources"]}
    d["sources"] += [s for s in sources if s["id"] not in have]
    for m in zimra_ai.clean_list(data.get("missing_information")):
        if m not in d["missing"]:
            d["missing"].append(m)
    if figs or refs:
        d["flags"].append(f"Section '{sec['heading']}': " + "; ".join(x for x in (
            ("figures to verify: " + ", ".join(dict.fromkeys(figs))) if figs else "", ("references to verify: " + ", ".join(dict.fromkeys(refs))) if refs else "") if x))
    report.set_data(d)
    report.status = "Draft"
    return True, f"Section '{sec['heading']}' rewritten."


# ------------------------------------------------------------------ the Word document

def _paras(doc, text, size=10.5):
    from docx.shared import Pt
    for chunk in [c for c in re.split(r"\n{2,}", text or "") if c.strip()]:
        p = doc.add_paragraph()
        brand._run(p, chunk.strip(), size=size)
        p.paragraph_format.space_after = Pt(6)


def build_docx(report):
    """The report as a Word document (io.BytesIO) on the firm's letterhead; letter kinds carry the addressee, salutation and signature."""
    from docx import Document as WordDoc
    from docx.shared import Pt
    from legislation_memo import _numbered
    k = KINDS[report.kind]
    d = report.data
    eng = report.engagement
    client = eng.client
    firm = brand.firm_info()
    signed = brand.signature_valid(report)
    order = _cites_order(d["sections"])
    by_id = {s["id"]: s for s in d["sources"]}
    doc = WordDoc()
    brand.setup_document(doc)
    brand.branded_footer(doc)
    brand.letterhead_band(doc, k["title"], sub=report.title or eng.title, number=report.reference or None)
    if not signed and report.status != "Reviewed":
        brand.note_box(doc, "DRAFT - not yet reviewed by a Partner. Check every figure, section and source against the instrument itself before this goes to anyone.", title="Draft")
    if k["letter"]:
        p = doc.add_paragraph()
        brand._run(p, _d(date.today()), size=10.5)
        if report.addressee:
            for n, line in enumerate([x for x in re.split(r"\n+", report.addressee) if x.strip()]):
                p = doc.add_paragraph()
                brand._run(p, line.strip(), size=10.5, bold=(n == 0))
                p.paragraph_format.space_after = Pt(0)
        doc.add_paragraph()
        p = doc.add_paragraph()
        brand._run(p, "Dear Sir/Madam" if report.kind == "zimra_submission" else "Dear Sirs", size=10.5)
        p = doc.add_paragraph()
        subj = (report.title or k["title"]) + (f" - {client.name}" if client else "")
        if report.reference:
            subj += f" - Ref: {report.reference}"
        brand._run(p, subj.upper(), size=10.5, bold=True, colour=brand.NAVY)
    else:
        brand.meta_strip(doc, [("Client", client.name if client else "-"), ("Engagement", eng.title), ("Date", _d(date.today())),
                               ("Status", "Signed" if signed else report.status)])
        doc.add_paragraph()
    for s in d["sections"]:
        if s.get("heading"):
            brand.heading(doc, s["heading"], level=2)
        _paras(doc, _numbered(s.get("text") or "", order))
    if report.kind == "risk_review" and d["risk_rows"]:
        brand.heading(doc, "Risk register", level=2)
        head = ["Tax head", "Issue", "Exposure", "Likelihood", "Mitigation"]
        tb = doc.add_table(rows=len(d["risk_rows"]) + 1, cols=len(head))
        for ci, h in enumerate(head):
            tb.rows[0].cells[ci].paragraphs[0].add_run(h)
        for ri, r in enumerate(d["risk_rows"], 1):
            for ci, key in enumerate(("tax_head", "issue", "exposure", "likelihood", "mitigation")):
                tb.rows[ri].cells[ci].paragraphs[0].add_run(_numbered(str(r.get(key) or ""), order))
        brand.style_table(tb)
        doc.add_paragraph()
    if k["letter"]:
        p = doc.add_paragraph()
        brand._run(p, "Yours faithfully", size=10.5)
        if signed:
            brand.add_signature_block(doc, firm_name=firm.get("name"), signed_on=report.mp_signed_at)
        else:
            doc.add_paragraph()
            brand.add_signature_block(doc, firm_name=firm.get("name"))
    cited = [i for i in order if i in by_id]
    if cited:
        brand.heading(doc, "Sources (Firm Library)", level=2)
        for n, i in enumerate(cited, 1):
            s = by_id[i]
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            brand._run(p, f"[{n}]  ", size=9.5, bold=True, colour=brand.NAVY)
            brand._run(p, s.get("title", ""), size=9.5, bold=True)
            tail = " · ".join(x for x in [s.get("type"), s.get("ref")] if x)
            if tail:
                brand._run(p, "  " + tail, size=9, colour=brand.MUTED)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    brand._run(p, "Prepared with AI assistance from the engagement record and the firm's filed Library, and reviewed by the firm. It is professional advice on the facts "
                  "described, not a guarantee of how ZIMRA or a court will treat them.", size=8, italic=True, colour=brand.MUTED)
    brand.fix_document(doc)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ------------------------------------------------------------------ the pages

def _report_or_404(report_id):
    report = TaxAdvisoryReport.query.get_or_404(report_id)
    import engagements as eng
    eng._ensure_engagement_access(report.engagement)
    return report


def _can_review(user):
    return user.role in ("admin", "partner", "manager")


def reports_for(engagement):
    return TaxAdvisoryReport.query.filter_by(engagement_id=engagement.id).order_by(TaxAdvisoryReport.created_at.desc()).all()


def new_report_options(engagement):
    """What the 'New report' form offers: this engagement's uploaded documents and the client's ZIMRA matters."""
    docs = Document.query.filter_by(engagement_id=engagement.id).order_by(Document.uploaded_at.desc()).limit(60).all()
    cases = ZimraCase.query.filter_by(client_id=engagement.client_id).order_by(ZimraCase.opened_on.desc()).limit(40).all()
    return {"docs": docs, "cases": cases}


@taxreports_bp.route("/engagement/<int:engagement_id>/new", methods=["POST"])
@login_required
def create(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    import engagements as eng
    eng._ensure_engagement_access(engagement)
    kind = request.form.get("kind", "")
    if kind not in KINDS:
        flash("Choose the kind of report to write.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    doc_ids = [int(x) for x in request.form.getlist("doc_ids") if x.isdigit()]
    case_id = request.form.get("zimra_case_id", type=int)
    report = TaxAdvisoryReport(engagement_id=engagement_id, kind=kind, created_by_id=current_user.id,
                               title=(request.form.get("title") or "").strip()[:200] or KINDS[kind]["title"],
                               addressee=(request.form.get("addressee") or "").strip()[:300] or None,
                               reference=(request.form.get("reference") or "").strip()[:100] or None,
                               instructions=(request.form.get("instructions") or "").strip() or None,
                               doc_ids_json=json.dumps(doc_ids), zimra_case_id=case_id if (case_id and kind == "zimra_submission") else None)
    db.session.add(report)
    db.session.flush()
    ok, msg = generate(report)
    report.updated_at = datetime.utcnow()
    db.session.commit()
    flash(("The first draft is ready to edit. " if ok else "") + msg + " Nothing is final until a Partner marks it reviewed.", "success" if ok else "warning")
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/<int:report_id>")
@login_required
def editor(report_id):
    report = _report_or_404(report_id)
    q = (request.args.get("q") or "").strip()
    results = []
    if q:
        for u, c in library_pairs(q, limit=8):
            results.append({"id": u.id, "title": u.title, "type": u.type_label, "ref": u.source_reference or "",
                            "heading": c.heading if c is not None else "", "text": (c.text if c is not None else (u.extracted_text or ""))[:420]})
    import legislation_understand
    weights = {}
    for s in report.data["sources"]:
        from models import LegislativeUpdate
        u = LegislativeUpdate.query.get(s["id"])
        weights[s["id"]] = legislation_understand.weight_label(u) if u is not None else ""
    return render_template("taxreports/editor.html", report=report, d=report.data, kind=KINDS[report.kind], q=q, results=results,
                           weights=weights, configured=is_configured(), can_review=_can_review(current_user), engagement=report.engagement,
                           cited_order=_cites_order(report.data["sections"]))


def _touch(report):
    report.updated_at = datetime.utcnow()
    report.status = "Draft"
    report.reviewed_by_id = None
    report.reviewed_at = None


@taxreports_bp.route("/<int:report_id>/save", methods=["POST"])
@login_required
def save(report_id):
    report = _report_or_404(report_id)
    d = report.data
    report.title = (request.form.get("title") or "").strip()[:200] or report.title
    report.addressee = (request.form.get("addressee") or "").strip()[:300] or None
    report.reference = (request.form.get("reference") or "").strip()[:100] or None
    report.instructions = (request.form.get("instructions") or "").strip() or None
    secs = []
    n = request.form.get("n_sections", type=int) or len(d["sections"])
    delete = request.form.get("action", "")
    for i in range(n):
        if delete == f"delete_{i}":
            continue
        heading = (request.form.get(f"heading_{i}") or "").strip()[:160]
        text = (request.form.get(f"text_{i}") or "").replace("\r\n", "\n").strip()
        if heading or text:
            secs.append({"heading": heading, "text": text})
    if delete == "add_section":
        secs.append({"heading": "New section", "text": ""})
    d["sections"] = secs
    rows = []
    for i in range(request.form.get("n_rows", type=int) or 0):
        row = {k: (request.form.get(f"r{i}_{k}") or "").strip() for k in ("tax_head", "issue", "exposure", "likelihood", "mitigation")}
        if delete == f"delrow_{i}" or not any(row.values()):
            continue
        if row["likelihood"] not in ("Low", "Medium", "High"):
            row["likelihood"] = "Medium"
        rows.append(row)
    if delete == "add_row":
        rows.append({"tax_head": "", "issue": "", "exposure": "", "likelihood": "Medium", "mitigation": ""})
    d["risk_rows"] = rows
    report.set_data(d)
    _touch(report)
    db.session.commit()
    flash("Saved. It needs to be marked reviewed again before it is signed." if delete in ("", "save") else "Done.", "success")
    return redirect(url_for("taxreports.editor", report_id=report.id) + ("#sections" if delete else ""))


@taxreports_bp.route("/<int:report_id>/regenerate", methods=["POST"])
@login_required
def regenerate(report_id):
    report = _report_or_404(report_id)
    ok, msg = generate(report, (request.form.get("extra") or "").strip())
    _touch(report)
    db.session.commit()
    flash(msg + (" The earlier wording is kept: use 'Restore the previous draft' if you prefer it." if ok and report.data.get("previous") else ""), "success" if ok else "warning")
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/<int:report_id>/restore", methods=["POST"])
@login_required
def restore(report_id):
    report = _report_or_404(report_id)
    d = report.data
    prev = d.pop("previous", None)
    if prev:
        d["sections"], d["risk_rows"] = prev["sections"], prev.get("risk_rows", [])
        report.set_data(d)
        _touch(report)
        db.session.commit()
        flash("The previous draft is back.", "success")
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/<int:report_id>/section/<int:idx>/rewrite", methods=["POST"])
@login_required
def rewrite(report_id, idx):
    report = _report_or_404(report_id)
    ok, msg = rewrite_section(report, idx, (request.form.get("instruction") or "").strip())
    if ok:
        _touch(report)
        db.session.commit()
    flash(msg, "success" if ok else "warning")
    return redirect(url_for("taxreports.editor", report_id=report.id) + f"#sec{idx}")


@taxreports_bp.route("/<int:report_id>/review", methods=["POST"])
@login_required
def review(report_id):
    report = _report_or_404(report_id)
    if not _can_review(current_user):
        abort(403)
    if request.form.get("undo"):
        report.status, report.reviewed_by_id, report.reviewed_at = "Draft", None, None
        flash("Marked as a draft again.", "info")
    else:
        if report.open_placeholders:
            flash(f"{report.open_placeholders} [CONFIRM: ...] placeholder(s) are still in the report. Complete or remove them before marking it reviewed.", "danger")
            return redirect(url_for("taxreports.editor", report_id=report.id))
        report.status, report.reviewed_by_id, report.reviewed_at = "Reviewed", current_user.id, datetime.utcnow()
        flash("Marked as reviewed. The Managing Partner can now sign it.", "success")
    db.session.commit()
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/<int:report_id>/delete", methods=["POST"])
@login_required
def delete(report_id):
    report = _report_or_404(report_id)
    if not (current_user.role in ("admin", "partner") or report.created_by_id == current_user.id):
        abort(403)
    eid = report.engagement_id
    db.session.delete(report)
    db.session.commit()
    flash("The report was deleted.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=eid, tab="finalisation"))


@taxreports_bp.route("/<int:report_id>/docx")
@login_required
def download(report_id):
    report = _report_or_404(report_id)
    buf = build_docx(report)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{report.engagement.client.name}_{KINDS[report.kind]['short']}").strip("_")
    return send_file(buf, as_attachment=True, download_name=f"{safe}_{date.today():%Y%m%d}.docx",
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
