"""
insiders.py - reads the company-insider tables for the website.

Two sources feed the Insiders section:
  - the SEC's Form 4 filings (US officers and directors), tables insider_filings
    and insider_trades, keyed by the SEC's CIK numbers;
  - European registers of executives' trades (EU Market Abuse Regulation,
    Article 19), table mar_trades, one row per transaction line, keyed by the
    country and the person's name. Sweden (Finansinspektionen) is the first.

Both become the same objects here: a Filing (one report) with InsiderTrades,
grouped by Company (a stock page) and by Person (an insider page). Each trade
knows its market, so the pages can show one market or all of them.

Rules shared with the Congress data: amendments and rows the collector marked
"suspect" (impossible numbers) are shown but not counted. European rows that
the register later revised or cancelled, and rows linked to a share programme
(not open-market trades), are shown but not counted too. Amounts stay in the
currency of the trade; a euro figure (ECB reference rate of the trade day) sits
next to anything that isn't dollars or euros, and ranks trades across markets.
"""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from data import fetch_all, parse_date, slugify, stock_slug

LATEST = 50
BIGGEST = 20
ACTIVE = 20
ON_STOCK_PAGE = 50          # insider trades shown on a stock page before the "all" link

MARKETS = {
    "US": {"code": "US", "slug": "united-states", "name": "United States", "currency": "USD",
           "regulator": "the SEC", "source": "SEC Form 4", "since": "2021",
           "rule": "Officers and directors must report trades in their own company’s stock to the SEC within two "
                   "business days, on Form 4.",
           "coverage": "open-market buys and sells by officers and directors of US-listed companies since 2021"},
    "SE": {"code": "SE", "slug": "sweden", "name": "Sweden", "currency": "SEK",
           "regulator": "Finansinspektionen", "source": "Finansinspektionen’s register", "since": "July 2016",
           "rule": "Executives and board members of companies listed in Sweden must report trades in their company’s "
                   "shares to Finansinspektionen, the financial regulator, within three business days (EU Market "
                   "Abuse Regulation, Article 19).",
           "coverage": "buys and sells of shares by executives and board members of companies listed in Sweden "
                       "since July 2016"},
}
ALL_MARKETS = {"code": "ALL", "slug": "", "name": "All markets", "currency": "EUR"}

VENUES = {   # the register's venue names, shortened
    "nasdaq stockholm ab": "Nasdaq Stockholm", "omx nordic exchange stockholm ab": "Nasdaq Stockholm",
    "nasdaq - all markets": "Nasdaq", "first north sweden": "First North", "first north sweden - sme growth market": "First North",
    "nordic growth market": "NGM", "spotlight stock market": "Spotlight", "outside a trading venue": "off-exchange",
}

FILINGS_SQL = """
select accession, form, to_char(filing_date, 'YYYY-MM-DD'), to_char(period, 'YYYY-MM-DD'),
       coalesce(company_cik, ''), coalesce(company_name, ''), coalesce(ticker, ''),
       case when plan_10b5_1 is null then '' else plan_10b5_1::int::text end,
       is_amendment::int::text
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

MAR_SQL = """
select country, row_key, filing_key, to_char(published_at at time zone 'Europe/Stockholm', 'YYYY-MM-DD'),
       coalesce(issuer_name, ''), coalesce(issuer_short, ''), coalesce(issuer_lei, ''), coalesce(notifier, ''),
       person_name, coalesce(position, ''), coalesce(role, ''), closely_associated::int::text,
       is_amendment::int::text, option_programme::int::text, coalesce(instrument_type, ''),
       coalesce(instrument_name, ''), coalesce(isin, ''), to_char(trade_date, 'YYYY-MM-DD'), kind,
       volume::text, price::text, coalesce(currency, ''), value::text, value_eur::text, coalesce(venue, ''),
       status, suspect::int::text, coalesce(suspect_reason, '')
from mar_trades
order by trade_date desc nulls last, published_at desc, row_key
"""

FX_SQL = "select to_char(day, 'YYYY-MM-DD'), currency, rate::text from fx_rates where currency = 'USD'"

HAS_TABLE_SQL = "select count(*)::text from information_schema.tables where table_name = %s"


def to_float(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def person_key(country, name):
    """European registers have no person IDs: the name, normalised, stands in for one."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return f"{country.lower()}:{re.sub(r'[^a-z0-9]+', '-', plain).strip('-')}"


def venue_text(venue):
    return VENUES.get((venue or "").lower(), (venue or "").title() if (venue or "").isupper() else venue or "")


@dataclass
class Filing:
    accession: str
    form: str
    filed: date | None
    period: date | None
    company_cik: str
    company_name: str
    ticker: str                # for European companies: the short company name
    plan: bool | None
    is_amendment: bool
    url: str
    country: str = "US"
    slug: str | None = None    # stock page slug when it isn't the ticker

    @property
    def market(self):
        return MARKETS[self.country]

    @property
    def source_label(self):
        return f"Form {self.form}" if self.country == "US" else "Register entry"


@dataclass
class InsiderTrade:
    filing: Filing
    row_key: str
    insider_cik: str           # the person key: SEC CIK, or country:name for European registers
    insider_name: str
    insider_title: str
    roles: list
    traded: date | None
    kind: str                  # buy / sell
    shares: float | None
    price: float | None
    value: float | None
    direct: bool
    nature: str
    security: str
    suspect: bool
    suspect_reason: str
    lots: int = 1              # broker fills combined into this row
    duplicate: bool = False    # the same trade appears in an earlier filing (a joint filer's)
    currency: str = "USD"
    value_eur: float | None = None
    status: str = "Current"    # European registers: Current / Revised / Cancelled
    programme: bool = False    # linked to a share or option programme (not an open-market trade)
    venue: str = ""
    superseded: bool = False   # an amendment in the register replaces this row
    instrument: str = ""       # European registers: the share class, e.g. "Investor B"
    transfer: bool = False     # a buy and a sell of the same lot on the same day: shares moved, not traded
    perf: object = None        # how the stock did afterwards (returns.Perf), when known

    @property
    def country(self):
        return self.filing.country

    @property
    def counted(self):
        if self.suspect or self.duplicate or self.superseded or self.programme or self.transfer:
            return False
        if self.status.lower() != "current":
            return False
        if self.country == "US" and self.filing.is_amendment:
            return False
        return True

    @property
    def label(self):
        return "Buy" if self.kind == "buy" else "Sell"

    @property
    def note(self):
        """The one line under the Buy/Sell label that says why a row isn't counted, if it isn't."""
        if self.country == "US" and self.filing.is_amendment:
            return "amendment"
        if self.suspect:
            return f"not counted: {self.suspect_reason}"
        if self.duplicate:
            return "also in an earlier filing"
        if self.superseded:
            return "revised later, see the corrected row"
        if self.transfer:
            return "moved between the insider and a related party, not counted"
        if self.status.lower() == "cancelled":
            return "cancelled by the filer"
        if self.status.lower() != "current":
            return self.status.lower()
        if self.programme:
            return "share programme, not counted"
        if self.filing.plan:
            return "pre-set plan"
        if self.country != "US" and self.filing.is_amendment:
            return "corrected filing"
        return ""

    @property
    def ticker(self):
        return self.filing.ticker

    @property
    def stock_slug(self):
        return self.filing.slug or stock_slug(self.filing.ticker)

    @property
    def role_text(self):
        """'CEO' if a title is known, else 'Director' / 'Officer'."""
        return self.insider_title or ", ".join(self.roles) or "Insider"

    @property
    def via(self):
        """Who held the shares when not the insider directly (a spouse, a company)."""
        if self.direct:
            return ""
        return self.nature or "indirect"

    @property
    def days_to_file(self):
        if self.traded and self.filing.filed:
            return (self.filing.filed - self.traded).days
        return None

    @property
    def eur_shown(self):
        """A euro figure is shown next to anything that isn't dollars or euros."""
        return self.currency not in ("USD", "EUR") and self.value_eur is not None


@dataclass
class Company:
    ticker: str
    slug: str
    cik: str
    name: str
    country: str = "US"
    isins: set = field(default_factory=set)
    trades: list = field(default_factory=list)

    @property
    def market(self):
        return MARKETS[self.country]

    @property
    def currency(self):
        return self.market["currency"]

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
            "bought_eur": sum(t.value_eur or 0 for t in trades if t.kind == "buy"),
            "sold_eur": sum(t.value_eur or 0 for t in trades if t.kind == "sell"),
            "currency": self.currency,
            "insiders": len({t.insider_cik for t in trades}),
            "last": max((t.traded for t in trades if t.traded), default=None),
        }


@dataclass
class Person:
    cik: str
    name: str
    slug: str
    country: str = "US"
    trades: list = field(default_factory=list)

    @property
    def market(self):
        return MARKETS[self.country]

    @property
    def currency(self):
        return self.market["currency"]

    @property
    def counted(self):
        return [t for t in self.trades if t.counted]

    @property
    def companies(self):
        """Companies this person filed for, newest first, with their latest title there."""
        seen = {}
        for t in self.trades:
            if t.ticker and t.stock_slug not in seen:
                seen[t.stock_slug] = {"ticker": t.ticker, "slug": t.stock_slug, "name": t.filing.company_name,
                                      "title": t.role_text, "country": t.country}
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
            "bought_eur": sum(t.value_eur or 0 for t in trades if t.kind == "buy"),
            "sold_eur": sum(t.value_eur or 0 for t in trades if t.kind == "sell"),
            "currency": self.currency,
            "first": min((t.traded for t in trades if t.traded), default=None),
            "last": max((t.traded for t in trades if t.traded), default=None),
            "amendments": sum(1 for t in self.trades if t.country == "US" and t.filing.is_amendment),
            "suspect": sum(1 for t in self.trades if t.suspect),
            "not_current": sum(1 for t in self.trades if t.status.lower() != "current" or t.superseded),
            "programme": sum(1 for t in self.trades if t.programme),
            "transfers": sum(1 for t in self.trades if t.transfer),
        }


def combine_lots(rows):
    """One row per filing, insider, direction and trade date: filers often list every broker fill.

    Shares and values are added up; the price becomes the average paid. Rows marked
    suspect, and rows without a price, are kept apart so their numbers aren't mixed in.
    """
    groups, order = {}, []
    for row in rows:
        key = (row.filing.accession, row.insider_cik, row.kind, row.traded, row.direct, row.security.lower(),
               row.suspect, row.price is None, row.status, row.programme, row.currency)
        if key in groups:
            first = groups[key]
            first.lots += 1
            first.shares = (first.shares or 0) + (row.shares or 0)
            if row.value is not None:
                first.value = (first.value or 0) + row.value
            if row.value_eur is not None:
                first.value_eur = (first.value_eur or 0) + row.value_eur
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
        if row.filing.is_amendment or row.suspect or not row.insider_cik or row.country != "US":
            continue
        key = (row.insider_cik, row.ticker, row.traded, row.kind, round(row.shares or 0, 3),
               round(row.price or 0, 2))
        if key in seen and seen[key] != row.filing.accession:
            row.duplicate = True
        else:
            seen.setdefault(key, row.filing.accession)
    return rows


def mark_transfers(rows):
    """A buy and a sell by the same insider of the same lot on the same day are one transfer.

    European filers report a move of shares between themselves and their own company
    (or a relative) as a disposal by one and an acquisition by the other; nothing was
    bought or sold on the market, so neither side is counted."""
    sides = defaultdict(set)
    for row in rows:
        if row.country != "US" and row.status.lower() == "current" and not row.suspect:
            key = (row.insider_cik, row.security, row.traded, round(row.shares or 0, 3), round(row.price or 0, 2))
            sides[key].add(row.kind)
    for row in rows:
        if row.country != "US" and row.status.lower() == "current" and not row.suspect:
            key = (row.insider_cik, row.security, row.traded, round(row.shares or 0, 3), round(row.price or 0, 2))
            if sides[key] == {"buy", "sell"}:
                row.transfer = True
    return rows


def mark_superseded(rows):
    """A European register's corrected filing replaces the original line for the same trade.

    The register marks the original 'Revised' itself, but only within the window the
    collector re-reads; here the older line is marked when a correction exists."""
    corrections = defaultdict(list)
    for row in rows:
        if row.country != "US" and row.filing.is_amendment and row.status.lower() == "current":
            corrections[(row.insider_cik, row.filing.company_cik, row.traded, row.kind)].append(row.filing.filed)
    for row in rows:
        if row.country == "US" or row.filing.is_amendment or row.status.lower() != "current":
            continue
        later = corrections.get((row.insider_cik, row.filing.company_cik, row.traded, row.kind))
        if later and row.filing.filed and any(d and d > row.filing.filed for d in later):
            row.superseded = True
    return rows


class InsiderData:
    def __init__(self, filings, trades, today=None):
        self.today = today or date.today()
        self.filings = filings
        self.trades = mark_superseded(mark_transfers(mark_duplicates(combine_lots(trades))))
        self.companies = {}
        self.people = {}
        for t in self.trades:
            if t.ticker:
                company = self.companies.get(t.stock_slug)
                if company is None:
                    company = Company(ticker=t.ticker, slug=t.stock_slug, cik=t.filing.company_cik,
                                      name=t.filing.company_name, country=t.country)
                    self.companies[t.stock_slug] = company
                company.trades.append(t)
                if t.security and t.country != "US":
                    company.isins.add(t.security)
            if t.insider_cik:
                person = self.people.get(t.insider_cik)
                if person is None:
                    person = Person(cik=t.insider_cik, name=t.insider_name, slug="", country=t.country)
                    self.people[t.insider_cik] = person
                person.trades.append(t)
        # the newest filing gives a company's current name and a person's current name
        for company in self.companies.values():
            newest = max(company.trades, key=lambda t: (t.filing.filed or date.min))
            company.name = newest.filing.company_name or company.name
        for person in self.people.values():
            newest = max(person.trades, key=lambda t: (t.filing.filed or date.min))
            person.name = newest.insider_name or person.name
        used = {m["slug"] for m in MARKETS.values()}          # /insiders/sweden/ is a market page
        for person in sorted(self.people.values(), key=lambda p: (p.name, p.cik)):
            base = slugify(person.name) or "insider"
            slug, n = base, 2
            while slug in used:
                slug = f"{base}-{n}"
                n += 1
            used.add(slug)
            person.slug = slug

    @property
    def markets(self):
        """The markets with data, in a fixed order."""
        present = {t.country for t in self.trades}
        return [MARKETS[code] for code in MARKETS if code in present]

    def market_info(self, country=None):
        return MARKETS[country] if country else ALL_MARKETS

    # ---- lists for the pages (country=None means all markets)

    def counted_trades(self, country=None):
        return [t for t in self.trades if t.counted and t.ticker and (country is None or t.country == country)]

    def latest(self, count=LATEST, country=None):
        trades = [t for t in self.counted_trades(country) if t.filing.filed]
        trades.sort(key=lambda t: (t.filing.filed, t.traded or date.min, -(t.value_eur or t.value or 0)), reverse=True)
        return trades[:count]

    def biggest(self, kind, days=30, count=BIGGEST, country=None):
        """Ranked by the euro value when markets are mixed, by the local value within one market."""
        since = self.today - timedelta(days=days)
        trades = [t for t in self.counted_trades(country) if t.kind == kind and t.traded and t.traded >= since]
        if country is None:
            trades = [t for t in trades if t.value_eur]
            trades.sort(key=lambda t: -t.value_eur)
        else:
            trades = [t for t in trades if t.value]
            trades.sort(key=lambda t: -t.value)
        return trades[:count]

    def most_active(self, days=30, count=ACTIVE, country=None):
        since = self.today - timedelta(days=days)
        rows = []
        for company in self.companies.values():
            if country is not None and company.country != country:
                continue
            stats = company.stats(since)
            if stats["trades"]:
                rows.append((company, stats))
        rows.sort(key=lambda r: (-r[1]["trades"], r[0].ticker))
        return rows[:count]

    def overview(self, days=30, country=None):
        since = self.today - timedelta(days=days)
        all_counted = self.counted_trades(country)
        recent = [t for t in all_counted if t.traded and t.traded >= since]
        companies = [c for c in self.companies.values() if country is None or c.country == country]
        people = [p for p in self.people.values() if country is None or p.country == country]
        filings = [f for f in self.filings if country is None or f.country == country]
        return {
            "currency": self.market_info(country)["currency"],
            "trades_30d": len(recent),
            "buys_30d": sum(1 for t in recent if t.kind == "buy"),
            "sells_30d": sum(1 for t in recent if t.kind == "sell"),
            "bought_30d": sum(t.value or 0 for t in recent if t.kind == "buy"),
            "sold_30d": sum(t.value or 0 for t in recent if t.kind == "sell"),
            "bought_30d_eur": sum(t.value_eur or 0 for t in recent if t.kind == "buy"),
            "sold_30d_eur": sum(t.value_eur or 0 for t in recent if t.kind == "sell"),
            "companies_30d": len({t.stock_slug for t in recent}),
            "insiders_30d": len({t.insider_cik for t in recent}),
            "trades_total": len(all_counted),
            "companies_total": len(companies),
            "insiders_total": len(people),
            "first_filing": min((f.filed for f in filings if f.filed), default=None),
            "last_filing": max((f.filed for f in filings if f.filed), default=None),
        }

    def weekly_counts(self, weeks=26, country=None):
        """Buys and sells per week (trade date), for the Insiders page chart."""
        this_monday = self.today - timedelta(days=self.today.weekday())
        starts = [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
        buys, sells = Counter(), Counter()
        for t in self.counted_trades(country):
            if t.traded and t.traded >= starts[0]:
                index = (t.traded - starts[0]).days // 7
                if 0 <= index < weeks:
                    (buys if t.kind == "buy" else sells)[index] += 1
        return [(start, buys.get(i, 0), sells.get(i, 0)) for i, start in enumerate(starts)]


# ------------------------------------------------------------------ loading

def table_exists(conn, name):
    return fetch_all(conn, HAS_TABLE_SQL.replace("%s", f"'{name}'"))[0][0] != "0"


class UsdRates:
    """Euro value of dollar trades, from the ECB's rates (1 EUR = rate USD), nearest earlier day."""

    def __init__(self, rows):
        self.rates = {parse_date(day): to_float(rate) for day, _, rate in rows}
        self.rates.pop(None, None)

    def eur(self, value, day):
        if value is None or day is None or not self.rates:
            return None
        for back in range(11):
            rate = self.rates.get(day - timedelta(days=back))
            if rate:
                return value / rate
        return None


def load_us(conn, rates):
    """The SEC tables -> (filings, trades)."""
    if not table_exists(conn, "insider_trades"):
        return [], []
    import sec_parsing   # the collector's own rules for names and addresses (the repository root is on the path)
    filings = {}
    for row in fetch_all(conn, FILINGS_SQL):
        filings[row[0]] = Filing(
            accession=row[0], form=row[1], filed=parse_date(row[2]), period=parse_date(row[3]),
            company_cik=row[4], company_name=row[5], ticker=row[6].upper(),
            plan=None if row[7] == "" else row[7] == "1", is_amendment=row[8] == "1",
            url=sec_parsing.accession_url(row[0], row[4]) or "",
        )
    for filing in filings.values():
        filing.company_name = sec_parsing.company_display_name(filing.company_name) or filing.company_name
    trades = []
    for row in fetch_all(conn, TRADES_SQL):
        filing = filings.get(row[0])
        if filing is None or sec_parsing.is_entity(row[3]):
            continue
        traded = parse_date(row[6])
        value = to_float(row[10])
        trades.append(InsiderTrade(
            filing=filing, row_key=row[1], insider_cik=row[2], insider_name=row[3], insider_title=row[4],
            roles=[r for r in (row[5] or "").split("|") if r], traded=traded, kind=row[7],
            shares=to_float(row[8]), price=to_float(row[9]), value=value, direct=row[11] == "1",
            nature=row[12], security=row[13], suspect=row[14] == "1", suspect_reason=row[15],
            currency="USD", value_eur=rates.eur(value, traded),
        ))
    return list(filings.values()), trades


def load_europe(conn):
    """The mar_trades table -> (filings, trades): one Filing per notification (filing_key)."""
    if not table_exists(conn, "mar_trades"):
        return [], []
    rows = fetch_all(conn, MAR_SQL)
    # company slugs first: /stocks/se/<short name>/, unique per issuer (LEI, or name)
    import sweden_parsing   # the collector's own naming rules (the repository root is on the path)
    issuers = {}
    for row in rows:
        country, issuer_name, lei = row[0], row[4], row[6]
        key = (country, lei or issuer_name.lower())
        issuers.setdefault(key, {"name": issuer_name, "short": sweden_parsing.company_short_name(issuer_name) or issuer_name,
                                 "slug": None})
    used = set()
    for key in sorted(issuers, key=lambda k: issuers[k]["short"].lower()):
        info = issuers[key]
        base = f"{key[0].lower()}/{slugify(info['short'])}"
        slug, n = base, 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        info["slug"] = slug
    filings, trades = {}, []
    for row in rows:
        (country, row_key, filing_key, published, issuer_name, issuer_short, lei, notifier, person, position, role,
         closely, amendment, programme, instrument_type, instrument_name, isin, traded, kind, volume, price,
         currency, value, value_eur, venue, status, suspect, reason) = row
        issuer = issuers[(country, lei or issuer_name.lower())]
        accession = f"{country.lower()}:{filing_key}"
        filing = filings.get(accession)
        if filing is None:
            filing = Filing(accession=accession, form="", filed=parse_date(published), period=None,
                            company_cik=lei or issuer_name, company_name=issuer_name, ticker=issuer["short"],
                            plan=None, is_amendment=amendment == "1", url="", country=country, slug=issuer["slug"])
            filings[accession] = filing
        filing.is_amendment = filing.is_amendment or amendment == "1"
        trade_day = parse_date(traded)
        if not filing.url:
            filing.url = sweden_parsing.register_search_url(person, issuer_name, trade_day)
        trades.append(InsiderTrade(
            filing=filing, row_key=row_key, insider_cik=person_key(country, person), insider_name=person,
            insider_title=position, roles=[role] if role else [], traded=trade_day, kind=kind,
            shares=to_float(volume), price=to_float(price), value=to_float(value), direct=closely != "1",
            nature=(f"via {notifier}" if notifier else "closely associated person") if closely == "1" else "",
            security=isin, suspect=suspect == "1", suspect_reason=reason, currency=currency or "SEK",
            value_eur=to_float(value_eur), status=status or "Current", programme=programme == "1",
            venue=venue_text(venue), instrument=instrument_name,
        ))
    return list(filings.values()), trades


def load(conn, today=None):
    """Read every insider source. Returns an InsiderData (empty when no table exists yet)."""
    rates = UsdRates(fetch_all(conn, FX_SQL) if table_exists(conn, "fx_rates") else [])
    us_filings, us_trades = load_us(conn, rates)
    eu_filings, eu_trades = load_europe(conn)
    return InsiderData(us_filings + eu_filings, us_trades + eu_trades, today)


# ------------------------------------------------------------- formatting

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def money_prefix(currency):
    symbol = SYMBOLS.get(currency or "USD")
    return symbol if symbol else f"{currency}\u00a0"      # "SEK 1,234" stays on one line


def fmt_money(value, currency="USD"):
    """$1,234,567 or SEK 1,234,567 (whole units), or '–' when unknown."""
    if value is None:
        return "–"
    return f"{money_prefix(currency)}{value:,.0f}"


def fmt_money_short(value, currency="USD"):
    """$1.2M, SEK 340K, €950: for tiles and tight columns ('–' when nothing)."""
    if not value:
        return "–"
    prefix = money_prefix(currency)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            number = value / limit
            return f"{prefix}{number:,.1f}{suffix}" if number < 100 else f"{prefix}{number:,.0f}{suffix}"
    return f"{prefix}{value:,.0f}"


def fmt_eur_short(value):
    return "≈ " + fmt_money_short(value, "EUR") if value else ""


def fmt_shares(value):
    if value is None:
        return "–"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def fmt_price(value, currency="USD"):
    if value is None:
        return "–"
    prefix = money_prefix(currency)
    return f"{prefix}{value:,.2f}" if value >= 1 else f"{prefix}{value:,.4f}".rstrip("0").rstrip(".")
