"""workpapers, part 2 of 4.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file workpapers.py did."""

def build_financial_statements_docx(engagement, statements, financial_statements, directors_statement=None, audit_opinion=None, client_key_people=None):
    show_opinion = engagement.type in ("Audit", "Assurance")

    doc = Document()
    brand.setup_document(doc)
    brand.branded_footer(doc)
    _cover_page(doc, engagement)

    toc_sections = ["Directors' Statement"]
    if show_opinion:
        toc_sections.append("Independent Auditor's Report" if (audit_opinion.report_basis if audit_opinion else "audit") != "review" else "Independent Reviewer's Report")
    toc_sections += [
        "Statement of Financial Position", "Statement of Profit or Loss and Other Comprehensive Income",
        "Statement of Changes in Equity", "Statement of Cash Flows", "Notes to the Financial Statements",
    ]
    _client_details_and_toc_page(doc, engagement, client_key_people, toc_sections)

    _directors_statement_section(doc, directors_statement, engagement)
    if show_opinion:
        _audit_opinion_section(doc, audit_opinion, engagement)

    # The financial statements are the client's, not the firm's: no Neverlank letterhead from here on (the auditor's report above carries it).
    doc._fs_ctx = (engagement.client.name if engagement.client else "", engagement.period_end)
    import client_notes
    doc._fs_years = [h.replace(" ", "\n", 1) for h in client_notes.year_headers(engagement, engagement.client)]
    draft = not financial_statements or not financial_statements.is_partner_signed

    is_ifrs_framework = engagement.reporting_framework in ("full_ifrs", "ifrs_for_smes")
    if not is_ifrs_framework and financial_statements and financial_statements.basis_of_preparation:
        _add_heading(doc, "Basis of Preparation", level=2)
        doc.add_paragraph(financial_statements.basis_of_preparation)
        doc.add_page_break()

    def two_col_table(title, rows, level=1):
        _add_heading(doc, title, level=level)
        table = doc.add_table(rows=1, cols=3)
        hdr = table.rows[0].cells
        hdr[0].text = ""
        hdr[1].text, hdr[2].text = _years(doc)
        _style_table(table)
        for r in rows:
            row = table.add_row().cells
            row[0].text = r["label"]
            row[1].text = _fmt_whole(r["current"])
            row[2].text = _fmt_whole(r.get("prior", 0))
            if r.get("bold"):
                for c in row:
                    for p in c.paragraphs:
                        for run in p.runs:
                            run.bold = True
        _col_widths(doc, table, [0.56, 0.22, 0.22])
        _right_align(table, first_col=1)
        doc.add_paragraph()
        return table

    def movement_table(title, note, level=1):
        # Property, plant and equipment note built from the Asset Register
        # (financials.build_ppe_movement_schedule) instead of a plain list
        # of trial balance accounts - condensed IAS 16.73(e) presentation:
        # one row per asset class reconciling opening to closing NBV.
        _add_heading(doc, title, level=level)
        headers = ["Asset class", "Opening NBV", "Additions", "Disposals", "Depreciation charge", "Closing NBV"]
        table = doc.add_table(rows=1, cols=len(headers))
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = h
        _style_table(table)

        def add_row(label, row, bold=False):
            disposals_nbv = row["disposals_cost"] - row["disposals_acc_dep"]
            values = [
                label, _fmt_whole(row["opening_nbv"]), _fmt_whole(row["additions"]),
                _fmt_whole(-disposals_nbv) if disposals_nbv else _fmt_whole(0),
                _fmt_whole(-row["charge"]) if row["charge"] else _fmt_whole(0),
                _fmt_whole(row["closing_nbv"]),
            ]
            cells = table.add_row().cells
            for i, v in enumerate(values):
                cells[i].text = v
                if bold:
                    for p in cells[i].paragraphs:
                        for run in p.runs:
                            run.bold = True

        for row in note["movement"]:
            add_row(row["class_name"], row)
        add_row("Total", note["movement_total"], bold=True)
        _col_widths(doc, table, [0.25, 0.15, 0.15, 0.15, 0.16, 0.14])
        _right_align(table, first_col=1)

        rec = note.get("reconciliation")
        if rec:
            # Always shown - links the register's own closing/opening NBV
            # back to the trial balance PPE total on both years, whether or
            # not they agree, per the standard "agree the note to the
            # underlying schedule" step. A non-nil difference is called out
            # as the basis for a proposed adjustment, not silently dropped.
            base = (
                f"Per trial balance: {_fmt_whole(rec['tb_current'])} (current year), {_fmt_whole(rec['tb_prior'])} (prior year). "
                f"Per Asset Register: {_fmt_whole(rec['register_current'])} (current year), {_fmt_whole(rec['register_prior'])} (prior year)."
            )
            if rec["ties_out"]:
                p = doc.add_paragraph(base + " Ties out to the trial balance.")
            else:
                p = doc.add_paragraph(
                    base + f" Difference of {_fmt_whole(rec['diff_current'])} (current year), {_fmt_whole(rec['diff_prior'])} (prior year) "
                    "- basis for a proposed adjustment to the trial balance or a correction to the Asset Register, as appropriate."
                )
            p.runs[0].italic = True
        doc.add_paragraph()

    sfp = statements["sfp"]
    sfp_specs = [{"section": "Non-current assets"}]
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["non_current_assets"]]
    sfp_specs.append({"cells": ["Total non-current assets", "", _fmt_whole(sfp["nca_total"]["current"]), _fmt_whole(sfp["nca_total"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Current assets"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["current_assets"]]
    sfp_specs.append({"cells": ["Total current assets", "", _fmt_whole(sfp["ca_total"]["current"]), _fmt_whole(sfp["ca_total"]["prior"])], "bold": True})
    sfp_specs.append({"cells": ["Total assets", "", _fmt_whole(sfp["total_assets"]["current"]), _fmt_whole(sfp["total_assets"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Equity"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["equity"]]
    sfp_specs.append({"cells": ["Total equity", "", _fmt_whole(sfp["total_equity"]["current"]), _fmt_whole(sfp["total_equity"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Non-current liabilities"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["non_current_liabilities"]]
    sfp_specs.append({"cells": ["Total non-current liabilities", "", _fmt_whole(sfp["ncl_total"]["current"]), _fmt_whole(sfp["ncl_total"]["prior"])], "bold": True})
    sfp_specs.append({"section": "Current liabilities"})
    sfp_specs += [{"cells": _fs_row_cells(r)} for r in sfp["current_liabilities"]]
    sfp_specs.append({"cells": ["Total current liabilities", "", _fmt_whole(sfp["cl_total"]["current"]), _fmt_whole(sfp["cl_total"]["prior"])], "bold": True})
    sfp_specs.append({"cells": ["Total equity and liabilities", "", _fmt_whole(sfp["total_equity_and_liabilities"]["current"]), _fmt_whole(sfp["total_equity_and_liabilities"]["prior"])], "bold": True})
    # One continuous table, top to bottom, exactly matching the Finalisation
    # tab on screen - not a separate table per section with paragraph
    # breaks in between.
    if draft:
        dp = doc.add_paragraph("Draft - not yet fully mapped/reviewed")
        dp.runs[0].italic = True
    _statement_table(doc, "Statement of Financial Position", ["", "Note", *_years(doc)], sfp_specs, period="as_at", new_page=False)
    bc = sfp["balance_check"]
    tie_note = "Ties out." if abs(bc["current"]) <= 0.01 and abs(bc["prior"]) <= 0.01 else "Non-zero - check every account is mapped to the right category and that the trial balance balances."
    doc.add_paragraph(f"Balance check (assets less equity and liabilities): current year {_fmt_whole(bc['current'])}, prior year {_fmt_whole(bc['prior'])}. {tie_note}").runs[0].italic = True
    if statements.get("whole_numbers"):
        rounding_text = "All amounts in these financial statements are rounded to the nearest whole number."
        for a in (statements.get("rounding") or {}).get("absorbed", []):
            rounding_text += f" A rounding difference of {_fmt_whole(abs(a['amount']))} on {a['label'].lower()} ({'current' if a['period'] == 'current' else 'prior'} year) is included in that line so that the statement of financial position balances."
        doc.add_paragraph(rounding_text).runs[0].italic = True

    restated = statements.get("prior_restated") or []
    if restated:
        refs = ", ".join(a["reference"] for a in restated if a["reference"]) or "adjustments"
        carried = any(a["carry"] for a in restated)
        doc.add_paragraph(
            "Comparative figures have been restated to correct prior period errors (IAS 8). The prior year amounts above include the restating "
            f"adjustments {refs}" + ("; the opening balances of the current year have been restated accordingly." if carried else ".")
        ).runs[0].italic = True

    pl_specs = [{"cells": _fs_row_cells(r), "bold": r.get("bold", False)} for r in statements["pl"]["rows"]]
    _statement_table(doc, "Statement of Profit or Loss and Other Comprehensive Income", ["", "Note", *_years(doc)], pl_specs)

    doc.add_page_break()
    _add_heading(doc, "Statement of Changes in Equity", level=1)
    _statement_banner(doc, "year_ended")
    eq_table = doc.add_table(rows=1, cols=4)
    hdr = eq_table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Component", "Opening", "Movement", "Closing"
    _style_table(eq_table)
    for r in statements["equity"]["rows"]:
        row = eq_table.add_row().cells
        row[0].text = r["label"] + (f" ({r['movement_desc'][0].lower() + r['movement_desc'][1:]})" if r["label"] == "Retained earnings" else "")
        row[1].text = _fmt_whole(r["opening"])
        row[2].text = _fmt_whole(r["movement"])
        row[3].text = _fmt_whole(r["closing"])
    tot = eq_table.add_row().cells
    tot[0].text, tot[1].text = "Total equity", _fmt_whole(statements["equity"]["total_opening"])
    tot[2].text, tot[3].text = _fmt_whole(statements["equity"]["total_closing"] - statements["equity"]["total_opening"]), _fmt_whole(statements["equity"]["total_closing"])
    for c in tot:
        for p in c.paragraphs:
            for run in p.runs:
                run.bold = True
    _col_widths(doc, eq_table, [0.43, 0.19, 0.19, 0.19])
    _right_align(eq_table, first_col=1)
    doc.add_paragraph()

    import cf_prior
    cf_prior.attach_prior_cash_flow(statements, engagement)
    cf = statements["cf"]
    method_label = CASH_FLOW_METHOD_LABELS.get(cf["method"], "Indirect method")
    pr = cf.get("prior")                    # the prior-year comparatives typed in by hand, or None
    def cf_cells(label, cur, pri, note=""):
        return [label, note, _fmt_whole(cur)] + ([_fmt_whole(pri)] if pr else [])
    def cf_row(r):
        cells = _fs_row_cells(r, with_prior=False)
        if pr:
            cells.append(_fmt_whole(r.get("prior") or 0) if r.get("prior") is not None else "")
        return cells
    cf_specs = [{"section": "Operating activities"}]
    for r in cf["operating_rows"]:
        if cf_prior.is_nil(r):
            continue                                  # nothing in either year
        cf_specs.append({"cells": cf_row(r), "bold": r.get("bold", False), "muted": r.get("memo", False) and not r.get("dep_merged")})
    cf_specs.append({"cells": cf_cells("Net cash from operating activities", cf["net_operating"], pr and pr["net_operating"]), "bold": True})
    cf_specs.append({"section": "Investing activities"})
    for r in cf["investing_rows"]:
        if cf_prior.is_nil(r):
            continue
        cf_specs.append({"cells": cf_row(r)})
    cf_specs.append({"cells": cf_cells("Net cash used in investing activities", cf["net_investing"], pr and pr["net_investing"]), "bold": True})
    cf_specs.append({"section": "Financing activities"})
    for r in cf["financing_rows"]:
        if cf_prior.is_nil(r):
            continue
        cf_specs.append({"cells": cf_row(r)})
    cf_specs.append({"cells": cf_cells("Net cash from financing activities", cf["net_financing"], pr and pr["net_financing"]), "bold": True})
    cf_specs.append({"cells": cf_cells("Net increase/(decrease) in cash", cf["net_movement"], pr and pr["net_movement"]), "bold": True})
    cf_specs.append({"cells": cf_cells("Cash at beginning of year", cf["cash_open"], pr and pr["cash_open"])})
    cf_specs.append({"cells": cf_cells("Cash at end of year (computed)", cf["cash_close_computed"], pr and pr["cash_close_computed"]), "bold": True})
    cf_specs.append({"cells": cf_cells("Cash at end of year (per trial balance)", cf["cash_close_actual"], pr and pr["cash_close_actual"])})
    _statement_table(doc, f"Statement of Cash Flows ({method_label}{'' if pr else ', current year'})",
                     ["", "Note", *(_years(doc) if pr else _years(doc)[:1])], cf_specs)
    variance_note = "Ties out to the trial balance." if abs(cf["variance"]) <= 0.01 else f"Computed closing cash differs from the trial balance by {_fmt_whole(cf['variance'])} - likely additions/disposals not visible from net asset movements, or linked to the retained earnings continuity check above. Review and adjust."
    doc.add_paragraph(variance_note).runs[0].italic = True
    if pr and abs(pr["variance"]) > 0.01:
        doc.add_paragraph(f"Prior-year comparatives: computed closing cash differs from the prior-year cash in the trial balance by {_fmt_whole(pr['variance'])} - check the comparative amounts typed in on the Finalisation tab.").runs[0].italic = True

    notes_started = False
    if is_ifrs_framework:
        _start_notes_section(doc, engagement)
        notes_started = True
        _add_heading(doc, "Notes to the Financial Statements", level=1)

        _add_heading(doc, "1. General information", level=2)
        doc.add_paragraph(fin.general_information_note(engagement.client, engagement))

        _add_heading(doc, "2. Basis of preparation", level=2)
        basis_text = (financial_statements.basis_of_preparation if financial_statements else "") or (
            fin.REPORTING_FRAMEWORK_COMPLIANCE_TEXT.get(engagement.reporting_framework, "") + fin.DEFAULT_BASIS_OF_PREPARATION_TAIL
        )
        doc.add_paragraph(basis_text)

        policy_notes = [n for n in statements["notes"] if n.get("policy")]
        if policy_notes:
            _add_heading(doc, "3. Significant accounting policies", level=2)
            for i, note in enumerate(policy_notes, start=1):
                parts = note.get("policy_parts")
                if parts:
                    # A policy with a table of bases (Property, plant and equipment): heading, short paragraphs, then the table.
                    p = doc.add_paragraph()
                    p.paragraph_format.keep_with_next = True
                    run = p.add_run(f"({i}) {note['title']}")
                    run.bold = True
                    for para in parts["paragraphs"]:
                        pp = doc.add_paragraph(para)
                        pp.paragraph_format.keep_with_next = True
                    tbl = parts["table"]
                    t = doc.add_table(rows=1, cols=len(tbl["headers"]))
                    for k, h in enumerate(tbl["headers"]):
                        t.rows[0].cells[k].text = h
                    _style_table(t)
                    for r in tbl["rows"]:
                        cells = t.add_row().cells
                        for k, v in enumerate(r):
                            cells[k].text = v
                    _col_widths(doc, t, [0.40, 0.24, 0.18, 0.18])
                    _right_align(t, first_col=2)
                    ap = doc.add_paragraph(parts["after"])
                    ap.paragraph_format.space_before = Pt(6)
                    continue
                p = doc.add_paragraph()
                run = p.add_run(f"({i}) {note['title']}. ")
                run.bold = True
                p.add_run(note["policy"])

        import client_notes
        client_notes.attach_client_notes(statements, engagement)
        for note in statements["notes"]:
            if note.get("share_layout") or (note.get("loan_layout") and note["loan_layout"]["replaces_breakdown"]):
                _add_heading(doc, f"{note['number']}. {note['title']}", level=2)
                if note.get("share_layout"):
                    _share_capital_layout(doc, note["share_layout"])
                else:
                    _loan_note_layout(doc, note["loan_layout"])
                _extra_note_tables(doc, note)
                continue
            if note.get("movement"):
                movement_table(f"{note['number']}. {note['title']}", note, level=2)
                _extra_note_tables(doc, note)
                continue
            account_rows = [
                {"label": (f"{a['account_code']} - " if a.get("account_code") else "") + a["account_name"],
                 "current": a["current"], "prior": a["prior"]}
                for a in note["accounts"]
            ] + [{"label": "Total", "current": note["total"]["current"], "prior": note["total"]["prior"], "bold": True}]
            two_col_table(f"{note['number']}. {note['title']}", account_rows, level=2)
            if note.get("loan_layout"):
                _loan_note_layout(doc, note["loan_layout"])
            _extra_note_tables(doc, note)

        next_num = (statements["notes"][-1]["number"] + 1) if statements["notes"] else 4
        closing_notes = [
            (next_num, "Related party transactions", financial_statements.related_party_note if financial_statements else None, fin.DEFAULT_CLOSING_NOTE_TEXT["related_party"]),
            (next_num + 1, "Contingencies and commitments", financial_statements.commitments_note if financial_statements else None, fin.DEFAULT_CLOSING_NOTE_TEXT["commitments"]),
            (next_num + 2, "Events after the reporting period", financial_statements.subsequent_events_note if financial_statements else None, fin.DEFAULT_CLOSING_NOTE_TEXT["subsequent_events"]),
        ]
        for number, title, saved_text, default_text in closing_notes:
            _add_heading(doc, f"{number}. {title}", level=2)
            doc.add_paragraph(saved_text or default_text)

        if financial_statements and financial_statements.notes_to_financial_statements:
            _add_heading(doc, "Other matters", level=2)
            for para in financial_statements.notes_to_financial_statements.split("\n"):
                doc.add_paragraph(para)
    elif financial_statements and financial_statements.notes_to_financial_statements:
        _start_notes_section(doc, engagement)
        notes_started = True
        _add_heading(doc, "Notes to the Financial Statements", level=1)
        for para in financial_statements.notes_to_financial_statements.split("\n"):
            doc.add_paragraph(para)

    if notes_started:
        _end_notes_section(doc)
    _add_heading(doc, "Sign-off", level=2)
    if financial_statements:
        doc.add_paragraph(
            f"Prepared by {financial_statements.completed_by.name if financial_statements.completed_by else '—'} "
            f"on {_fmt_date(financial_statements.completed_at)}."
        )
        if financial_statements.is_reviewed:
            doc.add_paragraph(f"Reviewed by {financial_statements.reviewed_by.name} on {_fmt_date(financial_statements.reviewed_at)}.")
        if financial_statements.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {financial_statements.partner_signed_by.name} on {_fmt_date(financial_statements.partner_signed_at)}.")
    else:
        doc.add_paragraph("Not yet prepared/signed off within the app.")

    return _finish(doc)


# ================================================================= Trial balance & adjustments (Excel)

def build_trial_balance_adjustments_xlsx(engagement, trial_balance):
    wb, ws = _new_workbook_sheet("Trial Balance & Adjustments — Ref. N1000", engagement, "Trial Balance")

    row = 7
    row = _header_row(ws, row, ["Account code", "Account name", "IAS 1 category", "Current debit", "Current credit", "Prior debit", "Prior credit"])
    tb_start = row
    for line in (trial_balance.lines if trial_balance else []):
        ws.cell(row=row, column=1, value=line.account_code or "").border = _BORDER
        ws.cell(row=row, column=2, value=line.account_name).border = _BORDER
        ws.cell(row=row, column=3, value=line.fs_category or "(unmapped)").border = _BORDER
        ws.cell(row=row, column=4, value=line.current_debit or 0).border = _BORDER
        ws.cell(row=row, column=5, value=line.current_credit or 0).border = _BORDER
        ws.cell(row=row, column=6, value=line.prior_debit or 0).border = _BORDER
        ws.cell(row=row, column=7, value=line.prior_credit or 0).border = _BORDER
        row += 1
    tb_end = row - 1
    if tb_end >= tb_start:
        row_totals = row
        ws.cell(row=row_totals, column=2, value="TOTAL").font = Font(bold=True)
        for col in (4, 5, 6, 7):
            letter = get_column_letter(col)
            cell = ws.cell(row=row_totals, column=col, value=f"=SUM({letter}{tb_start}:{letter}{tb_end})")
            cell.font = Font(bold=True)
        row = row_totals + 1

    row += 2
    ws.cell(row=row, column=1, value="Audit Adjustments").font = Font(bold=True, size=12, color=_GOLD_HEX)
    row += 1
    if trial_balance and trial_balance.adjustments:
        for adj in trial_balance.adjustments:
            ws.cell(row=row, column=1, value=f"{adj.reference or '(no ref)'} — {adj.description or ''}"
                    + (" [PRIOR YEAR - restates the comparatives" + (" and this year's opening balances]" if adj.carries_to_current else "]") if adj.is_prior_year else "")).font = Font(bold=True)
            row += 1
            row = _header_row(ws, row, ["Account", "IAS 1 category", "Debit", "Credit"])
            adj_start = row
            for line in adj.lines:
                ws.cell(row=row, column=1, value=line.account_name).border = _BORDER
                ws.cell(row=row, column=2, value=line.fs_category).border = _BORDER
                ws.cell(row=row, column=3, value=line.debit or 0).border = _BORDER
                ws.cell(row=row, column=4, value=line.credit or 0).border = _BORDER
                row += 1
            adj_end = row - 1
            if adj_end >= adj_start:
                ws.cell(row=row, column=1, value="Balanced" if adj.is_balanced else "OUT OF BALANCE").font = Font(
                    bold=True, color="1F7A1F" if adj.is_balanced else "B02A2A"
                )
                row += 1
            prep = f"Prepared by {adj.completed_by.name if adj.completed_by else '—'} on {_fmt_date(adj.completed_at)}."
            if adj.is_reviewed:
                prep += f" Reviewed by {adj.reviewed_by.name} on {_fmt_date(adj.reviewed_at)}."
            if adj.is_partner_signed:
                prep += f" Partner sign-off by {adj.partner_signed_by.name} on {_fmt_date(adj.partner_signed_at)}."
            ws.cell(row=row, column=1, value=prep).font = Font(italic=True, size=9)
            row += 2
    else:
        ws.cell(row=row, column=1, value="No adjustments proposed.")
        row += 1

    if trial_balance:
        row += 1
        prep = f"Trial balance prepared by {trial_balance.completed_by.name if trial_balance.completed_by else '—'} on {_fmt_date(trial_balance.completed_at)}."
        if trial_balance.is_reviewed:
            prep += f" Reviewed by {trial_balance.reviewed_by.name} on {_fmt_date(trial_balance.reviewed_at)}."
        if trial_balance.is_partner_signed:
            prep += f" Partner sign-off by {trial_balance.partner_signed_by.name} on {_fmt_date(trial_balance.partner_signed_at)}."
        ws.cell(row=row, column=1, value=prep).font = Font(italic=True, size=9)

    _autofit(ws, [16, 32, 24, 16, 16, 16, 16])
    return _finish_wb(wb)


# ================================================================= PPE Depreciation policy & Asset Register (Excel)

def build_ppe_asset_register_xlsx(engagement, asset_classes, assets):
    """Exports the two Property, Plant and Equipment working papers kept
    live in the app (Substantive Procedures > Property, Plant and
    Equipment) - the Depreciation policy and the Asset Register - as one
    workbook for filing. Both are already editable directly in the app;
    this is a point-in-time copy for the file, not the working paper's own
    source of truth (that stays in the database, same as every other
    in-app working paper this app exports)."""
    wb, ws = _new_workbook_sheet("PPE Depreciation Policy & Asset Register — Ref. N3300", engagement, "Depreciation policy")

    row = 7
    row = _header_row(ws, row, ["Asset class", "Depreciation method", "Rate (% p.a.)", "Useful life (years)"])
    for c in asset_classes:
        ws.cell(row=row, column=1, value=c.name).border = _BORDER
        ws.cell(row=row, column=2, value=c.depreciation_method or "").border = _BORDER
        ws.cell(row=row, column=3, value=c.rate_percent if c.rate_percent is not None else "").border = _BORDER
        ws.cell(row=row, column=4, value=c.useful_life_years if c.useful_life_years is not None else "").border = _BORDER
        row += 1
    if not asset_classes:
        ws.cell(row=row, column=1, value="No asset classes defined yet.")
        row += 1
    _autofit(ws, [28, 22, 16, 18])

    ws2 = wb.create_sheet("Asset Register")
    client_name = engagement.client.name if engagement.client else ""
    details = f"{client_name} — {engagement.title}"
    if engagement.period_end:
        details += f"  ·  Period ended {_fmt_date(engagement.period_end)}"
    brand.xlsx_title_band(ws2, "PPE Asset Register — Ref. N3300", [details], ncols=8)
    brand.xlsx_page_setup(ws2)

    headers = [
        "Asset code", "Description", "Asset class", "Date acquired", "Cost",
        "Opening accumulated depreciation", "Depreciation method", "Rate / useful life",
        "Current year depreciation charge", "Disposals - cost", "Disposals - accumulated depreciation",
        "Closing NBV",
    ]
    row = 7
    row = _header_row(ws2, row, headers)
    class_by_id = {c.id: c for c in asset_classes}
    reg_start = row
    for a in assets:
        cls = class_by_id.get(a.asset_class_id)
        values = [
            a.asset_code or "", a.description, cls.name if cls else "Unclassified",
            _fmt_date(a.date_acquired) if a.date_acquired else "",
            a.cost or 0.0, a.opening_accumulated_depreciation or 0.0,
            cls.depreciation_method if cls else "", cls.rate_display if cls else "—",
            a.current_year_depreciation or 0.0, a.disposal_cost or 0.0, a.disposal_accumulated_depreciation or 0.0,
            a.closing_nbv,
        ]
        for i, v in enumerate(values, start=1):
            ws2.cell(row=row, column=i, value=v).border = _BORDER
        row += 1
    reg_end = row - 1
    if reg_end >= reg_start:
        ws2.cell(row=row, column=2, value="TOTAL").font = Font(bold=True)
        for col in (5, 6, 9, 10, 11, 12):
            letter = get_column_letter(col)
            ws2.cell(row=row, column=col, value=f"=SUM({letter}{reg_start}:{letter}{reg_end})").font = Font(bold=True)
    else:
        ws2.cell(row=row, column=1, value="No assets in the register yet.")
    _autofit(ws2, [14, 32, 18, 14, 14, 16, 16, 16, 16, 14, 16, 14])

    return _finish_wb(wb)


def build_asset_schedules_xlsx(engagement, ctx, ca):
    """The Asset Register and Capital Allowance Schedule as one workbook for the file: opening balances (the year before's closing),
    the year's additions and disposals, the Asset Register schedule by class and the Capital Allowance Schedule. `ctx` is
    asset_schedules.tab_context(engagement); `ca` is engagement_tax.capital_allowance_context(engagement)."""
    from openpyxl.styles import Alignment
    as_at = ctx["as_opening_as_at"]
    wb, ws = _new_workbook_sheet("Asset Register & Capital Allowance Schedule — Ref. N3300", engagement, "Opening balances")
    money = "#,##0.00;(#,##0.00);-"
    classes = {c.id: c.name for c in ctx["as_classes"]}

    def put(sheet, row, values, bold=False, numeric_from=None):
        for i, v in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=i, value=v)
            cell.border = _BORDER
            if bold:
                cell.font = Font(bold=True)
            if numeric_from and i >= numeric_from and isinstance(v, (int, float)):
                cell.number_format = money
                cell.alignment = Alignment(horizontal="right")
        return row + 1

    # 1. opening balances
    ws.cell(row=6, column=1, value=f"Opening balances as at {_fmt_date(as_at)}").font = Font(bold=True, size=12, color=_GOLD_HEX)
    row = _header_row(ws, 7, ["Code", "Description", "Class", "Date acquired", "Cost b/f", "Accumulated depreciation b/f", "Net book value b/f", "Tax value b/f"])
    start = row
    for a in ctx["as_openings"]:
        row = put(ws, row, [a.asset_code or "", a.description, classes.get(a.asset_class_id, "Unclassified"),
                            _fmt_date(a.date_acquired) if a.date_acquired else "", a.cost or 0.0,
                            a.opening_accumulated_depreciation or 0.0, (a.cost or 0.0) - (a.opening_accumulated_depreciation or 0.0),
                            a.opening_tax_value if a.opening_tax_value is not None else ""], numeric_from=5)
    if row > start:
        put(ws, row, ["", "TOTAL", "", ""] + [f"=SUM({get_column_letter(c)}{start}:{get_column_letter(c)}{row - 1})" for c in (5, 6, 7, 8)], bold=True)
        for c in (5, 6, 7, 8):
            ws.cell(row=row, column=c).number_format = money
    _autofit(ws, [12, 36, 20, 14, 16, 18, 18, 16])

    # 2. additions
    w2 = wb.create_sheet("Additions")
    w2.cell(row=6, column=1, value="Additions during the year").font = Font(bold=True, size=12, color=_GOLD_HEX)
    r2 = _header_row(w2, 7, ["Code", "Description", "Class", "Date acquired", "Cost", "Depreciation in the accounts"])
    s2 = r2
    for a in ctx["as_additions"]:
        r2 = put(w2, r2, [a.asset_code or "", a.description, classes.get(a.asset_class_id, "Unclassified"),
                          _fmt_date(a.date_acquired), a.cost or 0.0, a.current_year_depreciation or 0.0], numeric_from=5)
    if r2 > s2:
        put(w2, r2, ["", "TOTAL", "", ""] + [f"=SUM({get_column_letter(c)}{s2}:{get_column_letter(c)}{r2 - 1})" for c in (5, 6)], bold=True)
        for c in (5, 6):
            w2.cell(row=r2, column=c).number_format = money
    _autofit(w2, [12, 36, 20, 14, 16, 22])

    # 3. disposals
    w3 = wb.create_sheet("Disposals")
    w3.cell(row=6, column=1, value="Disposals during the year").font = Font(bold=True, size=12, color=_GOLD_HEX)
    r3 = _header_row(w3, 7, ["Code", "Description", "Class", "Date sold", "Cost", "Accumulated depreciation", "Carrying amount", "Proceeds", "Profit / (loss)"])
    s3 = r3
    for d in ctx["as_disposals"]:
        a = d["asset"]
        r3 = put(w3, r3, [a.asset_code or "", a.description, classes.get(a.asset_class_id, "Unclassified"),
                          _fmt_date(d["date"]) if d["date"] else "", d["cost"], d["acc_dep"], d["nbv"],
                          d["proceeds"] if d["proceeds"] is not None else "", d["profit_loss"] if d["profit_loss"] is not None else ""], numeric_from=5)
    if r3 > s3:
        put(w3, r3, ["", "TOTAL", "", ""] + [f"=SUM({get_column_letter(c)}{s3}:{get_column_letter(c)}{r3 - 1})" for c in (5, 6, 7, 8, 9)], bold=True)
        for c in (5, 6, 7, 8, 9):
            w3.cell(row=r3, column=c).number_format = money
    _autofit(w3, [12, 36, 20, 14, 16, 18, 16, 16, 16])

    # 4. Asset Register schedule
    w4 = wb.create_sheet("Asset Register schedule")
    w4.cell(row=6, column=1, value=f"Asset Register - year ended {_fmt_date(engagement.period_end)}").font = Font(bold=True, size=12, color=_GOLD_HEX)
    r4 = _header_row(w4, 7, ["Asset class", "Cost b/f", "Additions", "Disposals", "Cost c/f", "Acc. depreciation b/f", "Charge for the year",
                             "Depreciation on disposals", "Acc. depreciation c/f", "NBV b/f", "NBV c/f"])
    mv = ctx["as_movement"]
    if mv:
        for r in mv["by_class"]:
            r4 = put(w4, r4, [r["class_name"], r["opening_cost"], r["additions"], -r["disposals_cost"], r["closing_cost"], r["opening_acc_dep"],
                              r["charge"], -r["disposals_acc_dep"], r["closing_acc_dep"], r["opening_nbv"], r["closing_nbv"]], numeric_from=2)
        t = mv["total"]
        put(w4, r4, ["Total", t["opening_cost"], t["additions"], -t["disposals_cost"], t["closing_cost"], t["opening_acc_dep"], t["charge"],
                     -t["disposals_acc_dep"], t["closing_acc_dep"], t["opening_nbv"], t["closing_nbv"]], bold=True, numeric_from=2)
    _autofit(w4, [28] + [16] * 10)

    # 5. Capital Allowance Schedule
    w5 = wb.create_sheet("Capital allowances")
    res = ca.get("ca_result")
    w5.cell(row=6, column=1, value=f"Capital Allowance Schedule - year ended {_fmt_date(engagement.period_end)}").font = Font(bold=True, size=12, color=_GOLD_HEX)
    r5 = _header_row(w5, 7, ["Asset class", "Tax value b/f", "Additions", "Disposals (tax value)", "Allowances", "Tax value c/f", "Disposal proceeds",
                             "Recoupment", "Scrapping allowance"])
    if res and res["rows"]:
        for c in res["by_class"]:
            r5 = put(w5, r5, [c["class_name"], c["opening"], c["additions"], -c["disposed_tv"], c["allowance"], c["closing"], c["proceeds"],
                              c["recoupment"], c["scrapping"]], numeric_from=2)
        t = res["total"]
        r5 = put(w5, r5, ["Total", t["opening"], t["additions"], -t["disposed_tv"], t["allowance"], t["closing"], t["proceeds"], t["recoupment"],
                          t["scrapping"]], bold=True, numeric_from=2)
        r5 += 2
        w5.cell(row=r5, column=1, value="Asset by asset").font = Font(bold=True, size=12, color=_GOLD_HEX)
        r5 = _header_row(w5, r5 + 1, ["Asset", "Tax value b/f", "Additions", "Disposals (tax value)", "Allowances", "Tax value c/f", "Disposal proceeds",
                                      "Recoupment", "Scrapping allowance", "Class", "Allowance method"])
        for r in res["rows"]:
            r5 = put(w5, r5, [f"{r['asset_code'] or ''} {r['description']}".strip(), r["opening"], r["additions"], -r["disposed_tv"], r["allowance"],
                              r["closing"], r["proceeds"], r["recoupment"], r["scrapping"], r["class_name"], r["method"]], numeric_from=2)
        r5 += 1
        w5.cell(row=r5, column=1, value="Allowance rules marked 'not verified' have not yet been checked against the Income Tax Act by a Partner or Admin.").font = Font(italic=True, size=9)
    else:
        w5.cell(row=r5, column=1, value="No assets in the schedule for this year.")
    _autofit(w5, [34, 16, 16, 18, 16, 16, 16, 14, 18, 22, 16])

    # 6. reconciliation to the trial balance
    rc = ctx.get("as_recon")
    if rc and rc.get("ok"):
        w6 = wb.create_sheet("Trial balance reconciliation")
        w6.cell(row=6, column=1, value="Asset Register reconciled to the trial balance").font = Font(bold=True, size=12, color=_GOLD_HEX)
        cont = rc.get("continuity")
        head = ["Opening", "Asset Register", "Trial balance (prior-year column)", "Difference"] + ([f"{cont.title} - closing", "Comparative vs last year"] if cont else [])
        r6 = _header_row(w6, 7, head)
        for r in rc["opening"]:
            vals = [r["label"], r["register"], r["tb"], r["diff"]] + ([r.get("cont"), r.get("cont_diff")] if cont else [])
            r6 = put(w6, r6, vals, bold=r is rc["opening"][-1], numeric_from=2)
        r6 += 1
        r6 = _header_row(w6, r6, ["Closing", "Asset Register", "Trial balance (with other adjustments)", "Difference", "Pushed (PPE-REG)", "Still unreconciled"])
        for r in rc["closing"] + [rc["depreciation"]]:
            r6 = put(w6, r6, [r["label"], r["register"], r["tb"], r["diff"], r["pushed"], r["remaining"]], numeric_from=2)
        r6 += 1
        for i in rc["info"]:
            r6 = put(w6, r6, [i["label"], i["value"]], numeric_from=2)
        p = rc["proposal"]
        if p["lines"]:
            r6 += 1
            r6 = _header_row(w6, r6, ["Proposed adjustment PPE-REG", "IAS 1 category", "Debit", "Credit"])
            for l in p["lines"]:
                r6 = put(w6, r6, [l["account_name"], l["fs_category"], l["debit"] or "", l["credit"] or ""], numeric_from=3)
            r6 = put(w6, r6, ["Total", "", p["total_debit"], p["total_credit"]], bold=True, numeric_from=3)
        _autofit(w6, [62, 18, 24, 16, 22, 22])
    return _finish_wb(wb)



# ================================================================= Management representation letter (Word)

def build_rep_letter_docx(engagement, statements, narrative=None):
    doc = _new_document("Management Representation Letter", engagement, subtitle=f"Ref. {filing_reference('rep_letter')}")

    client_name = engagement.client.name if engagement.client else "[Client]"
    period_end = _fmt_date(engagement.period_end)

    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_accounting = engagement.type == "Accounting & Bookkeeping"

    doc.add_paragraph(f"To: {FIRM_NAME}")
    doc.add_paragraph(f"Date: {_fmt_date(date.today())}")
    doc.add_paragraph()
    if is_business_it:
        doc.add_paragraph(
            f"This representation letter is provided in connection with your engagement in respect of the IT "
            f"governance, cyber security, information security and AML/CFT control environment of {client_name} "
            f"for the period ended {period_end}, for the purpose of expressing a conclusion on the matters within "
            f"the scope of that engagement."
        )
    elif is_accounting:
        doc.add_paragraph(
            f"This representation letter is provided in connection with your engagement to prepare and/or compile "
            f"the accounting, financial, management and cost accounting records and reports of {client_name} for "
            f"the period ended {period_end}, encompassing Financial Accounting, Management Accounting, Cost "
            f"Accounting and Tax compliance matters as applicable. This is a compilation/bookkeeping engagement "
            f"and not an audit or review, and accordingly no opinion or assurance conclusion is expressed on the "
            f"information prepared."
        )
    else:
        doc.add_paragraph(
            f"This representation letter is provided in connection with your engagement in respect of the financial "
            f"statements of {client_name} for the period ended {period_end}, for the purpose of expressing an opinion "
            f"as to whether the financial statements give a true and fair view (or present fairly, in all material "
            f"respects) in accordance with the applicable financial reporting framework."
        )
    doc.add_paragraph("We confirm that, to the best of our knowledge and belief, having made such inquiries as we considered necessary for the purpose of appropriately informing ourselves:")

    # The body of representations is a persistent, editable workpaper (see
    # models.WorkpaperNarrative, kind="rep_letter") rather than fixed text -
    # fall back to the firm's (type-appropriate) default wording if nothing
    # has been saved yet - see models.default_workpaper_narrative_body.
    body = (narrative.body if narrative and narrative.body else None) or default_workpaper_narrative_body("rep_letter", engagement)
    for line in body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line, style="List Bullet")

    if statements:
        pbt = statements["pl"]["profit_before_tax"]["current"]
        doc.add_paragraph()
        doc.add_paragraph(f"For reference, profit before tax for the period per the draft financial statements is {_fmt_num(pbt)}.")

    doc.add_paragraph()
    doc.add_paragraph("Signed on behalf of management:")
    doc.add_paragraph()
    doc.add_paragraph("_______________________________")
    doc.add_paragraph("Name / Title / Date")

    if narrative and narrative.completed_by:
        doc.add_paragraph()
        _add_heading(doc, "Sign-off", level=2)
        doc.add_paragraph(f"Prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")

    return _finish(doc)
