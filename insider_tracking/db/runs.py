"""runs.py - one row per collector run, with its verdict and summary (table collector_runs)."""

import json


class RunLog:
    def __init__(self, db):
        self.db = db

    def start(self, ran_on, collector):
        """Record that a run started; returns the run's id."""
        with self.db.transaction() as cursor:
            cursor.execute("insert into collector_runs (started_at, ran_on, collector) "
                           "values (now(), %s, %s) returning id", (ran_on, collector))
            return int(cursor.fetchone()[0])

    def finish(self, run_id, verdict, verdict_line, summary):
        with self.db.transaction() as cursor:
            cursor.execute("""
        update collector_runs
        set finished_at = now(), verdict = %s, verdict_line = %s, summary = %s::jsonb
        where id = %s""", (verdict, verdict_line, json.dumps(summary, default=str), run_id))
