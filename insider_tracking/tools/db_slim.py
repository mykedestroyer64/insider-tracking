"""
db_slim.py - frees space in the database after columns or indexes were dropped.

It brings the tables up to date (db.schema.ensure_schema, which drops what the collectors no
longer use), rewrites insider_filings (VACUUM FULL, about a minute, during which that table is
locked) and tidies the other tables.

Run by the "New sources check" workflow with the check name "slim"; the sizes before and
after appear as notes on the run. Needs the SUPABASE_DB_URL and SUPABASE_DB_PASSWORD secrets.
"""

from ..db.database import Database
from ..db.maintenance import Maintenance
from ..db.schema import ensure_schema
from ..db.size import DatabaseSize
from ..runs.settings import Settings
from .notes import Notes

REWRITE = ("insider_filings",)                       # tables whose dropped columns must be reclaimed
TIDY = ("insider_trades", "mar_trades", "reports", "transactions", "fx_rates",
        "collector_state", "collector_runs")


def describe(whole, tables):
    return f"whole database {whole / 1048576:.1f} MB; " + ", ".join(
        f"{name} {size / 1048576:.1f} MB" for name, size in tables[:8])


class SlimDatabase:
    def __init__(self, note=Notes("SLIM check")):
        self.note = note

    def run(self, argv, platform):
        settings = Settings.read(platform.environ)
        db = Database(platform.connect(settings.db_url, settings.db_password))
        size, note = DatabaseSize(db), self.note
        before = size.database_bytes(), size.tables()
        note("before: " + describe(*before))

        ensure_schema(db)            # drops the unused columns and indexes
        note("columns and indexes the collectors no longer use are dropped")

        maintenance = Maintenance(db)
        for table in REWRITE:
            note(f"rewrote {table} in {maintenance.rewrite(table):.0f} s")
        note(f"tidied {', '.join(TIDY)} in {maintenance.tidy(TIDY):.0f} s")

        after = size.database_bytes(), size.tables()
        note("after: " + describe(*after) + f"; freed {(before[0] - after[0]) / 1048576:.1f} MB")
        db.close()
        return 0


def command():
    """`python -m insider_tracking db slim`."""
    return SlimDatabase()
