"""tax, part 3 of 4.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file tax.py did."""

@tax_bp.route("/vat-act")
@login_required
def vat_act_check():
    """The Value Added Tax Act set beside the VAT register: what the Act says (in its own words, from the filed Act), what the register does, and where they differ."""
    st = act_import.status("vat")
    counts = {}
    for f in vat_checks.FINDINGS:
        counts[f["result"]] = counts.get(f["result"], 0) + 1
    return render_template("tax/vat_act_check.html", findings=vat_checks.FINDINGS, result_labels=vat_checks.RESULT_LABELS, st=st, counts=counts,
                           can_load=user_has_permission(current_user, "manage_legislative_updates"))


@tax_bp.route("/cobe-act")
@login_required
def cobe_act_check():
    """The Companies and Other Business Entities Act set beside the Secretarial module: what the Act says (in its own words, from the filed Act),
    what the module does, and where they differ - plus the Act's time limits, the section numbers that were corrected and the civil penalty categories."""
    import cobe_checks
    st = act_import.status("cobe")
    counts = {}
    for f in cobe_checks.FINDINGS:
        counts[f["result"]] = counts.get(f["result"], 0) + 1
    return render_template("tax/cobe_act_check.html", findings=cobe_checks.FINDINGS, result_labels=cobe_checks.RESULT_LABELS, st=st, counts=counts,
                           deadlines=cobe_checks.DEADLINES, corrections=cobe_checks.SECTION_CORRECTIONS,
                           penalty_categories=cobe_checks.CIVIL_PENALTY_CATEGORIES, late_table=cobe_checks.LATE_FILING_TABLE,
                           can_load=user_has_permission(current_user, "manage_legislative_updates"))


@tax_bp.route("/cgt-act")
@login_required
def cgt_act_check():
    """The Capital Gains Tax Act set beside what the app does for CGT: what the Act says (in its own words, from the filed Act), what the app does,
    where they differ; the Act's time limits and exemptions; and a small calculator for the dates the Act sets (nothing is saved)."""
    import cgt_checks
    st = act_import.status("cgt")
    counts = {}
    for f in cgt_checks.FINDINGS:
        counts[f["result"]] = counts.get(f["result"], 0) + 1

    def _d(name):
        raw = request.args.get(name, "").strip()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        except ValueError:
            return None
    given = {k: _d(k) for k in ("title_date", "pay_date", "sale_date", "damage_date", "notice_date")}
    dates = [
        ("Tax due (section 26(1)(b)): 30 days from formal transfer of title", cgt_checks.payment_due(given["title_date"])),
        ("Withholding tax due (sections 22C to 22E): 3rd working day from payment. Weekends skipped; public holidays are not", cgt_checks.withholding_due(given["pay_date"])),
        ("Reinvestment deadline (sections 21(2), 22(1)): end of the year of assessment next following the sale", cgt_checks.reinvestment_deadline(given["sale_date"])),
        ("Replacement of a damaged or destroyed asset (section 13(3)): 2 years", cgt_checks.damage_replacement_deadline(given["damage_date"])),
        ("Objection (section 25(1)): 30 days from the date of the notice", cgt_checks.objection_due(given["notice_date"])),
    ]
    return render_template("tax/cgt_act_check.html", findings=cgt_checks.FINDINGS, result_labels=cgt_checks.RESULT_LABELS, st=st, counts=counts,
                           deadlines=cgt_checks.DEADLINES, exemptions=cgt_checks.EXEMPTIONS_NOTED, given=given, dates=dates,
                           can_load=user_has_permission(current_user, "manage_legislative_updates"))


@tax_bp.route("/vat/import-batches/<int:batch_id>/currency", methods=["POST"])
@login_required
def set_vat_import_batch_currency(batch_id):
    """Re-tag a whole imported schedule (and every row it produced) with the
    right currency of trade - e.g. a ZWG schedule uploaded as USD."""
    _require_vat_schedule_permission()
    batch = VATImportBatch.query.get_or_404(batch_id)
    _ensure_engagement_access(batch.engagement)
    locked = vat_locked_months_among(batch.engagement_id, [i.invoice_date for i in batch.invoices])
    if locked:
        flash(_locked_message(locked, "this schedule's currency cannot be changed (it has documents dated in "
                                      + ("those months" if len(locked) > 1 else "that month") + ")"), "danger")
        return _tax_redirect(batch.engagement_id)
    currency = normalize_vat_currency(request.form.get("currency"))
    if currency:
        batch.currency = currency
        for inv in batch.invoices:
            inv.currency = currency
        db.session.commit()
        flash(f"Imported schedule moved to the {currency} VAT working paper ({len(batch.invoices)} document(s)).", "success")
    return _tax_redirect(batch.engagement_id)


@tax_bp.route("/vat/import/cancel", methods=["POST"])
@login_required
def cancel_vat_schedule_import():
    """Discards a staged file the preparer decided not to import after all
    (e.g. they picked the wrong file), rather than leaving it orphaned in
    Config.VAT_IMPORTS_DATA_DIR."""
    _require_vat_schedule_permission()
    engagement_id = request.form.get("engagement_id")
    staged_filename = secure_filename(request.form.get("staged_filename", "").strip())
    if staged_filename:
        staged_path = os.path.join(_vat_imports_dir(), staged_filename)
        if os.path.exists(staged_path):
            try:
                os.remove(staged_path)
            except OSError:
                pass
    if engagement_id:
        return _tax_redirect(int(engagement_id))
    return redirect(url_for("engagements.list_engagements"))


@tax_bp.route("/vat/import-batches/<int:batch_id>/delete", methods=["POST"])
@login_required
def delete_vat_import_batch(batch_id):
    """Reverses a mistaken schedule import in one action - deletes the
    batch record and, via the cascade on VATImportBatch.invoices (see
    models.py), every VATInvoice row it produced."""
    _require_vat_schedule_permission()
    batch = VATImportBatch.query.get_or_404(batch_id)
    _ensure_engagement_access(batch.engagement)
    engagement_id = batch.engagement_id
    locked = vat_locked_months_among(engagement_id, [i.invoice_date for i in batch.invoices])
    if locked:
        flash(_locked_message(locked, "this import cannot be deleted (it has documents dated in "
                                      + ("those months" if len(locked) > 1 else "that month") + ")"), "danger")
        return _tax_redirect(engagement_id)
    if batch.stored_filename:
        stored_path = os.path.join(_vat_imports_dir(), batch.stored_filename)
        if os.path.exists(stored_path):
            try:
                os.remove(stored_path)
            except OSError:
                pass
    db.session.delete(batch)
    db.session.commit()
    flash("Import batch and its invoices removed.", "success")
    return _tax_redirect(engagement_id)


# ---------- Monthly VAT computations ----------
# A VAT month is "In progress" until someone marks its computation as done; a
# done month is locked (see VATPeriod in models.py and _locked_message above).

@tax_bp.route("/<int:engagement_id>/vat/periods/<period_key>/done", methods=["POST"])
@login_required
def mark_vat_period_done(engagement_id, period_key):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    parsed = parse_vat_period_key(period_key)
    if not parsed:
        abort(404)
    year, month = parsed
    docs = [i for i in VATInvoice.query.filter_by(engagement_id=engagement_id).all() if vat_period_of(i.invoice_date) == parsed]
    label = vat_period_label(year, month)
    if not docs:
        flash(f"There are no VAT documents dated in {label}, so there is nothing to mark as done.", "danger")
        return _tax_redirect(engagement_id)
    row = VATPeriod.query.filter_by(engagement_id=engagement_id, year=year, month=month).first()
    if row is not None and row.is_done:
        flash(f"The {label} computation is already marked as done.", "info")
        return _tax_redirect(engagement_id, period_key)
    if row is None:
        row = VATPeriod(engagement_id=engagement_id, year=year, month=month)
        db.session.add(row)
    row.is_done = True
    row.completed_by_id = current_user.id
    row.completed_at = datetime.utcnow()
    db.session.commit()
    undated = VATInvoice.query.filter_by(engagement_id=engagement_id, invoice_date=None).count()
    note = (f" Note: {undated} document(s) have no date and are not part of any month - give them a date first if they belong here."
            if undated else "")
    flash(f"{label} VAT computation marked as done and locked. Documents dated in {label} can no longer be added, "
          f"deleted or imported until it is reopened.{note}", "success")
    return _tax_redirect(engagement_id, period_key)


@tax_bp.route("/<int:engagement_id>/vat/periods/<period_key>/reopen", methods=["POST"])
@login_required
def reopen_vat_period(engagement_id, period_key):
    engagement = _get_tax_engagement(engagement_id)
    if not user_has_permission(current_user, "reopen_vat_periods"):
        abort(403, description=(
            "Your account is not allowed to reopen a completed VAT month. This is limited to Supervisors, Partners "
            "and Admins by default - an Admin can grant it to you under Team > Permissions or your Access Rights."))
    parsed = parse_vat_period_key(period_key)
    if not parsed:
        abort(404)
    year, month = parsed
    label = vat_period_label(year, month)
    row = VATPeriod.query.filter_by(engagement_id=engagement_id, year=year, month=month).first()
    if row is None or not row.is_done:
        flash(f"The {label} computation is not marked as done, so there is nothing to reopen.", "info")
        return _tax_redirect(engagement_id, period_key)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash(f"Please give a reason for reopening {label} (it is kept on file).", "danger")
        return _tax_redirect(engagement_id, period_key)
    row.is_done = False
    row.reopened_by_id = current_user.id
    row.reopened_at = datetime.utcnow()
    row.reopen_reason = reason[:300]
    row.times_reopened = (row.times_reopened or 0) + 1
    db.session.commit()
    flash(f"{label} reopened - it is back to 'In progress' and documents dated in it can be changed again. "
          "Mark it as done again once the computation is final.", "info")
    return _tax_redirect(engagement_id, period_key)


# ---------- Dynamic Information Request List (Module 5) ----------

@tax_bp.route("/<int:engagement_id>/info-requests/add", methods=["POST"])
@login_required
def add_information_request(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    item = TaxInformationRequest(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        item_description=request.form.get("item_description", "").strip(),
        requested_from=request.form.get("requested_from", "").strip(),
        date_requested=_parse_date(request.form.get("date_requested")) or date.today(),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(item)
    db.session.commit()
    flash("Information request added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/info-requests/<int:item_id>/update", methods=["POST"])
@login_required
def update_information_request(item_id):
    item = TaxInformationRequest.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.status = request.form.get("status", item.status)
    if item.status == "Received" and not item.date_received:
        item.date_received = date.today()
    item.notes = request.form.get("notes", item.notes)
    db.session.commit()
    flash("Information request updated.", "success")
    return _tax_redirect(item.engagement_id)


@tax_bp.route("/info-requests/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_information_request(item_id):
    item = TaxInformationRequest.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Return Records (Module 7 + Module 10's e-filing/assessment recon) ----------

@tax_bp.route("/<int:engagement_id>/returns/add", methods=["POST"])
@login_required
def add_return_record(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    record = TaxReturnRecord(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", TAX_HEADS[0]),
        period_label=request.form.get("period_label", "").strip(),
        due_date=_parse_date(request.form.get("due_date")),
        amount_due=_parse_float(request.form.get("amount_due")),
        reference=request.form.get("reference", "").strip(),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(record)
    db.session.commit()
    flash("Return record added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/returns/<int:record_id>/update", methods=["POST"])
@login_required
def update_return_record(record_id):
    record = TaxReturnRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    record.status = request.form.get("status", record.status)
    record.filed_date = _parse_date(request.form.get("filed_date"))
    record.amount_due = _parse_float(request.form.get("amount_due"))
    record.amount_paid = _parse_float(request.form.get("amount_paid"))
    record.assessed_amount = _parse_float(request.form.get("assessed_amount"))
    record.reference = request.form.get("reference", record.reference)
    record.reconciliation_notes = request.form.get("reconciliation_notes", "").strip()
    record.notes = request.form.get("notes", record.notes)
    db.session.commit()
    flash("Return record updated.", "success")
    return _tax_redirect(record.engagement_id)


@tax_bp.route("/returns/<int:record_id>/delete", methods=["POST"])
@login_required
def delete_return_record(record_id):
    record = TaxReturnRecord.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    engagement_id = record.engagement_id
    db.session.delete(record)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Advisory Workflow (Module 7B - advisory-only) ----------

def _require_tax_advisory(engagement):
    if not engagement.has_tax_advisory:
        flash("The Tax Advisory service isn't on for this engagement.", "danger")
        return False
    return True


@tax_bp.route("/<int:engagement_id>/research/add", methods=["POST"])
@login_required
def add_research_log_entry(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_advisory(engagement):
        return _tax_redirect(engagement_id)
    entry = TaxResearchLogEntry(
        engagement_id=engagement_id,
        topic=request.form.get("topic", "").strip(),
        question=request.form.get("question", "").strip(),
        research_notes=request.form.get("research_notes", "").strip(),
        conclusion=request.form.get("conclusion", "").strip(),
        references=request.form.get("references", "").strip(),
        prepared_by_id=current_user.id,
    )
    db.session.add(entry)
    db.session.commit()
    flash("Research log entry added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/research/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_research_log_entry(entry_id):
    entry = TaxResearchLogEntry.query.get_or_404(entry_id)
    _ensure_engagement_access(entry.engagement)
    engagement_id = entry.engagement_id
    db.session.delete(entry)
    db.session.commit()
    return _tax_redirect(engagement_id)


@tax_bp.route("/<int:engagement_id>/structuring/add", methods=["POST"])
@login_required
def add_structuring_option(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_advisory(engagement):
        return _tax_redirect(engagement_id)
    option = TaxStructuringOption(
        engagement_id=engagement_id,
        option_name=request.form.get("option_name", "").strip(),
        description=request.form.get("description", "").strip(),
        estimated_tax_impact=_parse_float(request.form.get("estimated_tax_impact")),
        risk_rating=request.form.get("risk_rating", "Medium"),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(option)
    db.session.commit()
    flash("Structuring option added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/structuring/<int:option_id>/recommend", methods=["POST"])
@login_required
def recommend_structuring_option(option_id):
    option = TaxStructuringOption.query.get_or_404(option_id)
    _ensure_engagement_access(option.engagement)
    # Only one option can be "the" recommendation at a time - clear any
    # other recommended flag on the same engagement first.
    TaxStructuringOption.query.filter_by(engagement_id=option.engagement_id).update({"recommended": False})
    option.recommended = True
    db.session.commit()
    flash(f'"{option.option_name}" marked as the recommended option.', "success")
    return _tax_redirect(option.engagement_id)


@tax_bp.route("/structuring/<int:option_id>/delete", methods=["POST"])
@login_required
def delete_structuring_option(option_id):
    option = TaxStructuringOption.query.get_or_404(option_id)
    _ensure_engagement_access(option.engagement)
    engagement_id = option.engagement_id
    db.session.delete(option)
    db.session.commit()
    return _tax_redirect(engagement_id)


@tax_bp.route("/<int:engagement_id>/disputes/add", methods=["POST"])
@login_required
def add_dispute(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_advisory(engagement):
        return _tax_redirect(engagement_id)
    dispute = TaxDispute(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        issue_description=request.form.get("issue_description", "").strip(),
        amount_in_dispute=_parse_float(request.form.get("amount_in_dispute")),
        next_action=request.form.get("next_action", "").strip(),
        next_action_date=_parse_date(request.form.get("next_action_date")),
        notes=request.form.get("notes", "").strip(),
        created_by_id=current_user.id,
    )
    db.session.add(dispute)
    db.session.commit()
    flash("Dispute added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/disputes/<int:dispute_id>/update", methods=["POST"])
@login_required
def update_dispute(dispute_id):
    dispute = TaxDispute.query.get_or_404(dispute_id)
    _ensure_engagement_access(dispute.engagement)
    dispute.stage = request.form.get("stage", dispute.stage)
    dispute.amount_in_dispute = _parse_float(request.form.get("amount_in_dispute"))
    dispute.next_action = request.form.get("next_action", "").strip()
    dispute.next_action_date = _parse_date(request.form.get("next_action_date"))
    dispute.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Dispute updated.", "success")
    return _tax_redirect(dispute.engagement_id)


@tax_bp.route("/disputes/<int:dispute_id>/delete", methods=["POST"])
@login_required
def delete_dispute(dispute_id):
    dispute = TaxDispute.query.get_or_404(dispute_id)
    _ensure_engagement_access(dispute.engagement)
    engagement_id = dispute.engagement_id
    db.session.delete(dispute)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Tax Head Checklists / Quality Gates (Module 8) ----------

@tax_bp.route("/<int:engagement_id>/checklist/seed", methods=["POST"])
@login_required
def seed_tax_checklist(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    if TaxChecklistItem.query.filter_by(engagement_id=engagement_id).count() > 0:
        flash("The tax checklist already has items on it.", "info")
        return _tax_redirect(engagement_id)
    for order, (tax_head, item_text) in enumerate(DEFAULT_TAX_CHECKLIST_ITEMS):
        db.session.add(TaxChecklistItem(
            engagement_id=engagement_id, tax_head=tax_head, item_text=item_text,
            order=order, created_by_id=current_user.id,
        ))
    db.session.commit()
    flash("The firm's default tax checklist has been added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/<int:engagement_id>/checklist/add", methods=["POST"])
@login_required
def add_tax_checklist_item(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    max_order = db.session.query(db.func.max(TaxChecklistItem.order)).filter_by(engagement_id=engagement_id).scalar() or 0
    item = TaxChecklistItem(
        engagement_id=engagement_id,
        tax_head=request.form.get("tax_head", "").strip() or None,
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
        created_by_id=current_user.id,
    )
    db.session.add(item)
    db.session.commit()
    flash("Checklist item added.", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_tax_checklist_item(item_id):
    item = TaxChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.response = request.form.get("response", "")
    item.comment = request.form.get("comment", "").strip()
    item.completed_by_id = current_user.id
    item.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Checklist item updated.", "success")
    return _tax_redirect(item.engagement_id)


@tax_bp.route("/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_tax_checklist_item(item_id):
    item = TaxChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return _tax_redirect(engagement_id)


# ---------- Firm-wide Legislative Update Control (Module 8) ----------
#
# Started as a manually-typed log (title/summary/tax_head/effective_date/
# source_reference/impact_assessment - still exactly how "Log an update
# only" works below). add_legislative_update now also accepts an optional
# filed instrument (an Act/Notice/SI, a court Case, or a professional
# Publication, as a PDF or image): its text is extracted the same way as a
# Regulatory Notice and then analysed by AI (see legislation_summary.py)
# into ai_summary/ai_key_changes - a Case additionally gets a citation/
# court/jurisdiction and an authority assessment (see
# models.CASE_AUTHORITY_STATUSES), while a Publication (not Zimbabwean
# legislation and not a court judgment - e.g. a SAIT journal issue) is
# summarised on its own terms, see legislation_summary.PUBLICATION_SYSTEM_PROMPT.
# From there, a
# person confirms which practice areas it touches and tags the clients it
# affects (see LegislativeUpdateClientLink) - each tagged client then shows
# it on their own page, and a Tax Advisory engagement can pull the summary
# straight into its Technical Research Log with one click (see
# import_research_log_entry_from_legislative_update below).

def _render_legislative_updates(**extra):
    updates = LegislativeUpdate.query.order_by(LegislativeUpdate.effective_date.desc().nullslast(), LegislativeUpdate.created_at.desc()).all()
    context = dict(
        updates=updates, tax_heads=TAX_HEADS,
        can_edit=user_has_permission(current_user, "manage_legislative_updates"),
        instrument_types=LEGISLATIVE_UPDATE_TYPES, type_labels=LEGISLATIVE_UPDATE_TYPE_LABELS,
        areas=LEGISLATIVE_UPDATE_AREAS, clients=visible_clients(current_user),
        case_authority_statuses=CASE_AUTHORITY_STATUSES, case_authority_labels=CASE_AUTHORITY_LABELS,
        ask_question=None, ask_status=None, ask_answer=None, ask_citations=None, ask_candidates=None,
        ask_from_cache=False, ask_cache_hit_count=None, ask_cache_id=None, ask_lesson=None,
    )
    context.update(extra)
    return render_template("tax/legislative_updates.html", **context)


@tax_bp.route("/legislative-updates")
@login_required
def list_legislative_updates():
    return _render_legislative_updates()


@tax_bp.route("/legislative-updates/calendar")
@login_required
def legislative_updates_calendar():
    """A month-at-a-glance calendar of every filed instrument's own dates -
    effective_date (when it takes/took effect) and gazette_date (when it
    was gazetted) - drawn straight from what's already filed in
    Legislative Update Control (including anything bulk-imported from
    ZIMRA's Public Notices, see zimra_notices.py; a "Notice" instrument is
    filed exactly like any other). Deliberately just plots the two dates
    that are already there and already reliable, rather than having AI
    guess at a filing/compliance deadline buried in a notice's text - see
    the "Ask the library" box on the main list page for that kind of
    open-ended question instead. A logged entry with neither date set
    (a plain text-only log, or a file still missing both dates) simply
    never appears here; it's still on the main list.

    Also plots the firm-maintained StatutoryDeadline list (QPD/VAT/PAYE/etc
    dates a person typed in, not computed - see StatutoryDeadline's own
    docstring in models.py) as a third kind of event, managed from a list/
    add/delete panel on this same page."""
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    # clamp a stray/hand-edited query string back onto a real month rather than 500ing
    if month < 1 or month > 12 or year < 1900 or year > 2200:
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    days_in_month = calendar_module.monthrange(year, month)[1]
    last_day = date(year, month, days_in_month)

    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    events_by_day = {}

    def _add_event(day, kind, label, detail, link):
        # A plain dict with plain string keys ("label"/"detail"/"link"),
        # deliberately NOT keyed by an attribute name that collides with one
        # of dict's own built-in methods (an earlier version used "update"
        # as a key, and `ev.update` in the Jinja template silently resolved
        # to dict's own bound .update() method instead of doing a lookup).
        events_by_day.setdefault(day, []).append({"kind": kind, "label": label, "detail": detail, "link": link})

    for u in LegislativeUpdate.query.filter(
        LegislativeUpdate.effective_date >= first_day, LegislativeUpdate.effective_date <= last_day,
    ).order_by(LegislativeUpdate.title).all():
        detail = f"{u.title} (Effective{', ' + u.type_label if u.instrument_type else ''})"
        _add_event(u.effective_date.day, "Effective", u.title, detail, url_for("tax.list_legislative_updates") + f"#legislative-update-{u.id}")

    for u in LegislativeUpdate.query.filter(
        LegislativeUpdate.gazette_date >= first_day, LegislativeUpdate.gazette_date <= last_day,
    ).order_by(LegislativeUpdate.title).all():
        detail = f"{u.title} (Gazetted{', ' + u.type_label if u.instrument_type else ''})"
        _add_event(u.gazette_date.day, "Gazetted", u.title, detail, url_for("tax.list_legislative_updates") + f"#legislative-update-{u.id}")

    deadlines_this_month = StatutoryDeadline.query.filter(
        StatutoryDeadline.due_date >= first_day, StatutoryDeadline.due_date <= last_day,
    ).order_by(StatutoryDeadline.due_date, StatutoryDeadline.description).all()
    for d in deadlines_this_month:
        detail = f"{d.description}{' - ' + d.tax_head if d.tax_head else ''} (due {d.due_date.strftime('%d %b %Y')})"
        _add_event(d.due_date.day, "Deadline", d.description, detail, url_for("tax.legislative_updates_calendar", year=year, month=month) + f"#statutory-deadline-{d.id}")

    for day_events in events_by_day.values():
        day_events.sort(key=lambda e: e["label"])

    weeks = calendar_module.Calendar(firstweekday=0).monthdayscalendar(year, month)  # Monday-first, 0 = outside this month

    # Every upcoming (and this-year's) deadline for the management panel,
    # regardless of which month is currently being viewed - not just the
    # ones falling in this month's grid above.
    all_deadlines = StatutoryDeadline.query.order_by(StatutoryDeadline.due_date, StatutoryDeadline.description).all()

    return render_template(
        "tax/legislative_updates_calendar.html",
        year=year, month=month, month_label=first_day.strftime("%B %Y"),
        weeks=weeks, events_by_day=events_by_day,
        prev_year=prev_year, prev_month=prev_month, next_year=next_year, next_month=next_month,
        today=today, event_count=sum(len(v) for v in events_by_day.values()),
        all_deadlines=all_deadlines, tax_heads=TAX_HEADS,
        can_manage_deadlines=(current_user.role in REVIEWER_ROLES),
    )


@tax_bp.route("/legislative-updates/calendar/deadlines/add", methods=["POST"])
@login_required
def add_statutory_deadline():
    """Add one entry to the firm-maintained Statutory Deadlines list (see
    StatutoryDeadline in models.py). Gated the same way as raising an
    engagement Query (REVIEWER_ROLES) rather than the looser
    manage_checklist_templates permission some other firm-wide reference
    data uses - this is a compliance date the whole firm relies on, not a
    template."""
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    description = (request.form.get("description") or "").strip()
    due_date_str = request.form.get("due_date") or ""
    tax_head = (request.form.get("tax_head") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    try:
        due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Enter a valid due date.", "danger")
        return redirect(url_for("tax.legislative_updates_calendar"))
    if not description:
        flash("Enter a description for the deadline.", "danger")
        return redirect(url_for("tax.legislative_updates_calendar"))
    db.session.add(StatutoryDeadline(
        tax_head=tax_head or None, description=description, due_date=due_date,
        notes=notes or None, created_by_id=current_user.id,
    ))
    db.session.commit()
    flash("Statutory deadline added.", "success")
    return redirect(url_for("tax.legislative_updates_calendar", year=due_date.year, month=due_date.month))


@tax_bp.route("/legislative-updates/calendar/deadlines/<int:deadline_id>/delete", methods=["POST"])
@login_required
def delete_statutory_deadline(deadline_id):
    """Remove one Statutory Deadline entry - same REVIEWER_ROLES gate as
    adding one. Preserves whichever month was being viewed (passed through
    as hidden year/month fields) rather than bouncing back to the current
    month."""
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    deadline = StatutoryDeadline.query.get_or_404(deadline_id)
    db.session.delete(deadline)
    db.session.commit()
    flash("Statutory deadline removed.", "success")
    try:
        year = int(request.form.get("year") or date.today().year)
        month = int(request.form.get("month") or date.today().month)
    except (TypeError, ValueError):
        year, month = date.today().year, date.today().month
    return redirect(url_for("tax.legislative_updates_calendar", year=year, month=month))
