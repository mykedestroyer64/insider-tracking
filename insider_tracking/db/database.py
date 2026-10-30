"""
database.py - one open connection, used through short transactions.

Every repository works through Database.transaction(): the work is committed at the end,
and any driver error undoes the unfinished change and becomes a DatabaseError, so the
collectors only ever have to handle that one kind of database problem. Maintenance (VACUUM,
which can't run inside a transaction) works through Database.autocommit() instead.
"""

from contextlib import contextmanager

from ..core.errors import DatabaseError
from .connection import describe


class Database:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        """A cursor whose work is committed together (or undone together on an error)."""
        try:
            cursor = self.connection.cursor()
            yield cursor
            self.connection.commit()
        except DatabaseError:
            raise
        except Exception as error:
            try:
                self.connection.rollback()
            except Exception:
                pass
            raise DatabaseError(describe(error)) from error

    @contextmanager
    def autocommit(self):
        """A cursor whose statements go straight to the database, each one on its own (no
        transaction to commit or undo); driver errors become a DatabaseError, as above. The
        connection goes back to transactions afterwards."""
        self.connection.autocommit = True
        try:
            yield self.connection.cursor()
        except DatabaseError:
            raise
        except Exception as error:
            raise DatabaseError(describe(error)) from error
        finally:
            self.connection.autocommit = False

    def close(self):
        try:
            self.connection.close()
        except Exception:
            pass


def row_slots(count, last=None):
    """'(%s, %s, %s)' for one row of `count` values; `last` replaces the last slot ('%s::jsonb')."""
    slots = ["%s"] * count
    if last:
        slots[-1] = last
    return "(" + ", ".join(slots) + ")"


def chunks(rows, size):
    """The rows in groups of `size` (multi-row statements stay a manageable size)."""
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def is_true(value):
    """Drivers return booleans as True, 't', 'true' or 1 depending on how they're asked."""
    return value in (True, "t", "true", 1)
