"""workpapers, part 4 of 4.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file workpapers.py did."""

def build_engagement_file_summary_pdf(engagement):
    """The Engagement File Summary - one PDF pulling together every major
    workpaper section (see models.WORKPAPER_SECTIONS for the fixed A-L
    index), its sign-off status (honouring the Partner-preparer review
    exemption - models.effectively_reviewed), and, for the four checklist-
    driven sections, every response filed under that section's reference -
    the "responses summarised in a PDF with references filed under the
    relevant sections" requested for a completed, reviewed and signed
    engagement. Ends with the tickmark legend for whichever tickmarks were
    actually used on this engagement's Substantive Procedures."""
    is_forensic = engagement.type == "Investigative Engagement"
    narratives = {wn.kind: wn for wn in engagement.workpaper_narratives}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=0.8 * inch, rightMargin=0.8 * inch,
        topMargin=0.5 * inch, bottomMargin=0.8 * inch,
        title=f"Engagement File Summary - {engagement.title}", author=FIRM_NAME,
    )
    page_width = A4[0] - 1.6 * inch
    styles = getSampleStyleSheet()
    center = ParagraphStyle("center", parent=styles["Normal"], alignment=TA_CENTER)
    body = styles["BodyText"]
    navy = colors.HexColor(f"#{brand.NAVY}")
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Times-Bold", textColor=navy, fontSize=13, spaceBefore=14, spaceAfter=6)
    status_style = ParagraphStyle("status", parent=styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceAfter=8)

    story = [
        brand.pdf_letterhead(page_width, "File Summary", sub=f"Generated {_fmt_date(date.today())}"),
        Spacer(1, 14),
        brand.pdf_meta_strip(page_width, [
            ("Client", engagement.client.name if engagement.client else ""),
            ("Engagement", engagement.title),
            ("Type", engagement.type),
            ("Period ended", _fmt_date(engagement.period_end)),
        ]),
        Spacer(1, 6),
    ]

    for code, key, label, filing_code in WORKPAPER_SECTIONS:
        if key == "forensic_report" and not is_forensic:
            continue  # the forensic report only exists on Investigative Engagements
        story.append(Paragraph(f"{filing_code or code}. {label}", h1))

        if key == "acceptance":
            record = engagement.client_acceptance
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record:
                decision = record.decision or "Not yet decided"
                story.append(Paragraph(f"Decision: <b>{decision}</b>", body))
                if record.checklist_items:
                    story.append(Spacer(1, 4))
                    story.append(_checklist_items_table(record.checklist_items))
            else:
                story.append(Paragraph("No Client Acceptance record on this engagement yet.", body))

        elif key == "entity":
            record = engagement.entity_understanding
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record:
                if record.checklist_items:
                    story.append(_checklist_items_table(record.checklist_items))
                else:
                    for field, question, _ in ENTITY_UNDERSTANDING_FIELDS:
                        value = getattr(record, field, None)
                        if value:
                            story.append(Paragraph(f"<b>{question}</b> {value}", body))
                            story.append(Spacer(1, 4))
            else:
                story.append(Paragraph("Understanding the Entity's Business not yet started.", body))

        elif key == "risks":
            record = engagement.risk_assessment
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record and record.rating:
                story.append(Paragraph(f"Overall risk rating: <b>{record.rating}</b> (score {record.score}/25).", body))
            else:
                story.append(Paragraph("Risk Assessment not yet completed.", body))

        elif key == "planning":
            record = engagement.materiality
            story.append(Paragraph(_sign_off_line(record, preparer_attr="updated_by"), status_style))
            if record and record.overall_materiality:
                story.append(Paragraph(
                    f"Overall materiality: {_fmt_num(record.overall_materiality)}. "
                    f"Performance materiality: {_fmt_num(record.performance_materiality)}. "
                    f"Clearly trivial threshold: {_fmt_num(record.trivial_threshold)}.", body,
                ))
            else:
                story.append(Paragraph("Materiality not yet calculated.", body))

        elif key == "analytical":
            record = engagement.analytical_review
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record:
                flagged = len(record.significant_lines) if record.significant_lines else 0
                story.append(Paragraph(f"{flagged} line(s) flagged as significant fluctuations.", body))
            else:
                story.append(Paragraph("Analytical Review not yet started.", body))

        elif key == "checklist":
            items = engagement.checklist_items
            story.append(Paragraph(f"Progress: {engagement.checklist_progress}% complete.", status_style))
            if items:
                story.append(_checklist_items_table(items))
            else:
                story.append(Paragraph("No checklist items added yet.", body))

        elif key == "substantive":
            areas = sorted(engagement.substantive_procedure_areas, key=lambda a: a.area) if engagement.substantive_procedure_areas else []
            if areas:
                for area in areas:
                    story.append(Paragraph(f"<b>{area.area}</b> - {_sign_off_line(area)}", status_style))
            else:
                story.append(Paragraph("No substantive procedure areas started yet.", body))

        elif key == "financials":
            record = engagement.financial_statements
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record and record.notes_to_financial_statements:
                for line in record.notes_to_financial_statements.split("\n"):
                    if line.strip():
                        story.append(Paragraph(line.strip(), body))
            else:
                story.append(Paragraph("No notes to the financial statements recorded yet.", body))

        elif key == "finalisation":
            record = engagement.finalisation_checklist
            story.append(Paragraph(_sign_off_line(record), status_style))
            if record and record.checklist_items:
                story.append(_checklist_items_table(record.checklist_items))
            else:
                story.append(Paragraph("Finalisation checklist not yet started.", body))

        elif key in ("rep_letter", "report_to_management", "forensic_report"):
            narrative_kind = {"rep_letter": "rep_letter", "report_to_management": "report_to_management", "forensic_report": "forensic_executive_summary"}[key]
            narrative = narratives.get(narrative_kind)
            story.append(Paragraph(_sign_off_line(narrative), status_style))
            text = narrative.body if narrative and narrative.body else DEFAULT_WORKPAPER_NARRATIVE_BODIES.get(narrative_kind, "")
            for line in text.split("\n"):
                if line.strip():
                    story.append(Paragraph(f"&bull; {line.strip()}", body))

        story.append(Spacer(1, 6))

    # ------------------------------------------------------- Tickmark legend
    # Tickmarks are only used on Substantive Procedure items now - the four
    # checklist types (Acceptance, Entity, Checklist, Finalisation) don't
    # carry a tickmark field.
    used_ids = set()
    used = {}
    item_sources = []
    for area in engagement.substantive_procedure_areas:
        item_sources += list(area.items)
    for item in item_sources:
        tm = getattr(item, "tickmark", None)
        if tm and tm.id not in used_ids:
            used_ids.add(tm.id)
            used[tm.id] = tm

    story.append(PageBreak())
    story.append(Paragraph("Tickmark Legend", h1))
    if used:
        data = [["Symbol", "Meaning"]] + [[tm.symbol, tm.meaning] for tm in sorted(used.values(), key=lambda t: t.symbol)]
        legend_table = Table(data, colWidths=[80, 400])
        legend_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor(f"#{brand.LINE}")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(legend_table)
    else:
        story.append(Paragraph("No tickmarks were used anywhere in this engagement's file.", body))

    footer = brand.pdf_footer()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf
