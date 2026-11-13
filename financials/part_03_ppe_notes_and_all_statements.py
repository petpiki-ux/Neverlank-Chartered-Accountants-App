"""financials, part 3 of 3.  Holds: _PolicyText.

See __init__.py: all parts share one set of names, exactly as the single file financials.py did."""

def build_ppe_movement_schedule(asset_classes, assets, period_end):
    """Builds the Property, Plant and Equipment movement schedule (IAS
    16.73(e): a reconciliation of the carrying amount at the beginning and
    end of the period) from the Asset Register, grouped by Depreciation
    policy asset class - both plain lists of dicts (see PPEAssetClass/
    PPEAsset in models.py; the caller converts the ORM rows), kept this way
    so the maths can be unit tested without a database, exactly like every
    other function in this module.

    asset_classes: [{"id", "name"}, ...] (id may be None for "Unclassified")
    assets: [{"asset_class_id", "cost", "opening_accumulated_depreciation",
              "current_year_depreciation", "disposal_cost",
              "disposal_accumulated_depreciation", "date_acquired"}, ...]

    Whether an asset's cost is an opening balance or a current-year
    addition is decided by date_acquired against _infer_period_start()
    above; an asset with no date_acquired is treated as an opening balance
    (the conservative assumption - nothing marks it as new this year).

    Returns None if `assets` is empty, so the caller can fall back to the
    plain trial-balance-account note instead. Otherwise returns
    {"by_class": [...], "total": {...}}, each row holding opening_cost,
    additions, disposals_cost, closing_cost, opening_acc_dep, charge,
    disposals_acc_dep, closing_acc_dep, opening_nbv, closing_nbv - built so
    that, for every row, opening_nbv + additions - disposals (cost less
    accumulated depreciation eliminated) - charge always equals
    closing_nbv exactly, term for term."""
    if not assets:
        return None
    period_start = _infer_period_start(period_end)

    def _blank():
        return {
            "opening_cost": 0.0, "additions": 0.0, "disposals_cost": 0.0, "closing_cost": 0.0,
            "opening_acc_dep": 0.0, "charge": 0.0, "disposals_acc_dep": 0.0, "closing_acc_dep": 0.0,
            "opening_nbv": 0.0, "closing_nbv": 0.0,
        }

    def _accumulate(row, a):
        cost = a.get("cost") or 0.0
        disposal_cost = a.get("disposal_cost") or 0.0
        charge = a.get("current_year_depreciation") or 0.0
        disposal_acc_dep = a.get("disposal_accumulated_depreciation") or 0.0
        date_acquired = a.get("date_acquired")
        is_addition = bool(
            date_acquired and period_start and period_start <= date_acquired <= (period_end or date_acquired)
        )
        opening_cost = 0.0 if is_addition else cost
        addition = cost if is_addition else 0.0
        # An asset added this year can't have brought-forward accumulated
        # depreciation, whatever the field holds - zeroed here so a stray
        # figure can't distort the opening NBV.
        opening_acc_dep = 0.0 if is_addition else (a.get("opening_accumulated_depreciation") or 0.0)
        closing_cost = cost - disposal_cost
        closing_acc_dep = opening_acc_dep + charge - disposal_acc_dep
        row["opening_cost"] += opening_cost
        row["additions"] += addition
        row["disposals_cost"] += disposal_cost
        row["closing_cost"] += closing_cost
        row["opening_acc_dep"] += opening_acc_dep
        row["charge"] += charge
        row["disposals_acc_dep"] += disposal_acc_dep
        row["closing_acc_dep"] += closing_acc_dep
        row["opening_nbv"] += opening_cost - opening_acc_dep
        row["closing_nbv"] += closing_cost - closing_acc_dep

    class_order = {c.get("id"): i for i, c in enumerate(asset_classes)}
    class_name = {c.get("id"): c.get("name") for c in asset_classes}

    rows_by_class = {}
    total = _blank()
    for a in assets:
        cid = a.get("asset_class_id")
        rows_by_class.setdefault(cid, _blank())
        _accumulate(rows_by_class[cid], a)
        _accumulate(total, a)

    by_class = [
        {"class_id": cid, "class_name": class_name.get(cid, "Unclassified") or "Unclassified", **row}
        for cid, row in rows_by_class.items()
    ]
    by_class.sort(key=lambda r: (class_order.get(r["class_id"], len(class_order)), r["class_name"]))

    return {"by_class": by_class, "total": total}


class _PolicyText(str):
    """The flat policy sentence(s), plus `.parts` - the same policy broken into paragraphs and a table of the depreciation bases, for the
    Word pack and the on-screen notes (a table reads far better than a run-on list of classes)."""
    parts = None


def ppe_accounting_policy_text(asset_classes):
    """Builds the Property, plant and equipment accounting policy note from
    the Depreciation policy working paper (models.PPEAssetClass, converted
    to plain dicts by the caller: [{"name", "depreciation_method",
    "rate_percent", "useful_life_years"}, ...]) instead of the generic
    boilerplate in NOTE_DEFINITIONS, once at least one asset class has been
    defined. Returns None if `asset_classes` is empty, so the caller falls
    back to the standard boilerplate untouched. The result is a str (one
    flowing paragraph) carrying `.parts`: {"paragraphs", "table", "after"}."""
    if not asset_classes:
        return None
    first = ("Property, plant and equipment are stated at cost less accumulated depreciation and any "
             "accumulated impairment losses.")
    second = ("Depreciation is charged so as to write down the cost of each class of asset to its estimated "
              "residual value over its estimated useful life, on the following bases:")
    after = "Useful lives and residual values are reviewed, and adjusted if appropriate, at each reporting date."
    bases = []
    rows = []
    for c in asset_classes:
        method = c.get("depreciation_method") or "Straight-line"
        details = []
        rate = c.get("rate_percent")
        life = c.get("useful_life_years")
        if rate is not None:
            details.append(f"{rate:g}% per annum")
        if life is not None:
            details.append(f"{life:g} years")
        detail_str = " (" + ", ".join(details) + ")" if details else ""
        name = c.get("name", "Unclassified")
        bases.append(f"{name} - {method.lower()}{detail_str}")
        rows.append([
            name.strip().title() if name.strip().isupper() else name,
            method[:1].upper() + method[1:],
            f"{rate:g}%" if rate is not None else "\u2014",
            f"{life:g} years" if life is not None else "\u2014",
        ])
    # a class with neither a rate nor a useful life (nothing to disclose) is left out of the table and the sentence, unless that is every class
    keep = [i for i, r in enumerate(rows) if r[2] != "\u2014" or r[3] != "\u2014"]
    if keep and len(keep) < len(rows):
        rows = [rows[i] for i in keep]
        bases = [bases[i] for i in keep]
    text = _PolicyText(first + " " + second + " " + "; ".join(bases) + ".")
    text.parts = {
        "paragraphs": [first, second],
        "table": {"headers": ["Asset class", "Method", "Rate per annum", "Useful life"], "rows": rows},
        "after": after,
    }
    return text


# Checked in the order line items appear on the face of the primary
# statements (Statement of Financial Position: non-current assets, current
# assets, equity, non-current liabilities, current liabilities; then the
# Statement of Profit or Loss) - this is also the order breakdown notes are
# numbered in, so "Note 6" against a line on the face and "Note 6" heading
# a note always refer to the same thing. Each entry is
# (category codes, note title, accounting policy paragraph or None) - more
# than one code in a note (only "Administrative expenses" needs this) is
# for a note that mirrors a single combined face line rather than two.
# `policy` is standard, generic IFRS-consistent wording for a category at
# this level of detail - the preparer should tailor anything client-specific
# (e.g. an inventory costing method, a revaluation vs. cost model election)
# before filing, exactly as with any other boilerplate in this app.
NOTE_DEFINITIONS = [
    (["ppe"], "Property, plant and equipment", (
        "Property, plant and equipment are stated at cost less accumulated depreciation and any "
        "accumulated impairment losses. Depreciation is charged on a straight-line basis over the "
        "estimated useful life of each asset so as to write down its cost to its estimated residual "
        "value. Useful lives and residual values are reviewed, and adjusted if appropriate, at each "
        "reporting date.")),
    (["intangible_assets"], "Intangible assets", (
        "Intangible assets acquired separately are measured on initial recognition at cost and are "
        "subsequently carried at cost less accumulated amortisation and accumulated impairment losses. "
        "Intangible assets with finite useful lives are amortised on a straight-line basis over their "
        "useful economic lives.")),
    (["investment_property"], "Investment property", (
        "Investment property is property held to earn rental income and/or for capital appreciation, "
        "rather than for use in the production or supply of goods or services, or for administrative "
        "purposes, or for sale in the ordinary course of business.")),
    (["long_term_investments"], "Long-term investments", (
        "Long-term investments are non-current financial assets that the entity does not intend to "
        "realise within twelve months of the reporting date, and are carried at cost less any "
        "accumulated impairment losses unless a more appropriate measurement basis is stated.")),
    (["deferred_tax_asset"], "Deferred tax", (
        "Deferred tax is recognised on temporary differences between the carrying amounts of assets "
        "and liabilities for financial reporting purposes and the amounts used for taxation purposes, "
        "and on unused tax losses and credits. A deferred tax asset is recognised only to the extent "
        "it is probable that future taxable profit will be available against which it can be utilised.")),
    (["other_noncurrent_assets"], "Other non-current assets", None),
    (["inventories"], "Inventories", (
        "Inventories are stated at the lower of cost and net realisable value. Cost includes "
        "expenditure incurred in acquiring the inventories and bringing them to their existing "
        "location and condition, and is determined on a [FIFO/weighted average] basis.")),
    (["trade_receivables"], "Trade and other receivables", (
        "Trade and other receivables are recognised initially at fair value and subsequently measured "
        "at amortised cost, less any allowance for expected credit losses.")),
    (["other_current_assets"], "Other current assets", None),
    (["cash"], "Cash and cash equivalents", (
        "Cash and cash equivalents comprise cash on hand, deposits held at call with banks, and other "
        "short-term, highly liquid investments with original maturities of three months or less that "
        "are subject to an insignificant risk of changes in value.")),
    (["share_capital"], "Share capital", None),
    (["share_premium"], "Share premium", None),
    (["other_reserves"], "Other reserves", None),
    (["long_term_borrowings"], "Long-term borrowings", (
        "Borrowings are recognised initially at fair value, net of transaction costs incurred, and "
        "subsequently measured at amortised cost using the effective interest method.")),
    (["deferred_tax_liability"], "Deferred tax liabilities", None),
    (["long_term_provisions"], "Provisions", (
        "Provisions are recognised when the entity has a present legal or constructive obligation as "
        "a result of a past event, it is probable that an outflow of resources will be required to "
        "settle the obligation, and the amount can be reliably estimated.")),
    (["other_noncurrent_liabilities"], "Other non-current liabilities", None),
    (["trade_payables"], "Trade and other payables", (
        "Trade and other payables are obligations to pay for goods or services that have been acquired "
        "in the ordinary course of business. They are recognised initially at fair value and "
        "subsequently measured at amortised cost.")),
    (["short_term_borrowings"], "Short-term borrowings", None),
    (["current_tax_payable"], "Current tax liabilities", (
        "Current tax is the expected tax payable on the taxable income for the year, using tax rates "
        "enacted or substantively enacted at the reporting date, and any adjustment to tax payable in "
        "respect of previous years.")),
    (["short_term_provisions"], "Provisions", None),
    (["other_current_liabilities"], "Other current liabilities", None),
    (["revenue"], "Revenue", (
        "Revenue is recognised when control of the promised goods or services is transferred to the "
        "customer, at an amount that reflects the consideration the entity expects to be entitled to "
        "in exchange for those goods or services.")),
    (["cost_of_sales"], "Cost of sales", None),
    (["other_income"], "Other income", None),
    (["distribution_costs"], "Distribution costs", None),
    (["admin_expenses", "depreciation_amortisation"], "Administrative expenses", None),
    (["other_expenses"], "Other expenses", None),
    (["finance_costs"], "Finance costs", (
        "Finance costs comprise interest expense on borrowings and lease liabilities, and are "
        "recognised in profit or loss using the effective interest method.")),
    (["income_tax_expense"], "Income tax expense", (
        "Income tax expense comprises current and deferred tax. It is recognised in profit or loss "
        "except to the extent that it relates to items recognised directly in equity or in other "
        "comprehensive income.")),
    (["dividends_paid"], "Dividends", None),
]

def _signed_category_value(code, debit, credit):
    # Notes always show the natural, normal-balance-positive magnitude of an
    # expense or income item (e.g. Cost of sales as a positive 180,000),
    # matching how the illustrative IFRS/IFRS-for-SMEs templates present
    # note breakdowns - it's the face of the Statement of Profit or Loss,
    # not the note, that then negates/deducts expense categories.
    normal = CATEGORY_BY_CODE[code]["normal"]
    return (debit or 0.0) - (credit or 0.0) if normal == "debit" else (credit or 0.0) - (debit or 0.0)



def _round_note_rows(rows, codes, raw_totals, cur, pri):
    """Whole numbers for a breakdown note's account rows, adding up to the (whole) total: the rounding difference, if any, goes on the
    largest row. Done only when the unrounded rows really did add up to the category total (otherwise the gap is a real one - for
    example an audit adjustment, which the account list does not show - and is left visible)."""
    for key, total_whole, raw in (("current", cur, sum(raw_totals[c]["current"] for c in codes)),
                                  ("prior", pri, sum(raw_totals[c]["prior"] for c in codes))):
        raw_rows = sum(r[key] for r in rows)
        for r in rows:
            r[key] = whole(r[key])
        if rows and abs(raw_rows - raw) <= 0.01:
            diff = total_whole - sum(r[key] for r in rows)
            if diff:
                max(rows, key=lambda r: abs(r[key]))[key] += diff


_NAME_NOISE = {"charge", "for", "the", "year", "per", "asset", "register", "expense", "expenses", "of", "and", "adjustment", "movement"}

def _name_words(name):
    import re
    return {w for w in re.sub(r"\([^)]*\)", " ", (name or "").casefold()).replace("-", " ").split() if w.isalpha() and w not in _NAME_NOISE}


def _note_row_for_adjustment(account_rows, row_code, ecode, name):
    """The note row an adjustment line belongs on: the row of the same account name; else the one row of the same category that shares a
    significant word with it (a "Depreciation charge for the year (per Asset Register)" line belongs on the trial balance's Depreciation
    line). None when there is no such row or it is ambiguous: the account then gets a line of its own."""
    key = (name or "").strip().casefold()
    for r in account_rows:
        if (r["account_name"] or "").strip().casefold() == key:
            return r
    words = _name_words(name)
    hits = [r for r in account_rows if row_code.get(id(r)) == ecode and words & _name_words(r["account_name"])]
    if len(hits) == 1:
        return hits[0]
    # A shareholder / director loan journal belongs on the trial balance's shareholder loan account, even when other loan accounts (a bank
    # loan) share the word "loan": the one owner-loan row of the category, or else the owner-loan row that shares the most words with it.
    if _is_owner_loan(name):
        owner = [r for r in account_rows if row_code.get(id(r)) == ecode and _is_owner_loan(r["account_name"])]
        if len(owner) == 1:
            return owner[0]
        if owner:
            ranked = sorted(owner, key=lambda r: -len(words & _name_words(r["account_name"])))
            if len(words & _name_words(ranked[0]["account_name"])) > len(words & _name_words(ranked[1]["account_name"])):
                return ranked[0]
    return None


def build_notes(lines, totals, ppe_movement=None, ppe_policy_text=None, raw_totals=None, ppe_movement_raw=None, adjustments=None):
    """Builds the numbered Notes to the Financial Statements: one breakdown
    note per IAS 1 category (or small group of categories - see
    NOTE_DEFINITIONS) with a non-nil current or prior balance, each listing
    the individual trial balance accounts making up that total, in the
    same order categories appear on the face of the statements. A category
    with a nil balance in both years gets no note at all - matches the
    statements' own nil-line suppression, so a hidden face line never
    leaves a dangling, empty note behind.

    Numbering starts at 4, after the three fixed introductory notes (1
    General information, 2 Basis of preparation, 3 Significant accounting
    policies - composed by the caller, not here).

    `ppe_movement` (see build_ppe_movement_schedule() above), when given,
    replaces the "Property, plant and equipment" note's flat account list
    with the movement schedule instead - the note's own total then comes
    from the Asset Register's closing/opening NBV rather than the trial
    balance category total, and a `variance` key is added if the two
    disagree by more than a cent, so a register that hasn't been kept in
    step with the trial balance is flagged for follow-up rather than
    silently presented as if it ties out. This still only affects a note
    that would otherwise be built - if the "ppe" category itself is nil in
    both years and there's no register data either, no PPE note appears at
    all, exactly as before. `ppe_policy_text` (see
    ppe_accounting_policy_text() above), when given, replaces that note's
    accounting policy paragraph too.

    Returns (notes, note_number_by_category): `notes` is the ordered list
    of note dicts ready to render (each with `number`, `title`, `policy`,
    `accounts` - one row per underlying trial balance line - and `total`);
    `note_number_by_category` maps every category code that got a note to
    that note's number, for build_all_statements() to attach onto the
    matching statement rows so the face and the note cross-reference each
    other, exactly like the illustrative templates this was modelled on."""
    lines_by_category = {}
    for line in lines:
        lines_by_category.setdefault(line.fs_category, []).append(line)

    notes = []
    note_number_by_category = {}
    next_number = 4

    for codes, title, policy in NOTE_DEFINITIONS:
        is_ppe_note = codes == ["ppe"]
        has_register = bool(is_ppe_note and ppe_movement and ppe_movement.get("by_class"))

        cur = sum(totals[c]["current"] for c in codes)
        pri = sum(totals[c]["prior"] for c in codes)
        if _is_nil(cur) and _is_nil(pri) and not has_register:
            continue  # nothing to disclose - matches the statements' own nil-line suppression

        account_rows = []
        for code in codes:
            for l in lines_by_category.get(code, []):
                current = _signed_category_value(code, l.current_debit, l.current_credit)
                prior = _signed_category_value(code, l.prior_debit, l.prior_credit)
                if _is_nil(current) and _is_nil(prior):
                    continue
                account_rows.append({
                    "account_code": l.account_code, "account_name": l.account_name,
                    "current": current, "prior": prior, "from_tb": True,
                })
        # Audit adjustments (including the Asset Register's PPE-REG depreciation) go onto the account line they belong to: added to the
        # trial balance line of the same name, or a line of that account's own when the trial balance has none. They are never shown as
        # a separate "adjustment" line, and the lines then add up to the note total.
        if adjustments:
            row_code = {}
            for code in codes:
                for l in lines_by_category.get(code, []):
                    for r in account_rows:
                        if r["account_name"] == l.account_name and r["account_code"] == l.account_code:
                            row_code.setdefault(id(r), code)
            for adj in adjustments:
                for period, ecode, amount, name in adjustment_line_effects(adj):
                    if ecode not in codes or abs(amount) < 1e-9:
                        continue
                    row = _note_row_for_adjustment(account_rows, row_code, ecode, name)
                    if row is None:
                        row = {"account_code": "", "account_name": name, "current": 0.0, "prior": 0.0, "from_tb": False}
                        account_rows.append(row)
                        row_code[id(row)] = ecode
                    row[period] += amount
                    # where the amount came from, so a line that is not in the trial balance can be traced to its journal
                    row.setdefault("adj", []).append({"ref": (getattr(adj, "reference", None) or "adjustment"), "period": period, "amount": amount})
            account_rows = [r for r in account_rows if not (_is_nil(r["current"]) and _is_nil(r["prior"]))]
        if raw_totals is not None:
            _round_note_rows(account_rows, codes, raw_totals, cur, pri)

        number = next_number
        next_number += 1
        for code in codes:
            note_number_by_category[code] = number
        note = {
            "number": number, "title": title, "policy": policy,
            "accounts": account_rows, "total": {"current": cur, "prior": pri},
        }

        if has_register:
            reg_total = ppe_movement["total"]
            note["accounts"] = []
            note["movement"] = ppe_movement["by_class"]
            note["movement_total"] = reg_total
            note["total"] = {"current": reg_total["closing_nbv"], "prior": reg_total["opening_nbv"]}
            if ppe_policy_text:
                note["policy"] = str(ppe_policy_text)
                note["policy_parts"] = getattr(ppe_policy_text, "parts", None)
            # Always shown at the foot of the note - links the register's
            # own closing/opening NBV back to the trial balance's PPE
            # category total, on both years, whether or not they actually
            # agree. A non-nil difference is the whole point of doing this:
            # it's the figure that becomes the basis for a proposed audit
            # adjustment (either to the trial balance, or to correct the
            # Asset Register), never silently absorbed either way.
            diff_current = cur - reg_total["closing_nbv"]
            diff_prior = pri - reg_total["opening_nbv"]
            ties_out = abs(diff_current) <= 0.01 and abs(diff_prior) <= 0.01
            if raw_totals is not None and ppe_movement_raw:
                # In whole numbers the two sides can differ by the rounding alone: judged on the unrounded figures.
                raw_cur = sum(raw_totals[c]["current"] for c in codes)
                raw_pri = sum(raw_totals[c]["prior"] for c in codes)
                ties_out = (abs(raw_cur - ppe_movement_raw["total"]["closing_nbv"]) <= 0.5
                            and abs(raw_pri - ppe_movement_raw["total"]["opening_nbv"]) <= 0.5)
            note["reconciliation"] = {
                "tb_current": cur, "tb_prior": pri,
                "register_current": reg_total["closing_nbv"], "register_prior": reg_total["opening_nbv"],
                "diff_current": diff_current, "diff_prior": diff_prior, "ties_out": ties_out,
            }
            if not ties_out:
                # Kept alongside `reconciliation` (not instead of it) purely
                # for backward compatibility with anything that only checks
                # for a genuine mismatch via `note["variance"]`.
                note["variance"] = {
                    "tb_current": cur, "tb_prior": pri,
                    "register_current": reg_total["closing_nbv"], "register_prior": reg_total["opening_nbv"],
                }

        note["extra_tables"] = _movement_tables(codes, title, account_rows, cur, pri, adjustments)
        notes.append(note)

    return notes, note_number_by_category


_LOAN_WORDS = ("shareholder", "shareholders", "director", "directors", "member", "members")


def _is_owner_loan(name):
    n = (name or "").casefold()
    return "loan" in n and any(w in n for w in _LOAN_WORDS)


def _movement_tables(codes, title, account_rows, cur, pri, adjustments):
    """Roll-forward tables shown under a note's breakdown. Each is {"title", "headers", "rows": [[label, amounts...]], "total_last": bool}.
    - Deferred tax: balance at the start of the year, the charge to profit or loss, the part recognised in other comprehensive income,
      balance at the end of the year.
    (Shareholder loans are laid out by client_notes.attach_client_notes, which has the client's loan terms and prior-year opening.)"""
    tables = []
    if "deferred_tax_liability" in codes or "deferred_tax_asset" in codes:
        asset = "deferred_tax_asset" in codes
        move = cur - pri
        oci = 0.0
        for adj in adjustments or []:
            for period, ecode, amount, name in adjustment_line_effects(adj):
                if period == "current" and ecode == "oci_items" and "deferred" in (name or "").casefold():
                    oci += abs(amount)
        oci = (min(oci, abs(move)) * (1 if move > 0 else -1)) if abs(move) >= 0.5 and oci else 0.0
        pl_part = move - oci
        rows = [["Balance at beginning of year", pri],
                ["Credit to profit or loss" if asset else "Charge to profit or loss", pl_part]]
        if abs(oci) >= 0.5:
            rows.append(["Recognised in other comprehensive income", oci])
        rows.append(["Balance at end of year", cur])
        tables.append({"title": "Movement in deferred tax " + ("asset" if asset else "liability"), "headers": ["", "Current year"],
                       "rows": rows, "total_last": True})
    return tables


def _attach_note_numbers(pl, sfp, cf, note_number_by_category):
    """Sets row['note'] on every statement row that has a `category` (see
    _row()) and a matching entry in note_number_by_category - every such
    row survived nil-filtering, so a category with `category` set always
    resolves to a real note number here, never a dangling reference.

    `category` is usually a single code, giving a single note number
    (e.g. "5"). A Cash Flow Statement investing/financing row can instead
    carry a list of codes when it nets together more than one SFP category
    (e.g. "Proceeds from shares issued" = share capital + share premium) -
    row['note'] then becomes a comma-separated list of every distinct note
    number involved (e.g. "8, 9"), in the order NOTE_DEFINITIONS lists them.
    The Cash Flow Statement's operating activities section is deliberately
    left out here regardless of method (indirect or direct) - its lines are
    a fixed reconciliation/receipts-and-payments presentation, not
    individual trial balance accounts, so they never carry a note number."""
    all_row_lists = (
        [pl["rows"]]
        + [sfp[k] for k in ("non_current_assets", "current_assets", "equity", "non_current_liabilities", "current_liabilities")]
        + [cf["investing_rows"], cf["financing_rows"]]
    )
    for rows in all_row_lists:
        for row in rows:
            code = row.get("category")
            if not code:
                continue
            codes = code if isinstance(code, list) else [code]
            numbers = sorted({note_number_by_category[c] for c in codes if c in note_number_by_category})
            if numbers:
                row["note"] = ", ".join(str(n) for n in numbers)


def _route_owner_loan_journals(lines, adjustments):
    """A journal line on a shareholder / director loan account belongs on the loan account the trial balance carries, whatever statement
    line the journal happened to map it to: it is moved to the category of the trial balance's loan account (when that is one category on
    the statement of financial position), so it adjusts the loan on the face and in the note (Shareholder Funding) instead of sitting
    as a line of its own. Works on light copies; the stored journals are untouched."""
    from types import SimpleNamespace
    homes = {l.fs_category for l in lines
             if l.fs_category in CATEGORY_BY_CODE and _is_owner_loan(l.account_name) and CATEGORY_BY_CODE[l.fs_category]["statement"] == "SFP"}
    if len(homes) != 1:
        return adjustments
    home = next(iter(homes))
    out = []
    for adj in adjustments:
        new_lines, moved = [], False
        for ln in adj.lines:
            cat = CATEGORY_BY_CODE.get(ln.fs_category)
            if _is_owner_loan(getattr(ln, "account_name", None)) and cat and cat["statement"] == "SFP" and ln.fs_category != home:
                new_lines.append(SimpleNamespace(account_name=ln.account_name, fs_category=home, debit=ln.debit, credit=ln.credit))
                moved = True
            else:
                new_lines.append(ln)
        if moved:
            out.append(SimpleNamespace(
                reference=getattr(adj, "reference", None), description=getattr(adj, "description", None),
                applies_to=getattr(adj, "applies_to", "current"), carry_to_current=getattr(adj, "carry_to_current", True), lines=new_lines))
        else:
            out.append(adj)
    return out


def build_all_statements(lines, adjustments=None, reporting_framework="full_ifrs", cash_flow_method="indirect",
                          ppe_movement=None, ppe_policy_text=None, whole_numbers=False):
    """`adjustments`, when given (a TrialBalance's .adjustments), are applied
    on top of the preliminary trial balance's totals before the statements
    are built - see apply_adjustments() above. Leave it out (the default)
    to build statements straight from the preliminary trial balance, e.g.
    for a quick preliminary view before any adjustments are proposed.

    `reporting_framework` (see models.REPORTING_FRAMEWORKS) controls only
    whether the numbered Notes to the Financial Statements are built and
    cross-referenced onto the face (build_notes() above) - "full_ifrs" and
    "ifrs_for_smes" both get them, "other"/local-GAAP engagements get
    `notes=[]` and no note numbers on the face, leaving the plain
    statements exactly as before this feature.

    `cash_flow_method` ("indirect", the default, or "direct" - see
    models.CASH_FLOW_METHODS and build_cash_flow()) controls only how the
    Cash Flow Statement's operating activities section is presented; every
    other statement, and every Cash Flow subtotal, is unaffected.

    `ppe_movement`/`ppe_policy_text` (see build_ppe_movement_schedule() and
    ppe_accounting_policy_text() above) are passed straight through to
    build_notes() - see there for what they change. Leaving both out (the
    default) leaves the PPE note exactly as it was before this feature."""
    adjustments = list(adjustments) if adjustments else adjustments
    if adjustments:
        adjustments = _route_owner_loan_journals(lines, adjustments)
    totals = compute_totals(lines)
    if adjustments:
        totals = apply_adjustments(totals, adjustments)
    raw_totals, rounding, ppe_movement_raw = totals, None, ppe_movement
    if whole_numbers:
        totals, absorbed = round_totals_balanced(raw_totals)
        rounding = {"whole_numbers": True, "absorbed": absorbed}
        ppe_movement = round_ppe_movement(ppe_movement)
    pl = build_income_statement(totals)
    equity = build_equity_statement(totals, pl)
    sfp = build_financial_position(totals, equity)
    cf = build_cash_flow(totals, pl, method=cash_flow_method)
    if reporting_framework in ("full_ifrs", "ifrs_for_smes"):
        notes, note_number_by_category = build_notes(
            lines, totals, ppe_movement=ppe_movement, ppe_policy_text=ppe_policy_text,
            raw_totals=raw_totals if whole_numbers else None, ppe_movement_raw=ppe_movement_raw if whole_numbers else None,
            adjustments=adjustments)
        _attach_note_numbers(pl, sfp, cf, note_number_by_category)
    else:
        notes = []
    return {"totals": totals, "pl": pl, "equity": equity, "sfp": sfp, "cf": cf, "notes": notes,
            "whole_numbers": bool(whole_numbers), "rounding": rounding, "prior_restated": prior_year_adjustments(adjustments)}


def parse_tb_rows(rows):
    """rows: iterable of dicts with (case-insensitive, whitespace-tolerant)
    keys matching TB_IMPORT_COLUMNS. Returns a list of cleaned dicts:
    {account_code, account_name, current_debit, current_credit,
    prior_debit, prior_credit}. Rows with no account name are skipped.
    Raises ValueError with a human-readable message if a required header
    is missing."""
    def norm_key(k):
        return (k or "").strip().lower().replace("  ", " ")

    key_map = {
        "account code": "account_code", "account name": "account_name",
        "current year debit": "current_debit", "current year credit": "current_credit",
        "prior year debit": "prior_debit", "prior year credit": "prior_credit",
    }

    cleaned = []
    for raw_row in rows:
        row = {}
        for k, val in raw_row.items():
            mapped = key_map.get(norm_key(k))
            if mapped:
                row[mapped] = val
        name = (row.get("account_name") or "").strip()
        if not name:
            continue

        def to_float(v):
            if v in (None, ""):
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).strip().replace(",", "")
            if not s:
                return 0.0
            try:
                return float(s)
            except ValueError:
                return 0.0

        cleaned.append({
            "account_code": (row.get("account_code") or "").strip(),
            "account_name": name,
            "current_debit": to_float(row.get("current_debit")),
            "current_credit": to_float(row.get("current_credit")),
            "prior_debit": to_float(row.get("prior_debit")),
            "prior_credit": to_float(row.get("prior_credit")),
        })
    if not cleaned:
        raise ValueError(
            "No account rows found. Make sure the file has a header row with "
            "'Account Name' and at least one debit/credit column, and at "
            "least one row below it with an account name filled in."
        )
    return cleaned
