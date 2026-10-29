"""
daily.py - step 3 of the SEC collector: EDGAR's daily lists of filings, day by day, for the
days after the newest quarterly file.

EDGAR publishes no list on weekends and US federal holidays, and asking for a list that
doesn't exist gets "403 Access Denied" from the SEC's storage, which looks just like a
refusal. So a day's list is asked for only when EDGAR's listing of the quarter has it.

A day's list is final 6 hours after the day ends (UTC). Until then it is read but not marked
done, and the filings already read are remembered (sec_daily_seen), so the next run reads
only the new ones. The last day fully done is the bookmark sec_daily_cursor.
"""

import json
from datetime import date, datetime, timedelta

from ...core.errors import OutOfRequests, SiteStop
from ...core.status import CHANGED
from ...core.text import plural
from ...web.checks import unexpected
from ..ports import Bookmarks, InsiderStore, Note, SecGateway
from .parsing import FilingProblem, parse_daily_index, parse_filing
from .quarters import quarter_end, quarter_name, quarter_of

DAY_COMPLETE_AFTER = timedelta(hours=6)   # a day's list is final 6 hours after that day ends (UTC), i.e. after 1 am in Washington
FAILURES_BEFORE_STOP = 5   # unreadable filings in a row before we assume EDGAR changed


class DailyLists:
    """stats: the run's counts; now: when the run started (it decides which lists are final)."""

    def __init__(self, site: SecGateway, filings: InsiderStore, bookmarks: Bookmarks, stats: dict, note: Note,
                 now: datetime):
        self.site = site
        self.filings = filings
        self.bookmarks = bookmarks
        self.stats = stats
        self.note = note
        self.now = now
        self.final_day = (now - DAY_COMPLETE_AFTER).date() - timedelta(days=1)   # the newest day whose list is final
        self.days_done = self.fetched = self.kept = self.failures_in_a_row = 0
        self.progress, self.stopped = [], None          # what happened on each day, for the summary line
        self.listings = {}                              # (year, quarter) -> the days that have a list
        self.seen_state = {}

    def read(self, newest_quarter):
        """Read every list after `newest_quarter` (year, quarter); the step's summary line."""
        first_day = quarter_end(*newest_quarter) + timedelta(days=1)
        cursor = self.bookmarks.get("sec_daily_cursor")        # the last day fully processed
        day = max(first_day, date.fromisoformat(cursor) + timedelta(days=1)) if cursor else first_day
        if day > self.now.date():
            return f"up to date - the daily lists after {quarter_name(*newest_quarter)} are read through {cursor}"
        self.seen_state = json.loads(self.bookmarks.get("sec_daily_seen") or "{}")
        try:
            while day is not None and day <= self.now.date():
                day = self.read_day(day)
        except OutOfRequests:
            self.note("stopped: this run's request budget is used up")
            self.bookmarks.set("sec_daily_seen", json.dumps(self.seen_state))
        self.stats["filings_fetched"] += self.fetched
        cursor = self.bookmarks.get("sec_daily_cursor") or "none yet"
        detail = (f"{plural(self.days_done, 'day')} finished (through {cursor}); {self.fetched:,} Form 4s read, "
                  f"{self.kept:,} with officers' or directors' buys or sells")
        if self.progress or self.stopped:
            detail += " [" + "; ".join(self.progress + ([self.stopped] if self.stopped else [])) + "]"
        return detail

    def done_through(self, day):
        self.bookmarks.set("sec_daily_cursor", day.isoformat())

    def read_day(self, day):
        """One day: returns the next day to look at, or None to stop for this run."""
        if day.weekday() >= 5:                                # EDGAR publishes no lists at weekends
            if day <= self.final_day:
                self.done_through(day)
            return day + timedelta(days=1)
        year, quarter = quarter_of(day)
        if (year, quarter) not in self.listings:
            self.listings[(year, quarter)] = self.site.listed_days(year, quarter)
        if f"{day:%Y%m%d}" not in self.listings[(year, quarter)]:
            if day <= self.final_day:                         # a weekday without a list: a holiday
                self.progress.append(f"{day:%m/%d}: no list (EDGAR closed)")
                self.done_through(day)
                return day + timedelta(days=1)
            return None                                       # not published yet
        response = self.site.daily_index(year, quarter, day)
        if response.status_code == 404:                       # a holiday, or not published yet
            if day <= self.final_day:
                self.done_through(day)
                return day + timedelta(days=1)
            return None
        listed = parse_daily_index(response.text)
        if not listed and "Form Type" not in response.text:
            raise unexpected(response, f"the daily list for {day} could not be read")
        day_fetched, day_kept = self.read_filings(day, listed)
        self.bookmarks.set("sec_daily_seen", json.dumps(self.seen_state))
        self.progress.append(f"{day:%m/%d}: {len(listed):,} listed, {day_fetched:,} read, {day_kept} kept")
        if day <= self.final_day:
            self.done_through(day)
            self.bookmarks.set("sec_daily_seen", "{}")
            self.days_done += 1
            return day + timedelta(days=1)
        return None                                           # today's list isn't final: re-read it next run

    def read_filings(self, day, listed):
        """The day's Form 4s not read yet: (fetched, kept)."""
        seen_state = self.seen_state
        seen = set(seen_state.get("accessions", [])) if seen_state.get("day") == day.isoformat() else set()
        todo = [f for f in listed if f["accession"] not in seen]
        day_fetched, day_kept = 0, 0
        for filing in todo:
            try:
                page = self.site.filing(filing["path"])
            except OutOfRequests:
                self.stopped = f"{day:%m/%d}: {len(seen)} of {len(listed)} read, budget used up"
                raise
            self.fetched += 1
            day_fetched += 1
            if page.status_code == 404:
                self.note(f"filing {filing['accession']} is listed but not available (HTTP 404)")
            elif self.save_filing(page, filing, day):
                self.kept += 1
                day_kept += 1
            seen.add(filing["accession"])
            self.seen_state = {"day": day.isoformat(), "accessions": sorted(seen)}
            if len(seen) % 25 == 0:
                self.bookmarks.set("sec_daily_seen", json.dumps(self.seen_state))
        return day_fetched, day_kept

    def save_filing(self, page, filing, day):
        """Read one filing and save its officers' and directors' buys and sells; True if it had some."""
        try:
            parsed, trades = parse_filing(page.text, filing["accession"], filing["filed"])
        except FilingProblem as problem:
            self.failures_in_a_row += 1
            self.note(f"could not read filing {filing['accession']}: {problem}")
            if self.failures_in_a_row >= FAILURES_BEFORE_STOP:
                raise SiteStop(CHANGED, f"{self.failures_in_a_row} filings in a row could not be "
                                        f"read (last: {problem})", page) from None
            return False
        self.failures_in_a_row = 0
        if not trades:
            return False
        self.filings.save([parsed], trades, source=f"daily {day}")
        self.stats["filings_saved"] += 1
        self.stats["trades_saved"] += len(trades)
        self.stats["suspect"] += sum(1 for t in trades if t["suspect"])
        return True
