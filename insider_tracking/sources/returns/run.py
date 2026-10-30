"""
run.py - the returns collector's steps (the wiring is in collector.py).

  2. get SPY's prices (the S&P 500 fund) and list the trades that need returns: stock trades
     in Congress reports (found by ticker) and company insiders' trades (found by the
     company's SEC number) whose 1-month, 6-month or 1-year period has ended since they
     were last worked out
  3. get Sharadar's list of securities and find the security each trade was in
  4. get each security's prices and work out the returns, saving them as it goes
Prices are never stored or shown, only the returns (Sharadar's licence).

Written against the interfaces in ../ports.py only: it reaches no database and sends no
requests itself (the site gateway does; what it uses from web/ only reads a page it was given).
"""

from datetime import date, timedelta
from typing import Callable

from ...core.errors import OutOfRequests, SiteStop
from ...core.status import CHANGED
from ...core.text import plural
from ...runs.summary import whole_database_note
from ..ports import Budget, Note, PriceApi, ReturnStore, SizeGauge
from .calc import BENCHMARK, PERIODS, PRICE_GAP_DAYS, Series, returns, stopped_day
from .kinds import kind_of, stock_key
from .securities import Universe

RETRY_FAILED_DAYS = 30     # trades whose security wasn't found are tried again after this many days
FIRST_DAY = date(2012, 1, 1)
SAVE_EVERY = 100           # securities between saves
BENCHMARK_FROM = FIRST_DAY - timedelta(days=15)


def securities(count):
    return plural(count, "security", "securities")


def pending_items(items, stored, market_last, today):
    """The trades whose returns are missing, or whose period has ended since they were worked out."""
    pending, seen = [], set()
    for kind, ident, day, hint in items:
        if day is None or day < FIRST_DAY or day > market_last:
            continue
        key = stock_key(kind, ident)
        if (key, day) in seen:
            continue
        seen.add((key, day))
        row = stored.get((key, day))
        if row is None:
            pending.append((key, kind, ident, day, hint))
            continue
        values, note, checked = row[:3], row[3], row[4]
        if note and not note.startswith("stopped"):
            if (today - checked).days >= RETRY_FAILED_DAYS:
                pending.append((key, kind, ident, day, hint))
            continue
        for period, value in zip(PERIODS, values):
            end = day + timedelta(days=period)
            if value is None and end <= market_last and checked < end:
                pending.append((key, kind, ident, day, hint))
                break
    return pending


def result_row(key, day, security, result, checked):
    row = {"stock_key": key, "trade_date": day, "security": security, "note": result.note, "checked_on": checked}
    for period in PERIODS:
        stock, market = result.stock.get(period), result.market.get(period)
        row[f"r{period}"] = None if stock is None else round(stock, 6)
        row[f"m{period}"] = None if market is None else round(market, 6)
    return row


def failure_row(key, day, note, checked):
    return {"stock_key": key, "trade_date": day, "security": None, "note": note, "checked_on": checked,
            **{f"r{p}": None for p in PERIODS}, **{f"m{p}": None for p in PERIODS}}


def match(universe, pending, checked):
    """Group the pending trades by the security they were in; trades with no security get a failure row."""
    groups, failures = {}, []
    for key, kind, ident, day, hint in pending:
        security, why = kind_of(kind).find(universe, ident, day, hint)
        if security is None:
            failures.append(failure_row(key, day, why, checked))
            continue
        group = groups.setdefault((security.table, security.ticker), {"security": security, "items": []})
        group["items"].append((key, day))
    return groups, failures


class ReturnsRun:
    """One run's steps. returns: the worked-out returns and the trades that need them;
    open_api() -> Sharadar's API."""

    def __init__(self, returns: ReturnStore, size: SizeGauge, open_api: Callable[[], PriceApi], budget: Budget,
                 today: date, note: Note):
        self.returns = returns
        self.size = size
        self.open_api = open_api
        self.budget = budget
        self.today = today             # the UTC date when the run started
        self.note = note
        self.api = self.market = self.market_last = None
        self.pending, self.groups = [], {}
        self.stats = {"items": 0, "pending": 0, "unmatched": 0, "securities": 0, "worked_out": 0, "no_prices": 0,
                      "left": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        return f"tables ready, {self.returns.counts()['rows']:,} trade returns stored so far"

    def open(self):
        self.api = self.open_api()

    def steps(self):
        return [self.find_trades, self.find_securities, self.work_out]

    @property
    def requests_sent(self):
        return self.budget.sent if self.api else None

    def budget_used_up(self):
        return f"this run's request budget is used up; {self.stats['left']:,} securities left for the next run"

    # ----------------------------------------------------------- the steps

    def find_trades(self):
        """Step 2: the S&P 500's prices, and the trades whose returns are due."""
        market = Series.from_csv(self.api.closes("funds", BENCHMARK, BENCHMARK_FROM, self.today))
        if len(market) < 100:
            raise SiteStop(CHANGED, f"only {len(market)} prices for {BENCHMARK}")
        self.market, self.market_last = market, market.last_day
        items = self.returns.items()
        self.stats["items"] = len(items)
        self.pending = pending_items(items, self.returns.stored(), self.market_last, self.today)
        self.stats["pending"] = len(self.pending)
        return (f"{len(items):,} trade days with stocks; {len(self.pending):,} need returns "
                f"(market prices up to {self.market_last})")

    def find_securities(self):
        """Step 3: the security each pending trade was in (trades without one are saved as such)."""
        if not self.pending:
            return "nothing to find"
        universe = Universe.from_csv(self.api.table_file("tickers"))
        self.groups, failures = match(universe, self.pending, self.market_last)
        self.stats["unmatched"] = len(failures)
        self.returns.save(failures)
        reasons = {}
        for row in failures:
            reasons[row["note"]] = reasons.get(row["note"], 0) + 1
        return (f"{len(universe.securities):,} securities listed; {securities(len(self.groups))} "
                f"to read; {len(failures):,} trades without one"
                + (" (" + ", ".join(f"{n:,} {why}" for why, n in sorted(reasons.items())) + ")" if reasons else ""))

    def work_out(self):
        """Step 4: one request per security, then the returns of every pending trade in it."""
        if not self.pending:
            return "nothing to work out"
        market, market_last, stats = self.market, self.market_last, self.stats
        ordered = sorted(self.groups.values(), key=lambda g: (-len(g["items"]), g["security"].ticker))
        rows, done = [], 0
        try:
            for group in ordered:
                security, items = group["security"], group["items"]
                days = [day for _, day in items]
                start = min(days) - timedelta(days=PRICE_GAP_DAYS + 3)
                end = min(max(days) + timedelta(days=max(PERIODS) + PRICE_GAP_DAYS), market_last)
                series = Series.from_csv(self.api.closes(security.table, security.ticker, start, end))
                stopped = stopped_day(series, security, market_last) if len(series) else None
                for key, day in items:
                    if not len(series):
                        rows.append(failure_row(key, day, "no prices from Sharadar", market_last))
                        stats["no_prices"] += 1
                        continue
                    result = returns(series, market, day, market_last, stopped)
                    rows.append(result_row(key, day, security.ticker, result, market_last))
                    stats["worked_out"] += 1
                done += 1
                stats["securities"] += 1
                if len(rows) >= SAVE_EVERY * 10 or done % SAVE_EVERY == 0:
                    self.returns.save(rows)
                    rows = []
        except OutOfRequests:
            stats["left"] = len(ordered) - done
            raise
        finally:
            if rows:
                self.returns.save(rows)
        return f"{securities(done)}, {plural(stats['worked_out'], 'trade')} worked out"

    # ------------------------------------------------------- the summing up

    def read_totals(self):
        return self.returns.counts()

    def outcome(self, totals):
        stats = self.stats
        if not stats["pending"]:
            return "- nothing new, every return is up to date"
        return (f"- returns worked out for {plural(stats['worked_out'], 'trade day')} in {securities(stats['securities'])}"
                + (f"; {stats['left']:,} securities left for the next run" if stats["left"] else ""))

    def closing_lines(self, totals):
        if not totals:
            return []
        return [f"Database now: {totals['rows']:,} trade days with returns ({totals['r30']:,} with 1 month, "
                f"{totals['r182']:,} with 6 months, {totals['r365']:,} with 1 year; {totals['stopped']:,} stopped "
                f"trading early; {totals['failed']:,} without a security or prices); table uses {totals['size']}"
                + whole_database_note(self.size)]
