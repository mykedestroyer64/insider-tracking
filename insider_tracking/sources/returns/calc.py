"""
calc.py - works out how a stock did after a trade, compared with the S&P 500.

For every trade the site shows the stock's return 1 month, 6 months and 1 year after
the trade date (30, 182 and 365 days), next to the return of SPY (an S&P 500 fund)
over the same days. Prices are Sharadar's closing prices adjusted for splits,
dividends and spinoffs ("closeadj"), so a return includes the dividends paid.

  return = price on (trade date + 30 days) / price on the trade date - 1

When a day has no price (a weekend or holiday), the last close before it is used.
A period that hasn't ended yet stays empty until it has. A stock that stopped
trading before the end of a period (bought out, or delisted) gets the return up to
its last price, compared with the S&P 500 up to the same day, and the result says so.

This module only does the arithmetic (securities.py matches trades to Sharadar's
securities; collector.py does the downloading and saving). Prices are never
stored: only the returns are.
"""

import bisect
import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta

PERIODS = (30, 182, 365)            # days after the trade
PRICE_GAP_DAYS = 7                  # how far back to look for the last close before a day
STOPPED_AFTER_DAYS = 10             # a stock with no price for this long before the market's last day stopped trading
BENCHMARK = "SPY"


def parse_day(text):
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


# ------------------------------------------------------------------ arithmetic

class Series:
    """One security's adjusted closing prices: [(date, closeadj)] sorted by date."""

    def __init__(self, rows):
        rows = sorted((day, price) for day, price in rows if day and price and price > 0)
        self.days = [day for day, _ in rows]
        self.prices = [price for _, price in rows]

    @classmethod
    def from_csv(cls, text):
        """Sharadar's CSV answer with the columns date and closeadj."""
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for row in reader:
            try:
                rows.append((parse_day(row.get("date")), float(row.get("closeadj") or 0)))
            except ValueError:
                continue
        return cls(rows)

    def __len__(self):
        return len(self.days)

    @property
    def last_day(self):
        return self.days[-1] if self.days else None

    def on_or_before(self, day, gap=PRICE_GAP_DAYS):
        """The close on that day, or the last one before it (at most `gap` days before)."""
        index = bisect.bisect_right(self.days, day) - 1
        if index < 0 or (day - self.days[index]).days > gap:
            return None
        return self.prices[index]


@dataclass
class Result:
    stock: dict             # {30: 0.12, 182: None, 365: None}: fractions, None while a period runs
    market: dict            # the same for SPY
    note: str | None = None


def returns(series, market, day, market_last, stopped=None):
    """How the stock and SPY did 30, 182 and 365 days after `day`.

    `market_last` is the market's last day with prices. `stopped` is the day the stock
    stopped trading, if it did; periods ending after it use its last price."""
    base = series.on_or_before(day)
    market_base = market.on_or_before(day)
    if base is None:
        return Result({}, {}, "no price on the trade date")
    stock, bench, note = {}, {}, None
    for period in PERIODS:
        end = day + timedelta(days=period)
        if end > market_last:
            stock[period] = bench[period] = None        # not over yet
            continue
        if stopped and end > stopped:
            # bought out or delisted before the period ended: both returns run to its last price
            price = series.on_or_before(stopped, gap=STOPPED_AFTER_DAYS * 3)
            bench_price = market.on_or_before(stopped)
            note = f"stopped trading {stopped.isoformat()}"
        else:
            price = series.on_or_before(end)
            bench_price = market.on_or_before(end)
        stock[period] = (price / base - 1) if price else None
        bench[period] = (bench_price / market_base - 1) if bench_price and market_base else None
    return Result(stock, bench, note)


def stopped_day(series, security, market_last):
    """The day a stock stopped trading, or None if it still trades."""
    last = series.last_day or security.last
    if last and (security.delisted or (market_last - last).days > STOPPED_AFTER_DAYS):
        return last
    return None
