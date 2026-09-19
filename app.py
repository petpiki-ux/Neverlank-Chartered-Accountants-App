import os
import click
from flask import Flask, redirect, url_for
from flask_login import current_user
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from config import Config, INSTANCE_DIR
from extensions import db, login_manager, socketio
from models import User, DocumentTemplate, Permission, FilingIndexSection


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
        "document": [("reference", "VARCHAR(100)"), ("substantive_area_id", "INTEGER"), ("filing_index_id", "INTEGER")],
        "client": [("company_number", "VARCHAR(80)")],
        "message_recipient": [("recalled_at", "DATETIME")],
        "engagement": [("subdivision", "VARCHAR(50)"), ("acceptance_required", "BOOLEAN DEFAULT 0")],
        "analytical_review_line": [("source", "VARCHAR(10) DEFAULT 'manual'")],
        "engagement_checklist_item": [
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
        "substantive_procedure_item": [("tickmark_id", "INTEGER")],
        "engagement_task": [
            ("completed_by_id", "INTEGER"),
            ("completed_at", "DATETIME"),
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
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
        ],
        "materiality_calculation": [
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
        "entity_understanding": list(partner_signoff_cols),
        "analytical_review": list(partner_signoff_cols),
        "trial_balance": list(partner_signoff_cols),
        "financial_statements": list(partner_signoff_cols) + [("notes_to_financial_statements", "TEXT")],
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

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("engagements.dashboard"))
        return redirect(url_for("auth.login"))

    @app.context_processor
    def inject_globals():
        from models import user_has_permission, MessageRecipient
        unread = (
            MessageRecipient.query.filter_by(user_id=current_user.id, read_at=None).count()
            if current_user.is_authenticated
            else 0
        )
        return {
            "firm_name": "Neverlank Chartered Accountants",
            "user_has_permission": user_has_permission,
            "unread_message_count": unread,
        }

    with app.app_context():
        db.create_all()
        _add_missing_columns()
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
