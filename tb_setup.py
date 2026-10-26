"""Adding to the firm's set-up while working in an engagement's Trial Balance tab, without leaving it:

* a new Standard Chart of Accounts account (with the firm's own category inside its IAS 1 line), flagged for a Partner to review;
* the client's remembered mappings (account name -> standard account / IAS 1 category): add, change, delete, paste many;
* trial balance accounts pasted in from Excel, mapped automatically where a mapping is already remembered.

Everything keeps the existing rules: a line's `fs_category` is always one of financials.FS_CATEGORIES (the IAS 1 line the statements are
built from), picking a Standard account is authoritative for the category, and a change to the trial balance resets its sign-offs.
The firm's own categories (models.FSSubCategory) only add a finer heading inside an IAS 1 line, so the statements do not change.
"""
import re

from flask import Blueprint, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import (Engagement, COAMapping, StandardChartOfAccounts, TrialBalance, TrialBalanceLine, FSSubCategory)
import financials as fin
from standard_coa import get_or_create_sub, suggested_numbers, sub_category_choices, new_account_order

tb_setup_bp = Blueprint("tb_setup", __name__, url_prefix="/engagements")

MAX_PASTE_ROWS = 500


def _back(engagement_id, anchor="tb_setup"):
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance") + "#" + anchor)


def _engagement(engagement_id):
    import engagements as eng
    engagement = Engagement.query.get_or_404(engagement_id)
    eng._ensure_engagement_access(engagement)
    return engagement


def _std(number):
    number = (number or "").strip()
    return StandardChartOfAccounts.query.filter_by(account_number=number, is_active=True).first() if number else None


def _norm(name):
    return fin.normalize_account_name(name)


def resolve_category(text):
    """An IAS 1 category code from what a person typed: a code ('admin_expenses'), or its label ('Administrative expenses'), ignoring case;
    a unique start of a label also works. None when it matches nothing or more than one."""
    t = " ".join((text or "").split()).lower()
    if not t:
        return None
    for code, label, *_ in fin.FS_CATEGORIES:
        if t in (code.lower(), label.lower()):
            return code
    hits = [c[0] for c in fin.FS_CATEGORIES if c[1].lower().startswith(t)]
    return hits[0] if len(hits) == 1 else None


def _num(cell):
    """float or None for a pasted cell: '1,234.50', '(500)' (a negative), '-', blank (None means 'not a number')."""
    s = (cell or "").strip().replace(" ", "").replace(" ", "")
    if s in ("", "-"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def apply_mapping_to_lines(tb, mapping):
    """Map the still-unmapped lines of `tb` whose name matches `mapping`. Returns how many changed."""
    if tb is None:
        return 0
    n = 0
    std = _std(mapping.coa_account_number)
    for line in tb.lines:
        if not line.fs_category and _norm(line.account_name) == mapping.account_name:
            line.fs_category = mapping.fs_category
            line.coa_account_number = std.account_number if std else None
            line.sub_category = std.sub_category if std else None
            n += 1
    return n


def _upsert(client_id, name, category, coa_number, user_id):
    from datetime import datetime
    norm = _norm(name)
    m = COAMapping.query.filter_by(client_id=client_id, account_name=norm).first()
    if m is None:
        m = COAMapping(client_id=client_id, account_name=norm)
        db.session.add(m)
    m.fs_category, m.coa_account_number = category, coa_number
    m.updated_by_id, m.updated_at = user_id, datetime.utcnow()
    return m


def _category_for(standard_number, category_code):
    """(fs_category, standard account row or None) - a standard account decides the category."""
    std = _std(standard_number)
    if std:
        return std.fs_category, std
    if category_code and category_code in fin.CATEGORY_BY_CODE:
        return category_code, None
    return None, None


def _touch(tb):
    import engagements as eng
    eng._touch_trial_balance(tb)


# ------------------------------------------------------------------ what the Trial Balance tab needs to draw the new cards
def tab_context(engagement):
    """Extra template variables for the Trial Balance tab."""
    rows = []
    for m in COAMapping.query.filter_by(client_id=engagement.client_id).order_by(COAMapping.account_name).all():
        std = _std(m.coa_account_number) if m.coa_account_number else None
        rows.append({"m": m, "category_label": fin.category_label(m.fs_category), "std_label": (f"{std.account_number} - {std.account_name}" if std else ""),
                     "sub": std.sub_category if std else ""})
    review = StandardChartOfAccounts.query.filter_by(needs_review=True).count()
    return {"client_mappings": rows, "next_numbers": suggested_numbers(), "sub_choices": sub_category_choices(), "accounts_to_review": review}


# ------------------------------------------------------------------ 1. a new Standard Chart account
@tb_setup_bp.route("/<int:engagement_id>/trial-balance/standard-accounts/add", methods=["POST"])
@login_required
def add_standard_account(engagement_id):
    engagement = _engagement(engagement_id)
    category = (request.form.get("fs_category") or "").strip()
    name = " ".join((request.form.get("account_name") or "").split())[:200]
    number = (request.form.get("account_number") or "").strip()[:20]
    if category not in fin.CATEGORY_BY_CODE or not name:
        flash("Give the account a name and choose its IAS 1 category.", "danger")
        return _back(engagement_id)
    if not number:
        number = suggested_numbers().get(category) or ""
    if not number:
        flash("There is no numbered account in that category to follow, so please type an account number.", "danger")
        return _back(engagement_id)
    if StandardChartOfAccounts.query.filter_by(account_number=number).first():
        flash(f"Account number {number} is already in the Standard Chart. Choose another number.", "danger")
        return _back(engagement_id)
    dup = StandardChartOfAccounts.query.filter(db.func.lower(StandardChartOfAccounts.account_name) == name.lower(),
                                               StandardChartOfAccounts.fs_category == category, StandardChartOfAccounts.is_active.is_(True)).first()
    if dup:
        flash(f"'{dup.account_number} - {dup.account_name}' already exists in that category: pick it from the Standard Account list instead.", "warning")
        return _back(engagement_id)
    sub = get_or_create_sub(category, request.form.get("sub_category"), current_user.id)
    acct = StandardChartOfAccounts(account_number=number, account_name=name, fs_category=category, sub_category=sub, order=new_account_order(),
                                   notes=(request.form.get("notes") or "").strip() or f"Added from the Trial Balance of {engagement.title}.",
                                   needs_review=True, added_by_id=current_user.id, added_from_engagement_id=engagement.id)
    db.session.add(acct)
    done = ""
    # optionally map one of this trial balance's accounts to it straight away
    line_id = (request.form.get("apply_line_id") or "").strip()
    if line_id.isdigit():
        line = db.session.get(TrialBalanceLine, int(line_id))
        if line is not None and line.trial_balance.engagement_id == engagement.id:
            line.fs_category, line.coa_account_number, line.sub_category = category, number, sub
            _upsert(engagement.client_id, line.account_name, category, number, current_user.id)
            _touch(line.trial_balance)
            done = f" and '{line.account_name}' is mapped to it"
    db.session.commit()
    flash(f"Standard account {number} - {name} added{done}. It is available in every Standard Account list now, and a Partner will see it marked for review on the Chart of Accounts page.", "success")
    return _back(engagement_id, "tb_setup")


# ------------------------------------------------------------------ 2. the client's remembered mappings
@tb_setup_bp.route("/<int:engagement_id>/trial-balance/mappings/add", methods=["POST"])
@login_required
def add_mapping(engagement_id):
    engagement = _engagement(engagement_id)
    name = " ".join((request.form.get("account_name") or "").split())[:200]
    category, std = _category_for(request.form.get("coa_account_number"), (request.form.get("fs_category") or "").strip())
    if not name or not category:
        flash("Give the account name as it appears in the client's books, and choose a Standard Account or an IAS 1 category.", "danger")
        return _back(engagement_id, "tb_mappings")
    m = _upsert(engagement.client_id, name, category, std.account_number if std else None, current_user.id)
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    n = apply_mapping_to_lines(tb, m)
    if n:
        _touch(tb)
    db.session.commit()
    flash(f"Mapping saved for '{name}'." + (f" {n} account(s) on this trial balance were mapped with it." if n else " It will be used whenever that account name appears in this client's trial balance."), "success")
    return _back(engagement_id, "tb_mappings")


def _mapping_for(engagement, mapping_id):
    m = db.session.get(COAMapping, mapping_id)
    if m is None or m.client_id != engagement.client_id:
        from flask import abort
        abort(404)
    return m


@tb_setup_bp.route("/<int:engagement_id>/trial-balance/mappings/<int:mapping_id>/update", methods=["POST"])
@login_required
def update_mapping(engagement_id, mapping_id):
    engagement = _engagement(engagement_id)
    m = _mapping_for(engagement, mapping_id)
    category, std = _category_for(request.form.get("coa_account_number"), (request.form.get("fs_category") or "").strip())
    if not category:
        flash("Choose a Standard Account or an IAS 1 category.", "danger")
        return _back(engagement_id, "tb_mappings")
    m.fs_category, m.coa_account_number = category, (std.account_number if std else None)
    from datetime import datetime
    m.updated_by_id, m.updated_at = current_user.id, datetime.utcnow()
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    n = apply_mapping_to_lines(tb, m)
    if n:
        _touch(tb)
    db.session.commit()
    flash(f"Mapping for '{m.account_name}' updated. Accounts already mapped on this or earlier trial balances are not changed; it applies from the next import." + (f" {n} unmapped account(s) here were mapped." if n else ""), "success")
    return _back(engagement_id, "tb_mappings")


@tb_setup_bp.route("/<int:engagement_id>/trial-balance/mappings/<int:mapping_id>/delete", methods=["POST"])
@login_required
def delete_mapping(engagement_id, mapping_id):
    engagement = _engagement(engagement_id)
    m = _mapping_for(engagement, mapping_id)
    name = m.account_name
    db.session.delete(m)
    db.session.commit()
    flash(f"Mapping for '{name}' removed. Accounts already on trial balances keep the category they have.", "success")
    return _back(engagement_id, "tb_mappings")


@tb_setup_bp.route("/<int:engagement_id>/trial-balance/mappings/bulk", methods=["POST"])
@login_required
def bulk_mappings(engagement_id):
    """Paste one mapping per line: account name, then a Standard account number OR an IAS 1 category (tab, semicolon or ' => ' between)."""
    engagement = _engagement(engagement_id)
    text = request.form.get("mappings_text") or ""
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    saved = applied = 0
    problems = []
    for i, raw in enumerate([l for l in text.splitlines() if l.strip()][:MAX_PASTE_ROWS], 1):
        parts = [p.strip() for p in re.split(r"\t|;|=>|\|", raw) if p.strip()]
        if len(parts) < 2:
            problems.append(f"line {i} ('{raw.strip()[:40]}'): need the account name and then a Standard account number or category")
            continue
        name, target = " ".join(parts[0].split())[:200], parts[1]
        std = _std(target.split(" - ")[0].strip())
        category = std.fs_category if std else resolve_category(target)
        if not category:
            problems.append(f"line {i} ('{name[:40]}'): '{target[:40]}' is not a Standard account number or an IAS 1 category")
            continue
        m = _upsert(engagement.client_id, name, category, std.account_number if std else None, current_user.id)
        saved += 1
        applied += apply_mapping_to_lines(tb, m)
    if applied:
        _touch(tb)
    db.session.commit()
    if saved:
        flash(f"{saved} mapping(s) saved." + (f" {applied} account(s) on this trial balance were mapped with them." if applied else ""), "success")
    if problems:
        flash("Not saved: " + "; ".join(problems[:6]) + (f"; and {len(problems) - 6} more" if len(problems) > 6 else ""), "warning")
    if not saved and not problems:
        flash("Nothing to save: paste at least one line.", "info")
    return _back(engagement_id, "tb_mappings")


# ------------------------------------------------------------------ 3. trial balance accounts pasted in from Excel
@tb_setup_bp.route("/<int:engagement_id>/trial-balance/lines/bulk-add", methods=["POST"])
@login_required
def bulk_add_lines(engagement_id):
    """Paste rows copied from Excel: Code, Account name, Current Dr, Current Cr, Prior Dr, Prior Cr. Added to the existing accounts (an upload
    replaces them, this does not)."""
    import engagements as eng
    engagement = _engagement(engagement_id)
    text = request.form.get("rows_text") or ""
    rows = [l for l in text.splitlines() if l.strip()]
    if not rows:
        flash("Nothing to add: paste at least one row.", "info")
        return _back(engagement_id, "tb_paste")
    tb = None
    std_sub = {a.account_number: a.sub_category for a in StandardChartOfAccounts.query.all()}
    added = mapped = 0
    problems = []
    for i, raw in enumerate(rows[:MAX_PASTE_ROWS], 1):
        cells = raw.split("\t") if "\t" in raw else (raw.split(";") if ";" in raw else [raw])
        cells = [c.strip() for c in cells]
        if i == 1 and len(cells) > 1 and any(c.lower() in ("account name", "account code", "code", "account", "dr", "cr") or "debit" in c.lower() or "credit" in c.lower() for c in cells):
            continue                                     # a header row copied along with the numbers
        if len(cells) == 1:
            code, name, nums = "", cells[0], []
        elif cells[0] and _num(cells[0]) is None and cells[1] and _num(cells[1]) is not None and cells[1] not in ("", "-") and not re.search(r"[A-Za-z]", cells[1]):
            code, name, nums = "", cells[0], cells[1:5]  # no code column: name first, then the amounts
        else:
            code, name, nums = cells[0], cells[1] if len(cells) > 1 else "", cells[2:6]
        name = " ".join(name.split())[:200]
        if not name:
            problems.append(f"row {i}: no account name")
            continue
        vals = [_num(c) for c in nums] + [0.0] * (4 - len(nums))
        if any(v is None for v in vals):
            problems.append(f"row {i} ('{name[:30]}'): an amount is not a number")
            continue
        if tb is None:
            tb = eng._get_or_create_trial_balance(engagement.id, source="manual")
        m = eng._lookup_coa_mapping(engagement.client_id, name)
        line = TrialBalanceLine(trial_balance_id=tb.id, account_code=code[:50], account_name=name,
                                fs_category=m.fs_category if m else None, coa_account_number=m.coa_account_number if m else None,
                                sub_category=std_sub.get(m.coa_account_number) if m and m.coa_account_number else None,
                                current_debit=vals[0], current_credit=vals[1], prior_debit=vals[2], prior_credit=vals[3])
        db.session.add(line)
        added += 1
        mapped += 1 if m else 0
    if len(rows) > MAX_PASTE_ROWS:
        problems.append(f"only the first {MAX_PASTE_ROWS} rows were read")
    if added:
        eng._touch_trial_balance(tb)
    db.session.commit()
    if added:
        left = added - mapped
        flash(f"{added} account(s) added" + (f"; {mapped} mapped from this client's remembered mappings" if mapped else "") + (f"; {left} still need a category (see Account Mapping setup)." if left else "."), "success")
    if problems:
        flash("Skipped: " + "; ".join(problems[:6]) + (f"; and {len(problems) - 6} more" if len(problems) > 6 else ""), "warning")
    return _back(engagement_id, "tb_paste")
