"""size.py - how big the database and its tables are (Supabase's free plan allows 500 MB)."""

FREE_PLAN_MB = 500


class DatabaseSize:
    def __init__(self, db):
        self.db = db

    def database_bytes(self):
        with self.db.transaction() as cursor:
            cursor.execute("select pg_database_size(current_database())")
            return int(cursor.fetchone()[0])

    def total_mb(self):
        """The whole database's size in MB, rounded."""
        return round(self.database_bytes() / 1024 / 1024)

    def whole_database_note(self):
        """' (whole database 318 MB of the free plan's 500 MB)', for the end of a run's summary."""
        return f" (whole database {self.total_mb()} MB of the free plan's {FREE_PLAN_MB} MB)"

    def tables(self):
        """[(table, total bytes)], biggest first."""
        with self.db.transaction() as cursor:
            cursor.execute("""
        select c.relname, pg_total_relation_size(c.oid)
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r' order by 2 desc""")
            return [(str(name), int(size)) for name, size in cursor.fetchall()]

    def table_details(self):
        """[(table, total, data, indexes, toast, ~rows, dead rows)] in bytes, biggest first."""
        with self.db.transaction() as cursor:
            cursor.execute("""
        select c.relname, pg_total_relation_size(c.oid), pg_relation_size(c.oid), pg_indexes_size(c.oid),
               coalesce(pg_total_relation_size(nullif(c.reltoastrelid, 0)), 0), c.reltuples::bigint,
               coalesce(s.n_dead_tup, 0)
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        left join pg_stat_user_tables s on s.relid = c.oid
        where n.nspname = 'public' and c.relkind = 'r'
        order by 2 desc""")
            return [(str(name), int(total), int(heap), int(indexes), int(toast), int(rows), int(dead))
                    for name, total, heap, indexes, toast, rows, dead in cursor.fetchall()]

    def indexes(self):
        """[(index, table, bytes, times used)], biggest first."""
        with self.db.transaction() as cursor:
            cursor.execute("""
        select indexrelname, relname, pg_relation_size(indexrelid), idx_scan
        from pg_stat_user_indexes order by 3 desc""")
            return [(str(index), str(table), int(size), int(scans)) for index, table, size, scans in cursor.fetchall()]

    def column_averages(self, table):
        """(rows sampled, [(column, average bytes)]) from a 5% sample of a table; (0, []) if there's no such table."""
        with self.db.transaction() as cursor:
            cursor.execute("select column_name from information_schema.columns "
                           "where table_schema = 'public' and table_name = %s order by ordinal_position", (table,))
            columns = [str(row[0]) for row in cursor.fetchall()]
            if not columns:
                return 0, []
            averages = ", ".join(f'avg(pg_column_size("{column}"))::float' for column in columns)
            cursor.execute(f'select count(*), {averages} from (select * from "{table}" tablesample system (5)) sample')
            row = cursor.fetchone()
        values = [None if value is None else float(value) for value in row[1:]]
        return int(row[0]), list(zip(columns, values))
