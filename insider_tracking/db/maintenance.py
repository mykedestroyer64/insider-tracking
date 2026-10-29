"""
maintenance.py - freeing space in the database.

Dropping a column in PostgreSQL only hides it: the space comes back when the table is
rewritten (VACUUM FULL, which locks the table while it runs). VACUUM ANALYZE tidies a table
without locking it. Neither can run inside a transaction, so the connection is switched to
autocommit and the statements go straight to the database (no transaction to commit).
"""

import time


class Maintenance:
    def __init__(self, db, timer=time.monotonic):
        self.connection = db.connection
        self.timer = timer
        self.connection.autocommit = True     # VACUUM can't run inside a transaction
        self.cursor = self.connection.cursor()
        self.cursor.execute("set statement_timeout = 0")

    def rewrite(self, table):
        """VACUUM FULL one table; returns the seconds it took."""
        return self._timed(f"vacuum full {table}")

    def tidy(self, tables):
        """VACUUM ANALYZE these tables; returns the seconds it took."""
        return self._timed("vacuum analyze " + ", ".join(tables))

    def _timed(self, statement):
        started = self.timer()
        self.cursor.execute(statement)
        return self.timer() - started
