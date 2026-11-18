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
REPORT_MODEL = os.environ.get("TAX_REPORT_MODEL") or DEFAULT_MODEL     # set TAX_REPORT_MODEL on Render to use a stronger model for reports only
LONG_TIMEOUT = 290            # seconds the AI may take over each of the two steps (analysis, then writing)
THINKING_BUDGET = 8000        # tokens the AI may spend reasoning before the analysis (used only if this SDK/model supports it)
ANALYSIS_TOKENS = 14000
WRITE_TOKENS = 16000
BACKGROUND = True             # draft in a background thread (tests switch this off to run inline)
STALE_AFTER = 1500            # seconds after which a draft still 'Writing' is treated as lost (the server restarted)
MAX_PAIRS = 22
MAX_DOC_CHARS = 16000         # per uploaded document
MAX_DOCS_CHARS = 48000       # all uploaded documents together
MAX_SECTIONS = 18

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
    "You are a senior tax partner at Neverlank Chartered Accountants, a Zimbabwean firm of chartered accountants and tax practitioners, "
    "writing a report that a fellow Partner will review, edit and sign. Write the way an experienced, well-read practitioner would: "
    "reason from the facts to a conclusion, weigh both sides, and say what you think and why. A thin, generic or list-like report is a failure; "
    "a thorough, well-argued one is the aim.\n\n"
    "RULES\n"
    "1. FACTS: use only the facts in ENGAGEMENT RECORD, UPLOADED DOCUMENTS, ZIMRA MATTER and PARTNER'S INSTRUCTIONS. Never invent or estimate an "
    "amount, date, reference, name, document or event. Where the report needs something you were not given, write [CONFIRM: what is needed] and "
    "list it under missing_information.\n"
    "2. LAW: build the argument on the LIBRARY EXCERPTS (the firm's own filed material) and cite each legal statement inline as [#id] with the "
    "excerpt's Update id; list each excerpt relied on under citations. You may also use your general knowledge of Zimbabwean tax law and of tax "
    "principles to reason and to say where the law probably lies, but name an Act, section, statutory instrument or case that is NOT in an excerpt "
    "only where it really matters, and mark it '(not in the Firm Library - to be verified)'; never give a section number you are not sure of - "
    "write [CONFIRM: legal basis - what is needed] instead. Acts, Notices and SIs are primary law; a Zimbabwean court case is binding authority, "
    "a foreign case persuasive only; a Publication is commentary and a News Article an unconfirmed report - never present those two as law. "
    "Where excerpts disagree or carry a 'Check:' line (a later instrument, a date not reached), say which you relied on and why.\n"
    "3. POSITION: if the PARTNER'S INSTRUCTIONS state the position to take, advance it faithfully and as persuasively as the law honestly allows, "
    "and say plainly where it is weak. If they do not state one, reach your own reasoned recommendation and begin it with 'Recommended position "
    "(for the Partner to confirm):', then set the realistic alternatives out in reviewer_notes. Make no admission of liability, no promise to pay "
    "and no commitment to a date unless the instructions or facts give it.\n"
    "4. IDENTITY: the client's name, TIN and VAT number are replaced by the tokens {client_name}, {client_tin} and {client_vat}. Use those tokens "
    "(or 'the client'); never guess a name or number.\n"
    "5. FIGURES: use a figure only if it is in the material, exactly as given. Never calculate tax, penalties or interest yourself; quote the "
    "firm's penalty runs and the accounts' figures as they are.\n"
    "6. DEPTH: for every issue, state the rule, apply it to this client's actual facts, give the strongest counter-argument (and what ZIMRA would "
    "say), and conclude. Use the PRIOR ANALYSIS you are given: carry its reasoning, its weighing of the arguments and its risks into the "
    "wording; do not drop it for a shorter summary. Be specific to this client, never generic. Put the answer first, then the reasoning.\n"
    "7. STYLE: formal, plain English, well organised, no padding and no repetition between sections. No markdown symbols (no #, *, or bullet "
    "characters; use plain paragraphs or numbered points typed as '1.', '2.'). Follow the section plan, changing a heading only to make it more "
    "specific, and add a section where the matter needs one.\n"
    "8. LENGTH: as long as the matter needs and no longer: a substantial section is several paragraphs, not a few lines. An opinion or risk review "
    "on a real matter is typically 2,500 to 5,000 words; a client letter-report 1,500 to 3,000. The risk table may have up to 20 rows.\n"
    "9. Return everything through the tool, with the report's sections in the 'sections' array."
)

ANALYSIS_RULES = (
    "You are a senior tax partner at Neverlank Chartered Accountants (Zimbabwe), working out your thinking BEFORE a report is written. "
    "Do not write the report. Write your working analysis in plain text, as a thorough briefing to yourself, using only the facts in the "
    "material supplied (never invent a fact, figure, date or name; list what is missing). Cover, in this order:\n"
    "A. THE QUESTION: what exactly has to be decided or advised, and why it matters to this client.\n"
    "B. THE FACTS: the facts that matter, the facts that are unclear or missing, and the assumptions you would have to make.\n"
    "C. THE LAW: each rule that bears on it, with the Update id of the Firm Library excerpt where it appears; what each instrument is worth "
    "(Act or SI, court case, commentary); conflicts or later changes the excerpts reveal. Add what you know of the law beyond the excerpts, "
    "clearly marked as not in the Firm Library.\n"
    "D. APPLICATION: take each issue in turn, apply the law to these facts, and reach a view. Test the opposite view as hard as you can.\n"
    "E. ZIMRA'S LIKELY RESPONSE: what ZIMRA would argue, how strong that is, and how the client would answer.\n"
    "F. CONCLUSION AND CONFIDENCE: your recommended position (or the options, if the instructions leave it open), how confident you are, "
    "and what would change your mind.\n"
    "G. RISKS AND ACTIONS: the exposure (only figures that are in the material), what the client should do, in priority order.\n"
    "Be specific and rigorous. Use the Partner's instructions as the brief. Use the plain tokens {client_name}, {client_tin}, {client_vat} "
    "where the material uses them. Aim for 1,500 to 3,000 words of dense reasoning."
)


def system_prompt(kind):
    k = KINDS[kind]
    plan = "\n".join(f"- {h}: {hint}" for h, hint in k["sections"])
    return f"{RULES}\n\nTHIS REPORT: {k['label']}.\n{k['guide']}\n\nSECTION PLAN\n{plan}"


def _long_client():
    """A whole report takes the AI longer than a letter body, so this client waits up to LONG_TIMEOUT seconds (the draft runs in the background)."""
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=LONG_TIMEOUT)


def _is_model_missing(exc):
    t = str(exc).lower()
    return "model" in t and ("not_found" in t or "not found" in t or "invalid" in t or "does not exist" in t)


def _call_text(system, user_message, max_tokens=ANALYSIS_TOKENS):
    """Step 1: the AI's reasoning as plain text (it may think first when the SDK/model allows). Returns (text, None) or (None, error). Tests replace it."""
    def go(model, thinking):
        kw = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user_message}])
        if thinking:
            kw["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET}
        return _long_client().messages.create(**kw)
    response = None
    last = None
    for model in dict.fromkeys([REPORT_MODEL, DEFAULT_MODEL]):
        for thinking in (True, False):
            try:
                response = go(model, thinking)
                break
            except TypeError as exc:                      # this SDK does not know 'thinking'
                last = exc
                continue
            except Exception as exc:
                last = exc
                if thinking and not _is_model_missing(exc):
                    continue                              # this model may not allow thinking: try again without it
                break
        if response is not None or not (last is not None and _is_model_missing(last)):
            break
    if response is None:
        try:
            import legislation_search
            return None, legislation_search.friendly_error(last)
        except Exception:
            return None, str(last)[:400]
    text = "".join(getattr(b, "text", "") or "" for b in response.content if getattr(b, "type", None) == "text").strip()
    try:
        current_app.logger.info("tax_reports: analysis stop_reason=%s chars=%s", getattr(response, "stop_reason", None), len(text))
    except Exception:
        pass
    return (text, None) if text else (None, "The AI sent no analysis.")


def _call_model(system, user_message, tool, max_tokens=WRITE_TOKENS):
    """Step 2: returns (tool_input_dict, None) or (None, error message). The only place that writes the report (tests replace it)."""
    response = None
    last = None
    try:
        for model in dict.fromkeys([REPORT_MODEL, DEFAULT_MODEL]):
            try:
                response = _long_client().messages.create(
                    model=model, max_tokens=max_tokens, system=system, tools=[tool],
                    tool_choice={"type": "tool", "name": tool["name"]}, messages=[{"role": "user", "content": user_message}])
                break
            except Exception as exc:
                last = exc
                if not _is_model_missing(exc):
                    raise
        if response is None:
            raise last
    except Exception as exc:
        try:
            import legislation_search
            return None, legislation_search.friendly_error(exc)
        except Exception:
            return None, str(exc)[:400]
    stop = getattr(response, "stop_reason", None)
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool["name"]:
            data = block.input
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    data = {}
            data = data if isinstance(data, dict) else {}
            try:
                current_app.logger.info("tax_reports: AI replied stop_reason=%s keys=%s", stop, sorted(data.keys()))
            except Exception:
                pass
            if stop == "max_tokens" and not (data.get("sections") or data.get("text")):
                return None, "The report was longer than the AI could write in one go, so it was cut off. Shorten your instructions or tick fewer documents, then try again."
            return data, None
    return None, "The AI did not return a structured draft - try again."


def build_user_message(report, extra, pairs, docs, case_text, analysis=""):
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
    if analysis:
        parts.append("PRIOR ANALYSIS (your own working analysis of this matter, written first; the report must carry its reasoning)\n" + analysis)
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


def _note_failure(report, text):
    """Replace any earlier 'AI could not ...' line in a placeholder draft with the latest reason, so the page never shows a stale one."""
    d = report.data
    if report.ai_used or not d["sections"]:
        return
    d["missing"] = [m for m in d["missing"] if not m.startswith(("The AI could not", "The AI draft was not made", "The AI returned"))]
    d["missing"].insert(0, text)
    report.set_data(d)


def _sections_from(data):
    """The sections list from the model's reply, tolerating a JSON string or a dict keyed by heading."""
    secs = data.get("sections")
    if isinstance(secs, str):
        try:
            secs = json.loads(secs)
        except ValueError:
            secs = None
    if isinstance(secs, dict):
        secs = [{"heading": k, "text": v} for k, v in secs.items()]
    out = []
    for x in secs or []:
        if isinstance(x, dict):
            out.append(x)
        elif isinstance(x, str) and x.strip():
            out.append({"heading": "", "text": x})
    return out


def generate(report, extra=""):
    """(ok, message). Writes a fresh draft into `report` (keeping the previous wording so it can be restored). A report imported from outside is reviewed instead."""
    old = report.data
    if old.get("origin") == "imported":
        return review_draft(report, extra)
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
    # step 1: think the matter through in plain text; step 2: write the report from that analysis. A failed step 1 is not fatal.
    analysis, a_err = _call_text(ANALYSIS_RULES + f"\n\nTHE REPORT TO BE WRITTEN AFTERWARDS: {KINDS[report.kind]['label']}. {KINDS[report.kind]['guide']}",
                                 build_user_message(report, extra, pairs, docs, case_text) .replace("Write the report now.", "Write your analysis now."))
    data, err = _call_model(system_prompt(report.kind), build_user_message(report, extra, pairs, docs, case_text, analysis or ""), COMPOSE_TOOL)
    if err:
        if not old["sections"]:
            d = skeleton(report)
            d["missing"].insert(0, "The AI could not be reached: " + err)
            report.set_data(d)
        else:
            _note_failure(report, "The AI could not write the draft: " + err)
        report.ai_note = ("The AI draft failed: " + err)[:390]
        return False, report.ai_note
    corpus = source_corpus(report, extra, pairs, docs, case_text)
    shown = {u.id for u, _c in pairs}
    figs, refs = [], []
    sections = []
    for s in _sections_from(data)[:MAX_SECTIONS]:
        head = zimra_ai.restore_tokens(re.sub(r"\s+", " ", str(s.get("heading") or "")).strip()[:160], client)
        txt = guard_text(str(s.get("text") or ""), corpus, client, shown, figs, refs)
        txt = zimra_ai.strip_framing(txt)
        if head or txt:
            sections.append({"heading": head or "Section", "text": txt})
    if not sections:
        keys = ", ".join(sorted(map(str, data.keys()))) or "nothing"
        report.ai_note = f"The AI replied but sent no report sections (it sent: {keys}). Try again; if it keeps happening, shorten your instructions."
        _note_failure(report, "The AI returned no wording on the last try.")
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
    shown_analysis = zimra_ai.restore_tokens(analysis, client) if analysis else ""
    if a_err and not analysis:
        flags.append("The AI's reasoning step did not complete (" + str(a_err)[:160] + "), so this draft was written without it and may be less thorough. Try again.")
    d = {"sections": sections, "risk_rows": rows, "sources": sources, "considered": considered, "analysis": shown_analysis,
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



# ------------------------------------------------------------------ reviewing a draft written outside the app

MAX_IMPORT_CHARS = 80000
SEVERITIES = ("High", "Medium", "Low")
COMMENT_TYPES = {"law": "Law", "fact": "Facts", "figure": "Figure", "reasoning": "Reasoning", "missing": "Missing", "style": "Style"}

REVIEW_TOOL = {
    "name": "review_outside_draft",
    "description": "Return the review of the outside draft: a verdict, section-by-section comments, and the points a Partner must decide.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["Ready with minor edits", "Needs substantial work", "Not safe to rely on as it stands"]},
            "summary": {"type": "string", "description": "Three to six sentences: is the conclusion right, how well is it supported, and what matters most to fix."},
            "strengths": {"type": "array", "items": {"type": "string"}, "description": "What is sound and should be kept."},
            "weaknesses": {"type": "array", "items": {"type": "string"}, "description": "The main weaknesses, most serious first."},
            "missing_issues": {"type": "array", "items": {"type": "string"}, "description": "Issues the draft should deal with and does not."},
            "counter_arguments": {"type": "array", "items": {"type": "string"}, "description": "ZIMRA's (or the other side's) strongest points that the draft does not answer."},
            "sections": {
                "type": "array", "description": "One entry per section of the draft that has anything to say about it; `index` is the section's number as given (starting at 0).",
                "items": {"type": "object", "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["Sound", "Needs attention", "Unsupported"]},
                    "comments": {"type": "array", "items": {"type": "object", "properties": {
                        "type": {"type": "string", "enum": list(COMMENT_TYPES)},
                        "severity": {"type": "string", "enum": list(SEVERITIES)},
                        "quote": {"type": "string", "description": "The few words of the draft this comment is about (at most 15 words, copied exactly), or empty."},
                        "text": {"type": "string", "description": "What is wrong or missing, and why."},
                        "suggested_fix": {"type": "string", "description": "How to put it right, in a sentence or two. Empty if none."},
                        "library_id": {"type": "integer", "description": "Update id of the Firm Library excerpt that supports or contradicts this point, if any."},
                    }, "required": ["type", "severity", "text"]}},
                }, "required": ["index", "verdict", "comments"]},
            },
            "citations": {"type": "array", "items": {"type": "object", "properties": {"update_id": {"type": "integer"}, "used_for": {"type": "string"}}, "required": ["update_id", "used_for"]}},
            "missing_information": {"type": "array", "items": {"type": "string"}, "description": "Facts or documents needed to settle the open points."},
            "reviewer_notes": {"type": "array", "items": {"type": "string"}, "description": "Decisions the Partner has to take."},
        },
        "required": ["verdict", "summary", "sections", "citations", "missing_information", "reviewer_notes"],
    },
}

REVIEW_RULES = (
    "You are a senior tax partner at Neverlank Chartered Accountants (Zimbabwe) reviewing a draft report that was written OUTSIDE this app "
    "(for example researched with an AI assistant). The Partner will decide what goes to the client or to ZIMRA, so your job is a rigorous, fair, "
    "specific review: say what is sound, and find what is wrong, unsupported, missing or risky. Do not rewrite the draft.\n\n"
    "HOW TO REVIEW\n"
    "1. LAW: take each statement of law in turn and test it against the LIBRARY EXCERPTS. Say whether it is supported by an excerpt (give its Update "
    "id), unsupported by anything supplied, contradicted by an excerpt, or overtaken by a later instrument (look at each excerpt's 'Check:' line). "
    "A section number, SI or case in the draft that no excerpt contains is NOT confirmed: say so. You may use your own knowledge of Zimbabwean tax law "
    "to say where the law probably lies, but mark it 'not in the Firm Library - verify' and never present it as confirmed. Outside AI assistants often "
    "invent or misnumber sections and cite repealed or foreign law: look for that.\n"
    "2. FACTS AND FIGURES: test every fact, date and amount against ENGAGEMENT RECORD, UPLOADED DOCUMENTS, ZIMRA MATTER and PARTNER'S INSTRUCTIONS. "
    "Flag anything that is not there, that differs, or that has been assumed. Do not accept a figure just because it is stated confidently.\n"
    "3. REASONING: check the logic from facts to conclusion. Look for leaps, circular reasoning, one-sided argument, an authority stretched beyond what "
    "it says, missing counter-arguments (what ZIMRA would say), missing issues (other tax heads, time limits, penalties, procedure), and a conclusion "
    "stronger than the analysis supports. If the PARTNER'S INSTRUCTIONS state a position, check the draft takes it and say whether it is sustainable.\n"
    "4. CALIBRATE: High = would mislead the client or ZIMRA, rests on wrong or unconfirmed law or a wrong fact, or reverses the answer. Medium = a gap "
    "or weakness that should be fixed before signing. Low = polish. Do not pad: a few precise comments beat many vague ones, but do not miss a High. "
    "Praise what is genuinely sound (strengths) so it is kept.\n"
    "5. Every comment names the exact words it concerns in `quote` (copied exactly, at most 15 words) and says what to do in `suggested_fix`.\n"
    "6. IDENTITY: the client's name, TIN and VAT number appear as tokens {client_name}, {client_tin}, {client_vat}; use the tokens or 'the client'.\n"
    "7. Return everything through the tool. Sections are numbered from 0 as in the draft."
)

REVIEW_ANALYSIS_RULES = (
    "You are a senior tax partner at Neverlank Chartered Accountants (Zimbabwe) preparing to review a draft written outside the app. "
    "Do not write the review yet. Write your working analysis in plain text, using only the facts in the material supplied (never invent a fact, "
    "figure, date or name; list what is missing). First reach your OWN view of the matter from the facts and the Library excerpts, without deferring "
    "to the draft: the question, the facts, the law (with the Update id of each excerpt), the application, ZIMRA's likely response, your conclusion and "
    "confidence. Then test the draft against it, section by section: where its law is unsupported, contradicted or outdated; where its facts or figures "
    "are not in the record; where its reasoning leaps or ignores the counter-argument; what it leaves out; and where it is right. "
    "Aim for 1,500 to 3,000 words of dense, specific reasoning."
)

_HEAD_NUM = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,3}[.)]?|[A-Z][.)]|[IVX]{1,5}[.)]|(?:part|section|annexure|appendix)\s+[A-Z0-9]+[.:]?)\s+\S", re.I)


def _clean_import(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]*\*\*|__|^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^[ \t]*[•●▪‣]\s*", "- ", text, flags=re.M)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _reflow(text):
    """Join hard-wrapped lines of a PDF extract, then make every remaining line its own paragraph."""
    out = []
    for line in [l.strip() for l in text.split("\n") if l.strip()]:
        if out and len(out[-1]) >= 60 and not out[-1].endswith((".", ":", ";", "?", "!")) and line[:1].islower():
            out[-1] += " " + line
        else:
            out.append(line)
    return "\n\n".join(out)


def _sentence_pieces(para, size=3000):
    """A very long paragraph cut at sentence ends into pieces of about `size` characters."""
    if len(para) <= size * 1.3:
        return [para]
    pieces, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", para):
        if cur and len(cur) + len(sent) > size:
            pieces.append(cur.strip())
            cur = ""
        cur += sent + " "
    if cur.strip():
        pieces.append(cur.strip())
    return pieces


def split_draft(text):
    """[{heading, text}] from an outside draft. A short line standing alone (numbered, ALL CAPS or Title-like, no full stop) starts a section;
    with no headings the text is cut into parts at paragraph breaks. The wording itself is never changed."""
    text = _clean_import(text)
    if not text:
        return []
    if text.count("\n\n") < max(2, text.count("\n") // 6):      # a Word or PDF extract (or a paste) with one line per paragraph or hard-wrapped lines
        text = _reflow(text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    def heading_of(block):
        lines = block.split("\n")
        first = lines[0].strip()
        if len(lines) > 1 and not (len(first) <= 90 and not first.endswith((".", ",", ";", ":"))):
            return None, block
        if len(first) > 100 or len(first.split()) > 14 or first.endswith((".", ",", ";", "?")):
            return None, block
        letters = [c for c in first if c.isalpha()]
        is_caps = len(letters) >= 4 and first.upper() == first
        numbered = bool(_HEAD_NUM.match(first))
        titleish = len(first.split()) <= 9 and first[:1].isupper() and sum(w[:1].isupper() for w in first.split() if len(w) > 3) >= max(1, len([w for w in first.split() if len(w) > 3]) // 2)
        if len(lines) == 1:
            if is_caps or numbered or (titleish and len(first.split()) <= 8):
                return first.rstrip(":").strip(), ""
            return None, block
        if is_caps or numbered or first.endswith(":") is False and titleish and len(first.split()) <= 8:
            return first.rstrip(":").strip(), "\n".join(lines[1:]).strip()
        return None, block

    sections, cur = [], None
    for para in paras:
        head, rest = heading_of(para)
        if head is not None:
            cur = {"heading": re.sub(r"^\d{1,2}(?:\.\d{1,2}){0,3}[.)]?\s+", "", head)[:160] or head[:160], "text": rest}
            sections.append(cur)
        elif cur is None:
            cur = {"heading": "Opening", "text": para}
            sections.append(cur)
        else:
            cur["text"] = (cur["text"] + "\n\n" + para).strip()
    sections = [x for x in sections if x["heading"] or x["text"]]
    if len(sections) > 2 and not sections[0]["text"]:     # a bare title line at the top is not a section
        sections = sections[1:]
    # a lone title line followed by nothing but a heading-looking one is not worth a section of its own
    if len(sections) == 1 or sum(1 for x in sections if x["text"]) < 2:
        sections, chunk, size = [], [], 0
        for para in [x for p0 in paras for x in _sentence_pieces(p0)]:
            chunk.append(para)
            size += len(para)
            if size >= 3000:
                sections.append({"heading": f"Part {len(sections) + 1}", "text": "\n\n".join(chunk)})
                chunk, size = [], 0
        if chunk:
            sections.append({"heading": f"Part {len(sections) + 1}" if sections else "Imported draft", "text": "\n\n".join(chunk)})
    return sections[:MAX_SECTIONS]


def _empty_comment(kind, severity, text, fix="", quote="", library_id=None, source="app"):
    return {"type": kind if kind in COMMENT_TYPES else "reasoning", "severity": severity if severity in SEVERITIES else "Medium", "quote": quote[:200],
            "text": text[:900], "fix": fix[:600], "library_id": library_id, "source": source, "resolved": False}


def auto_checks(sections, corpus):
    """{section index: [comments]} from the app's own checks, no AI: figures and legal references the file and Library never mention."""
    out = {}
    for i, sec in enumerate(sections):
        text = _unprotect(_protect(sec.get("text") or ""))
        _t, figs = zimra_ai.flag_figures(text, corpus)
        _t, refs = zimra_ai.flag_legal_refs(text, corpus)
        found = []
        if figs:
            found.append(_empty_comment("figure", "Medium", "Figure(s) not found in the engagement file, the documents you ticked or your instructions: " +
                                        ", ".join(dict.fromkeys(figs)) + ". Confirm where each comes from.", "Trace each figure to the accounts, a document or the client; remove it if it cannot be sourced."))
        if refs:
            found.append(_empty_comment("law", "High", "Legal reference(s) not found in the Firm Library or the file: " + ", ".join(dict.fromkeys(refs)) +
                                        ". Outside drafts often misnumber or invent provisions, so this is unconfirmed.",
                                        "Check each against the instrument itself (file it in the Firm Library if it is genuine) or remove it."))
        if found:
            out[i] = found
    return out


def _numbered_draft(sections, client):
    return "\n\n".join(f"### SECTION {i}: {zimra_ai.redact(s['heading'], client)}\n{zimra_ai.redact(s['text'], client)}" for i, s in enumerate(sections))


def _draft_pairs(report, extra, sections):
    """Firm Library passages for the whole matter plus the best few for each section of the draft, without repeats."""
    pairs = list(library_pairs(library_query(report, extra + " " + " ".join(s["heading"] for s in sections))))
    seen = {(u.id, getattr(c, "id", None)) for u, c in pairs}
    for sec in sections[:MAX_SECTIONS]:
        for u, c in library_pairs((sec["heading"] + " " + sec["text"])[:900], limit=4):
            key = (u.id, getattr(c, "id", None))
            if key not in seen and len(pairs) < 34:
                seen.add(key)
                pairs.append((u, c))
    return pairs


def open_high_comments(report):
    """Unresolved High-severity review comments still on the report."""
    d = report.data
    return sum(1 for lst in (d.get("comments") or {}).values() for c in lst if c.get("severity") == "High" and not c.get("resolved"))


def review_draft(report, extra=""):
    """(ok, message): check an outside draft against the Firm Library and the engagement record and attach comments to its sections.
    The partner's wording is never changed."""
    d = report.data
    sections = d["sections"]
    if not d.get("original"):
        d["original"] = [dict(x) for x in sections]
    client = report.engagement.client
    pairs = _draft_pairs(report, extra, sections)
    docs = document_texts(report)
    case_text = zimra_matter_facts(report)
    corpus = source_corpus(report, extra, pairs, docs, case_text)
    comments = {str(i): v for i, v in auto_checks(sections, corpus).items()}
    d["considered"] = [{"id": u.id, "title": u.title, "type": u.type_label} for u in {u.id: u for u, _c in pairs}.values()]

    def save_checks_only(note):
        d["comments"] = comments
        d.pop("overall", None)
        report.set_data(d)
        report.ai_used = False
        report.ai_note = note
        report.status = "Draft"
        return False, note

    if not is_configured():
        return save_checks_only("AI is not switched on (no ANTHROPIC_API_KEY), so only the automatic checks ran: figures and legal references the file and Library never mention.")
    base = ["ENGAGEMENT RECORD\n" + zimra_ai.redact(engagement_facts(report.engagement), client)]
    if docs:
        base.append("UPLOADED DOCUMENTS\n" + "\n\n".join(zimra_ai.redact(f"--- {n} ---\n{t}", client) for n, t in docs))
    if case_text:
        base.append("ZIMRA MATTER\n" + zimra_ai.redact(case_text, client))
    ins = ((report.instructions or "") + (("\n\nFURTHER INSTRUCTION FOR THIS REVIEW: " + extra) if extra else "")).strip()
    base.append("PARTNER'S INSTRUCTIONS (the facts, the question and the position wanted)\n" + (zimra_ai.redact(ins, client) if ins else "(none given)"))
    base.append(("LIBRARY EXCERPTS (the firm's filed legislation and material - the only law you can confirm)\n\n" + excerpt_block(pairs)) if pairs
                else "LIBRARY EXCERPTS\n(No filed material matched. No legal statement in the draft can be confirmed from the Library.)")
    base.append(f"THE OUTSIDE DRAFT TO REVIEW ({KINDS[report.kind]['label']}), sections numbered from 0\n\n" + _numbered_draft(sections, client))
    material = "\n\n=====\n\n".join(base)
    analysis, a_err = _call_text(REVIEW_ANALYSIS_RULES, material + f"\n\n=====\n\nToday's date: {_d(date.today())}. Write your analysis now.")
    msg = material + (("\n\n=====\n\nYOUR PRIOR ANALYSIS (carry its findings into the review)\n" + analysis) if analysis else "") + \
        f"\n\n=====\n\nToday's date: {_d(date.today())}. Review the draft now."
    data, err = _call_model(REVIEW_RULES, msg, REVIEW_TOOL)
    if err:
        return save_checks_only("The AI review failed (" + err[:200] + "), so only the automatic checks ran. Try again.")
    shown = {u.id for u, _c in pairs}
    cited_extra = []
    dropped = 0
    for entry in data.get("sections") or []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(sections)):
            continue
        bucket = comments.setdefault(str(idx), [])
        for c in (entry.get("comments") or [])[:8]:
            if not isinstance(c, dict) or not str(c.get("text") or "").strip():
                continue
            lid = c.get("library_id")
            try:
                lid = int(lid) if lid not in (None, "", 0) else None
            except (TypeError, ValueError):
                lid = None
            if lid is not None and lid not in shown:
                lid, dropped = None, dropped + 1
            if lid is not None:
                cited_extra.append({"update_id": lid, "used_for": "Checked against this passage in the review"})
            bucket.append(_empty_comment(
                str(c.get("type") or "reasoning"), str(c.get("severity") or "Medium"),
                zimra_ai.restore_tokens(str(c.get("text")).strip(), client), zimra_ai.restore_tokens(str(c.get("suggested_fix") or "").strip(), client),
                str(c.get("quote") or "").strip(), lid, "AI"))
        verdicts = d.setdefault("verdicts", {})
        verdicts[str(idx)] = entry.get("verdict") if entry.get("verdict") in ("Sound", "Needs attention", "Unsupported") else "Needs attention"
    for k in list(comments):
        if not comments[k]:
            comments.pop(k)
    d["comments"] = comments
    rest = lambda key, n=12: [zimra_ai.restore_tokens(x, client) for x in zimra_ai.clean_list(data.get(key), limit=n, width=600)]
    d["overall"] = {"verdict": data.get("verdict") if data.get("verdict") in ("Ready with minor edits", "Needs substantial work", "Not safe to rely on as it stands") else "Needs substantial work",
                    "summary": zimra_ai.restore_tokens(str(data.get("summary") or "").strip()[:1500], client),
                    "strengths": rest("strengths"), "weaknesses": rest("weaknesses"), "missing_issues": rest("missing_issues"), "counter_arguments": rest("counter_arguments")}
    sources, _considered, dropped_cites = zimra_ai.build_sources(list(data.get("citations") or []) + cited_extra, pairs)
    d["sources"] = sources
    d["missing"] = zimra_ai.clean_list(data.get("missing_information"))
    d["notes"] = zimra_ai.clean_list(data.get("reviewer_notes"))
    d["analysis"] = zimra_ai.restore_tokens(analysis, client) if analysis else ""
    flags = []
    if a_err and not analysis:
        flags.append("The AI's reasoning step did not complete (" + str(a_err)[:160] + "), so this review may be less thorough. Check it again.")
    if dropped or dropped_cites:
        flags.append(f"{dropped + len(dropped_cites)} pointer(s) to Library material that was not supplied were removed.")
    d["flags"] = flags
    report.set_data(d)
    total = sum(len(v) for v in comments.values())
    high = sum(1 for v in comments.values() for c in v if c["severity"] == "High")
    report.ai_used = True
    report.ai_note = f"Draft reviewed against the engagement record and {len(pairs)} Firm Library passage(s): {total} comment(s), {high} high-priority."
    report.status = "Draft"
    return True, report.ai_note


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


# ------------------------------------------------------------------ running a draft in the background

def _finish(report, ok, msg):
    report.status = "Draft"
    report.updated_at = datetime.utcnow()
    if not ok and not report.ai_note:
        report.ai_note = msg


def _run_job(app, report_id, extra):
    with app.app_context():
        try:
            report = TaxAdvisoryReport.query.get(report_id)
            if report is None:
                return
            ok, msg = generate(report, extra)
            _finish(report, ok, msg)
            db.session.commit()
        except Exception as exc:                       # never leave a report stuck on 'Writing'
            db.session.rollback()
            report = TaxAdvisoryReport.query.get(report_id)
            if report is not None:
                report.status = "Draft"
                report.ai_note = ("The AI draft failed: " + str(exc))[:390]
                if not report.data["sections"]:
                    report.set_data(skeleton(report))
                db.session.commit()
        finally:
            db.session.remove()


def start_draft(report, extra=""):
    """Write the draft now (tests, or no AI switched on) or hand it to a background thread and return at once. Returns (done_now, message)."""
    if not BACKGROUND or not is_configured():
        ok, msg = generate(report, extra)
        _finish(report, ok, msg)
        db.session.commit()
        return True, msg
    import threading
    report.status = "Writing"
    report.updated_at = datetime.utcnow()
    db.session.commit()
    t = threading.Thread(target=_run_job, args=(current_app._get_current_object(), report.id, extra), daemon=True)
    t.start()
    return False, "The AI is writing the draft."


def is_writing(report):
    """True while a background draft is running; a draft left 'Writing' for too long (the server restarted) counts as lost."""
    if report.status != "Writing":
        return False
    age = (datetime.utcnow() - (report.updated_at or datetime.utcnow())).total_seconds()
    return age < STALE_AFTER


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
    db.session.commit()
    done, msg = start_draft(report)
    if done:
        flash(msg + " Nothing is final until a Partner marks it reviewed.", "success" if report.ai_used else "warning")
    else:
        flash("The AI is writing the first draft. This page refreshes by itself when it is ready (usually one to three minutes).", "info")
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/engagement/<int:engagement_id>/import", methods=["POST"])
@login_required
def import_draft(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    import engagements as eng
    eng._ensure_engagement_access(engagement)
    back = redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    kind = request.form.get("kind", "")
    if kind not in KINDS:
        flash("Choose the kind of report this draft is.", "danger")
        return back
    text = (request.form.get("draft_text") or "").strip()
    upload = request.files.get("draft_file")
    if not text and upload is not None and upload.filename:
        ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
        if ext in ("txt", "md"):
            text = upload.read().decode("utf-8", errors="ignore")
        elif ext in ("docx", "pdf"):
            import tempfile
            import file_text_extraction
            with tempfile.NamedTemporaryFile(suffix="." + ext, delete=False) as tmp:
                upload.save(tmp.name)
            try:
                text, status, _pages = file_text_extraction.extract_text_for_file(tmp.name, ext)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            if not (text or "").strip():
                flash("No text could be read from that file (a scanned PDF has none). Paste the text instead.", "danger")
                return back
        else:
            flash("Upload a Word (.docx), PDF or text file, or paste the draft in the box.", "danger")
            return back
    text = _clean_import(text)
    if len(text) < 200:
        flash("Paste or upload the draft report to review (it looks empty or too short).", "danger")
        return back
    cut = len(text) > MAX_IMPORT_CHARS
    sections = split_draft(text[:MAX_IMPORT_CHARS])
    doc_ids = [int(x) for x in request.form.getlist("doc_ids") if x.isdigit()]
    case_id = request.form.get("zimra_case_id", type=int)
    report = TaxAdvisoryReport(engagement_id=engagement_id, kind=kind, created_by_id=current_user.id,
                               title=(request.form.get("title") or "").strip()[:200] or KINDS[kind]["title"],
                               addressee=(request.form.get("addressee") or "").strip()[:300] or None,
                               reference=(request.form.get("reference") or "").strip()[:100] or None,
                               instructions=(request.form.get("instructions") or "").strip() or None,
                               doc_ids_json=json.dumps(doc_ids), zimra_case_id=case_id if (case_id and kind == "zimra_submission") else None)
    report.set_data({"origin": "imported", "sections": sections, "original": [dict(x) for x in sections], "risk_rows": [], "sources": [], "missing": [], "notes": [], "flags":
                     (["The draft was longer than " + f"{MAX_IMPORT_CHARS:,}" + " characters, so the end was left out of the review."] if cut else [])})
    db.session.add(report)
    db.session.commit()
    done, msg = start_draft(report)
    if done:
        flash(msg, "success" if report.ai_used else "warning")
    else:
        flash("The AI is reviewing the draft against the Firm Library and the engagement record. This page refreshes by itself (usually two to five minutes).", "info")
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/<int:report_id>/apply/<int:idx>", methods=["POST"])
@login_required
def apply_comments(report_id, idx):
    report = _report_or_404(report_id)
    if _busy(report):
        return redirect(url_for("taxreports.editor", report_id=report.id))
    d = report.data
    open_c = [c for c in (d.get("comments") or {}).get(str(idx), []) if not c.get("resolved")]
    if not open_c:
        flash("There are no open comments on that section.", "info")
        return redirect(url_for("taxreports.editor", report_id=report.id) + f"#sec{idx}")
    instruction = ("Revise this section so that it deals with each of these review comments, keeping everything that is sound and the author's own wording where it is "
                   "right:\n" + "\n".join(f"- ({COMMENT_TYPES.get(c['type'], c['type'])}, {c['severity']}) {c['text']}" + (f" Fix: {c['fix']}" if c.get("fix") else "") for c in open_c))
    ok, msg = rewrite_section(report, idx, instruction)
    if ok:
        d = report.data
        for c in d.get("comments", {}).get(str(idx), []):
            if not c.get("resolved"):
                c["resolved"] = True
                c["note"] = "Applied by AI rewrite"
        if str(idx) in d.get("verdicts", {}):
            d["verdicts"][str(idx)] = "Needs attention" if d["verdicts"][str(idx)] == "Unsupported" else d["verdicts"][str(idx)]
        report.set_data(d)
        _touch(report)
        db.session.commit()
        flash(msg + " Read it: the comments on this section are marked dealt with.", "success")
    else:
        flash(msg, "warning")
    return redirect(url_for("taxreports.editor", report_id=report.id) + f"#sec{idx}")


@taxreports_bp.route("/<int:report_id>/restore-original", methods=["POST"])
@login_required
def restore_original(report_id):
    report = _report_or_404(report_id)
    if _busy(report):
        return redirect(url_for("taxreports.editor", report_id=report.id))
    d = report.data
    if not d.get("original"):
        abort(404)
    d["sections"] = [dict(x) for x in d["original"]]
    d["comments"], d["verdicts"] = {}, {}
    d["flags"] = ["The draft was put back as it was brought in; run 'Check it again' to get fresh comments."]
    report.set_data(d)
    _touch(report)
    db.session.commit()
    flash("The draft is back exactly as you brought it in. Use 'Check it again' for fresh comments.", "success")
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/<int:report_id>/comment/<int:idx>/<int:cidx>", methods=["POST"])
@login_required
def comment_toggle(report_id, idx, cidx):
    report = _report_or_404(report_id)
    if _busy(report):
        return redirect(url_for("taxreports.editor", report_id=report.id))
    d = report.data
    try:
        c = d["comments"][str(idx)][cidx]
    except (KeyError, IndexError, TypeError):
        abort(404)
    c["resolved"] = not c.get("resolved")
    c["note"] = ("Dealt with by " + (current_user.name or "a user")) if c["resolved"] else ""
    report.set_data(d)
    db.session.commit()
    return redirect(url_for("taxreports.editor", report_id=report.id) + f"#sec{idx}")


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
    return render_template("taxreports/editor.html", report=report, d=report.data, writing=is_writing(report), lost=(report.status == "Writing" and not is_writing(report)), kind=KINDS[report.kind], q=q, results=results,
                           weights=weights, configured=is_configured(), can_review=_can_review(current_user), engagement=report.engagement,
                           cited_order=_cites_order(report.data["sections"]))


def _busy(report):
    """True (after telling the person) while the AI is still writing this report, so nothing is edited under it."""
    if is_writing(report):
        flash("The AI is still writing this report. Wait for it to finish (this page refreshes by itself).", "info")
        return True
    return False


def _touch(report):
    report.updated_at = datetime.utcnow()
    report.status = "Draft"
    report.reviewed_by_id = None
    report.reviewed_at = None


@taxreports_bp.route("/<int:report_id>/save", methods=["POST"])
@login_required
def save(report_id):
    report = _report_or_404(report_id)
    if _busy(report):
        return redirect(url_for("taxreports.editor", report_id=report.id))
    d = report.data
    report.title = (request.form.get("title") or "").strip()[:200] or report.title
    report.addressee = (request.form.get("addressee") or "").strip()[:300] or None
    report.reference = (request.form.get("reference") or "").strip()[:100] or None
    report.instructions = (request.form.get("instructions") or "").strip() or None
    secs, keep_map = [], {}
    n = request.form.get("n_sections", type=int) or len(d["sections"])
    delete = request.form.get("action", "")
    for i in range(n):
        if delete == f"delete_{i}":
            continue
        heading = (request.form.get(f"heading_{i}") or "").strip()[:160]
        text = (request.form.get(f"text_{i}") or "").replace("\r\n", "\n").strip()
        if heading or text:
            secs.append({"heading": heading, "text": text})
            keep_map[i] = len(secs) - 1
    if d.get("comments") or d.get("verdicts"):
        d["comments"] = {str(keep_map[int(k)]): v for k, v in (d.get("comments") or {}).items() if k.isdigit() and int(k) in keep_map}
        d["verdicts"] = {str(keep_map[int(k)]): v for k, v in (d.get("verdicts") or {}).items() if k.isdigit() and int(k) in keep_map}
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
    if is_writing(report):
        flash("The AI is already writing this report.", "info")
        return redirect(url_for("taxreports.editor", report_id=report.id))
    _touch(report)
    done, msg = start_draft(report, (request.form.get("extra") or "").strip())
    if done:
        flash(msg + (" The earlier wording is kept: use 'Restore the previous draft' if you prefer it." if report.ai_used and report.data.get("previous") else ""),
              "success" if report.ai_used else "warning")
    else:
        flash("The AI is writing a new draft. Your current wording is kept and can be restored afterwards. This page refreshes when it is ready.", "info")
    return redirect(url_for("taxreports.editor", report_id=report.id))


@taxreports_bp.route("/<int:report_id>/restore", methods=["POST"])
@login_required
def restore(report_id):
    report = _report_or_404(report_id)
    if _busy(report):
        return redirect(url_for("taxreports.editor", report_id=report.id))
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
    if _busy(report):
        return redirect(url_for("taxreports.editor", report_id=report.id))
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
    if _busy(report):
        return redirect(url_for("taxreports.editor", report_id=report.id))
    if not _can_review(current_user):
        abort(403)
    if request.form.get("undo"):
        report.status, report.reviewed_by_id, report.reviewed_at = "Draft", None, None
        flash("Marked as a draft again.", "info")
    else:
        if report.open_placeholders:
            flash(f"{report.open_placeholders} [CONFIRM: ...] placeholder(s) are still in the report. Complete or remove them before marking it reviewed.", "danger")
            return redirect(url_for("taxreports.editor", report_id=report.id))
        if open_high_comments(report):
            flash(f"{open_high_comments(report)} high-priority review comment(s) are still open. Deal with each (apply it, fix the wording, or mark it dealt with) before marking the report reviewed.", "danger")
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
