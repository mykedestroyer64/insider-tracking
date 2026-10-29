"""
The SEC collector's daily lists (sources/sec/daily.py) with in-memory stand-ins for EDGAR
and the database, and a fixed moment: weekends and holidays, today's list (read but not
final), the request budget running out in the middle of a day, and filings that can't be read.
The filings are real Form 4s (tests/fixtures/sec).
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from insider_tracking.core.errors import OutOfRequests, SiteStop
from insider_tracking.core.status import CHANGED
from insider_tracking.sources.sec.daily import DailyLists

from .support.checks import Checks

check = Checks()
SAMPLES = Path(__file__).resolve().parent / "fixtures" / "sec"
FORM4 = {p.stem: p.read_text(encoding="utf-8", errors="replace") for p in sorted(SAMPLES.glob("*.txt"))}
ACCESSIONS = list(FORM4)


def daily_index(day, accessions):
    lines = ["Description: Daily Index", "", "Form Type   Company Name    CIK   Date Filed  File Name", "-" * 80]
    lines += [f"4           SOME COMPANY INC                                              1234567     {day:%Y%m%d}    "
              f"edgar/data/1234567/{acc}.txt" for acc in accessions]
    return "\n".join(lines)


class Edgar:
    """The daily lists of some days, and their filings; `requests_left` like a run's budget."""

    def __init__(self, days, requests_left=1000, broken=()):
        self.days = days                     # date -> accession numbers
        self.requests_left = requests_left
        self.broken = set(broken)            # accessions whose filing can't be read
        self.asked = []

    def _request(self, what):
        if self.requests_left == 0:
            raise OutOfRequests()
        self.requests_left -= 1
        self.asked.append(what)

    def listed_days(self, year, quarter):
        self._request(f"listing {year}q{quarter}")
        return {f"{day:%Y%m%d}" for day in self.days if day.year == year and (day.month - 1) // 3 + 1 == quarter}

    def daily_index(self, year, quarter, day):
        self._request(f"list {day}")
        return SimpleNamespace(status_code=200, text=daily_index(day, self.days[day]))

    def filing(self, path):
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        self._request(f"filing {accession}")
        text = "<html>not a filing</html>" if accession in self.broken else FORM4[accession]
        return SimpleNamespace(status_code=200, text=text)


class Filings:
    def __init__(self):
        self.saved = []

    def save(self, filings, trades, source):
        self.saved.append((filings[0]["accession"], len(trades), source))
        return 1


class Bookmarks(dict):
    def get(self, key):
        return super().get(key)

    def set(self, key, value):
        self[key] = value


def daily(edgar, bookmarks=None, now=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)):
    notes, stats = [], {"filings_saved": 0, "trades_saved": 0, "filings_fetched": 0, "suspect": 0}
    lists = DailyLists(edgar, Filings(), bookmarks if bookmarks is not None else Bookmarks(), stats, notes.append, now)
    return lists, stats, notes


check.section("a normal week: Thursday to Tuesday (today)")
week = {date(2026, 10, 1): ACCESSIONS[:2], date(2026, 10, 2): ACCESSIONS[2:3],
        date(2026, 10, 5): ACCESSIONS[3:5], date(2026, 10, 6): ACCESSIONS[5:]}
edgar = Edgar(week)
lists, stats, notes = daily(edgar)
detail = lists.read((2026, 3))
print("  ", detail)
bookmarks = lists.bookmarks
check("the days whose list is final are finished: cursor at Monday", bookmarks["sec_daily_cursor"] == "2026-10-05",
      bookmarks)
check("3 days finished, every filing read", detail.startswith("3 days finished (through 2026-10-05); 6 Form 4s read"),
      detail)
check("the weekend is skipped without asking for its lists",
      not any("2026-10-03" in what or "2026-10-04" in what for what in edgar.asked), edgar.asked)
check("the quarter's listing is asked for once", edgar.asked.count("listing 2026q4") == 1, edgar.asked)
seen = json.loads(bookmarks["sec_daily_seen"])
check("today's filings are remembered as read, the day isn't finished",
      seen == {"day": "2026-10-06", "accessions": ACCESSIONS[5:]}, seen)
check("filings with officers' or directors' buys or sells are saved, with the day as source",
      all(source.startswith("daily 2026-10-0") for _, _, source in lists.filings.saved) and stats["filings_saved"]
      == len(lists.filings.saved), lists.filings.saved)

check.section("the next run, the same day")
edgar = Edgar(week)
lists, stats, notes = daily(edgar, bookmarks)
detail = lists.read((2026, 3))
check("only today's list is read again, and no filing fetched again",
      [what for what in edgar.asked if what.startswith(("list ", "filing"))] == ["list 2026-10-06"], edgar.asked)

check.section("a holiday, and a list not published yet")
days = {date(2026, 10, 9): ACCESSIONS[:1], date(2026, 10, 13): ACCESSIONS[1:2]}     # Monday 12 Oct: EDGAR closed
edgar = Edgar(days)
lists, stats, notes = daily(edgar, Bookmarks(sec_daily_cursor="2026-10-08"),
                            now=datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc))
detail = lists.read((2026, 3))
print("  ", detail)
check("the holiday is named and passed", "10/12: no list (EDGAR closed)" in detail, detail)
check("the holiday's list is never asked for", "list 2026-10-12" not in edgar.asked, edgar.asked)
check("Wednesday's list isn't out yet: the run stops at Tuesday (final 6 hours after the day ends)",
      lists.bookmarks["sec_daily_cursor"] == "2026-10-13" and "list 2026-10-14" not in edgar.asked, lists.bookmarks)

check.section("the request budget runs out in the middle of a day")
edgar = Edgar({date(2026, 10, 1): ACCESSIONS}, requests_left=4)      # the listing, the list, 2 filings
lists, stats, notes = daily(edgar)
detail = lists.read((2026, 3))
print("  ", detail)
seen = json.loads(lists.bookmarks["sec_daily_seen"])
check("the filings read so far are remembered", seen == {"day": "2026-10-01", "accessions": sorted(ACCESSIONS[:2])}, seen)
check("the day isn't finished", "sec_daily_cursor" not in lists.bookmarks, lists.bookmarks)
check("the summary says where it stopped", "10/01: 2 of 6 read, budget used up" in detail
      and "stopped: this run's request budget is used up" in notes, (detail, notes))
check("the filings read are counted", stats["filings_fetched"] == 2, stats)

check.section("filings that can't be read")
edgar = Edgar({date(2026, 10, 1): ACCESSIONS[:5] + ACCESSIONS[5:]}, broken=ACCESSIONS[:5])
lists, stats, notes = daily(edgar)
try:
    lists.read((2026, 3))
    check("5 unreadable filings in a row stop the run", False)
except SiteStop as stop:
    check("5 unreadable filings in a row stop the run: CHANGED", stop.status == CHANGED
          and stop.detail.startswith("5 filings in a row could not be read"), stop.detail)
check("each one is a note", sum(note.startswith("could not read filing") for note in notes) == 5, notes)
edgar = Edgar({date(2026, 10, 1): ACCESSIONS}, broken=ACCESSIONS[:2])
lists, stats, notes = daily(edgar)
detail = lists.read((2026, 3))
check("a few unreadable filings among good ones don't stop it", detail.startswith("1 day finished"), detail)
sys.exit(check.finish())
