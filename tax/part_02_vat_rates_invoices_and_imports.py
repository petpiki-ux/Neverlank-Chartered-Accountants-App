"""tax, part 2 of 4.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file tax.py did."""

def _resolve_vat_amount_and_rate(taxable_amount, vat_rate, vat_amount_input):
    """Given the selected firm-wide VATRate (or None) and whatever the
    preparer typed into the VAT amount field (or None if left blank),
    returns (vat_amount, rate_percent_used) to save on the VATInvoice.
    Prefers the invoice's own stated VAT amount when one was entered - a
    real invoice's VAT can differ from a plain rate x amount calculation by
    a cent or two of rounding, and this is a register of what the invoice
    actually says - but still records a rate_percent_used either way (the
    selected rate, or the rate implied by the entered amounts) so the
    working paper can always show which invoices used less than the
    standard rate."""
    if vat_amount_input is not None:
        if vat_rate is not None:
            rate_percent_used = vat_rate.rate_percent
        elif taxable_amount:
            rate_percent_used = round((vat_amount_input / taxable_amount) * 100, 4)
        else:
            rate_percent_used = None
        return vat_amount_input, rate_percent_used
    if vat_rate is not None:
        return round((taxable_amount or 0.0) * (vat_rate.rate_percent / 100.0), 2), vat_rate.rate_percent
    return 0.0, None


@tax_bp.route("/vat-rates")
@login_required
def list_vat_rates():
    rates = VATRate.query.order_by(VATRate.rate_type, VATRate.effective_from.desc()).all()
    return render_template(
        "tax/vat_rates.html", rates=rates, rate_types=VAT_RATE_TYPES,
        can_manage=user_has_permission(current_user, "manage_checklist_templates"),
    )


@tax_bp.route("/vat-rates/add", methods=["POST"])
@login_required
def add_vat_rate():
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    effective_from = _parse_date(request.form.get("effective_from"))
    rate_percent = _parse_float(request.form.get("rate_percent"))
    if not effective_from or rate_percent is None:
        flash("Please enter both an effective date and a rate.", "danger")
        return redirect(url_for("tax.list_vat_rates"))
    rate = VATRate(
        rate_type=request.form.get("rate_type") or "Standard",
        rate_percent=rate_percent,
        effective_from=effective_from,
        effective_to=_parse_date(request.form.get("effective_to")),
        notes=request.form.get("notes", "").strip(),
        entered_by_id=current_user.id,
    )
    db.session.add(rate)
    db.session.commit()
    flash("VAT rate added.", "success")
    return redirect(url_for("tax.list_vat_rates"))


@tax_bp.route("/vat-rates/<int:rate_id>/delete", methods=["POST"])
@login_required
def delete_vat_rate(rate_id):
    if not user_has_permission(current_user, "manage_checklist_templates"):
        abort(403)
    rate = VATRate.query.get_or_404(rate_id)
    db.session.delete(rate)
    db.session.commit()
    return redirect(url_for("tax.list_vat_rates"))


@tax_bp.route("/<int:engagement_id>/vat/invoices/add", methods=["POST"])
@login_required
def add_vat_invoice(engagement_id):
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    resolved = resolve_vat_kind(request.form.get("kind"), request.form.get("direction"))
    if not resolved:
        flash("Please choose what you are capturing - a sales invoice, export sale, credit note, purchase invoice or Bill of Entry.", "danger")
        return _tax_redirect(engagement_id)
    direction, doc_type = resolved
    taxable_amount = _parse_float(request.form.get("taxable_amount"))
    if taxable_amount is None:
        flash("Please enter the taxable amount (excluding VAT).", "danger")
        return _tax_redirect(engagement_id)
    rate_id = request.form.get("vat_rate_id") or None
    vat_rate = VATRate.query.get(int(rate_id)) if rate_id else None
    vat_amount_input = _parse_float(request.form.get("vat_amount"))
    if doc_type == "Export Sale":
        # Exports are zero-rated: the value is reported, the VAT is always 0.
        zero_rate = (VATRate.query.filter_by(rate_type="Zero-rated")
                     .order_by(VATRate.effective_from.desc()).first())
        vat_rate = zero_rate
        vat_amount, rate_percent_used = 0.0, 0.0
    else:
        vat_amount, rate_percent_used = _resolve_vat_amount_and_rate(taxable_amount, vat_rate, vat_amount_input)
    if doc_type == "Credit Note":
        # Figures are captured as printed on the credit note and subtracted in
        # the totals - so never let a minus sign double-count.
        taxable_amount, vat_amount = abs(taxable_amount), abs(vat_amount or 0.0)
    invoice_date = _parse_date(request.form.get("invoice_date"))
    locked = vat_locked_months_among(engagement_id, [invoice_date])
    if locked:
        flash(_locked_message(locked, "a document dated in it cannot be added"), "danger")
        return _tax_redirect(engagement_id, _vat_doc_period_arg(invoice_date))
    invoice = VATInvoice(
        engagement_id=engagement_id,
        direction=direction,
        doc_type=doc_type,
        currency=normalize_vat_currency(request.form.get("currency"), VAT_DEFAULT_CURRENCY),
        related_reference=request.form.get("related_reference", "").strip() or None,
        customs_duty=_parse_float(request.form.get("customs_duty")) if doc_type == "Bill of Entry" else None,
        invoice_date=invoice_date,
        invoice_number=request.form.get("invoice_number", "").strip() or None,
        counterparty_name=request.form.get("counterparty_name", "").strip() or None,
        description=request.form.get("description", "").strip() or None,
        taxable_amount=taxable_amount,
        vat_rate_id=vat_rate.id if vat_rate else None,
        vat_rate_percent_used=rate_percent_used,
        vat_amount=vat_amount,
        source="manual",
        created_by_id=current_user.id,
    )
    db.session.add(invoice)
    db.session.commit()
    flash(f"{VAT_KIND_LABELS.get(invoice.kind) or VAT_INVOICE_DIRECTION_LABELS.get(direction, direction)} captured.", "success")
    return _tax_redirect(engagement_id, _vat_doc_period_arg(invoice_date))


@tax_bp.route("/vat/invoices/<int:invoice_id>/delete", methods=["POST"])
@login_required
def delete_vat_invoice(invoice_id):
    invoice = VATInvoice.query.get_or_404(invoice_id)
    _ensure_engagement_access(invoice.engagement)
    engagement_id = invoice.engagement_id
    period_arg = _vat_doc_period_arg(invoice.invoice_date)
    locked = vat_locked_months_among(engagement_id, [invoice.invoice_date])
    if locked:
        flash(_locked_message(locked, "this document cannot be deleted"), "danger")
        return _tax_redirect(engagement_id, period_arg)
    db.session.delete(invoice)
    db.session.commit()
    return _tax_redirect(engagement_id, period_arg)


# The fields a VAT Output/Input Tax schedule import can populate, and the
# header-name synonyms used to suggest a mapping for each one - the same
# "heuristic suggests, person confirms" pattern as
# financials.suggest_fs_category, never applied on its own (see
# _suggest_vat_column_mapping and models.VATImportBatch's docstring).
VAT_SCHEDULE_FIELDS = [
    ("invoice_date", "Invoice date"),
    ("invoice_number", "Invoice number"),
    ("counterparty_name", "Customer / supplier name"),
    ("description", "Description"),
    ("taxable_amount", "Taxable amount (excl. VAT)"),
    ("vat_amount", "VAT amount"),
    ("rate_percent", "VAT rate % (only used to compute the VAT amount when that column is blank)"),
    ("related_reference", "Original invoice # (credit notes) / customs or export entry ref"),
    ("customs_duty", "Customs duty (Bills of Entry only)"),
    ("currency", "Currency column (ZWG / USD) - optional, overrides the currency chosen at upload"),
]
VAT_SCHEDULE_REQUIRED_FIELDS = {"taxable_amount"}

VAT_SCHEDULE_FIELD_SYNONYMS = {
    "invoice_date": ["invoice date", "date", "trans date", "transaction date", "tax point", "tax point date"],
    "invoice_number": ["bill of entry no", "bill of entry number", "boe no", "boe number", "boe #", "credit note no", "credit note number", "credit note #", "cn no", "cn number", "export invoice no", "invoice no", "invoice number", "inv no", "invoice #", "reference", "ref", "ref no", "document no", "doc no"],
    "counterparty_name": ["customer", "customer name", "client", "client name", "supplier", "supplier name", "vendor", "vendor name", "counterparty", "name", "trading name"],
    "description": ["description", "particulars", "details", "narrative", "goods/services", "nature of supply"],
    "taxable_amount": ["taxable value", "vat exclusive", "exclusive amount", "excl. vat", "ex vat", "nett", "net value", "net sales", "net purchases", "customs value", "vat value", "taxable amount", "amount excl vat", "amount excluding vat", "net amount", "excl vat", "net", "value excl vat", "sales excl vat", "purchases excl vat", "amount (excl. vat)"],
    "vat_amount": ["vat amt", "vat value", "output tax", "input tax", "import vat", "vat paid", "vat amount", "vat", "tax amount", "output vat", "input vat", "vat charged", "vat claimed", "tax"],
    "rate_percent": ["vat rate", "rate", "vat %", "tax rate", "rate %", "vat rate %"],
    "related_reference": ["original invoice", "original invoice no", "against invoice", "credited invoice", "related invoice", "customs entry no", "entry reference", "customs entry", "export entry", "export reference", "customs reference", "mrn"],
    "customs_duty": ["customs duty", "duty", "import duty", "duty paid"],
    "currency": ["currency", "currency code", "ccy", "curr", "trade currency"],
}


def _suggest_vat_column_mapping(headers):
    """Best-effort column-name matching for an uploaded Output/Input Tax
    schedule - never guaranteed correct, and never itself imports anything;
    the preparer reviews/adjusts every suggestion on the confirmation
    screen before any VATInvoice row is created. Prefers an exact (case-
    insensitive) header match over a mere substring match, and never
    suggests the same column for two different fields."""
    lower_headers = {h: h.strip().lower() for h in headers if h and str(h).strip()}
    mapping = {}
    used = set()
    for field, _ in VAT_SCHEDULE_FIELDS:
        synonyms = VAT_SCHEDULE_FIELD_SYNONYMS[field]
        best = None
        for h, lh in lower_headers.items():
            if h in used:
                continue
            if lh in synonyms:
                best = h
                break
        if not best:
            for h, lh in lower_headers.items():
                if h in used:
                    continue
                if any(s in lh for s in synonyms):
                    best = h
                    break
        mapping[field] = best
        if best:
            used.add(best)
    return mapping


def _vat_header_score(cells):
    """How many of a row's non-empty cells look like VAT schedule column
    headings (date / invoice no / customer / amount excl. VAT / VAT ...). Used
    to find the real header row in a schedule that has a title, company name
    or blank lines above it - which a schedule exported from an accounting
    system nearly always does."""
    score = 0
    for c in cells:
        text = str(c).strip().lower() if c is not None else ""
        if not text:
            continue
        for synonyms in VAT_SCHEDULE_FIELD_SYNONYMS.values():
            if text in synonyms or any(len(s) > 3 and s in text for s in synonyms):
                score += 1
                break
    return score


def _vat_rows_to_table(all_rows):
    """(headers, rows, score) from a sheet's raw rows (list of lists): finds
    the header row (the earliest row among the first 40 that looks most like
    VAT column headings; failing that the first row with 2+ filled cells),
    de-duplicates repeated headings, and returns each later row as a
    {heading: value} dict, skipping completely empty rows."""
    best_i, best_score = None, 0
    for i, row in enumerate(all_rows[:40]):
        filled = [c for c in row if c is not None and str(c).strip() != ""]
        if len(filled) < 2:
            continue
        score = _vat_header_score(filled)
        if score > best_score:
            best_i, best_score = i, score
    if best_i is None:
        for i, row in enumerate(all_rows[:40]):
            if len([c for c in row if c is not None and str(c).strip() != ""]) >= 2:
                best_i = i
                break
    if best_i is None:
        return [], [], 0
    headers, seen = [], {}
    for c in all_rows[best_i]:
        h = str(c).strip() if c is not None else ""
        if h and h in seen:
            seen[h] += 1
            h = f"{h} ({seen[h]})"
        elif h:
            seen[h] = 1
        headers.append(h)
    rows = []
    for row in all_rows[best_i + 1:]:
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        rows.append({h: v for h, v in zip(headers, row) if h})
    return [h for h in headers if h], rows, best_score


def _read_vat_schedule_rows(filepath, filename):
    """Returns (headers, rows): the file's own column headers in order, and
    a list of {header: value} dicts, for an uploaded Output/Input Tax
    schedule already saved to `filepath`. Tolerates what real exports look
    like: title/company lines or blank rows above the headings (the header
    row is found, not assumed to be row 1), several worksheets (the one with
    the best-looking headings wins), old .xls files, and semicolon/tab
    delimited CSVs. Raises ValueError with a plain-English message if
    nothing usable is found."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "csv":
        with open(filepath, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            text = f.read()
        # Pick the delimiter that actually separates columns on most of the
        # first lines (a title line above the headings defeats csv.Sniffer).
        sample_lines = text.splitlines()[:40]
        delimiter = max(",;\t|", key=lambda d: sum(1 for ln in sample_lines if ln.count(d) >= 2))
        if not any(ln.count(delimiter) >= 2 for ln in sample_lines):
            delimiter = ","
        all_rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
        sheets = [all_rows]
    elif ext == "xlsx":
        workbook = openpyxl.load_workbook(filepath, data_only=True)
        sheets = [list(ws.iter_rows(values_only=True)) for ws in workbook.worksheets]
    elif ext == "xls":
        try:
            import xlrd
        except ImportError:
            raise ValueError("Old Excel (.xls) files need one extra step on this server - please open the file in Excel, choose Save As > Excel Workbook (.xlsx) (or CSV), and upload that instead.")
        try:
            book = xlrd.open_workbook(filepath)
        except Exception:
            raise ValueError("That .xls file could not be read - please open it in Excel and Save As an Excel Workbook (.xlsx) or CSV, then upload that.")
        sheets = []
        for sh in book.sheets():
            sheet_rows = []
            for r in range(sh.nrows):
                row = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            value = xlrd.xldate.xldate_as_datetime(value, book.datemode)
                        except Exception:
                            pass
                    elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        value = None
                    row.append(value)
                sheet_rows.append(row)
            sheets.append(sheet_rows)
    else:
        raise ValueError("Please upload an Excel (.xlsx/.xls) or CSV file.")

    best = ([], [], -1)
    for sheet_rows in sheets:
        if not sheet_rows:
            continue
        table = _vat_rows_to_table(sheet_rows)
        # prefer the sheet whose headings look most like a VAT schedule; on a
        # tie keep the earlier sheet; a sheet with data beats one without
        if (table[2], len(table[1]) > 0) > (best[2], len(best[1]) > 0):
            best = table
    headers, rows, _score = best
    if not headers:
        raise ValueError("Could not find a header row in that file - somewhere near the top there should be a row of column headings (e.g. Date, Invoice No, Customer, Amount excl. VAT, VAT).")
    return headers, rows


_VAT_TOTAL_ROW = re.compile(r"^\s*(grand\s+|sub[\s-]*|net\s+|report\s+)?totals?\s*(for\b.*|carried\s+forward|c/?f|b/?f)?\s*[:\-]?\s*$", re.IGNORECASE)


def _is_vat_total_row(row):
    """A 'Total' / 'Sub-total' / 'Grand total' line at the foot of a
    schedule - never a real document, and importing it would double-count
    every figure above it."""
    return any(isinstance(v, str) and _VAT_TOTAL_ROW.match(v) for v in row.values())


def _parse_vat_schedule_amount(value):
    """Parses one spreadsheet cell into a float, tolerant of blanks,
    currency-formatted text ("$1,234.50", "USD 1 234.50", "ZWG1,234.50"),
    bracketed or trailing-minus negatives ("(1,234.50)", "1,234.50-") and
    non-breaking spaces - a schedule from another system is never
    guaranteed to hand back plain numbers. Returns None (never raises) if
    it can't be read as a number at all."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\u00a0", " ").strip()
    if not text:
        return None
    negative = (text.startswith("(") and text.endswith(")")) or text.endswith("-") or text.startswith("-")
    cleaned = re.sub(r"[^0-9.]", "", text.replace(",", ""))
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        amount = abs(float(cleaned))
    except ValueError:
        return None
    return -amount if negative else amount


def _norm_vat_header(text):
    """Header text normalised for matching: case, runs of whitespace
    (including line breaks inside a wrapped Excel heading) ignored."""
    return " ".join(str(text or "").split()).lower()


def _resolve_vat_mapping_header(posted, headers):
    """The real file header a posted <select> value stands for, or None.
    Browsers normalise line breaks and the server strips whitespace, so an
    exact comparison can miss a heading like "Amount\n(excl. VAT)"; match
    exactly first, then on normalised text."""
    if not posted:
        return None
    if posted in headers:
        return posted
    wanted = _norm_vat_header(posted)
    for h in headers:
        if _norm_vat_header(h) == wanted:
            return h
    return None


def _parse_vat_schedule_date(value):
    """Parses one spreadsheet cell into a date - openpyxl already hands
    back a real datetime/date object for a genuine Excel date cell; this
    only has to additionally handle a CSV's plain text date in a few common
    layouts. Returns None (never raises) if it can't be read as a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _require_vat_schedule_permission():
    """Uploading/mapping/confirming/deleting a VAT schedule import needs the
    "manage_vat_schedules" permission (Supervisor, Partner and Admin by
    default; an admin can change this per role or person)."""
    if not user_has_permission(current_user, "manage_vat_schedules"):
        abort(403, description=(
            "Your account is not allowed to upload or change VAT schedules. This is limited to Supervisors, "
            "Partners and Admins by default - an Admin can grant it to you under Team > Permissions or your Access Rights."
        ))


def _render_vat_mapping_screen(engagement, direction, doc_type, staged_name, original_name, headers, rows, mapping, problem=None, currency=VAT_DEFAULT_CURRENCY):
    """The "confirm column mapping" screen. Used for the first look at an
    uploaded file AND to send the preparer straight back to it - with their
    choices intact and the staged file kept - when the confirm step can't
    import anything, so a bad mapping never just dumps them back on the tab
    with their upload gone."""
    code = vat_kind_code(direction, doc_type)
    return render_template(
        "tax/vat_import_confirm.html",
        engagement=engagement, direction=direction, kind=code,
        direction_label=VAT_KIND_LABELS.get(code) or VAT_INVOICE_DIRECTION_LABELS.get(direction, direction),
        staged_filename=staged_name, original_filename=original_name,
        headers=headers, preview_rows=rows[:10], total_row_count=len(rows),
        schedule_fields=VAT_SCHEDULE_FIELDS, suggested_mapping=mapping, problem=problem,
        currency=currency,
    )


@tax_bp.route("/<int:engagement_id>/vat/import/stage", methods=["POST"])
@login_required
def stage_vat_schedule_import(engagement_id):
    """Step 1 of the schedule import: save the upload, read its headers,
    suggest a column mapping, and show the preparer a confirmation screen -
    nothing is imported yet. See models.VATImportBatch's docstring for why
    this is a two-step flow."""
    _require_vat_schedule_permission()
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    resolved = resolve_vat_kind(request.form.get("kind"), request.form.get("direction"))
    if not resolved:
        flash("Please choose what kind of schedule this is - sales invoices, export sales, credit notes, purchase invoices or Bills of Entry.", "danger")
        return _tax_redirect(engagement_id)
    direction, doc_type = resolved
    raw_currency = request.form.get("currency", "").strip()
    currency = normalize_vat_currency(raw_currency, VAT_DEFAULT_CURRENCY if not raw_currency else None)
    if not currency:
        flash("Please choose the currency of trade this schedule is in (ZWG or USD) - ZIMRA requires VAT to be accounted for in the currency of trade.", "danger")
        return _tax_redirect(engagement_id)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to import.", "danger")
        return _tax_redirect(engagement_id)
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("xlsx", "xls", "csv"):
        flash("Only Excel (.xlsx/.xls) or CSV files are accepted for a schedule import.", "danger")
        return _tax_redirect(engagement_id)

    original_name = secure_filename(file.filename)
    staged_name = f"vatstage_{engagement_id}_{uuid.uuid4().hex[:8]}_{original_name}"
    staged_path = os.path.join(_vat_imports_dir(), staged_name)
    file.save(staged_path)

    try:
        headers, rows = _read_vat_schedule_rows(staged_path, original_name)
    except ValueError as exc:
        os.remove(staged_path)
        flash(str(exc), "danger")
        return _tax_redirect(engagement_id)

    if not rows:
        os.remove(staged_path)
        flash("That file has a header row but no data rows to import.", "danger")
        return _tax_redirect(engagement_id)

    return _render_vat_mapping_screen(
        engagement, direction, doc_type, staged_name, original_name, headers, rows,
        _suggest_vat_column_mapping(headers), currency=currency,
    )


@tax_bp.route("/<int:engagement_id>/vat/import/confirm", methods=["POST"])
@login_required
def confirm_vat_schedule_import(engagement_id):
    """Step 2: the preparer's confirmed (or corrected) column mapping is
    actually applied, row by row, and a VATImportBatch + one VATInvoice per
    readable row are created. Re-reads the staged file rather than trusting
    anything posted from the browser about its contents."""
    _require_vat_schedule_permission()
    engagement = _get_tax_engagement(engagement_id)
    if not _require_tax_module(engagement):
        return _tax_redirect(engagement_id)
    resolved = resolve_vat_kind(request.form.get("kind"), request.form.get("direction"))
    direction, doc_type = resolved if resolved else ("", "Invoice")
    currency = normalize_vat_currency(request.form.get("currency"), VAT_DEFAULT_CURRENCY)
    staged_filename = secure_filename(request.form.get("staged_filename", "").strip())
    original_filename = request.form.get("original_filename", "").strip()
    staged_path = os.path.join(_vat_imports_dir(), staged_filename) if staged_filename else ""
    if not resolved or not staged_filename or not os.path.exists(staged_path):
        flash("That import session has expired or the staged file could not be found - please upload the file again.", "danger")
        return _tax_redirect(engagement_id)

    try:
        headers, rows = _read_vat_schedule_rows(staged_path, original_filename or staged_filename)
    except ValueError as exc:
        os.remove(staged_path)
        flash(str(exc), "danger")
        return _tax_redirect(engagement_id)

    posted = {field: (request.form.get(f"map_{field}", "").strip() or None) for field, _ in VAT_SCHEDULE_FIELDS}
    mapping = {field: _resolve_vat_mapping_header(value, headers) for field, value in posted.items()}

    def _back_to_mapping(message):
        # keep the staged file and the preparer's choices; explain what to fix
        return _render_vat_mapping_screen(
            engagement, direction, doc_type, staged_filename, original_filename or staged_filename,
            headers, rows, mapping, problem=message, currency=currency,
        )

    missing_required = [label for field, label in VAT_SCHEDULE_FIELDS if field in VAT_SCHEDULE_REQUIRED_FIELDS and not mapping.get(field)]
    if missing_required:
        return _back_to_mapping(f"Please choose which column holds: {', '.join(missing_required)} - it is required to import a schedule.")

    # Dry run first: nothing is saved unless at least one row is readable.
    readable = [r for r in rows if not _is_vat_total_row(r) and _parse_vat_schedule_amount(r.get(mapping["taxable_amount"])) is not None]
    if not readable:
        sample = next((r.get(mapping["taxable_amount"]) for r in rows if r.get(mapping["taxable_amount"]) not in (None, "")), None)
        hint = f' The first value found in the "{mapping["taxable_amount"]}" column is "{sample}".' if sample is not None else f' The "{mapping["taxable_amount"]}" column is empty in every row.'
        return _back_to_mapping(
            f"Nothing was imported: none of the {len(rows)} row(s) has a readable number in the column mapped to Taxable amount.{hint} "
            "Check that Taxable amount is the column holding the amount excluding VAT, then confirm again."
        )

    # A schedule can't put documents into a month that is already marked
    # 'Computation done'. Checked BEFORE anything is saved, and the staged file
    # is kept so the preparer can reopen the month (or fix the file) and retry.
    if mapping.get("invoice_date"):
        row_dates = [_parse_vat_schedule_date(r.get(mapping["invoice_date"])) for r in readable]
        locked = vat_locked_months_among(engagement_id, row_dates)
        if locked:
            return _back_to_mapping(
                "Nothing was imported. " + _locked_message(locked, "this schedule cannot be imported because it has documents dated in "
                                                                  + ("those months" if len(locked) > 1 else "that month"))
                + " Reopen the month (or remove those rows from the file and upload it again), then confirm.")

    # Move the staged file into a permanent, engagement-scoped name for the
    # audit trail rather than leaving it under its transient staging name -
    # the same "keep the original filed document" approach as every other
    # upload in this app.
    stored_name = f"vat{engagement_id}_{uuid.uuid4().hex[:8]}_{secure_filename(original_filename or staged_filename)}"
    stored_path = os.path.join(_vat_imports_dir(), stored_name)
    os.replace(staged_path, stored_path)

    batch = VATImportBatch(
        engagement_id=engagement_id,
        direction=direction,
        doc_type=doc_type,
        currency=currency,
        original_filename=original_filename or staged_filename,
        stored_filename=stored_name,
        column_mapping_json=json.dumps(mapping),
        imported_by_id=current_user.id,
    )
    db.session.add(batch)
    db.session.flush()  # assign batch.id before creating its invoices

    imported = 0
    skipped = 0
    for row in rows:
        if _is_vat_total_row(row):
            skipped += 1
            continue
        taxable_amount = _parse_vat_schedule_amount(row.get(mapping["taxable_amount"])) if mapping.get("taxable_amount") else None
        if taxable_amount is None:
            skipped += 1
            continue
        vat_amount = _parse_vat_schedule_amount(row.get(mapping["vat_amount"])) if mapping.get("vat_amount") else None
        rate_percent = _parse_vat_schedule_amount(row.get(mapping["rate_percent"])) if mapping.get("rate_percent") else None
        if vat_amount is None and rate_percent is not None:
            vat_amount = round(taxable_amount * (rate_percent / 100.0), 2)
        if doc_type == "Export Sale":
            vat_amount, rate_percent = 0.0, 0.0  # zero-rated, whatever the file says
        if doc_type == "Credit Note":
            taxable_amount, vat_amount = abs(taxable_amount), abs(vat_amount or 0.0)  # subtracted in the totals

        def _mapped_text(field):
            col = mapping.get(field)
            if not col:
                return None
            value = row.get(col)
            return str(value).strip() or None if value not in (None, "") else None

        invoice = VATInvoice(
            engagement_id=engagement_id,
            direction=direction,
            doc_type=doc_type,
            currency=normalize_vat_currency(row.get(mapping["currency"]), currency) if mapping.get("currency") else currency,
            related_reference=_mapped_text("related_reference"),
            customs_duty=(_parse_vat_schedule_amount(row.get(mapping["customs_duty"])) if (doc_type == "Bill of Entry" and mapping.get("customs_duty")) else None),
            invoice_date=_parse_vat_schedule_date(row.get(mapping["invoice_date"])) if mapping.get("invoice_date") else None,
            invoice_number=_mapped_text("invoice_number"),
            counterparty_name=_mapped_text("counterparty_name"),
            description=_mapped_text("description"),
            taxable_amount=taxable_amount,
            vat_rate_percent_used=rate_percent,
            vat_amount=vat_amount or 0.0,
            source="import",
            import_batch_id=batch.id,
            created_by_id=current_user.id,
        )
        db.session.add(invoice)
        imported += 1

    batch.row_count = imported
    batch.skipped_row_count = skipped
    db.session.commit()

    if skipped:
        flash(f"Imported {imported} {VAT_KIND_LABELS.get(vat_kind_code(direction, doc_type), direction).lower()} row(s) - {skipped} row(s) were skipped (no readable taxable amount).", "warning")
    else:
        flash(f"Imported {imported} {VAT_KIND_LABELS.get(vat_kind_code(direction, doc_type), direction).lower()} row(s).", "success")
    return _tax_redirect(engagement_id)


@tax_bp.route("/vat/invoices/<int:invoice_id>/currency", methods=["POST"])
@login_required
def set_vat_invoice_currency(invoice_id):
    """Re-tag one VAT document's currency of trade (e.g. a row captured
    before currencies were recorded, which are treated as USD)."""
    invoice = VATInvoice.query.get_or_404(invoice_id)
    _ensure_engagement_access(invoice.engagement)
    period_arg = _vat_doc_period_arg(invoice.invoice_date)
    locked = vat_locked_months_among(invoice.engagement_id, [invoice.invoice_date])
    if locked:
        flash(_locked_message(locked, "this document's currency cannot be changed"), "danger")
        return _tax_redirect(invoice.engagement_id, period_arg)
    currency = normalize_vat_currency(request.form.get("currency"))
    if currency:
        invoice.currency = currency
        db.session.commit()
        flash(f"Document moved to the {currency} VAT working paper.", "success")
    return _tax_redirect(invoice.engagement_id, period_arg)


@tax_bp.route("/vat/invoices/<int:invoice_id>/denial", methods=["POST"])
@login_required
def set_vat_input_denial(invoice_id):
    """Mark one input document's VAT as not deductible (VAT Act section 16(2)), or put it back. The document stays in the
    register; only the claim changes. A completed month is locked like every other change to it."""
    invoice = VATInvoice.query.get_or_404(invoice_id)
    _ensure_engagement_access(invoice.engagement)
    period_arg = _vat_doc_period_arg(invoice.invoice_date)
    locked = vat_locked_months_among(invoice.engagement_id, [invoice.invoice_date])
    if locked:
        flash(_locked_message(locked, "this document's deduction cannot be changed"), "danger")
        return _tax_redirect(invoice.engagement_id, period_arg)
    if invoice.direction != "Input":
        flash("Only input tax (purchases, imports and credit notes received) can be marked as not deductible.", "danger")
        return _tax_redirect(invoice.engagement_id, period_arg)
    if request.form.get("action") == "restore":
        invoice.input_tax_denied = False
        invoice.denial_ground = None
        flash("The document's VAT is back in the Input Tax claim.", "success")
    else:
        ground = request.form.get("ground") or "other"
        if ground not in VAT_DENIAL_LABELS:
            ground = "other"
        invoice.input_tax_denied = True
        invoice.denial_ground = ground
        flash("The document's VAT is left out of the Input Tax claim (%s, section %s of the VAT Act)." % (
            VAT_DENIAL_LABELS[ground], dict((k, r) for k, _l, r in VAT_DENIAL_GROUNDS)[ground]), "success")
    db.session.commit()
    return _tax_redirect(invoice.engagement_id, period_arg)
