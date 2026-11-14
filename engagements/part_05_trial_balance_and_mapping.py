"""engagements, part 5 of 7.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file engagements.py did."""

def _read_tb_upload_rows(file_storage, filename):
    """Returns (rows, note): a list of {header: value} dicts from an
    uploaded .xlsx/.xls or .csv trial balance file, and an optional note
    to flash to the user (e.g. when an alternate format was auto-
    recognized). Raises ValueError with a plain-English message if it
    can't find a usable header row in any recognized format."""
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        content = file_storage.read().decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(content))), None

    workbook = openpyxl.load_workbook(file_storage, data_only=True)
    sheet = workbook.active
    all_rows = list(sheet.iter_rows(values_only=True))
    header_idx = None
    for i, row in enumerate(all_rows):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if "account name" in cells:
            header_idx = i
            break
    if header_idx is not None:
        headers = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
        data_rows = []
        for row in all_rows[header_idx + 1:]:
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            data_rows.append({h: v for h, v in zip(headers, row) if h})
        return data_rows, None

    # Not the standard single-sheet "Account Name" layout - check whether
    # this is the alternate one-sheet-per-year "GL Code/Name/Closing
    # balance" export before giving up.
    alt_rows, alt_note = _read_alt_format_tb_workbook(workbook)
    if alt_rows is not None:
        return alt_rows, alt_note

    raise ValueError(
        "Could not find a header row containing 'Account Name' in that file. "
        "Download the template below and use its column headers."
    )


def _get_or_create_trial_balance(engagement_id, source="manual"):
    tb = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not tb:
        tb = TrialBalance(engagement_id=engagement_id, source=source)
        db.session.add(tb)
        db.session.flush()
    return tb


def _touch_trial_balance(tb):
    tb.completed_by_id = current_user.id
    tb.completed_at = datetime.utcnow()
    tb.reviewed_by_id = None
    tb.reviewed_at = None
    tb.partner_signed_by_id = None
    tb.partner_signed_at = None
    # Entering real trial balance data supersedes an earlier "not
    # applicable" note - the note only makes sense while there's nothing
    # else on this tab.
    tb.not_applicable = False


def _lookup_coa_mapping(client_id, account_name):
    norm = fin.normalize_account_name(account_name)
    if not norm:
        return None
    return COAMapping.query.filter_by(client_id=client_id, account_name=norm).first()


def _upsert_coa_mapping(client_id, account_name, fs_category, user_id, coa_account_number=None):
    norm = fin.normalize_account_name(account_name)
    if not norm or not fs_category:
        return
    mapping = COAMapping.query.filter_by(client_id=client_id, account_name=norm).first()
    if not mapping:
        mapping = COAMapping(client_id=client_id, account_name=norm)
        db.session.add(mapping)
    mapping.fs_category = fs_category
    mapping.coa_account_number = coa_account_number
    mapping.updated_by_id = user_id
    mapping.updated_at = datetime.utcnow()


def _resolve_coa_account(coa_account_number):
    """The active StandardChartOfAccounts row for coa_account_number, or
    None if blank or not an active standard account - same helper as
    qpd.py's (kept local rather than imported, same existing convention as
    _lookup_coa_mapping/_upsert_coa_mapping above, which are already
    duplicated between the two files rather than shared)."""
    coa_account_number = (coa_account_number or "").strip()
    if not coa_account_number:
        return None
    return StandardChartOfAccounts.query.filter_by(account_number=coa_account_number, is_active=True).first()


@engagements_bp.route("/<int:engagement_id>/trial-balance/upload", methods=["POST"])
@login_required
def upload_trial_balance(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    if not _allowed_tb_file(file.filename):
        flash("Please upload a .xlsx or .csv file.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    original_name = secure_filename(file.filename)
    try:
        raw_rows, format_note = _read_tb_upload_rows(file, original_name)
        cleaned_rows = fin.parse_tb_rows(raw_rows)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    except Exception:
        flash("Could not read that file - make sure it's a .xlsx or .csv using the template's columns.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    tb = _get_or_create_trial_balance(engagement_id, source="upload")
    tb.source = "upload"
    tb.original_filename = original_name

    # Preserve any category already set on the existing lines (in case it
    # was set only on this TB and hadn't been saved to the reusable
    # mapping yet) before replacing them with the freshly imported rows.
    for existing_line in tb.lines:
        if existing_line.fs_category:
            _upsert_coa_mapping(engagement.client_id, existing_line.account_name, existing_line.fs_category, current_user.id, existing_line.coa_account_number)
    TrialBalanceLine.query.filter_by(trial_balance_id=tb.id).delete()

    std_sub = {a.account_number: a.sub_category for a in StandardChartOfAccounts.query.all()}
    for row in cleaned_rows:
        mapping = _lookup_coa_mapping(engagement.client_id, row["account_name"])
        db.session.add(TrialBalanceLine(
            trial_balance_id=tb.id,
            account_code=row["account_code"],
            account_name=row["account_name"],
            fs_category=mapping.fs_category if mapping else None,
            coa_account_number=mapping.coa_account_number if mapping else None,
            sub_category=std_sub.get(mapping.coa_account_number) if mapping and mapping.coa_account_number else None,
            current_debit=row["current_debit"], current_credit=row["current_credit"],
            prior_debit=row["prior_debit"], prior_credit=row["prior_credit"],
        ))

    _touch_trial_balance(tb)
    db.session.commit()
    if format_note:
        flash(format_note, "info")
    flash(f"Imported {len(cleaned_rows)} account(s) from '{original_name}'.", "success")
    for yr, dk, ck in (("current year", "current_debit", "current_credit"), ("prior year", "prior_debit", "prior_credit")):
        d, c_ = sum(r[dk] or 0 for r in cleaned_rows), sum(r[ck] or 0 for r in cleaned_rows)
        if abs(d - c_) >= 0.01:
            flash(f"The {yr} columns do not balance after import: debits {d:,.2f}, credits {c_:,.2f}, difference {d - c_:,.2f}. "
                  "Check the file for subtotal or total rows, or use 'Upload one year on its own' on this tab, which shows every row before importing.", "danger")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/<int:engagement_id>/trial-balance/confirm-suggested-mappings", methods=["POST"])
@login_required
def confirm_suggested_tb_mappings(engagement_id):
    """Batch-confirms the IAS 1 category chosen for each still-unmapped
    account on the Account Mapping setup screen - one submit instead of
    the one-account-at-a-time "Map" form, meant to be used alongside
    fin.suggest_fs_category()'s pre-filled suggestions there: the
    accountant reviews/adjusts each row's dropdown (already pre-filled
    with a suggestion where one exists), then confirms the whole batch at
    once. A row left as "Unmapped" is simply skipped - it stays on the
    list for next time, exactly as if this batch action had never run."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    tb = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    if not tb:
        flash("No trial balance to map yet.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    mapped_count = 0
    for line in tb.lines:
        if line.fs_category:
            continue  # already mapped - this batch only ever touches unmapped rows
        category = request.form.get(f"fs_category__{line.id}", "").strip()
        coa_account = _resolve_coa_account(request.form.get(f"coa_account_number__{line.id}"))
        if coa_account:
            category = coa_account.fs_category  # a chosen standard account is authoritative - see update_trial_balance_line
        if not category or category not in fin.CATEGORY_BY_CODE:
            continue  # left as "Unmapped" (or an unrecognized value) - skip, don't guess
        line.fs_category = category
        line.coa_account_number = coa_account.account_number if coa_account else None
        line.sub_category = coa_account.sub_category if coa_account else None
        _upsert_coa_mapping(engagement.client_id, line.account_name, category, current_user.id, line.coa_account_number)
        mapped_count += 1

    if mapped_count == 0:
        flash("No mappings were confirmed - every row was left as “Unmapped”.", "info")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    _touch_trial_balance(tb)
    db.session.commit()
    remaining = tb.unmapped_count
    if remaining:
        flash(f"{mapped_count} account(s) mapped. {remaining} still need a category.", "success")
    else:
        flash(f"{mapped_count} account(s) mapped - every account now has an IAS 1 category.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/<int:engagement_id>/trial-balance/lines/add", methods=["POST"])
@login_required
def add_trial_balance_line(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    name = request.form.get("account_name", "").strip()
    if not name:
        flash("Please give the account a name.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))

    def to_float(field):
        raw = request.form.get(field, "").strip()
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    tb = _get_or_create_trial_balance(engagement_id, source="manual")
    category = request.form.get("fs_category", "").strip() or None
    coa_account = _resolve_coa_account(request.form.get("coa_account_number"))
    if coa_account:
        category = coa_account.fs_category
    elif not category:
        # nothing chosen: use what is already remembered for this client under that account name
        remembered = _lookup_coa_mapping(engagement.client_id, name)
        if remembered:
            category = remembered.fs_category
            coa_account = _resolve_coa_account(remembered.coa_account_number)
    db.session.add(TrialBalanceLine(
        trial_balance_id=tb.id,
        account_code=request.form.get("account_code", "").strip(),
        account_name=name,
        fs_category=category,
        coa_account_number=coa_account.account_number if coa_account else None,
        sub_category=coa_account.sub_category if coa_account else None,
        current_debit=to_float("current_debit"), current_credit=to_float("current_credit"),
        prior_debit=to_float("prior_debit"), prior_credit=to_float("prior_credit"),
    ))
    if category:
        _upsert_coa_mapping(engagement.client_id, name, category, current_user.id, coa_account.account_number if coa_account else None)
    _touch_trial_balance(tb)
    db.session.commit()
    flash("Account added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_trial_balance_line(line_id):
    line = TrialBalanceLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.trial_balance.engagement)
    tb = line.trial_balance
    engagement = tb.engagement

    def to_float(field, current):
        raw = request.form.get(field)
        if raw is None:
            return current
        raw = raw.strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return current

    line.account_code = request.form.get("account_code", line.account_code or "").strip()
    line.account_name = request.form.get("account_name", line.account_name).strip() or line.account_name
    category = request.form.get("fs_category", "").strip() or None
    # Picking a Standard Chart of Accounts account is authoritative - it
    # drives BOTH the account number and the IAS 1 category together (the
    # template also auto-fills the category dropdown client-side via JS,
    # but this is enforced server-side too for a direct POST).
    coa_account = _resolve_coa_account(request.form.get("coa_account_number"))
    if coa_account:
        category = coa_account.fs_category
        line.coa_account_number = coa_account.account_number
        line.sub_category = coa_account.sub_category
    else:
        line.coa_account_number = None
        line.sub_category = None
    line.fs_category = category
    line.current_debit = to_float("current_debit", line.current_debit)
    line.current_credit = to_float("current_credit", line.current_credit)
    line.prior_debit = to_float("prior_debit", line.prior_debit)
    line.prior_credit = to_float("prior_credit", line.prior_credit)
    if category:
        _upsert_coa_mapping(engagement.client_id, line.account_name, category, current_user.id, line.coa_account_number)
    _touch_trial_balance(tb)
    db.session.commit()
    flash("Account updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_trial_balance_line(line_id):
    line = TrialBalanceLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.trial_balance.engagement)
    tb = line.trial_balance
    engagement_id = tb.engagement_id
    db.session.delete(line)
    _touch_trial_balance(tb)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/review", methods=["POST"])
@login_required
def review_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not tb.is_fully_mapped:
        flash("Every account needs an IAS 1 category (or 'Excluded') before the trial balance can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    if tb.completed_by_id == current_user.id:
        flash("You can't review a trial balance you imported/entered yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    tb.reviewed_by_id = current_user.id
    tb.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Trial balance marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/unreview", methods=["POST"])
@login_required
def unreview_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    tb.reviewed_by_id = None
    tb.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not tb.is_fully_mapped:
        flash("Every account needs an IAS 1 category (or 'Excluded') before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    if tb.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a trial balance you imported/entered yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))
    tb.partner_signed_by_id = current_user.id
    tb.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_trial_balance(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    tb.partner_signed_by_id = None
    tb.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


@engagements_bp.route("/<int:engagement_id>/trial-balance/mark-not-applicable", methods=["POST"])
@login_required
def mark_trial_balance_not_applicable(engagement_id):
    """Records that this engagement genuinely has no trial balance (e.g. a
    Consulting/Secretarial engagement, or an Investigative Engagement
    scoped to a specific matter) instead of leaving the tab looking
    unfinished. Refuses if real data already exists - clear the trial
    balance's lines first if you actually want to switch it to N/A."""
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    reason = request.form.get("not_applicable_reason", "").strip()
    if not reason:
        flash("Please explain why this engagement has no trial balance.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    tb = _get_or_create_trial_balance(engagement_id)
    if tb.lines:
        flash("This engagement already has trial balance accounts entered - remove them first if it's genuinely not applicable.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))
    tb.not_applicable = True
    tb.not_applicable_reason = reason
    tb.marked_na_by_id = current_user.id
    tb.marked_na_at = datetime.utcnow()
    db.session.commit()
    flash("Trial balance marked as not applicable for this engagement.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/unmark-not-applicable", methods=["POST"])
@login_required
def unmark_trial_balance_not_applicable(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    tb.not_applicable = False
    tb.not_applicable_reason = None
    tb.marked_na_by_id = None
    tb.marked_na_at = None
    db.session.commit()
    flash("Trial balance no longer marked as not applicable.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="trial_balance"))


@engagements_bp.route("/trial-balance/<int:tb_id>/adjustments/add", methods=["POST"])
@login_required
def add_audit_adjustment(tb_id):
    tb = TrialBalance.query.get_or_404(tb_id)
    _ensure_engagement_access(tb.engagement)
    reference = request.form.get("reference", "").strip() or next_journal_reference(tb.id)
    adjustment = AuditAdjustment(
        trial_balance_id=tb.id,
        reference=reference,
        description=request.form.get("description", "").strip(),
        applies_to="prior" if request.form.get("applies_to") == "prior" else "current",
        carry_to_current=request.form.get("carry_to_current", "1") != "0",
        completed_by_id=current_user.id,
        completed_at=datetime.utcnow(),
    )
    db.session.add(adjustment)
    db.session.commit()
    flash(f"{'Prior-year adjustment' if adjustment.is_prior_year else 'Adjustment'} '{reference}' added - now add its debit/credit lines below.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=tb.engagement_id, tab="finalisation"))


def _touch_adjustment(adjustment):
    """Every change to an adjustment (its reference/description, or a line
    added/edited/removed) re-records who last worked on it and clears any
    stale review/partner sign-off - the same convention used everywhere
    else in the app."""
    adjustment.completed_by_id = current_user.id
    adjustment.completed_at = datetime.utcnow()
    adjustment.reviewed_by_id = None
    adjustment.reviewed_at = None
    adjustment.partner_signed_by_id = None
    adjustment.partner_signed_at = None


@engagements_bp.route("/adjustments/<int:adjustment_id>/update", methods=["POST"])
@login_required
def update_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    adjustment.reference = request.form.get("reference", adjustment.reference or "").strip() or adjustment.reference
    adjustment.description = request.form.get("description", "").strip()
    if "applies_to" in request.form:
        adjustment.applies_to = "prior" if request.form.get("applies_to") == "prior" else "current"
        adjustment.carry_to_current = request.form.get("carry_to_current", "1") != "0"
    _touch_adjustment(adjustment)
    db.session.commit()
    flash("Adjustment updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/delete", methods=["POST"])
@login_required
def delete_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    engagement_id = adjustment.trial_balance.engagement_id
    db.session.delete(adjustment)
    db.session.commit()
    flash("Adjustment deleted.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/lines/add", methods=["POST"])
@login_required
def add_audit_adjustment_line(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    name = request.form.get("account_name", "").strip()
    category = request.form.get("fs_category", "").strip()
    if not name or not category:
        flash("Please give the line an account name and an IAS 1 category.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))

    def to_float(field):
        raw = request.form.get(field, "").strip()
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    db.session.add(AuditAdjustmentLine(
        adjustment_id=adjustment.id,
        account_name=name,
        fs_category=category,
        debit=to_float("debit"),
        credit=to_float("credit"),
    ))
    _touch_adjustment(adjustment)
    db.session.commit()
    flash("Adjustment line added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustment-lines/<int:line_id>/update", methods=["POST"])
@login_required
def update_audit_adjustment_line(line_id):
    line = AuditAdjustmentLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.adjustment.trial_balance.engagement)
    adjustment = line.adjustment

    def to_float(field, current):
        raw = request.form.get(field)
        if raw is None:
            return current
        raw = raw.strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return current

    line.account_name = request.form.get("account_name", line.account_name).strip() or line.account_name
    line.fs_category = request.form.get("fs_category", line.fs_category).strip() or line.fs_category
    line.debit = to_float("debit", line.debit)
    line.credit = to_float("credit", line.credit)
    _touch_adjustment(adjustment)
    db.session.commit()
    flash("Adjustment line updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustment-lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_audit_adjustment_line(line_id):
    line = AuditAdjustmentLine.query.get_or_404(line_id)
    _ensure_engagement_access(line.adjustment.trial_balance.engagement)
    adjustment = line.adjustment
    engagement_id = adjustment.trial_balance.engagement_id
    db.session.delete(line)
    _touch_adjustment(adjustment)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/review", methods=["POST"])
@login_required
def review_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not adjustment.lines or not adjustment.is_balanced:
        flash("The adjustment needs at least one line and must balance (debits = credits) before it can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    if adjustment.completed_by_id == current_user.id:
        flash("You can't review an adjustment you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    adjustment.reviewed_by_id = current_user.id
    adjustment.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Adjustment marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/unreview", methods=["POST"])
@login_required
def unreview_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    adjustment.reviewed_by_id = None
    adjustment.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not adjustment.lines or not adjustment.is_balanced:
        flash("The adjustment needs at least one line and must balance (debits = credits) before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    if adjustment.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on an adjustment you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))
    adjustment.partner_signed_by_id = current_user.id
    adjustment.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/adjustments/<int:adjustment_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_audit_adjustment(adjustment_id):
    adjustment = AuditAdjustment.query.get_or_404(adjustment_id)
    _ensure_engagement_access(adjustment.trial_balance.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    adjustment.partner_signed_by_id = None
    adjustment.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=adjustment.trial_balance.engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/financial-statements/save", methods=["POST"])
@login_required
def save_financial_statements_notes(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    fs = FinancialStatements.query.filter_by(engagement_id=engagement_id).first()
    if not fs:
        fs = FinancialStatements(engagement_id=engagement_id)
        db.session.add(fs)
    # Which framework the Financial Statements below are prepared under -
    # see models.REPORTING_FRAMEWORKS. Only "Full IFRS"/"IFRS for SMEs" get
    # the auto-generated, numbered Notes (financials.build_notes); changing
    # this takes effect immediately since the statements are always
    # rebuilt live from the trial balance, never stored.
    framework = request.form.get("reporting_framework", "").strip()
    if framework in dict(REPORTING_FRAMEWORKS):
        engagement.reporting_framework = framework
    # Which method the Cash Flow Statement's operating activities section
    # uses (IAS 7.18) - see models.CASH_FLOW_METHODS. Every other figure on
    # every statement is unaffected either way.
    cash_flow_method = request.form.get("cash_flow_method", "").strip()
    if cash_flow_method in dict(CASH_FLOW_METHODS):
        engagement.cash_flow_method = cash_flow_method
    # Public Interest Entity checklist - see models.PIE_CRITERIA. A pure
    # guidance checklist: it never sets reporting_framework itself, so an
    # unticked box here never silently changes which statements are shown.
    for code, _ in PIE_CRITERIA:
        setattr(engagement, code, request.form.get(code) == "on")
    # Small and Medium Enterprises Act classification - recorded as plain
    # client information (see models.SME_ACT_SECTORS / SME_ACT_SIZE_BANDS);
    # doesn't affect reporting_framework or the statements themselves.
    sme_sector = request.form.get("sme_sector", "").strip()
    engagement.sme_sector = sme_sector if sme_sector in SME_ACT_SECTORS else None
    sme_size_band = request.form.get("sme_size_band", "").strip()
    engagement.sme_size_band = sme_size_band if sme_size_band in dict(SME_ACT_SIZE_BANDS) else None

    def _blank_tolerant_number(field_name, cast):
        raw = request.form.get(field_name, "").strip().replace(",", "")
        if not raw:
            return None
        try:
            return cast(raw)
        except ValueError:
            return None

    engagement.sme_annual_turnover = _blank_tolerant_number("sme_annual_turnover", float)
    engagement.sme_gross_assets = _blank_tolerant_number("sme_gross_assets", float)
    engagement.sme_staff_headcount = _blank_tolerant_number("sme_staff_headcount", int)
    fs.basis_of_preparation = request.form.get("basis_of_preparation", "").strip()
    # Notes to the Financial Statements are free text and editable here, same
    # as the basis of preparation - but note that the FIGURES throughout the
    # statements themselves are never set from this form: they always come
    # live from the adjusted TrialBalance (financials.py) and stay that way.
    fs.notes_to_financial_statements = request.form.get("notes_to_financial_statements", "").strip()
    # The three standard closing notes (see financials.DEFAULT_CLOSING_NOTE_TEXT
    # for what the Finalisation tab shows before any of these have been saved).
    fs.related_party_note = request.form.get("related_party_note", "").strip()
    fs.commitments_note = request.form.get("commitments_note", "").strip()
    fs.subsequent_events_note = request.form.get("subsequent_events_note", "").strip()
    fs.completed_by_id = current_user.id
    fs.completed_at = datetime.utcnow()
    fs.reviewed_by_id = None
    fs.reviewed_at = None
    fs.partner_signed_by_id = None
    fs.partner_signed_at = None
    db.session.commit()
    flash("Financial statements notes saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/review", methods=["POST"])
@login_required
def review_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    trial_balance = TrialBalance.query.filter_by(engagement_id=fs.engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Import or enter the trial balance before the financial statements can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    if fs.completed_by_id == current_user.id:
        flash("You can't review financial statements you prepared yourself - ask another supervisor/partner to review them.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    fs.reviewed_by_id = current_user.id
    fs.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Financial statements marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/unreview", methods=["POST"])
@login_required
def unreview_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    fs.reviewed_by_id = None
    fs.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    trial_balance = TrialBalance.query.filter_by(engagement_id=fs.engagement_id).first()
    if not trial_balance or not trial_balance.lines:
        flash("Import or enter the trial balance before the partner can sign off the financial statements.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    if fs.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on financial statements you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))
    fs.partner_signed_by_id = current_user.id
    fs.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


@engagements_bp.route("/financial-statements/<int:fs_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_financial_statements(fs_id):
    fs = FinancialStatements.query.get_or_404(fs_id)
    _ensure_engagement_access(fs.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    fs.partner_signed_by_id = None
    fs.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=fs.engagement_id, tab="finalisation"))


# ---------- Workpaper Narratives (persistent, editable copies of the
# Management Representation Letter, Report to Management, and the Forensic
# Investigation Report's Executive Summary - see models.WorkpaperNarrative)
# ----------

def _get_or_seed_workpaper_narrative(engagement_id, kind, engagement=None):
    """The existing WorkpaperNarrative row for (engagement, kind), or a new
    unsaved one pre-filled with the firm's default starting wording - so the
    edit form always has sensible text to start from rather than a blank
    box, the first time this narrative is opened on an engagement. engagement
    is optional (only needed so default_workpaper_narrative_body can pick a
    type-appropriate default, e.g. "rep_letter" on a Business Intelligence
    and IT Engagement) - pass it when already in scope to avoid a query."""
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind=kind).first()
    if not narrative:
        narrative = WorkpaperNarrative(engagement_id=engagement_id, kind=kind, body=default_workpaper_narrative_body(kind, engagement))
    return narrative


def _narrative_tab(kind):
    """Which engagement-detail tab a WorkpaperNarrative's own Save/Review/
    Partner-sign actions should redirect back to - "tax" for a Tax Opinion/
    Tax Health Check part (see models.TAX_OPINION_PARTS/TAX_HEALTH_CHECK_
    PARTS), "accounting" for a Management Accounts Report part (see
    models.MANAGEMENT_ACCOUNTS_REPORT_PARTS), "finalisation" for every other
    kind (rep_letter, report_to_management, forensic_executive_summary),
    unchanged from before the Tax/Accounting modules existed."""
    if kind.startswith("tax_opinion") or kind.startswith("tax_health_check"):
        return "tax"
    if kind.startswith("mgmt_report"):
        return "accounting"
    return "finalisation"


@engagements_bp.route("/<int:engagement_id>/workpaper-narrative/<string:kind>/save", methods=["POST"])
@login_required
def save_workpaper_narrative(engagement_id, kind):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if kind not in WORKPAPER_NARRATIVE_KIND_KEYS:
        abort(404)
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind=kind).first()
    if not narrative:
        narrative = WorkpaperNarrative(engagement_id=engagement_id, kind=kind)
        db.session.add(narrative)
    narrative.body = request.form.get("body", "").strip()
    narrative.completed_by_id = current_user.id
    narrative.completed_at = datetime.utcnow()
    # Editing after review/sign-off invalidates them, same as every other
    # workpaper - a changed write-up needs a fresh review.
    narrative.reviewed_by_id = None
    narrative.reviewed_at = None
    narrative.partner_signed_by_id = None
    narrative.partner_signed_at = None
    db.session.commit()
    flash(f"{narrative.label} saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab=_narrative_tab(kind)))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/review", methods=["POST"])
@login_required
def review_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if narrative.completed_by_id == current_user.id:
        flash("You can't review a write-up you prepared yourself - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))
    narrative.reviewed_by_id = current_user.id
    narrative.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f"{narrative.label} marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/unreview", methods=["POST"])
@login_required
def unreview_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    narrative.reviewed_by_id = None
    narrative.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if narrative.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a write-up you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))
    narrative.partner_signed_by_id = current_user.id
    narrative.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


@engagements_bp.route("/workpaper-narrative/<int:narrative_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_workpaper_narrative(narrative_id):
    narrative = WorkpaperNarrative.query.get_or_404(narrative_id)
    _ensure_engagement_access(narrative.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    narrative.partner_signed_by_id = None
    narrative.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=narrative.engagement_id, tab=_narrative_tab(narrative.kind)))


# ---------- Substantive Procedures (system-based: by audit area, driven by risk + industry) ----------

def _substantive_areas_for(engagement):
    """The area names valid for this engagement's Substantive Procedures tab
    (Execution Plan, on a Secretarial engagement) - the four forensic
    evidence-type categories on an Investigative Engagement, the four
    cyber/IT/AML-CFT assurance domains on a Business Intelligence and IT
    Engagement, the five Execution Plan workstreams on a Secretarial
    engagement, or the ordinary financial-statement audit areas otherwise.
    Used wherever an area name submitted from a form needs validating
    against whichever set is actually in play."""
    if engagement.type == "Investigative Engagement":
        return FORENSIC_SUBSTANTIVE_AREAS
    if engagement.type == "Business Intelligence and IT Engagements":
        return BUSINESS_IT_SUBSTANTIVE_AREAS
    if engagement.type == "Secretarial":
        return secretarial_substantive_areas(engagement)
    return AUDIT_AREAS


def sync_substantive_procedures(engagement):
    """Additive top-up of suggested procedures across every area, driven by
    the engagement's CURRENT Risk Assessment rating and the client's
    CURRENT industry (see AUDIT_AREAS / BASELINE_SUBSTANTIVE_PROCEDURES /
    HIGH_RISK_EXTRA_PROCEDURES / INDUSTRY_EXTRA_PROCEDURES above) - or, on
    an Investigative Engagement, the four forensic evidence-type categories,
    or on a Business Intelligence and IT Engagement, the four cyber/IT/
    AML-CFT assurance domains, instead (see FORENSIC_SUBSTANTIVE_AREAS /
    FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES / BUSINESS_IT_SUBSTANTIVE_AREAS
    / BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES above; the risk/industry
    extras are audit-area captions and don't apply there, so those two
    engagement types only ever get their baseline list). This is the shared
    engine behind both the manual "Generate suggested procedures" button
    and the automatic re-sync fired whenever the risk rating or the
    client's industry changes (see sync_substantive_procedures_if_started,
    save_risk_assessment, and clients.edit_client) - so the
    suggested-procedures checklist keeps itself current instead of only
    ever reflecting whatever the risk rating/industry happened to be the
    first time someone clicked the button. Exactly like the manual button,
    it only ever ADDS whatever's newly applicable and not already present -
    it never edits, reorders or removes an existing item (ticked-off work,
    sign-offs and manually added procedures are never touched), so it's
    safe to call as often as needed. Returns how many procedures were
    added."""
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if engagement.is_tax_advisory_type:
        return 0   # a Tax Advisory engagement has no audit areas to top up; its tax-head Execution Plan is kept by hand on the Tax tab
    if is_forensic:
        areas = FORENSIC_SUBSTANTIVE_AREAS
        baseline = FORENSIC_BASELINE_SUBSTANTIVE_PROCEDURES
        high_risk = False
        industry_map = {}
    elif is_business_it:
        areas = BUSINESS_IT_SUBSTANTIVE_AREAS
        baseline = BUSINESS_IT_BASELINE_SUBSTANTIVE_PROCEDURES
        high_risk = False
        industry_map = {}
    elif is_secretarial:
        areas = secretarial_substantive_areas(engagement)
        baseline = SECRETARIAL_BASELINE_SUBSTANTIVE_PROCEDURES
        high_risk = False
        industry_map = {}
    else:
        areas = AUDIT_AREAS
        baseline = BASELINE_SUBSTANTIVE_PROCEDURES
        risk_assessment = RiskAssessment.query.filter_by(engagement_id=engagement.id).first()
        high_risk = bool(risk_assessment and risk_assessment.rating == "High")
        industry_map = INDUSTRY_EXTRA_PROCEDURES.get(engagement.client.industry, {})

    added_count = 0
    for area_name in areas:
        area = SubstantiveProcedureArea.query.filter_by(engagement_id=engagement.id, area=area_name).first()
        if not area:
            area = SubstantiveProcedureArea(engagement_id=engagement.id, area=area_name)
            db.session.add(area)
            db.session.flush()

        desired = [(t, "baseline") for t in baseline.get(area_name, [])]
        if high_risk:
            desired += [(t, "risk") for t in HIGH_RISK_EXTRA_PROCEDURES.get(area_name, [])]
        desired += [(t, "industry") for t in industry_map.get(area_name, [])]

        existing_texts = {i.procedure_text for i in area.items}
        next_order = len(area.items)
        area_changed = False
        for text, source in desired:
            if text not in existing_texts:
                item = SubstantiveProcedureItem(area_id=area.id, procedure_text=text, source=source, order=next_order)
                if is_secretarial:
                    details = SECRETARIAL_EXECUTION_TASK_DETAILS.get(text)
                    if details:
                        item.trigger_event, item.responsible_role, item.target_output = details
                if is_business_it:
                    item.procedure_kind = BUSINESS_IT_PROCEDURE_KIND.get(text)
                db.session.add(item)
                next_order += 1
                added_count += 1
                area_changed = True
        if area_changed:
            # Only touch (and clear any existing sign-off on) areas that
            # actually gained new suggested procedures - re-running this on
            # an already-signed-off area with nothing new to add shouldn't
            # disturb it.
            _touch_substantive_area(area)

    return added_count


def sync_substantive_procedures_if_started(engagement_id):
    """Auto-sync wrapper used by triggers OTHER than the explicit "Generate
    suggested procedures" button (i.e. saving the Risk Assessment, and
    editing the client's industry) - deliberately only does anything when
    the Substantive Procedures section has already been started for this
    engagement (at least one area/procedure already exists), so it never
    conjures the whole audit-area section out of nowhere on an engagement
    that hasn't opened that tab, e.g. an Investigative Engagement, which
    doesn't use Substantive Procedures at all. Returns how many procedures
    were added (0 if nothing to do or the section hasn't been started)."""
    engagement = Engagement.query.get(engagement_id)
    if not engagement or not engagement.client:
        return 0
    if not SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).first():
        return 0
    return sync_substantive_procedures(engagement)


@engagements_bp.route("/<int:engagement_id>/substantive-procedures/generate", methods=["POST"])
@login_required
def generate_substantive_procedures(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    added_count = sync_substantive_procedures(engagement)
    db.session.commit()
    if engagement.type == "Investigative Engagement":
        if added_count:
            flash(f"Generated {added_count} suggested forensic procedure(s) across the four investigative categories.", "success")
        else:
            flash("No new suggested procedures to add - the full forensic procedure list is already there below.", "info")
    elif engagement.type == "Business Intelligence and IT Engagements":
        if added_count:
            flash(f"Generated {added_count} suggested procedure(s) across the four cyber/IT/AML-CFT assurance domains.", "success")
        else:
            flash("No new suggested procedures to add - the full baseline procedure list is already there below.", "info")
    elif engagement.type == "Secretarial":
        if added_count:
            flash(f"Generated {added_count} Execution Plan task(s) across the {len(secretarial_substantive_areas(engagement))} secretarial workstreams.", "success")
        else:
            flash("No new Execution Plan tasks to add - the full baseline task list is already there below.", "info")
    else:
        risk_assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
        high_risk = bool(risk_assessment and risk_assessment.rating == "High")
        if added_count:
            flash(f"Generated {added_count} suggested procedure(s) across the audit areas, based on the current risk rating{' (High)' if high_risk else ''} and the client's industry.", "success")
        else:
            flash("No new suggested procedures to add - everything suggested for the current risk rating and industry is already listed below.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


def _touch_substantive_area(area):
    """Every change within an area (a procedure added/edited/removed, or its
    notes changed) re-records who last worked on it and clears any stale
    review/partner sign-off - the same convention used everywhere else."""
    area.completed_by_id = current_user.id
    area.completed_at = datetime.utcnow()
    area.reviewed_by_id = None
    area.reviewed_at = None
    area.partner_signed_by_id = None
    area.partner_signed_at = None


def _get_or_create_substantive_area(engagement_id, area_name):
    area = SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id, area=area_name).first()
    if not area:
        area = SubstantiveProcedureArea(engagement_id=engagement_id, area=area_name)
        db.session.add(area)
        db.session.flush()
    return area


# ---------- PPE Depreciation policy / Asset Register (Substantive Procedures > Property, Plant and Equipment) ----------

def _ppe_plain_data(engagement_id):
    """Depreciation policy (PPEAssetClass) and Asset Register (PPEAsset)
    rows for an engagement, both as ORM objects (for the template to render
    and edit) and as the plain dicts financials.py's
    build_ppe_movement_schedule()/ppe_accounting_policy_text() expect -
    keeping those functions free of any ORM/session dependency, consistent
    with the rest of financials.py."""
    classes = PPEAssetClass.query.filter_by(engagement_id=engagement_id).order_by(PPEAssetClass.order, PPEAssetClass.id).all()
    assets = PPEAsset.query.filter_by(engagement_id=engagement_id).order_by(PPEAsset.order, PPEAsset.id).all()
    import ppe_revaluation
    revalued = ppe_revaluation.applied_values(engagement_id)            # {asset id: fair value} once a revaluation is applied
    revalued_classes = ppe_revaluation.revalued_class_ids(engagement_id)
    class_dicts = [
        {"id": c.id, "name": c.name, "depreciation_method": c.depreciation_method,
         "rate_percent": c.rate_percent, "useful_life_years": c.useful_life_years,
         "revalued": c.id in revalued_classes, "engagement_id": engagement_id}
        for c in classes
    ]
    asset_dicts = [
        {"asset_class_id": a.asset_class_id, "cost": a.cost or 0.0, "revalued_to": revalued.get(a.id),
         "opening_accumulated_depreciation": a.opening_accumulated_depreciation or 0.0,
         "current_year_depreciation": a.current_year_depreciation or 0.0,
         "disposal_cost": a.disposal_cost or 0.0,
         "disposal_accumulated_depreciation": a.disposal_accumulated_depreciation or 0.0,
         "date_acquired": a.date_acquired}
        for a in assets
    ]
    return classes, assets, class_dicts, asset_dicts
