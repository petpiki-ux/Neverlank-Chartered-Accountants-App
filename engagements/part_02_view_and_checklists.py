"""engagements, part 2 of 7.  Holds: functions and constants.

See __init__.py: all parts share one set of names, exactly as the single file engagements.py did."""

@engagements_bp.route("/<int:engagement_id>")
@login_required
def view_engagement(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    # require_accepted=False: the page itself (including its own Acceptance
    # tab) must always render for anyone with confidentiality access, even
    # before the gate clears - the gate below only decides which tabs' real
    # content vs. a "locked" placeholder the template shows.
    _ensure_engagement_access(engagement, require_accepted=False)
    client_acceptance = ClientAcceptance.query.filter_by(engagement_id=engagement_id).first()
    acceptance_cleared = engagement_acceptance_cleared(engagement)
    # Automated screening status, shown alongside the Sanctions & Adverse
    # Notice Screening card on the acceptance tab - how current the UN/OFAC/
    # EU cache is, and how many RBZ/FIU notices are on file to check against.
    sanctions_list_status = {
        row.source: row for row in SanctionsListStatus.query.filter(SanctionsListStatus.source.in_(SANCTIONS_AUTO_SOURCES)).all()
    }
    regulatory_notice_counts = {}
    for source in REGULATORY_NOTICE_SOURCES:
        notices = RegulatoryNotice.query.filter_by(source=source).all()
        regulatory_notice_counts[source] = {
            "total": len(notices),
            "searchable": sum(1 for n in notices if n.extraction_status == "extracted"),
        }
    # Confirmed Directors/Shareholders/etc already on file for this client
    # (see models.ClientKeyPerson, company_documents.py) - offered as a
    # quick-pick on the Sanctions & Adverse Notice Screening card so they
    # don't have to be retyped for every engagement. An unconfirmed
    # (AI-suggested) person isn't offered here - see
    # acceptance.add_screening_from_key_person.
    client_key_people = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, status="Confirmed").order_by(ClientKeyPerson.full_name).all()
    users = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    tab = request.args.get("tab", "overview")
    # Trial Balance is removed entirely for Secretarial engagements (per the
    # firm's instruction) - there's no nav link to it (see base template),
    # but guard the tab param directly too in case an old link/bookmark
    # still points at it.
    if tab == "trial_balance" and engagement.type == "Secretarial":
        tab = "overview"
    if tab == "registry_forms" and engagement.type != "Secretarial":
        tab = "overview"
    # A dedicated Tax Advisory engagement works from the client's finished
    # accounts: no Analytical Review, Risk Assessment, Trial Balance or
    # Substantive Procedures (its tax-head Execution Plan is on the Tax tab).
    # Old links and bookmarks to those tabs land somewhere sensible.
    # It also has no Understanding the Entity or Planning tab (the Tax tab
    # holds the Tax Entity Profile and Regulatory Matrix, the Tax Analytical
    # Review and the Tax Risk Register). It keeps the Trial Balance tab, to
    # import a trial balance, next to the Accounting tab, to capture one, and
    # has its own Asset Register tab.
    if engagement.is_tax_advisory_type:
        if tab == "substantive":
            tab = "tax"
        elif tab in ("analytical", "risks", "entity", "planning"):
            tab = "overview"
    elif tab == "asset_register" and not engagement.has_asset_schedules:
        tab = "substantive"
    if tab == "qpd" and not engagement.has_tax_module:
        tab = "overview"
    if tab == "capital_allowances" and not (engagement.has_tax_module or engagement.has_asset_schedules):
        tab = "overview"
    # The VAT register has its own tab once the client has a VAT number (and stays on the Tax tab until then).
    if tab == "vat" and not engagement.shows_vat_tab:
        tab = "tax" if engagement.has_tax_module else "overview"
    elif tab == "tax" and engagement.shows_vat_tab and request.args.get("vat_period"):
        tab = "vat"
    # The stand-alone service lines (Whistle-blower / Training) only have the
    # shared Overview / Client Acceptance / Documents / Tasks tabs here - their
    # own workspaces live in whistleblower.py / training.py. Corporate Finance &
    # Business Advisory additionally keeps the Checklist tab; its models and
    # appraisals live in corporate_finance.py. None of them has the audit
    # (entity / analytical / risk / planning / TB / substantive / finalisation)
    # or tax-computation tabs.
    if engagement.is_standalone_service:
        allowed_tabs = ("overview", "acceptance", "documents", "tasks")
        if engagement.is_corporate_finance:
            allowed_tabs += ("checklist",)
        if tab not in allowed_tabs:
            tab = "overview"
    # Document Templates matching this engagement's type (Audit/Assurance/
    # Consulting) - shown on the Documents tab so the right blank
    # letters/workpapers for this client's engagement are one click away,
    # with no need to go hunting in the separate Document Templates library.
    matching_templates = (
        DocumentTemplate.query.filter_by(type=engagement.type)
        .order_by(DocumentTemplate.ref_code)
        .all()
    )
    risk_assessment = RiskAssessment.query.filter_by(engagement_id=engagement_id).first()
    # An Investigative Engagement gets the Fraud Triangle-based forensic
    # questionnaire, and a Business Intelligence and IT Engagement gets the
    # cyber/IT/AML-CFT control questionnaire, instead of the ordinary audit
    # risk-of-material-misstatement one - see RiskAssessment.
    # likelihood_questions/impact_questions, which the save route below
    # mirrors.
    is_forensic_risk = engagement.type == "Investigative Engagement"
    is_business_it_risk = engagement.type == "Business Intelligence and IT Engagements"
    is_secretarial = engagement.type == "Secretarial"
    if is_forensic_risk:
        likelihood_questions = FORENSIC_RISK_LIKELIHOOD_QUESTIONS
        impact_questions = FORENSIC_RISK_IMPACT_QUESTIONS
    elif is_business_it_risk:
        likelihood_questions = BUSINESS_IT_RISK_LIKELIHOOD_QUESTIONS
        impact_questions = BUSINESS_IT_RISK_IMPACT_QUESTIONS
    elif is_secretarial:
        likelihood_questions = SECRETARIAL_RISK_LIKELIHOOD_QUESTIONS
        impact_questions = SECRETARIAL_RISK_IMPACT_QUESTIONS
    else:
        likelihood_questions = RISK_LIKELIHOOD_QUESTIONS
        impact_questions = RISK_IMPACT_QUESTIONS
    materiality = MaterialityCalculation.query.filter_by(engagement_id=engagement_id).first()
    qualitative_materiality_factors = {
        f.factor_key: f for f in QualitativeMaterialityFactor.query.filter_by(engagement_id=engagement_id).all()
    }
    qualitative_materiality_index = QualitativeMaterialityFactor.overall_rating_for(qualitative_materiality_factors.values())
    audit_strategy = AuditStrategy.query.filter_by(engagement_id=engagement_id).first() if is_forensic_risk else None
    # The Secretarial Engagement Plan - what the Planning tab is replaced
    # with on a Secretarial engagement, exactly like audit_strategy above
    # replaces it for an Investigative Engagement.
    secretarial_plan = SecretarialPlan.query.filter_by(engagement_id=engagement_id).first() if is_secretarial else None
    secretarial_compliance_suggestions = _secretarial_compliance_calendar_suggestions(engagement.period_end, secretarial_plan) if is_secretarial else None
    # The Registry Forms tab (Secretarial only): the Registrar's CR forms, and this engagement's filing log.
    registry_form_groups = []
    registry_filings = []
    registry_activity_keys = []
    if is_secretarial:
        import cr_forms
        registry_activity_keys = [a for a in (engagement.secretarial_activities or "").split(",") if a]
        _by_group = {}
        for _f in sorted(cr_forms.CR_FORMS, key=lambda f: cr_forms.sort_key(f["code"])):
            _by_group.setdefault(_f["group"], []).append(_f)
        registry_form_groups = [(g, _by_group[g]) for g in cr_forms.GROUPS if g in _by_group]
        registry_filings = sorted(
            RegistryFormFiling.query.filter_by(engagement_id=engagement_id).all(),
            key=lambda x: (x.status in ("Lodged", "Registered", "Withdrawn"), x.due_date or date.max, x.id),
        )
    entity_understanding = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    # Public-information scans live on the Client (see models.
    # EntityPublicResearch/company_documents.run_public_research) since the
    # firm's understanding of a client's business doesn't reset between
    # engagements - only the most recent run is shown here, as a pointer
    # back to the client's page where the full history lives and new scans
    # are run from.
    latest_public_research = engagement.client.public_research_runs[0] if engagement.client.public_research_runs else None
    analytical_review = AnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
    scope_suggestion = SCOPE_SUGGESTIONS.get(risk_assessment.rating) if risk_assessment and risk_assessment.rating else None

    trial_balance = TrialBalance.query.filter_by(engagement_id=engagement_id).first()
    financial_statements = FinancialStatements.query.filter_by(engagement_id=engagement_id).first()
    # Depreciation policy / Asset Register (Substantive Procedures >
    # Property, Plant and Equipment) - feeds both the PPE note below (in
    # place of the plain trial-balance-account list, once populated) and
    # the "Property, plant and equipment" accounting policy paragraph.
    ppe_asset_classes, ppe_assets, ppe_class_dicts, ppe_asset_dicts = _ppe_plain_data(engagement_id)
    ppe_movement, ppe_policy_text = _ppe_movement_and_policy(ppe_class_dicts, ppe_asset_dicts, engagement.period_end)
    # The Financial Statements are always built from the ADJUSTED trial
    # balance (preliminary TB + audit adjustments, current year only) -
    # Analytical Review and Substantive Procedures work from the preliminary
    # trial_balance.lines directly, unadjusted, so they're unaffected by this.
    statements = (
        fin.build_all_statements(
            trial_balance.lines, trial_balance.adjustments,
            reporting_framework=engagement.reporting_framework, cash_flow_method=engagement.cash_flow_method,
            ppe_movement=ppe_movement, ppe_policy_text=ppe_policy_text, whole_numbers=True,
        )
        if trial_balance and trial_balance.lines else None
    )
    if statements:
        import client_notes, cf_prior
        client_notes.attach_client_notes(statements, engagement)
        cf_prior.attach_prior_cash_flow(statements, engagement, editable=True)
    # Directors' Statement, Audit Opinion / Review Report, Income Tax
    # Computation, Deferred Tax Computation - each queried only (never
    # created here); the "not saved yet" state is rendered with sensible
    # defaults by the template, and the record is only actually created the
    # first time its own Save action is posted (same pattern as the
    # Financial Statements closing notes' own defaults).
    directors_statement = DirectorsStatement.query.filter_by(engagement_id=engagement_id).first()
    suggested_directors = ClientKeyPerson.query.filter_by(client_id=engagement.client_id, role="Director", status="Confirmed").order_by(ClientKeyPerson.id).all()
    audit_opinion = AuditOpinion.query.filter_by(engagement_id=engagement_id).first()
    default_opinion_paragraphs = fin.default_audit_opinion_paragraphs(
        (audit_opinion.report_basis if audit_opinion else ("audit" if engagement.type == "Audit" else "review")),
        (audit_opinion.modification if audit_opinion else "unmodified"),
        engagement.client.name, engagement.period_end, REPORTING_FRAMEWORK_LABELS.get(engagement.reporting_framework),
    )
    income_tax_computation = IncomeTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    profit_before_tax = statements["pl"]["profit_before_tax"]["current"] if statements else None
    profit_before_tax_is_manual = False
    if profit_before_tax is None and income_tax_computation and income_tax_computation.profit_before_tax_manual is not None:
        # no Trial Balance (a Tax Advisory engagement works from the client's finished accounts): the figure typed on this tab
        profit_before_tax = income_tax_computation.profit_before_tax_manual
        profit_before_tax_is_manual = True
    income_tax_lines = [
        {"item_type": l.item_type, "description": l.description, "amount": l.amount}
        for l in (income_tax_computation.items if income_tax_computation else [])
    ]
    income_tax_result = fin.build_income_tax_computation(
        profit_before_tax, income_tax_lines,
        tax_loss_brought_forward=income_tax_computation.tax_loss_brought_forward if income_tax_computation else 0.0,
        tax_rate_percent=income_tax_computation.tax_rate_percent if income_tax_computation else None,
        aids_levy_percent=income_tax_computation.aids_levy_percent if income_tax_computation else None,
    ) if profit_before_tax is not None else None
    income_tax_tb_current = statements["totals"]["income_tax_expense"]["current"] if statements else None
    # Same "always shown, basis for a proposed adjustment" reconciliation
    # pattern as the PPE note (financials.build_notes) - the computed
    # current tax charge vs whatever is actually recorded in the trial
    # balance's Income Tax Expense line, whether or not they agree.
    income_tax_reconciliation = None
    if income_tax_result is not None and income_tax_tb_current is not None:
        diff = income_tax_tb_current - income_tax_result["total_tax_charge"]
        income_tax_reconciliation = {
            "tb_current": income_tax_tb_current, "computed": income_tax_result["total_tax_charge"],
            "diff": diff, "ties_out": abs(diff) <= 0.01,
        }

    deferred_tax_computation = DeferredTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    ppe_accounting_nbv = ppe_movement["total"]["closing_nbv"] if ppe_movement else (statements["totals"]["ppe"]["current"] if statements else None)
    deferred_tax_other_items = [
        {"description": i.description, "accounting_amount": i.accounting_amount, "tax_base_amount": i.tax_base_amount}
        for i in (deferred_tax_computation.items if deferred_tax_computation else [])
    ]
    # The PPE temporary difference is picked up automatically: carrying amounts from the Asset Register, tax bases from the Capital
    # Allowance schedule, class by class (unless the preparer chose to type the tax base).
    deferred_tax_ppe = None
    ca_ctx_for_dt = None
    if tab == "deferred_tax" and (engagement.has_tax_module or engagement.has_asset_schedules):
        import engagement_tax
        ca_ctx_for_dt = engagement_tax.capital_allowance_context(engagement)
        deferred_tax_ppe = engagement_tax.deferred_tax_ppe(ca_ctx_for_dt, ppe_movement, deferred_tax_computation)
    deferred_tax_adj = None
    if tab == "deferred_tax" and trial_balance and trial_balance.lines and deferred_tax_computation and deferred_tax_computation.tax_rate_percent is not None:
        import deferred_tax_adj as _dta
        deferred_tax_adj = _dta.position(engagement)
    dt_ppe_base = (deferred_tax_ppe["tax_base_total"] if deferred_tax_ppe is not None else (deferred_tax_computation.ppe_tax_base if deferred_tax_computation else None))
    deferred_tax_result = fin.build_deferred_tax_computation(
        deferred_tax_computation.tax_rate_percent if deferred_tax_computation else None,
        ppe_accounting_nbv, dt_ppe_base,
        deferred_tax_other_items,
        ppe_rows=deferred_tax_ppe["rows"] if deferred_tax_ppe is not None else None,
    )
    deferred_tax_tb_net = (statements["totals"]["deferred_tax_liability"]["current"] - statements["totals"]["deferred_tax_asset"]["current"]) if statements else None
    deferred_tax_reconciliation = None
    if deferred_tax_result is not None and deferred_tax_tb_net is not None:
        diff = deferred_tax_tb_net - deferred_tax_result["total_deferred_tax"]
        deferred_tax_reconciliation = {
            "tb_net": deferred_tax_tb_net, "computed": deferred_tax_result["total_deferred_tax"],
            "diff": diff, "ties_out": abs(diff) <= 0.01,
        }

    # Face of Trial Balance (Trial Balance tab): the summarised, by-category
    # view alongside the detailed, account-by-account one - built from the
    # same preliminary lines so the two views (and their totals) always agree.
    tb_face_summary = fin.summarise_tb_by_category(trial_balance.lines) if trial_balance and trial_balance.lines else []
    # Account Mapping setup (Trial Balance tab): a best-effort category
    # suggestion per unmapped account, from its name alone, for the
    # accountant to review and confirm in one batch rather than picking
    # every account from a blank dropdown - see fin.suggest_fs_category().
    # Never written to the trial balance on its own; only pre-fills the
    # dropdown, and only takes effect once confirmed via
    # confirm_suggested_tb_mappings below.
    suggested_categories = (
        {line.id: fin.suggest_fs_category(line.account_name) for line in trial_balance.lines if not line.fs_category}
        if trial_balance else {}
    )

    # On an Investigative Engagement, Substantive Procedures uses the four
    # forensic evidence-type categories, and on a Business Intelligence and
    # IT Engagement the four cyber/IT/AML-CFT assurance domains, instead of
    # the financial-statement audit areas - see FORENSIC_SUBSTANTIVE_AREAS/
    # BUSINESS_IT_SUBSTANTIVE_AREAS above.
    if is_forensic_risk:
        substantive_area_names = FORENSIC_SUBSTANTIVE_AREAS
        substantive_area_refs = FORENSIC_AREA_REFERENCES
    elif is_business_it_risk:
        substantive_area_names = BUSINESS_IT_SUBSTANTIVE_AREAS
        substantive_area_refs = BUSINESS_IT_AREA_REFERENCES
    elif is_secretarial:
        substantive_area_names = secretarial_substantive_areas(engagement)
        substantive_area_refs = SECRETARIAL_AREA_REFERENCES
    elif engagement.is_tax_advisory_type:
        substantive_area_names = []   # the audit areas do not apply; the tax heads are added just below
        substantive_area_refs = AUDIT_AREA_REFERENCES
    else:
        substantive_area_names = AUDIT_AREAS
        substantive_area_refs = AUDIT_AREA_REFERENCES

    # The Tax module's Execution Plan (Module 7) reuses this exact
    # Substantive Procedures machinery - one area per TAX_HEADS entry -
    # exactly like Secretarial's own Execution Plan does above, so tax-head
    # sections just show up as extra sections on this same tab (with their
    # own Preparer/Reviewer/Partner sign-off) whenever the Tax module is on,
    # on top of whatever this engagement type's own areas already are -
    # never replacing them, since Tax is commonly bundled onto another
    # engagement type rather than being its own.
    if engagement.has_tax_module:
        substantive_area_names = list(substantive_area_names) + [h for h in TAX_HEADS if h not in substantive_area_names]

    # Same reuse technique for the Accounting module's write-up/close-out
    # Execution Plan - one area per ACCOUNTING_AREAS entry, appended on top
    # of whatever this engagement type's own areas already are, whenever
    # Engagement.has_accounting_module is True.
    if engagement.has_accounting_module and not engagement.is_tax_advisory_type:
        substantive_area_names = list(substantive_area_names) + [a for a in ACCOUNTING_AREAS if a not in substantive_area_names]

    substantive_areas_by_name = {
        a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).all()
    }

    finalisation_checklist = FinalisationChecklist.query.filter_by(engagement_id=engagement_id).first()

    # Firm-wide tickmark legend (see tickmarks.py) - offered as a picker on
    # each Substantive Procedures item.
    tickmarks = Tickmark.query.order_by(Tickmark.symbol).all()

    # Firm-wide Filing Index (see filing_index.py) - Current File (N-series)
    # codes only, offered as a WP No. picker when uploading a Document.
    filing_index_sections = FilingIndexSection.query.filter_by(is_permanent=False, is_active=True).order_by(FilingIndexSection.order, FilingIndexSection.code).all()

    # The narrative workpapers with a persistent, editable copy (Rep Letter,
    # Report to Management, Forensic Report executive summary) - keyed by
    # kind so the Finalisation tab can look each one up directly. Any kind
    # not yet saved is seeded (in memory only, not committed) with the
    # firm's default starting wording, so the edit box is never blank.
    workpaper_narratives = {
        wn.kind: wn for wn in WorkpaperNarrative.query.filter_by(engagement_id=engagement_id).all()
    }
    # The Tax Opinion's 14 parts and the Tax Health Check Report's 15 parts
    # (see models.TAX_OPINION_PARTS/TAX_HEALTH_CHECK_PARTS) are only ever
    # seeded/shown on an engagement with the Tax module on - every other
    # WorkpaperNarrative kind (rep_letter etc.) is seeded for every
    # engagement, unchanged from before the Tax module existed.
    seedable_kinds = [
        (kind, label, section) for kind, label, section in WORKPAPER_NARRATIVE_KINDS
        if (engagement.has_tax_module or section not in ("tax_opinion", "tax_health_check"))
        and (engagement.has_accounting_module or section != "management_accounts_report")
    ]
    for kind, _label, _section in seedable_kinds:
        if kind not in workpaper_narratives:
            workpaper_narratives[kind] = _get_or_seed_workpaper_narrative(engagement_id, kind, engagement)

    # Review Queries, grouped for the template: by section for every plain
    # section, and separately by audit area for "substantive" (which has one
    # query list per area rather than one for the whole tab). Newest first
    # within each group.
    all_queries = (
        EngagementQuery.query.filter_by(engagement_id=engagement_id)
        .order_by(EngagementQuery.raised_at.desc())
        .all()
    )
    queries_by_section = {}
    queries_by_area = {}
    for q in all_queries:
        if q.section == "substantive" and q.area_name:
            queries_by_area.setdefault(q.area_name, []).append(q)
        else:
            queries_by_section.setdefault(q.section, []).append(q)
    open_query_count = sum(1 for q in all_queries if q.status == "Open")

    # ---------- Tax tab (Tax Advisory & Tax Compliance module) ----------
    # Only actually queried when the Tax tab could be shown - a plain
    # Audit/Assurance/etc. engagement with no Tax service on pays for none
    # of this. See Engagement.has_tax_module / models.py's "Tax Advisory &
    # Tax Compliance module" section for the full picture of what's reused
    # (RiskItem, SubstantiveProcedureArea/Item, IncomeTaxComputation/
    # DeferredTaxComputation, WorkpaperNarrative) vs. genuinely new here.
    tax_entity_profile = None
    tax_registrations = []
    tax_deadlines = []
    tax_analytical_review = None
    tax_risk_items = []
    tax_positions = []
    tax_return_records = []
    tax_information_requests = []
    tax_research_log = []
    tax_structuring_options = []
    tax_disputes = []
    legislative_updates_for_import = []
    tax_checklist_items = []
    tax_checklist_by_head = {}
    tax_execution_areas_by_name = {}
    if engagement.has_tax_module:
        tax_entity_profile = TaxEntityProfile.query.filter_by(engagement_id=engagement_id).first()
        tax_registrations = TaxRegistration.query.filter_by(engagement_id=engagement_id).order_by(TaxRegistration.record_type, TaxRegistration.id).all()
        tax_deadlines = TaxDeadline.query.filter_by(engagement_id=engagement_id).order_by(TaxDeadline.due_date.asc()).all()
        tax_analytical_review = TaxAnalyticalReview.query.filter_by(engagement_id=engagement_id).first()
        tax_risk_items = RiskItem.query.filter_by(engagement_id=engagement_id, module="tax").order_by(RiskItem.id).all()
        tax_positions = TaxPosition.query.filter_by(engagement_id=engagement_id).order_by(TaxPosition.id).all()
        tax_return_records = TaxReturnRecord.query.filter_by(engagement_id=engagement_id).order_by(TaxReturnRecord.due_date.asc().nullslast()).all()
        tax_information_requests = TaxInformationRequest.query.filter_by(engagement_id=engagement_id).order_by(TaxInformationRequest.id).all()
        if engagement.has_tax_advisory:
            tax_research_log = TaxResearchLogEntry.query.filter_by(engagement_id=engagement_id).order_by(TaxResearchLogEntry.prepared_at.desc()).all()
            tax_structuring_options = TaxStructuringOption.query.filter_by(engagement_id=engagement_id).order_by(TaxStructuringOption.id).all()
            tax_disputes = TaxDispute.query.filter_by(engagement_id=engagement_id).order_by(TaxDispute.id).all()
            # Legislative updates tagged as relevant to this engagement's own
            # client (see LegislativeUpdateClientLink) come first, since
            # those are the ones most likely worth pulling into this
            # engagement's Research Log - but every filed/logged update is
            # offered too, in case a firm-wide update matters here even
            # though no one has tagged this particular client yet.
            client_tagged_update_ids = {link.legislative_update_id for link in engagement.client.legislative_update_links} if engagement.client else set()
            all_legislative_updates = LegislativeUpdate.query.order_by(LegislativeUpdate.created_at.desc()).all()
            legislative_updates_for_import = (
                [u for u in all_legislative_updates if u.id in client_tagged_update_ids]
                + [u for u in all_legislative_updates if u.id not in client_tagged_update_ids]
            )
        tax_checklist_items = TaxChecklistItem.query.filter_by(engagement_id=engagement_id).order_by(TaxChecklistItem.order, TaxChecklistItem.id).all()
        for item in tax_checklist_items:
            tax_checklist_by_head.setdefault(item.tax_head or "General", []).append(item)
        # The Execution Plan (Module 7) - one SubstantiveProcedureArea per
        # tax head, exactly like Secretarial's own Execution Plan reuses
        # this same model (see SECRETARIAL_SUBSTANTIVE_AREAS above).
        tax_execution_areas_by_name = {
            a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).filter(SubstantiveProcedureArea.area.in_(TAX_HEADS)).all()
        }
    current_penalty_interest_rates = PenaltyInterestRate.query.order_by(PenaltyInterestRate.tax_head, PenaltyInterestRate.rate_type, PenaltyInterestRate.effective_from.desc()).all()
    tax_penalty_calculations = PenaltyInterestCalculation.query.filter_by(engagement_id=engagement_id).order_by(PenaltyInterestCalculation.calculated_at.desc()).all() if engagement.has_tax_module else []
    tax_opinion_narratives = {k: workpaper_narratives.get(k) for k, _ in TAX_OPINION_PARTS} if engagement.has_tax_module else {}
    tax_health_check_narratives = {k: workpaper_narratives.get(k) for k, _ in TAX_HEALTH_CHECK_PARTS} if engagement.has_tax_module else {}

    # VAT Invoice Register & Input/Output Tax Working Paper - see tax.py's
    # module comment above VATRate/its own "VAT Invoice Register" section.
    # Rates are firm-wide (like Penalty & Interest rates above); invoices
    # and import batches are engagement-scoped.
    current_vat_rates = VATRate.query.order_by(VATRate.rate_type, VATRate.effective_from.desc()).all()
    vat_invoices = []
    vat_import_batches = []
    vat_output_tax_total = 0.0
    vat_input_tax_total = 0.0
    vat_output_taxable_total = 0.0
    vat_input_taxable_total = 0.0
    if engagement.has_tax_module:
        vat_invoices = VATInvoice.query.filter_by(engagement_id=engagement_id).order_by(VATInvoice.invoice_date.desc().nullslast(), VATInvoice.id.desc()).all()
        vat_import_batches = VATImportBatch.query.filter_by(engagement_id=engagement_id).order_by(VATImportBatch.imported_at.desc()).all()
        # Each kind (sales invoices, export sales, credit notes issued,
        # purchase invoices, Bills of Entry, credit notes received) is totalled
        # separately; the headline figures are the NET of credit notes.
        vat_summary = vat_working_paper_summary(vat_invoices)
        vat_output_tax_total = vat_summary["output_tax"]
        vat_input_tax_total = vat_summary["input_tax"]
        vat_output_taxable_total = vat_summary["output_taxable"]
        vat_input_taxable_total = vat_summary["input_taxable"]
    else:
        vat_summary = vat_working_paper_summary([])
    vat_net_position = vat_output_tax_total - vat_input_tax_total
    # Tasks by due date, soonest first; a task with no due date goes last
    # (sorting a blank against a date used to crash the engagement page).
    engagement_tasks_sorted = sorted(engagement.tasks, key=lambda t: (t.due_date is None, t.due_date or date.max))
    from models import ready_to_bill_tasks
    ready_to_bill_count = len(ready_to_bill_tasks(engagement))
    vat_invoices_by_kind = {code: [i for i in vat_invoices if i.kind == code] for code, *_ in VAT_DOCUMENT_KINDS}

    # Monthly VAT computations: the register opens as a list of VAT months
    # (by each document's own date) with their status and net VAT; a month's
    # working paper and documents are only shown once it is opened
    # (?vat_period=2026-09, or 'undated' for documents with no date).
    vat_periods = []
    vat_undated_docs = [i for i in vat_invoices if not i.invoice_date]
    vat_selected_key = None
    vat_selected_period = None
    vat_selected_docs = []
    if engagement.has_tax_module:
        period_rows = {(r.year, r.month): r for r in VATPeriod.query.filter_by(engagement_id=engagement_id).all()}
        docs_by_month = {}
        for inv in vat_invoices:
            ym = vat_period_of(inv.invoice_date)
            if ym:
                docs_by_month.setdefault(ym, []).append(inv)
        for ym in sorted(set(docs_by_month) | set(period_rows), reverse=True):
            docs = docs_by_month.get(ym, [])
            row = period_rows.get(ym)
            vat_periods.append({
                "key": vat_period_key(*ym), "label": vat_period_label(*ym), "year": ym[0], "month": ym[1],
                "count": len(docs), "by_currency": vat_working_paper_by_currency(docs),
                "row": row, "is_done": bool(row and row.is_done),
            })
        requested = request.args.get("vat_period", "").strip()
        if requested == "undated" and vat_undated_docs:
            vat_selected_key, vat_selected_docs = "undated", vat_undated_docs
            vat_selected_period = {"key": "undated", "label": "Documents with no date", "count": len(vat_undated_docs),
                                   "row": None, "is_done": False, "undated": True}
        else:
            parsed = parse_vat_period_key(requested) if requested else None
            match = next((p for p in vat_periods if parsed and (p["year"], p["month"]) == parsed), None)
            if match:
                vat_selected_key, vat_selected_period = match["key"], match
                vat_selected_docs = docs_by_month.get(parsed, [])
    # ZIMRA wants VAT accounted for and paid in the currency of trade, so the
    # working paper and the register are produced separately per currency -
    # here only for the month that has been opened.
    vat_by_currency = vat_working_paper_by_currency(vat_selected_docs)
    vat_invoices_by_currency_kind = {
        cur: {code: [i for i in vat_selected_docs if (i.currency or VAT_DEFAULT_CURRENCY) == cur and i.kind == code] for code, *_ in VAT_DOCUMENT_KINDS}
        for cur in vat_by_currency
    }

    # Points to look at in the opened month, each tied to the VAT Act (vat_checks.py). Prompts only.
    vat_month_checks = []
    vat_return_due = None
    if vat_selected_period and vat_selected_key and vat_selected_key != "undated":
        _ym = parse_vat_period_key(vat_selected_key)
        if _ym:
            import vat_checks
            vat_return_due = vat_checks.return_due_date(*_ym)
            vat_month_checks = vat_checks.month_checks(vat_selected_docs, _ym[0], _ym[1], is_done=bool(vat_selected_period.get("is_done")))

    # ---------- Accounting tab (Financial/Cost/Management Accounting module) ----------
    # Only actually queried when the Accounting tab could be shown - see
    # Engagement.has_accounting_module / the module comment in models.py for
    # what's reused (RiskItem, SubstantiveProcedureArea/Item, TrialBalance/
    # TrialBalanceLine, WorkpaperNarrative) vs. genuinely new here.
    gl_accounts = []
    journal_entries = []
    bank_reconciliations = []
    accounting_risk_items = []
    cost_centres = []
    costing_records = []
    standard_cost_variances = []
    cvp_analyses = []
    budgets = []
    kpi_metrics = []
    accounting_checklist_items = []
    accounting_checklist_by_area = {}
    accounting_execution_areas_by_name = {}
    if engagement.has_accounting_module:
        gl_accounts = GLAccount.query.filter_by(engagement_id=engagement_id).order_by(GLAccount.order, GLAccount.code).all()
        journal_entries = JournalEntry.query.filter_by(engagement_id=engagement_id).order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).all()
        bank_reconciliations = BankReconciliation.query.filter_by(engagement_id=engagement_id).order_by(BankReconciliation.statement_date.desc()).all()
        accounting_risk_items = RiskItem.query.filter_by(engagement_id=engagement_id, module="accounting").order_by(RiskItem.id).all()
        if engagement.has_cost_accounting:
            cost_centres = CostCentre.query.filter_by(engagement_id=engagement_id).order_by(CostCentre.name).all()
            costing_records = CostingRecord.query.filter_by(engagement_id=engagement_id).order_by(CostingRecord.id.desc()).all()
            standard_cost_variances = StandardCostVariance.query.filter_by(engagement_id=engagement_id).order_by(StandardCostVariance.id.desc()).all()
            cvp_analyses = CVPAnalysis.query.filter_by(engagement_id=engagement_id).order_by(CVPAnalysis.id.desc()).all()
        if engagement.has_management_accounting:
            budgets = Budget.query.filter_by(engagement_id=engagement_id).order_by(Budget.id.desc()).all()
            kpi_metrics = KPIMetric.query.filter_by(engagement_id=engagement_id).order_by(KPIMetric.id.desc()).all()
        accounting_checklist_items = AccountingChecklistItem.query.filter_by(engagement_id=engagement_id).order_by(AccountingChecklistItem.order, AccountingChecklistItem.id).all()
        for item in accounting_checklist_items:
            accounting_checklist_by_area.setdefault(item.area or "General", []).append(item)
        # The Execution Plan - one SubstantiveProcedureArea per
        # ACCOUNTING_AREAS entry, exactly like the Tax module's own
        # Execution Plan reuses this same model (see TAX_HEADS above).
        accounting_execution_areas_by_name = {
            a.area: a for a in SubstantiveProcedureArea.query.filter_by(engagement_id=engagement_id).filter(SubstantiveProcedureArea.area.in_(ACCOUNTING_AREAS)).all()
        }
    management_report_narratives = {k: workpaper_narratives.get(k) for k, _ in MANAGEMENT_ACCOUNTS_REPORT_PARTS} if engagement.has_accounting_module else {}

    # Tax Advisory engagements: the Capital Allowances and Provisional Tax (QPD) tabs, and the allowance schedule's tax value offered on
    # the Deferred Tax tab. Worked only for the tabs that show them.
    tax_tab_context = {}
    if engagement.has_tax_module:
        import engagement_tax
        if tab == "deferred_tax" and ca_ctx_for_dt is not None:
            tax_tab_context.update(ca_ctx_for_dt)
        elif tab in ("capital_allowances", "deferred_tax", "income_tax"):
            tax_tab_context.update(engagement_tax.capital_allowance_context(engagement))
        if tab == "qpd":
            tax_tab_context.update(engagement_tax.qpd_context(engagement))
    elif engagement.has_asset_schedules and tab in ("capital_allowances", "deferred_tax"):
        import engagement_tax
        tax_tab_context.update(ca_ctx_for_dt if (tab == "deferred_tax" and ca_ctx_for_dt is not None) else engagement_tax.capital_allowance_context(engagement))
    # The Asset Register and Capital Allowances tabs: opening balances, additions, disposals and the schedules.
    if engagement.has_asset_schedules and tab in ("asset_register", "capital_allowances"):
        import asset_schedules
        tax_tab_context.update(asset_schedules.tab_context(engagement))

    return render_template(
        "engagements/detail.html",
        profit_before_tax_is_manual=profit_before_tax_is_manual,
        vat_home_tab="vat" if engagement.shows_vat_tab else "tax",
        **tax_tab_context,
        engagement=engagement,
        users=users,
        tab=tab,
        checklist_statuses=CHECKLIST_STATUSES,
        risk_statuses=RISK_STATUSES,
        task_statuses=TASK_STATUSES,
        task_categories=TASK_CATEGORIES,
        matching_templates=matching_templates,
        risk_assessment=risk_assessment,
        likelihood_questions=likelihood_questions,
        impact_questions=impact_questions,
        is_forensic_risk=is_forensic_risk,
        is_secretarial=is_secretarial,
        materiality=materiality,
        qualitative_materiality_factors=qualitative_materiality_factors,
        qualitative_materiality_index=qualitative_materiality_index,
        qualitative_materiality_factor_defs=QUALITATIVE_MATERIALITY_FACTORS,
        qualitative_materiality_ratings=QUALITATIVE_MATERIALITY_RATINGS,
        audit_strategy=audit_strategy,
        audit_strategy_phase_statuses=AUDIT_STRATEGY_PHASE_STATUSES,
        secretarial_plan=secretarial_plan,
        secretarial_compliance_suggestions=secretarial_compliance_suggestions,
        registry_form_groups=registry_form_groups,
        registry_filings=registry_filings,
        registry_activity_keys=registry_activity_keys,
        registry_form_statuses=(__import__('cr_forms').FORM_STATUSES),
        registry_today=date.today(),
        secretarial_activities=SECRETARIAL_ACTIVITIES,
        scope_suggestion=scope_suggestion,
        entity_understanding=entity_understanding,
        entity_fields=ENTITY_UNDERSTANDING_FIELDS,
        latest_public_research=latest_public_research,
        analytical_review=analytical_review,
        trial_balance=trial_balance,
        tb_face_summary=tb_face_summary,
        suggested_categories=suggested_categories,
        financial_statements=financial_statements,
        statements=statements,
        cf_prior_lines=__import__("cf_prior").lines_for(engagement.id),
        reporting_frameworks=REPORTING_FRAMEWORKS,
        reporting_framework_labels=REPORTING_FRAMEWORK_LABELS,
        general_information_note=fin.general_information_note(engagement.client, engagement),
        default_basis_of_preparation=fin.REPORTING_FRAMEWORK_COMPLIANCE_TEXT.get(engagement.reporting_framework, "") + fin.DEFAULT_BASIS_OF_PREPARATION_TAIL,
        default_closing_notes=fin.DEFAULT_CLOSING_NOTE_TEXT,
        cash_flow_methods=CASH_FLOW_METHODS,
        cash_flow_method_labels=CASH_FLOW_METHOD_LABELS,
        pie_criteria=PIE_CRITERIA,
        sme_act_sectors=SME_ACT_SECTORS,
        sme_act_size_bands=SME_ACT_SIZE_BANDS,
        sme_act_size_thresholds=fin.SME_ACT_SIZE_THRESHOLDS,
        suggested_sme_size_band=fin.classify_sme_size(engagement.sme_staff_headcount, engagement.sme_annual_turnover, engagement.sme_gross_assets),
        category_choices=fin.category_choices(),
        coa_accounts=coa_account_choices(),
        **(__import__("tb_setup").tab_context(engagement) if tab == "trial_balance" else {}),
        it_change_plan_risk_levels=IT_CHANGE_PLAN_RISK_LEVELS,
        category_label=fin.category_label,
        audit_areas=substantive_area_names,
        substantive_areas=substantive_areas_by_name,
        substantive_area_refs=substantive_area_refs,
        queries_by_section=queries_by_section,
        queries_by_area=queries_by_area,
        open_query_count=open_query_count,
        client_acceptance=client_acceptance,
        acceptance_cleared=acceptance_cleared,
        acceptance_decisions=CLIENT_ACCEPTANCE_DECISIONS,
        acceptance_checklist_responses=CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
        risk_categories=RISK_CATEGORIES,
        sanctions_screening_sources=SANCTIONS_SCREENING_SOURCES,
        sanctions_screening_results=SANCTIONS_SCREENING_RESULTS,
        sanctions_auto_sources=SANCTIONS_AUTO_SOURCES,
        sanctions_list_status=sanctions_list_status,
        can_manage_sanctions_lists=user_has_permission(current_user, "manage_sanctions_lists"),
        regulatory_notice_sources=REGULATORY_NOTICE_SOURCES,
        regulatory_notice_source_labels=REGULATORY_NOTICE_SOURCE_LABELS,
        regulatory_notice_counts=regulatory_notice_counts,
        client_key_people=client_key_people,
        finalisation_checklist=finalisation_checklist,
        finalisation_checklist_responses=CLIENT_ACCEPTANCE_CHECKLIST_RESPONSES,
        tickmarks=tickmarks,
        filing_index_sections=filing_index_sections,
        workpaper_sections=WORKPAPER_SECTIONS,
        workpaper_reference=workpaper_reference,
        filing_reference=filing_reference,
        effectively_reviewed=effectively_reviewed,
        workpaper_narratives=workpaper_narratives,
        workpaper_narrative_kinds=WORKPAPER_NARRATIVE_KINDS,
        filed_workpapers_by_kind=_filed_workpapers_by_kind(engagement_id),
        REVIEWER_ROLES=REVIEWER_ROLES,
        PARTNER_SIGNOFF_ROLES=PARTNER_SIGNOFF_ROLES,
        ppe_asset_classes=ppe_asset_classes,
        ppe_assets=ppe_assets,
        ppe_depreciation_methods=PPE_DEPRECIATION_METHODS,
        ppe_movement=ppe_movement,
        directors_statement=directors_statement,
        suggested_directors=suggested_directors,
        default_directors_statement_text=fin.default_directors_statement_text(engagement),
        audit_opinion=audit_opinion,
        default_opinion_paragraphs=default_opinion_paragraphs,
        audit_opinion_bases=AUDIT_OPINION_BASES,
        audit_opinion_basis_labels=AUDIT_OPINION_BASIS_LABELS,
        opinion_modifications=OPINION_MODIFICATIONS,
        opinion_modification_labels=OPINION_MODIFICATION_LABELS,
        income_tax_computation=income_tax_computation,
        income_tax_lines=income_tax_lines,
        income_tax_result=income_tax_result,
        income_tax_tb_current=income_tax_tb_current,
        income_tax_reconciliation=income_tax_reconciliation,
        profit_before_tax=profit_before_tax,
        income_tax_item_types=INCOME_TAX_ITEM_TYPES,
        income_tax_item_type_labels=INCOME_TAX_ITEM_TYPE_LABELS,
        deferred_tax_computation=deferred_tax_computation,
        ppe_accounting_nbv=ppe_accounting_nbv,
        deferred_tax_other_items=deferred_tax_other_items,
        deferred_tax_result=deferred_tax_result,
        deferred_tax_ppe=deferred_tax_ppe,
        deferred_tax_adj=deferred_tax_adj,
        deferred_tax_tb_net=deferred_tax_tb_net,
        deferred_tax_reconciliation=deferred_tax_reconciliation,
        tax_services=TAX_SERVICES,
        tax_service_labels=TAX_SERVICE_LABELS,
        tax_heads=TAX_HEADS,
        tax_entity_profile=tax_entity_profile,
        tax_entity_classifications=TAX_ENTITY_CLASSIFICATIONS,
        tax_registrations=tax_registrations,
        tax_registration_record_types=TAX_REGISTRATION_RECORD_TYPES,
        tax_deadlines=tax_deadlines,
        tax_analytical_review=tax_analytical_review,
        tax_risk_items=tax_risk_items,
        tax_positions=tax_positions,
        tax_position_classifications=TAX_POSITION_CLASSIFICATIONS,
        tax_position_approval_statuses=TAX_POSITION_APPROVAL_STATUSES,
        tax_return_records=tax_return_records,
        tax_return_statuses=TAX_RETURN_STATUSES,
        tax_information_requests=tax_information_requests,
        tax_info_request_statuses=TAX_INFO_REQUEST_STATUSES,
        tax_research_log=tax_research_log,
        legislative_updates_for_import=legislative_updates_for_import,
        tax_structuring_options=tax_structuring_options,
        tax_disputes=tax_disputes,
        tax_dispute_stages=TAX_DISPUTE_STAGES,
        tax_checklist_by_head=tax_checklist_by_head,
        tax_execution_areas_by_name=tax_execution_areas_by_name,
        current_penalty_interest_rates=current_penalty_interest_rates,
        penalty_interest_rate_types=PENALTY_INTEREST_RATE_TYPES,
        tax_penalty_calculations=tax_penalty_calculations,
        current_vat_rates=current_vat_rates,
        vat_invoices=vat_invoices,
        vat_import_batches=vat_import_batches,
        vat_invoice_directions=VAT_INVOICE_DIRECTIONS,
        vat_document_kinds=VAT_DOCUMENT_KINDS,
        vat_summary=vat_summary,
        vat_invoices_by_kind=vat_invoices_by_kind,
        engagement_tasks_sorted=engagement_tasks_sorted, ready_to_bill_count=ready_to_bill_count,
        vat_by_currency=vat_by_currency,
        vat_month_checks=vat_month_checks,
        vat_return_due=vat_return_due,
        vat_denial_grounds=VAT_DENIAL_GROUNDS,
        vat_denial_labels=VAT_DENIAL_LABELS,
        zimra_cases=sorted(engagement.zimra_cases, key=lambda c: (c.is_closed, -(c.id or 0))),
        vat_periods=vat_periods, vat_undated_docs=vat_undated_docs,
        vat_selected_key=vat_selected_key, vat_selected_period=vat_selected_period,
        vat_selected_docs=vat_selected_docs,
        vat_invoices_by_currency_kind=vat_invoices_by_currency_kind,
        vat_currencies=VAT_CURRENCIES,
        vat_default_currency=VAT_DEFAULT_CURRENCY,
        vat_invoice_direction_labels=VAT_INVOICE_DIRECTION_LABELS,
        vat_output_tax_total=vat_output_tax_total,
        vat_input_tax_total=vat_input_tax_total,
        vat_output_taxable_total=vat_output_taxable_total,
        vat_input_taxable_total=vat_input_taxable_total,
        vat_net_position=vat_net_position,
        tax_opinion_parts=TAX_OPINION_PARTS,
        tax_opinion_narratives=tax_opinion_narratives,
        tax_health_check_parts=TAX_HEALTH_CHECK_PARTS,
        tax_health_check_narratives=tax_health_check_narratives,
        accounting_services=ACCOUNTING_SERVICES,
        accounting_service_labels=ACCOUNTING_SERVICE_LABELS,
        gl_accounts=gl_accounts,
        gl_account_types=GL_ACCOUNT_TYPES,
        journal_entries=journal_entries,
        journal_entry_sources=JOURNAL_ENTRY_SOURCES,
        bank_reconciliations=bank_reconciliations,
        bank_reconciliation_item_types=BANK_RECONCILIATION_ITEM_TYPES,
        accounting_risk_items=accounting_risk_items,
        cost_centres=cost_centres,
        cost_centre_types=COST_CENTRE_TYPES,
        costing_records=costing_records,
        costing_methods=COSTING_METHODS,
        standard_cost_variances=standard_cost_variances,
        variance_types=VARIANCE_TYPES,
        cvp_analyses=cvp_analyses,
        budgets=budgets,
        budget_types=BUDGET_TYPES,
        kpi_metrics=kpi_metrics,
        kpi_categories=KPI_CATEGORIES,
        accounting_checklist_by_area=accounting_checklist_by_area,
        accounting_execution_areas_by_name=accounting_execution_areas_by_name,
        management_report_parts=MANAGEMENT_ACCOUNTS_REPORT_PARTS,
        management_report_narratives=management_report_narratives,
    )


# ---------- Checklist ----------

@engagements_bp.route("/<int:engagement_id>/checklist/add", methods=["POST"])
@login_required
def add_checklist_item(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    max_order = max([i.order for i in engagement.checklist_items], default=0)
    item = EngagementChecklistItem(
        engagement_id=engagement.id,
        section=request.form.get("section", "").strip(),
        item_text=request.form.get("item_text", "").strip(),
        order=max_order + 1,
    )
    db.session.add(item)
    db.session.commit()
    flash("Checklist item added.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/update", methods=["POST"])
@login_required
def update_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    item.status = request.form.get("status", item.status)
    item.notes = request.form.get("notes", item.notes)
    if item.status in ("Done", "N/A"):
        item.completed_by_id = current_user.id
        item.completed_at = datetime.utcnow()
    else:
        item.completed_by_id = None
        item.completed_at = None
        # Re-opening a previously completed item invalidates any earlier
        # review/partner sign-off - it needs to be looked at again once re-done.
        item.reviewed_by_id = None
        item.reviewed_at = None
        item.partner_signed_by_id = None
        item.partner_signed_at = None
    db.session.commit()
    flash("Checklist item updated.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/review", methods=["POST"])
@login_required
def review_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if item.status not in ("Done", "N/A"):
        flash("This item needs to be prepared (marked Done or N/A) before it can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    if item.completed_by_id == current_user.id:
        flash("You can't review your own work - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    item.reviewed_by_id = current_user.id
    item.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Checklist item marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/unreview", methods=["POST"])
@login_required
def unreview_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    item.reviewed_by_id = None
    item.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if item.status not in ("Done", "N/A"):
        flash("This item needs to be prepared (marked Done or N/A) before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    if item.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on work you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))
    item.partner_signed_by_id = current_user.id
    item.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    item.partner_signed_by_id = None
    item.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=item.engagement_id, tab="checklist"))


@engagements_bp.route("/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_checklist_item(item_id):
    item = EngagementChecklistItem.query.get_or_404(item_id)
    _ensure_engagement_access(item.engagement)
    engagement_id = item.engagement_id
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="checklist"))


# ---------- Multiple reviews on a line-item workpaper (see models.WorkpaperReview) ----------
#
# Generic across every reviewable_type in REVIEWABLE_TYPES - the user's own
# requested starting point is Substantive Procedures items and checklist
# items (both wired in below); a further workpaper type later only needs a
# new entry in REVIEWABLE_MODELS/_reviewable_engagement/_reviewable_redirect
# and a `reviews` property on that model, not a new table or routes.

REVIEWABLE_MODELS = {
    REVIEWABLE_TYPE_SUBSTANTIVE_ITEM: SubstantiveProcedureItem,
    REVIEWABLE_TYPE_CHECKLIST_ITEM: EngagementChecklistItem,
}


def _reviewable_engagement(reviewable_type, item):
    if reviewable_type == REVIEWABLE_TYPE_SUBSTANTIVE_ITEM:
        return item.area_record.engagement
    if reviewable_type == REVIEWABLE_TYPE_CHECKLIST_ITEM:
        return item.engagement
    abort(400)


def _reviewable_redirect(reviewable_type, item):
    engagement = _reviewable_engagement(reviewable_type, item)
    tab = "substantive" if reviewable_type == REVIEWABLE_TYPE_SUBSTANTIVE_ITEM else "checklist"
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab=tab))


@engagements_bp.route("/workpaper-reviews/<reviewable_type>/<int:reviewable_id>/add", methods=["POST"])
@login_required
def add_workpaper_review(reviewable_type, reviewable_id):
    """Adds one dated review entry to a line-item workpaper - purely
    additive, same "never gates anything" philosophy as Queries: it doesn't
    touch that item's own reviewed_by/partner_signed_by fields (where it
    has them), it just lets more than one reviewer each leave their own
    note. See models.WorkpaperReview."""
    if reviewable_type not in REVIEWABLE_TYPES:
        abort(404)
    model = REVIEWABLE_MODELS[reviewable_type]
    item = model.query.get_or_404(reviewable_id)
    engagement = _reviewable_engagement(reviewable_type, item)
    _ensure_engagement_access(engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    review = WorkpaperReview(
        reviewable_type=reviewable_type, reviewable_id=reviewable_id,
        reviewer_id=current_user.id, notes=request.form.get("notes", "").strip() or None,
    )
    db.session.add(review)
    db.session.commit()
    flash("Review added.", "success")
    return _reviewable_redirect(reviewable_type, item)


@engagements_bp.route("/workpaper-reviews/<int:review_id>/delete", methods=["POST"])
@login_required
def delete_workpaper_review(review_id):
    """A review entry can be removed by whoever left it, or by a Partner/
    Admin (the ultimate reviewer) tidying up someone else's mistake - not
    by an ordinary Supervisor removing another reviewer's entry."""
    review = WorkpaperReview.query.get_or_404(review_id)
    if review.reviewable_type not in REVIEWABLE_TYPES:
        abort(404)
    model = REVIEWABLE_MODELS[review.reviewable_type]
    item = model.query.get_or_404(review.reviewable_id)
    engagement = _reviewable_engagement(review.reviewable_type, item)
    _ensure_engagement_access(engagement)
    if review.reviewer_id != current_user.id and current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    reviewable_type = review.reviewable_type
    db.session.delete(review)
    db.session.commit()
    flash("Review removed.", "info")
    return _reviewable_redirect(reviewable_type, item)


# ---------- Understanding the Entity (system-based, structured prompts) ----------

@engagements_bp.route("/<int:engagement_id>/entity-understanding/save", methods=["POST"])
@login_required
def save_entity_understanding(engagement_id):
    engagement = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(engagement)
    record = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = EntityUnderstanding(engagement_id=engagement_id)
        db.session.add(record)

    for field, _, _ in ENTITY_UNDERSTANDING_FIELDS:
        setattr(record, field, request.form.get(field, "").strip())

    record.completed_by_id = current_user.id
    record.completed_at = datetime.utcnow()
    # Changing the write-up invalidates any earlier review/partner sign-off.
    record.reviewed_by_id = None
    record.reviewed_at = None
    record.partner_signed_by_id = None
    record.partner_signed_at = None

    db.session.commit()
    flash("Understanding of the entity saved.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/review", methods=["POST"])
@login_required
def review_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    if not record.is_complete:
        flash("All sections need to be filled in before this can be reviewed.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    if record.completed_by_id == current_user.id:
        flash("You can't review your own write-up - ask another supervisor/partner to review it.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    record.reviewed_by_id = current_user.id
    record.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Marked as reviewed.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/unreview", methods=["POST"])
@login_required
def unreview_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in REVIEWER_ROLES:
        abort(403)
    record.reviewed_by_id = None
    record.reviewed_at = None
    db.session.commit()
    flash("Review sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/partner-sign", methods=["POST"])
@login_required
def partner_sign_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    if not record.is_complete:
        flash("All sections need to be filled in before the partner can sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    if record.completed_by_id == current_user.id:
        flash("You can't give the partner sign-off on a write-up you prepared yourself - ask another partner to sign off.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))
    record.partner_signed_by_id = current_user.id
    record.partner_signed_at = datetime.utcnow()
    db.session.commit()
    flash("Partner sign-off recorded.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


@engagements_bp.route("/entity-understanding/<int:record_id>/partner-unsign", methods=["POST"])
@login_required
def partner_unsign_entity_understanding(record_id):
    record = EntityUnderstanding.query.get_or_404(record_id)
    _ensure_engagement_access(record.engagement)
    if current_user.role not in PARTNER_SIGNOFF_ROLES:
        abort(403)
    record.partner_signed_by_id = None
    record.partner_signed_at = None
    db.session.commit()
    flash("Partner sign-off removed.", "info")
    return redirect(url_for("engagements.view_engagement", engagement_id=record.engagement_id, tab="entity"))


def _get_or_create_entity_understanding(engagement_id):
    record = EntityUnderstanding.query.filter_by(engagement_id=engagement_id).first()
    if not record:
        record = EntityUnderstanding(engagement_id=engagement_id)
        db.session.add(record)
        db.session.flush()
    return record
