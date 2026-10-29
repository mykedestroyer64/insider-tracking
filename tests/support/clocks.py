"""clocks.py - a clock the test sets, so a run can happen "on" any day."""

from datetime import timezone


class TestClock:
    """Says whatever time `when` is (timezone-aware, UTC); a test may move it between runs."""

    def __init__(self, when):
        self.when = when

    def now(self):
        return self.when

    def today(self):
        return self.when.astimezone(timezone.utc).date()
