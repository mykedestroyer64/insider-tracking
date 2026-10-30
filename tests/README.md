# Tests

The tests run every part of the project the way GitHub runs it, but against **stand-ins**: small
local copies of the sites (serving saved or made-up pages) and a real PostgreSQL test database
instead of Supabase. Nothing is sent to the real sites and the real database is never touched.

GitHub runs them by itself on every push and every pull request (the **Tests** workflow, see
`.github/workflows/tests.yml`). A green tick next to a commit means every check passed.

## What is tested

| File | What it runs |
|---|---|
| `test_senate.py` | The Senate collector: a 15-year backfill run after run, expiring sessions, a block, an outage, changed pages, a database failure, a bug |
| `test_house.py` | The House collector: this year's list, the history year by year, a missing PDF, a block |
| `test_sec.py` | The SEC collector: quarterly files, daily lists, a holiday, a refused day, a block |
| `test_sec_parsing.py` | Which SEC filings and trades are kept |
| `test_sweden.py` | The Swedish collector: recent weeks, the history, robots.txt, a refusal, the export's line limit |
| `test_returns.py` | The returns collector: matching trades to securities and working out the returns |
| `test_runs.py`, `test_report_queue.py`, `test_sec_daily.py` | The collectors' steps on their own, with stand-ins for the database and the sites (no web server, no database) |
| `test_ports.py` | Every real repository and site gateway fits the interface the collectors' steps are written against |
| `test_efd_check.py`, `test_house_check.py`, `test_sec_check.py`, `test_sweden_check.py`, `test_netherlands_check.py` | The access checks |
| `test_sharadar_check.py`, `test_probes.py` | The probes that only post notes (Sharadar, EDGAR's daily lists, library versions) |
| `test_schema.py` | Bringing the tables up to date, only when they aren't, without waiting for a collector that is saving |
| `test_db_tools.py` | The database size report and freeing space |
| `test_site.py` | The website build: every page, well-formed HTML, working links, the expected totals |
| `test_workflows.py` | The GitHub workflows: names, schedules, secrets, and their shell steps run for real |

Each file prints one `PASS` or `FAIL` line per check. `run.py` runs them all and sums up.

## Running them on your own computer

You need Python 3.12 or newer, PostgreSQL (with its `psql` program), and Git. Then, in the
project folder:

```
python -m pip install -r requirements.txt -r tests/requirements.txt
```

Tell the tests where your PostgreSQL server is (a user that may create databases):

```
set TEST_DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/postgres      (Windows)
export TEST_DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/postgres   (Mac, Linux)
```

and run:

```
python -m tests.run                 every test file
python -m tests.run house sec       only test_house.py and test_sec.py
python -m tests.test_house          one file, with all its output
```

Each test file makes its own database (named `it_<file>`) and starts it empty every time.

Where the `pg8000` library can't be installed, the tests fall back to `support/psql_driver.py`,
which sends each statement through `psql` (slower, and without real transactions). Set
`TEST_DB_DRIVER=pg8000` or `TEST_DB_DRIVER=psql` to choose.

## How the tests are organised

- `support/` - what every test file uses: the PASS/FAIL lines, the test database, running a
  command and catching what it prints, the settings and clock the code under test gets.
- `sites/` - stand-in sites used by more than one test, and made-up SEC data files.
- `fixtures/` - saved real examples and a filled database (see `fixtures/README.md`).
