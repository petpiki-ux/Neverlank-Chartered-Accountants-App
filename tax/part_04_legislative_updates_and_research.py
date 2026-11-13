"""tax, part 4 of 4.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file tax.py did."""

@tax_bp.route("/legislative-updates/ask", methods=["POST"])
@login_required
def ask_legislative_updates():
    """"Ask the library" - a plain-English question answered from the
    firm's own filed Legislative Update Control library (see
    legislation_search.py), never from the model's general knowledge. Open
    to any logged-in user, same as viewing the list itself - it only reads,
    it never files or changes anything."""
    question = request.form.get("question", "").strip()
    if not question:
        flash("Please enter a question to ask.", "danger")
        return _render_legislative_updates()

    status, payload = legislation_search.ask(question, asked_by_id=current_user.id)
    if status == "not_configured":
        flash("Automatic question-answering isn't set up yet (ANTHROPIC_API_KEY is not set) - see the README.", "danger")
        return _render_legislative_updates(ask_question=question, ask_status=status)
    if status == "no_candidates":
        return _render_legislative_updates(ask_question=question, ask_status=status)
    if status == "verified":
        return _render_legislative_updates(ask_question=question, ask_status=status, ask_answer=payload["answer"],
                                           ask_lesson=payload["lesson"])
    if status == "no_answer":
        return _render_legislative_updates(
            ask_question=question, ask_status=status,
            ask_answer=payload["answer"], ask_candidates=payload["candidates"],
            ask_from_cache=payload.get("from_cache", False), ask_cache_hit_count=payload.get("cache_hit_count"),
        )
    if status == "error":
        flash(f"Couldn't answer that: {payload}", "danger")
        return _render_legislative_updates(ask_question=question, ask_status=status)
    # status == "done"
    return _render_legislative_updates(
        ask_question=question, ask_status=status,
        ask_answer=payload["answer"], ask_citations=payload["citations"],
        ask_from_cache=payload.get("from_cache", False), ask_cache_hit_count=payload.get("cache_hit_count"),
        ask_cache_id=payload.get("cache_id"),
    )


def _search_filters():
    """The search filters in the request (form or query string) as legislation_index expects them, plus what to show back in the form."""
    src = request.values
    types = [t for t in src.getlist("type") if t in LEGISLATIVE_UPDATE_TYPES]
    f = {}
    if types:
        f["types"] = types
    if src.get("tax_head") in TAX_HEADS:
        f["tax_head"] = src.get("tax_head")
    if src.get("area") in LEGISLATIVE_UPDATE_AREAS:
        f["area"] = src.get("area")
    year = src.get("year", type=int)
    if year and 1900 <= year <= 2100:
        f["year"] = year
    if src.get("reviewed") == "1":
        f["reviewed"] = True
    return f


def _render_search(**extra):
    q = (request.values.get("q") or "").strip()
    filters = _search_filters()
    sort = request.values.get("sort") if request.values.get("sort") in ("relevance", "newest") else "relevance"
    page = request.values.get("page", type=int) or 1
    ctx = dict(q=q, filters=filters, sort=sort, page=page, result=None, brief=None, tax_heads=TAX_HEADS, areas=LEGISLATIVE_UPDATE_AREAS,
               instrument_types=LEGISLATIVE_UPDATE_TYPES, type_labels=LEGISLATIVE_UPDATE_TYPE_LABELS, advice=None, advice_status=None,
               explain=[], library_size=LegislativeUpdate.query.count(), highlight=legislation_index.highlight, snippet=legislation_index.snippet)
    if q:
        if legislation_index.is_brief(q) and sort == "relevance" and page == 1:
            ctx["brief"] = legislation_index.search_brief(q, filters)
            ctx["result"] = ctx["brief"]["overall"]
        else:
            ctx["result"] = legislation_index.search(q, filters, sort=sort, page=page)
        ctx["explain"] = legislation_index.describe(ctx["result"].plan)
    ctx.update(extra)
    return render_template("tax/legislative_search.html", **ctx)


@tax_bp.route("/legislative-updates/search")
@login_required
def search_legislative_updates():
    """Deep search of the whole filed library: every passage of every Act, Notice, SI, case, publication and news article, ranked, with the
    matching words highlighted and a link into the full text at the right passage (see legislation_index.py). Reads only."""
    return _render_search()


@tax_bp.route("/legislative-updates/advise", methods=["POST"])
@login_required
def advise_legislative_updates():
    """Draft advice for a described situation from the filed library alone (legislation_search.advise): topic by topic, with the sources
    cited and the gaps named. A draft for a person to review; it never files or changes anything."""
    q = (request.form.get("q") or "").strip()
    if not q:
        flash("Describe the situation first.", "danger")
        return redirect(url_for("tax.search_legislative_updates"))
    status, payload = legislation_search.advise(q, asked_by_id=current_user.id)
    if status == "not_configured":
        flash("Drafting advice isn't set up yet (ANTHROPIC_API_KEY is not set). The search below still works.", "danger")
    elif status == "error":
        flash(f"Couldn't draft that: {payload}", "danger")
    return _render_search(advice=payload if status == "done" else None, advice_status=status)


@tax_bp.route("/legislative-updates/<int:update_id>/read")
@login_required
def read_legislative_update(update_id):
    """The full filed text of one update, section by section, with the words from a search marked and the first matching passage in view."""
    update = LegislativeUpdate.query.get_or_404(update_id)
    q = (request.args.get("q") or "").strip()
    legislation_chunking.ensure_chunks(update)
    db.session.commit()
    plan = legislation_index.parse_query(q) if q else None
    passages = []
    if plan is not None and not plan.empty:
        passages = legislation_index.reader_passages(update, plan)
    else:
        from legislation_index import Plan
        plan = Plan()
        passages = [{"chunk": c, "html": c.text, "matches": 0} for c in update.chunks]
    matching = [p for p in passages if p["matches"]]
    best = max(matching, key=lambda p: p["matches"])["chunk"].id if matching else None
    return render_template("tax/legislative_reader.html", update=update, q=q, passages=passages, total_matches=sum(p["matches"] for p in passages),
                           matching_ids=[p["chunk"].id for p in matching], best_id=best, explain=legislation_index.describe(plan) if q else [])


# ----------------------------------------------------------------- the Act, section by section

def _act_or_404(code):
    if code not in act_import.ACTS:
        abort(404)
    return act_import.ACTS[code]


@tax_bp.route("/act/<code>")
@login_required
def act_contents(code):
    """Contents of an Act that has been filed section by section: Parts, sections and schedules, each opening at its own number."""
    cfg = _act_or_404(code)
    st = act_import.status(code)
    sections, schedules = [], []
    if st["loaded"]:
        rows = LegislativeProvision.query.filter_by(act_code=code).order_by(LegislativeProvision.sort_order).all()
        sections = [r for r in rows if r.kind == "section"]
        schedules = [r for r in rows if r.kind == "schedule"]
    parts = []
    for r in sections:
        if not parts or parts[-1]["part"] != r.part:
            parts.append({"part": r.part, "title": r.part_title, "rows": []})
        parts[-1]["rows"].append(r)
    return render_template("tax/act_contents.html", cfg=cfg, st=st, parts=parts, schedules=schedules, code=code,
                           can_load=user_has_permission(current_user, "manage_legislative_updates"))


@tax_bp.route("/act/<code>/load", methods=["POST"])
@login_required
@legislative_update_editor_required
def act_load(code):
    """File (or refresh) the Act section by section from the copy bundled with the app, or from a text file uploaded here."""
    _act_or_404(code)
    text = None
    f = request.files.get("file")
    if f and f.filename:
        if not f.filename.lower().endswith(".txt"):
            flash("Upload the Act as a .txt file (or leave the box empty to use the copy supplied with the app).", "danger")
            return redirect(url_for("tax.act_contents", code=code))
        text = f.read().decode("utf-8", errors="replace")
    try:
        res = act_import.load_act(code, text=text, user_id=current_user.id)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("tax.act_contents", code=code))
    msg = "%s filed: %d sections and %d schedules, in %d searchable passages." % (act_import.ACTS[code]["title"], res["sections"], res["schedules"], res["chunks"])
    if not res["created"]:
        msg += " The earlier copy of this Act in the library was replaced."
    flash(msg, "success")
    for w in res["warnings"][:5]:
        flash(w, "warning")
    try:
        import prohibited_deductions
        n = prohibited_deductions.align_with_act() if code == "ita" else 0
        if n:
            flash("%d rule%s on the Section 16 rules page were brought into line with the Act's wording (they remain unverified until you tick them)." % (n, "" if n == 1 else "s"), "info")
    except Exception:
        db.session.rollback()
    return redirect(url_for("tax.act_contents", code=code))


@tax_bp.route("/act/<code>/<path:ref>")
@login_required
def act_provision(code, ref):
    """One section or schedule of the Act in its own words. A reference with a paragraph - 16(1)(m) - shows that paragraph first, with the whole section below."""
    cfg = _act_or_404(code)
    found = act_import.paragraph_text(ref, code)
    if found is None:
        st = act_import.status(code)
        if not st["loaded"]:
            flash("The %s has not been filed section by section yet. Use 'File the Act' on this page." % cfg["short"], "warning")
            return redirect(url_for("tax.act_contents", code=code))
        flash("Couldn't find '%s' in the %s." % (ref, cfg["short"]), "warning")
        return redirect(url_for("tax.act_contents", code=code))
    row = LegislativeProvision.query.get(found["provision_id"])
    prev_row = LegislativeProvision.query.filter(LegislativeProvision.act_code == code, LegislativeProvision.sort_order < row.sort_order).order_by(LegislativeProvision.sort_order.desc()).first()
    next_row = LegislativeProvision.query.filter(LegislativeProvision.act_code == code, LegislativeProvision.sort_order > row.sort_order).order_by(LegislativeProvision.sort_order).first()
    q = (request.args.get("q") or "").strip()
    html = None
    if q:
        plan = legislation_index.parse_query(q)
        if not plan.empty:
            html = legislation_index.highlight(row.text, plan)
    if found["kind"] == "schedule" and found["path"]:
        sub = ", paragraph " + found["path"][0] + "".join("(%s)" % x for x in found["path"][1:])
    elif found["path"]:
        sub = ", " + "".join("(%s)" % x for x in found["path"])
    else:
        sub = ""
    return render_template("tax/act_provision.html", cfg=cfg, code=code, found=found, row=row, sub=sub, prev_row=prev_row, next_row=next_row, q=q,
                           html=html, canonical=act_import.canonical, update=row.legislative_update)


@tax_bp.route("/legislative-updates/ask/feedback", methods=["POST"])
@login_required
def ask_legislative_feedback():
    """Tell the app whether an "Ask the library" answer was right. Helpful answers can be put forward as the firm's
    verified answer; a wrong answer is dropped from the recall cache so it is never reused, and the person's correction
    (if given) is put forward as a lesson. Nothing becomes the firm's verified answer until a Partner or Admin approves
    it under Firm Learning (see learning.py)."""
    import learning
    from models import LegislativeAskCache, FirmLesson
    f = request.form
    verdict = f.get("verdict")
    question = f.get("question", "").strip()
    correction = f.get("correction", "").strip()
    row = LegislativeAskCache.query.get(f.get("cache_id", type=int)) if f.get("cache_id", type=int) else None
    lesson = FirmLesson.query.get(f.get("lesson_id", type=int)) if f.get("lesson_id", type=int) else None
    back = redirect(url_for("tax.list_legislative_updates"))

    if verdict == "helpful":
        if row is not None:
            row.helpful_count = (row.helpful_count or 0) + 1
        if lesson is not None and lesson.status == "Approved":
            lesson.helpful_count = (lesson.helpful_count or 0) + 1
        db.session.commit()
        flash("Thanks - noted.", "success")
    elif verdict == "verify":
        if row is None or row.status != "done":
            flash("Only a library answer that cites filed material can be put forward.", "danger")
            return back
        cited = [u.title for u in legislation_search._row_to_payload(row).get("citations", [])]
        new, err = learning.propose("library", "Verified answer", row.question_original, row.answer, current_user,
                                    evidence="Answer drawn from: " + "; ".join(cited) if cited else None,
                                    source_kind="library_answer", source_id=row.id)
        if new is None:
            flash(err, "danger")
        elif err == "duplicate":
            flash("That answer is already with the Partners or approved.", "info")
        else:
            db.session.commit()
            flash("Put forward as the firm's verified answer. A Partner will review it; until then nothing changes.", "success")
    elif verdict == "not_right":
        if row is not None:
            row.unhelpful_count = (row.unhelpful_count or 0) + 1
            db.session.delete(row)     # a wrong answer is never recalled again
        if lesson is not None and lesson.status == "Approved":
            lesson.unhelpful_count = (lesson.unhelpful_count or 0) + 1
        made = False
        if correction and question:
            new, err = learning.propose("library", "Verified answer", question, correction, current_user,
                                        evidence="Put forward as the correction to an answer the app gave.",
                                        source_kind="library_answer", source_id=row.id if row is not None else None)
            made = new is not None and err != "duplicate"
        db.session.commit()
        flash("Thanks - that answer will not be reused." + (" Your correction has gone to the Partners to approve." if made else ""),
              "success")
    else:
        flash("Nothing to record.", "info")
    return back


@tax_bp.route("/legislative-updates/add", methods=["POST"])
@login_required
@legislative_update_editor_required
def add_legislative_update(engagement_id=None):
    title = request.form.get("title", "").strip()
    if not title:
        flash("Please enter a title.", "danger")
        return redirect(url_for("tax.list_legislative_updates"))

    instrument_type = request.form.get("instrument_type", "").strip() or None
    if instrument_type and instrument_type not in LEGISLATIVE_UPDATE_TYPES:
        instrument_type = None

    update = LegislativeUpdate(
        tax_head=request.form.get("tax_head", "").strip() or None,
        title=title,
        summary=request.form.get("summary", "").strip(),
        effective_date=_parse_date(request.form.get("effective_date")),
        source_reference=request.form.get("source_reference", "").strip(),
        impact_assessment=request.form.get("impact_assessment", "").strip(),
        instrument_type=instrument_type,
        gazette_date=_parse_date(request.form.get("gazette_date")),
        created_by_id=current_user.id,
    )
    db.session.add(update)

    file = request.files.get("file")
    if instrument_type == "News":
        # A News entry is filed by pasting a URL rather than uploading a
        # file - the app fetches the page itself and extracts its text. See
        # _fetch_and_extract_news_article and models.LegislativeUpdate's
        # docstring.
        article_url = request.form.get("article_url", "").strip()
        if not article_url:
            flash("Please paste the news article's URL - a News entry is filed by URL rather than an uploaded file. The update was logged without a source.", "warning")
        else:
            db.session.flush()  # assign update.id without committing yet
            text, fetch_error = _fetch_and_extract_news_article(article_url)
            update.article_url = article_url
            update.extraction_status = "extracted" if text else "no_text_found"
            if text:
                update.extracted_text = text
            db.session.commit()  # save the fetched article itself before attempting the AI call, so a slow/failed AI step never loses it

            if text:
                result, ai_status, ai_error = legislation_summary.summarize_news_update(text, article_url)
                update.ai_status = ai_status
                update.ai_error = ai_error
                update.ai_processed_at = datetime.utcnow()
                if ai_status == "done":
                    update.ai_summary = result["summary"]
                    update.set_ai_key_changes(result["key_changes"])
                    update.set_ai_suggested_areas(result["suggested_areas"])
                legislation_chunking.ensure_chunks(update)
            else:
                update.ai_status = "error"
                update.ai_error = fetch_error
                update.ai_processed_at = datetime.utcnow()
    elif file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in LEGISLATIVE_UPDATE_FILE_EXTENSIONS:
            flash("Only PDF, JPG and PNG files are accepted for a filed instrument - the update was logged without a file.", "warning")
        else:
            db.session.flush()  # assign update.id without committing yet
            original_name = secure_filename(file.filename)
            is_image = ext in LEGISLATIVE_UPDATE_IMAGE_EXTENSIONS
            stored_name = f"leg{update.id}_{uuid.uuid4().hex[:8]}_{original_name}"
            filepath = os.path.join(_legislative_updates_dir(), stored_name)
            file.save(filepath)
            update.original_filename = original_name
            update.stored_filename = stored_name

            if is_image:
                update.extraction_status = None  # not applicable - no PDF text layer to try
            else:
                text, extraction_status, page_count = sanctions_data.extract_pdf_text(filepath)
                update.extracted_text = text
                update.extraction_status = extraction_status
                update.page_count = page_count

            db.session.commit()  # save the upload itself before attempting the AI call, so a slow/failed AI step never loses the file

            if instrument_type == "Case":
                # A court judgment gets analysed as case law, not as
                # legislation - see legislation_summary.CASE_SYSTEM_PROMPT
                # for why (a foreign case, especially South African, is
                # assessed for persuasive authority rather than dismissed
                # outright for not being Zimbabwean legislation).
                result, ai_status, ai_error = legislation_summary.summarize_case_law_update(
                    filepath, is_image, update.extracted_text, update.extraction_status,
                )
                update.ai_status = ai_status
                update.ai_error = ai_error
                update.ai_processed_at = datetime.utcnow()
                if ai_status == "done":
                    update.ai_summary = result["summary"]
                    update.set_ai_key_changes(result["key_holdings"])
                    update.set_ai_suggested_areas(result["suggested_areas"])
                    update.case_citation = result["citation"] or None
                    update.case_court = result["court"] or None
                    update.case_jurisdiction = result["jurisdiction"] or None
                    update.ai_case_authority_status = result["authority_status"]
                    update.ai_case_authority_reasoning = result["authority_reasoning"] or None
            elif instrument_type == "Pub":
                # A professional publication/journal article gets analysed
                # as what it is - not Zimbabwean legislation and not a court
                # judgment - see legislation_summary.PUBLICATION_SYSTEM_PROMPT
                # for why (a South African tax journal issue, for instance,
                # has nowhere honest to go under the legislation-only prompt
                # and comes back mis-framed as though it were a Zimbabwean
                # statutory instrument).
                result, ai_status, ai_error = legislation_summary.summarize_publication_update(
                    filepath, is_image, update.extracted_text, update.extraction_status,
                )
                update.ai_status = ai_status
                update.ai_error = ai_error
                update.ai_processed_at = datetime.utcnow()
                if ai_status == "done":
                    update.ai_summary = result["summary"]
                    update.set_ai_key_changes(result["key_changes"])
                    update.set_ai_suggested_areas(result["suggested_areas"])
            else:
                skip_reason = legislation_summary.skip_summary_reason(instrument_type)
                if skip_reason:
                    update.ai_status = "skipped"
                    update.ai_error = skip_reason
                    update.ai_processed_at = datetime.utcnow()
                else:
                    result, ai_status, ai_error = legislation_summary.summarize_legislative_update(
                        filepath, is_image, update.extracted_text, update.extraction_status,
                    )
                    update.ai_status = ai_status
                    update.ai_error = ai_error
                    update.ai_processed_at = datetime.utcnow()
                    if ai_status == "done":
                        update.ai_summary = result["summary"]
                        update.set_ai_key_changes(result["key_changes"])
                        update.set_ai_suggested_areas(result["suggested_areas"])

            if update.extracted_text:
                legislation_chunking.ensure_chunks(update)

    db.session.commit()

    # a scanned instrument has no text of its own: read it in the background so it becomes searchable ("Ask the library")
    if update.stored_filename and not (update.extracted_text or "").strip():
        import ocr_reader, scan_text
        if ocr_reader.engine_to_use() is not None:
            scan_text.start("legislative_update", update)

    if update.ai_status == "done" and instrument_type == "Case":
        flash(f"'{update.title}' filed - AI analysis generated below, including a suggested authority assessment. Confirm the authority status and practice areas, then tag any clients it affects.", "success")
    elif update.ai_status == "done":
        flash(f"'{update.title}' filed - AI summary generated below. Confirm which practice areas it touches, then tag any clients it affects.", "success")
    elif update.ai_status == "skipped":
        flash(f"'{update.title}' filed - {update.ai_error} Confirm which practice areas it touches, then tag any clients it affects.", "info")
    elif update.ai_status == "not_configured":
        flash(f"'{update.title}' filed, but automatic summarisation isn't set up yet ({update.ai_error}) - add a summary manually below, or reprocess once it's configured.", "warning")
    elif update.ai_status == "error":
        flash(f"'{update.title}' filed, but automatic summarisation failed ({update.ai_error}) - add a summary manually below, or use \"Reprocess\" to try again.", "warning")
    else:
        flash("Legislative update logged.", "success")
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/legislative-updates/<int:update_id>/reprocess", methods=["POST"])
@login_required
@legislative_update_editor_required
def reprocess_legislative_update(update_id):
    """Re-run AI summarisation against an already-filed instrument, without
    re-uploading the file - for when the API key wasn't configured yet at
    filing time, or the previous attempt failed transiently."""
    update = LegislativeUpdate.query.get_or_404(update_id)
    skip_reason = legislation_summary.skip_summary_reason(update.instrument_type)
    if skip_reason:
        flash(f"This entry isn't auto-summarised - {skip_reason}", "info")
        return redirect(url_for("tax.list_legislative_updates"))

    if update.instrument_type == "News":
        # A News entry has no uploaded file to reprocess from - it
        # re-fetches the article fresh from article_url instead, in case the
        # page has changed or the previous fetch/AI attempt failed.
        if not update.article_url:
            flash("This entry has no article URL to fetch - it was only ever logged as text.", "danger")
            return redirect(url_for("tax.list_legislative_updates"))
        text, fetch_error = _fetch_and_extract_news_article(update.article_url)
        update.extraction_status = "extracted" if text else "no_text_found"
        if text:
            update.extracted_text = text
            result, ai_status, ai_error = legislation_summary.summarize_news_update(text, update.article_url)
            update.ai_status = ai_status
            update.ai_error = ai_error
            update.ai_processed_at = datetime.utcnow()
            if ai_status == "done":
                update.ai_summary = result["summary"]
                update.set_ai_key_changes(result["key_changes"])
                update.set_ai_suggested_areas(result["suggested_areas"])
            legislation_chunking.ensure_chunks(update)
        else:
            update.ai_status = "error"
            update.ai_error = fetch_error
            update.ai_processed_at = datetime.utcnow()
        db.session.commit()
        if update.ai_status == "done":
            flash("Reprocessed - the article was re-fetched and the AI summary below has been refreshed.", "success")
        else:
            flash(f"Reprocessing failed: {update.ai_error}", "danger")
        return redirect(url_for("tax.list_legislative_updates"))

    if not update.has_file:
        flash("This entry has no filed instrument to reprocess - it was only ever logged as text.", "danger")
        return redirect(url_for("tax.list_legislative_updates"))
    filepath = os.path.join(_legislative_updates_dir(), update.stored_filename or "")
    if not os.path.exists(filepath):
        flash("The original file can no longer be found on disk - re-file the instrument.", "danger")
        return redirect(url_for("tax.list_legislative_updates"))

    is_image = update.file_ext in LEGISLATIVE_UPDATE_IMAGE_EXTENSIONS
    if update.instrument_type == "Case":
        result, ai_status, ai_error = legislation_summary.summarize_case_law_update(
            filepath, is_image, update.extracted_text, update.extraction_status,
        )
        update.ai_status = ai_status
        update.ai_error = ai_error
        update.ai_processed_at = datetime.utcnow()
        if ai_status == "done":
            update.ai_summary = result["summary"]
            update.set_ai_key_changes(result["key_holdings"])
            update.set_ai_suggested_areas(result["suggested_areas"])
            update.case_citation = result["citation"] or None
            update.case_court = result["court"] or None
            update.case_jurisdiction = result["jurisdiction"] or None
            update.ai_case_authority_status = result["authority_status"]
            update.ai_case_authority_reasoning = result["authority_reasoning"] or None
    elif update.instrument_type == "Pub":
        result, ai_status, ai_error = legislation_summary.summarize_publication_update(
            filepath, is_image, update.extracted_text, update.extraction_status,
        )
        update.ai_status = ai_status
        update.ai_error = ai_error
        update.ai_processed_at = datetime.utcnow()
        if ai_status == "done":
            update.ai_summary = result["summary"]
            update.set_ai_key_changes(result["key_changes"])
            update.set_ai_suggested_areas(result["suggested_areas"])
    else:
        result, ai_status, ai_error = legislation_summary.summarize_legislative_update(
            filepath, is_image, update.extracted_text, update.extraction_status,
        )
        update.ai_status = ai_status
        update.ai_error = ai_error
        update.ai_processed_at = datetime.utcnow()
        if ai_status == "done":
            update.ai_summary = result["summary"]
            update.set_ai_key_changes(result["key_changes"])
            update.set_ai_suggested_areas(result["suggested_areas"])
    db.session.commit()
    if ai_status == "done":
        flash("Reprocessed - the AI summary below has been refreshed.", "success")
    else:
        flash(f"Reprocessing failed: {ai_error}", "danger")
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/legislative-updates/<int:update_id>/areas", methods=["POST"])
@login_required
def save_legislative_update_areas(update_id):
    """Confirm/edit which practice areas this update actually touches - open
    to anyone logged in, same as tagging a client below, since this is
    ordinary practice-relevance judgement rather than governance of the
    library's raw content. Nothing is ever saved here except what a person
    actually submits, even though the form starts pre-ticked from the AI's
    suggested_areas."""
    update = LegislativeUpdate.query.get_or_404(update_id)
    submitted = request.form.getlist("areas")
    update.set_areas([a for a in submitted if a in LEGISLATIVE_UPDATE_AREAS])
    message = "Practice areas updated."
    if update.instrument_type == "Case":
        authority_status = request.form.get("case_authority_status", "").strip()
        update.case_authority_status = authority_status if authority_status in CASE_AUTHORITY_STATUSES else None
        update.case_citation = request.form.get("case_citation", "").strip() or None
        update.case_court = request.form.get("case_court", "").strip() or None
        update.case_jurisdiction = request.form.get("case_jurisdiction", "").strip() or None
        message = "Practice areas and authority assessment updated."
    db.session.commit()
    flash(message, "success")
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/legislative-updates/download/<int:update_id>")
@login_required
def download_legislative_update(update_id):
    update = LegislativeUpdate.query.get_or_404(update_id)
    directory = _legislative_updates_dir()
    if not update.stored_filename or not os.path.exists(os.path.join(directory, update.stored_filename)):
        abort(404)
    return send_from_directory(directory, update.stored_filename, as_attachment=True)


@tax_bp.route("/legislative-updates/<int:update_id>/review", methods=["POST"])
@login_required
def review_legislative_update(update_id):
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    update = LegislativeUpdate.query.get_or_404(update_id)
    update.reviewed_by_id = current_user.id
    update.reviewed_at = datetime.utcnow()
    update.impact_assessment = request.form.get("impact_assessment", update.impact_assessment)
    db.session.commit()
    flash("Legislative update marked as reviewed.", "success")
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/legislative-updates/<int:update_id>/delete", methods=["POST"])
@login_required
@legislative_update_editor_required
def delete_legislative_update(update_id):
    update = LegislativeUpdate.query.get_or_404(update_id)
    if update.stored_filename:
        path = os.path.join(_legislative_updates_dir(), update.stored_filename)
        if os.path.exists(path):
            os.remove(path)
    db.session.delete(update)
    db.session.commit()
    flash("Legislative update deleted.", "info")
    return redirect(url_for("tax.list_legislative_updates"))


# ---------- Legislative Update Control: taggable relevance to clients ----------

@tax_bp.route("/legislative-updates/link-client", methods=["POST"])
@login_required
def link_client_to_update():
    """Tag a client as affected by a logged/filed update - open to anyone
    logged in (see legislative_update_editor_required's docstring above).
    Both the update and the client are taken from the form so the same
    route serves the Legislative Updates library page (choose a client per
    update) and a client's own page (choose an update per client) - an
    optional redirect_client_id form field sends the person back to the
    client's page instead of the library when called from there."""
    update_id = request.form.get("update_id", "").strip()
    update = LegislativeUpdate.query.get(int(update_id)) if update_id.isdigit() else None
    redirect_client_id = request.form.get("redirect_client_id", "").strip()
    client_id = request.form.get("client_id", "").strip()
    client = Client.query.get(int(client_id)) if client_id.isdigit() else None
    if not update or not client:
        flash("Choose both an update and a client to tag.", "danger")
    else:
        existing = LegislativeUpdateClientLink.query.filter_by(
            legislative_update_id=update.id, client_id=client.id,
        ).first()
        if existing:
            flash(f"{client.name} is already tagged against '{update.title}'.", "info")
        else:
            db.session.add(LegislativeUpdateClientLink(
                legislative_update_id=update.id,
                client_id=client.id,
                notes=request.form.get("notes", "").strip() or None,
                linked_by_id=current_user.id,
            ))
            db.session.commit()
            flash(f"{client.name} tagged as affected by '{update.title}'.", "success")
    if redirect_client_id.isdigit():
        return redirect(url_for("clients.view_client", client_id=int(redirect_client_id)))
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/legislative-updates/link/<int:link_id>/delete", methods=["POST"])
@login_required
def unlink_client_from_update(link_id):
    link = LegislativeUpdateClientLink.query.get_or_404(link_id)
    redirect_client_id = request.form.get("redirect_client_id", "").strip()
    db.session.delete(link)
    db.session.commit()
    if redirect_client_id.isdigit():
        return redirect(url_for("clients.view_client", client_id=int(redirect_client_id)))
    return redirect(url_for("tax.list_legislative_updates"))


@tax_bp.route("/<int:engagement_id>/research/import-from-legislative-update", methods=["POST"])
@login_required
def import_research_log_entry_from_legislative_update(engagement_id):
    """One-click pull of a filed/logged legislative update's summary into a
    Tax Advisory engagement's Technical Research Log, instead of retyping
    it - pre-fills a new TaxResearchLogEntry from the chosen LegislativeUpdate
    (its AI summary/key changes when it has one, otherwise its manually-typed
    summary), still fully editable afterwards like any other entry."""
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_advisory(engagement):
        return _tax_redirect(engagement_id)
    update_id = request.form.get("update_id", "").strip()
    update = LegislativeUpdate.query.get(int(update_id)) if update_id.isdigit() else None
    if not update:
        flash("Choose a legislative update to import.", "danger")
        return _tax_redirect(engagement_id)

    reference_bits = [b for b in [update.type_label, update.source_reference] if b]
    conclusion_bits = [update.ai_summary or update.summary or ""]
    if update.ai_key_changes:
        conclusion_bits.append("\n".join(f"- {c}" for c in update.ai_key_changes))
    db.session.add(TaxResearchLogEntry(
        engagement_id=engagement_id,
        topic=update.title,
        question=f"How does '{update.title}' affect this engagement?",
        research_notes=update.ai_summary or update.summary or "",
        conclusion="\n\n".join(b for b in conclusion_bits if b),
        references=" - ".join(reference_bits) if reference_bits else update.title,
        prepared_by_id=current_user.id,
    ))
    db.session.commit()
    flash(f"'{update.title}' imported into the Technical Research Log - edit it below as needed.", "success")
    return _tax_redirect(engagement_id)
