"""workpapers, part 3 of 4.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file workpapers.py did."""

# ================================================================= Report to Management (Word)

def build_report_to_management_docx(engagement, narrative=None):
    """The Report to Management (RTM) - the standard audit deliverable to
    client management covering internal-control observations and
    recommendations arising during the engagement, distinct from the
    Management Representation Letter above (which is signed BY management,
    not addressed TO them). Its body is a persistent, editable workpaper
    (models.WorkpaperNarrative, kind="report_to_management")."""
    doc = _new_document("Report to Management", engagement, subtitle=f"Ref. {filing_reference('report_to_management')}")

    client_name = engagement.client.name if engagement.client else "[Client]"
    period_end = _fmt_date(engagement.period_end)

    doc.add_paragraph(f"To: The Directors, {client_name}")
    doc.add_paragraph(f"Date: {_fmt_date(date.today())}")
    doc.add_paragraph()
    doc.add_paragraph(
        f"In connection with our engagement in respect of {client_name} for the period ended {period_end}, "
        f"we set out below the matters we wish to bring to the attention of management."
    )
    doc.add_paragraph()

    body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES["report_to_management"]
    for line in body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line, style="List Bullet")

    doc.add_paragraph()
    doc.add_paragraph("Yours faithfully,")
    if narrative is not None and brand.signature_valid(narrative):
        brand.add_signature_block(doc, firm_name=FIRM_NAME, signed_on=narrative.mp_signed_at)
    else:
        doc.add_paragraph()
        doc.add_paragraph("_______________________________")
        doc.add_paragraph(FIRM_NAME)

    if narrative and narrative.completed_by:
        doc.add_paragraph()
        _add_heading(doc, "Sign-off", level=2)
        doc.add_paragraph(f"Prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")

    return _finish(doc)


# ================================================================= Tax Opinion / Tax Health Check Report (Word)
# Both are just an ordered set of WorkpaperNarrative parts (see
# models.TAX_OPINION_PARTS / TAX_HEALTH_CHECK_PARTS) rendered as numbered
# sections - one shared builder covers both, parameterised by title/parts.

def _build_tax_sections_docx(engagement, title, filing_kind, parts, narratives_by_kind):
    doc = _new_document(title, engagement, subtitle=f"Ref. {filing_reference(filing_kind)}")

    client_name = engagement.client.name if engagement.client else "[Client]"
    doc.add_paragraph(f"Client: {client_name}")
    doc.add_paragraph(f"Engagement: {engagement.title}")
    doc.add_paragraph(f"Date: {_fmt_date(date.today())}")
    doc.add_paragraph()

    for i, (kind, label) in enumerate(parts, start=1):
        narrative = narratives_by_kind.get(kind)
        _add_heading(doc, f"{i}. {label}", level=2)
        body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES.get(kind, "")
        for line in body.split("\n"):
            line = line.strip()
            if line:
                doc.add_paragraph(line)
        if narrative and narrative.completed_by:
            signoff_bits = [f"Prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}."]
            if narrative.is_reviewed:
                signoff_bits.append(f"Reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
            if narrative.is_partner_signed:
                signoff_bits.append(f"Partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")
            p = doc.add_paragraph(" ".join(signoff_bits))
            for run in p.runs:
                run.italic = True
                run.font.size = Pt(9)
        doc.add_paragraph()

    return _finish(doc)


def build_tax_opinion_docx(engagement, narratives_by_kind):
    """The Tax Opinion (Tax module, Module 10) - its 14 parts are each a
    persistent WorkpaperNarrative (see models.TAX_OPINION_PARTS); this just
    renders them, in order, as one combined Word document."""
    from models import TAX_OPINION_PARTS
    return _build_tax_sections_docx(engagement, "Tax Opinion", "tax_opinion", TAX_OPINION_PARTS, narratives_by_kind)


def build_tax_health_check_docx(engagement, narratives_by_kind):
    """The Tax Health Check Report (Tax module, Module 10) - its 15 parts
    are each a persistent WorkpaperNarrative (see models.
    TAX_HEALTH_CHECK_PARTS); this just renders them, in order, as one
    combined Word document."""
    from models import TAX_HEALTH_CHECK_PARTS
    return _build_tax_sections_docx(engagement, "Tax Health Check Report", "tax_health_check", TAX_HEALTH_CHECK_PARTS, narratives_by_kind)


def build_management_accounts_report_docx(engagement, narratives_by_kind):
    """The Management Accounts Report (Accounting module) - its 10 parts
    are each a persistent WorkpaperNarrative (see models.
    MANAGEMENT_ACCOUNTS_REPORT_PARTS); reuses the same shared section-by-
    section builder as the Tax Opinion/Health Check above, since the shape
    (an ordered set of named, independently signed-off narrative sections)
    is identical."""
    from models import MANAGEMENT_ACCOUNTS_REPORT_PARTS
    return _build_tax_sections_docx(engagement, "Management Accounts Report", "management_accounts_report", MANAGEMENT_ACCOUNTS_REPORT_PARTS, narratives_by_kind)


# ================================================================= Forensic investigation report (Word)

def build_forensic_report_docx(engagement, narrative=None):
    ca = engagement.client_acceptance
    ra = engagement.risk_assessment
    strategy = engagement.audit_strategy
    entity = engagement.entity_understanding
    finalisation = engagement.finalisation_checklist

    doc = _new_document("Forensic Investigation Report", engagement, subtitle="Confidential — Ref. FR-1")

    doc.add_paragraph("Confidential", ).runs[0].bold = True
    doc.add_paragraph(f"Date of report: {_fmt_date(date.today())}")

    # Executive Summary is a persistent, editable workpaper (see models.
    # WorkpaperNarrative, kind="forensic_executive_summary") - everything
    # else in this report stays a live roll-up of the engagement's other
    # tabs, since there's no other free-standing narrative to persist there.
    _add_heading(doc, "Executive Summary")
    summary_body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES["forensic_executive_summary"]
    for line in summary_body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line)

    _add_heading(doc, "1. Background and Mandate")
    if strategy and strategy.objective_statement:
        doc.add_paragraph(strategy.objective_statement)
    else:
        doc.add_paragraph("[Objective statement not yet recorded on the Audit Strategy tab.]")
    if strategy and strategy.reporting_destination:
        doc.add_paragraph(f"Reporting destination: {strategy.reporting_destination}")

    _add_heading(doc, "2. Scope and Legal Framework")
    if strategy and strategy.chain_of_custody_notes:
        doc.add_paragraph(strategy.chain_of_custody_notes)
    if ca and ca.legal_evidence_notes:
        doc.add_paragraph(f"Legal framework & evidence control assessment: {ca.legal_evidence_notes}")

    _add_heading(doc, "3. Preliminary Fraud Theory")
    if strategy:
        if strategy.potential_perpetrators:
            doc.add_paragraph(f"Potential perpetrators: {strategy.potential_perpetrators}")
        if strategy.fraud_vulnerability:
            doc.add_paragraph(f"Control vulnerability exploited: {strategy.fraud_vulnerability}")
        if strategy.evidentiary_red_flags:
            doc.add_paragraph(f"Evidentiary red flags: {strategy.evidentiary_red_flags}")
    else:
        doc.add_paragraph("[Preliminary fraud theory not yet recorded on the Audit Strategy tab.]")

    _add_heading(doc, "4. Risk Assessment Summary")
    if ra and ra.rating:
        doc.add_paragraph(f"Overall fraud risk rating: {ra.rating} (score {ra.score}/25).")
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "High-risk scheme"
        table.rows[0].cells[1].text = "Significance (1-5)"
        _style_table(table)
        for field, label, _ in ra.impact_questions:
            val = getattr(ra, field)
            if val is not None:
                row = table.add_row().cells
                row[0].text = label
                row[1].text = str(val)
    else:
        doc.add_paragraph("[Risk Assessment not yet completed.]")

    _add_heading(doc, "5. Procedures Performed")
    areas = sorted(engagement.substantive_procedure_areas, key=lambda a: a.area) if engagement.substantive_procedure_areas else []
    if areas:
        for area in areas:
            doc.add_paragraph(area.area, style="List Bullet")
            for item in area.items:
                p = doc.add_paragraph(f"{item.procedure_text} — {item.status}", style="List Bullet 2")
                if item.notes:
                    p.add_run(f" ({item.notes})").italic = True
    else:
        doc.add_paragraph("[No substantive/investigative procedures recorded yet.]")

    _add_heading(doc, "6. Findings and Conclusion")
    if finalisation:
        for section_name in ["Findings & Conclusions"]:
            items = [i for i in finalisation.checklist_items if i.section == section_name]
            for item in items:
                p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
                if item.comment:
                    p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Finalisation checklist not yet seeded/completed - see the Finalisation tab.]")

    _add_heading(doc, "7. Sign-off")
    if strategy:
        doc.add_paragraph(
            f"Prepared by {strategy.completed_by.name if strategy.completed_by else '—'} on {_fmt_date(strategy.completed_at)}."
        )
        if strategy.is_reviewed:
            doc.add_paragraph(f"Reviewed by {strategy.reviewed_by.name} on {_fmt_date(strategy.reviewed_at)}.")
        if strategy.is_partner_signed:
            doc.add_paragraph(f"Partner sign-off by {strategy.partner_signed_by.name} on {_fmt_date(strategy.partner_signed_at)}.")
    if narrative and narrative.completed_by:
        doc.add_paragraph(f"Executive Summary prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Executive Summary reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Executive Summary partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")

    return _finish(doc)


def build_it_audit_report_docx(engagement, narrative=None):
    """The Business Intelligence and IT Engagements equivalent of
    build_forensic_report_docx above - a standalone "IT & Cyber Assurance
    Report" bringing together the engagement's own IT-specific data (Client
    Acceptance's Management Integrity & Cooperativeness questions, the
    Regulatory Landscape entity-understanding answers, the ITGC/Substantive
    procedures actually performed grouped by the four scoped domains, and
    the Finalisation checklist's Findings & Recommendations section) under
    one persistent, editable Executive Summary - see models.
    WorkpaperNarrative, kind="it_audit_report_summary"."""
    ca = engagement.client_acceptance
    entity = engagement.entity_understanding
    finalisation = engagement.finalisation_checklist

    doc = _new_document("IT & Cyber Assurance Report", engagement, subtitle="Confidential")

    doc.add_paragraph("Confidential").runs[0].bold = True
    doc.add_paragraph(f"Date of report: {_fmt_date(date.today())}")

    _add_heading(doc, "Executive Summary")
    summary_body = (narrative.body if narrative and narrative.body else None) or DEFAULT_WORKPAPER_NARRATIVE_BODIES["it_audit_report_summary"]
    for line in summary_body.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(line)

    _directors_statement_section(doc, engagement.directors_statement, engagement)

    _add_heading(doc, "1. Scope and Engagement Acceptance")
    if ca:
        doc.add_paragraph(f"Decision: {ca.decision or 'Not yet decided'}")
        mgmt_items = [i for i in ca.checklist_items if i.section == "Competence - Management Integrity & Cooperativeness"]
        if mgmt_items:
            doc.add_paragraph("Management integrity & cooperativeness (access to logs, source code, configurations, database schemas):")
            for item in mgmt_items:
                p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
                if item.comment:
                    p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Client Acceptance not yet recorded - see the Client Acceptance tab.]")

    _add_heading(doc, "2. Regulatory Landscape & Standards Alignment")
    reg_items = [i for i in entity.checklist_items if i.section == "Regulatory Landscape & Standards Alignment"] if entity else []
    if reg_items:
        for item in reg_items:
            p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
            if item.comment:
                p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Not yet completed - see the Understanding the Entity tab's Regulatory Landscape & Standards Alignment section.]")

    _add_heading(doc, "3. Procedures Performed and Results, by Domain")
    areas = sorted(engagement.substantive_procedure_areas, key=lambda a: a.area) if engagement.substantive_procedure_areas else []
    if areas:
        for area in areas:
            _add_heading(doc, area.area, level=2)
            if area.items:
                table = doc.add_table(rows=1, cols=4)
                hdr = table.rows[0].cells
                hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Procedure", "Kind", "Status", "Notes / Findings"
                _style_table(table)
                for item in area.items:
                    row = table.add_row().cells
                    row[0].text = item.procedure_text
                    row[1].text = item.procedure_kind or ""
                    row[2].text = item.status
                    row[3].text = item.notes or ""
            else:
                doc.add_paragraph("[No procedures recorded yet for this domain.]")
            if area.notes:
                p = doc.add_paragraph()
                p.add_run("Domain findings: ").bold = True
                p.add_run(area.notes)
    else:
        doc.add_paragraph("[No substantive procedures recorded yet - see the Substantive Procedures tab.]")

    _add_heading(doc, "4. Findings, Recommendations and Conclusion")
    if finalisation:
        for section_name in ["Findings & Recommendations"]:
            items = [i for i in finalisation.checklist_items if i.section == section_name]
            for item in items:
                p = doc.add_paragraph(f"{item.item_text} — {item.response or 'Not yet assessed'}", style="List Bullet")
                if item.comment:
                    p.add_run(f" ({item.comment})").italic = True
    else:
        doc.add_paragraph("[Finalisation checklist not yet seeded/completed - see the Finalisation tab.]")

    _add_heading(doc, "5. Sign-off")
    if narrative and narrative.completed_by:
        doc.add_paragraph(f"Executive Summary prepared by {narrative.completed_by.name} on {_fmt_date(narrative.completed_at)}.")
        if narrative.is_reviewed:
            doc.add_paragraph(f"Executive Summary reviewed by {narrative.reviewed_by.name} on {_fmt_date(narrative.reviewed_at)}.")
        if narrative.is_partner_signed:
            doc.add_paragraph(f"Executive Summary partner sign-off by {narrative.partner_signed_by.name} on {_fmt_date(narrative.partner_signed_at)}.")
    else:
        doc.add_paragraph("[Executive Summary not yet prepared - see the Finalisation tab.]")

    return _finish(doc)


# ================================================================= Substantive Procedures programme (Word / Excel)

def build_substantive_procedures_docx(engagement, areas_by_name, area_order, area_refs):
    """areas_by_name: {area_name: SubstantiveProcedureArea or None}
    area_order: the ordered list of area names for this engagement type
    area_refs: {area_name: reference code string}"""
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic:
        title, ref_prefix = "Investigative Procedures Programme", "IP"
    elif is_business_it:
        title, ref_prefix = "IT & Cyber Assurance Procedures Programme", "ITP"
    elif is_secretarial:
        title, ref_prefix = "Secretarial Execution Plan", "EXE"
    else:
        title, ref_prefix = "Substantive Procedures Programme", "SP"
    doc = _new_document(title, engagement, subtitle=f"Ref. {ref_prefix}-1")

    for area_name in area_order:
        area = areas_by_name.get(area_name)
        ref = area_refs.get(area_name, "")
        _add_heading(doc, f"{ref} — {area_name}" if ref else area_name, level=1)
        items = area.items if area else []
        if items:
            # A Secretarial Execution Plan gets three extra columns
            # (Trigger/Event, Lead Responsible, Target Output) matching the
            # firm's Practical Secretarial Execution Plan Schedule; a
            # Business Intelligence and IT Engagement gets one extra column
            # (Kind - ITGC or Substantive, see BUSINESS_IT_PROCEDURE_KIND) so
            # the filed programme reads as the ITGC/Substantive table the
            # methodology calls for. Every other engagement type keeps the
            # original 5-column layout.
            cols = 8 if is_secretarial else (6 if is_business_it else 5)
            table = doc.add_table(rows=1, cols=cols)
            hdr = table.rows[0].cells
            if is_secretarial:
                headers = ["Task", "Trigger / Event", "Lead Responsible", "Target Output", "Source", "Status", "Notes", "Tickmark"]
            elif is_business_it:
                headers = ["Procedure", "Kind", "Source", "Status", "Notes", "Tickmark"]
            else:
                headers = ["Procedure", "Source", "Status", "Notes", "Tickmark"]
            for i, h in enumerate(headers):
                hdr[i].text = h
            _style_table(table)
            for item in items:
                row = table.add_row().cells
                if is_secretarial:
                    row[0].text = item.procedure_text
                    row[1].text = item.trigger_event or ""
                    row[2].text = item.responsible_role or ""
                    row[3].text = item.target_output or ""
                    row[4].text = item.source
                    row[5].text = item.status
                    row[6].text = item.notes or ""
                    row[7].text = item.tickmark.symbol if item.tickmark else ""
                elif is_business_it:
                    row[0].text = item.procedure_text
                    row[1].text = item.procedure_kind or ""
                    row[2].text = item.source
                    row[3].text = item.status
                    row[4].text = item.notes or ""
                    row[5].text = item.tickmark.symbol if item.tickmark else ""
                else:
                    row[0].text = item.procedure_text
                    row[1].text = item.source
                    row[2].text = item.status
                    row[3].text = item.notes or ""
                    row[4].text = item.tickmark.symbol if item.tickmark else ""
        else:
            doc.add_paragraph("No procedures recorded for this area.")
        if area and area.notes:
            # The editable "Area notes" free-text box on the Substantive
            # Procedures tab (see engagements.save_substantive_area_notes) -
            # previously captured in the app but never actually carried
            # through into this generated/filed programme document.
            notes_p = doc.add_paragraph()
            notes_p.add_run("Notes: ").bold = True
            notes_p.add_run(area.notes)
        if area:
            prep = f"Prepared by {area.completed_by.name if area.completed_by else '—'} on {_fmt_date(area.completed_at)}."
            if area.is_reviewed:
                prep += f" Reviewed by {area.reviewed_by.name} on {_fmt_date(area.reviewed_at)}."
            if area.is_partner_signed:
                prep += f" Partner sign-off by {area.partner_signed_by.name} on {_fmt_date(area.partner_signed_at)}."
            note_p = doc.add_paragraph(prep)
            note_p.runs[0].italic = True
            note_p.runs[0].font.size = Pt(9)
        doc.add_paragraph()

    return _finish(doc)


def build_substantive_procedures_xlsx(engagement, areas_by_name, area_order, area_refs):
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic:
        sheet_title = "Investigative Procedures Programme — Ref. IP-1"
    elif is_business_it:
        sheet_title = "IT & Cyber Assurance Procedures Programme — Ref. ITP-1"
    elif is_secretarial:
        sheet_title = "Secretarial Execution Plan — Ref. EXE-1"
    else:
        sheet_title = "Substantive Procedures Programme — Ref. SP-1"
    wb, ws = _new_workbook_sheet(sheet_title, engagement, "Programme")

    row = 7
    if is_secretarial:
        headers = ["Ref.", "Workstream", "Task", "Trigger / Event", "Lead Responsible", "Target Output", "Source", "Status", "Notes", "Tickmark"]
    elif is_business_it:
        headers = ["Ref.", "Area", "Procedure", "Kind", "Source", "Status", "Notes", "Tickmark"]
    else:
        headers = ["Ref.", "Area", "Procedure", "Source", "Status", "Notes", "Tickmark"]
    row = _header_row(ws, row, headers)
    for area_name in area_order:
        area = areas_by_name.get(area_name)
        ref = area_refs.get(area_name, "")
        items = area.items if area else []
        last_col = len(headers)
        if not items:
            ws.cell(row=row, column=1, value=ref).border = _BORDER
            ws.cell(row=row, column=2, value=area_name).border = _BORDER
            ws.cell(row=row, column=3, value="(no procedures recorded)").border = _BORDER
            for col in range(4, last_col + 1):
                ws.cell(row=row, column=col, value="").border = _BORDER
            row += 1
            continue
        for i, item in enumerate(items):
            ws.cell(row=row, column=1, value=ref if i == 0 else "").border = _BORDER
            ws.cell(row=row, column=2, value=area_name if i == 0 else "").border = _BORDER
            ws.cell(row=row, column=3, value=item.procedure_text).border = _BORDER
            if is_secretarial:
                ws.cell(row=row, column=4, value=item.trigger_event or "").border = _BORDER
                ws.cell(row=row, column=5, value=item.responsible_role or "").border = _BORDER
                ws.cell(row=row, column=6, value=item.target_output or "").border = _BORDER
                ws.cell(row=row, column=7, value=item.source).border = _BORDER
                ws.cell(row=row, column=8, value=item.status).border = _BORDER
                ws.cell(row=row, column=9, value=item.notes or "").border = _BORDER
                ws.cell(row=row, column=10, value=item.tickmark.symbol if item.tickmark else "").border = _BORDER
            elif is_business_it:
                ws.cell(row=row, column=4, value=item.procedure_kind or "").border = _BORDER
                ws.cell(row=row, column=5, value=item.source).border = _BORDER
                ws.cell(row=row, column=6, value=item.status).border = _BORDER
                ws.cell(row=row, column=7, value=item.notes or "").border = _BORDER
                ws.cell(row=row, column=8, value=item.tickmark.symbol if item.tickmark else "").border = _BORDER
            else:
                ws.cell(row=row, column=4, value=item.source).border = _BORDER
                ws.cell(row=row, column=5, value=item.status).border = _BORDER
                ws.cell(row=row, column=6, value=item.notes or "").border = _BORDER
                ws.cell(row=row, column=7, value=item.tickmark.symbol if item.tickmark else "").border = _BORDER
            row += 1
        if area and area.notes:
            # Same editable "Area notes" gap fix as build_substantive_
            # procedures_docx above - now carried into the Excel programme too.
            ws.cell(row=row, column=1, value="").border = _BORDER
            ws.cell(row=row, column=2, value="Notes").border = _BORDER
            ws.cell(row=row, column=3, value=area.notes).border = _BORDER
            for col in range(4, last_col + 1):
                ws.cell(row=row, column=col, value="").border = _BORDER
            row += 1

    if is_secretarial:
        _autofit(ws, [8, 24, 45, 22, 16, 30, 12, 14, 26, 10])
    elif is_business_it:
        _autofit(ws, [8, 24, 55, 14, 12, 14, 30, 10])
    else:
        _autofit(ws, [8, 28, 55, 12, 14, 30, 10])
    return _finish_wb(wb)


# ================================================================= Checklist workpapers (Word)
#
# None of the four checklist types (Client Acceptance, Understanding the
# Entity, the main Engagement Checklist, Finalisation) previously had a
# downloadable Word workpaper of their own - only the on-screen tab and the
# Engagement File Summary PDF showed their responses. These four functions
# fill that gap, each as its own workpaper with a table of every response
# plus the section's sign-off.

def _checklist_table_docx(doc, items, per_item_status=False):
    """A docx table of checklist-item rows: Section | Item | Response |
    Comment, and (only for the main Engagement Checklist, whose items each
    carry their own Preparer/Reviewer/Partner sign-off rather than one for
    the whole checklist) a trailing Status column.

    Handles both field-naming conventions across the four checklist-item
    models: "response"/"comment" (Client Acceptance, Entity Understanding,
    Finalisation) and "status"/"notes" (the main Engagement Checklist)."""
    cols = 5 if per_item_status else 4
    table = doc.add_table(rows=1, cols=cols)
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Section", "Item", "Response", "Comment"
    if per_item_status:
        hdr[4].text = "Status"
    _style_table(table)
    for item in items:
        response = getattr(item, "response", None) or getattr(item, "status", None) or "Not assessed"
        comment = getattr(item, "comment", None) or getattr(item, "notes", None) or ""
        row = table.add_row().cells
        row[0].text = item.section or ""
        row[1].text = item.item_text or ""
        row[2].text = response
        row[3].text = comment
        if per_item_status:
            row[4].text = _sign_off_line(item)
    return table


def build_client_acceptance_checklist_docx(engagement, client_acceptance):
    doc = _new_document("Client Acceptance & Continuance - Checklist", engagement, subtitle=f"Ref. {filing_reference('acceptance')}")
    items = client_acceptance.checklist_items if client_acceptance else []
    if items:
        _checklist_table_docx(doc, items)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    _add_heading(doc, "Sign-off", level=2)
    doc.add_paragraph(f"Decision: {client_acceptance.decision if client_acceptance and client_acceptance.decision else 'Not yet decided'}")
    doc.add_paragraph(_sign_off_line(client_acceptance))
    return _finish(doc)


def build_entity_understanding_checklist_docx(engagement, entity_understanding):
    doc = _new_document("Understanding the Entity's Business - Checklist", engagement, subtitle=f"Ref. {filing_reference('entity')}")
    items = entity_understanding.checklist_items if entity_understanding else []
    if items:
        _checklist_table_docx(doc, items)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    _add_heading(doc, "Sign-off", level=2)
    doc.add_paragraph(_sign_off_line(entity_understanding))
    return _finish(doc)


def build_engagement_checklist_docx(engagement):
    doc = _new_document("Engagement Checklist", engagement, subtitle="Ref. F-1")
    items = engagement.checklist_items
    if items:
        _checklist_table_docx(doc, items, per_item_status=True)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    doc.add_paragraph(f"Overall progress: {engagement.checklist_progress}% complete.").runs[0].italic = True
    return _finish(doc)


def build_finalisation_checklist_docx(engagement, finalisation_checklist):
    doc = _new_document("Finalisation Checklist", engagement, subtitle=f"Ref. {filing_reference('finalisation')}")
    items = finalisation_checklist.checklist_items if finalisation_checklist else []
    if items:
        _checklist_table_docx(doc, items)
    else:
        doc.add_paragraph("No checklist items recorded yet.")
    doc.add_paragraph()
    _add_heading(doc, "Sign-off", level=2)
    doc.add_paragraph(_sign_off_line(finalisation_checklist))
    return _finish(doc)


# ================================================================= Engagement File Summary (PDF)

def _sign_off_line(record, label="Prepared", preparer_attr="completed_by"):
    """One plain-text status line for any workpaper record carrying the
    standard Preparer/Reviewer/Partner fields (or MaterialityCalculation's
    updated_by/updated_at variant) - "not yet started" if there's no record
    or no preparer yet, otherwise Prepared/Reviewed(-or-exempt)/Partner-
    signed, each only if it applies."""
    if record is None:
        return "Not yet started."
    preparer = getattr(record, preparer_attr, None)
    prepared_at = getattr(record, "completed_at", None) or getattr(record, "updated_at", None)
    if not preparer:
        return "Not yet started."
    parts = [f"{label} by {preparer.name} on {_fmt_date(prepared_at)}."]
    if getattr(record, "is_reviewed", False):
        parts.append(f"Reviewed by {record.reviewed_by.name} on {_fmt_date(record.reviewed_at)}.")
    elif effectively_reviewed(record):
        parts.append("Prepared by a Partner/Admin - no separate reviewer required.")
    else:
        parts.append("Not yet reviewed.")
    if getattr(record, "is_partner_signed", False):
        parts.append(f"Partner sign-off by {record.partner_signed_by.name} on {_fmt_date(record.partner_signed_at)}.")
    return " ".join(parts)


def _checklist_items_table(items):
    """A reportlab Table summarising a list of checklist-item rows (item
    text, response, comment) - used for every checklist-driven workpaper
    section in the file summary below."""
    # EngagementChecklistItem (the main Checklist tab) predates the other
    # three checklist-item models and names its fields differently -
    # "status" (Not Started/In Progress/Done/N/A) instead of "response", and
    # "notes" instead of "comment" - handle both rather than special-casing
    # the main checklist tab's items at every call site.
    data = [["Item", "Response", "Comment"]]
    for item in items:
        response = getattr(item, "response", None) or getattr(item, "status", None) or "Not assessed"
        comment = getattr(item, "comment", None) or getattr(item, "notes", None) or ""
        data.append([
            Paragraph(item.item_text or "", getSampleStyleSheet()["BodyText"]),
            response,
            Paragraph(comment, getSampleStyleSheet()["BodyText"]),
        ])
    table = Table(data, colWidths=[220, 70, 200], repeatRows=1)
    table.setStyle(brand.pdf_table_style())
    return table
