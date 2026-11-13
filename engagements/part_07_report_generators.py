"""engagements, part 7 of 7.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file engagements.py did."""

@engagements_bp.route("/<int:engagement_id>/workpapers/rep-letter/generate", methods=["POST"])
@login_required
def generate_rep_letter_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    statements = (
        fin.build_all_statements(trial_balance.lines, trial_balance.adjustments)
        if trial_balance and trial_balance.lines else None
    )
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="rep_letter").first()
    buf = wp.build_rep_letter_docx(engagement, statements, narrative)
    doc = _file_generated_workpaper(engagement, "rep_letter", "Management Representation Letter", "N9006", "Management_Representation_Letter", "docx", buf)
    flash(f"Management representation letter filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/report-to-management/generate", methods=["POST"])
@login_required
def generate_report_to_management_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="report_to_management").first()
    buf = wp.build_report_to_management_docx(engagement, narrative)
    doc = _file_generated_workpaper(engagement, "report_to_management", "Report to Management", "N8300", "Report_to_Management", "docx", buf)
    flash(f"Report to management filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/forensic-report/generate", methods=["POST"])
@login_required
def generate_forensic_report_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if engagement.type != "Investigative Engagement":
        flash("The forensic investigation report is only available on Investigative Engagements.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="forensic_executive_summary").first()
    buf = wp.build_forensic_report_docx(engagement, narrative)
    doc = _file_generated_workpaper(engagement, "forensic_report", "Forensic Investigation Report", None, "Forensic_Investigation_Report", "docx", buf, reference_override=filing_reference("forensic_report"))
    flash(f"Forensic investigation report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/it-audit-report/generate", methods=["POST"])
@login_required
def generate_it_audit_report_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if engagement.type != "Business Intelligence and IT Engagements":
        flash("The IT & Cyber Assurance Report is only available on Business Intelligence and IT Engagements.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))
    narrative = WorkpaperNarrative.query.filter_by(engagement_id=engagement_id, kind="it_audit_report_summary").first()
    buf = wp.build_it_audit_report_docx(engagement, narrative)
    doc = _file_generated_workpaper(engagement, "it_audit_report", "IT & Cyber Assurance Report", None, "IT_Cyber_Assurance_Report", "docx", buf, reference_override=filing_reference("it_audit_report"))
    flash(f"IT & Cyber Assurance Report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/tax-opinion/generate", methods=["POST"])
@login_required
def generate_tax_opinion_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if not engagement.has_tax_module:
        flash("The Tax Opinion is only available on an engagement with a Tax service turned on.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))
    narratives_by_kind = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
        if wn.kind.startswith("tax_opinion")
    }
    buf = wp.build_tax_opinion_docx(engagement, narratives_by_kind)
    doc = _file_generated_workpaper(engagement, "tax_opinion", "Tax Opinion", None, "Tax_Opinion", "docx", buf, reference_override=filing_reference("tax_opinion"))
    flash(f"Tax Opinion filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))


@engagements_bp.route("/<int:engagement_id>/workpapers/tax-health-check/generate", methods=["POST"])
@login_required
def generate_tax_health_check_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if not engagement.has_tax_module:
        flash("The Tax Health Check Report is only available on an engagement with a Tax service turned on.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))
    narratives_by_kind = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
        if wn.kind.startswith("tax_health_check")
    }
    buf = wp.build_tax_health_check_docx(engagement, narratives_by_kind)
    doc = _file_generated_workpaper(engagement, "tax_health_check", "Tax Health Check Report", None, "Tax_Health_Check_Report", "docx", buf, reference_override=filing_reference("tax_health_check"))
    flash(f"Tax Health Check Report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="tax"))


@engagements_bp.route("/<int:engagement_id>/workpapers/management-accounts-report/generate", methods=["POST"])
@login_required
def generate_management_accounts_report_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    if not engagement.has_accounting_module:
        flash("The Management Accounts Report is only available on an engagement with an Accounting service turned on.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="accounting"))
    narratives_by_kind = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
        if wn.kind.startswith("mgmt_report")
    }
    buf = wp.build_management_accounts_report_docx(engagement, narratives_by_kind)
    doc = _file_generated_workpaper(engagement, "management_accounts_report", "Management Accounts Report", None, "Management_Accounts_Report", "docx", buf, reference_override=filing_reference("management_accounts_report"))
    flash(f"Management Accounts Report filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="accounting"))


@engagements_bp.route("/<int:engagement_id>/workpapers/file-summary/generate", methods=["POST"])
@login_required
def generate_engagement_file_summary_pdf(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_engagement_file_summary_pdf(engagement)
    doc = _file_generated_workpaper(engagement, "file_summary", "Engagement File Summary", None, "Engagement_File_Summary", "pdf", buf)
    flash(f"Engagement file summary filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


@engagements_bp.route("/<int:engagement_id>/workpapers/acceptance-checklist/generate", methods=["POST"])
@login_required
def generate_acceptance_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_client_acceptance_checklist_docx(engagement, engagement.client_acceptance)
    doc = _file_generated_workpaper(engagement, "acceptance_checklist", "Client Acceptance Checklist", "N1009", "Client_Acceptance_Checklist", "docx", buf)
    flash(f"Client acceptance checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="acceptance"))


@engagements_bp.route("/<int:engagement_id>/workpapers/entity-checklist/generate", methods=["POST"])
@login_required
def generate_entity_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_entity_understanding_checklist_docx(engagement, engagement.entity_understanding)
    doc = _file_generated_workpaper(engagement, "entity_checklist", "Understanding the Entity Checklist", "N1003", "Understanding_the_Entity_Checklist", "docx", buf)
    flash(f"Understanding the Entity checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/<int:engagement_id>/workpapers/engagement-checklist/generate", methods=["POST"])
@login_required
def generate_engagement_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_engagement_checklist_docx(engagement)
    doc = _file_generated_workpaper(engagement, "engagement_checklist", "Engagement Checklist", None, "Engagement_Checklist", "docx", buf, reference_override="F-1")
    flash(f"Engagement checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


@engagements_bp.route("/<int:engagement_id>/workpapers/finalisation-checklist/generate", methods=["POST"])
@login_required
def generate_finalisation_checklist_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    buf = wp.build_finalisation_checklist_docx(engagement, engagement.finalisation_checklist)
    doc = _file_generated_workpaper(engagement, "finalisation_checklist", "Finalisation Checklist", "N9009", "Finalisation_Checklist", "docx", buf)
    flash(f"Finalisation checklist filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="finalisation"))


def _substantive_programme_context(engagement):
    """Shared setup for the two Substantive Procedures workpaper generators
    below - the same area list/order/reference codes the tab itself shows
    (see view_engagement), so the generated file always matches the screen."""
    is_forensic = engagement.type == "Investigative Engagement"
    is_business_it = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic:
        area_order, area_refs = FORENSIC_SUBSTANTIVE_AREAS, FORENSIC_AREA_REFERENCES
    elif is_business_it:
        area_order, area_refs = BUSINESS_IT_SUBSTANTIVE_AREAS, BUSINESS_IT_AREA_REFERENCES
    elif is_secretarial:
        area_order, area_refs = secretarial_substantive_areas(engagement), SECRETARIAL_AREA_REFERENCES
    else:
        area_order, area_refs = AUDIT_AREAS, AUDIT_AREA_REFERENCES
    areas_by_name = {
        a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement.id).all()
    }
    return areas_by_name, area_order, area_refs


def _procedures_programme_label(engagement):
    """The filename label for the generated procedures programme - matches
    the document title chosen in workpapers.build_substantive_procedures_
    docx/xlsx for each engagement type."""
    if engagement.type == "Investigative Engagement":
        return "Investigative_Procedures_Programme"
    if engagement.type == "Business Intelligence and IT Engagements":
        return "IT_Cyber_Assurance_Procedures_Programme"
    if engagement.type == "Secretarial":
        return "Secretarial_Execution_Plan"
    return "Substantive_Procedures_Programme"


@engagements_bp.route("/<int:engagement_id>/workpapers/substantive-procedures-docx/generate", methods=["POST"])
@login_required
def generate_substantive_procedures_docx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    areas_by_name, area_order, area_refs = _substantive_programme_context(engagement)
    if not areas_by_name:
        flash("Generate suggested procedures (or add some manually) before filing this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    buf = wp.build_substantive_procedures_docx(engagement, areas_by_name, area_order, area_refs)
    label = _procedures_programme_label(engagement)
    doc = _file_generated_workpaper(engagement, "substantive_procedures_docx", "Substantive Procedures Programme", None, label, "docx", buf, reference_override=filing_reference("substantive"))
    flash(f"Procedures programme (Word) filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))


@engagements_bp.route("/<int:engagement_id>/workpapers/substantive-procedures-xlsx/generate", methods=["POST"])
@login_required
def generate_substantive_procedures_xlsx(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    areas_by_name, area_order, area_refs = _substantive_programme_context(engagement)
    if not areas_by_name:
        flash("Generate suggested procedures (or add some manually) before filing this working paper.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
    buf = wp.build_substantive_procedures_xlsx(engagement, areas_by_name, area_order, area_refs)
    label = _procedures_programme_label(engagement)
    doc = _file_generated_workpaper(engagement, "substantive_procedures_xlsx", "Substantive Procedures Programme", None, label, "xlsx", buf, reference_override=filing_reference("substantive"))
    flash(f"Procedures programme (Excel) filed (v{doc.version}).", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="substantive"))
