"""
The website build (python -m insider_tracking site build) from a filled test database
(tests/fixtures/site_database.sql.gz: made-up Senate reports, real House, SEC and Swedish
samples, made-up returns) with a small list of senators, "on" 29 Oct 2026: every page is
built, the HTML is well formed, every internal link works, and the totals are the expected ones.

With TEST_LEGISLATORS_DIR set to a folder holding legislators-current.json and
legislators-historical.json (the public list), the build uses the real list instead.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from insider_tracking.db.database import Database
from insider_tracking.db.schema import ensure_schema
from insider_tracking.website.build import SiteBuild
from insider_tracking.website.pages import PAGES, PageBuilder
from insider_tracking.website.people.dataset import FixedRecords

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.database import TestDatabase
from .support.platforms import SUPABASE_SETTINGS, test_platform

DB = TestDatabase("it_site").fresh()
DB.restore(Path(__file__).resolve().parent / "fixtures" / "site_database.sql.gz")
db = Database(DB.connect())
ensure_schema(db)          # the saved database gets any table or column added since it was saved
db.close()
OUT = Path(tempfile.mkdtemp()) / "insider-tracking"
check = Checks()


def leg(bioguide, first, last, party, state, start, end, nickname=None, official=None):
    return {"id": {"bioguide": bioguide},
            "name": {"first": first, "last": last, "nickname": nickname, "official_full": official or f"{first} {last}"},
            "terms": [{"type": "sen", "start": start, "end": end, "party": party, "state": state}]}


SENATORS = [
    leg("F000479", "John", "Fetterman", "Democrat", "PA", "2023-01-03", "2029-01-03"),
    leg("B001277", "Richard", "Blumenthal", "Democrat", "CT", "2011-01-05", "2029-01-03"),
    leg("M000355", "Mitch", "McConnell", "Republican", "KY", "1985-01-03", "2027-01-03", official="Mitch McConnell"),
    leg("J000312", "Jim", "Justice", "Republican", "WV", "2025-01-03", "2031-01-03"),
    leg("W000802", "Sheldon", "Whitehouse", "Democrat", "RI", "2007-01-04", "2031-01-03"),
    leg("C001047", "Shelley", "Capito", "Republican", "WV", "2015-01-06", "2027-01-03", official="Shelley Moore Capito"),
    leg("T000278", "Tommy", "Tuberville", "Republican", "AL", "2021-01-03", "2027-01-03"),
    leg("W000779", "Ron", "Wyden", "Democrat", "OR", "1996-02-06", "2029-01-03"),
    leg("T000461", "Patrick", "Toomey", "Republican", "PA", "2011-01-05", "2023-01-03", nickname="Pat",
        official="Patrick J. Toomey"),
    leg("L000594", "Kelly", "Loeffler", "Republican", "GA", "2020-01-06", "2021-01-20"),
    # distractors: an old senator with a matching last name, and a House member (no Senate terms)
    leg("X000001", "Robert", "Justice", "Democrat", "OH", "1961-01-03", "1967-01-03"),
    {"id": {"bioguide": "X000002"}, "name": {"first": "John", "last": "Fetterman"},
     "terms": [{"type": "rep", "start": "2019-01-03", "end": "2021-01-03", "party": "Democrat", "state": "PA"}]},
]
REAL = os.environ.get("TEST_LEGISLATORS_DIR")
if REAL:
    records = []
    for name in ("legislators-current.json", "legislators-historical.json"):
        records += json.loads((Path(REAL) / name).read_text(encoding="utf-8"))
    legislators = FixedRecords(records)
else:
    legislators = FixedRecords(SENATORS)


class HtmlChecker(HTMLParser):
    """Finds unclosed or stray tags and duplicate ids."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
            "path", "circle", "rect", "line"}

    def __init__(self):
        super().__init__()
        self.stack, self.problems, self.ids = [], [], set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id":
                if value in self.ids:
                    self.problems.append(f"duplicate id {value}")
                self.ids.add(value)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.problems.append(f"unclosed <{self.stack.pop()}> before </{tag}>")
            self.stack.pop()
        else:
            self.problems.append(f"stray </{tag}>")


check.section("the build")
platform = test_platform(DB, dict(SUPABASE_SETTINGS), clock=TestClock(datetime(2026, 10, 29, 21, 0, tzinfo=timezone.utc)))
result = run_command(SiteBuild(legislators), ["--out", str(OUT), "--base", "/insider-tracking",
                                              "--site-url", "https://mykedestroyer64.github.io/insider-tracking", "--offline"],
                     platform)
print(result.out)
check("exit code 0", result.code == 0, result.code)
if not REAL:
    check("the database's totals", "1236 reports, 3935 trades, 30 filers; 335 insider trades" in result.out)
    check("the 10 senators matched; the 20 House members aren't in this small list",
          "10 filers matched" in result.out and "20 members of Congress without party/state" in result.out)
    check("returns attached to the trades", "returns: 1,030 stored, 1,264 trades shown with one" in result.out)
    check("277 pages: every trade, filer, stock and insider",
          "277 pages, 3935 trades, 30 filers, 31 stocks, 335 insider trades by 131 insiders at 74 companies "
          "(United States 100, Sweden 235)" in result.out)
check("every page builder is a PageBuilder (build(site))", all(isinstance(page, PageBuilder) for page in PAGES),
      [type(page).__name__ for page in PAGES if not isinstance(page, PageBuilder)])

check.section("the pages")
files = sorted(OUT.rglob("*"))
html_files = [p for p in files if p.suffix == ".html"]
print(len(html_files), "html files;", sum(p.stat().st_size for p in files if p.is_file()) // 1024, "KB in all")
problems = {}
for page in html_files:
    checker = HtmlChecker()
    checker.feed(page.read_text(encoding="utf-8"))
    if checker.stack:
        checker.problems.append(f"unclosed at the end: {checker.stack[-5:]}")
    if checker.problems:
        problems[str(page.relative_to(OUT))] = checker.problems[:3]
check("every page is well-formed HTML", not problems, list(problems.items())[:3])
for name in ("index.html", "trades/index.html", "senators/index.html", "stocks/index.html", "late/index.html",
             "feed.xml", "sitemap.xml", "data/search.json"):
    check(f"{name} is there", (OUT / name).is_file())
data_files = sorted(OUT.glob("data/trades-*.json"))
check("the data files are valid JSON", data_files and all(isinstance(json.loads(p.read_text()), list) for p in data_files),
      [p.name for p in data_files])
members = sorted(p.parent.name for p in (OUT / "congress").glob("*/index.html"))
print("member pages:", members)
check("a page for each member of Congress", len(members) >= 30, len(members))
missing = set()
for page in html_files:
    for href in re.findall(r'href="(/insider-tracking/[^"#?]*)', page.read_text(encoding="utf-8")):
        target = OUT / href[len("/insider-tracking/"):]
        if not (target.exists() or (target / "index.html").exists()):
            missing.add(href)
check("every internal link leads to a page", not missing, sorted(missing)[:10])
sys.exit(check.finish())
