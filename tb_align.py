"""Align a trial balance whose two years were uploaded separately.

When the current and the prior year come from different files, the same account is often spelled a little differently
("Land & Buildings Accumulated Depreciation" / "Land and Buildings - Accumulated Depreciation", "Ecobank ZWL - 2778" /
"Ecobank GRA ZWL- 5754900002778"), so it sits on two rows: one with only current figures and one with only prior figures.
This finds those pairs and, once the user ticks them, merges each into one row. Nothing is merged without a tick."""
import re

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Engagement, TrialBalance, AuditAdjustmentLine
import financials as fin

tbalign_bp = Blueprint("tbalign", __name__, url_prefix="/engagements")

_SYN = {"acc": "accumulated", "accum": "accumulated", "accumulated": "accumulated", "dep": "depreciation", "deprec": "depreciation",
        "depn": "depreciation", "depreciation": "depreciation", "a": "", "the": "", "account": "", "ac": "", "of": ""}


def name_key(name):
    """A spelling-proof form of an account name: lower case, '&' as 'and', no punctuation, common abbreviations spelled out."""
    s = (name or "").casefold().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = [_SYN.get(w, w) for w in s.split()]
    return " ".join(w for w in words if w)


def _letters(name):
    return re.sub(r"[^a-z]", "", re.sub(r"\d+", " ", (name or "").casefold()))


def _numbers(name):
    return [n for n in re.findall(r"\d{4,}", name or "")]


def _has(line, kind):
    return bool(round(getattr(line, f"{kind}_debit") or 0, 2) or round(getattr(line, f"{kind}_credit") or 0, 2))


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _number_match(a, b):
    """The same bank-style account number: one number is the end of the other (2778 / 5754900002778) and the bank names start alike."""
    na, nb = _numbers(a), _numbers(b)
    for x in na:
        for y in nb:
            if x == y or x.endswith(y) or y.endswith(x):
                la, lb = _letters(a), _letters(b)
                short = min(len(la), len(lb))
                if short >= 4 and _common_prefix(la, lb) >= max(4, short // 2):
                    return x if len(x) < len(y) else y
    # one name carries a short number (the last digits) that the other writes in full
    return None


def candidates(tb):
    """[{cur, pri, why, strength, warn}] pairs of lines that look like one account: the current line has only current figures, the prior
    line only prior figures, and the names agree once spelling is ignored (strong) or share a bank account number (strong when the
    categories agree). A line is offered once."""
    only_cur = [l for l in tb.lines if _has(l, "current") and not _has(l, "prior")]
    only_pri = [l for l in tb.lines if _has(l, "prior") and not _has(l, "current")]
    used, out = set(), []
    pri_by_key = {}
    for p in only_pri:
        pri_by_key.setdefault(name_key(p.account_name), []).append(p)
    for c in only_cur:                       # 1. the same name once spelling is ignored
        group = [p for p in pri_by_key.get(name_key(c.account_name), []) if p.id not in used]
        if len(group) == 1 and name_key(c.account_name):
            p = group[0]
            used.update({c.id, p.id})
            out.append(_pair(c, p, "Same name apart from spelling or punctuation", "strong"))
    for c in only_cur:                       # 2. the same bank account number
        if c.id in used:
            continue
        hits = [p for p in only_pri if p.id not in used and _number_match(c.account_name, p.account_name)]
        if len(hits) == 1:
            p = hits[0]
            used.update({c.id, p.id})
            num = _number_match(c.account_name, p.account_name)
            out.append(_pair(c, p, f"Same account number ending {num[-6:]}", "strong" if c.fs_category == p.fs_category else "check"))
    for c in only_cur:                       # 3. nearly the same words, same IAS 1 category
        if c.id in used:
            continue
        cw = set(name_key(c.account_name).split())
        hits = []
        for p in only_pri:
            if p.id in used or p.fs_category != c.fs_category:
                continue
            pw = set(name_key(p.account_name).split())
            if cw and pw and len(cw & pw) / len(cw | pw) >= 0.75:
                hits.append(p)
        if len(hits) == 1:
            p = hits[0]
            used.update({c.id, p.id})
            out.append(_pair(c, p, "Nearly the same words, same category", "check"))
    return out


def _pair(c, p, why, strength):
    warn = ""
    if c.fs_category != p.fs_category:
        warn = f"The categories differ: {fin.category_label(c.fs_category)} / {fin.category_label(p.fs_category)}. Merged, the account takes the category of the line you keep."
    cs = (c.current_debit or 0) - (c.current_credit or 0)
    ps = (p.prior_debit or 0) - (p.prior_credit or 0)
    if cs * ps < 0:
        warn = (warn + " " if warn else "") + "The balance changes side between the years."
    return {"cur": c, "pri": p, "why": why, "strength": strength, "warn": warn, "key": f"{c.id}-{p.id}"}


def merge_pair(tb, cur_line, pri_line, keep="cur"):
    """Fold the two lines into one: the kept line gets the other's figures, and any code, standard account or category it lacks.
    Adjustment lines naming the removed account are renamed to the kept one. Returns the kept line."""
    keeper, other = (cur_line, pri_line) if keep == "cur" else (pri_line, cur_line)
    for kind in ("current", "prior"):
        if not _has(keeper, kind):
            setattr(keeper, f"{kind}_debit", getattr(other, f"{kind}_debit") or 0.0)
            setattr(keeper, f"{kind}_credit", getattr(other, f"{kind}_credit") or 0.0)
    for f in ("account_code", "coa_account_number", "sub_category"):
        if not (getattr(keeper, f) or "").strip() and (getattr(other, f) or "").strip():
            setattr(keeper, f, getattr(other, f))
    if not keeper.fs_category and other.fs_category:
        keeper.fs_category = other.fs_category
    if other.account_name != keeper.account_name:
        for al in AuditAdjustmentLine.query.join(AuditAdjustmentLine.adjustment).filter_by(trial_balance_id=tb.id, ).all():
            if (al.account_name or "").strip().casefold() == (other.account_name or "").strip().casefold():
                al.account_name = keeper.account_name
    db.session.delete(other)
    return keeper


def _engagement_tb(engagement_id):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    return engagement, TrialBalance.query.filter_by(engagement_id=engagement.id).first()


@tbalign_bp.route("/<int:engagement_id>/trial-balance/align", methods=["GET"])
@login_required
def align_page(engagement_id):
    engagement, tb = _engagement_tb(engagement_id)
    if not tb or not tb.lines:
        flash("There is no trial balance to align yet.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    return render_template("engagements/tb_align.html", engagement=engagement, tb=tb, pairs=candidates(tb), cat_label=fin.category_label)


@tbalign_bp.route("/<int:engagement_id>/trial-balance/align", methods=["POST"])
@login_required
def align_confirm(engagement_id):
    import engagements as eng
    engagement, tb = _engagement_tb(engagement_id)
    if not tb:
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    wanted = set(request.form.getlist("pair"))
    keeps = {k[5:]: v for k, v in request.form.items() if k.startswith("keep_")}
    merged = 0
    for pr in candidates(tb):
        if pr["key"] in wanted:
            merge_pair(tb, pr["cur"], pr["pri"], keeps.get(pr["key"], "cur"))
            merged += 1
    if merged:
        eng._touch_trial_balance(tb)
        db.session.commit()
        flash(f"{merged} pair(s) of accounts merged into one line each. Sign-offs on the trial balance were cleared: review it again.", "success")
    else:
        flash("Nothing was ticked, so nothing was merged.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
