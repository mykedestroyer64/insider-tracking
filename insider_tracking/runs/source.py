"""
source.py - the two shapes a data source takes for the runner (runner.py).

A Source is the data source itself: its definition, and how to start a run of it.
A SourceRun is one run of it: the steps after step 1, and the words that sum the run up.
The runner only ever talks to these two, so it never changes when a source is added.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Callable, Protocol, Sequence

if TYPE_CHECKING:
    from ..db.database import Database
    from .console import Console
    from .definition import CollectorDefinition
    from .settings import Settings


@dataclass
class RunContext:
    """What a source gets for one run: the open database and the outside world."""
    db: Database | None     # opened by the runner at step 1
    settings: Settings
    max_requests: int
    started: datetime       # when the run started (UTC)
    today: date             # the computer's date when the run started
    console: Console        # for progress notes
    sleep: Callable         # how the run waits (time.sleep; tests pass one that doesn't)
    new_session: Callable   # (user_agent, compressed=False) -> a web session


class SourceRun(Protocol):
    stats: dict             # this run's counts, kept in the run's record

    def prepare(self) -> str:
        """Step 1, after the tables are ready: tidy what needs tidying; the step's explanation."""

    def open(self) -> None:
        """Create the web clients (right after step 1)."""

    def steps(self) -> Sequence[Callable]:
        """Steps 2, 3, ...: each returns its explanation (str) or a steps.StepResult."""

    @property
    def requests_sent(self) -> int | None:
        """Requests sent so far (None before open())."""

    def budget_used_up(self) -> str:
        """The step's explanation when the run's request budget runs out."""

    def read_totals(self) -> dict | None:
        """The database's totals after the run (may raise DatabaseError)."""

    def outcome(self, totals) -> str:
        """The end of the verdict line for a run that didn't fail, e.g. "- 2 new reports, ..."."""

    def closing_lines(self, totals) -> list:
        """The lines printed before the verdict: "Database now: ...", "History: ..."."""


class Source(Protocol):
    definition: CollectorDefinition

    def start(self, context: RunContext) -> SourceRun:
        """A new run, with its repositories (no web requests yet)."""
