"""A plain-file copy of the whole database that does not depend on the database program.

Used for backups when the app runs on PostgreSQL (on SQLite the backup page copies the database file itself). The dump is one gzip file,
one line per row, readable by anyone with Python. load_dump() puts it back into an EMPTY database whose tables were created from the app's
models (tools/restore_pg_dump.py does that for you).
"""
import base64
import datetime as dt
import decimal
import gzip
import json

from sqlalchemy import select, inspect, text

FORMAT = "neverlank-dump-1"


def _enc(v):
    if isinstance(v, dt.datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, dt.date):
        return {"$d": v.isoformat()}
    if isinstance(v, dt.time):
        return {"$t": v.isoformat()}
    if isinstance(v, decimal.Decimal):
        return {"$n": str(v)}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$b": base64.b64encode(bytes(v)).decode()}
    return v


def _dec(v):
    if isinstance(v, dict) and len(v) == 1:
        (k, x), = v.items()
        if k == "$dt": return dt.datetime.fromisoformat(x)
        if k == "$d": return dt.date.fromisoformat(x)
        if k == "$t": return dt.time.fromisoformat(x)
        if k == "$n": return decimal.Decimal(x)
        if k == "$b": return base64.b64decode(x)
    return v


def dump_to_file(engine, metadata, path):
    """Write every row of every app table to `path` (gzip). Returns {table: rows}. Reads inside one transaction, so the copy is consistent."""
    counts = {}
    present = set(inspect(engine).get_table_names())
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as out, engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        out.write(json.dumps({"format": FORMAT, "created": dt.datetime.utcnow().isoformat(), "dialect": engine.dialect.name}) + "\n")
        for table in metadata.sorted_tables:
            if table.name not in present:
                continue
            names = [c.name for c in table.columns]
            n = 0
            for row in conn.execute(select(table)).yield_per(2000):
                out.write(json.dumps({"t": table.name, "r": [_enc(v) for v in row]}, separators=(",", ":")) + "\n")
                n += 1
            counts[table.name] = n
        conn.rollback()
    return counts


def load_dump(engine, metadata, path, batch=500):
    """Fill an empty database (tables already created) from a dump. Returns {table: rows}. Raises if a table in the file is not in the app."""
    tables = {t.name: t for t in metadata.sorted_tables}
    counts, buf, current = {}, [], None

    def flush(conn):
        nonlocal buf
        if buf:
            conn.execute(tables[current].insert(), buf)
            counts[current] = counts.get(current, 0) + len(buf)
            buf = []

    with gzip.open(path, "rt", encoding="utf-8") as fh, engine.begin() as conn:
        head = json.loads(fh.readline())
        if head.get("format") != FORMAT:
            raise ValueError("This is not a Neverlank database dump.")
        for line in fh:
            rec = json.loads(line)
            name = rec["t"]
            if name not in tables:
                raise ValueError(f"The dump holds a table the app does not have: {name}")
            if name != current:
                flush(conn)
                current = name
            cols = [c.name for c in tables[name].columns]
            buf.append(dict(zip(cols, [_dec(v) for v in rec["r"]])))
            if len(buf) >= batch:
                flush(conn)
        flush(conn)
        import db_indexes
        db_indexes.reset_sequences(conn)
    return counts
