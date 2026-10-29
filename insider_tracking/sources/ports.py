"""
ports.py - what the collectors need from the outside world, as small interfaces.

A collector's steps are written against these, not against the database or the web: each
source's start() hands them the real things (db/ repositories, a site gateway on the polite
web client), and a test can hand them stand-ins. The db/ repositories fit these shapes.
"""

from typing import Callable, Protocol


class Bookmarks(Protocol):
    """Where a collector keeps its place between runs (collector_state)."""

    def get(self, key) -> str | None: ...

    def set(self, key, value) -> None: ...


class ReportStore(Protocol):
    """Senate and House trade reports (reports, transactions, report_pages)."""

    def counts(self, chamber="senate") -> dict: ...

    def add(self, reports) -> list: ...

    def to_read(self, limit, chamber="senate") -> list: ...

    def save_read(self, report_path, transactions, page, note=None, is_amendment=None) -> None: ...

    def mark_failed(self, report_path, reason, page=None) -> None: ...

    def mark_scanned(self, report_path, reason) -> None: ...


class InsiderStore(Protocol):
    """SEC Form 4 filings and their buys and sells."""

    def save(self, filings, trades, source) -> int: ...

    def remove_entities(self, pattern) -> int: ...

    def counts(self) -> dict: ...


class MarStore(Protocol):
    """European registers' transaction lines."""

    def save(self, rows, source) -> tuple: ...

    def without_eur(self, country) -> list: ...

    def set_eur(self, country, values) -> None: ...

    def counts(self, country) -> dict: ...


class RateStore(Protocol):
    """The ECB's euro exchange rates."""

    def save(self, rows) -> int: ...

    def load(self) -> list: ...


class ReturnStore(Protocol):
    """Worked-out returns, and the trades that need them."""

    def items(self) -> list: ...

    def stored(self) -> dict: ...

    def save(self, rows) -> int: ...

    def counts(self) -> dict: ...


class SizeGauge(Protocol):
    """The database's size, for the end of a run's summary."""

    def whole_database_note(self) -> str: ...


Note = Callable[[str], None]          # prints a progress line within a step
