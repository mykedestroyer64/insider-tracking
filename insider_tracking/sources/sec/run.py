"""
run.py - the SEC collector's steps (the wiring is in collector.py).

  2. load the SEC's quarterly data files, newest first, two per run, back to 2021
     (quarterly.py)
  3. read EDGAR's daily lists of filings for the days after the newest quarterly file,
     download each new Form 4 and keep the same kind of trades (daily.py)
Filings seen before are updated, never duplicated, so the two sources can overlap.

Written against the interfaces in ../ports.py only: it reaches no database and sends no
requests itself (the site gateway does; what it uses from web/ only reads a page it was given).
"""

from datetime import datetime
from typing import Callable

from ...core.text import plural
from ...runs.summary import whole_database_note
from ..ports import Bookmarks, Budget, InsiderStore, Note, SecGateway, SizeGauge
from .daily import DailyLists
from .names import ENTITY
from .quarterly import QuarterlyFiles


class SecRun:
    """One run's steps. filings: the Form 4 filings and their trades; open_site() -> the SEC's
    sites; first_quarter: (year, quarter), how far back the quarterly files are loaded."""

    def __init__(self, filings: InsiderStore, bookmarks: Bookmarks, size: SizeGauge, open_site: Callable[[], SecGateway],
                 budget: Budget, first_quarter: tuple, now: datetime, note: Note):
        self.filings = filings
        self.bookmarks = bookmarks
        self.size = size
        self.open_site = open_site
        self.budget = budget
        self.first_quarter = first_quarter
        self.now = now                 # when the run started: decides which daily lists are final
        self.note = note
        self.site = None
        self.files = None
        self.stats = {"filings_saved": 0, "trades_saved": 0, "filings_fetched": 0, "suspect": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        removed = self.filings.remove_entities(ENTITY)
        before = self.filings.counts()
        return (f"tables ready, {before['trades']:,} insider trades stored so far"
                + (f" ({removed:,} rows by funds or companies removed)" if removed else ""))

    def open(self):
        self.site = self.open_site()

    def steps(self):
        return [self.load_quarters, self.read_daily]

    @property
    def requests_sent(self):
        return self.budget.sent if self.site else None

    def budget_used_up(self):
        return "this run's request budget is used up"

    # ----------------------------------------------------------- the steps

    def load_quarters(self):
        """Step 2: the quarterly files not loaded yet, newest first (quarterly.py)."""
        self.files = self.site.quarterly_files()
        loader = QuarterlyFiles(self.site, self.filings, self.bookmarks, self.stats, self.note, self.first_quarter)
        return loader.load(self.files)

    def read_daily(self):
        """Step 3: new filings from EDGAR's daily lists, for the days after the newest quarterly
        file (daily.py)."""
        lists = DailyLists(self.site, self.filings, self.bookmarks, self.stats, self.note, self.now)
        return lists.read(max(self.files))

    # ------------------------------------------------------- the summing up

    def read_totals(self):
        return self.filings.counts()

    def outcome(self, totals):
        stats = self.stats
        if stats["filings_saved"] == 0 and stats["filings_fetched"] == 0:
            return "- nothing new, everything is up to date"
        outcome = (f"- {plural(stats['trades_saved'], 'trade')} saved from "
                   f"{plural(stats['filings_saved'], 'filing')}")
        if stats["filings_fetched"]:
            outcome += f", {stats['filings_fetched']:,} new filings read"
        return outcome

    def closing_lines(self, totals):
        if not totals:
            return []
        return [f"Database now: {totals['trades']:,} insider trades ({totals['buys']:,} buys, {totals['sells']:,} sells, "
                f"{totals['suspect']:,} suspect) from {totals['filings']:,} filings by {totals['insiders']:,} insiders, "
                f"filed {totals['first_filing']} to {totals['last_filing']}; tables use {totals['size']}"
                + whole_database_note(self.size)]
