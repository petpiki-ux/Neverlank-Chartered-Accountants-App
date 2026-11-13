"""Put a database dump (from the Backup page on PostgreSQL) back into a new, empty PostgreSQL database.

    python tools/restore_pg_dump.py --dump neverlank_pg.jsonl.gz --pg "postgresql://USER:PASS@HOST/NEWDB"

The zip made by the Backup page holds the dump as database/neverlank_pg.jsonl.gz; unzip it first. Then point the app's DATABASE_URL at the new
database. Nothing is changed in any existing database.
"""
import argparse, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--pg", required=True)
    args = ap.parse_args(argv)
    from sqlalchemy import create_engine, inspect
    from tools.sqlite_to_postgres import load_metadata, normalise_pg_url
    metadata = load_metadata()
    dst = create_engine(normalise_pg_url(args.pg), pool_pre_ping=True)
    if [t for t in inspect(dst).get_table_names() if t in metadata.tables]:
        print("That database already has app tables. Use a new, empty database.")
        return 2
    metadata.create_all(dst)
    import db_indexes, db_dump
    db_indexes.ensure_indexes(dst)
    counts = db_dump.load_dump(dst, metadata, args.dump)
    print(f"Restored {sum(counts.values())} rows into {len(counts)} tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
