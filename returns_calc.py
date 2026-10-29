"""
returns_calc.py - works out how a stock did after a trade, compared with the S&P 500.

For every trade the site shows the stock's return 1 month, 6 months and 1 year after
the trade date (30, 182 and 365 days), next to the return of SPY (an S&P 500 fund)
over the same days. Prices are Sharadar's closing prices adjusted for splits,
dividends and spinoffs ("closeadj"), so a return includes the dividends paid.

  return = price on (trade date + 30 days) / price on the trade date - 1

When a day has no price (a weekend or holiday), the last close before it is used.
A period that hasn't ended yet stays empty until it has. A stock that stopped
trading before the end of a period (bought out, or delisted) gets the return up to
its last price, compared with the S&P 500 up to the same day, and the result says so.

This module only does the arithmetic and the matching of tickers to Sharadar's
securities; collect_returns.py does the downloading and saving. Prices are never
stored: only the returns are.
"""

import bisect
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

PERIODS = (30, 182, 365)            # days after the trade
PRICE_GAP_DAYS = 7                  # how far back to look for the last close before a day
STOPPED_AFTER_DAYS = 10             # a stock with no price for this long before the market's last day stopped trading
BENCHMARK = "SPY"

CONGRESS_TYPES = ("Stock", "Exchange Traded Fund", "Exchange Traded Note", "REIT")


def parse_day(text):
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def normalise_ticker(ticker):
    """'BRK/B', 'BRK-B' and 'brk.b' -> 'BRK.B' (Sharadar's spelling)."""
    return re.sub(r"[/\-]", ".", (ticker or "").strip().upper())


def stock_key(source, ident):
    """How a trade's stock is named in the trade_returns table (the site uses the same names):
    T:<ticker> for a ticker in a Congress report, C:<CIK> for a company in an SEC Form 4."""
    if source == "congress":
        return "T:" + normalise_ticker(ident)
    return "C:" + (str(ident or "").lstrip("0") or "0")


# ------------------------------------------------------------------ securities

@dataclass
class Security:
    permaticker: int
    ticker: str
    table: str                  # "stocks" or "funds"
    name: str
    category: str
    delisted: bool
    first: date | None          # first and last day with a price
    last: date | None
    cik: str | None             # SEC company number, without leading zeros
    related: tuple = ()         # other tickers it traded under

    def covers(self, day):
        """True when the security had prices around that day."""
        return bool(self.first and day >= self.first and (self.last is None or day <= self.last + timedelta(days=PRICE_GAP_DAYS)))


@dataclass
class Universe:
    """Sharadar's list of securities, with lookups by ticker, former ticker and SEC company number."""
    securities: list = field(default_factory=list)

    def __post_init__(self):
        self.by_ticker, self.by_related, self.by_cik = {}, {}, {}
        for security in self.securities:
            self.by_ticker.setdefault(security.ticker, []).append(security)
            for other in security.related:
                self.by_related.setdefault(other, []).append(security)
            if security.cik:
                self.by_cik.setdefault(security.cik, []).append(security)

    @classmethod
    def from_csv(cls, text):
        """Sharadar's tickers table (CSV) -> Universe of its stocks and funds."""
        reader = csv.DictReader(io.StringIO(text))
        seen, securities = set(), []
        for row in reader:
            table = (row.get("table") or "").strip().lower()
            if table in ("sep",):
                table = "stocks"
            if table in ("sfp",):
                table = "funds"
            if table not in ("stocks", "funds"):
                continue
            try:
                permaticker = int(float(row.get("permaticker") or 0))
            except ValueError:
                continue
            if (table, permaticker) in seen or not row.get("ticker"):
                continue
            seen.add((table, permaticker))
            cik = None
            match = re.search(r"CIK=(\d+)", row.get("secfilings") or "")
            if match:
                cik = match.group(1).lstrip("0") or None
            related = tuple(normalise_ticker(t) for t in re.split(r"[\s,;]+", row.get("relatedtickers") or "") if t.strip())
            securities.append(Security(
                permaticker=permaticker, ticker=normalise_ticker(row["ticker"]), table=table,
                name=(row.get("name") or "").strip(), category=(row.get("category") or "").strip(),
                delisted=(row.get("isdelisted") or "").strip().upper() == "Y",
                first=parse_day(row.get("firstpricedate")), last=parse_day(row.get("lastpricedate")),
                cik=cik, related=related))
        return cls(securities)

    # ---- matching

    def for_ticker(self, ticker, day):
        """The security a disclosed ticker meant on the trade day: (security, None) or (None, reason)."""
        ticker = normalise_ticker(ticker)
        candidates = _unique(self.by_ticker.get(ticker, []) + self.by_related.get(ticker, []))
        return _choose(candidates, day, ticker)

    def for_company(self, cik, day, ticker=None):
        """The stock of an SEC filer (by its company number) on the trade day."""
        candidates = [s for s in self.by_cik.get((cik or "").lstrip("0"), []) if s.table == "stocks"]
        return _choose(candidates, day, normalise_ticker(ticker) if ticker else None)

    def benchmark(self):
        for security in self.by_ticker.get(BENCHMARK, []):
            if security.table == "funds":
                return security
        return None


def _unique(securities):
    seen, out = set(), []
    for security in securities:
        key = (security.table, security.permaticker)
        if key not in seen:
            seen.add(key)
            out.append(security)
    return out


def _choose(candidates, day, hint):
    if not candidates:
        return None, "no match"
    live = [s for s in candidates if s.covers(day)]
    if not live:
        return None, "no prices around the trade date"
    for keep in (lambda s: s.ticker == hint,
                 lambda s: hint in s.related,
                 lambda s: "common" in s.category.lower() and not any(
                     word in s.category.lower() for word in ("warrant", "preferred", "unit", "right")),
                 lambda s: "primary" in s.category.lower(),
                 lambda s: s.table == "stocks"):
        if len(live) > 1:
            narrowed = [s for s in live if keep(s)]
            if narrowed:
                live = narrowed
    if len(live) == 1:
        return live[0], None
    return None, "ambiguous"


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
