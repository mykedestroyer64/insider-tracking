"""
run.py - the Swedish executives collector's steps (the wiring is in collector.py).

  2. update the euro exchange rates from the European Central Bank, so every trade in
     kronor (or dollars, or pounds) gets a euro figure next to it
  3. read the register site's robots.txt, the rules it gives to scripts
  4. export the notifications published in the last 45 days from the register (one
     request) and save the buys and sells of shares in them
  5. load the history, twelve months per run, back to July 2016 when the register started
Lines seen before are updated, never duplicated, so the windows can overlap and a
notification that is later revised or cancelled is marked as such.

Written against the interfaces in ../ports.py only: no database or web code in here.
"""

from datetime import date, timedelta
from typing import Callable

from ...core.errors import SiteStop
from ...core.status import BLOCKED, CHANGED
from ...core.text import plural
from ...runs.summary import whole_database_note
from ...web.robots import read_rules
from ..ecb.rates import Rates
from ..ports import Bookmarks, Budget, MarStore, Note, RateStore, RatesGateway, RegisterGateway, SizeGauge
from .parsing import COUNTRY, ExportProblem, parse_export

EXPORT_CAP = 1000          # the register's export stops at this many lines: longer windows are split
HISTORY_VERSION = "2"      # bump to make the next run load the history again (v1 lost lines to the cap)
RECENT_DAYS = 45           # the window re-exported on every run (catches revisions and cancellations)
MONTHS_PER_RUN = 12        # history months loaded per run
FIRST_DAY = date(2016, 7, 3)   # the register starts with the Market Abuse Regulation
RATES_STALE_AFTER = 80     # days: with older rates than this the whole history file is fetched again
RATES_FROM = date(2015, 1, 1)   # rates older than this aren't kept


def as_date(value):
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value


def last_business_day(today):
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


class SwedenRun:
    """One run's steps. trades: the register's lines; fx: the euro rates; open_sites() ->
    (the register, the ECB), which share one client (one budget, one pace)."""

    def __init__(self, trades: MarStore, fx: RateStore, bookmarks: Bookmarks, size: SizeGauge,
                 open_sites: Callable[[], tuple[RegisterGateway, RatesGateway]], budget: Budget, today: date,
                 computer_today: date, note: Note):
        self.trades = trades
        self.fx = fx
        self.bookmarks = bookmarks
        self.size = size
        self.open_sites = open_sites
        self.budget = budget
        self.today = today                      # today in Stockholm, when the run started
        self.computer_today = computer_today    # the computer's date (when nothing is loaded yet)
        self.note = note
        self.register = self.ecb = self.rates = None
        self.stats = {"lines": 0, "kept": 0, "new": 0, "updated": 0, "suspect": 0, "skipped_nature": 0,
                      "skipped_instrument": 0, "skipped_entities": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        return f"tables ready, {self.trades.counts(COUNTRY)['trades']:,} Swedish trades stored so far"

    def open(self):
        self.register, self.ecb = self.open_sites()

    def steps(self):
        return [self.update_rates, self.check_rules, self.export_recent, self.load_history]

    @property
    def requests_sent(self):
        return self.budget.sent if self.register else None

    def budget_used_up(self):
        return "this run's request budget is used up"

    # ----------------------------------------------------------- the steps

    def update_rates(self):
        """Step 2: make sure the euro rates cover every trade day."""
        today = self.today
        rates = Rates(self.fx.load())
        latest = rates.latest_day()
        fetched = 0
        if latest is None or latest < today - timedelta(days=RATES_STALE_AFTER):
            rows = [row for row in self.ecb.history_rows() if row[0] >= RATES_FROM]   # the registers start in 2016
            fetched = self.fx.save(rows)
            what = f"loaded the whole history ({fetched:,} new daily rates)"
        elif latest < last_business_day(today):
            fetched = self.fx.save(self.ecb.recent_rows())
            what = f"{fetched:,} new daily rates"
        else:
            what = "already up to date"
        if fetched:
            rates = Rates(self.fx.load())
        latest = rates.latest_day()
        # euro figures for rows saved while a rate was missing
        filled = {}
        for row in self.trades.without_eur(COUNTRY):
            rate = rates.rate_on(as_date(row["trade_date"]), row["currency"])
            if rate:
                filled[row["row_key"]] = row["value"] / rate
        if filled:
            self.trades.set_eur(COUNTRY, filled)
        self.rates = rates
        detail = f"{what}; newest rate {latest}" if latest else what
        if filled:
            detail += f"; euro values added to {plural(len(filled), 'older row')}"
        return detail

    def check_rules(self):
        """Step 3: the register's robots.txt, the rules it gives to scripts (and its delay)."""
        response = self.register.robots_file()
        if response is None:
            return "no robots.txt (no rules given)"
        rules = read_rules(response.text, self.register.addresses())
        if not rules.allows_all:
            raise SiteStop(BLOCKED, "robots.txt says scripts may not read the register", response)
        self.register.slow_down_to(rules.delay)
        return "robots.txt allows the register" + (f", asks for {rules.delay} s between requests" if rules.delay else "")

    def save_export(self, start, end, label):
        """Export one window of the register and save it. Returns (rows kept, new, updated, lines).

        The register's export never holds more than EXPORT_CAP lines, so a window that comes
        back full is split in two and both halves are exported again."""
        response = self.register.export(start, end)
        try:
            rows, counts = parse_export(response.content, self.rates.rate_on)
        except ExportProblem as problem:
            raise SiteStop(CHANGED, f"the export for {start}..{end} could not be read: {problem}", response) from None
        if counts["lines"] >= EXPORT_CAP and end > start:
            middle = start + (end - start) // 2
            first = self.save_export(start, middle, label)
            second = self.save_export(middle + timedelta(days=1), end, label)
            return tuple(a + b for a, b in zip(first, second))
        if counts["lines"] >= EXPORT_CAP:
            self.note(f"{start}: {counts['lines']:,} lines on one day, the export's limit; some may be missing")
        new, updated = self.trades.save(rows, source=f"{label} {start}..{end}")
        stats = self.stats
        stats["lines"] += counts["lines"]
        stats["kept"] += len(rows)
        stats["new"] += new
        stats["updated"] += updated
        stats["suspect"] += counts["suspect"]
        stats["skipped_nature"] += counts["other_nature"]
        stats["skipped_instrument"] += counts["other_instrument"]
        stats["skipped_entities"] += counts["entities"]
        return len(rows), new, updated, counts["lines"]

    def export_recent(self):
        """Step 4: the last RECENT_DAYS days of publications (one request)."""
        today = self.today
        start = today - timedelta(days=RECENT_DAYS)
        kept, new, updated, lines = self.save_export(start, today, "recent")
        if self.bookmarks.get("se_history_cursor") is None:
            self.bookmarks.set("se_history_cursor", start.isoformat())   # history continues from here
        self.bookmarks.set("se_recent_through", today.isoformat())
        return (f"{lines:,} lines published {start} to {today}; {kept:,} buys or sells of shares kept "
                f"({new:,} new, {updated:,} seen before)")

    def load_history(self):
        """Step 5: months before the loaded range, MONTHS_PER_RUN per run, back to FIRST_DAY."""
        if self.bookmarks.get("se_history_version") != HISTORY_VERSION:
            recent_from = as_date(self.bookmarks.get("se_recent_through")) or self.computer_today
            self.bookmarks.set("se_history_cursor", (recent_from - timedelta(days=RECENT_DAYS)).isoformat())
            self.bookmarks.set("se_history_version", HISTORY_VERSION)
            self.note("the history is loaded again from the start (the way it is read changed)")
        cursor = as_date(self.bookmarks.get("se_history_cursor"))
        if cursor is None or cursor <= FIRST_DAY:
            return f"complete - the register is loaded back to {FIRST_DAY}"
        loaded = []
        for _ in range(MONTHS_PER_RUN):
            if cursor <= FIRST_DAY:
                break
            end = cursor - timedelta(days=1)
            start = max(end.replace(day=1), FIRST_DAY)
            kept, new, updated, lines = self.save_export(start, end, "history")
            loaded.append(f"{start:%Y-%m}: {lines:,} lines, {kept:,} kept ({new:,} new)")
            cursor = start
            self.bookmarks.set("se_history_cursor", cursor.isoformat())
        for line in loaded:
            self.note(line)
        if cursor <= FIRST_DAY:
            return f"loaded {len(loaded)} months; the history is complete back to {FIRST_DAY}"
        months_left = (cursor.year - FIRST_DAY.year) * 12 + cursor.month - FIRST_DAY.month
        return f"loaded {len(loaded)} months, back to {cursor}; about {months_left} months left"

    # ------------------------------------------------------- the summing up

    def read_totals(self):
        return self.trades.counts(COUNTRY)

    def outcome(self, totals):
        stats = self.stats
        if stats["kept"] == 0 and stats["lines"] == 0:
            return "- nothing new, everything is up to date"
        return (f"- {plural(stats['kept'], 'trade')} saved from {stats['lines']:,} register lines "
                f"({stats['new']:,} new, {stats['updated']:,} seen before)")

    def closing_lines(self, totals):
        stats, lines = self.stats, []
        if stats["lines"]:
            lines.append(f"Left out this run: {stats['skipped_nature']:,} lines that aren't buys or sells (subscriptions, "
                         f"option exercises, gifts...), {stats['skipped_instrument']:,} in other instruments (warrants, "
                         f"bonds...), {stats['skipped_entities']:,} reported for companies or funds rather than people")
        if totals:
            first, last = totals["first_published"], totals["last_published"]
            span = f"published {str(first)[:10]} to {str(last)[:10]}" if first and last else "nothing published yet"
            lines.append(f"Database now: {totals['trades']:,} Swedish trades ({totals['buys']:,} buys, "
                         f"{totals['sells']:,} sells, {totals['suspect']:,} suspect, {totals['not_current']:,} revised "
                         f"or cancelled) by {totals['people']:,} people at {totals['companies']:,} companies, {span}; "
                         f"table uses {totals['size']}" + whole_database_note(self.size))
        return lines
