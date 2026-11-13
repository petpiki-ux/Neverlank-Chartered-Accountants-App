# Database, speed and tests (Nov 2026)

Part of the "what holds the app back" work (security: see firm_security.md).

## Running on PostgreSQL
The app now runs on PostgreSQL as well as SQLite. The whole test suite passes on both (SQLite: 108 files / 5,287 checks; PostgreSQL: all except 7 files that test the repair of an old SQLite file, which have no meaning there).
- `config.py`: `DATABASE_URL` in the host's form (`postgres://` or `postgresql://`) is turned into `postgresql+psycopg2://`; on PostgreSQL a pool of 8 connections (+4), pre-ping, 1-hour recycle. `psycopg2-binary` added to requirements.txt.
- `app.py` `_add_missing_columns` now also works on PostgreSQL (DATETIME -> TIMESTAMP, DEFAULT 0/1 -> FALSE/TRUE).
- `db_indexes.py`: adds a plain index on every foreign-key column that is searched (not the *_by_id audit columns): 183 indexes, created once at start-up, no-op afterwards. Also `reset_sequences()` (PostgreSQL id counters).
- `db_dump.py` + Backup page: on PostgreSQL the backup zip holds `database/neverlank_pg.jsonl.gz` (a plain-file dump of every table). Restore from the page is not offered on PostgreSQL; use `tools/restore_pg_dump.py`. Render's own database backups remain the first line of defence.
- Bugs found by running on PostgreSQL and fixed: `legislative_provision.part` 30 -> 80 characters (Companies Act "Chapter III, Part III, Sub-part H"), `analytical_review_line.label` 150 -> 400 (Companies Act review lines), id counter after the client-vetting migration inserted chosen ids.

## Moving the live data (the user does this on Render)
1. Create a Render PostgreSQL database; copy its Internal Database URL.
2. Take a backup (Backup page) and note the date. Stop using the app for the length of the copy.
3. In a Render shell on the web service: `python tools/sqlite_to_postgres.py --sqlite /var/data/instance/neverlank.db --pg "$PG_URL" --dry-run` and read the report; then the same without `--dry-run`. It stops before writing if any text is too long for its column (use `--truncate-long-text` or widen the column), and lists any "orphan" rows (SQLite never enforced links between tables; PostgreSQL does).
4. Set `DATABASE_URL` on the web service to the new database and redeploy. Keep the SQLite file untouched as a fallback.
Uploads stay on the disk; only the database moves. Keep `--workers 1` (call signalling and the sign-in lockout live in memory).

## Speed
Measured with 300 clients / 900 engagements / 2,400 invoices: invoice list 7,540 -> 36 queries (1.8 s -> 0.5 s); analytics 6,186 -> 37; every ordinary page lost about 1,000 queries (the menu's "invoice reminders due" badge loaded each unpaid invoice's lines, receipts, credit notes and history one at a time). Fix: `selectinload` in `invoice_send._with_money_and_history`, `invoicing.with_money`, analytics. `smoke_test_perf_queries.py` fails if a page goes back to a query per row.

## Tests
- `python run_tests.py` runs every smoke test two at a time (5 minutes instead of 15); `-j N`, word filters (`python run_tests.py payroll tax`), and `--pg URL` to run on PostgreSQL (each test gets its own database).
- `NL_STRICT_LENGTHS=1` (set by run_tests.py): refuses to save text longer than its column, so SQLite runs catch what PostgreSQL would reject (`db_strict.py`).
- New calculation tests worked out by hand with random checks: `smoke_test_calc_payroll.py`, `smoke_test_calc_financials.py` (statements, cash flow, tax computation, deferred tax, SME size, QPD, leave dates), `smoke_test_calc_invoicing.py` (VAT, receipts, credit notes, refunds).
- Observation, not changed: the cash flow shows the change in PPE net in investing activities and leaves depreciation as a memo line (totals reconcile); the usual presentation adds depreciation back and shows purchases gross.
