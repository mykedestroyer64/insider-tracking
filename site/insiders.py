"""
insiders.py - reads the company-insider tables (SEC Form 4) for the website.

Officers' and directors' open-market buys and sells, grouped by company (ticker)
and by person (the SEC's CIK number), plus the lists the Insiders pages show:
the latest trades, the biggest of the last 30 days, the most active companies.

Rules shared with the Congress data: amendments are shown but not counted, and
rows the collector marked "suspect" (impossible numbers) are shown but not counted.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from data import fetch_all, parse_date, slugify, stock_slug

LATEST = 50
BIGGEST = 20
ACTIVE = 20
ON_STOCK_PAGE = 50          # insider trades shown on a stock page before the "all" link

FILINGS_SQL = """
select accession, form, to_char(filing_date, 'YYYY-MM-DD'), to_char(period, 'YYYY-MM-DD'),
       coalesce(company_cik, ''), coalesce(company_name, ''), coalesce(ticker, ''),
       case when plan_10b5_1 is null then '' else plan_10b5_1::int::text end,
       is_amendment::int::text, url
from insider_filings
"""

TRADES_SQL = """
select accession, row_key, coalesce(insider_cik, ''), coalesce(insider_name, ''), coalesce(insider_title, ''),
       array_to_string(roles, '|'), to_char(trade_date, 'YYYY-MM-DD'), kind, shares::text, price::text,
       value::text, direct::int::text, coalesce(nature, ''), coalesce(security, ''), suspect::int::text,
       coalesce(suspect_reason, '')
from insider_trades
order by trade_date desc nulls last, accession, row_key
"""

HAS_TABLES_SQL = """
select count(*)::text from information_schema.tables where table_name = 'insider_trades'
"""


def to_float(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


@dataclass
class Filing:
    accession: str
    form: str
    filed: date | None
    period: date | None
    company_cik: str
    company_name: str
    ticker: str
    plan: bool | None
    is_amendment: bool
    url: str


@dataclass
class InsiderTrade:
    filing: Filing
    row_key: str
    insider_cik: str
    insider_name: str
    insider_title: str
    roles: list
    traded: date | None
    kind: str                 # buy / sell
    shares: float | None
    price: float | None
    value: float | None
    direct: bool
    nature: str
    security: str
    suspect: bool
    suspect_reason: str
    lots: int = 1             # broker fills combined into this row
    duplicate: bool = False   # the same trade appears in an earlier filing (a joint filer's)

    @property
    def counted(self):
        return not self.filing.is_amendment and not self.suspect and not self.duplicate

    @property
    def label(self):
        return "Buy" if self.kind == "buy" else "Sell"

    @property
    def ticker(self):
        return self.filing.ticker

    @property
    def stock_slug(self):
        return stock_slug(self.filing.ticker)

    @property
    def role_text(self):
        """'CEO' if a title is known, else 'Director' / 'Officer'."""
        return self.insider_title or ", ".join(self.roles) or "Insider"

    @property
    def days_to_file(self):
        if self.traded and self.filing.filed:
            return (self.filing.filed - self.traded).days
        return None


@dataclass
class Company:
    ticker: str
    slug: str
    cik: str
    name: str
    trades: list = field(default_factory=list)

    @property
    def counted(self):
        return [t for t in self.trades if t.counted]

    def stats(self, since=None):
        trades = [t for t in self.counted if since is None or (t.traded and t.traded >= since)]
        return {
            "trades": len(trades),
            "buys": sum(1 for t in trades if t.kind == "buy"),
            "sells": sum(1 for t in trades if t.kind == "sell"),
            "bought": sum(t.value or 0 for t in trades if t.kind == "buy"),
            "sold": sum(t.value or 0 for t in trades if t.kind == "sell"),
            "insiders": len({t.insider_cik for t in trades}),
            "last": max((t.traded for t in trades if t.traded), default=None),
        }


@dataclass
class Person:
    cik: str
    name: str
    slug: str
    trades: list = field(default_factory=list)

    @property
    def counted(self):
        return [t for t in self.trades if t.counted]

    @property
    def companies(self):
        """Companies this person filed for, newest first, with their latest title there."""
        seen = {}
        for t in self.trades:
            if t.ticker and t.ticker not in seen:
                seen[t.ticker] = {"ticker": t.ticker, "slug": t.stock_slug, "name": t.filing.company_name,
                                  "title": t.role_text}
        return list(seen.values())

    @property
    def description(self):
        parts = [f"{c['title']}, {c['name']}" for c in self.companies[:3]]
        return " · ".join(parts)

    def stats(self):
        trades = self.counted
        return {
            "trades": len(trades),
            "buys": sum(1 for t in trades if t.kind == "buy"),
            "sells": sum(1 for t in trades if t.kind == "sell"),
            "bought": sum(t.value or 0 for t in trades if t.kind == "buy"),
            "sold": sum(t.value or 0 for t in trades if t.kind == "sell"),
            "first": min((t.traded for t in trades if t.traded), default=None),
            "last": max((t.traded for t in trades if t.traded), default=None),
            "amendments": sum(1 for t in self.trades if t.filing.is_amendment),
            "suspect": sum(1 for t in self.trades if t.suspect),
        }


def combine_lots(rows):
    """One row per filing, insider, direction and trade date: filers often list every broker fill.

    Shares and values are added up; the price becomes the average paid. Rows marked
    suspect, and rows without a price, are kept apart so their numbers aren't mixed in.
    """
    groups, order = {}, []
    for row in rows:
        key = (row.filing.accession, row.insider_cik, row.kind, row.traded, row.direct, row.security.lower(),
               row.suspect, row.price is None)
        if key in groups:
            first = groups[key]
            first.lots += 1
            first.shares = (first.shares or 0) + (row.shares or 0)
            if row.value is not None:
                first.value = (first.value or 0) + row.value
            if first.shares and first.value is not None:
                first.price = first.value / first.shares
            if row.row_key < first.row_key:
                first.row_key = row.row_key
        else:
            groups[key] = row
            order.append(row)
    return order


def mark_duplicates(rows):
    """The same insider, stock, day, direction, shares and price in two filings: count it once.

    Happens when a fund and its partner each file for the same trade. The earlier filing wins."""
    seen = {}
    for row in sorted(rows, key=lambda r: (r.filing.filed or date.max, r.filing.accession)):
        if row.filing.is_amendment or row.suspect or not row.insider_cik:
            continue
        key = (row.insider_cik, row.ticker, row.traded, row.kind, round(row.shares or 0, 3),
               round(row.price or 0, 2))
        if key in seen and seen[key] != row.filing.accession:
            row.duplicate = True
        else:
            seen.setdefault(key, row.filing.accession)
    return rows


class InsiderData:
    def __init__(self, filings, trades, today=None):
        self.today = today or date.today()
        self.filings = filings
        self.trades = mark_duplicates(combine_lots(trades))
        self.companies = {}
        self.people = {}
        for t in trades:
            if t.ticker:
                company = self.companies.get(t.stock_slug)
                if company is None:
                    company = Company(ticker=t.ticker, slug=t.stock_slug, cik=t.filing.company_cik,
                                      name=t.filing.company_name)
                    self.companies[t.stock_slug] = company
                company.trades.append(t)
            if t.insider_cik:
                person = self.people.get(t.insider_cik)
                if person is None:
                    person = Person(cik=t.insider_cik, name=t.insider_name, slug="")
                    self.people[t.insider_cik] = person
                person.trades.append(t)
        # the newest filing gives a company's current name and a person's current name
        for company in self.companies.values():
            newest = max(company.trades, key=lambda t: (t.filing.filed or date.min))
            company.name = newest.filing.company_name or company.name
        for person in self.people.values():
            newest = max(person.trades, key=lambda t: (t.filing.filed or date.min))
            person.name = newest.insider_name or person.name
        used = set()
        for person in sorted(self.people.values(), key=lambda p: (p.name, p.cik)):
            base = slugify(person.name) or "insider"
            slug, n = base, 2
            while slug in used:
                slug = f"{base}-{n}"
                n += 1
            used.add(slug)
            person.slug = slug

    # ---- lists for the pages

    def counted_trades(self):
        return [t for t in self.trades if t.counted and t.ticker]

    def latest(self, count=LATEST):
        trades = [t for t in self.counted_trades() if t.filing.filed]
        trades.sort(key=lambda t: (t.filing.filed, t.traded or date.min, -(t.value or 0)), reverse=True)
        return trades[:count]

    def biggest(self, kind, days=30, count=BIGGEST):
        since = self.today - timedelta(days=days)
        trades = [t for t in self.counted_trades() if t.kind == kind and t.value and t.traded and t.traded >= since]
        trades.sort(key=lambda t: -t.value)
        return trades[:count]

    def most_active(self, days=30, count=ACTIVE):
        since = self.today - timedelta(days=days)
        rows = []
        for company in self.companies.values():
            stats = company.stats(since)
            if stats["trades"]:
                rows.append((company, stats))
        rows.sort(key=lambda r: (-r[1]["trades"], r[0].ticker))
        return rows[:count]

    def overview(self, days=30):
        since = self.today - timedelta(days=days)
        recent = [t for t in self.counted_trades() if t.traded and t.traded >= since]
        return {
            "trades_30d": len(recent),
            "buys_30d": sum(1 for t in recent if t.kind == "buy"),
            "sells_30d": sum(1 for t in recent if t.kind == "sell"),
            "bought_30d": sum(t.value or 0 for t in recent if t.kind == "buy"),
            "sold_30d": sum(t.value or 0 for t in recent if t.kind == "sell"),
            "companies_30d": len({t.ticker for t in recent}),
            "insiders_30d": len({t.insider_cik for t in recent}),
            "trades_total": len(self.counted_trades()),
            "companies_total": len(self.companies),
            "insiders_total": len(self.people),
            "first_filing": min((f.filed for f in self.filings if f.filed), default=None),
            "last_filing": max((f.filed for f in self.filings if f.filed), default=None),
        }

    def weekly_counts(self, weeks=26):
        """Buys and sells per week (trade date), for the Insiders page chart."""
        this_monday = self.today - timedelta(days=self.today.weekday())
        starts = [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
        buys, sells = Counter(), Counter()
        for t in self.counted_trades():
            if t.traded and t.traded >= starts[0]:
                index = (t.traded - starts[0]).days // 7
                if 0 <= index < weeks:
                    (buys if t.kind == "buy" else sells)[index] += 1
        return [(start, buys.get(i, 0), sells.get(i, 0)) for i, start in enumerate(starts)]


def load(conn, today=None):
    """Read the insider tables. Returns an InsiderData (empty if the tables don't exist yet)."""
    if fetch_all(conn, HAS_TABLES_SQL)[0][0] == "0":
        return InsiderData([], [], today)
    filings = {}
    for row in fetch_all(conn, FILINGS_SQL):
        filings[row[0]] = Filing(
            accession=row[0], form=row[1], filed=parse_date(row[2]), period=parse_date(row[3]),
            company_cik=row[4], company_name=row[5], ticker=row[6].upper(),
            plan=None if row[7] == "" else row[7] == "1", is_amendment=row[8] == "1", url=row[9] or "",
        )
    import sec_parsing   # the collector's own rules for names (the repository root is on the path)
    for filing in filings.values():
        filing.company_name = sec_parsing.company_display_name(filing.company_name) or filing.company_name
    trades = []
    for row in fetch_all(conn, TRADES_SQL):
        filing = filings.get(row[0])
        if filing is None or sec_parsing.is_entity(row[3]):
            continue
        trades.append(InsiderTrade(
            filing=filing, row_key=row[1], insider_cik=row[2], insider_name=row[3], insider_title=row[4],
            roles=[r for r in (row[5] or "").split("|") if r], traded=parse_date(row[6]), kind=row[7],
            shares=to_float(row[8]), price=to_float(row[9]), value=to_float(row[10]), direct=row[11] == "1",
            nature=row[12], security=row[13], suspect=row[14] == "1", suspect_reason=row[15],
        ))
    return InsiderData(list(filings.values()), trades, today)


# ------------------------------------------------------------- formatting

def fmt_money(value):
    """$1,234,567 (whole dollars), or '–' when unknown."""
    if value is None:
        return "–"
    return f"${value:,.0f}"


def fmt_money_short(value):
    """$1.2M, $340K, $950: for tiles and tight columns ('–' when nothing)."""
    if not value:
        return "–"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            number = value / limit
            return f"${number:,.1f}{suffix}" if number < 100 else f"${number:,.0f}{suffix}"
    return f"${value:,.0f}"


def fmt_shares(value):
    if value is None:
        return "–"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def fmt_price(value):
    if value is None:
        return "–"
    return f"${value:,.2f}" if value >= 1 else f"${value:,.4f}".rstrip("0").rstrip(".")
