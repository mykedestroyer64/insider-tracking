"""
The database layer's promises (db/database.py), with a stand-in driver: no database. An error of
the driver becomes a DatabaseError (the run says DB_ERROR); a mistake in the code stays what it
is (the run says ERROR, with its traceback); either way the unfinished change is undone.
Also: a chamber's repository adds reports as that chamber's.
"""
import sys

from insider_tracking.core.errors import DatabaseError
from insider_tracking.db.congress import CongressReports
from insider_tracking.db.database import Database

from .support.checks import Checks

check = Checks()


class DriverProblem(Exception):
    """What a driver raises (pg8000 raises its own kinds)."""


class StandInCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.connection.statements.append((sql, params))
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise DriverProblem({"S": "ERROR", "M": "simulated: server closed the connection"})

    def fetchall(self):
        return [("/r/1",)]


class StandInConnection:
    def __init__(self, fail_on=None, fail_commit=False):
        self.fail_on = fail_on
        self.fail_commit = fail_commit
        self.statements, self.commits, self.rollbacks = [], 0, 0
        self.autocommit = False

    def cursor(self):
        return StandInCursor(self)

    def commit(self):
        if self.fail_commit:
            raise DriverProblem({"S": "ERROR", "M": "simulated: could not commit"})
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


check.section("transactions")
connection = StandInConnection()
with Database(connection).transaction() as cursor:
    cursor.execute("select 1")
    rows = cursor.rowcount
check("work that goes well is committed", connection.commits == 1 and connection.rollbacks == 0)
check("the cursor's attributes are the driver's", rows == 1)

connection = StandInConnection(fail_on="insert")
try:
    with Database(connection).transaction() as cursor:
        cursor.execute("insert into reports values (1)")
    check("a statement that fails stops the work", False)
except DatabaseError as error:
    check("a statement that fails is a DatabaseError, with the server's message",
          "simulated: server closed the connection" in str(error), str(error))
check("and the change is undone, not committed", connection.rollbacks == 1 and connection.commits == 0)

connection = StandInConnection()
try:
    with Database(connection).transaction() as cursor:
        cursor.execute("insert into reports values (1)")
        raise KeyError("report_uuid")      # as a mistake in the code would
    check("a mistake in the code stops the work", False)
except DatabaseError:
    check("a mistake in the code is not called a database problem", False)
except KeyError:
    check("a mistake in the code stays what it is (KeyError)", True)
check("and the change is undone", connection.rollbacks == 1 and connection.commits == 0)

connection = StandInConnection(fail_commit=True)
try:
    with Database(connection).transaction() as cursor:
        cursor.execute("select 1")
    check("a failed commit stops the work", False)
except DatabaseError as error:
    check("a failed commit is a DatabaseError", "could not commit" in str(error), str(error))

check.section("statements outside a transaction (VACUUM)")
connection = StandInConnection(fail_on="vacuum full")
database = Database(connection)
with database.autocommit() as cursor:
    check("the connection is switched to autocommit", connection.autocommit is True)
    cursor.execute("vacuum analyze reports")
check("and back afterwards", connection.autocommit is False and connection.commits == 0)
try:
    with database.autocommit() as cursor:
        cursor.execute("vacuum full insider_filings")
    check("a failing VACUUM stops the work", False)
except DatabaseError:
    check("a failing VACUUM is a DatabaseError, and the connection goes back to transactions",
          connection.autocommit is False)

check.section("a chamber's reports")
connection = StandInConnection()
report = {"report_path": "/r/1", "report_uuid": "u1", "kind": "electronic", "first_name": "Jane", "last_name": "Doe",
          "filer_name": "Jane Doe", "filer_type": "Member", "title": "PTR", "is_amendment": False,
          "date_received": None, "url": "https://example.org/r/1"}
CongressReports(Database(connection), "house").add([report])
sql, params = connection.statements[-1]
check("a report without a chamber is added as the repository's chamber's", "house" in params and "senate" not in params,
      params)
CongressReports(Database(connection), "senate").add([dict(report, chamber="house")])
check("a report that names its chamber keeps it", "house" in connection.statements[-1][1], connection.statements[-1][1])
sys.exit(check.finish())
