"""
db_size_check.py - shows how much space each table, index and column takes in the
database (read only: it changes nothing). Supabase's free plan allows 500 MB.

Run by the "New sources check" workflow with the check name "dbsize"; the results
appear as notes on the run. Needs the SUPABASE_DB_URL and SUPABASE_DB_PASSWORD secrets.
"""

import os

import database

BIG_TABLES = ("insider_trades", "insider_filings", "transactions", "mar_trades", "report_pages", "reports")


def note(text):
    print("::notice title=DBSIZE check::" + text.replace("\n", " "))


def mb(size):
    return f"{(size or 0) / 1048576:.1f} MB"


def main():
    conn = database.connect(os.environ["SUPABASE_DB_URL"], os.environ.get("SUPABASE_DB_PASSWORD"))
    cursor = conn.cursor()
    cursor.execute("select pg_database_size(current_database())")
    whole = cursor.fetchone()[0]

    cursor.execute("""
        select c.relname, pg_total_relation_size(c.oid), pg_relation_size(c.oid), pg_indexes_size(c.oid),
               coalesce(pg_total_relation_size(nullif(c.reltoastrelid, 0)), 0), c.reltuples::bigint,
               coalesce(s.n_dead_tup, 0)
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        left join pg_stat_user_tables s on s.relid = c.oid
        where n.nspname = 'public' and c.relkind = 'r'
        order by 2 desc""")
    tables = cursor.fetchall()
    note(f"whole database {mb(whole)}; tables: " + " | ".join(
        f"{name} {mb(total)} (data {mb(heap)}, indexes {mb(indexes)}, toast {mb(toast)}, ~{rows:,} rows, {dead:,} dead)"
        for name, total, heap, indexes, toast, rows, dead in tables))

    cursor.execute("""
        select indexrelname, relname, pg_relation_size(indexrelid), idx_scan
        from pg_stat_user_indexes order by 3 desc""")
    note("indexes: " + " | ".join(f"{index} on {table} {mb(size)}, used {scans:,} times"
                                  for index, table, size, scans in cursor.fetchall()))

    for table in BIG_TABLES:
        cursor.execute("select column_name from information_schema.columns "
                       "where table_schema = 'public' and table_name = %s order by ordinal_position", (table,))
        columns = [row[0] for row in cursor.fetchall()]
        if not columns:
            continue
        averages = ", ".join(f'avg(pg_column_size("{column}"))::float' for column in columns)
        cursor.execute(f'select count(*), {averages} from (select * from "{table}" tablesample system (5)) sample')
        row = cursor.fetchone()
        parts = sorted(zip(columns, row[1:]), key=lambda pair: -(pair[1] or 0))
        note(f"{table}, average bytes per row by column (sample of {row[0]:,} rows): "
             + ", ".join(f"{column} {value or 0:.0f}" for column, value in parts))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
