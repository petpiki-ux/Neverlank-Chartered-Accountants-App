"""A test-time guard: refuse to save a text value that is longer than its column allows.

SQLite quietly stores any length. PostgreSQL (the database this app is being prepared to move to) rejects it with "value too long". So a
value that works today on SQLite becomes an error after the move. With NL_STRICT_LENGTHS=1 (set by run_tests.py) every save is checked
here, which finds the problem on the ordinary SQLite test run instead of after the move. Not active in normal use.
"""
from sqlalchemy import String, event
from sqlalchemy.orm import Session


def install():
    @event.listens_for(Session, "before_flush")
    def _check(session, flush_context, instances):
        for obj in list(session.new) + list(session.dirty):
            table = getattr(obj, "__table__", None)
            if table is None:
                continue
            for col in table.columns:
                if isinstance(col.type, String) and col.type.length:
                    val = getattr(obj, col.key, None)
                    if isinstance(val, str) and len(val) > col.type.length:
                        raise ValueError(f"{table.name}.{col.name} holds {len(val)} characters but the column allows {col.type.length}: {val[:60]!r}")
