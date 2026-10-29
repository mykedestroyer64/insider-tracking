"""
storage.py - the database as a collector run uses it: opened at step 1, its tables brought up
to date, and the run recorded (collector_runs) at the start and at the end.
"""

from ..db.database import Database
from ..db.runs import RunLog
from ..db.schema import ensure_schema


class RunStorage:
    """connect(url, password) opens a DB-API connection (the platform's database driver)."""

    def __init__(self, connect):
        self.connect = connect
        self.db = None

    def open(self, settings):
        """The run's database (raises DatabaseError when it can't be reached)."""
        self.db = Database(self.connect(settings.db_url, settings.db_password))
        return self.db

    def ensure_tables(self):
        ensure_schema(self.db)

    def start_run(self, ran_on, collector):
        return RunLog(self.db).start(ran_on, collector)

    def finish_run(self, run_id, verdict, verdict_line, summary):
        RunLog(self.db).finish(run_id, verdict, verdict_line, summary)

    def close(self):
        if self.db is not None:
            self.db.close()
