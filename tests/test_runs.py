"""
Each collector's steps (sources/*/run.py) with in-memory stand-ins for the sites and the
database: no web server, no database, no clock. The steps are written against small
interfaces (sources/ports.py), so a dict can be the bookmarks and a list the database.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from insider_tracking.core.errors import OutOfRequests, SiteStop
from insider_tracking.core.status import BLOCKED
from insider_tracking.sources.house.run import HISTORY_START_YEAR, HouseRun
from insider_tracking.sources.returns.run import RETRY_FAILED_DAYS, pending_items
from insider_tracking.sources.sec.run import SecRun
from insider_tracking.sources.senate.run import HISTORY_START, SenateRun
from insider_tracking.sources.sweden.run import SwedenRun

from .sites.sec_files import quarter_zip
from .support.checks import Checks

check = Checks()


class Bookmarks(dict):
    def get(self, key):
        return super().get(key)

    def set(self, key, value):
        self[key] = value


class Budget:
    def __init__(self, left=100):
        self.left, self.sent = left, 0


class Reports:
    """A chamber's reports, in memory: add() keeps the new ones."""

    def __init__(self):
        self.stored = {}

    def add(self, reports):
        new = [r for r in reports if r["report_path"] not in self.stored]
        self.stored.update({r["report_path"]: r for r in new})
        return new

    def counts(self):
        return {"reports": len(self.stored), "read": 0, "to_read": 0, "paper_not_read": 0, "failed": 0,
                "transactions": 0}

    def to_read(self, limit):
        return []


def notes():
    found = []
    return found, found.append


# ------------------------------------------------------------------ Senate
class Efd:
    """eFD with one report a month (on the 15th); search_all() answers for any range of days."""
    base_url = "https://efdsearch.senate.gov"

    def __init__(self, requests_left=1000):
        self.requests_left = requests_left
        self.searches = []

    def open(self):
        return [200, 302, 200]

    def search_all(self, date_from, date_to):
        if self.requests_left == 0:
            raise OutOfRequests()
        self.requests_left -= 1
        self.searches.append((date_from, date_to))
        rows, day = [], date_from.replace(day=15)
        while day < date_to:
            if day >= date_from:
                rows.append(["Jane", "Example", "Example, Jane (Senator)",
                             f'<a href="/search/view/ptr/{day:%Y%m}/">Periodic Transaction Report for {day:%m/%d/%Y}</a>',
                             f"{day:%m/%d/%Y}"])
            day = (day.replace(day=28) + timedelta(days=4)).replace(day=15)
        return rows

    def get_report(self, url):
        raise AssertionError("no report is read in these checks")


check.section("Senate: the recent reports, then the history month by month")
efd, (found, note) = Efd(), notes()
reports, bookmarks = Reports(), Bookmarks()
run = SenateRun(reports, bookmarks, lambda: efd, Budget(), reader=None, today=date(2026, 10, 29), note=note)
run.open()
detail = run.check_recent()
print("  ", detail)
check("the last 60 days are searched (to tomorrow, so today's reports are in)",
      efd.searches == [(date(2026, 8, 30), date(2026, 10, 30))], efd.searches)
check("the reports found are stored, and the step says how many are new",
      len(reports.stored) == 2 and detail == "2 reports received since 08/30/2026, 2 new (2 electronic, 0 paper)",
      (detail, reports.stored.keys()))
check("bookmarks: the day searched, and where the history listing starts",
      bookmarks == {"recent_scan_day": "2026-10-29", "history_listed_back_to": "2026-08-30"}, bookmarks)
detail = run.list_history()
print("  ", detail)
check("24 months listed per run, each from the first of the month",
      len(efd.searches) == 25 and efd.searches[1] == (date(2026, 8, 1), date(2026, 8, 30)), efd.searches[:3])
check("the history bookmark moved back 24 months", bookmarks["history_listed_back_to"] == "2024-09-01", bookmarks)
bookmarks["history_listed_back_to"] = "2012-03-01"
efd.requests_left = 1
detail = run.list_history()
check("the budget running out ends the listing quietly", "stopped listing" in found[-1] and detail.startswith("listed 1 more month"),
      (detail, found))
bookmarks["history_listed_back_to"] = HISTORY_START.isoformat()
check("history complete back to January 2012", run.list_history() == "complete - every report back to 01/2012 is listed")


# ------------------------------------------------------------------ House
class Clerk:
    """The House Clerk's yearly lists: `lists` holds the years that have one; `unchanged` the
    years whose list hasn't changed since the bookmarked ETag."""

    def __init__(self, lists, unchanged=()):
        self.lists, self.unchanged, self.asked = lists, set(unchanged), []

    def get_list(self, year, etag=None, modified=None):
        self.asked.append((year, etag))
        if year in self.unchanged and etag:
            return None, "unchanged"
        if year not in self.lists:
            return None, "missing"
        return SimpleNamespace(content=self.lists[year], headers={"ETag": f'"{year}-v1"'}), "new"

    def get_report(self, url):
        raise AssertionError("no report is read in these checks")


def house_list(year, ids):
    members = "".join(f"<Member><Prefix>Hon.</Prefix><Last>Doe</Last><First>John</First><FilingType>P</FilingType>"
                      f"<StateDst>OH01</StateDst><Year>{year}</Year><FilingDate>3/{n + 1}/{year}</FilingDate>"
                      f"<DocID>{doc}</DocID></Member>" for n, doc in enumerate(ids))
    return f'<?xml version="1.0" encoding="utf-8"?><FinancialDisclosure>{members}</FinancialDisclosure>'.encode()


check.section("House: this year's and last year's lists, then older years")
clerk = Clerk({2026: house_list(2026, ["20030001", "20030002"]), 2025: house_list(2025, ["20020001"]),
               2024: house_list(2024, ["20010001"])}, unchanged={2026})
reports, bookmarks, (found, note) = Reports(), Bookmarks(), notes()
run = HouseRun(reports, bookmarks, lambda: clerk, Budget(), "https://example/{year}/{doc_id}.pdf", reader=None,
               today=date(2026, 10, 29), note=note)
run.open()
detail = run.check_recent()
print("  ", detail)
check("both lists read the first time, their reports stored", len(reports.stored) == 3
      and detail == "2026: 2 trade reports, 2 new (2 electronic, 0 scanned); 2025: 1 trade report, 1 new "
                    "(1 electronic, 0 scanned)", detail)
check("each list's ETag kept, so next time it is only downloaded if it changed",
      bookmarks.get("house_list_etag_2026") == '"2026-v1"' and bookmarks.get("house_list_etag_2025") == '"2025-v1"', bookmarks)
detail = run.check_recent()
check("an unchanged list is not read again", detail.startswith("2026: unchanged"), detail)
detail = run.list_history()
print("  ", detail)
check("three older years per run, missing ones noted",
      detail.startswith("read the lists for 2024, 2023, 2022") and "no list for 2023" in found, (detail, found))
bookmarks["house_history_listed_back_to"] = str(HISTORY_START_YEAR)
check("history complete back to 2013", run.list_history() == "complete - every list back to 2013 is read")


# ------------------------------------------------------------------ SEC
class Sec:
    def __init__(self, quarters):
        self.quarters = quarters          # (year, quarter) -> ZIP bytes
        self.downloads = []

    def quarterly_files(self):
        return {quarter: f"/files/{quarter[0]}q{quarter[1]}.zip" for quarter in self.quarters}

    def quarterly_file(self, url):
        self.downloads.append(url)
        quarter = next(q for q in self.quarters if url.endswith(f"{q[0]}q{q[1]}.zip"))
        return SimpleNamespace(content=self.quarters[quarter], status_code=200, text="", headers={}, url=url)


class Filings:
    def __init__(self):
        self.saved = []

    def save(self, filings, trades, source):
        self.saved.append((source, len(filings), len(trades)))
        return len(filings)

    def counts(self):
        return {"trades": sum(n for _, _, n in self.saved)}

    def remove_entities(self, pattern):
        return 0


check.section("SEC: the quarterly files, two per run, newest first")
sec = Sec({(2026, 3): quarter_zip("Q3", 6, "15-AUG-2026"), (2026, 2): quarter_zip("Q2", 4, "15-MAY-2026"),
           (2021, 1): quarter_zip("Q1", 2, "15-FEB-2021"), (2020, 4): quarter_zip("OLD", 2, "15-NOV-2020")})
filings, bookmarks, (found, note) = Filings(), Bookmarks(), notes()
run = SecRun(filings, bookmarks, size=None, open_site=lambda: sec, budget=Budget(), first_quarter=(2021, 1),
             now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc), note=note)
run.open()
detail = run.load_quarters()
print("  ", detail)
check("the two newest quarters loaded", [source for source, _, _ in filings.saved] == ["2026q3", "2026q2"]
      and detail == "loaded 2; 1 quarterly file left to load", (detail, filings.saved))
check("the quarters done are bookmarked", bookmarks["sec_quarters_done"] == "2026q2,2026q3", bookmarks)
detail = run.load_quarters()
check("then the rest, never before the first quarter wanted (2021 Q1)",
      [source for source, _, _ in filings.saved][-1] == "2021q1" and "/files/2020q4.zip" not in sec.downloads, sec.downloads)
check("and then it says it is complete", run.load_quarters().startswith("complete - every quarterly file from 2021q1"))


# ------------------------------------------------------------------ Sweden
class Register:
    def __init__(self, robots):
        self.robots, self.waits = robots, []

    def robots_file(self):
        return None if self.robots is None else SimpleNamespace(text=self.robots, status_code=200, content=b"",
                                                                headers={}, url="/robots.txt")

    def addresses(self):
        return ["https://register.example/search", "https://register.example/export?from=2026-01-01"]

    def slow_down_to(self, seconds):
        self.waits.append(seconds)


def sweden_run(register):
    run = SwedenRun(trades=None, fx=None, bookmarks=Bookmarks(), size=None, open_sites=lambda: (register, None),
                    budget=Budget(), today=date(2026, 10, 29), computer_today=date(2026, 10, 29), note=print)
    run.open()
    return run


check.section("Sweden: the register's rules for scripts (robots.txt)")
check("no robots.txt: no rules", sweden_run(Register(None)).check_rules() == "no robots.txt (no rules given)")
register = Register("User-agent: *\nDisallow: /private/\nCrawl-delay: 4\n")
detail = sweden_run(register).check_rules()
check("rules that allow the register, with a delay: kept to", detail == "robots.txt allows the register, asks for 4 s "
      "between requests" and register.waits == [4], (detail, register.waits))
try:
    sweden_run(Register("User-agent: *\nDisallow: /\n")).check_rules()
    check("rules that forbid it stop the run", False)
except SiteStop as stop:
    check("rules that forbid it stop the run: BLOCKED", stop.status == BLOCKED, stop.detail)


# ------------------------------------------------------------------ returns
check.section("returns: which trades need their returns worked out")
market_last, today = date(2026, 10, 28), date(2026, 10, 29)
items = [("congress", "AAPL", date(2026, 10, 1), None), ("congress", "AAPL", date(2026, 10, 1), None),
         ("insider", "320193", date(2025, 9, 1), "AAPL"), ("congress", "MSFT", date(2010, 5, 1), None),
         ("congress", "NVDA", date(2026, 10, 29), None), ("congress", "XYZ", date(2025, 1, 1), None)]
stored = {("C:320193", date(2025, 9, 1)): (0.1, 0.2, None, None, date(2026, 8, 1)),      # its year ended since
          ("T:XYZ", date(2025, 1, 1)): (None, None, None, "no prices around the trade date",
                                        today - timedelta(days=RETRY_FAILED_DAYS - 1))}
pending = pending_items(items, stored, market_last, today)
keys = [(key, day) for key, _, _, day, _ in pending]
check("a new trade needs its returns, once", keys.count(("T:AAPL", date(2026, 10, 1))) == 1, keys)
check("a trade whose 1-year period ended since it was worked out is due again", ("C:320193", date(2025, 9, 1)) in keys, keys)
check("trades before 2012 or after the newest market day aren't", ("T:MSFT", date(2010, 5, 1)) not in keys
      and ("T:NVDA", date(2026, 10, 29)) not in keys, keys)
check(f"a trade without a security is tried again only after {RETRY_FAILED_DAYS} days", ("T:XYZ", date(2025, 1, 1)) not in keys
      and ("T:XYZ", date(2025, 1, 1)) in [(k, d) for k, _, _, d, _ in pending_items(
          items, {**stored, ("T:XYZ", date(2025, 1, 1)): (None, None, None, "ambiguous",
                                                         today - timedelta(days=RETRY_FAILED_DAYS))}, market_last, today)],
      keys)
sys.exit(check.finish())
