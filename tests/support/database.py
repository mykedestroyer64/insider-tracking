"""
database.py - the test database: a real PostgreSQL server, and a fresh database per test file.

Settings (environment variables):
  TEST_DATABASE_URL   the server, e.g. postgresql://postgres:postgres@localhost:5432/postgres
                      (GitHub's tests workflow sets this). When it isn't set: the server on this
                      computer, through its socket, as the current user (like psql without options).
  TEST_DB_DRIVER      how the code under test talks to the database: "pg8000" (the real driver,
                      the default when it is installed) or "psql" (psql_driver.py: slower, for
                      computers where pg8000 can't be installed).

The tests read the database back with the psql program (query()), so every value comes back
as text, whichever driver the code under test used.
"""

import getpass
import gzip
import os
import subprocess

from insider_tracking.db.connection import parse_db_url

NULL = "\x02NULL\x02"
FIELD, RECORD = "\x1f", "\x1e"
LOCAL_SOCKET = "/var/run/postgresql/.s.PGSQL.5432"


class PsqlError(Exception):
    """psql reported an error (the message is what it printed)."""


def default_driver():
    try:
        import pg8000.dbapi   # noqa: F401
        return "pg8000"
    except ImportError:
        return "psql"


class TestDatabase:
    """One database on the test server, named `name` (each test file uses its own)."""

    def __init__(self, name, server=None, driver=None):
        self.name = name
        url = os.environ.get("TEST_DATABASE_URL", "") if server is None else server
        self.server = parse_db_url(url) if url else None      # user, password, host, port
        self.driver = driver or os.environ.get("TEST_DB_DRIVER") or default_driver()

    # ---------------------------------------------------------------- psql

    def psql(self, *args, database=None, stdin=None):
        """Run the psql program against this database (or another one); what it printed."""
        command = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1"]
        environ = dict(os.environ)
        if self.server:
            command += ["-h", self.server["host"], "-p", str(self.server["port"]), "-U", self.server["user"]]
            environ["PGPASSWORD"] = self.server["password"]
        command += ["-d", database or self.name, *args]
        result = subprocess.run(command, capture_output=True, text=True, env=environ, input=stdin)
        if result.returncode != 0:
            raise PsqlError(result.stderr.strip())
        return result.stdout

    def query(self, sql, null=""):
        """The rows a statement returns, every value as text; NULL comes back as `null` ("" by
        default, as psql shows it)."""
        out = self.psql("-A", "-t", "-F", FIELD, "-R", RECORD, "-P", f"null={NULL}", "-c", sql)
        if out.endswith("\n"):
            out = out[:-1]
        return [[null if value == NULL else value for value in record.split(FIELD)]
                for record in out.split(RECORD) if record != ""]

    def value(self, sql):
        """The first value of the first row (text), or None."""
        rows = self.query(sql)
        return rows[0][0] if rows else None

    def fresh(self):
        """Start with an empty database of this name."""
        self.psql("-c", f'drop database if exists "{self.name}" with (force)', database="postgres")
        self.psql("-c", f'create database "{self.name}"', database="postgres")
        return self

    def restore(self, path):
        """Load a pg_dump file (.sql or .sql.gz) into this database."""
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as file:
            # \restrict and \unrestrict are new psql commands that older psql programs don't know
            sql = "".join(line for line in file if not line.startswith(("\\restrict", "\\unrestrict")))
        self.psql("-f", "-", stdin=sql)

    # ------------------------------------------------- the code under test

    def connect(self):
        """A DB-API connection to this database, for the code under test."""
        if self.driver == "psql":
            from .psql_driver import PsqlConnection
            return PsqlConnection(self)
        import pg8000.dbapi
        if self.server:
            return pg8000.dbapi.connect(user=self.server["user"], password=self.server["password"],
                                        host=self.server["host"], port=self.server["port"], database=self.name)
        return pg8000.dbapi.connect(user=getpass.getuser(), unix_sock=LOCAL_SOCKET, database=self.name)


class FailingConnection:
    """Wraps a connection: statements containing `text` fail, as if the connection dropped."""

    def __init__(self, connection, text):
        self.connection = connection
        self.text = text

    def cursor(self):
        return FailingCursor(self.connection.cursor(), self.text)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


class FailingCursor:
    def __init__(self, cursor, text):
        self.cursor = cursor
        self.text = text

    def execute(self, sql, params=()):
        if self.text in sql:
            raise Exception({"S": "ERROR", "M": "simulated failure: server closed the connection"})
        return self.cursor.execute(sql, params)

    def __getattr__(self, name):          # fetchone, fetchall, rowcount, ...
        return getattr(self.cursor, name)
