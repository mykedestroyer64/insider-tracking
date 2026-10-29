# Test fixtures

Saved files the tests serve from their stand-in sites, or load into the test database.

| Folder or file | What it is | Used by |
|---|---|---|
| `house/2026FD-sample.xml` | The House's list of 2026 reports, cut down to the reports in `house/pdfs` and the first ten candidate reports | `test_house.py` |
| `house/pdfs/` | Real House trade reports (PDF), saved by the House access check | `test_house.py` |
| `house_check/` | Three small made-up House reports: one with two trades, one with one trade, one scanned (no text) | `test_house_check.py` |
| `sec/` | Real SEC Form 4 filings, saved by the SEC access check | `test_sec.py` |
| `sweden/recent_examples.csv` | Real rows of Finansinspektionen's register, saved by the Sweden access check | `test_sweden.py` |
| `site_database.sql.gz` | A filled test database (pg_dump): made-up Senate reports from the stand-in eFD, the House, SEC and Swedish samples above, and made-up returns. Made on 29 Oct 2026 | `test_site.py`, `test_db_tools.py` |

The reports, filings and register rows are public records. Nothing here is a password, a key or
a price from Sharadar.

`test_site.py` brings the saved database's tables up to date before building, so it keeps working
when a column is added. If the site's totals change on purpose, update the numbers in
`test_site.py`.
