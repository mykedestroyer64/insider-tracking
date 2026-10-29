"""
Bringing the tables up to date (db/schema.py): the statements run on an empty database, then
not again while the tables are up to date, and again when a table is missing or the list
changed. With the real database driver, a run that finds the tables up to date doesn't wait
for another collector that is in the middle of saving (the old way locked every table, and
two runs could block each other: "deadlock detected").
"""
import sys
import threading

from insider_tracking.db import schema
from insider_tracking.db.database import Database

from .support.checks import Checks
from .support.database import TestDatabase

DB = TestDatabase("it_schema").fresh()
check = Checks()


def ensure():
    db = Database(DB.connect())
    try:
        return schema.ensure_schema(db)
    finally:
        db.close()


def tables():
    return {row[0] for row in DB.query("select tablename from pg_tables where schemaname = 'public'")}


check.section("an empty database, then again")
check("the statements run on an empty database", ensure() is True)
check("every table is there", set(schema.TABLES) <= tables(), sorted(tables()))
check("the version is kept as a bookmark",
      DB.value("select value from collector_state where key = 'schema_version'") == schema.VERSION)
check("the next run finds the tables up to date and runs nothing", ensure() is False)

check.section("out of date")
DB.query("drop table fx_rates")
check("a missing table: the statements run again", ensure() is True and "fx_rates" in tables())
DB.query("update collector_state set value = 'older' where key = 'schema_version'")
check("another version of the list: the statements run again", ensure() is True)
check("and the bookmark has this version again",
      DB.value("select value from collector_state where key = 'schema_version'") == schema.VERSION)
check("bookmarks of the collectors are left alone",
      DB.value("select count(*) from collector_state where key <> 'schema_version'") == "0")

check.section("while another collector is saving")
if DB.driver != "pg8000":
    print("  (skipped: needs real transactions, i.e. the pg8000 driver)")
else:
    saving = DB.connect()                      # another collector, in the middle of saving
    cursor = saving.cursor()
    cursor.execute("insert into collector_state (key, value) values ('house_test', 'x')")
    cursor.execute("insert into fx_rates (day, currency, rate) values ('2026-10-01', 'SEK', 11.1)")
    result = {}
    worker = threading.Thread(target=lambda: result.update(ran=ensure()), daemon=True)
    worker.start()
    worker.join(timeout=10)
    check("a run that finds the tables up to date doesn't wait for it", not worker.is_alive() and result.get("ran") is False,
          result)
    saving.rollback()
    saving.close()

    # two runs that both find the tables out of date take turns instead of blocking each other
    DB.query("update collector_state set value = 'older' where key = 'schema_version'")
    outcomes, errors = [], []

    def run_one():
        try:
            outcomes.append(ensure())
        except Exception as error:          # noqa: BLE001 - a deadlock comes back as an error
            errors.append(str(error))

    starting = [threading.Thread(target=run_one, daemon=True) for _ in range(3)]
    for thread in starting:
        thread.start()
    for thread in starting:
        thread.join(timeout=60)
    check("three runs at once: one brings the tables up to date, the others wait their turn and skip",
          not errors and sorted(outcomes) == [False, False, True], (outcomes, errors))
sys.exit(check.finish())
