import os
import click
from datetime import datetime
from flask import Flask, redirect, url_for
from flask_login import current_user
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from config import Config, INSTANCE_DIR
from extensions import db, login_manager, socketio
from models import User, DocumentTemplate, Permission, FilingIndexSection, StatutoryDeadline, QPDInstalmentRate, StandardChartOfAccounts


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    # Only applies to SQLite connections (harmless no-op otherwise). WAL mode
    # lets reads and writes happen concurrently instead of locking the whole
    # database file, and a busy_timeout makes a brief write collision wait and
    # retry instead of failing outright - both matter once this is hosted
    # online with more than one person using it at the same time (or more
    # than one gunicorn worker process), not just one office PC.
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def _backfill_task_categories():
    """One-time (idempotent) fill for tasks that pre-date task categories:
    an engagement task takes the category implied by its engagement's type, a
    general to-do takes its engagement's category if it references one and
    "Other" otherwise. Tax Services tasks are then forced to High priority
    (the firm's rule - penalties apply). Only touches rows with no category,
    so it never overrides a category someone has chosen; the Tax Services
    priority rule is re-applied on every save by effective_task_priority."""
    from models import EngagementTask, PersonalTask, resolve_task_category, effective_task_priority
    changed = False
    for model in (EngagementTask, PersonalTask):
        for task in model.query.filter(model.category.is_(None)).all():
            engagement = task.engagement
            task.category = resolve_task_category(None, engagement, fallback="Other")
            task.priority = effective_task_priority(task.category, task.priority)
            changed = True
    if changed:
        db.session.commit()


def _add_missing_columns():
    """Lightweight auto-migration for SQLite: db.create_all() only creates
    brand-new tables, it never adds a column to a table that already exists.
    So when a code update adds a new column to an existing model (like
    Document.reference below), an already-running install's database needs a
    one-time ALTER TABLE to pick it up. This runs on every startup, checks
    what's actually there first, and only adds what's missing - so it's a
    no-op on a fresh install (the column is already in db.create_all()'s
    table definition) and safe to run repeatedly.
    """
    if db.engine.dialect.name != "sqlite":
        return  # only SQLite is supported/expected; skip silently otherwise
    partner_signoff_cols = [("partner_signed_by_id", "INTEGER"), ("partner_signed_at", "DATETIME")]
    additions = {
        "document": [
            ("reference", "VARCHAR(100)"), ("substantive_area_id", "INTEGER"), ("filing_index_id", "INTEGER"),
            ("is_generated", "BOOLEAN DEFAULT 0"), ("workpaper_kind", "VARCHAR(40)"),
            ("is_current_version", "BOOLEAN DEFAULT 1"),
            ("reviewed_by_id", "INTEGER"), ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
        "client": [("company_number", "VARCHAR(80)"), ("logo_filename", "VARCHAR(255)")],
        # last_seen_at - see models.User.is_online and this file's
        # _update_last_seen - drives the Messages "who's in the app" online
        # indicator.
        "user": [("last_seen_at", "DATETIME")],
        "message_recipient": [("recalled_at", "DATETIME")],
        "engagement": [
            ("subdivision", "VARCHAR(50)"), ("acceptance_required", "BOOLEAN DEFAULT 0"),
            ("reporting_framework", "VARCHAR(20) DEFAULT 'full_ifrs'"),
            ("cash_flow_method", "VARCHAR(10) DEFAULT 'indirect'"),
            ("pie_listed", "BOOLEAN DEFAULT 0"), ("pie_financial_institution", "BOOLEAN DEFAULT 0"),
            ("pie_insurer", "BOOLEAN DEFAULT 0"), ("pie_asset_manager", "BOOLEAN DEFAULT 0"),
            ("pie_pension_fund", "BOOLEAN DEFAULT 0"), ("pie_medical_aid", "BOOLEAN DEFAULT 0"),
            ("pie_debt_equity_issuer", "BOOLEAN DEFAULT 0"),
            ("sme_sector", "VARCHAR(50)"), ("sme_size_band", "VARCHAR(10)"),
            ("sme_annual_turnover", "FLOAT"), ("sme_gross_assets", "FLOAT"), ("sme_staff_headcount", "INTEGER"),
            ("secretarial_activities", "TEXT"),
            ("tax_services", "TEXT"),
            ("accounting_services", "TEXT"),
        ],
        "risk_item": [
            ("module", "VARCHAR(20)"),
            ("created_by_id", "INTEGER"),
            ("created_at", "DATETIME"),
        ],
        "analytical_review_line": [("source", "VARCHAR(10) DEFAULT 'manual'")],
        "engagement_checklist_item": [
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
        "substantive_procedure_item": [
            ("tickmark_id", "INTEGER"),
            ("trigger_event", "VARCHAR(200)"), ("responsible_role", "VARCHAR(100)"), ("target_output", "VARCHAR(200)"),
            ("procedure_kind", "VARCHAR(20)"),
        ],
        "engagement_task": [
            ("completed_by_id", "INTEGER"),
            ("completed_at", "DATETIME"),
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
            # category - see models.TASK_CATEGORIES; backfilled from the
            # engagement type by _backfill_task_categories on startup.
            ("category", "VARCHAR(60)"),
        ] + partner_signoff_cols,
        "personal_task": [("category", "VARCHAR(60)")],
        # doc_type/related_reference/customs_duty - see models.VAT_DOC_TYPES:
        # credit notes, Bills of Entry and export sales. Existing rows
        # default to a plain 'Invoice', exactly what they were.
        "vat_invoice": [("doc_type", "VARCHAR(20) DEFAULT 'Invoice'"), ("related_reference", "VARCHAR(100)"), ("customs_duty", "FLOAT")],
        "vat_import_batch": [("doc_type", "VARCHAR(20) DEFAULT 'Invoice'")],
        "risk_assessment": list(partner_signoff_cols) + [
            ("q_fraud_incentive", "INTEGER"),
            ("q_fraud_opportunity", "INTEGER"),
            ("q_fraud_rationalization", "INTEGER"),
            ("q_fraud_override", "INTEGER"),
            ("q_fraud_intel", "INTEGER"),
            ("q_scheme_revenue_gl", "INTEGER"),
            ("q_scheme_asset_misappropriation", "INTEGER"),
            ("q_scheme_procurement_vendor", "INTEGER"),
            ("q_scheme_payroll_expenses", "INTEGER"),
            ("q_scheme_significance", "INTEGER"),
            ("q_threat_exposure", "INTEGER"),
            ("q_control_maturity", "INTEGER"),
            ("q_environment_complexity", "INTEGER"),
            ("q_third_party_cloud", "INTEGER"),
            ("q_aml_customer_channel", "INTEGER"),
            ("q_data_sensitivity", "INTEGER"),
            ("q_continuity_dependency", "INTEGER"),
            ("q_regulatory_legal", "INTEGER"),
            ("q_reputational", "INTEGER"),
            ("q_overall_significance", "INTEGER"),
            ("q_filing_compliance_history", "INTEGER"),
            ("q_governance_procedural_rigor", "INTEGER"),
            ("q_ownership_capital_complexity", "INTEGER"),
            ("q_transactional_activity", "INTEGER"),
            ("q_registry_standing", "INTEGER"),
            ("q_penalty_exposure", "INTEGER"),
            ("q_governance_invalidity", "INTEGER"),
            ("q_solvency_director_liability", "INTEGER"),
            ("q_encumbrance_control_risk", "INTEGER"),
        ],
        "materiality_calculation": [
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
            ("quantitative_important", "BOOLEAN DEFAULT 1"),
        ] + partner_signoff_cols,
        "entity_understanding": list(partner_signoff_cols),
        "entity_understanding_checklist_item": [("response_options", "TEXT")],
        "analytical_review": list(partner_signoff_cols) + [
            ("not_necessary", "BOOLEAN DEFAULT 0"), ("not_necessary_reason", "TEXT"),
        ],
        "trial_balance": list(partner_signoff_cols) + [
            ("not_applicable", "BOOLEAN DEFAULT 0"), ("not_applicable_reason", "TEXT"),
            ("marked_na_by_id", "INTEGER"), ("marked_na_at", "DATETIME"),
        ],
        "financial_statements": list(partner_signoff_cols) + [
            ("notes_to_financial_statements", "TEXT"),
            ("related_party_note", "TEXT"), ("commitments_note", "TEXT"), ("subsequent_events_note", "TEXT"),
        ],
        "client_acceptance": [
            ("risk_conflicts_score", "INTEGER"),
            ("risk_security_score", "INTEGER"),
            ("risk_integrity_edd_score", "INTEGER"),
            ("risk_evidence_legal_score", "INTEGER"),
            ("risk_scope_capabilities_score", "INTEGER"),
            ("risk_scoring_notes", "TEXT"),
            ("conflict_threat_clear", "BOOLEAN"),
            ("conflict_threat_notes", "TEXT"),
            ("edd_completed", "BOOLEAN"),
            ("edd_notes", "TEXT"),
            ("legal_evidence_satisfactory", "BOOLEAN"),
            ("legal_evidence_notes", "TEXT"),
            ("competence_scope_confirmed", "BOOLEAN"),
            ("competence_scope_notes", "TEXT"),
            ("kyc_ubo_satisfactory", "BOOLEAN"),
            ("kyc_ubo_notes", "TEXT"),
            ("authority_background_satisfactory", "BOOLEAN"),
            ("authority_background_notes", "TEXT"),
            ("nature_of_business_risk_acceptable", "BOOLEAN"),
            ("nature_of_business_risk_notes", "TEXT"),
            ("conflict_dispute_clear", "BOOLEAN"),
            ("conflict_dispute_notes", "TEXT"),
            ("operational_capacity_confirmed", "BOOLEAN"),
            ("operational_capacity_notes", "TEXT"),
            ("partner_authorized_continue_by_id", "INTEGER"),
            ("partner_authorized_continue_at", "DATETIME"),
            ("partner_authorized_continue_notes", "TEXT"),
        ],
        "engagement_query": [
            ("resolved_via_partner_override", "BOOLEAN DEFAULT 0"),
            ("partner_override_notes", "TEXT"),
        ],
        "sanctions_screening": [
            ("un_auto_notes", "TEXT"),
            ("ofac_auto_notes", "TEXT"),
            ("eu_auto_notes", "TEXT"),
            ("rbz_auto_notes", "TEXT"),
            ("fiu_auto_notes", "TEXT"),
            ("auto_screened_at", "DATETIME"),
            ("auto_screened_by_id", "INTEGER"),
        ],
        "client_key_person": [
            ("number_of_shares", "VARCHAR(50)"),
            ("shareholding_percentage", "VARCHAR(50)"),
            ("id_number", "VARCHAR(100)"),
            ("nationality", "VARCHAR(100)"),
            ("address", "VARCHAR(300)"),
            ("needs_detail_review", "BOOLEAN DEFAULT 0"),
        ],
        # Legislative Update Control (Module 8) could previously only log a
        # text entry - these let it also FILE the actual instrument (an
        # Act/Notice/SI, as a PDF or image) and get an AI summary, confirmed
        # practice-area tags, and client-relevance tags. See models.
        # LegislativeUpdate's docstring.
        "legislative_update": [
            ("instrument_type", "VARCHAR(10)"),
            ("gazette_date", "DATE"),
            ("areas_json", "TEXT"),
            ("original_filename", "VARCHAR(300)"),
            ("stored_filename", "VARCHAR(300)"),
            ("extracted_text", "TEXT"),
            ("extraction_status", "VARCHAR(20)"),
            ("page_count", "INTEGER"),
            ("ai_summary", "TEXT"),
            ("ai_key_changes_json", "TEXT"),
            ("ai_suggested_areas_json", "TEXT"),
            ("ai_status", "VARCHAR(20)"),
            ("ai_error", "TEXT"),
            ("ai_processed_at", "DATETIME"),
            # Case Law filing (instrument_type == "Case") - a court judgment
            # rather than a piece of legislation. See models.LegislativeUpdate
            # and models.CASE_AUTHORITY_STATUSES for why a foreign judgment
            # (especially South African) is assessed for persuasive value
            # rather than dismissed for not being Zimbabwean law.
            ("case_citation", "VARCHAR(300)"),
            ("case_court", "VARCHAR(200)"),
            ("case_jurisdiction", "VARCHAR(100)"),
            ("ai_case_authority_status", "VARCHAR(20)"),
            ("ai_case_authority_reasoning", "TEXT"),
            ("case_authority_status", "VARCHAR(20)"),
            # News filing (instrument_type == "News") - filed by pasting a
            # URL rather than uploading a file; see models.LegislativeUpdate's
            # docstring and the has_source property.
            ("article_url", "VARCHAR(500)"),
        ],
        # The Policies & Procedures library ("Firm Library") upgraded from a
        # static depository to an AI-powered research hub - these let a
        # filed policy/procedure get its text extracted (file_text_
        # extraction.py) and AI-summarised (policy_summary.py) the same way
        # a filed Act/Notice/SI already is. See models.PolicyDocument's
        # docstring.
        "policy_document": [
            ("extracted_text", "TEXT"),
            ("extraction_status", "VARCHAR(20)"),
            ("page_count", "INTEGER"),
            ("ai_summary", "TEXT"),
            ("ai_key_points_json", "TEXT"),
            ("ai_suggested_category", "VARCHAR(50)"),
            ("ai_status", "VARCHAR(20)"),
            ("ai_error", "TEXT"),
            ("ai_processed_at", "DATETIME"),
        ],
        # status replaces the original is_done boolean (see
        # models.PersonalSubtask) - a subtask is now a "pure task" carrying
        # the same TASK_STATUSES as everything else, independent of its
        # parent to-do's own status. The old is_done/completed_at columns
        # are left in place harmlessly on any install that already created
        # this table (SQLite auto-migration only ever adds columns).
        "personal_subtask": [("status", "VARCHAR(20) DEFAULT 'To Do'")],
        # frequency - see models.PayrollTaxBand's docstring: ZIMRA publishes a
        # genuinely different PAYE band table per pay frequency, not just the
        # Monthly one divided down, so bands are now kept per-frequency
        # rather than as one global list applied to every employee. Existing
        # installs' pre-existing band rows (all entered before this column
        # existed) default to "Monthly" here, which is correct: that was the
        # only frequency the old single global list could ever have meant.
        "payroll_tax_band": [("frequency", "VARCHAR(20) DEFAULT 'Monthly'")],
        # exempt_income_total/paye_before_credits/tax_credits_total - see
        # models.Payslip's docstring and PAYSLIP_ITEM_CATEGORIES: PAYE
        # computation now supports "Exempt Income" and "Tax Credit" line
        # items (ZIMRA's own published method has both steps) - existing
        # payslips default to 0 for all three, exactly as if no such items
        # existed on them yet, which is correct (nothing retroactively
        # changes their already-stored PAYE/net pay figures).
        "payslip": [
            ("exempt_income_total", "FLOAT DEFAULT 0"),
            ("paye_before_credits", "FLOAT DEFAULT 0"),
            ("tax_credits_total", "FLOAT DEFAULT 0"),
            # apwcs - see models.Payslip's docstring and PAYROLL_TAX_CAVEAT:
            # Accident Prevention and Workers' Compensation Scheme, 1.25% of
            # Basic Salary payable to NSSA by the employer, never affecting
            # the employee's own payslip. Existing payslips default to 0,
            # same reasoning as the three fields above - nothing
            # retroactively changes an already-stored payslip; a reviewer
            # can backfill it manually (see update_payslip) or by hitting
            # Recalculate.
            ("apwcs", "FLOAT DEFAULT 0"),
        ],
        # apwcs_pct - see models.PayrollTaxSettings's docstring and
        # PAYROLL_TAX_CAVEAT: unlike the payslip-level column above, this
        # DEFAULT backfills the firm's single existing settings row
        # immediately (SQLite applies a literal ALTER TABLE ... DEFAULT to
        # existing rows, not just new ones) - so an existing install picks
        # up the firm-confirmed 1.25% rate automatically, exactly like a fresh
        # install's column default would.
        "payroll_tax_settings": [("apwcs_pct", "FLOAT DEFAULT 1.25")],
        # nssa_applicable - see PayslipItem's docstring in models.py: lets an
        # Allowance/Exempt Income item (e.g. the EMPLOYER's own medical aid
        # contribution) be excluded from NSSA Insurable Earnings specifically,
        # separate from whether it counts toward gross pay or PAYE taxable
        # income. Existing items default to True (1) - unchanged NSSA
        # behaviour for every item already on a payslip - until a firm
        # explicitly unticks it for a specific line like Medical Aid.
        "payslip_item": [("nssa_applicable", "BOOLEAN DEFAULT 1")],
        # leave_type_id/leave_days - see models.LeaveType/LeaveBalance: a
        # Time Sheet entry can now record leave taken (Annual/Sick/Special/
        # Maternity, etc.) instead of hours worked. Existing entries default
        # to NULL for both, i.e. an ordinary hours-worked entry, exactly as
        # before this feature existed.
        "time_entry": [("leave_type_id", "INTEGER"), ("leave_days", "FLOAT")],
        # reference_number/source_company_document_id - see models.
        # PermanentFileDocument's docstring: a document filed under a
        # Permanent File P-code now gets its own numbered sub-reference
        # (P1001, P1002, ...) instead of every document under the same code
        # sharing its bare "P1000", and a document auto-filed here from a
        # Company Document upload records where it came from. Existing rows
        # default to NULL for both - unchanged (bare P-code, "manually
        # filed") until a preparer renumbers them or re-uploads via Company
        # Documents.
        "permanent_file_document": [("reference_number", "INTEGER"), ("source_company_document_id", "INTEGER")],
        # estimated_annual_revenue/revenue_usd_pct - see models.QPDEstimate's
        # docstring: the dual-currency (USD/ZWG) split ZIMRA requires once a
        # client earns revenue in more than one currency. Existing estimates
        # default to NULL for both (no currency split shown) until a
        # preparer enters the actual USD % for that estimate.
        "qpd_estimate": [("estimated_annual_revenue", "FLOAT"), ("revenue_usd_pct", "FLOAT")],
        # usd_pct_snapshot/fx_rate_snapshot/paid_amount_usd/paid_amount_zwg -
        # see models.QPDInstalmentRecord's docstring: usd_pct_snapshot
        # freezes the USD/ZWG split in effect when an instalment was
        # computed; fx_rate_snapshot additionally freezes the firm's logged
        # USD:ZWG FxRate as of that date, so the ZWG-denominated portion can
        # be converted into an actual ZWG currency amount; paid_amount_usd/
        # paid_amount_zwg optionally record the actual currency breakdown of
        # what was remitted. Existing instalments default to NULL for all
        # four (no split/conversion/breakdown shown) until recomputed or
        # re-paid.
        "qpd_instalment_record": [
            ("usd_pct_snapshot", "FLOAT"), ("fx_rate_snapshot", "FLOAT"),
            ("paid_amount_usd", "FLOAT"), ("paid_amount_zwg", "FLOAT"),
        ],
        # coa_account_number - see models.StandardChartOfAccounts/COAMapping's
        # docstrings: an optional link from a remembered/trial-balance
        # account mapping to one of the firm's own Standard Chart of
        # Accounts entries, alongside the existing bare fs_category.
        # Existing rows default to NULL (no standard account chosen yet)
        # until a preparer picks one.
        # original_rate_zwl - see models.FxRate: remembers the pre-conversion
        # ZWL figure on a rate converted to ZWG, so it can't be converted
        # twice and can be undone. NULL on everything entered in ZWG.
        # estimated_annual_salaries - see models.QPDEstimate: the Salaries &
        # wages line of the return's expenses split; NULL until entered.
        "qpd_estimate": [("estimated_annual_salaries", "FLOAT")],
        "fx_rate": [("original_rate_zwl", "FLOAT")],
        "fx_average_rate": [("original_rate_zwl", "FLOAT")],
        "coa_mapping": [("coa_account_number", "VARCHAR(20)")],
        "trial_balance_line": [("coa_account_number", "VARCHAR(20)")],
        "qpd_trial_balance_line": [("coa_account_number", "VARCHAR(20)")],
    }
    with db.engine.connect() as conn:
        for table, columns in additions.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col_name, col_type in columns:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                    conn.commit()


def _fix_forensic_template_type():
    """One-time data fix, safe to run on every startup: the "Forensic Audit /
    Investigation" checklist template was originally seeded tagged as type
    "Assurance" (there was no dedicated type for investigative work yet).
    Now that "Investigative Engagement" is its own Engagement Type, retag
    that existing template row to match - seeding alone can't do this,
    since run_seed() only ever creates a template that doesn't already
    exist by name, it never updates one that's already there.
    """
    from models import ChecklistTemplate
    template = ChecklistTemplate.query.filter_by(name="Forensic Audit / Investigation").first()
    if template and template.type != "Investigative Engagement":
        template.type = "Investigative Engagement"
        db.session.commit()


# The Forensic Audit Client Acceptance Questionnaire's four sections were
# renamed after some engagements had already had it seeded (Independence /
# Regulatory / AML / Engagement letter / Competence -> Conflict of Interest
# & Threat Assessment / Enhanced Due Diligence (EDD) / Legal Framework &
# Evidence Control / Competence & Scope Realism), and it was later expanded
# from 3 to 6 questions per section. seed_acceptance_checklist() only ever
# seeds a checklist that's still empty, so anyone who'd already clicked
# "Add the firm's default checklist" before either change would otherwise
# be stuck: their already-seeded items carry the OLD section label, which
# no longer matches any of the four current headings, so those items
# silently fall into "Other checklist items" and every section shows "No
# checklist questions yet" - with the seed button hidden too, since the
# checklist isn't empty. This exact (old section, old wording) map lets the
# fix below relabel only genuinely-drifted items (never a custom item a
# team member typed in themselves) and then top up any of the current
# questions that are still missing, without disturbing responses/comments
# already recorded on anything.
_OLD_FORENSIC_CHECKLIST_SECTION_RENAMES = {
    ("Independence", "Have we screened all suspects, target entities, key witnesses, and related parties against our firm's active and past client database?"): "Conflict of Interest & Threat Assessment",
    ("Independence", "Have we previously provided any services (like bookkeeping or standard audits) to this client or target that could create a self-review or advocacy threat in court?"): "Conflict of Interest & Threat Assessment",
    ("Independence", "Does this investigation involve high-risk individuals, corporate retaliation, or hostile environments that require specialised physical or cybersecurity measures for our staff?"): "Conflict of Interest & Threat Assessment",
    ("Regulatory / AML", "Have we fully verified the identity of the engaging entity and its directors through standard KYC and AML protocols?"): "Enhanced Due Diligence (EDD)",
    ("Regulatory / AML", "Have we identified the Ultimate Beneficial Owners (UBOs) of both the client and the target to rule out hidden conflicts?"): "Enhanced Due Diligence (EDD)",
    ("Regulatory / AML", "Do background checks in court registries, regulatory databases, and media reports reveal a history of bad faith, fraud, or vexatious litigation by any key player?"): "Enhanced Due Diligence (EDD)",
    ("Engagement letter", "Does the client have the absolute legal authority to grant us access to the target's emails, personal devices, and financial records without breaching privacy laws (e.g., GDPR)?"): "Legal Framework & Evidence Control",
    ("Engagement letter", "Has the client or a third party already altered, deleted, or mismanaged the data, potentially damaging its admissibility in court?"): "Legal Framework & Evidence Control",
    ("Engagement letter", "Should we be retained directly by the client, or hired through their external legal counsel to shield our work under attorney-client privilege?"): "Legal Framework & Evidence Control",
    ("Competence", "Do we have available Certified Fraud Examiners (CFEs), digital forensics specialists, or industry experts required for this specific type of fraud?"): "Competence & Scope Realism",
    ("Competence", "Is the scope clearly defined (e.g., quantifying an insurance loss, tracing stolen assets, or preparing for criminal prosecution), or is the client asking for a vague \"fishing expedition\"?"): "Competence & Scope Realism",
    ("Competence", "Does the client understand that building legally sound evidence takes time, and are they willing to pay an upfront retainer to mitigate our non-payment risk?"): "Competence & Scope Realism",
}


def _fix_forensic_checklist_items():
    """One-time+idempotent startup fix - see the comment above
    _OLD_FORENSIC_CHECKLIST_SECTION_RENAMES for why this is needed. Only
    touches Client Acceptance checklists on engagements of type
    "Investigative Engagement", and only ever acts on items whose (section,
    exact wording) matches the old forensic question set or the current
    one - never a custom item a team member typed in themselves, and never
    a checklist that hasn't been started at all (an empty one is left for
    the normal "Add the firm's default checklist" button).

    The old question set's wording was also reworded (not just moved to a
    new section) when it was expanded from 3 to 6 questions per section, so
    this doesn't just relabel an old item's section and leave its old
    wording sitting there duplicating the fresh canonical question - it
    upgrades each drifted item's own text in place (by its position within
    its section, best-effort - good enough since these are advisory
    prompts, not identifiers) so any response/comment already recorded on
    it carries forward onto the closest current question, and only deletes
    it outright if that would collide with a canonical question already on
    the checklist. Whatever's still missing afterwards is added fresh.
    """
    from models import ClientAcceptance, ClientAcceptanceChecklistItem, FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS, Engagement

    investigative_ids = [e.id for e in Engagement.query.filter_by(type="Investigative Engagement").with_entities(Engagement.id).all()]
    if not investigative_ids:
        return

    canonical_by_section = {}
    for section, text_ in FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS:
        canonical_by_section.setdefault(section, []).append(text_)
    canonical_texts = {text_ for _section, text_ in FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS}

    changed = False
    records = ClientAcceptance.query.filter(ClientAcceptance.engagement_id.in_(investigative_ids)).all()
    for record in records:
        current_items = list(record.checklist_items)  # already ordered by .order
        if not current_items:
            continue  # never seeded - leave the normal "Add default checklist" button in place

        drifted = [i for i in current_items if (i.section, i.item_text) in _OLD_FORENSIC_CHECKLIST_SECTION_RENAMES]
        if not drifted and not any(i.item_text in canonical_texts for i in current_items):
            continue  # doesn't look like it was seeded from our list - don't touch it

        live_texts = {i.item_text for i in current_items}

        if drifted:
            by_new_section = {}
            for old_item in drifted:
                new_section = _OLD_FORENSIC_CHECKLIST_SECTION_RENAMES[(old_item.section, old_item.item_text)]
                by_new_section.setdefault(new_section, []).append(old_item)

            for new_section, old_items_in_section in by_new_section.items():
                canonical_list = canonical_by_section.get(new_section, [])
                for position, old_item in enumerate(old_items_in_section):
                    target_text = canonical_list[position] if position < len(canonical_list) else None
                    if target_text and target_text not in live_texts:
                        old_item.section = new_section
                        old_item.item_text = target_text
                        live_texts.add(target_text)
                    else:
                        current_items.remove(old_item)
                        db.session.delete(old_item)
                    changed = True

        live_texts = {i.item_text for i in current_items}
        max_order = max((i.order for i in current_items), default=0)
        for section, text_ in FORENSIC_ACCEPTANCE_CHECKLIST_ITEMS:
            if text_ in live_texts:
                continue
            max_order += 1
            db.session.add(ClientAcceptanceChecklistItem(
                client_acceptance_id=record.id,
                section=section,
                item_text=text_,
                order=max_order,
            ))
            live_texts.add(text_)
            changed = True

    if changed:
        db.session.commit()


def create_app():
    app = Flask(
        __name__,
        template_folder=Config.TEMPLATE_FOLDER,
        static_folder=Config.STATIC_FOLDER,
    )
    app.config.from_object(Config)

    os.makedirs(INSTANCE_DIR, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    socketio.init_app(app)

    from auth import auth_bp
    from clients import clients_bp
    from engagements import engagements_bp
    from users import users_bp
    from doc_templates import doc_templates_bp
    from hr import hr_bp
    from messages import messages_bp
    import calls  # noqa: F401 - registers the @socketio.on(...) handlers as a side effect
    from calls import calls_bp
    from acceptance import acceptance_bp
    from invoicing import invoicing_bp
    from regulatory_notices import regulatory_notices_bp
    from company_documents import company_documents_bp
    from payroll import payroll_bp
    from tickmarks import tickmarks_bp
    from filing_index import filing_index_bp
    from permanent_file import permanent_file_bp
    from filing_archive import filing_archive_bp
    from tax import tax_bp
    from accounting import accounting_bp
    from qpd import qpd_bp, qpd_dashboard_bp
    from standard_coa import standard_coa_bp
    from whistleblower import wb_bp, wb_public_bp
    from training import training_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(engagements_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(doc_templates_bp)
    app.register_blueprint(hr_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(calls_bp)
    app.register_blueprint(acceptance_bp)
    app.register_blueprint(invoicing_bp)
    app.register_blueprint(regulatory_notices_bp)
    app.register_blueprint(company_documents_bp)
    app.register_blueprint(payroll_bp)
    app.register_blueprint(tickmarks_bp)
    app.register_blueprint(filing_index_bp)
    app.register_blueprint(permanent_file_bp)
    app.register_blueprint(filing_archive_bp)
    app.register_blueprint(tax_bp)
    app.register_blueprint(accounting_bp)
    app.register_blueprint(qpd_bp)
    app.register_blueprint(qpd_dashboard_bp)
    app.register_blueprint(standard_coa_bp)
    app.register_blueprint(wb_bp)
    app.register_blueprint(wb_public_bp)
    app.register_blueprint(training_bp)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("engagements.dashboard"))
        return redirect(url_for("auth.login"))

    @app.before_request
    def _update_last_seen():
        """Lightweight presence tracking behind the Messages "who's online"
        indicator (see models.User.last_seen_at/is_online) - stamps the
        current user's last_seen_at on any authenticated request. Throttled
        to at most once a minute per user so an active session doesn't write
        to the database on every single page load/asset request; the
        ONLINE_THRESHOLD_MINUTES window in models.py is wider than this
        throttle, so it never makes someone look offline sooner than that
        window just because of the throttle."""
        if current_user.is_authenticated:
            now = datetime.utcnow()
            if not current_user.last_seen_at or (now - current_user.last_seen_at).total_seconds() > 60:
                current_user.last_seen_at = now
                db.session.commit()

    @app.context_processor
    def inject_globals():
        from models import user_has_permission, MessageRecipient, EngagementTask, PersonalTask, CheckInRecord
        unread = (
            MessageRecipient.query.filter_by(user_id=current_user.id, read_at=None).count()
            if current_user.is_authenticated
            else 0
        )
        open_tasks = 0
        open_check_in = None
        if current_user.is_authenticated:
            open_tasks = (
                EngagementTask.query.filter_by(assigned_to_id=current_user.id).filter(EngagementTask.status != "Done").count()
                + PersonalTask.query.filter_by(assigned_to_id=current_user.id).filter(PersonalTask.status != "Done").count()
            )
            # Powers the Check In / Check Out button in the top bar (see
            # hr.check_in/check_out) - shown on every page, not just the Time
            # Sheets page, so staff can clock in/out from wherever they are.
            open_check_in = CheckInRecord.query.filter_by(user_id=current_user.id, check_out_at=None).first()
        return {
            "firm_name": "Neverlank Chartered Accountants",
            "user_has_permission": user_has_permission,
            "unread_message_count": unread,
            "my_open_task_count": open_tasks,
            "open_check_in": open_check_in,
        }

    with app.app_context():
        db.create_all()
        _add_missing_columns()
        _backfill_task_categories()
        # First-run convenience: seed an admin login + starter checklist
        # templates automatically so a freshly-installed copy works with zero
        # command-line steps.
        if User.query.count() == 0:
            from seed import run_seed
            run_seed()
        elif DocumentTemplate.query.count() == 0:
            # Existing installs updated to this version won't have any
            # Document Template rows yet (new feature) - populate them from
            # the bundled originals automatically on next launch, with zero
            # steps needed from the user. Safe/idempotent either way.
            from seed import seed_document_templates
            seed_document_templates()
        # Configurable role permissions: seed_permissions() only ever adds a
        # (role, key) row that doesn't already exist, so it's safe/cheap to
        # call on every startup rather than gating it on the table being
        # empty - that's what picks up a later update's new permission keys
        # (e.g. manage_regulatory_notices, manage_sanctions_lists) on an
        # existing install without a manual migration step. A no-op on a
        # brand-new install, since run_seed() above already seeds every
        # current key.
        from seed import seed_permissions
        seed_permissions()
        # Filing Index (FilingIndexSection): another new-feature table that
        # an existing install won't have any rows in yet - seed it the same
        # way as Document Templates above, but as its own independent check
        # (not part of the if/elif chain above it) so it still runs even
        # when Document Templates was already seeded long ago.
        if FilingIndexSection.query.count() == 0:
            from seed import seed_filing_index
            seed_filing_index()
        # Statutory Deadlines (StatutoryDeadline): another new-feature table,
        # seeded the same way as Filing Index above - pre-load the current
        # year's 4 QPD dates once, as an editable/deletable starting point,
        # never re-seeded afterwards (see seed_statutory_deadlines's own
        # docstring for why).
        if StatutoryDeadline.query.count() == 0:
            from seed import seed_statutory_deadlines
            seed_statutory_deadlines()
        # QPD Instalment Rates (QPDInstalmentRate): the $ split/dates behind
        # the client-level QPD (provisional income tax) estimator - seeded
        # the same way as Statutory Deadlines above, once, as an editable
        # starting point (see seed_qpd_instalment_rates's own docstring).
        if QPDInstalmentRate.query.count() == 0:
            from seed import seed_qpd_instalment_rates
            seed_qpd_instalment_rates()
        # Standard Chart of Accounts (StandardChartOfAccounts): a firm-wide numbered
        # account list ("Neverlank Standard") used to speed up mapping a
        # client's trial balance accounts to an IAS 1 category - seeded the
        # same way as QPD Instalment Rates above, once, as an editable
        # starting point (see seed_chart_of_accounts's own docstring).
        if StandardChartOfAccounts.query.count() == 0:
            from seed import seed_chart_of_accounts
            seed_chart_of_accounts()
        # Document Templates whose reference codes were renumbered to the
        # Filing Index's N-codes (e.g. SA-02 -> N9006) need already-seeded
        # rows on an existing install updated to match - safe/cheap to run
        # on every startup for the same reason as seed_permissions() above:
        # it only touches a row still on its exact old code, so it's a
        # no-op once done (or on a brand-new install that seeded with the
        # new codes to begin with).
        from seed import migrate_document_template_ref_codes
        migrate_document_template_ref_codes()
        _fix_forensic_template_type()
        _fix_forensic_checklist_items()
        # CR-form code renumbering under the new Companies and Other
        # Business Entities Act [Chapter 24:31] (COBE Act): old CR14 (Return
        # of Directors) is now CR6, old CR6 (Notice of Situation of
        # Registered Office) is now CR5 - see migrate_cr_form_codes's own
        # docstring. Same no-op-once-done safety as the migration above.
        from seed import migrate_cr_form_codes, migrate_secretarial_finalisation_company_summary
        migrate_cr_form_codes()
        migrate_secretarial_finalisation_company_summary()
        # Payroll PAYE bands: seed_payroll_tax_bands() only ever populates a
        # pay frequency that currently has ZERO PayrollTaxBand rows of its
        # own, so it's safe/cheap to call on every startup (like
        # seed_permissions() above) - a no-op once every frequency has been
        # seeded or firm-customised, but it closes the gap for Fortnightly/
        # Weekly on any existing install that only ever had the old single
        # global (effectively Monthly) band list.
        from seed import seed_payroll_tax_bands
        seed_payroll_tax_bands()
        # NSSA employee/employer % and Insurable Earnings ceiling are now
        # confirmed (see PAYROLL_TAX_CAVEAT in models.py) - backfill them
        # onto an existing install whose PayrollTaxSettings row is still at
        # the old unconfirmed 0%/0%/no-ceiling defaults. Safe/cheap to call
        # on every startup for the same reason as seed_payroll_tax_bands
        # above: a no-op once done, or if the firm already customised any of
        # the three fields itself.
        from seed import seed_payroll_nssa_defaults
        seed_payroll_nssa_defaults()
        # Leave Management: seed_leave_types() only ever adds a LeaveType
        # that doesn't already exist by name, so it's safe/cheap to call on
        # every startup for the same reason as seed_payroll_tax_bands above -
        # a no-op once every statutory type has been seeded (or the firm has
        # renamed/retired one), but it closes the gap on first install.
        from seed import seed_leave_types
        seed_leave_types()

    register_cli(app)

    return app


def register_cli(app):
    @app.cli.command("seed")
    def seed():
        """Seed the database with an admin user and sample checklist templates."""
        from seed import run_seed
        run_seed()
        click.echo("Database seeded.")

    @app.cli.command("refresh-sanctions-lists")
    def refresh_sanctions_lists():
        """Refresh the cached UN/OFAC/EU sanctions lists used by automated
        screening (see sanctions_data.refresh_all_sources). Run this from a
        scheduled job (e.g. a Render Cron Job hitting this command on a
        daily/weekly schedule) to keep the cache current without anyone
        having to click "Refresh lists now" in the app - see the README for
        how to set that up. Safe to run any time; each source is refreshed
        independently, so one being temporarily unreachable doesn't stop
        the others."""
        import sanctions_data
        results = sanctions_data.refresh_all_sources()
        for source, (ok, count, error) in results.items():
            if ok:
                click.echo(f"{source}: refreshed, {count} entries.")
            else:
                click.echo(f"{source}: FAILED - {error}")

    @app.cli.command("import-zimra-notices")
    @click.option("--dry-run", is_flag=True, help="List what would be imported without downloading or saving anything.")
    @click.option("--pages", type=int, default=None, help="Only fetch this many ZIMRA listing pages (20 notices/page) - use a small number to test first.")
    @click.option("--limit", type=int, default=None, help="Import at most this many new notices.")
    @click.option("--sleep", "sleep_seconds", type=float, default=1.5, help="Seconds to wait between requests to ZIMRA's server (politeness delay).")
    def import_zimra_notices(dry_run, pages, limit, sleep_seconds):
        """Bulk-import ZIMRA's Public Notices page
        (https://www.zimra.co.zw/public-notices) into Legislative Update
        Control as filed "Notice" entries, each with an AI summary - see
        zimra_notices.py. This is a CLI command rather than a button in the
        app because, at roughly 480 notices, a single web request would
        badly exceed any reasonable timeout - run it from Render's Shell
        instead, where it can take as long as it needs.

        ALWAYS run with --dry-run --pages 1 first to sanity-check what
        actually gets extracted from a real page before committing to a
        full import (see zimra_notices.py's module docstring for why).
        Safe to interrupt and re-run at any point: every notice already
        imported (matched by ZIMRA's own id) is skipped, never duplicated."""
        import zimra_notices
        zimra_notices.import_notices(
            dry_run=dry_run, max_pages=pages, limit=limit, sleep_seconds=sleep_seconds, log=click.echo,
        )

    @app.cli.command("import-zimlii-legislation")
    @click.option("--dry-run", is_flag=True, help="List what would be imported (and why it matched) without fetching or saving anything.")
    @click.option("--acts-only", is_flag=True, help="Only consider Acts/Ordinances - skip subsidiary legislation (regulations/SIs).")
    @click.option("--subsidiary-only", is_flag=True, help="Only consider subsidiary legislation (regulations/SIs) - skip Acts/Ordinances.")
    @click.option("--pages", type=int, default=None, help="Only fetch this many listing pages per collection (20 documents/page) - use a small number to test first.")
    @click.option("--limit", type=int, default=None, help="Import at most this many new documents.")
    @click.option("--sleep", "sleep_seconds", type=float, default=2.0, help="Seconds to wait between requests to ZimLII's server (politeness delay).")
    def import_zimlii_legislation(dry_run, acts_only, subsidiary_only, pages, limit, sleep_seconds):
        """Bulk-import Zimbabwean primary legislation and current subsidiary
        legislation from ZimLII (https://zimlii.org/legislation/) into
        Legislative Update Control, filtered to tax, customs, companies,
        commercial, estate duty and related Finance law - see
        zimlii_import.py for the full design and the CATEGORY_KEYWORDS list
        that decides what counts as "related". This is a CLI command rather
        than a button in the app for the same reason import-zimra-notices
        is: a few hundred documents, some of them full Acts running to
        hundreds of pages, would badly exceed a web request's timeout - run
        it from Render's Shell instead.

        ALWAYS run with --dry-run first (see zimlii_import.py's module
        docstring for the full two-step dry-run routine this needs).
        Safe to interrupt and re-run at any point: every document already
        imported (matched by its own stable ZimLII id) is skipped, never
        duplicated."""
        if acts_only and subsidiary_only:
            raise click.UsageError("--acts-only and --subsidiary-only can't both be set.")
        import zimlii_import
        zimlii_import.import_documents(
            include_acts=not subsidiary_only, include_subsidiary=not acts_only,
            dry_run=dry_run, max_pages=pages, limit=limit, sleep_seconds=sleep_seconds, log=click.echo,
        )

    @app.cli.command("import-veritas-legislation")
    @click.option("--term", "term_id", type=int, default=98, help="Veritas taxonomy term id to import from (default: 98, 'Income Tax' - https://www.veritaszim.net/taxonomy/term/98).")
    @click.option("--area", "default_area", default="Tax", help="Practice area (see LEGISLATIVE_UPDATE_AREAS) to tag every imported item with - default 'Tax', which fits term 98. Pass '' for none.")
    @click.option("--dry-run", is_flag=True, help="List what would be imported (and the identifier it matched) without downloading or saving anything.")
    @click.option("--pages", type=int, default=None, help="Only fetch this many listing pages (20 items/page) - use a small number to test first.")
    @click.option("--limit", type=int, default=None, help="Import at most this many new items.")
    @click.option("--sleep", "sleep_seconds", type=float, default=1.5, help="Seconds to wait between requests to Veritas's server (politeness delay).")
    def import_veritas_legislation(term_id, default_area, dry_run, pages, limit, sleep_seconds):
        """Bulk-import Zimbabwean tax legislation from one of Veritas
        Zimbabwe's taxonomy listing pages (default: term 98, "Income Tax",
        https://www.veritaszim.net/taxonomy/term/98) into Legislative
        Update Control - see veritas_import.py for the full design.

        Per Petros's own instruction: skips anything where the same Act or
        SI is already on file, from ANY source (Veritas, ZimLII, ZIMRA, or
        a manual entry) - see veritas_import.py's module docstring for
        exactly how that's matched. A listing item that isn't recognisably
        an Act/SI/Government Notice (e.g. a Veritas "Bill Watch" commentary
        piece) is skipped outright - only legislation itself gets filed.

        ALWAYS run with --dry-run --pages 1 first (see veritas_import.py's
        module docstring for why - this environment couldn't test the page
        parsing against the live site). Safe to interrupt and re-run at any
        point."""
        import veritas_import
        veritas_import.import_legislation(
            term_id=term_id, default_area=(default_area or None),
            dry_run=dry_run, max_pages=pages, limit=limit, sleep_seconds=sleep_seconds, log=click.echo,
        )


app = create_app()


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


if __name__ == "__main__":
    import threading
    import webbrowser

    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    print("=" * 64)
    print(" Neverlank Audit, Assurance & Consulting App")
    print(" Starting up...")
    print(f" On this computer:            http://localhost:{port}")
    print(f" From other office computers: http://<this-PC-IP>:{port}")
    print(" Keep this window open while the app is in use.")
    print(" Close this window (or press CTRL+C) to stop the app.")
    print("=" * 64)

    if not debug:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    # socketio.run() (not app.run()) so Messages > Call works in the
    # standalone desktop build too, not just under gunicorn on Render.
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, use_reloader=False, allow_unsafe_werkzeug=True)
