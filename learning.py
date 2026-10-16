"""Firm Learning - the app's memory of what works, kept by the firm and approved by a Partner.

The idea in one line: the app watches how the firm's people correct and use its AI features, turns repeated corrections
into short, anonymised LESSONS, and - only after a Partner or Admin approves each one - feeds them back into the next
draft, answer or estimate. Nothing is retrained and nothing changes silently.

* A lesson is proposed by a person (anyone) or by an analysis of edits (a Partner presses "Suggest lessons" on an
  approved letter). It starts as *Proposed* and affects nothing.
* A Partner/Admin approves, edits, rejects or later retires it. Every step is logged.
* Approved lessons are handed to the AI as "firm standards" for the matching area (for example ZIMRA letters of a given
  template and tax head), or - for a verified answer to a legislation question - served directly.
* Lessons are anonymised before they are stored: client names, TINs, VAT numbers, e-mails and phone numbers are removed.
  They never carry facts about a client and never state law; law still comes only from the filed library.
* A lesson that depends on current law carries a review date and is not served after it.
* The dashboard shows what is being learned, what is used, what people find helpful, and how much of each AI letter draft
  the team keeps.
"""
import difflib
import json
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import (
    FirmLesson, Client, ZimraReply, LESSON_AREAS, LESSON_AREA_LABELS, LESSON_KINDS, LESSON_STATUSES,
)

learning_bp = Blueprint("learning", __name__, url_prefix="/learning")

TAG_KEYS = ("template_key", "tax_head", "case_type", "engagement_type", "industry")
MAX_TITLE, MAX_CONTENT, MAX_EVIDENCE = 250, 2000, 6000
MAX_LESSONS_IN_PROMPT = 8
MAX_PROMPT_CHARS = 5000
LIBRARY_REVIEW_DAYS = 365
FUZZY_QUESTION = 0.9


def is_senior(user=None):
    return getattr(user or current_user, "role", None) in ("partner", "admin")


# ------------------------------------------------------------------ tags

def parse_tags(text):
    """'template_key=objection, info_response; tax_head=VAT' -> {'template_key': ['objection', 'info_response'], 'tax_head': ['VAT']}"""
    tags = {}
    for part in re.split(r"[;\n]+", text or ""):
        if "=" not in part:
            continue
        key, _, values = part.partition("=")
        key = key.strip().lower()
        vals = [v.strip() for v in values.split(",") if v.strip()]
        if key in TAG_KEYS and vals:
            tags[key] = vals
    return tags


def tags_text(tags):
    return "; ".join(f"{k}={', '.join(v)}" for k, v in (tags or {}).items())


def clean_tags(tags):
    out = {}
    for key, vals in (tags or {}).items():
        if key in TAG_KEYS:
            vals = [str(v).strip() for v in (vals if isinstance(vals, list) else [vals]) if str(v).strip()]
            if vals:
                out[key] = vals[:6]
    return out


# ----------------------------------------------------------- anonymising

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+263|\(?0\)?)\s?\d{2,3}[\s\-]?\d{3}[\s\-]?\d{3,4}(?!\d)")
_TIN_RE = re.compile(r"(?<![\d,.])\d{10}(?![\d,.])")


def _client_name_pattern():
    import zimra_ai
    names = set()
    for (name,) in db.session.query(Client.name).all():
        for variant in zimra_ai._name_variants(name):
            if len(variant) >= 5:
                names.add(variant)
    if not names:
        return None
    return re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)), re.I)


def anonymise(text, client=None, officer=None):
    """Remove anything that identifies a client or a person from text that is about to become firm knowledge."""
    import zimra_ai
    out = text or ""
    if client is not None:
        out = zimra_ai.redact(out, client, officer)
    pattern = _client_name_pattern()
    if pattern is not None:
        out = pattern.sub("[client]", out)
    out = out.replace("{client_name}", "[client]").replace("{client_tin}", "[TIN]").replace("{client_vat}", "[VAT no.]")
    out = out.replace("the ZIMRA officer", "the ZIMRA officer")
    out = _EMAIL_RE.sub("[email]", out)
    out = _PHONE_RE.sub("[phone]", out)
    out = _TIN_RE.sub("[TIN]", out)
    return out


# ------------------------------------------------------------- register

def _norm(text):
    return " ".join((text or "").lower().split())


def propose(area, kind, title, content, user, tags=None, evidence=None, source_kind="manual", source_id=None,
            client=None, officer=None):
    """Create a Proposed lesson. Returns (lesson, None) or (None, reason). Does not commit."""
    if area not in LESSON_AREA_LABELS:
        return None, "Choose what the lesson is about."
    if kind not in LESSON_KINDS:
        return None, "Choose the kind of lesson."
    title = anonymise((title or "").strip(), client, officer)[:MAX_TITLE]
    content = anonymise((content or "").strip(), client, officer)[:MAX_CONTENT]
    if not title or not content:
        return None, "Give the lesson a title and say what the firm has learned."
    existing = FirmLesson.query.filter(FirmLesson.area == area, FirmLesson.status.in_(("Proposed", "Approved"))).all()
    for e in existing:
        if _norm(e.title) == _norm(title) and _norm(e.content) == _norm(content):
            return e, "duplicate"
    lesson = FirmLesson(
        area=area, kind=kind, title=title, content=content, tags_json=json.dumps(clean_tags(tags)) if tags else None,
        evidence=anonymise(evidence, client, officer)[:MAX_EVIDENCE] if evidence else None,
        source_kind=source_kind, source_id=source_id, status="Proposed", proposed_by_id=getattr(user, "id", None))
    if kind == "Verified answer" or area == "library":
        lesson.review_due = date.today() + timedelta(days=LIBRARY_REVIEW_DAYS)
    lesson.log(user, "Proposed", f"From {source_kind}" if source_kind else None)
    db.session.add(lesson)
    db.session.flush()
    return lesson, None


def applicable(area, ctx=None, limit=MAX_LESSONS_IN_PROMPT):
    """Approved, in-date lessons for `area` that fit `ctx` (a dict like {'template_key': 'objection', 'tax_head': 'VAT'}),
    best first. A lesson with tags only applies when every tag matches the context; an untagged lesson always applies."""
    ctx = {k: str(v).strip().lower() for k, v in (ctx or {}).items() if v}
    scored = []
    for lesson in FirmLesson.query.filter_by(area=area, status="Approved").all():
        if not lesson.is_servable or lesson.kind == "Verified answer":
            continue
        score, ok = 0, True
        for key, values in lesson.tags.items():
            wanted = {str(v).strip().lower() for v in values}
            if ctx.get(key) in wanted:
                score += 1
            else:
                ok = False
                break
        if not ok:
            continue
        rate = lesson.helpful_rate
        scored.append((score, rate if rate is not None else 0.5, lesson.times_used or 0, lesson.id, lesson))
    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]), reverse=True)
    return [t[4] for t in scored[:limit]]


def render_block(lessons):
    """The text handed to the AI. Each line is labelled with the lesson's id so a reviewer can trace it."""
    lines, used = [], 0
    for l in lessons:
        line = f"[Lesson L{l.id} - {l.kind}] {l.title}: {l.content}"
        if used + len(line) > MAX_PROMPT_CHARS:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def mark_used(lesson_ids):
    now = datetime.utcnow()
    for l in FirmLesson.query.filter(FirmLesson.id.in_(list(lesson_ids or []))).all():
        l.times_used = (l.times_used or 0) + 1
        l.last_used_at = now


def verified_answer(question):
    """An approved, in-date 'Verified answer' lesson whose title is this question (or a very close repeat of it)."""
    normalized = _norm(question)
    if not normalized:
        return None
    best, best_ratio = None, 0.0
    for l in FirmLesson.query.filter_by(area="library", kind="Verified answer", status="Approved").all():
        if not l.is_servable:
            continue
        ratio = 1.0 if _norm(l.title) == normalized else difflib.SequenceMatcher(None, normalized, _norm(l.title)).ratio()
        if ratio > best_ratio:
            best, best_ratio = l, ratio
    return best if best_ratio >= FUZZY_QUESTION else None


# --------------------------------------------- learning from edited letters

def split_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def similarity(a, b):
    return round(difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio(), 3)


def changed_pairs(before, after):
    """[(before_paragraph, after_paragraph)] for each place the final text differs from the AI's draft."""
    b, a = split_paragraphs(before), split_paragraphs(after)
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b, a).get_opcodes():
        if tag == "equal":
            continue
        pairs.append(("\n\n".join(b[i1:i2]) or "(nothing - this was added)", "\n\n".join(a[j1:j2]) or "(removed)"))
    return pairs


EXTRACT_TOOL = {
    "name": "propose_lessons",
    "description": "Propose general, reusable lessons the firm can learn from the corrections a Partner made to an AI draft.",
    "input_schema": {
        "type": "object",
        "properties": {"lessons": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["Wording", "Position", "Avoid"]},
            "title": {"type": "string", "description": "A short name for the lesson, no client details."},
            "content": {"type": "string", "description": "One or two sentences telling a future drafter what to do (or not do) and why. General: no client names, numbers, dates or references; no statements of law."},
            "tags": {"type": "object", "description": "Optional: when this applies, e.g. {\"tax_head\": [\"VAT\"]}. Keys allowed: tax_head, case_type.",
                     "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        }, "required": ["kind", "title", "content"]}}},
        "required": ["lessons"],
    },
}
EXTRACT_SYSTEM = (
    "You help a Zimbabwean firm of chartered accountants learn from corrections. Below are places where a Partner changed "
    "an AI-drafted letter to ZIMRA before approving it (BEFORE = the AI's text, AFTER = the approved text). Identify up to "
    "three GENERAL lessons that would improve future drafts: wording the firm prefers, positions or tone it takes, things "
    "to avoid. Ignore changes that only fill in a [CONFIRM: ...] placeholder with case facts, and ignore fixes of typos. "
    "Never include client names, amounts, dates, references or personal details, and never state or paraphrase law. If "
    "the changes teach nothing general, return no lessons. Return via the propose_lessons tool."
)


def _call_extract(system, user_message):
    """(list of lesson dicts, None) or (None, error). The only place that calls the model for this."""
    import zimra_ai
    try:
        response = zimra_ai._client().messages.create(
            model=zimra_ai.DEFAULT_MODEL, max_tokens=1500, temperature=0.2, system=system, tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": EXTRACT_TOOL["name"]}, messages=[{"role": "user", "content": user_message}])
    except Exception as exc:
        return None, str(exc)[:500]
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == EXTRACT_TOOL["name"]:
            return list((block.input or {}).get("lessons") or []), None
    return None, "The model did not return lessons."


def learn_from_zimra_reply(reply, user):
    """Propose lessons from how a Partner changed an AI-drafted letter. Returns (status, payload):
    'not_ai' / 'no_change' / 'not_configured' / 'error' (payload message) or 'done' (payload list of lessons). Does not commit."""
    import zimra_ai
    ai = reply.ai
    if not ai or not ai.get("generated_body"):
        return "not_ai", None
    pairs = changed_pairs(ai["generated_body"], reply.body)
    if not pairs or similarity(ai["generated_body"], reply.body) >= 0.985:
        return "no_change", None
    if not zimra_ai.is_configured():
        return "not_configured", None
    client, officer = reply.case.client, reply.case.zimra_officer
    text = "\n\n=====\n\n".join(f"BEFORE:\n{b}\n\nAFTER:\n{a}" for b, a in pairs)
    safe = anonymise(text, client, officer)[:MAX_EVIDENCE]
    context = f"Letter type: {reply.template_key}; tax head: {reply.case.tax_head or '-'}; case type: {reply.case.case_type or '-'}."
    found, error = _call_extract(EXTRACT_SYSTEM, f"{context}\n\n{safe}")
    if error:
        return "error", error
    base_tags = {"template_key": [reply.template_key]} if reply.template_key else {}
    made = []
    for item in found or []:
        tags = dict(base_tags)
        tags.update(clean_tags({k: v for k, v in (item.get("tags") or {}).items() if k in ("tax_head", "case_type")}))
        lesson, err = propose("zimra_letters", item.get("kind") if item.get("kind") in LESSON_KINDS else "Wording",
                              item.get("title"), item.get("content"), user, tags=tags, evidence=safe,
                              source_kind="zimra_reply", source_id=reply.id, client=client, officer=officer)
        if lesson is not None and err != "duplicate":
            made.append(lesson)
    ai["learned_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    ai["learned_count"] = len(made)
    reply.set_ai(ai)
    return "done", made


# ----------------------------------------------------------- statistics

def stats():
    lessons = FirmLesson.query.all()
    by_area = {}
    for key, label in LESSON_AREAS:
        mine = [l for l in lessons if l.area == key]
        used = [l for l in mine if l.status == "Approved"]
        rated = [l.helpful_rate for l in used if l.helpful_rate is not None]
        by_area[key] = {
            "label": label, "proposed": sum(1 for l in mine if l.status == "Proposed"),
            "approved": len(used), "rejected": sum(1 for l in mine if l.status == "Rejected"),
            "retired": sum(1 for l in mine if l.status == "Retired"),
            "uses": sum(l.times_used or 0 for l in used), "helpful": (sum(rated) / len(rated)) if rated else None,
            "due": sum(1 for l in used if l.is_due_for_review),
        }
    sims = []
    for r in ZimraReply.query.filter(ZimraReply.ai_meta.isnot(None)).all():
        ai = r.ai or {}
        if ai.get("similarity") is not None and r.status in ("Approved", "Sent"):
            sims.append(ai["similarity"])
    return {"by_area": by_area, "ai_letters": len(sims), "ai_kept": (sum(sims) / len(sims)) if sims else None,
            "pending": sum(1 for l in lessons if l.status == "Proposed")}


# ----------------------------------------------------------------- routes

def _safe_next(value, fallback):
    return value if value and value.startswith("/") and not value.startswith("//") else fallback


def _senior_only(what):
    if not is_senior():
        abort(403, description=f"Only a Partner or Admin can {what}.")


@learning_bp.route("/")
@login_required
def index():
    area = request.args.get("area", "")
    status = request.args.get("status", "")
    q = FirmLesson.query
    if area in LESSON_AREA_LABELS:
        q = q.filter(FirmLesson.area == area)
    if status in LESSON_STATUSES:
        q = q.filter(FirmLesson.status == status)
    elif not status:
        q = q.filter(FirmLesson.status.in_(("Proposed", "Approved")))
    lessons = q.order_by(FirmLesson.status.desc(), FirmLesson.proposed_at.desc()).all()
    # Proposed first
    lessons.sort(key=lambda l: (l.status != "Proposed",))
    return render_template("learning/index.html", lessons=lessons, stats=stats(), areas=LESSON_AREAS, kinds=LESSON_KINDS,
                           statuses=LESSON_STATUSES, area=area, status=status, is_senior=is_senior())


@learning_bp.route("/<int:lesson_id>")
@login_required
def view(lesson_id):
    lesson = FirmLesson.query.get_or_404(lesson_id)
    return render_template("learning/view.html", lesson=lesson, is_senior=is_senior(), tags_text=tags_text(lesson.tags),
                           areas=LESSON_AREAS, kinds=LESSON_KINDS)


@learning_bp.route("/propose", methods=["POST"])
@login_required
def propose_route():
    f = request.form
    source_kind = f.get("source_kind") or "manual"
    source_id = f.get("source_id", type=int)
    client = officer = None
    if source_kind == "zimra_reply" and source_id:
        reply = ZimraReply.query.get(source_id)
        if reply is not None:
            from zimra import _ensure_access
            _ensure_access(reply.case)
            client, officer = reply.case.client, reply.case.zimra_officer
    lesson, err = propose(f.get("area", ""), f.get("kind", ""), f.get("title"), f.get("content"), current_user,
                          tags=parse_tags(f.get("tags")), evidence=f.get("evidence"), source_kind=source_kind,
                          source_id=source_id, client=client, officer=officer)
    nxt = _safe_next(f.get("next"), url_for("learning.index"))
    if lesson is None:
        flash(err, "danger")
        return redirect(nxt)
    if err == "duplicate":
        flash("The firm already has this lesson on file.", "info")
        return redirect(nxt)
    db.session.commit()
    flash("Thank you - the lesson is with the Partners for a decision. It changes nothing until approved.", "success")
    return redirect(nxt)


def _apply_edit(lesson, form):
    """Partner edits before/after approval. Returns error text or None."""
    title = anonymise(form.get("title", "").strip())[:MAX_TITLE]
    content = anonymise(form.get("content", "").strip())[:MAX_CONTENT]
    if not title or not content:
        return "A lesson needs a title and its content."
    kind = form.get("kind", lesson.kind)
    if kind not in LESSON_KINDS:
        return "Choose the kind of lesson."
    lesson.title, lesson.content, lesson.kind = title, content, kind
    if "tags" in form:
        tags = parse_tags(form.get("tags"))
        lesson.tags_json = json.dumps(tags) if tags else None
    months = form.get("review_months", type=int)
    if months:
        lesson.review_due = date.today() + timedelta(days=30 * max(1, min(months, 60)))
    elif form.get("review_months") == "":
        pass
    return None


@learning_bp.route("/<int:lesson_id>/approve", methods=["POST"])
@login_required
def approve(lesson_id):
    _senior_only("approve a lesson")
    lesson = FirmLesson.query.get_or_404(lesson_id)
    back = redirect(url_for("learning.view", lesson_id=lesson.id))
    if lesson.status not in ("Proposed", "Retired", "Rejected"):
        flash("That lesson is already approved.", "info")
        return back
    error = _apply_edit(lesson, request.form)
    if error:
        flash(error, "danger")
        return back
    was = lesson.status
    lesson.status = "Approved"
    lesson.decided_by_id, lesson.decided_at, lesson.decision_note = current_user.id, datetime.utcnow(), request.form.get("note", "").strip() or None
    lesson.log(current_user, "Approved" if was == "Proposed" else f"Re-approved (was {was})", lesson.decision_note)
    db.session.commit()
    flash("Approved. The app will now use this lesson where it applies.", "success")
    return back


@learning_bp.route("/<int:lesson_id>/edit", methods=["POST"])
@login_required
def edit(lesson_id):
    _senior_only("edit a lesson")
    lesson = FirmLesson.query.get_or_404(lesson_id)
    error = _apply_edit(lesson, request.form)
    if error:
        flash(error, "danger")
    else:
        lesson.log(current_user, "Edited")
        db.session.commit()
        flash("Saved.", "success")
    return redirect(url_for("learning.view", lesson_id=lesson.id))


def _decide(lesson_id, new_status, verb):
    _senior_only(f"{verb} a lesson")
    lesson = FirmLesson.query.get_or_404(lesson_id)
    back = redirect(url_for("learning.view", lesson_id=lesson.id))
    note = request.form.get("note", "").strip()
    if not note:
        flash(f"Say why you are {verb}ing it, so the record is clear.", "danger")
        return back
    if lesson.status == new_status:
        return back
    lesson.status = new_status
    lesson.decided_by_id, lesson.decided_at, lesson.decision_note = current_user.id, datetime.utcnow(), note
    lesson.log(current_user, new_status, note)
    db.session.commit()
    flash(f"Lesson {new_status.lower()}.", "info")
    return back


@learning_bp.route("/<int:lesson_id>/reject", methods=["POST"])
@login_required
def reject(lesson_id):
    return _decide(lesson_id, "Rejected", "reject")


@learning_bp.route("/<int:lesson_id>/retire", methods=["POST"])
@login_required
def retire(lesson_id):
    return _decide(lesson_id, "Retired", "retire")


@learning_bp.route("/<int:lesson_id>/feedback", methods=["POST"])
@login_required
def feedback(lesson_id):
    lesson = FirmLesson.query.get_or_404(lesson_id)
    verdict = request.form.get("verdict")
    if lesson.status == "Approved" and verdict in ("helpful", "unhelpful"):
        if verdict == "helpful":
            lesson.helpful_count = (lesson.helpful_count or 0) + 1
        else:
            lesson.unhelpful_count = (lesson.unhelpful_count or 0) + 1
        db.session.commit()
        flash("Thanks - noted.", "success")
    return redirect(_safe_next(request.form.get("next"), url_for("learning.view", lesson_id=lesson.id)))
