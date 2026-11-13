"""Copy the firm's SQLite database into an empty PostgreSQL database (a one-time move).

    python tools/sqlite_to_postgres.py --sqlite /var/data/instance/neverlank.db --pg "postgresql://USER:PASS@HOST/DBNAME" [--dry-run]

What it does, in order:
  1. Checks the SQLite file (integrity) and that the PostgreSQL database holds no app tables yet.
  2. Creates every table in PostgreSQL from the app's own models (plus the lookup indexes).
  3. Copies every row, table by table, parents before children. SQLite does not enforce links between tables, so an old file can
     hold "orphans" (a row pointing at a parent that was deleted). PostgreSQL refuses those. An orphan link that may be empty is set
     to empty; a row whose link is required is left out. Everything changed or left out is listed in the report.
  4. Moves PostgreSQL's id counters past the copied ids (otherwise the next new record would clash with an old one).
  5. Counts the rows on both sides and says whether they agree.

The SQLite file is only read, never changed. Run it with --dry-run first: it does steps 1 and 3 in memory and reports what it would do
without writing anything to PostgreSQL. Uploaded files (the uploads folder) are not part of the database and need no copying.
"""
import argparse
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def load_metadata():
    """The app's table definitions, loaded without touching any real database."""
    tmp = tempfile.mkdtemp(prefix="nl_meta_")
    os.environ["DATA_DIR"] = tmp
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "meta.db")
    from app import create_app
    from extensions import db
    create_app()
    return db.metadata


def normalise_pg_url(url):
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", required=True, help="path of the SQLite database file")
    ap.add_argument("--pg", required=True, help="PostgreSQL URL of an empty database")
    ap.add_argument("--dry-run", action="store_true", help="read and check only; write nothing to PostgreSQL")
    ap.add_argument("--truncate-long-text", action="store_true", help="cut text longer than its column instead of stopping")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args(argv)

    from sqlalchemy import create_engine, inspect, select, text, String

    if not os.path.exists(args.sqlite):
        print("SQLite file not found:", args.sqlite)
        return 2
    src = create_engine("sqlite:///" + os.path.abspath(args.sqlite))
    with src.connect() as c:
        res = c.execute(text("PRAGMA integrity_check")).fetchone()[0]
    print("SQLite integrity check:", res)
    if res != "ok":
        print("The SQLite file is damaged. Restore a good backup first.")
        return 2

    metadata = load_metadata()
    tables = list(metadata.sorted_tables)
    s_insp = inspect(src)
    src_tables = set(s_insp.get_table_names())
    print(f"The app defines {len(tables)} tables; the SQLite file has {len(src_tables)}.")
    extra = sorted(src_tables - {t.name for t in tables})
    if extra:
        print("In SQLite but not in the app (not copied):", ", ".join(extra))

    def copy_all(dst):
        # ---- parent keys, to find orphans: {table: set of primary key values that WILL exist in the target}
        kept_keys = {}
        report = {"orphan_nulled": [], "orphan_dropped": [], "truncated": [], "too_long": []}
        totals = {}
        t0 = time.time()

        for table in tables:
            if table.name not in src_tables:
                totals[table.name] = (0, 0)
                continue
            s_cols = {c["name"] for c in s_insp.get_columns(table.name)}
            cols = [c for c in table.columns if c.name in s_cols]
            pk_cols = [c.name for c in table.primary_key.columns]
            fks = []
            for fk in table.foreign_key_constraints:
                if len(fk.columns) == 1:
                    col = list(fk.columns)[0]
                    parent = list(fk.elements)[0].column
                    fks.append((col, parent.table.name, parent.name))
            str_limits = {c.name: c.type.length for c in cols if isinstance(c.type, String) and c.type.length}
            kept_ids = set()
            n_src = n_out = 0
            self_ids = None
            if any(ptable == table.name for _c, ptable, _p in fks) and len(pk_cols) == 1:
                with src.connect() as sc:           # a table that points at itself (parent/child rows): the parent may come later in the file
                    self_ids = {r[0] for r in sc.execute(select(table.c[pk_cols[0]]))}
            with src.connect() as sc:
                result = sc.execute(select(*cols))
                batch = []

                def flush():
                    nonlocal batch, n_out
                    if batch and dst is not None:
                        with dst.begin() as dc:
                            dc.execute(table.insert(), batch)
                    n_out += len(batch)
                    batch = []

                for row in result:
                    n_src += 1
                    data = dict(zip([c.name for c in cols], row))
                    drop = False
                    for col, ptable, pcol in fks:
                        val = data.get(col.name)
                        if val is None:
                            continue
                        if ptable == table.name and self_ids is not None:
                            parent_ok = val in self_ids
                        else:
                            parent_ok = val in kept_keys[ptable] if ptable in kept_keys else True
                        if not parent_ok:
                            if col.nullable:
                                report["orphan_nulled"].append((table.name, pk_value(data, pk_cols), col.name, val, ptable))
                                data[col.name] = None
                            else:
                                report["orphan_dropped"].append((table.name, pk_value(data, pk_cols), col.name, val, ptable))
                                drop = True
                    if drop:
                        continue
                    for name, limit in str_limits.items():
                        v = data.get(name)
                        if isinstance(v, str) and len(v) > limit:
                            if args.truncate_long_text:
                                report["truncated"].append((table.name, pk_value(data, pk_cols), name, len(v), limit))
                                data[name] = v[:limit]
                            else:
                                report["too_long"].append((table.name, pk_value(data, pk_cols), name, len(v), limit))
                    if len(pk_cols) == 1:
                        kept_ids.add(data.get(pk_cols[0]))
                    batch.append(data)
                    if len(batch) >= args.batch:
                        flush()
                flush()
            kept_keys[table.name] = kept_ids if len(pk_cols) == 1 else None
            if kept_keys[table.name] is None:
                del kept_keys[table.name]
            totals[table.name] = (n_src, n_out)
            if n_src and (dst is not None or args.dry_run):
                print(f"  {table.name:<45} {n_src:>7} read, {n_out:>7} {'copied' if dst is not None else 'would be copied'}")
        return report, totals

    # first pass reads everything and checks it, writing nothing; only a clean first pass is followed by the real copy
    report, totals = copy_all(None)
    if report["too_long"]:
        print("\nSTOPPED before writing anything: text longer than its column allows (PostgreSQL would refuse it):")
        for r in report["too_long"][:30]:
            print("  %s id=%s column %s: %s characters, limit %s" % r)
        print("Run again with --truncate-long-text to cut these, or widen the column in models.py first.")
        return 1
    dst = None
    if not args.dry_run:
        print("Checks passed; copying now.")
        dst = create_engine(normalise_pg_url(args.pg), pool_pre_ping=True)
        d_insp = inspect(dst)
        existing = [t for t in d_insp.get_table_names() if t in {x.name for x in tables}]
        if existing:
            print("The PostgreSQL database already has app tables (e.g. %s). Use a new, empty database." % ", ".join(existing[:5]))
            return 2
        metadata.create_all(dst)
        import db_indexes
        print("PostgreSQL tables created; lookup indexes added:", db_indexes.ensure_indexes(dst))
        report, totals = copy_all(dst)
    t0 = time.time()

    if dst is not None:
        import db_indexes
        with dst.begin() as dc:
            print("Id counters moved for", db_indexes.reset_sequences(dc), "tables.")

    print()
    if report["orphan_nulled"]:
        print(f"{len(report['orphan_nulled'])} link(s) pointed at a row that no longer exists and were set to empty:")
        for r in report["orphan_nulled"][:30]:
            print("  %s id=%s: %s=%s (no such row in %s)" % r)
    if report["orphan_dropped"]:
        print(f"{len(report['orphan_dropped'])} row(s) were left out because a required link pointed at a missing row:")
        for r in report["orphan_dropped"][:30]:
            print("  %s id=%s: %s=%s (no such row in %s)" % r)
    if report["truncated"]:
        print(f"{len(report['truncated'])} text value(s) were cut to fit their column.")

    bad = []
    if dst is not None:
        with dst.connect() as dc:
            for name, (n_src, n_out) in totals.items():
                n_dst = dc.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
                if n_dst != n_out:
                    bad.append((name, n_out, n_dst))
    lost = sum(n_src - n_out for n_src, n_out in totals.values())
    print(f"Done in {time.time() - t0:.0f}s. Rows read {sum(v[0] for v in totals.values())}, "
          f"{'copied' if dst is not None else 'that would be copied'} {sum(v[1] for v in totals.values())}, left out {lost}.")
    if bad:
        print("COUNT MISMATCH after copying:", bad)
        return 1
    print("Row counts agree on both sides." if dst is not None else "Dry run only: nothing was written.")
    return 0


def pk_value(data, pk_cols):
    return ",".join(str(data.get(c)) for c in pk_cols) if pk_cols else "?"


if __name__ == "__main__":
    sys.exit(main())
