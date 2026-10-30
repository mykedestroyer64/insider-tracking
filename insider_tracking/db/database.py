"""
database.py - one open connection, used through short transactions.

Every repository works through Database.transaction(): the work is committed at the end,
and anything that goes wrong undoes the unfinished change. An error of the database driver
(a statement that fails, a dropped connection) becomes a DatabaseError, so the collectors only
ever have to handle that one kind of database problem; any other error is a mistake in the
code and stays what it is, so the run reports it as one (with its traceback). Maintenance
(VACUUM, which can't run inside a transaction) works through Database.autocommit() instead.
"""

from contextlib import contextmanager

from ..core.errors import DatabaseError
from .connection import describe


def driver_call(function, *args, **kwargs):
    """function(*args) of the database driver, with any error it raises as a DatabaseError."""
    try:
        return function(*args, **kwargs)
    except DatabaseError:
        raise
    except Exception as error:
        raise DatabaseError(describe(error)) from error


class DriverCursor:
    """The driver's cursor, with the errors of its methods (execute, fetchall, ...) as a
    DatabaseError."""

    def __init__(self, cursor):
        self._cursor = cursor

    def __getattr__(self, name):
        value = getattr(self._cursor, name)
        if not callable(value):
            return value               # rowcount, description
        return lambda *args, **kwargs: driver_call(value, *args, **kwargs)


class Database:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        """A cursor whose work is committed together (or undone together when anything goes wrong)."""
        try:
            cursor = DriverCursor(driver_call(self.connection.cursor))
            yield cursor
            driver_call(self.connection.commit)
        except Exception:
            self.undo()
            raise

    @contextmanager
    def autocommit(self):
        """A cursor whose statements go straight to the database, each one on its own (no
        transaction to commit or undo). The connection goes back to transactions afterwards."""
        self.connection.autocommit = True
        try:
            yield DriverCursor(driver_call(self.connection.cursor))
        finally:
            self.connection.autocommit = False

    def undo(self):
        """Undo the unfinished change (if the connection still works)."""
        try:
            self.connection.rollback()
        except Exception:
            pass

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
