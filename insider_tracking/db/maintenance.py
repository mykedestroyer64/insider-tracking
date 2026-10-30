"""
maintenance.py - freeing space in the database.

Dropping a column in PostgreSQL only hides it: the space comes back when the table is
rewritten (VACUUM FULL, which locks the table while it runs). VACUUM ANALYZE tidies a table
without locking it. Neither can run inside a transaction, so they go through
Database.autocommit(): straight to the database, with no transaction to commit.
"""

import time


class Maintenance:
    def __init__(self, db, timer=time.monotonic):
        self.db = db
        self.timer = timer

    def rewrite(self, table):
        """VACUUM FULL one table; returns the seconds it took."""
        return self._timed(f"vacuum full {table}")

    def tidy(self, tables):
        """VACUUM ANALYZE these tables; returns the seconds it took."""
        return self._timed("vacuum analyze " + ", ".join(tables))

    def _timed(self, statement):
        with self.db.autocommit() as cursor:
            cursor.execute("set statement_timeout = 0")     # a rewrite can take longer than the usual limit
            started = self.timer()
            cursor.execute(statement)
            return self.timer() - started
