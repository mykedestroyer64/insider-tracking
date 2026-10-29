"""
psql_driver.py - a stand-in for the pg8000 driver, for computers where it can't be installed:
each statement goes through the psql program, with the %s parameters written into the SQL as
literals. It is slow, returns every value as text, and commits each statement on its own (no
transactions), so the real driver remains the one that counts (GitHub's tests use it).
"""

import datetime
import re

from .database import PsqlError

class DriverError(Exception):
    pass


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "'\\x" + bytes(value).hex() + "'::bytea"
    if isinstance(value, datetime.datetime):
        return "'" + value.isoformat() + "'::timestamptz"
    if isinstance(value, datetime.date):
        return "'" + value.isoformat() + "'::date"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (list, tuple)):
        return "ARRAY[" + ", ".join(literal(v) for v in value) + "]::text[]" if value else "'{}'::text[]"
    raise TypeError(f"unsupported parameter type {type(value)}")


def interpolate(sql, params):
    params = list(params or [])
    out, used = [], 0
    for piece in re.split(r"(%%|%s)", sql):
        if piece == "%%":
            out.append("%")
        elif piece == "%s":
            out.append(literal(params[used]))
            used += 1
        else:
            out.append(piece)
    if used != len(params):
        raise DriverError({"M": f"{len(params)} parameters but {used} placeholders"})
    return "".join(out)


class PsqlCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, params=()):
        if self.connection.closed:
            raise DriverError({"M": "connection is closed"})
        try:
            self.rows = [tuple(row) for row in self.connection.database.query(interpolate(sql, params), null=None)]
        except PsqlError as error:
            raise DriverError({"S": "ERROR", "M": str(error)}) from None
        self.connection.statements += 1

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class PsqlConnection:
    def __init__(self, database):
        self.database = database        # a TestDatabase
        self.closed = False
        self.statements = 0

    def cursor(self):
        return PsqlCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True
