"""
The website build with a copy of the data kept between builds (python -m insider_tracking site
build --copy FILE, website/datacopy.py): the pages come out exactly as when everything is
downloaded, whatever changed in the database in between, and a build downloads only the parts
that changed. Also: a damaged copy, and a copy made by an older version of a query.
"""
import gzip
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from insider_tracking.db.database import Database
from insider_tracking.db.schema import ensure_schema
from insider_tracking.website.build import SiteBuild
from insider_tracking.website.people.dataset import FixedRecords

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.database import TestDatabase
from .support.platforms import SUPABASE_SETTINGS, test_platform

DB = TestDatabase("it_site_copy").fresh()
DB.restore(Path(__file__).resolve().parent / "fixtures" / "site_database.sql.gz")
db = Database(DB.connect())
ensure_schema(db)
db.close()
WORK = Path(tempfile.mkdtemp())
COPY = WORK / "site-data" / "data.json.gz"
LEGISLATORS = FixedRecords([{"id": {"bioguide": "F000479"}, "name": {"first": "John", "last": "Fetterman"},
                             "terms": [{"type": "sen", "start": "2023-01-03", "end": "2029-01-03",
                                        "party": "Democrat", "state": "PA"}]}])
check = Checks()


def build(name, copy=True):
    """Build the site into WORK/name (with the copy, or reading everything); (output, folder)."""
    platform = test_platform(DB, dict(SUPABASE_SETTINGS), clock=TestClock(datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)))
    out = WORK / name
    argv = ["--out", str(out), "--base", "/insider-tracking", "--offline"] + (["--copy", str(COPY)] if copy else [])
    result = run_command(SiteBuild(LEGISLATORS), argv, platform)
    check(f"{name}: exit code 0", result.code == 0, result.out[-2000:])
    return result.out, out


def differences(first, second):
    """The files that aren't the same in two builds."""
    names = {p.relative_to(first) for p in first.rglob("*") if p.is_file()}
    names |= {p.relative_to(second) for p in second.rglob("*") if p.is_file()}
    return sorted(str(n) for n in names if not ((first / n).is_file() and (second / n).is_file()
                                                and (first / n).read_bytes() == (second / n).read_bytes()))


def line(out, start):
    return next((text.strip() for text in out.splitlines() if text.strip().startswith(start)), "")


check.section("the first build: no copy yet")
out, everything = build("everything", copy=False)
check("without --copy, nothing about a copy", "copy" not in out.lower(), line(out, "downloaded"))
out, first = build("first")
print("  ", line(out, "no copy"), "|", line(out, "downloaded"))
check("it says there is no copy yet", "no copy of the data yet: everything is downloaded" in out)
check("the pages are exactly the ones a build reading everything makes", not differences(everything, first),
      differences(everything, first)[:5])
check("the copy is saved", COPY.is_file())
total = int(line(out, "downloaded").split()[1].replace(",", ""))

check.section("nothing changed")
out, second = build("second")
print("  ", line(out, "downloaded"))
check("only the fingerprints (and the newest-run date) are downloaded; every row comes from the copy",
      line(out, "downloaded").startswith("downloaded 1 row ") and f"{total - 1:,} rows from the last build's copy" in out,
      line(out, "downloaded"))
check("the same pages", not differences(first, second), differences(first, second)[:5])

check.section("the database changed")
DB.psql("-c", """
update insider_trades set price = price * 1.5, value = value * 1.5
 where (accession, row_key) = (select accession, row_key from insider_trades order by accession, row_key limit 1);
delete from insider_trades where (accession, row_key) = (select accession, row_key from insider_trades order by accession desc,
 row_key limit 1);
insert into insider_trades select accession, row_key || 'x', insider_cik, insider_name, insider_title, roles, date '2026-10-30',
 code, kind, shares, price, value, acquired_disposed, shares_after, direct, nature, security, suspect, suspect_reason
 from insider_trades order by accession, row_key limit 1 offset 5;
update insider_filings set company_name = company_name || ' (renamed)'
 where accession = (select accession from insider_filings order by accession limit 1 offset 3);
update mar_trades set status = 'Revised' where row_key = (select row_key from mar_trades order by row_key limit 1 offset 7);
update reports set title = coalesce(title, '') || ' (amended)'
 where report_path = (select report_path from reports order by report_path limit 1 offset 11);
delete from transactions where (report_path, row_number) = (select report_path, row_number from transactions
 order by report_path, row_number limit 1 offset 20);
update fx_rates set rate = rate + 0.01 where currency = 'USD' and day = (select max(day) from fx_rates where currency = 'USD');
delete from trade_returns where (stock_key, trade_date) = (select stock_key, trade_date from trade_returns
 order by stock_key, trade_date limit 1);
""")
out, changed = build("changed")
print("  ", line(out, "downloaded"))
_, reference = build("reference", copy=False)
check("the pages are exactly the ones a build reading everything makes", not differences(reference, changed),
      differences(reference, changed)[:5])
check("and they did change", differences(second, changed))
downloaded = int(line(out, "downloaded").split()[1].replace(",", ""))
check("only the changed parts are downloaded (a small share of the rows)", 1 < downloaded < total / 10,
      line(out, "downloaded"))

check.section("a copy that can't be used")
COPY.write_bytes(b"not a gzip file")
out, damaged = build("damaged")
check("a damaged copy is said so, and everything is downloaded",
      "the copy of the data can't be used" in out and line(out, "downloaded").endswith("; 0 rows from the last build's copy"),
      line(out, "the copy") + " | " + line(out, "downloaded"))
check("the same pages", not differences(reference, damaged), differences(reference, damaged)[:5])
with gzip.open(COPY, "rt", encoding="utf-8") as file:
    data = json.load(file)
data["queries"]["insider_trades"]["definition"] = "an older query"
with gzip.open(COPY, "wt", encoding="utf-8") as file:
    json.dump(data, file)
out, older = build("older")
trades = int(DB.value("select count(*) from insider_trades"))
check("a query that changed since the copy was made is downloaded again, the others aren't",
      int(line(out, "downloaded").split()[1].replace(",", "")) == trades + 1, (line(out, "downloaded"), trades))
check("the same pages", not differences(reference, older), differences(reference, older)[:5])
sys.exit(check.finish())
