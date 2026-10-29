"""
securities.py - Sharadar's list of securities, and which one a disclosed trade was in.

Congress reports name a ticker (which may have changed hands since: the same symbol can
belong to another company years later), Form 4s name the company's SEC number. Both are
matched to the security that had prices around the trade date; when several fit, common
stock and the primary listing win; when it is still unclear, the trade gets no return.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .calc import BENCHMARK, PRICE_GAP_DAYS, parse_day
from .keys import normalise_ticker


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
