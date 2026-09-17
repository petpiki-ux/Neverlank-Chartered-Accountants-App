import os
import click
from flask import Flask, redirect, url_for
from flask_login import current_user
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from config import Config, INSTANCE_DIR
from extensions import db, login_manager
from models import User, DocumentTemplate, Permission


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
        "document": [("reference", "VARCHAR(100)"), ("substantive_area_id", "INTEGER")],
        "engagement": [("subdivision", "VARCHAR(50)")],
        "engagement_checklist_item": [
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
        "engagement_task": [
            ("completed_by_id", "INTEGER"),
            ("completed_at", "DATETIME"),
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
        "risk_assessment": list(partner_signoff_cols),
        "materiality_calculation": [
            ("reviewed_by_id", "INTEGER"),
            ("reviewed_at", "DATETIME"),
        ] + partner_signoff_cols,
        "entity_understanding": list(partner_signoff_cols),
        "analytical_review": list(partner_signoff_cols),
        "trial_balance": list(partner_signoff_cols),
        "financial_statements": list(partner_signoff_cols),
    }
    with db.engine.connect() as conn:
        for table, columns in additions.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col_name, col_type in columns:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                    conn.commit()


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

    from auth import auth_bp
    from clients import clients_bp
    from engagements import engagements_bp
    from users import users_bp
    from doc_templates import doc_templates_bp
    from hr import hr_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(engagements_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(doc_templates_bp)
    app.register_blueprint(hr_bp)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("engagements.dashboard"))
        return redirect(url_for("auth.login"))

    @app.context_processor
    def inject_globals():
        from models import user_has_permission
        return {"firm_name": "Neverlank Chartered Accountants", "user_has_permission": user_has_permission}

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
        if Permission.query.count() == 0:
            # Likewise for the configurable role permissions (also a new
            # feature) - seed default rows that exactly reproduce this
            # app's previous hardcoded behaviour, so upgrading changes
            # nothing until an admin opens Team > Permissions and changes
            # a toggle. A plain `if` (not `elif`) so this still runs on the
            # very first launch of a brand-new install alongside the seeding
            # above - though run_seed() already seeds permissions itself, so
            # this is a no-op there and only does real work on an upgrade.
            from seed import seed_permissions
            seed_permissions()

    register_cli(app)

    return app


def register_cli(app):
    @app.cli.command("seed")
    def seed():
        """Seed the database with an admin user and sample checklist templates."""
        from seed import run_seed
        run_seed()
        click.echo("Database seeded.")


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

    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
