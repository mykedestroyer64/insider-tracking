"""
clock.py - where "now" and "today" come from.

Code that needs the time asks a Clock instead of the computer directly, so tests can run a
collector "on" any day (a holiday, a weekend, the day after a quarter ends).
"""

from datetime import date, datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware, in UTC."""

    def today(self) -> date:
        """Today's date on the computer that runs the code (UTC on GitHub's servers)."""


class SystemClock:
    """The computer's own clock."""

    def now(self):
        return datetime.now(timezone.utc)

    def today(self):
        return date.today()
