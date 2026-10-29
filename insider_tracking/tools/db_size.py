"""
db_size.py - shows how much space each table, index and column takes in the database (read
only: it changes nothing). Supabase's free plan allows 500 MB.

Run by the "New sources check" workflow with the check name "dbsize"; the results appear as
notes on the run. Needs the SUPABASE_DB_URL and SUPABASE_DB_PASSWORD secrets.
"""

from ..db.database import Database
from ..db.size import DatabaseSize
from ..runs.settings import Settings
from .notes import Notes, mb

BIG_TABLES = ("insider_trades", "insider_filings", "transactions", "mar_trades", "report_pages", "reports")


class DatabaseSizeReport:
    def __init__(self, note=Notes("DBSIZE check")):
        self.note = note

    def run(self, argv, platform):
        settings = Settings.read(platform.environ)
        db = Database(platform.connect(settings.db_url, settings.db_password))
        size, note = DatabaseSize(db), self.note
        note(f"whole database {mb(size.database_bytes())}; tables: " + " | ".join(
            f"{name} {mb(total)} (data {mb(heap)}, indexes {mb(indexes)}, toast {mb(toast)}, ~{rows:,} rows, {dead:,} dead)"
            for name, total, heap, indexes, toast, rows, dead in size.table_details()))
        note("indexes: " + " | ".join(f"{index} on {table} {mb(nbytes)}, used {scans:,} times"
                                      for index, table, nbytes, scans in size.indexes()))
        for table in BIG_TABLES:
            sampled, columns = size.column_averages(table)
            if not columns:
                continue
            parts = sorted(columns, key=lambda pair: -(pair[1] or 0))
            note(f"{table}, average bytes per row by column (sample of {sampled:,} rows): "
                 + ", ".join(f"{column} {value or 0:.0f}" for column, value in parts))
        db.close()
        return 0


def command():
    """`python -m insider_tracking db size`."""
    return DatabaseSizeReport()
