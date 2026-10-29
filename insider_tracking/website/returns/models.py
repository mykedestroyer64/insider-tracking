"""models.py - how one trade's stock did afterwards (Perf), and the periods the pages show."""

from dataclasses import dataclass, field

from ...sources.returns.calc import PERIODS   # the same periods the returns collector works out

LABELS = {30: "1 month", 182: "6 months", 365: "1 year"}
SHORT = {30: "1 mo", 182: "6 mo", 365: "1 yr"}


@dataclass
class Perf:
    security: str
    stock: dict = field(default_factory=dict)      # {30: 0.021, 182: None, 365: None}
    market: dict = field(default_factory=dict)
    note: str = ""

    def has(self, period):
        return self.stock.get(period) is not None and self.market.get(period) is not None

    @property
    def best(self):
        """The longest period that has ended: (period, stock return, S&P 500 return), or None."""
        for period in reversed(PERIODS):
            if self.has(period):
                return period, self.stock[period], self.market[period]
        return None

    @property
    def stopped(self):
        return self.note.startswith("stopped")
