"""Indexes for the columns the app looks rows up by.

SQLite and PostgreSQL both make a primary key fast, but neither indexes a foreign key column on its own. Without an index, "all the
documents of engagement 12" reads every document row. That is invisible with a few hundred rows and slow with a few hundred thousand.
This adds a plain index on every foreign key column that has none, once, and does nothing on later starts. Columns that only record
who did something (created_by_id, reviewed_by_id, ...) are left out: they are shown, not searched, so an index would only slow writing.
"""
import re
from sqlalchemy import inspect, text

SKIP = re.compile(r"(_by_id|_by)$")


def missing_indexes(engine):
    """[(table, column)] for every foreign key column that is not the first column of an existing index, key or unique constraint."""
    insp = inspect(engine)
    out = []
    for table in insp.get_table_names():
        covered = {c for ix in insp.get_indexes(table) for c in ix["column_names"][:1]}
        covered |= {u["column_names"][0] for u in insp.get_unique_constraints(table) if u["column_names"]}
        covered |= set((insp.get_pk_constraint(table).get("constrained_columns") or [])[:1])
        for fk in insp.get_foreign_keys(table):
            col = (fk.get("constrained_columns") or [None])[0]
            if col and col not in covered and not SKIP.search(col):
                covered.add(col)
                out.append((table, col))
    return out


def ensure_indexes(engine):
    """Create the missing indexes; returns how many were made. Safe to run on every start."""
    made = 0
    todo = missing_indexes(engine)
    if not todo:
        return 0
    with engine.begin() as conn:
        for table, col in todo:
            name = f"ix_{table}_{col}"[:60]
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ("{col}")'))
            made += 1
    return made


def reset_sequences(conn):
    """PostgreSQL only: move every table's id counter past the largest id already stored. Needed after rows were saved with an id chosen
    by the program (or copied in from SQLite): the counter does not know about them and would hand out an id that is taken."""
    if conn.dialect.name != "postgresql":
        return 0
    n = 0
    rows = conn.execute(text(
        "SELECT c.table_name, pg_get_serial_sequence(format('%I.%I', c.table_schema, c.table_name), c.column_name) "
        "FROM information_schema.columns c WHERE c.table_schema = current_schema() AND c.column_name = 'id'")).fetchall()
    for table, seq in rows:
        if seq:
            conn.execute(text(f'SELECT setval(:seq, COALESCE((SELECT MAX(id) FROM "{table}"), 1), (SELECT MAX(id) FROM "{table}") IS NOT NULL)'), {"seq": seq})
            n += 1
    return n
