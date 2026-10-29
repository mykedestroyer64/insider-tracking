"""
ports.py - what the collectors' steps need from the outside world, as small interfaces.

The steps (each source's run.py) are written against these, never against the database or
the web: each source's collector.py hands them the real things (db/ repositories, a site
gateway on the polite web client, the run's request budget), and a test can hand them
in-memory stand-ins. tests/test_ports.py checks that every real thing fits its interface.

Each interface has only what one kind of client needs, so a step depends on no method it
doesn't use.
"""

from datetime import date
from typing import Callable, Protocol, runtime_checkable

Note = Callable[[str], None]          # prints a progress line within a step


# ------------------------------------------------------------------ the run itself

@runtime_checkable
class Budget(Protocol):
    """The run's request budget (web.pacing.RequestBudget)."""

    sent: int

    @property
    def left(self) -> int | None: ...


@runtime_checkable
class Bookmarks(Protocol):
    """Where a collector keeps its place between runs (collector_state)."""

    def get(self, key) -> str | None: ...

    def set(self, key, value) -> None: ...


@runtime_checkable
class SizeGauge(Protocol):
    """The database's size, for the end of a run's summary."""

    def whole_database_note(self) -> str: ...


# ------------------------------------------------------------------ the stores

@runtime_checkable
class ReportList(Protocol):
    """A chamber's trade reports as listed by its site (reports)."""

    def add(self, reports) -> list: ...

    def counts(self) -> dict: ...


@runtime_checkable
class ReportQueueStore(Protocol):
    """A chamber's electronic reports that haven't been read yet."""

    def to_read(self, limit) -> list: ...

    def counts(self) -> dict: ...


@runtime_checkable
class ReportResults(Protocol):
    """What reading a report found (transactions, report_pages, the report's status)."""

    def save_read(self, report_path, transactions, page, note=None, is_amendment=None) -> None: ...

    def mark_failed(self, report_path, reason, page=None) -> None: ...

    def mark_scanned(self, report_path, reason) -> None: ...


@runtime_checkable
class InsiderStore(Protocol):
    """SEC Form 4 filings and their buys and sells."""

    def save(self, filings, trades, source) -> int: ...

    def remove_entities(self, pattern) -> int: ...

    def counts(self) -> dict: ...


@runtime_checkable
class MarStore(Protocol):
    """European registers' transaction lines."""

    def save(self, rows, source) -> tuple: ...

    def without_eur(self, country) -> list: ...

    def set_eur(self, country, values) -> None: ...

    def counts(self, country) -> dict: ...


@runtime_checkable
class RateStore(Protocol):
    """The ECB's euro exchange rates."""

    def save(self, rows) -> int: ...

    def load(self) -> list: ...


@runtime_checkable
class ReturnStore(Protocol):
    """Worked-out returns, and the trades that need them."""

    def items(self) -> list: ...

    def stored(self) -> dict: ...

    def save(self, rows) -> int: ...

    def counts(self) -> dict: ...


# ------------------------------------------------------------------ the sites
# Each answer ("response") has .status_code, .text, .content, .headers and .url.

@runtime_checkable
class EfdGateway(Protocol):
    """The Senate's eFD (sources/senate/site.py)."""

    @property
    def base_url(self) -> str: ...

    def open(self) -> list: ...

    def search_all(self, date_from, date_to) -> list: ...

    def get_report(self, url): ...


@runtime_checkable
class HouseGateway(Protocol):
    """The House Clerk's site (sources/house/site.py)."""

    def get_list(self, year, etag=None, modified=None) -> tuple: ...

    def get_report(self, url): ...


@runtime_checkable
class SecGateway(Protocol):
    """The SEC's data sets and EDGAR (sources/sec/site.py)."""

    def quarterly_files(self) -> dict: ...

    def quarterly_file(self, url): ...

    def listed_days(self, year, quarter) -> set: ...

    def daily_index(self, year, quarter, day): ...

    def filing(self, path): ...


@runtime_checkable
class RegisterGateway(Protocol):
    """Finansinspektionen's register (sources/sweden/site.py)."""

    def robots_file(self): ...

    def addresses(self) -> list: ...

    def slow_down_to(self, seconds) -> None: ...

    def export(self, start: date, end: date): ...


@runtime_checkable
class RatesGateway(Protocol):
    """The European Central Bank's exchange rates (sources/ecb/site.py)."""

    def history_rows(self) -> list: ...

    def recent_rows(self) -> list: ...


@runtime_checkable
class PriceApi(Protocol):
    """Sharadar's API (sources/sharadar/site.py)."""

    def table_file(self, table) -> str: ...

    def closes(self, table, ticker, start, end) -> str: ...
