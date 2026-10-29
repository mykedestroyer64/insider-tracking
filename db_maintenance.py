"""
db_maintenance.py - frees space in the database after columns or indexes were dropped.

Dropping a column in PostgreSQL only hides it: the space comes back when the table is
rewritten. This script brings the tables up to date (database.ensure_schema, which
drops what the collectors no longer use), rewrites insider_filings (VACUUM FULL,
about a minute, during which that table is locked) and tidies the other tables.

Run by the "New sources check" workflow with the check name "slim"; the sizes before
and after appear as notes on the run. Needs the SUPABASE_DB_URL and
SUPABASE_DB_PASSWORD secrets.
"""

import os
import time

import database

REWRITE = ("insider_filings",)                       # tables whose dropped columns must be reclaimed
TIDY = ("insider_trades", "mar_trades", "reports", "transactions", "fx_rates",
        "collector_state", "collector_runs")


def note(text):
    print("::notice title=SLIM check::" + text.replace("\n", " "))


def sizes(cursor):
    cursor.execute("select pg_database_size(current_database())")
    whole = int(cursor.fetchone()[0])
    cursor.execute("""
        select c.relname, pg_total_relation_size(c.oid)
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r' order by 2 desc""")
    tables = [(str(name), int(size)) for name, size in cursor.fetchall()]
    return whole, tables


def describe(whole, tables):
    return f"whole database {whole / 1048576:.1f} MB; " + ", ".join(
        f"{name} {size / 1048576:.1f} MB" for name, size in tables[:8])


def main():
    conn = database.connect(os.environ["SUPABASE_DB_URL"], os.environ.get("SUPABASE_DB_PASSWORD"))
    cursor = conn.cursor()
    before = sizes(cursor)
    conn.commit()
    note("before: " + describe(*before))

    database.ensure_schema(conn)            # drops the unused columns and indexes
    note("columns and indexes the collectors no longer use are dropped")

    conn.autocommit = True                  # VACUUM can't run inside a transaction
    cursor = conn.cursor()
    cursor.execute("set statement_timeout = 0")
    for table in REWRITE:
        started = time.time()
        cursor.execute(f"vacuum full {table}")
        note(f"rewrote {table} in {time.time() - started:.0f} s")
    started = time.time()
    cursor.execute("vacuum analyze " + ", ".join(TIDY))
    note(f"tidied {', '.join(TIDY)} in {time.time() - started:.0f} s")

    after = sizes(cursor)
    note("after: " + describe(*after) + f"; freed {(before[0] - after[0]) / 1048576:.1f} MB")
    conn.close()


if __name__ == "__main__":
    main()
